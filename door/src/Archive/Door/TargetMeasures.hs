{-# LANGUAGE OverloadedStrings #-}

-- | Payload rules measured against a pinned record's payload.
--
-- Each rule here reads a number or an array from the Claim (or Artifact) a
-- reference pins and bounds this record's payload by it: rates, counts,
-- channel indexes, sample positions, chord-sequence positions, windows, and
-- distinct track content.
module Archive.Door.TargetMeasures
  ( validateTargetMeasure
  , validateSameSubject
  , validateTargetBound
  , validateTargetSamples
  , validateTargetIndex
  , validateSpanWindow
  , validateDistinctTrackContent
  ) where

import Archive.Door.Contracts
  ( ContractBundle (contractBundleSchema)
  , SameSubjectRule (..)
  , SpanWindowRule (..)
  , TargetBoundRule (..)
  , TargetIndexRule (..)
  , TargetMeasureRule (..)
  , TargetSamplesRule (..)
  , distinctTrackContentRule
  , pathIntegers
  , sameSubjectRule
  , spanWindowRule
  , targetBoundRule
  , targetIndexRule
  , targetMeasureRule
  , targetSamplesRule
  )
import Archive.Door.ContractRuleProblem (ContractRuleProblem (..), outsideRule)
import Archive.Door.ReferenceTargets
  ( Target (..)
  , referencedEntry
  , singleReference
  , targetClaimPayload
  )
import Archive.Door.Types
  ( DraftEnvelope
  , RecordBody (..)
  , Reference (..)
  , envelopeBody
  , envelopeReferences
  )
import Data.Aeson (Value (Array, Number, Object, String))
import qualified Data.Aeson.Key as Key
import qualified Data.Aeson.KeyMap as KeyMap
import Data.Foldable (toList)
import Data.Scientific (floatingOrInteger)
import qualified Data.Set as Set
import Data.Text (Text)
import qualified Data.Text as Text

-- | A record's measurements fit the Claim that measured the audio: its rate
-- equals the pinned Claim's, and no span ends past the pinned Claim's count.
validateTargetMeasure :: [Target] -> DraftEnvelope -> ContractBundle -> Either ContractRuleProblem ()
validateTargetMeasure records draft bundle = do
  rule <- outsideRule (targetMeasureRule (contractBundleSchema bundle))
  case rule of
    Nothing -> Right ()
    Just measure
      | targetMeasureOptional measure, ClaimRecord (Object own) <- envelopeBody draft
      , not (KeyMap.member (Key.fromText (maybe (targetMeasurePayload measure) id (targetMeasureWithin measure))) own)
        -> Right ()
    Just measure -> do
      let role = targetMeasureRole measure
      reference <- singleReference draft role
      target <- targetClaimPayload records role reference
      own <- case envelopeBody draft of
        ClaimRecord (Object payload) -> Right payload
        _ -> Left (RuleNeedsClaim "target measure")
      targetRate <- integerField (targetMeasureRate measure) target
      targetCount <- integerField (targetMeasureCount measure) target
      ownRate <- integerField (targetMeasureRate measure) own
      if ownRate /= targetRate
        then Left (MustEqualTarget (targetMeasureRate measure) role)
        else Right ()
      groups <- case targetMeasureWithin measure of
        Nothing -> Right [own]
        Just within -> case KeyMap.lookup (Key.fromText within) own of
          Just (Array values) -> mapM (\value -> case value of
            Object group -> Right group
            _ -> Left (MustContainObjects within)) (toList values)
          _ -> Left (NotAnArray within)
      spans <- concat <$> mapM (\group -> case KeyMap.lookup (Key.fromText (targetMeasurePayload measure)) group of
        Just (Array values) -> Right (toList values)
        -- With within, an element without the array has nothing to measure.
        Nothing | targetMeasureWithin measure /= Nothing -> Right []
        _ -> Left (NotAnArray (targetMeasurePayload measure))) groups
      ends <- mapM (spanEnd (targetMeasureEnd measure)) spans
      if all (<= targetCount) ends
        then Right ()
        else Left (MustNotExceed (targetMeasureEnd measure) role (targetMeasureCount measure))
  where
    spanEnd name (Object piece) = integerField name piece
    spanEnd _ _ = Left SpanNotObject

-- | The pinned Claim is about the very revision this record names: its one
-- subject reference and this record's one compared reference pin the same
-- object and revision.
validateSameSubject :: [Target] -> DraftEnvelope -> ContractBundle -> Either ContractRuleProblem ()
validateSameSubject records draft bundle = do
  rule <- outsideRule (sameSubjectRule (contractBundleSchema bundle))
  case rule of
    Nothing -> Right ()
    Just same
      | sameSubjectOptional same
      , not (any ((== sameSubjectRole same) . referenceRole) (envelopeReferences draft)) -> Right ()
    Just same -> do
      let role = sameSubjectRole same
      reference <- singleReference draft role
      target <- referencedEntry records role reference
      subject <- singleReference (targetDraft target) (sameSubjectSubject same)
      compared <- singleReference draft (sameSubjectEquals same)
      if referenceObject subject == referenceObject compared
          && referenceRevision subject == referenceRevision compared
          && referenceRevision compared /= Nothing
        then Right ()
        else Left (NotSameSubject role (sameSubjectEquals same))

-- | A payload index within what the pinned Claim counts: a channel index no
-- greater than the pinned stream's channel count.
validateTargetBound :: [Target] -> DraftEnvelope -> ContractBundle -> Either ContractRuleProblem ()
validateTargetBound records draft bundle = do
  rule <- outsideRule (targetBoundRule (contractBundleSchema bundle))
  case rule of
    Nothing -> Right ()
    Just bound -> do
      own <- case envelopeBody draft of
        ClaimRecord (Object payload) -> Right payload
        _ -> Left (RuleNeedsClaim "target bound")
      case KeyMap.lookup (Key.fromText (targetBoundField bound)) own of
        Nothing -> Right ()
        Just _ -> do
          let role = targetBoundRole bound
          reference <- case filter ((== role) . referenceRole) (envelopeReferences draft) of
            [reference] -> Right reference
            _ -> Left (NeedsOneReference (targetBoundField bound) role)
          target <- targetClaimPayload records role reference
          value <- integerField (targetBoundField bound) own
          limit <- integerField (targetBoundBound bound) target
          if value <= limit
            then Right ()
            else Left (MustNotExceed (targetBoundField bound) role (targetBoundBound bound))

-- | Samples within what the pinned Claim counts: a beat-only layer's beats
-- before the pinned stream's sample count.
validateTargetSamples :: [Target] -> DraftEnvelope -> ContractBundle -> Either ContractRuleProblem ()
validateTargetSamples records draft bundle = do
  rule <- outsideRule (targetSamplesRule (contractBundleSchema bundle))
  case rule of
    Nothing -> Right ()
    Just samples -> do
      own <- case envelopeBody draft of
        ClaimRecord (Object payload) -> Right payload
        _ -> Left (RuleNeedsClaim "target samples")
      values <- concat <$> mapM (outsideRule . (`pathIntegers` own)) (targetSamplesPaths samples)
      if null values
        then Right ()
        else do
          let role = targetSamplesRole samples
          reference <- singleReference draft role
          target <- targetClaimPayload records role reference
          count <- integerField (targetSamplesCount samples) target
          if all (< count) values
            then Right ()
            else Left (SamplesNotBefore role (targetSamplesCount samples))

-- | Positions in the pinned Claim's array: each span's entry in the chord
-- sequence it was placed from.
validateTargetIndex :: [Target] -> DraftEnvelope -> ContractBundle -> Either ContractRuleProblem ()
validateTargetIndex records draft bundle = do
  rule <- outsideRule (targetIndexRule (contractBundleSchema bundle))
  case rule of
    Nothing -> Right ()
    Just index -> do
      own <- case envelopeBody draft of
        ClaimRecord (Object payload) -> Right payload
        _ -> Left (RuleNeedsClaim "target index")
      values <- outsideRule (pathIntegers (targetIndexPath index) own)
      if null values
        then Right ()
        else do
          let role = targetIndexRole index
          reference <- case filter ((== role) . referenceRole) (envelopeReferences draft) of
            [reference] -> Right reference
            _ -> Left (NeedsOneReference (Text.intercalate "." (targetIndexPath index)) role)
          target <- targetClaimPayload records role reference
          size <- case KeyMap.lookup (Key.fromText (targetIndexArray index)) target of
            Just (Array items) -> Right (fromIntegral (length items))
            _ -> Left (TargetArrayNotArray role (targetIndexArray index))
          if all (\value -> value >= 0 && value < size) values
            then Right ()
            else Left (NotAPosition (Text.intercalate "." (targetIndexPath index)) role (targetIndexArray index))

-- | A window is a run of whole spans of the Claim it was cut from: its rate
-- equals the pinned Claim's, its start is some span's start, and its end is
-- the end of that span or of a later one.
validateSpanWindow :: [Target] -> DraftEnvelope -> ContractBundle -> Either ContractRuleProblem ()
validateSpanWindow records draft bundle = do
  rule <- outsideRule (spanWindowRule (contractBundleSchema bundle))
  case rule of
    Nothing -> Right ()
    Just window -> do
      let role = spanWindowRole window
      reference <- singleReference draft role
      target <- targetClaimPayload records role reference
      own <- case envelopeBody draft of
        ClaimRecord (Object payload) -> Right payload
        _ -> Left (RuleNeedsClaim "span window")
      targetRate <- integerField (spanWindowRate window) target
      ownRate <- integerField (spanWindowRate window) own
      if ownRate /= targetRate
        then Left (MustEqualTarget (spanWindowRate window) role)
        else Right ()
      bounds <- case KeyMap.lookup (Key.fromText (spanWindowPayload window)) own of
        Just (Object fields) -> Right fields
        _ -> Left (NotAnObject (spanWindowPayload window))
      start <- integerField (spanWindowStart window) bounds
      end <- integerField (spanWindowEnd window) bounds
      spans <- case KeyMap.lookup (Key.fromText (spanWindowSpans window)) target of
        Just (Array values) -> mapM spanBounds (toList values)
        _ -> Left (TargetArrayNotArray role (spanWindowSpans window))
      case dropWhile ((/= start) . fst) spans of
        [] -> Left (WindowStartNotSpanStart (spanWindowPayload window) (spanWindowStart window)
          role (spanWindowSpans window))
        from | any ((== end) . snd) from -> Right ()
        _ -> Left (WindowEndNotSpanEnd (spanWindowPayload window) (spanWindowEnd window))
      where
        spanBounds (Object piece) = (,) <$> integerField (spanWindowStart window) piece
          <*> integerField (spanWindowEnd window) piece
        spanBounds _ = Left SpanNotObject

-- | Bit-identical recordings are one source.  The digest is the pinned
-- Artifact revision's committed content digest, never a proposer's claim.
validateDistinctTrackContent :: [Target] -> DraftEnvelope -> Value -> ContractBundle -> Either ContractRuleProblem ()
validateDistinctTrackContent records draft instanceValue bundle = do
  rule <- outsideRule (distinctTrackContentRule (contractBundleSchema bundle) instanceValue)
  case rule of
    Nothing -> Right ()
    Just (role, counts) -> do
      let references = filter ((== role) . referenceRole) (envelopeReferences draft)
      if sum counts /= length references
        then Left TrackSpanCountMismatch
        else pure ()
      digests <- mapM (artifactDigest role) references
      let perTrack = map Set.fromList (splitCounts counts digests)
          repeated = Set.fromList
            [ digest
            | (index, track) <- zip [0 :: Int ..] perTrack
            , (otherIndex, other) <- zip [0 ..] perTrack
            , index < otherIndex
            , digest <- Set.toList (Set.intersection track other)
            ]
      if Set.null repeated
        then Right ()
        else Left (RepeatedTrackContent (Set.toList repeated))
  where
    artifactDigest role reference = do
      target <- referencedEntry records role reference
      case envelopeBody (targetDraft target) of
        ArtifactRecord (Object payload) -> case KeyMap.lookup "content" payload of
          Just (Object content) -> case KeyMap.lookup "digest" content of
            Just (String digest) -> Right digest
            _ -> Left (ArtifactNoDigest role)
          _ -> Left (ArtifactNoContent role)
        _ -> Left (TargetWrongShape role "artifact")
    splitCounts [] _ = []
    splitCounts (count : rest) values = take count values : splitCounts rest (drop count values)

-- | An integer field of a payload; the rules here all refuse a missing or
-- fractional one with the same words.
integerField :: Text -> KeyMap.KeyMap Value -> Either ContractRuleProblem Integer
integerField name fields = case KeyMap.lookup (Key.fromText name) fields of
  Just (Number value) | Right integer <- (floatingOrInteger value :: Either Double Integer) -> Right integer
  _ -> Left (NotAnInteger name)
