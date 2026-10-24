{-# LANGUAGE OverloadedStrings #-}

-- | The profile rules that compare a record with the Claim it pins in a
-- role. Door checks them during admission, where the pinned records are
-- available; here they are parsed and their roles checked against the
-- reference target rules.
module Archive.Door.Contracts.Profile.PinnedTargets
  ( TargetMeasureRule (..)
  , targetMeasureRule
  , SameSubjectRule (..)
  , sameSubjectRule
  , SpanWindowRule (..)
  , spanWindowRule
  , TargetBoundRule (..)
  , targetBoundRule
  , TargetSamplesRule (..)
  , targetSamplesRule
  , TargetIndexRule (..)
  , targetIndexRule
  ) where

import Archive.Door.Contracts.Fields
  ( objectValue
  , requireExactKeys
  , requiredText
  , validateNamedFieldsRule
  )
import Archive.Door.Contracts.Profile.Paths (payloadPaths, requiredPath)
import Archive.Door.Contracts.Profile.ReferenceTargets (requireSingleClaimTarget)
import Data.Aeson (Value (..))
import Data.Aeson.KeyMap qualified as KeyMap
import Data.Text (Text)

-- | @x-archive-target-measure@: the record pins one Claim in @role@ whose
-- payload holds integer fields @rate@ and @count@.  The record's own payload
-- @rate@ MUST equal the target's, and every element of the payload array
-- @payload@ MUST have an integer @end@ no greater than the target's @count@.
--
-- With @within@ (specification 0.0.9), @payload@ is an array inside each
-- element of the payload array @within@, and every element of every such
-- array is measured.
data TargetMeasureRule = TargetMeasureRule
  { targetMeasureRole :: Text
  , targetMeasureRate :: Text
  , targetMeasureCount :: Text
  , targetMeasurePayload :: Text
  , targetMeasureEnd :: Text
  , targetMeasureWithin :: Maybe Text
  -- | With @optional@ true, a record whose payload lacks @payload@ (or
  -- @within@) is not measured.
  , targetMeasureOptional :: Bool
  } deriving (Eq, Show)

targetMeasureRule :: Value -> Either Text (Maybe TargetMeasureRule)
targetMeasureRule schema@(Object fields) = case KeyMap.lookup "x-archive-target-measure" fields of
  Nothing -> Right Nothing
  Just value -> do
    let names = ["role", "rate", "count", "payload", "end"]
    validateWithinFieldsRule "target measure rule" names value
    rule <- objectValue "target measure rule" value
    optional <- optionalFlag rule
    role <- requiredText "role" rule
    rate <- requiredText "rate" rule
    count <- requiredText "count" rule
    payload <- requiredText "payload" rule
    end <- requiredText "end" rule
    within <- optionalWithin rule
    requireSingleClaimTarget "target measure rule" schema role
    pure (Just (TargetMeasureRule role rate count payload end within optional))
targetMeasureRule _ = Left "contract schema must be an object"

-- | @x-archive-same-subject@: the Claim pinned in @role@ has exactly one
-- @subject@ reference, and it MUST pin the same object and revision as this
-- record's one @equals@ reference.
data SameSubjectRule = SameSubjectRule
  { sameSubjectRole :: Text
  , sameSubjectSubject :: Text
  , sameSubjectEquals :: Text
  -- | With @optional@ true, a record with no reference in @role@ is not
  -- compared.
  , sameSubjectOptional :: Bool
  } deriving (Eq, Show)

sameSubjectRule :: Value -> Either Text (Maybe SameSubjectRule)
sameSubjectRule schema@(Object fields) = case KeyMap.lookup "x-archive-same-subject" fields of
  Nothing -> Right Nothing
  Just value -> do
    let names = ["role", "subject", "equals"]
    validateOptionalFieldsRule "same subject rule" names value
    rule <- objectValue "same subject rule" value
    optional <- optionalFlag rule
    role <- requiredText "role" rule
    subject <- requiredText "subject" rule
    equals <- requiredText "equals" rule
    if role == equals
      then Left "same subject rule must compare two different roles"
      else Right ()
    requireSingleClaimTarget "same subject rule" schema role
    pure (Just (SameSubjectRule role subject equals optional))
sameSubjectRule _ = Left "contract schema must be an object"

-- | @x-archive-span-window@: the record pins one Claim in @role@ whose
-- payload holds an integer @rate@ field and an array @spans@ of objects with
-- integer @start@ and @end@ fields.  The record's own payload @rate@ MUST
-- equal the target's, and its payload object @payload@ is a window whose
-- @start@ MUST be the @start@ of some element of @spans@ and whose @end@ MUST
-- be the @end@ of that element or of a later one: a run of whole spans.
data SpanWindowRule = SpanWindowRule
  { spanWindowRole :: Text
  , spanWindowRate :: Text
  , spanWindowPayload :: Text
  , spanWindowStart :: Text
  , spanWindowEnd :: Text
  , spanWindowSpans :: Text
  } deriving (Eq, Show)

spanWindowRule :: Value -> Either Text (Maybe SpanWindowRule)
spanWindowRule schema@(Object fields) = case KeyMap.lookup "x-archive-span-window" fields of
  Nothing -> Right Nothing
  Just value -> do
    let names = ["role", "rate", "payload", "start", "end", "spans"]
    validateNamedFieldsRule "span window rule" names value
    rule <- objectValue "span window rule" value
    role <- requiredText "role" rule
    rate <- requiredText "rate" rule
    payload <- requiredText "payload" rule
    start <- requiredText "start" rule
    end <- requiredText "end" rule
    spans <- requiredText "spans" rule
    if start == end
      then Left "span window rule must name two distinct bound fields"
      else Right ()
    requireSingleClaimTarget "span window rule" schema role
    pure (Just (SpanWindowRule role rate payload start end spans))
spanWindowRule _ = Left "contract schema must be an object"

-- | A rule with named fields that may also name @within@ (specification
-- 0.0.9): a payload array whose elements each hold the rule's array.
validateWithinFieldsRule :: Text -> [Text] -> Value -> Either Text ()
validateWithinFieldsRule label names value@(Object fields) =
  validateOptionalFieldsRule label (names <> ["within" | KeyMap.member "within" fields]) value
validateWithinFieldsRule label _ _ = Left (label <> " must be an object")

-- | A rule with named fields that may also say @optional@: true
-- (specification 0.0.9), so that it applies only to records that have what
-- it reads. @optional@ false is refused: absence says it.
validateOptionalFieldsRule :: Text -> [Text] -> Value -> Either Text ()
validateOptionalFieldsRule label names value@(Object fields) = do
  validateNamedFieldsRule label names (Object (KeyMap.delete "optional" fields))
  _ <- optionalFlag fields
  pure (const () value)
validateOptionalFieldsRule label _ _ = Left (label <> " must be an object")

optionalFlag :: KeyMap.KeyMap Value -> Either Text Bool
optionalFlag rule = case KeyMap.lookup "optional" rule of
  Nothing -> Right False
  Just (Bool True) -> Right True
  Just _ -> Left "a rule's optional must be true when present"

optionalWithin :: KeyMap.KeyMap Value -> Either Text (Maybe Text)
optionalWithin rule = case KeyMap.lookup "within" rule of
  Nothing -> Right Nothing
  Just _ -> Just <$> requiredText "within" rule

-- | @x-archive-target-samples@: every integer the @paths@ reach MUST be
-- less than the integer @count@ of the Claim the record pins in @role@ (a
-- beat's sample within the pinned stream).
data TargetSamplesRule = TargetSamplesRule
  { targetSamplesRole :: Text
  , targetSamplesCount :: Text
  , targetSamplesPaths :: [[Text]]
  } deriving (Eq, Show)

targetSamplesRule :: Value -> Either Text (Maybe TargetSamplesRule)
targetSamplesRule schema@(Object fields) = case KeyMap.lookup "x-archive-target-samples" fields of
  Nothing -> Right Nothing
  Just value -> do
    rule <- objectValue "target samples rule" value
    requireExactKeys "target samples rule" ["role", "count", "paths"] rule
    role <- requiredText "role" rule
    count <- requiredText "count" rule
    paths <- maybe (Left "target samples rule requires paths") (payloadPaths "target samples rule paths")
      (KeyMap.lookup "paths" rule)
    if null paths then Left "target samples rule must name at least one path" else Right ()
    requireSingleClaimTarget "target samples rule" schema role
    pure (Just (TargetSamplesRule role count paths))
targetSamplesRule _ = Left "contract schema must be an object"

-- | @x-archive-target-index@: every integer the @path@ reaches MUST be a
-- position in the payload array @array@ of the Claim the record pins in
-- @role@; a record with such integers MUST pin exactly one (a span's entry
-- in the chord sequence it was placed from).
data TargetIndexRule = TargetIndexRule
  { targetIndexRole :: Text
  , targetIndexPath :: [Text]
  , targetIndexArray :: Text
  } deriving (Eq, Show)

targetIndexRule :: Value -> Either Text (Maybe TargetIndexRule)
targetIndexRule schema@(Object fields) = case KeyMap.lookup "x-archive-target-index" fields of
  Nothing -> Right Nothing
  Just value -> do
    rule <- objectValue "target index rule" value
    requireExactKeys "target index rule" ["role", "path", "array"] rule
    role <- requiredText "role" rule
    path <- requiredPath "path" rule
    array <- requiredText "array" rule
    requireSingleClaimTarget "target index rule" schema role
    pure (Just (TargetIndexRule role path array))
targetIndexRule _ = Left "contract schema must be an object"

-- | @x-archive-target-bound@: when this record's payload has the integer
-- @field@, the record pins one Claim in @role@, and @field@ MUST NOT exceed
-- the integer @bound@ of that Claim's payload (a channel index within the
-- pinned stream's channel count).
data TargetBoundRule = TargetBoundRule
  { targetBoundRole :: Text
  , targetBoundField :: Text
  , targetBoundBound :: Text
  } deriving (Eq, Show)

targetBoundRule :: Value -> Either Text (Maybe TargetBoundRule)
targetBoundRule schema@(Object fields) = case KeyMap.lookup "x-archive-target-bound" fields of
  Nothing -> Right Nothing
  Just value -> do
    validateNamedFieldsRule "target bound rule" ["role", "field", "bound"] value
    rule <- objectValue "target bound rule" value
    role <- requiredText "role" rule
    field <- requiredText "field" rule
    bound <- requiredText "bound" rule
    requireSingleClaimTarget "target bound rule" schema role
    pure (Just (TargetBoundRule role field bound))
targetBoundRule _ = Left "contract schema must be an object"
