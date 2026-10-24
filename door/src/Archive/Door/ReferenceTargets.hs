{-# LANGUAGE OverloadedStrings #-}

-- | Resolving a contract reference to the exact revision it pins.
--
-- A contract may say what a reference role must target: a shape, an Entity
-- kind, or a Claim citing a contract. The target is a committed record or an
-- earlier record of the same proposed batch, never a proposer's description
-- of one.
module Archive.Door.ReferenceTargets
  ( Target (..)
  , entryTarget
  , validateReferenceTargetRules
  , singleReference
  , targetClaimPayload
  , referencedEntry
  ) where

import Archive.Door.Contracts
  ( ContractBundle (contractBundleSchema)
  , ReferenceTargetRule (..)
  , contractReferenceTargets
  )
import Archive.Door.ContractRuleProblem
  ( ContractRuleProblem (..)
  , outsideRule
  , shapeArticle
  )
import Archive.Door.Snapshot.Data (RecordEntry (..))
import Archive.Door.Types
  ( ContractCitation (..)
  , DraftEnvelope
  , RecordBody (..)
  , Reference (..)
  , StoredEnvelope (..)
  , envelopeBody
  , envelopeContracts
  , envelopeReferences
  , ObjectId
  , RevisionId
  , objectIdText
  , revisionIdText
  )
import Data.Aeson (Value (Object, String))
import qualified Data.Aeson.KeyMap as KeyMap
import Data.Either (isRight)
import Data.Text (Text)
import qualified Data.Text as Text

-- | A record a contract reference may name: committed in the snapshot, or an
-- earlier record of the proposed batch.
data Target = Target
  { targetObject :: ObjectId
  , targetRevision :: RevisionId
  , targetDraft :: DraftEnvelope
  }

entryTarget :: RecordEntry -> Target
entryTarget entry = Target (entryObject entry) (entryRevision entry)
  (storedDraft (entryEnvelope entry))

validateReferenceTargetRules :: [Target] -> DraftEnvelope -> [ContractBundle] -> Either ContractRuleProblem ()
validateReferenceTargetRules records draft bundles = do
  rules <- concat <$> mapM (outsideRule . contractReferenceTargets . contractBundleSchema) bundles
  mapM_ validate rules
  where
    -- A target rule constrains the references present.  How many a role
    -- needs is the schema's job (its contains bounds), which has already
    -- accepted the instance; this keeps optional roles possible.
    validate (role, rule) = do
      let references = filter ((== role) . referenceRole) (envelopeReferences draft)
      if not (referenceTargetMany rule) && length references > 1
        then Left (NotExactlyOneReference role)
        else mapM_ (validateOne role rule) references
    -- A rule naming one shape refuses any other; a rule naming several
    -- accepts a revision of any of them, then applies that shape's checks.
    validateOne role rule reference = do
      target <- referencedEntry records role reference
      case referenceTargetAny rule of
        [] -> validateAgainst role rule target
        alternatives
          | any (\alternative -> isRight (validateAgainst role alternative target)) alternatives -> Right ()
          | otherwise -> Left (TargetNoneOf role (map describe alternatives))
    describe alternative = case (referenceTargetShapes alternative, referenceTargetKind alternative,
                                 referenceTargetKinds alternative, referenceTargetContract alternative) of
      (_, Just kind, _, _) -> "an Entity revision of kind " <> kind
      (_, _, kinds@(_ : _), _) -> "an Entity revision of kind " <> Text.intercalate " or " kinds
      (_, _, _, Just (identifier, version)) -> "a Claim citing " <> identifier <> " version " <> version
      ([single], _, _, _) -> shapeArticle single
      (shapes, _, _, _) -> "a revision of shape " <> Text.intercalate " or " shapes
    validateAgainst role rule target = do
      let body = envelopeBody (targetDraft target)
          shape = bodyShape body
      case referenceTargetShapes rule of
        [single]
          | single /= shape -> Left (TargetWrongShape role single)
        shapes
          | shape `notElem` shapes -> Left (TargetNotOfShapes role shapes)
        _ -> validateShape role rule target body
    validateShape role rule target body = case bodyShape body of
        "claim" -> case body of
          ClaimRecord _ -> case referenceTargetContract rule of
            Nothing -> Right ()
            Just (identifier, version) ->
              if any (\citation -> contractId citation == identifier && contractVersion citation == version)
                  (envelopeContracts (targetDraft target))
                then Right ()
                else Left (TargetNotCiting role identifier version)
          _ -> Left (TargetWrongShape role "claim")
        "assessment" -> case body of
          AssessmentRecord _ -> Right ()
          _ -> Left (TargetWrongShape role "assessment")
        "artifact" -> case body of
          ArtifactRecord _ -> Right ()
          _ -> Left (TargetWrongShape role "artifact")
        "entity" -> case body of
          EntityRecord payload -> do
            kind <- case payload of
              Object fields -> case KeyMap.lookup "kind" fields of
                Just (String value) -> Right value
                _ -> Left (TargetEntityNoKind role)
              _ -> Left (TargetEntityNotObject role)
            case (referenceTargetKind rule, referenceTargetKinds rule) of
              (Just expected, _)
                | kind == expected -> Right ()
                | otherwise -> Left (TargetEntityKind role [expected])
              (Nothing, [])-> Right ()
              (Nothing, expected)
                | kind `elem` expected -> Right ()
                | otherwise -> Left (TargetEntityKind role expected)
          _ -> Left (TargetWrongShape role "entity")
        _ -> Left UnsupportedTargetShape

bodyShape :: RecordBody -> Text
bodyShape body = case body of
  ArtifactRecord _ -> "artifact"
  EntityRecord _ -> "entity"
  ClaimRecord _ -> "claim"
  AssessmentRecord _ -> "assessment"

-- | The one reference a rule reads in a role, or a refusal naming the role.
singleReference :: DraftEnvelope -> Text -> Either ContractRuleProblem Reference
singleReference draft role =
  case filter ((== role) . referenceRole) (envelopeReferences draft) of
    [reference] -> Right reference
    _ -> Left (NotExactlyOneReference role)

-- | A pinned Claim's payload as an object.
targetClaimPayload :: [Target] -> Text -> Reference -> Either ContractRuleProblem (KeyMap.KeyMap Value)
targetClaimPayload records role reference = do
  target <- referencedEntry records role reference
  case envelopeBody (targetDraft target) of
    ClaimRecord (Object payload) -> Right payload
    _ -> Left (TargetWrongShape role "claim")

referencedEntry :: [Target] -> Text -> Reference -> Either ContractRuleProblem Target
referencedEntry records role reference = do
  revision <- maybe (Left (ReferenceUnpinned role)) Right
    (referenceRevision reference)
  -- An exact retry finds its own records both committed and proposed; they
  -- are one revision, so the first match is the committed one.
  case filter
      (\target -> targetObject target == referenceObject reference
        && targetRevision target == revision) records of
    target : _ -> Right target
    [] -> Left (ReferenceNotAdmitted (revisionIdText revision) (objectIdText (referenceObject reference)))
