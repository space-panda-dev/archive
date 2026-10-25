{-# LANGUAGE OverloadedStrings #-}

-- | The profile rules over musical structure: @x-archive-pitch-stacks@,
-- @x-archive-bar-beats@, and @x-archive-metric-modulations@.
module Archive.Door.Contracts.Profile.Music
  ( PitchStacks (..)
  , pitchStacksRule
  , checkPitchStacks
  , BarBeats (..)
  , barBeatsRule
  , checkBarBeats
  , MetricModulations (..)
  , metricModulationsRule
  , checkMetricModulations
  ) where

import Archive.Door.Contracts.Fields
  ( requireExactKeys
  , requiredInteger
  , requiredObject
  , requiredText
  , unique
  )
import Archive.Door.Contracts.Profile.Error (RuleError (..), RuleViolation (..))
import Archive.Door.Contracts.Profile.Paths (descend, pathHolders, requiredPath)
import Archive.Door.Contracts.Profile.Syntax (validateNamedFieldsRule)
import Archive.Door.SpanRules (withinGroups)
import Data.Aeson (Value (..))
import Data.Aeson.Key qualified as Key
import Data.Aeson.KeyMap qualified as KeyMap
import Data.Bifunctor (first)
import Data.Foldable (toList)
import Data.List (sort)
import Data.Scientific (floatingOrInteger)
import Data.Text (Text)
import qualified Data.Set as Set

-- | @x-archive-pitch-stacks@: every object @payload@ reaches that has a
-- @bass@ and @tones@ is a stack of pitch classes (0 to 11): @tones@ above the bass, and
-- optional @uncertain@ groups of @classes@ and a @voicing@ of MIDI notes.
-- No uncertain class is the bass, a tone, or in another group; the groups
-- are in ascending order of their classes; the voicing's lowest note is the
-- bass, and the classes of its other notes include every tone and nothing
-- outside the tones and uncertain classes.
data PitchStacks = PitchStacks
  { pitchStacksPath :: [Text]
  , pitchStacksBass :: Text
  , pitchStacksTones :: Text
  , pitchStacksUncertain :: Text
  , pitchStacksClasses :: Text
  , pitchStacksVoicing :: Text
  } deriving (Eq, Show)

pitchStacksRule :: Value -> Either RuleError PitchStacks
pitchStacksRule (Object fields) = do
  let names = ["bass", "tones", "uncertain", "classes", "voicing"]
  requireExactKeys "pitch stacks rule" ("payload" : names) fields
  path <- requiredPath "payload" fields
  named <- mapM (`requiredText` fields) names
  case named of
    [bass, tones, uncertain, classes, voicing] | length named == length (unique named) ->
      Right (PitchStacks path bass tones uncertain classes voicing)
    _ -> Left (FieldsNotDistinct "pitch stacks rule")
pitchStacksRule _ = Left (RuleNotObject "pitch stacks rule")

checkPitchStacks :: PitchStacks -> KeyMap.KeyMap Value -> Either RuleViolation ()
checkPitchStacks (PitchStacks path bassName tonesName uncertainName classesName voicingName) instanceValue = do
  payload <- requiredObject "payload" instanceValue
  holders <- pathHolders (path <> [bassName]) payload
  -- A stack has a bass and its tones; a bass-only reading (specification
  -- 0.0.10) has a bass and no tones, and nothing for this rule to check.
  let stacks = filter (\fields -> KeyMap.member (Key.fromText bassName) fields
        && KeyMap.member (Key.fromText tonesName) fields) holders
  mapM_ stack stacks
  where
    stack fields = do
      bass <- requiredInteger bassName fields
      tones <- integers tonesName fields
      groups <- case KeyMap.lookup (Key.fromText uncertainName) fields of
        Nothing -> Right []
        Just (Array values) -> mapM (\group -> case group of
          Object groupFields -> integers classesName groupFields
          _ -> Left (NotObjects uncertainName)) (toList values)
        Just _ -> Left (NotArray uncertainName)
      let certain = Set.fromList (bass : tones)
          unsure = concat groups
      if Set.null (Set.fromList unsure `Set.intersection` certain) && length unsure == length (unique unsure)
        then Right ()
        else Left (UncertainOverlaps uncertainName)
      if groups == sort groups
        then Right ()
        else Left (UncertainUnordered uncertainName classesName)
      case KeyMap.lookup (Key.fromText voicingName) fields of
        Nothing -> Right ()
        Just _ -> do
          notes <- integers voicingName fields
          case notes of
            lowest : upper
              | lowest `mod` 12 /= bass -> Left (VoicingBass voicingName)
              | otherwise -> do
                  let above = Set.fromList (map (`mod` 12) upper)
                  if Set.fromList tones `Set.isSubsetOf` above
                      && above `Set.isSubsetOf` Set.fromList (tones <> unsure)
                    then Right ()
                    else Left (VoicingTones voicingName)
            [] -> Left (NotEmpty voicingName)
    integers name fields = case KeyMap.lookup (Key.fromText name) fields of
      Just (Array values) -> mapM (\item -> case item of
        Number value | Right integer <- (floatingOrInteger value :: Either Double Integer) -> Right integer
        _ -> Left (NotIntegers name)) (toList values)
      _ -> Left (NotArray name)

-- | @x-archive-bar-beats@: every bar @payload@ reaches has an integer
-- @numerator@; its optional @grouping@ MUST sum to the numerator, and its
-- optional @beats@ MUST hold one sample per group (or per numerator unit
-- without a grouping), the first at the bar's @start@, each later than the
-- one before, and all before its @end@.
data BarBeats = BarBeats
  { barBeatsPath :: [Text]
  , barBeatsStart :: Text
  , barBeatsEnd :: Text
  , barBeatsNumerator :: Text
  , barBeatsGrouping :: Text
  , barBeatsBeats :: Text
  } deriving (Eq, Show)

barBeatsRule :: Value -> Either RuleError BarBeats
barBeatsRule (Object fields) = do
  let names = ["start", "end", "numerator", "grouping", "beats"]
  requireExactKeys "bar beats rule" ("payload" : names) fields
  path <- requiredPath "payload" fields
  named <- mapM (`requiredText` fields) names
  case named of
    [start, end, numerator, grouping, beats] | length named == length (unique named) ->
      Right (BarBeats path start end numerator grouping beats)
    _ -> Left (FieldsNotDistinct "bar beats rule")
barBeatsRule _ = Left (RuleNotObject "bar beats rule")

checkBarBeats :: BarBeats -> KeyMap.KeyMap Value -> Either RuleViolation ()
checkBarBeats (BarBeats path startName endName numeratorName groupingName beatsName) instanceValue = do
  payload <- requiredObject "payload" instanceValue
  let bars = concatMap (\value -> case value of
        Array items -> toList items
        _ -> []) (descend path [Object payload])
  mapM_ (\bar -> case bar of
    Object fields -> do
      start <- requiredInteger startName fields
      end <- requiredInteger endName fields
      numerator <- requiredInteger numeratorName fields
      count <- case KeyMap.lookup (Key.fromText groupingName) fields of
        Nothing -> Right numerator
        Just _ -> do
          groups <- integerArray groupingName fields
          if sum groups == numerator
            then Right (fromIntegral (length groups))
            else Left (GroupingSum groupingName numeratorName)
      case KeyMap.lookup (Key.fromText beatsName) fields of
        Nothing -> Right ()
        Just _ -> do
          beats <- integerArray beatsName fields
          if fromIntegral (length beats) /= count
            then Left (BeatCount beatsName)
            else if take 1 beats /= [start]
              || not (and (zipWith (<) beats (drop 1 beats)))
              || any (>= end) beats
              then Left (BeatsMisplaced beatsName)
              else Right ()
    _ -> Left (NotObject "bar")) bars
  where
    integerArray name fields = case KeyMap.lookup (Key.fromText name) fields of
      Just (Array values) -> mapM (\item -> case item of
        Number value | Right integer <- (floatingOrInteger value :: Either Double Integer) -> Right integer
        _ -> Left (NotIntegers name)) (toList values)
      _ -> Left (NotArray name)

-- | @x-archive-metric-modulations@: each element of the payload array
-- @payload@ joins bar @bar@ of layer @layer@ (positions in the payload
-- arrays @layers@ and, in that layer, @bars@; the layer MUST hold @bars@) to
-- the bar before it, which MUST end where it starts. Links are distinct and
-- in order of layer and bar. A link with both @pulse_before@ and
-- @pulse_after@ (a regrouping) MUST name note values (an integer
-- @denominator@ and optional @dots@) of different lengths: the pulse moves.
data MetricModulations = MetricModulations
  { modulationsPayload :: Text
  , modulationsLayers :: Text
  , modulationsBars :: Text
  , modulationsStart :: Text
  , modulationsEnd :: Text
  , modulationsLayer :: Text
  , modulationsBar :: Text
  , modulationsPulseBefore :: Text
  , modulationsPulseAfter :: Text
  , modulationsDenominator :: Text
  , modulationsDots :: Text
  } deriving (Eq, Show)

metricModulationsRule :: Value -> Either RuleError MetricModulations
metricModulationsRule value = do
  let names = [ "payload", "layers", "bars", "start", "end", "layer", "bar", "pulse_before", "pulse_after"
              , "denominator", "dots" ]
  validateNamedFieldsRule "metric modulations rule" names value
  case value of
    Object fields -> do
      named <- mapM (`requiredText` fields) names
      case named of
        [payload, layers, bars, start, end, layer, bar, pulseBefore, pulseAfter, denominator, dots]
          | length named == length (unique named) ->
              Right (MetricModulations payload layers bars start end layer bar pulseBefore pulseAfter denominator dots)
        _ -> Left (FieldsNotDistinct "metric modulations rule")
    _ -> Left (RuleNotObject "metric modulations rule")

checkMetricModulations :: MetricModulations -> KeyMap.KeyMap Value -> Either RuleViolation ()
checkMetricModulations
  (MetricModulations payloadName layersName barsName startName endName layerName barName
    pulseBeforeName pulseAfterName denominatorName dotsName)
  instanceValue = do
  payload <- requiredObject "payload" instanceValue
  links <- case KeyMap.lookup (Key.fromText payloadName) payload of
    Nothing -> Right []
    Just (Array values) -> Right (toList values)
    Just _ -> Left (NotArray payloadName)
  layers <- first ForeignViolation (withinGroups (Just layersName) payload)
  places <- mapM (\link -> case link of
    Object fields -> do
      layer <- requiredInteger layerName fields
      bar <- requiredInteger barName fields
      bars <- case drop (fromIntegral layer) layers of
        chosen : _ | layer >= 0 -> case KeyMap.lookup (Key.fromText barsName) chosen of
          Just (Array values) -> Right (toList values)
          _ -> Left (LayerWithoutBars payloadName barsName)
        _ -> Left (NoSuchLayer layerName)
      (previous, this) <- case drop (fromIntegral bar - 1) bars of
        Object earlier : Object later : _ | bar >= 1 -> Right (earlier, later)
        _ -> Left (NoEarlierBar barName)
      previousEnd <- requiredInteger endName previous
      thisStart <- requiredInteger startName this
      if previousEnd /= thisStart
        then Left (BarsNotAdjacent payloadName)
        else Right ()
      case (KeyMap.lookup (Key.fromText pulseBeforeName) fields, KeyMap.lookup (Key.fromText pulseAfterName) fields) of
        (Just (Object before), Just (Object after)) -> do
          (beforeTop, beforeBottom) <- noteLength before
          (afterTop, afterBottom) <- noteLength after
          if beforeTop * afterBottom == afterTop * beforeBottom
            then Left (PulseUnmoved payloadName)
            else Right ()
        _ -> Right ()
      pure (layer, bar)
    _ -> Left (NotObjects payloadName)) links
  if places == sort places && length places == length (unique places)
    then Right ()
    else Left (LinksOutOfOrder payloadName)
  where
    -- A note value's length in whole notes as (numerator, denominator):
    -- (2^(dots+1) - 1) / (denominator * 2^dots).
    noteLength fields = do
      denominator <- requiredInteger denominatorName fields
      dots <- case KeyMap.lookup (Key.fromText dotsName) fields of
        Nothing -> Right 0
        Just _ -> requiredInteger dotsName fields
      if denominator <= 0 || dots < 0
        then Left (NotNoteValue denominatorName dotsName)
        else Right (2 ^ (dots + 1) - 1, denominator * 2 ^ dots :: Integer)
