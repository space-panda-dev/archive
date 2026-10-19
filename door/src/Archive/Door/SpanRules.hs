{-# LANGUAGE LambdaCase #-}
{-# LANGUAGE OverloadedStrings #-}

-- | The span rules, @x-archive-sequential-spans@ (specification 0.0.8) and
-- @x-archive-contained-spans@ (0.0.10), as Door parses and checks them.
--
-- Specification 0.0.10 lets each be a nonempty list of rule objects, each
-- checked as if it stood alone, and lets a rule's @within@ or @outer@ be a
-- path: a list of payload field names, each reaching an array inside the
-- elements of the one before it. A timed lyric then orders the syllables of
-- every word of every line. A rule written as one object with a field name
-- means exactly what it meant before.
module Archive.Door.SpanRules
  ( SequentialSpans (..)
  , Within (..)
  , sequentialSpansRule
  , validateSequentialSpans
  , ContainedSpans (..)
  , containedSpansRule
  , validateContainedSpans
  , withinGroups
  ) where

import Archive.Door.RuleJson
import Data.Aeson (Value (..))
import Data.Aeson.Key qualified as Key
import Data.Aeson.KeyMap qualified as KeyMap
import Data.Foldable (toList)
import Data.List (nub)
import Data.Text (Text)

-- | Where a rule's array lives: the payload, the elements of one payload
-- array (0.0.9), or every element a path reaches (0.0.10).
data Within = AtRoot | WithinField Text | WithinPath [Text]
  deriving (Eq, Show)

data SequentialSpans = SequentialSpans
  { sequentialPayload :: Text
  , sequentialStart :: Text
  , sequentialEnd :: Text
  , sequentialWithin :: Within
  , sequentialOptional :: Bool
  } deriving (Eq, Show)

-- | One rule object, or a nonempty list of them.
oneOrMany :: Text -> (KeyMap.KeyMap Value -> Either Text a) -> Value -> Either Text [a]
oneOrMany label item value = case value of
  Object fields -> (: []) <$> item fields
  Array _ -> ruleList label item value
  _ -> Left (label <> " must be an object or a nonempty list of objects")

sequentialSpansRule :: Value -> Either Text [SequentialSpans]
sequentialSpansRule = oneOrMany label $ \fields -> do
  let allowed = ["payload", "start", "end"] <> ["within" | KeyMap.member "within" fields] <> ["optional" | KeyMap.member "optional" fields]
  exactKeys label allowed fields
  payload <- textField label "payload" fields
  start <- textField label "start" fields
  end <- textField label "end" fields
  within <- withinField label "within" fields
  optional <- case KeyMap.lookup "optional" fields of
    Nothing -> Right False
    Just (Bool True) -> Right True
    Just _ -> Left "a rule's optional must be true when present"
  pure (SequentialSpans payload start end within optional)
  where label = "sequential spans rule"

-- | A field name, or (0.0.10) a nonempty path of field names.
withinField :: Text -> Text -> KeyMap.KeyMap Value -> Either Text Within
withinField label name fields = case KeyMap.lookup (Key.fromText name) fields of
  Nothing -> Right AtRoot
  Just (String _) -> WithinField <$> textField label name fields
  Just (Array _) -> WithinPath <$> pathField label name fields
  Just _ -> Left (label <> " " <> name <> " must be a field name or a path")

-- | Every span array the rule reaches: @0 <= start < end@, and each span
-- starts at or after the previous one's end.
validateSequentialSpans :: [SequentialSpans] -> KeyMap.KeyMap Value -> Either Text ()
validateSequentialSpans rules payload = mapM_ one rules
  where
    one rule = do
      let reachesNothing = case sequentialWithin rule of
            AtRoot -> not (KeyMap.member (Key.fromText (sequentialPayload rule)) payload)
            WithinField name -> not (KeyMap.member (Key.fromText name) payload)
            WithinPath (first : _) -> not (KeyMap.member (Key.fromText first) payload)
            WithinPath [] -> True
      groups <- groupsOf (sequentialWithin rule)
      if sequentialOptional rule && reachesNothing
        then Right ()
        else mapM_ (spansIn rule) groups
    groupsOf AtRoot = Right [payload]
    groupsOf (WithinField name) = withinGroups (Just name) payload
    groupsOf (WithinPath path) = Right [group | Object group <- elements path payload]

spansIn :: SequentialSpans -> KeyMap.KeyMap Value -> Either Text ()
spansIn rule group = do
  spans <- case KeyMap.lookup (Key.fromText (sequentialPayload rule)) group of
    Just (Array values) | not (null values) -> Right (toList values)
    -- With within, an element without the array (a beat-only layer beside
    -- layers of bars, a word not split into syllables) has none to order.
    Nothing | sequentialWithin rule /= AtRoot -> Right []
    _ -> Left "sequential spans payload must be a nonempty array"
  bounds <- mapM spanBounds spans
  if all (\(start, end) -> start >= 0 && end > start) bounds
    then Right ()
    else Left "each span requires 0 <= start < end"
  if and (zipWith (\(_, previousEnd) (start, _) -> start >= previousEnd) bounds (drop 1 bounds))
    then Right ()
    else Left "spans must be in order and must not overlap"
  where
    spanBounds (Object piece) = (,) <$> integer (sequentialStart rule) piece <*> integer (sequentialEnd rule) piece
    spanBounds _ = Left "span must be an object"
    integer name piece = maybe (Left (name <> " must be an integer")) Right (integerAt name piece)

-- | The objects a rule's array lives in: the payload itself, or, with
-- @within@, each element of the payload array @within@ (one layer of bars,
-- say), so that order is checked inside each group and never across them.
withinGroups :: Maybe Text -> KeyMap.KeyMap Value -> Either Text [KeyMap.KeyMap Value]
withinGroups Nothing payload = Right [payload]
withinGroups (Just name) payload = case KeyMap.lookup (Key.fromText name) payload of
  Just (Array values) -> mapM asObject (toList values)
  _ -> Left (name <> " must be an array")
  where
    asObject (Object group) = Right group
    asObject _ = Left (name <> " must contain objects")

data ContainedSpans = ContainedSpans
  { containedOuter :: [Text]
  , containedInner :: Text
  , containedStart :: Text
  , containedEnd :: Text
  } deriving (Eq, Show)

containedSpansRule :: Value -> Either Text [ContainedSpans]
containedSpansRule = oneOrMany label $ \fields -> do
  exactKeys label ["outer", "inner", "start", "end"] fields
  outer <- withinField label "outer" fields >>= \case
    WithinField name -> Right [name]
    WithinPath path -> Right path
    AtRoot -> Left (label <> " outer must be a field name or a path")
  inner <- textField label "inner" fields
  start <- textField label "start" fields
  end <- textField label "end" fields
  let named = [inner, start, end] <> [name | [name] <- [outer]]
  if length named /= length (nub named)
    then Left (label <> " must name distinct fields")
    else Right (ContainedSpans outer inner start end)
  where label = "contained spans rule"

-- | Every inner span lies within the outer element that holds it, for every
-- outer element the path reaches.
validateContainedSpans :: [ContainedSpans] -> KeyMap.KeyMap Value -> Either Text ()
validateContainedSpans rules payload = mapM_ one rules
  where
    one rule = mapM_ (outer rule) [fields | Object fields <- elements (containedOuter rule) payload]
    outer rule fields = case bounds rule fields of
      Nothing -> Right ()
      Just (low, high) -> mapM_ (\inner -> case bounds rule inner of
        Just (start, end) | start < low || end > high ->
          Left (containedInner rule <> " must lie within their " <> pathLabel (containedOuter rule) <> " element")
        _ -> Right ()) [inner | Object inner <- arrayAt (containedInner rule) fields]
    bounds rule fields = (,) <$> integerAt (containedStart rule) fields <*> integerAt (containedEnd rule) fields
