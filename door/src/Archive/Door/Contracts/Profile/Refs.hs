{-# LANGUAGE OverloadedStrings #-}

-- | The profile rules over a record's references alone:
-- @x-archive-unique-sorted-refs@, @x-archive-distinct-refs@, and
-- @x-archive-ordered-distinct-refs@, with the reference readers the span
-- rules share.
module Archive.Door.Contracts.Profile.Refs
  ( validateSortedRefsRule
  , validateSingleRoleRule
  , validateDistinctRefsRule
  , validateSortedRefsInstance
  , validateDistinctRefsInstance
  , validateOrderedDistinctRefsInstance
  , instanceReferences
  , referencesWithRole
  , refObject
  , validateRefRole
  ) where

import Archive.Door.Contracts.Fields (requireExactKeys, requiredString, requiredStrings, unique)
import Data.Aeson (Value (..))
import Data.Aeson.KeyMap qualified as KeyMap
import Data.Foldable (toList)
import Data.List (sort)
import Data.Text (Text)
import Data.Text qualified as Text

validateSortedRefsRule :: Value -> Either Text ()
validateSortedRefsRule (Object fields) = do
  requireExactKeys "unique sorted references rule" ["role"] fields
  _ <- requiredString "role" fields
  pure ()
validateSortedRefsRule _ = Left "unique sorted references rule must be an object"

validateSingleRoleRule :: Text -> Value -> Either Text ()
validateSingleRoleRule label (Object fields) = do
  requireExactKeys label ["role"] fields
  _ <- requiredString "role" fields
  pure ()
validateSingleRoleRule label _ = Left (label <> " must be an object")

validateDistinctRefsRule :: Value -> Either Text ()
validateDistinctRefsRule (Object fields) = do
  requireExactKeys "distinct references rule" ["roles"] fields
  names <- requiredStrings "roles" fields
  if length names >= 2 && length names == length (unique names)
    then Right ()
    else Left "distinct references rule must name at least two distinct roles"
validateDistinctRefsRule _ = Left "distinct references rule must be an object"

validateDistinctRefsInstance :: KeyMap.KeyMap Value -> KeyMap.KeyMap Value -> Either Text ()
validateDistinctRefsInstance rule instanceValue = do
  roles <- requiredStrings "roles" rule
  refs <- instanceReferences instanceValue
  objects <- concat <$> mapM (\role -> referencesWithRole role refs >>= mapM refObject) roles
  if length objects == length (unique objects)
    then Right ()
    else Left ("references in roles " <> Text.intercalate ", " roles <> " must name different objects")

validateOrderedDistinctRefsInstance :: KeyMap.KeyMap Value -> KeyMap.KeyMap Value -> Either Text ()
validateOrderedDistinctRefsInstance rule instanceValue = do
  role <- requiredString "role" rule
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
    refRole (Object fields) = requiredString "role" fields
    refRole _ = Left "contract instance reference must be an object"

refObject :: Value -> Either Text Text
refObject (Object fields) = requiredString "id" fields
refObject _ = Left "contract instance reference must be an object"

validateSortedRefsInstance :: KeyMap.KeyMap Value -> KeyMap.KeyMap Value -> Either Text ()
validateSortedRefsInstance rule instanceValue = do
  role <- requiredString "role" rule
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
  actual <- requiredString "role" fields
  if actual == expected then Right () else Left ("reference role must be " <> expected)
validateRefRole _ _ = Left "contract instance reference must be an object"

refKey :: Value -> Either Text (Text, Text)
refKey (Object fields) = (,) <$> requiredString "id" fields <*> requiredString "revision_id" fields
refKey _ = Left "contract instance reference must be an object"
