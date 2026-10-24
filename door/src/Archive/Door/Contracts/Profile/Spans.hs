{-# LANGUAGE OverloadedStrings #-}

-- | The sample span rules: @x-archive-ordered-sample-spans@,
-- @x-archive-track-sample-spans@, and @x-archive-distinct-track-content@,
-- which join a record's spans to the references that pin their audio.
module Archive.Door.Contracts.Profile.Spans
  ( OrderedSpans (..)
  , orderedSpansRule
  , checkOrderedSpans
  , TrackSpans (..)
  , trackSpansRule
  , checkTrackSpans
  , distinctTrackContentRule
  ) where

import Archive.Door.Contracts.Fields
  ( requireExactKeys
  , requiredInteger
  , requiredObject
  , requiredText
  )
import Archive.Door.Contracts.Profile.Error (RuleError (..), RuleViolation (..))
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

-- | @x-archive-ordered-sample-spans@: the payload array @payload@ holds one
-- span per reference, every reference in @role@.
data OrderedSpans = OrderedSpans
  { orderedSpansPayload :: Text
  , orderedSpansRole :: Text
  , orderedSpansStart :: Text
  , orderedSpansEnd :: Text
  , orderedSpansRate :: Text
  } deriving (Eq, Show)

orderedSpansRule :: Value -> Either RuleError OrderedSpans
orderedSpansRule (Object fields) = do
  requireExactKeys "ordered sample spans rule" ["payload", "role", "start", "end", "rate"] fields
  OrderedSpans <$> requiredText "payload" fields <*> requiredText "role" fields
    <*> requiredText "start" fields <*> requiredText "end" fields <*> requiredText "rate" fields
orderedSpansRule _ = Left (RuleNotObject "ordered sample spans rule")

-- | @x-archive-track-sample-spans@: the payload array @payload@ holds
-- tracks, each an array @pieces@ of spans, and the references in @role@
-- follow the spans in order.
data TrackSpans = TrackSpans
  { trackSpansPayload :: Text
  , trackSpansPieces :: Text
  , trackSpansRole :: Text
  , trackSpansStart :: Text
  , trackSpansEnd :: Text
  , trackSpansRate :: Text
  } deriving (Eq, Show)

trackSpansRule :: Value -> Either RuleError TrackSpans
trackSpansRule (Object fields) = do
  requireExactKeys "track sample spans rule" ["payload", "pieces", "role", "start", "end", "rate"] fields
  TrackSpans <$> requiredText "payload" fields <*> requiredText "pieces" fields
    <*> requiredText "role" fields <*> requiredText "start" fields <*> requiredText "end" fields
    <*> requiredText "rate" fields
trackSpansRule _ = Left (RuleNotObject "track sample spans rule")

-- | Spans are grouped by track, and references of the span role follow the
-- tracks in order.  Bounds are compared only between consecutive spans of one
-- track that pin the same Artifact object: different recordings have
-- different clocks, so their sample offsets are not comparable here.
checkTrackSpans :: TrackSpans -> KeyMap.KeyMap Value -> Either RuleViolation ()
checkTrackSpans rule instanceValue = do
  tracks <- trackPieces (trackSpansPayload rule) (trackSpansPieces rule) instanceValue
  refs <- instanceReferences instanceValue
  roleRefs <- referencesWithRole (trackSpansRole rule) refs
  let spanCount = sum (map length tracks)
  if null tracks || any null tracks || spanCount /= length roleRefs
    then Left TrackSpanCount
    else pure ()
  pieceObjects <- mapM refObject roleRefs
  let pinned = splitPlaces (map length tracks) pieceObjects
  mapM_ validateTrack (zip tracks pinned)
  where
    validateTrack (pieces, objects) = do
      bounds <- mapM spanBounds pieces
      let consecutive = zip (zip objects bounds) (drop 1 (zip objects bounds))
      if all (\((left, (_, leftEnd)), (right, (rightStart, _))) -> left /= right || rightStart >= leftEnd) consecutive
        then Right ()
        else Left TrackSpansOverlap
    spanBounds (Object piece) = do
      start <- requiredInteger (trackSpansStart rule) piece
      end <- requiredInteger (trackSpansEnd rule) piece
      rate <- requiredInteger (trackSpansRate rule) piece
      if start >= 0 && end > start && rate > 0
        then Right (start, end)
        else Left BadSampleSpan
    spanBounds _ = Left (NotObject "sample span")

-- | Each track's spans, from the payload array @payloadName@ of tracks that
-- each hold the array @piecesName@.
trackPieces :: Text -> Text -> KeyMap.KeyMap Value -> Either RuleViolation [[Value]]
trackPieces payloadName piecesName instanceValue = do
  payload <- requiredObject "payload" instanceValue
  tracks <- case KeyMap.lookup (Key.fromText payloadName) payload of
    Just (Array values) -> Right (toList values)
    _ -> Left (NotArray "track sample spans payload")
  mapM pieces tracks
  where
    pieces (Object track) = case KeyMap.lookup (Key.fromText piecesName) track of
      Just (Array values) -> Right (toList values)
      _ -> Left TrackWithoutSpans
    pieces _ = Left (NotObject "track")

-- | For a schema with a distinct-track-content rule, the span role and the
-- number of span references in each track, in reference order.  Door needs
-- committed Artifact revisions to compare digests, so the comparison itself
-- happens where the snapshot is available.
--
-- It reads the schema's rules as they are written rather than as a loaded
-- bundle holds them, because admission calls it with a schema in hand.
distinctTrackContentRule :: Value -> Value -> Either RuleViolation (Maybe (Text, [Int]))
distinctTrackContentRule (Object schema) (Object instanceValue) =
  case (KeyMap.lookup "x-archive-distinct-track-content" schema, KeyMap.lookup "x-archive-track-sample-spans" schema) of
    (Nothing, _) -> Right Nothing
    (Just (Object distinctRule), Just (Object spansRule)) -> do
      role <- requiredText "role" distinctRule
      payloadName <- requiredText "payload" spansRule
      piecesName <- requiredText "pieces" spansRule
      spanRole <- requiredText "role" spansRule
      tracks <- trackPieces payloadName piecesName instanceValue
      if role /= spanRole
        then Left (BrokenRule DistinctContentRole)
        else Right (Just (role, map length tracks))
    _ -> Left (BrokenRule DistinctContentWithoutSpans)
distinctTrackContentRule _ _ = Left InstanceNotObject

splitPlaces :: [Int] -> [a] -> [[a]]
splitPlaces [] _ = []
splitPlaces (count : counts) values = take count values : splitPlaces counts (drop count values)

checkOrderedSpans :: OrderedSpans -> KeyMap.KeyMap Value -> Either RuleViolation ()
checkOrderedSpans rule instanceValue = do
  payload <- requiredObject "payload" instanceValue
  pieces <- case KeyMap.lookup (Key.fromText (orderedSpansPayload rule)) payload of
    Just (Array values) -> Right (toList values)
    _ -> Left (NotArray "ordered sample spans payload")
  refs <- instanceReferences instanceValue
  if null pieces || length pieces /= length refs
    then Left OrderedSpanCount
    else pure ()
  mapM_ validatePiece pieces
  mapM_ (validateRefRole (orderedSpansRole rule)) refs
  where
    validatePiece (Object piece) = do
      start <- requiredInteger (orderedSpansStart rule) piece
      end <- requiredInteger (orderedSpansEnd rule) piece
      rate <- requiredInteger (orderedSpansRate rule) piece
      if start >= 0 && end > start && rate > 0
        then Right ()
        else Left BadSampleSpan
    validatePiece _ = Left (NotObject "sample span")
