{-# LANGUAGE OverloadedStrings #-}

-- | The first reader for specification-owned contract resources.
--
-- Contract bytes are evidence about the validator that will interpret a
-- claim.  This module therefore refuses malformed, non-canonical, changed,
-- open, or network-dependent bundles before a caller can use their schema.
--
-- The parts live by concept under "Archive.Door.Contracts": the schema
-- keyword checks and evaluator, the regular-expression subset, JSON
-- Pointers, the JSON-LD contexts, and the @x-archive-*@ profile rules. This
-- module loads a bundle through them and re-exports what callers use.
module Archive.Door.Contracts
  ( ContractBundle (..)
  , loadContract
  , loadCoreContext
  , validateContractBundle
  , validateCoreContext
  , validateCoreContextForSpecification
  , validateContractInstance
  , validateBundleInstance
  , ReferenceTargetRule (..)
  , contractReferenceTargets
  , distinctTrackContentRule
  , TargetMeasureRule (..)
  , targetMeasureRule
  , SameSubjectRule (..)
  , sameSubjectRule
  , SpanWindowRule (..)
  , spanWindowRule
  , TargetBoundRule (..)
  , targetBoundRule
  , TargetSamplesRule (..)
  , targetSamplesRule
  , TargetIndexRule (..)
  , targetIndexRule
  , pathIntegers
  ) where

import Archive.Door.Contracts.Context
  ( ContextError
  , renderContextError
  , validateContractContext
  )
import Archive.Door.Contracts.Context qualified as Context
import Archive.Door.Contracts.Evaluate (evaluateSchema, explainMismatch)
import Archive.Door.Contracts.Fields
  ( FieldError (..)
  , FromFieldError (..)
  , expect
  , objectValue
  , renderFieldError
  , requireExactKeys
  , requiredBool
  , requiredObject
  , requiredText
  , requiredValue
  )
import Archive.Door.Contracts.Profile
  ( ProfileRules (..)
  , checkProfile
  , parseProfileRules
  )
import Archive.Door.Contracts.Profile.Error
  ( RuleViolation
  , renderRuleError
  , renderRuleViolation
  )
import Archive.Door.Contracts.Profile.Paths qualified as Paths
import Archive.Door.Contracts.Profile.PinnedTargets
  ( SameSubjectRule (..)
  , SpanWindowRule (..)
  , TargetBoundRule (..)
  , TargetIndexRule (..)
  , TargetMeasureRule (..)
  , TargetSamplesRule (..)
  )
import Archive.Door.Contracts.Profile.PinnedTargets qualified as PinnedTargets
import Archive.Door.Contracts.Profile.ReferenceTargets (ReferenceTargetRule (..))
import Archive.Door.Contracts.Profile.ReferenceTargets qualified as ReferenceTargets
import Archive.Door.Contracts.Profile.Spans qualified as Spans
import Archive.Door.Contracts.Schema (walkSchema)
import Archive.Door.Contracts.SchemaError (SchemaError (..), renderSchemaError)
import Archive.Door.Hex (hexText)
import Archive.Door.Json (decodeUnique)
import Archive.Door.RecordRules (validateRecordRules)
import Archive.Door.RuleSyntax (validateReleaseRules)
import Archive.Door.WhenPinned (effectiveSchema)
import Archive.Door.Layout
  ( archiveRoot
  , contractPath
  , parseSha256Digest
  , specContextPath
  )
import Control.Exception (IOException, try)
import Crypto.Hash.SHA256 qualified as SHA256
import Data.Aeson (Value (..))
import Data.Aeson.KeyMap qualified as KeyMap
import Data.Aeson.RFC8785 (encodeCanonical)
import Data.Bifunctor (first)
import Data.ByteString.Lazy qualified as LazyByteString
import Data.Text (Text)
import Data.Text qualified as Text

data ContractBundle = ContractBundle
  { contractBundleId :: Text
  , contractBundleVersion :: Text
  , contractBundleSchema :: Value
  , contractBundleContext :: KeyMap.KeyMap Value
  , contractBundleDigest :: Text
  -- | The schema's profile rules, parsed when the bundle loaded. They are
  -- the rules of 'contractBundleSchema' as loaded; a caller that swaps in a
  -- record's effective schema keeps them, since they already hold each
  -- form's rules.
  , contractBundleRules :: ProfileRules
  }
  deriving (Eq, Show)

-- | Why a bundle, a context, or an instance is refused, carried as data
-- until a refusal leaves this module, where 'renderBundleError' writes the
-- text callers and the suites see.
data BundleError
  = BundleField FieldError
  | IdentifierMismatch
  | VersionMismatch
  | DigestMismatch
  | NotCanonical
  -- | The path, and the read's exception as 'show' gives it.
  | Unreadable FilePath Text
  | BundleSchema SchemaError
  | BundleContext ContextError
  | BundleViolation RuleViolation
  -- | The explanation 'explainMismatch' composed: text by construction.
  | InstanceMismatch Text
  -- | Text from Door's shared readers (the archive layout, the strict JSON
  -- decoder, @x-archive-when-pinned@, and the record rules), which keep
  -- their own wording across Door.
  | ForeignBundle Text

instance FromFieldError BundleError where
  fieldError = BundleField

renderBundleError :: BundleError -> Text
renderBundleError problem = case problem of
  BundleField field -> renderFieldError field
  IdentifierMismatch -> "contract identifier does not match citation"
  VersionMismatch -> "contract version does not match citation"
  DigestMismatch -> "contract bytes do not match cited digest"
  NotCanonical -> "contract bytes are not RFC 8785 canonical"
  Unreadable path shown -> "unable to read " <> Text.pack path <> ": " <> shown
  BundleSchema schema -> renderSchemaError schema
  BundleContext context -> renderContextError context
  BundleViolation violation -> renderRuleViolation violation
  InstanceMismatch reason -> "contract instance does not satisfy its schema: " <> reason
  ForeignBundle text -> text

-- | Load a cited bundle only through its digest-addressed path.  The bytes'
-- digest, canonical form, identifier, and version all have to agree with the
-- citation; a caller cannot redirect a citation to a differently named file.
loadContract :: FilePath -> Text -> Text -> Text -> IO (Either Text ContractBundle)
loadContract root expectedId expectedVersion digestText = do
  case (archiveRoot root, parseSha256Digest digestText) of
    (Left problem, _) -> pure (Left problem)
    (_, Left problem) -> pure (Left problem)
    (Right archive, Right digest) -> do
      let path = contractPath archive digest
      result <- readBytes path
      pure (first renderBundleError (result >>= validateContractBytes expectedId expectedVersion digestText))

-- | Load the specification's local core context.  It is not digest-addressed,
-- but it is still parsed strictly and rejected if its local profile is not the
-- one used by the accepted core protocol.
loadCoreContext :: FilePath -> Text -> IO (Either Text Value)
loadCoreContext root specification = do
  case archiveRoot root >>= (`specContextPath` specification) of
    Left problem -> pure (Left problem)
    Right path ->
      readBytes path >>= pure . first renderBundleError . (>>= decodeAndValidateCoreFor specification)

decodeAndValidateCoreFor :: Text -> LazyByteString.ByteString -> Either BundleError Value
decodeAndValidateCoreFor specification bytes = do
  value <- decode bytes
  first BundleContext (Context.validateCoreContextForSpecification specification value)
  pure value

validateCoreContext :: Value -> Either Text ()
validateCoreContext = first renderContextError . Context.validateCoreContext

validateCoreContextForSpecification :: Text -> Value -> Either Text ()
validateCoreContextForSpecification specification =
  first renderContextError . Context.validateCoreContextForSpecification specification

validateContractBundle :: Text -> Text -> Text -> Value -> Either Text ContractBundle
validateContractBundle expectedId expectedVersion digestText value = do
  _ <- parseSha256Digest digestText
  first renderBundleError (validateBundleValue expectedId expectedVersion digestText value)

-- Kept separate from 'loadContract' so the byte-level checks are also usable
-- by pure tests and by archive initialization before a citation is written.
validateBundleValue :: Text -> Text -> Text -> Value -> Either BundleError ContractBundle
validateBundleValue expectedId expectedVersion digest value = do
  fields <- objectValue "contract bundle" value
  requireExactKeys "contract bundle" ["id", "version", "schema", "context"] fields
  bundleId <- requiredText "id" fields
  bundleVersion <- requiredText "version" fields
  if bundleId /= expectedId
    then Left IdentifierMismatch
    else if bundleVersion /= expectedVersion
      then Left VersionMismatch
      else pure ()
  schema <- requiredValue "schema" fields
  rules <- first BundleSchema (validateSchema schema)
  context <- requiredObject "context" fields
  first BundleContext (validateContractContext context)
  pure ContractBundle
    { contractBundleId = bundleId
    , contractBundleVersion = bundleVersion
    , contractBundleSchema = schema
    , contractBundleContext = context
    , contractBundleDigest = digest
    , contractBundleRules = rules
    }

-- | Check a schema and parse its profile rules. A schema with
-- @x-archive-when-pinned@ is checked once more for each form the rule makes,
-- and keeps each form's rules.
validateSchema :: Value -> Either SchemaError ProfileRules
validateSchema schema@(Object fields) = do
  marker <- requiredText "$schema" fields
  expect "JSON Schema marker" "https://json-schema.org/draft/2020-12/schema" marker
  additional <- requiredBool "additionalProperties" fields
  if additional then Left SchemaOpen else pure ()
  rules <- first BadRule (parseProfileRules fields)
  forms <- validateReleaseRules ForeignSchema nestedRules fields
  walkSchema schema [] schema
  pure $ case forms of
    Nothing -> ProfileRules rules
    Just (role, pinned, unpinned) -> PinnedForms role (formRules pinned) (formRules unpinned)
  where
    -- A form's schema has no when-pinned rule of its own (nesting is
    -- refused), so its rules are a plain list.
    formRules (ProfileRules form) = form
    formRules (PinnedForms _ form _) = form
validateSchema _ = Left (fieldError (NotJsonObject "contract schema"))

-- | The schema one map of @x-archive-when-pinned@ makes (D-18), checked as
-- a bundle's schema is.
nestedRules :: KeyMap.KeyMap Value -> Either SchemaError ProfileRules
nestedRules = validateSchema . Object

-- | Validate the instance object that a cited contract owns.  The first
-- argument is the installed JSON Schema and the second is exactly
-- @{"payload": ..., "refs": ...}@.  Schema errors are refused rather than
-- treated as a non-match, which keeps an incomplete evaluator fail-closed.
validateContractInstance :: Value -> Value -> Either Text ()
validateContractInstance schema instanceValue = first renderBundleError $ do
  rules <- first BundleSchema (validateSchema schema)
  checkContractInstance schema rules instanceValue

-- | 'validateContractInstance' for a loaded bundle, whose schema was checked
-- and whose rules were parsed when it loaded.
validateBundleInstance :: ContractBundle -> Value -> Either Text ()
validateBundleInstance bundle =
  first renderBundleError . checkContractInstance (contractBundleSchema bundle) (contractBundleRules bundle)

checkContractInstance :: Value -> ProfileRules -> Value -> Either BundleError ()
checkContractInstance schema rules instanceValue = do
  matches <- first BundleSchema (evaluateSchema schema [] schema instanceValue)
  if not matches
    then do
      reason <- first BundleSchema (explainMismatch schema [] [] schema instanceValue)
      Left (InstanceMismatch reason)
    else do
      effective <- first ForeignBundle (effectiveSchema schema instanceValue)
      first BundleViolation (checkProfile rules instanceValue)
      case (effective, instanceValue) of
        (Object root, Object fields) -> first ForeignBundle (validateRecordRules root fields)
        _ -> Right ()

-- The profile rules' readers, for admission, with their refusals written as
-- text as every refusal leaving this module is.

contractReferenceTargets :: Value -> Either Text [(Text, ReferenceTargetRule)]
contractReferenceTargets = first renderRuleError . ReferenceTargets.contractReferenceTargets

distinctTrackContentRule :: Value -> Value -> Either Text (Maybe (Text, [Int]))
distinctTrackContentRule schema = first renderRuleViolation . Spans.distinctTrackContentRule schema

targetMeasureRule :: Value -> Either Text (Maybe TargetMeasureRule)
targetMeasureRule = first renderRuleError . PinnedTargets.targetMeasureRule

sameSubjectRule :: Value -> Either Text (Maybe SameSubjectRule)
sameSubjectRule = first renderRuleError . PinnedTargets.sameSubjectRule

spanWindowRule :: Value -> Either Text (Maybe SpanWindowRule)
spanWindowRule = first renderRuleError . PinnedTargets.spanWindowRule

targetBoundRule :: Value -> Either Text (Maybe TargetBoundRule)
targetBoundRule = first renderRuleError . PinnedTargets.targetBoundRule

targetSamplesRule :: Value -> Either Text (Maybe TargetSamplesRule)
targetSamplesRule = first renderRuleError . PinnedTargets.targetSamplesRule

targetIndexRule :: Value -> Either Text (Maybe TargetIndexRule)
targetIndexRule = first renderRuleError . PinnedTargets.targetIndexRule

pathIntegers :: [Text] -> KeyMap.KeyMap Value -> Either Text [Integer]
pathIntegers path = first renderRuleViolation . Paths.pathIntegers path

decode :: LazyByteString.ByteString -> Either BundleError Value
decode bytes = case decodeUnique bytes of
  Left problem -> Left (ForeignBundle (Text.pack problem))
  Right value -> Right value

readBytes :: FilePath -> IO (Either BundleError LazyByteString.ByteString)
readBytes path = do
  result <- try (LazyByteString.readFile path) :: IO (Either IOException LazyByteString.ByteString)
  pure $ case result of
    Left problem -> Left (Unreadable path (Text.pack (show problem)))
    Right bytes -> Right bytes

validateContractBytes :: Text -> Text -> Text -> LazyByteString.ByteString -> Either BundleError ContractBundle
validateContractBytes expectedId expectedVersion digestText bytes = do
  _ <- first ForeignBundle (parseSha256Digest digestText)
  value <- decode bytes
  let canonical = encodeCanonical value
      actualDigest = "sha256:" <> hexText (SHA256.hashlazy bytes)
  if actualDigest /= digestText
    then Left DigestMismatch
    else if canonical /= bytes
      then Left NotCanonical
      else validateBundleValue expectedId expectedVersion digestText value
