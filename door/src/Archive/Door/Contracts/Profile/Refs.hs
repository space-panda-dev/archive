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
import Data.Aeson (Value (..))
import Data.Aeson.KeyMap qualified as KeyMap
import Data.Foldable (toList)
import Data.List (sort)
import Data.Text (Text)
import Data.Text qualified as Text

-- | A rule that names one reference role and nothing else
-- (@x-archive-unique-sorted-refs@, @x-archive-distinct-track-content@,
-- @x-archive-ordered-distinct-refs@): the role.
roleRule :: Text -> Value -> Either Text Text
roleRule label (Object fields) = do
  requireExactKeys label ["role"] fields
  requiredText "role" fields
roleRule label _ = Left (label <> " must be an object")

-- | @x-archive-distinct-refs@: at least two distinct roles whose references
-- must name different objects.
distinctRefsRule :: Value -> Either Text [Text]
distinctRefsRule (Object fields) = do
  requireExactKeys "distinct references rule" ["roles"] fields
  names <- requiredStrings "roles" fields
  if length names >= 2 && length names == length (unique names)
    then Right names
    else Left "distinct references rule must name at least two distinct roles"
distinctRefsRule _ = Left "distinct references rule must be an object"

checkDistinctRefs :: [Text] -> KeyMap.KeyMap Value -> Either Text ()
checkDistinctRefs roles instanceValue = do
  refs <- instanceReferences instanceValue
  objects <- concat <$> mapM (\role -> referencesWithRole role refs >>= mapM refObject) roles
  if length objects == length (unique objects)
    then Right ()
    else Left ("references in roles " <> Text.intercalate ", " roles <> " must name different objects")

checkOrderedDistinctRefs :: Text -> KeyMap.KeyMap Value -> Either Text ()
checkOrderedDistinctRefs role instanceValue = do
  refs <- instanceReferences instanceValue
  objects <- referencesWithRole role refs >>= mapM refObject
  if length objects == length (unique objects)
    then Right ()
    else Left (role <> " references must name different objects")

referencesWithRole :: Text -> [Value] -> Either Text [Value]
referencesWithRole role refs = do
  roles <- mapM refRole refs
  pure [ref | (ref, actual) <- zip refs roles, actual == role]
  where
    refRole (Object fields) = requiredText "role" fields
    refRole _ = Left "contract instance reference must be an object"

refObject :: Value -> Either Text Text
refObject (Object fields) = requiredText "id" fields
refObject _ = Left "contract instance reference must be an object"

checkSortedRefs :: Text -> KeyMap.KeyMap Value -> Either Text ()
checkSortedRefs role instanceValue = do
  refs <- instanceReferences instanceValue
  mapM_ (validateRefRole role) refs
  keys <- mapM refKey refs
  if not (null keys) && keys == sort (unique keys) && length keys == length (unique keys)
    then Right ()
    else Left "references must be nonempty, unique, and sorted by object and revision"

instanceReferences :: KeyMap.KeyMap Value -> Either Text [Value]
instanceReferences instanceValue = case KeyMap.lookup "refs" instanceValue of
  Just (Array values) -> Right (toList values)
  _ -> Left "contract instance refs must be an array"

validateRefRole :: Text -> Value -> Either Text ()
validateRefRole expected (Object fields) = do
  actual <- requiredText "role" fields
  if actual == expected then Right () else Left ("reference role must be " <> expected)
validateRefRole _ _ = Left "contract instance reference must be an object"

refKey :: Value -> Either Text (Text, Text)
refKey (Object fields) = (,) <$> requiredText "id" fields <*> requiredText "revision_id" fields
refKey _ = Left "contract instance reference must be an object"
