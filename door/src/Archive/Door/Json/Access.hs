{-# LANGUAGE OverloadedStrings #-}

-- | Required fields of a decoded JSON object, refused with the field's name.
--
-- Records, proposals, layouts and journals all read named fields the same
-- way, and the refusal text is part of Door's interface: the Python suites
-- and door-test compare it. Keeping one copy of each accessor keeps that
-- text from drifting between modules. Two spellings of a missing or wrong
-- string exist and both are kept: 'requiredText' tells an empty string from
-- a wrong type, 'requiredNonEmptyText' refuses both with one message.
module Archive.Door.Json.Access
  ( requiredArray
  , requiredNonEmptyText
  , requiredObject
  , requiredText
  , requiredValue
  ) where

import Data.Aeson (Value (Array, Object, String))
import qualified Data.Aeson.Key as Key
import qualified Data.Aeson.KeyMap as KeyMap
import Data.Foldable (toList)
import Data.Text (Text)
import qualified Data.Text as Text

-- | A field of any type.
requiredValue :: Text -> KeyMap.KeyMap Value -> Either Text Value
requiredValue name fields = case KeyMap.lookup (Key.fromText name) fields of
  Nothing -> Left (name <> " is required")
  Just value -> Right value

-- | A non-empty string field; an empty string and a non-string are refused
-- separately.
requiredText :: Text -> KeyMap.KeyMap Value -> Either Text Text
requiredText name fields = case KeyMap.lookup (Key.fromText name) fields of
  Nothing -> Left (name <> " is required")
  Just (String value)
    | Text.null value -> Left (name <> " must not be empty")
    | otherwise -> Right value
  Just _ -> Left (name <> " must be a string")

-- | A non-empty string field, with one refusal for empty and non-string.
requiredNonEmptyText :: Text -> KeyMap.KeyMap Value -> Either Text Text
requiredNonEmptyText name fields = case KeyMap.lookup (Key.fromText name) fields of
  Just (String value) | not (Text.null value) -> Right value
  Just _ -> Left (name <> " must be a non-empty string")
  Nothing -> Left (name <> " is required")

-- | An array field's items.
requiredArray :: Text -> KeyMap.KeyMap Value -> Either Text [Value]
requiredArray name fields = case KeyMap.lookup (Key.fromText name) fields of
  Nothing -> Left (name <> " is required")
  Just (Array values) -> Right (toList values)
  Just _ -> Left (name <> " must be an array")

-- | An object field. Contracts refuses a non-object as "must be a JSON
-- object" and keeps its own accessor for that text.
requiredObject :: Text -> KeyMap.KeyMap Value -> Either Text (KeyMap.KeyMap Value)
requiredObject name fields = case KeyMap.lookup (Key.fromText name) fields of
  Just (Object value) -> Right value
  Just _ -> Left (name <> " must be an object")
  Nothing -> Left (name <> " is required")
