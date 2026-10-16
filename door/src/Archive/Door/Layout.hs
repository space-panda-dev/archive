{-# LANGUAGE OverloadedStrings #-}

-- | The only constructors for Archive's normative paths.
--
-- The root is caller-controlled, but every component below it is fixed or is
-- checked as a protocol identifier before it is appended.  No record or
-- content path is built from an object name, source path, or observed
-- filename.
module Archive.Door.Layout
  ( ArchiveManifest
  , ArchiveRoot
  , Sha256Digest
  , archiveManifestBytes
  , archiveManifestPath
  , archiveRoot
  , contentPath
  , contractPath
  , contractsDirectoryPath
  , digestText
  , makeArchiveManifest
  , manifestValue
  , manifestArchiveId
  , manifestLayout
  , manifestSpec
  , parseArchiveManifest
  , parseSha256Digest
  , recordPath
  , recordRelativePath
  , recordDirectoryChain
  , parseRecordRelativePath
  , contentRelativePath
  , parseContentRelativePath
  , currentLayout
  , recordsDirectoryPath
  , specContextPath
  , specDirectoryPath
  , specDocumentPath
  , specVersion
  , specVersionFilePath
  , validateArchiveManifest
  ) where

import Archive.Door.Identity (parseArchiveId)
import Data.Aeson (Value (Object, String), object, (.=))
import qualified Data.Aeson.Key as Key
import qualified Data.Aeson.KeyMap as KeyMap
import Data.Aeson.RFC8785 (encodeCanonical)
import qualified Data.ByteString.Lazy as LazyByteString
import Data.Char (isDigit)
import Data.List (sort)
import Data.Text (Text)
import qualified Data.Text as Text
import System.FilePath ((</>))

newtype ArchiveRoot = ArchiveRoot FilePath
  deriving (Eq, Ord, Show)

newtype Sha256Digest = Sha256Digest Text
  deriving (Eq, Ord, Show)

data ArchiveManifest = ArchiveManifest
  { manifestArchiveId :: Text
  , manifestLayout :: Text
  , manifestSpec :: Text
  } deriving (Eq, Show)

archiveRoot :: FilePath -> Either Text ArchiveRoot
archiveRoot value
  | null value = Left "archive root must not be empty"
  | '\0' `elem` value = Left "archive root must not contain NUL"
  | otherwise = Right (ArchiveRoot value)

archiveManifestPath :: ArchiveRoot -> FilePath
archiveManifestPath root = child root "archive.json"

specDirectoryPath :: ArchiveRoot -> Text -> Either Text FilePath
specDirectoryPath root version = do
  versionSegment <- specVersion version
  pure (child root "spec" </> Text.unpack versionSegment)

specVersionFilePath :: ArchiveRoot -> Text -> Either Text FilePath
specVersionFilePath root version = (</> "VERSION") <$> specDirectoryPath root version

specDocumentPath :: ArchiveRoot -> Text -> Either Text FilePath
specDocumentPath root version = (</> "archive-spec.md") <$> specDirectoryPath root version

specContextPath :: ArchiveRoot -> Text -> Either Text FilePath
specContextPath root version = (</> "context.jsonld") <$> specDirectoryPath root version

contractsDirectoryPath :: ArchiveRoot -> FilePath
contractsDirectoryPath root = child root "contracts"

recordsDirectoryPath :: ArchiveRoot -> FilePath
recordsDirectoryPath root = child root "records"

contractPath :: ArchiveRoot -> Sha256Digest -> FilePath
contractPath root digest = contractsDirectoryPath root </> "sha256-" <> Text.unpack (digestHex digest) <> ".json"

-- | The layout this build creates and supports (ADR 0053). Layout
-- @archive/1@, with flat @records/@ and @content/sha256/@ directories, is
-- refused: no archive that needs keeping was created with it.
currentLayout :: Text
currentLayout = "archive/2"

-- | The two fan-out directories of a digest: its first and second pairs of
-- hexadecimal characters.
fanOut :: Sha256Digest -> [FilePath]
fanOut digest = let hex = Text.unpack (digestHex digest) in [take 2 hex, take 2 (drop 2 hex)]

recordPath :: ArchiveRoot -> Sha256Digest -> FilePath
recordPath root digest = child root (recordRelativePath digest)

-- | @records/<h0h1>/<h2h3>/sha256-<digest>.json@, relative to the archive root.
recordRelativePath :: Sha256Digest -> FilePath
recordRelativePath digest = foldl (</>) "records" (fanOut digest)
  </> "sha256-" <> Text.unpack (digestHex digest) <> ".json"

-- | The directories between @records/@ and a record's file, outermost first.
recordDirectoryChain :: Sha256Digest -> [FilePath]
recordDirectoryChain = fanOut

-- | The digest named by an archive-relative record path, refusing any other
-- shape, including the flat @archive/1@ form.
parseRecordRelativePath :: FilePath -> Either Text Sha256Digest
parseRecordRelativePath path = case splitOn '/' path of
  ["records", first, second, name]
    | Just hex <- stripAffixes "sha256-" ".json" name -> do
        digest <- parseSha256Digest ("sha256:" <> Text.pack hex)
        if fanOut digest == [first, second]
          then Right digest
          else Left "record path fan-out does not match its digest"
  _ -> Left "record path must be records/<h0h1>/<h2h3>/sha256-<digest>.json"

contentPath :: ArchiveRoot -> Sha256Digest -> FilePath
contentPath root digest = child root (contentRelativePath digest)

-- | @content/sha256/<h0h1>/<h2h3>/<digest>@, relative to the archive root.
contentRelativePath :: Sha256Digest -> FilePath
contentRelativePath digest = foldl (</>) ("content" </> "sha256") (fanOut digest)
  </> Text.unpack (digestHex digest)

parseContentRelativePath :: FilePath -> Either Text Sha256Digest
parseContentRelativePath path = case splitOn '/' path of
  ["content", "sha256", first, second, hex] -> do
    digest <- parseSha256Digest ("sha256:" <> Text.pack hex)
    if fanOut digest == [first, second]
      then Right digest
      else Left "content path fan-out does not match its digest"
  _ -> Left "content path must be content/sha256/<h0h1>/<h2h3>/<digest>"

splitOn :: Char -> String -> [String]
splitOn separator value = case break (== separator) value of
  (part, []) -> [part]
  (part, _ : rest) -> part : splitOn separator rest

stripAffixes :: String -> String -> String -> Maybe String
stripAffixes prefix suffix value
  | take (length prefix) value == prefix
  , length value >= length prefix + length suffix
  , drop (length value - length suffix) value == suffix =
      Just (take (length value - length prefix - length suffix) (drop (length prefix) value))
  | otherwise = Nothing

parseSha256Digest :: Text -> Either Text Sha256Digest
parseSha256Digest value
  | Text.length value /= 71 = invalid
  | not (Text.isPrefixOf "sha256:" value) = invalid
  | not (Text.all isLowerHex (Text.drop 7 value)) = invalid
  | otherwise = Right (Sha256Digest value)
  where
    invalid = Left "digest must be sha256 followed by 64 lowercase hexadecimal characters"
    isLowerHex character = isDigit character || ('a' <= character && character <= 'f')

digestText :: Sha256Digest -> Text
digestText (Sha256Digest value) = value

digestHex :: Sha256Digest -> Text
digestHex = Text.drop 7 . digestText

makeArchiveManifest :: Text -> Text -> Either Text ArchiveManifest
makeArchiveManifest archiveId specification = do
  _ <- parseArchiveId archiveId
  _ <- specVersion specification
  pure ArchiveManifest
    { manifestArchiveId = archiveId
    , manifestLayout = currentLayout
    , manifestSpec = specification
    }

manifestValue :: ArchiveManifest -> Value
manifestValue manifest = object
  [ "archive_id" .= manifestArchiveId manifest
  , "layout" .= manifestLayout manifest
  , "spec" .= manifestSpec manifest
  ]

archiveManifestBytes :: ArchiveManifest -> LazyByteString.ByteString
archiveManifestBytes = encodeCanonical . manifestValue

validateArchiveManifest :: Value -> Either Text ()
validateArchiveManifest value = () <$ parseArchiveManifest value

parseArchiveManifest :: Value -> Either Text ArchiveManifest
parseArchiveManifest (Object fields) = do
  let extra = sort (filter (`notElem` ["archive_id", "layout", "spec"])
        (map Key.toText (KeyMap.keys fields)))
  case extra of
    [] -> pure ()
    names -> Left ("not allowed in archive.json: " <> Text.intercalate ", " names)
  archiveId <- requiredText "archive_id" fields
  _ <- parseArchiveId archiveId
  layout <- requiredText "layout" fields
  if layout == "archive/1"
    then Left "layout archive/1 is not supported by this build (ADR 0053); read it with an earlier Door or rebuild the archive"
    else if layout /= currentLayout
      then Left ("layout must be " <> currentLayout)
      else pure ()
  specification <- requiredText "spec" fields
  _ <- specVersion specification
  pure ArchiveManifest
    { manifestArchiveId = archiveId
    , manifestLayout = layout
    , manifestSpec = specification
    }
parseArchiveManifest _ = Left "archive.json must be a JSON object"

specVersion :: Text -> Either Text Text
specVersion value = case Text.stripPrefix "archive/" value of
  Just version
    | validVersion version -> Right version
  _ -> Left "spec must be archive/ followed by a three-part numeric version"
  where
    validVersion version =
      let components = Text.splitOn "." version
      in length components == 3
        && all (not . Text.null) components
        && Text.all (\character -> isDigit character || character == '.') version
        && all (Text.all isDigit) components

requiredText :: Text -> KeyMap.KeyMap Value -> Either Text Text
requiredText name fields = case KeyMap.lookup (Key.fromText name) fields of
  Nothing -> Left (name <> " is required")
  Just (String value)
    | Text.null value -> Left (name <> " must not be empty")
    | otherwise -> Right value
  Just _ -> Left (name <> " must be a string")

child :: ArchiveRoot -> FilePath -> FilePath
child (ArchiveRoot root) segment = root </> segment
