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
  , expectedRecordRevision
  , introducedAt
  , requireIntroducedAt
  , loadIntroducers
  , requireDistinct
  , objectText
  , revisionText
  ) where

import Archive.Door.Json (decodeUnique)
import Archive.Door.Layout (digestText, parseRecordRelativePath)
import Archive.Door.Snapshot.Data
  ( AdmissionSnapshotLoadError (..)
  , RecordEntry (..)
  )
import Archive.Door.Types (ObjectId, RevisionId, objectIdText, revisionIdText)
import Archive.GitSnapshot (GitSnapshot)
import qualified Archive.GitSnapshot as Git
import Data.Aeson (Value)
import Data.Aeson.RFC8785 (encodeCanonical)
import qualified Data.ByteString.Lazy as LazyByteString
import qualified Data.Map.Strict as Map
import qualified Data.Set as Set
import Data.Text (Text)
import qualified Data.Text as Text

introducedAt :: Map.Map FilePath Text -> FilePath -> Either Text Text
introducedAt additions path = case Map.lookup path additions of
  Just commit -> Right commit
  Nothing -> Left ("immutable history omitted " <> Text.pack path)

requireIntroducedAt :: Map.Map FilePath Text -> Text -> FilePath -> Either Text ()
requireIntroducedAt additions expected path = do
  actual <- introducedAt additions path
  if actual == expected
    then Right ()
    else Left (Text.pack path <> " was not introduced with the archive specification")

loadIntroducers :: Map.Map FilePath Text -> [RecordEntry]
                -> Either Text (Map.Map Text Text)
loadIntroducers additions entries =
  Map.fromList <$> mapM one entries
  where
    one entry = do
      commit <- introducedAt additions (entryPath entry)
      pure (entryRevision entry, commit)

expectedRecordRevision :: FilePath -> Either Text Text
expectedRecordRevision path = digestText <$> parseRecordRelativePath path

readCanonicalJson
  :: GitSnapshot
  -> FilePath
  -> IO (Either AdmissionSnapshotLoadError Value)
readCanonicalJson snapshot path = do
  bytes <- readBytes snapshot path
  pure $ do
    raw <- bytes
    value <- mapIntegrity (decodeBytes path raw)
    if encodeCanonical value == raw
      then Right value
      else Left (AdmissionSnapshotIntegrityError
        (Text.pack path <> " is not RFC 8785 canonical"))

readJson :: GitSnapshot -> FilePath -> IO (Either AdmissionSnapshotLoadError Value)
readJson snapshot path = do
  bytes <- readBytes snapshot path
  pure (bytes >>= mapIntegrity . decodeBytes path)

readBytes
  :: GitSnapshot
  -> FilePath
  -> IO (Either AdmissionSnapshotLoadError LazyByteString.ByteString)
readBytes snapshot path = do
  result <- Git.readPath snapshot path
  pure $ case result of
    Left problem -> Left (AdmissionSnapshotGitError problem)
    Right bytes -> Right bytes

mapIntegrity :: Either Text a -> Either AdmissionSnapshotLoadError a
mapIntegrity = either (Left . AdmissionSnapshotIntegrityError) Right

decodeBytes :: FilePath -> LazyByteString.ByteString -> Either Text Value
decodeBytes path bytes = case decodeUnique bytes of
  Left problem -> Left (Text.pack path <> ": invalid JSON: " <> Text.pack problem)
  Right value -> Right value

requireDistinct :: Text -> [Text] -> Either Text ()
requireDistinct label values
  | Set.size (Set.fromList values) == length values = Right ()
  | otherwise = Left (label <> " must be unique")

objectText :: ObjectId -> Text
objectText = objectIdText

revisionText :: RevisionId -> Text
revisionText = revisionIdText
