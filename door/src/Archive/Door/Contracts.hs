{-# LANGUAGE OverloadedStrings #-}

-- | The first reader for specification-owned contract resources.
--
-- Contract bytes are evidence about the validator that will interpret a
-- claim.  This module therefore refuses malformed, non-canonical, changed,
-- open, or network-dependent bundles before a caller can use their schema.
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

import Archive.Door.Json (decodeUnique)
import Archive.Door.RecordRules (validateRecordRules)
import Archive.Door.RuleSyntax (release011RuleNames, releaseRuleNames, validateReleaseRules)
import Archive.Door.SpanRules (sequentialSpansRule, validateSequentialSpans, withinGroups)
import Archive.Door.WhenPinned (effectiveSchema)
import Archive.Door.SchemaObjectKeywords
  ( evaluateObjectKeywords
  , objectKeywordNames
  , objectKeywordSchemas
  , validateObjectKeywordTypes
  )
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
import Data.ByteString qualified as ByteString
import Data.ByteString.Lazy qualified as LazyByteString
import Data.Foldable (toList)
import Data.List (sort)
import Data.Text (Text)
import Data.Text qualified as Text
import Data.Scientific (floatingOrInteger)
import qualified Data.Set as Set
import Data.Time (Day, UTCTime, ZonedTime, defaultTimeLocale, fromGregorian, fromGregorianValid, gregorianMonthLength, parseTimeM)

data ContractBundle = ContractBundle
  { contractBundleId :: Text
  , contractBundleVersion :: Text
  , contractBundleSchema :: Value
  , contractBundleContext :: KeyMap.KeyMap Value
  , contractBundleDigest :: Text
  }
  deriving (Eq, Show)

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

validateCoreContext :: Value -> Either Text ()
validateCoreContext (Object outer) = do
  requireExactKeys "core context" ["@context"] outer
  inner <- requiredObject "@context" outer
  rejectContextDirectives "core context" inner
  let actualKeys = Set.fromList (map Key.toText (KeyMap.keys inner))
  keys <- case filter ((== actualKeys) . Set.fromList) [creditContextKeys, reviewContextKeys, worldContextKeys, coreContextKeys] of
    catalog : _ -> Right catalog
    [] -> Left "core context has an unknown term catalog"
  requireExactKeys "core context terms" keys inner
  expectJsonLdVersion inner
  expectProtected inner
  mapM_ (checkCoreString inner)
    [ ("arch", "urn:uuid:3e8b29f8-70c9-403e-bb4c-2280d59eca7f#")
    , ("as", "http://www.w3.org/ns/activitystreams#")
    , ("dcterms", "http://purl.org/dc/terms/")
    , ("prov", "http://www.w3.org/ns/prov#")
    , ("rdf", "http://www.w3.org/1999/02/22-rdf-syntax-ns#")
    , ("rdfs", "http://www.w3.org/2000/01/rdf-schema#")
    , ("xsd", "http://www.w3.org/2001/XMLSchema#")
    , ("spec", "arch:spec"), ("revision_id", "arch:revision_id")
    , ("object_id", "arch:object_id"), ("by", "as:generator")
    , ("agent", "dcterms:creator"), ("payload", "arch:payload")
    , ("refs", "arch:refs"), ("supersedes", "arch:supersedes")
    , ("source", "arch:source"), ("contracts", "arch:contracts")
    , ("contract", "arch:contract"), ("reference", "arch:reference")
    , ("archive_id", "arch:archive_id"), ("layout", "arch:layout")
    , ("artifact", "arch:Artifact"), ("entity", "arch:Entity")
    , ("claim", "arch:Claim"), ("assessment", "arch:Assessment")
    , ("label", "rdfs:label"), ("person", "prov:Person")
    , ("organisation", "prov:Organization"), ("software", "prov:SoftwareAgent")
    , ("model", "arch:ModelAgent"), ("process", "arch:ProcessAgent")
    , ("id", "arch:id"), ("namespace", "arch:namespace")
    , ("name", "arch:name"), ("revision", "arch:revision")
    , ("content", "arch:content"), ("digest", "arch:digest")
    , ("size", "arch:size"), ("schema", "arch:schema")
    , ("version", "arch:version"), ("protocol", "arch:protocol")
    , ("records", "arch:records"), ("identities", "arch:identities")
    , ("contents", "arch:contents"), ("expected_heads", "arch:expected_heads")
    , ("expected", "arch:expected"), ("annex_key", "arch:annex_key")
    , ("staging_remote", "arch:staging_remote")
    ]
  case KeyMap.lookup (Key.fromText "skos") inner of
    Nothing -> Right ()
    Just _ -> checkCoreString inner ("skos", "http://www.w3.org/2004/02/skos/core#")
  case KeyMap.lookup (Key.fromText "musical_work") inner of
    Nothing -> Right ()
    Just _ -> checkCoreString inner ("musical_work", "arch:MusicalWork")
  case KeyMap.lookup (Key.fromText "recording_session") inner of
    Nothing -> Right ()
    Just _ -> checkCoreString inner ("recording_session", "arch:RecordingSession")
  case KeyMap.lookup (Key.fromText "release") inner of
    Nothing -> Right ()
    Just _ -> checkCoreString inner ("release", "arch:Release")
  case KeyMap.lookup (Key.fromText "recording") inner of
    Nothing -> Right ()
    Just _ -> checkCoreString inner ("recording", "arch:Recording")
  requiredString "arch" inner >>= expect "Archive namespace" "urn:uuid:3e8b29f8-70c9-403e-bb4c-2280d59eca7f#"
  requiredString "by" inner >>= expect "by mapping" "as:generator"
  requiredString "agent" inner >>= expect "agent mapping" "dcterms:creator"
  validateCoreObjectMapping inner "at" "prov:generatedAtTime" "xsd:dateTime"
  validateCoreObjectMapping inner "shape" "arch:shape" "@vocab"
  validateCoreObjectMapping inner "kind" "rdf:type" "@vocab"
  validateCoreObjectMapping inner "role" "arch:role" "@vocab"
  mapM_ (requiredKey inner)
    [ "spec", "revision_id", "object_id", "shape", "payload", "refs"
    , "supersedes", "source", "contracts", "artifact", "entity", "claim"
    , "assessment", "label", "id", "content", "digest", "size"
    ]
  pure ()
validateCoreContext _ = Left "core context must be a JSON object"

validateCoreContextForSpecification :: Text -> Value -> Either Text ()
validateCoreContextForSpecification specification value = do
  validateCoreContext value
  context <- objectValue "core context" value >>= requiredObject "@context"
  let keys = Set.fromList (map Key.toText (KeyMap.keys context))
      expected = case specification of
        "archive/0.0.1" -> Set.fromList coreContextKeys
        "archive/0.0.2" -> Set.fromList coreContextKeys
        "archive/0.0.3" -> Set.fromList worldContextKeys
        "archive/0.0.4" -> Set.fromList worldContextKeys
        "archive/0.0.5" -> Set.fromList reviewContextKeys
        "archive/0.0.6" -> Set.fromList reviewContextKeys
        "archive/0.0.7" -> Set.fromList reviewContextKeys
        "archive/0.0.8" -> Set.fromList reviewContextKeys
        "archive/0.0.9" -> Set.fromList creditContextKeys
        "archive/0.0.10" -> Set.fromList creditContextKeys
        "archive/0.0.11" -> Set.fromList creditContextKeys
        _ -> Set.empty
  if not (Set.null expected) && keys == expected
    then Right ()
    else Left "core context terms do not match the selected specification version"

coreContextKeys :: [Text]
coreContextKeys =
  [ "@version", "@protected", "arch", "as", "dcterms", "prov", "rdf", "rdfs", "xsd"
  , "spec", "revision_id", "object_id", "shape", "by", "agent", "at", "payload"
  , "refs", "supersedes", "source", "contracts", "contract", "reference", "archive_id"
  , "layout", "artifact", "entity", "claim", "assessment", "kind", "label", "person"
  , "organisation", "software", "model", "process", "role", "id", "namespace", "name"
  , "revision", "content", "digest", "size", "schema", "version", "protocol", "records"
  , "identities", "contents", "expected_heads", "expected", "annex_key", "staging_remote"
  ]

worldContextKeys :: [Text]
worldContextKeys = coreContextKeys <> ["skos", "musical_work"]

reviewContextKeys :: [Text]
reviewContextKeys = worldContextKeys <> ["recording_session"]

-- | Specification 0.0.9 adds the Entity kinds @release@ and @recording@
-- (ADR 0078).
creditContextKeys :: [Text]
creditContextKeys = reviewContextKeys <> ["release", "recording"]

expectJsonLdVersion :: KeyMap.KeyMap Value -> Either Text ()
expectJsonLdVersion fields = case KeyMap.lookup "@version" fields of
  Just (Number value) | value == 1.1 -> Right ()
  _ -> Left "core context @version must be 1.1"

expectProtected :: KeyMap.KeyMap Value -> Either Text ()
expectProtected fields = case KeyMap.lookup "@protected" fields of
  Just (Bool True) -> Right ()
  _ -> Left "core context @protected must be true"

checkCoreString :: KeyMap.KeyMap Value -> (Text, Text) -> Either Text ()
checkCoreString fields (name, expected) =
  requiredString name fields >>= expect (name <> " mapping") expected

validateCoreObjectMapping :: KeyMap.KeyMap Value -> Text -> Text -> Text -> Either Text ()
validateCoreObjectMapping fields name expectedId expectedType = do
  mapping <- requiredObject name fields
  requireExactKeys (name <> " mapping") ["@id", "@type"] mapping
  requiredString "@id" mapping >>= expect (name <> " @id") expectedId
  requiredString "@type" mapping >>= expect (name <> " @type") expectedType

-- Kept separate from 'loadContract' so the byte-level checks are also usable
-- by pure tests and by archive initialization before a citation is written.
validateBundleValue :: Text -> Text -> Text -> Value -> Either Text ContractBundle
validateBundleValue expectedId expectedVersion digest value = do
  fields <- objectValue "contract bundle" value
  requireExactKeys "contract bundle" ["id", "version", "schema", "context"] fields
  bundleId <- requiredString "id" fields
  bundleVersion <- requiredString "version" fields
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
  marker <- requiredString "$schema" fields
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

profileRuleNames :: [Text]
profileRuleNames =
  [ "x-archive-reference-targets", "x-archive-ordered-sample-spans", "x-archive-unique-sorted-refs"
  , "x-archive-track-sample-spans", "x-archive-distinct-track-content"
  , "x-archive-monotonic-anchors", "x-archive-distinct-refs", "x-archive-ordered-distinct-refs"
  , "x-archive-ordered-instants", "x-archive-sequential-spans", "x-archive-target-measure"
  , "x-archive-same-subject", "x-archive-span-window"
  , "x-archive-ascending-items", "x-archive-pitch-stacks", "x-archive-bar-beats"
  , "x-archive-metric-modulations", "x-archive-target-bound", "x-archive-partial-dates"
  , "x-archive-subset", "x-archive-document-order", "x-archive-nested-ranges"
  , "x-archive-target-samples", "x-archive-target-index"
  ] <> releaseRuleNames <> release011RuleNames

-- | The schema one map of @x-archive-when-pinned@ makes (D-18), checked as
-- a bundle's schema is.
nestedRules :: KeyMap.KeyMap Value -> Either Text ()
nestedRules = validateSchema . Object

walkSchema :: Value -> [Text] -> Value -> Either Text ()
walkSchema root path (Object fields) = do
  if not (null path) && any ((`KeyMap.member` fields) . Key.fromText) profileRuleNames
    then Left "contract profile rules are allowed only at the schema root"
    else pure ()
  rejectUnknownSchemaKeywords path fields
  validateRef root fields
  validateSchemaKeywordTypes fields
  mapM_ (\(key, value) -> walkSchema root (Key.toText key : path) value)
    (nestedSchemas fields)
walkSchema _ path _ = Left (schemaPath path <> " must be an object")

-- The evaluator deliberately supports a small, closed subset of 2020-12.  A
-- bundle is specification-owned data, so silently accepting a new keyword
-- would be worse than refusing a bundle until the evaluator grows.
schemaKeywords :: [Text]
schemaKeywords =
  [ "$schema", "$defs", "$ref", "type", "const", "enum", "required"
  , "properties", "additionalProperties", "items", "minItems", "maxItems"
  , "uniqueItems", "contains", "minContains", "maxContains", "allOf", "anyOf", "not"
  , "if", "then", "else", "pattern"
  , "minLength", "maxLength", "format", "minimum", "maximum"
  , "$comment", "title", "description", "default", "deprecated"
  , "examples", "readOnly", "writeOnly"
  ] <> objectKeywordNames <> profileRuleNames

rejectUnknownSchemaKeywords :: [Text] -> KeyMap.KeyMap Value -> Either Text ()
rejectUnknownSchemaKeywords path fields =
  case filter (`notElem` schemaKeywords) (map (Key.toText . fst) (KeyMap.toList fields)) of
    [] -> Right ()
    names -> Left (schemaPath path <> " contains unsupported keyword(s): "
      <> Text.intercalate ", " names)

validateReferenceTargetRules :: Value -> Either Text ()
validateReferenceTargetRules (Object roles) = mapM_ validateRole (KeyMap.toList roles)
  where
    validateRole (role, value)
      | Text.null (Key.toText role) = Left "contract reference target role must not be empty"
      | otherwise = () <$ parseTargetRule True value
validateReferenceTargetRules _ = Left "contract reference targets must be an object"

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
    identifier <- requiredString "id" citation
    version <- requiredString "version" citation
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

validateOrderedSpansRule :: Value -> Either Text ()
validateOrderedSpansRule (Object fields) = do
  requireExactKeys "ordered sample spans rule" ["payload", "role", "start", "end", "rate"] fields
  mapM_ (`requiredString` fields) ["payload", "role", "start", "end", "rate"]
  pure ()
validateOrderedSpansRule _ = Left "ordered sample spans rule must be an object"

validateSortedRefsRule :: Value -> Either Text ()
validateSortedRefsRule (Object fields) = do
  requireExactKeys "unique sorted references rule" ["role"] fields
  _ <- requiredString "role" fields
  pure ()
validateSortedRefsRule _ = Left "unique sorted references rule must be an object"

validateTrackSpansRule :: Value -> Either Text ()
validateTrackSpansRule (Object fields) = do
  let names = ["payload", "pieces", "role", "start", "end", "rate"]
  requireExactKeys "track sample spans rule" names fields
  mapM_ (`requiredString` fields) names
validateTrackSpansRule _ = Left "track sample spans rule must be an object"

validateSingleRoleRule :: Text -> Value -> Either Text ()
validateSingleRoleRule label (Object fields) = do
  requireExactKeys label ["role"] fields
  _ <- requiredString "role" fields
  pure ()
validateSingleRoleRule label _ = Left (label <> " must be an object")

validateMonotonicAnchorsRule :: Value -> Either Text ()
validateMonotonicAnchorsRule (Object fields) = do
  requireExactKeys "monotonic anchors rule" ["payload", "fields"] fields
  _ <- requiredString "payload" fields
  names <- requiredStrings "fields" fields
  if length names == 2 && length (unique names) == 2
    then Right ()
    else Left "monotonic anchors rule must name two distinct fields"
validateMonotonicAnchorsRule _ = Left "monotonic anchors rule must be an object"

validateDistinctRefsRule :: Value -> Either Text ()
validateDistinctRefsRule (Object fields) = do
  requireExactKeys "distinct references rule" ["roles"] fields
  names <- requiredStrings "roles" fields
  if length names >= 2 && length names == length (unique names)
    then Right ()
    else Left "distinct references rule must name at least two distinct roles"
validateDistinctRefsRule _ = Left "distinct references rule must be an object"

validateNamedFieldsRule :: Text -> [Text] -> Value -> Either Text ()
validateNamedFieldsRule label names (Object fields) = do
  requireExactKeys label names fields
  mapM_ (`requiredString` fields) names
validateNamedFieldsRule label _ _ = Left (label <> " must be an object")

validateOrderedInstantsRule :: Value -> Either Text ()
validateOrderedInstantsRule value = do
  validateNamedFieldsRule "ordered instants rule" ["start", "end"] value
  case value of
    Object fields -> do
      start <- requiredString "start" fields
      end <- requiredString "end" fields
      if start == end then Left "ordered instants rule must name two distinct fields" else Right ()
    _ -> Left "ordered instants rule must be an object"

-- | @x-archive-target-measure@: the record pins one Claim in @role@ whose
-- payload holds integer fields @rate@ and @count@.  The record's own payload
-- @rate@ MUST equal the target's, and every element of the payload array
-- @payload@ MUST have an integer @end@ no greater than the target's @count@.
--
-- With @within@ (specification 0.0.9), @payload@ is an array inside each
-- element of the payload array @within@, and every element of every such
-- array is measured.
data TargetMeasureRule = TargetMeasureRule
  { targetMeasureRole :: Text
  , targetMeasureRate :: Text
  , targetMeasureCount :: Text
  , targetMeasurePayload :: Text
  , targetMeasureEnd :: Text
  , targetMeasureWithin :: Maybe Text
  -- | With @optional@ true, a record whose payload lacks @payload@ (or
  -- @within@) is not measured.
  , targetMeasureOptional :: Bool
  } deriving (Eq, Show)

targetMeasureRule :: Value -> Either Text (Maybe TargetMeasureRule)
targetMeasureRule schema@(Object fields) = case KeyMap.lookup "x-archive-target-measure" fields of
  Nothing -> Right Nothing
  Just value -> do
    let names = ["role", "rate", "count", "payload", "end"]
    validateWithinFieldsRule "target measure rule" names value
    rule <- objectValue "target measure rule" value
    optional <- optionalFlag rule
    role <- requiredString "role" rule
    rate <- requiredString "rate" rule
    count <- requiredString "count" rule
    payload <- requiredString "payload" rule
    end <- requiredString "end" rule
    within <- optionalWithin rule
    requireSingleClaimTarget "target measure rule" schema role
    pure (Just (TargetMeasureRule role rate count payload end within optional))
targetMeasureRule _ = Left "contract schema must be an object"

-- | @x-archive-same-subject@: the Claim pinned in @role@ has exactly one
-- @subject@ reference, and it MUST pin the same object and revision as this
-- record's one @equals@ reference.
data SameSubjectRule = SameSubjectRule
  { sameSubjectRole :: Text
  , sameSubjectSubject :: Text
  , sameSubjectEquals :: Text
  -- | With @optional@ true, a record with no reference in @role@ is not
  -- compared.
  , sameSubjectOptional :: Bool
  } deriving (Eq, Show)

sameSubjectRule :: Value -> Either Text (Maybe SameSubjectRule)
sameSubjectRule schema@(Object fields) = case KeyMap.lookup "x-archive-same-subject" fields of
  Nothing -> Right Nothing
  Just value -> do
    let names = ["role", "subject", "equals"]
    validateOptionalFieldsRule "same subject rule" names value
    rule <- objectValue "same subject rule" value
    optional <- optionalFlag rule
    role <- requiredString "role" rule
    subject <- requiredString "subject" rule
    equals <- requiredString "equals" rule
    if role == equals
      then Left "same subject rule must compare two different roles"
      else Right ()
    requireSingleClaimTarget "same subject rule" schema role
    pure (Just (SameSubjectRule role subject equals optional))
sameSubjectRule _ = Left "contract schema must be an object"

-- | @x-archive-span-window@: the record pins one Claim in @role@ whose
-- payload holds an integer @rate@ field and an array @spans@ of objects with
-- integer @start@ and @end@ fields.  The record's own payload @rate@ MUST
-- equal the target's, and its payload object @payload@ is a window whose
-- @start@ MUST be the @start@ of some element of @spans@ and whose @end@ MUST
-- be the @end@ of that element or of a later one: a run of whole spans.
data SpanWindowRule = SpanWindowRule
  { spanWindowRole :: Text
  , spanWindowRate :: Text
  , spanWindowPayload :: Text
  , spanWindowStart :: Text
  , spanWindowEnd :: Text
  , spanWindowSpans :: Text
  } deriving (Eq, Show)

spanWindowRule :: Value -> Either Text (Maybe SpanWindowRule)
spanWindowRule schema@(Object fields) = case KeyMap.lookup "x-archive-span-window" fields of
  Nothing -> Right Nothing
  Just value -> do
    let names = ["role", "rate", "payload", "start", "end", "spans"]
    validateNamedFieldsRule "span window rule" names value
    rule <- objectValue "span window rule" value
    role <- requiredString "role" rule
    rate <- requiredString "rate" rule
    payload <- requiredString "payload" rule
    start <- requiredString "start" rule
    end <- requiredString "end" rule
    spans <- requiredString "spans" rule
    if start == end
      then Left "span window rule must name two distinct bound fields"
      else Right ()
    requireSingleClaimTarget "span window rule" schema role
    pure (Just (SpanWindowRule role rate payload start end spans))
spanWindowRule _ = Left "contract schema must be an object"

-- | A rule with named fields that may also name @within@ (specification
-- 0.0.9): a payload array whose elements each hold the rule's array.
validateWithinFieldsRule :: Text -> [Text] -> Value -> Either Text ()
validateWithinFieldsRule label names value@(Object fields) =
  validateOptionalFieldsRule label (names <> ["within" | KeyMap.member "within" fields]) value
validateWithinFieldsRule label _ _ = Left (label <> " must be an object")

-- | A rule with named fields that may also say @optional@: true
-- (specification 0.0.9), so that it applies only to records that have what
-- it reads. @optional@ false is refused: absence says it.
validateOptionalFieldsRule :: Text -> [Text] -> Value -> Either Text ()
validateOptionalFieldsRule label names value@(Object fields) = do
  validateNamedFieldsRule label names (Object (KeyMap.delete "optional" fields))
  _ <- optionalFlag fields
  pure (const () value)
validateOptionalFieldsRule label _ _ = Left (label <> " must be an object")

optionalFlag :: KeyMap.KeyMap Value -> Either Text Bool
optionalFlag rule = case KeyMap.lookup "optional" rule of
  Nothing -> Right False
  Just (Bool True) -> Right True
  Just _ -> Left "a rule's optional must be true when present"

optionalWithin :: KeyMap.KeyMap Value -> Either Text (Maybe Text)
optionalWithin rule = case KeyMap.lookup "within" rule of
  Nothing -> Right Nothing
  Just _ -> Just <$> requiredString "within" rule

-- Paths (specification 0.0.9). A path is a nonempty array of field names
-- read from the payload; every array met on the way is entered element by
-- element, so ["spans", "readings", "tones"] reaches the tones of every
-- reading of every span. A missing field reaches nothing: whether it must
-- be present is the schema's question, not the path's.

payloadPath :: Text -> Value -> Either Text [Text]
payloadPath label (Array values) = case mapM name (toList values) of
  Right names | not (null names) -> Right names
  _ -> Left (label <> " must be a nonempty array of field names")
  where
    name (String field) | not (Text.null field) = Right field
    name _ = Left (label <> " must contain non-empty field names")
payloadPath label _ = Left (label <> " must be a nonempty array of field names")

payloadPaths :: Text -> Value -> Either Text [[Text]]
payloadPaths label (Array values) = do
  paths <- mapM (payloadPath label) (toList values)
  if length paths /= length (unique paths)
    then Left (label <> " must not repeat a path")
    else Right paths
payloadPaths label _ = Left (label <> " must be an array of paths")

requiredPath :: Text -> KeyMap.KeyMap Value -> Either Text [Text]
requiredPath name fields = case KeyMap.lookup (Key.fromText name) fields of
  Just value -> payloadPath name value
  Nothing -> Left (name <> " is required")

-- | The values a path's fields lead to, entering arrays on the way but not
-- the last value itself.
descend :: [Text] -> [Value] -> [Value]
descend [] values = values
descend (name : rest) values = descend rest (concatMap step values)
  where
    step (Object fields) = case KeyMap.lookup (Key.fromText name) fields of
      Just (Array items) | not (null rest) -> toList items
      Just value -> [value]
      Nothing -> []
    step _ = []

-- | The objects that hold a path's last field (or would): every object
-- reached by the path without its last name, entering arrays.
pathHolders :: [Text] -> KeyMap.KeyMap Value -> Either Text [KeyMap.KeyMap Value]
pathHolders path payload = mapM holder (enter (descend (init path) [Object payload]))
  where
    enter = concatMap (\value -> case value of
      Array items -> toList items
      _ -> [value])
    holder (Object fields) = Right fields
    holder _ = Left (Text.intercalate "." path <> " must be reached through objects")

-- | @x-archive-ascending-items@: @paths@ names arrays of the payload whose
-- items MUST strictly ascend: all integers in numeric order, or all strings
-- in code point order, and no item twice.
validateAscendingItemsRule :: Value -> Either Text ()
validateAscendingItemsRule (Object fields) = do
  requireExactKeys "ascending items rule" ["paths"] fields
  paths <- maybe (Left "ascending items rule requires paths") (payloadPaths "ascending items rule paths")
    (KeyMap.lookup "paths" fields)
  if null paths then Left "ascending items rule must name at least one path" else Right ()
validateAscendingItemsRule _ = Left "ascending items rule must be an object"

data Ordered = OrderedInteger Integer | OrderedText Text
  deriving (Eq, Ord)

validateAscendingItemsInstance :: KeyMap.KeyMap Value -> KeyMap.KeyMap Value -> Either Text ()
validateAscendingItemsInstance rule instanceValue = do
  paths <- maybe (Left "ascending items rule requires paths") (payloadPaths "ascending items rule paths")
    (KeyMap.lookup "paths" rule)
  payload <- requiredObject "payload" instanceValue
  mapM_ (checkPath payload) paths
  where
    checkPath payload path = do
      holders <- pathHolders path payload
      let named = Text.intercalate "." path
      mapM_ (\fields -> case KeyMap.lookup (Key.fromText (last path)) fields of
        Nothing -> Right ()
        Just (Array values) -> do
          items <- mapM (orderedItem named) (toList values)
          let sameKind = all isInteger items || not (any isInteger items)
          if sameKind && and (zipWith (<) items (drop 1 items))
            then Right ()
            else Left (named <> " must be distinct items in ascending order")
        Just _ -> Left (named <> " must be an array")) holders
    orderedItem _ (Number value) | Right integer <- (floatingOrInteger value :: Either Double Integer) =
      Right (OrderedInteger integer)
    orderedItem _ (String text) = Right (OrderedText text)
    orderedItem named _ = Left (named <> " must contain integers or strings")
    isInteger (OrderedInteger _) = True
    isInteger _ = False

-- | @x-archive-pitch-stacks@: every object @payload@ reaches that has a
-- @bass@ and @tones@ is a stack of pitch classes (0 to 11): @tones@ above the bass, and
-- optional @uncertain@ groups of @classes@ and a @voicing@ of MIDI notes.
-- No uncertain class is the bass, a tone, or in another group; the groups
-- are in ascending order of their classes; the voicing's lowest note is the
-- bass, and the classes of its other notes include every tone and nothing
-- outside the tones and uncertain classes.
validatePitchStacksRule :: Value -> Either Text ()
validatePitchStacksRule (Object fields) = do
  let names = ["bass", "tones", "uncertain", "classes", "voicing"]
  requireExactKeys "pitch stacks rule" ("payload" : names) fields
  _ <- requiredPath "payload" fields
  named <- mapM (`requiredString` fields) names
  if length named /= length (unique named)
    then Left "pitch stacks rule must name distinct fields"
    else Right ()
validatePitchStacksRule _ = Left "pitch stacks rule must be an object"

validatePitchStacksInstance :: KeyMap.KeyMap Value -> KeyMap.KeyMap Value -> Either Text ()
validatePitchStacksInstance rule instanceValue = do
  path <- requiredPath "payload" rule
  bassName <- requiredString "bass" rule
  tonesName <- requiredString "tones" rule
  uncertainName <- requiredString "uncertain" rule
  classesName <- requiredString "classes" rule
  voicingName <- requiredString "voicing" rule
  payload <- requiredObject "payload" instanceValue
  holders <- pathHolders (path <> [bassName]) payload
  -- A stack has a bass and its tones; a bass-only reading (specification
  -- 0.0.10) has a bass and no tones, and nothing for this rule to check.
  let stacks = filter (\fields -> KeyMap.member (Key.fromText bassName) fields
        && KeyMap.member (Key.fromText tonesName) fields) holders
  mapM_ (stack bassName tonesName uncertainName classesName voicingName) stacks
  where
    stack bassName tonesName uncertainName classesName voicingName fields = do
      bass <- requiredInteger bassName fields
      tones <- integers tonesName fields
      groups <- case KeyMap.lookup (Key.fromText uncertainName) fields of
        Nothing -> Right []
        Just (Array values) -> mapM (\group -> case group of
          Object groupFields -> integers classesName groupFields
          _ -> Left (uncertainName <> " must contain objects")) (toList values)
        Just _ -> Left (uncertainName <> " must be an array")
      let certain = Set.fromList (bass : tones)
          unsure = concat groups
      if Set.null (Set.fromList unsure `Set.intersection` certain) && length unsure == length (unique unsure)
        then Right ()
        else Left (uncertainName <> " must not name the bass, a tone, or a class of another group")
      if groups == sort groups
        then Right ()
        else Left (uncertainName <> " groups must be in ascending order of their " <> classesName)
      case KeyMap.lookup (Key.fromText voicingName) fields of
        Nothing -> Right ()
        Just _ -> do
          notes <- integers voicingName fields
          case notes of
            lowest : upper
              | lowest `mod` 12 /= bass -> Left (voicingName <> "'s lowest note must be the bass")
              | otherwise -> do
                  let above = Set.fromList (map (`mod` 12) upper)
                  if Set.fromList tones `Set.isSubsetOf` above
                      && above `Set.isSubsetOf` Set.fromList (tones <> unsure)
                    then Right ()
                    else Left (voicingName <> "'s upper notes must sound every tone and only tones or uncertain classes")
            [] -> Left (voicingName <> " must not be empty")
    integers name fields = case KeyMap.lookup (Key.fromText name) fields of
      Just (Array values) -> mapM (\item -> case item of
        Number value | Right integer <- (floatingOrInteger value :: Either Double Integer) -> Right integer
        _ -> Left (name <> " must contain integers")) (toList values)
      _ -> Left (name <> " must be an array")

-- | @x-archive-bar-beats@: every bar @payload@ reaches has an integer
-- @numerator@; its optional @grouping@ MUST sum to the numerator, and its
-- optional @beats@ MUST hold one sample per group (or per numerator unit
-- without a grouping), the first at the bar's @start@, each later than the
-- one before, and all before its @end@.
validateBarBeatsRule :: Value -> Either Text ()
validateBarBeatsRule (Object fields) = do
  let names = ["start", "end", "numerator", "grouping", "beats"]
  requireExactKeys "bar beats rule" ("payload" : names) fields
  _ <- requiredPath "payload" fields
  named <- mapM (`requiredString` fields) names
  if length named /= length (unique named)
    then Left "bar beats rule must name distinct fields"
    else Right ()
validateBarBeatsRule _ = Left "bar beats rule must be an object"

validateBarBeatsInstance :: KeyMap.KeyMap Value -> KeyMap.KeyMap Value -> Either Text ()
validateBarBeatsInstance rule instanceValue = do
  path <- requiredPath "payload" rule
  startName <- requiredString "start" rule
  endName <- requiredString "end" rule
  numeratorName <- requiredString "numerator" rule
  groupingName <- requiredString "grouping" rule
  beatsName <- requiredString "beats" rule
  payload <- requiredObject "payload" instanceValue
  let bars = concatMap (\value -> case value of
        Array items -> toList items
        _ -> []) (descend path [Object payload])
  mapM_ (\bar -> case bar of
    Object fields -> do
      start <- requiredInteger startName fields
      end <- requiredInteger endName fields
      numerator <- requiredInteger numeratorName fields
      count <- case KeyMap.lookup (Key.fromText groupingName) fields of
        Nothing -> Right numerator
        Just _ -> do
          groups <- integerArray groupingName fields
          if sum groups == numerator
            then Right (fromIntegral (length groups))
            else Left (groupingName <> " must add up to the bar's " <> numeratorName)
      case KeyMap.lookup (Key.fromText beatsName) fields of
        Nothing -> Right ()
        Just _ -> do
          beats <- integerArray beatsName fields
          if fromIntegral (length beats) /= count
            then Left (beatsName <> " must hold one sample per counted beat")
            else if take 1 beats /= [start]
              || not (and (zipWith (<) beats (drop 1 beats)))
              || any (>= end) beats
              then Left (beatsName <> " must start on the bar's downbeat and rise within the bar")
              else Right ()
    _ -> Left "bar must be an object") bars
  where
    integerArray name fields = case KeyMap.lookup (Key.fromText name) fields of
      Just (Array values) -> mapM (\item -> case item of
        Number value | Right integer <- (floatingOrInteger value :: Either Double Integer) -> Right integer
        _ -> Left (name <> " must contain integers")) (toList values)
      _ -> Left (name <> " must be an array")

-- | @x-archive-metric-modulations@: each element of the payload array
-- @payload@ joins bar @bar@ of layer @layer@ (positions in the payload
-- arrays @layers@ and, in that layer, @bars@; the layer MUST hold @bars@) to
-- the bar before it, which MUST end where it starts. Links are distinct and
-- in order of layer and bar. A link with both @pulse_before@ and
-- @pulse_after@ (a regrouping) MUST name note values (an integer
-- @denominator@ and optional @dots@) of different lengths: the pulse moves.
validateMetricModulationsRule :: Value -> Either Text ()
validateMetricModulationsRule value = do
  let names = [ "payload", "layers", "bars", "start", "end", "layer", "bar", "pulse_before", "pulse_after"
              , "denominator", "dots" ]
  validateNamedFieldsRule "metric modulations rule" names value
  case value of
    Object fields -> do
      named <- mapM (`requiredString` fields) names
      if length named /= length (unique named)
        then Left "metric modulations rule must name distinct fields"
        else Right ()
    _ -> Left "metric modulations rule must be an object"

validateMetricModulationsInstance :: KeyMap.KeyMap Value -> KeyMap.KeyMap Value -> Either Text ()
validateMetricModulationsInstance rule instanceValue = do
  payloadName <- requiredString "payload" rule
  layersName <- requiredString "layers" rule
  barsName <- requiredString "bars" rule
  startName <- requiredString "start" rule
  endName <- requiredString "end" rule
  layerName <- requiredString "layer" rule
  barName <- requiredString "bar" rule
  pulseBeforeName <- requiredString "pulse_before" rule
  pulseAfterName <- requiredString "pulse_after" rule
  denominatorName <- requiredString "denominator" rule
  dotsName <- requiredString "dots" rule
  payload <- requiredObject "payload" instanceValue
  links <- case KeyMap.lookup (Key.fromText payloadName) payload of
    Nothing -> Right []
    Just (Array values) -> Right (toList values)
    Just _ -> Left (payloadName <> " must be an array")
  layers <- withinGroups (Just layersName) payload
  places <- mapM (\link -> case link of
    Object fields -> do
      layer <- requiredInteger layerName fields
      bar <- requiredInteger barName fields
      bars <- case drop (fromIntegral layer) layers of
        chosen : _ | layer >= 0 -> case KeyMap.lookup (Key.fromText barsName) chosen of
          Just (Array values) -> Right (toList values)
          _ -> Left (payloadName <> " must join bars of a layer that holds " <> barsName)
        _ -> Left (layerName <> " must name a layer of the claim")
      (previous, this) <- case drop (fromIntegral bar - 1) bars of
        Object earlier : Object later : _ | bar >= 1 -> Right (earlier, later)
        _ -> Left (barName <> " must name a bar after the first of its layer")
      previousEnd <- requiredInteger endName previous
      thisStart <- requiredInteger startName this
      if previousEnd /= thisStart
        then Left (payloadName <> " must join bars that are adjacent")
        else Right ()
      case (KeyMap.lookup (Key.fromText pulseBeforeName) fields, KeyMap.lookup (Key.fromText pulseAfterName) fields) of
        (Just (Object before), Just (Object after)) -> do
          (beforeTop, beforeBottom) <- noteLength denominatorName dotsName before
          (afterTop, afterBottom) <- noteLength denominatorName dotsName after
          if beforeTop * afterBottom == afterTop * beforeBottom
            then Left (payloadName <> " must move the pulse to a note value of another length")
            else Right ()
        _ -> Right ()
      pure (layer, bar)
    _ -> Left (payloadName <> " must contain objects")) links
  if places == sort places && length places == length (unique places)
    then Right ()
    else Left (payloadName <> " must be distinct and in order of layer and bar")
  where
    -- A note value's length in whole notes as (numerator, denominator):
    -- (2^(dots+1) - 1) / (denominator * 2^dots).
    noteLength denominatorName dotsName fields = do
      denominator <- requiredInteger denominatorName fields
      dots <- case KeyMap.lookup (Key.fromText dotsName) fields of
        Nothing -> Right 0
        Just _ -> requiredInteger dotsName fields
      if denominator <= 0 || dots < 0
        then Left (denominatorName <> " and " <> dotsName <> " must be a note value")
        else Right (2 ^ (dots + 1) - 1, denominator * 2 ^ dots :: Integer)

-- | @x-archive-subset@: on every object the @payload@ path reaches, the
-- array @items@, when present, holds only values of its array @of@ (a beat
-- layer's downbeats among its beats).
validateSubsetRule :: Value -> Either Text ()
validateSubsetRule (Object fields) = do
  requireExactKeys "subset rule" ["payload", "items", "of"] fields
  _ <- requiredPath "payload" fields
  items <- requiredString "items" fields
  others <- requiredString "of" fields
  if items == others then Left "subset rule must name two different arrays" else Right ()
validateSubsetRule _ = Left "subset rule must be an object"

validateSubsetInstance :: KeyMap.KeyMap Value -> KeyMap.KeyMap Value -> Either Text ()
validateSubsetInstance rule instanceValue = do
  path <- requiredPath "payload" rule
  items <- requiredString "items" rule
  others <- requiredString "of" rule
  payload <- requiredObject "payload" instanceValue
  holders <- pathHolders (path <> [items]) payload
  mapM_ (\fields -> case (KeyMap.lookup (Key.fromText items) fields, KeyMap.lookup (Key.fromText others) fields) of
    (Nothing, _) -> Right ()
    (Just (Array chosen), Just (Array pool))
      | all (`elem` toList pool) (toList chosen) -> Right ()
    _ -> Left (items <> " must be among " <> others)) holders

-- | @x-archive-document-order@: the elements of the payload array @payload@
-- that have a @locator@ object never go backwards in the document: compared
-- by the locator's integer @line@ and then by the first of the @within@
-- fields it has (0 when none), each is at or after the one before.
validateDocumentOrderRule :: Value -> Either Text ()
validateDocumentOrderRule (Object fields) = do
  requireExactKeys "document order rule" ["payload", "locator", "line", "within"] fields
  mapM_ (`requiredString` fields) ["payload", "locator", "line"]
  within <- requiredStrings "within" fields
  if null within || length within /= length (unique within)
    then Left "document order rule must name distinct within fields"
    else Right ()
validateDocumentOrderRule _ = Left "document order rule must be an object"

validateDocumentOrderInstance :: KeyMap.KeyMap Value -> KeyMap.KeyMap Value -> Either Text ()
validateDocumentOrderInstance rule instanceValue = do
  payloadName <- requiredString "payload" rule
  locatorName <- requiredString "locator" rule
  lineName <- requiredString "line" rule
  within <- requiredStrings "within" rule
  payload <- requiredObject "payload" instanceValue
  elements <- case KeyMap.lookup (Key.fromText payloadName) payload of
    Just (Array values) -> Right (toList values)
    _ -> Left (payloadName <> " must be an array")
  positions <- concat <$> mapM (\element -> case element of
    Object fields -> case KeyMap.lookup (Key.fromText locatorName) fields of
      Nothing -> Right []
      Just (Object locator) -> do
        line <- requiredInteger lineName locator
        inner <- case [name | name <- within, KeyMap.member (Key.fromText name) locator] of
          name : _ -> requiredInteger name locator
          [] -> Right 0
        pure [(line, inner)]
      Just _ -> Left (locatorName <> " must be an object")
    _ -> Left (payloadName <> " must contain objects")) elements
  if and (zipWith (<=) positions (drop 1 positions))
    then Right ()
    else Left (payloadName <> " located in the document must not go backwards")

-- | @x-archive-nested-ranges@: each element of the payload array @payload@
-- is a range of positions in the payload array @of@ with integer @first@
-- and @last@: @first <= last@, and @last@ is a position of @of@. Ranges are
-- distinct and in order of first position, the longer of two with the same
-- first position first, and two ranges nest or are apart, never cross.
validateNestedRangesRule :: Value -> Either Text ()
validateNestedRangesRule value = do
  validateNamedFieldsRule "nested ranges rule" ["payload", "first", "last", "of"] value
  case value of
    Object fields -> do
      first <- requiredString "first" fields
      lastName <- requiredString "last" fields
      if first == lastName then Left "nested ranges rule must name two bound fields" else Right ()
    _ -> Left "nested ranges rule must be an object"

validateNestedRangesInstance :: KeyMap.KeyMap Value -> KeyMap.KeyMap Value -> Either Text ()
validateNestedRangesInstance rule instanceValue = do
  payloadName <- requiredString "payload" rule
  firstName <- requiredString "first" rule
  lastName <- requiredString "last" rule
  ofName <- requiredString "of" rule
  payload <- requiredObject "payload" instanceValue
  size <- case KeyMap.lookup (Key.fromText ofName) payload of
    Just (Array values) -> Right (fromIntegral (length values) :: Integer)
    _ -> Left (ofName <> " must be an array")
  ranges <- case KeyMap.lookup (Key.fromText payloadName) payload of
    Nothing -> Right []
    Just (Array values) -> mapM (\element -> case element of
      Object fields -> do
        first <- requiredInteger firstName fields
        lastPosition <- requiredInteger lastName fields
        if 0 <= first && first <= lastPosition && lastPosition < size
          then Right (first, lastPosition)
          else Left (payloadName <> " must be ranges of positions in " <> ofName)
      _ -> Left (payloadName <> " must contain objects")) (toList values)
    Just _ -> Left (payloadName <> " must be an array")
  let keys = [(first, negate lastPosition) | (first, lastPosition) <- ranges]
  if keys == sort keys && length keys == length (unique keys)
    then Right ()
    else Left (payloadName <> " must be distinct and in order of first position, outer before inner")
  let crossing (a, b) (c, d) = (c > a && c <= b && d > b) || (a > c && a <= d && b > d)
  if or [crossing x y | (index, x) <- zip [0 :: Int ..] ranges, y <- drop (index + 1) ranges]
    then Left (payloadName <> " must nest or be apart, never cross")
    else Right ()

-- | @x-archive-target-samples@: every integer the @paths@ reach MUST be
-- less than the integer @count@ of the Claim the record pins in @role@ (a
-- beat's sample within the pinned stream).
data TargetSamplesRule = TargetSamplesRule
  { targetSamplesRole :: Text
  , targetSamplesCount :: Text
  , targetSamplesPaths :: [[Text]]
  } deriving (Eq, Show)

targetSamplesRule :: Value -> Either Text (Maybe TargetSamplesRule)
targetSamplesRule schema@(Object fields) = case KeyMap.lookup "x-archive-target-samples" fields of
  Nothing -> Right Nothing
  Just value -> do
    rule <- objectValue "target samples rule" value
    requireExactKeys "target samples rule" ["role", "count", "paths"] rule
    role <- requiredString "role" rule
    count <- requiredString "count" rule
    paths <- maybe (Left "target samples rule requires paths") (payloadPaths "target samples rule paths")
      (KeyMap.lookup "paths" rule)
    if null paths then Left "target samples rule must name at least one path" else Right ()
    requireSingleClaimTarget "target samples rule" schema role
    pure (Just (TargetSamplesRule role count paths))
targetSamplesRule _ = Left "contract schema must be an object"

-- | The integers a path's last field holds, on every object it reaches: the
-- field's value, or each item of it when it is an array.
pathIntegers :: [Text] -> KeyMap.KeyMap Value -> Either Text [Integer]
pathIntegers path payload = do
  holders <- pathHolders path payload
  concat <$> mapM (\fields -> case KeyMap.lookup (Key.fromText (last path)) fields of
    Nothing -> Right []
    Just (Array values) -> mapM integerAt (toList values)
    Just value -> (: []) <$> integerAt value) holders
  where
    integerAt (Number value) | Right integer <- (floatingOrInteger value :: Either Double Integer) = Right integer
    integerAt _ = Left (Text.intercalate "." path <> " must hold integers")

-- | @x-archive-target-index@: every integer the @path@ reaches MUST be a
-- position in the payload array @array@ of the Claim the record pins in
-- @role@; a record with such integers MUST pin exactly one (a span's entry
-- in the chord sequence it was placed from).
data TargetIndexRule = TargetIndexRule
  { targetIndexRole :: Text
  , targetIndexPath :: [Text]
  , targetIndexArray :: Text
  } deriving (Eq, Show)

targetIndexRule :: Value -> Either Text (Maybe TargetIndexRule)
targetIndexRule schema@(Object fields) = case KeyMap.lookup "x-archive-target-index" fields of
  Nothing -> Right Nothing
  Just value -> do
    rule <- objectValue "target index rule" value
    requireExactKeys "target index rule" ["role", "path", "array"] rule
    role <- requiredString "role" rule
    path <- requiredPath "path" rule
    array <- requiredString "array" rule
    requireSingleClaimTarget "target index rule" schema role
    pure (Just (TargetIndexRule role path array))
targetIndexRule _ = Left "contract schema must be an object"

-- | @x-archive-target-bound@: when this record's payload has the integer
-- @field@, the record pins one Claim in @role@, and @field@ MUST NOT exceed
-- the integer @bound@ of that Claim's payload (a channel index within the
-- pinned stream's channel count).
data TargetBoundRule = TargetBoundRule
  { targetBoundRole :: Text
  , targetBoundField :: Text
  , targetBoundBound :: Text
  } deriving (Eq, Show)

targetBoundRule :: Value -> Either Text (Maybe TargetBoundRule)
targetBoundRule schema@(Object fields) = case KeyMap.lookup "x-archive-target-bound" fields of
  Nothing -> Right Nothing
  Just value -> do
    validateNamedFieldsRule "target bound rule" ["role", "field", "bound"] value
    rule <- objectValue "target bound rule" value
    role <- requiredString "role" rule
    field <- requiredString "field" rule
    bound <- requiredString "bound" rule
    requireSingleClaimTarget "target bound rule" schema role
    pure (Just (TargetBoundRule role field bound))
targetBoundRule _ = Left "contract schema must be an object"

-- | @x-archive-partial-dates@: each payload field in @fields@, when present,
-- begins with a calendar date written @YYYY@, @YYYY-MM@, or @YYYY-MM-DD@ (a
-- year from 1, a real month, a real day of it); what may follow is the
-- schema's pattern's. With @start@ and @end@, both among @fields@, the
-- last day @end@ can mean MUST NOT be before the first day @start@ can mean.
validatePartialDatesRule :: Value -> Either Text ()
validatePartialDatesRule (Object fields) = do
  let ordered = KeyMap.member "start" fields || KeyMap.member "end" fields
  requireExactKeys "partial dates rule" (["fields"] <> (if ordered then ["start", "end"] else [])) fields
  names <- requiredStrings "fields" fields
  if null names || length names /= length (unique names)
    then Left "partial dates rule must name distinct fields"
    else Right ()
  if ordered
    then do
      start <- requiredString "start" fields
      end <- requiredString "end" fields
      if start /= end && start `elem` names && end `elem` names
        then Right ()
        else Left "partial dates rule must order two of its fields"
    else Right ()
validatePartialDatesRule _ = Left "partial dates rule must be an object"

validatePartialDatesInstance :: KeyMap.KeyMap Value -> KeyMap.KeyMap Value -> Either Text ()
validatePartialDatesInstance rule instanceValue = do
  names <- requiredStrings "fields" rule
  payload <- requiredObject "payload" instanceValue
  let dateOf name = case KeyMap.lookup (Key.fromText name) payload of
        Nothing -> Right Nothing
        Just (String text) -> maybe (Left (name <> " must begin with a calendar date")) (Right . Just)
          (partialDate (Text.takeWhile (/= 'T') text))
        Just _ -> Left (name <> " must be text")
  mapM_ dateOf names
  case (KeyMap.lookup "start" rule, KeyMap.lookup "end" rule) of
    (Just (String start), Just (String end)) -> do
      from <- dateOf start
      to <- dateOf end
      case (from, to) of
        (Just (earliest, _), Just (_, latest)) | latest < earliest ->
          Left (end <> " must not be before " <> start)
        _ -> Right ()
    _ -> Right ()

-- | The first and last day a partial date can mean.
partialDate :: Text -> Maybe (Day, Day)
partialDate text = case Text.splitOn "-" text of
  [year] -> do
    y <- digits 4 year
    if y < 1 then Nothing else Just (fromGregorian y 1 1, fromGregorian y 12 31)
  [year, month] -> do
    y <- digits 4 year
    m <- digits 2 month
    first <- if y < 1 then Nothing else fromGregorianValid y (fromInteger m) 1
    pure (first, fromGregorian y (fromInteger m) (gregorianMonthLength y (fromInteger m)))
  [year, month, day] -> do
    y <- digits 4 year
    m <- digits 2 month
    d <- digits 2 day
    exact <- if y < 1 then Nothing else fromGregorianValid y (fromInteger m) (fromInteger d)
    pure (exact, exact)
  _ -> Nothing
  where
    digits count piece
      | Text.length piece == count && Text.all (\c -> '0' <= c && c <= '9') piece = Just (read (Text.unpack piece))
      | otherwise = Nothing

-- | The rules that read a pinned Claim's payload or references need that
-- role to name exactly one Claim: its target rule says so.
requireSingleClaimTarget :: Text -> Value -> Text -> Either Text ()
requireSingleClaimTarget label schema role = do
  targets <- contractReferenceTargets schema
  case lookup role targets of
    Just rule | referenceTargetShapes rule == ["claim"] && not (referenceTargetMany rule) -> Right ()
    _ -> Left (label <> " role must have a single Claim reference target rule")

requiredStrings :: Text -> KeyMap.KeyMap Value -> Either Text [Text]
requiredStrings name fields = case KeyMap.lookup (Key.fromText name) fields of
  Just (Array values) -> mapM one (toList values)
  _ -> Left (name <> " must be an array of strings")
  where
    one (String value) | not (Text.null value) = Right value
    one _ = Left (name <> " must contain non-empty strings")

contractReferenceTargets :: Value -> Either Text [(Text, ReferenceTargetRule)]
contractReferenceTargets (Object fields) = case KeyMap.lookup (Key.fromText "x-archive-reference-targets") fields of
  Nothing -> Right []
  Just (Object roles) -> mapM (\(role, rule) -> (,) (Key.toText role) <$> parseTargetRule True rule)
    (KeyMap.toList roles)
  Just _ -> Left "contract reference targets must be an object"
contractReferenceTargets _ = Left "contract schema must be an object"

validateRef :: Value -> KeyMap.KeyMap Value -> Either Text ()
validateRef root fields = case KeyMap.lookup "$ref" fields of
  Nothing -> Right ()
  Just (String ref)
    | Text.isPrefixOf "#" ref -> case resolvePointer root ref of
        Just _ -> Right ()
        Nothing -> Left "contract schema contains an unresolved local $ref"
    | otherwise -> Left "contract schema contains a remote $ref"
  Just _ -> Left "contract schema $ref must be a string"

validateSchemaKeywordTypes :: KeyMap.KeyMap Value -> Either Text ()
validateSchemaKeywordTypes fields = do
  maybe (Right ()) validateTypeKeyword (KeyMap.lookup "type" fields)
  maybe (Right ()) (const (Right ())) (KeyMap.lookup "const" fields)
  maybe (Right ()) validateEnumKeyword (KeyMap.lookup "enum" fields)
  maybe (Right ()) validateRequiredKeyword (KeyMap.lookup "required" fields)
  maybe (Right ()) validatePropertiesKeyword (KeyMap.lookup "properties" fields)
  maybe (Right ()) validateDefsKeyword (KeyMap.lookup "$defs" fields)
  maybe (Right ()) validateAdditionalKeyword (KeyMap.lookup "additionalProperties" fields)
  maybe (Right ()) (validateIntegerKeyword "minItems") (KeyMap.lookup "minItems" fields)
  maybe (Right ()) (validateIntegerKeyword "maxItems") (KeyMap.lookup "maxItems" fields)
  maybe (Right ()) (validateIntegerKeyword "minContains") (KeyMap.lookup "minContains" fields)
  maybe (Right ()) (validateIntegerKeyword "maxContains") (KeyMap.lookup "maxContains" fields)
  maybe (Right ()) (validateIntegerKeyword "minLength") (KeyMap.lookup "minLength" fields)
  maybe (Right ()) (validateIntegerKeyword "maxLength") (KeyMap.lookup "maxLength" fields)
  maybe (Right ()) (validateNumberKeyword "minimum") (KeyMap.lookup "minimum" fields)
  maybe (Right ()) (validateNumberKeyword "maximum") (KeyMap.lookup "maximum" fields)
  maybe (Right ()) validateStringKeyword (KeyMap.lookup "pattern" fields)
  maybe (Right ()) validateFormatKeyword (KeyMap.lookup "format" fields)
  validateArrayKeyword "allOf" fields
  validateNonEmptyArrayKeyword "anyOf" fields
  maybe (Right ()) validateUniqueItemsKeyword (KeyMap.lookup "uniqueItems" fields)
  validateSchemaKeyword "items" fields
  validateSchemaKeyword "contains" fields
  mapM_ (`validateSchemaKeyword` fields) ["not", "if", "then", "else"]
  if not (KeyMap.member "if" fields) && (KeyMap.member "then" fields || KeyMap.member "else" fields)
    then Left "contract schema then/else require if"
    else Right ()
  validateContainsBounds fields
  validateObjectKeywordTypes fields

validateTypeKeyword :: Value -> Either Text ()
validateTypeKeyword (String name)
  | name `elem` typeNames = Right ()
  | otherwise = Left "contract schema has an unsupported type"
validateTypeKeyword (Array names)
  | null namesList = Left "contract schema type array must not be empty"
  | length namesList /= length (unique namesList) = Left "contract schema type array has duplicates"
  | all (`elem` typeNames) namesList = Right ()
  | otherwise = Left "contract schema has an unsupported type"
  where
    namesList = case traverse asString (toList names) of
      Right values -> values
      Left _ -> []
    asString :: Value -> Either Text Text
    asString (String name) = Right name
    asString _ = Left "contract schema type must contain only strings"
validateTypeKeyword _ = Left "contract schema type must be a string or array"

typeNames :: [Text]
typeNames = ["null", "boolean", "object", "array", "number", "integer", "string"]

unique :: Eq a => [a] -> [a]
unique [] = []
unique (value : values) = value : unique (filter (/= value) values)

validateEnumKeyword :: Value -> Either Text ()
validateEnumKeyword (Array values)
  | null (toList values) = Left "contract schema enum must not be empty"
  | otherwise = Right ()
validateEnumKeyword _ = Left "contract schema enum must be an array"

validateRequiredKeyword :: Value -> Either Text ()
validateRequiredKeyword (Array values)
  | length names == length (toList values)
      && all validName names && length names == length (unique names) = Right ()
  | otherwise = Left "contract schema required must contain unique strings"
  where
    names = [name | String name <- toList values]
    validName name = not (Text.null name)
validateRequiredKeyword _ = Left "contract schema required must be an array"

validatePropertiesKeyword :: Value -> Either Text ()
validatePropertiesKeyword (Object _) = Right ()
validatePropertiesKeyword _ = Left "contract schema properties must be an object"

validateDefsKeyword :: Value -> Either Text ()
validateDefsKeyword (Object _) = Right ()
validateDefsKeyword _ = Left "contract schema $defs must be an object"

validateAdditionalKeyword :: Value -> Either Text ()
validateAdditionalKeyword (Bool _) = Right ()
validateAdditionalKeyword value@(Object _) = validateSchemaKeywordValue "additionalProperties" value
validateAdditionalKeyword _ = Left "contract schema additionalProperties must be boolean or schema"

validateArrayKeyword :: Text -> KeyMap.KeyMap Value -> Either Text ()
validateArrayKeyword name fields = case KeyMap.lookup (Key.fromText name) fields of
  Nothing -> Right ()
  Just (Array _) -> Right ()
  Just _ -> Left ("contract schema " <> name <> " must be an array")

-- | @anyOf@ (specification 0.0.9): a nonempty array of schemas, at least one
-- of which the instance must satisfy.
validateNonEmptyArrayKeyword :: Text -> KeyMap.KeyMap Value -> Either Text ()
validateNonEmptyArrayKeyword name fields = case KeyMap.lookup (Key.fromText name) fields of
  Nothing -> Right ()
  Just (Array values) | not (null values) -> Right ()
  Just _ -> Left ("contract schema " <> name <> " must be a nonempty array")

validateUniqueItemsKeyword :: Value -> Either Text ()
validateUniqueItemsKeyword (Bool _) = Right ()
validateUniqueItemsKeyword _ = Left "contract schema uniqueItems must be a boolean"

validateSchemaKeyword :: Text -> KeyMap.KeyMap Value -> Either Text ()
validateSchemaKeyword name fields = maybe (Right ()) (validateSchemaKeywordValue name)
  (KeyMap.lookup (Key.fromText name) fields)

validateSchemaKeywordValue :: Text -> Value -> Either Text ()
validateSchemaKeywordValue _ (Object _) = Right ()
validateSchemaKeywordValue name _ = Left ("contract schema " <> name <> " must be a schema object")

validateIntegerKeyword :: Text -> Value -> Either Text ()
validateIntegerKeyword name (Number value) =
  case floatingOrInteger value :: Either Double Integer of
    Right integer | integer >= 0 -> Right ()
    _ -> Left ("contract schema " <> name <> " must be a non-negative integer")
validateIntegerKeyword name _ = Left ("contract schema " <> name <> " must be a non-negative integer")

validateNumberKeyword :: Text -> Value -> Either Text ()
validateNumberKeyword _ (Number _) = Right ()
validateNumberKeyword name _ = Left ("contract schema " <> name <> " must be a number")

validateStringKeyword :: Value -> Either Text ()
validateStringKeyword value@(String _) = compilePattern value >> Right ()
validateStringKeyword _ = Left "contract schema pattern must be a string"

validateFormatKeyword :: Value -> Either Text ()
validateFormatKeyword (String "date-time") = Right ()
validateFormatKeyword _ = Left "contract schema format must be date-time"

validateContainsBounds :: KeyMap.KeyMap Value -> Either Text ()
validateContainsBounds fields = do
  let containsPresent = KeyMap.member "contains" fields
  if not containsPresent && (KeyMap.member "minContains" fields || KeyMap.member "maxContains" fields)
    then Left "contract schema minContains/maxContains require contains"
    else pure ()
  minValue <- integerValue "minContains" (KeyMap.lookup "minContains" fields)
  maxValue <- integerValue "maxContains" (KeyMap.lookup "maxContains" fields)
  case (minValue, maxValue) of
    (Just lower, Just upper) | lower > upper -> Left "contract schema minContains exceeds maxContains"
    _ -> Right ()

integerValue :: Text -> Maybe Value -> Either Text (Maybe Integer)
integerValue _ Nothing = Right Nothing
integerValue name (Just (Number value)) = case floatingOrInteger value :: Either Double Integer of
  Right integer | integer >= 0 -> Right (Just integer)
  _ -> Left ("contract schema " <> name <> " must be a non-negative integer")
integerValue name (Just _) = Left ("contract schema " <> name <> " must be a non-negative integer")

nestedSchemas :: KeyMap.KeyMap Value -> [(Key.Key, Value)]
nestedSchemas fields = concatMap one (KeyMap.toList fields) <> objectKeywordSchemas fields
  where
    one (key, value) = case Key.toText key of
      "$defs" -> case value of
        Object definitions -> KeyMap.toList definitions
        _ -> []
      "properties" -> case value of
        Object properties -> KeyMap.toList properties
        _ -> []
      "allOf" -> case value of
        Array schemas -> zipWith (\index schema -> (Key.fromText ("allOf[" <> Text.pack (show index) <> "]"), schema)) [0 :: Int ..] (toList schemas)
        _ -> []
      "anyOf" -> case value of
        Array schemas -> zipWith (\index schema -> (Key.fromText ("anyOf[" <> Text.pack (show index) <> "]"), schema)) [0 :: Int ..] (toList schemas)
        _ -> []
      "items" -> [(Key.fromText "items", value)]
      name | name `elem` ["not", "if", "then", "else"] -> [(key, value)]
      "contains" -> [(Key.fromText "contains", value)]
      "additionalProperties" -> case value of
        Object _ -> [(key, value)]
        _ -> []
      _ -> []

schemaPath :: [Text] -> Text
schemaPath [] = "contract schema"
schemaPath path = "contract schema at " <> Text.intercalate "." (reverse path)

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

validateContractProfile :: Value -> Value -> Either Text ()
validateContractProfile (Object schema) (Object instanceValue) = do
  case KeyMap.lookup "x-archive-ordered-sample-spans" schema of
    Nothing -> pure ()
    Just (Object rule) -> validateOrderedSpansInstance rule instanceValue
    Just _ -> Left "ordered sample spans rule must be an object"
  case KeyMap.lookup "x-archive-unique-sorted-refs" schema of
    Nothing -> pure ()
    Just (Object rule) -> validateSortedRefsInstance rule instanceValue
    Just _ -> Left "unique sorted references rule must be an object"
  case KeyMap.lookup "x-archive-track-sample-spans" schema of
    Nothing -> pure ()
    Just (Object rule) -> validateTrackSpansInstance rule instanceValue
    Just _ -> Left "track sample spans rule must be an object"
  case KeyMap.lookup "x-archive-monotonic-anchors" schema of
    Nothing -> pure ()
    Just (Object rule) -> validateMonotonicAnchorsInstance rule instanceValue
    Just _ -> Left "monotonic anchors rule must be an object"
  case KeyMap.lookup "x-archive-distinct-refs" schema of
    Nothing -> pure ()
    Just (Object rule) -> validateDistinctRefsInstance rule instanceValue
    Just _ -> Left "distinct references rule must be an object"
  case KeyMap.lookup "x-archive-ordered-distinct-refs" schema of
    Nothing -> pure ()
    Just (Object rule) -> validateOrderedDistinctRefsInstance rule instanceValue
    Just _ -> Left "ordered distinct references rule must be an object"
  case KeyMap.lookup "x-archive-ordered-instants" schema of
    Nothing -> pure ()
    Just (Object rule) -> validateOrderedInstantsInstance rule instanceValue
    Just _ -> Left "ordered instants rule must be an object"
  case KeyMap.lookup "x-archive-sequential-spans" schema of
    Nothing -> pure ()
    Just rule -> do
      rules <- sequentialSpansRule rule
      payload <- requiredObject "payload" instanceValue
      validateSequentialSpans rules payload
  case KeyMap.lookup "x-archive-ascending-items" schema of
    Nothing -> pure ()
    Just (Object rule) -> validateAscendingItemsInstance rule instanceValue
    Just _ -> Left "ascending items rule must be an object"
  case KeyMap.lookup "x-archive-pitch-stacks" schema of
    Nothing -> pure ()
    Just (Object rule) -> validatePitchStacksInstance rule instanceValue
    Just _ -> Left "pitch stacks rule must be an object"
  case KeyMap.lookup "x-archive-bar-beats" schema of
    Nothing -> pure ()
    Just (Object rule) -> validateBarBeatsInstance rule instanceValue
    Just _ -> Left "bar beats rule must be an object"
  case KeyMap.lookup "x-archive-metric-modulations" schema of
    Nothing -> pure ()
    Just (Object rule) -> validateMetricModulationsInstance rule instanceValue
    Just _ -> Left "metric modulations rule must be an object"
  case KeyMap.lookup "x-archive-partial-dates" schema of
    Nothing -> pure ()
    Just (Object rule) -> validatePartialDatesInstance rule instanceValue
    Just _ -> Left "partial dates rule must be an object"
  case KeyMap.lookup "x-archive-subset" schema of
    Nothing -> pure ()
    Just (Object rule) -> validateSubsetInstance rule instanceValue
    Just _ -> Left "subset rule must be an object"
  case KeyMap.lookup "x-archive-document-order" schema of
    Nothing -> pure ()
    Just (Object rule) -> validateDocumentOrderInstance rule instanceValue
    Just _ -> Left "document order rule must be an object"
  case KeyMap.lookup "x-archive-nested-ranges" schema of
    Nothing -> pure ()
    Just (Object rule) -> validateNestedRangesInstance rule instanceValue
    Just _ -> Left "nested ranges rule must be an object"
validateContractProfile _ _ = Left "contract profile requires an object instance"

-- | Spans are grouped by track, and references of the span role follow the
-- tracks in order.  Bounds are compared only between consecutive spans of one
-- track that pin the same Artifact object: different recordings have
-- different clocks, so their sample offsets are not comparable here.
validateTrackSpansInstance :: KeyMap.KeyMap Value -> KeyMap.KeyMap Value -> Either Text ()
validateTrackSpansInstance rule instanceValue = do
  (role, tracks) <- trackSpans rule instanceValue
  startName <- requiredString "start" rule
  endName <- requiredString "end" rule
  rateName <- requiredString "rate" rule
  refs <- instanceReferences instanceValue
  roleRefs <- referencesWithRole role refs
  let spanCount = sum (map length tracks)
  if null tracks || any null tracks || spanCount /= length roleRefs
    then Left "track spans and span references must be nonempty and equal in number"
    else pure ()
  pieceObjects <- mapM refObject roleRefs
  let pinned = splitPlaces (map length tracks) pieceObjects
  mapM_ (validateTrack startName endName rateName) (zip tracks pinned)
  where
    validateTrack startName endName rateName (pieces, objects) = do
      bounds <- mapM (spanBounds startName endName rateName) pieces
      let consecutive = zip (zip objects bounds) (drop 1 (zip objects bounds))
      if all (\((left, (_, leftEnd)), (right, (rightStart, _))) -> left /= right || rightStart >= leftEnd) consecutive
        then Right ()
        else Left "spans of one track that pin the same Artifact must not overlap or run backwards"
    spanBounds startName endName rateName (Object piece) = do
      start <- requiredInteger startName piece
      end <- requiredInteger endName piece
      rate <- requiredInteger rateName piece
      if start >= 0 && end > start && rate > 0
        then Right (start, end)
        else Left "sample span requires 0 <= start < end and positive sample rate"
    spanBounds _ _ _ _ = Left "sample span must be an object"

trackSpans :: KeyMap.KeyMap Value -> KeyMap.KeyMap Value -> Either Text (Text, [[Value]])
trackSpans rule instanceValue = do
  payloadName <- requiredString "payload" rule
  piecesName <- requiredString "pieces" rule
  role <- requiredString "role" rule
  payload <- requiredObject "payload" instanceValue
  tracks <- case KeyMap.lookup (Key.fromText payloadName) payload of
    Just (Array values) -> Right (toList values)
    _ -> Left "track sample spans payload must be an array"
  pieces <- mapM (trackPieces piecesName) tracks
  pure (role, pieces)
  where
    trackPieces piecesName (Object track) = case KeyMap.lookup (Key.fromText piecesName) track of
      Just (Array values) -> Right (toList values)
      _ -> Left "track must contain a span array"
    trackPieces _ _ = Left "track must be an object"

-- | For a schema with a distinct-track-content rule, the span role and the
-- number of span references in each track, in reference order.  Door needs
-- committed Artifact revisions to compare digests, so the comparison itself
-- happens where the snapshot is available.
distinctTrackContentRule :: Value -> Value -> Either Text (Maybe (Text, [Int]))
distinctTrackContentRule (Object schema) (Object instanceValue) =
  case (KeyMap.lookup "x-archive-distinct-track-content" schema, KeyMap.lookup "x-archive-track-sample-spans" schema) of
    (Nothing, _) -> Right Nothing
    (Just (Object distinctRule), Just (Object spansRule)) -> do
      role <- requiredString "role" distinctRule
      (spanRole, tracks) <- trackSpans spansRule instanceValue
      if role /= spanRole
        then Left "distinct track content rule must name the span reference role"
        else Right (Just (role, map length tracks))
    _ -> Left "distinct track content rule requires a track sample spans rule"
distinctTrackContentRule _ _ = Left "contract profile requires an object instance"

validateMonotonicAnchorsInstance :: KeyMap.KeyMap Value -> KeyMap.KeyMap Value -> Either Text ()
validateMonotonicAnchorsInstance rule instanceValue = do
  payloadName <- requiredString "payload" rule
  names <- requiredStrings "fields" rule
  payload <- requiredObject "payload" instanceValue
  anchors <- case KeyMap.lookup (Key.fromText payloadName) payload of
    Just (Array values) | not (null values) -> Right (toList values)
    _ -> Left "monotonic anchors payload must be a nonempty array"
  rows <- mapM (anchorRow names) anchors
  let increasing column = and (zipWith (<) (map (!! column) rows) (drop 1 (map (!! column) rows)))
  if all increasing [0 .. length names - 1]
    then Right ()
    else Left "anchors must strictly increase in every named field"
  where
    anchorRow names (Object anchor) = mapM (`requiredInteger` anchor) names
    anchorRow _ _ = Left "anchor must be an object"

-- | Both named payload fields are RFC 3339 instants in UTC with a @Z@
-- suffix, and the second is not earlier than the first.
validateOrderedInstantsInstance :: KeyMap.KeyMap Value -> KeyMap.KeyMap Value -> Either Text ()
validateOrderedInstantsInstance rule instanceValue = do
  startName <- requiredString "start" rule
  endName <- requiredString "end" rule
  payload <- requiredObject "payload" instanceValue
  start <- utcInstant startName payload
  end <- utcInstant endName payload
  if end >= start
    then Right ()
    else Left (endName <> " must not be before " <> startName)
  where
    utcInstant name payload = do
      value <- requiredString name payload
      case parseUtcInstant value of
        Just instant -> Right instant
        Nothing -> Left (name <> " must be an RFC 3339 instant in UTC with a Z suffix")

parseUtcInstant :: Text -> Maybe UTCTime
parseUtcInstant value
  | Text.isSuffixOf "Z" value && Text.isInfixOf "T" value =
      parseTimeM False defaultTimeLocale "%Y-%m-%dT%H:%M:%S%QZ" (Text.unpack value)
  | otherwise = Nothing

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

splitPlaces :: [Int] -> [a] -> [[a]]
splitPlaces [] _ = []
splitPlaces (count : counts) values = take count values : splitPlaces counts (drop count values)

validateOrderedSpansInstance :: KeyMap.KeyMap Value -> KeyMap.KeyMap Value -> Either Text ()
validateOrderedSpansInstance rule instanceValue = do
  payloadName <- requiredString "payload" rule
  role <- requiredString "role" rule
  startName <- requiredString "start" rule
  endName <- requiredString "end" rule
  rateName <- requiredString "rate" rule
  payload <- requiredObject "payload" instanceValue
  pieces <- case KeyMap.lookup (Key.fromText payloadName) payload of
    Just (Array values) -> Right (toList values)
    _ -> Left "ordered sample spans payload must be an array"
  refs <- instanceReferences instanceValue
  if null pieces || length pieces /= length refs
    then Left "ordered sample spans and piece references must have equal nonzero length"
    else pure ()
  mapM_ (validatePiece startName endName rateName) pieces
  mapM_ (validateRefRole role) refs
  where
    validatePiece startName endName rateName (Object piece) = do
      start <- requiredInteger startName piece
      end <- requiredInteger endName piece
      rate <- requiredInteger rateName piece
      if start >= 0 && end > start && rate > 0
        then Right ()
        else Left "sample span requires 0 <= start < end and positive sample rate"
    validatePiece _ _ _ _ = Left "sample span must be an object"

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

requiredInteger :: Text -> KeyMap.KeyMap Value -> Either Text Integer
requiredInteger name fields = case KeyMap.lookup (Key.fromText name) fields of
  Just (Number value) -> case floatingOrInteger value :: Either Double Integer of
    Right integer -> Right integer
    Left _ -> Left (name <> " must be an integer")
  _ -> Left (name <> " must be an integer")

evaluateSchema :: Value -> [Text] -> Value -> Value -> Either Text Bool
evaluateSchema root path (Object fields) value = do
  validateSchemaNodeForEvaluation root path fields
  referenced <- case KeyMap.lookup "$ref" fields of
    Nothing -> pure Nothing
    Just (String reference) -> case resolvePointer root reference of
      Just target -> pure (Just target)
      Nothing -> Left "contract schema contains an unresolved local $ref"
    Just _ -> Left "contract schema $ref must be a string"
  referenceMatch <- maybe (pure True) (\target -> evaluateSchema root path target value) referenced
  keywordMatch <- and <$> sequence
    [ evaluateType fields value
    , evaluateConst fields value
    , evaluateEnum fields value
    , evaluateMinimum fields value
    , evaluateMaximum fields value
    , evaluateObject root path fields value
    , evaluateArray root path fields value
    , evaluateString fields value
    , evaluateAllOf root path fields value
    , evaluateAnyOf root path fields value
    , evaluateNot root path fields value
    , evaluateConditional root path fields value
    , evaluateObjectKeywords (\schema item -> evaluateSchema root ("propertyNames" : path) schema item) fields value
    ]
  pure (referenceMatch && keywordMatch)
evaluateSchema _ path _ _ = Left (schemaPath path <> " must be an object")

validateSchemaNodeForEvaluation :: Value -> [Text] -> KeyMap.KeyMap Value -> Either Text ()
validateSchemaNodeForEvaluation root path fields = do
  rejectUnknownSchemaKeywords path fields
  validateRef root fields
  validateSchemaKeywordTypes fields

-- | Name the first rule an instance breaks, for a refusal a person can act
-- on: where in the instance (a JSON Pointer from @{"payload", "refs"}@) and
-- which keyword of which part of the schema. Called only after
-- 'evaluateSchema' answered no; it descends into the first failing
-- property, item, @allOf@ branch, @$ref@, or applicable @then@/@else@, and
-- otherwise names the keyword at the node where the instance fails.
explainMismatch :: Value -> [Text] -> [Text] -> Value -> Value -> Either Text Text
explainMismatch root path location (Object fields) value = do
  let fails schemaPath' schema item = not <$> evaluateSchema root schemaPath' schema item
      at = instancePointer location
      leaf keyword = Right (at <> " breaks " <> keyword <> " of the " <> schemaPath path)
      firstJust [] = Right Nothing
      firstJust (step : rest) = step >>= maybe (firstJust rest) (pure . Just)
      reasons = firstJust
        [ case KeyMap.lookup "$ref" fields of
            Just (String reference) | Just target <- resolvePointer root reference -> do
              broken <- fails path target value
              if broken then Just <$> explainMismatch root path location target value else pure Nothing
            _ -> pure Nothing
        , firstJust
            [ do
                broken <- not <$> evaluateLeaf keyword
                if broken then Just <$> leaf keyword else pure Nothing
            | keyword <- leafKeywords, KeyMap.member (Key.fromText keyword) fields ]
        , objectReason
        , arrayReason
        , case KeyMap.lookup "allOf" fields of
            Just (Array schemas) -> firstJust
              [ do
                  let branchPath = ("allOf[" <> Text.pack (show index) <> "]") : path
                  broken <- fails branchPath schema value
                  if broken then Just <$> explainMismatch root branchPath location schema value else pure Nothing
              | (index, schema) <- zip [0 :: Int ..] (toList schemas) ]
            _ -> pure Nothing
        , case KeyMap.lookup "anyOf" fields of
            Just (Array schemas) -> do
              matched <- or <$> mapM (\schema -> evaluateSchema root ("anyOf" : path) schema value) (toList schemas)
              if matched then pure Nothing else Just <$> leaf "anyOf (it matches none of the choices)"
            _ -> pure Nothing
        , case KeyMap.lookup "not" fields of
            Just schema -> do
              matched <- evaluateSchema root ("not" : path) schema value
              if matched then Just <$> leaf "not (it matches what is forbidden)" else pure Nothing
            Nothing -> pure Nothing
        , case KeyMap.lookup "if" fields of
            Just condition -> do
              holds <- evaluateSchema root ("if" : path) condition value
              let name = if holds then "then" else "else"
              case KeyMap.lookup (Key.fromText name) fields of
                Just schema -> do
                  broken <- fails (name : path) schema value
                  if broken then Just <$> explainMismatch root (name : path) location schema value else pure Nothing
                Nothing -> pure Nothing
            Nothing -> pure Nothing
        , do
            broken <- not <$> evaluateObjectKeywords
              (\schema item -> evaluateSchema root ("propertyNames" : path) schema item) fields value
            if broken
              then Just <$> leaf (Text.intercalate ", " [k | k <- objectKeywordNames, KeyMap.member (Key.fromText k) fields])
              else pure Nothing
        ]
      evaluateLeaf keyword =
        let only = KeyMap.filterWithKey (\key _ -> Key.toText key == keyword) fields
        in and <$> sequence [ evaluateType only value, evaluateConst only value, evaluateEnum only value
                            , evaluateMinimum only value, evaluateMaximum only value, evaluateString only value ]
      objectReason = case value of
        Object object -> do
          let missing = case KeyMap.lookup "required" fields of
                Just (Array names) -> [name | String name <- toList names, not (KeyMap.member (Key.fromText name) object)]
                _ -> []
              properties = case KeyMap.lookup "properties" fields of
                Just (Object definitions) -> definitions
                _ -> KeyMap.empty
              property (key, item) = case KeyMap.lookup key properties of
                Just schema -> nested (Key.toText key : path) schema
                Nothing -> case KeyMap.lookup "additionalProperties" fields of
                  Just (Bool False) -> pure (Just (instancePointer (Key.toText key : location)
                    <> " is a member the " <> schemaPath path <> " does not allow"))
                  Just schema@(Object _) -> nested ("additionalProperties" : path) schema
                  _ -> pure Nothing
                where
                  nested schemaPath' schema = do
                    broken <- fails schemaPath' schema item
                    if broken
                      then Just <$> explainMismatch root schemaPath' (Key.toText key : location) schema item
                      else pure Nothing
          if not (null missing)
            then Just <$> leaf ("required (" <> Text.intercalate ", " missing <> " missing)")
            else firstJust (map property (KeyMap.toList object))
        _ -> pure Nothing
      arrayReason = case value of
        Array values -> do
          let items = toList values
          fromItems <- case KeyMap.lookup "items" fields of
            Just schema@(Object _) -> firstJust
              [ do
                  broken <- fails ("items" : path) schema item
                  if broken
                    then Just <$> explainMismatch root ("items" : path) (Text.pack (show index) : location) schema item
                    else pure Nothing
              | (index, item) <- zip [0 :: Int ..] items ]
            _ -> pure Nothing
          case fromItems of
            Just reason -> pure (Just reason)
            Nothing -> firstJust
              [ do
                  let only = KeyMap.filterWithKey (\key _ -> Key.toText key `elem` (name : companions)) fields
                  broken <- not <$> evaluateArray root path only value
                  if broken then Just <$> leaf name else pure Nothing
              | (name, companions) <- [("minItems", []), ("maxItems", []), ("uniqueItems", []),
                                       ("contains", ["minContains", "maxContains"])]
              , KeyMap.member (Key.fromText name) fields ]
        _ -> pure Nothing
  found <- reasons
  pure (maybe (instancePointer location <> " does not satisfy the " <> schemaPath path) id found)
explainMismatch _ path location _ _ = Right (instancePointer location <> " does not satisfy the " <> schemaPath path)

leafKeywords :: [Text]
leafKeywords = ["type", "const", "enum", "minimum", "maximum", "pattern", "minLength", "maxLength", "format"]

-- | A JSON Pointer to a place in the instance, from its reversed path.
instancePointer :: [Text] -> Text
instancePointer [] = "the instance"
instancePointer location =
  "/" <> Text.intercalate "/" (map escape (reverse location))
  where
    escape = Text.replace "/" "~1" . Text.replace "~" "~0"

evaluateType :: KeyMap.KeyMap Value -> Value -> Either Text Bool
evaluateType fields value = case KeyMap.lookup "type" fields of
  Nothing -> Right True
  Just typeValue -> do
    names <- case typeValue of
      String name -> Right [name]
      Array values -> mapM asString (toList values)
      _ -> Left "contract schema type must be a string or array"
    pure (any (`valueHasType` value) names)
  where
    asString (String name) = Right name
    asString _ = Left "contract schema type must contain only strings"

valueHasType :: Text -> Value -> Bool
valueHasType "null" Null = True
valueHasType "boolean" (Bool _) = True
valueHasType "object" (Object _) = True
valueHasType "array" (Array _) = True
valueHasType "number" (Number _) = True
valueHasType "integer" (Number value) = case floatingOrInteger value :: Either Double Integer of
  Right _ -> True
  Left _ -> False
valueHasType "string" (String _) = True
valueHasType _ _ = False

evaluateConst :: KeyMap.KeyMap Value -> Value -> Either Text Bool
evaluateConst fields value = Right (maybe True (== value) (KeyMap.lookup "const" fields))

evaluateEnum :: KeyMap.KeyMap Value -> Value -> Either Text Bool
evaluateEnum fields value = Right (maybe True (value `elem`) (arrayValues <$> KeyMap.lookup "enum" fields))
  where
    arrayValues (Array values) = toList values
    arrayValues _ = []

evaluateMinimum :: KeyMap.KeyMap Value -> Value -> Either Text Bool
evaluateMinimum fields (Number value) = case KeyMap.lookup "minimum" fields of
  Nothing -> Right True
  Just (Number lower) -> Right (value >= lower)
  Just _ -> Left "contract schema minimum must be a number"
evaluateMinimum _ _ = Right True

evaluateMaximum :: KeyMap.KeyMap Value -> Value -> Either Text Bool
evaluateMaximum fields (Number value) = case KeyMap.lookup "maximum" fields of
  Nothing -> Right True
  Just (Number upper) -> Right (value <= upper)
  Just _ -> Left "contract schema maximum must be a number"
evaluateMaximum _ _ = Right True

evaluateObject :: Value -> [Text] -> KeyMap.KeyMap Value -> Value -> Either Text Bool
evaluateObject root path fields (Object object) = do
  requiredMatch <- case KeyMap.lookup "required" fields of
    Nothing -> Right True
    Just (Array names) -> pure (all (`KeyMap.member` object) [Key.fromText name | String name <- toList names])
    Just _ -> Left "contract schema required must be an array"
  properties <- case KeyMap.lookup "properties" fields of
    Nothing -> Right KeyMap.empty
    Just (Object definitions) -> Right definitions
    Just _ -> Left "contract schema properties must be an object"
  propertyMatch <- and <$> mapM (validateProperty properties) (KeyMap.toList object)
  pure (requiredMatch && propertyMatch)
  where
    validateProperty properties (key, item) = case KeyMap.lookup key properties of
      Just propertySchema -> evaluateSchema root (Key.toText key : path) propertySchema item
      Nothing -> case KeyMap.lookup "additionalProperties" fields of
        Nothing -> Right True
        Just (Bool allowed) -> Right allowed
        Just additionalSchema -> evaluateSchema root (Key.toText key : path) additionalSchema item
evaluateObject _ _ fields _ = pure (not (hasObjectOnlyConstraint fields))

hasObjectOnlyConstraint :: KeyMap.KeyMap Value -> Bool
hasObjectOnlyConstraint fields = any (`KeyMap.member` fields)
  ["required", "properties"]

evaluateArray :: Value -> [Text] -> KeyMap.KeyMap Value -> Value -> Either Text Bool
evaluateArray root path fields (Array values) = do
  itemMatch <- case KeyMap.lookup "items" fields of
    Nothing -> Right True
    Just (Object itemSchema) -> and <$> mapM (evaluateSchema root ("items" : path) (Object itemSchema)) (toList values)
    Just _ -> Left "contract schema items must be a schema object"
  minMatch <- compareArrayBound "minItems" (>=) fields (toList values)
  maxMatch <- compareArrayBound "maxItems" (<=) fields (toList values)
  containsMatch <- case KeyMap.lookup "contains" fields of
    Nothing -> Right True
    Just (Object containsSchema) -> do
      matches <- mapM (evaluateSchema root ("contains" : path) (Object containsSchema)) (toList values)
      let count = fromIntegral (length (filter id matches)) :: Integer
          lower = maybe 1 id (integerField "minContains" fields)
          upper = integerField "maxContains" fields
      pure (count >= lower && maybe True (count <=) upper)
    Just _ -> Left "contract schema contains must be a schema object"
  uniqueMatch <- case KeyMap.lookup "uniqueItems" fields of
    Nothing -> Right True
    Just (Bool False) -> Right True
    -- JSON equality: aeson compares objects by their members and numbers by
    -- value, as JSON Schema's uniqueItems does.
    Just (Bool True) -> Right (length (toList values) == length (unique (toList values)))
    Just _ -> Left "contract schema uniqueItems must be a boolean"
  pure (itemMatch && minMatch && maxMatch && containsMatch && uniqueMatch)
evaluateArray _ _ fields _ = pure (not (hasArrayOnlyConstraint fields))

hasArrayOnlyConstraint :: KeyMap.KeyMap Value -> Bool
hasArrayOnlyConstraint fields = any (`KeyMap.member` fields)
  ["items", "minItems", "maxItems", "uniqueItems", "contains", "minContains", "maxContains"]

compareArrayBound :: Text -> (Integer -> Integer -> Bool) -> KeyMap.KeyMap Value -> VectorLike -> Either Text Bool
compareArrayBound name comparison fields values = case integerField name fields of
  Nothing -> Right True
  Just bound -> Right (lengthLike values `comparison` bound)

-- Keeping this alias local avoids exposing aeson's vector type in the module's
-- API while retaining a small, readable helper signature.
type VectorLike = [Value]

lengthLike :: VectorLike -> Integer
lengthLike = fromIntegral . length

integerField :: Text -> KeyMap.KeyMap Value -> Maybe Integer
integerField name fields = case KeyMap.lookup (Key.fromText name) fields of
  Just (Number value) -> case floatingOrInteger value :: Either Double Integer of
    Right integer | integer >= 0 -> Just integer
    _ -> Nothing
  _ -> Nothing

evaluateString :: KeyMap.KeyMap Value -> Value -> Either Text Bool
evaluateString fields (String value) = do
  patternMatch <- case KeyMap.lookup "pattern" fields of
    Nothing -> Right True
    Just patternValue -> matchesPattern <$> compilePattern patternValue <*> pure value
  lengthMatch <- case KeyMap.lookup "minLength" fields of
    Nothing -> Right True
    Just (Number number) -> case floatingOrInteger number :: Either Double Integer of
      Right lower -> Right (fromIntegral (Text.length value) >= (lower :: Integer))
      Left _ -> Left "contract schema minLength must be an integer"
    Just _ -> Left "contract schema minLength must be an integer"
  -- Data.Text counts Unicode scalar values, which is JSON Schema's
  -- code-point definition of string length.
  maxLengthMatch <- case KeyMap.lookup "maxLength" fields of
    Nothing -> Right True
    Just (Number number) -> case floatingOrInteger number :: Either Double Integer of
      Right upper -> Right (fromIntegral (Text.length value) <= (upper :: Integer))
      Left _ -> Left "contract schema maxLength must be an integer"
    Just _ -> Left "contract schema maxLength must be an integer"
  formatMatch <- case KeyMap.lookup "format" fields of
    Nothing -> Right True
    Just (String "date-time") -> Right (validDateTime value)
    Just _ -> Left "contract schema format must be date-time"
  pure (patternMatch && lengthMatch && maxLengthMatch && formatMatch)
evaluateString _ _ = Right True

evaluateAllOf :: Value -> [Text] -> KeyMap.KeyMap Value -> Value -> Either Text Bool
evaluateAllOf root path fields value = case KeyMap.lookup "allOf" fields of
  Nothing -> Right True
  Just (Array schemas) -> and <$> mapM (\schema -> evaluateSchema root ("allOf" : path) schema value) (toList schemas)
  Just _ -> Left "contract schema allOf must be an array"

-- | @not@ (specification 0.0.9): the instance must not satisfy the schema.
evaluateNot :: Value -> [Text] -> KeyMap.KeyMap Value -> Value -> Either Text Bool
evaluateNot root path fields value = case KeyMap.lookup "not" fields of
  Nothing -> Right True
  Just schema -> not <$> evaluateSchema root ("not" : path) schema value

-- | @if@, @then@, and @else@ (specification 0.0.9): when the instance
-- satisfies @if@ it must satisfy @then@, and otherwise @else@; an absent
-- branch is satisfied. Both branches are evaluated, so a malformed branch is
-- refused whichever applies.
evaluateConditional :: Value -> [Text] -> KeyMap.KeyMap Value -> Value -> Either Text Bool
evaluateConditional root path fields value = case KeyMap.lookup "if" fields of
  Nothing -> Right True
  Just condition -> do
    holds <- evaluateSchema root ("if" : path) condition value
    whenTrue <- branch "then"
    whenFalse <- branch "else"
    pure (if holds then whenTrue else whenFalse)
  where
    branch name = maybe (Right True) (\schema -> evaluateSchema root (name : path) schema value)
      (KeyMap.lookup (Key.fromText name) fields)

-- | Every branch is evaluated, so a malformed branch is refused even when an
-- earlier one matched; the instance must satisfy at least one.
evaluateAnyOf :: Value -> [Text] -> KeyMap.KeyMap Value -> Value -> Either Text Bool
evaluateAnyOf root path fields value = case KeyMap.lookup "anyOf" fields of
  Nothing -> Right True
  Just (Array schemas) | not (null schemas) ->
    or <$> mapM (\schema -> evaluateSchema root ("anyOf" : path) schema value) (toList schemas)
  Just _ -> Left "contract schema anyOf must be a nonempty array"

-- Contract patterns use a small, closed regular-expression subset instead of
-- a regex engine with dialect-dependent behaviour: anchored patterns built from
-- literals, escaped metacharacters, ASCII character classes without negation,
-- groups with alternation, and the quantifiers ?, *, +, {n}, and {n,m}.  Any
-- other syntax is an unsupported contract, rejected while the bundle loads.
data PatternNode
  = PatternLiteral Char
  | PatternClass [Char]
  | PatternGroup [[PatternNode]]
  | PatternRepeat PatternNode Int (Maybe Int)

newtype ContractPattern = ContractPattern [PatternNode]

compilePattern :: Value -> Either Text ContractPattern
compilePattern (String patternText) = parsePattern patternText
compilePattern _ = Left "contract schema pattern must be a string"

parsePattern :: Text -> Either Text ContractPattern
parsePattern source = case Text.uncons source of
  Just ('^', rest) | Just (body, '$') <- Text.unsnoc rest -> do
    (alternatives, remaining) <- parseAlternatives (Text.unpack body)
    case (alternatives, remaining) of
      ([sequence'], []) -> Right (ContractPattern sequence')
      (_, []) -> Right (ContractPattern [PatternGroup alternatives])
      _ -> Left "contract schema pattern has an unbalanced group"
  _ -> Left "contract schema pattern must be anchored"

-- | Parse alternatives up to an unconsumed ')' or the end of input.
parseAlternatives :: String -> Either Text ([[PatternNode]], String)
parseAlternatives input = do
  (first, rest) <- parseSequence input
  case rest of
    '|' : more -> do
      (others, remaining) <- parseAlternatives more
      Right (first : others, remaining)
    _ -> Right ([first], rest)

parseSequence :: String -> Either Text ([PatternNode], String)
parseSequence input = case input of
  [] -> Right ([], [])
  ')' : _ -> Right ([], input)
  '|' : _ -> Right ([], input)
  _ -> do
    (atom, afterAtom) <- parseAtom input
    (node, afterQuantifier) <- parseQuantifier atom afterAtom
    (nodes, rest) <- parseSequence afterQuantifier
    Right (node : nodes, rest)

parseAtom :: String -> Either Text (PatternNode, String)
parseAtom input = case input of
  '\\' : character : rest
    | character `elem` ('-' : metaCharacters) -> Right (PatternLiteral character, rest)
    | otherwise -> Left "contract schema pattern uses an unsupported escape"
  '[' : rest -> case break (== ']') rest of
    (body, ']' : afterClass) -> do
      characters <- parseClass body
      Right (PatternClass characters, afterClass)
    _ -> Left "contract schema pattern has an unterminated class"
  '(' : '?' : _ -> Left "contract schema pattern uses unsupported group syntax"
  '(' : rest -> do
    (alternatives, remaining) <- parseAlternatives rest
    case remaining of
      ')' : afterGroup -> Right (PatternGroup alternatives, afterGroup)
      _ -> Left "contract schema pattern has an unbalanced group"
  character : rest
    | character `elem` metaCharacters -> Left "contract schema pattern uses unsupported syntax"
    | otherwise -> Right (PatternLiteral character, rest)
  [] -> Left "contract schema pattern is incomplete"

metaCharacters :: String
metaCharacters = ".*+?()|{}[]\\^$"

-- | A class is a nonempty run of ASCII letters and digits and ranges between
-- them; negation and escapes inside classes are unsupported.
parseClass :: String -> Either Text [Char]
parseClass [] = Left "contract schema pattern has an empty class"
parseClass body = go body
  where
    go [] = Right []
    go (low : '-' : high : rest)
      | classCharacter low && classCharacter high && low <= high = (enumFromTo low high <>) <$> go rest
      | otherwise = Left "contract schema pattern uses an unsupported character class"
    go (character : rest)
      | classCharacter character = (character :) <$> go rest
      | otherwise = Left "contract schema pattern uses an unsupported character class"
    classCharacter character =
      ('0' <= character && character <= '9') || ('a' <= character && character <= 'z')
        || ('A' <= character && character <= 'Z')

parseQuantifier :: PatternNode -> String -> Either Text (PatternNode, String)
parseQuantifier atom input = case input of
  '?' : rest -> Right (PatternRepeat atom 0 (Just 1), rest)
  '*' : rest -> Right (PatternRepeat atom 0 Nothing, rest)
  '+' : rest -> Right (PatternRepeat atom 1 Nothing, rest)
  '{' : rest -> case break (== '}') rest of
    (bounds, '}' : afterBounds) -> do
      (lower, upper) <- parseBounds bounds
      Right (PatternRepeat atom lower upper, afterBounds)
    _ -> Left "contract schema pattern has an unterminated quantifier"
  _ -> Right (atom, input)

parseBounds :: String -> Either Text (Int, Maybe Int)
parseBounds bounds = case break (== ',') bounds of
  (exact, []) -> do
    count <- natural exact
    if count > 0 then Right (count, Just count) else invalid
  (lower, ',' : []) -> do
    count <- natural lower
    Right (count, Nothing)
  (lower, ',' : upper) -> do
    low <- natural lower
    high <- natural upper
    if high > 0 && low <= high then Right (low, Just high) else invalid
  _ -> invalid
  where
    natural digits
      | not (null digits) && all (\character -> '0' <= character && character <= '9') digits
          && length digits <= 6 = Right (read digits)
      | otherwise = invalid
    invalid = Left "contract schema pattern quantifier must be a valid count"

matchesPattern :: ContractPattern -> Text -> Bool
matchesPattern (ContractPattern nodes) value = any null (matchSequence nodes (Text.unpack value))

-- | Every way the nodes can consume a prefix, as the remaining suffixes.
matchSequence :: [PatternNode] -> String -> [String]
matchSequence [] input = [input]
matchSequence (node : nodes) input = concatMap (matchSequence nodes) (matchNode node input)

matchNode :: PatternNode -> String -> [String]
matchNode node input = case node of
  PatternLiteral expected -> case input of
    character : rest | character == expected -> [rest]
    _ -> []
  PatternClass allowed -> case input of
    character : rest | character `elem` allowed -> [rest]
    _ -> []
  PatternGroup alternatives -> concatMap (`matchSequence` input) alternatives
  PatternRepeat inner lower upper -> repeatNode inner lower upper input

-- | Each further repetition must consume input, so an unbounded repeat of a
-- pattern that can match the empty string still terminates.
repeatNode :: PatternNode -> Int -> Maybe Int -> String -> [String]
repeatNode inner lower upper input
  | lower > 0 = concatMap (repeatNode inner (lower - 1) (subtract 1 <$> upper)) (matchNode inner input)
  | upper == Just 0 = [input]
  | otherwise = input : concatMap continue (matchNode inner input)
  where
    continue rest
      | length rest < length input = repeatNode inner 0 (subtract 1 <$> upper) rest
      | otherwise = []

validDateTime :: Text -> Bool
validDateTime value
  | Text.isSuffixOf "Z" value = case parseTimeM True defaultTimeLocale "%Y-%m-%dT%H:%M:%S%QZ" (Text.unpack value) :: Maybe UTCTime of
      Just _ -> True
      Nothing -> False
  | otherwise = case parseTimeM True defaultTimeLocale "%Y-%m-%dT%H:%M:%S%Q%z" (Text.unpack value) :: Maybe ZonedTime of
      Just _ -> True
      Nothing -> False

resolvePointer :: Value -> Text -> Maybe Value
resolvePointer root pointer
  | pointer == "#" = Just root
  | Just path <- Text.stripPrefix "#/" pointer =
      resolveSegments root (map decodePointerSegment (Text.splitOn "/" path))
  | otherwise = Nothing

resolveSegments :: Value -> [Text] -> Maybe Value
resolveSegments value [] = Just value
resolveSegments (Object fields) (segment : rest) =
  KeyMap.lookup (Key.fromText segment) fields >>= (`resolveSegments` rest)
resolveSegments (Array values) (segment : rest) = do
  index <- readNatural segment
  value <- atMay (toList values) index
  resolveSegments value rest
resolveSegments _ _ = Nothing

atMay :: [a] -> Int -> Maybe a
atMay values index
  | index < 0 = Nothing
  | otherwise = case drop index values of
      value : _ -> Just value
      [] -> Nothing

decodePointerSegment :: Text -> Text
decodePointerSegment = replace "~1" "/" . replace "~0" "~"

replace :: Text -> Text -> Text -> Text
replace needle replacement input
  | Text.null needle = input
  | otherwise = case Text.breakOn needle input of
      (before, after)
        | Text.null after -> input
        | otherwise -> before <> replacement <> replace needle replacement (Text.drop (Text.length needle) after)

readNatural :: Text -> Maybe Int
readNatural value
  | Text.null value = Nothing
  | Text.head value == '0' && Text.length value > 1 = Nothing
  | Text.all (\character -> '0' <= character && character <= '9') value =
      case reads (Text.unpack value) of
        [(index, "")] -> Just index
        _ -> Nothing
  | otherwise = Nothing

validateContractContext :: KeyMap.KeyMap Value -> Either Text ()
validateContractContext context = do
  rejectContextDirectives "contract context" context
  mapM_ validateTermDefinition (KeyMap.toList context)
  pure ()

validateTermDefinition :: (Key.Key, Value) -> Either Text ()
validateTermDefinition (key, value)
  | Key.toText key `elem` coreContextNames =
      Left ("contract context redefines core term or prefix: " <> Key.toText key)
  | Text.null (Key.toText key) || Text.head (Key.toText key) == '@' =
      Left "contract context term name must be non-empty and not a directive"
  | otherwise = case value of
      String term
        | Text.null term -> Left "contract context term definition must not be empty"
        | otherwise -> pure ()
      Object definition -> validateTermDefinitionObject definition
      _ -> Left "contract context term definition must be a string or object"

validateTermDefinitionObject :: KeyMap.KeyMap Value -> Either Text ()
validateTermDefinitionObject definition = do
  let allowed = ["@id", "@type"]
      unknown = filter (`notElem` allowed) (map (Key.toText . fst) (KeyMap.toList definition))
  case unknown of
    [] -> pure ()
    names -> Left ("contract context definition has unknown keys: " <> Text.intercalate ", " names)
  identifier <- requiredString "@id" definition
  if Text.null identifier
    then Left "contract context definition @id must not be empty"
    else pure ()
  case KeyMap.lookup "@type" definition of
    Nothing -> pure ()
    Just (String value)
      | Text.null value -> Left "contract context definition @type must not be empty"
      | otherwise -> pure ()
    Just _ -> Left "contract context definition @type must be a string"

coreContextNames :: [Text]
coreContextNames =
  [ "arch", "as", "dcterms", "prov", "rdf", "rdfs", "xsd"
  , "spec", "revision_id", "object_id", "shape", "by", "agent", "at"
  , "payload", "refs", "supersedes", "source", "contracts", "contract"
  , "reference", "archive_id", "layout", "artifact", "entity", "claim"
  , "assessment", "kind", "label", "person", "organisation", "software"
  , "model", "process", "role", "id", "namespace", "name", "revision"
  , "content", "digest", "size", "schema", "version", "protocol", "records"
  , "identities", "contents", "expected_heads", "expected", "annex_key"
  , "staging_remote", "skos", "musical_work", "recording_session", "release", "recording"
  ]

rejectContextDirectives :: Text -> KeyMap.KeyMap Value -> Either Text ()
rejectContextDirectives label fields = do
  let forbidden = filter (`KeyMap.member` fields) ["@context", "@import"]
  case forbidden of
    [] -> pure ()
    names -> Left (label <> " contains forbidden directive(s): "
      <> Text.intercalate ", " (map Key.toText names))
  mapM_ (rejectNestedDirectives label . snd) (KeyMap.toList fields)

rejectNestedDirectives :: Text -> Value -> Either Text ()
rejectNestedDirectives label (Object fields) = rejectContextDirectives label fields
rejectNestedDirectives label (Array values) = mapM_ (rejectNestedDirectives label) (toList values)
rejectNestedDirectives _ _ = pure ()

requiredKey :: KeyMap.KeyMap Value -> Text -> Either Text ()
requiredKey fields name
  | KeyMap.member (Key.fromText name) fields = Right ()
  | otherwise = Left ("core context is missing " <> name)

requireExactKeys :: Text -> [Text] -> KeyMap.KeyMap Value -> Either Text ()
requireExactKeys label expected fields =
  let actual = sort (map (Key.toText . fst) (KeyMap.toList fields))
      wanted = sort expected
  in if actual == wanted
       then Right ()
       else Left (label <> " must contain exactly " <> Text.intercalate ", " wanted)

objectValue :: Text -> Value -> Either Text (KeyMap.KeyMap Value)
objectValue _label (Object fields) = Right fields
objectValue label _ = Left (label <> " must be a JSON object")

requiredObject :: Text -> KeyMap.KeyMap Value -> Either Text (KeyMap.KeyMap Value)
requiredObject name fields = maybe (Left (name <> " is required")) (objectValue name)
  (KeyMap.lookup (Key.fromText name) fields)

requiredValue :: Text -> KeyMap.KeyMap Value -> Either Text Value
requiredValue name fields = maybe (Left (name <> " is required")) Right
  (KeyMap.lookup (Key.fromText name) fields)

requiredString :: Text -> KeyMap.KeyMap Value -> Either Text Text
requiredString name fields = case KeyMap.lookup (Key.fromText name) fields of
  Just (String value) | not (Text.null value) -> Right value
  Just (String _) -> Left (name <> " must not be empty")
  Just _ -> Left (name <> " must be a string")
  Nothing -> Left (name <> " is required")

requiredBool :: Text -> KeyMap.KeyMap Value -> Either Text Bool
requiredBool name fields = case KeyMap.lookup (Key.fromText name) fields of
  Just (Bool value) -> Right value
  Just _ -> Left (name <> " must be a boolean")
  Nothing -> Left (name <> " is required")

expect :: Text -> Text -> Text -> Either Text ()
expect label wanted actual
  | actual == wanted = Right ()
  | otherwise = Left (label <> " must be " <> wanted)

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
      actualDigest = "sha256:" <> hex (SHA256.hashlazy bytes)
  if actualDigest /= digestText
    then Left "contract bytes do not match cited digest"
    else if canonical /= bytes
      then Left "contract bytes are not RFC 8785 canonical"
      else validateBundleValue expectedId expectedVersion digestText value

hex :: ByteString.ByteString -> Text
hex = Text.pack . concatMap byteHex . ByteString.unpack
  where
    byteHex byte = [digits !! fromIntegral (byte `div` 16), digits !! fromIntegral (byte `mod` 16)]
    digits = "0123456789abcdef"
