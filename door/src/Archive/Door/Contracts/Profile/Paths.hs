{-# LANGUAGE OverloadedStrings #-}

-- | Payload paths (specification 0.0.9), as profile rules name and follow
-- them.
module Archive.Door.Contracts.Profile.Paths
  ( payloadPaths
  , requiredPath
  , descend
  , pathHolders
  , pathIntegers
  ) where

import Archive.Door.Contracts.Fields (unique)
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

payloadPath :: Text -> Value -> Either Text [Text]
payloadPath label (Array values) = case mapM name (toList values) of
  Right names | not (null names) -> Right names
  _ -> Left (label <> " must be a nonempty array of field names")
  where
    name (String field) | not (Text.null field) = Right field
    name _ = Left (label <> " must contain non-empty field names")
payloadPath label _ = Left (label <> " must be a nonempty array of field names")

payloadPaths :: Text -> Value -> Either Text [[Text]]
payloadPaths label (Array values) = do
  paths <- mapM (payloadPath label) (toList values)
  if length paths /= length (unique paths)
    then Left (label <> " must not repeat a path")
    else Right paths
payloadPaths label _ = Left (label <> " must be an array of paths")

requiredPath :: Text -> KeyMap.KeyMap Value -> Either Text [Text]
requiredPath name fields = case KeyMap.lookup (Key.fromText name) fields of
  Just value -> payloadPath name value
  Nothing -> Left (name <> " is required")

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
pathHolders :: [Text] -> KeyMap.KeyMap Value -> Either Text [KeyMap.KeyMap Value]
pathHolders path payload = mapM holder (enter (descend (init path) [Object payload]))
  where
    enter = concatMap (\value -> case value of
      Array items -> toList items
      _ -> [value])
    holder (Object fields) = Right fields
    holder _ = Left (Text.intercalate "." path <> " must be reached through objects")

-- | The integers a path's last field holds, on every object it reaches: the
-- field's value, or each item of it when it is an array.
pathIntegers :: [Text] -> KeyMap.KeyMap Value -> Either Text [Integer]
pathIntegers path payload = do
  holders <- pathHolders path payload
  concat <$> mapM (\fields -> case KeyMap.lookup (Key.fromText (last path)) fields of
    Nothing -> Right []
    Just (Array values) -> mapM integerAt (toList values)
    Just value -> (: []) <$> integerAt value) holders
  where
    integerAt (Number value) | Right integer <- (floatingOrInteger value :: Either Double Integer) = Right integer
    integerAt _ = Left (Text.intercalate "." path <> " must hold integers")
