{-# LANGUAGE OverloadedStrings #-}

-- | The archive's transition facts, rebuilt by replaying its history.
--
-- Who the active operator is, what is retired, and what is destroyed are
-- derived from committed records in commit order, never stored, so they are
-- regenerable from the evidence alone.
module Archive.Door.Transition.Replay
  ( snapshotFacts
  ) where

import Archive.Door.References
  ( StoreIndex
  , emptyIndex
  , insertRecord
  , validateReferences
  )
import Archive.Door.Snapshot.Data
  ( RecordEntry (..)
  , SnapshotData (..)
  )
import Archive.Door.Transition.Dispatch
  ( TransitionKey (..)
  , TransitionKind (..)
  , dispatch
  , transitionCitationKind
  )
import Archive.Door.Transition.Problem (TransitionProblem (..), outsideCheck)
import Archive.Door.Transition.Rules
  ( Prepared (..)
  , SnapshotFacts (..)
  , TransitionState (..)
  , artifactContent
  , objectText
  , oneReference
  , requirePerson
  , validateBatchContracts
  , validateOne
  )
import Archive.Door.Types
  ( DraftEnvelope (..)
  , RecordBody (..)
  , Reference (..)
  , ObjectId
  , RevisionId
  , StoredEnvelope (..)
  , agentIdText
  )
import Control.Monad (foldM)
import Data.Aeson (Value (Null))
import Data.Map.Strict (Map)
import qualified Data.Map.Strict as Map
import Data.Set (Set)
import qualified Data.Set as Set
import Data.Text (Text)

data ReplayState = ReplayState
  { replayHeads :: Map ObjectId RevisionId
  , replaySeen :: Map RevisionId RecordEntry
  , replayActiveOperator :: Maybe Text
  , replayRetired :: Set Text
  , replayDestroyed :: Set Text
  , replayStore :: StoreIndex
  }

snapshotFacts :: SnapshotData -> Either TransitionProblem SnapshotFacts
snapshotFacts snapshot = do
  initialCommit <- case snapshotCommitOrder snapshot of
    commit : _ -> Right commit
    [] -> Left HistoryEmpty
  let additions = snapshotRevisionIntroducers snapshot
      byCommit = Map.fromListWith (<>)
        [ (commit, [entry])
        | entry <- snapshotRecords snapshot
        , Just commit <- [Map.lookup (entryRevision entry) additions]
        ]
  if Map.size additions == length (snapshotRecords snapshot)
    then pure ()
    else Left HistoryIncomplete
  replayed <- foldM (replayCommit snapshot initialCommit byCommit) emptyReplay
    (snapshotCommitOrder snapshot)
  active <- maybe
    (Left NoActiveOperator)
    Right
    (replayActiveOperator replayed)
  if replayHeads replayed == snapshotHeadsByObject snapshot
    then pure ()
    else Left HeadsDisagree
  pure SnapshotFacts
    { factByRevision = replaySeen replayed
    , factHeads = replayHeads replayed
    , factActiveOperator = active
    , factRetired = replayRetired replayed
    , factDestroyed = replayDestroyed replayed
    , factStore = replayStore replayed
    }

emptyReplay :: ReplayState
emptyReplay = ReplayState
  { replayHeads = Map.empty
  , replaySeen = Map.empty
  , replayActiveOperator = Nothing
  , replayRetired = Set.empty
  , replayDestroyed = Set.empty
  , replayStore = emptyIndex
  }

replayCommit
  :: SnapshotData
  -> Text
  -> Map Text [RecordEntry]
  -> ReplayState
  -> Text
  -> Either TransitionProblem ReplayState
replayCommit snapshot initialCommit byCommit state commit = do
  let entries = Map.findWithDefault [] commit byCommit
      classify = entryKind (snapshotTransitionKeys snapshot)
      initializations = [entry | entry <- entries, classify entry == Just OperatorInitialization]
      transitions = [entry | entry <- entries, maybe False (/= OperatorInitialization) (classify entry)]
      ordinary = [entry | entry <- entries, classify entry == Nothing]
  case (initializations, transitions) of
    ([], []) -> applyUnorderedEntries state ordinary
    ([initialization], []) -> do
      if commit == initialCommit && replayActiveOperator state == Nothing
        then pure ()
        else Left InitializationNotAtRoot
      if length ordinary == 1
        then pure ()
        else Left RootCommitShape
      state' <- applyUnorderedEntries state ordinary
      operator <- validateInitialization state' initialization
      state'' <- applyEntry state' initialization
      pure state'' { replayActiveOperator = Just operator }
    ([], _) -> do
      if null ordinary
        then replayTransitionBatch snapshot state transitions
        else Left MixedTransitionCommit
    _ -> Left InitializationSharesCommit

validateInitialization :: ReplayState -> RecordEntry -> Either TransitionProblem Text
validateInitialization state entry = do
  if replayActiveOperator state == Nothing
    then pure ()
    else Left OperatorNotUnique
  let draft = storedDraft (entryEnvelope entry)
      references = envelopeReferences draft
  author <- oneReference "author" references
  authorizer <- oneReference "authorizer" references
  appoints <- oneReference "appoints" references
  requireSameReference author authorizer
  requireSameReference author appoints
  personEntry <- resolveReplayReference state author
  requirePerson personEntry
  requireReplayCurrent state author
  let operator = objectText (referenceObject author)
  if agentIdText (envelopeAgent draft) == operator
    then Right operator
    else Left InitializationAgentMismatch

requireSameReference :: Reference -> Reference -> Either TransitionProblem ()
requireSameReference left right =
  if referenceObject left == referenceObject right
      && referenceRevision left == referenceRevision right
    then Right ()
    else Left InitializationReferencesDiffer

replayTransitionBatch :: SnapshotData -> ReplayState -> [RecordEntry]
                      -> Either TransitionProblem ReplayState
replayTransitionBatch snapshot state entries = do
  active <- maybe
    (Left TransitionBeforeInitialization)
    Right
    (replayActiveOperator state)
  ordered <- orderHistoricalTransitions (snapshotTransitionKeys snapshot) entries
  foldM (replayOneTransition active) state ordered

replayOneTransition :: Text -> ReplayState -> (TransitionKey, Prepared, RecordEntry)
                    -> Either TransitionProblem ReplayState
replayOneTransition active state (key, prepared, entry) = do
  let facts = replayFacts active state
      transitionState = TransitionState
        (replayRetired state)
        (replayDestroyed state)
  stateEffect <- validateOne facts transitionState (key, prepared)
  state' <- applyEntry state entry
  pure state'
    { replayRetired = retiredObjects stateEffect
    , replayDestroyed = destroyedDigests stateEffect
    }

replayFacts :: Text -> ReplayState -> SnapshotFacts
replayFacts active state = SnapshotFacts
  { factByRevision = replaySeen state
  , factHeads = replayHeads state
  , factActiveOperator = active
  , factRetired = replayRetired state
  , factDestroyed = replayDestroyed state
  , factStore = replayStore state
  }

orderHistoricalTransitions :: Set (Text, Text, Text) -> [RecordEntry]
                           -> Either TransitionProblem [(TransitionKey, Prepared, RecordEntry)]
orderHistoricalTransitions installed entries = do
  triples <- mapM prepareEntry entries
  ordered <- orderByPredecessor triples
  validateBatchContracts
    [key | (key, _, _) <- ordered]
    [prepared | (_, prepared, _) <- ordered]
  pure ordered
  where
    prepareEntry entry = do
      key <- dispatch installed (storedDraft (entryEnvelope entry))
      let prepared = Prepared
            (entryRevision entry)
            Null
            (storedDraft (entryEnvelope entry))
      pure (key, prepared, entry)

orderByPredecessor :: [(TransitionKey, Prepared, RecordEntry)]
                   -> Either TransitionProblem [(TransitionKey, Prepared, RecordEntry)]
orderByPredecessor [] = Left EmptyTransitionCommit
orderByPredecessor triples = do
  roots <- filterMNoPredecessor triples
  root <- case roots of
    [value] -> Right value
    _ -> Left NotOnePredecessorRoot
  walk [root] (removeTriple root triples)
  where
    walk ordered [] = Right ordered
    walk ordered remaining = do
      let (_, previous, _) = last ordered
          successors = filter (follows previous) remaining
      next <- case successors of
        [value] -> Right value
        [] -> Left PredecessorChainBroken
        _ -> Left PredecessorChainForks
      walk (ordered <> [next]) (removeTriple next remaining)

    follows previous (_, item, _) = case predecessorReference item of
      Right (Just reference) ->
        referenceObject reference == envelopeObject (preparedDraft previous)
        && referenceRevision reference == Just (preparedRevision previous)
      _ -> False

filterMNoPredecessor :: [(TransitionKey, Prepared, RecordEntry)]
                     -> Either TransitionProblem [(TransitionKey, Prepared, RecordEntry)]
filterMNoPredecessor = foldM step []
  where
    step roots triple@(_, item, _) = do
      predecessor <- predecessorReference item
      pure $ case predecessor of
        Nothing -> roots <> [triple]
        Just _ -> roots

predecessorReference :: Prepared -> Either TransitionProblem (Maybe Reference)
predecessorReference item = case
    filter ((== "transition_predecessor") . referenceRole)
      (envelopeReferences (preparedDraft item)) of
  [] -> Right Nothing
  [reference] -> Right (Just reference)
  _ -> Left MultiplePredecessors

removeTriple :: (TransitionKey, Prepared, RecordEntry)
             -> [(TransitionKey, Prepared, RecordEntry)]
             -> [(TransitionKey, Prepared, RecordEntry)]
removeTriple (_, prepared, _) =
  filter ((/= preparedRevision prepared) . preparedRevision . middle)
  where
    middle (_, value, _) = value

applyUnorderedEntries :: ReplayState -> [RecordEntry] -> Either TransitionProblem ReplayState
applyUnorderedEntries state [] = Right state
applyUnorderedEntries state pending = case firstApplicable state pending of
  Nothing -> Left UnreplayableRecords
  Just entry -> do
    state' <- applyEntry state entry
    applyUnorderedEntries state' (filter ((/= entryRevision entry) . entryRevision) pending)

firstApplicable :: ReplayState -> [RecordEntry] -> Maybe RecordEntry
firstApplicable state = firstWhere (isApplicable state)

firstWhere :: (a -> Bool) -> [a] -> Maybe a
firstWhere _ [] = Nothing
firstWhere predicate (value : rest)
  | predicate value = Just value
  | otherwise = firstWhere predicate rest

isApplicable :: ReplayState -> RecordEntry -> Bool
isApplicable state entry = case applyEntry state entry of
  Right _ -> True
  Left _ -> False

applyEntry :: ReplayState -> RecordEntry -> Either TransitionProblem ReplayState
applyEntry state entry = do
  let draft = storedDraft (entryEnvelope entry)
      objectId = entryObject entry
      supersedes =
        [ revision
        | reference <- envelopeSupersedes draft
        , Just revision <- [referenceRevision reference]
        ]
  case envelopeBody draft of
    ArtifactRecord payload -> do
      (digest, _) <- artifactContent payload
      if Set.member digest (replayDestroyed state)
        then Left DestroyedContentReadmitted
        else Right ()
    _ -> Right ()
  case (Map.lookup objectId (replayHeads state), supersedes) of
    (Nothing, []) -> Right ()
    (Just current, [previous]) | current == previous -> Right ()
    _ -> Left HistoricalHeadNotExtended
  let revision = entryRevision entry
  outsideCheck (validateReferences (replayStore state) revision draft)
  pure state
    { replayHeads = Map.insert objectId (entryRevision entry) (replayHeads state)
    , replaySeen = Map.insert (entryRevision entry) entry (replaySeen state)
    , replayStore = insertRecord revision draft (replayStore state)
    }

resolveReplayReference :: ReplayState -> Reference -> Either TransitionProblem RecordEntry
resolveReplayReference state reference = do
  revision <- maybe
    (Left ReferenceUnpinned)
    Right
    (referenceRevision reference)
  entry <- maybe
    (Left ReplayReferenceNotEarlier)
    Right
    (Map.lookup revision (replaySeen state))
  if entryObject entry == referenceObject reference
    then Right entry
    else Left ReferenceOtherObject

requireReplayCurrent :: ReplayState -> Reference -> Either TransitionProblem ()
requireReplayCurrent state reference = do
  revision <- maybe
    (Left ReferenceUnpinned)
    Right
    (referenceRevision reference)
  case Map.lookup (referenceObject reference) (replayHeads state) of
    Just current | current == revision -> Right ()
    _ -> Left ReplayReferenceNotCurrent

entryKind :: Set (Text, Text, Text) -> RecordEntry -> Maybe TransitionKind
entryKind installed entry =
  fst <$> transitionCitationKind installed (storedDraft (entryEnvelope entry))
