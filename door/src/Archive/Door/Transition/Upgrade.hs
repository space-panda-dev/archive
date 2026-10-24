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
import Archive.Door.Transition.Problem
  ( TransitionProblem (..)
  , outsideCheck
  , transitionProblemMessage
  )
import Archive.Door.Types
  ( ContractCitation (..)
  , DraftEnvelope (..)
  , RecordBody (..)
  )
import qualified Archive.GitSnapshot as Git
import Data.Aeson (Value (..))
import qualified Data.Aeson.KeyMap as KeyMap
import qualified Data.ByteString.Lazy as LazyByteString
import Control.Monad.Trans.Class (lift)
import Control.Monad.Trans.Except (ExceptT (..), except, runExceptT, withExceptT)
import Data.Bifunctor (first)
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
          pure (rejected UpgradeNotSingleRecord)
    record : _ -> case canonicalizeDraftStrict (proposalRecordValue record) of
      Left problem -> pure (rejected (OutsideCheck problem))
      Right (revision, _)
        | Map.member revision (snapshotRevisionIntroducers snapshot) -> pure (Right (snapshot, []))
        | otherwise -> prepareNew record
  where
    -- Every refusal here is a transition problem, rendered where it
    -- becomes a planning error.
    rejected :: TransitionProblem -> Either TransitionPlanningError a
    rejected = Left . TransitionProposalRejected . transitionProblemMessage
    rejectedBy :: Either Text a -> Either TransitionPlanningError a
    rejectedBy = either (rejected . OutsideCheck) Right
    citesUpgrade record = any ((== upgradeContractId) . contractId)
      (envelopeContracts (proposalRecordDraft record))

    prepareNew record = runExceptT $ do
      manifestBytes <- withExceptT TransitionSnapshotFailure
        (ExceptT (Git.readPath gitSnapshot "archive.json"))
      manifestValue <- except $ either
        (\problem -> Left (TransitionSnapshotIntegrityFailure ("archive.json: " <> Text.pack problem)))
        Right (decodeUnique manifestBytes)
      manifest <- withExceptT TransitionSnapshotIntegrityFailure
        (except (parseArchiveManifest manifestValue))
      let current = manifestSpec manifest
          draft = proposalRecordDraft record
      to <- except (either rejected Right (upgradeRequest current draft))
      context <- ExceptT (currentContext current)
      let release = SpecificationRelease
            { releaseSpecification = current
            , releaseCatalog = Map.keysSet (snapshotInstalled snapshot)
            , releaseLayout = manifestLayout manifest
            , releaseContext = context
            }
      targetResult <- lift (loadUpgradeTarget release to)
      except $ do
        target <- rejectedBy targetResult
        requireTargetSpec to draft
        upgraded <- rejectedBy (makeArchiveManifest (manifestArchiveId manifest) to)
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
        _ -> Left UpgradeNotClaimObject
      from <- outsideCheck (requiredNonEmptyText "from" fields)
      to <- outsideCheck (requiredNonEmptyText "to" fields)
      _ <- first (UpgradeVersionInvalid "from") (specVersion from)
      _ <- first (UpgradeVersionInvalid "to") (specVersion to)
      order <- outsideCheck (compareSpecifications to from)
      case order of
        LT -> Left (UpgradeIsDowngrade to from)
        EQ -> Left (UpgradeNotLater from)
        GT -> Right ()
      if from /= current
        then Left (UpgradeFromNotCurrent from current)
        else Right ()
      pure to

    requireTargetSpec to draft =
      if envelopeSpec draft /= to
        then rejected (UpgradeWrittenUnder to (envelopeSpec draft))
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
        else rejected (UpgradeContractNotCitable to (contractId citation))

    prospective :: [((Text, Text, Text), ContractBundle)] -> UpgradeTarget
                -> Map (Text, Text, Text) ContractBundle -> SnapshotData
    prospective added target installed = snapshot
      { snapshotInstalled = installed
      , snapshotTransitionKeys = snapshotTransitionKeys snapshot `Set.union` Set.fromList
          [ key | (key@(identifier, _, _), _) <- added, Text.isPrefixOf "archive.transition/" identifier ]
      , snapshotImmutableAdditions = Map.union (snapshotImmutableAdditions snapshot)
          (Map.fromList [(path, "") | (path, _) <- targetFiles target])
      }
