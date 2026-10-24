{-# LANGUAGE OverloadedStrings #-}

-- | What a specification upgrade (ADR 0068, D-15) may adopt, and the files
-- it installs.
--
-- An upgrade moves an archive from its specification to a later, additive
-- release. The decision whether a release is additive is pure
-- ('additiveUpgrade') so it can be checked without an archive. The release
-- itself comes from this Door's own specification files, never from the
-- proposal: the catalog lists the bundles (ADR 0057), and the document,
-- version, and core context are the ones `archive init` copies.
module Archive.Door.SpecificationUpgrade
  ( upgradeContractId
  , upgradeContractVersion
  , SpecificationRelease (..)
  , additiveUpgrade
  , compareSpecifications
  , UpgradeTarget (..)
  , loadUpgradeTarget
  , specificationFilePaths
  ) where

import Archive.Door.Catalog (CatalogEntry, loadContractCatalog)
import Archive.Door.Contracts (ContractBundle, validateContractBundle, validateCoreContextForSpecification)
import Archive.Door.Hex (hexText)
import Archive.Door.Json (decodeUnique)
import Archive.Door.Layout (specVersion, specificationLayout)
import Control.Exception (IOException, try)
import qualified Crypto.Hash.SHA256 as SHA256
import Data.Aeson (Value (..))
import qualified Data.Aeson.Key as Key
import qualified Data.Aeson.KeyMap as KeyMap
import Data.Aeson.RFC8785 (encodeCanonical)
import qualified Data.ByteString.Lazy as LazyByteString
import qualified Data.Map.Strict as Map
import Data.Set (Set)
import qualified Data.Set as Set
import Data.Text (Text)
import qualified Data.Text as Text
import qualified Data.Text.Encoding as Text
import Paths_archive (getDataFileName)

upgradeContractId :: Text
upgradeContractId = "archive.transition/specification_upgrade"

upgradeContractVersion :: Text
upgradeContractVersion = "1"

-- | One release as the additive rule compares it: its catalog, its layout,
-- and the object inside its core context's @\@context@.
data SpecificationRelease = SpecificationRelease
  { releaseSpecification :: Text
  , releaseCatalog :: Set CatalogEntry
  , releaseLayout :: Text
  , releaseContext :: KeyMap.KeyMap Value
  }
  deriving (Eq, Show)

-- | ADR 0068: the target is additive when its catalog contains every bundle
-- of the current one with identical bytes (a bundle's digest names its
-- bytes), its layout is the current layout, and its core context defines
-- every term the current one defines with the same mapping. Anything else is
-- refused as unsupported: such a release needs its own migration decision.
additiveUpgrade :: SpecificationRelease -> SpecificationRelease -> Either Text ()
additiveUpgrade current target = do
  let name = releaseSpecification target
      missing = Set.toList (releaseCatalog current `Set.difference` releaseCatalog target)
  case missing of
    [] -> Right ()
    (identifier, version, digest) : _ -> Left ("unsupported: " <> name <> " is not additive: its catalog lacks "
      <> identifier <> " version " <> version <> " (" <> digest <> ")")
  let versions = Map.fromListWith (+) [((identifier, version), 1 :: Int) | (identifier, version, _) <- Set.toList (releaseCatalog target)]
  case [identifier <> " version " <> version | ((identifier, version), count) <- Map.toList versions, count > 1] of
    [] -> Right ()
    repeated : _ -> Left ("unsupported: " <> name <> " is not additive: its catalog gives "
      <> repeated <> " different bytes")
  if releaseLayout target == releaseLayout current
    then Right ()
    else Left ("unsupported: " <> name <> " is not additive: its layout " <> releaseLayout target
      <> " is not the archive's layout " <> releaseLayout current)
  case [ Key.toText term
       | (term, mapping) <- KeyMap.toList (releaseContext current)
       , KeyMap.lookup term (releaseContext target) /= Just mapping ] of
    [] -> Right ()
    terms -> Left ("unsupported: " <> name <> " is not additive: its core context drops or changes "
      <> Text.intercalate ", " terms)

-- | Order two specification identifiers by their numeric versions.
compareSpecifications :: Text -> Text -> Either Text Ordering
compareSpecifications left right = do
  leftVersion <- numeric left
  rightVersion <- numeric right
  pure (compare leftVersion rightVersion)
  where
    numeric specification = do
      version <- specVersion specification
      pure (map (read . Text.unpack) (Text.splitOn "." version) :: [Integer])

-- | A release this Door can install into an archive at @current@: the bundles
-- it adds, each verified against the target catalog, and the files the
-- upgrade commit writes, by archive-relative path.
data UpgradeTarget = UpgradeTarget
  { targetSpecification :: Text
  , targetAddedBundles :: [(CatalogEntry, ContractBundle)]
  , targetFiles :: [(FilePath, LazyByteString.ByteString)]
  }
  deriving (Eq, Show)

-- | The release a proposal names, read from this Door's specification files
-- and checked against the archive's current release. Every refusal names why
-- the upgrade is unsupported; none is an empty success.
loadUpgradeTarget :: SpecificationRelease -> Text -> IO (Either Text UpgradeTarget)
loadUpgradeTarget current to = case specVersion to of
  Left problem -> pure (Left ("unsupported: specification upgrade target: " <> problem))
  Right version -> do
    catalogResult <- loadContractCatalog to
    case catalogResult of
      Left _ -> pure (Left ("unsupported: unknown specification version " <> to
        <> ": this Door has no contract catalog for it"))
      Right catalog -> do
        contextResult <- readDataFile ("spec/" <> Text.unpack version <> "/context.jsonld")
        releasedResult <- readDataFile "spec/VERSION"
        documentResult <- readDataFile "spec/archive-spec.md"
        case (contextResult, releasedResult, documentResult) of
          (Left problem, _, _) -> pure (Left problem)
          (_, Left problem, _) -> pure (Left problem)
          (_, _, Left problem) -> pure (Left problem)
          (Right contextBytes, Right releasedBytes, Right documentBytes) -> do
            let prepared = do
                  layout <- either (Left . ("unsupported: " <>)) Right (specificationLayout to)
                  contextValue <- either (Left . (("core context of " <> to <> " is not JSON: ") <>) . Text.pack)
                    Right (decodeUnique contextBytes)
                  context <- innerContext contextValue
                  additiveUpgrade current SpecificationRelease
                    { releaseSpecification = to
                    , releaseCatalog = catalog
                    , releaseLayout = layout
                    , releaseContext = context
                    }
                  either (Left . (("core context of " <> to <> ": ") <>)) Right
                    (validateCoreContextForSpecification to contextValue)
                  released <- either (const (Left "spec/VERSION is not UTF-8")) Right
                    (Text.decodeUtf8' (LazyByteString.toStrict releasedBytes))
                  if Text.stripEnd released == version
                    then Right ()
                    else Left ("unsupported: this Door carries the specification text of archive/"
                      <> Text.stripEnd released <> " only, not " <> to)
                  if LazyByteString.null documentBytes
                    then Left "the specification document this Door carries is empty"
                    else Right ()
            case prepared of
              Left problem -> pure (Left problem)
              Right () -> do
                let added = Set.toList (catalog `Set.difference` releaseCatalog current)
                bundles <- mapM loadBundle added
                pure $ do
                  loaded <- sequence bundles
                  Right UpgradeTarget
                    { targetSpecification = to
                    , targetAddedBundles = [(entry, bundle) | (entry, bundle, _) <- loaded]
                    , targetFiles =
                        [ ("spec/" <> Text.unpack version <> "/VERSION", releasedBytes)
                        , ("spec/" <> Text.unpack version <> "/archive-spec.md", documentBytes)
                        , ("spec/" <> Text.unpack version <> "/context.jsonld", contextBytes)
                        ]
                        <> [(contractFilePath digest, bytes) | ((_, _, digest), _, bytes) <- loaded]
                    }

-- | The paths an upgrade to @specification@ adds for its documents.
specificationFilePaths :: Text -> Either Text [FilePath]
specificationFilePaths specification = do
  version <- specVersion specification
  pure [ "spec/" <> Text.unpack version <> "/" <> name | name <- ["VERSION", "archive-spec.md", "context.jsonld"] ]

innerContext :: Value -> Either Text (KeyMap.KeyMap Value)
innerContext (Object outer) = case KeyMap.lookup "@context" outer of
  Just (Object inner) -> Right inner
  _ -> Left "core context has no @context object"
innerContext _ = Left "core context must be a JSON object"

contractFilePath :: Text -> FilePath
contractFilePath digest = "contracts/sha256-" <> Text.unpack (Text.drop 7 digest) <> ".json"

-- | One bundle of the target catalog, by its digest path in this Door's
-- specification files: its bytes must be canonical, hash to the listed
-- digest, and carry the listed identifier and version.
loadBundle :: CatalogEntry -> IO (Either Text (CatalogEntry, ContractBundle, LazyByteString.ByteString))
loadBundle entry@(identifier, version, digest) = do
  result <- readDataFile ("spec/" <> contractFilePath digest)
  pure $ do
    bytes <- result
    value <- either (Left . (("bundle " <> digest <> " is not JSON: ") <>) . Text.pack) Right (decodeUnique bytes)
    if encodeCanonical value /= bytes
      then Left ("bundle " <> digest <> " is not RFC 8785 canonical")
      else Right ()
    if "sha256:" <> hexText (SHA256.hashlazy bytes) /= digest
      then Left ("bundle bytes do not match the catalog digest " <> digest)
      else Right ()
    bundle <- validateContractBundle identifier version digest value
    pure (entry, bundle, bytes)

readDataFile :: FilePath -> IO (Either Text LazyByteString.ByteString)
readDataFile relative = do
  path <- getDataFileName relative
  result <- try (LazyByteString.readFile path) :: IO (Either IOException LazyByteString.ByteString)
  pure $ case result of
    Left _ -> Left ("this Door cannot read its specification file " <> Text.pack relative)
    Right bytes -> Right (LazyByteString.fromStrict (LazyByteString.toStrict bytes))
