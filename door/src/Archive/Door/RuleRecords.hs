{-# LANGUAGE OverloadedStrings #-}

-- | What the 0.0.10 profile rules may read beyond the record they check:
-- the exact revisions it pins, the head revision of an object, and the
-- active operator (D-18). Admission builds this from the committed records
-- and the earlier records of the proposed batch.
module Archive.Door.RuleRecords
  ( RuleRecords
  , ruleRecords
  , pinnedRecord
  , headRecord
  , activeOperator
  , draftPayload
  , draftRefs
  , refObject
  , refRevision
  , singleIn
  , citesContract
  ) where

import Archive.Door.Types
  ( ContractCitation (..)
  , DraftEnvelope (..)
  , RecordBody (..)
  , Reference (..)
  , objectIdText
  , revisionIdText
  )
import Data.Aeson (Value (..))
import Data.Aeson.KeyMap qualified as KeyMap
import Data.List (nub)
import Data.Map.Strict qualified as Map
import Data.Set qualified as Set
import Data.Text (Text)

data RuleRecords = RuleRecords
  { byRevision :: Map.Map (Text, Text) DraftEnvelope
  , heads :: Map.Map Text DraftEnvelope
  , operator :: Maybe Text
  }

-- | Records as (object, revision, draft). Everything is computed lazily, so
-- a record no rule reads beyond itself costs nothing more.
ruleRecords :: [(Text, Text, DraftEnvelope)] -> RuleRecords
ruleRecords records = RuleRecords
  { byRevision = Map.fromList [((object, revision), draft) | (object, revision, draft) <- records]
  , heads = Map.fromList [ (object, draft) | (object, revision, draft) <- records
                         , not (Set.member (object, revision) superseded) ]
  , operator = case nub [ objectIdText (referenceObject ref)
                    | (_, _, draft) <- records
                    , citesContract "archive.transition/operator_initialization" draft
                    , ref <- envelopeReferences draft, referenceRole ref == "appoints" ] of
      [appointed] -> Just appointed
      _ -> Nothing
  }
  where
    superseded = Set.fromList [ (objectIdText (referenceObject ref), maybe "" revisionIdText (referenceRevision ref))
                              | (_, _, draft) <- records, ref <- envelopeSupersedes draft ]

-- | The exact revision a reference pins.
pinnedRecord :: RuleRecords -> Reference -> Either Text DraftEnvelope
pinnedRecord records ref = case referenceRevision ref of
  Nothing -> Left (referenceRole ref <> " reference must pin an exact revision")
  Just revision -> maybe (Left (referenceRole ref <> " reference " <> revisionIdText revision <> " is not admitted"))
    Right (Map.lookup (objectIdText (referenceObject ref), revisionIdText revision) (byRevision records))

-- | An object's head revision: the one no other revision supersedes.
headRecord :: RuleRecords -> Text -> Maybe DraftEnvelope
headRecord records object = Map.lookup object (heads records)

-- | The person the operator initialization appoints; succession is
-- unsupported (D-10), so there is at most one.
activeOperator :: RuleRecords -> Either Text Text
activeOperator = maybe (Left "the archive's active operator cannot be determined") Right . operator

draftPayload :: DraftEnvelope -> KeyMap.KeyMap Value
draftPayload draft = case envelopeBody draft of
  ClaimRecord (Object payload) -> payload
  AssessmentRecord (Object payload) -> payload
  EntityRecord (Object payload) -> payload
  ArtifactRecord (Object payload) -> payload
  _ -> KeyMap.empty

draftRefs :: Text -> DraftEnvelope -> [Reference]
draftRefs role = filter ((== role) . referenceRole) . envelopeReferences

refObject :: Reference -> Text
refObject = objectIdText . referenceObject

refRevision :: Reference -> Maybe Text
refRevision = fmap revisionIdText . referenceRevision

-- | The one reference a rule reads in a role.
singleIn :: Text -> DraftEnvelope -> Either Text Reference
singleIn role draft = case draftRefs role draft of
  [ref] -> Right ref
  _ -> Left ("the record must pin exactly one " <> role <> " reference")

citesContract :: Text -> DraftEnvelope -> Bool
citesContract identifier = any ((== identifier) . contractId) . envelopeContracts
