{-# LANGUAGE OverloadedStrings #-}

-- | Door's operation journal: the one durable note of an unfinished write.
--
-- A batch publishes the journal before it creates any staging file and
-- replaces it, by a synchronized rename, at each phase change, so a killed
-- process leaves enough to prove what it owned. The encoding is canonical:
-- a journal that does not re-encode to its exact bytes is refused for
-- operator inspection instead of guessed at.
module Archive.Door.RecordCommit.Journal
  ( JournalEntry (..)
  , JournalIdentity (..)
  , JournalIndex (..)
  , OperationJournal (..)
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
import Archive.Door.RecordCommit.Failure (failText, tryIO, unlessEither)
import Archive.Door.RecordCommit.Paths
  ( uniqueSorted
  , validateArchiveFilePath
  , validateContentPath
  , validateRecordPath
  )
import qualified Control.Exception as Exception
import Control.Monad (unless)
import qualified Data.ByteString as ByteString
import Data.Text (Text)
import qualified Data.Text as Text
import qualified Data.Text.Encoding as TextEncoding
import Data.Unique (hashUnique, newUnique)
import System.IO (Handle)
import System.IO.Error (isDoesNotExistError)
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
import Text.Read (readMaybe)

data OperationJournal = OperationJournal
  { journalOldHead :: Text
  , journalBranch :: Text
  , journalPhase :: Text
  , journalTargetCommit :: Maybe Text
  , journalIndex :: JournalIndex
  , journalEntries :: [JournalEntry]
  -- | Content locations this batch adds as git-annex symlink entries, with
  -- their exact link blobs. They have no worktree file before the commit, so
  -- recovery only verifies them in a committed tree.
  , journalContent :: [(FilePath, Text)]
  -- | Specification files a specification upgrade adds or replaces, with
  -- their exact blobs. Like content links they exist only in the temporary
  -- index before the commit, so recovery only verifies them in a committed
  -- tree.
  , journalFiles :: [(FilePath, Text)]
  } deriving (Eq, Show)

data JournalIndex = JournalIndex
  { journalIndexName :: FilePath
  , journalIndexDevice :: DeviceID
  , journalIndexFile :: FileID
  } deriving (Eq, Show)

data JournalEntry = JournalEntry
  { journalPath :: FilePath
  , journalTemporary :: FilePath
  , journalBlob :: Maybe Text
  , journalDevice :: Maybe DeviceID
  , journalFile :: Maybe FileID
  } deriving (Eq, Show)

data JournalIdentity = JournalIdentity DeviceID FileID deriving (Eq, Show)

operationJournalName :: FilePath
operationJournalName = "archive-door-operation"

operationJournalByteLimit :: Int
operationJournalByteLimit = 16 * 1024 * 1024

createOperationJournal :: Fd -> OperationJournal -> IO (Either Text ())
createOperationJournal gitFd journal = case encodeOperationJournal journal of
  Left problem -> pure (Left problem)
  Right bytes -> do
    opened <- Exception.try $ openFdAt (Just gitFd) operationJournalName WriteOnly
      defaultFileFlags { creat = Just ownerFileModes, exclusive = True, nofollow = True, cloexec = True }
      :: IO (Either Exception.IOException Fd)
    case opened of
      Left problem -> pure (Left ("could not reserve Door operation journal: " <> Text.pack (show problem)))
      Right fd -> Exception.bracket (pure fd) closeFd $ \journalFd -> do
        written <- tryIO (writeAll journalFd bytes >> syncStrongFdOrFail journalFd >> syncDirectoryFdOrFail gitFd)
        pure (written >> Right ())

replaceOperationJournal :: Fd -> OperationJournal -> OperationJournal -> IO (Either Text ())
replaceOperationJournal gitFd expected replacement = do
  current <- readOperationJournal gitFd
  case current of
    Left problem -> pure (Left problem)
    Right Nothing -> pure (Left "Door operation journal disappeared during mutation")
    Right (Just (journal, _))
      | journal /= expected -> pure (Left "Door operation journal changed during mutation")
      | otherwise -> case encodeOperationJournal replacement of
          Left problem -> pure (Left problem)
          Right bytes -> do
            temporary <- freshJournalTemporaryName
            opened <- Exception.try $ openFdAt (Just gitFd) temporary WriteOnly
              defaultFileFlags { creat = Just ownerFileModes, exclusive = True, nofollow = True, cloexec = True }
              :: IO (Either Exception.IOException Fd)
            case opened of
              Left problem -> pure (Left ("could not create replacement operation journal: " <> Text.pack (show problem)))
              Right fd -> Exception.bracket (pure fd) closeFd $ \temporaryFd -> do
                written <- tryIO (writeAll temporaryFd bytes >> syncStrongFdOrFail temporaryFd)
                case written of
                  Left problem -> pure (Left problem)
                  Right () -> do
                    renamed <- tryIO (renameAt gitFd temporary operationJournalName)
                    case renamed of
                      Left problem -> pure (Left problem)
                      Right () -> do
                        synced <- syncFd gitFd
                        pure $ case synced of
                          Left problem -> Left ("could not sync operation journal directory: " <> problem)
                          Right () -> Right ()

clearJournalIfSame :: Fd -> JournalIdentity -> OperationJournal -> IO (Either Text ())
clearJournalIfSame gitFd identity expected = do
  current <- readOperationJournal gitFd
  case current of
    Left problem -> pure (Left problem)
    Right Nothing -> pure (Left "Door operation journal disappeared before cleanup")
    Right (Just (journal, observedIdentity))
      | journal /= expected -> pure (Left "Door operation journal changed before cleanup")
      | observedIdentity /= identity -> pure (Left "Door operation journal inode changed before cleanup")
      | otherwise -> do
          opened <- Exception.try $ openFdAt (Just gitFd) operationJournalName ReadOnly
            defaultFileFlags { nofollow = True, nonBlock = True, cloexec = True }
            :: IO (Either Exception.IOException Fd)
          case opened of
            Left problem -> pure (Left ("Door operation journal path changed before cleanup: " <> Text.pack (show problem)))
            Right fd -> Exception.bracket (pure fd) closeFd $ \journalFd -> do
              status <- tryIO (getFdStatus journalFd)
              case status of
                Left problem -> pure (Left problem)
                Right fileStatus
                  | JournalIdentity (deviceID fileStatus) (fileID fileStatus) /= identity ->
                      pure (Left "Door operation journal inode changed immediately before unlink")
                  | otherwise -> do
                      removed <- tryIO (unlinkAt gitFd operationJournalName)
                      case removed of
                        Left problem -> pure (Left problem)
                        Right () -> syncFd gitFd

readOperationJournal :: Fd -> IO (Either Text (Maybe (OperationJournal, JournalIdentity)))
readOperationJournal gitFd = do
  opened <- Exception.try $ openFdAt (Just gitFd) operationJournalName ReadOnly
    defaultFileFlags { nofollow = True, nonBlock = True, cloexec = True }
    :: IO (Either Exception.IOException Fd)
  case opened of
    Left problem
      | isDoesNotExistError problem -> pure (Right Nothing)
      | otherwise -> pure (Left ("could not open Door operation journal: " <> Text.pack (show problem)))
    Right fd -> Exception.bracket (pure fd) closeFd $ \journalFd -> do
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

encodeOperationJournal :: OperationJournal -> Either Text ByteString.ByteString
encodeOperationJournal journal = do
  validateJournal journal
  let header =
        [ "archive-door-operation\t1"
        , "old\t" <> journalOldHead journal
        , "branch\t" <> journalBranch journal
        , "phase\t" <> journalPhase journal
        , "target\t" <> maybe "-" id (journalTargetCommit journal)
        , "index\t" <> Text.pack (journalIndexName (journalIndex journal))
          <> "\t" <> Text.pack (show (journalIndexDevice (journalIndex journal)))
          <> "\t" <> Text.pack (show (journalIndexFile (journalIndex journal)))
        ]
      entryLines = map encodeEntry (journalEntries journal)
        <> [ "content\t" <> Text.pack path <> "\t" <> blob | (path, blob) <- journalContent journal ]
        <> [ "file\t" <> Text.pack path <> "\t" <> blob | (path, blob) <- journalFiles journal ]
      encodeEntry entry = case (journalBlob entry, journalDevice entry, journalFile entry) of
        (Nothing, Nothing, Nothing) -> "entry-preparing\t" <> Text.pack (journalPath entry)
          <> "\t" <> Text.pack (journalTemporary entry)
        (Just blob, Just device, Just inode) -> "entry-ready\t" <> Text.pack (journalPath entry)
          <> "\t" <> blob <> "\t" <> Text.pack (show device) <> "\t"
          <> Text.pack (show inode) <> "\t" <> Text.pack (journalTemporary entry)
        _ -> "entry-invalid"
      body = Text.intercalate "\n" (header <> entryLines) <> "\n"
  let bytes = TextEncoding.encodeUtf8 body
  unlessEither (ByteString.length bytes <= operationJournalByteLimit)
    "Door operation journal exceeds the size limit"
  pure bytes

parseOperationJournal :: ByteString.ByteString -> Either Text OperationJournal
parseOperationJournal bytes = do
  decoded <- case TextEncoding.decodeUtf8' bytes of
    Left _ -> Left "journal is not UTF-8"
    Right value -> Right value
  unlessEither (Text.isSuffixOf "\n" decoded) "journal is missing its final newline"
  journal <- case Text.lines decoded of
    "archive-door-operation\t1" : oldLine : branchLine : phaseLine : targetLine : indexLine : entryLines -> do
      oldHead <- field "old" oldLine
      branch <- field "branch" branchLine
      phase <- field "phase" phaseLine
      targetText <- field "target" targetLine
      target <- if targetText == "-" then Right Nothing else Right (Just targetText)
      index <- case Text.splitOn "\t" indexLine of
        ["index", name, deviceText, inodeText] -> do
          device <- maybe (Left "journal index device identifier is invalid") Right (readMaybe (Text.unpack deviceText))
          inode <- maybe (Left "journal index inode identifier is invalid") Right (readMaybe (Text.unpack inodeText))
          Right (JournalIndex (Text.unpack name) device inode)
        _ -> Left "journal index ownership is malformed"
      let (recordLines, linkLines) = break (\line -> "content\t" `Text.isPrefixOf` line
            || "file\t" `Text.isPrefixOf` line) entryLines
          (contentLines, fileLines) = break ("file\t" `Text.isPrefixOf`) linkLines
      entries <- traverse parseEntry recordLines
      content <- traverse parseContent contentLines
      files <- traverse parseFile fileLines
      Right (OperationJournal oldHead branch phase target index entries content files)
    _ -> Left "journal header or field order is invalid"
  validateJournal journal
  case encodeOperationJournal journal of
    Left problem -> Left problem
    Right canonical | canonical /= bytes -> Left "journal encoding is not canonical"
    Right _ -> Right journal
  where
    field name line = case Text.splitOn "\t" line of
      [actual, value] | actual == name -> Right value
      _ -> Left ("journal field is malformed: " <> name)
    parseEntry line = case Text.splitOn "\t" line of
      ["entry-preparing", path, temporary] -> Right (JournalEntry
        (Text.unpack path) (Text.unpack temporary) Nothing Nothing Nothing)
      ["entry-ready", path, blob, deviceText, inodeText, temporary] -> do
        device <- maybe (Left "journal device identifier is invalid") Right (readMaybe (Text.unpack deviceText))
        inode <- maybe (Left "journal inode identifier is invalid") Right (readMaybe (Text.unpack inodeText))
        Right (JournalEntry (Text.unpack path) (Text.unpack temporary) (Just blob) (Just device) (Just inode))
      _ -> Left "journal record entry is malformed"
    parseContent line = case Text.splitOn "\t" line of
      ["content", path, blob] -> Right (Text.unpack path, blob)
      _ -> Left "journal content entry is malformed"
    parseFile line = case Text.splitOn "\t" line of
      ["file", path, blob] -> Right (Text.unpack path, blob)
      _ -> Left "journal specification file entry is malformed"

validateJournal :: OperationJournal -> Either Text ()
validateJournal journal = do
  unlessEither (validObjectId (journalOldHead journal)) "journal old HEAD is invalid"
  unlessEither ("refs/heads/" `Text.isPrefixOf` journalBranch journal
    && not (Text.any (`elem` ['\n', '\r', '\t', '\0']) (journalBranch journal)))
    "journal branch is invalid"
  unlessEither (journalPhase journal `elem` ["preparing", "installing"])
    "journal phase is unsupported"
  case journalTargetCommit journal of
    Nothing -> Right ()
    Just target -> unlessEither (validObjectId target && journalPhase journal == "installing")
      "journal target commit is invalid"
  unlessEither (isIndexReservationName (journalIndexName (journalIndex journal)))
    "journal index name is invalid"
  let entries = journalEntries journal
      paths = map journalPath entries
      temporaries = map journalTemporary entries
  unlessEither (not (null entries)) "journal has no record entries"
  unlessEither (length paths == length (uniqueSorted paths)) "journal has duplicate record paths"
  unlessEither (length temporaries == length (uniqueSorted temporaries)) "journal has duplicate temporary names"
  mapM_ validateEntry entries
  let contentPaths = map fst (journalContent journal)
  unlessEither (length contentPaths == length (uniqueSorted contentPaths)) "journal has duplicate content paths"
  mapM_ validateContent (journalContent journal)
  let filePaths = map fst (journalFiles journal)
  unlessEither (length filePaths == length (uniqueSorted filePaths)) "journal has duplicate specification file paths"
  mapM_ validateFile (journalFiles journal)
  where
    validateFile (path, blob) = do
      validateArchiveFilePath path
      unlessEither (validObjectId blob) "journal specification file blob identifier is invalid"
    validateContent (path, blob) = do
      _ <- validateContentPath path
      unlessEither (validObjectId blob) "journal content blob identifier is invalid"
    validateEntry entry = do
      _ <- validateRecordPath (journalPath entry)
      unlessEither (isRecordTemporaryName (journalTemporary entry)) "journal temporary name is invalid"
      case (journalPhase journal, journalBlob entry, journalDevice entry, journalFile entry) of
        ("preparing", Nothing, Nothing, Nothing) -> Right ()
        ("installing", Just blob, Just _, Just _) -> unlessEither (validObjectId blob) "journal blob identifier is invalid"
        _ -> Left "journal phase and record ownership fields disagree"

validObjectId :: Text -> Bool
validObjectId value = Text.length value `elem` [40, 64]
  && Text.all (\character -> ('0' <= character && character <= '9')
        || ('a' <= character && character <= 'f')) value

isRecordTemporaryName :: FilePath -> Bool
isRecordTemporaryName value = ".archive-door-record-" `prefixOf` value
  && let suffix = drop (length (".archive-door-record-" :: String)) value
     in not (null suffix) && all (\character -> character >= '0' && character <= '9'
          || character >= 'a' && character <= 'z' || character == '-') suffix
  && '/' `notElem` value
  where
    prefixOf prefix text = take (length prefix) text == prefix

isIndexReservationName :: FilePath -> Bool
isIndexReservationName value =
  "archive-door-index-" `prefixOf` value
    && let suffix = drop (length ("archive-door-index-" :: String)) value
       in not (null suffix) && all (\character -> character >= '0' && character <= '9'
            || character >= 'a' && character <= 'z' || character == '-') suffix
    && '/' `notElem` value
  where
    prefixOf prefix text = take (length prefix) text == prefix

freshJournalTemporaryName :: IO FilePath
freshJournalTemporaryName = do
  processId <- getProcessID
  unique <- newUnique
  pure (".archive-door-journal-" <> show processId <> "-" <> show (hashUnique unique))
