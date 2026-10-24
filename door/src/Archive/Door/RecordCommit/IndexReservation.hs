{-# LANGUAGE OverloadedStrings #-}

-- | The private Git index a batch builds its tree in.
--
-- The index is reserved with @O_EXCL@ under a Door-only prefix and its inode
-- tracked across every Git child, so the operation journal can name exactly
-- the index this operation owns and recovery removes nothing else.
module Archive.Door.RecordCommit.IndexReservation
  ( IndexReservation
  , attachJournal
  , detachJournal
  , reservedIndex
  , runIndexGit
  , withNewIndexReservation
  ) where

import Archive.Door.RecordCommit.Descriptors (ownerFileModes, unlinkAt)
import Archive.Door.RecordCommit.Failure (failText, require, tryIO)
import Archive.Door.RecordCommit.Git (runGit)
import Archive.Door.RecordCommit.Journal
  ( JournalIndex (..)
  , OperationJournalOf (..)
  , PendingJournal
  , replaceOperationJournal
  )
import Archive.Executables (Executable)
import qualified Control.Exception as Exception
import Control.Monad.Trans.Class (lift)
import Control.Monad.Trans.Except (ExceptT (..), except, runExceptT)
import Data.IORef (IORef, newIORef, readIORef, writeIORef)
import Data.Text (Text)
import qualified Data.Text as Text
import Data.Unique (hashUnique, newUnique)
import System.IO.Error (isAlreadyExistsError)
import System.Posix.Files (deviceID, fileID, getFdStatus, isRegularFile, linkCount)
import System.Posix.IO
  ( OpenFileFlags (cloexec, creat, exclusive, nofollow, nonBlock)
  , OpenMode (ReadOnly, WriteOnly)
  , closeFd
  , defaultFileFlags
  , openFdAt
  )
import System.Posix.Process (getProcessID)
import System.Posix.Types (DeviceID, Fd (..), FileID)
import Foreign.C.String (CString, withCString)
import Foreign.C.Types (CInt (..))

foreign import ccall unsafe "archive_has_entry_prefix"
  c_hasEntryPrefix :: CInt -> CString -> IO CInt

-- Git atomically replaces its index file. Keep the prior descriptor so a
-- replacement is accepted only when Git removed the reserved inode and the
-- new path is a regular file. The reference is created for this operation.
-- This ownership proof assumes the configured Git executable is trusted and
-- there is no hostile same-uid process racing inside .git; POSIX offers no
-- conditional unlink-by-inode for Git's pathname-based index replacement.
-- We still recheck before and after every child, and fail closed on mismatch.
data IndexReservation = IndexReservation
  { reservationName :: FilePath
  -- | The index inode this operation owns now; Git's replacements move it.
  , reservationOwned :: IORef OwnedIndex
  -- | The batch's current journal once it is published, so a replacement
  -- of the index inode is journaled before the next Git child runs.
  , reservationJournal :: IORef (Maybe (IORef PendingJournal))
  }

-- | A descriptor that keeps the owned index inode from being reused while
-- Door holds it, and that inode's identity.
data OwnedIndex = OwnedIndex
  { ownedIndexFd :: Fd
  , ownedIndexIdentity :: (DeviceID, FileID)
  }

-- | The reserved index's name and the identity of the inode owned now.
reservedIndex :: IndexReservation -> IO (FilePath, (DeviceID, FileID))
reservedIndex reservation = do
  owned <- readIORef (reservationOwned reservation)
  pure (reservationName reservation, ownedIndexIdentity owned)

-- | Journal every later change of the owned index inode in this journal.
attachJournal :: IndexReservation -> IORef PendingJournal -> IO ()
attachJournal reservation journal = writeIORef (reservationJournal reservation) (Just journal)

-- | Stop journaling index changes: the journal was never published.
detachJournal :: IndexReservation -> IO ()
detachJournal reservation = writeIORef (reservationJournal reservation) Nothing

reserveIndex :: Fd -> IO (Either Text IndexReservation)
reserveIndex gitFd = runExceptT $ do
  -- A kill after O_EXCL reservation but before the operation journal is
  -- published leaves no inode proof. The next mutation refuses this stale
  -- private-prefix artifact for operator inspection; it never guesses ownership.
  stale <- ExceptT (hasStaleIndex gitFd)
  require (not stale) "stale Door index artifact requires operator inspection"
  ExceptT tryNames
  where
    tryNames = do
      processId <- getProcessID
      unique <- newUnique
      let name = "archive-door-index-" <> show processId <> "-" <> show (hashUnique unique)
      opened <- Exception.try $ openFdAt (Just gitFd) name WriteOnly
        defaultFileFlags
          { creat = Just ownerFileModes
          , exclusive = True
          , nofollow = True
          , cloexec = True
          }
        :: IO (Either Exception.IOException Fd)
      case opened of
        Left problem
          | isAlreadyExistsError problem -> tryNames
          | otherwise -> pure (Left (Text.pack (show problem)))
        Right fd -> do
          status <- tryIO (getFdStatus fd)
          case status of
            Left problem -> closeFd fd >> pure (Left problem)
            Right fileStatus -> do
              owned <- newIORef (OwnedIndex fd (deviceID fileStatus, fileID fileStatus))
              journalWriter <- newIORef Nothing
              pure (Right (IndexReservation name owned journalWriter))

hasStaleIndex :: Fd -> IO (Either Text Bool)
hasStaleIndex (Fd descriptor) =
  withCString "archive-door-index" $ \prefix -> do
    result <- c_hasEntryPrefix descriptor prefix
    pure $ if result < 0
      then Left ("could not inspect stale Door indexes: " <> Text.pack (show result))
      else Right (result == 1)

-- | Run Git on the reserved index, proving the index is still owned on each
-- side of the child and journaling a replacement inode before returning.
runIndexGit :: Executable -> FilePath -> Fd -> Fd -> IndexReservation -> [String]
            -> IO (Either Text Text)
runIndexGit executable rootPath rootFd gitFd reservation arguments = runExceptT $ do
  ExceptT (verifyIndexReservation gitFd reservation)
  result <- lift $ runGit executable rootPath rootFd gitFd (Just (reservationName reservation)) arguments
  ExceptT (verifyIndexReservation gitFd reservation)
  ExceptT (persistIndexJournal gitFd reservation)
  except result

persistIndexJournal :: Fd -> IndexReservation -> IO (Either Text ())
persistIndexJournal gitFd reservation = do
  writer <- readIORef (reservationJournal reservation)
  case writer of
    Nothing -> pure (Right ())
    Just journalRef -> do
      (device, inode) <- ownedIndexIdentity <$> readIORef (reservationOwned reservation)
      journal <- readIORef journalRef
      let updated = journal { journalIndex = JournalIndex (reservationName reservation) device inode }
      if updated == journal
        then pure (Right ())
        else runExceptT $ do
          ExceptT (replaceOperationJournal gitFd journal updated)
          lift (writeIORef journalRef updated)

verifyIndexReservation :: Fd -> IndexReservation -> IO (Either Text ())
verifyIndexReservation gitFd reservation = do
  OwnedIndex previousFd previousIdentity <- readIORef (reservationOwned reservation)
  opened <- Exception.try $ openFdAt (Just gitFd) (reservationName reservation) ReadOnly
    defaultFileFlags { nofollow = True, nonBlock = True, cloexec = True }
    :: IO (Either Exception.IOException Fd)
  case opened of
    Left problem -> pure (Left ("Door index identity is unavailable: " <> Text.pack (show problem)))
    Right currentFd -> do
      inspected <- tryIO $ do
        current <- getFdStatus currentFd
        unlessRegular current
        let currentIdentity = (deviceID current, fileID current)
        if currentIdentity == previousIdentity
          then pure False
          else do
            previousStatus <- getFdStatus previousFd
            if linkCount previousStatus == 0
              then writeIORef (reservationOwned reservation) (OwnedIndex currentFd currentIdentity)
                >> closeFd previousFd >> pure True
              else failText "Door index changed without unlinking the owned index inode"
      case inspected of
        Left problem -> closeFd currentFd >> pure (Left problem)
        Right False -> closeFd currentFd >> pure (Right ())
        Right True -> pure (Right ())
  where
    unlessRegular status = if isRegularFile status
      then pure ()
      else failText "Door index path is not a regular file"

cleanupIndexReservation :: Fd -> IndexReservation -> IO (Either Text ())
cleanupIndexReservation gitFd reservation = do
  verified <- verifyIndexReservation gitFd reservation
  ownedFd <- ownedIndexFd <$> readIORef (reservationOwned reservation)
  case verified of
    Left problem -> do
      closeFd ownedFd
      pure (Left ("could not prove Door index ownership during cleanup: " <> problem))
    Right () -> do
      removed <- tryIO (unlinkAt gitFd (reservationName reservation))
      closeFd ownedFd
      pure removed

withNewIndexReservation :: Fd
                        -> (IndexReservation -> IO (Either Text (Either Text Text)))
                        -> IO (Either Text Text)
withNewIndexReservation gitFd action = Exception.mask $ \restore -> do
  reserved <- reserveIndex gitFd
  case reserved of
    Left problem -> pure (Left problem)
    Right reservation -> do
      outcome <- Exception.try (restore (action reservation))
        :: IO (Either Exception.SomeException (Either Text (Either Text Text)))
      cleaned <- cleanupIndexReservation gitFd reservation
      case outcome of
        Left exception -> Exception.throwIO exception
        Right (Left problem) -> pure $ case cleaned of
          Left cleanupProblem -> Left (problem <> "; cleanup also failed: " <> cleanupProblem)
          Right () -> Left problem
        Right (Right value) -> pure $ case cleaned of
          Left cleanupProblem -> appendCleanupResult value cleanupProblem
          Right () -> value

appendCleanupResult :: Either Text Text -> Text -> Either Text Text
appendCleanupResult result cleanupProblem = case result of
  Left problem -> Left (problem <> "; cleanup also failed: " <> cleanupProblem)
  Right _ -> Left ("Door committed but could not remove its owned index: " <> cleanupProblem)
