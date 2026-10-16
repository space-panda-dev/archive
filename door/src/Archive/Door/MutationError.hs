{-# LANGUAGE OverloadedStrings #-}

-- | Typed failures returned by Door mutation entry points.
--
-- This is a propagation bridge, not a complete taxonomy: planner, recovery,
-- and subprocess helpers still expose some diagnostics as 'Text'. Those are
-- carried by 'MutationOperationFailure' until their source APIs can return
-- narrower errors without guessing from message contents.
module Archive.Door.MutationError
  ( MutationError (..)
  , mutationErrorMessage
  ) where

import Data.Text (Text)
import qualified Data.Text as Text
import Archive.GitSnapshot (GitSnapshotError, gitSnapshotErrorMessage)

-- | Keep refusal, operating-system, subprocess, integrity, and cleanup
-- failures distinct so a caller cannot mistake a failed mutation for a
-- successful empty result.
data MutationError
  = MutationRejected Text
  -- | The proposal relied on an expected head that is no longer current.
  | MutationStale Text
  | MutationConfigurationFailure Text
  | MutationIoFailure Text
  | MutationSubprocessFailure Text
  | MutationUncertain Text
  | MutationIntegrityFailure Text
  | MutationSnapshotFailure GitSnapshotError
  -- | A lower layer still returns a diagnostic string whose source has not
  -- yet been assigned a narrower category. Keep it typed without guessing.
  | MutationOperationFailure Text
  | MutationCleanupFailure MutationError MutationError
  -- | The batch failed after this operation obtained these keys. Their
  -- content stays present as reported, unadmitted storage; Door never
  -- deletes it.
  | MutationUnadmittedContent MutationError [Text]
  deriving (Eq, Show)

mutationErrorMessage :: MutationError -> Text
mutationErrorMessage failure = case failure of
  MutationRejected message -> message
  MutationStale message -> message
  MutationConfigurationFailure message -> message
  MutationIoFailure message -> message
  MutationSubprocessFailure message -> message
  MutationUncertain message -> message
  MutationIntegrityFailure message -> message
  MutationSnapshotFailure problem -> gitSnapshotErrorMessage problem
  MutationOperationFailure message -> message
  MutationCleanupFailure original cleanup ->
    mutationErrorMessage original <> "; cleanup also failed: "
      <> mutationErrorMessage cleanup
  MutationUnadmittedContent original keys ->
    mutationErrorMessage original <> "; transferred content remains unadmitted storage: "
      <> Text.intercalate ", " keys
