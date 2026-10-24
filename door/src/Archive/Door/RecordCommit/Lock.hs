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
import Archive.Door.RecordCommit.Failure (failText, tryIO)
import Archive.Door.RecordCommit.Git (gitDirectoryMatches, rootDirectoryMatches)
import Archive.Door.RecordCommit.Internal (ArchiveWriteLock (..))
import Archive.Door.RecordCommit.Recovery (reconcileOperationJournal)
import Archive.Executables (resolveGitExecutable)
import Control.Concurrent.MVar (MVar, newMVar, withMVar)
import qualified Control.Exception as Exception
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
{-# NOINLINE processWriteLock #-}
processWriteLock :: MVar ()
processWriteLock = unsafePerformIO (newMVar ())

-- | Run one Door mutation while holding the archive-wide advisory lock.
-- The lock file is operational state under .git and never enters the archive.
withArchiveWriteLock
  :: FilePath
  -> (ArchiveWriteLock -> IO (Either MutationError a))
  -> IO (Either MutationError a)
withArchiveWriteLock root action = withMVar processWriteLock $ \_ -> do
  executableResult <- resolveGitExecutable
  case executableResult of
    Left problem -> pure (Left (MutationConfigurationFailure problem))
    Right executable -> do
      absolute <- tryIO (makeAbsolute root)
      case absolute of
        Left problem -> pure (Left (MutationIoFailure problem))
        Right absoluteRoot -> do
          result <- tryIO $ Exception.bracket (openArchiveDirectories absoluteRoot)
            closeArchiveDirectories $ \(rootFd, gitFd) ->
              Exception.bracket (acquire gitFd) closeFd
                (\_ -> do
                  topology <- validateGitObjectStoreTopology executable absoluteRoot rootFd gitFd
                  case topology of
                    Left problem -> pure (Left (MutationOperationFailure problem))
                    Right () -> do
                      recovered <- reconcileOperationJournal executable absoluteRoot rootFd gitFd
                      case recovered of
                        Left problem -> pure (Left (MutationOperationFailure problem))
                        Right () -> action (ArchiveWriteLock absoluteRoot rootFd gitFd executable))
          pure $ case result of
            Left problem -> Left (MutationIoFailure ("archive lock failed: " <> problem))
            Right value -> value
  where
    acquire gitFd = do
      fd <- openFdAt (Just gitFd) "archive-door.lock" ReadWrite
        defaultFileFlags { creat = Just ownerFileModes, cloexec = True, nofollow = True }
      locked <- tryIO (setLock fd (WriteLock, AbsoluteSeek, 0, 0))
      case locked of
        Right () -> pure fd
        Left problem -> closeFd fd >> failText
          ("another Door operation holds the archive lock: " <> problem)

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
  withMVar processWriteLock $ \_ -> do
    executableResult <- resolveGitExecutable
    case executableResult of
      Left problem -> pure (Left (MutationConfigurationFailure problem))
      Right executable -> do
        absolute <- tryIO (makeAbsolute root)
        case absolute of
          Left problem -> pure (Left (MutationIoFailure problem))
          Right absoluteRoot -> do
            pinned <- tryIO $ (,) <$> rootDirectoryMatches absoluteRoot rootFd
              <*> gitDirectoryMatches rootFd gitFd
            case pinned of
              Left problem -> pure (Left (MutationIoFailure problem))
              Right (False, _) -> pure (Left (MutationRejected
                "archive root changed before the pinned archive lock was acquired"))
              Right (_, False) -> pure (Left (MutationRejected
                "Git directory changed before the pinned archive lock was acquired"))
              Right (True, True) -> do
                result <- tryIO $ Exception.bracket (acquire gitFd) closeFd $ \_ -> do
                  topology <- validateGitObjectStoreTopology executable absoluteRoot rootFd gitFd
                  case topology of
                    Left problem -> pure (Left (MutationOperationFailure problem))
                    Right () -> do
                      recovered <- reconcileOperationJournal executable absoluteRoot rootFd gitFd
                      case recovered of
                        Left problem -> pure (Left (MutationOperationFailure problem))
                        Right () -> action (ArchiveWriteLock absoluteRoot rootFd gitFd executable)
                pure $ case result of
                  Left problem -> Left (MutationIoFailure ("archive lock failed: " <> problem))
                  Right (Left failure) -> Left failure
                  Right (Right value) -> Right value
  where
    acquire directoryFd = do
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
