{-# LANGUAGE OverloadedStrings #-}

-- | Pure transition planning against one committed Git snapshot.
--
-- This module is deliberately below the command seam: it retains no proposal
-- files, writes no archive state, and never asks git-annex to perform an act.
-- The caller receives the revisions and derived state that a transition would
-- produce, after exact contract dispatch and the state fences have passed.
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

import Archive.Door.AdmissionDryRun
  ( AdmissionSnapshotLoadError (..)
  , RecordEntry (..)
  , SnapshotData (..)
  , loadSnapshot
  , validateProposalContracts
  )
import Archive.Door.Canonical (canonicalizeDraftStrict)
import Archive.Door.Contracts (ContractBundle)
import Archive.Door.Json (decodeUnique)
import Archive.Door.Layout
  ( archiveManifestBytes
  , makeArchiveManifest
  , manifestArchiveId
  , manifestLayout
  , manifestSpec
  , parseArchiveManifest
  , specVersion
  )
import Archive.Door.SpecificationUpgrade
  ( SpecificationRelease (..)
  , UpgradeTarget (..)
  , compareSpecifications
  , loadUpgradeTarget
  , upgradeContractId
  , upgradeContractVersion
  )
import Archive.Door.Refusal (PlanRefusal (..), refuse)
import Archive.Door.Identity (allocateObjectId)
import Archive.Door.Proposal
  ( ExpectedHead (..)
  , IdentityAllocation (..)
  , Proposal (..)
  , ProposalKind (..)
  , ProposalManifest (..)
  , ProposalRecord (..)
  , validateProposal
  , recheckProposal
  )
import Archive.Door.References
  ( StoreIndex
  , emptyIndex
  , insertRecord
  , validateReferences
  )
import Archive.Door.Types
  ( ContractCitation (..)
  , agentIdText
  , DraftEnvelope (..)
  , ObjectId
  , objectIdText
  , RecordBody (..)
  , Reference (..)
  , RevisionId, parseRevisionId, revisionIdText
  , StoredEnvelope (..)
  )
import Archive.GitSnapshot (GitSnapshotError)
import Archive.GitSnapshot (gitSnapshotErrorMessage)
import qualified Archive.GitSnapshot as Git
import Control.Monad (foldM)
import Data.Aeson (Value (..))
import qualified Data.ByteString.Lazy as LazyByteString
import qualified Data.Aeson.Key as Key
import qualified Data.Aeson.KeyMap as KeyMap
import Data.Map.Strict (Map)
import qualified Data.Map.Strict as Map
import Data.Scientific (floatingOrInteger)
import Data.Set (Set)
import qualified Data.Set as Set
import Data.Text (Text)
import qualified Data.Text as Text

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

data Prepared = Prepared
  { preparedRevision :: Text
  , preparedValue :: Value
  , preparedDraft :: DraftEnvelope
  }

data TransitionKind
  = OperatorInitialization
  | Retire
  | Destroy
  | PublicationApproval
  | PublicationWithdrawal
  | SpecificationUpgrade
  deriving (Eq, Show)

-- | The only transition contracts Door can commit, by exact identifier and
-- version (ADRs 0028, 0051, 0068).  Dispatch additionally requires the citation's
-- digest to be installed.  Tests enumerate this table and
-- 'transitionContractsWithoutHandler' against the installed catalog, so a
-- transition contract cannot ship without a handler or a recorded refusal.
transitionDispatchTable :: [((Text, Text), TransitionKind)]
transitionDispatchTable =
  [ (("archive.transition/operator_initialization", "1"), OperatorInitialization)
  , (("archive.transition/retire", "1"), Retire)
  , (("archive.transition/retire", "2"), Retire)
  , (("archive.transition/destroy", "1"), Destroy)
  , (("archive.transition/destroy", "2"), Destroy)
  , (("archive.transition/publication_approval", "1"), PublicationApproval)
  , (("archive.transition/publication_withdrawal", "1"), PublicationWithdrawal)
  , ((upgradeContractId, upgradeContractVersion), SpecificationUpgrade)
  ]

-- | Installed transition contracts this Door refuses as unsupported. None
-- today: the specification upgrade, the last one 0.0.7 installed without a
-- handler, is dispatched (ADR 0068). A later release that installs a
-- transition before its handler exists lists it here, so it fails loudly.
transitionContractsWithoutHandler :: [(Text, Text)]
transitionContractsWithoutHandler = []

-- | The table entry for a draft's single installed transition citation.
transitionCitationKind :: Set (Text, Text, Text) -> DraftEnvelope -> Maybe (TransitionKind, Text)
transitionCitationKind installed draft = case transitionCitations of
  [key@(identifier, version, _digest)]
    | Set.member key installed ->
        fmap (\kind -> (kind, version)) (lookup (identifier, version) transitionDispatchTable)
  _ -> Nothing
  where
    transitionCitations =
      [ (contractId citation, contractVersion citation, contractDigest citation)
      | citation <- envelopeContracts draft
      , Text.isPrefixOf "archive.transition/" (contractId citation)
      ]

data TransitionKey = TransitionKey
  { transitionKind :: TransitionKind
  , transitionVersion :: Text
  }
  deriving (Eq, Show)

data TransitionState = TransitionState
  { retiredObjects :: Set Text
  , destroyedDigests :: Set Text
  }

data SnapshotFacts = SnapshotFacts
  { factByRevision :: Map Text RecordEntry
  , factHeads :: Map Text Text
  , factActiveOperator :: Text
  , factRetired :: Set Text
  , factDestroyed :: Set Text
  , factStore :: StoreIndex
  }

data ReplayState = ReplayState
  { replayHeads :: Map Text Text
  , replaySeen :: Map Text RecordEntry
  , replayActiveOperator :: Maybe Text
  , replayRetired :: Set Text
  , replayDestroyed :: Set Text
  , replayStore :: StoreIndex
  }

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
dryRunTransitionVerified root proposal = do
  snapshotResult <- Git.resolveSnapshot root
  case snapshotResult of
    Left problem -> pure (Left (TransitionSnapshotFailure problem))
    Right gitSnapshot -> do
      snapshotResult' <- loadSnapshot gitSnapshot
      -- Transition planning reads every committed record; read their bytes
      -- in bulk rather than one at a time as each is first used. A failure
      -- here is reported by the read that needs the bytes.
      _ <- Git.prefetchPaths gitSnapshot ["records"]
      case snapshotResult' of
        Left problem -> pure (Left (transitionSnapshotError problem))
        Right snapshot -> do
          upgrade <- prepareUpgrade gitSnapshot snapshot proposal
          case upgrade of
            Left problem -> pure (Left problem)
            Right (planning, files) -> do
              contracts <- validateProposalContracts planning proposal
              case contracts of
                Left problem -> pure (Left (TransitionProposalRejected problem))
                Right () -> do
                  let result = planTransition planning proposal
                  case result of
                    Left (PlanRefused problem) -> pure (Left (TransitionProposalRejected problem))
                    Left (PlanStale problem) -> pure (Left (TransitionProposalStale problem))
                    Right plan -> do
                      rechecked <- recheckProposal proposal
                      pure $ case rechecked of
                        Left problem -> Left (TransitionProposalRejected problem)
                        Right () -> Right
                          ( plan
                              { plannedTransitionSnapshot = Git.snapshotCommit gitSnapshot
                              , plannedGitExecutableIdentity = Git.snapshotExecutableIdentity gitSnapshot
                              , plannedSpecificationFiles = if transitionRetry plan then [] else files
                              }
                          , snapshot
                          )

-- | A specification upgrade (ADR 0068) is planned against the archive as it
-- will be once the target release is installed: its added bundles are
-- dispatchable and its core context is held. The archive's own snapshot is
-- returned unchanged to the caller for the commit's verification; only
-- planning sees the prospective one. Any other proposal, and a complete
-- retry of a committed upgrade, is planned against the snapshot as it is.
prepareUpgrade
  :: Git.GitSnapshot
  -> SnapshotData
  -> Proposal
  -> IO (Either TransitionPlanningError (SnapshotData, [(FilePath, LazyByteString.ByteString)]))
prepareUpgrade gitSnapshot snapshot proposal =
  case filter citesUpgrade (proposalRecords proposal) of
    [] -> pure (Right (snapshot, []))
    upgrades
      | length upgrades /= 1 || length (proposalRecords proposal) /= 1 ->
          pure (rejected "a specification upgrade proposal must contain exactly one record")
    record : _ -> case canonicalizeDraftStrict (proposalRecordValue record) of
      Left problem -> pure (rejected problem)
      Right (revision, _)
        | Map.member revision (snapshotRevisionIntroducers snapshot) -> pure (Right (snapshot, []))
        | otherwise -> prepareNew record
  where
    rejected = Left . TransitionProposalRejected
    citesUpgrade record = any ((== upgradeContractId) . contractId)
      (envelopeContracts (proposalRecordDraft record))

    prepareNew record = do
      manifestResult <- Git.readPath gitSnapshot "archive.json"
      case manifestResult of
        Left problem -> pure (Left (TransitionSnapshotFailure problem))
        Right manifestBytes -> case decodeUnique manifestBytes of
          Left problem -> pure (Left (TransitionSnapshotIntegrityFailure ("archive.json: " <> Text.pack problem)))
          Right manifestValue -> case parseArchiveManifest manifestValue of
            Left problem -> pure (Left (TransitionSnapshotIntegrityFailure problem))
            Right manifest -> do
              let current = manifestSpec manifest
                  draft = proposalRecordDraft record
              case upgradeRequest current draft of
                Left problem -> pure (rejected problem)
                Right to -> do
                  contextResult <- currentContext current
                  case contextResult of
                    Left problem -> pure (Left problem)
                    Right context -> do
                      let release = SpecificationRelease
                            { releaseSpecification = current
                            , releaseCatalog = Map.keysSet (snapshotInstalled snapshot)
                            , releaseLayout = manifestLayout manifest
                            , releaseContext = context
                            }
                      targetResult <- loadUpgradeTarget release to
                      pure $ do
                        target <- either rejected Right targetResult
                        requireTargetSpec to draft
                        upgraded <- either rejected Right
                          (makeArchiveManifest (manifestArchiveId manifest) to)
                        let added = targetAddedBundles target
                            installed = Map.union (snapshotInstalled snapshot)
                              (Map.fromList [(key, bundle) | (key, bundle) <- added])
                        mapM_ (requireCitable to installed) (envelopeContracts draft)
                        pure
                          ( prospective added target installed
                          , ("archive.json", archiveManifestBytes upgraded) : targetFiles target
                          )

    -- The payload's `from` must be the archive's specification and `to` a
    -- later version; once the target is known to this Door, the record must
    -- belong to the release it adopts.
    upgradeRequest current draft = do
      fields <- case envelopeBody draft of
        ClaimRecord (Object payload) -> Right payload
        _ -> Left "a specification upgrade must be a claim with an object payload"
      from <- requiredText "from" fields
      to <- requiredText "to" fields
      _ <- either (Left . ("specification upgrade from: " <>)) Right (specVersion from)
      _ <- either (Left . ("specification upgrade to: " <>)) Right (specVersion to)
      order <- compareSpecifications to from
      case order of
        LT -> Left ("unsupported: no downgrade exists: " <> to <> " is earlier than " <> from)
        EQ -> Left ("unsupported: a specification upgrade must name a version later than " <> from)
        GT -> Right ()
      if from /= current
        then Left ("specification upgrade from " <> from
          <> " does not start at the archive's specification " <> current)
        else Right ()
      pure to

    requireTargetSpec to draft =
      if envelopeSpec draft /= to
        then rejected ("a specification upgrade record must be written under its target specification "
          <> to <> ", not " <> envelopeSpec draft)
        else Right ()

    currentContext current = case specVersion current of
      Left problem -> pure (Left (TransitionSnapshotIntegrityFailure problem))
      Right version -> do
        let path = "spec/" <> Text.unpack version <> "/context.jsonld"
        bytes <- Git.readPath gitSnapshot path
        pure $ case bytes of
          Left problem -> Left (TransitionSnapshotFailure problem)
          Right content -> case decodeUnique content of
            Right (Object outer) | Just (Object inner) <- KeyMap.lookup "@context" outer -> Right inner
            _ -> Left (TransitionSnapshotIntegrityFailure (Text.pack path <> " has no @context object"))

    requireCitable to installed citation =
      if Map.member (contractId citation, contractVersion citation, contractDigest citation) installed
        then Right ()
        else rejected ("proposed contract is not installed at this snapshot or in the catalog of "
          <> to <> ": " <> contractId citation)

    prospective :: [((Text, Text, Text), ContractBundle)] -> UpgradeTarget
                -> Map (Text, Text, Text) ContractBundle -> SnapshotData
    prospective added target installed = snapshot
      { snapshotInstalled = installed
      , snapshotTransitionKeys = snapshotTransitionKeys snapshot `Set.union` Set.fromList
          [ key | (key@(identifier, _, _), _) <- added, Text.isPrefixOf "archive.transition/" identifier ]
      , snapshotImmutableAdditions = Map.union (snapshotImmutableAdditions snapshot)
          (Map.fromList [(path, "") | (path, _) <- targetFiles target])
      }

transitionSnapshotError :: AdmissionSnapshotLoadError -> TransitionPlanningError
transitionSnapshotError problem = case problem of
  AdmissionSnapshotGitError issue -> TransitionSnapshotFailure issue
  AdmissionSnapshotIntegrityError message -> TransitionSnapshotIntegrityFailure message

planTransition :: SnapshotData -> Proposal -> Either PlanRefusal TransitionPlan
planTransition snapshot proposal = do
  refuse $ if proposalKind proposal /= TransitionProposal
    then Left "transition planning requires a transition proposal"
    else pure ()
  refuse (validateProposal proposal)
  prepared <- refuse (mapM prepare (proposalRecords proposal))
  refuse (requireDistinct "computed transition revision" (map preparedRevision prepared))
  keys <- refuse (mapM (dispatch (snapshotTransitionKeys snapshot) . preparedDraft) prepared)
  refuse (validateBatchContracts keys prepared)
  facts <- refuse (snapshotFacts snapshot)
  let revisions = map preparedRevision prepared
      present = map (`Map.member` snapshotRevisionIntroducers snapshot) revisions
      retry = completeRetry snapshot revisions
  refuse $ if any id present && not retry
    then Left "existing transition revisions are not one committed batch"
    else pure ()
  refuse (validateAllocations snapshot proposal prepared retry)
  validateExpectedHeads facts proposal prepared retry
  refuse (if retry then pure () else validateLineage (factHeads facts) prepared)
  refuse (validateReferencesInOrder (factStore facts) prepared)
  finalState <- refuse $ if retry
    then pure (TransitionState (factRetired facts) (factDestroyed facts))
    else validateTransitions facts keys prepared
  pure TransitionPlan
    { plannedTransitionSnapshot = ""
    , plannedGitExecutableIdentity = ""
    , plannedTransitionRevisions = revisions
    , plannedTransitionRecords =
        [(preparedRevision item, preparedValue item) | item <- prepared]
    , transitionRetry = retry
    , resultingRetiredObjects = retiredObjects finalState
    , resultingDestroyedDigests = destroyedDigests finalState
    , plannedSpecificationFiles = []
    }

prepare :: ProposalRecord -> Either Text Prepared
prepare record = do
  (revision, _) <- canonicalizeDraftStrict (proposalRecordValue record)
  pure Prepared
    { preparedRevision = revision
    , preparedValue = proposalRecordValue record
    , preparedDraft = proposalRecordDraft record
    }

dispatch :: Set (Text, Text, Text) -> DraftEnvelope -> Either Text TransitionKey
dispatch installed draft = case transitionCitationKind installed draft of
  Just (kind, version) -> Right (TransitionKey kind version)
  Nothing -> case [ (identifier, version)
                  | citation <- envelopeContracts draft
                  , let identifier = contractId citation
                        version = contractVersion citation
                  , (identifier, version) `elem` transitionContractsWithoutHandler ] of
    (identifier, version) : _ -> Left ("unsupported transition contract: " <> identifier
      <> " version " <> version <> " is installed but this Door has no handler for it")
    [] -> Left "unsupported transition contract"

validateBatchContracts :: [TransitionKey] -> [Prepared] -> Either Text ()
validateBatchContracts keys prepared = case prepared of
  [item] -> requireNoPredecessor item
  _
    | all ((== "2") . transitionVersion) keys -> validatePredecessorChain prepared
    | otherwise ->
        Left "multi-record transition batches require version 2 retire/destroy contracts"

validatePredecessorChain :: [Prepared] -> Either Text ()
validatePredecessorChain [] = Left "transition proposal must contain at least one record"
validatePredecessorChain (first : rest) = do
  requireNoPredecessor first
  go first rest
  where
    go _ [] = Right ()
    go previous (item : remaining) = do
      predecessor <- oneReference "transition_predecessor"
        (envelopeReferences (preparedDraft item))
      revision <- maybe
        (Left "transition_predecessor must pin an exact revision")
        (Right . revisionText)
        (referenceRevision predecessor)
      let expectedObject = objectText (envelopeObject (preparedDraft previous))
      if objectText (referenceObject predecessor) == expectedObject
          && revision == preparedRevision previous
        then go item remaining
        else Left "transition_predecessor must pin the immediately preceding draft"

requireNoPredecessor :: Prepared -> Either Text ()
requireNoPredecessor item = case
    filter ((== "transition_predecessor") . referenceRole)
      (envelopeReferences (preparedDraft item)) of
  [] -> Right ()
  _ -> Left "the first transition record must not have a transition_predecessor"

snapshotFacts :: SnapshotData -> Either Text SnapshotFacts
snapshotFacts snapshot = do
  initialCommit <- case snapshotCommitOrder snapshot of
    commit : _ -> Right commit
    [] -> Left "archive history is empty"
  let additions = snapshotRevisionIntroducers snapshot
      byCommit = Map.fromListWith (<>)
        [ (commit, [entry])
        | entry <- snapshotRecords snapshot
        , Just commit <- [Map.lookup (entryRevision entry) additions]
        ]
  if Map.size additions == length (snapshotRecords snapshot)
    then pure ()
    else Left "record history does not account for every committed record"
  replayed <- foldM (replayCommit snapshot initialCommit byCommit) emptyReplay
    (snapshotCommitOrder snapshot)
  active <- maybe
    (Left "no active operator is established by a transition claim")
    Right
    (replayActiveOperator replayed)
  if replayHeads replayed == snapshotHeadsByObject snapshot
    then pure ()
    else Left "replayed object heads disagree with the committed snapshot"
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
  -> Either Text ReplayState
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
        else Left "operator initialization must occur exactly once in the archive root commit"
      if length ordinary == 1
        then pure ()
        else Left "archive root commit must contain exactly the appointed person and initialization claim"
      state' <- applyUnorderedEntries state ordinary
      operator <- validateInitialization state' initialization
      state'' <- applyEntry state' initialization
      pure state'' { replayActiveOperator = Just operator }
    ([], _) -> do
      if null ordinary
        then replayTransitionBatch snapshot state transitions
        else Left "a transition commit must contain only one transition batch"
    _ -> Left "operator initialization cannot share a commit with another transition"

validateInitialization :: ReplayState -> RecordEntry -> Either Text Text
validateInitialization state entry = do
  if replayActiveOperator state == Nothing
    then pure ()
    else Left "active operator is not uniquely established"
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
    else Left "operator initialization agent must identify its appointed person"

requireSameReference :: Reference -> Reference -> Either Text ()
requireSameReference left right =
  if referenceObject left == referenceObject right
      && referenceRevision left == referenceRevision right
    then Right ()
    else Left "operator initialization references must pin the same person revision"

replayTransitionBatch :: SnapshotData -> ReplayState -> [RecordEntry]
                      -> Either Text ReplayState
replayTransitionBatch snapshot state entries = do
  active <- maybe
    (Left "transition history precedes operator initialization")
    Right
    (replayActiveOperator state)
  ordered <- orderHistoricalTransitions (snapshotTransitionKeys snapshot) entries
  foldM (replayOneTransition active) state ordered

replayOneTransition :: Text -> ReplayState -> (TransitionKey, Prepared, RecordEntry)
                    -> Either Text ReplayState
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
                           -> Either Text [(TransitionKey, Prepared, RecordEntry)]
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
                   -> Either Text [(TransitionKey, Prepared, RecordEntry)]
orderByPredecessor [] = Left "transition commit contains no transition records"
orderByPredecessor triples = do
  roots <- filterMNoPredecessor triples
  root <- case roots of
    [value] -> Right value
    _ -> Left "committed transition batch must have exactly one predecessor-chain root"
  walk [root] (removeTriple root triples)
  where
    walk ordered [] = Right ordered
    walk ordered remaining = do
      let (_, previous, _) = last ordered
          successors = filter (follows previous) remaining
      next <- case successors of
        [value] -> Right value
        [] -> Left "committed transition predecessor chain is missing or crosses a batch"
        _ -> Left "committed transition predecessor chain forks"
      walk (ordered <> [next]) (removeTriple next remaining)

    follows previous (_, item, _) = case predecessorReference item of
      Right (Just reference) ->
        objectText (referenceObject reference)
          == objectText (envelopeObject (preparedDraft previous))
        && fmap revisionText (referenceRevision reference)
          == Just (preparedRevision previous)
      _ -> False

filterMNoPredecessor :: [(TransitionKey, Prepared, RecordEntry)]
                     -> Either Text [(TransitionKey, Prepared, RecordEntry)]
filterMNoPredecessor = foldM step []
  where
    step roots triple@(_, item, _) = do
      predecessor <- predecessorReference item
      pure $ case predecessor of
        Nothing -> roots <> [triple]
        Just _ -> roots

predecessorReference :: Prepared -> Either Text (Maybe Reference)
predecessorReference item = case
    filter ((== "transition_predecessor") . referenceRole)
      (envelopeReferences (preparedDraft item)) of
  [] -> Right Nothing
  [reference] -> Right (Just reference)
  _ -> Left "transition record has multiple transition_predecessor references"

removeTriple :: (TransitionKey, Prepared, RecordEntry)
             -> [(TransitionKey, Prepared, RecordEntry)]
             -> [(TransitionKey, Prepared, RecordEntry)]
removeTriple (_, prepared, _) =
  filter ((/= preparedRevision prepared) . preparedRevision . middle)
  where
    middle (_, value, _) = value

applyUnorderedEntries :: ReplayState -> [RecordEntry] -> Either Text ReplayState
applyUnorderedEntries state [] = Right state
applyUnorderedEntries state pending = case firstApplicable state pending of
  Nothing -> Left "committed records cannot be replayed from their historical predecessors"
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

applyEntry :: ReplayState -> RecordEntry -> Either Text ReplayState
applyEntry state entry = do
  let draft = storedDraft (entryEnvelope entry)
      objectId = entryObject entry
      supersedes =
        [ revisionText revision
        | reference <- envelopeSupersedes draft
        , Just revision <- [referenceRevision reference]
        ]
  case envelopeBody draft of
    ArtifactRecord payload -> do
      (digest, _) <- artifactContent payload
      if Set.member digest (replayDestroyed state)
        then Left "destroyed content may not be admitted again"
        else Right ()
    _ -> Right ()
  case (Map.lookup objectId (replayHeads state), supersedes) of
    (Nothing, []) -> Right ()
    (Just current, [previous]) | current == previous -> Right ()
    _ -> Left "historical record does not extend the then-current object head"
  revision <- parseRevisionId (entryRevision entry)
  validateReferences (replayStore state) revision draft
  pure state
    { replayHeads = Map.insert objectId (entryRevision entry) (replayHeads state)
    , replaySeen = Map.insert (entryRevision entry) entry (replaySeen state)
    , replayStore = insertRecord revision draft (replayStore state)
    }

resolveReplayReference :: ReplayState -> Reference -> Either Text RecordEntry
resolveReplayReference state reference = do
  revision <- maybe
    (Left "transition references must pin an exact revision")
    (Right . revisionText)
    (referenceRevision reference)
  entry <- maybe
    (Left "transition reference does not name an earlier committed revision")
    Right
    (Map.lookup revision (replaySeen state))
  if entryObject entry == objectText (referenceObject reference)
    then Right entry
    else Left "transition reference revision belongs to another object"

requireReplayCurrent :: ReplayState -> Reference -> Either Text ()
requireReplayCurrent state reference = do
  revision <- maybe
    (Left "transition references must pin an exact revision")
    (Right . revisionText)
    (referenceRevision reference)
  case Map.lookup (objectText (referenceObject reference)) (replayHeads state) of
    Just current | current == revision -> Right ()
    _ -> Left "transition reference must pin the then-current object head"

entryKind :: Set (Text, Text, Text) -> RecordEntry -> Maybe TransitionKind
entryKind installed entry =
  fst <$> transitionCitationKind installed (storedDraft (entryEnvelope entry))

completeRetry :: SnapshotData -> [Text] -> Bool
completeRetry snapshot revisions = case mapM lookupIntroducer revisions of
  Just (first : rest)
    | all (== first) rest ->
        let committed = Map.keysSet (Map.filter (== first) (snapshotRevisionIntroducers snapshot))
        in committed == Set.fromList revisions
  _ -> False
  where
    lookupIntroducer revision = Map.lookup revision (snapshotRevisionIntroducers snapshot)

validateAllocations :: SnapshotData -> Proposal -> [Prepared] -> Bool -> Either Text ()
validateAllocations snapshot proposal prepared retry = do
  let allocations = manifestIdentities (proposalManifest proposal)
      heads = snapshotHeadsByObject snapshot
      drafted = Set.fromList (map (objectText . envelopeObject . preparedDraft) prepared)
      newObjects = drafted `Set.difference` Map.keysSet heads
      allocated = Set.fromList (map identityObjectId allocations)
  mapM_ (validateAllocation (snapshotManifestId snapshot)) allocations
  if retry
    then if allocated `Set.isSubsetOf` drafted
      then Right ()
      else Left "identities must refer to drafted transition objects"
    else if allocated == newObjects
      then Right ()
      else Left "identities must contain exactly one allocation for each new transition object"

validateAllocation :: Text -> IdentityAllocation -> Either Text ()
validateAllocation archive allocation = do
  derived <- allocateObjectId archive (identityNamespace allocation) (identityName allocation)
  if derived == identityObjectId allocation
    then Right ()
    else Left "identity allocation object_id does not match UUIDv5 allocation"

validateExpectedHeads :: SnapshotFacts -> Proposal -> [Prepared] -> Bool -> Either PlanRefusal ()
validateExpectedHeads facts proposal prepared retry = do
  let drafts = Set.fromList
        [ objectText (envelopeObject (preparedDraft item)) | item <- prepared ]
      references = Set.fromList
        [ objectText (referenceObject reference)
        | item <- prepared
        , reference <- envelopeReferences (preparedDraft item)
          <> envelopeSupersedes (preparedDraft item)
        ]
      required = drafts `Set.union` references
      supplied = Map.fromList
        [ (expectedObjectId expected, expectedRevisionId expected)
        | expected <- manifestExpectedHeads (proposalManifest proposal)
        ]
  if Map.keysSet supplied /= required
    then Left (PlanRefused "expected_heads must cover exactly drafted and referenced objects")
    else if retry
      then Right ()
      else mapM_ (check drafts supplied) (Set.toList required)
  where
    check drafts supplied objectId = do
      expected <- case Map.lookup objectId supplied of
        Nothing -> Left (PlanRefused "expected_heads omitted a required object")
        Just value -> Right value
      actual <- case Map.lookup objectId (factHeads facts) of
        Just value -> Right (Just value)
        Nothing
          | Set.member objectId drafts -> Right Nothing
          | otherwise -> Left (PlanRefused "expected head names a missing object")
      if expected == actual
        then Right ()
        else Left (PlanStale "expected head is stale or has the wrong null expectation")

validateLineage :: Map Text Text -> [Prepared] -> Either Text ()
validateLineage initial = go initial
  where
    go _ [] = Right ()
    go heads (item : rest) = do
      let objectId = objectText (envelopeObject (preparedDraft item))
          supersedes =
            [ revisionText revision
            | reference <- envelopeSupersedes (preparedDraft item)
            , Just revision <- [referenceRevision reference]
            ]
      case (Map.lookup objectId heads, supersedes) of
        (Nothing, []) -> pure ()
        (Just current, [previous])
          | current == previous -> pure ()
        _ -> Left "proposed transitions must extend one linear supersession head"
      go (Map.insert objectId (preparedRevision item) heads) rest

validateReferencesInOrder :: StoreIndex -> [Prepared] -> Either Text ()
validateReferencesInOrder = go
  where
    go _ [] = Right ()
    go index (item : rest) = do
      revision <- parseRevisionId (preparedRevision item)
      validateReferences index revision (preparedDraft item)
      go (insertRecord revision (preparedDraft item) index) rest

validateTransitions :: SnapshotFacts -> [TransitionKey] -> [Prepared]
                    -> Either Text TransitionState
validateTransitions facts keys prepared = do
  let initial = TransitionState (factRetired facts) (factDestroyed facts)
  foldM (validateOne facts) initial
    (zip keys prepared)

validateOne :: SnapshotFacts -> TransitionState
            -> (TransitionKey, Prepared)
            -> Either Text TransitionState
validateOne facts state (key, item) = do
  case transitionKind key of
    PublicationApproval -> validateAuthorizer facts draft
    PublicationWithdrawal -> validateAuthorizer facts draft
    SpecificationUpgrade -> validateAuthorizer facts draft
    _ -> validateActors facts draft
  case transitionKind key of
    OperatorInitialization -> Left "unsupported: operator initialization is initialization-only"
    -- An approval, or a hold-back, covers only what the reviewer saw last:
    -- every covered object's current head (ADRs 0019, 0051).
    PublicationApproval -> do
      covers <- referencesWithRole "covers" (envelopeReferences draft)
      if null covers then Left "publication approval requires at least one covers reference" else pure ()
      mapM_ (\reference -> resolveReference facts reference >> requireCurrent facts reference) covers
      pure state
    -- Which versions an upgrade may name, and what it installs, depend on
    -- the archive's specification and Door's own release files; the caller
    -- checks those before planning, and the snapshot loader checks them for
    -- every committed upgrade. Here, as in replay, only its actors.
    SpecificationUpgrade -> do
      payload <- transitionPayload draft
      _ <- requiredText "from" payload
      _ <- requiredText "to" payload
      pure state
    PublicationWithdrawal -> do
      reference <- oneReference "withdraws" (envelopeReferences draft)
      approval <- resolveReference facts reference
      if citesContract "archive.transition/publication_approval" "1" (storedDraft (entryEnvelope approval))
        then pure ()
        else Left "withdraws reference must target a publication approval"
      revision <- maybe (Left "withdraws must pin an exact revision") (Right . revisionText)
        (referenceRevision reference)
      if revision `elem` withdrawnApprovals facts
        then Left "publication approval is already withdrawn"
        else pure state
    Retire -> do
      reference <- oneReference "retires" (envelopeReferences draft)
      _ <- resolveReference facts reference
      requireCurrent facts reference
      let objectId = objectText (referenceObject reference)
      if Set.member objectId (retiredObjects state)
        then Left "retire target is already retired"
        else pure state { retiredObjects = Set.insert objectId (retiredObjects state) }
    Destroy -> do
      payload <- transitionPayload draft
      content <- requiredObject "content" payload
      digest <- requiredText "digest" content
      size <- requiredSize content
      references <- referencesWithRole "destroys" (envelopeReferences draft)
      if null references then Left "destroy requires at least one destroys reference" else pure ()
      mapM_ (validateDestroyTarget facts (retiredObjects state) digest size) references
      if Set.member digest (destroyedDigests state)
        then Left "content has already been destroyed"
        else pure ()
      validateUnretiredReferences facts (retiredObjects state) digest
      validateDependents (envelopeReferences draft)
      validateExternalSurvivors (envelopeReferences draft)
      pure state { destroyedDigests = Set.insert digest (destroyedDigests state) }
  where
    draft = preparedDraft item

-- | Publication records name no author: the envelope agent may be whoever
-- drafted them, while the authorizer must be the active operator (ADR 0051).
validateAuthorizer :: SnapshotFacts -> DraftEnvelope -> Either Text ()
validateAuthorizer facts draft = do
  authorizer <- oneReference "authorizer" (envelopeReferences draft)
  authorizerEntry <- resolveReference facts authorizer
  _ <- requirePerson authorizerEntry
  if objectText (referenceObject authorizer) /= factActiveOperator facts
    then Left "transition authorizer is not the active operator"
    else pure ()
  requireCurrent facts authorizer

citesContract :: Text -> Text -> DraftEnvelope -> Bool
citesContract identifier version draft =
  any (\citation -> contractId citation == identifier && contractVersion citation == version)
    (envelopeContracts draft)

-- | Approval revisions named by a committed withdrawal.  During replay the
-- facts hold only earlier records, so this is the then-current set.
withdrawnApprovals :: SnapshotFacts -> [Text]
withdrawnApprovals facts =
  [ revisionText revision
  | entry <- Map.elems (factByRevision facts)
  , let draft = storedDraft (entryEnvelope entry)
  , citesContract "archive.transition/publication_withdrawal" "1" draft
  , reference <- envelopeReferences draft
  , referenceRole reference == "withdraws"
  , Just revision <- [referenceRevision reference]
  ]

validateActors :: SnapshotFacts -> DraftEnvelope -> Either Text ()
validateActors facts draft = do
  author <- oneReference "author" (envelopeReferences draft)
  authorizer <- oneReference "authorizer" (envelopeReferences draft)
  authorEntry <- resolveReference facts author
  _ <- requirePerson authorEntry
  if agentIdText (envelopeAgent draft) /= objectText (referenceObject author)
    then Left "transition agent must identify its author"
    else pure ()
  authorizerEntry <- resolveReference facts authorizer
  _ <- requirePerson authorizerEntry
  let authorizerObject = objectText (referenceObject authorizer)
  if authorizerObject /= factActiveOperator facts
    then Left "transition authorizer is not the active operator"
    else pure ()
  requireCurrent facts authorizer

resolveReference :: SnapshotFacts -> Reference -> Either Text RecordEntry
resolveReference facts reference = do
  revision <- maybe (Left "transition references must pin an exact revision") Right
    (referenceRevision reference)
  entry <- case Map.lookup (revisionText revision) (factByRevision facts) of
    Nothing -> Left "transition reference does not name a committed revision"
    Just value -> Right value
  if objectText (envelopeObject (storedDraft (entryEnvelope entry)))
      == objectText (referenceObject reference)
    then Right entry
    else Left "transition reference revision belongs to another object"

requireCurrent :: SnapshotFacts -> Reference -> Either Text ()
requireCurrent facts reference = do
  revision <- maybe (Left "transition references must pin an exact revision") Right
    (referenceRevision reference)
  case Map.lookup (objectText (referenceObject reference)) (factHeads facts) of
    Just current | current == revisionText revision -> Right ()
    _ -> Left "transition reference must pin the current object head"

requirePerson :: RecordEntry -> Either Text ()
requirePerson entry = case envelopeBody (storedDraft (entryEnvelope entry)) of
  EntityRecord (Object fields) -> case KeyMap.lookup "kind" fields of
    Just (String "person") -> Right ()
    _ -> Left "transition author and authorizer must be person entities"
  _ -> Left "transition author and authorizer must be person entities"

validateDestroyTarget :: SnapshotFacts -> Set Text -> Text -> Integer -> Reference -> Either Text ()
validateDestroyTarget facts legalRetired digest size reference = do
  entry <- resolveReference facts reference
  requireCurrent facts reference
  let objectId = objectText (referenceObject reference)
  if Set.member objectId legalRetired then pure () else Left "destroy target is not retired"
  case envelopeBody (storedDraft (entryEnvelope entry)) of
    ArtifactRecord payload -> do
      (actualDigest, actualSize) <- artifactContent payload
      if actualDigest == digest && actualSize == size
        then Right ()
        else Left "destroy content does not match every destroys reference"
    _ -> Left "destroy references must name artifact revisions"

validateUnretiredReferences :: SnapshotFacts -> Set Text -> Text -> Either Text ()
validateUnretiredReferences facts legalRetired digest =
  case [objectText (envelopeObject draft)
       | revision <- Map.elems (factHeads facts)
       , Just entry <- [Map.lookup revision (factByRevision facts)]
       , let draft = storedDraft (entryEnvelope entry)
       , ArtifactRecord payload <- [envelopeBody draft]
       , Right (actualDigest, _) <- [artifactContent payload]
       , actualDigest == digest
       , not (Set.member (objectText (envelopeObject draft)) legalRetired)] of
    [] -> Right ()
    objects -> Left ("destroy refused while an unretired object refers to content: "
      <> Text.intercalate "," objects)
validateDependents :: [Reference] -> Either Text ()
validateDependents references = do
  let dependents = filter ((`elem` ["dependent_destroy", "dependent_source_gone"])
                          . referenceRole) references
  if null dependents
    then Right ()
    else Left "unsupported: no installed artifact contract defines dependent reachability"

validateExternalSurvivors :: [Reference] -> Either Text ()
validateExternalSurvivors references =
  if any ((== "external_survivor") . referenceRole) references
    then Left "unsupported: external survivor evidence contracts are not installed"
    else Right ()

transitionPayload :: DraftEnvelope -> Either Text (KeyMap.KeyMap Value)
transitionPayload draft = case envelopeBody draft of
  ClaimRecord (Object fields) -> Right fields
  _ -> Left "transition record must be a claim"

artifactContent :: Value -> Either Text (Text, Integer)
artifactContent (Object fields) = do
  content <- requiredObject "content" fields
  digest <- requiredText "digest" content
  size <- requiredSize content
  pure (digest, size)
artifactContent _ = Left "artifact payload must be an object"

requiredObject :: Text -> KeyMap.KeyMap Value -> Either Text (KeyMap.KeyMap Value)
requiredObject name fields = case KeyMap.lookup (Key.fromText name) fields of
  Just (Object value) -> Right value
  Just _ -> Left (name <> " must be an object")
  Nothing -> Left (name <> " is required")

requiredText :: Text -> KeyMap.KeyMap Value -> Either Text Text
requiredText name fields = case KeyMap.lookup (Key.fromText name) fields of
  Just (String value) | not (Text.null value) -> Right value
  Just _ -> Left (name <> " must be a non-empty string")
  Nothing -> Left (name <> " is required")

requiredSize :: KeyMap.KeyMap Value -> Either Text Integer
requiredSize fields = case KeyMap.lookup "size" fields of
  Just (Number value) -> case (floatingOrInteger value :: Either Double Integer) of
    Right integer | integer >= 0 -> Right integer
    _ -> Left "content size must be a non-negative integer"
  Just _ -> Left "content size must be a non-negative integer"
  Nothing -> Left "size is required"

oneReference :: Text -> [Reference] -> Either Text Reference
oneReference role references = case filter ((== role) . referenceRole) references of
  [reference] -> Right reference
  _ -> Left (role <> " transition must contain exactly one reference")

referencesWithRole :: Text -> [Reference] -> Either Text [Reference]
referencesWithRole role = Right . filter ((== role) . referenceRole)

objectText :: ObjectId -> Text
objectText = objectIdText

revisionText :: RevisionId -> Text
revisionText = revisionIdText

requireDistinct :: Text -> [Text] -> Either Text ()
requireDistinct label values
  | Set.size (Set.fromList values) == length values = Right ()
  | otherwise = Left (label <> " must be unique")
