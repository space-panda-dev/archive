{-# LANGUAGE OverloadedStrings #-}

-- | Stable refusal codes for Door's answers (ADR 0058).
--
-- Every failed request Door's service answers carries one code from
-- 'RefusalCode' beside its message. The code is chosen where the failure is
-- typed, never by reading the message. Codes are only ever added; a code is
-- never renamed, reused, or removed. Messages are for people and may change.
module Archive.Door.Refusal
  ( RefusalCode (..)
  , refusalCodeText
  , refusalCodeDescription
  , Refusal (..)
  , refusalValue
  , mutationRefusal
  , PlanRefusal (..)
  , planRefusalMessage
  , refuse
  ) where

import Archive.Door.MutationError (MutationError (..), mutationErrorMessage)
import Archive.GitSnapshot (gitSnapshotErrorMessage)
import Data.Aeson (Value, object, (.=))
import Data.Text (Text)

-- | The published list. Order is documentation order only.
data RefusalCode
  = RequestMalformed
  | ProposalMalformed
  | ProposalRefused
  | ExpectedHeadStale
  | ArchiveUnreadable
  | IntegrityFailure
  | ConfigurationFailure
  | IoFailure
  | SubprocessFailure
  | OperationFailed
  | OutcomeUncertain
  | InternalError
  deriving (Eq, Ord, Show, Enum, Bounded)

refusalCodeText :: RefusalCode -> Text
refusalCodeText code = case code of
  RequestMalformed -> "request_malformed"
  ProposalMalformed -> "proposal_malformed"
  ProposalRefused -> "proposal_refused"
  ExpectedHeadStale -> "expected_head_stale"
  ArchiveUnreadable -> "archive_unreadable"
  IntegrityFailure -> "integrity_failure"
  ConfigurationFailure -> "configuration_failure"
  IoFailure -> "io_failure"
  SubprocessFailure -> "subprocess_failure"
  OperationFailed -> "operation_failed"
  OutcomeUncertain -> "outcome_uncertain"
  InternalError -> "internal_error"

-- | What the code means and what a client should do; published in Door's
-- service description.
refusalCodeDescription :: RefusalCode -> Text
refusalCodeDescription code = case code of
  RequestMalformed -> "The service request itself is not a valid request. Fix the client."
  ProposalMalformed -> "The proposal directory, manifest, or a draft cannot be read as a proposal. Fix the proposal."
  ProposalRefused -> "The proposal is well formed, but Door refuses it against this archive. Change the proposal."
  ExpectedHeadStale -> "An expected head no longer matches the archive. Read the current heads, rebuild the proposal, and send it again."
  ArchiveUnreadable -> "Door could not read the archive's committed state. Nothing was written; the operator must look."
  IntegrityFailure -> "Bytes or committed evidence did not verify. Nothing was admitted; the operator must look."
  ConfigurationFailure -> "Door's configuration, such as its Git or git-annex executable, is wrong or changed. Nothing was written."
  IoFailure -> "An operating-system read or write failed."
  SubprocessFailure -> "A program Door runs, such as Git or git-annex, failed."
  OperationFailed -> "The operation failed in a way that has no narrower code yet."
  OutcomeUncertain -> "Door cannot tell whether the write committed. Send the identical proposal again: a committed batch answers as a retry."
  InternalError -> "Door failed unexpectedly. Report it."

-- | One failed request: its code, its message, and any content keys that
-- were transferred but stay unadmitted storage.
data Refusal = Refusal
  { refusalCode :: RefusalCode
  , refusalMessage :: Text
  , refusalUnadmittedKeys :: [Text]
  } deriving (Eq, Show)

refusalValue :: Refusal -> Value
refusalValue refusal = object $
  [ "code" .= refusalCodeText (refusalCode refusal)
  , "message" .= refusalMessage refusal
  ] <> [ "unadmitted_keys" .= refusalUnadmittedKeys refusal | not (null (refusalUnadmittedKeys refusal)) ]

-- | Each mutation category has exactly one code.
mutationRefusal :: MutationError -> Refusal
mutationRefusal failure = case failure of
  MutationRejected message -> Refusal ProposalRefused message []
  MutationStale message -> Refusal ExpectedHeadStale message []
  MutationConfigurationFailure message -> Refusal ConfigurationFailure message []
  MutationIoFailure message -> Refusal IoFailure message []
  MutationSubprocessFailure message -> Refusal SubprocessFailure message []
  MutationUncertain message -> Refusal OutcomeUncertain message []
  MutationIntegrityFailure message -> Refusal IntegrityFailure message []
  MutationSnapshotFailure problem -> Refusal ArchiveUnreadable (gitSnapshotErrorMessage problem) []
  MutationOperationFailure message -> Refusal OperationFailed message []
  MutationCleanupFailure original _ ->
    (mutationRefusal original) { refusalMessage = mutationErrorMessage failure }
  MutationUnadmittedContent original keys ->
    (mutationRefusal original)
      { refusalMessage = mutationErrorMessage failure
      , refusalUnadmittedKeys = keys
      }

-- | A planner's refusal: either the proposal is refused, or it relied on an
-- expected head that is no longer current.
data PlanRefusal
  = PlanRefused Text
  | PlanStale Text
  deriving (Eq, Show)

planRefusalMessage :: PlanRefusal -> Text
planRefusalMessage (PlanRefused message) = message
planRefusalMessage (PlanStale message) = message

refuse :: Either Text a -> Either PlanRefusal a
refuse = either (Left . PlanRefused) Right
