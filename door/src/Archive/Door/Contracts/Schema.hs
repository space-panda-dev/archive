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
import Archive.Door.Contracts.SchemaError (Requirement (..), SchemaError (..), schemaPath)
import Archive.Door.SchemaObjectKeywords
  ( objectKeywordNames
  , objectKeywordSchemas
  , validateObjectKeywordTypes
  )
import Data.Aeson (Value (..))
import Data.Aeson.Key qualified as Key
import Data.Aeson.KeyMap qualified as KeyMap
import Data.Bifunctor (first)
import Data.Foldable (toList)
import Data.Maybe (fromMaybe)
import Data.Scientific (floatingOrInteger)
import Data.Text (Text)
import Data.Text qualified as Text

walkSchema :: Value -> [Text] -> Value -> Either SchemaError ()
walkSchema root path (Object fields) = do
  if not (null path) && any ((`KeyMap.member` fields) . Key.fromText) profileRuleNames
    then Left ProfileRuleBelowRoot
    else pure ()
  rejectUnknownSchemaKeywords path fields
  validateRef root fields
  validateSchemaKeywordTypes fields
  mapM_ (\(key, value) -> walkSchema root (Key.toText key : path) value)
    (nestedSchemas fields)
walkSchema _ path _ = Left (NodeNotObject path)

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

rejectUnknownSchemaKeywords :: [Text] -> KeyMap.KeyMap Value -> Either SchemaError ()
rejectUnknownSchemaKeywords path fields =
  case filter (`notElem` schemaKeywords) (map (Key.toText . fst) (KeyMap.toList fields)) of
    [] -> Right ()
    names -> Left (UnsupportedKeywords path names)

validateRef :: Value -> KeyMap.KeyMap Value -> Either SchemaError ()
validateRef root fields = case KeyMap.lookup "$ref" fields of
  Nothing -> Right ()
  Just (String ref)
    | Text.isPrefixOf "#" ref -> case resolvePointer root ref of
        Just _ -> Right ()
        Nothing -> Left UnresolvedRef
    | otherwise -> Left RemoteRef
  Just _ -> Left (KeywordMust "$ref" BeString)

validateSchemaKeywordTypes :: KeyMap.KeyMap Value -> Either SchemaError ()
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
    then Left ThenElseWithoutIf
    else Right ()
  validateContainsBounds fields
  first ForeignSchema (validateObjectKeywordTypes fields)

validateTypeKeyword :: Value -> Either SchemaError ()
validateTypeKeyword (String name)
  | name `elem` typeNames = Right ()
  | otherwise = Left UnsupportedType
validateTypeKeyword (Array names)
  | null namesList = Left (KeywordMust "type array" NotBeEmpty)
  | length namesList /= length (unique namesList) = Left TypeArrayDuplicates
  | all (`elem` typeNames) namesList = Right ()
  | otherwise = Left UnsupportedType
  where
    -- An array holding a non-string reads as empty, and is refused as one.
    namesList = fromMaybe [] (traverse asString (toList names))
    asString (String name) = Just name
    asString _ = Nothing
validateTypeKeyword _ = Left (KeywordMust "type" BeStringOrArray)

typeNames :: [Text]
typeNames = ["null", "boolean", "object", "array", "number", "integer", "string"]

validateEnumKeyword :: Value -> Either SchemaError ()
validateEnumKeyword (Array values)
  | null (toList values) = Left (KeywordMust "enum" NotBeEmpty)
  | otherwise = Right ()
validateEnumKeyword _ = Left (KeywordMust "enum" BeArray)

validateRequiredKeyword :: Value -> Either SchemaError ()
validateRequiredKeyword (Array values)
  | length names == length (toList values)
      && all validName names && length names == length (unique names) = Right ()
  | otherwise = Left (KeywordMust "required" ContainUniqueStrings)
  where
    names = [name | String name <- toList values]
    validName name = not (Text.null name)
validateRequiredKeyword _ = Left (KeywordMust "required" BeArray)

validatePropertiesKeyword :: Value -> Either SchemaError ()
validatePropertiesKeyword (Object _) = Right ()
validatePropertiesKeyword _ = Left (KeywordMust "properties" BeObject)

validateDefsKeyword :: Value -> Either SchemaError ()
validateDefsKeyword (Object _) = Right ()
validateDefsKeyword _ = Left (KeywordMust "$defs" BeObject)

validateAdditionalKeyword :: Value -> Either SchemaError ()
validateAdditionalKeyword (Bool _) = Right ()
validateAdditionalKeyword value@(Object _) = validateSchemaKeywordValue "additionalProperties" value
validateAdditionalKeyword _ = Left (KeywordMust "additionalProperties" BeBooleanOrSchema)

validateArrayKeyword :: Text -> KeyMap.KeyMap Value -> Either SchemaError ()
validateArrayKeyword name fields = case KeyMap.lookup (Key.fromText name) fields of
  Nothing -> Right ()
  Just (Array _) -> Right ()
  Just _ -> Left (KeywordMust name BeArray)

-- | @anyOf@ (specification 0.0.9): a nonempty array of schemas, at least one
-- of which the instance must satisfy.
validateNonEmptyArrayKeyword :: Text -> KeyMap.KeyMap Value -> Either SchemaError ()
validateNonEmptyArrayKeyword name fields = case KeyMap.lookup (Key.fromText name) fields of
  Nothing -> Right ()
  Just (Array values) | not (null values) -> Right ()
  Just _ -> Left (KeywordMust name BeNonEmptyArray)

validateUniqueItemsKeyword :: Value -> Either SchemaError ()
validateUniqueItemsKeyword (Bool _) = Right ()
validateUniqueItemsKeyword _ = Left (KeywordMust "uniqueItems" BeBoolean)

validateSchemaKeyword :: Text -> KeyMap.KeyMap Value -> Either SchemaError ()
validateSchemaKeyword name fields = maybe (Right ()) (validateSchemaKeywordValue name)
  (KeyMap.lookup (Key.fromText name) fields)

validateSchemaKeywordValue :: Text -> Value -> Either SchemaError ()
validateSchemaKeywordValue _ (Object _) = Right ()
validateSchemaKeywordValue name _ = Left (KeywordMust name BeSchemaObject)

validateIntegerKeyword :: Text -> Value -> Either SchemaError ()
validateIntegerKeyword name (Number value) =
  case floatingOrInteger value :: Either Double Integer of
    Right integer | integer >= 0 -> Right ()
    _ -> Left (KeywordMust name BeNonNegativeInteger)
validateIntegerKeyword name _ = Left (KeywordMust name BeNonNegativeInteger)

validateNumberKeyword :: Text -> Value -> Either SchemaError ()
validateNumberKeyword _ (Number _) = Right ()
validateNumberKeyword name _ = Left (KeywordMust name BeNumber)

validateStringKeyword :: Value -> Either SchemaError ()
validateStringKeyword value = () <$ first BadPattern (compilePattern value)

validateFormatKeyword :: Value -> Either SchemaError ()
validateFormatKeyword (String "date-time") = Right ()
validateFormatKeyword _ = Left (KeywordMust "format" BeDateTime)

validateContainsBounds :: KeyMap.KeyMap Value -> Either SchemaError ()
validateContainsBounds fields = do
  let containsPresent = KeyMap.member "contains" fields
  if not containsPresent && (KeyMap.member "minContains" fields || KeyMap.member "maxContains" fields)
    then Left ContainsBoundsWithoutContains
    else pure ()
  minValue <- integerValue "minContains" (KeyMap.lookup "minContains" fields)
  maxValue <- integerValue "maxContains" (KeyMap.lookup "maxContains" fields)
  case (minValue, maxValue) of
    (Just lower, Just upper) | lower > upper -> Left MinContainsExceedsMax
    _ -> Right ()

integerValue :: Text -> Maybe Value -> Either SchemaError (Maybe Integer)
integerValue _ Nothing = Right Nothing
integerValue name (Just (Number value)) = case floatingOrInteger value :: Either Double Integer of
  Right integer | integer >= 0 -> Right (Just integer)
  _ -> Left (KeywordMust name BeNonNegativeInteger)
integerValue name (Just _) = Left (KeywordMust name BeNonNegativeInteger)

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
