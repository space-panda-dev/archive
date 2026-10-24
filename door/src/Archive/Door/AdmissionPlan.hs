{-# LANGUAGE OverloadedStrings #-}

-- | The pure admission decision below the command boundary.
--
-- This seam binds a proposal to a snapshot of committed records without
-- choosing a git-annex command or a placement policy.  The caller supplies a
-- snapshot read from Git HEAD, then rechecks both the snapshot and the
-- retained proposal before performing the one-commit operation.
module Archive.Door.AdmissionPlan
  ( AdmissionPlan (..)
  , AdmissionSnapshot (..)
  , planAdmission
  ) where

import Archive.Door.Canonical (canonicalizeDraftStrict)
import Archive.Door.Distinct (distinctOr)
import Archive.Door.PlanProblem
  ( PlanProblem (..)
  , Planner (..)
  , refuseProblem
  )
import Archive.Door.Refusal (PlanRefusal, refuse)
import Archive.Door.Identity (allocateObjectId)
import Archive.Door.Proposal
  ( ExpectedHead (..)
  , IdentityAllocation (..)
  , Proposal (..)
  , ProposalKind (..)
  , ProposalManifest (..)
  , ProposalRecord (..)
  , validateProposal
  )
import Archive.Door.References
  ( StoreIndex
  , insertRecord
  , validateReferences
  )
import Archive.Door.Types
  ( ContractCitation (..)
  , DraftEnvelope (..)
  , ObjectId
  , objectIdText
  , Reference (referenceRevision)
  , RecordBody (..)
  , RevisionId, parseRevisionId, revisionIdText
  )
import Data.Aeson (Value)
import Data.Map.Strict (Map)
import qualified Data.Map.Strict as Map
import Data.Set (Set)
import qualified Data.Set as Set
import Data.Text (Text)

data AdmissionSnapshot = AdmissionSnapshot
  { snapshotArchiveId :: Text
  , snapshotHeads :: Map ObjectId RevisionId
  , snapshotRevisions :: Set RevisionId
  , snapshotRevisionBatches :: Map RevisionId Text
  , snapshotReferenceIndex :: StoreIndex
  , snapshotTransitionContracts :: Set (Text, Text, Text)
  }

data AdmissionPlan = AdmissionPlan
  { plannedAdmissionSnapshot :: Text
  , plannedGitExecutableIdentity :: Text
  , plannedRevisions :: [Text]
  , plannedRecords :: [(Text, Value)]
  , planIsRetry :: Bool
  } deriving (Eq, Show)

planAdmission :: AdmissionSnapshot -> Proposal -> Either PlanRefusal AdmissionPlan
planAdmission snapshot proposal = do
  refuseProblem $ if proposalKind proposal /= AdmissionProposal
    then Left (WrongProposalKind AdmissionPlanner)
    else pure ()
  refuse (validateProposal proposal)
  prepared <- refuse (mapM prepareRecord (proposalRecords proposal))
  refuseProblem (distinctOr (DuplicateComputedRevision AdmissionPlanner) (map preparedRevision prepared))
  let revisions = map preparedRevision prepared
      present = map (`Set.member` snapshotRevisions snapshot) revisions
      batchEvidence = map (\revision -> Map.lookup revision (snapshotRevisionBatches snapshot)) revisions
      completeRetry = case sequence batchEvidence of
        Just batches@(batch : _)
          | allEqual batches && and present ->
              let committedBatch = Map.keysSet
                    (Map.filter (== batch) (snapshotRevisionBatches snapshot))
              in committedBatch == Set.fromList revisions
        Nothing -> False
        _ -> False
  refuseProblem $ if any id present && not completeRetry
    then Left (ExistingRevisionsNotOneBatch AdmissionPlanner)
    else pure ()
  refuseProblem (mapM_ (rejectUnsupported snapshot) (proposalRecords proposal))
  refuseProblem (validateAllocations snapshot proposal prepared completeRetry)
  if completeRetry
    then pure ()
    else do
      validateExpectedHeads snapshot proposal prepared
      refuseProblem (validateLinearHeads snapshot prepared)
  refuse (validateReferencesInOrder snapshot prepared)
  pure AdmissionPlan
    { plannedAdmissionSnapshot = ""
    , plannedGitExecutableIdentity = ""
    , plannedRevisions = map revisionIdText revisions
    , plannedRecords = [(revisionIdText (preparedRevision item), preparedCanonical item) | item <- prepared]
    , planIsRetry = completeRetry
    }
  where
    allEqual [] = True
    allEqual (value : values) = all (== value) values

rejectUnsupported :: AdmissionSnapshot -> ProposalRecord -> Either PlanProblem ()
rejectUnsupported snapshot record = case envelopeBody (proposalRecordDraft record) of
  ClaimRecord _ -> rejectCitations
  AssessmentRecord _ -> rejectCitations
  _ -> Right ()
  where
    rejectCitations =
      if any (\citation -> citationTuple citation `Set.member` snapshotTransitionContracts snapshot)
          (envelopeContracts (proposalRecordDraft record))
        then Left TransitionContractNotAdmissible
        else Right ()
    citationTuple citation =
      (contractId citation, contractVersion citation, contractDigest citation)

data Prepared = Prepared
  { preparedRevision :: RevisionId
  , preparedCanonical :: Value
  , preparedObject :: ObjectId
  , preparedDraft :: DraftEnvelope
  }

-- | The canonicalizer writes a revision as @sha256:@ and 64 lowercase hex
-- digits, which 'parseRevisionId' always accepts; the parse gives it its
-- type and cannot refuse.
prepareRecord :: ProposalRecord -> Either Text Prepared
prepareRecord record = do
  (digest, canonical) <- canonicalizeDraftStrict (proposalRecordValue record)
  revision <- parseRevisionId digest
  pure Prepared
    { preparedRevision = revision
    , preparedCanonical = canonical
    , preparedObject = envelopeObject draft
    , preparedDraft = draft
    }
  where
    draft = proposalRecordDraft record

validateAllocations :: AdmissionSnapshot -> Proposal -> [Prepared] -> Bool -> Either PlanProblem ()
validateAllocations snapshot proposal prepared completeRetry = do
  let allocations = manifestIdentities (proposalManifest proposal)
      draftObjects = Set.fromList (map preparedObject prepared)
      existingObjects = Map.keysSet (snapshotHeads snapshot)
      newObjects = draftObjects `Set.difference` existingObjects
      allocatedObjects = Set.fromList (map identityObjectId allocations)
  mapM_ (validateAllocation snapshot) allocations
  -- A proposal's allocations are its own text; the objects are compared by
  -- their spelling.
  if completeRetry
    then if allocatedObjects `Set.isSubsetOf` Set.map objectIdText draftObjects then pure ()
      else Left (AllocationOutsideDrafts AdmissionPlanner)
  else if allocatedObjects /= Set.map objectIdText newObjects
    then Left (AllocationsNotOnePerNewObject AdmissionPlanner)
    else pure ()

validateAllocation :: AdmissionSnapshot -> IdentityAllocation -> Either PlanProblem ()
validateAllocation snapshot allocation = do
  derived <- either (Left . AllocationUnderivable) Right
    (allocateObjectId (snapshotArchiveId snapshot)
      (identityNamespace allocation) (identityName allocation))
  if derived == identityObjectId allocation
    then Right ()
    else Left AllocationIdMismatch

validateExpectedHeads :: AdmissionSnapshot -> Proposal -> [Prepared] -> Either PlanRefusal ()
validateExpectedHeads snapshot proposal _ = refuseProblem $ mapM_ check (manifestExpectedHeads (proposalManifest proposal))
  where
    -- Expected heads are the proposal's own text, so the heads are looked
    -- up by their spelling; 'objectIdText' preserves the keys' order.
    heads = Map.mapKeysMonotonic objectIdText (snapshotHeads snapshot)
    check expected = case Map.lookup (expectedObjectId expected) heads of
      Nothing
        | expectedRevisionId expected == Nothing -> Right ()
        | otherwise -> Left ExpectedHeadMissingObject
      Just actual
        | expectedRevisionId expected == Just (revisionIdText actual) -> Right ()
        | otherwise -> Left (ExpectedHeadNotCurrent AdmissionPlanner)

validateReferencesInOrder :: AdmissionSnapshot -> [Prepared] -> Either Text ()
validateReferencesInOrder snapshot = go (snapshotReferenceIndex snapshot)
  where
    go _ [] = Right ()
    go index (item : rest) = do
      let revision = preparedRevision item
      validateReferences index revision (preparedDraft item)
      go (insertRecord revision (preparedDraft item) index) rest

validateLinearHeads :: AdmissionSnapshot -> [Prepared] -> Either PlanProblem ()
validateLinearHeads snapshot = go (snapshotHeads snapshot)
  where
    go _ [] = Right ()
    go heads (item : rest) = do
      let objectId = preparedObject item
          superseded =
            [ revision
            | reference <- envelopeSupersedes (preparedDraft item)
            , Just revision <- [referenceRevision reference]
            ]
      case (Map.lookup objectId heads, superseded) of
        (Nothing, []) -> pure ()
        (Just current, [previous])
          | current == previous -> pure ()
        _ -> Left (NonLinearSupersession AdmissionPlanner)
      go (Map.insert objectId (preparedRevision item) heads) rest
