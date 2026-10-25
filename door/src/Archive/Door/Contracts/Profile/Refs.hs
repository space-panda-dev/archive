{-# LANGUAGE OverloadedStrings #-}

-- | The profile rules over a record's references alone:
-- @x-archive-unique-sorted-refs@, @x-archive-distinct-refs@, and
-- @x-archive-ordered-distinct-refs@, with the reference readers the span
-- rules share.
module Archive.Door.Contracts.Profile.Refs
  ( roleRule
  , distinctRefsRule
  , checkSortedRefs
  , checkDistinctRefs
  , checkOrderedDistinctRefs
  , instanceReferences
  , referencesWithRole
  , refObject
  , validateRefRole
  ) where

import Archive.Door.Contracts.Fields (requireExactKeys, requiredText, requiredStrings, unique)
import Archive.Door.Contracts.Profile.Error (RuleError (..), RuleViolation (..))
import Data.Aeson (Value (..))
import Data.Aeson.KeyMap qualified as KeyMap
import Data.Foldable (toList)
import Data.List (sort)
import Data.Text (Text)

-- | A rule that names one reference role and nothing else
-- (@x-archive-unique-sorted-refs@, @x-archive-distinct-track-content@,
-- @x-archive-ordered-distinct-refs@): the role.
roleRule :: Text -> Value -> Either RuleError Text
roleRule label (Object fields) = do
  requireExactKeys label ["role"] fields
  requiredText "role" fields
roleRule label _ = Left (RuleNotObject label)

-- | @x-archive-distinct-refs@: at least two distinct roles whose references
-- must name different objects.
distinctRefsRule :: Value -> Either RuleError [Text]
distinctRefsRule (Object fields) = do
  requireExactKeys "distinct references rule" ["roles"] fields
  names <- requiredStrings "roles" fields
  if length names >= 2 && length names == length (unique names)
    then Right names
    else Left TooFewDistinctRoles
distinctRefsRule _ = Left (RuleNotObject "distinct references rule")

checkDistinctRefs :: [Text] -> KeyMap.KeyMap Value -> Either RuleViolation ()
checkDistinctRefs roles instanceValue = do
  refs <- instanceReferences instanceValue
  objects <- concat <$> mapM (\role -> referencesWithRole role refs >>= mapM refObject) roles
  if length objects == length (unique objects)
    then Right ()
    else Left (RolesShareObject roles)

checkOrderedDistinctRefs :: Text -> KeyMap.KeyMap Value -> Either RuleViolation ()
checkOrderedDistinctRefs role instanceValue = do
  refs <- instanceReferences instanceValue
  objects <- referencesWithRole role refs >>= mapM refObject
  if length objects == length (unique objects)
    then Right ()
    else Left (RoleRepeatsObject role)

referencesWithRole :: Text -> [Value] -> Either RuleViolation [Value]
referencesWithRole role refs = do
  roles <- mapM refRole refs
  pure [ref | (ref, actual) <- zip refs roles, actual == role]
  where
    refRole (Object fields) = requiredText "role" fields
    refRole _ = Left (NotObject "contract instance reference")

refObject :: Value -> Either RuleViolation Text
refObject (Object fields) = requiredText "id" fields
refObject _ = Left (NotObject "contract instance reference")

checkSortedRefs :: Text -> KeyMap.KeyMap Value -> Either RuleViolation ()
checkSortedRefs role instanceValue = do
  refs <- instanceReferences instanceValue
  mapM_ (validateRefRole role) refs
  keys <- mapM refKey refs
  if not (null keys) && keys == sort (unique keys) && length keys == length (unique keys)
    then Right ()
    else Left RefsNotSorted

instanceReferences :: KeyMap.KeyMap Value -> Either RuleViolation [Value]
instanceReferences instanceValue = case KeyMap.lookup "refs" instanceValue of
  Just (Array values) -> Right (toList values)
  _ -> Left RefsNotArray

validateRefRole :: Text -> Value -> Either RuleViolation ()
validateRefRole expected (Object fields) = do
  actual <- requiredText "role" fields
  if actual == expected then Right () else Left (WrongRole expected)
validateRefRole _ _ = Left (NotObject "contract instance reference")

refKey :: Value -> Either RuleViolation (Text, Text)
refKey (Object fields) = (,) <$> requiredText "id" fields <*> requiredText "revision_id" fields
refKey _ = Left (NotObject "contract instance reference")
