{-# LANGUAGE OverloadedStrings #-}

-- | Why the admission or transition planner refuses a proposal.
--
-- Both planners make the same batch-level checks (retry evidence, identity
-- allocations, linear supersession, expected heads) and differ only in their
-- wording, so each check is one constructor and 'Planner' picks the wording.
-- 'planProblemRefusal' is the only place the messages are spelled; clients
-- and the Python suites compare them byte for byte, so they are kept exactly.
module Archive.Door.PlanProblem
  ( Planner (..)
  , PlanProblem (..)
  , planProblemRefusal
  , refuseProblem
  ) where

import Archive.Door.Refusal (PlanRefusal (..))
import Data.Text (Text)

data Planner = AdmissionPlanner | TransitionPlanner
  deriving (Eq, Show)

data PlanProblem
  = WrongProposalKind Planner
  | DuplicateComputedRevision Planner
  | ExistingRevisionsNotOneBatch Planner
  | TransitionContractNotAdmissible
  | AllocationOutsideDrafts Planner
  | AllocationsNotOnePerNewObject Planner
  | AllocationUnderivable Text
    -- ^ the UUIDv5 allocation itself failed; its message passes through
  | AllocationIdMismatch
  | NonLinearSupersession Planner
  | ExpectedHeadsWrongCover
  | ExpectedHeadOmitted
  | ExpectedReferenceHeadNotCurrent
  | ExpectedHeadMissingObject
  | ExpectedHeadNotCurrent Planner
  deriving (Eq, Show)

-- | A stale expected head is the one problem a client fixes by re-reading
-- the archive, so it alone becomes 'PlanStale'.
planProblemRefusal :: PlanProblem -> PlanRefusal
planProblemRefusal problem = case problem of
  WrongProposalKind AdmissionPlanner -> PlanRefused "admission planning requires an admission proposal"
  WrongProposalKind TransitionPlanner -> PlanRefused "transition planning requires a transition proposal"
  DuplicateComputedRevision AdmissionPlanner -> PlanRefused "computed revision_id must be unique"
  DuplicateComputedRevision TransitionPlanner -> PlanRefused "computed transition revision must be unique"
  ExistingRevisionsNotOneBatch AdmissionPlanner ->
    PlanRefused "existing revisions are not evidenced as one committed admission batch"
  ExistingRevisionsNotOneBatch TransitionPlanner ->
    PlanRefused "existing transition revisions are not one committed batch"
  TransitionContractNotAdmissible ->
    PlanRefused "unsupported: transition contracts cannot be admitted generically"
  AllocationOutsideDrafts AdmissionPlanner -> PlanRefused "identities must refer to drafted objects"
  AllocationOutsideDrafts TransitionPlanner -> PlanRefused "identities must refer to drafted transition objects"
  AllocationsNotOnePerNewObject AdmissionPlanner ->
    PlanRefused "identities must contain exactly one allocation for each new object"
  AllocationsNotOnePerNewObject TransitionPlanner ->
    PlanRefused "identities must contain exactly one allocation for each new transition object"
  AllocationUnderivable message -> PlanRefused message
  AllocationIdMismatch -> PlanRefused "identity allocation object_id does not match UUIDv5 allocation"
  NonLinearSupersession AdmissionPlanner ->
    PlanRefused "proposed revisions must extend one linear supersession head"
  NonLinearSupersession TransitionPlanner ->
    PlanRefused "proposed transitions must extend one linear supersession head"
  ExpectedHeadsWrongCover -> PlanRefused "expected_heads must cover exactly drafted and referenced objects"
  ExpectedHeadOmitted -> PlanRefused "expected_heads omitted a required object"
  ExpectedReferenceHeadNotCurrent -> PlanStale "expected reference head is stale"
  ExpectedHeadMissingObject -> PlanRefused "expected head names a missing object"
  ExpectedHeadNotCurrent AdmissionPlanner -> PlanStale "expected head is stale"
  ExpectedHeadNotCurrent TransitionPlanner ->
    PlanStale "expected head is stale or has the wrong null expectation"

refuseProblem :: Either PlanProblem a -> Either PlanRefusal a
refuseProblem = either (Left . planProblemRefusal) Right
