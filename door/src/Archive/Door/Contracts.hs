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
  ( validateContractContext
  , validateCoreContext
  , validateCoreContextForSpecification
  )
import Archive.Door.Contracts.Evaluate (evaluateSchema, explainMismatch)
import Archive.Door.Contracts.Fields
  ( expect
  , objectValue
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
import Archive.Door.Contracts.Profile.Paths (pathIntegers)
import Archive.Door.Contracts.Profile.PinnedTargets
  ( SameSubjectRule (..)
  , SpanWindowRule (..)
  , TargetBoundRule (..)
  , TargetIndexRule (..)
  , TargetMeasureRule (..)
  , TargetSamplesRule (..)
  , sameSubjectRule
  , spanWindowRule
  , targetBoundRule
  , targetIndexRule
  , targetMeasureRule
  , targetSamplesRule
  )
import Archive.Door.Contracts.Profile.ReferenceTargets
  ( ReferenceTargetRule (..)
  , contractReferenceTargets
  )
import Archive.Door.Contracts.Profile.Spans (distinctTrackContentRule)
import Archive.Door.Contracts.Schema (walkSchema)
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
      pure (result >>= validateContractBytes expectedId expectedVersion digestText)

-- | Load the specification's local core context.  It is not digest-addressed,
-- but it is still parsed strictly and rejected if its local profile is not the
-- one used by the accepted core protocol.
loadCoreContext :: FilePath -> Text -> IO (Either Text Value)
loadCoreContext root specification = do
  case archiveRoot root >>= (`specContextPath` specification) of
    Left problem -> pure (Left problem)
    Right path -> readBytes path >>= pure . (>>= decodeAndValidateCoreFor specification)

decodeAndValidateCoreFor :: Text -> LazyByteString.ByteString -> Either Text Value
decodeAndValidateCoreFor specification bytes = do
  value <- decode bytes
  validateCoreContextForSpecification specification value
  pure value

validateContractBundle :: Text -> Text -> Text -> Value -> Either Text ContractBundle
validateContractBundle expectedId expectedVersion digestText value = do
  _ <- parseSha256Digest digestText
  validateBundleValue expectedId expectedVersion digestText value

-- Kept separate from 'loadContract' so the byte-level checks are also usable
-- by pure tests and by archive initialization before a citation is written.
validateBundleValue :: Text -> Text -> Text -> Value -> Either Text ContractBundle
validateBundleValue expectedId expectedVersion digest value = do
  fields <- objectValue "contract bundle" value
  requireExactKeys "contract bundle" ["id", "version", "schema", "context"] fields
  bundleId <- requiredText "id" fields
  bundleVersion <- requiredText "version" fields
  if bundleId /= expectedId
    then Left "contract identifier does not match citation"
    else if bundleVersion /= expectedVersion
      then Left "contract version does not match citation"
      else pure ()
  schema <- requiredValue "schema" fields
  rules <- validateSchema schema
  context <- requiredObject "context" fields
  validateContractContext context
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
validateSchema :: Value -> Either Text ProfileRules
validateSchema schema@(Object fields) = do
  marker <- requiredText "$schema" fields
  expect "JSON Schema marker" "https://json-schema.org/draft/2020-12/schema" marker
  additional <- requiredBool "additionalProperties" fields
  if additional then Left "contract schema must be closed" else pure ()
  rules <- parseProfileRules fields
  forms <- validateReleaseRules nestedRules fields
  walkSchema schema [] schema
  pure $ case forms of
    Nothing -> ProfileRules rules
    Just (role, pinned, unpinned) -> PinnedForms role (formRules pinned) (formRules unpinned)
  where
    -- A form's schema has no when-pinned rule of its own (nesting is
    -- refused), so its rules are a plain list.
    formRules (ProfileRules form) = form
    formRules (PinnedForms _ form _) = form
validateSchema _ = Left "contract schema must be a JSON object"

-- | The schema one map of @x-archive-when-pinned@ makes (D-18), checked as
-- a bundle's schema is.
nestedRules :: KeyMap.KeyMap Value -> Either Text ProfileRules
nestedRules = validateSchema . Object

-- | Validate the instance object that a cited contract owns.  The first
-- argument is the installed JSON Schema and the second is exactly
-- @{"payload": ..., "refs": ...}@.  Schema errors are refused rather than
-- treated as a non-match, which keeps an incomplete evaluator fail-closed.
validateContractInstance :: Value -> Value -> Either Text ()
validateContractInstance schema instanceValue = do
  rules <- validateSchema schema
  checkContractInstance schema rules instanceValue

-- | 'validateContractInstance' for a loaded bundle, whose schema was checked
-- and whose rules were parsed when it loaded.
validateBundleInstance :: ContractBundle -> Value -> Either Text ()
validateBundleInstance bundle =
  checkContractInstance (contractBundleSchema bundle) (contractBundleRules bundle)

checkContractInstance :: Value -> ProfileRules -> Value -> Either Text ()
checkContractInstance schema rules instanceValue = do
  matches <- evaluateSchema schema [] schema instanceValue
  if not matches
    then do
      reason <- explainMismatch schema [] [] schema instanceValue
      Left ("contract instance does not satisfy its schema: " <> reason)
    else do
      effective <- effectiveSchema schema instanceValue
      checkProfile rules instanceValue
      case (effective, instanceValue) of
        (Object root, Object fields) -> validateRecordRules root fields
        _ -> Right ()

decode :: LazyByteString.ByteString -> Either Text Value
decode bytes = case decodeUnique bytes of
  Left problem -> Left (Text.pack problem)
  Right value -> Right value

readBytes :: FilePath -> IO (Either Text LazyByteString.ByteString)
readBytes path = do
  result <- try (LazyByteString.readFile path) :: IO (Either IOException LazyByteString.ByteString)
  pure $ case result of
    Left problem -> Left ("unable to read " <> Text.pack path <> ": " <> Text.pack (show problem))
    Right bytes -> Right bytes

validateContractBytes :: Text -> Text -> Text -> LazyByteString.ByteString -> Either Text ContractBundle
validateContractBytes expectedId expectedVersion digestText bytes = do
  _ <- parseSha256Digest digestText
  value <- decode bytes
  let canonical = encodeCanonical value
      actualDigest = "sha256:" <> hexText (SHA256.hashlazy bytes)
  if actualDigest /= digestText
    then Left "contract bytes do not match cited digest"
    else if canonical /= bytes
      then Left "contract bytes are not RFC 8785 canonical"
      else validateBundleValue expectedId expectedVersion digestText value
