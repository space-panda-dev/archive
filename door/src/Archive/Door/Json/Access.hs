{-# LANGUAGE OverloadedStrings #-}

-- | Required fields of a decoded JSON object, refused with the field's name.
--
-- Records, proposals, layouts and journals all read named fields the same
-- way, and the refusal text is part of Door's interface: the Python suites
-- and door-test compare it. Keeping one copy of each accessor keeps that
-- text from drifting between modules. Two spellings of a missing or wrong
-- string exist and both are kept: 'requiredText' tells an empty string from
-- a wrong type, 'requiredNonEmptyText' refuses both with one message.
--
-- The @...Field@ accessors refuse with a typed 'FieldRefusal', for a module
-- that keeps its refusals as data until its own edge (a proposal's
-- 'Archive.Door.ProposalProblem.ProposalProblem', say); the @required...@
-- accessors are the same checks with the refusal already written as text.
-- 'fieldRefusalText' is the one place either is spelled.
module Archive.Door.Json.Access
  ( FieldRefusal (..)
  , JsonType (..)
  , fieldRefusalText
  , arrayField
  , numberField
  , textField
  , hasExactKeys
  , requiredArray
  , requiredNonEmptyText
  , requiredObject
  , requiredText
  , requiredValue
  ) where

import Data.Aeson (Value (Array, Number, Object, String))
import qualified Data.Aeson.Key as Key
import qualified Data.Aeson.KeyMap as KeyMap
import Data.Bifunctor (first)
import Data.Foldable (toList)
import Data.List (sort)
import Data.Text (Text)
import qualified Data.Text as Text

-- | Why a named field was refused.
data FieldRefusal
  = FieldMissing Text
  | FieldEmpty Text
    -- ^ a string field held the empty string
  | FieldNotA JsonType Text
    -- ^ the field held a value of another type
  deriving (Eq, Show)

-- | The type a field was required to have, as its refusal names it.
data JsonType
  = JsonString
  | JsonNonEmptyString
  | JsonArray
  | JsonObject
  | JsonNumber
  deriving (Eq, Show)

fieldRefusalText :: FieldRefusal -> Text
fieldRefusalText refusal = case refusal of
  FieldMissing name -> name <> " is required"
  FieldEmpty name -> name <> " must not be empty"
  FieldNotA expected name -> name <> " must be " <> article expected
  where
    article expected = case expected of
      JsonString -> "a string"
      JsonNonEmptyString -> "a non-empty string"
      JsonArray -> "an array"
      JsonObject -> "an object"
      JsonNumber -> "a number"

-- | Whether an object has exactly these keys, in any order. Each caller
-- words its own refusal, so this answers only yes or no.
hasExactKeys :: [Text] -> KeyMap.KeyMap Value -> Bool
hasExactKeys expected fields = sort (map Key.toText (KeyMap.keys fields)) == sort expected

-- | A non-empty string field; an empty string and a non-string are refused
-- separately.
textField :: Text -> KeyMap.KeyMap Value -> Either FieldRefusal Text
textField name fields = case KeyMap.lookup (Key.fromText name) fields of
  Nothing -> Left (FieldMissing name)
  Just (String value)
    | Text.null value -> Left (FieldEmpty name)
    | otherwise -> Right value
  Just _ -> Left (FieldNotA JsonString name)

-- | An array field's items.
arrayField :: Text -> KeyMap.KeyMap Value -> Either FieldRefusal [Value]
arrayField name fields = case KeyMap.lookup (Key.fromText name) fields of
  Nothing -> Left (FieldMissing name)
  Just (Array values) -> Right (toList values)
  Just _ -> Left (FieldNotA JsonArray name)

-- | A number field, still as its JSON value, since callers bound it
-- differently (a size, an index).
numberField :: Text -> KeyMap.KeyMap Value -> Either FieldRefusal Value
numberField name fields = case KeyMap.lookup (Key.fromText name) fields of
  Nothing -> Left (FieldMissing name)
  Just value@(Number _) -> Right value
  Just _ -> Left (FieldNotA JsonNumber name)

-- | A field of any type.
requiredValue :: Text -> KeyMap.KeyMap Value -> Either Text Value
requiredValue name fields = case KeyMap.lookup (Key.fromText name) fields of
  Nothing -> Left (fieldRefusalText (FieldMissing name))
  Just value -> Right value

-- | 'textField', refused as text.
requiredText :: Text -> KeyMap.KeyMap Value -> Either Text Text
requiredText name = first fieldRefusalText . textField name

-- | A non-empty string field, with one refusal for empty and non-string.
requiredNonEmptyText :: Text -> KeyMap.KeyMap Value -> Either Text Text
requiredNonEmptyText name fields = first fieldRefusalText $ case KeyMap.lookup (Key.fromText name) fields of
  Just (String value) | not (Text.null value) -> Right value
  Just _ -> Left (FieldNotA JsonNonEmptyString name)
  Nothing -> Left (FieldMissing name)

-- | 'arrayField', refused as text.
requiredArray :: Text -> KeyMap.KeyMap Value -> Either Text [Value]
requiredArray name = first fieldRefusalText . arrayField name

-- | An object field. Contracts refuses a non-object as "must be a JSON
-- object" and keeps its own accessor for that text.
requiredObject :: Text -> KeyMap.KeyMap Value -> Either Text (KeyMap.KeyMap Value)
requiredObject name fields = first fieldRefusalText $ case KeyMap.lookup (Key.fromText name) fields of
  Just (Object value) -> Right value
  Just _ -> Left (FieldNotA JsonObject name)
  Nothing -> Left (FieldMissing name)
