{-# LANGUAGE OverloadedStrings #-}

-- | A proposal's contract citations and contract rules against a snapshot.
--
-- Admission and transition planning both run these checks before the
-- planner itself: citations, then reference targets and payload rules in
-- batch order, so a record may pin an earlier record of the same batch.
module Archive.Door.ProposalContracts
  ( validateProposalContracts
  ) where

import Archive.Door.Canonical (canonicalizeDraftStrict)
import Archive.Door.ContractCitations (contractInstance, validateCitationContexts)
import Archive.Door.Contracts (validateBundleInstance)
import Archive.Door.Proposal (Proposal (..), ProposalRecord (..))
import Archive.Door.ReferenceTargets
  ( Target (..)
  , entryTarget
  , validateReferenceTargetRules
  )
import Archive.Door.ReleaseAdmission (effectiveBundles, validateReleaseAdmission)
import Archive.Door.Snapshot.Data (SnapshotData (..))
import Archive.Door.TargetMeasures
  ( validateDistinctTrackContent
  , validateSameSubject
  , validateSpanWindow
  , validateTargetBound
  , validateTargetIndex
  , validateTargetMeasure
  , validateTargetSamples
  )
import Archive.Door.Types
  ( ContractCitation (..)
  , DraftEnvelope (envelopeSpec)
  , envelopeContracts
  , envelopeObject
  , objectIdText
  )
import Control.Monad (foldM_)
import qualified Data.Map.Strict as Map
import Data.Text (Text)
import qualified Data.Text as Text

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
      -- Rules nested by when-pinned apply to the form this record has (D-18).
      effective <- effectiveBundles instanceValue bundles
      mapM_ (validateDistinctTrackContent targets
        (proposalRecordDraft record) instanceValue) effective
      mapM_ (validateTargetMeasure targets (proposalRecordDraft record)) effective
      mapM_ (validateSameSubject targets (proposalRecordDraft record)) effective
      mapM_ (validateSpanWindow targets (proposalRecordDraft record)) effective
      mapM_ (validateTargetBound targets (proposalRecordDraft record)) effective
      mapM_ (validateTargetSamples targets (proposalRecordDraft record)) effective
      mapM_ (validateTargetIndex targets (proposalRecordDraft record)) effective
      validateReleaseAdmission [(targetObject t, targetRevision t, targetDraft t) | t <- targets]
        (proposalRecordDraft record) effective
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
      validateBundleInstance bundle instanceValue
      pure bundle
