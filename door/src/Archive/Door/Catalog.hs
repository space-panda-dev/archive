{-# LANGUAGE OverloadedStrings #-}

-- | Each specification version's contract catalog (ADR 0057).
--
-- The specification distribution lists, in @spec/<version>/contracts.json@,
-- exactly which contract bundles that version installs. Door reads the list
-- from its installed data files and never keeps a copy in source (ADR 0027).
module Archive.Door.Catalog
  ( CatalogEntry
  , loadContractCatalog
  , parseContractCatalog
  ) where

import Archive.Door.Json (decodeUnique)
import Control.Exception (IOException, try)
import Data.Aeson (Value (..))
import qualified Data.Aeson.KeyMap as KeyMap
import qualified Data.ByteString.Lazy as LazyByteString
import qualified Data.Set as Set
import Data.Text (Text)
import qualified Data.Text as Text
import Paths_archive (getDataFileName)

-- | A contract identifier, version, and canonical digest.
type CatalogEntry = (Text, Text, Text)

-- | The catalog of @archive/<version>@. A specification without a catalog,
-- or a catalog Door cannot read, is a failure, never an empty set.
loadContractCatalog :: Text -> IO (Either Text (Set.Set CatalogEntry))
loadContractCatalog specification = case Text.stripPrefix "archive/" specification of
  Nothing -> pure (Left ("specification is not archive/<version>: " <> specification))
  Just version
    | Text.any (`notElem` ("0123456789." :: String)) version || Text.null version ->
        pure (Left ("specification version is malformed: " <> specification))
    | otherwise -> do
        path <- getDataFileName ("spec/" <> Text.unpack version <> "/contracts.json")
        bytes <- try (LazyByteString.readFile path) :: IO (Either IOException LazyByteString.ByteString)
        pure $ case bytes of
          Left _ -> Left ("no installed contract catalog for " <> specification)
          Right content -> parseContractCatalog specification content

parseContractCatalog :: Text -> LazyByteString.ByteString -> Either Text (Set.Set CatalogEntry)
parseContractCatalog specification bytes = do
  value <- either (Left . (("contract catalog for " <> specification <> " is not JSON: ") <>) . Text.pack) Right
    (decodeUnique bytes)
  fields <- object value
  if Set.fromList (KeyMap.keys fields) /= Set.fromList ["spec", "contracts"]
    then Left "contract catalog must be exactly {spec, contracts}"
    else pure ()
  if KeyMap.lookup "spec" fields /= Just (String specification)
    then Left ("contract catalog does not name " <> specification)
    else pure ()
  entries <- case KeyMap.lookup "contracts" fields of
    Just (Array items) | not (null items) -> mapM entry (foldr (:) [] items)
    _ -> Left "contract catalog must list at least one contract"
  let set = Set.fromList entries
  if Set.size set /= length entries
    then Left "contract catalog lists a contract twice"
    else Right set
  where
    object (Object fields) = Right fields
    object _ = Left "contract catalog entry must be an object"
    entry item = do
      fields <- object item
      if Set.fromList (KeyMap.keys fields) /= Set.fromList ["id", "version", "digest"]
        then Left "contract catalog entry must be exactly {id, version, digest}"
        else pure ()
      case (KeyMap.lookup "id" fields, KeyMap.lookup "version" fields, KeyMap.lookup "digest" fields) of
        (Just (String identifier), Just (String version), Just (String digest))
          | validDigest digest -> Right (identifier, version, digest)
        _ -> Left "contract catalog entry needs string id, version, and sha256 digest"
    validDigest digest = case Text.stripPrefix "sha256:" digest of
      Just hex -> Text.length hex == 64 && Text.all (`elem` ("0123456789abcdef" :: String)) hex
      Nothing -> False
