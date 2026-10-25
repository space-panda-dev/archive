{-# LANGUAGE OverloadedStrings #-}

-- | Object heads and supersession over a snapshot's committed records.
--
-- Each object has exactly one unsuperseded head and supersession never
-- forms a cycle; a snapshot that breaks either is refused as incomplete
-- evidence rather than read in some order.
module Archive.Door.Snapshot.Supersession
  ( indexRecords
  , deriveHeads
  , detectSupersessionCycles
  ) where

import Archive.Door.References
  ( StoreIndex
  , emptyIndex
  , insertRecord
  , validateReferences
  )
import Archive.Door.Snapshot.Data (RecordEntry (..))
import Archive.Door.Distinct (distinctOr)
import Archive.Door.Snapshot.Problem (SnapshotProblem (..), snapshotCheck)
import Archive.Door.Types
  ( ObjectId
  , Reference (..)
  , RevisionId
  , StoredEnvelope (..)
  , envelopeSupersedes
  , objectIdText
  )
import qualified Data.Map.Strict as Map
import qualified Data.Set as Set

indexRecords :: [RecordEntry] -> Either SnapshotProblem (StoreIndex, Map.Map ObjectId RevisionId)
indexRecords entries = do
  let revisions = map entryRevision entries
  distinctOr (NotUnique "record revision") revisions
  let store = foldl' add emptyIndex entries
      superseded =
        Set.fromList
          [ (entryObject entry, revision)
          | entry <- entries
          , reference <- envelopeSupersedes (storedDraft (entryEnvelope entry))
          , Just revision <- [referenceRevision reference]
          ]
      byObject = Map.fromListWith (<>)
        [(entryObject entry, [entry]) | entry <- entries]
  mapM_ (validateOne store) entries
  heads <- deriveHeads byObject superseded
  detectSupersessionCycles entries
  pure (store, heads)
  where
    add store entry = insertRecord (storedRevision (entryEnvelope entry))
      (storedDraft (entryEnvelope entry)) store
    validateOne store entry = snapshotCheck $
      validateReferences store (storedRevision (entryEnvelope entry))
        (storedDraft (entryEnvelope entry))

deriveHeads :: Map.Map ObjectId [RecordEntry] -> Set.Set (ObjectId, RevisionId)
            -> Either SnapshotProblem (Map.Map ObjectId RevisionId)
deriveHeads byObject superseded = Map.fromList <$> mapM one (Map.toList byObject)
  where
    one (objectId, entries) =
      case filter (not . isSuperseded objectId) entries of
        [entry] -> Right (objectId, entryRevision entry)
        [] -> Left (NoUnsupersededHead (objectIdText objectId))
        _ -> Left (ForkedHeads (objectIdText objectId))
    isSuperseded objectId entry =
      Set.member (objectId, entryRevision entry) superseded

detectSupersessionCycles :: [RecordEntry] -> Either SnapshotProblem ()
detectSupersessionCycles entries = go Set.empty Set.empty allRevisions
  where
    allRevisions = map entryRevision entries
    edges = Map.fromList
      [ (entryRevision entry
        , [revision
          | reference <- envelopeSupersedes (storedDraft (entryEnvelope entry))
          , Just revision <- [referenceRevision reference]])
      | entry <- entries
      ]
    go _ _ [] = Right ()
    go visiting visited (revision : rest)
      | Set.member revision visited = go visiting visited rest
      | otherwise = do
          (newVisited, newVisiting) <- visit visiting visited revision
          go newVisiting newVisited rest
    visit visiting visited revision
      | Set.member revision visiting = Left SupersessionCycle
      | Set.member revision visited = Right (visited, visiting)
      | otherwise = do
          let next = Map.findWithDefault [] revision edges
          (visited', visiting') <- foldl' (visitStep revision) (Right (visited, Set.insert revision visiting)) next
          pure (Set.insert revision visited', Set.delete revision visiting')
    visitStep _ (Left problem) _ = Left problem
    visitStep _ (Right (visited, visiting)) next = visit visiting visited next
