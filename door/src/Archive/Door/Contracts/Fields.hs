{-# LANGUAGE OverloadedStrings #-}

-- | Strict readers for the JSON a contract bundle is made of.
--
-- A bundle is specification-owned, so its fields are read strictly and a
-- refusal names the field. Every part of "Archive.Door.Contracts" reads
-- through these, so the refusal text, which the suites compare, has one
-- spelling. Each part has its own error type; a reader returns whichever
-- the caller needs, through 'FromFieldError', so a field refusal carries the
-- field as data until 'renderFieldError' writes it at the edge.
--
-- 'requiredValue' and 'requiredText' are Door's shared accessors from
-- "Archive.Door.Json.Access", whose refusal text is shared with the rest of
-- Door on purpose and so is carried as that text. 'requiredObject' is
-- Contracts' own, because Contracts refuses a non-object as "must be a JSON
-- object", not the shared "must be an object".
module Archive.Door.Contracts.Fields
  ( FieldError (..)
  , FromFieldError (..)
  , renderFieldError
  , requireExactKeys
  , objectValue
  , requiredObject
  , requiredValue
  , requiredText
  , requiredBool
  , requiredStrings
  , requiredInteger
  , expect
  , unique
  ) where

import Archive.Door.Json.Access qualified as Access
import Data.Aeson (Value (..))
import Data.Aeson.Key qualified as Key
import Data.Aeson.KeyMap qualified as KeyMap
import Data.Bifunctor (first)
import Data.Foldable (toList)
import Data.List (sort)
import Data.Scientific (floatingOrInteger)
import Data.Text (Text)
import Data.Text qualified as Text

-- | A field that is missing or not what the bundle's format says.
data FieldError
  = -- | An object (named by the label) whose keys are not exactly these.
    NotExactKeys Text [Text]
  | NotJsonObject Text
  | FieldMissing Text
  | NotBoolean Text
  | NotStringArray Text
  | EmptyStringItem Text
  | NotInteger Text
  -- | A label, the value it must have.
  | NotExpected Text Text
  -- | A refusal from "Archive.Door.Json.Access", in its shared wording.
  | AccessRefusal Text
  deriving (Eq, Show)

-- | The error types of Contracts' parts each hold a 'FieldError'.
class FromFieldError e where
  fieldError :: FieldError -> e

instance FromFieldError FieldError where
  fieldError = id

renderFieldError :: FieldError -> Text
renderFieldError problem = case problem of
  NotExactKeys label expected -> label <> " must contain exactly " <> Text.intercalate ", " (sort expected)
  NotJsonObject label -> label <> " must be a JSON object"
  FieldMissing name -> name <> " is required"
  NotBoolean name -> name <> " must be a boolean"
  NotStringArray name -> name <> " must be an array of strings"
  EmptyStringItem name -> name <> " must contain non-empty strings"
  NotInteger name -> name <> " must be an integer"
  NotExpected label wanted -> label <> " must be " <> wanted
  AccessRefusal text -> text

refuse :: FromFieldError e => FieldError -> Either e a
refuse = Left . fieldError

requireExactKeys :: FromFieldError e => Text -> [Text] -> KeyMap.KeyMap Value -> Either e ()
requireExactKeys label expected fields
  | Access.hasExactKeys expected fields = Right ()
  | otherwise = refuse (NotExactKeys label expected)

objectValue :: FromFieldError e => Text -> Value -> Either e (KeyMap.KeyMap Value)
objectValue _label (Object fields) = Right fields
objectValue label _ = refuse (NotJsonObject label)

requiredObject :: FromFieldError e => Text -> KeyMap.KeyMap Value -> Either e (KeyMap.KeyMap Value)
requiredObject name fields = maybe (refuse (FieldMissing name)) (objectValue name)
  (KeyMap.lookup (Key.fromText name) fields)

requiredValue :: FromFieldError e => Text -> KeyMap.KeyMap Value -> Either e Value
requiredValue name fields = first (fieldError . AccessRefusal) (Access.requiredValue name fields)

requiredText :: FromFieldError e => Text -> KeyMap.KeyMap Value -> Either e Text
requiredText name fields = first (fieldError . AccessRefusal) (Access.requiredText name fields)

requiredBool :: FromFieldError e => Text -> KeyMap.KeyMap Value -> Either e Bool
requiredBool name fields = case KeyMap.lookup (Key.fromText name) fields of
  Just (Bool value) -> Right value
  Just _ -> refuse (NotBoolean name)
  Nothing -> refuse (FieldMissing name)

expect :: FromFieldError e => Text -> Text -> Text -> Either e ()
expect label wanted actual
  | actual == wanted = Right ()
  | otherwise = refuse (NotExpected label wanted)

requiredStrings :: FromFieldError e => Text -> KeyMap.KeyMap Value -> Either e [Text]
requiredStrings name fields = case KeyMap.lookup (Key.fromText name) fields of
  Just (Array values) -> mapM one (toList values)
  _ -> refuse (NotStringArray name)
  where
    one (String value) | not (Text.null value) = Right value
    one _ = refuse (EmptyStringItem name)

requiredInteger :: FromFieldError e => Text -> KeyMap.KeyMap Value -> Either e Integer
requiredInteger name fields = case KeyMap.lookup (Key.fromText name) fields of
  Just (Number value) -> case floatingOrInteger value :: Either Double Integer of
    Right integer -> Right integer
    Left _ -> refuse (NotInteger name)
  _ -> refuse (NotInteger name)

unique :: Eq a => [a] -> [a]
unique [] = []
unique (value : values) = value : unique (filter (/= value) values)
