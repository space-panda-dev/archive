{-# LANGUAGE OverloadedStrings #-}

-- | The bootstrap ownership journal: proof that init made an archive's files.
--
-- Init writes the journal, durably, before its first archive file, and
-- removes it only after the initialization commit is durable. A later init
-- that finds it can finish or verify an interrupted initialization instead
-- of refusing a root it cannot otherwise tell from a stranger's.
module Archive.Door.Bootstrap.Journal
  ( BootstrapJournal (..)
  , bootstrapJournalName
  , writeBootstrapJournal
  , removeBootstrapJournal
  , readBootstrapJournal
  , verifyBootstrapJournal
  , removeBootstrapJournalAt
  ) where

import Archive.Door.Bootstrap.Abort (failIO, ioEither, safeClose, tryIO)
import Archive.Door.Bootstrap.Contents (InitResult (..), objectFields)
import Archive.Door.Bootstrap.Pinned
  ( ownerFileModes
  , rejectSymlink
  , removeCreatedPinned
  , safeRelativeComponents
  , syncDirectory
  , verifyRootStillPinned
  )
import Archive.Door.Bootstrap.Tracked
  ( PathTracker
  , TrackedPath (..)
  , newPathTracker
  , track
  , untrack
  )
import Archive.Door.Hex (hexText)
import Archive.Door.Identity (parseArchiveId)
import Archive.Door.Json (decodeUnique)
import Archive.Door.Json.Access (requiredText)
import Archive.Door.Types (parseObjectId)
import qualified Crypto.Hash.SHA256 as SHA256
import Control.Monad (unless, when)
import qualified Control.Exception as Exception
import Data.Aeson (Value (..), object, (.=))
import qualified Data.Aeson.Key as Key
import qualified Data.Aeson.KeyMap as KeyMap
import Data.Aeson.RFC8785 (encodeCanonical)
import qualified Data.ByteString as ByteString
import qualified Data.ByteString.Lazy as LazyByteString
import Data.List (sort)
import qualified Data.Set as Set
import Data.Text (Text)
import qualified Data.Text as Text
import Foreign.C.Error (throwErrnoIfMinus1_)
import Foreign.C.Types (CInt (..))
import System.Directory (listDirectory)
import System.FilePath (isAbsolute, (</>), makeRelative, splitDirectories)
import System.IO (Handle, hClose, hFlush)
import System.IO.Error (isDoesNotExistError)
import System.Posix.Files (deviceID, fileID, getFdStatus, isRegularFile)
import System.Posix.IO
  ( OpenFileFlags (cloexec, creat, exclusive, nofollow, nonBlock)
  , OpenMode (ReadOnly, WriteOnly)
  , closeFd
  , defaultFileFlags
  , dup
  , fdToHandle
  , openFdAt
  )
import System.Posix.Types (Fd (..))

foreign import ccall safe "archive_fullsync_fd"
  c_fullsync :: CInt -> IO CInt

-- This marker is ownership evidence only under the trusted local-operator
-- model also used by RecordCommit journals: it is reserved exclusively after
-- observing an empty, pinned root, and recovery binds it to that root inode.
-- It is not authentication against a hostile same-uid process that can edit
-- the archive filesystem.
data BootstrapJournal = BootstrapJournal
  { bootstrapJournalRootDevice :: Text
  , bootstrapJournalRootFile :: Text
  , bootstrapJournalArchiveId :: Text
  , bootstrapJournalOperatorId :: Text
  , bootstrapJournalFiles :: [(FilePath, Text)]
  } deriving (Eq, Show)

bootstrapJournalName :: FilePath
bootstrapJournalName = ".archive-init-journal"

writeBootstrapJournal
  :: PathTracker -> Fd -> FilePath -> Text -> Text
  -> [(FilePath, LazyByteString.ByteString)] -> IO BootstrapJournal
writeBootstrapJournal tracker rootFd rootPath archiveId operatorId files = do
  verifyRootStillPinned rootPath rootFd
  entriesAtStart <- listDirectory rootPath
  unless (null entriesAtStart)
    (failIO "bootstrap ownership journal can only be created in an empty archive root")
  entries <- mapM journalEntry files
  status <- getFdStatus rootFd
  let journal = BootstrapJournal
        { bootstrapJournalRootDevice = Text.pack (show (deviceID status))
        , bootstrapJournalRootFile = Text.pack (show (fileID status))
        , bootstrapJournalArchiveId = archiveId
        , bootstrapJournalOperatorId = operatorId
        , bootstrapJournalFiles = sort entries
        }
      bytes = LazyByteString.toStrict (encodeCanonical (bootstrapJournalValue journal))
  when (ByteString.length bytes > bootstrapJournalByteLimit)
    (failIO "bootstrap ownership journal exceeds its size limit")
  opened <- openFdAt (Just rootFd) bootstrapJournalName WriteOnly
    defaultFileFlags
      { creat = Just ownerFileModes
      , exclusive = True
      , nofollow = True
      , cloexec = True
      }
  journalStatus <- getFdStatus opened
  track tracker
    (TrackedPath (rootPath </> bootstrapJournalName)
      (deviceID journalStatus) (fileID journalStatus))
  handle <- fdToHandle opened
  let writeAndSync = do
        ByteString.hPut handle bytes
        hFlush handle
        let Fd descriptor = opened
        throwErrnoIfMinus1_ "full fsync(bootstrap ownership journal)"
          (c_fullsync (fromIntegral descriptor))
  writeAndSync `Exception.onException` (do
      _ <- tryIO (safeClose handle)
      pure ())
  hClose handle
  syncDirectory rootFd "full fsync(bootstrap ownership journal directory)"
  verifyRootStillPinned rootPath rootFd
  pure journal
  where
    journalEntry (path, contents) = do
      let relative = makeRelative rootPath path
          bytes = LazyByteString.toStrict contents
      pure (relative, hexText (SHA256.hash bytes))

bootstrapJournalByteLimit :: Int
bootstrapJournalByteLimit = 1024 * 1024

bootstrapJournalValue :: BootstrapJournal -> Value
bootstrapJournalValue journal = object
  [ "format" .= ("archive-init/1" :: Text)
  , "root_device" .= bootstrapJournalRootDevice journal
  , "root_file" .= bootstrapJournalRootFile journal
  , "archive_id" .= bootstrapJournalArchiveId journal
  , "operator_id" .= bootstrapJournalOperatorId journal
  , "files" .= map fileValue (bootstrapJournalFiles journal)
  ]
  where
    fileValue (path, digest) = object ["path" .= path, "sha256" .= digest]

removeBootstrapJournal :: PathTracker -> Fd -> FilePath -> IO ()
removeBootstrapJournal tracker rootFd rootPath = do
  removeCreatedPinned tracker rootFd rootPath bootstrapJournalName
  untrack tracker (rootPath </> bootstrapJournalName)
  syncDirectory rootFd "full fsync(bootstrap journal removal)"

readBootstrapJournal :: FilePath -> Fd -> IO (Maybe (BootstrapJournal, TrackedPath))
readBootstrapJournal rootPath rootFd = do
  opened <- Exception.try $ openFdAt (Just rootFd) bootstrapJournalName ReadOnly
    defaultFileFlags { nofollow = True, nonBlock = True, cloexec = True }
    :: IO (Either Exception.IOException Fd)
  case opened of
    Left problem
      | isDoesNotExistError problem -> pure Nothing
      | otherwise -> Exception.throwIO problem
    Right fd -> Exception.bracket (pure fd) closeFd $ \journalFd -> do
      status <- getFdStatus journalFd
      unless (isRegularFile status) (failIO "bootstrap ownership journal is not a regular file")
      duplicate <- dup journalFd
      handle <- fdToHandle duplicate
      bytes <- Exception.bracket (pure handle) hClose readBoundedBootstrapJournal
      value <- case decodeUnique (LazyByteString.fromStrict bytes) of
        Left problem -> failIO ("bootstrap ownership journal is malformed: " <> Text.pack problem)
        Right decoded -> pure decoded
      unless (encodeCanonical value == LazyByteString.fromStrict bytes)
        (failIO "bootstrap ownership journal is not canonical")
      journal <- ioEither (parseBootstrapJournal value)
      pure (Just (journal, TrackedPath (rootPath </> bootstrapJournalName)
        (deviceID status) (fileID status)))

readBoundedBootstrapJournal :: Handle -> IO ByteString.ByteString
readBoundedBootstrapJournal handle = collect [] 0
  where
    collect chunks total = do
      chunk <- ByteString.hGetSome handle (min 65536 (bootstrapJournalByteLimit + 1 - total))
      if ByteString.null chunk
        then pure (ByteString.concat (reverse chunks))
        else do
          let total' = total + ByteString.length chunk
          when (total' > bootstrapJournalByteLimit)
            (failIO "bootstrap ownership journal exceeds its size limit")
          collect (chunk : chunks) total'

parseBootstrapJournal :: Value -> Either Text BootstrapJournal
parseBootstrapJournal value = do
  fields <- objectFields "bootstrap ownership journal" value
  exactKeys ["format", "root_device", "root_file", "archive_id", "operator_id", "files"] fields
  format <- requiredText "format" fields
  when (format /= "archive-init/1") (Left "unsupported bootstrap ownership journal format")
  rootDevice <- requiredText "root_device" fields
  rootFile <- requiredText "root_file" fields
  archiveId <- requiredText "archive_id" fields
  _ <- parseArchiveId archiveId
  operatorId <- requiredText "operator_id" fields
  _ <- parseObjectId operatorId
  fileValue <- maybe (Left "bootstrap journal files are required")
    Right (KeyMap.lookup "files" fields)
  files <- case fileValue of
    Array values -> mapM parseBootstrapJournalFile (foldr (:) [] values)
    _ -> Left "bootstrap journal files must be an array"
  let paths = map fst files
  when (null files || paths /= sort paths || length paths /= Set.size (Set.fromList paths))
    (Left "bootstrap journal paths are empty, duplicated, or unsorted")
  when (bootstrapJournalName `elem` paths)
    (Left "bootstrap journal cannot list itself as an archive file")
  pure BootstrapJournal
    { bootstrapJournalRootDevice = rootDevice
    , bootstrapJournalRootFile = rootFile
    , bootstrapJournalArchiveId = archiveId
    , bootstrapJournalOperatorId = operatorId
    , bootstrapJournalFiles = files
    }
  where
    parseBootstrapJournalFile entry = do
      fields <- objectFields "bootstrap journal file" entry
      exactKeys ["path", "sha256"] fields
      path <- requiredText "path" fields
      digest <- requiredText "sha256" fields
      when (Text.length digest /= 64 || Text.any (not . isLowerHex) digest)
        (Left "bootstrap journal file digest is malformed")
      when (isAbsolute (Text.unpack path)
        || any (== "..") (splitDirectories (Text.unpack path)))
        (Left "bootstrap journal path escapes the archive root")
      pure (Text.unpack path, digest)
    isLowerHex character =
      (character >= '0' && character <= '9') || (character >= 'a' && character <= 'f')
    exactKeys expected fields =
      let actual = sort (map Key.toText (KeyMap.keys fields))
      in when (actual /= sort expected) (Left "bootstrap ownership journal has unknown or missing fields")

verifyBootstrapJournal
  :: BootstrapJournal -> Fd -> FilePath -> Text -> InitResult -> [FilePath] -> IO ()
verifyBootstrapJournal journal rootFd rootPath requestedOperator result expectedPaths = do
  status <- getFdStatus rootFd
  unless (bootstrapJournalRootDevice journal == Text.pack (show (deviceID status))
      && bootstrapJournalRootFile journal == Text.pack (show (fileID status)))
    (failIO "bootstrap ownership journal belongs to a different archive root")
  unless (bootstrapJournalArchiveId journal == initArchiveId result
      && bootstrapJournalOperatorId journal == initOperatorId result)
    (failIO "bootstrap ownership journal does not match the initialized archive")
  unless (bootstrapJournalOperatorId journal == requestedOperator)
    (failIO "requested operator differs from the bootstrap ownership journal")
  let journalPaths = map fst (bootstrapJournalFiles journal)
  unless (journalPaths == expectedPaths)
    (failIO "bootstrap ownership journal file list differs from the complete initialized archive")
  actualDigests <- mapM digestInstalledFile (bootstrapJournalFiles journal)
  unless (actualDigests == map snd (bootstrapJournalFiles journal))
    (failIO "an initialized archive file differs from its bootstrap ownership journal")
  where
    digestInstalledFile (relative, _) = do
      _ <- safeRelativeComponents relative
      let path = rootPath </> relative
      rejectSymlink "bootstrap file" path
      contents <- LazyByteString.readFile path
      verifyRootStillPinned rootPath rootFd
      pure (hexText (SHA256.hash (LazyByteString.toStrict contents)))

removeBootstrapJournalAt :: FilePath -> Fd -> TrackedPath -> IO ()
removeBootstrapJournalAt rootPath rootFd tracked = do
  tracker <- newPathTracker [tracked]
  removeCreatedPinned tracker rootFd rootPath bootstrapJournalName
  syncDirectory rootFd "full fsync(bootstrap journal removal)"
