{-# LANGUAGE OverloadedStrings #-}

-- | The prospective snapshot a specification upgrade is planned against.
module Archive.Door.Transition.Upgrade
  ( prepareUpgrade
  ) where

import Archive.Door.Canonical (canonicalizeDraftStrict)
import Archive.Door.Contracts (ContractBundle)
import Archive.Door.Json (decodeUnique)
import Archive.Door.Json.Access (requiredNonEmptyText)
import Archive.Door.Layout
  ( archiveManifestBytes
  , makeArchiveManifest
  , manifestArchiveId
  , manifestLayout
  , manifestSpec
  , parseArchiveManifest
  , specVersion
  )
import Archive.Door.Proposal (Proposal (..), ProposalRecord (..))
import Archive.Door.Snapshot.Data (SnapshotData (..))
import Archive.Door.SpecificationUpgrade
  ( SpecificationRelease (..)
  , UpgradeTarget (..)
  , compareSpecifications
  , loadUpgradeTarget
  , upgradeContractId
  )
import Archive.Door.Transition.Plan (TransitionPlanningError (..))
import Archive.Door.Types
  ( ContractCitation (..)
  , DraftEnvelope (..)
  , RecordBody (..)
  )
import qualified Archive.GitSnapshot as Git
import Data.Aeson (Value (..))
import qualified Data.Aeson.KeyMap as KeyMap
import qualified Data.ByteString.Lazy as LazyByteString
import Data.Map.Strict (Map)
import qualified Data.Map.Strict as Map
import qualified Data.Set as Set
import Data.Text (Text)
import qualified Data.Text as Text

-- | A specification upgrade (ADR 0068) is planned against the archive as it
-- will be once the target release is installed: its added bundles are
-- dispatchable and its core context is held. The archive's own snapshot is
-- returned unchanged to the caller for the commit's verification; only
-- planning sees the prospective one. Any other proposal, and a complete
-- retry of a committed upgrade, is planned against the snapshot as it is.
prepareUpgrade
  :: Git.GitSnapshot
  -> SnapshotData
  -> Proposal
  -> IO (Either TransitionPlanningError (SnapshotData, [(FilePath, LazyByteString.ByteString)]))
prepareUpgrade gitSnapshot snapshot proposal =
  case filter citesUpgrade (proposalRecords proposal) of
    [] -> pure (Right (snapshot, []))
    upgrades
      | length upgrades /= 1 || length (proposalRecords proposal) /= 1 ->
          pure (rejected "a specification upgrade proposal must contain exactly one record")
    record : _ -> case canonicalizeDraftStrict (proposalRecordValue record) of
      Left problem -> pure (rejected problem)
      Right (revision, _)
        | Map.member revision (snapshotRevisionIntroducers snapshot) -> pure (Right (snapshot, []))
        | otherwise -> prepareNew record
  where
    rejected = Left . TransitionProposalRejected
    citesUpgrade record = any ((== upgradeContractId) . contractId)
      (envelopeContracts (proposalRecordDraft record))

    prepareNew record = do
      manifestResult <- Git.readPath gitSnapshot "archive.json"
      case manifestResult of
        Left problem -> pure (Left (TransitionSnapshotFailure problem))
        Right manifestBytes -> case decodeUnique manifestBytes of
          Left problem -> pure (Left (TransitionSnapshotIntegrityFailure ("archive.json: " <> Text.pack problem)))
          Right manifestValue -> case parseArchiveManifest manifestValue of
            Left problem -> pure (Left (TransitionSnapshotIntegrityFailure problem))
            Right manifest -> do
              let current = manifestSpec manifest
                  draft = proposalRecordDraft record
              case upgradeRequest current draft of
                Left problem -> pure (rejected problem)
                Right to -> do
                  contextResult <- currentContext current
                  case contextResult of
                    Left problem -> pure (Left problem)
                    Right context -> do
                      let release = SpecificationRelease
                            { releaseSpecification = current
                            , releaseCatalog = Map.keysSet (snapshotInstalled snapshot)
                            , releaseLayout = manifestLayout manifest
                            , releaseContext = context
                            }
                      targetResult <- loadUpgradeTarget release to
                      pure $ do
                        target <- either rejected Right targetResult
                        requireTargetSpec to draft
                        upgraded <- either rejected Right
                          (makeArchiveManifest (manifestArchiveId manifest) to)
                        let added = targetAddedBundles target
                            installed = Map.union (snapshotInstalled snapshot)
                              (Map.fromList [(key, bundle) | (key, bundle) <- added])
                        mapM_ (requireCitable to installed) (envelopeContracts draft)
                        pure
                          ( prospective added target installed
                          , ("archive.json", archiveManifestBytes upgraded) : targetFiles target
                          )

    -- The payload's `from` must be the archive's specification and `to` a
    -- later version; once the target is known to this Door, the record must
    -- belong to the release it adopts.
    upgradeRequest current draft = do
      fields <- case envelopeBody draft of
        ClaimRecord (Object payload) -> Right payload
        _ -> Left "a specification upgrade must be a claim with an object payload"
      from <- requiredNonEmptyText "from" fields
      to <- requiredNonEmptyText "to" fields
      _ <- either (Left . ("specification upgrade from: " <>)) Right (specVersion from)
      _ <- either (Left . ("specification upgrade to: " <>)) Right (specVersion to)
      order <- compareSpecifications to from
      case order of
        LT -> Left ("unsupported: no downgrade exists: " <> to <> " is earlier than " <> from)
        EQ -> Left ("unsupported: a specification upgrade must name a version later than " <> from)
        GT -> Right ()
      if from /= current
        then Left ("specification upgrade from " <> from
          <> " does not start at the archive's specification " <> current)
        else Right ()
      pure to

    requireTargetSpec to draft =
      if envelopeSpec draft /= to
        then rejected ("a specification upgrade record must be written under its target specification "
          <> to <> ", not " <> envelopeSpec draft)
        else Right ()

    currentContext current = case specVersion current of
      Left problem -> pure (Left (TransitionSnapshotIntegrityFailure problem))
      Right version -> do
        let path = "spec/" <> Text.unpack version <> "/context.jsonld"
        bytes <- Git.readPath gitSnapshot path
        pure $ case bytes of
          Left problem -> Left (TransitionSnapshotFailure problem)
          Right content -> case decodeUnique content of
            Right (Object outer) | Just (Object inner) <- KeyMap.lookup "@context" outer -> Right inner
            _ -> Left (TransitionSnapshotIntegrityFailure (Text.pack path <> " has no @context object"))

    requireCitable to installed citation =
      if Map.member (contractId citation, contractVersion citation, contractDigest citation) installed
        then Right ()
        else rejected ("proposed contract is not installed at this snapshot or in the catalog of "
          <> to <> ": " <> contractId citation)

    prospective :: [((Text, Text, Text), ContractBundle)] -> UpgradeTarget
                -> Map (Text, Text, Text) ContractBundle -> SnapshotData
    prospective added target installed = snapshot
      { snapshotInstalled = installed
      , snapshotTransitionKeys = snapshotTransitionKeys snapshot `Set.union` Set.fromList
          [ key | (key@(identifier, _, _), _) <- added, Text.isPrefixOf "archive.transition/" identifier ]
      , snapshotImmutableAdditions = Map.union (snapshotImmutableAdditions snapshot)
          (Map.fromList [(path, "") | (path, _) <- targetFiles target])
      }
