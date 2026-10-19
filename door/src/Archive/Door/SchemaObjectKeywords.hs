{-# LANGUAGE OverloadedStrings #-}

-- | The JSON Schema object keywords specification 0.0.10 adds to Door's
-- closed evaluator: @propertyNames@, @minProperties@, and @maxProperties@.
-- An export's options are an object whose names a pattern fixes and whose
-- count is bounded (ADR 0089); nothing else in the evaluator can say that.
--
-- They follow JSON Schema 2020-12: each applies only to an object instance
-- and is satisfied by any other value. @propertyNames@ is a schema every
-- property name, as a JSON string, must satisfy; the two counts are
-- non-negative integers.
module Archive.Door.SchemaObjectKeywords
  ( objectKeywordNames
  , validateObjectKeywordTypes
  , objectKeywordSchemas
  , evaluateObjectKeywords
  ) where

import Data.Aeson (Value (..))
import Data.Aeson.Key qualified as Key
import Data.Aeson.KeyMap qualified as KeyMap
import Data.Scientific (floatingOrInteger)
import Data.Text (Text)

objectKeywordNames :: [Text]
objectKeywordNames = ["propertyNames", "minProperties", "maxProperties"]

-- | Refuse a malformed keyword while the bundle loads.
validateObjectKeywordTypes :: KeyMap.KeyMap Value -> Either Text ()
validateObjectKeywordTypes fields = do
  case KeyMap.lookup "propertyNames" fields of
    Nothing -> Right ()
    Just (Object _) -> Right ()
    Just _ -> Left "contract schema propertyNames must be a schema object"
  mapM_ count ["minProperties", "maxProperties"]
  where
    count name = case KeyMap.lookup (Key.fromText name) fields of
      Nothing -> Right ()
      Just value
        | Just _ <- natural value -> Right ()
        | otherwise -> Left ("contract schema " <> name <> " must be a non-negative integer")

-- | The nested schemas these keywords hold, for the bundle walk.
objectKeywordSchemas :: KeyMap.KeyMap Value -> [(Key.Key, Value)]
objectKeywordSchemas fields = case KeyMap.lookup "propertyNames" fields of
  Just value -> [("propertyNames", value)]
  Nothing -> []

-- | Evaluate the keywords against an instance; @nested@ evaluates a schema
-- this node holds (the evaluator's own, so @propertyNames@ may use every
-- keyword it supports).
evaluateObjectKeywords :: (Value -> Value -> Either Text Bool) -> KeyMap.KeyMap Value -> Value -> Either Text Bool
evaluateObjectKeywords nested fields (Object object) = do
  let size = toInteger (KeyMap.size object)
      bound name compare' = case KeyMap.lookup (Key.fromText name) fields >>= natural of
        Nothing -> True
        Just limit -> size `compare'` limit
  names <- case KeyMap.lookup "propertyNames" fields of
    Nothing -> Right True
    Just schema -> and <$> mapM (nested schema . String . Key.toText) (KeyMap.keys object)
  pure (names && bound "minProperties" (>=) && bound "maxProperties" (<=))
evaluateObjectKeywords _ _ _ = Right True

natural :: Value -> Maybe Integer
natural (Number value) = case floatingOrInteger value :: Either Double Integer of
  Right integer | integer >= 0 -> Just integer
  _ -> Nothing
natural _ = Nothing
