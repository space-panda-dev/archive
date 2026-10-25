{-# LANGUAGE OverloadedStrings #-}

-- | Reading files and immutable path history from the pinned commit.
--
-- Every reader returns an 'AdmissionSnapshotLoadError' that keeps a Git
-- failure apart from bytes that do not verify, because persistent admission
-- reports the two with different refusal codes.
module Archive.Door.Snapshot.Read
  ( readBytes
  , readJson
  , readCanonicalJson
  , decodeBytes
  , mapIntegrity
  , integrity
  , expectedRecordRevision
  , introducedAt
  , requireIntroducedAt
  , loadIntroducers
  , objectText
  , revisionText
  ) where

import Archive.Door.Json (decodeUnique)
import Archive.Door.Layout (digestText, parseRecordRelativePath)
import Archive.Door.Snapshot.Data
  ( AdmissionSnapshotLoadError (..)
  , RecordEntry (..)
  )
import Archive.Door.Snapshot.Problem (SnapshotProblem (..), snapshotCheck)
import Archive.Door.Types (ObjectId, RevisionId, objectIdText, parseRevisionId, revisionIdText)
import Archive.GitSnapshot (GitSnapshot)
import qualified Archive.GitSnapshot as Git
import Data.Aeson (Value)
import Data.Aeson.RFC8785 (encodeCanonical)
import qualified Data.ByteString.Lazy as LazyByteString
import qualified Data.Map.Strict as Map
import Data.Text (Text)

introducedAt :: Map.Map FilePath Text -> FilePath -> Either SnapshotProblem Text
introducedAt additions path = case Map.lookup path additions of
  Just commit -> Right commit
  Nothing -> Left (HistoryOmitted path)

requireIntroducedAt :: Map.Map FilePath Text -> Text -> FilePath -> Either SnapshotProblem ()
requireIntroducedAt additions expected path = do
  actual <- introducedAt additions path
  if actual == expected
    then Right ()
    else Left (NotIntroducedWithSpecification path)

loadIntroducers :: Map.Map FilePath Text -> [RecordEntry]
                -> Either SnapshotProblem (Map.Map RevisionId Text)
loadIntroducers additions entries =
  Map.fromList <$> mapM one entries
  where
    one entry = do
      commit <- introducedAt additions (entryPath entry)
      pure (entryRevision entry, commit)

-- | The revision a record path names. Layout's digest parser and
-- 'parseRevisionId' accept exactly the same spellings, so the second parse
-- cannot fail once the first has succeeded; it only gives the digest its
-- revision type.
expectedRecordRevision :: FilePath -> Either SnapshotProblem RevisionId
expectedRecordRevision path = snapshotCheck $ do
  digest <- parseRecordRelativePath path
  parseRevisionId (digestText digest)

readCanonicalJson
  :: GitSnapshot
  -> FilePath
  -> IO (Either AdmissionSnapshotLoadError Value)
readCanonicalJson snapshot path = do
  bytes <- readBytes snapshot path
  pure $ do
    raw <- bytes
    value <- integrity (decodeBytes path raw)
    if encodeCanonical value == raw
      then Right value
      else Left (AdmissionSnapshotIntegrityError (NotCanonical path))

readJson :: GitSnapshot -> FilePath -> IO (Either AdmissionSnapshotLoadError Value)
readJson snapshot path = do
  bytes <- readBytes snapshot path
  pure (bytes >>= integrity . decodeBytes path)

readBytes
  :: GitSnapshot
  -> FilePath
  -> IO (Either AdmissionSnapshotLoadError LazyByteString.ByteString)
readBytes snapshot path = do
  result <- Git.readPath snapshot path
  pure $ case result of
    Left problem -> Left (AdmissionSnapshotGitError problem)
    Right bytes -> Right bytes

-- | A snapshot problem, as an integrity failure of the snapshot.
integrity :: Either SnapshotProblem a -> Either AdmissionSnapshotLoadError a
integrity = either (Left . AdmissionSnapshotIntegrityError) Right

-- | Another module's 'Text' failure, as an integrity failure of the
-- snapshot with its message unchanged.
mapIntegrity :: Either Text a -> Either AdmissionSnapshotLoadError a
mapIntegrity = integrity . snapshotCheck

decodeBytes :: FilePath -> LazyByteString.ByteString -> Either SnapshotProblem Value
decodeBytes path bytes = case decodeUnique bytes of
  Left problem -> Left (InvalidJson path problem)
  Right value -> Right value

objectText :: ObjectId -> Text
objectText = objectIdText

revisionText :: RevisionId -> Text
revisionText = revisionIdText
