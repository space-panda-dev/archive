{-# LANGUAGE OverloadedStrings #-}

-- | The rules each transition kind must meet against the archive's facts.
--
-- Planning a new transition and replaying committed ones apply the same
-- rules, so a history Door would refuse today cannot be read back as valid.
module Archive.Door.Transition.Rules
  ( Prepared (..)
  , TransitionState (..)
  , SnapshotFacts (..)
  , validateBatchContracts
  , validateOne
  , requirePerson
  , artifactContent
  , oneReference
  , objectText
  , revisionText
  ) where

import Archive.Door.Json.Access (requiredNonEmptyText, requiredObject)
import Archive.Door.References (StoreIndex)
import Archive.Door.Snapshot.Data (RecordEntry (..))
import Archive.Door.Transition.Dispatch
  ( TransitionKey (..)
  , TransitionKind (..)
  )
import Archive.Door.Types
  ( ContractCitation (..)
  , DraftEnvelope (..)
  , ObjectId
  , RecordBody (..)
  , Reference (..)
  , RevisionId
  , StoredEnvelope (..)
  , agentIdText
  , objectIdText
  , revisionIdText
  )
import Data.Aeson (Value (..))
import qualified Data.Aeson.KeyMap as KeyMap
import Data.Map.Strict (Map)
import qualified Data.Map.Strict as Map
import Data.Scientific (floatingOrInteger)
import Data.Set (Set)
import qualified Data.Set as Set
import Data.Text (Text)
import qualified Data.Text as Text

data Prepared = Prepared
  { preparedRevision :: Text
  , preparedValue :: Value
  , preparedDraft :: DraftEnvelope
  }

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
      _ <- requiredNonEmptyText "from" payload
      _ <- requiredNonEmptyText "to" payload
      pure state
    PublicationWithdrawal -> do
      reference <- oneReference "withdraws" (envelopeReferences draft)
      approval <- resolveReference facts reference
      if any (\version -> citesContract "archive.transition/publication_approval" version (storedDraft (entryEnvelope approval))) ["1", "2"]
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
      digest <- requiredNonEmptyText "digest" content
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

-- | Approval revisions named by a committed withdrawal of either version,
-- so no approval is withdrawn twice across them.  During replay the facts
-- hold only earlier records, so this is the then-current set.
withdrawnApprovals :: SnapshotFacts -> [Text]
withdrawnApprovals facts =
  [ revisionText revision
  | entry <- Map.elems (factByRevision facts)
  , let draft = storedDraft (entryEnvelope entry)
  , any (\version -> citesContract "archive.transition/publication_withdrawal" version draft) ["1", "2"]
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
  digest <- requiredNonEmptyText "digest" content
  size <- requiredSize content
  pure (digest, size)
artifactContent _ = Left "artifact payload must be an object"

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
