{-# LANGUAGE OverloadedStrings #-}

-- | Read-only admission validation against one immutable Git snapshot.
--
-- This module never reads the worktree, writes an archive, invokes
-- git-annex, or resolves a second commit. The snapshot is the authority for
-- every manifest, contract, record, and introducer used by the dry run.
-- Loading the snapshot lives in "Archive.Door.Snapshot.Load" and the
-- proposal's contract checks in "Archive.Door.ProposalContracts"; this
-- module joins them to the planner and re-exports what callers used here.
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
import Archive.Door.Proposal (Proposal, recheckProposal)
import Archive.Door.ProposalContracts (validateProposalContracts)
import Archive.Door.Refusal (PlanRefusal (..))
import Archive.Door.Snapshot.Data
  ( AdmissionSnapshotLoadError (..)
  , RecordEntry (..)
  , SnapshotData (..)
  , snapshotVerification
  )
import Archive.Door.Snapshot.Load (loadSnapshot)
import Archive.Door.Snapshot.Problem (snapshotProblemMessage)
import Archive.Door.Snapshot.Memo (forgetSnapshots, rememberCommitted)
import Archive.GitSnapshot
  ( GitSnapshotError
  , gitSnapshotErrorMessage
  )
import qualified Archive.GitSnapshot as Git
import Control.Monad.Trans.Except (ExceptT (..), except, runExceptT, withExceptT)
import Data.Bifunctor (first)
import qualified Data.Map.Strict as Map
import qualified Data.Set as Set
import Data.Text (Text)

-- | Preserve where a planning failure came from so persistent admission can
-- distinguish a refusal from an invalid committed snapshot or a Git failure.
data AdmissionPlanningError
  = AdmissionSnapshotFailure GitSnapshotError
  | AdmissionSnapshotIntegrityFailure Text
  | AdmissionProposalRejected Text
  | AdmissionProposalStale Text
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
dryRunAdmissionVerified root proposal = runExceptT $ do
  snapshot <- withExceptT AdmissionSnapshotFailure (ExceptT (Git.resolveSnapshot root))
  data' <- withExceptT planningSnapshotError (ExceptT (loadSnapshot snapshot))
  withExceptT AdmissionProposalRejected (ExceptT (validateProposalContracts data' proposal))
  let admissionSnapshot = AdmissionSnapshot
        { snapshotArchiveId = snapshotManifestId data'
        , snapshotHeads = snapshotHeadsByObject data'
        , snapshotRevisions = Set.fromList
            (Map.keys (snapshotRevisionIntroducers data'))
        , snapshotRevisionBatches = snapshotRevisionIntroducers data'
        , snapshotReferenceIndex = snapshotStore data'
        , snapshotTransitionContracts = snapshotTransitionKeys data'
        }
  plan <- except (first planningRefusal (planAdmission admissionSnapshot proposal))
  withExceptT AdmissionProposalRejected (ExceptT (recheckProposal proposal))
  pure
    ( plan
        { plannedAdmissionSnapshot = Git.snapshotCommit snapshot
        , plannedGitExecutableIdentity = Git.snapshotExecutableIdentity snapshot
        }
    , data'
    )
  where
    planningRefusal (PlanRefused problem) = AdmissionProposalRejected problem
    planningRefusal (PlanStale problem) = AdmissionProposalStale problem

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

planningSnapshotError :: AdmissionSnapshotLoadError -> AdmissionPlanningError
planningSnapshotError problem = case problem of
  AdmissionSnapshotGitError issue -> AdmissionSnapshotFailure issue
  AdmissionSnapshotIntegrityError problem' -> AdmissionSnapshotIntegrityFailure (snapshotProblemMessage problem')

snapshotError :: GitSnapshotError -> Text
snapshotError = gitSnapshotErrorMessage
