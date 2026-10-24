{-# LANGUAGE OverloadedStrings #-}

-- | The @x-archive-*@ profile rules: what JSON Schema cannot say about a
-- record, such as ordered spans or the targets of its references. Each rule
-- is checked at a schema's root while a bundle loads and enforced on every
-- instance; its syntax and its evaluator live together in a module under
-- "Archive.Door.Contracts.Profile".
module Archive.Door.Contracts.Profile
  ( profileRuleNames
  , validateContractProfile
  ) where

import Archive.Door.Contracts.Fields (requiredObject)
import Archive.Door.Contracts.Profile.Music
  ( validateBarBeatsInstance
  , validateMetricModulationsInstance
  , validatePitchStacksInstance
  )
import Archive.Door.Contracts.Profile.Payload
  ( validateAscendingItemsInstance
  , validateDocumentOrderInstance
  , validateMonotonicAnchorsInstance
  , validateNestedRangesInstance
  , validateOrderedInstantsInstance
  , validatePartialDatesInstance
  , validateSubsetInstance
  )
import Archive.Door.Contracts.Profile.Refs
  ( validateDistinctRefsInstance
  , validateOrderedDistinctRefsInstance
  , validateSortedRefsInstance
  )
import Archive.Door.Contracts.Profile.Spans (validateOrderedSpansInstance, validateTrackSpansInstance)
import Archive.Door.RuleSyntax (release011RuleNames, releaseRuleNames)
import Archive.Door.SpanRules (sequentialSpansRule, validateSequentialSpans)
import Data.Aeson (Value (..))
import Data.Aeson.KeyMap qualified as KeyMap
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

validateContractProfile :: Value -> Value -> Either Text ()
validateContractProfile (Object schema) (Object instanceValue) = do
  case KeyMap.lookup "x-archive-ordered-sample-spans" schema of
    Nothing -> pure ()
    Just (Object rule) -> validateOrderedSpansInstance rule instanceValue
    Just _ -> Left "ordered sample spans rule must be an object"
  case KeyMap.lookup "x-archive-unique-sorted-refs" schema of
    Nothing -> pure ()
    Just (Object rule) -> validateSortedRefsInstance rule instanceValue
    Just _ -> Left "unique sorted references rule must be an object"
  case KeyMap.lookup "x-archive-track-sample-spans" schema of
    Nothing -> pure ()
    Just (Object rule) -> validateTrackSpansInstance rule instanceValue
    Just _ -> Left "track sample spans rule must be an object"
  case KeyMap.lookup "x-archive-monotonic-anchors" schema of
    Nothing -> pure ()
    Just (Object rule) -> validateMonotonicAnchorsInstance rule instanceValue
    Just _ -> Left "monotonic anchors rule must be an object"
  case KeyMap.lookup "x-archive-distinct-refs" schema of
    Nothing -> pure ()
    Just (Object rule) -> validateDistinctRefsInstance rule instanceValue
    Just _ -> Left "distinct references rule must be an object"
  case KeyMap.lookup "x-archive-ordered-distinct-refs" schema of
    Nothing -> pure ()
    Just (Object rule) -> validateOrderedDistinctRefsInstance rule instanceValue
    Just _ -> Left "ordered distinct references rule must be an object"
  case KeyMap.lookup "x-archive-ordered-instants" schema of
    Nothing -> pure ()
    Just (Object rule) -> validateOrderedInstantsInstance rule instanceValue
    Just _ -> Left "ordered instants rule must be an object"
  case KeyMap.lookup "x-archive-sequential-spans" schema of
    Nothing -> pure ()
    Just rule -> do
      rules <- sequentialSpansRule rule
      payload <- requiredObject "payload" instanceValue
      validateSequentialSpans rules payload
  case KeyMap.lookup "x-archive-ascending-items" schema of
    Nothing -> pure ()
    Just (Object rule) -> validateAscendingItemsInstance rule instanceValue
    Just _ -> Left "ascending items rule must be an object"
  case KeyMap.lookup "x-archive-pitch-stacks" schema of
    Nothing -> pure ()
    Just (Object rule) -> validatePitchStacksInstance rule instanceValue
    Just _ -> Left "pitch stacks rule must be an object"
  case KeyMap.lookup "x-archive-bar-beats" schema of
    Nothing -> pure ()
    Just (Object rule) -> validateBarBeatsInstance rule instanceValue
    Just _ -> Left "bar beats rule must be an object"
  case KeyMap.lookup "x-archive-metric-modulations" schema of
    Nothing -> pure ()
    Just (Object rule) -> validateMetricModulationsInstance rule instanceValue
    Just _ -> Left "metric modulations rule must be an object"
  case KeyMap.lookup "x-archive-partial-dates" schema of
    Nothing -> pure ()
    Just (Object rule) -> validatePartialDatesInstance rule instanceValue
    Just _ -> Left "partial dates rule must be an object"
  case KeyMap.lookup "x-archive-subset" schema of
    Nothing -> pure ()
    Just (Object rule) -> validateSubsetInstance rule instanceValue
    Just _ -> Left "subset rule must be an object"
  case KeyMap.lookup "x-archive-document-order" schema of
    Nothing -> pure ()
    Just (Object rule) -> validateDocumentOrderInstance rule instanceValue
    Just _ -> Left "document order rule must be an object"
  case KeyMap.lookup "x-archive-nested-ranges" schema of
    Nothing -> pure ()
    Just (Object rule) -> validateNestedRangesInstance rule instanceValue
    Just _ -> Left "nested ranges rule must be an object"
validateContractProfile _ _ = Left "contract profile requires an object instance"
