{-# LANGUAGE OverloadedStrings #-}

module Archive.Door.References
  ( StoreIndex (..)
  , Known (..)
  , emptyIndex
  , insertRecord
  , insertKnown
  , validateReferences
  ) where

import Archive.Door.Types
  ( DraftEnvelope (envelopeBody, envelopeObject, envelopeReferences, envelopeSupersedes)
  , ObjectId
  , objectIdText
  , RecordBody (ArtifactRecord, AssessmentRecord, ClaimRecord, EntityRecord)
  , Reference (referenceObject, referenceRevision, referenceRole)
  , RevisionId, revisionIdText
  , Shape (Artifact, Assessment, Claim, Entity)
  )
import qualified Data.Map.Strict as Map
import Data.Text (Text)

data StoreIndex = StoreIndex
  { revisionsByObject :: Map.Map ObjectId [Known]
  } deriving (Eq, Show)

data Known = Known
  { knownRevision :: RevisionId
  , knownShape :: Shape
  } deriving (Eq, Show)

emptyIndex :: StoreIndex
emptyIndex = StoreIndex Map.empty

validateReferences :: StoreIndex -> RevisionId -> DraftEnvelope -> Either Text ()
validateReferences index candidateRevision envelope = do
  mapM_ validate (envelopeReferences envelope)
  mapM_ validateSupersedes (envelopeSupersedes envelope)
  case (Map.lookup (envelopeObject envelope) (revisionsByObject index), envelopeSupersedes envelope) of
    (Nothing, _) -> Right ()
    (Just known, [])
      | any ((== candidateRevision) . knownRevision) known -> Right ()
      | otherwise -> Left "an existing object requires explicit supersession"
    (Just _, _) -> Right ()
  where
    validate reference = case Map.lookup (referenceObject reference) (revisionsByObject index) of
      Nothing -> Left ("reference " <> objectText (referenceObject reference) <> " does not name an admitted object")
      Just known -> case referenceRevision reference of
        Nothing -> validateTarget reference known
        Just revision
          | any ((== revision) . knownRevision) known ->
              validateTarget reference (filter ((== revision) . knownRevision) known)
          | otherwise -> Left ("reference revision " <> revisionText revision
              <> " is not a revision of object " <> objectText (referenceObject reference))
    validateTarget reference targets
      | isAssessment (envelopeBody envelope) && referenceRole reference == "assessment_of"
          && any ((/= Claim) . knownShape) targets =
          Left "assessment_of must pin a claim revision"
      | otherwise = Right ()
    validateSupersedes reference
      | referenceObject reference /= envelopeObject envelope =
          Left "a revision may supersede only the same object"
      | otherwise = case (Map.lookup (referenceObject reference) (revisionsByObject index),
                          referenceRevision reference) of
          (Just known, Just revision) -> case filter ((== revision) . knownRevision) known of
            [] -> Left ("superseded revision " <> revisionText revision
                    <> " is not a revision of object " <> objectText (referenceObject reference))
            targets
              | all ((== bodyShape (envelopeBody envelope)) . knownShape) targets -> Right ()
              | otherwise -> Left "a revision may supersede only the same shape"
          _ -> Left "supersedes reference does not resolve to an admitted revision"

insertRecord :: RevisionId -> DraftEnvelope -> StoreIndex -> StoreIndex
insertRecord revision envelope index = index
  { revisionsByObject = Map.insertWith (<>) (envelopeObject envelope)
      [Known revision (bodyShape (envelopeBody envelope))] (revisionsByObject index)
  }

-- | 'insertRecord' from an already-known object, revision, and shape.
insertKnown :: ObjectId -> RevisionId -> Shape -> StoreIndex -> StoreIndex
insertKnown object revision shape index = index
  { revisionsByObject = Map.insertWith (<>) object [Known revision shape] (revisionsByObject index)
  }

bodyShape :: RecordBody -> Shape
bodyShape (ArtifactRecord _) = Artifact
bodyShape (EntityRecord _) = Entity
bodyShape (ClaimRecord _) = Claim
bodyShape (AssessmentRecord _) = Assessment

isAssessment :: RecordBody -> Bool
isAssessment (AssessmentRecord _) = True
isAssessment _ = False

objectText :: ObjectId -> Text
objectText = objectIdText

revisionText :: RevisionId -> Text
revisionText = revisionIdText
