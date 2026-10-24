{-# LANGUAGE OverloadedStrings #-}

-- | The @x-archive-*@ profile rules: what JSON Schema cannot say about a
-- record, such as ordered spans or the targets of its references.
--
-- A bundle's rules are parsed once, when it loads, into 'ProfileRule'
-- values; every instance is then checked against those values by one total
-- 'checkInstance', so a rule that loads is the rule that is enforced and no
-- evaluator re-reads the rule's JSON. Each rule's syntax and evaluator live
-- together in a module under "Archive.Door.Contracts.Profile".
module Archive.Door.Contracts.Profile
  ( profileRuleNames
  , ProfileRule (..)
  , parseProfileRules
  , ProfileRules (..)
  , rulesFor
  , checkProfile
  , checkInstance
  ) where

import Archive.Door.Contracts.Fields (requiredObject)
import Archive.Door.Contracts.Profile.Arrays
  ( DocumentOrder
  , NestedRanges
  , Subset
  , ascendingItemsRule
  , checkAscendingItems
  , checkDocumentOrder
  , checkNestedRanges
  , checkSubset
  , documentOrderRule
  , nestedRangesRule
  , subsetRule
  )
import Archive.Door.Contracts.Profile.Music
  ( BarBeats
  , MetricModulations
  , PitchStacks
  , barBeatsRule
  , checkBarBeats
  , checkMetricModulations
  , checkPitchStacks
  , metricModulationsRule
  , pitchStacksRule
  )
import Archive.Door.Contracts.Profile.Payload
  ( MonotonicAnchors
  , OrderedInstants
  , PartialDates
  , checkMonotonicAnchors
  , checkOrderedInstants
  , checkPartialDates
  , monotonicAnchorsRule
  , orderedInstantsRule
  , partialDatesRule
  )
import Archive.Door.Contracts.Profile.PinnedTargets
  ( SameSubjectRule
  , SpanWindowRule
  , TargetBoundRule
  , TargetIndexRule
  , TargetMeasureRule
  , TargetSamplesRule
  , sameSubjectRule
  , spanWindowRule
  , targetBoundRule
  , targetIndexRule
  , targetMeasureRule
  , targetSamplesRule
  )
import Archive.Door.Contracts.Profile.ReferenceTargets (ReferenceTargetRule, referenceTargetRules)
import Archive.Door.Contracts.Profile.Refs
  ( checkDistinctRefs
  , checkOrderedDistinctRefs
  , checkSortedRefs
  , distinctRefsRule
  , roleRule
  )
import Archive.Door.Contracts.Profile.Spans
  ( OrderedSpans
  , TrackSpans
  , checkOrderedSpans
  , checkTrackSpans
  , orderedSpansRule
  , trackSpansRule
  )
import Archive.Door.RuleJson (InstanceRef (..), instanceRefs)
import Archive.Door.RuleSyntax (release011RuleNames, releaseRuleNames)
import Archive.Door.SpanRules (SequentialSpans, sequentialSpansRule, validateSequentialSpans)
import Data.Aeson (Value (..))
import Data.Aeson.Key qualified as Key
import Data.Aeson.KeyMap qualified as KeyMap
import Data.Maybe (catMaybes)
import Data.Text (Text)

profileRuleNames :: [Text]
profileRuleNames =
  [ "x-archive-reference-targets", "x-archive-ordered-sample-spans", "x-archive-unique-sorted-refs"
  , "x-archive-track-sample-spans", "x-archive-distinct-track-content"
  , "x-archive-monotonic-anchors", "x-archive-distinct-refs", "x-archive-ordered-distinct-refs"
  , "x-archive-ordered-instants", "x-archive-sequential-spans", "x-archive-target-measure"
  , "x-archive-same-subject", "x-archive-span-window"
  , "x-archive-ascending-items", "x-archive-pitch-stacks", "x-archive-bar-beats"
  , "x-archive-metric-modulations", "x-archive-target-bound", "x-archive-partial-dates"
  , "x-archive-subset", "x-archive-document-order", "x-archive-nested-ranges"
  , "x-archive-target-samples", "x-archive-target-index"
  ] <> releaseRuleNames <> release011RuleNames

-- | One profile rule of a bundle's schema, parsed. The rules that compare a
-- record with what it pins ('RuleReferenceTargets', 'RuleDistinctTrackContent'
-- and the @RuleTarget*@, 'RuleSameSubject' and 'RuleSpanWindow' rules) are
-- checked during admission, where the pinned records are; on an instance
-- alone they hold.
data ProfileRule
  = RuleReferenceTargets [(Text, ReferenceTargetRule)]
  | RuleOrderedSampleSpans OrderedSpans
  | RuleUniqueSortedRefs Text
  | RuleTrackSampleSpans TrackSpans
  | RuleDistinctTrackContent Text
  | RuleMonotonicAnchors MonotonicAnchors
  | RuleDistinctRefs [Text]
  | RuleOrderedDistinctRefs Text
  | RuleOrderedInstants OrderedInstants
  | RuleSequentialSpans [SequentialSpans]
  | RuleTargetMeasure TargetMeasureRule
  | RuleSameSubject SameSubjectRule
  | RuleSpanWindow SpanWindowRule
  | RuleAscendingItems [[Text]]
  | RulePitchStacks PitchStacks
  | RuleBarBeats BarBeats
  | RuleMetricModulations MetricModulations
  | RuleTargetBound TargetBoundRule
  | RulePartialDates PartialDates
  | RuleSubset Subset
  | RuleDocumentOrder DocumentOrder
  | RuleNestedRanges NestedRanges
  | RuleTargetSamples TargetSamplesRule
  | RuleTargetIndex TargetIndexRule
  deriving (Eq, Show)

-- | Parse the profile rules at a schema's root, in a fixed order: the first
-- malformed rule in that order is the one refused, and the instance checks
-- run in the same order, so the first broken rule is the one reported.
parseProfileRules :: KeyMap.KeyMap Value -> Either Text [ProfileRule]
parseProfileRules fields = do
  referenceTargets <- rule "x-archive-reference-targets" (fmap RuleReferenceTargets . referenceTargetRules)
  orderedSpans <- rule "x-archive-ordered-sample-spans" (fmap RuleOrderedSampleSpans . orderedSpansRule)
  sortedRefs <- rule "x-archive-unique-sorted-refs"
    (fmap RuleUniqueSortedRefs . roleRule "unique sorted references rule")
  trackSpans <- rule "x-archive-track-sample-spans" (fmap RuleTrackSampleSpans . trackSpansRule)
  distinctContent <- rule "x-archive-distinct-track-content"
    (fmap RuleDistinctTrackContent . roleRule "distinct track content rule")
  anchors <- rule "x-archive-monotonic-anchors" (fmap RuleMonotonicAnchors . monotonicAnchorsRule)
  distinctRefs <- rule "x-archive-distinct-refs" (fmap RuleDistinctRefs . distinctRefsRule)
  orderedDistinct <- rule "x-archive-ordered-distinct-refs"
    (fmap RuleOrderedDistinctRefs . roleRule "ordered distinct references rule")
  case (KeyMap.member "x-archive-distinct-track-content" fields, KeyMap.member "x-archive-track-sample-spans" fields) of
    (True, False) -> Left "distinct track content rule requires a track sample spans rule"
    _ -> Right ()
  instants <- rule "x-archive-ordered-instants" (fmap RuleOrderedInstants . orderedInstantsRule)
  sequential <- rule "x-archive-sequential-spans" (fmap RuleSequentialSpans . sequentialSpansRule)
  measure <- fmap RuleTargetMeasure <$> targetMeasureRule schema
  subject <- fmap RuleSameSubject <$> sameSubjectRule schema
  window <- fmap RuleSpanWindow <$> spanWindowRule schema
  ascending <- rule "x-archive-ascending-items" (fmap RuleAscendingItems . ascendingItemsRule)
  stacks <- rule "x-archive-pitch-stacks" (fmap RulePitchStacks . pitchStacksRule)
  beats <- rule "x-archive-bar-beats" (fmap RuleBarBeats . barBeatsRule)
  modulations <- rule "x-archive-metric-modulations" (fmap RuleMetricModulations . metricModulationsRule)
  bound <- fmap RuleTargetBound <$> targetBoundRule schema
  dates <- rule "x-archive-partial-dates" (fmap RulePartialDates . partialDatesRule)
  subset <- rule "x-archive-subset" (fmap RuleSubset . subsetRule)
  documentOrder <- rule "x-archive-document-order" (fmap RuleDocumentOrder . documentOrderRule)
  ranges <- rule "x-archive-nested-ranges" (fmap RuleNestedRanges . nestedRangesRule)
  samples <- fmap RuleTargetSamples <$> targetSamplesRule schema
  index <- fmap RuleTargetIndex <$> targetIndexRule schema
  pure $ catMaybes
    [ referenceTargets, orderedSpans, sortedRefs, trackSpans, distinctContent, anchors, distinctRefs
    , orderedDistinct, instants, sequential, measure, subject, window, ascending, stacks, beats
    , modulations, bound, dates, subset, documentOrder, ranges, samples, index ]
  where
    schema = Object fields
    rule name parse = traverse parse (KeyMap.lookup (Key.fromText name) fields)

-- | A bundle's rules. A schema with @x-archive-when-pinned@ (D-18) has two
-- forms, chosen by whether the record pins a reference in the role; each
-- form holds every rule of the schema that form makes (see
-- "Archive.Door.WhenPinned").
data ProfileRules
  = ProfileRules [ProfileRule]
  -- | The role, the rules when it is pinned, and the rules otherwise.
  | PinnedForms Text [ProfileRule] [ProfileRule]
  deriving (Eq, Show)

-- | The rules an instance (@{payload, refs}@) is checked under, chosen as
-- 'Archive.Door.WhenPinned.effectiveSchema' chooses its schema.
rulesFor :: ProfileRules -> KeyMap.KeyMap Value -> [ProfileRule]
rulesFor (ProfileRules rules) _ = rules
rulesFor (PinnedForms role pinned unpinned) instanceValue
  | any ((== role) . instanceRefRole) (instanceRefs instanceValue) = pinned
  | otherwise = unpinned

-- | Check an instance against a bundle's rules, in their parse order.
checkProfile :: ProfileRules -> Value -> Either Text ()
checkProfile rules (Object instanceValue) = mapM_ (`checkInstance` instanceValue) (rulesFor rules instanceValue)
checkProfile _ _ = Left "contract profile requires an object instance"

-- | Check one rule against an instance (@{payload, refs}@).
checkInstance :: ProfileRule -> KeyMap.KeyMap Value -> Either Text ()
checkInstance profileRule instanceValue = case profileRule of
  RuleOrderedSampleSpans rule -> checkOrderedSpans rule instanceValue
  RuleUniqueSortedRefs role -> checkSortedRefs role instanceValue
  RuleTrackSampleSpans rule -> checkTrackSpans rule instanceValue
  RuleMonotonicAnchors rule -> checkMonotonicAnchors rule instanceValue
  RuleDistinctRefs roles -> checkDistinctRefs roles instanceValue
  RuleOrderedDistinctRefs role -> checkOrderedDistinctRefs role instanceValue
  RuleOrderedInstants rule -> checkOrderedInstants rule instanceValue
  RuleSequentialSpans rules -> requiredObject "payload" instanceValue >>= validateSequentialSpans rules
  RuleAscendingItems paths -> checkAscendingItems paths instanceValue
  RulePitchStacks rule -> checkPitchStacks rule instanceValue
  RuleBarBeats rule -> checkBarBeats rule instanceValue
  RuleMetricModulations rule -> checkMetricModulations rule instanceValue
  RulePartialDates rule -> checkPartialDates rule instanceValue
  RuleSubset rule -> checkSubset rule instanceValue
  RuleDocumentOrder rule -> checkDocumentOrder rule instanceValue
  RuleNestedRanges rule -> checkNestedRanges rule instanceValue
  RuleReferenceTargets _ -> Right ()
  RuleDistinctTrackContent _ -> Right ()
  RuleTargetMeasure _ -> Right ()
  RuleSameSubject _ -> Right ()
  RuleSpanWindow _ -> Right ()
  RuleTargetBound _ -> Right ()
  RuleTargetSamples _ -> Right ()
  RuleTargetIndex _ -> Right ()
