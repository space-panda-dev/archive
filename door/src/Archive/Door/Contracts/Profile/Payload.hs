{-# LANGUAGE OverloadedStrings #-}

-- | The profile rules over a record's payload fields: monotonic anchors,
-- ordered instants, and partial dates.
module Archive.Door.Contracts.Profile.Payload
  ( MonotonicAnchors (..)
  , monotonicAnchorsRule
  , checkMonotonicAnchors
  , OrderedInstants (..)
  , orderedInstantsRule
  , checkOrderedInstants
  , PartialDates (..)
  , partialDatesRule
  , checkPartialDates
  ) where

import Archive.Door.Contracts.Fields
  ( requireExactKeys
  , requiredInteger
  , requiredObject
  , requiredText
  , requiredStrings
  , unique
  )
import Archive.Door.Contracts.Profile.Error (RuleError (..), RuleViolation (..))
import Archive.Door.Contracts.Profile.Syntax (validateNamedFieldsRule)
import Data.Aeson (Value (..))
import Data.Aeson.Key qualified as Key
import Data.Aeson.KeyMap qualified as KeyMap
import Data.Foldable (toList)
import Data.Text (Text)
import Data.Text qualified as Text
import Data.Time (Day, UTCTime, defaultTimeLocale, fromGregorian, fromGregorianValid, gregorianMonthLength, parseTimeM)

-- | @x-archive-monotonic-anchors@: every element of the payload array
-- @payload@ holds the two integer fields, and each field strictly increases
-- from one element to the next. The rule names exactly two, so they are a
-- pair.
data MonotonicAnchors = MonotonicAnchors
  { anchorsPayload :: Text
  , anchorsFields :: (Text, Text)
  } deriving (Eq, Show)

monotonicAnchorsRule :: Value -> Either RuleError MonotonicAnchors
monotonicAnchorsRule (Object fields) = do
  requireExactKeys "monotonic anchors rule" ["payload", "fields"] fields
  payload <- requiredText "payload" fields
  names <- requiredStrings "fields" fields
  case names of
    [first, second] | first /= second -> Right (MonotonicAnchors payload (first, second))
    _ -> Left (TwoFieldsNotDistinct "monotonic anchors rule")
monotonicAnchorsRule _ = Left (RuleNotObject "monotonic anchors rule")

checkMonotonicAnchors :: MonotonicAnchors -> KeyMap.KeyMap Value -> Either RuleViolation ()
checkMonotonicAnchors rule instanceValue = do
  payload <- requiredObject "payload" instanceValue
  anchors <- case KeyMap.lookup (Key.fromText (anchorsPayload rule)) payload of
    Just (Array values) | not (null values) -> Right (toList values)
    _ -> Left (NotNonEmptyArray "monotonic anchors payload")
  rows <- mapM anchorRow anchors
  let increasing column = and (zipWith (<) (map column rows) (drop 1 (map column rows)))
  if increasing fst && increasing snd
    then Right ()
    else Left AnchorsNotIncreasing
  where
    (firstName, secondName) = anchorsFields rule
    anchorRow (Object anchor) = (,) <$> requiredInteger firstName anchor <*> requiredInteger secondName anchor
    anchorRow _ = Left (NotObject "anchor")

-- | @x-archive-ordered-instants@: both named payload fields are RFC 3339
-- instants in UTC with a @Z@ suffix, and @end@ is not earlier than @start@.
data OrderedInstants = OrderedInstants
  { instantsStart :: Text
  , instantsEnd :: Text
  } deriving (Eq, Show)

orderedInstantsRule :: Value -> Either RuleError OrderedInstants
orderedInstantsRule value = do
  validateNamedFieldsRule "ordered instants rule" ["start", "end"] value
  case value of
    Object fields -> do
      start <- requiredText "start" fields
      end <- requiredText "end" fields
      if start == end then Left (TwoFieldsNotDistinct "ordered instants rule") else Right ()
      pure (OrderedInstants start end)
    _ -> Left (RuleNotObject "ordered instants rule")

checkOrderedInstants :: OrderedInstants -> KeyMap.KeyMap Value -> Either RuleViolation ()
checkOrderedInstants rule instanceValue = do
  payload <- requiredObject "payload" instanceValue
  start <- utcInstant startName payload
  end <- utcInstant endName payload
  if end >= start
    then Right ()
    else Left (EndBeforeStart endName startName)
  where
    startName = instantsStart rule
    endName = instantsEnd rule
    utcInstant name payload = do
      value <- requiredText name payload
      case parseUtcInstant value of
        Just instant -> Right instant
        Nothing -> Left (NotUtcInstant name)

parseUtcInstant :: Text -> Maybe UTCTime
parseUtcInstant value
  | Text.isSuffixOf "Z" value && Text.isInfixOf "T" value =
      parseTimeM False defaultTimeLocale "%Y-%m-%dT%H:%M:%S%QZ" (Text.unpack value)
  | otherwise = Nothing

-- | @x-archive-partial-dates@: each payload field in @fields@, when present,
-- begins with a calendar date written @YYYY@, @YYYY-MM@, or @YYYY-MM-DD@ (a
-- year from 1, a real month, a real day of it); what may follow is the
-- schema's pattern's. With @start@ and @end@, both among @fields@, the
-- last day @end@ can mean MUST NOT be before the first day @start@ can mean.
data PartialDates = PartialDates
  { partialDatesFields :: [Text]
  -- | The @start@ and @end@ fields, when the rule orders two of its fields.
  , partialDatesOrder :: Maybe (Text, Text)
  } deriving (Eq, Show)

partialDatesRule :: Value -> Either RuleError PartialDates
partialDatesRule (Object fields) = do
  let ordered = KeyMap.member "start" fields || KeyMap.member "end" fields
  requireExactKeys "partial dates rule" (["fields"] <> (if ordered then ["start", "end"] else [])) fields
  names <- requiredStrings "fields" fields
  if null names || length names /= length (unique names)
    then Left (FieldsNotDistinct "partial dates rule")
    else Right ()
  order <- if ordered
    then do
      start <- requiredText "start" fields
      end <- requiredText "end" fields
      if start /= end && start `elem` names && end `elem` names
        then Right (Just (start, end))
        else Left DatesOrderNotOwnFields
    else Right Nothing
  pure (PartialDates names order)
partialDatesRule _ = Left (RuleNotObject "partial dates rule")

checkPartialDates :: PartialDates -> KeyMap.KeyMap Value -> Either RuleViolation ()
checkPartialDates rule instanceValue = do
  payload <- requiredObject "payload" instanceValue
  let dateOf name = case KeyMap.lookup (Key.fromText name) payload of
        Nothing -> Right Nothing
        Just (String text) -> maybe (Left (NotPartialDate name)) (Right . Just)
          (partialDate (Text.takeWhile (/= 'T') text))
        Just _ -> Left (NotText name)
  mapM_ dateOf (partialDatesFields rule)
  case partialDatesOrder rule of
    Just (start, end) -> do
      from <- dateOf start
      to <- dateOf end
      case (from, to) of
        (Just (earliest, _), Just (_, latest)) | latest < earliest ->
          Left (EndBeforeStart end start)
        _ -> Right ()
    Nothing -> Right ()

-- | The first and last day a partial date can mean.
partialDate :: Text -> Maybe (Day, Day)
partialDate text = case Text.splitOn "-" text of
  [year] -> do
    y <- digits 4 year
    if y < 1 then Nothing else Just (fromGregorian y 1 1, fromGregorian y 12 31)
  [year, month] -> do
    y <- digits 4 year
    m <- digits 2 month
    first <- if y < 1 then Nothing else fromGregorianValid y (fromInteger m) 1
    pure (first, fromGregorian y (fromInteger m) (gregorianMonthLength y (fromInteger m)))
  [year, month, day] -> do
    y <- digits 4 year
    m <- digits 2 month
    d <- digits 2 day
    exact <- if y < 1 then Nothing else fromGregorianValid y (fromInteger m) (fromInteger d)
    pure (exact, exact)
  _ -> Nothing
  where
    digits count piece
      | Text.length piece == count && Text.all (\c -> '0' <= c && c <= '9') piece = Just (read (Text.unpack piece))
      | otherwise = Nothing
