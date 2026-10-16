{-# LANGUAGE LambdaCase #-}
{-# LANGUAGE OverloadedStrings #-}

-- | Read-only admission validation against one immutable Git snapshot.
--
-- This module never reads the worktree, writes an archive, invokes
-- git-annex, or resolves a second commit. The snapshot is the authority for
-- every manifest, contract, record, and introducer used by the dry run.
module Archive.Door.AdmissionDryRun
  ( SnapshotData (..)
  , RecordEntry (..)
  , AdmissionSnapshotLoadError (..)
  , AdmissionPlanningError (..)
  , admissionPlanningErrorMessage
  , dryRunAdmissionForCommit
  , dryRunAdmissionVerified
  , snapshotVerification
  , rememberCommitted
  , forgetSnapshots
  , recheckAdmissionForCommit
  , dryRunAdmission
  , loadSnapshot
  , validateProposalContracts
  ) where

import Archive.Door.AdmissionPlan
  ( AdmissionPlan (..)
  , AdmissionSnapshot (..)
  , planAdmission
  )
import Archive.Door.Canonical (canonicalizeDraftStrict)
import Archive.Door.Catalog (CatalogEntry, loadContractCatalog)
import Archive.Door.Refusal (PlanRefusal (..))
import Archive.Door.Contracts
  ( ContractBundle
      ( contractBundleContext
      , contractBundleDigest
      , contractBundleId
      , contractBundleSchema
      , contractBundleVersion
      )
  , ReferenceTargetRule (..)
  , SameSubjectRule (..)
  , TargetMeasureRule (..)
  , contractReferenceTargets
  , distinctTrackContentRule
  , sameSubjectRule
  , targetMeasureRule
  , validateContractBundle
  , validateContractInstance
  , validateCoreContextForSpecification
  )
import Archive.Door.Json (decodeUnique)
import Archive.Door.Layout
  ( ArchiveManifest
  , digestText
  , manifestArchiveId
  , manifestLayout
  , manifestSpec
  , parseArchiveManifest
  , parseRecordRelativePath
  , specVersion
  , specificationLayout
  )
import Archive.Door.SpecificationUpgrade
  ( SpecificationRelease (..)
  , additiveUpgrade
  , compareSpecifications
  , specificationFilePaths
  , upgradeContractId
  , upgradeContractVersion
  )
import Archive.Door.Proposal
  ( Proposal (..)
  , ProposalRecord (..)
  , recheckProposal
  )
import Archive.Door.References
  ( StoreIndex
  , emptyIndex
  , insertKnown
  , insertRecord
  , validateReferences
  )
import Archive.Door.Types
  ( ContractCitation (..)
  , DraftEnvelope (envelopeSpec)
  , ObjectId
  , objectIdText
  , Reference (..)
  , RecordBody (..)
  , RevisionId, revisionIdText
  , StoredEnvelope (..)
  , envelopeContracts
  , envelopeObject
  , envelopeBody
  , envelopeReferences
  , envelopeSupersedes
  , Shape (..)
  , parseObjectId
  , parseRevisionId
  , parseStoredEnvelope
  , storedDraft
  , storedRevision
  )
import Archive.Door.VerifiedCheckpoint
  ( RecordSummary (..)
  , VerifiedCheckpoint (..)
  , readCheckpoint
  , summarizeEnvelope
  )
import Archive.GitSnapshot
  ( GitSnapshot
  , GitSnapshotError
  , gitSnapshotErrorMessage
  )
import qualified Archive.GitSnapshot as Git
import Archive.Read.Core (CommittedRecord (..))
import Control.Monad (foldM_)
import qualified Crypto.Hash.SHA256 as SHA256
import Data.Aeson (Value (Array, Number, Object, String), object, (.=))
import Data.Foldable (toList)
import Data.Scientific (floatingOrInteger)
import qualified Data.Aeson.Key as Key
import qualified Data.Aeson.KeyMap as KeyMap
import qualified Data.ByteString as StrictByteString
import qualified Data.ByteString.Lazy as LazyByteString
import Data.Aeson.RFC8785 (encodeCanonical)
import qualified Data.Map.Strict as Map
import qualified Data.Set as Set
import Data.Text (Text)
import qualified Data.Text as Text
import qualified Data.Text.Encoding as Text
import System.IO.Unsafe (unsafeInterleaveIO, unsafePerformIO)
import Data.IORef (IORef, newIORef, readIORef, writeIORef)
import Data.List (sortOn)
import System.Directory (canonicalizePath)
import System.FilePath (splitDirectories)

data Installed = Installed
  { installedPath :: FilePath
  , installedKey :: (Text, Text, Text)
  , installedBundle :: ContractBundle
  }

data RecordEntry = RecordEntry
  { entryPath :: FilePath
  , entryRevision :: Text
  , entryObject :: Text
  , entryEnvelope :: StoredEnvelope
  , entryCommitted :: CommittedRecord
  }

data SnapshotData = SnapshotData
  { snapshotCommitId :: Text
  , snapshotManifestId :: Text
  , snapshotRecords :: [RecordEntry]
  , snapshotInstalled :: Map.Map (Text, Text, Text) ContractBundle
  , snapshotStore :: StoreIndex
  , snapshotHeadsByObject :: Map.Map Text Text
  , snapshotRevisionIntroducers :: Map.Map Text Text
  , snapshotCommitOrder :: [Text]
  , snapshotImmutableAdditions :: Map.Map FilePath Text
  , snapshotTransitionKeys :: Set.Set (Text, Text, Text)
  , snapshotRecordSummaries :: Map.Map FilePath RecordSummary
  , snapshotCheckpointChain :: Maybe Int
  , snapshotFromCheckpoint :: Bool
  -- ^ True when a checkpoint (ADR 0052), not a full read, vouched for this
  -- snapshot and the objects beneath it.
  }

-- | Preserve where a planning failure came from so persistent admission can
-- distinguish a refusal from an invalid committed snapshot or a Git failure.
data AdmissionPlanningError
  = AdmissionSnapshotFailure GitSnapshotError
  | AdmissionSnapshotIntegrityFailure Text
  | AdmissionProposalRejected Text
  | AdmissionProposalStale Text
  deriving (Eq, Show)

data AdmissionSnapshotLoadError
  = AdmissionSnapshotGitError GitSnapshotError
  | AdmissionSnapshotIntegrityError Text
  deriving (Eq, Show)

admissionPlanningErrorMessage :: AdmissionPlanningError -> Text
admissionPlanningErrorMessage problem = case problem of
  AdmissionSnapshotFailure issue -> snapshotError issue
  AdmissionSnapshotIntegrityFailure message -> message
  AdmissionProposalRejected message -> message
  AdmissionProposalStale message -> message

-- | Validate an already parsed proposal against exactly one committed HEAD.
-- The proposal is rechecked after all snapshot work and before success.
dryRunAdmission :: FilePath -> Proposal -> IO (Either Text AdmissionPlan)
dryRunAdmission root proposal = fmap (either (Left . admissionPlanningErrorMessage) Right)
  (dryRunAdmissionForCommit root proposal)

-- | Typed form used by persistent admission. The public dry-run command keeps
-- its established text diagnostics by rendering this result at its boundary.
dryRunAdmissionForCommit
  :: FilePath
  -> Proposal
  -> IO (Either AdmissionPlanningError AdmissionPlan)
dryRunAdmissionForCommit root proposal =
  fmap fst <$> dryRunAdmissionVerified root proposal

-- | Plan an admission and also return what was verified about the snapshot
-- it was planned against, so a commit on top of it can extend that
-- verification (ADR 0052).
dryRunAdmissionVerified
  :: FilePath
  -> Proposal
  -> IO (Either AdmissionPlanningError (AdmissionPlan, SnapshotData))
dryRunAdmissionVerified root proposal = do
  snapshotResult <- Git.resolveSnapshot root
  case snapshotResult of
    Left problem -> pure (Left (AdmissionSnapshotFailure problem))
    Right snapshot -> do
      loaded <- loadSnapshot snapshot
      case loaded of
        Left problem -> pure (Left (planningSnapshotError problem))
        Right data' -> do
          proposalContracts <- validateProposalContracts data' proposal
          case proposalContracts of
            Left problem -> pure (Left (AdmissionProposalRejected problem))
            Right () -> do
              let admissionSnapshot = AdmissionSnapshot
                    { snapshotArchiveId = snapshotManifestId data'
                    , snapshotHeads = snapshotHeadsByObject data'
                    , snapshotRevisions = Set.fromList
                        (Map.keys (snapshotRevisionIntroducers data'))
                    , snapshotRevisionBatches = snapshotRevisionIntroducers data'
                    , snapshotReferenceIndex = snapshotStore data'
                    , snapshotTransitionContracts = snapshotTransitionKeys data'
                    }
              case planAdmission admissionSnapshot proposal of
                Left (PlanRefused problem) -> pure (Left (AdmissionProposalRejected problem))
                Left (PlanStale problem) -> pure (Left (AdmissionProposalStale problem))
                Right plan -> do
                  rechecked <- recheckProposal proposal
                  pure $ case rechecked of
                    Left problem -> Left (AdmissionProposalRejected problem)
                    Right () -> Right
                      ( plan
                          { plannedAdmissionSnapshot = Git.snapshotCommit snapshot
                          , plannedGitExecutableIdentity = Git.snapshotExecutableIdentity snapshot
                          }
                      , data'
                      )

-- | What a snapshot says was verified about its commit, for a commit made on
-- top of it, and whether a checkpoint (not just a fresh read) vouches for the
-- objects beneath it.
snapshotVerification :: SnapshotData -> (VerifiedCheckpoint, Bool)
snapshotVerification data' =
  ( VerifiedCheckpoint
      { checkpointCommit = snapshotCommitId data'
      , checkpointAdditions = snapshotImmutableAdditions data'
      , checkpointHistory = snapshotCommitOrder data'
      , checkpointRecords = snapshotRecordSummaries data'
      , checkpointChain = snapshotCheckpointChain data'
      }
  , snapshotFromCheckpoint data'
  )

-- | The last snapshot this process loaded or extended, keyed by archive root
-- and commit (ADR 0054). A commit is immutable, so its snapshot never goes
-- stale; a long-running Door answers each request on an unchanged HEAD from
-- memory and extends the snapshot after its own commits. It is never written
-- anywhere and is lost with the process.
snapshotMemo :: IORef (Maybe (FilePath, Text, SnapshotData))
snapshotMemo = unsafePerformIO (newIORef Nothing)
{-# NOINLINE snapshotMemo #-}

forgetSnapshots :: IO ()
forgetSnapshots = writeIORef snapshotMemo Nothing

remember :: FilePath -> SnapshotData -> IO ()
remember root data' = writeIORef snapshotMemo (Just (root, snapshotCommitId data', data'))

-- | Extend the remembered snapshot with a batch this process just committed
-- on top of it. Every added record was checked by this batch's plan; the
-- extended snapshot is what a full read of the new commit would build.
rememberCommitted :: FilePath -> SnapshotData -> Text -> Maybe Int
                  -> [(FilePath, LazyByteString.ByteString)] -> IO ()
rememberCommitted rootPath base commit chain records = do
  canonical <- canonicalizePath rootPath
  case extendSnapshot base commit chain records of
    Left _ -> forgetSnapshots
    Right extended -> remember canonical extended

extendSnapshot :: SnapshotData -> Text -> Maybe Int -> [(FilePath, LazyByteString.ByteString)]
               -> Either AdmissionSnapshotLoadError SnapshotData
extendSnapshot base commit chain records = do
  added <- mapM entryFor records
  let store = foldl' (\index entry -> insertRecord (storedRevision (entryEnvelope entry))
        (storedDraft (entryEnvelope entry)) index) (snapshotStore base) added
      heads = foldl' (\current entry -> Map.insert (entryObject entry) (entryRevision entry) current)
        (snapshotHeadsByObject base) added
  pure base
    { snapshotCommitId = commit
    , snapshotRecords = mergeByPath (snapshotRecords base) (sortOn entryPath added)
    , snapshotStore = store
    , snapshotHeadsByObject = heads
    , snapshotRevisionIntroducers = foldl' (\current entry -> Map.insert (entryRevision entry) commit current)
        (snapshotRevisionIntroducers base) added
    , snapshotCommitOrder = snapshotCommitOrder base <> [commit]
    , snapshotImmutableAdditions = foldl' (\current entry -> Map.insert (entryPath entry) commit current)
        (snapshotImmutableAdditions base) added
    , snapshotRecordSummaries = foldl' (\current entry -> Map.insert (entryPath entry)
        (summarizeEnvelope (entryEnvelope entry)) current) (snapshotRecordSummaries base) added
    , snapshotCheckpointChain = chain
    , snapshotFromCheckpoint = True
    }
  where
    entryFor (path, bytes) = do
      revision <- mapIntegrity (expectedRecordRevision path)
      value <- mapIntegrity (decodeBytes path bytes)
      envelope <- mapIntegrity (parseStoredEnvelope value)
      if revisionText (storedRevision envelope) /= revision
        then Left (AdmissionSnapshotIntegrityError (Text.pack path <> " disagrees with embedded revision_id"))
        else Right RecordEntry
          { entryPath = path
          , entryRevision = revision
          , entryObject = objectText (envelopeObject (storedDraft envelope))
          , entryEnvelope = envelope
          , entryCommitted = CommittedRecord
              { committedPath = path
              , committedBytes = bytes
              , committedValue = value
              , committedEnvelope = envelope
              , committedContractsValid = True
              }
          }
    mergeByPath left [] = left
    mergeByPath [] right = right
    mergeByPath (l : ls) (r : rs)
      | entryPath l <= entryPath r = l : mergeByPath ls (r : rs)
      | otherwise = r : mergeByPath (l : ls) rs

-- | Plan again immediately before commit, under the same archive lock.
--
-- A plan is a function of the pinned commit's tree, the proposal's bytes, and
-- the Git executable: 'loadSnapshot' reads only from the pinned commit, which
-- is immutable. When HEAD still names the planned commit and the executable is
-- unchanged, re-reading the commit would rebuild the same snapshot and the
-- same plan, so only the proposal bytes are checked again. Any other HEAD gets
-- a full plan, compared by the caller as before.
recheckAdmissionForCommit
  :: FilePath
  -> Proposal
  -> AdmissionPlan
  -> IO (Either AdmissionPlanningError AdmissionPlan)
recheckAdmissionForCommit root proposal expected = do
  snapshotResult <- Git.resolveSnapshot root
  case snapshotResult of
    Left problem -> pure (Left (AdmissionSnapshotFailure problem))
    Right snapshot
      | Git.snapshotCommit snapshot == plannedAdmissionSnapshot expected
          && Git.snapshotExecutableIdentity snapshot == plannedGitExecutableIdentity expected -> do
          rechecked <- recheckProposal proposal
          pure $ case rechecked of
            Left problem -> Left (AdmissionProposalRejected problem)
            Right () -> Right expected
      | otherwise -> dryRunAdmissionForCommit root proposal

-- | Load the snapshot Door plans against. When this Door build has a
-- checkpoint for exactly this commit (ADR 0052), the records are read but not
-- checked again; any inconsistency between the checkpoint and the commit, or
-- any doubt, falls back to full verification.
loadSnapshot :: GitSnapshot -> IO (Either AdmissionSnapshotLoadError SnapshotData)
loadSnapshot snapshot = do
  memo <- readIORef snapshotMemo
  case memo of
    Just (root, commit, data')
      | root == Git.snapshotRoot snapshot && commit == Git.snapshotCommit snapshot -> pure (Right data')
    _ -> do
      loaded <- loadFromStore
      either (const (pure ())) (remember (Git.snapshotRoot snapshot)) loaded
      pure loaded
  where
    loadFromStore = do
      checkpoint <- readCheckpoint (Git.snapshotRoot snapshot)
      case checkpoint of
        Just verified | checkpointCommit verified == Git.snapshotCommit snapshot -> do
          trusted <- loadCheckpointedSnapshot snapshot verified
          case trusted of
            Right data' -> pure (Right data')
            Left _ -> loadVerifiedSnapshot snapshot
        _ -> loadVerifiedSnapshot snapshot

-- | Rebuild the planning index of a commit this Door build already verified.
-- Record and contract bytes still come from the pinned commit; the
-- canonical-form, revision, citation, reference, and history checks are not
-- repeated, because they held for this commit when the checkpoint was written.
loadCheckpointedSnapshot
  :: GitSnapshot -> VerifiedCheckpoint -> IO (Either AdmissionSnapshotLoadError SnapshotData)
loadCheckpointedSnapshot snapshot verified = do
  -- Record bytes are read only if planning parses a record (see
  -- 'loadTrustedRecord'); everything else is read now.
  prefetched <- Git.prefetchPaths snapshot ["archive.json", "spec", "contracts"]
  case prefetched of
    Left problem -> pure (Left (AdmissionSnapshotGitError problem))
    Right () -> do
      manifestResult <- readJson snapshot "archive.json"
      contracts <- loadContracts snapshot
      pathsResult <- Git.listPaths snapshot "records"
      specResult <- Git.listPaths snapshot "spec"
      case (manifestResult >>= mapIntegrity . parseArchiveManifest, contracts, (,) <$> pathsResult <*> specResult) of
        (Left problem, _, _) -> pure (Left problem)
        (_, Left problem, _) -> pure (Left problem)
        (_, _, Left problem) -> pure (Left (AdmissionSnapshotGitError problem))
        (Right manifest, Right installed, Right (paths, specPaths)) -> do
          loaded <- mapM (loadTrustedRecord snapshot (checkpointRecords verified)) paths
          pure $ do
            entries <- sequence loaded
            let additions = checkpointAdditions verified
                summaries = checkpointRecords verified
            if Map.keysSet additions /= Set.fromList
                ("archive.json" : specPaths <> map installedPath installed <> paths)
                || Map.keysSet summaries /= Set.fromList paths
              then Left (AdmissionSnapshotIntegrityError
                "checkpoint paths do not match the committed tree")
              else Right ()
            shaped <- mapM (\(entry, summary) -> (,) entry <$> summaryShapeOf summary)
              [(entry, summary) | entry <- entries, Just summary <- [Map.lookup (entryPath entry) summaries]]
            objects <- mapM (\(entry, _) -> mapIntegrity (parseObjectId (entryObject entry))) shaped
            revisions <- mapM (\(entry, _) -> mapIntegrity (parseRevisionId (entryRevision entry))) shaped
            let store = foldl' (\index ((_, shape), objectId, revision) -> insertKnown objectId revision shape index)
                  emptyIndex (zip3 shaped objects revisions)
                superseded = Set.fromList
                  [ (summaryObject summary, revision)
                  | summary <- Map.elems summaries
                  , revision <- summarySupersedes summary
                  ]
                byObject = Map.fromListWith (<>) [(entryObject entry, [entry]) | entry <- entries]
            heads <- mapIntegrity (deriveHeads byObject superseded)
            batches <- mapIntegrity (loadIntroducers additions entries)
            pure SnapshotData
              { snapshotCommitId = Git.snapshotCommit snapshot
              , snapshotManifestId = manifestArchiveId manifest
              , snapshotRecords = entries
              , snapshotInstalled = installedMap installed
              , snapshotStore = store
              , snapshotHeadsByObject = heads
              , snapshotRevisionIntroducers = batches
              , snapshotCommitOrder = checkpointHistory verified
              , snapshotImmutableAdditions = additions
              , snapshotTransitionKeys = transitionKeys installed
              , snapshotRecordSummaries = summaries
              , snapshotCheckpointChain = checkpointChain verified
              , snapshotFromCheckpoint = True
              }
  where
    summaryShapeOf summary = case summaryShape summary of
      "artifact" -> Right Artifact
      "entity" -> Right Entity
      "claim" -> Right Claim
      "assessment" -> Right Assessment
      _ -> Left (AdmissionSnapshotIntegrityError "checkpoint names an unknown record shape")

-- | A checkpointed record: its identity comes from the path and the
-- checkpoint; its envelope is parsed only if planning looks at it. The
-- checkpointed commit's records parsed when it was verified, so a failure
-- here means the object store no longer returns those bytes, and Door stops.
loadTrustedRecord
  :: GitSnapshot -> Map.Map FilePath RecordSummary -> FilePath
  -> IO (Either AdmissionSnapshotLoadError RecordEntry)
loadTrustedRecord snapshot summaries path = do
  -- Planning parses only the records a proposal references, so each record's
  -- bytes are read when first needed. A caller that needs every record
  -- prefetches them in bulk first.
  bytes <- unsafeInterleaveIO $ do
    result <- Git.readPath snapshot path
    either (\problem -> error ("door: checkpointed record " <> path <> " is unreadable: "
      <> show problem)) pure result
  pure $ do
    revision <- mapIntegrity (expectedRecordRevision path)
    summary <- maybe (Left (AdmissionSnapshotIntegrityError
      (Text.pack path <> " is missing from the checkpoint"))) Right (Map.lookup path summaries)
    let parsed = do
          value <- decodeBytes path bytes
          stored <- parseStoredEnvelope value
          pure (value, stored)
        trusted :: forall a. ((Value, StoredEnvelope) -> a) -> a
        trusted use = either (\problem -> error ("door: checkpointed record " <> path
          <> " no longer parses: " <> Text.unpack problem)) use parsed
        envelope = trusted snd
    Right RecordEntry
      { entryPath = path
      , entryRevision = revision
      , entryObject = summaryObject summary
      , entryEnvelope = envelope
      , entryCommitted = CommittedRecord
          { committedPath = path
          , committedBytes = bytes
          , committedValue = trusted fst
          , committedEnvelope = envelope
          , committedContractsValid = True
          }
      }

transitionKeys :: [Installed] -> Set.Set (Text, Text, Text)
transitionKeys installed = Set.fromList
  [ installedKey item
  | item <- installed
  , Text.isPrefixOf "archive.transition/" (firstKey (installedKey item))
  ]

loadVerifiedSnapshot :: GitSnapshot -> IO (Either AdmissionSnapshotLoadError SnapshotData)
loadVerifiedSnapshot snapshot = do
  -- Every committed file is checked below; read them all in bulk first, so
  -- the Git processes do not grow with the number of records.
  prefetched <- Git.prefetchPaths snapshot archivePaths
  -- Only the manifest may change after it is added, and only by a
  -- specification upgrade; 'validateUpgradeHistory' proves every change is
  -- one (ADR 0068).
  historyResult <- case prefetched of
    Left problem -> pure (Left problem)
    Right () -> Git.archivePathHistory snapshot archivePaths ["archive.json"]
  case historyResult of
    Left problem -> pure (Left (AdmissionSnapshotGitError problem))
    Right (additions, modifications) -> do
      let upgradeCommits = map snd modifications
      manifestResult <- readCanonicalJson snapshot "archive.json"
      case manifestResult >>= mapIntegrity . parseArchiveManifest of
        Left problem -> pure (Left problem)
        Right manifest -> do
          resources <- validateResources snapshot additions upgradeCommits (manifestSpec manifest)
          case resources of
            Left problem -> pure (Left problem)
            Right () -> do
              contracts <- loadContracts snapshot
              case contracts of
                Left problem -> pure (Left problem)
                Right installed -> do
                  catalogResult <- mapIntegrity <$> validateInstalledCatalog additions
                    upgradeCommits (manifestSpec manifest) installed
                  case catalogResult of
                    Left problem -> pure (Left problem)
                    Right () -> do
                      records <- loadRecords snapshot installed
                      case records of
                        Left problem -> pure (Left problem)
                        Right entries -> do
                          immutable <- validateImmutablePaths snapshot additions
                            installed entries
                          upgrades <- validateUpgradeHistory snapshot additions upgradeCommits
                            manifest installed entries
                          case immutable >> upgrades of
                            Left problem -> pure (Left problem)
                            Right () -> do
                              let indexed = indexRecords entries
                              case mapIntegrity indexed of
                                Left problem -> pure (Left problem)
                                Right (store, heads) -> do
                                  history <- Git.linearHistory snapshot
                                  pure $ do
                                    commits <- either (Left . AdmissionSnapshotGitError) Right history
                                    batches <- mapIntegrity (loadIntroducers additions entries)
                                    mapIntegrity (validateCitationOrder commits additions installed entries)
                                    pure SnapshotData
                                      { snapshotCommitId = Git.snapshotCommit snapshot
                                      , snapshotManifestId = manifestArchiveId manifest
                                      , snapshotRecords = entries
                                      , snapshotInstalled = installedMap installed
                                      , snapshotStore = store
                                      , snapshotHeadsByObject = heads
                                      , snapshotRevisionIntroducers = batches
                                      , snapshotCommitOrder = commits
                                      , snapshotImmutableAdditions = additions
                                      , snapshotTransitionKeys = transitionKeys installed
                                      , snapshotRecordSummaries = Map.fromList
                                          [ (entryPath entry, summarizeEnvelope (entryEnvelope entry))
                                          | entry <- entries ]
                                      , snapshotCheckpointChain = Nothing
                                      , snapshotFromCheckpoint = False
                                      }

archivePaths :: [FilePath]
archivePaths = ["archive.json", "spec", "contracts", "records"]

firstKey :: (Text, Text, Text) -> Text
firstKey (value, _, _) = value

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

loadContracts :: GitSnapshot -> IO (Either AdmissionSnapshotLoadError [Installed])
loadContracts snapshot = do
  pathsResult <- Git.listPaths snapshot "contracts"
  case pathsResult of
    Left problem -> pure (Left (AdmissionSnapshotGitError problem))
    Right paths -> do
      loaded <- mapM (loadContractPath snapshot) paths
      pure (sequence loaded)

loadContractPath
  :: GitSnapshot
  -> FilePath
  -> IO (Either AdmissionSnapshotLoadError Installed)
loadContractPath snapshot path = do
  bytesResult <- readBytes snapshot path
  pure $ do
    bytes <- bytesResult
    digest <- mapIntegrity (contractDigestFromPath path)
    value <- mapIntegrity (decodeBytes path bytes)
    if encodeCanonical value /= bytes
      then Left (AdmissionSnapshotIntegrityError
        (Text.pack path <> " is not RFC 8785 canonical"))
      else do
        let actualDigest = "sha256:" <> hashHex (SHA256.hashlazy bytes)
        if actualDigest /= digest
          then Left (AdmissionSnapshotIntegrityError
            (Text.pack path <> " does not match its digest path"))
          else do
            fields <- mapIntegrity (objectFields value)
            identifier <- mapIntegrity (requiredString "id" fields)
            version <- mapIntegrity (requiredString "version" fields)
            bundle <- mapIntegrity (validateContractBundle identifier version digest value)
            pure Installed
              { installedPath = path
              , installedKey = (identifier, version, digest)
              , installedBundle = bundle
              }

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

objectFields :: Value -> Either Text (KeyMap.KeyMap Value)
objectFields (Object fields) = Right fields
objectFields _ = Left "contract bundle must be an object"

installedMap :: [Installed] -> Map.Map (Text, Text, Text) ContractBundle
installedMap = Map.fromList . map (\item -> (installedKey item, installedBundle item))

loadRecords
  :: GitSnapshot
  -> [Installed]
  -> IO (Either AdmissionSnapshotLoadError [RecordEntry])
loadRecords snapshot installed = do
  pathsResult <- Git.listPaths snapshot "records"
  case pathsResult of
    Left problem -> pure (Left (AdmissionSnapshotGitError problem))
    Right paths -> do
      records <- mapM (loadRecord snapshot installed) paths
      pure (sequence records)

loadRecord
  :: GitSnapshot
  -> [Installed]
  -> FilePath
  -> IO (Either AdmissionSnapshotLoadError RecordEntry)
loadRecord snapshot installed path = do
  bytesResult <- readBytes snapshot path
  pure $ do
    bytes <- bytesResult
    expected <- mapIntegrity (expectedRecordRevision path)
    value <- mapIntegrity (decodeBytes path bytes)
    if encodeCanonical value /= bytes
      then Left (AdmissionSnapshotIntegrityError
        (Text.pack path <> " is not RFC 8785 canonical"))
      else do
        envelope <- mapIntegrity (parseStoredEnvelope value)
        let embedded = revisionText (storedRevision envelope)
        if embedded /= expected
          then Left (AdmissionSnapshotIntegrityError
            (Text.pack path <> " disagrees with embedded revision_id"))
          else do
            recomputed <- mapIntegrity (recomputeRevision value)
            if recomputed /= embedded
              then Left (AdmissionSnapshotIntegrityError
                (Text.pack path <> " does not match its P-3 revision"))
              else do
                mapIntegrity (validateCitations installed value
                  (envelopeContracts (storedDraft envelope)))
                pure RecordEntry
                  { entryPath = path
                  , entryRevision = embedded
                  , entryObject = objectText (envelopeObject (storedDraft envelope))
                  , entryEnvelope = envelope
                  , entryCommitted = CommittedRecord
                      { committedPath = path
                      , committedBytes = bytes
                      , committedValue = value
                      , committedEnvelope = envelope
                      , committedContractsValid = True
                      }
                  }

validateCitations
  :: [Installed]
  -> Value
  -> [ContractCitation]
  -> Either Text ()
validateCitations installed value citations = do
  instanceValue <- contractInstance value
  bundles <- mapM (resolveOne instanceValue) citations
  validateCitationContexts bundles
  where
    catalog = Map.fromList [(installedKey item, installedBundle item) | item <- installed]
    resolveOne instanceValue citation = do
      let key = (contractId citation, contractVersion citation, contractDigest citation)
      bundle <- case Map.lookup key catalog of
        Nothing -> Left ("contract is not installed at this snapshot: " <> contractId citation)
        Just value' -> Right value'
      validateContractInstance (contractBundleSchema bundle) instanceValue
      pure bundle

validateProposalContracts :: SnapshotData -> Proposal -> IO (Either Text ())
validateProposalContracts snapshot proposal = pure $ do
  let catalog = snapshotInstalled snapshot
  -- Door's reference validation lets a record pin an earlier record of the
  -- same batch, so contract target rules see those records too.
  foldM_ (validateOne catalog) [] (proposalRecords proposal)
  where
    committed = map entryTarget (snapshotRecords snapshot)
    validateOne catalog earlier record = do
      requireLocalContext (proposalRecordDraft record)
      instanceValue <- contractInstance (proposalRecordValue record)
      bundles <- mapM (validateCitation catalog instanceValue)
        (envelopeContracts (proposalRecordDraft record))
      validateCitationContexts bundles
      let targets = committed <> earlier
      validateReferenceTargetRules targets (proposalRecordDraft record) bundles
      mapM_ (validateDistinctTrackContent targets
        (proposalRecordDraft record) instanceValue) bundles
      mapM_ (validateTargetMeasure targets (proposalRecordDraft record)) bundles
      mapM_ (validateSameSubject targets (proposalRecordDraft record)) bundles
      (revision, _) <- canonicalizeDraftStrict (proposalRecordValue record)
      pure (earlier <> [Target (objectIdText (envelopeObject (proposalRecordDraft record)))
        revision (proposalRecordDraft record)])
    -- D-1: the archive holds the local documents that interpret every
    -- admitted record, so a record's `spec` must select a core context this
    -- archive already holds (D-6). A record written for a later release than
    -- the archive governs is refused instead of leaving it incomplete.
    requireLocalContext draft =
      let specification = envelopeSpec draft
          contextPath = case Text.stripPrefix "archive/" specification of
            Just version -> Just ("spec/" <> Text.unpack version <> "/context.jsonld")
            Nothing -> Nothing
      in case contextPath of
        Just path | Map.member path (snapshotImmutableAdditions snapshot) -> Right ()
        _ -> Left ("record spec " <> specification
          <> " selects a core context this archive does not hold")
    validateCitation catalog instanceValue citation = do
      let key = (contractId citation, contractVersion citation, contractDigest citation)
      bundle <- case Map.lookup key catalog of
        Nothing -> Left ("proposed contract is not installed at this snapshot: " <> contractId citation)
        Just value -> Right value
      validateContractInstance (contractBundleSchema bundle) instanceValue
      pure bundle

-- | A record a contract reference may name: committed in the snapshot, or an
-- earlier record of the proposed batch.
data Target = Target
  { targetObject :: Text
  , targetRevision :: Text
  , targetDraft :: DraftEnvelope
  }

entryTarget :: RecordEntry -> Target
entryTarget entry = Target (entryObject entry) (entryRevision entry)
  (storedDraft (entryEnvelope entry))

validateReferenceTargetRules :: [Target] -> DraftEnvelope -> [ContractBundle] -> Either Text ()
validateReferenceTargetRules records draft bundles = do
  rules <- concat <$> mapM (contractReferenceTargets . contractBundleSchema) bundles
  mapM_ validate rules
  where
    -- A target rule constrains the references present.  How many a role
    -- needs is the schema's job (its contains bounds), which has already
    -- accepted the instance; this keeps optional roles possible.
    validate (role, rule) = do
      let references = filter ((== role) . referenceRole) (envelopeReferences draft)
      if not (referenceTargetMany rule) && length references > 1
        then Left ("contract requires exactly one " <> role <> " reference")
        else mapM_ (validateOne role rule) references
    -- A rule naming one shape refuses any other; a rule naming several
    -- accepts a revision of any of them, then applies that shape's checks.
    validateOne role rule reference = do
      target <- referencedEntry records role reference
      let body = envelopeBody (targetDraft target)
          shape = bodyShape body
      case referenceTargetShapes rule of
        [single]
          | single /= shape -> Left (role <> " reference must target " <> article single)
        shapes
          | shape `notElem` shapes -> Left (role <> " reference must target a revision of shape "
              <> Text.intercalate " or " shapes)
        _ -> validateShape role rule target body
    validateShape role rule target body = case bodyShape body of
        "claim" -> case body of
          ClaimRecord _ -> case referenceTargetContract rule of
            Nothing -> Right ()
            Just (identifier, version) ->
              if any (\citation -> contractId citation == identifier && contractVersion citation == version)
                  (envelopeContracts (targetDraft target))
                then Right ()
                else Left (role <> " reference must target a Claim citing " <> identifier <> " version " <> version)
          _ -> Left (role <> " reference must target a Claim revision")
        "assessment" -> case body of
          AssessmentRecord _ -> Right ()
          _ -> Left (role <> " reference must target an Assessment revision")
        "artifact" -> case body of
          ArtifactRecord _ -> Right ()
          _ -> Left (role <> " reference must target an Artifact revision")
        "entity" -> case body of
          EntityRecord payload -> do
            kind <- case payload of
              Object fields -> case KeyMap.lookup "kind" fields of
                Just (String value) -> Right value
                _ -> Left (role <> " target Entity has no string kind")
              _ -> Left (role <> " target Entity payload is not an object")
            case (referenceTargetKind rule, referenceTargetKinds rule) of
              (Just expected, _)
                | kind == expected -> Right ()
                | otherwise -> Left (role <> " reference must target an Entity revision of kind " <> expected)
              (Nothing, [])-> Right ()
              (Nothing, expected)
                | kind `elem` expected -> Right ()
                | otherwise -> Left (role <> " reference must target an Entity revision of kind "
                    <> Text.intercalate " or " expected)
          _ -> Left (role <> " reference must target an Entity revision")
        _ -> Left "installed contract has an unsupported reference target shape"
    article shape = case shape of
      "claim" -> "a Claim revision"
      "assessment" -> "an Assessment revision"
      "artifact" -> "an Artifact revision"
      "entity" -> "an Entity revision"
      other -> "a " <> other <> " revision"

bodyShape :: RecordBody -> Text
bodyShape body = case body of
  ArtifactRecord _ -> "artifact"
  EntityRecord _ -> "entity"
  ClaimRecord _ -> "claim"
  AssessmentRecord _ -> "assessment"

-- | The one reference a rule reads in a role, or a refusal naming the role.
singleReference :: DraftEnvelope -> Text -> Either Text Reference
singleReference draft role =
  case filter ((== role) . referenceRole) (envelopeReferences draft) of
    [reference] -> Right reference
    _ -> Left ("contract requires exactly one " <> role <> " reference")

-- | A pinned Claim's payload as an object.
targetClaimPayload :: [Target] -> Text -> Reference -> Either Text (KeyMap.KeyMap Value)
targetClaimPayload records role reference = do
  target <- referencedEntry records role reference
  case envelopeBody (targetDraft target) of
    ClaimRecord (Object payload) -> Right payload
    _ -> Left (role <> " reference must target a Claim revision")

-- | A record's measurements fit the Claim that measured the audio: its rate
-- equals the pinned Claim's, and no span ends past the pinned Claim's count.
validateTargetMeasure :: [Target] -> DraftEnvelope -> ContractBundle -> Either Text ()
validateTargetMeasure records draft bundle = do
  rule <- targetMeasureRule (contractBundleSchema bundle)
  case rule of
    Nothing -> Right ()
    Just measure -> do
      let role = targetMeasureRole measure
      reference <- singleReference draft role
      target <- targetClaimPayload records role reference
      own <- case envelopeBody draft of
        ClaimRecord (Object payload) -> Right payload
        _ -> Left "target measure rule applies only to a Claim payload"
      targetRate <- integerField (targetMeasureRate measure) target
      targetCount <- integerField (targetMeasureCount measure) target
      ownRate <- integerField (targetMeasureRate measure) own
      if ownRate /= targetRate
        then Left (targetMeasureRate measure <> " must equal the " <> role <> " claim's "
          <> targetMeasureRate measure)
        else Right ()
      spans <- case KeyMap.lookup (Key.fromText (targetMeasurePayload measure)) own of
        Just (Array values) -> Right (toList values)
        _ -> Left (targetMeasurePayload measure <> " must be an array")
      ends <- mapM (spanEnd (targetMeasureEnd measure)) spans
      if all (<= targetCount) ends
        then Right ()
        else Left (targetMeasureEnd measure <> " must not exceed the " <> role <> " claim's "
          <> targetMeasureCount measure)
  where
    spanEnd name (Object piece) = integerField name piece
    spanEnd _ _ = Left "span must be an object"
    integerField name fields = case KeyMap.lookup (Key.fromText name) fields of
      Just (Number value) | Right integer <- (floatingOrInteger value :: Either Double Integer) -> Right integer
      _ -> Left (name <> " must be an integer")

-- | The pinned Claim is about the very revision this record names: its one
-- subject reference and this record's one compared reference pin the same
-- object and revision.
validateSameSubject :: [Target] -> DraftEnvelope -> ContractBundle -> Either Text ()
validateSameSubject records draft bundle = do
  rule <- sameSubjectRule (contractBundleSchema bundle)
  case rule of
    Nothing -> Right ()
    Just same -> do
      let role = sameSubjectRole same
      reference <- singleReference draft role
      target <- referencedEntry records role reference
      subject <- singleReference (targetDraft target) (sameSubjectSubject same)
      compared <- singleReference draft (sameSubjectEquals same)
      if referenceObject subject == referenceObject compared
          && referenceRevision subject == referenceRevision compared
          && referenceRevision compared /= Nothing
        then Right ()
        else Left (role <> " claim must be about the same " <> sameSubjectEquals same
          <> " revision as this record's " <> sameSubjectEquals same <> " reference")

referencedEntry :: [Target] -> Text -> Reference -> Either Text Target
referencedEntry records role reference = do
  revision <- maybe (Left ("contract reference " <> role <> " must pin an exact revision")) Right
    (referenceRevision reference)
  -- An exact retry finds its own records both committed and proposed; they
  -- are one revision, so the first match is the committed one.
  case filter
      (\target -> targetObject target == objectIdText (referenceObject reference)
        && targetRevision target == revisionText revision) records of
    target : _ -> Right target
    [] -> Left ("reference revision " <> revisionText revision <> " is not an admitted revision of " <> objectIdText (referenceObject reference))

-- | Bit-identical recordings are one source.  The digest is the pinned
-- Artifact revision's committed content digest, never a proposer's claim.
validateDistinctTrackContent :: [Target] -> DraftEnvelope -> Value -> ContractBundle -> Either Text ()
validateDistinctTrackContent records draft instanceValue bundle = do
  rule <- distinctTrackContentRule (contractBundleSchema bundle) instanceValue
  case rule of
    Nothing -> Right ()
    Just (role, counts) -> do
      let references = filter ((== role) . referenceRole) (envelopeReferences draft)
      if sum counts /= length references
        then Left "track spans and span references must be equal in number"
        else pure ()
      digests <- mapM (artifactDigest role) references
      let perTrack = map Set.fromList (splitCounts counts digests)
          repeated = Set.fromList
            [ digest
            | (index, track) <- zip [0 :: Int ..] perTrack
            , (otherIndex, other) <- zip [0 ..] perTrack
            , index < otherIndex
            , digest <- Set.toList (Set.intersection track other)
            ]
      if Set.null repeated
        then Right ()
        else Left ("tracks must not pin the same content digest: " <> Text.intercalate ", " (Set.toList repeated))
  where
    artifactDigest role reference = do
      target <- referencedEntry records role reference
      case envelopeBody (targetDraft target) of
        ArtifactRecord (Object payload) -> case KeyMap.lookup "content" payload of
          Just (Object content) -> case KeyMap.lookup "digest" content of
            Just (String digest) -> Right digest
            _ -> Left (role <> " Artifact has no content digest")
          _ -> Left (role <> " Artifact has no content")
        _ -> Left (role <> " reference must target an Artifact revision")
    splitCounts [] _ = []
    splitCounts (count : rest) values = take count values : splitCounts rest (drop count values)

validateCitationContexts :: [ContractBundle] -> Either Text ()
validateCitationContexts bundles = do
  requireDistinct "contract citation"
    [ contractBundleId bundle <> "\0" <> contractBundleVersion bundle <> "\0"
        <> contractBundleDigest bundle
    | bundle <- bundles
    ]
  let owners = Map.fromListWith Set.union
        [ (Key.toText term, Set.singleton (contractBundleId bundle))
        | bundle <- bundles
        , term <- KeyMap.keys (contractBundleContext bundle)
        ]
      collisions = [term | (term, values) <- Map.toList owners, Set.size values > 1]
  case collisions of
    [] -> Right ()
    terms -> Left ("contract context term collision: " <> Text.intercalate ", " terms)

contractInstance :: Value -> Either Text Value
contractInstance (Object fields) = do
  payload <- maybe (Left "contract payload is missing") Right (KeyMap.lookup "payload" fields)
  refs <- maybe (Left "contract refs are missing") Right (KeyMap.lookup "refs" fields)
  pure (object ["payload" .= payload, "refs" .= refs])
contractInstance _ = Left "record envelope must be an object"

indexRecords :: [RecordEntry] -> Either Text (StoreIndex, Map.Map Text Text)
indexRecords entries = do
  let revisions = map entryRevision entries
  requireDistinct "record revision" revisions
  let store = foldl' add emptyIndex entries
      superseded =
        Set.fromList
          [ (entryObject entry, revisionText revision)
          | entry <- entries
          , reference <- envelopeSupersedes (storedDraft (entryEnvelope entry))
          , Just revision <- [referenceRevision reference]
          ]
      byObject = Map.fromListWith (<>)
        [(entryObject entry, [entry]) | entry <- entries]
  mapM_ (validateOne store) entries
  heads <- deriveHeads byObject superseded
  detectSupersessionCycles entries
  pure (store, heads)
  where
    add store entry = insertRecord (storedRevision (entryEnvelope entry))
      (storedDraft (entryEnvelope entry)) store
    validateOne store entry =
      validateReferences store (storedRevision (entryEnvelope entry))
        (storedDraft (entryEnvelope entry))

deriveHeads :: Map.Map Text [RecordEntry] -> Set.Set (Text, Text) -> Either Text (Map.Map Text Text)
deriveHeads byObject superseded = Map.fromList <$> mapM one (Map.toList byObject)
  where
    one (objectId, entries) =
      case filter (not . isSuperseded objectId) entries of
        [entry] -> Right (objectId, entryRevision entry)
        [] -> Left ("object has no unsuperseded head: " <> objectId)
        _ -> Left ("object has forked unsuperseded heads: " <> objectId)
    isSuperseded objectId entry =
      Set.member (objectId, entryRevision entry) superseded

detectSupersessionCycles :: [RecordEntry] -> Either Text ()
detectSupersessionCycles entries = go Set.empty Set.empty allRevisions
  where
    allRevisions = map entryRevision entries
    edges = Map.fromList
      [ (entryRevision entry
        , [revisionText revision
          | reference <- envelopeSupersedes (storedDraft (entryEnvelope entry))
          , Just revision <- [referenceRevision reference]])
      | entry <- entries
      ]
    go _ _ [] = Right ()
    go visiting visited (revision : rest)
      | Set.member revision visited = go visiting visited rest
      | otherwise = do
          (newVisited, newVisiting) <- visit visiting visited revision
          go newVisiting newVisited rest
    visit visiting visited revision
      | Set.member revision visiting = Left "supersession cycle detected"
      | Set.member revision visited = Right (visited, visiting)
      | otherwise = do
          let next = Map.findWithDefault [] revision edges
          (visited', visiting') <- foldl' (visitStep revision) (Right (visited, Set.insert revision visiting)) next
          pure (Set.insert revision visited', Set.delete revision visiting')
    visitStep _ (Left problem) _ = Left problem
    visitStep _ (Right (visited, visiting)) next = visit visiting visited next

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

recomputeRevision :: Value -> Either Text Text
recomputeRevision (Object fields) = do
  (revision, _) <- canonicalizeDraftStrict
    (Object (KeyMap.delete "at" (KeyMap.delete "revision_id" fields)))
  pure revision
recomputeRevision _ = Left "stored record must be an object"

expectedRecordRevision :: FilePath -> Either Text Text
expectedRecordRevision path = digestText <$> parseRecordRelativePath path

contractDigestFromPath :: FilePath -> Either Text Text
contractDigestFromPath path = case splitDirectories path of
  ["contracts", name] -> do
    hex <- case Text.stripPrefix "sha256-" (Text.pack name)
      >>= Text.stripSuffix ".json" of
      Nothing -> Left "contract path must be contracts/sha256-<digest>.json"
      Just value -> Right value
    if Text.length hex == 64 && Text.all isLowerHex hex
      then Right ("sha256:" <> hex)
      else Left "contract path has an invalid digest"
  _ -> Left "contract path must be contracts/sha256-<digest>.json"
  where
    isLowerHex character = ('0' <= character && character <= '9')
      || ('a' <= character && character <= 'f')

requiredString :: Text -> KeyMap.KeyMap Value -> Either Text Text
requiredString name fields = case KeyMap.lookup (Key.fromText name) fields of
  Just (String value) | not (Text.null value) -> Right value
  Just _ -> Left (name <> " must be a non-empty string")
  Nothing -> Left (name <> " is required")

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

planningSnapshotError :: AdmissionSnapshotLoadError -> AdmissionPlanningError
planningSnapshotError problem = case problem of
  AdmissionSnapshotGitError issue -> AdmissionSnapshotFailure issue
  AdmissionSnapshotIntegrityError message -> AdmissionSnapshotIntegrityFailure message

decodeBytes :: FilePath -> LazyByteString.ByteString -> Either Text Value
decodeBytes path bytes = case decodeUnique bytes of
  Left problem -> Left (Text.pack path <> ": invalid JSON: " <> Text.pack problem)
  Right value -> Right value

decodeUtf8 :: FilePath -> LazyByteString.ByteString -> Either Text Text
decodeUtf8 path bytes = case Text.decodeUtf8' (LazyByteString.toStrict bytes) of
  Left _ -> Left (Text.pack path <> " is not valid UTF-8")
  Right value -> Right value

requireDistinct :: Text -> [Text] -> Either Text ()
requireDistinct label values
  | Set.size (Set.fromList values) == length values = Right ()
  | otherwise = Left (label <> " must be unique")

objectText :: ObjectId -> Text
objectText = objectIdText

revisionText :: RevisionId -> Text
revisionText = revisionIdText

hashHex :: StrictByteString.ByteString -> Text
hashHex bytes = Text.pack (concatMap byteHex (StrictByteString.unpack bytes))
  where
    byteHex byte =
      ["0123456789abcdef" !! fromIntegral (div byte 16)
      ,"0123456789abcdef" !! fromIntegral (mod byte 16)]

snapshotError :: GitSnapshotError -> Text
snapshotError = gitSnapshotErrorMessage
