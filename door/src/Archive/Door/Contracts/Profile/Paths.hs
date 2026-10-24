{-# LANGUAGE OverloadedStrings #-}

-- | Payload paths (specification 0.0.9), as profile rules name and follow
-- them.
module Archive.Door.Contracts.Profile.Paths
  ( payloadPaths
  , requiredPath
  , descend
  , pathHolders
  , pathValues
  , pathIntegers
  ) where

import Archive.Door.Contracts.Fields (FieldError (..), unique)
import Archive.Door.Contracts.Profile.Error (RuleError (..), RuleViolation (..))
import Data.Aeson (Value (..))
import Data.Aeson.Key qualified as Key
import Data.Aeson.KeyMap qualified as KeyMap
import Data.Foldable (toList)
import Data.Scientific (floatingOrInteger)
import Data.Text (Text)
import Data.Text qualified as Text

-- Paths (specification 0.0.9). A path is a nonempty array of field names
-- read from the payload; every array met on the way is entered element by
-- element, so ["spans", "readings", "tones"] reaches the tones of every
-- reading of every span. A missing field reaches nothing: whether it must
-- be present is the schema's question, not the path's.

payloadPath :: Text -> Value -> Either RuleError [Text]
payloadPath label (Array values) = case mapM name (toList values) of
  Right names | not (null names) -> Right names
  _ -> Left (PathNotFieldNames label)
  where
    name (String field) | not (Text.null field) = Right field
    name _ = Left (PathEmptyName label)
payloadPath label _ = Left (PathNotFieldNames label)

payloadPaths :: Text -> Value -> Either RuleError [[Text]]
payloadPaths label (Array values) = do
  paths <- mapM (payloadPath label) (toList values)
  if length paths /= length (unique paths)
    then Left (PathRepeated label)
    else Right paths
payloadPaths label _ = Left (PathsNotArray label)

requiredPath :: Text -> KeyMap.KeyMap Value -> Either RuleError [Text]
requiredPath name fields = case KeyMap.lookup (Key.fromText name) fields of
  Just value -> payloadPath name value
  Nothing -> Left (RuleField (FieldMissing name))

-- | The values a path's fields lead to, entering arrays on the way but not
-- the last value itself.
descend :: [Text] -> [Value] -> [Value]
descend [] values = values
descend (name : rest) values = descend rest (concatMap step values)
  where
    step (Object fields) = case KeyMap.lookup (Key.fromText name) fields of
      Just (Array items) | not (null rest) -> toList items
      Just value -> [value]
      Nothing -> []
    step _ = []

-- | The objects that hold a path's last field (or would): every object
-- reached by the path without its last name, entering arrays.
pathHolders :: [Text] -> KeyMap.KeyMap Value -> Either RuleViolation [KeyMap.KeyMap Value]
pathHolders path payload = mapM holder (enter (descend (take (length path - 1) path) [Object payload]))
  where
    enter = concatMap (\value -> case value of
      Array items -> toList items
      _ -> [value])
    holder (Object fields) = Right fields
    holder _ = Left (PathNotThroughObjects path)

-- | What a path's last field holds on each object that holds it (or
-- would). A path is nonempty once parsed; an empty one reaches nothing.
pathValues :: [Text] -> KeyMap.KeyMap Value -> Either RuleViolation [Maybe Value]
pathValues path payload = case reverse path of
  [] -> Right []
  final : _ -> map (KeyMap.lookup (Key.fromText final)) <$> pathHolders path payload

-- | The integers a path's last field holds, on every object it reaches: the
-- field's value, or each item of it when it is an array.
pathIntegers :: [Text] -> KeyMap.KeyMap Value -> Either RuleViolation [Integer]
pathIntegers path payload = do
  values <- pathValues path payload
  concat <$> mapM (\found -> case found of
    Nothing -> Right []
    Just (Array items) -> mapM integerAt (toList items)
    Just value -> (: []) <$> integerAt value) values
  where
    integerAt (Number value) | Right integer <- (floatingOrInteger value :: Either Double Integer) = Right integer
    integerAt _ = Left (PathNotIntegers path)
