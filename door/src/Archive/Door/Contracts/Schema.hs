{-# LANGUAGE OverloadedStrings #-}

-- | The closed subset of JSON Schema 2020-12 a contract may use, checked
-- keyword by keyword while a bundle loads.
module Archive.Door.Contracts.Schema
  ( walkSchema
  , rejectUnknownSchemaKeywords
  , validateRef
  , validateSchemaKeywordTypes
  , schemaPath
  ) where

import Archive.Door.Contracts.Fields (unique)
import Archive.Door.Contracts.Pattern (compilePattern)
import Archive.Door.Contracts.Pointer (resolvePointer)
import Archive.Door.Contracts.Profile (profileRuleNames)
import Archive.Door.SchemaObjectKeywords
  ( objectKeywordNames
  , objectKeywordSchemas
  , validateObjectKeywordTypes
  )
import Data.Aeson (Value (..))
import Data.Aeson.Key qualified as Key
import Data.Aeson.KeyMap qualified as KeyMap
import Data.Foldable (toList)
import Data.Scientific (floatingOrInteger)
import Data.Text (Text)
import Data.Text qualified as Text

walkSchema :: Value -> [Text] -> Value -> Either Text ()
walkSchema root path (Object fields) = do
  if not (null path) && any ((`KeyMap.member` fields) . Key.fromText) profileRuleNames
    then Left "contract profile rules are allowed only at the schema root"
    else pure ()
  rejectUnknownSchemaKeywords path fields
  validateRef root fields
  validateSchemaKeywordTypes fields
  mapM_ (\(key, value) -> walkSchema root (Key.toText key : path) value)
    (nestedSchemas fields)
walkSchema _ path _ = Left (schemaPath path <> " must be an object")

-- The evaluator deliberately supports a small, closed subset of 2020-12.  A
-- bundle is specification-owned data, so silently accepting a new keyword
-- would be worse than refusing a bundle until the evaluator grows.
schemaKeywords :: [Text]
schemaKeywords =
  [ "$schema", "$defs", "$ref", "type", "const", "enum", "required"
  , "properties", "additionalProperties", "items", "minItems", "maxItems"
  , "uniqueItems", "contains", "minContains", "maxContains", "allOf", "anyOf", "not"
  , "if", "then", "else", "pattern"
  , "minLength", "maxLength", "format", "minimum", "maximum"
  , "$comment", "title", "description", "default", "deprecated"
  , "examples", "readOnly", "writeOnly"
  ] <> objectKeywordNames <> profileRuleNames

rejectUnknownSchemaKeywords :: [Text] -> KeyMap.KeyMap Value -> Either Text ()
rejectUnknownSchemaKeywords path fields =
  case filter (`notElem` schemaKeywords) (map (Key.toText . fst) (KeyMap.toList fields)) of
    [] -> Right ()
    names -> Left (schemaPath path <> " contains unsupported keyword(s): "
      <> Text.intercalate ", " names)

validateRef :: Value -> KeyMap.KeyMap Value -> Either Text ()
validateRef root fields = case KeyMap.lookup "$ref" fields of
  Nothing -> Right ()
  Just (String ref)
    | Text.isPrefixOf "#" ref -> case resolvePointer root ref of
        Just _ -> Right ()
        Nothing -> Left "contract schema contains an unresolved local $ref"
    | otherwise -> Left "contract schema contains a remote $ref"
  Just _ -> Left "contract schema $ref must be a string"

validateSchemaKeywordTypes :: KeyMap.KeyMap Value -> Either Text ()
validateSchemaKeywordTypes fields = do
  maybe (Right ()) validateTypeKeyword (KeyMap.lookup "type" fields)
  maybe (Right ()) (const (Right ())) (KeyMap.lookup "const" fields)
  maybe (Right ()) validateEnumKeyword (KeyMap.lookup "enum" fields)
  maybe (Right ()) validateRequiredKeyword (KeyMap.lookup "required" fields)
  maybe (Right ()) validatePropertiesKeyword (KeyMap.lookup "properties" fields)
  maybe (Right ()) validateDefsKeyword (KeyMap.lookup "$defs" fields)
  maybe (Right ()) validateAdditionalKeyword (KeyMap.lookup "additionalProperties" fields)
  maybe (Right ()) (validateIntegerKeyword "minItems") (KeyMap.lookup "minItems" fields)
  maybe (Right ()) (validateIntegerKeyword "maxItems") (KeyMap.lookup "maxItems" fields)
  maybe (Right ()) (validateIntegerKeyword "minContains") (KeyMap.lookup "minContains" fields)
  maybe (Right ()) (validateIntegerKeyword "maxContains") (KeyMap.lookup "maxContains" fields)
  maybe (Right ()) (validateIntegerKeyword "minLength") (KeyMap.lookup "minLength" fields)
  maybe (Right ()) (validateIntegerKeyword "maxLength") (KeyMap.lookup "maxLength" fields)
  maybe (Right ()) (validateNumberKeyword "minimum") (KeyMap.lookup "minimum" fields)
  maybe (Right ()) (validateNumberKeyword "maximum") (KeyMap.lookup "maximum" fields)
  maybe (Right ()) validateStringKeyword (KeyMap.lookup "pattern" fields)
  maybe (Right ()) validateFormatKeyword (KeyMap.lookup "format" fields)
  validateArrayKeyword "allOf" fields
  validateNonEmptyArrayKeyword "anyOf" fields
  maybe (Right ()) validateUniqueItemsKeyword (KeyMap.lookup "uniqueItems" fields)
  validateSchemaKeyword "items" fields
  validateSchemaKeyword "contains" fields
  mapM_ (`validateSchemaKeyword` fields) ["not", "if", "then", "else"]
  if not (KeyMap.member "if" fields) && (KeyMap.member "then" fields || KeyMap.member "else" fields)
    then Left "contract schema then/else require if"
    else Right ()
  validateContainsBounds fields
  validateObjectKeywordTypes fields

validateTypeKeyword :: Value -> Either Text ()
validateTypeKeyword (String name)
  | name `elem` typeNames = Right ()
  | otherwise = Left "contract schema has an unsupported type"
validateTypeKeyword (Array names)
  | null namesList = Left "contract schema type array must not be empty"
  | length namesList /= length (unique namesList) = Left "contract schema type array has duplicates"
  | all (`elem` typeNames) namesList = Right ()
  | otherwise = Left "contract schema has an unsupported type"
  where
    namesList = case traverse asString (toList names) of
      Right values -> values
      Left _ -> []
    asString :: Value -> Either Text Text
    asString (String name) = Right name
    asString _ = Left "contract schema type must contain only strings"
validateTypeKeyword _ = Left "contract schema type must be a string or array"

typeNames :: [Text]
typeNames = ["null", "boolean", "object", "array", "number", "integer", "string"]

validateEnumKeyword :: Value -> Either Text ()
validateEnumKeyword (Array values)
  | null (toList values) = Left "contract schema enum must not be empty"
  | otherwise = Right ()
validateEnumKeyword _ = Left "contract schema enum must be an array"

validateRequiredKeyword :: Value -> Either Text ()
validateRequiredKeyword (Array values)
  | length names == length (toList values)
      && all validName names && length names == length (unique names) = Right ()
  | otherwise = Left "contract schema required must contain unique strings"
  where
    names = [name | String name <- toList values]
    validName name = not (Text.null name)
validateRequiredKeyword _ = Left "contract schema required must be an array"

validatePropertiesKeyword :: Value -> Either Text ()
validatePropertiesKeyword (Object _) = Right ()
validatePropertiesKeyword _ = Left "contract schema properties must be an object"

validateDefsKeyword :: Value -> Either Text ()
validateDefsKeyword (Object _) = Right ()
validateDefsKeyword _ = Left "contract schema $defs must be an object"

validateAdditionalKeyword :: Value -> Either Text ()
validateAdditionalKeyword (Bool _) = Right ()
validateAdditionalKeyword value@(Object _) = validateSchemaKeywordValue "additionalProperties" value
validateAdditionalKeyword _ = Left "contract schema additionalProperties must be boolean or schema"

validateArrayKeyword :: Text -> KeyMap.KeyMap Value -> Either Text ()
validateArrayKeyword name fields = case KeyMap.lookup (Key.fromText name) fields of
  Nothing -> Right ()
  Just (Array _) -> Right ()
  Just _ -> Left ("contract schema " <> name <> " must be an array")

-- | @anyOf@ (specification 0.0.9): a nonempty array of schemas, at least one
-- of which the instance must satisfy.
validateNonEmptyArrayKeyword :: Text -> KeyMap.KeyMap Value -> Either Text ()
validateNonEmptyArrayKeyword name fields = case KeyMap.lookup (Key.fromText name) fields of
  Nothing -> Right ()
  Just (Array values) | not (null values) -> Right ()
  Just _ -> Left ("contract schema " <> name <> " must be a nonempty array")

validateUniqueItemsKeyword :: Value -> Either Text ()
validateUniqueItemsKeyword (Bool _) = Right ()
validateUniqueItemsKeyword _ = Left "contract schema uniqueItems must be a boolean"

validateSchemaKeyword :: Text -> KeyMap.KeyMap Value -> Either Text ()
validateSchemaKeyword name fields = maybe (Right ()) (validateSchemaKeywordValue name)
  (KeyMap.lookup (Key.fromText name) fields)

validateSchemaKeywordValue :: Text -> Value -> Either Text ()
validateSchemaKeywordValue _ (Object _) = Right ()
validateSchemaKeywordValue name _ = Left ("contract schema " <> name <> " must be a schema object")

validateIntegerKeyword :: Text -> Value -> Either Text ()
validateIntegerKeyword name (Number value) =
  case floatingOrInteger value :: Either Double Integer of
    Right integer | integer >= 0 -> Right ()
    _ -> Left ("contract schema " <> name <> " must be a non-negative integer")
validateIntegerKeyword name _ = Left ("contract schema " <> name <> " must be a non-negative integer")

validateNumberKeyword :: Text -> Value -> Either Text ()
validateNumberKeyword _ (Number _) = Right ()
validateNumberKeyword name _ = Left ("contract schema " <> name <> " must be a number")

validateStringKeyword :: Value -> Either Text ()
validateStringKeyword value@(String _) = compilePattern value >> Right ()
validateStringKeyword _ = Left "contract schema pattern must be a string"

validateFormatKeyword :: Value -> Either Text ()
validateFormatKeyword (String "date-time") = Right ()
validateFormatKeyword _ = Left "contract schema format must be date-time"

validateContainsBounds :: KeyMap.KeyMap Value -> Either Text ()
validateContainsBounds fields = do
  let containsPresent = KeyMap.member "contains" fields
  if not containsPresent && (KeyMap.member "minContains" fields || KeyMap.member "maxContains" fields)
    then Left "contract schema minContains/maxContains require contains"
    else pure ()
  minValue <- integerValue "minContains" (KeyMap.lookup "minContains" fields)
  maxValue <- integerValue "maxContains" (KeyMap.lookup "maxContains" fields)
  case (minValue, maxValue) of
    (Just lower, Just upper) | lower > upper -> Left "contract schema minContains exceeds maxContains"
    _ -> Right ()

integerValue :: Text -> Maybe Value -> Either Text (Maybe Integer)
integerValue _ Nothing = Right Nothing
integerValue name (Just (Number value)) = case floatingOrInteger value :: Either Double Integer of
  Right integer | integer >= 0 -> Right (Just integer)
  _ -> Left ("contract schema " <> name <> " must be a non-negative integer")
integerValue name (Just _) = Left ("contract schema " <> name <> " must be a non-negative integer")

nestedSchemas :: KeyMap.KeyMap Value -> [(Key.Key, Value)]
nestedSchemas fields = concatMap one (KeyMap.toList fields) <> objectKeywordSchemas fields
  where
    one (key, value) = case Key.toText key of
      "$defs" -> case value of
        Object definitions -> KeyMap.toList definitions
        _ -> []
      "properties" -> case value of
        Object properties -> KeyMap.toList properties
        _ -> []
      "allOf" -> case value of
        Array schemas -> zipWith (\index schema -> (Key.fromText ("allOf[" <> Text.pack (show index) <> "]"), schema)) [0 :: Int ..] (toList schemas)
        _ -> []
      "anyOf" -> case value of
        Array schemas -> zipWith (\index schema -> (Key.fromText ("anyOf[" <> Text.pack (show index) <> "]"), schema)) [0 :: Int ..] (toList schemas)
        _ -> []
      "items" -> [(Key.fromText "items", value)]
      name | name `elem` ["not", "if", "then", "else"] -> [(key, value)]
      "contains" -> [(Key.fromText "contains", value)]
      "additionalProperties" -> case value of
        Object _ -> [(key, value)]
        _ -> []
      _ -> []

schemaPath :: [Text] -> Text
schemaPath [] = "contract schema"
schemaPath path = "contract schema at " <> Text.intercalate "." (reverse path)
