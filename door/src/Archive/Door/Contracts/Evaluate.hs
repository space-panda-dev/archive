{-# LANGUAGE OverloadedStrings #-}

-- | Door's JSON Schema evaluator over the closed keyword subset
-- "Archive.Door.Contracts.Schema" admits, and the explanation of the first
-- rule an instance breaks.
module Archive.Door.Contracts.Evaluate
  ( evaluateSchema
  , explainMismatch
  ) where

import Archive.Door.Contracts.Fields (unique)
import Archive.Door.Contracts.Pattern (compilePattern, matchesPattern)
import Archive.Door.Contracts.Pointer (instancePointer, resolvePointer)
import Archive.Door.Contracts.Schema
  ( rejectUnknownSchemaKeywords
  , validateRef
  , validateSchemaKeywordTypes
  )
import Archive.Door.Contracts.SchemaError (Requirement (..), SchemaError (..), schemaPath)
import Archive.Door.SchemaObjectKeywords (evaluateObjectKeywords, objectKeywordNames)
import Data.Aeson (Value (..))
import Data.Aeson.Key qualified as Key
import Data.Aeson.KeyMap qualified as KeyMap
import Data.Bifunctor (first)
import Data.Foldable (toList)
import Data.Scientific (floatingOrInteger)
import Data.Text (Text)
import Data.Text qualified as Text
import Data.Time (UTCTime, ZonedTime, defaultTimeLocale, parseTimeM)

evaluateSchema :: Value -> [Text] -> Value -> Value -> Either SchemaError Bool
evaluateSchema root path (Object fields) value = do
  validateSchemaNodeForEvaluation root path fields
  referenced <- case KeyMap.lookup "$ref" fields of
    Nothing -> pure Nothing
    Just (String reference) -> case resolvePointer root reference of
      Just target -> pure (Just target)
      Nothing -> Left UnresolvedRef
    Just _ -> Left (KeywordMust "$ref" BeString)
  referenceMatch <- maybe (pure True) (\target -> evaluateSchema root path target value) referenced
  keywordMatch <- and <$> sequence
    [ evaluateType fields value
    , evaluateConst fields value
    , evaluateEnum fields value
    , evaluateMinimum fields value
    , evaluateMaximum fields value
    , evaluateObject root path fields value
    , evaluateArray root path fields value
    , evaluateString fields value
    , evaluateAllOf root path fields value
    , evaluateAnyOf root path fields value
    , evaluateNot root path fields value
    , evaluateConditional root path fields value
    , evaluateObjectKeywords (\schema item -> evaluateSchema root ("propertyNames" : path) schema item) fields value
    ]
  pure (referenceMatch && keywordMatch)
evaluateSchema _ path _ _ = Left (NodeNotObject path)

validateSchemaNodeForEvaluation :: Value -> [Text] -> KeyMap.KeyMap Value -> Either SchemaError ()
validateSchemaNodeForEvaluation root path fields = do
  rejectUnknownSchemaKeywords path fields
  validateRef root fields
  validateSchemaKeywordTypes fields

-- | Name the first rule an instance breaks, for a refusal a person can act
-- on: where in the instance (a JSON Pointer from @{"payload", "refs"}@) and
-- which keyword of which part of the schema. Called only after
-- 'evaluateSchema' answered no; it descends into the first failing
-- property, item, @allOf@ branch, @$ref@, or applicable @then@/@else@, and
-- otherwise names the keyword at the node where the instance fails.
explainMismatch :: Value -> [Text] -> [Text] -> Value -> Value -> Either SchemaError Text
explainMismatch root path location (Object fields) value = do
  let fails schemaPath' schema item = not <$> evaluateSchema root schemaPath' schema item
      at = instancePointer location
      leaf keyword = Right (at <> " breaks " <> keyword <> " of the " <> schemaPath path)
      firstJust [] = Right Nothing
      firstJust (step : rest) = step >>= maybe (firstJust rest) (pure . Just)
      reasons = firstJust
        [ case KeyMap.lookup "$ref" fields of
            Just (String reference) | Just target <- resolvePointer root reference -> do
              broken <- fails path target value
              if broken then Just <$> explainMismatch root path location target value else pure Nothing
            _ -> pure Nothing
        , firstJust
            [ do
                broken <- not <$> evaluateLeaf keyword
                if broken then Just <$> leaf keyword else pure Nothing
            | keyword <- leafKeywords, KeyMap.member (Key.fromText keyword) fields ]
        , objectReason
        , arrayReason
        , case KeyMap.lookup "allOf" fields of
            Just (Array schemas) -> firstJust
              [ do
                  let branchPath = ("allOf[" <> Text.pack (show index) <> "]") : path
                  broken <- fails branchPath schema value
                  if broken then Just <$> explainMismatch root branchPath location schema value else pure Nothing
              | (index, schema) <- zip [0 :: Int ..] (toList schemas) ]
            _ -> pure Nothing
        , case KeyMap.lookup "anyOf" fields of
            Just (Array schemas) -> do
              matched <- or <$> mapM (\schema -> evaluateSchema root ("anyOf" : path) schema value) (toList schemas)
              if matched then pure Nothing else Just <$> leaf "anyOf (it matches none of the choices)"
            _ -> pure Nothing
        , case KeyMap.lookup "not" fields of
            Just schema -> do
              matched <- evaluateSchema root ("not" : path) schema value
              if matched then Just <$> leaf "not (it matches what is forbidden)" else pure Nothing
            Nothing -> pure Nothing
        , case KeyMap.lookup "if" fields of
            Just condition -> do
              holds <- evaluateSchema root ("if" : path) condition value
              let name = if holds then "then" else "else"
              case KeyMap.lookup (Key.fromText name) fields of
                Just schema -> do
                  broken <- fails (name : path) schema value
                  if broken then Just <$> explainMismatch root (name : path) location schema value else pure Nothing
                Nothing -> pure Nothing
            Nothing -> pure Nothing
        , do
            broken <- not <$> evaluateObjectKeywords
              (\schema item -> evaluateSchema root ("propertyNames" : path) schema item) fields value
            if broken
              then Just <$> leaf (Text.intercalate ", " [k | k <- objectKeywordNames, KeyMap.member (Key.fromText k) fields])
              else pure Nothing
        ]
      evaluateLeaf keyword =
        let only = KeyMap.filterWithKey (\key _ -> Key.toText key == keyword) fields
        in and <$> sequence [ evaluateType only value, evaluateConst only value, evaluateEnum only value
                            , evaluateMinimum only value, evaluateMaximum only value, evaluateString only value ]
      objectReason = case value of
        Object object -> do
          let missing = case KeyMap.lookup "required" fields of
                Just (Array names) -> [name | String name <- toList names, not (KeyMap.member (Key.fromText name) object)]
                _ -> []
              properties = case KeyMap.lookup "properties" fields of
                Just (Object definitions) -> definitions
                _ -> KeyMap.empty
              property (key, item) = case KeyMap.lookup key properties of
                Just schema -> nested (Key.toText key : path) schema
                Nothing -> case KeyMap.lookup "additionalProperties" fields of
                  Just (Bool False) -> pure (Just (instancePointer (Key.toText key : location)
                    <> " is a member the " <> schemaPath path <> " does not allow"))
                  Just schema@(Object _) -> nested ("additionalProperties" : path) schema
                  _ -> pure Nothing
                where
                  nested schemaPath' schema = do
                    broken <- fails schemaPath' schema item
                    if broken
                      then Just <$> explainMismatch root schemaPath' (Key.toText key : location) schema item
                      else pure Nothing
          if not (null missing)
            then Just <$> leaf ("required (" <> Text.intercalate ", " missing <> " missing)")
            else firstJust (map property (KeyMap.toList object))
        _ -> pure Nothing
      arrayReason = case value of
        Array values -> do
          let items = toList values
          fromItems <- case KeyMap.lookup "items" fields of
            Just schema@(Object _) -> firstJust
              [ do
                  broken <- fails ("items" : path) schema item
                  if broken
                    then Just <$> explainMismatch root ("items" : path) (Text.pack (show index) : location) schema item
                    else pure Nothing
              | (index, item) <- zip [0 :: Int ..] items ]
            _ -> pure Nothing
          case fromItems of
            Just reason -> pure (Just reason)
            Nothing -> firstJust
              [ do
                  let only = KeyMap.filterWithKey (\key _ -> Key.toText key `elem` (name : companions)) fields
                  broken <- not <$> evaluateArray root path only value
                  if broken then Just <$> leaf name else pure Nothing
              | (name, companions) <- [("minItems", []), ("maxItems", []), ("uniqueItems", []),
                                       ("contains", ["minContains", "maxContains"])]
              , KeyMap.member (Key.fromText name) fields ]
        _ -> pure Nothing
  found <- reasons
  pure (maybe (instancePointer location <> " does not satisfy the " <> schemaPath path) id found)
explainMismatch _ path location _ _ = Right (instancePointer location <> " does not satisfy the " <> schemaPath path)

leafKeywords :: [Text]
leafKeywords = ["type", "const", "enum", "minimum", "maximum", "pattern", "minLength", "maxLength", "format"]

evaluateType :: KeyMap.KeyMap Value -> Value -> Either SchemaError Bool
evaluateType fields value = case KeyMap.lookup "type" fields of
  Nothing -> Right True
  Just typeValue -> do
    names <- case typeValue of
      String name -> Right [name]
      Array values -> mapM asString (toList values)
      _ -> Left (KeywordMust "type" BeStringOrArray)
    pure (any (`valueHasType` value) names)
  where
    asString (String name) = Right name
    asString _ = Left (KeywordMust "type" ContainOnlyStrings)

valueHasType :: Text -> Value -> Bool
valueHasType "null" Null = True
valueHasType "boolean" (Bool _) = True
valueHasType "object" (Object _) = True
valueHasType "array" (Array _) = True
valueHasType "number" (Number _) = True
valueHasType "integer" (Number value) = case floatingOrInteger value :: Either Double Integer of
  Right _ -> True
  Left _ -> False
valueHasType "string" (String _) = True
valueHasType _ _ = False

evaluateConst :: KeyMap.KeyMap Value -> Value -> Either SchemaError Bool
evaluateConst fields value = Right (maybe True (== value) (KeyMap.lookup "const" fields))

evaluateEnum :: KeyMap.KeyMap Value -> Value -> Either SchemaError Bool
evaluateEnum fields value = Right (maybe True (value `elem`) (arrayValues <$> KeyMap.lookup "enum" fields))
  where
    arrayValues (Array values) = toList values
    arrayValues _ = []

evaluateMinimum :: KeyMap.KeyMap Value -> Value -> Either SchemaError Bool
evaluateMinimum fields (Number value) = case KeyMap.lookup "minimum" fields of
  Nothing -> Right True
  Just (Number lower) -> Right (value >= lower)
  Just _ -> Left (KeywordMust "minimum" BeNumber)
evaluateMinimum _ _ = Right True

evaluateMaximum :: KeyMap.KeyMap Value -> Value -> Either SchemaError Bool
evaluateMaximum fields (Number value) = case KeyMap.lookup "maximum" fields of
  Nothing -> Right True
  Just (Number upper) -> Right (value <= upper)
  Just _ -> Left (KeywordMust "maximum" BeNumber)
evaluateMaximum _ _ = Right True

evaluateObject :: Value -> [Text] -> KeyMap.KeyMap Value -> Value -> Either SchemaError Bool
evaluateObject root path fields (Object object) = do
  requiredMatch <- case KeyMap.lookup "required" fields of
    Nothing -> Right True
    Just (Array names) -> pure (all (`KeyMap.member` object) [Key.fromText name | String name <- toList names])
    Just _ -> Left (KeywordMust "required" BeArray)
  properties <- case KeyMap.lookup "properties" fields of
    Nothing -> Right KeyMap.empty
    Just (Object definitions) -> Right definitions
    Just _ -> Left (KeywordMust "properties" BeObject)
  propertyMatch <- and <$> mapM (validateProperty properties) (KeyMap.toList object)
  pure (requiredMatch && propertyMatch)
  where
    validateProperty properties (key, item) = case KeyMap.lookup key properties of
      Just propertySchema -> evaluateSchema root (Key.toText key : path) propertySchema item
      Nothing -> case KeyMap.lookup "additionalProperties" fields of
        Nothing -> Right True
        Just (Bool allowed) -> Right allowed
        Just additionalSchema -> evaluateSchema root (Key.toText key : path) additionalSchema item
evaluateObject _ _ fields _ = pure (not (hasObjectOnlyConstraint fields))

hasObjectOnlyConstraint :: KeyMap.KeyMap Value -> Bool
hasObjectOnlyConstraint fields = any (`KeyMap.member` fields)
  ["required", "properties"]

evaluateArray :: Value -> [Text] -> KeyMap.KeyMap Value -> Value -> Either SchemaError Bool
evaluateArray root path fields (Array values) = do
  itemMatch <- case KeyMap.lookup "items" fields of
    Nothing -> Right True
    Just (Object itemSchema) -> and <$> mapM (evaluateSchema root ("items" : path) (Object itemSchema)) (toList values)
    Just _ -> Left (KeywordMust "items" BeSchemaObject)
  minMatch <- compareArrayBound "minItems" (>=) fields (toList values)
  maxMatch <- compareArrayBound "maxItems" (<=) fields (toList values)
  containsMatch <- case KeyMap.lookup "contains" fields of
    Nothing -> Right True
    Just (Object containsSchema) -> do
      matches <- mapM (evaluateSchema root ("contains" : path) (Object containsSchema)) (toList values)
      let count = fromIntegral (length (filter id matches)) :: Integer
          lower = maybe 1 id (integerField "minContains" fields)
          upper = integerField "maxContains" fields
      pure (count >= lower && maybe True (count <=) upper)
    Just _ -> Left (KeywordMust "contains" BeSchemaObject)
  uniqueMatch <- case KeyMap.lookup "uniqueItems" fields of
    Nothing -> Right True
    Just (Bool False) -> Right True
    -- JSON equality: aeson compares objects by their members and numbers by
    -- value, as JSON Schema's uniqueItems does.
    Just (Bool True) -> Right (length (toList values) == length (unique (toList values)))
    Just _ -> Left (KeywordMust "uniqueItems" BeBoolean)
  pure (itemMatch && minMatch && maxMatch && containsMatch && uniqueMatch)
evaluateArray _ _ fields _ = pure (not (hasArrayOnlyConstraint fields))

hasArrayOnlyConstraint :: KeyMap.KeyMap Value -> Bool
hasArrayOnlyConstraint fields = any (`KeyMap.member` fields)
  ["items", "minItems", "maxItems", "uniqueItems", "contains", "minContains", "maxContains"]

compareArrayBound :: Text -> (Integer -> Integer -> Bool) -> KeyMap.KeyMap Value -> VectorLike -> Either SchemaError Bool
compareArrayBound name comparison fields values = case integerField name fields of
  Nothing -> Right True
  Just bound -> Right (lengthLike values `comparison` bound)

-- Keeping this alias local avoids exposing aeson's vector type in the module's
-- API while retaining a small, readable helper signature.
type VectorLike = [Value]

lengthLike :: VectorLike -> Integer
lengthLike = fromIntegral . length

integerField :: Text -> KeyMap.KeyMap Value -> Maybe Integer
integerField name fields = case KeyMap.lookup (Key.fromText name) fields of
  Just (Number value) -> case floatingOrInteger value :: Either Double Integer of
    Right integer | integer >= 0 -> Just integer
    _ -> Nothing
  _ -> Nothing

evaluateString :: KeyMap.KeyMap Value -> Value -> Either SchemaError Bool
evaluateString fields (String value) = do
  patternMatch <- case KeyMap.lookup "pattern" fields of
    Nothing -> Right True
    Just patternValue -> (`matchesPattern` value) <$> first BadPattern (compilePattern patternValue)
  lengthMatch <- case KeyMap.lookup "minLength" fields of
    Nothing -> Right True
    Just (Number number) -> case floatingOrInteger number :: Either Double Integer of
      Right lower -> Right (fromIntegral (Text.length value) >= (lower :: Integer))
      Left _ -> Left (KeywordMust "minLength" BeInteger)
    Just _ -> Left (KeywordMust "minLength" BeInteger)
  -- Data.Text counts Unicode scalar values, which is JSON Schema's
  -- code-point definition of string length.
  maxLengthMatch <- case KeyMap.lookup "maxLength" fields of
    Nothing -> Right True
    Just (Number number) -> case floatingOrInteger number :: Either Double Integer of
      Right upper -> Right (fromIntegral (Text.length value) <= (upper :: Integer))
      Left _ -> Left (KeywordMust "maxLength" BeInteger)
    Just _ -> Left (KeywordMust "maxLength" BeInteger)
  formatMatch <- case KeyMap.lookup "format" fields of
    Nothing -> Right True
    Just (String "date-time") -> Right (validDateTime value)
    Just _ -> Left (KeywordMust "format" BeDateTime)
  pure (patternMatch && lengthMatch && maxLengthMatch && formatMatch)
evaluateString _ _ = Right True

evaluateAllOf :: Value -> [Text] -> KeyMap.KeyMap Value -> Value -> Either SchemaError Bool
evaluateAllOf root path fields value = case KeyMap.lookup "allOf" fields of
  Nothing -> Right True
  Just (Array schemas) -> and <$> mapM (\schema -> evaluateSchema root ("allOf" : path) schema value) (toList schemas)
  Just _ -> Left (KeywordMust "allOf" BeArray)

-- | @not@ (specification 0.0.9): the instance must not satisfy the schema.
evaluateNot :: Value -> [Text] -> KeyMap.KeyMap Value -> Value -> Either SchemaError Bool
evaluateNot root path fields value = case KeyMap.lookup "not" fields of
  Nothing -> Right True
  Just schema -> not <$> evaluateSchema root ("not" : path) schema value

-- | @if@, @then@, and @else@ (specification 0.0.9): when the instance
-- satisfies @if@ it must satisfy @then@, and otherwise @else@; an absent
-- branch is satisfied. Both branches are evaluated, so a malformed branch is
-- refused whichever applies.
evaluateConditional :: Value -> [Text] -> KeyMap.KeyMap Value -> Value -> Either SchemaError Bool
evaluateConditional root path fields value = case KeyMap.lookup "if" fields of
  Nothing -> Right True
  Just condition -> do
    holds <- evaluateSchema root ("if" : path) condition value
    whenTrue <- branch "then"
    whenFalse <- branch "else"
    pure (if holds then whenTrue else whenFalse)
  where
    branch name = maybe (Right True) (\schema -> evaluateSchema root (name : path) schema value)
      (KeyMap.lookup (Key.fromText name) fields)

-- | Every branch is evaluated, so a malformed branch is refused even when an
-- earlier one matched; the instance must satisfy at least one.
evaluateAnyOf :: Value -> [Text] -> KeyMap.KeyMap Value -> Value -> Either SchemaError Bool
evaluateAnyOf root path fields value = case KeyMap.lookup "anyOf" fields of
  Nothing -> Right True
  Just (Array schemas) | not (null schemas) ->
    or <$> mapM (\schema -> evaluateSchema root ("anyOf" : path) schema value) (toList schemas)
  Just _ -> Left (KeywordMust "anyOf" BeNonEmptyArray)

validDateTime :: Text -> Bool
validDateTime value
  | Text.isSuffixOf "Z" value = case parseTimeM True defaultTimeLocale "%Y-%m-%dT%H:%M:%S%QZ" (Text.unpack value) :: Maybe UTCTime of
      Just _ -> True
      Nothing -> False
  | otherwise = case parseTimeM True defaultTimeLocale "%Y-%m-%dT%H:%M:%S%Q%z" (Text.unpack value) :: Maybe ZonedTime of
      Just _ -> True
      Nothing -> False
