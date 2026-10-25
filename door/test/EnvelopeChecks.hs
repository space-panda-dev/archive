{-# LANGUAGE OverloadedStrings #-}

-- | Checks of identity and the envelope: RFC 8785 numbers, UUIDv5 allocation,
-- the identifier smart constructors, and single drafts and batches through
-- the dry-run validator (Archive.Door.Types, Canonical, Identity, References).
module EnvelopeChecks
  ( tests
  ) where

import Archive.Door.Canonical (canonicalizeDraft)
import Archive.Door.Identity (parseIdentityName, parseIdentityNamespace, uuidV5)
import Archive.Door.Json (decodeUnique)
import Archive.Door.Types
  ( parseRevisionId
  , revisionIdText
  , agentIdText
  , objectIdText
  , parseStoredEnvelope
  , parseAgentId
  , parseObjectId
  , parseProgramId
  , programIdText
  )
import qualified Data.Aeson as Aeson
import qualified Data.Aeson.KeyMap as KeyMap
import Data.Aeson.RFC8785 (encodeCanonical)
import qualified Data.ByteString.Lazy.Char8 as LazyByteString
import qualified Data.Text as Text
import Data.Word (Word64)
import Fixtures
import GHC.Float (castWord64ToDouble)
import IdentityVectors (identityVectorChecks)
import Numeric (showHex)
import Rfc8785Vectors (rfc8785Numbers)
import Support
import System.Exit (ExitCode (ExitSuccess))
import Test.Tasty (TestTree, testGroup)
import Test.Tasty.HUnit (testCase)

assessmentDraft :: String -> String -> String
assessmentDraft objectId target = envelope objectId "assessment" "{}"
  ("[{\"role\":\"assessment_of\",\"id\":\"" <> target <> "\"}]")
  (",\"contracts\":[{\"id\":\"archive.test/example\",\"version\":\"1\",\"digest\":\"" <> zeroDigest <> "\"}]")

claimWithRevision :: String -> String -> String -> String
claimWithRevision objectId target revision = replace "}]" (",\"revision_id\":\"" <> revision <> "\"}]" ) (claimDraft objectId target)

assessmentWithPins :: String -> String -> String -> String
assessmentWithPins objectId target revision = replace "}]" (",\"revision_id\":\"" <> revision <> "\"}]" ) (assessmentDraft objectId target)

rfc8785NumberChecks :: TestTree
rfc8785NumberChecks = testGroup "rfc8785NumberChecks"
  [ testCase ("RFC 8785 Appendix B number " <> showHexWord64 bits) (check (bits, expected))
  | (bits, expected) <- rfc8785Numbers ]
  where
    check (bits, expected) = do
      let original = case decodeUnique (LazyByteString.pack (claimDraft claim entity)) of
            Right (Aeson.Object fields) ->
              case KeyMap.lookup "payload" fields of
                Just (Aeson.Object payload) ->
                  Right (Aeson.Object (KeyMap.insert "payload"
                    (Aeson.Object (KeyMap.insert "jcs_number"
                      (Aeson.toJSON (castWord64ToDouble bits :: Double)) payload)) fields))
                _ -> Left "claim draft payload is not an object"
            Right _ -> Left "claim draft is not an object"
            Left problem -> Left (Text.pack problem)
          original :: Either Text.Text Aeson.Value
          canonicalNumber :: Either Text.Text Aeson.Value
          canonicalNumber = do
            draft <- original
            (_, normalized) <- canonicalizeDraft draft
            case normalized of
              Aeson.Object fields -> case KeyMap.lookup "payload" fields of
                Just (Aeson.Object payload) ->
                  maybe (Left "normalized payload omitted jcs_number") Right
                    (KeyMap.lookup "jcs_number" payload)
                _ -> Left "normalized record payload is not an object"
              _ -> Left "normalized record is not an object"
          actual = fmap encodeCanonical canonicalNumber
      assert ("RFC 8785 Appendix B number " <> showHexWord64 bits)
        (actual == Right (LazyByteString.pack expected)) ExitSuccess
        (either Text.unpack LazyByteString.unpack actual) expected

showHexWord64 :: Word64 -> String
showHexWord64 value = replicate (16 - length rendered) '0' <> rendered
  where
    rendered = showHex value ""

-- | UUIDv5 allocation is total: a bad archive UUID is a Left, not a crash.
uuidV5Checks :: IO ()
uuidV5Checks = do
  assert "UUIDv5 refuses an invalid archive UUID without throwing"
    (case (parseIdentityNamespace "archive.core/entity", parseIdentityName "person") of
      (Right namespace, Right name) -> isLeft (uuidV5 "not-a-uuid" namespace name)
      _ -> False)
    ExitSuccess "" ""

-- | The identifier smart constructors and the stored envelope's revision.
identifierChecks :: Recorder -> IO ()
identifierChecks check = do
  check "object identifier smart constructor accepts a canonical UUID URN"
    ((objectIdText <$> parseObjectId (Text.pack entity)) == Right (Text.pack entity))
    ExitSuccess "" ""
  check "object identifier smart constructor rejects a hostname URL"
    (case parseObjectId "https://host.example/object" of Left _ -> True; Right _ -> False)
    ExitSuccess "" ""
  check "object identifier smart constructor rejects a non-UUID token"
    (parseObjectId "local-object" == Left "object_id must be a lowercase urn:uuid identifier")
    ExitSuccess "" ""
  check "agent identifier smart constructor accepts an object UUID"
    ((agentIdText <$> parseAgentId (Text.pack person)) == Right (Text.pack person))
    ExitSuccess "" ""
  check "agent identifier smart constructor accepts a canonical Package URL"
    ((agentIdText <$> parseAgentId (Text.pack writer)) == Right (Text.pack writer))
    ExitSuccess "" ""
  check "agent identifier smart constructor rejects a hostname"
    (case parseAgentId "host.example.com" of Left _ -> True; Right _ -> False)
    ExitSuccess "" ""
  check "program identifier smart constructor accepts a canonical Package URL"
    ((programIdText <$> parseProgramId (Text.pack writer)) == Right (Text.pack writer))
    ExitSuccess "" ""
  check "program identifier smart constructor rejects a hostname URL"
    (case parseProgramId "https://host.example/archive" of Left _ -> True; Right _ -> False)
    ExitSuccess "" ""
  check "revision identifier smart constructor accepts a canonical digest"
    (fmap revisionIdText (parseRevisionId (Text.pack zeroDigest)) == Right (Text.pack zeroDigest))
    ExitSuccess "" ""
  check "revision identifier smart constructor rejects malformed digests"
    (all (isLeft . parseRevisionId) ["", "sha256:abcd", "sha256:" <> Text.replicate 64 "A", "not-a-digest"])
    ExitSuccess "" ""
  check "stored envelope parsing rejects an invalid revision identifier"
    (case decodeUnique (LazyByteString.pack
      (withField (withField (entityDraft entity) "revision_id" "not-a-digest")
        "at" "2026-01-01T00:00:00Z")) of
      Right value -> parseStoredEnvelope value
        == Left "revision_id must be sha256 followed by 64 lowercase hexadecimal characters"
      Left _ -> False)
    ExitSuccess "" ""

-- | Single drafts through the dry-run validator: what an envelope admits
-- and the exact refusal of each malformed one.
envelopeChecks :: TestTree
envelopeChecks = testGroup "envelopeChecks"
  [ succeedsCase "dry-run uses RFC 8785 identity and retains spec" (entityDraft entity)
  , succeedsCase "dry-run canonicalises the closed artifact payload" (artifactDraft entity)
  , succeedsCase "specification 0.0.5 admits its installed musical_work kind"
      (replace "{\"kind\":\"person\"" "{\"kind\":\"musical_work\"" (entityDraft entity))
  , refusesCase "specification 0.0.2 refuses the later musical_work kind"
      (replace "archive/0.0.12" "archive/0.0.2"
        (replace "{\"kind\":\"person\"" "{\"kind\":\"musical_work\"" (entityDraft entity)))
      "entity kind is not defined by the core context"
  , refusesCase "a supplied recording time is not a draft" (withField (entityDraft entity) "at" "1900-01-01T00:00:00Z") "draft envelope must omit at"
  , refusesCase "a supplied revision is not a draft" (withField (entityDraft entity) "revision_id" zeroDigest) "draft envelope must omit revision_id"
  , comparesDifferentCase "the specification version remains in revision identity" (entityDraft entity) (replace "archive/0.0.12" "archive/0.0.2" (entityDraft entity))
  , refusesCase "an empty batch is not a successful admission" "[]" "admission batch must contain at least one record"
  , refusesCase "a malformed specification identifier is refused" (replace "archive/0.0.12" "not-an-archive-spec" (entityDraft entity)) "spec must be archive/ followed by a three-part numeric version"
  , refusesCase "an object identifier containing a hostname is refused" (replace entity "http://host.example/path" (entityDraft entity)) "object_id must not contain a hostname"
  , refusesCase "a non-UUID object identifier is refused" (replace entity "local-object" (entityDraft entity)) "object_id must be a lowercase urn:uuid identifier"
  , refusesCase "draft field errors keep precedence over object UUID validation"
      (withField (replace entity "local-object" (entityDraft entity)) "revision_id" zeroDigest)
      "draft envelope must omit revision_id"
  , refusesCase "a non-UUID reference identifier is refused" (claimDraft claim "local-object") "object_id must be a lowercase urn:uuid identifier"
  , refusesCase "a malformed referenced revision is refused at the envelope boundary"
      (claimWithRevision claim entity "not-a-digest")
      "revision_id must be sha256 followed by 64 lowercase hexadecimal characters"
  , refusesCase "an agent identifier containing a hostname is refused" (replace person "host.example.com" (entityDraft entity)) "agent must be a canonical Package URL"
  , refusesCase "an invalid program identifier cannot cross the envelope boundary" (replace writer "https://host.example/archive" (entityDraft entity)) "by must be a canonical Package URL"
  , refusesCase "a malformed supplied recording time is refused" (withField (entityDraft entity) "at" "[]") "at must be a non-empty string when supplied"
  , refusesCase "an envelope without an agent is refused" (removeField (entityDraft entity) "agent") "agent is required"
  , refusesCase "an envelope field outside P-2 is refused" (insertBeforeEnd (entityDraft entity) ",\"location\":{}") "not allowed in an envelope: location"
  , refusesCase "the obsolete decision shape is refused" (replace "\"shape\":\"entity\"" "\"shape\":\"decision\"" (entityDraft entity)) "shape must be artifact, entity, claim, or assessment"
  , refusesCase "an envelope without an object identity is refused" (removeField (entityDraft entity) "object_id") "object_id is required"
  , refusesCase "supersession must pin an exact revision" (replace "\"supersedes\":[]" ("\"supersedes\":[{\"role\":\"supersedes\",\"id\":\"" <> entity <> "\"}]" ) (entityDraft entity)) "supersedes reference must pin revision_id"
  , refusesCase "a reference with predecessor-only metadata is refused" (replace "}],\"supersedes\"" ",\"resolution\":\"unresolved\"}],\"supersedes\"" (claimDraft claim entity)) "not allowed in a refs reference: resolution"
  , refusesCase "a claim without a contract citation is refused" (removeField (claimDraft claim entity) "contracts") "claim requires at least one contract citation"
  , refusesCase "an assessment must pin the exact claim revision it judges" (replace "\"shape\":\"claim\"" "\"shape\":\"assessment\"" (claimDraft claim entity)) "assessment requires a pinned assessment_of reference"
  , refusesCase "a malformed contract digest is refused" (replace zeroDigest "sha256:bad" (claimDraft claim entity)) "contract digest must be sha256 followed by 64 lowercase hexadecimal characters"
  , refusesCase "duplicate JSON object keys are refused" (replace "\"object_id\":\"" ("\"object_id\":\"" <> entity <> "\",\"object_id\":\"") (entityDraft entity)) "duplicate JSON object key: object_id"
  , refusesCase "an envelope without its writer is refused" (removeField (entityDraft entity) "by") "by is required"
  , refusesCase "a dangling reference is refused" (claimDraft claim missingObject) ("reference " <> missingObject <> " does not name an admitted object")
  , refusesSilentlyCase "claim contract validation stays unsupported without an initialized archive" ("[" <> entityDraft entity <> "," <> claimDraft claim entity <> "]") "claim contracts require an initialized archive"
  ]

-- | Batches that cite the base revision of an entity computed first, so the
-- revision and the checks that pin it stay one case.
batchRevisionChecks :: IO ()
batchRevisionChecks = do
  baseRevision <- revisionOf (entityDraft entity)
  refuses "a revision cannot supersede another object" ("[" <> entityDraft entity <> "," <> superseding claim entity baseRevision <> "]") "a revision may supersede only the same object"
  accepts "a revision may explicitly supersede the same object and shape" ("[" <> entityDraft entity <> "," <> superseding entity entity baseRevision <> "]")
  refuses "an existing object cannot gain an implicit revision" ("[" <> entityDraft entity <> "," <> replace "\"label\":\"test\"" "\"label\":\"silent replacement\"" (entityDraft entity) <> "]") "an existing object requires explicit supersession"
  refuses "an assessment cannot judge an entity revision" ("[" <> entityDraft entity <> "," <> assessmentWithPins assessment entity baseRevision <> "]") "assessment_of must pin a claim revision"
  refuses "every assessment target must pin an exact claim revision" ("[" <> entityDraft entity <> "," <> claimDraft claim entity <> "," <> replace "}],\"supersedes\"" ("},{\"role\":\"assessment_of\",\"id\":\"" <> claim <> "\"}],\"supersedes\"") (assessmentWithPins assessment claim baseRevision) <> "]") "assessment requires a pinned assessment_of reference"
  refusesSilently "an unavailable contract never looks like a valid claim" ("[" <> entityDraft entity <> "," <> replace "archive.test/example" "does.not.exist" (replace "\"version\":\"1\"" "\"version\":\"999\"" (claimDraft claim entity)) <> "]") "claim contracts require an initialized archive"
  refuses "an exact pin must belong to the named object" ("[" <> entityDraft entity <> "," <> claimWithRevision claim entity zeroDigest <> "]") ("reference revision " <> zeroDigest <> " is not a revision of object " <> entity)
  refuses "supersession cannot change an object's shape" ("[" <> entityDraft entity <> "," <> replace "\"supersedes\":[]" ("\"supersedes\":[{\"role\":\"supersedes\",\"id\":\"" <> entity <> "\",\"revision_id\":\"" <> baseRevision <> "\"}]" ) (artifactDraft entity) <> "]") "a revision may supersede only the same shape"
  accepts "an identical revision is an idempotent retry" ("[" <> entityDraft entity <> "," <> entityDraft entity <> "]")
  refusesSilently "a refused batch emits no successful prefix" ("[" <> entityDraft entity <> "," <> claimDraft claim missingObject <> "]") ("reference " <> missingObject <> " does not name an admitted object")

missingObject :: String
missingObject = "urn:uuid:00000000-0000-4000-8000-000000000099"

accepts :: String -> String -> IO ()
accepts label input = do
  (code, output, errors) <- dryRun input
  assert label (code == ExitSuccess) code output errors

-- | The dry-run and command checks as test cases named by their labels, so a
-- group of independent checks is one case each.
succeedsCase :: String -> String -> TestTree
succeedsCase label input = testCase label (succeeds label input)

refusesCase, refusesSilentlyCase :: String -> String -> String -> TestTree
refusesCase label input expectedError = testCase label (refuses label input expectedError)
refusesSilentlyCase label input expectedError =
  testCase label (refusesSilently label input expectedError)

comparesDifferentCase :: String -> String -> String -> TestTree
comparesDifferentCase label first second = testCase label (comparesDifferent label first second)

comparesDifferent :: String -> String -> String -> IO ()
comparesDifferent label first second = do
  firstRevision <- revisionOf first
  secondRevision <- revisionOf second
  assert label (firstRevision /= secondRevision) ExitSuccess (firstRevision <> "\n" <> secondRevision) ""

withField :: String -> String -> String -> String
withField value field fieldValue = insertBeforeEnd value (",\"" <> field <> "\":" <> jsonValue fieldValue)

jsonValue :: String -> String
jsonValue value | value == "[]" = value
                | otherwise = "\"" <> value <> "\""

removeField :: String -> String -> String
removeField value "contracts" = replace (",\"contracts\":[{\"id\":\"archive.test/example\",\"version\":\"1\",\"digest\":\"" <> zeroDigest <> "\"}]") "" value
removeField value field = case after marker value of
  Nothing -> value
  Just suffix -> replace (marker <> takeWhile (/= '"') suffix <> "\"") "" value
  where marker = ",\"" <> field <> "\":\""

insertBeforeEnd :: String -> String -> String
insertBeforeEnd value addition = take (length value - 1) value <> addition <> "}"

-- | The envelope checks, one case per check or row.
tests :: IO TestTree
tests = fmap (testGroup "envelope") $ sequence
  [ treeOf rfc8785NumberChecks
  , caseOf "uuidV5Checks" uuidV5Checks
  , treeOf (checkGroup "identityVectorChecks" identityVectorChecks)
  , rowsOf "identifierChecks" identifierChecks
  , treeOf envelopeChecks
  , caseOf "batchRevisionChecks" batchRevisionChecks
  ]
