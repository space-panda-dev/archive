{-# LANGUAGE OverloadedStrings #-}

-- | Why a transition, new or replayed from history, breaks the transition rules.
--
-- "Archive.Door.Transition.Rules" and "Archive.Door.Transition.Replay" return
-- these values; the planner renders them with 'transitionProblemMessage' when
-- it refuses. The messages are compared byte for byte by Door's clients and
-- the Python suites, so this function is the one place they are spelled.
module Archive.Door.Transition.Problem
  ( TransitionProblem (..)
  , transitionProblemMessage
  , outsideCheck
  ) where

import Data.Bifunctor (first)
import Data.Text (Text)
import qualified Data.Text as Text

data TransitionProblem
  -- Batch shape and predecessor chains.
  = MultiRecordNeedsVersion2
  | EmptyTransitionProposal
  | PredecessorUnpinned
  | PredecessorNotPreceding
  | FirstHasPredecessor
  | NotExactlyOneReference Text
    -- ^ the reference role
  -- The per-kind rules.
  | InitializationOnly
  | ApprovalWithoutCovers
  | WithdrawsNotApproval
  | WithdrawsUnpinned
  | AlreadyWithdrawn
  | AlreadyRetired
  | DestroyWithoutReferences
  | AlreadyDestroyed
  | DestroyTargetNotRetired
  | DestroyContentMismatch
  | DestroyNotArtifact
  | UnretiredReferrers [Text]
    -- ^ the unretired objects that still refer to the content
  | DependentReachabilityUnsupported
  | ExternalSurvivorUnsupported
  | NotAClaim
  | ArtifactPayloadNotObject
  | ContentSizeInvalid
  | SizeRequired
  -- Actors and references.
  | AuthorizerNotActive
  | AgentNotAuthor
  | ReferenceUnpinned
  | ReferenceUncommitted
  | ReferenceOtherObject
  | ReferenceNotCurrent
  | NotPersonEntity
  -- Replaying committed history.
  | HistoryEmpty
  | HistoryIncomplete
  | NoActiveOperator
  | HeadsDisagree
  | InitializationNotAtRoot
  | RootCommitShape
  | MixedTransitionCommit
  | InitializationSharesCommit
  | OperatorNotUnique
  | InitializationAgentMismatch
  | InitializationReferencesDiffer
  | TransitionBeforeInitialization
  | EmptyTransitionCommit
  | NotOnePredecessorRoot
  | PredecessorChainBroken
  | PredecessorChainForks
  | MultiplePredecessors
  | UnreplayableRecords
  | DestroyedContentReadmitted
  | HistoricalHeadNotExtended
  | ReplayReferenceNotEarlier
  | ReplayReferenceNotCurrent
  -- | A check owned by another module (JSON access, dispatch, references)
  -- failed; its own message passes through unchanged.
  | OutsideCheck Text
  deriving (Eq, Show)

-- | Lift another module's 'Text' failure into this family unchanged.
outsideCheck :: Either Text a -> Either TransitionProblem a
outsideCheck = first OutsideCheck

transitionProblemMessage :: TransitionProblem -> Text
transitionProblemMessage problem = case problem of
  MultiRecordNeedsVersion2 -> "multi-record transition batches require version 2 retire/destroy contracts"
  EmptyTransitionProposal -> "transition proposal must contain at least one record"
  PredecessorUnpinned -> "transition_predecessor must pin an exact revision"
  PredecessorNotPreceding -> "transition_predecessor must pin the immediately preceding draft"
  FirstHasPredecessor -> "the first transition record must not have a transition_predecessor"
  NotExactlyOneReference role -> role <> " transition must contain exactly one reference"
  InitializationOnly -> "unsupported: operator initialization is initialization-only"
  ApprovalWithoutCovers -> "publication approval requires at least one covers reference"
  WithdrawsNotApproval -> "withdraws reference must target a publication approval"
  WithdrawsUnpinned -> "withdraws must pin an exact revision"
  AlreadyWithdrawn -> "publication approval is already withdrawn"
  AlreadyRetired -> "retire target is already retired"
  DestroyWithoutReferences -> "destroy requires at least one destroys reference"
  AlreadyDestroyed -> "content has already been destroyed"
  DestroyTargetNotRetired -> "destroy target is not retired"
  DestroyContentMismatch -> "destroy content does not match every destroys reference"
  DestroyNotArtifact -> "destroy references must name artifact revisions"
  UnretiredReferrers objects ->
    "destroy refused while an unretired object refers to content: " <> Text.intercalate "," objects
  DependentReachabilityUnsupported ->
    "unsupported: no installed artifact contract defines dependent reachability"
  ExternalSurvivorUnsupported -> "unsupported: external survivor evidence contracts are not installed"
  NotAClaim -> "transition record must be a claim"
  ArtifactPayloadNotObject -> "artifact payload must be an object"
  ContentSizeInvalid -> "content size must be a non-negative integer"
  SizeRequired -> "size is required"
  AuthorizerNotActive -> "transition authorizer is not the active operator"
  AgentNotAuthor -> "transition agent must identify its author"
  ReferenceUnpinned -> "transition references must pin an exact revision"
  ReferenceUncommitted -> "transition reference does not name a committed revision"
  ReferenceOtherObject -> "transition reference revision belongs to another object"
  ReferenceNotCurrent -> "transition reference must pin the current object head"
  NotPersonEntity -> "transition author and authorizer must be person entities"
  HistoryEmpty -> "archive history is empty"
  HistoryIncomplete -> "record history does not account for every committed record"
  NoActiveOperator -> "no active operator is established by a transition claim"
  HeadsDisagree -> "replayed object heads disagree with the committed snapshot"
  InitializationNotAtRoot -> "operator initialization must occur exactly once in the archive root commit"
  RootCommitShape -> "archive root commit must contain exactly the appointed person and initialization claim"
  MixedTransitionCommit -> "a transition commit must contain only one transition batch"
  InitializationSharesCommit -> "operator initialization cannot share a commit with another transition"
  OperatorNotUnique -> "active operator is not uniquely established"
  InitializationAgentMismatch -> "operator initialization agent must identify its appointed person"
  InitializationReferencesDiffer -> "operator initialization references must pin the same person revision"
  TransitionBeforeInitialization -> "transition history precedes operator initialization"
  EmptyTransitionCommit -> "transition commit contains no transition records"
  NotOnePredecessorRoot -> "committed transition batch must have exactly one predecessor-chain root"
  PredecessorChainBroken -> "committed transition predecessor chain is missing or crosses a batch"
  PredecessorChainForks -> "committed transition predecessor chain forks"
  MultiplePredecessors -> "transition record has multiple transition_predecessor references"
  UnreplayableRecords -> "committed records cannot be replayed from their historical predecessors"
  DestroyedContentReadmitted -> "destroyed content may not be admitted again"
  HistoricalHeadNotExtended -> "historical record does not extend the then-current object head"
  ReplayReferenceNotEarlier -> "transition reference does not name an earlier committed revision"
  ReplayReferenceNotCurrent -> "transition reference must pin the then-current object head"
  OutsideCheck message -> message
