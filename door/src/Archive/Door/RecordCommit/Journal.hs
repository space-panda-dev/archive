{-# LANGUAGE OverloadedStrings #-}

-- | Door's operation journal: the one durable note of an unfinished write.
--
-- A batch publishes the journal before it creates any staging file and
-- replaces it, by a synchronized rename, at each phase change, so a killed
-- process leaves enough to prove what it owned. The encoding is canonical:
-- a journal that does not re-encode to its exact bytes is refused for
-- operator inspection instead of guessed at. That encoding lives in
-- "Archive.Door.RecordCommit.JournalFormat"; this module does the descriptor
-- work around it.
module Archive.Door.RecordCommit.Journal
  ( JournalEntry
  , JournalEntryOf (..)
  , JournalIdentity (..)
  , JournalIndex (..)
  , OperationJournal
  , OperationJournalOf (..)
  , PendingEntry
  , PendingJournal
  , clearJournalIfSame
  , createOperationJournal
  , operationJournalName
  , parseOperationJournal
  , readBoundedJournal
  , replaceOperationJournal
  ) where

import Archive.Door.RecordCommit.Descriptors
  ( ownerFileModes
  , renameAt
  , syncDirectoryFdOrFail
  , syncFd
  , syncStrongFdOrFail
  , unlinkAt
  , withDuplicatedBinaryHandle
  , writeAll
  )
import Archive.Door.RecordCommit.Failure (failText, orFail, require, tryIO, tryPresent)
import Archive.Door.RecordCommit.JournalFormat
  ( JournalEntry
  , JournalEntryOf (..)
  , JournalIndex (..)
  , OperationJournal
  , OperationJournalOf (..)
  , PendingEntry
  , PendingJournal
  , encodeOperationJournal
  , gitObjectNameText
  , operationJournalByteLimit
  , parseOperationJournal
  )
import qualified Control.Exception as Exception
import Control.Monad (unless)
import Control.Monad.Trans.Class (lift)
import Control.Monad.Trans.Except (ExceptT (..), except, runExceptT, throwE)
import qualified Data.ByteString as ByteString
import Data.Text (Text)
import Data.Unique (hashUnique, newUnique)
import System.IO (Handle)
import System.Posix.Files (deviceID, fileID, getFdStatus, isRegularFile)
import System.Posix.IO
  ( OpenFileFlags (cloexec, creat, exclusive, nofollow, nonBlock)
  , OpenMode (ReadOnly, WriteOnly)
  , closeFd
  , defaultFileFlags
  , openFdAt
  )
import System.Posix.Process (getProcessID)
import System.Posix.Types (DeviceID, Fd, FileID)

-- | The journal file's device and inode, which cleanup re-proves before it
-- unlinks.
data JournalIdentity = JournalIdentity DeviceID FileID deriving (Eq, Show)

operationJournalName :: FilePath
operationJournalName = "archive-door-operation"

createOperationJournal :: Fd -> PendingJournal -> IO (Either Text ())
createOperationJournal gitFd journal = runExceptT $ do
  bytes <- except (encodeOperationJournal journal)
  fd <- orFail ("could not reserve Door operation journal: " <>) $ tryIO $
    openFdAt (Just gitFd) operationJournalName WriteOnly
      defaultFileFlags { creat = Just ownerFileModes, exclusive = True, nofollow = True, cloexec = True }
  ExceptT $ Exception.bracket (pure fd) closeFd $ \journalFd ->
    tryIO (writeAll journalFd bytes >> syncStrongFdOrFail journalFd >> syncDirectoryFdOrFail gitFd)

-- | Replace the journal only while it still holds what this operation last
-- wrote: the replacement is written and synchronized under a fresh name,
-- renamed over the journal, and the rename synchronized.
replaceOperationJournal :: Fd -> PendingJournal -> PendingJournal -> IO (Either Text ())
replaceOperationJournal gitFd expected replacement = runExceptT $ do
  current <- ExceptT (readOperationJournal gitFd)
  case current of
    Nothing -> throwE "Door operation journal disappeared during mutation"
    Just (journal, _) -> require (fmap gitObjectNameText journal == expected)
      "Door operation journal changed during mutation"
  bytes <- except (encodeOperationJournal replacement)
  temporary <- lift freshJournalTemporaryName
  fd <- orFail ("could not create replacement operation journal: " <>) $ tryIO $
    openFdAt (Just gitFd) temporary WriteOnly
      defaultFileFlags { creat = Just ownerFileModes, exclusive = True, nofollow = True, cloexec = True }
  ExceptT $ Exception.bracket (pure fd) closeFd $ \temporaryFd -> runExceptT $ do
    ExceptT (tryIO (writeAll temporaryFd bytes >> syncStrongFdOrFail temporaryFd))
    ExceptT (tryIO (renameAt gitFd temporary operationJournalName))
    orFail ("could not sync operation journal directory: " <>) (syncFd gitFd)

-- | Remove the journal only while its contents and inode are still the ones
-- recovery reconciled, re-proving the inode immediately before the unlink.
clearJournalIfSame :: Fd -> JournalIdentity -> OperationJournal -> IO (Either Text ())
clearJournalIfSame gitFd identity expected = runExceptT $ do
  current <- ExceptT (readOperationJournal gitFd)
  case current of
    Nothing -> throwE "Door operation journal disappeared before cleanup"
    Just (journal, observedIdentity) -> do
      require (journal == expected) "Door operation journal changed before cleanup"
      require (observedIdentity == identity) "Door operation journal inode changed before cleanup"
  fd <- orFail ("Door operation journal path changed before cleanup: " <>) $ tryIO $
    openFdAt (Just gitFd) operationJournalName ReadOnly
      defaultFileFlags { nofollow = True, nonBlock = True, cloexec = True }
  ExceptT $ Exception.bracket (pure fd) closeFd $ \journalFd -> runExceptT $ do
    fileStatus <- ExceptT (tryIO (getFdStatus journalFd))
    require (JournalIdentity (deviceID fileStatus) (fileID fileStatus) == identity)
      "Door operation journal inode changed immediately before unlink"
    ExceptT (tryIO (unlinkAt gitFd operationJournalName))
    ExceptT (syncFd gitFd)

readOperationJournal :: Fd -> IO (Either Text (Maybe (OperationJournal, JournalIdentity)))
readOperationJournal gitFd = runExceptT $ do
  opened <- orFail ("could not open Door operation journal: " <>) $ tryPresent $
    openFdAt (Just gitFd) operationJournalName ReadOnly
      defaultFileFlags { nofollow = True, nonBlock = True, cloexec = True }
  case opened of
    Nothing -> pure Nothing
    Just fd -> ExceptT $ Exception.bracket (pure fd) closeFd $ \journalFd -> do
      result <- tryIO $ do
        status <- getFdStatus journalFd
        unless (isRegularFile status) (failText "Door operation journal is not regular")
        bytes <- withDuplicatedBinaryHandle journalFd readBoundedJournal
          >>= either failText pure
        pure (status, bytes)
      pure $ do
        (status, bytes) <- result
        journal <- parseOperationJournal bytes
        Right (Just (journal, JournalIdentity (deviceID status) (fileID status)))

readBoundedJournal :: Handle -> IO (Either Text ByteString.ByteString)
readBoundedJournal handle = collect [] 0
  where
    collect chunks total = do
      let remaining = operationJournalByteLimit + 1 - total
      chunk <- ByteString.hGetSome handle (min 32768 remaining)
      if ByteString.null chunk
        then pure (Right (ByteString.concat (reverse chunks)))
        else
          let nextTotal = total + ByteString.length chunk
          in if nextTotal > operationJournalByteLimit
            then pure (Left "Door operation journal exceeds the size limit")
            else collect (chunk : chunks) nextTotal

freshJournalTemporaryName :: IO FilePath
freshJournalTemporaryName = do
  processId <- getProcessID
  unique <- newUnique
  pure (".archive-door-journal-" <> show processId <> "-" <> show (hashUnique unique))
