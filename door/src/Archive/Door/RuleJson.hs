{-# LANGUAGE OverloadedStrings #-}

-- | Small readers shared by the profile rules of specification 0.0.10
-- (D-18): strict parsing of a rule's own JSON, and lenient reading of a
-- record's payload and references.
--
-- A rule's JSON is specification-owned, so it is parsed strictly: exact keys,
-- non-empty names, and distinct fields. A payload has already satisfied its
-- bundle's schema when a rule reads it, so a reader here skips what is absent
-- and leaves types to the schema; a rule refuses only what it states.
module Archive.Door.RuleJson
  ( -- * Rule syntax
    ruleObject
  , exactKeys
  , textField
  , textsField
  , pathField
  , boolField
  , ruleList
  , NamedFields
  , namedFields
    -- * Payload reading
  , integerOf
  , integerAt
  , textAt
  , objectAt
  , arrayAt
  , holders
  , reached
  , elements
  , pathLabel
    -- * References
  , InstanceRef (..)
  , instancePayload
  , instanceRefs
  , refsIn
  ) where

import Data.Aeson (Value (..))
import Data.Aeson.Key qualified as Key
import Data.Aeson.KeyMap qualified as KeyMap
import Data.Foldable (toList)
import Data.List (nub, sort)
import Data.Map.Strict qualified as Map
import Data.Scientific (floatingOrInteger)
import Data.Text (Text)
import Data.Text qualified as Text

-- ---------------------------------------------------------------------------
-- Rule syntax

ruleObject :: Text -> Value -> Either Text (KeyMap.KeyMap Value)
ruleObject _ (Object fields) = Right fields
ruleObject label _ = Left (label <> " must be an object")

exactKeys :: Text -> [Text] -> KeyMap.KeyMap Value -> Either Text ()
exactKeys label wanted fields
  | sort (map Key.toText (KeyMap.keys fields)) == sort wanted = Right ()
  | otherwise = Left (label <> " must contain exactly " <> Text.intercalate ", " (sort wanted))

textField :: Text -> Text -> KeyMap.KeyMap Value -> Either Text Text
textField label name fields = case KeyMap.lookup (Key.fromText name) fields of
  Just (String value) | not (Text.null value) -> Right value
  _ -> Left (label <> " " <> name <> " must be a non-empty string")

-- | A non-empty array of distinct non-empty strings.
textsField :: Text -> Text -> KeyMap.KeyMap Value -> Either Text [Text]
textsField label name fields = case KeyMap.lookup (Key.fromText name) fields of
  Just (Array values) | not (null values) -> do
    names <- mapM text (toList values)
    if length names == length (nub names) then Right names
      else Left (label <> " " <> name <> " must not repeat a name")
  _ -> Left (label <> " " <> name <> " must be a non-empty array")
  where
    text (String value) | not (Text.null value) = Right value
    text _ = Left (label <> " " <> name <> " must hold non-empty strings")

-- | A payload path: a non-empty array of non-empty field names.
pathField :: Text -> Text -> KeyMap.KeyMap Value -> Either Text [Text]
pathField label name fields = case KeyMap.lookup (Key.fromText name) fields of
  Just (Array values) | not (null values) -> mapM step (toList values)
  _ -> Left (label <> " " <> name <> " must be a non-empty path")
  where
    step (String value) | not (Text.null value) = Right value
    step _ = Left (label <> " " <> name <> " must hold non-empty field names")

boolField :: Text -> Text -> KeyMap.KeyMap Value -> Either Text Bool
boolField label name fields = case KeyMap.lookup (Key.fromText name) fields of
  Just (Bool value) -> Right value
  _ -> Left (label <> " " <> name <> " must be a boolean")

-- | A rule that is a non-empty list of objects, each parsed by @item@.
ruleList :: Text -> (KeyMap.KeyMap Value -> Either Text a) -> Value -> Either Text [a]
ruleList label item (Array values) | not (null values) = mapM (\value -> ruleObject label value >>= item) (toList values)
ruleList label _ _ = Left (label <> " must be a non-empty array of objects")

-- | A rule of exactly the named string fields, each non-empty and all
-- distinct ("each names distinct fields", D-18).
type NamedFields = Text -> Text

namedFields :: Text -> [Text] -> Value -> Either Text NamedFields
namedFields label names value = do
  fields <- ruleObject label value
  exactKeys label names fields
  values <- mapM (\name -> textField label name fields) names
  if length values /= length (nub values)
    then Left (label <> " must name distinct fields")
    else let table = Map.fromList (zip names values)
         in Right (\name -> Map.findWithDefault name name table)

-- ---------------------------------------------------------------------------
-- Payload reading

integerOf :: Value -> Maybe Integer
integerOf (Number value) = either (const Nothing) Just (floatingOrInteger value :: Either Double Integer)
integerOf _ = Nothing

integerAt :: Text -> KeyMap.KeyMap Value -> Maybe Integer
integerAt name fields = KeyMap.lookup (Key.fromText name) fields >>= integerOf

textAt :: Text -> KeyMap.KeyMap Value -> Maybe Text
textAt name fields = case KeyMap.lookup (Key.fromText name) fields of
  Just (String value) -> Just value
  _ -> Nothing

objectAt :: Text -> KeyMap.KeyMap Value -> Maybe (KeyMap.KeyMap Value)
objectAt name fields = case KeyMap.lookup (Key.fromText name) fields of
  Just (Object value) -> Just value
  _ -> Nothing

-- | An array field's items; an absent or other field holds none.
arrayAt :: Text -> KeyMap.KeyMap Value -> [Value]
arrayAt name fields = case KeyMap.lookup (Key.fromText name) fields of
  Just (Array values) -> toList values
  _ -> []

-- | The objects that hold a path's last field: every object the path
-- without its last name reaches, entering arrays on the way.
holders :: [Text] -> KeyMap.KeyMap Value -> [KeyMap.KeyMap Value]
holders path payload = case splitLast path of
  Nothing -> []
  Just (parents, _) -> [fields | Object fields <- enter (go parents [Object payload])]
  where
    go [] values = values
    go (name : rest) values = go rest (concat [maybe [] (: []) (KeyMap.lookup (Key.fromText name) fields)
                                              | Object fields <- enter values])

-- | The values of a path's last field on each of its holders, as they are.
reached :: [Text] -> KeyMap.KeyMap Value -> [Value]
reached path payload = case splitLast path of
  Nothing -> []
  Just (_, name) -> [value | fields <- holders path payload
                           , Just value <- [KeyMap.lookup (Key.fromText name) fields]]

-- | A path's names before its last, and its last; an empty path has neither.
splitLast :: [a] -> Maybe ([a], a)
splitLast = foldr step Nothing
  where
    step name Nothing = Just ([], name)
    step name (Just (rest, final)) = Just (name : rest, final)

-- | What a path reaches with its last array entered too: "every object the
-- path reaches" (a payload object, or each element of a payload array).
elements :: [Text] -> KeyMap.KeyMap Value -> [Value]
elements path payload = enter (reached path payload)

enter :: [Value] -> [Value]
enter = concatMap (\value -> case value of
  Array items -> toList items
  _ -> [value])

pathLabel :: [Text] -> Text
pathLabel = Text.intercalate "."

-- ---------------------------------------------------------------------------
-- References of a contract instance (@{payload, refs}@)

data InstanceRef = InstanceRef
  { instanceRefRole :: Text
  , instanceRefObject :: Text
  , instanceRefRevision :: Maybe Text
  } deriving (Eq, Ord, Show)

instancePayload :: KeyMap.KeyMap Value -> KeyMap.KeyMap Value
instancePayload instanceValue = maybe KeyMap.empty id (objectAt "payload" instanceValue)

instanceRefs :: KeyMap.KeyMap Value -> [InstanceRef]
instanceRefs instanceValue =
  [ InstanceRef role object (textAt "revision_id" fields)
  | Object fields <- arrayAt "refs" instanceValue
  , Just role <- [textAt "role" fields]
  , Just object <- [textAt "id" fields]
  ]

refsIn :: Text -> [InstanceRef] -> [InstanceRef]
refsIn role = filter ((== role) . instanceRefRole)
