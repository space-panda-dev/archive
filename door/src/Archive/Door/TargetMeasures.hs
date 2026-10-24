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
validateTargetMeasure :: [Target] -> DraftEnvelope -> ContractBundle -> Either Text ()
validateTargetMeasure records draft bundle = do
  rule <- targetMeasureRule (contractBundleSchema bundle)
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
        _ -> Left "target measure rule applies only to a Claim payload"
      targetRate <- integerField (targetMeasureRate measure) target
      targetCount <- integerField (targetMeasureCount measure) target
      ownRate <- integerField (targetMeasureRate measure) own
      if ownRate /= targetRate
        then Left (targetMeasureRate measure <> " must equal the " <> role <> " claim's "
          <> targetMeasureRate measure)
        else Right ()
      groups <- case targetMeasureWithin measure of
        Nothing -> Right [own]
        Just within -> case KeyMap.lookup (Key.fromText within) own of
          Just (Array values) -> mapM (\value -> case value of
            Object group -> Right group
            _ -> Left (within <> " must contain objects")) (toList values)
          _ -> Left (within <> " must be an array")
      spans <- concat <$> mapM (\group -> case KeyMap.lookup (Key.fromText (targetMeasurePayload measure)) group of
        Just (Array values) -> Right (toList values)
        -- With within, an element without the array has nothing to measure.
        Nothing | targetMeasureWithin measure /= Nothing -> Right []
        _ -> Left (targetMeasurePayload measure <> " must be an array")) groups
      ends <- mapM (spanEnd (targetMeasureEnd measure)) spans
      if all (<= targetCount) ends
        then Right ()
        else Left (targetMeasureEnd measure <> " must not exceed the " <> role <> " claim's "
          <> targetMeasureCount measure)
  where
    spanEnd name (Object piece) = integerField name piece
    spanEnd _ _ = Left "span must be an object"
    integerField name fields = case KeyMap.lookup (Key.fromText name) fields of
      Just (Number value) | Right integer <- (floatingOrInteger value :: Either Double Integer) -> Right integer
      _ -> Left (name <> " must be an integer")

-- | The pinned Claim is about the very revision this record names: its one
-- subject reference and this record's one compared reference pin the same
-- object and revision.
validateSameSubject :: [Target] -> DraftEnvelope -> ContractBundle -> Either Text ()
validateSameSubject records draft bundle = do
  rule <- sameSubjectRule (contractBundleSchema bundle)
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
        else Left (role <> " claim must be about the same " <> sameSubjectEquals same
          <> " revision as this record's " <> sameSubjectEquals same <> " reference")

-- | A payload index within what the pinned Claim counts: a channel index no
-- greater than the pinned stream's channel count.
validateTargetBound :: [Target] -> DraftEnvelope -> ContractBundle -> Either Text ()
validateTargetBound records draft bundle = do
  rule <- targetBoundRule (contractBundleSchema bundle)
  case rule of
    Nothing -> Right ()
    Just bound -> do
      own <- case envelopeBody draft of
        ClaimRecord (Object payload) -> Right payload
        _ -> Left "target bound rule applies only to a Claim payload"
      case KeyMap.lookup (Key.fromText (targetBoundField bound)) own of
        Nothing -> Right ()
        Just _ -> do
          let role = targetBoundRole bound
          reference <- case filter ((== role) . referenceRole) (envelopeReferences draft) of
            [reference] -> Right reference
            _ -> Left (targetBoundField bound <> " needs exactly one " <> role <> " reference")
          target <- targetClaimPayload records role reference
          value <- integerField (targetBoundField bound) own
          limit <- integerField (targetBoundBound bound) target
          if value <= limit
            then Right ()
            else Left (targetBoundField bound <> " must not exceed the " <> role <> " claim's "
              <> targetBoundBound bound)
  where
    integerField name fields = case KeyMap.lookup (Key.fromText name) fields of
      Just (Number value) | Right integer <- (floatingOrInteger value :: Either Double Integer) -> Right integer
      _ -> Left (name <> " must be an integer")

-- | Samples within what the pinned Claim counts: a beat-only layer's beats
-- before the pinned stream's sample count.
validateTargetSamples :: [Target] -> DraftEnvelope -> ContractBundle -> Either Text ()
validateTargetSamples records draft bundle = do
  rule <- targetSamplesRule (contractBundleSchema bundle)
  case rule of
    Nothing -> Right ()
    Just samples -> do
      own <- case envelopeBody draft of
        ClaimRecord (Object payload) -> Right payload
        _ -> Left "target samples rule applies only to a Claim payload"
      values <- concat <$> mapM (`pathIntegers` own) (targetSamplesPaths samples)
      if null values
        then Right ()
        else do
          let role = targetSamplesRole samples
          reference <- singleReference draft role
          target <- targetClaimPayload records role reference
          count <- case KeyMap.lookup (Key.fromText (targetSamplesCount samples)) target of
            Just (Number value) | Right integer <- (floatingOrInteger value :: Either Double Integer) -> Right integer
            _ -> Left (targetSamplesCount samples <> " must be an integer")
          if all (< count) values
            then Right ()
            else Left ("samples must be before the " <> role <> " claim's " <> targetSamplesCount samples)

-- | Positions in the pinned Claim's array: each span's entry in the chord
-- sequence it was placed from.
validateTargetIndex :: [Target] -> DraftEnvelope -> ContractBundle -> Either Text ()
validateTargetIndex records draft bundle = do
  rule <- targetIndexRule (contractBundleSchema bundle)
  case rule of
    Nothing -> Right ()
    Just index -> do
      own <- case envelopeBody draft of
        ClaimRecord (Object payload) -> Right payload
        _ -> Left "target index rule applies only to a Claim payload"
      values <- pathIntegers (targetIndexPath index) own
      if null values
        then Right ()
        else do
          let role = targetIndexRole index
          reference <- case filter ((== role) . referenceRole) (envelopeReferences draft) of
            [reference] -> Right reference
            _ -> Left (Text.intercalate "." (targetIndexPath index) <> " needs exactly one " <> role <> " reference")
          target <- targetClaimPayload records role reference
          size <- case KeyMap.lookup (Key.fromText (targetIndexArray index)) target of
            Just (Array items) -> Right (fromIntegral (length items))
            _ -> Left (role <> " claim's " <> targetIndexArray index <> " must be an array")
          if all (\value -> value >= 0 && value < size) values
            then Right ()
            else Left (Text.intercalate "." (targetIndexPath index) <> " must be a position in the "
              <> role <> " claim's " <> targetIndexArray index)

-- | A window is a run of whole spans of the Claim it was cut from: its rate
-- equals the pinned Claim's, its start is some span's start, and its end is
-- the end of that span or of a later one.
validateSpanWindow :: [Target] -> DraftEnvelope -> ContractBundle -> Either Text ()
validateSpanWindow records draft bundle = do
  rule <- spanWindowRule (contractBundleSchema bundle)
  case rule of
    Nothing -> Right ()
    Just window -> do
      let role = spanWindowRole window
      reference <- singleReference draft role
      target <- targetClaimPayload records role reference
      own <- case envelopeBody draft of
        ClaimRecord (Object payload) -> Right payload
        _ -> Left "span window rule applies only to a Claim payload"
      targetRate <- integerField (spanWindowRate window) target
      ownRate <- integerField (spanWindowRate window) own
      if ownRate /= targetRate
        then Left (spanWindowRate window <> " must equal the " <> role <> " claim's "
          <> spanWindowRate window)
        else Right ()
      bounds <- case KeyMap.lookup (Key.fromText (spanWindowPayload window)) own of
        Just (Object fields) -> Right fields
        _ -> Left (spanWindowPayload window <> " must be an object")
      start <- integerField (spanWindowStart window) bounds
      end <- integerField (spanWindowEnd window) bounds
      spans <- case KeyMap.lookup (Key.fromText (spanWindowSpans window)) target of
        Just (Array values) -> mapM spanBounds (toList values)
        _ -> Left (role <> " claim's " <> spanWindowSpans window <> " must be an array")
      case dropWhile ((/= start) . fst) spans of
        [] -> Left (spanWindowPayload window <> " " <> spanWindowStart window
          <> " must be the " <> spanWindowStart window <> " of one of the " <> role <> " claim's "
          <> spanWindowSpans window)
        from | any ((== end) . snd) from -> Right ()
        _ -> Left (spanWindowPayload window <> " " <> spanWindowEnd window
          <> " must be the " <> spanWindowEnd window <> " of its first span or of a later one")
      where
        spanBounds (Object piece) = (,) <$> integerField (spanWindowStart window) piece
          <*> integerField (spanWindowEnd window) piece
        spanBounds _ = Left "span must be an object"
  where
    integerField name fields = case KeyMap.lookup (Key.fromText name) fields of
      Just (Number value) | Right integer <- (floatingOrInteger value :: Either Double Integer) -> Right integer
      _ -> Left (name <> " must be an integer")

-- | Bit-identical recordings are one source.  The digest is the pinned
-- Artifact revision's committed content digest, never a proposer's claim.
validateDistinctTrackContent :: [Target] -> DraftEnvelope -> Value -> ContractBundle -> Either Text ()
validateDistinctTrackContent records draft instanceValue bundle = do
  rule <- distinctTrackContentRule (contractBundleSchema bundle) instanceValue
  case rule of
    Nothing -> Right ()
    Just (role, counts) -> do
      let references = filter ((== role) . referenceRole) (envelopeReferences draft)
      if sum counts /= length references
        then Left "track spans and span references must be equal in number"
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
        else Left ("tracks must not pin the same content digest: " <> Text.intercalate ", " (Set.toList repeated))
  where
    artifactDigest role reference = do
      target <- referencedEntry records role reference
      case envelopeBody (targetDraft target) of
        ArtifactRecord (Object payload) -> case KeyMap.lookup "content" payload of
          Just (Object content) -> case KeyMap.lookup "digest" content of
            Just (String digest) -> Right digest
            _ -> Left (role <> " Artifact has no content digest")
          _ -> Left (role <> " Artifact has no content")
        _ -> Left (role <> " reference must target an Artifact revision")
    splitCounts [] _ = []
    splitCounts (count : rest) values = take count values : splitCounts rest (drop count values)
