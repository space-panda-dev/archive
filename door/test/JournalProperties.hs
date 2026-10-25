{-# LANGUAGE OverloadedStrings #-}

-- | Properties of the operation journal's encoding
-- ("Archive.Door.RecordCommit.JournalFormat").
--
-- Recovery trusts a journal only when it re-encodes to its exact bytes, so
-- encoding then parsing must be the identity on every journal the writer can
-- produce, and a journal cut short must never read back as the journal that
-- was being written. The format has no trailer, so a cut at a line boundary
-- after the header can still parse, as a journal with fewer lines; what it
-- cannot do is parse as the original.
--
-- A cut journal cannot arise from Door's own writes: creation links the
-- journal's name to bytes already written and synchronized under a
-- temporary name, and replacement renames such bytes over it, so the name
-- never holds a partial journal (door-test's kill-recovery check confirms a
-- killed Door's journal parses and no temporary remains). These properties
-- pin down what a journal cut by anything else reads as.
module JournalProperties (journalProperties) where

import Archive.Door.Hex (hexText)
import Archive.Door.RecordCommit.JournalFormat
  ( JournalEntryOf (..)
  , JournalIndex (..)
  , OperationJournalOf (..)
  , PendingEntry
  , PendingJournal
  , encodeOperationJournal
  , gitObjectNameText
  , parseOperationJournal
  )
import qualified Data.ByteString as ByteString
import qualified Data.ByteString.Char8 as ByteStringChar8
import Data.List (nub)
import Data.Text (Text)
import qualified Data.Text as Text
import Data.Word (Word32)
import Test.Tasty (TestTree, testGroup)
import Test.Tasty.QuickCheck
  ( Gen
  , Property
  , arbitrary
  , choose
  , counterexample
  , elements
  , forAll
  , listOf1
  , oneof
  , resize
  , testProperty
  , vectorOf
  , (===)
  )

journalProperties :: TestTree
journalProperties = testGroup "journalProperties"
  [ testProperty "encoding then parsing gives back the journal" roundTrip
  , testProperty "a truncated journal never parses as the original" truncationNotOriginal
  , testProperty "a journal cut inside a line is refused" truncationInsideLine
  , testProperty "a journal cut inside its header is refused" truncationInsideHeader
  , testProperty "every proper prefix is refused unless it ends a line after a record entry" everyPrefix
  ]

-- | A Git object name in either format, as the parser requires.
genObjectName :: Gen Text
genObjectName = do
  size <- elements [20, 32]
  hexText . ByteString.pack <$> vectorOf size arbitrary

genDigestHex :: Gen String
genDigestHex = Text.unpack . hexText . ByteString.pack <$> vectorOf 32 arbitrary

-- | ADR 0053's digest-addressed record path.
recordPath :: String -> FilePath
recordPath hex = "records/" <> take 2 hex <> "/" <> take 2 (drop 2 hex) <> "/sha256-" <> hex <> ".json"

contentPath :: String -> FilePath
contentPath hex = "content/sha256/" <> take 2 hex <> "/" <> take 2 (drop 2 hex) <> "/" <> hex

-- | A suffix the temporary and index-reservation names allow.
genSuffix :: Gen String
genSuffix = resize 12 (listOf1 (elements ("0123456789abcdefxyz-" :: String)))

genIdentifier :: Integral a => Gen a
genIdentifier = fromIntegral <$> (arbitrary :: Gen Word32)

-- | Only journals the writer could write: unique paths and temporary names,
-- and record ownership fields that agree with the phase.
genJournal :: Gen PendingJournal
genJournal = do
  installing <- arbitrary
  oldHead <- genObjectName
  branch <- ("refs/heads/" <>) . Text.pack <$> resize 10 (listOf1 (elements ("main-/é0" :: String)))
  target <- if installing then oneof [pure Nothing, Just <$> genObjectName] else pure Nothing
  index <- JournalIndex <$> (("archive-door-index-" <>) <$> genSuffix) <*> genIdentifier <*> genIdentifier
  recordDigests <- nub <$> (choose (1, 4) >>= (`vectorOf` genDigestHex))
  temporaries <- nub <$> vectorOf (length recordDigests) ((".archive-door-record-" <>) <$> genSuffix)
  entries <- mapM (genEntry installing) (zip recordDigests temporaries)
  content <- uniquePairs . map contentPath <$> (choose (0, 3) >>= (`vectorOf` genDigestHex))
  contentPairs <- mapM (\path -> (,) path <$> genObjectName) content
  files <- uniquePairs <$> (choose (0, 3) >>= (`vectorOf` genArchiveFile))
  filePairs <- mapM (\path -> (,) path <$> genObjectName) files
  pure OperationJournal
    { journalOldHead = oldHead
    , journalBranch = branch
    , journalPhase = if installing then "installing" else "preparing"
    , journalTargetCommit = target
    , journalIndex = index
    , journalEntries = entries
    , journalContent = contentPairs
    , journalFiles = filePairs
    }
  where
    uniquePairs = nub
    genArchiveFile = oneof
      [ pure "archive.json"
      , (\hex -> "contracts/sha256-" <> hex <> ".json") <$> genDigestHex
      ]

genEntry :: Bool -> (String, FilePath) -> Gen PendingEntry
genEntry installing (hex, temporary)
  | installing = do
      blob <- genObjectName
      device <- genIdentifier
      inode <- genIdentifier
      pure (JournalEntry (recordPath hex) temporary (Just blob) (Just device) (Just inode))
  | otherwise = pure (JournalEntry (recordPath hex) temporary Nothing Nothing Nothing)

-- | A generated journal and its bytes; generation never yields one the
-- encoder refuses, so a refusal here is a failure of the generator's claim.
withEncoded :: (PendingJournal -> ByteString.ByteString -> Property) -> Property
withEncoded check = forAll genJournal $ \journal ->
  case encodeOperationJournal journal of
    Left problem -> counterexample (show journal <> ": " <> Text.unpack problem) False
    Right bytes -> check journal bytes

parsedPending :: ByteString.ByteString -> Either Text PendingJournal
parsedPending = fmap (fmap gitObjectNameText) . parseOperationJournal

roundTrip :: Property
roundTrip = withEncoded $ \journal bytes -> parsedPending bytes === Right journal

truncationNotOriginal :: Property
truncationNotOriginal = withEncoded $ \journal bytes ->
  forAll (choose (0, ByteString.length bytes - 1)) $ \cut ->
    let prefix = ByteString.take cut bytes
    in counterexample (ByteStringChar8.unpack prefix) (parsedPending prefix /= Right journal)

truncationInsideLine :: Property
truncationInsideLine = withEncoded $ \_ bytes ->
  forAll (choose (1, ByteString.length bytes - 1)) $ \cut ->
    let prefix = ByteString.take cut bytes
    in ByteStringChar8.last prefix == '\n' || isLeft (parsedPending prefix)

-- | The six header lines are all required, so any cut before the end of the
-- sixth line is refused whatever byte it falls on.
truncationInsideHeader :: Property
truncationInsideHeader = withEncoded $ \_ bytes ->
  let headerEnd = ByteString.length (ByteString.concat (take 6 (ByteStringChar8.lines bytes))) + 6
  in forAll (choose (0, headerEnd - 1)) $ \cut ->
       isLeft (parsedPending (ByteString.take cut bytes))

isLeft :: Either a b -> Bool
isLeft = either (const True) (const False)

-- | Every proper prefix, at every byte rather than a sampled one, is refused
-- unless it ends exactly at a line boundary after the first record entry,
-- and one that parses is a journal of fewer lines than the original.
everyPrefix :: Property
everyPrefix = withEncoded $ \_ bytes ->
  let lineCount = ByteStringChar8.count '\n' bytes
      verdict cut = case parsedPending prefix of
          Left _ -> True
          Right _ -> ByteStringChar8.last prefix == '\n'
            && ByteStringChar8.count '\n' prefix >= 7
            && ByteStringChar8.count '\n' prefix < lineCount
        where
          prefix = ByteString.take cut bytes
      failures = filter (not . verdict) [0 .. ByteString.length bytes - 1]
  in counterexample (show failures) (null failures)
