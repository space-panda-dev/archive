{-# LANGUAGE OverloadedStrings #-}

-- | The operation journal's bytes: its types, canonical encoding and parser.
--
-- Kept apart from the descriptor work in "Archive.Door.RecordCommit.Journal"
-- because it is pure: the parser refuses any journal that does not re-encode
-- to its exact bytes, and that round trip is what the property tests in
-- door-test exercise. The library exposes this module for them; it grants no
-- write capability, since every function here only builds or reads bytes.
module Archive.Door.RecordCommit.JournalFormat
  ( GitObjectName
  , JournalEntry
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
  ) where

import Archive.Door.RecordCommit.Failure (unlessEither)
import Archive.Door.RecordCommit.ObjectName (GitObjectName, gitObjectNameText, parseGitObjectName)
import Archive.Door.RecordCommit.Paths
  ( uniqueSorted
  , validateArchiveFilePath
  , validateContentPath
  , validateRecordPath
  )
import qualified Data.ByteString as ByteString
import Data.Text (Text)
import qualified Data.Text as Text
import qualified Data.Text.Encoding as TextEncoding
import System.Posix.Types (DeviceID, FileID)
import Text.Read (readMaybe)

-- | The journal over the type of its Git object names. The writer composes
-- a 'PendingJournal' from Git's output, whose names are still text that
-- encoding validates; reading parses a journal into an 'OperationJournal',
-- whose names recovery can hand to Git without checking them again.
data OperationJournalOf name = OperationJournal
  { journalOldHead :: name
  , journalBranch :: Text
  , journalPhase :: Text
  , journalTargetCommit :: Maybe name
  , journalIndex :: JournalIndex
  , journalEntries :: [JournalEntryOf name]
  -- | Content locations this batch adds as git-annex symlink entries, with
  -- their exact link blobs. They have no worktree file before the commit, so
  -- recovery only verifies them in a committed tree.
  , journalContent :: [(FilePath, name)]
  -- | Specification files a specification upgrade adds or replaces, with
  -- their exact blobs. Like content links they exist only in the temporary
  -- index before the commit, so recovery only verifies them in a committed
  -- tree.
  , journalFiles :: [(FilePath, name)]
  } deriving (Eq, Show, Functor)

type OperationJournal = OperationJournalOf GitObjectName

type PendingJournal = OperationJournalOf Text

data JournalIndex = JournalIndex
  { journalIndexName :: FilePath
  , journalIndexDevice :: DeviceID
  , journalIndexFile :: FileID
  } deriving (Eq, Show)

data JournalEntryOf name = JournalEntry
  { journalPath :: FilePath
  , journalTemporary :: FilePath
  , journalBlob :: Maybe name
  , journalDevice :: Maybe DeviceID
  , journalFile :: Maybe FileID
  } deriving (Eq, Show, Functor)

type JournalEntry = JournalEntryOf GitObjectName

type PendingEntry = JournalEntryOf Text


operationJournalByteLimit :: Int
operationJournalByteLimit = 16 * 1024 * 1024

encodeOperationJournal :: PendingJournal -> Either Text ByteString.ByteString
encodeOperationJournal journal = do
  _ <- validateJournal journal
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
  pending <- case Text.lines decoded of
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
  journal <- validateJournal pending
  case encodeOperationJournal pending of
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

-- | Every check a journal must pass, in the order its refusals are reported;
-- each Git object name is parsed at the point its check used to run, so the
-- first failing field still names the refusal.
validateJournal :: PendingJournal -> Either Text OperationJournal
validateJournal journal = do
  oldHead <- objectName "journal old HEAD is invalid" (journalOldHead journal)
  unlessEither ("refs/heads/" `Text.isPrefixOf` journalBranch journal
    && not (Text.any (`elem` ['\n', '\r', '\t', '\0']) (journalBranch journal)))
    "journal branch is invalid"
  unlessEither (journalPhase journal `elem` ["preparing", "installing"])
    "journal phase is unsupported"
  target <- traverse (\name -> if journalPhase journal == "installing"
      then objectName "journal target commit is invalid" name
      else Left "journal target commit is invalid")
    (journalTargetCommit journal)
  unlessEither (isIndexReservationName (journalIndexName (journalIndex journal)))
    "journal index name is invalid"
  let entries = journalEntries journal
      paths = map journalPath entries
      temporaries = map journalTemporary entries
  unlessEither (not (null entries)) "journal has no record entries"
  unlessEither (length paths == length (uniqueSorted paths)) "journal has duplicate record paths"
  unlessEither (length temporaries == length (uniqueSorted temporaries)) "journal has duplicate temporary names"
  validEntries <- mapM validateEntry entries
  let contentPaths = map fst (journalContent journal)
  unlessEither (length contentPaths == length (uniqueSorted contentPaths)) "journal has duplicate content paths"
  content <- mapM validateContent (journalContent journal)
  let filePaths = map fst (journalFiles journal)
  unlessEither (length filePaths == length (uniqueSorted filePaths)) "journal has duplicate specification file paths"
  files <- mapM validateFile (journalFiles journal)
  Right journal
    { journalOldHead = oldHead
    , journalTargetCommit = target
    , journalEntries = validEntries
    , journalContent = content
    , journalFiles = files
    }
  where
    objectName problem = maybe (Left problem) Right . parseGitObjectName
    validateFile (path, blob) = do
      validateArchiveFilePath path
      (,) path <$> objectName "journal specification file blob identifier is invalid" blob
    validateContent (path, blob) = do
      _ <- validateContentPath path
      (,) path <$> objectName "journal content blob identifier is invalid" blob
    validateEntry entry = do
      _ <- validateRecordPath (journalPath entry)
      unlessEither (isRecordTemporaryName (journalTemporary entry)) "journal temporary name is invalid"
      blob <- case (journalPhase journal, journalBlob entry, journalDevice entry, journalFile entry) of
        ("preparing", Nothing, Nothing, Nothing) -> Right Nothing
        ("installing", Just blob, Just _, Just _) -> Just <$> objectName "journal blob identifier is invalid" blob
        _ -> Left "journal phase and record ownership fields disagree"
      Right entry { journalBlob = blob }

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
