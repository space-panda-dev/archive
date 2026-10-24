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
import Archive.Door.Contracts.Profile (validateContractProfile)
import Archive.Door.Contracts.Profile.Music
  ( validateBarBeatsRule
  , validateMetricModulationsRule
  , validatePitchStacksRule
  )
import Archive.Door.Contracts.Profile.Paths (pathIntegers)
import Archive.Door.Contracts.Profile.Payload
  ( validateAscendingItemsRule
  , validateDocumentOrderRule
  , validateMonotonicAnchorsRule
  , validateNestedRangesRule
  , validateOrderedInstantsRule
  , validatePartialDatesRule
  , validateSubsetRule
  )
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
  , validateReferenceTargetRules
  )
import Archive.Door.Contracts.Profile.Refs
  ( validateDistinctRefsRule
  , validateSingleRoleRule
  , validateSortedRefsRule
  )
import Archive.Door.Contracts.Profile.Spans
  ( distinctTrackContentRule
  , validateOrderedSpansRule
  , validateTrackSpansRule
  )
import Archive.Door.Contracts.Schema (walkSchema)
import Archive.Door.Hex (hexText)
import Archive.Door.Json (decodeUnique)
import Archive.Door.RecordRules (validateRecordRules)
import Archive.Door.RuleSyntax (validateReleaseRules)
import Archive.Door.SpanRules (sequentialSpansRule)
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
import Data.Aeson.Key qualified as Key
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
  validateSchema schema
  context <- requiredObject "context" fields
  validateContractContext context
  pure ContractBundle
    { contractBundleId = bundleId
    , contractBundleVersion = bundleVersion
    , contractBundleSchema = schema
    , contractBundleContext = context
    , contractBundleDigest = digest
    }

validateSchema :: Value -> Either Text ()
validateSchema schema@(Object fields) = do
  marker <- requiredText "$schema" fields
  expect "JSON Schema marker" "https://json-schema.org/draft/2020-12/schema" marker
  additional <- requiredBool "additionalProperties" fields
  if additional then Left "contract schema must be closed" else pure ()
  maybe (Right ()) validateReferenceTargetRules
    (KeyMap.lookup (Key.fromText "x-archive-reference-targets") fields)
  maybe (Right ()) validateOrderedSpansRule
    (KeyMap.lookup "x-archive-ordered-sample-spans" fields)
  maybe (Right ()) validateSortedRefsRule
    (KeyMap.lookup "x-archive-unique-sorted-refs" fields)
  maybe (Right ()) validateTrackSpansRule
    (KeyMap.lookup "x-archive-track-sample-spans" fields)
  maybe (Right ()) (validateSingleRoleRule "distinct track content rule")
    (KeyMap.lookup "x-archive-distinct-track-content" fields)
  maybe (Right ()) validateMonotonicAnchorsRule
    (KeyMap.lookup "x-archive-monotonic-anchors" fields)
  maybe (Right ()) validateDistinctRefsRule
    (KeyMap.lookup "x-archive-distinct-refs" fields)
  maybe (Right ()) (validateSingleRoleRule "ordered distinct references rule")
    (KeyMap.lookup "x-archive-ordered-distinct-refs" fields)
  case (KeyMap.member "x-archive-distinct-track-content" fields, KeyMap.member "x-archive-track-sample-spans" fields) of
    (True, False) -> Left "distinct track content rule requires a track sample spans rule"
    _ -> Right ()
  maybe (Right ()) validateOrderedInstantsRule
    (KeyMap.lookup "x-archive-ordered-instants" fields)
  maybe (Right ()) (fmap (const ()) . sequentialSpansRule)
    (KeyMap.lookup "x-archive-sequential-spans" fields)
  _ <- targetMeasureRule schema
  _ <- sameSubjectRule schema
  _ <- spanWindowRule schema
  maybe (Right ()) validateAscendingItemsRule
    (KeyMap.lookup "x-archive-ascending-items" fields)
  maybe (Right ()) validatePitchStacksRule
    (KeyMap.lookup "x-archive-pitch-stacks" fields)
  maybe (Right ()) validateBarBeatsRule
    (KeyMap.lookup "x-archive-bar-beats" fields)
  maybe (Right ()) validateMetricModulationsRule
    (KeyMap.lookup "x-archive-metric-modulations" fields)
  _ <- targetBoundRule schema
  maybe (Right ()) validatePartialDatesRule
    (KeyMap.lookup "x-archive-partial-dates" fields)
  maybe (Right ()) validateSubsetRule (KeyMap.lookup "x-archive-subset" fields)
  maybe (Right ()) validateDocumentOrderRule (KeyMap.lookup "x-archive-document-order" fields)
  maybe (Right ()) validateNestedRangesRule (KeyMap.lookup "x-archive-nested-ranges" fields)
  _ <- targetSamplesRule schema
  _ <- targetIndexRule schema
  validateReleaseRules nestedRules fields
  walkSchema schema [] schema
validateSchema _ = Left "contract schema must be a JSON object"

-- | The schema one map of @x-archive-when-pinned@ makes (D-18), checked as
-- a bundle's schema is.
nestedRules :: KeyMap.KeyMap Value -> Either Text ()
nestedRules = validateSchema . Object

-- | Validate the instance object that a cited contract owns.  The first
-- argument is the installed JSON Schema and the second is exactly
-- @{"payload": ..., "refs": ...}@.  Schema errors are refused rather than
-- treated as a non-match, which keeps an incomplete evaluator fail-closed.
validateContractInstance :: Value -> Value -> Either Text ()
validateContractInstance schema instanceValue = do
  validateSchema schema
  matches <- evaluateSchema schema [] schema instanceValue
  if not matches
    then do
      reason <- explainMismatch schema [] [] schema instanceValue
      Left ("contract instance does not satisfy its schema: " <> reason)
    else do
      effective <- effectiveSchema schema instanceValue
      validateContractProfile effective instanceValue
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
