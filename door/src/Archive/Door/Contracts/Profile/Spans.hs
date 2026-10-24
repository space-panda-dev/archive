{-# LANGUAGE OverloadedStrings #-}

-- | The sample span rules: @x-archive-ordered-sample-spans@,
-- @x-archive-track-sample-spans@, and @x-archive-distinct-track-content@,
-- which join a record's spans to the references that pin their audio.
module Archive.Door.Contracts.Profile.Spans
  ( validateOrderedSpansRule
  , validateTrackSpansRule
  , validateOrderedSpansInstance
  , validateTrackSpansInstance
  , distinctTrackContentRule
  ) where

import Archive.Door.Contracts.Fields
  ( requireExactKeys
  , requiredInteger
  , requiredObject
  , requiredText
  )
import Archive.Door.Contracts.Profile.Refs
  ( instanceReferences
  , refObject
  , referencesWithRole
  , validateRefRole
  )
import Data.Aeson (Value (..))
import Data.Aeson.Key qualified as Key
import Data.Aeson.KeyMap qualified as KeyMap
import Data.Foldable (toList)
import Data.Text (Text)

validateOrderedSpansRule :: Value -> Either Text ()
validateOrderedSpansRule (Object fields) = do
  requireExactKeys "ordered sample spans rule" ["payload", "role", "start", "end", "rate"] fields
  mapM_ (`requiredText` fields) ["payload", "role", "start", "end", "rate"]
  pure ()
validateOrderedSpansRule _ = Left "ordered sample spans rule must be an object"

validateTrackSpansRule :: Value -> Either Text ()
validateTrackSpansRule (Object fields) = do
  let names = ["payload", "pieces", "role", "start", "end", "rate"]
  requireExactKeys "track sample spans rule" names fields
  mapM_ (`requiredText` fields) names
validateTrackSpansRule _ = Left "track sample spans rule must be an object"

-- | Spans are grouped by track, and references of the span role follow the
-- tracks in order.  Bounds are compared only between consecutive spans of one
-- track that pin the same Artifact object: different recordings have
-- different clocks, so their sample offsets are not comparable here.
validateTrackSpansInstance :: KeyMap.KeyMap Value -> KeyMap.KeyMap Value -> Either Text ()
validateTrackSpansInstance rule instanceValue = do
  (role, tracks) <- trackSpans rule instanceValue
  startName <- requiredText "start" rule
  endName <- requiredText "end" rule
  rateName <- requiredText "rate" rule
  refs <- instanceReferences instanceValue
  roleRefs <- referencesWithRole role refs
  let spanCount = sum (map length tracks)
  if null tracks || any null tracks || spanCount /= length roleRefs
    then Left "track spans and span references must be nonempty and equal in number"
    else pure ()
  pieceObjects <- mapM refObject roleRefs
  let pinned = splitPlaces (map length tracks) pieceObjects
  mapM_ (validateTrack startName endName rateName) (zip tracks pinned)
  where
    validateTrack startName endName rateName (pieces, objects) = do
      bounds <- mapM (spanBounds startName endName rateName) pieces
      let consecutive = zip (zip objects bounds) (drop 1 (zip objects bounds))
      if all (\((left, (_, leftEnd)), (right, (rightStart, _))) -> left /= right || rightStart >= leftEnd) consecutive
        then Right ()
        else Left "spans of one track that pin the same Artifact must not overlap or run backwards"
    spanBounds startName endName rateName (Object piece) = do
      start <- requiredInteger startName piece
      end <- requiredInteger endName piece
      rate <- requiredInteger rateName piece
      if start >= 0 && end > start && rate > 0
        then Right (start, end)
        else Left "sample span requires 0 <= start < end and positive sample rate"
    spanBounds _ _ _ _ = Left "sample span must be an object"

trackSpans :: KeyMap.KeyMap Value -> KeyMap.KeyMap Value -> Either Text (Text, [[Value]])
trackSpans rule instanceValue = do
  payloadName <- requiredText "payload" rule
  piecesName <- requiredText "pieces" rule
  role <- requiredText "role" rule
  payload <- requiredObject "payload" instanceValue
  tracks <- case KeyMap.lookup (Key.fromText payloadName) payload of
    Just (Array values) -> Right (toList values)
    _ -> Left "track sample spans payload must be an array"
  pieces <- mapM (trackPieces piecesName) tracks
  pure (role, pieces)
  where
    trackPieces piecesName (Object track) = case KeyMap.lookup (Key.fromText piecesName) track of
      Just (Array values) -> Right (toList values)
      _ -> Left "track must contain a span array"
    trackPieces _ _ = Left "track must be an object"

-- | For a schema with a distinct-track-content rule, the span role and the
-- number of span references in each track, in reference order.  Door needs
-- committed Artifact revisions to compare digests, so the comparison itself
-- happens where the snapshot is available.
distinctTrackContentRule :: Value -> Value -> Either Text (Maybe (Text, [Int]))
distinctTrackContentRule (Object schema) (Object instanceValue) =
  case (KeyMap.lookup "x-archive-distinct-track-content" schema, KeyMap.lookup "x-archive-track-sample-spans" schema) of
    (Nothing, _) -> Right Nothing
    (Just (Object distinctRule), Just (Object spansRule)) -> do
      role <- requiredText "role" distinctRule
      (spanRole, tracks) <- trackSpans spansRule instanceValue
      if role /= spanRole
        then Left "distinct track content rule must name the span reference role"
        else Right (Just (role, map length tracks))
    _ -> Left "distinct track content rule requires a track sample spans rule"
distinctTrackContentRule _ _ = Left "contract profile requires an object instance"

splitPlaces :: [Int] -> [a] -> [[a]]
splitPlaces [] _ = []
splitPlaces (count : counts) values = take count values : splitPlaces counts (drop count values)

validateOrderedSpansInstance :: KeyMap.KeyMap Value -> KeyMap.KeyMap Value -> Either Text ()
validateOrderedSpansInstance rule instanceValue = do
  payloadName <- requiredText "payload" rule
  role <- requiredText "role" rule
  startName <- requiredText "start" rule
  endName <- requiredText "end" rule
  rateName <- requiredText "rate" rule
  payload <- requiredObject "payload" instanceValue
  pieces <- case KeyMap.lookup (Key.fromText payloadName) payload of
    Just (Array values) -> Right (toList values)
    _ -> Left "ordered sample spans payload must be an array"
  refs <- instanceReferences instanceValue
  if null pieces || length pieces /= length refs
    then Left "ordered sample spans and piece references must have equal nonzero length"
    else pure ()
  mapM_ (validatePiece startName endName rateName) pieces
  mapM_ (validateRefRole role) refs
  where
    validatePiece startName endName rateName (Object piece) = do
      start <- requiredInteger startName piece
      end <- requiredInteger endName piece
      rate <- requiredInteger rateName piece
      if start >= 0 && end > start && rate > 0
        then Right ()
        else Left "sample span requires 0 <= start < end and positive sample rate"
    validatePiece _ _ _ _ = Left "sample span must be an object"
