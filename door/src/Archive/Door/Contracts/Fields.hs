{-# LANGUAGE OverloadedStrings #-}

-- | Strict readers for the JSON a contract bundle is made of.
--
-- A bundle is specification-owned, so its fields are read strictly and a
-- refusal names the field. Every part of "Archive.Door.Contracts" reads
-- through these, so the refusal text, which the suites compare, has one
-- spelling. 'requiredValue' and 'requiredText' are Door's shared accessors;
-- 'requiredObject' stays here because Contracts refuses a non-object as "must
-- be a JSON object", not the shared "must be an object".
module Archive.Door.Contracts.Fields
  ( requireExactKeys
  , objectValue
  , requiredObject
  , requiredValue
  , requiredText
  , requiredBool
  , requiredStrings
  , requiredInteger
  , validateNamedFieldsRule
  , expect
  , unique
  ) where

import Archive.Door.Json.Access (requiredText, requiredValue)
import Data.Aeson (Value (..))
import Data.Aeson.Key qualified as Key
import Data.Aeson.KeyMap qualified as KeyMap
import Data.Foldable (toList)
import Data.List (sort)
import Data.Scientific (floatingOrInteger)
import Data.Text (Text)
import Data.Text qualified as Text

requireExactKeys :: Text -> [Text] -> KeyMap.KeyMap Value -> Either Text ()
requireExactKeys label expected fields =
  let actual = sort (map (Key.toText . fst) (KeyMap.toList fields))
      wanted = sort expected
  in if actual == wanted
       then Right ()
       else Left (label <> " must contain exactly " <> Text.intercalate ", " wanted)

objectValue :: Text -> Value -> Either Text (KeyMap.KeyMap Value)
objectValue _label (Object fields) = Right fields
objectValue label _ = Left (label <> " must be a JSON object")

requiredObject :: Text -> KeyMap.KeyMap Value -> Either Text (KeyMap.KeyMap Value)
requiredObject name fields = maybe (Left (name <> " is required")) (objectValue name)
  (KeyMap.lookup (Key.fromText name) fields)

requiredBool :: Text -> KeyMap.KeyMap Value -> Either Text Bool
requiredBool name fields = case KeyMap.lookup (Key.fromText name) fields of
  Just (Bool value) -> Right value
  Just _ -> Left (name <> " must be a boolean")
  Nothing -> Left (name <> " is required")

expect :: Text -> Text -> Text -> Either Text ()
expect label wanted actual
  | actual == wanted = Right ()
  | otherwise = Left (label <> " must be " <> wanted)

requiredStrings :: Text -> KeyMap.KeyMap Value -> Either Text [Text]
requiredStrings name fields = case KeyMap.lookup (Key.fromText name) fields of
  Just (Array values) -> mapM one (toList values)
  _ -> Left (name <> " must be an array of strings")
  where
    one (String value) | not (Text.null value) = Right value
    one _ = Left (name <> " must contain non-empty strings")

requiredInteger :: Text -> KeyMap.KeyMap Value -> Either Text Integer
requiredInteger name fields = case KeyMap.lookup (Key.fromText name) fields of
  Just (Number value) -> case floatingOrInteger value :: Either Double Integer of
    Right integer -> Right integer
    Left _ -> Left (name <> " must be an integer")
  _ -> Left (name <> " must be an integer")

validateNamedFieldsRule :: Text -> [Text] -> Value -> Either Text ()
validateNamedFieldsRule label names (Object fields) = do
  requireExactKeys label names fields
  mapM_ (`requiredText` fields) names
validateNamedFieldsRule label _ _ = Left (label <> " must be an object")

unique :: Eq a => [a] -> [a]
unique [] = []
unique (value : values) = value : unique (filter (/= value) values)
