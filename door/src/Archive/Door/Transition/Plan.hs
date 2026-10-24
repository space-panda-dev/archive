{-# LANGUAGE OverloadedStrings #-}

-- | What transition planning answers: a plan, or where its failure came from.
--
-- Persistent transition tells a refusal from a stale expected head, an
-- invalid committed snapshot, and a Git failure by these constructors, never
-- by reading a message.
module Archive.Door.Transition.Plan
  ( TransitionPlan (..)
  , TransitionPlanningError (..)
  , transitionPlanningErrorMessage
  , transitionSnapshotError
  ) where

import Archive.Door.Snapshot.Data (AdmissionSnapshotLoadError (..))
import Archive.GitSnapshot (GitSnapshotError, gitSnapshotErrorMessage)
import Data.Aeson (Value)
import qualified Data.ByteString.Lazy as LazyByteString
import Data.Set (Set)
import Data.Text (Text)

data TransitionPlan = TransitionPlan
  { plannedTransitionSnapshot :: Text
  , plannedGitExecutableIdentity :: Text
  , plannedTransitionRevisions :: [Text]
  , plannedTransitionRecords :: [(Text, Value)]
  , transitionRetry :: Bool
  , resultingRetiredObjects :: Set Text
  , resultingDestroyedDigests :: Set Text
  , plannedSpecificationFiles :: [(FilePath, LazyByteString.ByteString)]
  -- ^ For a specification upgrade (ADR 0068), the replaced manifest and the
  -- new release's documents and bundles, by archive-relative path; empty for
  -- every other transition and for a retry.
  }
  deriving (Eq, Show)

data TransitionPlanningError
  = TransitionSnapshotFailure GitSnapshotError
  | TransitionSnapshotIntegrityFailure Text
  | TransitionProposalRejected Text
  | TransitionProposalStale Text
  deriving (Eq, Show)

transitionPlanningErrorMessage :: TransitionPlanningError -> Text
transitionPlanningErrorMessage problem = case problem of
  TransitionSnapshotFailure issue -> gitSnapshotErrorMessage issue
  TransitionSnapshotIntegrityFailure message -> message
  TransitionProposalRejected message -> message
  TransitionProposalStale message -> message

transitionSnapshotError :: AdmissionSnapshotLoadError -> TransitionPlanningError
transitionSnapshotError problem = case problem of
  AdmissionSnapshotGitError issue -> TransitionSnapshotFailure issue
  AdmissionSnapshotIntegrityError message -> TransitionSnapshotIntegrityFailure message
