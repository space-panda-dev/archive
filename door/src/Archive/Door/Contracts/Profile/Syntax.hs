{-# LANGUAGE OverloadedStrings #-}

-- | The shape most profile rules share: an object of exactly the named
-- fields, each a string naming a payload field.
module Archive.Door.Contracts.Profile.Syntax
  ( validateNamedFieldsRule
  ) where

import Archive.Door.Contracts.Fields (requireExactKeys, requiredText)
import Archive.Door.Contracts.Profile.Error (RuleError (..))
import Data.Aeson (Value (..))
import Data.Text (Text)

validateNamedFieldsRule :: Text -> [Text] -> Value -> Either RuleError ()
validateNamedFieldsRule label names (Object fields) = do
  requireExactKeys label names fields
  mapM_ (`requiredText` fields) names
validateNamedFieldsRule label _ _ = Left (RuleNotObject label)
