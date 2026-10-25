{-# LANGUAGE OverloadedStrings #-}

-- | Read-only loading of one immutable Git snapshot.
--
-- This module never reads the worktree, writes an archive, invokes
-- git-annex, or resolves a second commit. The snapshot is the authority for
-- every manifest, contract, record, and introducer planning uses.
module Archive.Door.Snapshot.Load
  ( loadSnapshot
  ) where

import Archive.Door.Canonical (canonicalizeDraftStrict)
import Archive.Door.ContractCitations (validateCitations)
import Archive.Door.Contracts (validateContractBundle)
import Archive.Door.Hex (hexText)
import Archive.Door.Json.Access (requiredNonEmptyText)
import Archive.Door.Layout
  ( manifestArchiveId
  , manifestSpec
  , parseArchiveManifest
  )
import Archive.Door.References (emptyIndex, insertKnown)
import Archive.Door.Snapshot.Data
  ( AdmissionSnapshotLoadError (..)
  , Installed (..)
  , RecordEntry (..)
  , SnapshotData (..)
  , installedMap
  , transitionKeys
  )
import Archive.Door.Snapshot.History
  ( validateCitationOrder
  , validateImmutablePaths
  , validateInstalledCatalog
  , validateResources
  , validateUpgradeHistory
  )
import Archive.Door.Snapshot.Memo (remember, snapshotMemo)
import Archive.Door.Snapshot.Problem (SnapshotProblem (..), snapshotCheck, snapshotProblemMessage)
import Archive.Door.Snapshot.Read
  ( decodeBytes
  , expectedRecordRevision
  , loadIntroducers
  , integrity
  , mapIntegrity
  , readBytes
  , readCanonicalJson
  , readJson
  )
import Archive.Door.Snapshot.Supersession (deriveHeads, indexRecords)
import Archive.Door.Types
  ( Shape (..)
  , StoredEnvelope (..)
  , envelopeContracts
  , envelopeObject
  , parseObjectId
  , parseRevisionId
  , parseStoredEnvelope
  , revisionIdText
  )
import Archive.Door.VerifiedCheckpoint
  ( RecordSummary (..)
  , VerifiedCheckpoint (..)
  , readCheckpoint
  , summarizeEnvelope
  )
import Archive.GitSnapshot (GitSnapshot)
import qualified Archive.GitSnapshot as Git
import Control.Monad.Trans.Class (lift)
import Control.Monad.Trans.Except (ExceptT (..), except, runExceptT, withExceptT)
import Archive.Read.Core (CommittedRecord (..))
import qualified Crypto.Hash.SHA256 as SHA256
import Data.Aeson (Value (Object))
import qualified Data.Aeson.KeyMap as KeyMap
import Data.Aeson.RFC8785 (encodeCanonical)
import Data.Bifunctor (first)
import Data.IORef (readIORef)
import qualified Data.Map.Strict as Map
import qualified Data.Set as Set
import Data.Text (Text)
import qualified Data.Text as Text
import System.FilePath (splitDirectories)
import System.IO.Unsafe (unsafeInterleaveIO)

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
loadCheckpointedSnapshot snapshot verified = runExceptT $ do
  -- Record bytes are read only if planning parses a record (see
  -- 'loadTrustedRecord'); everything else is read now.
  withExceptT AdmissionSnapshotGitError
    (ExceptT (Git.prefetchPaths snapshot ["archive.json", "spec", "contracts"]))
  -- All four reads run before any result is checked.
  manifestResult <- lift (readJson snapshot "archive.json")
  contracts <- lift (loadContracts snapshot)
  pathsResult <- lift (Git.listPaths snapshot "records")
  specResult <- lift (Git.listPaths snapshot "spec")
  manifest <- except (manifestResult >>= mapIntegrity . parseArchiveManifest)
  installed <- except contracts
  (paths, specPaths) <- withExceptT AdmissionSnapshotGitError
    (except ((,) <$> pathsResult <*> specResult))
  loaded <- lift (mapM (loadTrustedRecord snapshot (checkpointRecords verified)) paths)
  except $ do
    entries <- sequence loaded
    let additions = checkpointAdditions verified
        summaries = checkpointRecords verified
    if Map.keysSet additions /= Set.fromList
        ("archive.json" : specPaths <> map installedPath installed <> paths)
        || Map.keysSet summaries /= Set.fromList paths
      then Left (AdmissionSnapshotIntegrityError CheckpointPathsMismatch)
      else Right ()
    shaped <- mapM (\(entry, summary) -> (,) entry <$> summaryShapeOf summary)
      [(entry, summary) | entry <- entries, Just summary <- [Map.lookup (entryPath entry) summaries]]
    let store = foldl' (\index (entry, shape) -> insertKnown (entryObject entry) (entryRevision entry) shape index)
          emptyIndex shaped
        -- The checkpoint keeps supersession as text. A pair that does not
        -- parse names no committed record, so it could never match a head
        -- and is dropped, exactly as comparing the text left it unmatched.
        superseded = Set.fromList
          [ (object, revision)
          | summary <- Map.elems summaries
          , Right object <- [parseObjectId (summaryObject summary)]
          , supersededText <- summarySupersedes summary
          , Right revision <- [parseRevisionId supersededText]
          ]
        byObject = Map.fromListWith (<>) [(entryObject entry, [entry]) | entry <- entries]
    heads <- integrity (deriveHeads byObject superseded)
    batches <- integrity (loadIntroducers additions entries)
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
      _ -> Left (AdmissionSnapshotIntegrityError CheckpointUnknownShape)

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
    revision <- integrity (expectedRecordRevision path)
    summary <- maybe (Left (AdmissionSnapshotIntegrityError (MissingFromCheckpoint path)))
      Right (Map.lookup path summaries)
    -- A checkpoint is not trusted to spell an object: one that does not
    -- parse fails this load, and the caller falls back to full verification.
    object <- mapIntegrity (parseObjectId (summaryObject summary))
    let parsed = do
          value <- first snapshotProblemMessage (decodeBytes path bytes)
          stored <- parseStoredEnvelope value
          pure (value, stored)
        trusted :: forall a. ((Value, StoredEnvelope) -> a) -> a
        trusted use = either (\problem -> error ("door: checkpointed record " <> path
          <> " no longer parses: " <> Text.unpack problem)) use parsed
        envelope = trusted snd
    Right RecordEntry
      { entryPath = path
      , entryRevision = revision
      , entryObject = object
      , entryEnvelope = envelope
      , entryCommitted = CommittedRecord
          { committedPath = path
          , committedBytes = bytes
          , committedValue = trusted fst
          , committedEnvelope = envelope
          , committedContractsValid = True
          }
      }

loadVerifiedSnapshot :: GitSnapshot -> IO (Either AdmissionSnapshotLoadError SnapshotData)
loadVerifiedSnapshot snapshot = runExceptT $ do
  -- Every committed file is checked below; read them all in bulk first, so
  -- the Git processes do not grow with the number of records.
  prefetched <- lift (Git.prefetchPaths snapshot archivePaths)
  -- Only the manifest may change after it is added, and only by a
  -- specification upgrade; 'validateUpgradeHistory' proves every change is
  -- one (ADR 0068).
  (additions, modifications) <- withExceptT AdmissionSnapshotGitError $ ExceptT $
    case prefetched of
      Left problem -> pure (Left problem)
      Right () -> Git.archivePathHistory snapshot archivePaths ["archive.json"]
  let upgradeCommits = map snd modifications
  manifest <- ExceptT (readCanonicalJson snapshot "archive.json")
    >>= except . mapIntegrity . parseArchiveManifest
  ExceptT (validateResources snapshot additions upgradeCommits (manifestSpec manifest))
  installed <- ExceptT (loadContracts snapshot)
  ExceptT (integrity <$> validateInstalledCatalog additions
    upgradeCommits (manifestSpec manifest) installed)
  entries <- ExceptT (loadRecords snapshot installed)
  -- Both histories are checked before either result is.
  immutable <- lift (validateImmutablePaths snapshot additions installed entries)
  upgrades <- lift (validateUpgradeHistory snapshot additions upgradeCommits
    manifest installed entries)
  except (immutable >> upgrades)
  (store, heads) <- except (integrity (indexRecords entries))
  history <- lift (Git.linearHistory snapshot)
  commits <- withExceptT AdmissionSnapshotGitError (except history)
  batches <- except (integrity (loadIntroducers additions entries))
  except (integrity (validateCitationOrder commits additions installed entries))
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
    digest <- integrity (contractDigestFromPath path)
    value <- integrity (decodeBytes path bytes)
    if encodeCanonical value /= bytes
      then Left (AdmissionSnapshotIntegrityError (NotCanonical path))
      else do
        let actualDigest = "sha256:" <> hexText (SHA256.hashlazy bytes)
        if actualDigest /= digest
          then Left (AdmissionSnapshotIntegrityError (DigestPathMismatch path))
          else do
            fields <- integrity (objectFields value)
            identifier <- mapIntegrity (requiredNonEmptyText "id" fields)
            version <- mapIntegrity (requiredNonEmptyText "version" fields)
            bundle <- mapIntegrity (validateContractBundle identifier version digest value)
            pure Installed
              { installedPath = path
              , installedKey = (identifier, version, digest)
              , installedBundle = bundle
              }

objectFields :: Value -> Either SnapshotProblem (KeyMap.KeyMap Value)
objectFields (Object fields) = Right fields
objectFields _ = Left BundleNotObject

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
    expected <- integrity (expectedRecordRevision path)
    value <- integrity (decodeBytes path bytes)
    if encodeCanonical value /= bytes
      then Left (AdmissionSnapshotIntegrityError (NotCanonical path))
      else do
        envelope <- mapIntegrity (parseStoredEnvelope value)
        let embedded = storedRevision envelope
        if embedded /= expected
          then Left (AdmissionSnapshotIntegrityError (EmbeddedRevisionDisagrees path))
          else do
            recomputed <- integrity (recomputeRevision value)
            if recomputed /= revisionIdText embedded
              then Left (AdmissionSnapshotIntegrityError (RevisionMismatch path))
              else do
                integrity (first CitationRefused (validateCitations installed value
                  (envelopeContracts (storedDraft envelope))))
                pure RecordEntry
                  { entryPath = path
                  , entryRevision = embedded
                  , entryObject = envelopeObject (storedDraft envelope)
                  , entryEnvelope = envelope
                  , entryCommitted = CommittedRecord
                      { committedPath = path
                      , committedBytes = bytes
                      , committedValue = value
                      , committedEnvelope = envelope
                      , committedContractsValid = True
                      }
                  }

recomputeRevision :: Value -> Either SnapshotProblem Text
recomputeRevision (Object fields) = do
  (revision, _) <- snapshotCheck (canonicalizeDraftStrict
    (Object (KeyMap.delete "at" (KeyMap.delete "revision_id" fields))))
  pure revision
recomputeRevision _ = Left StoredRecordNotObject

contractDigestFromPath :: FilePath -> Either SnapshotProblem Text
contractDigestFromPath path = case splitDirectories path of
  ["contracts", name] -> do
    hex <- case Text.stripPrefix "sha256-" (Text.pack name)
      >>= Text.stripSuffix ".json" of
      Nothing -> Left ContractPathShape
      Just value -> Right value
    if Text.length hex == 64 && Text.all isLowerHex hex
      then Right ("sha256:" <> hex)
      else Left ContractPathDigest
  _ -> Left ContractPathShape
  where
    isLowerHex character = ('0' <= character && character <= '9')
      || ('a' <= character && character <= 'f')
