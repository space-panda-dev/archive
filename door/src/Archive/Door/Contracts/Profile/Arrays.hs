{-# LANGUAGE OverloadedStrings #-}

-- | The profile rules over a record's payload arrays: ascending items,
-- subsets, document order, and nested ranges.
module Archive.Door.Contracts.Profile.Arrays
  ( ascendingItemsRule
  , checkAscendingItems
  , Subset (..)
  , subsetRule
  , checkSubset
  , DocumentOrder (..)
  , documentOrderRule
  , checkDocumentOrder
  , NestedRanges (..)
  , nestedRangesRule
  , checkNestedRanges
  ) where

import Archive.Door.Contracts.Fields
  ( requireExactKeys
  , requiredInteger
  , requiredObject
  , requiredText
  , requiredStrings
  , unique
  , validateNamedFieldsRule
  )
import Archive.Door.Contracts.Profile.Paths (pathHolders, pathValues, payloadPaths, requiredPath)
import Data.Aeson (Value (..))
import Data.Aeson.Key qualified as Key
import Data.Aeson.KeyMap qualified as KeyMap
import Data.Foldable (toList)
import Data.List (sort)
import Data.Scientific (floatingOrInteger)
import Data.Text (Text)
import Data.Text qualified as Text

-- | @x-archive-ascending-items@: @paths@ names arrays of the payload whose
-- items MUST strictly ascend: all integers in numeric order, or all strings
-- in code point order, and no item twice. The rule is its paths.
ascendingItemsRule :: Value -> Either Text [[Text]]
ascendingItemsRule (Object fields) = do
  requireExactKeys "ascending items rule" ["paths"] fields
  paths <- maybe (Left "ascending items rule requires paths") (payloadPaths "ascending items rule paths")
    (KeyMap.lookup "paths" fields)
  if null paths then Left "ascending items rule must name at least one path" else Right paths
ascendingItemsRule _ = Left "ascending items rule must be an object"

data Ordered = OrderedInteger Integer | OrderedText Text
  deriving (Eq, Ord)

checkAscendingItems :: [[Text]] -> KeyMap.KeyMap Value -> Either Text ()
checkAscendingItems paths instanceValue = do
  payload <- requiredObject "payload" instanceValue
  mapM_ (checkPath payload) paths
  where
    checkPath payload path = do
      reached <- pathValues path payload
      let named = Text.intercalate "." path
      mapM_ (\found -> case found of
        Nothing -> Right ()
        Just (Array values) -> do
          items <- mapM (orderedItem named) (toList values)
          let sameKind = all isInteger items || not (any isInteger items)
          if sameKind && and (zipWith (<) items (drop 1 items))
            then Right ()
            else Left (named <> " must be distinct items in ascending order")
        Just _ -> Left (named <> " must be an array")) reached
    orderedItem _ (Number value) | Right integer <- (floatingOrInteger value :: Either Double Integer) =
      Right (OrderedInteger integer)
    orderedItem _ (String text) = Right (OrderedText text)
    orderedItem named _ = Left (named <> " must contain integers or strings")
    isInteger (OrderedInteger _) = True
    isInteger _ = False

-- | @x-archive-subset@: on every object the @payload@ path reaches, the
-- array @items@, when present, holds only values of its array @of@ (a beat
-- layer's downbeats among its beats).
data Subset = Subset
  { subsetPath :: [Text]
  , subsetItems :: Text
  , subsetOf :: Text
  } deriving (Eq, Show)

subsetRule :: Value -> Either Text Subset
subsetRule (Object fields) = do
  requireExactKeys "subset rule" ["payload", "items", "of"] fields
  path <- requiredPath "payload" fields
  items <- requiredText "items" fields
  others <- requiredText "of" fields
  if items == others then Left "subset rule must name two different arrays" else Right ()
  pure (Subset path items others)
subsetRule _ = Left "subset rule must be an object"

checkSubset :: Subset -> KeyMap.KeyMap Value -> Either Text ()
checkSubset rule instanceValue = do
  payload <- requiredObject "payload" instanceValue
  holders <- pathHolders (subsetPath rule <> [items]) payload
  mapM_ (\fields -> case (KeyMap.lookup (Key.fromText items) fields, KeyMap.lookup (Key.fromText others) fields) of
    (Nothing, _) -> Right ()
    (Just (Array chosen), Just (Array pool))
      | all (`elem` toList pool) (toList chosen) -> Right ()
    _ -> Left (items <> " must be among " <> others)) holders
  where
    items = subsetItems rule
    others = subsetOf rule

-- | @x-archive-document-order@: the elements of the payload array @payload@
-- that have a @locator@ object never go backwards in the document: compared
-- by the locator's integer @line@ and then by the first of the @within@
-- fields it has (0 when none), each is at or after the one before.
data DocumentOrder = DocumentOrder
  { documentOrderPayload :: Text
  , documentOrderLocator :: Text
  , documentOrderLine :: Text
  , documentOrderWithin :: [Text]
  } deriving (Eq, Show)

documentOrderRule :: Value -> Either Text DocumentOrder
documentOrderRule (Object fields) = do
  requireExactKeys "document order rule" ["payload", "locator", "line", "within"] fields
  payload <- requiredText "payload" fields
  locator <- requiredText "locator" fields
  line <- requiredText "line" fields
  within <- requiredStrings "within" fields
  if null within || length within /= length (unique within)
    then Left "document order rule must name distinct within fields"
    else Right (DocumentOrder payload locator line within)
documentOrderRule _ = Left "document order rule must be an object"

checkDocumentOrder :: DocumentOrder -> KeyMap.KeyMap Value -> Either Text ()
checkDocumentOrder rule instanceValue = do
  payload <- requiredObject "payload" instanceValue
  elements <- case KeyMap.lookup (Key.fromText payloadName) payload of
    Just (Array values) -> Right (toList values)
    _ -> Left (payloadName <> " must be an array")
  positions <- concat <$> mapM (\element -> case element of
    Object fields -> case KeyMap.lookup (Key.fromText locatorName) fields of
      Nothing -> Right []
      Just (Object locator) -> do
        line <- requiredInteger (documentOrderLine rule) locator
        inner <- case [name | name <- documentOrderWithin rule, KeyMap.member (Key.fromText name) locator] of
          name : _ -> requiredInteger name locator
          [] -> Right 0
        pure [(line, inner)]
      Just _ -> Left (locatorName <> " must be an object")
    _ -> Left (payloadName <> " must contain objects")) elements
  if and (zipWith (<=) positions (drop 1 positions))
    then Right ()
    else Left (payloadName <> " located in the document must not go backwards")
  where
    payloadName = documentOrderPayload rule
    locatorName = documentOrderLocator rule

-- | @x-archive-nested-ranges@: each element of the payload array @payload@
-- is a range of positions in the payload array @of@ with integer @first@
-- and @last@: @first <= last@, and @last@ is a position of @of@. Ranges are
-- distinct and in order of first position, the longer of two with the same
-- first position first, and two ranges nest or are apart, never cross.
data NestedRanges = NestedRanges
  { nestedRangesPayload :: Text
  , nestedRangesFirst :: Text
  , nestedRangesLast :: Text
  , nestedRangesOf :: Text
  } deriving (Eq, Show)

nestedRangesRule :: Value -> Either Text NestedRanges
nestedRangesRule value = do
  validateNamedFieldsRule "nested ranges rule" ["payload", "first", "last", "of"] value
  case value of
    Object fields -> do
      payload <- requiredText "payload" fields
      first <- requiredText "first" fields
      lastName <- requiredText "last" fields
      ofName <- requiredText "of" fields
      if first == lastName then Left "nested ranges rule must name two bound fields" else Right ()
      pure (NestedRanges payload first lastName ofName)
    _ -> Left "nested ranges rule must be an object"

checkNestedRanges :: NestedRanges -> KeyMap.KeyMap Value -> Either Text ()
checkNestedRanges rule instanceValue = do
  payload <- requiredObject "payload" instanceValue
  size <- case KeyMap.lookup (Key.fromText ofName) payload of
    Just (Array values) -> Right (fromIntegral (length values) :: Integer)
    _ -> Left (ofName <> " must be an array")
  ranges <- case KeyMap.lookup (Key.fromText payloadName) payload of
    Nothing -> Right []
    Just (Array values) -> mapM (\element -> case element of
      Object fields -> do
        first <- requiredInteger (nestedRangesFirst rule) fields
        lastPosition <- requiredInteger (nestedRangesLast rule) fields
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
  where
    payloadName = nestedRangesPayload rule
    ofName = nestedRangesOf rule
