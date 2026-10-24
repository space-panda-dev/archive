{-# LANGUAGE OverloadedStrings #-}

-- | The profile rules over a record's payload fields: monotonic anchors,
-- ordered instants, ascending items, subsets, document order, nested
-- ranges, and partial dates.
module Archive.Door.Contracts.Profile.Payload
  ( validateMonotonicAnchorsRule
  , validateOrderedInstantsRule
  , validateAscendingItemsRule
  , validateSubsetRule
  , validateDocumentOrderRule
  , validateNestedRangesRule
  , validatePartialDatesRule
  , validateMonotonicAnchorsInstance
  , validateOrderedInstantsInstance
  , validateAscendingItemsInstance
  , validateSubsetInstance
  , validateDocumentOrderInstance
  , validateNestedRangesInstance
  , validatePartialDatesInstance
  ) where

import Archive.Door.Contracts.Fields
  ( requireExactKeys
  , requiredInteger
  , requiredObject
  , requiredString
  , requiredStrings
  , unique
  , validateNamedFieldsRule
  )
import Archive.Door.Contracts.Profile.Paths (pathHolders, payloadPaths, requiredPath)
import Data.Aeson (Value (..))
import Data.Aeson.Key qualified as Key
import Data.Aeson.KeyMap qualified as KeyMap
import Data.Foldable (toList)
import Data.List (sort)
import Data.Scientific (floatingOrInteger)
import Data.Text (Text)
import Data.Text qualified as Text
import Data.Time (Day, UTCTime, defaultTimeLocale, fromGregorian, fromGregorianValid, gregorianMonthLength, parseTimeM)

validateMonotonicAnchorsRule :: Value -> Either Text ()
validateMonotonicAnchorsRule (Object fields) = do
  requireExactKeys "monotonic anchors rule" ["payload", "fields"] fields
  _ <- requiredString "payload" fields
  names <- requiredStrings "fields" fields
  if length names == 2 && length (unique names) == 2
    then Right ()
    else Left "monotonic anchors rule must name two distinct fields"
validateMonotonicAnchorsRule _ = Left "monotonic anchors rule must be an object"

validateOrderedInstantsRule :: Value -> Either Text ()
validateOrderedInstantsRule value = do
  validateNamedFieldsRule "ordered instants rule" ["start", "end"] value
  case value of
    Object fields -> do
      start <- requiredString "start" fields
      end <- requiredString "end" fields
      if start == end then Left "ordered instants rule must name two distinct fields" else Right ()
    _ -> Left "ordered instants rule must be an object"

-- | @x-archive-ascending-items@: @paths@ names arrays of the payload whose
-- items MUST strictly ascend: all integers in numeric order, or all strings
-- in code point order, and no item twice.
validateAscendingItemsRule :: Value -> Either Text ()
validateAscendingItemsRule (Object fields) = do
  requireExactKeys "ascending items rule" ["paths"] fields
  paths <- maybe (Left "ascending items rule requires paths") (payloadPaths "ascending items rule paths")
    (KeyMap.lookup "paths" fields)
  if null paths then Left "ascending items rule must name at least one path" else Right ()
validateAscendingItemsRule _ = Left "ascending items rule must be an object"

data Ordered = OrderedInteger Integer | OrderedText Text
  deriving (Eq, Ord)

validateAscendingItemsInstance :: KeyMap.KeyMap Value -> KeyMap.KeyMap Value -> Either Text ()
validateAscendingItemsInstance rule instanceValue = do
  paths <- maybe (Left "ascending items rule requires paths") (payloadPaths "ascending items rule paths")
    (KeyMap.lookup "paths" rule)
  payload <- requiredObject "payload" instanceValue
  mapM_ (checkPath payload) paths
  where
    checkPath payload path = do
      holders <- pathHolders path payload
      let named = Text.intercalate "." path
      mapM_ (\fields -> case KeyMap.lookup (Key.fromText (last path)) fields of
        Nothing -> Right ()
        Just (Array values) -> do
          items <- mapM (orderedItem named) (toList values)
          let sameKind = all isInteger items || not (any isInteger items)
          if sameKind && and (zipWith (<) items (drop 1 items))
            then Right ()
            else Left (named <> " must be distinct items in ascending order")
        Just _ -> Left (named <> " must be an array")) holders
    orderedItem _ (Number value) | Right integer <- (floatingOrInteger value :: Either Double Integer) =
      Right (OrderedInteger integer)
    orderedItem _ (String text) = Right (OrderedText text)
    orderedItem named _ = Left (named <> " must contain integers or strings")
    isInteger (OrderedInteger _) = True
    isInteger _ = False

-- | @x-archive-subset@: on every object the @payload@ path reaches, the
-- array @items@, when present, holds only values of its array @of@ (a beat
-- layer's downbeats among its beats).
validateSubsetRule :: Value -> Either Text ()
validateSubsetRule (Object fields) = do
  requireExactKeys "subset rule" ["payload", "items", "of"] fields
  _ <- requiredPath "payload" fields
  items <- requiredString "items" fields
  others <- requiredString "of" fields
  if items == others then Left "subset rule must name two different arrays" else Right ()
validateSubsetRule _ = Left "subset rule must be an object"

validateSubsetInstance :: KeyMap.KeyMap Value -> KeyMap.KeyMap Value -> Either Text ()
validateSubsetInstance rule instanceValue = do
  path <- requiredPath "payload" rule
  items <- requiredString "items" rule
  others <- requiredString "of" rule
  payload <- requiredObject "payload" instanceValue
  holders <- pathHolders (path <> [items]) payload
  mapM_ (\fields -> case (KeyMap.lookup (Key.fromText items) fields, KeyMap.lookup (Key.fromText others) fields) of
    (Nothing, _) -> Right ()
    (Just (Array chosen), Just (Array pool))
      | all (`elem` toList pool) (toList chosen) -> Right ()
    _ -> Left (items <> " must be among " <> others)) holders

-- | @x-archive-document-order@: the elements of the payload array @payload@
-- that have a @locator@ object never go backwards in the document: compared
-- by the locator's integer @line@ and then by the first of the @within@
-- fields it has (0 when none), each is at or after the one before.
validateDocumentOrderRule :: Value -> Either Text ()
validateDocumentOrderRule (Object fields) = do
  requireExactKeys "document order rule" ["payload", "locator", "line", "within"] fields
  mapM_ (`requiredString` fields) ["payload", "locator", "line"]
  within <- requiredStrings "within" fields
  if null within || length within /= length (unique within)
    then Left "document order rule must name distinct within fields"
    else Right ()
validateDocumentOrderRule _ = Left "document order rule must be an object"

validateDocumentOrderInstance :: KeyMap.KeyMap Value -> KeyMap.KeyMap Value -> Either Text ()
validateDocumentOrderInstance rule instanceValue = do
  payloadName <- requiredString "payload" rule
  locatorName <- requiredString "locator" rule
  lineName <- requiredString "line" rule
  within <- requiredStrings "within" rule
  payload <- requiredObject "payload" instanceValue
  elements <- case KeyMap.lookup (Key.fromText payloadName) payload of
    Just (Array values) -> Right (toList values)
    _ -> Left (payloadName <> " must be an array")
  positions <- concat <$> mapM (\element -> case element of
    Object fields -> case KeyMap.lookup (Key.fromText locatorName) fields of
      Nothing -> Right []
      Just (Object locator) -> do
        line <- requiredInteger lineName locator
        inner <- case [name | name <- within, KeyMap.member (Key.fromText name) locator] of
          name : _ -> requiredInteger name locator
          [] -> Right 0
        pure [(line, inner)]
      Just _ -> Left (locatorName <> " must be an object")
    _ -> Left (payloadName <> " must contain objects")) elements
  if and (zipWith (<=) positions (drop 1 positions))
    then Right ()
    else Left (payloadName <> " located in the document must not go backwards")

-- | @x-archive-nested-ranges@: each element of the payload array @payload@
-- is a range of positions in the payload array @of@ with integer @first@
-- and @last@: @first <= last@, and @last@ is a position of @of@. Ranges are
-- distinct and in order of first position, the longer of two with the same
-- first position first, and two ranges nest or are apart, never cross.
validateNestedRangesRule :: Value -> Either Text ()
validateNestedRangesRule value = do
  validateNamedFieldsRule "nested ranges rule" ["payload", "first", "last", "of"] value
  case value of
    Object fields -> do
      first <- requiredString "first" fields
      lastName <- requiredString "last" fields
      if first == lastName then Left "nested ranges rule must name two bound fields" else Right ()
    _ -> Left "nested ranges rule must be an object"

validateNestedRangesInstance :: KeyMap.KeyMap Value -> KeyMap.KeyMap Value -> Either Text ()
validateNestedRangesInstance rule instanceValue = do
  payloadName <- requiredString "payload" rule
  firstName <- requiredString "first" rule
  lastName <- requiredString "last" rule
  ofName <- requiredString "of" rule
  payload <- requiredObject "payload" instanceValue
  size <- case KeyMap.lookup (Key.fromText ofName) payload of
    Just (Array values) -> Right (fromIntegral (length values) :: Integer)
    _ -> Left (ofName <> " must be an array")
  ranges <- case KeyMap.lookup (Key.fromText payloadName) payload of
    Nothing -> Right []
    Just (Array values) -> mapM (\element -> case element of
      Object fields -> do
        first <- requiredInteger firstName fields
        lastPosition <- requiredInteger lastName fields
        if 0 <= first && first <= lastPosition && lastPosition < size
          then Right (first, lastPosition)
          else Left (payloadName <> " must be ranges of positions in " <> ofName)
      _ -> Left (payloadName <> " must contain objects")) (toList values)
    Just _ -> Left (payloadName <> " must be an array")
  let keys = [(first, negate lastPosition) | (first, lastPosition) <- ranges]
  if keys == sort keys && length keys == length (unique keys)
    then Right ()
    else Left (payloadName <> " must be distinct and in order of first position, outer before inner")
  let crossing (a, b) (c, d) = (c > a && c <= b && d > b) || (a > c && a <= d && b > d)
  if or [crossing x y | (index, x) <- zip [0 :: Int ..] ranges, y <- drop (index + 1) ranges]
    then Left (payloadName <> " must nest or be apart, never cross")
    else Right ()

-- | @x-archive-partial-dates@: each payload field in @fields@, when present,
-- begins with a calendar date written @YYYY@, @YYYY-MM@, or @YYYY-MM-DD@ (a
-- year from 1, a real month, a real day of it); what may follow is the
-- schema's pattern's. With @start@ and @end@, both among @fields@, the
-- last day @end@ can mean MUST NOT be before the first day @start@ can mean.
validatePartialDatesRule :: Value -> Either Text ()
validatePartialDatesRule (Object fields) = do
  let ordered = KeyMap.member "start" fields || KeyMap.member "end" fields
  requireExactKeys "partial dates rule" (["fields"] <> (if ordered then ["start", "end"] else [])) fields
  names <- requiredStrings "fields" fields
  if null names || length names /= length (unique names)
    then Left "partial dates rule must name distinct fields"
    else Right ()
  if ordered
    then do
      start <- requiredString "start" fields
      end <- requiredString "end" fields
      if start /= end && start `elem` names && end `elem` names
        then Right ()
        else Left "partial dates rule must order two of its fields"
    else Right ()
validatePartialDatesRule _ = Left "partial dates rule must be an object"

validatePartialDatesInstance :: KeyMap.KeyMap Value -> KeyMap.KeyMap Value -> Either Text ()
validatePartialDatesInstance rule instanceValue = do
  names <- requiredStrings "fields" rule
  payload <- requiredObject "payload" instanceValue
  let dateOf name = case KeyMap.lookup (Key.fromText name) payload of
        Nothing -> Right Nothing
        Just (String text) -> maybe (Left (name <> " must begin with a calendar date")) (Right . Just)
          (partialDate (Text.takeWhile (/= 'T') text))
        Just _ -> Left (name <> " must be text")
  mapM_ dateOf names
  case (KeyMap.lookup "start" rule, KeyMap.lookup "end" rule) of
    (Just (String start), Just (String end)) -> do
      from <- dateOf start
      to <- dateOf end
      case (from, to) of
        (Just (earliest, _), Just (_, latest)) | latest < earliest ->
          Left (end <> " must not be before " <> start)
        _ -> Right ()
    _ -> Right ()

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

validateMonotonicAnchorsInstance :: KeyMap.KeyMap Value -> KeyMap.KeyMap Value -> Either Text ()
validateMonotonicAnchorsInstance rule instanceValue = do
  payloadName <- requiredString "payload" rule
  names <- requiredStrings "fields" rule
  payload <- requiredObject "payload" instanceValue
  anchors <- case KeyMap.lookup (Key.fromText payloadName) payload of
    Just (Array values) | not (null values) -> Right (toList values)
    _ -> Left "monotonic anchors payload must be a nonempty array"
  rows <- mapM (anchorRow names) anchors
  let increasing column = and (zipWith (<) (map (!! column) rows) (drop 1 (map (!! column) rows)))
  if all increasing [0 .. length names - 1]
    then Right ()
    else Left "anchors must strictly increase in every named field"
  where
    anchorRow names (Object anchor) = mapM (`requiredInteger` anchor) names
    anchorRow _ _ = Left "anchor must be an object"

-- | Both named payload fields are RFC 3339 instants in UTC with a @Z@
-- suffix, and the second is not earlier than the first.
validateOrderedInstantsInstance :: KeyMap.KeyMap Value -> KeyMap.KeyMap Value -> Either Text ()
validateOrderedInstantsInstance rule instanceValue = do
  startName <- requiredString "start" rule
  endName <- requiredString "end" rule
  payload <- requiredObject "payload" instanceValue
  start <- utcInstant startName payload
  end <- utcInstant endName payload
  if end >= start
    then Right ()
    else Left (endName <> " must not be before " <> startName)
  where
    utcInstant name payload = do
      value <- requiredString name payload
      case parseUtcInstant value of
        Just instant -> Right instant
        Nothing -> Left (name <> " must be an RFC 3339 instant in UTC with a Z suffix")

parseUtcInstant :: Text -> Maybe UTCTime
parseUtcInstant value
  | Text.isSuffixOf "Z" value && Text.isInfixOf "T" value =
      parseTimeM False defaultTimeLocale "%Y-%m-%dT%H:%M:%S%QZ" (Text.unpack value)
  | otherwise = Nothing
