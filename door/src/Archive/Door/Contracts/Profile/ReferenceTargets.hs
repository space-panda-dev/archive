{-# LANGUAGE OverloadedStrings #-}

-- | @x-archive-reference-targets@: for each reference role, the shapes,
-- kinds, or contract a pinned record must have.
module Archive.Door.Contracts.Profile.ReferenceTargets
  ( ReferenceTargetRule (..)
  , referenceTargetRules
  , contractReferenceTargets
  , requireSingleClaimTarget
  ) where

import Archive.Door.Contracts.Fields (objectValue, requireExactKeys, requiredText, unique)
import Data.Aeson (Value (..))
import Data.Aeson.Key qualified as Key
import Data.Aeson.KeyMap qualified as KeyMap
import Data.Foldable (toList)
import Data.Text (Text)
import Data.Text qualified as Text

data ReferenceTargetRule = ReferenceTargetRule
  { -- | The accepted record shapes: one from `shape`, or a set from `shapes`.
    referenceTargetShapes :: [Text]
  , referenceTargetKind :: Maybe Text
  -- | A nonempty set of accepted Entity kinds (`kinds`), or empty when the
  -- rule names a single `kind` or none.
  , referenceTargetKinds :: [Text]
  , referenceTargetContract :: Maybe (Text, Text)
  , referenceTargetMany :: Bool
  -- | Alternatives (specification 0.0.9): a reference in the role must
  -- satisfy one of these rules, each a shape with its kind or contract.
  -- Empty for an ordinary rule.
  , referenceTargetAny :: [ReferenceTargetRule]
  } deriving (Eq, Show)

-- | The value of @x-archive-reference-targets@: each role's rule, refusing
-- an empty role.
referenceTargetRules :: Value -> Either Text [(Text, ReferenceTargetRule)]
referenceTargetRules (Object roles) = mapM validateRole (KeyMap.toList roles)
  where
    validateRole (role, value)
      | Text.null (Key.toText role) = Left "contract reference target role must not be empty"
      | otherwise = (,) (Key.toText role) <$> parseTargetRule True value
referenceTargetRules _ = Left "contract reference targets must be an object"

-- | One role's target rule, checked strictly. With @any@ (specification
-- 0.0.9) it lists at least two alternative rules, none of which may itself
-- name @any@ or @many@; @many@ then belongs to the role.
parseTargetRule :: Bool -> Value -> Either Text ReferenceTargetRule
parseTargetRule allowAny value = do
  fields <- objectValue "contract reference target rule" value
  if KeyMap.member "any" fields
    then do
      if allowAny then Right () else Left "contract reference target alternatives must not nest"
      requireExactKeys "contract reference target rule" (["any"] <> ["many" | KeyMap.member "many" fields]) fields
      alternatives <- case KeyMap.lookup "any" fields of
        Just (Array values) | length values >= 2 -> mapM (parseTargetRule False) (toList values)
        _ -> Left "contract reference target any must list at least two rules"
      if any referenceTargetMany alternatives
        then Left "contract reference target alternatives must not name many"
        else Right ()
      if length alternatives /= length (unique alternatives)
        then Left "contract reference target alternatives must differ"
        else Right ()
      many <- optionalMany fields
      pure ReferenceTargetRule
        { referenceTargetShapes = unique (concatMap referenceTargetShapes alternatives)
        , referenceTargetKind = Nothing
        , referenceTargetKinds = []
        , referenceTargetContract = Nothing
        , referenceTargetMany = many
        , referenceTargetAny = alternatives
        }
    else do
      shapes <- targetShapes fields
      kind <- case KeyMap.lookup "kind" fields of
        Nothing -> Right Nothing
        Just (String value') | not (Text.null value') -> Right (Just value')
        _ -> Left "contract reference target kind must be a non-empty string"
      kinds <- optionalKinds fields
      targetContract <- optionalTargetContract fields
      many <- optionalMany fields
      let expectedKeys = [if KeyMap.member "shapes" fields then "shapes" else "shape"]
            <> ["kind" | kind /= Nothing]
            <> ["kinds" | not (null kinds)]
            <> ["contract" | targetContract /= Nothing]
            <> ["many" | KeyMap.member "many" fields]
      requireExactKeys "contract reference target rule" expectedKeys fields
      if (kind /= Nothing || not (null kinds)) && shapes /= ["entity"]
        then Left "contract reference target kind is valid only for entity shape"
        else if kind /= Nothing && not (null kinds)
          then Left "contract reference target may name kind or kinds, not both"
          else if targetContract /= Nothing && shapes /= ["claim"]
            then Left "contract reference target contract is valid only for claim shape"
            else if many && kind /= Nothing
              then Left "repeated Entity kind rules are unsupported"
              else Right ()
      pure (ReferenceTargetRule shapes kind kinds targetContract many [])

-- | A target rule names one record shape in `shape`, or, for a role that may
-- pin records of several shapes, a set of at least two in `shapes`.
targetShapes :: KeyMap.KeyMap Value -> Either Text [Text]
targetShapes fields = case (KeyMap.lookup "shape" fields, KeyMap.lookup "shapes" fields) of
  (Just _, Just _) -> Left "contract reference target may name shape or shapes, not both"
  (Just (String shape), Nothing) -> (: []) <$> knownShape shape
  (Just _, Nothing) -> Left "contract reference target shape must be a string"
  (Nothing, Just (Array values)) -> do
    names <- mapM shapeName (toList values)
    if length names < 2 || length names /= length (unique names)
      then Left "contract reference target shapes must be a set of at least two shapes"
      else mapM knownShape names
  (Nothing, Just _) -> Left "contract reference target shapes must be an array"
  (Nothing, Nothing) -> Left "contract reference target rule requires shape or shapes"
  where
    shapeName (String name) = Right name
    shapeName _ = Left "contract reference target shapes must contain strings"
    knownShape shape
      | shape `elem` ["artifact", "assessment", "claim", "entity"] = Right shape
      | otherwise = Left "contract reference target shape must be artifact, assessment, claim, or entity"

optionalTargetContract :: KeyMap.KeyMap Value -> Either Text (Maybe (Text, Text))
optionalTargetContract fields = case KeyMap.lookup "contract" fields of
  Nothing -> Right Nothing
  Just (Object citation) -> do
    requireExactKeys "contract reference target citation" ["id", "version"] citation
    identifier <- requiredText "id" citation
    version <- requiredText "version" citation
    pure (Just (identifier, version))
  Just _ -> Left "contract reference target contract must be an object"

optionalKinds :: KeyMap.KeyMap Value -> Either Text [Text]
optionalKinds fields = case KeyMap.lookup "kinds" fields of
  Nothing -> Right []
  Just (Array values) -> do
    names <- mapM kindName (toList values)
    if null names || length names /= length (unique names)
      then Left "contract reference target kinds must be a nonempty set"
      else Right names
  Just _ -> Left "contract reference target kinds must be an array"
  where
    kindName (String value) | not (Text.null value) = Right value
    kindName _ = Left "contract reference target kinds must contain non-empty strings"

optionalMany :: KeyMap.KeyMap Value -> Either Text Bool
optionalMany fields = case KeyMap.lookup "many" fields of
  Nothing -> Right False
  Just (Bool value) -> Right value
  Just _ -> Left "contract reference target many must be a boolean"

-- | The rules that read a pinned Claim's payload or references need that
-- role to name exactly one Claim: its target rule says so.
requireSingleClaimTarget :: Text -> Value -> Text -> Either Text ()
requireSingleClaimTarget label schema role = do
  targets <- contractReferenceTargets schema
  case lookup role targets of
    Just rule | referenceTargetShapes rule == ["claim"] && not (referenceTargetMany rule) -> Right ()
    _ -> Left (label <> " role must have a single Claim reference target rule")

contractReferenceTargets :: Value -> Either Text [(Text, ReferenceTargetRule)]
contractReferenceTargets (Object fields) = case KeyMap.lookup (Key.fromText "x-archive-reference-targets") fields of
  Nothing -> Right []
  Just (Object roles) -> mapM (\(role, rule) -> (,) (Key.toText role) <$> parseTargetRule True rule)
    (KeyMap.toList roles)
  Just _ -> Left "contract reference targets must be an object"
contractReferenceTargets _ = Left "contract schema must be an object"
