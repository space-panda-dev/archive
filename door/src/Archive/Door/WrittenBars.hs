{-# LANGUAGE OverloadedStrings #-}

-- | @x-archive-written-bars@ (D-18): the rules of an untimed meter layers
-- version 2 payload, a Work's or a document's written bars, that JSON Schema
-- cannot state.
--
-- Within a layer, a bar's grouping adds up to its numerator; sections,
-- chord sequence entries, and document bars never go back; and within a bar
-- the chords' written offsets strictly increase and fall before the bar's
-- end. A modulation joins a bar to the one before it in its layer (untimed
-- bars are adjacent by position); modulations are distinct and in order of
-- layer and bar, and a regrouping moves the pulse to a note value of another
-- length. Whether a section or entry exists in the pinned form or sequence
-- is World's check (D-18).
module Archive.Door.WrittenBars (validateWrittenBars) where

import Archive.Door.RuleJson
import Data.Aeson (Value (..))
import Data.Aeson.KeyMap qualified as KeyMap
import Data.List (nub, sort)
import Data.Ratio ((%))
import Data.Text (Text)

validateWrittenBars :: KeyMap.KeyMap Value -> NamedFields -> Either Text ()
validateWrittenBars payload named = do
  let layers = [fields | Object fields <- arrayAt (named "layers") payload]
      barsOf layer = [fields | Object fields <- arrayAt (named "bars") layer]
  mapM_ (writtenLayer . barsOf) layers
  places <- mapM (modulation (map barsOf layers)) [fields | Object fields <- arrayAt (named "modulations") payload]
  if places == sort places && length places == length (nub places) then Right ()
    else Left (named "modulations" <> " must be distinct and in order of layer and bar")
  where
    modulation layers link = do
      let layerAt = integerAt "layer" link
          barAt = integerAt "bar" link
      bars <- case layerAt of
        Just layer | layer >= 0, (chosen : _) <- drop (fromIntegral layer) layers, not (null chosen) -> Right chosen
        _ -> Left (named "modulations" <> " must join bars of a layer of the claim")
      bar <- case barAt of
        Just bar | bar >= 1 && bar < fromIntegral (length bars) -> Right bar
        _ -> Left (named "modulations" <> " must name a bar after the first of its layer")
      case (objectAt "pulse_before" link, objectAt "pulse_after" link) of
        (Just before, Just after) | noteValue before == noteValue after ->
          Left (named "modulations" <> " regrouping must move the pulse to a note value of another length")
        _ -> Right ()
      pure (maybe 0 id layerAt, bar)
    noteValue value = (integerAt "denominator" value, maybe 0 id (integerAt "dots" value))

-- | One layer's bars in written order.
writtenLayer :: [KeyMap.KeyMap Value] -> Either Text ()
writtenLayer bars = go (0, 0, 0) bars
  where
    go _ [] = Right ()
    go (section, entry, locator) (bar : rest) = do
      numerator <- maybe (Left "a written bar needs an integer numerator") Right (integerAt "numerator" bar)
      denominator <- maybe (Left "a written bar needs an integer denominator") Right (integerAt "denominator" bar)
      case KeyMap.lookup "grouping" bar of
        Just (Array groups) | sum (map (maybe 0 id . integerOf) (foldr (:) [] groups)) /= numerator ->
          Left "a written bar's grouping must add up to its numerator"
        _ -> Right ()
      locator' <- rising "document bar" locator (objectAt "locator" bar >>= integerAt "bar")
      section' <- rising "section" section (integerAt "section" bar)
      let chords = [fields | Object fields <- arrayAt "chords" bar]
      entry' <- foldl (\acc chord -> acc >>= \previous -> rising "sequence entry" previous (integerAt "entry" chord))
        (Right entry) chords
      offsets <- mapM offset chords
      let size = numerator % max 1 denominator
      if all (< size) offsets && and (zipWith (<) offsets (drop 1 offsets)) then Right ()
        else Left "a written bar's chords must fall at increasing offsets before the bar's end"
      go (section', entry', locator') rest
    rising _ previous Nothing = Right previous
    rising what previous (Just value)
      | value >= previous = Right value
      | otherwise = Left ("a written bar's " <> what <> " goes back: bars are in written order")
    -- A written length, count notes of value 1/unit; absent, the downbeat.
    offset chord = case objectAt "at" chord of
      Nothing -> Right 0
      Just at -> case (integerAt "count" at, integerAt "unit" at) of
        (Just count, Just unit) | unit > 0 -> Right (count % unit)
        _ -> Left "a chord's offset must be a written length"
