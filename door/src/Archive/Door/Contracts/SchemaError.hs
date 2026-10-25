{-# LANGUAGE OverloadedStrings #-}

-- | Why a contract's schema is refused, while its bundle loads or while an
-- instance is evaluated against it.
--
-- The values carry the schema path, keyword, and requirement, so a caller
-- tells refusals apart without matching text; 'renderSchemaError' writes
-- the text the suites compare where a refusal leaves
-- "Archive.Door.Contracts".
module Archive.Door.Contracts.SchemaError
  ( SchemaError (..)
  , Requirement (..)
  , renderSchemaError
  , schemaPath
  ) where

import Archive.Door.Contracts.Fields (FieldError, FromFieldError (..), renderFieldError)
import Archive.Door.Contracts.Pattern (PatternError, renderPatternError)
import Archive.Door.Contracts.Profile.Error (RuleError, renderRuleError)
import Data.Text (Text)
import Data.Text qualified as Text

data SchemaError
  = SchemaField FieldError
  | SchemaOpen
  | ProfileRuleBelowRoot
  -- | A node, at this reversed schema path, that is no object.
  | NodeNotObject [Text]
  -- | A node's reversed schema path and the keywords it may not use.
  | UnsupportedKeywords [Text] [Text]
  | UnresolvedRef
  | RemoteRef
  | UnsupportedType
  | TypeArrayDuplicates
  | ThenElseWithoutIf
  | ContainsBoundsWithoutContains
  | MinContainsExceedsMax
  -- | A keyword whose value does not meet the requirement.
  | KeywordMust Text Requirement
  | BadPattern PatternError
  | BadRule RuleError
  -- | Text from "Archive.Door.SchemaObjectKeywords" and
  -- "Archive.Door.RuleSyntax", which Door's record rules share and which
  -- keep their own wording.
  | ForeignSchema Text
  deriving (Eq, Show)

instance FromFieldError SchemaError where
  fieldError = SchemaField

-- | What a keyword's value must be.
data Requirement
  = BeString
  | BeStringOrArray
  | BeArray
  | BeNonEmptyArray
  | BeObject
  | BeSchemaObject
  | BeBoolean
  | BeBooleanOrSchema
  | BeNonNegativeInteger
  | BeInteger
  | BeNumber
  | BeDateTime
  | ContainOnlyStrings
  | ContainUniqueStrings
  | NotBeEmpty
  deriving (Eq, Show)

renderSchemaError :: SchemaError -> Text
renderSchemaError problem = case problem of
  SchemaField field -> renderFieldError field
  SchemaOpen -> "contract schema must be closed"
  ProfileRuleBelowRoot -> "contract profile rules are allowed only at the schema root"
  NodeNotObject path -> schemaPath path <> " must be an object"
  UnsupportedKeywords path names ->
    schemaPath path <> " contains unsupported keyword(s): " <> Text.intercalate ", " names
  UnresolvedRef -> "contract schema contains an unresolved local $ref"
  RemoteRef -> "contract schema contains a remote $ref"
  UnsupportedType -> "contract schema has an unsupported type"
  TypeArrayDuplicates -> "contract schema type array has duplicates"
  ThenElseWithoutIf -> "contract schema then/else require if"
  ContainsBoundsWithoutContains -> "contract schema minContains/maxContains require contains"
  MinContainsExceedsMax -> "contract schema minContains exceeds maxContains"
  KeywordMust keyword requirement -> "contract schema " <> keyword <> " must " <> renderRequirement requirement
  BadPattern pattern' -> renderPatternError pattern'
  BadRule rule -> renderRuleError rule
  ForeignSchema text -> text

renderRequirement :: Requirement -> Text
renderRequirement requirement = case requirement of
  BeString -> "be a string"
  BeStringOrArray -> "be a string or array"
  BeArray -> "be an array"
  BeNonEmptyArray -> "be a nonempty array"
  BeObject -> "be an object"
  BeSchemaObject -> "be a schema object"
  BeBoolean -> "be a boolean"
  BeBooleanOrSchema -> "be boolean or schema"
  BeNonNegativeInteger -> "be a non-negative integer"
  BeInteger -> "be an integer"
  BeNumber -> "be a number"
  BeDateTime -> "be date-time"
  ContainOnlyStrings -> "contain only strings"
  ContainUniqueStrings -> "contain unique strings"
  NotBeEmpty -> "not be empty"

-- | A place in a schema, from its reversed path, as refusals name it.
schemaPath :: [Text] -> Text
schemaPath [] = "contract schema"
schemaPath path = "contract schema at " <> Text.intercalate "." (reverse path)
