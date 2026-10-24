{-# LANGUAGE LambdaCase #-}
{-# LANGUAGE OverloadedStrings #-}

-- | The checks a full read makes of a snapshot's immutable history.
--
-- Paths are only ever added, except the manifest, which only a
-- specification upgrade changes (ADR 0068). These checks prove that from the
-- commit's own history and Door's specification files, so a snapshot that
-- passes holds exactly what its specification and its upgrades installed.
module Archive.Door.Snapshot.History
  ( validateResources
  , validateInstalledCatalog
  , validateUpgradeHistory
  , validateCitationOrder
  , validateImmutablePaths
  ) where

import Archive.Door.Catalog (CatalogEntry, loadContractCatalog)
import Archive.Door.Contracts (validateCoreContextForSpecification)
import Archive.Door.Layout
  ( ArchiveManifest
  , manifestArchiveId
  , manifestLayout
  , manifestSpec
  , parseArchiveManifest
  , specVersion
  , specificationLayout
  )
import Archive.Door.Snapshot.Data
  ( AdmissionSnapshotLoadError (..)
  , Installed (..)
  , RecordEntry (..)
  )
import Archive.Door.Snapshot.Read
  ( decodeBytes
  , introducedAt
  , mapIntegrity
  , readBytes
  , readJson
  , requireDistinct
  , requireIntroducedAt
  )
import Archive.Door.SpecificationUpgrade
  ( SpecificationRelease (..)
  , additiveUpgrade
  , compareSpecifications
  , specificationFilePaths
  , upgradeContractId
  , upgradeContractVersion
  )
import Archive.Door.Types
  ( ContractCitation (..)
  , DraftEnvelope (envelopeSpec)
  , RecordBody (..)
  , StoredEnvelope (..)
  , envelopeBody
  , envelopeContracts
  )
import Archive.GitSnapshot (GitSnapshot)
import qualified Archive.GitSnapshot as Git
import Data.Aeson (Value (Object, String))
import qualified Data.Aeson.KeyMap as KeyMap
import Data.Aeson.RFC8785 (encodeCanonical)
import qualified Data.ByteString.Lazy as LazyByteString
import qualified Data.Map.Strict as Map
import qualified Data.Set as Set
import Data.Text (Text)
import qualified Data.Text as Text
import qualified Data.Text.Encoding as Text

-- | The governing specification's three documents were added by the commit
-- that adopted it: the root commit, or the last specification upgrade.
validateResources
  :: GitSnapshot
  -> Map.Map FilePath Text
  -> [Text]
  -> Text
  -> IO (Either AdmissionSnapshotLoadError ())
validateResources snapshot additions upgradeCommits specification = do
  let version = case Text.stripPrefix "archive/" specification of
        Just value -> value
        Nothing -> ""
      versionPath = "spec/" <> Text.unpack version <> "/VERSION"
      documentPath = "spec/" <> Text.unpack version <> "/archive-spec.md"
      contextPath = "spec/" <> Text.unpack version <> "/context.jsonld"
  versionResult <- readBytes snapshot versionPath
  documentResult <- readBytes snapshot documentPath
  contextResult <- readJson snapshot contextPath
  pure $ do
    initialCommit <- mapIntegrity (introducedAt additions "archive.json")
    let adoptingCommit = last (initialCommit : upgradeCommits)
    mapM_ (mapIntegrity . requireIntroducedAt additions adoptingCommit)
      [versionPath, documentPath, contextPath]
    versionBytes <- versionResult
    actualVersion <- mapIntegrity (decodeUtf8 versionPath versionBytes)
    if Text.stripEnd actualVersion /= version || Text.null (Text.stripEnd actualVersion)
      then Left (AdmissionSnapshotIntegrityError "specification VERSION does not match archive.json")
      else pure ()
    documentBytes <- documentResult
    if LazyByteString.null documentBytes
      then Left (AdmissionSnapshotIntegrityError "copied specification is empty")
      else pure ()
    context <- contextResult
    mapIntegrity (validateCoreContextForSpecification specification context)

-- | Every installed bundle was added by the root commit or by a
-- specification upgrade ('validateUpgradeHistory' says which upgrade adds
-- which), and together they are exactly the governing release's catalog.
validateInstalledCatalog :: Map.Map FilePath Text -> [Text] -> Text -> [Installed]
                         -> IO (Either Text ())
validateInstalledCatalog additions upgradeCommits specification installed = do
  catalog <- loadContractCatalog specification
  pure $ do
    initialCommit <- introducedAt additions "archive.json"
    mapM_ (requireIntroducedAmong additions (initialCommit : upgradeCommits) . installedPath) installed
    expected <- catalog
    validateInstalledContexts expected installed
  where
    requireIntroducedAmong additions' commits path = do
      actual <- introducedAt additions' path
      if actual `elem` commits
        then Right ()
        else Left (Text.pack path <> " was not introduced with the archive specification")

-- | Prove that every change of the manifest is one specification upgrade
-- (ADR 0068, D-15). Each changing commit keeps the archive identifier and
-- layout and names a later specification; it introduces exactly one record,
-- an upgrade from the previous specification to the new one written under
-- the new one; and it adds exactly the new release's three documents and the
-- bundles its catalog adds to the previous one, which must be additive. The
-- root commit added exactly its own release's bundles, and no upgrade
-- record exists outside such a commit. A history that fails any of this is
-- refused as incomplete evidence, never reinterpreted.
validateUpgradeHistory
  :: GitSnapshot
  -> Map.Map FilePath Text
  -> [Text]
  -> ArchiveManifest
  -> [Installed]
  -> [RecordEntry]
  -> IO (Either AdmissionSnapshotLoadError ())
validateUpgradeHistory snapshot additions upgradeCommits current installed entries =
  case introducedAt additions "archive.json" of
    Left problem -> pure (Left (AdmissionSnapshotIntegrityError problem))
    Right initialCommit -> do
      -- Without an upgrade the manifest never changed, so the root commit's
      -- manifest is the current one and is not read again.
      manifests <- if null upgradeCommits
        then pure [Right current]
        else mapM manifestAt (initialCommit : upgradeCommits)
      case sequence manifests of
        Left problem -> pure (Left problem)
        Right chain -> do
          catalogs <- mapM (loadContractCatalog . manifestSpec) chain
          contexts <- mapM (contextAt . manifestSpec) chain
          pure $ do
            loaded <- mapIntegrity (sequence catalogs)
            held <- sequence contexts
            mapIntegrity $ do
              case reverse chain of
                final : _ | final == current -> Right ()
                _ -> Left "the last specification upgrade does not name the archive's specification"
              case loaded of
                rootCatalog : _ -> requireBundles initialCommit rootCatalog
                [] -> Left "archive history has no root manifest"
              mapM_ step (zip3 upgradeCommits (zip chain (drop 1 chain))
                (zip (zip loaded held) (drop 1 (zip loaded held))))
              let upgradeRecords = [entry | entry <- entries, citesUpgrade entry]
                  introducers = [Map.lookup (entryPath entry) additions | entry <- upgradeRecords]
              if length upgradeRecords == length upgradeCommits
                  && all (`elem` map Just upgradeCommits) introducers
                then Right ()
                else Left "a specification upgrade record exists outside a specification upgrade commit"
  where
    manifestAt commit = do
      bytes <- Git.readPathAt snapshot commit "archive.json"
      pure $ case bytes of
        Left problem -> Left (AdmissionSnapshotGitError problem)
        Right raw -> mapIntegrity $ do
          value <- decodeBytes "archive.json" raw
          if encodeCanonical value /= raw
            then Left ("archive.json at " <> commit <> " is not RFC 8785 canonical")
            else parseArchiveManifest value

    contextAt specification = case specVersion specification of
      Left problem -> pure (Left (AdmissionSnapshotIntegrityError problem))
      Right version -> do
        value <- readJson snapshot ("spec/" <> Text.unpack version <> "/context.jsonld")
        pure $ value >>= \case
          Object outer | Just (Object inner) <- KeyMap.lookup "@context" outer -> Right inner
          _ -> Left (AdmissionSnapshotIntegrityError
            ("the core context of " <> specification <> " has no @context object"))

    contractsAddedAt commit = Set.fromList
      [ installedKey item | item <- installed, Map.lookup (installedPath item) additions == Just commit ]
    pathsAddedAt prefix commit = Set.fromList
      [ path | (path, introducer) <- Map.toList additions, introducer == commit
      , (prefix <> "/") `Text.isPrefixOf` Text.pack path ]

    requireBundles commit expected =
      if contractsAddedAt commit == expected
        then Right ()
        else Left "a commit that adopted a specification did not add exactly the bundles its catalog adds"

    citesUpgrade entry = any (\citation -> contractId citation == upgradeContractId)
      (envelopeContracts (storedDraft (entryEnvelope entry)))

    step (commit, (previous, next), ((previousCatalog, previousContext), (nextCatalog, nextContext))) = do
      let from = manifestSpec previous
          to = manifestSpec next
      if manifestArchiveId next == manifestArchiveId previous && manifestLayout next == manifestLayout previous
        then Right ()
        else Left "a specification upgrade changed the archive identifier or layout"
      order <- compareSpecifications to from
      if order == GT then Right () else Left "a specification upgrade does not name a later specification"
      previousLayout <- specificationLayout from
      nextLayout <- specificationLayout to
      additiveUpgrade
        (SpecificationRelease from previousCatalog previousLayout previousContext)
        (SpecificationRelease to nextCatalog nextLayout nextContext)
      requireBundles commit (nextCatalog `Set.difference` previousCatalog)
      documents <- specificationFilePaths to
      if pathsAddedAt "spec" commit == Set.fromList documents
        then Right ()
        else Left "a specification upgrade commit did not add exactly its release's documents"
      case [entry | entry <- entries, Map.lookup (entryPath entry) additions == Just commit] of
        [entry] -> do
          let draft = storedDraft (entryEnvelope entry)
              keys = [ (contractId citation, contractVersion citation, contractDigest citation)
                     | citation <- envelopeContracts draft ]
          case keys of
            [(identifier, version, _)]
              | identifier == upgradeContractId && version == upgradeContractVersion -> Right ()
            _ -> Left "a specification upgrade commit's record is not one upgrade claim"
          case envelopeBody draft of
            ClaimRecord (Object payload)
              | KeyMap.lookup "from" payload == Just (String from)
              , KeyMap.lookup "to" payload == Just (String to)
              , envelopeSpec draft == to -> Right ()
            _ -> Left "a specification upgrade record does not name the manifest change it made"
        _ -> Left "a specification upgrade commit must introduce exactly one record"

-- | A record cites only bundles the archive held when it was committed: the
-- bundle's path was added by the record's commit or an earlier one. Likewise
-- a record written under a specification whose core context the archive
-- holds was committed with or after that context, so a specification
-- upgrade cannot make an earlier record readable after the fact (D-1,
-- ADR 0068). A record whose context the archive never held is left to the
-- checks that read it.
validateCitationOrder :: [Text] -> Map.Map FilePath Text -> [Installed] -> [RecordEntry] -> Either Text ()
validateCitationOrder commits additions installed entries = mapM_ check entries
  where
    position = Map.fromList (zip commits [0 :: Int ..])
    paths = Map.fromList [(installedKey item, installedPath item) | item <- installed]
    at path = Map.lookup path additions >>= (`Map.lookup` position)
    check entry = do
      contextHeld entry
      mapM_ (cited entry) (envelopeContracts (storedDraft (entryEnvelope entry)))
    contextHeld entry =
      let specification = envelopeSpec (storedDraft (entryEnvelope entry))
      in case Text.stripPrefix "archive/" specification of
        Just version
          | let path = "spec/" <> Text.unpack version <> "/context.jsonld"
          , Map.member path additions -> case (at path, at (entryPath entry)) of
              (Just context, Just record) | context <= record -> Right ()
              _ -> Left (Text.pack (entryPath entry) <> " is written under " <> specification
                <> " before the archive held its core context")
        _ -> Right ()
    cited entry citation =
      case Map.lookup (contractId citation, contractVersion citation, contractDigest citation) paths of
        Nothing -> Left ("contract is not installed at this snapshot: " <> contractId citation)
        Just path -> case (at path, at (entryPath entry)) of
          (Just bundle, Just record) | bundle <= record -> Right ()
          _ -> Left (Text.pack (entryPath entry) <> " cites " <> contractId citation
            <> " before the archive held it")

-- | The installed bundles must be exactly the specification's catalog
-- (ADR 0057), which Door reads from its own specification files.
validateInstalledContexts :: Set.Set CatalogEntry -> [Installed] -> Either Text ()
validateInstalledContexts expected installed = do
  requireDistinct "installed contract tuple"
    [Text.intercalate "\0" [identifier, version, digest]
    | item <- installed
    , let (identifier, version, digest) = installedKey item]
  requireDistinct "installed contract identifier and version"
    [Text.intercalate "\0" [identifier, version]
    | item <- installed
    , let (identifier, version, _) = installedKey item]
  if Set.fromList (map installedKey installed) == expected
    then Right ()
    else Left "installed contract catalog does not match the selected specification"

validateImmutablePaths
  :: GitSnapshot
  -> Map.Map FilePath Text
  -> [Installed]
  -> [RecordEntry]
  -> IO (Either AdmissionSnapshotLoadError ())
validateImmutablePaths snapshot additions installed entries = do
  specResult <- Git.listPaths snapshot "spec"
  pure $ do
    specPaths <- either (Left . AdmissionSnapshotGitError) Right specResult
    let current = Set.fromList
          ("archive.json" : specPaths
            <> map installedPath installed
            <> map entryPath entries)
    if current == Map.keysSet additions
      then Right ()
      else Left (AdmissionSnapshotIntegrityError
        "immutable archive path history does not match the committed tree")

decodeUtf8 :: FilePath -> LazyByteString.ByteString -> Either Text Text
decodeUtf8 path bytes = case Text.decodeUtf8' (LazyByteString.toStrict bytes) of
  Left _ -> Left (Text.pack path <> " is not valid UTF-8")
  Right value -> Right value
