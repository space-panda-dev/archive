{-# LANGUAGE OverloadedStrings #-}

-- | Acquiring the archive-wide write lock every Door mutation runs under.
--
-- Acquiring the lock also proves the object-store topology and reconciles
-- any operation journal a previous Door left, so the caller starts from a
-- state Door fully accounts for.
module Archive.Door.RecordCommit.Lock
  ( withArchiveWriteLock
  , withArchiveWriteLockAtPinned
  ) where

import Archive.Door.MutationError (MutationError (..))
import Archive.Door.RecordCommit.Descriptors (ownerFileModes)
import Archive.Door.RecordCommit.Durability (validateGitObjectStoreTopology)
import Archive.Door.RecordCommit.Failure (failText, orFail, tryIO)
import Archive.Door.RecordCommit.Git (gitDirectoryMatches, rootDirectoryMatches)
import Archive.Door.RecordCommit.Internal (ArchiveWriteLock (..))
import Archive.Door.RecordCommit.Recovery (reconcileOperationJournal)
import Archive.Executables (Executable, resolveGitExecutable)
import Control.Concurrent.MVar (MVar, newMVar, withMVar)
import qualified Control.Exception as Exception
import Control.Monad.Trans.Except (ExceptT (..), except, runExceptT, throwE)
import Data.Text (Text)
import System.Directory (makeAbsolute)
import System.IO (SeekMode (AbsoluteSeek))
import System.IO.Unsafe (unsafePerformIO)
import System.Posix.IO
  ( LockRequest (WriteLock)
  , OpenFileFlags (cloexec, creat, directory, nofollow)
  , OpenMode (ReadOnly, ReadWrite)
  , closeFd
  , defaultFileFlags
  , openFd
  , openFdAt
  , setLock
  )
import System.Posix.Types (Fd)

-- POSIX record locks are owned by a process, so a second thread in this
-- process can acquire the same lock. Serialize those callers locally too.
-- The serializing MVar must be one value for the whole process, which is
-- why it is a top-level 'unsafePerformIO': NOINLINE stops GHC from inlining
-- the definition at each use, which would give every caller a fresh MVar and
-- no mutual exclusion at all.
{-# NOINLINE processWriteLock #-}
processWriteLock :: MVar ()
processWriteLock = unsafePerformIO (newMVar ())

-- | Run one Door mutation while holding the archive-wide advisory lock.
-- The lock file is operational state under .git and never enters the archive.
withArchiveWriteLock
  :: FilePath
  -> (ArchiveWriteLock -> IO (Either MutationError a))
  -> IO (Either MutationError a)
withArchiveWriteLock root action = withMVar processWriteLock $ \_ -> runExceptT $ do
  executable <- orFail MutationConfigurationFailure resolveGitExecutable
  absoluteRoot <- orFail MutationIoFailure (tryIO (makeAbsolute root))
  result <- orFail lockFailure $ tryIO $ Exception.bracket (openArchiveDirectories absoluteRoot)
    closeArchiveDirectories $ \(rootFd, gitFd) ->
      Exception.bracket (acquireLockFile gitFd) closeFd
        (\_ -> runLocked executable absoluteRoot rootFd gitFd action)
  except result

-- | Run initialization work under the same process and filesystem lock as
-- ordinary Door mutations. Both directory descriptors are supplied by the
-- caller, so acquiring the lock does not resolve a possibly replaced root or
-- .git pathname a second time.
withArchiveWriteLockAtPinned
  :: FilePath
  -> Fd
  -> Fd
  -> (ArchiveWriteLock -> IO (Either MutationError a))
  -> IO (Either MutationError a)
withArchiveWriteLockAtPinned root rootFd gitFd action =
  withMVar processWriteLock $ \_ -> runExceptT $ do
    executable <- orFail MutationConfigurationFailure resolveGitExecutable
    absoluteRoot <- orFail MutationIoFailure (tryIO (makeAbsolute root))
    pinned <- orFail MutationIoFailure $ tryIO $ (,) <$> rootDirectoryMatches absoluteRoot rootFd
      <*> gitDirectoryMatches rootFd gitFd
    case pinned of
      (False, _) -> throwE (MutationRejected
        "archive root changed before the pinned archive lock was acquired")
      (_, False) -> throwE (MutationRejected
        "Git directory changed before the pinned archive lock was acquired")
      (True, True) -> pure ()
    result <- orFail lockFailure $ tryIO $ Exception.bracket (acquireLockFile gitFd) closeFd $ \_ ->
      runLocked executable absoluteRoot rootFd gitFd action
    except result

lockFailure :: Text -> MutationError
lockFailure problem = MutationIoFailure ("archive lock failed: " <> problem)

-- | With the lock held: prove the object-store topology, finish or undo any
-- journaled operation, and only then run the caller's mutation.
runLocked
  :: Executable
  -> FilePath
  -> Fd
  -> Fd
  -> (ArchiveWriteLock -> IO (Either MutationError a))
  -> IO (Either MutationError a)
runLocked executable absoluteRoot rootFd gitFd action = runExceptT $ do
  orFail MutationOperationFailure (validateGitObjectStoreTopology executable absoluteRoot rootFd gitFd)
  orFail MutationOperationFailure (reconcileOperationJournal executable absoluteRoot rootFd gitFd)
  ExceptT (action (ArchiveWriteLock absoluteRoot rootFd gitFd executable))

acquireLockFile :: Fd -> IO Fd
acquireLockFile directoryFd = do
  fd <- openFdAt (Just directoryFd) "archive-door.lock" ReadWrite
    defaultFileFlags { creat = Just ownerFileModes, cloexec = True, nofollow = True }
  locked <- tryIO (setLock fd (WriteLock, AbsoluteSeek, 0, 0))
  case locked of
    Right () -> pure fd
    Left problem -> closeFd fd >> failText
      ("another Door operation holds the archive lock: " <> problem)

openArchiveDirectories :: FilePath -> IO (Fd, Fd)
openArchiveDirectories root = do
  rootFd <- openFd root ReadOnly directoryFlags
  gitResult <- Exception.try (openFdAt (Just rootFd) ".git" ReadOnly directoryFlags)
    :: IO (Either Exception.IOException Fd)
  case gitResult of
    Left problem -> closeFd rootFd >> Exception.throwIO problem
    Right gitFd -> pure (rootFd, gitFd)
  where
    directoryFlags = defaultFileFlags
      { directory = True, nofollow = True, cloexec = True }

closeArchiveDirectories :: (Fd, Fd) -> IO ()
closeArchiveDirectories (rootFd, gitFd) = do
  closeFd gitFd
  closeFd rootFd
