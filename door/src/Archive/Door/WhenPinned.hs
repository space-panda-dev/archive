{-# LANGUAGE OverloadedStrings #-}

-- | @x-archive-when-pinned@ (D-18): a contract whose record has two forms,
-- such as meter layers version 2, timed when it pins a @source@ and untimed
-- otherwise, applies one map of earlier profile rules to each form.
--
-- Door resolves the rule before evaluating any other: the record's
-- effective schema is the bundle's, without the rule, with the chosen map's
-- rules at its root. Every evaluator then reads the effective schema as it
-- reads any bundle, so a nested rule means exactly what it means at a root.
module Archive.Door.WhenPinned (effectiveSchema) where

import Archive.Door.RuleJson (InstanceRef (..), instanceRefs)
import Archive.Door.RuleSyntax (WhenPinnedRule (..), ruleAt, whenPinnedRule)
import Data.Aeson (Value (..))
import Data.Aeson.KeyMap qualified as KeyMap
import Data.Text (Text)

-- | The schema a record is checked under: @schema@ with its when-pinned
-- rule replaced by the rules it selects for @instanceValue@
-- (@{payload, refs}@). A schema without the rule is returned as it is.
effectiveSchema :: Value -> Value -> Either Text Value
effectiveSchema schema@(Object root) (Object instanceValue) = do
  found <- ruleAt "x-archive-when-pinned" whenPinnedRule root
  pure $ case found of
    Nothing -> schema
    Just rule ->
      let pinned = any ((== whenPinnedRole rule) . instanceRefRole) (instanceRefs instanceValue)
          chosen = if pinned then whenPinnedThen rule else whenPinnedElse rule
      in Object (KeyMap.union chosen (KeyMap.delete "x-archive-when-pinned" root))
effectiveSchema schema _ = Right schema
