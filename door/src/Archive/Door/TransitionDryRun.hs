{-# LANGUAGE OverloadedStrings #-}

-- | Pure transition planning against one committed Git snapshot.
--
-- This module is deliberately below the command seam: it retains no proposal
-- files, writes no archive state, and never asks git-annex to perform an act.
-- The caller receives the revisions and derived state that a transition would
-- produce, after exact contract dispatch and the state fences have passed.
-- Dispatch, the per-kind rules, history replay, and the upgrade's
-- prospective snapshot live under "Archive.Door.Transition"; this module
-- joins them and re-exports what callers used here.
module Archive.Door.TransitionDryRun
  ( TransitionPlan (..)
  , TransitionPlanningError (..)
  , transitionPlanningErrorMessage
  , dryRunTransitionForCommit
  , dryRunTransitionVerified
  , dryRunTransition
  , TransitionKind (..)
  , transitionDispatchTable
  , transitionContractsWithoutHandler
  ) where

import Archive.Door.BeyondCircle (validateBeyondCircle)
import Archive.Door.Canonical (canonicalizeDraftStrict)
import Archive.Door.Contracts (ContractBundle (..))
import Archive.Door.Identity (allocateObjectId)
import Archive.Door.Proposal
  ( ExpectedHead (..)
  , IdentityAllocation (..)
  , Proposal (..)
  , ProposalKind (..)
  , ProposalManifest (..)
  , ProposalRecord (..)
  , recheckProposal
  , validateProposal
  )
import Archive.Door.ProposalContracts (validateProposalContracts)
import Archive.Door.Reach (Held (..))
import Archive.Door.References
  ( StoreIndex
  , insertRecord
  , validateReferences
  )
import Archive.Door.Distinct (distinctOr)
import Archive.Door.PlanProblem
  ( PlanProblem (..)
  , Planner (..)
  , refuseProblem
  )
import Archive.Door.Refusal (PlanRefusal (..), refuse)
import Archive.Door.Transition.Problem (TransitionProblem, transitionProblemMessage)
import Archive.Door.Snapshot.Data
  ( RecordEntry (..)
  , SnapshotData (..)
  )
import Archive.Door.Snapshot.Load (loadSnapshot)
import Archive.Door.Transition.Dispatch
  ( TransitionKey
  , TransitionKind (..)
  , dispatch
  , transitionContractsWithoutHandler
  , transitionDispatchTable
  )
import Archive.Door.Transition.Plan
  ( TransitionPlan (..)
  , TransitionPlanningError (..)
  , transitionPlanningErrorMessage
  , transitionSnapshotError
  )
import Archive.Door.Transition.Replay (snapshotFacts)
import Archive.Door.Transition.Rules
  ( Prepared (..)
  , SnapshotFacts (..)
  , TransitionState (..)
  , validateBatchContracts
  , validateOne
  )
import Archive.Door.Transition.Upgrade (prepareUpgrade)
import Archive.Door.Types
  ( ContractCitation (..)
  , DraftEnvelope (..)
  , Reference (..)
  , ObjectId
  , RevisionId
  , StoredEnvelope (..)
  , objectIdText
  , parseRevisionId
  , revisionIdText
  )
import qualified Archive.GitSnapshot as Git
import Control.Monad (foldM)
import Control.Monad.Trans.Class (lift)
import Control.Monad.Trans.Except (ExceptT (..), except, runExceptT, withExceptT)
import Data.Bifunctor (first)
import Data.Map.Strict (Map)
import qualified Data.Map.Strict as Map
import qualified Data.Set as Set
import Data.Text (Text)
import Data.Time (Day, getCurrentTime, utctDay)

-- | Validate and compute a transition plan against the one committed HEAD
-- selected by 'loadSnapshot'.
dryRunTransition :: FilePath -> Proposal -> IO (Either Text TransitionPlan)
dryRunTransition root proposal = fmap (either (Left . transitionPlanningErrorMessage) Right)
  (dryRunTransitionForCommit root proposal)

-- | Typed form used by persistent transition. The dry-run command renders
-- these errors at its existing text boundary.
dryRunTransitionForCommit
  :: FilePath
  -> Proposal
  -> IO (Either TransitionPlanningError TransitionPlan)
dryRunTransitionForCommit root proposal = fmap fst <$> dryRunTransitionVerified root proposal

-- | Plan a transition and return the snapshot it was planned against, so a
-- commit on top of it can extend that snapshot's verification (ADR 0054).
dryRunTransitionVerified
  :: FilePath
  -> Proposal
  -> IO (Either TransitionPlanningError (TransitionPlan, SnapshotData))
dryRunTransitionVerified root proposal = runExceptT $ do
  snapshotResult <- lift (Git.resolveSnapshot root)
  -- The day the archive's own limits are read on (S-30): today, in UTC.
  today <- lift (utctDay <$> getCurrentTime)
  gitSnapshot <- withExceptT TransitionSnapshotFailure (except snapshotResult)
  loaded <- lift (loadSnapshot gitSnapshot)
  -- Transition planning reads every committed record; read their bytes
  -- in bulk rather than one at a time as each is first used. A failure
  -- here is reported by the read that needs the bytes.
  _ <- lift (Git.prefetchPaths gitSnapshot ["records"])
  snapshot <- withExceptT transitionSnapshotError (except loaded)
  (planning, files) <- ExceptT (prepareUpgrade gitSnapshot snapshot proposal)
  withExceptT TransitionProposalRejected (ExceptT (validateProposalContracts planning proposal))
  plan <- except (first planningRefusal (planTransition today planning proposal))
  withExceptT TransitionProposalRejected (ExceptT (recheckProposal proposal))
  pure
    ( plan
        { plannedTransitionSnapshot = Git.snapshotCommit gitSnapshot
        , plannedGitExecutableIdentity = Git.snapshotExecutableIdentity gitSnapshot
        , plannedSpecificationFiles = if transitionRetry plan then [] else files
        }
    , snapshot
    )
  where
    planningRefusal (PlanRefused problem) = TransitionProposalRejected problem
    planningRefusal (PlanStale problem) = TransitionProposalStale problem

planTransition :: Day -> SnapshotData -> Proposal -> Either PlanRefusal TransitionPlan
planTransition today snapshot proposal = do
  refuseProblem $ if proposalKind proposal /= TransitionProposal
    then Left (WrongProposalKind TransitionPlanner)
    else pure ()
  refuse (validateProposal proposal)
  prepared <- refuse (mapM prepare (proposalRecords proposal))
  refuseProblem (distinctOr (DuplicateComputedRevision TransitionPlanner) (map preparedRevision prepared))
  keys <- refuseTransition (mapM (dispatch (snapshotTransitionKeys snapshot) . preparedDraft) prepared)
  refuseTransition (validateBatchContracts keys prepared)
  facts <- refuseTransition (snapshotFacts snapshot)
  let revisions = map preparedRevision prepared
      present = map (`Map.member` snapshotRevisionIntroducers snapshot) revisions
      retry = completeRetry snapshot revisions
  refuseProblem $ if any id present && not retry
    then Left (ExistingRevisionsNotOneBatch TransitionPlanner)
    else pure ()
  refuseProblem (validateAllocations snapshot proposal prepared retry)
  validateExpectedHeads facts proposal prepared retry
  refuseProblem (if retry then pure () else validateLineage (factHeads facts) prepared)
  refuse (validateReferencesInOrder (factStore facts) prepared)
  finalState <- refuseTransition $ if retry
    then pure (TransitionState (factRetired facts) (factDestroyed facts))
    else validateTransitions facts keys prepared
  refuse (if retry then pure () else mapM_ (validatePublicationReach today snapshot facts) prepared)
  pure TransitionPlan
    { plannedTransitionSnapshot = ""
    , plannedGitExecutableIdentity = ""
    , plannedTransitionRevisions = map revisionIdText revisions
    , plannedTransitionRecords =
        [(revisionIdText (preparedRevision item), preparedValue item) | item <- prepared]
    , transitionRetry = retry
    , resultingRetiredObjects = retiredObjects finalState
    , resultingDestroyedDigests = destroyedDigests finalState
    , plannedSpecificationFiles = []
    }

-- | A publication approval beyond the circle must pass the archive's own
-- sensitivity, rights, and consent on the day it is made (S-30). Only a
-- new transition is checked: a committed one stands as it was approved.
validatePublicationReach :: Day -> SnapshotData -> SnapshotFacts -> Prepared -> Either Text ()
validatePublicationReach today snapshot facts item =
  validateBeyondCircle today (factActiveOperator facts) held draft schemas
  where
    draft = preparedDraft item
    held = [ Held (objectIdText (entryObject entry)) (revisionIdText (entryRevision entry))
             (storedAt (entryEnvelope entry)) (storedDraft (entryEnvelope entry))
           | entry <- Map.elems (factByRevision facts) ]
    schemas = [ contractBundleSchema bundle
              | citation <- envelopeContracts draft
              , Just bundle <- [Map.lookup (contractId citation, contractVersion citation, contractDigest citation)
                                  (snapshotInstalled snapshot)] ]

-- | The canonicalizer writes a revision as @sha256:@ and 64 lowercase hex
-- digits, which 'parseRevisionId' always accepts; the parse gives it its
-- type and cannot refuse.
prepare :: ProposalRecord -> Either Text Prepared
prepare record = do
  (digest, _) <- canonicalizeDraftStrict (proposalRecordValue record)
  revision <- parseRevisionId digest
  pure Prepared
    { preparedRevision = revision
    , preparedValue = proposalRecordValue record
    , preparedDraft = proposalRecordDraft record
    }

completeRetry :: SnapshotData -> [RevisionId] -> Bool
completeRetry snapshot revisions = case mapM lookupIntroducer revisions of
  Just (introducer : rest)
    | all (== introducer) rest ->
        let committed = Map.keysSet (Map.filter (== introducer) (snapshotRevisionIntroducers snapshot))
        in committed == Set.fromList revisions
  _ -> False
  where
    lookupIntroducer revision = Map.lookup revision (snapshotRevisionIntroducers snapshot)

validateAllocations :: SnapshotData -> Proposal -> [Prepared] -> Bool -> Either PlanProblem ()
validateAllocations snapshot proposal prepared retry = do
  let allocations = manifestIdentities (proposalManifest proposal)
      heads = snapshotHeadsByObject snapshot
      drafted = Set.fromList (map (envelopeObject . preparedDraft) prepared)
      newObjects = drafted `Set.difference` Map.keysSet heads
      -- A proposal's allocations are its own text; the objects are compared
      -- by their spelling.
      allocated = Set.fromList (map identityObjectId allocations)
  mapM_ (validateAllocation (snapshotManifestId snapshot)) allocations
  if retry
    then if allocated `Set.isSubsetOf` Set.map objectIdText drafted
      then Right ()
      else Left (AllocationOutsideDrafts TransitionPlanner)
    else if allocated == Set.map objectIdText newObjects
      then Right ()
      else Left (AllocationsNotOnePerNewObject TransitionPlanner)

validateAllocation :: Text -> IdentityAllocation -> Either PlanProblem ()
validateAllocation archive allocation = do
  derived <- either (Left . AllocationUnderivable) Right
    (allocateObjectId archive (identityNamespace allocation) (identityName allocation))
  if derived == identityObjectId allocation
    then Right ()
    else Left AllocationIdMismatch

validateExpectedHeads :: SnapshotFacts -> Proposal -> [Prepared] -> Bool -> Either PlanRefusal ()
validateExpectedHeads facts proposal prepared retry = refuseProblem $ do
  let drafts = Set.fromList
        [ envelopeObject (preparedDraft item) | item <- prepared ]
      references = Set.fromList
        [ referenceObject reference
        | item <- prepared
        , reference <- envelopeReferences (preparedDraft item)
          <> envelopeSupersedes (preparedDraft item)
        ]
      required = drafts `Set.union` references
      supplied = Map.fromList
        [ (expectedObjectId expected, expectedRevisionId expected)
        | expected <- manifestExpectedHeads (proposalManifest proposal)
        ]
  if Map.keysSet supplied /= Set.map objectIdText required
    then Left ExpectedHeadsWrongCover
    else if retry
      then Right ()
      else mapM_ (check drafts supplied) (Set.toList required)
  where
    check drafts supplied objectId = do
      expected <- case Map.lookup (objectIdText objectId) supplied of
        Nothing -> Left ExpectedHeadOmitted
        Just value -> Right value
      actual <- case Map.lookup objectId (factHeads facts) of
        Just value -> Right (Just (revisionIdText value))
        Nothing
          | Set.member objectId drafts -> Right Nothing
          | otherwise -> Left ExpectedHeadMissingObject
      if expected == actual
        then Right ()
        else Left (ExpectedHeadNotCurrent TransitionPlanner)

validateLineage :: Map ObjectId RevisionId -> [Prepared] -> Either PlanProblem ()
validateLineage initial = go initial
  where
    go _ [] = Right ()
    go heads (item : rest) = do
      let objectId = envelopeObject (preparedDraft item)
          supersedes =
            [ revision
            | reference <- envelopeSupersedes (preparedDraft item)
            , Just revision <- [referenceRevision reference]
            ]
      case (Map.lookup objectId heads, supersedes) of
        (Nothing, []) -> pure ()
        (Just current, [previous])
          | current == previous -> pure ()
        _ -> Left (NonLinearSupersession TransitionPlanner)
      go (Map.insert objectId (preparedRevision item) heads) rest

validateReferencesInOrder :: StoreIndex -> [Prepared] -> Either Text ()
validateReferencesInOrder = go
  where
    go _ [] = Right ()
    go index (item : rest) = do
      let revision = preparedRevision item
      validateReferences index revision (preparedDraft item)
      go (insertRecord revision (preparedDraft item) index) rest

validateTransitions :: SnapshotFacts -> [TransitionKey] -> [Prepared]
                    -> Either TransitionProblem TransitionState
validateTransitions facts keys prepared = do
  let initial = TransitionState (factRetired facts) (factDestroyed facts)
  foldM (validateOne facts) initial
    (zip keys prepared)

-- | Refuse with a broken transition rule's message.
refuseTransition :: Either TransitionProblem a -> Either PlanRefusal a
refuseTransition = either (Left . PlanRefused . transitionProblemMessage) Right
