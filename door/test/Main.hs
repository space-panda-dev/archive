{-# LANGUAGE ForeignFunctionInterface #-}
{-# LANGUAGE OverloadedStrings #-}

module Main (main) where

import Archive.GitSnapshot
  ( immutableAdditions
  , linearHistory
  , resolveSnapshot
  , readPath
  , snapshotCommit
  , GitSnapshotError (..)
  , gitSnapshotErrorMessage
  , uniqueIntroducer
  )
import Archive.Executables (resolveAnnexExecutable, resolveGitExecutable)
import Archive.Door.AdmissionPlan (AdmissionPlan (plannedAdmissionSnapshot))
import Archive.Door.Canonical (canonicalizeDraft, finalizeStored)
import Archive.Door.Catalog (loadContractCatalog, parseContractCatalog)
import Archive.Door.Refusal (refusalCodeDescription, refusalCodeText)
import Archive.Door.Bootstrap (BootstrapEnvironment (..), initArchiveWith)
import Archive.Door.MutationError (MutationError (..), mutationErrorMessage)
import Archive.Door.AdmissionDryRun
  ( AdmissionPlanningError (..)
  , dryRunAdmission
  , dryRunAdmissionForCommit
  , recheckAdmissionForCommit
  , forgetSnapshots
  , loadSnapshot
  , RecordEntry (..)
  , SnapshotData (..)
  )
import Archive.Door.TransitionCommit (commitTransition)
import Archive.Door.TransitionDryRun
  ( TransitionPlan (..)
  , TransitionPlanningError (..)
  , dryRunTransition
  , dryRunTransitionForCommit
  , transitionDispatchTable
  , transitionContractsWithoutHandler
  )
import Archive.Door.Identity (parseIdentityName, parseIdentityNamespace, uuidV5)
import Archive.Door.Contracts (validateCoreContextForSpecification, validateContractInstance)
import Archive.Door.Json (decodeUnique)
import BeyondCircle010 (beyondCircle010Checks)
import Rules010 (rules010Checks)
import Specification010 (specification010Checks)
import Rules011 (rules011Checks)
import Specification011 (specification011Checks)
import qualified Data.Map.Strict as CheckpointMap
import Archive.Door.Layout (contentRelativePath, parseSha256Digest, recordRelativePath, specificationLayout)
import Archive.Door.AdmissionCommit (commitAdmission)
import Archive.Door.RecordCommit (commitRecordBatch, prunePackedGitObjects, validateArchiveFilePath, validateRecordPath, withArchiveWriteLock)
import Archive.Door.SpecificationUpgrade (SpecificationRelease (..), additiveUpgrade, compareSpecifications)
import qualified Data.Set as Set
import Archive.Door.VerifiedCheckpoint (VerifiedCheckpoint (..), checkpointCompactAfter, readCheckpoint)
import Archive.Door.Proposal (readAdmissionProposal, readTransitionProposal)
import Archive.Process
  ( ProcessError (..)
  , ProcessStream (..)
  , runProcessBounded
  , runProcessBoundedAt
  )
import Archive.Door.References
  ( StoreIndex (..)
  , emptyIndex
  , insertRecord
  , validateReferences
  )
import Archive.Door.Types
  ( DraftEnvelope (envelopeBody)
  , RecordBody (ClaimRecord)
  , parseRevisionId
  , revisionIdText
  , agentIdText
  , objectIdText
  , parseEnvelope
  , parseStoredEnvelope
  , parseAgentId
  , parseObjectId
  , parseProgramId
  , programIdText
  )
import qualified Crypto.Hash.SHA256 as SHA256
import qualified Data.Aeson as Aeson
import qualified Data.Aeson.KeyMap as KeyMap
import Data.Aeson ((.=))
import Data.Aeson.RFC8785 (encodeCanonical)
import qualified Data.ByteString as ByteString
import qualified Data.ByteString.Lazy.Char8 as LazyByteString
import Data.IORef (newIORef, modifyIORef', readIORef)
import Data.Foldable (foldlM, toList)
import Data.List (isPrefixOf, sort)
import Control.Monad (when)
import qualified Data.Text as Text
import Data.Word (Word64)
import GHC.Float (castWord64ToDouble)
import Numeric (showHex)
import Control.Exception (AsyncException (UserInterrupt), bracket, bracket_, throwIO, try)
import Control.Concurrent (forkIO, threadDelay, throwTo)
import Control.Concurrent.MVar (newEmptyMVar, putMVar, takeMVar, tryReadMVar)
import System.Timeout (timeout)
import System.Exit (ExitCode (ExitFailure, ExitSuccess), exitFailure)
import System.Directory
  ( createDirectory
  , createDirectoryIfMissing
  , doesDirectoryExist
  , createFileLink
  , createDirectoryLink
  , doesFileExist
  , doesPathExist
  , findExecutable
  , listDirectory
  , pathIsSymbolicLink
  , getPermissions
  , removeFile
  , removePathForcibly
  , renameFile
  , renameDirectory
  , setOwnerExecutable
  , setOwnerWritable
  , setPermissions
  )
import System.Environment (getEnvironment, lookupEnv, setEnv, unsetEnv)
import System.Process
  ( CreateProcess (cwd, env, std_err, std_out)
  , StdStream (CreatePipe, NoStream)
  , proc
  , readCreateProcessWithExitCode
  , readProcessWithExitCode
  , terminateProcess
  , waitForProcess
  , withCreateProcess
  )
import qualified Network.Socket as Socket
import qualified Network.Socket.ByteString as SocketBytes
import System.Posix.Files (fileMode, getFileStatus, isNamedPipe, setFileMode)
import System.Posix.IO
  ( OpenFileFlags (directory)
  , OpenMode (ReadOnly)
  , closeFd
  , defaultFileFlags
  , openFd
  )
import Foreign.C.Types (CInt (..))
import System.Posix.Types (Fd (..))
import System.Posix.User (getEffectiveUserID)

foreign import ccall safe "archive_fullsync_fd"
  c_fullsyncFd :: CInt -> IO CInt

foreign import ccall safe "archive_fullsync_directory_fd"
  c_fullsyncDirectoryFd :: CInt -> IO CInt

foreign import ccall safe "archive_sync_tree"
  c_syncTree :: CInt -> IO CInt

-- The proposal tests use the public command interface, anchoring the dispatch
-- chain. Focused envelope tests call the same library mechanisms directly.

writer, person, entity, claim, assessment, zeroDigest, otherDigest :: String
writer = "pkg:hackage/archive@0.0.1.0"
person = "urn:uuid:00000000-0000-4000-8000-000000000001"
entity = "urn:uuid:00000000-0000-4000-8000-000000000002"
claim = "urn:uuid:00000000-0000-4000-8000-000000000003"
assessment = "urn:uuid:00000000-0000-4000-8000-000000000004"
zeroDigest = "sha256:0000000000000000000000000000000000000000000000000000000000000000"
otherDigest = "sha256:1111111111111111111111111111111111111111111111111111111111111111"

entityDraft, artifactDraft :: String -> String
entityDraft objectId = envelope objectId "entity" "{\"kind\":\"person\",\"label\":\"test\"}" "[]" ""
artifactDraft objectId = envelope objectId "artifact" ("{\"content\":{\"digest\":\"" <> zeroDigest <> "\",\"size\":0}}") "[]" ""

claimDraft :: String -> String -> String
claimDraft objectId target = envelope objectId "claim" "{}"
  ("[{\"role\":\"about\",\"id\":\"" <> target <> "\"}]")
  (",\"contracts\":[{\"id\":\"archive.test/example\",\"version\":\"1\",\"digest\":\"" <> zeroDigest <> "\"}]")

assessmentDraft :: String -> String -> String
assessmentDraft objectId target = envelope objectId "assessment" "{}"
  ("[{\"role\":\"assessment_of\",\"id\":\"" <> target <> "\"}]")
  (",\"contracts\":[{\"id\":\"archive.test/example\",\"version\":\"1\",\"digest\":\"" <> zeroDigest <> "\"}]")

claimWithRevision :: String -> String -> String -> String
claimWithRevision objectId target revision = replace "}]" (",\"revision_id\":\"" <> revision <> "\"}]" ) (claimDraft objectId target)

assessmentWithPins :: String -> String -> String -> String
assessmentWithPins objectId target revision = replace "}]" (",\"revision_id\":\"" <> revision <> "\"}]" ) (assessmentDraft objectId target)

envelope :: String -> String -> String -> String -> String -> String
envelope objectId shape payload refs extra =
  "{\"spec\":\"archive/0.0.11\",\"object_id\":\"" <> objectId
  <> "\",\"shape\":\"" <> shape <> "\",\"by\":\"" <> writer
  <> "\",\"agent\":\"" <> person <> "\",\"payload\":" <> payload
  <> ",\"refs\":" <> refs <> ",\"supersedes\":[]" <> extra <> "}"

recordPathChecks :: IO ()
recordPathChecks = do
  let valid = recordRelative zeroDigest
  assert "the record layout fans out by the digest's first two character pairs (ADR 0053)"
    (valid == "records/00/00/sha256-0000000000000000000000000000000000000000000000000000000000000000.json")
    ExitSuccess valid ""
  assert "RecordCommit accepts a digest-addressed relative record path"
    (either (const False) (const True) (validateRecordPath valid))
    ExitSuccess "" ""
  assert "RecordCommit refuses the flat archive/1 record path"
    (isLeft (validateRecordPath "records/sha256-0000000000000000000000000000000000000000000000000000000000000000.json"))
    ExitSuccess "" ""
  assert "RecordCommit refuses a fan-out that does not match the digest"
    (isLeft (validateRecordPath "records/01/00/sha256-0000000000000000000000000000000000000000000000000000000000000000.json"))
    ExitSuccess "" ""
  assert "RecordCommit rejects traversal at the mutation seam"
    (isLeft (validateRecordPath "records/../outside.json"))
    ExitSuccess "" ""
  assert "RecordCommit rejects absolute paths at the mutation seam"
    (isLeft (validateRecordPath "/tmp/outside.json"))
    ExitSuccess "" ""
  assert "RecordCommit rejects a malformed digest path at the mutation seam"
    (isLeft (validateRecordPath "records/not-a-digest.json"))
    ExitSuccess "" ""

rfc8785NumberChecks :: IO ()
rfc8785NumberChecks = mapM_ check rfc8785Numbers
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

rfc8785Numbers :: [(Word64, String)]
rfc8785Numbers =
  [ (0x0000000000000000, "0")
  , (0x8000000000000000, "0")
  , (0x0000000000000001, "5e-324")
  , (0x8000000000000001, "-5e-324")
  , (0x7fefffffffffffff, "1.7976931348623157e+308")
  , (0xffefffffffffffff, "-1.7976931348623157e+308")
  , (0x4340000000000000, "9007199254740992")
  , (0xc340000000000000, "-9007199254740992")
  , (0x4430000000000000, "295147905179352830000")
  , (0x44b52d02c7e14af5, "9.999999999999997e+22")
  , (0x44b52d02c7e14af6, "1e+23")
  , (0x44b52d02c7e14af7, "1.0000000000000001e+23")
  , (0x444b1ae4d6e2ef4e, "999999999999999700000")
  , (0x444b1ae4d6e2ef4f, "999999999999999900000")
  , (0x444b1ae4d6e2ef50, "1e+21")
  , (0x3eb0c6f7a0b5ed8c, "9.999999999999997e-7")
  , (0x3eb0c6f7a0b5ed8d, "0.000001")
  , (0x41b3de4355555553, "333333333.3333332")
  , (0x41b3de4355555554, "333333333.33333325")
  , (0x41b3de4355555555, "333333333.3333333")
  , (0x41b3de4355555556, "333333333.3333334")
  , (0x41b3de4355555557, "333333333.33333343")
  , (0xbecbf647612f3696, "-0.0000033333333333333333")
  , (0x43143ff3c1cb0959, "1424953923781206.2")
  ]

main :: IO ()
main = do
  configureExecutablePaths
  -- The fixtures commit with plain `git`, which can otherwise start Git's
  -- maintenance detached in the background and repack an archive while a
  -- check reads it. Door's own Git commands turn it off themselves.
  mapM_ (uncurry setEnv)
    [ ("GIT_CONFIG_COUNT", "2")
    , ("GIT_CONFIG_KEY_0", "maintenance.auto"), ("GIT_CONFIG_VALUE_0", "false")
    , ("GIT_CONFIG_KEY_1", "gc.auto"), ("GIT_CONFIG_VALUE_1", "0")
    ]
  withTemporaryRoot directoryBarrierChecks
  recordPathChecks
  rfc8785NumberChecks
  assert "UUIDv5 refuses an invalid archive UUID without throwing"
    (case (parseIdentityNamespace "archive.core/entity", parseIdentityName "person") of
      (Right namespace, Right name) -> isLeft (uuidV5 "not-a-uuid" namespace name)
      _ -> False)
    ExitSuccess "" ""
  versionedContextChecks
  audioContractChecks
  assert "object identifier smart constructor accepts a canonical UUID URN"
    ((objectIdText <$> parseObjectId (Text.pack entity)) == Right (Text.pack entity))
    ExitSuccess "" ""
  assert "object identifier smart constructor rejects a hostname URL"
    (case parseObjectId "https://host.example/object" of Left _ -> True; Right _ -> False)
    ExitSuccess "" ""
  assert "object identifier smart constructor rejects a non-UUID token"
    (parseObjectId "local-object" == Left "object_id must be a lowercase urn:uuid identifier")
    ExitSuccess "" ""
  assert "agent identifier smart constructor accepts an object UUID"
    ((agentIdText <$> parseAgentId (Text.pack person)) == Right (Text.pack person))
    ExitSuccess "" ""
  assert "agent identifier smart constructor accepts a canonical Package URL"
    ((agentIdText <$> parseAgentId (Text.pack writer)) == Right (Text.pack writer))
    ExitSuccess "" ""
  assert "agent identifier smart constructor rejects a hostname"
    (case parseAgentId "host.example.com" of Left _ -> True; Right _ -> False)
    ExitSuccess "" ""
  assert "program identifier smart constructor accepts a canonical Package URL"
    ((programIdText <$> parseProgramId (Text.pack writer)) == Right (Text.pack writer))
    ExitSuccess "" ""
  assert "program identifier smart constructor rejects a hostname URL"
    (case parseProgramId "https://host.example/archive" of Left _ -> True; Right _ -> False)
    ExitSuccess "" ""
  assert "revision identifier smart constructor accepts a canonical digest"
    (fmap revisionIdText (parseRevisionId (Text.pack zeroDigest)) == Right (Text.pack zeroDigest))
    ExitSuccess "" ""
  assert "revision identifier smart constructor rejects malformed digests"
    (all (isLeft . parseRevisionId) ["", "sha256:abcd", "sha256:" <> Text.replicate 64 "A", "not-a-digest"])
    ExitSuccess "" ""
  assert "stored envelope parsing rejects an invalid revision identifier"
    (case decodeUnique (LazyByteString.pack
      (withField (withField (entityDraft entity) "revision_id" "not-a-digest")
        "at" "2026-01-01T00:00:00Z")) of
      Right value -> parseStoredEnvelope value
        == Left "revision_id must be sha256 followed by 64 lowercase hexadecimal characters"
      Left _ -> False)
    ExitSuccess "" ""
  succeeds "dry-run uses RFC 8785 identity and retains spec" (entityDraft entity)
  succeeds "dry-run canonicalises the closed artifact payload" (artifactDraft entity)
  succeeds "specification 0.0.5 admits its installed musical_work kind"
    (replace "{\"kind\":\"person\"" "{\"kind\":\"musical_work\"" (entityDraft entity))
  refuses "specification 0.0.2 refuses the later musical_work kind"
    (replace "archive/0.0.11" "archive/0.0.2"
      (replace "{\"kind\":\"person\"" "{\"kind\":\"musical_work\"" (entityDraft entity)))
    "entity kind is not defined by the core context"
  refuses "a supplied recording time is not a draft" (withField (entityDraft entity) "at" "1900-01-01T00:00:00Z") "draft envelope must omit at"
  refuses "a supplied revision is not a draft" (withField (entityDraft entity) "revision_id" zeroDigest) "draft envelope must omit revision_id"
  comparesDifferent "the specification version remains in revision identity" (entityDraft entity) (replace "archive/0.0.11" "archive/0.0.2" (entityDraft entity))
  refuses "an empty batch is not a successful admission" "[]" "admission batch must contain at least one record"
  refuses "a malformed specification identifier is refused" (replace "archive/0.0.11" "not-an-archive-spec" (entityDraft entity)) "spec must be archive/ followed by a three-part numeric version"
  refuses "an object identifier containing a hostname is refused" (replace entity "http://host.example/path" (entityDraft entity)) "object_id must not contain a hostname"
  refuses "a non-UUID object identifier is refused" (replace entity "local-object" (entityDraft entity)) "object_id must be a lowercase urn:uuid identifier"
  refuses "draft field errors keep precedence over object UUID validation"
    (withField (replace entity "local-object" (entityDraft entity)) "revision_id" zeroDigest)
    "draft envelope must omit revision_id"
  refuses "a non-UUID reference identifier is refused" (claimDraft claim "local-object") "object_id must be a lowercase urn:uuid identifier"
  refuses "a malformed referenced revision is refused at the envelope boundary"
    (claimWithRevision claim entity "not-a-digest")
    "revision_id must be sha256 followed by 64 lowercase hexadecimal characters"
  refuses "an agent identifier containing a hostname is refused" (replace person "host.example.com" (entityDraft entity)) "agent must be a canonical Package URL"
  refuses "an invalid program identifier cannot cross the envelope boundary" (replace writer "https://host.example/archive" (entityDraft entity)) "by must be a canonical Package URL"
  refuses "a malformed supplied recording time is refused" (withField (entityDraft entity) "at" "[]") "at must be a non-empty string when supplied"
  refuses "an envelope without an agent is refused" (removeField (entityDraft entity) "agent") "agent is required"
  refuses "an envelope field outside P-2 is refused" (insertBeforeEnd (entityDraft entity) ",\"location\":{}") "not allowed in an envelope: location"
  refuses "the obsolete decision shape is refused" (replace "\"shape\":\"entity\"" "\"shape\":\"decision\"" (entityDraft entity)) "shape must be artifact, entity, claim, or assessment"
  refuses "an envelope without an object identity is refused" (removeField (entityDraft entity) "object_id") "object_id is required"
  refuses "supersession must pin an exact revision" (replace "\"supersedes\":[]" ("\"supersedes\":[{\"role\":\"supersedes\",\"id\":\"" <> entity <> "\"}]" ) (entityDraft entity)) "supersedes reference must pin revision_id"
  refuses "a reference with predecessor-only metadata is refused" (replace "}],\"supersedes\"" ",\"resolution\":\"unresolved\"}],\"supersedes\"" (claimDraft claim entity)) "not allowed in a refs reference: resolution"
  refuses "a claim without a contract citation is refused" (removeField (claimDraft claim entity) "contracts") "claim requires at least one contract citation"
  refuses "an assessment must pin the exact claim revision it judges" (replace "\"shape\":\"claim\"" "\"shape\":\"assessment\"" (claimDraft claim entity)) "assessment requires a pinned assessment_of reference"
  refuses "a malformed contract digest is refused" (replace zeroDigest "sha256:bad" (claimDraft claim entity)) "contract digest must be sha256 followed by 64 lowercase hexadecimal characters"
  refuses "duplicate JSON object keys are refused" (replace "\"object_id\":\"" ("\"object_id\":\"" <> entity <> "\",\"object_id\":\"") (entityDraft entity)) "duplicate JSON object key: object_id"
  refuses "an envelope without its writer is refused" (removeField (entityDraft entity) "by") "by is required"
  refuses "a dangling reference is refused" (claimDraft claim missingObject) ("reference " <> missingObject <> " does not name an admitted object")
  refusesSilently "claim contract validation stays unsupported without an initialized archive" ("[" <> entityDraft entity <> "," <> claimDraft claim entity <> "]") "claim contracts require an initialized archive"

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

  subprocessCaptureLimitChecks
  typedPlannerErrorChecks

  withTemporaryRoot initChecks
  withGitHistoryRoot gitSnapshotHistoryChecks
  withGitHistoryRoot gitSnapshotRootSwapChecks
  withGitHistoryRoot configuredGitExecutableChecks
  withProposalTemporaryRoot proposalChecks
  withProposalTemporaryRoot worldContractAdmissionChecks
  withProposalTemporaryRoot reviewContractAdmissionChecks
  reviewContractProfileChecks
  withProposalTemporaryRoot migrationContractAdmissionChecks
  migrationContractProfileChecks
  withProposalTemporaryRoot derivationContractAdmissionChecks
  derivationContractProfileChecks
  withProposalTemporaryRoot windowMatchAdmissionChecks
  windowMatchProfileChecks
  musicAndCreditProfileChecks
  specification010Checks >>= mapM_ (\(label, passed, detail) -> assert label passed ExitSuccess detail "")
  rules010Checks >>= mapM_ (\(label, passed, detail) -> assert label passed ExitSuccess detail "")
  beyondCircle010Checks >>= mapM_ (\(label, passed, detail) -> assert label passed ExitSuccess detail "")
  specification011Checks >>= mapM_ (\(label, passed, detail) -> assert label passed ExitSuccess detail "")
  rules011Checks >>= mapM_ (\(label, passed, detail) -> assert label passed ExitSuccess detail "")
  withProposalTemporaryRoot previousReleaseUpgradeChecks
  withProposalTemporaryRoot specificationUpgradeRefusalChecks
  withProposalTemporaryRoot earlierSpecificationChecks
  specificationUpgradeRuleChecks
  withProposalTemporaryRoot specificationUpgradeChecks
  withProposalTemporaryRoot forgedUpgradeChecks
  withProposalTemporaryRoot forgedUpgradeHistoryChecks
  withProposalTemporaryRoot specificationUpgradeServiceChecks
  withProposalTemporaryRoot specificationUpgradeRecoveryChecks
  transitionDispatchCatalogChecks
  contractCatalogChecks
  refusalCodeSchemaChecks
  withProposalTemporaryRoot publicationTransitionChecks
  withProposalTemporaryRoot controlPlaneInputLimitChecks
  withProposalTemporaryRoot subprocessOutputLimitChecks
  withProposalTemporaryRoot subprocessCaptureInterruptionChecks
  withProposalTemporaryRoot historicalReplayChecks
  withProposalTemporaryRoot persistentAdmissionChecks
  withProposalTemporaryRoot admissionRecheckChecks
  withProposalTemporaryRoot checkpointChecks
  withProposalTemporaryRoot rememberedSnapshotChecks
  withProposalTemporaryRoot serviceChecks
  withProposalTemporaryRoot fileModeChecks
  withProposalTemporaryRoot initMaintenanceChecks
  withProposalTemporaryRoot contentAdmissionChecks
  withProposalTemporaryRoot gitObjectStoreTopologyChecks
  withProposalTemporaryRoot gitObjectStoreBarrierFailureChecks
  withProposalTemporaryRoot operationJournalSizeCheck
  withProposalTemporaryRoot operationJournalKillRecoveryCheck
  withProposalTemporaryRoot indexRecoveryRecordBatchChecks
  withProposalTemporaryRoot indexRecoveryRefusalChecks
  withProposalTemporaryRoot indexRecoveryTransitionCheck
  withProposalTemporaryRoot persistentRecordParentSymlinkCheck
  withProposalTemporaryRoot persistentRecordParentSwapCheck
  withProposalTemporaryRoot persistentRecordCleanupOwnershipCheck
  withProposalTemporaryRoot persistentRecordCleanupFifoCheck
  withProposalTemporaryRoot persistentGitDirectorySwapCheck
  withProposalTemporaryRoot persistentArchiveRootSwapCheck
  withProposalTemporaryRoot pinnedGitPermissionCheck
  withProposalTemporaryRoot persistentTransitionChecks
  withProposalTemporaryRoot indexReservationInterruptionCheck
  withProposalTemporaryRoot indexReservationIdentityCheck
  withProposalTemporaryRoot uncertainRefOutcomeCheck
  withProposalTemporaryRoot postRefSyncFailureCheck
  withProposalTemporaryRoot processLockCheck
  withProposalTemporaryRoot interruptedInitCheck

  commandRefuses "stdin admission is refused" ["admit", "--dry-run"] "admission requires --proposal DIRECTORY"
  commandRefuses "transition stdin is refused" ["transition", "--dry-run"] "transition requires --proposal DIRECTORY"
  commandRefuses "persistent admission requires a proposal" ["admit"] "admit requires --proposal DIRECTORY"

configureExecutablePaths :: IO ()
configureExecutablePaths = do
  gitPath <- findExecutable "git" >>= maybe
    (ioError (userError "tests require an installed Git executable")) pure
  annexPath <- findExecutable "git-annex" >>= maybe
    (ioError (userError "tests require an installed git-annex executable")) pure
  setEnv "ARCHIVE_GIT_EXECUTABLE" gitPath
  setEnv "ARCHIVE_GIT_ANNEX_EXECUTABLE" annexPath
versionedContextChecks :: IO ()
versionedContextChecks = do
  oldBytes <- readFile "spec/0.0.2/context.jsonld"
  priorBytes <- readFile "spec/0.0.3/context.jsonld"
  newBytes <- readFile "spec/0.0.4/context.jsonld"
  let oldContext = decodeUnique (LazyByteString.pack oldBytes)
      priorContext = decodeUnique (LazyByteString.pack priorBytes)
      newContext = decodeUnique (LazyByteString.pack newBytes)
  assert "the immutable 0.0.2 context remains selected by archive/0.0.2"
    (case oldContext of
      Right value -> validateCoreContextForSpecification "archive/0.0.2" value == Right ()
      Left _ -> False)
    ExitSuccess "" ""
  assert "0.0.2 refuses the later musical_work context mapping"
    (case newContext of
      Right value -> case validateCoreContextForSpecification "archive/0.0.2" value of
        Left _ -> True
        Right () -> False
      Left _ -> False)
    ExitSuccess "" ""
  assert "0.0.4 accepts its versioned musical_work context"
    (case newContext of
      Right value -> validateCoreContextForSpecification "archive/0.0.4" value == Right ()
      Left _ -> False)
    ExitSuccess "" ""
  assert "0.0.3 retains its own versioned context"
    (case priorContext of
      Right value -> validateCoreContextForSpecification "archive/0.0.3" value == Right ()
      Left _ -> False)
    ExitSuccess "" ""

audioContractChecks :: IO ()
audioContractChecks = do
  selectionSchema <- installedSchema "spec/contracts/sha256-bfde7fb0daaf7ab9af80eabe8d4e684e07f3a9dbf5cbec68266ddf4b9493cc28.json"
  occurrenceSchema <- installedSchema "spec/contracts/sha256-990e8f3a0938bb7ddd25f42a112f135b08460514038096807b573a6101a1a16f.json"
  let pin role objectId revision = Aeson.object
        ["role" .= role, "id" .= objectId, "revision_id" .= revision]
      piece start end = Aeson.object
        ["start_sample" .= (start :: Int), "end_sample" .= (end :: Int), "sample_rate" .= (48000 :: Int)]
      selection pieces refs = Aeson.object ["payload" .= Aeson.object ["pieces" .= pieces], "refs" .= refs]
      occurrence refs = Aeson.object ["payload" .= Aeson.object [], "refs" .= refs]
      first = pin ("piece" :: Text.Text) (Text.pack entity) (Text.pack zeroDigest)
      second = pin ("piece" :: Text.Text) (Text.pack claim) (Text.pack otherDigest)
      one = pin ("selection" :: Text.Text) (Text.pack entity) (Text.pack zeroDigest)
      two = pin ("selection" :: Text.Text) (Text.pack claim) (Text.pack otherDigest)
  assert "selection profile accepts ordered pieces and aligned Artifact pins"
    (validateContractInstance selectionSchema (selection [piece 0 50, piece 75 100] [first, second]) == Right ()) ExitSuccess "" ""
  assert "selection profile rejects misaligned pieces and pins"
    (isLeft (validateContractInstance selectionSchema (selection [piece 0 50, piece 75 100] [first]))) ExitSuccess "" ""
  assert "selection profile rejects inverted sample bounds"
    (isLeft (validateContractInstance selectionSchema (selection [piece 50 50] [first]))) ExitSuccess "" ""
  assert "occurrence profile accepts canonical membership"
    (validateContractInstance occurrenceSchema (occurrence [one, two]) == Right ()) ExitSuccess "" ""
  assert "occurrence profile rejects duplicate membership"
    (isLeft (validateContractInstance occurrenceSchema (occurrence [one, one]))) ExitSuccess "" ""
  assert "occurrence profile rejects reordered membership"
    (isLeft (validateContractInstance occurrenceSchema (occurrence [two, one]))) ExitSuccess "" ""

installedSchema :: FilePath -> IO Aeson.Value
installedSchema path = do
  bytes <- readFile path
  case decodeUnique (LazyByteString.pack bytes) of
    Right (Aeson.Object bundle) -> case KeyMap.lookup "schema" bundle of
      Just schema -> pure schema
      Nothing -> ioError (userError (path <> " has no schema"))
    _ -> ioError (userError (path <> " is not a bundle"))

subprocessCaptureLimitChecks :: IO ()
subprocessCaptureLimitChecks = do
  python <- findExecutable "python3" >>= maybe
    (ioError (userError "subprocess-limit test cannot find python3")) pure
  let process = proc python ["-c", "import sys; sys.stdout.write('x' * 65536)"]
  result <- runProcessBounded process (Just 1024) 1024
  assert "bounded subprocess capture refuses oversized control output"
    (result == Left (ProcessOutputLimitFailure StandardOutput 1024))
    ExitSuccess "" (show result)
  stderrResult <- runProcessBounded
    (proc python ["-c", "import sys; sys.stderr.write('e' * 65536)"])
    Nothing 32
  assert "bounded subprocess capture types oversized diagnostics separately"
    (stderrResult == Left (ProcessOutputLimitFailure StandardError 32))
    ExitSuccess "" (show stderrResult)

typedPlannerErrorChecks :: IO ()
typedPlannerErrorChecks = do
  let processFailure = ProcessSpawnFailure "could not spawn selected Git"
      snapshotFailure = GitProcessExecutionFailure processFailure
      legacyText = "Git snapshot error: GitProcessFailure \"could not spawn selected Git\""
  assert "Git snapshot retains the process failure and preserves its CLI text"
    (gitSnapshotErrorMessage snapshotFailure == legacyText
      && mutationErrorMessage (MutationSnapshotFailure snapshotFailure) == legacyText)
    ExitSuccess (show snapshotFailure <> " / " <> Text.unpack (gitSnapshotErrorMessage snapshotFailure)) ""

superseding :: String -> String -> String -> String
superseding objectId target revision = replace "\"supersedes\":[]" ("\"supersedes\":[{\"role\":\"supersedes\",\"id\":\"" <> target <> "\",\"revision_id\":\"" <> revision <> "\"}]") (replace "\"label\":\"test\"" "\"label\":\"revised\"" (entityDraft objectId))

missingObject :: String
missingObject = "urn:uuid:00000000-0000-4000-8000-000000000099"

-- The target object is deliberately different from the object named by the
-- supersession reference.
doorCommand, archiveCommand :: [String] -> String -> IO (ExitCode, String, String)
doorCommand arguments input = readProcessWithExitCode "door" arguments (input <> "\n")
archiveCommand arguments input = readProcessWithExitCode "archive" arguments (input <> "\n")

archiveCommandWithPath :: FilePath -> [String] -> String
                       -> IO (ExitCode, String, String)
archiveCommandWithPath prefix arguments input = do
  environment <- getEnvironment
  let withoutExecutable = filter ((/= "ARCHIVE_GIT_EXECUTABLE") . fst) environment
      process = (proc "archive" arguments)
        { env = Just (("ARCHIVE_GIT_EXECUTABLE", prefix) : withoutExecutable) }
  readCreateProcessWithExitCode process (input <> "\n")

dryRun :: String -> IO (ExitCode, String, String)
dryRun input = pure $ case validateDraftBatch input of
  Left problem -> (ExitFailure 1, "", "door: " <> Text.unpack problem <> "\n")
  Right rows -> (ExitSuccess, concatMap ((<> "\n") . LazyByteString.unpack . Aeson.encode) rows, "")

validateDraftBatch :: String -> Either Text.Text [Aeson.Value]
validateDraftBatch input = do
  value <- either (Left . ("invalid JSON: " <>) . Text.pack) Right
    (decodeUnique (LazyByteString.pack input))
  let values = case value of
        Aeson.Array array -> toList array
        _ -> [value]
  if null values
    then Left "admission batch must contain at least one record"
    else pure ()
  (_, rows, unresolvedClaims) <- foldlM validateOne (emptyIndex, [], False) values
  if unresolvedClaims
    then Left "unsupported: claim contracts require an initialized archive"
    else Right (reverse rows)

validateOne :: (StoreIndex, [Aeson.Value], Bool) -> Aeson.Value
            -> Either Text.Text (StoreIndex, [Aeson.Value], Bool)
validateOne (index, rows, unresolvedClaims) value = do
  parsedEnvelope <- parseEnvelope value
  (revision, record) <- canonicalizeDraft value
  revisionId <- parseRevisionId revision
  validateReferences index revisionId parsedEnvelope
  let row = Aeson.object
        [ "ok" .= True
        , "revision_id" .= revision
        , "record" .= record
        ]
      next = insertRecord revisionId parsedEnvelope index
      hasClaim = case envelopeBody parsedEnvelope of
        ClaimRecord _ -> True
        _ -> False
  pure (next, row : rows, unresolvedClaims || hasClaim)

succeeds :: String -> String -> IO ()
succeeds label input = do
  (code, output, errors) <- dryRun input
  assert label (code == ExitSuccess && "sha256:" `contains` output) code output errors

accepts :: String -> String -> IO ()
accepts label input = do
  (code, output, errors) <- dryRun input
  assert label (code == ExitSuccess) code output errors

refuses, refusesSilently :: String -> String -> String -> IO ()
refuses label input expectedError = do
  (code, output, errors) <- dryRun input
  assert label (code /= ExitSuccess && expectedError `contains` errors) code output errors
refusesSilently label input expectedError = do
  (code, output, errors) <- dryRun input
  assert label (code /= ExitSuccess && null output && expectedError `contains` errors) code output errors

commandRefuses :: String -> [String] -> String -> IO ()
commandRefuses label arguments expectedError = do
  (code, output, errors) <- doorCommand arguments ""
  assert label (code /= ExitSuccess && null output && expectedError `contains` errors) code output errors

revisionOf :: String -> IO String
revisionOf input = do
  (code, output, errors) <- dryRun input
  if code /= ExitSuccess then do
    putStrLn ("not ok - cannot compute revision: " <> errors)
    exitFailure
  else case after "\"revision_id\":\"" output of
    Nothing -> putStrLn "not ok - command did not return revision_id" >> exitFailure
    Just suffix -> pure (takeWhile (/= '"') suffix)

canonicalRevisionOf :: String -> IO String
canonicalRevisionOf input = do
  value <- case decodeUnique (LazyByteString.pack input) of
    Left problem -> putStrLn ("not ok - revision fixture JSON: " <> problem) >> exitFailure
    Right parsed -> pure parsed
  case canonicalizeDraft value of
    Left problem -> putStrLn ("not ok - cannot canonicalize revision fixture: "
      <> Text.unpack problem) >> exitFailure
    Right (revision, _) -> pure (Text.unpack revision)

comparesDifferent :: String -> String -> String -> IO ()
comparesDifferent label first second = do
  firstRevision <- revisionOf first
  secondRevision <- revisionOf second
  assert label (firstRevision /= secondRevision) ExitSuccess (firstRevision <> "\n" <> secondRevision) ""

withTemporaryRoot :: (FilePath -> IO ()) -> IO ()
withTemporaryRoot action = do
  (code, output, errors) <- shell "mktemp -d /tmp/archive-phase4-tests.XXXXXX"
  if code /= ExitSuccess then putStrLn ("not ok - mktemp: " <> errors) >> exitFailure else do
    let parent = trim output
    shellChecked ("mkdir " <> quote (parent <> "/empty"))
    action (parent <> "/missing")
    action (parent <> "/empty")
    symlinkRootCheck parent
    subprocessFailureCheck parent
    removePathForcibly parent

directoryBarrierChecks :: FilePath -> IO ()
directoryBarrierChecks parent = do
  let root = parent <> "/directory-barrier"
      nested = root <> "/nested"
      file = nested <> "/record"
  createDirectoryIfMissing True nested
  writeFile file "durability barrier fixture\n"
  fileFd <- openFd file ReadOnly defaultFileFlags
  fileSync <- c_fullsyncFd (fdNumber fileFd)
  closeFd fileFd
  rootFd <- openFd root ReadOnly defaultFileFlags { directory = True }
  treeSync <- c_syncTree (fdNumber rootFd)
  fileRejectedAsDirectory <- fdNumberForFile file
  closeFd rootFd
  assert "tree synchronization completes file, nested-directory, and containing-directory barriers"
    (fileSync == 0 && treeSync == 0)
    ExitSuccess ("file=" <> show fileSync <> ", tree=" <> show treeSync) ""
  assert "directory barrier refuses a regular-file descriptor"
    (fileRejectedAsDirectory == -1)
    ExitSuccess ("result=" <> show fileRejectedAsDirectory) ""
  where
    fdNumber (Fd descriptor) = fromIntegral descriptor
    fdNumberForFile path = do
      descriptor <- openFd path ReadOnly defaultFileFlags
      result <- c_fullsyncDirectoryFd (fdNumber descriptor)
      closeFd descriptor
      pure result

withGitHistoryRoot :: (FilePath -> IO ()) -> IO ()
withGitHistoryRoot action = do
  (code, output, errors) <- shell "mktemp -d /tmp/archive-git-history-tests.XXXXXX"
  if code /= ExitSuccess
    then putStrLn ("not ok - mktemp: " <> errors) >> exitFailure
    else bracket (pure (trim output)) removePathForcibly action

gitSnapshotHistoryChecks :: FilePath -> IO ()
gitSnapshotHistoryChecks parent = do
  let root = parent <> "/repo"
  shellChecked ("mkdir " <> quote root
    <> " && git -C " <> quote root <> " init -q"
    <> " && git -C " <> quote root <> " config user.name archive-test"
    <> " && git -C " <> quote root
    <> " config user.email archive-test@example.invalid")
  writeFile (root <> "/modified.txt") "initial\n"
  writeFile (root <> "/deleted.txt") "initial\n"
  writeFile (root <> "/readded.txt") "initial\n"
  commitGitHistory root "initial paths"
  writeFile (root <> "/modified.txt") "modified\n"
  commitGitHistory root "modify path"
  removeFile (root <> "/deleted.txt")
  commitGitHistory root "delete path"
  removeFile (root <> "/readded.txt")
  commitGitHistory root "remove readded path"
  writeFile (root <> "/readded.txt") "readded\n"
  commitGitHistory root "readd path"
  snapshotResult <- resolveSnapshot root
  snapshot <- case snapshotResult of
    Left problem -> do
      assert "history fixture resolves as a Git snapshot" False (ExitFailure 1) "" (show problem)
      exitFailure
    Right value -> pure value
  history <- linearHistory snapshot
  (code, output, errors) <- readProcessWithExitCode "git"
    ["-C", root, "rev-list", "--reverse", "HEAD"] ""
  let expected = map Text.pack (lines output)
  assert "linear history returns every real commit root-to-pinned-head"
    (code == ExitSuccess && history == Right expected
      && not (null expected) && last expected == snapshotCommit snapshot)
    code (show history <> "\nexpected: " <> show expected) errors
  modified <- immutableAdditions snapshot ["modified.txt"]
  deleted <- immutableAdditions snapshot ["deleted.txt"]
  readded <- immutableAdditions snapshot ["readded.txt"]
  introducer <- uniqueIntroducer snapshot "readded.txt"
  assert "immutable path modification is refused" (isLeft modified)
    ExitSuccess (show modified) ""
  assert "immutable path deletion is refused" (isLeft deleted)
    ExitSuccess (show deleted) ""
  assert "immutable path re-addition is refused" (isLeft readded)
    ExitSuccess (show readded) ""
  assert "re-added path has no unique introducer" (isLeft introducer)
    ExitSuccess (show introducer) ""
  let shallow = parent <> "/shallow"
  shellChecked ("git clone -q --depth=1 file://" <> quote root <> " " <> quote shallow)
  shallowResult <- resolveSnapshot shallow
  shallowSnapshot <- case shallowResult of
    Left problem -> do
      assert "shallow fixture resolves as a Git snapshot" False (ExitFailure 1) "" (show problem)
      exitFailure
    Right value -> pure value
  shallowHistory <- linearHistory shallowSnapshot
  assert "shallow history is refused as unprovable" (isLeft shallowHistory)
    ExitSuccess (show shallowHistory) ""
  shellChecked ("git -C " <> quote root <> " branch base"
    <> " && git -C " <> quote root <> " checkout -q -b side"
    <> " && printf side\\n > " <> quote (root <> "/side.txt")
    <> " && git -C " <> quote root <> " add side.txt"
    <> " && git -C " <> quote root <> " commit -q -m side"
    <> " && git -C " <> quote root <> " checkout -q base"
    <> " && printf main\\n > " <> quote (root <> "/main.txt")
    <> " && git -C " <> quote root <> " add main.txt"
    <> " && git -C " <> quote root <> " commit -q -m main"
    <> " && git -C " <> quote root <> " merge -q --no-ff -m merge side")
  mergedResult <- resolveSnapshot root
  mergedSnapshot <- case mergedResult of
    Left problem -> do
      assert "merge fixture resolves as a Git snapshot" False (ExitFailure 1) "" (show problem)
      exitFailure
    Right value -> pure value
  mergedHistory <- linearHistory mergedSnapshot
  assert "merge history is refused by the linear-history seam" (isLeft mergedHistory)
    ExitSuccess (show mergedHistory) ""

gitSnapshotRootSwapChecks :: FilePath -> IO ()
gitSnapshotRootSwapChecks parent = do
  let root = parent <> "/snapshot-root"
      renamedRoot = parent <> "/snapshot-root-original"
      victim = parent <> "/snapshot-root-victim"
  shellChecked ("mkdir " <> quote root <> " " <> quote victim
    <> " && git -C " <> quote root <> " init -q"
    <> " && git -C " <> quote root
    <> " -c user.name=test -c user.email=test@example.invalid commit --allow-empty -q -m original")
  writeFile (root <> "/selected.txt") "original\n"
  commitGitHistory root "selected original"
  shellChecked ("git -C " <> quote victim <> " init -q"
    <> " && git -C " <> quote victim
    <> " -c user.name=test -c user.email=test@example.invalid commit --allow-empty -q -m victim")
  writeFile (victim <> "/selected.txt") "victim\n"
  commitGitHistory victim "selected victim"
  oldGit <- lookupEnv "ARCHIVE_GIT_EXECUTABLE"
  realGit <- findExecutable "git" >>= maybe
    (ioError (userError "snapshot root-swap fixture cannot find Git")) pure
  bracket_ (setEnv "ARCHIVE_GIT_EXECUTABLE" realGit)
    (maybe (unsetEnv "ARCHIVE_GIT_EXECUTABLE") (setEnv "ARCHIVE_GIT_EXECUTABLE") oldGit) $ do
      resolved <- resolveSnapshot root
      snapshot <- case resolved of
        Left problem -> do
          assert "root-swap fixture resolves before pathname replacement" False
            (ExitFailure 1) "" (show problem)
          exitFailure
        Right value -> pure value
      renameDirectory root renamedRoot
      createDirectoryLink victim root
      result <- readPath snapshot "selected.txt"
      assert "snapshot refuses a replaced archive-root pathname before starting Git"
        (case result of
          Left (GitProcessFailure detail) -> detail == "archive root changed during operation"
          _ -> False)
        ExitSuccess (show result) ""

configuredGitExecutableChecks :: FilePath -> IO ()
configuredGitExecutableChecks parent = do
  let root = parent <> "/configured-git-repo"
      decoyBin = parent <> "/ambient-git-bin"
      decoy = decoyBin </> "git"
      selected = parent </> "selected-git"
      ambientMarker = parent </> "ambient-git-called"
      selectedMarker = parent </> "selected-git-called"
      replacement = parent </> "replacement-git"
      replacementMarker = parent </> "replacement-git-called"
  realGit <- findExecutable "git" >>= maybe
    (ioError (userError "configured Git fixture cannot find Git")) pure
  shellChecked ("mkdir " <> quote root <> " && git -C " <> quote root
    <> " init -q && git -C " <> quote root
    <> " -c user.name=test -c user.email=test@example.invalid commit --allow-empty -q -m init")
  writeFile (root </> "selected.txt") "configured executable fixture\n"
  commitGitHistory root "configured executable fixture"
  createDirectoryIfMissing True decoyBin
  writeFile decoy ("#!/bin/sh\ntouch " <> quote ambientMarker <> "\nexit 88\n")
  writeFile selected ("#!/bin/sh\ntouch " <> quote selectedMarker <> "\nexec "
    <> quote realGit <> " \"$@\"\n")
  makeExecutable decoy
  makeExecutable selected
  oldPath <- lookupEnv "PATH"
  oldGit <- lookupEnv "ARCHIVE_GIT_EXECUTABLE"
  let restore name value = maybe (unsetEnv name) (setEnv name) value
  bracket_ (setEnv "PATH" (decoyBin <> ":" <> maybe "" id oldPath))
    (restore "PATH" oldPath) $
      bracket_ (setEnv "ARCHIVE_GIT_EXECUTABLE" realGit)
        (restore "ARCHIVE_GIT_EXECUTABLE" oldGit) $ do
          result <- resolveSnapshot root
          assert "ambient PATH Git cannot substitute for operator configuration"
            (not (isLeft result)) ExitSuccess (show result) ""
          ambientCalled <- doesPathExist ambientMarker
          assert "ambient PATH Git shim was not invoked" (not ambientCalled)
            ExitSuccess (show ambientCalled) ""
  bracket_ (setEnv "ARCHIVE_GIT_EXECUTABLE" selected)
    (restore "ARCHIVE_GIT_EXECUTABLE" oldGit) $ do
      result <- resolveSnapshot root
      selectedCalled <- doesPathExist selectedMarker
      assert "configured absolute Git executable is invoked directly"
        (not (isLeft result) && selectedCalled) ExitSuccess
        (show result <> ", selectedCalled=" <> show selectedCalled) ""
      snapshot <- case result of
        Left problem -> ioError (userError (show problem))
        Right value -> pure value
      writeFile replacement
        ("#!/bin/sh\ntouch " <> quote replacementMarker <> "\nexit 88\n")
      makeExecutable replacement
      renameFile replacement selected
      readResult <- readPath snapshot "selected.txt"
      replacementCalled <- doesPathExist replacementMarker
      assert "snapshot refuses an executable replaced after planning before spawn"
        (case readResult of
          Left (GitProcessFailure detail) ->
            detail == "configured Git executable changed during operation"
          _ -> False)
        ExitSuccess (show readResult) ""
      assert "replacement executable is not called after identity refusal"
        (not replacementCalled) ExitSuccess (show replacementCalled) ""
  bracket_ (setEnv "ARCHIVE_GIT_EXECUTABLE" "git")
    (restore "ARCHIVE_GIT_EXECUTABLE" oldGit) $ do
      result <- resolveGitExecutable
      assert "relative Git executable configuration fails closed" (isLeft result)
        ExitSuccess (show result) ""
  oldAnnex <- lookupEnv "ARCHIVE_GIT_ANNEX_EXECUTABLE"
  bracket_ (setEnv "ARCHIVE_GIT_ANNEX_EXECUTABLE" "git-annex")
    (restore "ARCHIVE_GIT_ANNEX_EXECUTABLE" oldAnnex) $ do
      result <- resolveAnnexExecutable
      assert "relative git-annex configuration fails closed" (isLeft result)
        ExitSuccess (show result) ""
  where
    makeExecutable path = do
      permissions <- getPermissions path
      setPermissions path (setOwnerExecutable True permissions)

commitGitHistory :: FilePath -> String -> IO ()
commitGitHistory root message = shellChecked
  ("git -C " <> quote root <> " add -A"
    <> " && git -C " <> quote root <> " commit -q -m " <> quote message)

isRight :: Either a b -> Bool
isRight = not . isLeft

isLeft :: Either a b -> Bool
isLeft result = case result of
  Left _ -> True
  Right _ -> False

subprocessFailureCheck :: FilePath -> IO ()
subprocessFailureCheck parent = do
  let fakeBin = parent <> "/fake-bin"
      fakeGit = fakeBin <> "/git"
      failedRoot = parent <> "/failed-init"
  createDirectory fakeBin
  writeFile fakeGit "#!/bin/sh\nexit 73\n"
  permissions <- getPermissions fakeGit
  setPermissions fakeGit (setOwnerExecutable True permissions)
  (code, output, errors) <- archiveCommandWithPath fakeGit
    ["init", "--operator", "alice", failedRoot] ""
  failedRootIsDirectory <- doesDirectoryExist failedRoot
  failedEntries <- if failedRootIsDirectory
    then listDirectory failedRoot
    else pure ["missing"]
  -- Exit 73 comes only from the fake Git, so this also proves the pinned
  -- spawn ran; a spawn failure would otherwise satisfy "init refused".
  assert "a Git failure refuses init and removes tracked entries without pathname cleanup"
    (code /= ExitSuccess && null failedEntries && contains "(73)" errors
      && not (contains "could not spawn" errors)) code output errors
  let partialRoot = parent <> "/partial-git-init"
      partialBin = parent <> "/partial-git-init-bin"
      partialGit = partialBin <> "/git"
      partialSentinel = partialRoot <> "/.git/objects/info/owned-by-git-init"
      partialJournal = partialRoot <> "/.archive-init-journal"
  createDirectory partialBin
  writeFile partialGit
    "#!/bin/sh\nmkdir -p objects/info refs/heads\nprintf 'partial\\n' > objects/info/owned-by-git-init\nexit 73\n"
  partialPermissions <- getPermissions partialGit
  setPermissions partialGit (setOwnerExecutable True partialPermissions)
  (partialCode, partialOutput, partialErrors) <- archiveCommandWithPath partialGit
    ["init", "--operator", "alice", partialRoot] ""
  partialDataPresent <- doesFileExist partialSentinel
  partialJournalPresent <- doesFileExist partialJournal
  assert "partial Git init failure preserves untracked Git-created .git contents and reports cleanup failure"
    (partialCode /= ExitSuccess && null partialOutput && partialDataPresent && partialJournalPresent
      && contains "cleanup also failed" partialErrors)
    partialCode partialOutput partialErrors
  workingGit <- findExecutable "git" >>= maybe
    (ioError (userError "partial-init retry fixture cannot find git")) pure
  (partialRetryCode, partialRetryOutput, partialRetryErrors) <- archiveCommandWithPath workingGit
    ["init", "--operator", "alice", partialRoot] ""
  partialDataStillPresent <- doesFileExist partialSentinel
  partialJournalStillPresent <- doesFileExist partialJournal
  assert "retry refuses incomplete init without deleting partial Git data or reporting success"
    (partialRetryCode /= ExitSuccess && null partialRetryOutput && partialDataStillPresent
      && partialJournalStillPresent)
    partialRetryCode partialRetryOutput partialRetryErrors
  let typedRoot = parent <> "/typed-failed-init"
      typedEnvironment = BootstrapEnvironment
        { runSubprocess = \_ _ _ -> pure (ExitFailure 73, "", "injected failure")
        , runPinnedSubprocess = Nothing
        }
  typedResult <- initArchiveWith typedEnvironment "alice" typedRoot
  typedRootIsDirectory <- doesDirectoryExist typedRoot
  typedEntries <- if typedRootIsDirectory
    then listDirectory typedRoot
    else pure ["missing"]
  assert "init reports subprocess failure as a typed mutation error and rolls back"
    (typedResult == Left (MutationSubprocessFailure "git failed (73): injected failure")
      && null typedEntries)
    ExitSuccess (show typedResult) ""
  let ioRoot = parent <> "/io-failed-init"
      ioEnvironment = BootstrapEnvironment
        { runSubprocess = \_ _ _ -> ioError (userError "injected I/O failure")
        , runPinnedSubprocess = Nothing
        }
  ioResult <- initArchiveWith ioEnvironment "alice" ioRoot
  ioRootIsDirectory <- doesDirectoryExist ioRoot
  ioEntries <- if ioRootIsDirectory
    then listDirectory ioRoot
    else pure ["missing"]
  assert "init reports synchronous I/O failure as typed and rolls back"
    (case ioResult of
      Left (MutationIoFailure _) -> null ioEntries
      _ -> False)
    ExitSuccess (show ioResult) ""
  realGit <- findExecutable "git" >>= maybe
    (ioError (userError "commit uncertainty fixture cannot find git")) pure
  let hookRoot = parent <> "/hook-disabled-init"
      hookBin = parent <> "/hook-disabled-bin"
      hookGit = hookBin <> "/git"
      hookInjected = hookRoot <> "/hook-injected.txt"
      hookJournal = hookRoot <> "/.archive-init-journal"
  createDirectory hookBin
  writeFile hookGit
    ("#!/bin/sh\nfor argument in \"$@\"; do\n"
      <> "  if [ \"$argument\" = commit ]; then\n"
      <> "    mkdir -p .git/hooks\n"
      <> "    cat > .git/hooks/pre-commit <<'HOOK'\n"
      <> "#!/bin/sh\nprintf 'foreign\\n' > hook-injected.txt\n"
      <> "git add -- hook-injected.txt\n"
      <> "HOOK\n"
      <> "    chmod +x .git/hooks/pre-commit\n"
      <> "  fi\ndone\nexec " <> quote realGit <> " \"$@\"\n")
  hookPermissions <- getPermissions hookGit
  setPermissions hookGit (setOwnerExecutable True hookPermissions)
  (hookCode, hookOutput, hookErrors) <- archiveCommandWithPath hookGit
    ["init", "--operator", "alice", hookRoot] ""
  hookInjectedPresent <- doesFileExist hookInjected
  hookJournalPresent <- doesFileExist hookJournal
  assert "bootstrap commits cannot run repository hooks that alter the journaled tree"
    (hookCode == ExitSuccess && null hookErrors
      && jsonStringField "archive_id" hookOutput /= Nothing
      && not hookInjectedPresent && not hookJournalPresent)
    hookCode hookOutput hookErrors
  let modeRoot = parent <> "/mode-tampered-init"
      modeBin = parent <> "/mode-tampered-bin"
      modeGit = modeBin <> "/git"
      modeJournal = modeRoot <> "/.archive-init-journal"
  createDirectory modeBin
  writeFile modeGit
    ("#!/bin/sh\nfor argument in \"$@\"; do\n"
      <> "  if [ \"$argument\" = commit ]; then\n"
      <> "    blob=$(printf 'foreign-target' | " <> quote realGit <> " hash-object -w --stdin) || exit 75\n"
      <> "    printf '120000 %s\\tarchive.json\\n' \"$blob\" | "
      <> quote realGit <> " update-index --index-info || exit 76\n"
      <> "  fi\ndone\nexec " <> quote realGit <> " \"$@\"\n")
  modePermissions <- getPermissions modeGit
  setPermissions modeGit (setOwnerExecutable True modePermissions)
  (modeCode, modeOutput, modeErrors) <- archiveCommandWithPath modeGit
    ["init", "--operator", "alice", modeRoot] ""
  modeJournalPresent <- doesFileExist modeJournal
  (_, modeTreeEntry, _) <- shell
    ("git -C " <> quote modeRoot <> " ls-tree HEAD -- archive.json")
  assert "a committed symlink mode is refused while retaining the ownership journal"
    (modeCode /= ExitSuccess && null modeOutput
      && contains "non-regular entry" modeErrors && modeJournalPresent
      && contains "120000 blob" modeTreeEntry)
    modeCode modeOutput modeErrors
  let preRefRoot = parent <> "/pre-ref-commit-init"
      preRefBin = parent <> "/pre-ref-commit-bin"
      preRefGit = preRefBin <> "/git"
      preRefJournal = preRefRoot <> "/.archive-init-journal"
  createDirectory preRefBin
  writeFile preRefGit
    ("#!/bin/sh\nfor argument in \"$@\"; do\n"
      <> "  if [ \"$argument\" = init ]; then [ -f ../.archive-init-journal ] || exit 74; fi\n"
      <> "  if [ \"$argument\" = commit ]; then exit 73; fi\n"
      <> "done\nexec " <> quote realGit <> " \"$@\"\n")
  preRefPermissions <- getPermissions preRefGit
  setPermissions preRefGit (setOwnerExecutable True preRefPermissions)
  (preRefCode, preRefOutput, preRefErrors) <- archiveCommandWithPath preRefGit
    ["init", "--operator", "alice", preRefRoot] ""
  preRefJournalPresent <- doesFileExist preRefJournal
  (_, preRefHead, _) <- shell
    ("git -C " <> quote preRefRoot <> " rev-parse --verify HEAD")
  (annexRefCode, _, _) <- shell
    ("git -C " <> quote preRefRoot <> " show-ref --verify --quiet refs/heads/git-annex")
  assert "a pre-ref init failure retains its durable ownership journal and unborn branch"
    (preRefCode /= ExitSuccess && null preRefOutput
      && contains "Git commit outcome is uncertain" preRefErrors
      && preRefJournalPresent && trim preRefHead == "" && annexRefCode == ExitSuccess)
    preRefCode preRefOutput preRefErrors
  (preRefRetryCode, preRefRetryOutput, preRefRetryErrors) <- archiveCommand
    ["init", "--operator", "alice", preRefRoot] ""
  preRefJournalAfterRetry <- doesPathExist preRefJournal
  (_, preRefCommitCount, _) <- shell
    ("git -C " <> quote preRefRoot <> " rev-list --count HEAD")
  assert "retry commits only the exact journaled pre-ref bootstrap and removes its journal"
    (preRefRetryCode == ExitSuccess && null preRefRetryErrors
      && jsonStringField "archive_id" preRefRetryOutput /= Nothing
      && not preRefJournalAfterRetry && trim preRefCommitCount == "1")
    preRefRetryCode preRefRetryOutput preRefRetryErrors
  let alteredPreRefRoot = parent <> "/altered-pre-ref-commit-init"
      alteredPreRefJournal = alteredPreRefRoot <> "/.archive-init-journal"
  (alteredCode, _, _) <- archiveCommandWithPath preRefGit
    ["init", "--operator", "alice", alteredPreRefRoot] ""
  alteredJournalPresent <- doesFileExist alteredPreRefJournal
  shellChecked ("printf 'foreign\\n' > " <> quote (alteredPreRefRoot <> "/foreign.txt"))
  (alteredRetryCode, alteredRetryOutput, alteredRetryErrors) <- archiveCommand
    ["init", "--operator", "alice", alteredPreRefRoot] ""
  alteredJournalStillPresent <- doesFileExist alteredPreRefJournal
  foreignFileStillPresent <- doesFileExist (alteredPreRefRoot <> "/foreign.txt")
  (_, alteredHead, _) <- shell
    ("git -C " <> quote alteredPreRefRoot <> " rev-parse --verify HEAD")
  assert "journal recovery refuses and preserves any unexpected root entry"
    (alteredCode /= ExitSuccess && alteredJournalPresent
      && alteredRetryCode /= ExitSuccess && null alteredRetryOutput
      && alteredJournalStillPresent && foreignFileStillPresent && trim alteredHead == "")
    alteredRetryCode alteredRetryOutput alteredRetryErrors
  let uncertainRoot = parent <> "/uncertain-commit-init"
      uncertainBin = parent <> "/uncertain-commit-bin"
      uncertainGit = uncertainBin <> "/git"
  createDirectory uncertainBin
  writeFile uncertainGit
    ("#!/bin/sh\nfor argument in \"$@\"; do\n"
      <> "  if [ \"$argument\" = commit ]; then\n"
      <> "    " <> quote realGit <> " \"$@\"\n"
      <> "    status=$?\n"
      <> "    if [ \"$status\" -eq 0 ]; then exit 73; else exit \"$status\"; fi\n"
      <> "  fi\ndone\nexec " <> quote realGit <> " \"$@\"\n")
  uncertainPermissions <- getPermissions uncertainGit
  setPermissions uncertainGit (setOwnerExecutable True uncertainPermissions)
  (uncertainCode, uncertainOutput, uncertainErrors) <- archiveCommandWithPath uncertainGit
    ["init", "--operator", "alice", uncertainRoot] ""
  uncertainExists <- doesDirectoryExist (uncertainRoot <> "/.git")
  (_, uncertainCommitCount, _) <- shell
    ("git -C " <> quote uncertainRoot <> " rev-list --count HEAD")
  assert "a commit that succeeds before a lost failure response is retained and reported uncertain"
    (uncertainCode /= ExitSuccess && null uncertainOutput
      && contains "Git commit outcome is uncertain" uncertainErrors
      && contains "initialized files were retained" uncertainErrors
      && uncertainExists && trim uncertainCommitCount == "1")
    uncertainCode uncertainOutput uncertainErrors
  (uncertainRetryCode, uncertainRetryOutput, uncertainRetryErrors) <- archiveCommand
    ["init", "--operator", "alice", uncertainRoot] ""
  assert "init retry recognizes the archive after an uncertain successful commit"
    (uncertainRetryCode == ExitSuccess && null uncertainRetryErrors
      && jsonStringField "archive_id" uncertainRetryOutput /= Nothing)
    uncertainRetryCode uncertainRetryOutput uncertainRetryErrors
  let removedRecordRoot = parent <> "/removed-record-during-commit"
      removedRecordBin = parent <> "/removed-record-during-commit-bin"
      removedRecordGit = removedRecordBin <> "/git"
  createDirectory removedRecordBin
  writeFile removedRecordGit
    ("#!/bin/sh\nfor argument in \"$@\"; do\n"
      <> "  if [ \"$argument\" = commit ]; then\n"
      <> "    " <> quote realGit <> " \"$@\" || exit $?\n"
      <> "    record=$(find \"$GIT_WORK_TREE/records\" -type f -print -quit)\n"
      <> "    [ -n \"$record\" ] && rm -f \"$record\" || exit 74\n"
      <> "    exit 0\n"
      <> "  fi\ndone\nexec " <> quote realGit <> " \"$@\"\n")
  removedRecordPermissions <- getPermissions removedRecordGit
  setPermissions removedRecordGit (setOwnerExecutable True removedRecordPermissions)
  (removedRecordCode, removedRecordOutput, removedRecordErrors) <-
    archiveCommandWithPath removedRecordGit
      ["init", "--operator", "alice", removedRecordRoot] ""
  (_, removedRecordCommitCount, _) <- shell
    ("git -C " <> quote removedRecordRoot <> " rev-list --count HEAD")
  assert "bootstrap durability fails when a required tracked record disappears"
    (removedRecordCode /= ExitSuccess && null removedRecordOutput
      && contains "required bootstrap path disappeared before parent sync" removedRecordErrors
      && trim removedRecordCommitCount == "1")
    removedRecordCode removedRecordOutput removedRecordErrors
  realAnnex <- findExecutable "git-annex" >>= maybe
    (ioError (userError "bootstrap barrier fixture cannot find git-annex")) pure
  let barrierRoot = parent <> "/bootstrap-failed-durability-barrier"
      barrierBin = parent <> "/bootstrap-failed-durability-bin"
      barrierGit = barrierBin <> "/git"
      barrierAnnex = barrierBin <> "/git-annex"
      barrierFifo = barrierRoot <> "/.git/annex/init-barrier-fifo"
  createDirectory barrierBin
  writeFile barrierGit
    ("#!/bin/sh\nis_commit=0\n"
      <> "for argument in \"$@\"; do if [ \"$argument\" = commit ]; then is_commit=1; fi; done\n"
      <> quote realGit <> " \"$@\"\nstatus=$?\n"
      <> "if [ \"$is_commit\" = 1 ] && [ \"$status\" -eq 0 ]; then\n"
      <> "  mkfifo annex/init-barrier-fifo || exit 74\n"
      <> "fi\nexit \"$status\"\n")
  writeFile barrierAnnex ("#!/bin/sh\nexec " <> quote realAnnex <> " \"$@\"\n")
  mapM_ (\path -> do
      filePermissions <- getPermissions path
      setPermissions path (setOwnerExecutable True filePermissions))
    [barrierGit, barrierAnnex]
  oldAnnexForBarrier <- lookupEnv "ARCHIVE_GIT_ANNEX_EXECUTABLE"
  let restoreBarrierAnnex = maybe (unsetEnv "ARCHIVE_GIT_ANNEX_EXECUTABLE")
        (setEnv "ARCHIVE_GIT_ANNEX_EXECUTABLE") oldAnnexForBarrier
  bracket_ (setEnv "ARCHIVE_GIT_ANNEX_EXECUTABLE" barrierAnnex) restoreBarrierAnnex $ do
    (barrierCode, barrierOutput, barrierErrors) <- archiveCommandWithPath barrierGit
      ["init", "--operator", "alice", barrierRoot] ""
    fifoPresent <- doesPathExist barrierFifo
    commitCountOutput <- shell
      ("git -C " <> quote barrierRoot <> " rev-list --count HEAD")
    assert "a committed init with unsynchronized special state fails as uncertain and retains the commit"
      (barrierCode /= ExitSuccess && null barrierOutput
        && contains "Git commit outcome is uncertain" barrierErrors
        && contains "initialized files were retained" barrierErrors
        && fifoPresent && trim (let (_, commitCountText, _) = commitCountOutput in commitCountText) == "1")
      barrierCode barrierOutput barrierErrors
    (blockedRetryCode, blockedRetryOutput, blockedRetryErrors) <- archiveCommand
      ["init", "--operator", "alice", barrierRoot] ""
    assert "init retry cannot report success until the durability barrier can complete"
      (blockedRetryCode /= ExitSuccess && null blockedRetryOutput)
      blockedRetryCode blockedRetryOutput blockedRetryErrors
    removeFile barrierFifo
    (recoveredRetryCode, recoveredRetryOutput, recoveredRetryErrors) <- archiveCommand
      ["init", "--operator", "alice", barrierRoot] ""
    assert "init retry succeeds after the failed barrier condition is removed"
      (recoveredRetryCode == ExitSuccess && null recoveredRetryErrors
        && jsonStringField "archive_id" recoveredRetryOutput /= Nothing)
      recoveredRetryCode recoveredRetryOutput recoveredRetryErrors
  let newParent = parent <> "/created-init-parent"
      nestedRoot = newParent <> "/archive"
      parentEnvironment = BootstrapEnvironment
        { runSubprocess = \_ _ _ -> pure (ExitFailure 73, "", "injected failure")
        , runPinnedSubprocess = Nothing
        }
  parentResult <- initArchiveWith parentEnvironment "alice" nestedRoot
  parentExists <- doesDirectoryExist newParent
  nestedRootExists <- doesDirectoryExist nestedRoot
  nestedEntries <- if nestedRootExists
    then listDirectory nestedRoot
    else pure ["missing"]
  assert "rollback reports when newly created ancestors lie outside the pinned root"
    (case parentResult of
      Left (MutationCleanupFailure _ _) ->
        parentExists && nestedEntries == [".archive-init-journal"]
      _ -> False)
    ExitSuccess (show parentResult) ""
  let collisionAncestor = parent <> "/.archive-init-journal"
      collisionRoot = collisionAncestor <> "/archive"
      collisionJournal = collisionRoot <> "/.archive-init-journal"
  collisionResult <- initArchiveWith parentEnvironment "alice" collisionRoot
  collisionAncestorExists <- doesDirectoryExist collisionAncestor
  collisionRootEntries <- listDirectory collisionRoot
  collisionJournalExists <- doesFileExist collisionJournal
  assert "rollback retains the root journal when an outside ancestor has the same basename"
    (case collisionResult of
      Left (MutationCleanupFailure _ _) ->
        collisionAncestorExists && collisionJournalExists
          && collisionRootEntries == [".archive-init-journal"]
      _ -> False)
    ExitSuccess (show collisionResult) ""
  bootstrapRootSwapCheck parent
  bootstrapRootSuccessSwapCheck parent
  bootstrapGitDirectorySwapCheck parent
  bootstrapProcessRootSwapCheck parent
  bootstrapEntryReplacementCheck parent
  bootstrapSymlinkDescendantCheck parent

bootstrapRootSwapCheck :: FilePath -> IO ()
bootstrapRootSwapCheck parent = do
  let root = parent <> "/bootstrap-root-swap"
      movedRoot = root <> "-pinned"
      victim = parent <> "/bootstrap-root-victim"
      sentinel = victim <> "/.git/sentinel"
      environment = BootstrapEnvironment
        { runSubprocess = \program arguments _cwd ->
            if program == "git" && arguments == ["init", "--quiet"]
              then do
                renameDirectory root movedRoot
                createDirectoryLink victim root
                pure (ExitFailure 73, "", "injected failure after root swap")
              else pure (ExitSuccess, "", "")
        , runPinnedSubprocess = Nothing
        }
  createDirectory root
  createDirectory victim
  createDirectory (victim <> "/.git")
  writeFile sentinel "preserve victim\n"
  result <- initArchiveWith environment "alice" root
  sentinelPresent <- doesFileExist sentinel
  gitDirectoryPresent <- doesPathExist (victim <> "/.git")
  assert "bootstrap rollback skips pathname cleanup after root replacement"
    (case result of Left _ -> sentinelPresent && gitDirectoryPresent; Right _ -> False)
    ExitSuccess (show result) ""

bootstrapRootSuccessSwapCheck :: FilePath -> IO ()
bootstrapRootSuccessSwapCheck parent = do
  let root = parent <> "/bootstrap-root-success-swap"
      movedRoot = root <> "-pinned"
      victim = parent <> "/bootstrap-root-success-victim"
      victimLock = victim <> "/.archive-init-lock"
      sentinel = victimLock <> "/keep"
  calls <- newIORef []
  let environment = BootstrapEnvironment
        { runSubprocess = \program arguments _rootFd -> do
            modifyIORef' calls ((program, arguments) :)
            if program == "git" && arguments == ["init", "--quiet"]
              then do
                shellChecked ("git -C " <> quote root <> " init --quiet")
                pure (ExitSuccess, "", "")
              else if program == "git" && dropWhile (/= "add") arguments ==
                ["add", "--", ":/archive.json", ":/spec", ":/contracts", ":/records"]
                then do
                  renameDirectory root movedRoot
                  renameDirectory victim root
                  pure (ExitSuccess, "", "")
                else pure (ExitSuccess, "", "")
        , runPinnedSubprocess = Nothing
        }
  createDirectory root
  createDirectory victim
  createDirectory victimLock
  writeFile sentinel "preserve victim\\n"
  result <- initArchiveWith environment "alice" root
  sentinelPresent <- doesFileExist (root <> "/.archive-init-lock/keep")
  observedCalls <- readIORef calls
  assert "successful init path swap never removes a replacement root lock subtree"
    (case result of Left _ -> sentinelPresent; _ -> False)
      ExitSuccess (show result <> " sentinelPresent=" <> show sentinelPresent) ""
  assert "no Git or annex subprocess is started after the pinned root name changes"
    (not (any (\(program, arguments) -> program == "git" && "commit" `elem` arguments) observedCalls))
    ExitSuccess (show observedCalls) ""

bootstrapGitDirectorySwapCheck :: FilePath -> IO ()
bootstrapGitDirectorySwapCheck parent = do
  realGit <- findExecutable "git" >>= maybe
    (ioError (userError "bootstrap .git swap fixture cannot find Git")) pure
  let root = parent <> "/bootstrap-git-directory-swap"
      oldGitDirectory = root <> "/.git-pinned"
      victimGit = parent <> "/bootstrap-git-directory-victim"
      victimSentinel = victimGit <> "/sentinel"
      fakeBin = parent <> "/bootstrap-git-directory-bin"
      fakeGit = fakeBin <> "/git"
      fakeAnnex = fakeBin <> "/git-annex"
      commitMarker = parent <> "/bootstrap-git-directory-commit-called"
  createDirectory fakeBin
  createDirectory victimGit
  writeFile victimSentinel "preserve replacement Git directory\n"
  writeFile fakeGit
    ("#!/bin/sh\nis_add=0\n"
      <> "for argument in \"$@\"; do\n"
      <> "  if [ \"$argument\" = commit ]; then touch " <> quote commitMarker <> "; fi\n"
      <> "  if [ \"$argument\" = add ]; then is_add=1; fi\n"
      <> "done\n"
      <> "if [ \"$is_add\" = 1 ]; then\n"
      <> "  " <> quote realGit <> " \"$@\"\n"
      <> "  status=$?\n"
      <> "  mv " <> quote (root <> "/.git") <> " " <> quote oldGitDirectory <> "\n"
      <> "  mv " <> quote victimGit <> " " <> quote (root <> "/.git") <> "\n"
      <> "  exit \"$status\"\n"
      <> "fi\nexec " <> quote realGit <> " \"$@\"\n")
  writeFile fakeAnnex "#!/bin/sh\nexit 0\n"
  mapM_ (\path -> do
      filePermissions <- getPermissions path
      setPermissions path (setOwnerExecutable True filePermissions))
    [fakeGit, fakeAnnex]
  oldAnnex <- lookupEnv "ARCHIVE_GIT_ANNEX_EXECUTABLE"
  let restoreAnnex = maybe (unsetEnv "ARCHIVE_GIT_ANNEX_EXECUTABLE")
        (setEnv "ARCHIVE_GIT_ANNEX_EXECUTABLE") oldAnnex
  bracket_ (setEnv "ARCHIVE_GIT_ANNEX_EXECUTABLE" fakeAnnex) restoreAnnex $ do
    (code, output, errors) <- archiveCommandWithPath fakeGit
      ["init", "--operator", "alice", root] ""
    victimPreserved <- doesFileExist (root <> "/.git/sentinel")
    pinnedGitExists <- doesDirectoryExist oldGitDirectory
    commitCalled <- doesPathExist commitMarker
    assert "pinned Git cwd prevents a .git pathname swap from redirecting init writes"
      (code /= ExitSuccess && null output && contains "Git directory changed during initialization" errors
        && victimPreserved && pinnedGitExists && not commitCalled)
      code output errors

bootstrapProcessRootSwapCheck :: FilePath -> IO ()
bootstrapProcessRootSwapCheck parent = do
  realGit <- findExecutable "git" >>= maybe
    (ioError (userError "bootstrap root swap fixture cannot find Git")) pure
  let root = parent <> "/bootstrap-process-root-swap"
      movedRoot = root <> ".pinned"
      victim = parent <> "/bootstrap-process-root-victim"
      victimLock = victim <> "/.archive-init-lock"
      sentinel = victimLock <> "/keep"
      fakeBin = parent <> "/bootstrap-process-root-bin"
      fakeGit = fakeBin <> "/git"
      fakeAnnex = fakeBin <> "/git-annex"
      commitMarker = parent <> "/bootstrap-process-commit-called"
  createDirectory fakeBin
  createDirectory victim
  createDirectory victimLock
  writeFile sentinel "preserve victim\n"
  writeFile fakeGit
    ("#!/bin/sh\n"
      <> "is_add=0\n"
      <> "for argument in \"$@\"; do\n"
      <> "  if [ \"$argument\" = commit ]; then touch " <> quote commitMarker <> "; fi\n"
      <> "  if [ \"$argument\" = add ]; then is_add=1; fi\n"
      <> "done\n"
      <> "if [ \"$is_add\" = 1 ]; then\n"
      <> "  " <> quote realGit <> " \"$@\"\n"
      <> "  status=$?\n"
      <> "  mv " <> quote root <> " " <> quote movedRoot <> "\n"
      <> "  mv " <> quote victim <> " " <> quote root <> "\n"
      <> "  exit \"$status\"\n"
      <> "fi\nexec " <> quote realGit <> " \"$@\"\n")
  writeFile fakeAnnex "#!/bin/sh\nexit 0\n"
  mapM_ makeExecutable [fakeGit, fakeAnnex]
  oldAnnex <- lookupEnv "ARCHIVE_GIT_ANNEX_EXECUTABLE"
  let restoreAnnex = maybe (unsetEnv "ARCHIVE_GIT_ANNEX_EXECUTABLE")
        (setEnv "ARCHIVE_GIT_ANNEX_EXECUTABLE") oldAnnex
  bracket_ (setEnv "ARCHIVE_GIT_ANNEX_EXECUTABLE" fakeAnnex) restoreAnnex $ do
    (code, output, errors) <- archiveCommandWithPath fakeGit
      ["init", "--operator", "alice", root] ""
    sentinelPresent <- doesFileExist (root <> "/.archive-init-lock/keep")
    commitCalled <- doesPathExist commitMarker
    assert "real pinned Git child cannot redirect later init work after root rename"
      (code /= ExitSuccess && null output && contains "archive root pathname changed" errors
        && sentinelPresent && not commitCalled)
      code output errors
  where
    makeExecutable path = do
      permissions <- getPermissions path
      setPermissions path (setOwnerExecutable True permissions)

bootstrapEntryReplacementCheck :: FilePath -> IO ()
bootstrapEntryReplacementCheck parent = do
  let root = parent <> "/bootstrap-entry-replacement"
      manifest = root <> "/archive.json"
      originalManifest = root <> "/archive.json.init-owned"
      environment = BootstrapEnvironment
        { runSubprocess = \program arguments _cwd ->
            if program == "git" && arguments == ["init", "--quiet"]
              then do
                renameFile manifest originalManifest
                writeFile manifest "operator replacement\n"
                pure (ExitFailure 73, "", "injected failure after file replacement")
              else pure (ExitSuccess, "", "")
        , runPinnedSubprocess = Nothing
        }
  createDirectory root
  result <- initArchiveWith environment "alice" root
  remaining <- readFile manifest
  assert "bootstrap rollback preserves a replaced tracked file and reports cleanup refusal"
    (case result of Left (MutationCleanupFailure _ _) -> remaining == "operator replacement\n"; _ -> False)
    ExitSuccess (show result <> "\n" <> remaining) ""

bootstrapSymlinkDescendantCheck :: FilePath -> IO ()
bootstrapSymlinkDescendantCheck parent = do
  let root = parent <> "/bootstrap-symlink-descendant"
      manifest = root <> "/archive.json"
      displaced = root <> "/archive.json.init-owned"
      victim = parent <> "/bootstrap-symlink-victim"
      environment = BootstrapEnvironment
        { runSubprocess = \program arguments _rootFd ->
            if program == "git" && arguments == ["init", "--quiet"]
              then do
                renameFile manifest displaced
                createFileLink victim manifest
                pure (ExitFailure 73, "", "injected failure after symlink replacement")
              else pure (ExitSuccess, "", "")
        , runPinnedSubprocess = Nothing
        }
  createDirectory root
  writeFile victim "preserve external bytes\n"
  result <- initArchiveWith environment "alice" root
  manifestIsSymlink <- pathIsSymbolicLink manifest
  victimContents <- readFile victim
  assert "rollback never follows a symlinked descendant into an external file"
    (case result of Left (MutationCleanupFailure _ _) -> manifestIsSymlink && victimContents == "preserve external bytes\n"; _ -> False)
    ExitSuccess (show result <> " symlink=" <> show manifestIsSymlink <> " victim=" <> victimContents) ""

-- Proposal tests deliberately use the command boundary.  The archive and
-- proposal are both made on disk so that these tests exercise the manifest,
-- descriptor traversal, UUID allocation, and committed-record checks together.
withProposalTemporaryRoot :: (FilePath -> IO ()) -> IO ()
withProposalTemporaryRoot action = do
  (code, output, errors) <- shell "mktemp -d /tmp/archive-admission-proposals.XXXXXX"
  if code /= ExitSuccess then putStrLn ("not ok - mktemp: " <> errors) >> exitFailure else do
    let parent = trim output
    action parent
    removePathForcibly parent

indexReservationInterruptionCheck :: FilePath -> IO ()
indexReservationInterruptionCheck parent = do
  (root, _, _) <- newProposalArchive parent "index-reservation-interruption"
  headBefore <- gitHead root
  refusal <- withArchiveWriteLock root $ \lock ->
    commitRecordBatch lock (Text.pack headBefore) "typed refusal test"
      [(recordRelative zeroDigest, "{}")]
      (pure (Left (MutationRejected "typed refusal")))
  headAfterRefusal <- gitHead root
  assert "the shared record seam preserves a typed refusal and its exact message"
    (refusal == Left (MutationRejected "typed refusal") && headAfterRefusal == headBefore
      && mutationErrorMessage (MutationCleanupFailure (MutationRejected "typed refusal")
        (MutationOperationFailure "cleanup detail"))
        == "typed refusal; cleanup also failed: cleanup detail")
    ExitSuccess "" (show refusal)
  let operation = withArchiveWriteLock root $ \lock ->
        commitRecordBatch lock (Text.pack headBefore) "interrupted test" []
          (throwIO UserInterrupt)
  result <- try operation
    :: IO (Either AsyncException (Either MutationError Text.Text))
  gitEntries <- listDirectory (root </> ".git")
  headAfter <- gitHead root
  assert "asynchronous interruption cleans only the owned reserved index"
    (result == Left UserInterrupt && headAfter == headBefore
      && not (any (contains "archive-door-index") gitEntries))
    ExitSuccess "" (show result <> "\n" <> show gitEntries)

indexReservationIdentityCheck :: FilePath -> IO ()
indexReservationIdentityCheck parent = do
  (root, archiveId, initOutput) <- newProposalArchive parent "index-reservation-identity"
  operatorId <- requiredField "operator_id" initOutput
  operatorRevision <- recordRevisionForObject root operatorId
  target <- allocatedObjectId archiveId "archive.core/entity" "index-identity-target"
  targetRevision <- commitEntityFixture root target
  transitionObject <- allocatedObjectId archiveId
    "archive.transition/retire" "index-identity-retire"
  retireContract <- contractCitation root "archive.transition/retire"
  let retireRecord = retireDraft transitionObject operatorId operatorRevision
        target targetRevision retireContract
  proposal <- writeTransitionProposal root "index-identity"
    (transitionManifestFor
      ["records/retire.json"]
      [(transitionObject, "archive.transition/retire", "index-identity-retire")]
      [(transitionObject, Nothing), (operatorId, Just operatorRevision)
      , (target, Just targetRevision)])
    ["records/retire.json", retireRecord]
  realGit <- findExecutable "git" >>= maybe
    (ioError (userError "index identity fixture cannot find git")) pure
  let fakeBin = parent </> "index-identity-fake-bin"
      fakeGit = fakeBin </> "git"
      marker = parent </> "index-identity-name"
      foreignFile = parent </> "index-identity-foreign"
      updateMarker = parent </> "index-identity-update-index-called"
  createDirectoryIfMissing True fakeBin
  writeFile foreignFile "foreign operator data"
  writeFile fakeGit
    ("#!/bin/sh\nswap=0\nfor argument in \"$@\"; do\n"
      <> "  if [ \"$argument\" = read-tree ]; then swap=1; fi\n"
      <> "  if [ \"$argument\" = update-index ] && [ -n \"$GIT_INDEX_FILE\" ]; then touch " <> quote updateMarker <> "; fi\n"
      <> "done\n" <> quote realGit <> " \"$@\"\nstatus=$?\n"
      <> "if [ \"$swap\" = 1 ] && [ -n \"$GIT_INDEX_FILE\" ]; then\n"
      <> "  printf '%s' \"$GIT_INDEX_FILE\" > " <> quote marker <> "\n"
      <> "  rm \"$GIT_INDEX_FILE\"\n"
      <> "  ln -s " <> quote foreignFile <> " \"$GIT_INDEX_FILE\"\n"
      <> "fi\nexit \"$status\"\n")
  permissions <- getPermissions fakeGit
  setPermissions fakeGit (setOwnerExecutable True permissions)
  environment <- getEnvironment
  let clean = ("ARCHIVE_GIT_EXECUTABLE", fakeGit)
        : filter ((/= "ARCHIVE_GIT_EXECUTABLE") . fst) environment
      process = (proc "door" ["transition", "--proposal", proposal])
        { cwd = Just root, env = Just clean }
  headBefore <- gitHead root
  commitsBefore <- gitCommitCount root
  recordsBefore <- recordCount root
  (code, output, errors) <- readCreateProcessWithExitCode process ""
  headAfter <- gitHead root
  commitsAfter <- gitCommitCount root
  recordsAfter <- recordCount root
  indexName <- readFile marker
  let foreignIndex = root </> ".git" </> indexName
  isSymlink <- pathIsSymbolicLink foreignIndex
  updateIndexCalled <- doesPathExist updateMarker
  foreignBytes <- readFile foreignFile
  assert "Git index inode mismatch refuses before another child and preserves foreign target"
    (code /= ExitSuccess && null output && contains "Door index identity" errors
      && contains "cleanup also failed" errors
      && headAfter == headBefore && commitsAfter == commitsBefore
      && recordsAfter == recordsBefore && isSymlink && not updateIndexCalled
      && foreignBytes == "foreign operator data")
    code output errors

uncertainRefOutcomeCheck :: FilePath -> IO ()
uncertainRefOutcomeCheck parent = do
  (root, archiveId, initOutput) <- newProposalArchive parent "uncertain-ref-outcome"
  operatorId <- requiredField "operator_id" initOutput
  operatorRevision <- recordRevisionForObject root operatorId
  target <- allocatedObjectId archiveId "archive.core/entity" "uncertain-ref-target"
  targetRevision <- commitEntityFixture root target
  transitionObject <- allocatedObjectId archiveId
    "archive.transition/retire" "uncertain-ref-retire"
  retireContract <- contractCitation root "archive.transition/retire"
  let retireRecord = retireDraft transitionObject operatorId operatorRevision
        target targetRevision retireContract
  plannedRevision <- canonicalRevisionOf retireRecord
  proposal <- writeTransitionProposal root "uncertain-ref"
    (transitionManifestFor
      ["records/retire.json"]
      [(transitionObject, "archive.transition/retire", "uncertain-ref-retire")]
      [(transitionObject, Nothing), (operatorId, Just operatorRevision)
      , (target, Just targetRevision)])
    ["records/retire.json", retireRecord]
  realGit <- findExecutable "git" >>= maybe
    (ioError (userError "uncertain ref fixture cannot find git")) pure
  let fakeBin = parent </> "uncertain-ref-fake-bin"
      fakeGit = fakeBin </> "git"
  createDirectoryIfMissing True fakeBin
  writeFile fakeGit
    ("#!/bin/sh\nupdate=0\nfor argument in \"$@\"; do\n"
      <> "  if [ \"$argument\" = update-ref ]; then update=1; fi\n"
      <> "done\n" <> quote realGit <> " \"$@\"\nstatus=$?\n"
      <> "if [ \"$update\" = 1 ] && [ \"$status\" = 0 ]; then exit 88; fi\n"
      <> "exit \"$status\"\n")
  permissions <- getPermissions fakeGit
  setPermissions fakeGit (setOwnerExecutable True permissions)
  environment <- getEnvironment
  let restoreExecutable = case lookup "ARCHIVE_GIT_EXECUTABLE" environment of
        Just value -> setEnv "ARCHIVE_GIT_EXECUTABLE" value
        Nothing -> unsetEnv "ARCHIVE_GIT_EXECUTABLE"
  headBefore <- gitHead root
  commitsBefore <- gitCommitCount root
  recordsBefore <- recordCount root
  loadedProposal <- readTransitionProposal proposal >>= either
    (ioError . userError . Text.unpack) pure
  typedResult <- bracket_ (setEnv "ARCHIVE_GIT_EXECUTABLE" fakeGit) restoreExecutable
    (commitTransition root loadedProposal)
  headAfter <- gitHead root
  commitsAfter <- gitCommitCount root
  recordsAfter <- recordCount root
  let uncertain = case typedResult of
        Left (MutationUncertain message) ->
          contains "Git ref outcome is uncertain; installed record files were retained" (Text.unpack message)
            && contains "git exited 88:" (Text.unpack message)
        _ -> False
  assert "failed update-ref response with an advanced ref is reported uncertain and keeps the batch"
    (uncertain
      && headAfter /= headBefore && commitsAfter == commitsBefore + 1
      && recordsAfter == recordsBefore + 1)
    ExitSuccess "" (show typedResult)
  (retryCode, retryOutput, retryErrors, annexCalled) <-
    persistentCommandWithFakeAnnex parent "uncertain-ref-retry" root proposal
  assert "retry recognizes the batch after an uncertain update-ref outcome"
    (retryCode == ExitSuccess && null retryErrors && not annexCalled
      && decodeUnique (LazyByteString.pack retryOutput) == Right (Aeson.object
        [ "status" .= ("committed" :: Text.Text)
        , "revision_ids" .= [plannedRevision]
        , "retired_objects" .= [Text.pack target]
        , "destroyed_digests" .= ([] :: [Text.Text])
        , "retry" .= True
        ]))
    retryCode retryOutput ("annexCalled=" <> show annexCalled <> "\n" <> retryErrors)

postRefSyncFailureCheck :: FilePath -> IO ()
postRefSyncFailureCheck parent = do
  (root, archiveId, initOutput) <- newProposalArchive parent "post-ref-sync-failure"
  operatorId <- requiredField "operator_id" initOutput
  operatorRevision <- recordRevisionForObject root operatorId
  target <- allocatedObjectId archiveId "archive.core/entity" "post-ref-sync-target"
  targetRevision <- commitEntityFixture root target
  transitionObject <- allocatedObjectId archiveId
    "archive.transition/retire" "post-ref-sync-retire"
  retireContract <- contractCitation root "archive.transition/retire"
  let retireRecord = retireDraft transitionObject operatorId operatorRevision
        target targetRevision retireContract
  proposal <- writeTransitionProposal root "post-ref-sync"
    (transitionManifestFor
      ["records/retire.json"]
      [(transitionObject, "archive.transition/retire", "post-ref-sync-retire")]
      [(transitionObject, Nothing), (operatorId, Just operatorRevision)
      , (target, Just targetRevision)])
    ["records/retire.json", retireRecord]
  realGit <- findExecutable "git" >>= maybe
    (ioError (userError "post-ref sync fixture cannot find git")) pure
  let fakeBin = parent </> "post-ref-sync-fake-bin"
      fakeGit = fakeBin </> "git"
  createDirectoryIfMissing True fakeBin
  writeFile fakeGit
    ("#!/bin/sh\nupdate=0\nfor argument in \"$@\"; do\n"
      <> "  if [ \"$argument\" = update-ref ]; then update=1; fi\n"
      <> "done\n" <> quote realGit <> " \"$@\"\nstatus=$?\n"
      <> "if [ \"$update\" = 1 ] && [ \"$status\" = 0 ]; then\n"
      <> "  branch=$(" <> quote realGit <> " --git-dir=. symbolic-ref -q HEAD)\n"
      <> "  chmod 444 \"$branch\"\n"
      <> "fi\nexit \"$status\"\n")
  permissions <- getPermissions fakeGit
  setPermissions fakeGit (setOwnerExecutable True permissions)
  environment <- getEnvironment
  let clean = ("ARCHIVE_GIT_EXECUTABLE", fakeGit)
        : filter ((/= "ARCHIVE_GIT_EXECUTABLE") . fst) environment
      process = (proc "door" ["transition", "--proposal", proposal])
        { cwd = Just root, env = Just clean }
  headBefore <- gitHead root
  commitsBefore <- gitCommitCount root
  recordsBefore <- recordCount root
  (code, output, errors) <- readCreateProcessWithExitCode process ""
  headAfter <- gitHead root
  commitsAfter <- gitCommitCount root
  recordsAfter <- recordCount root
  journalPresent <- doesFileExist (root </> ".git" </> "archive-door-operation")
  branch <- checkedOutput "post-ref sync fixture reads the branch ref"
    ["-C", root, "symbolic-ref", "-q", "HEAD"]
  let branchPath = root </> ".git" </> branch
  refPermissions <- getPermissions branchPath
  setPermissions branchPath (setOwnerWritable True refPermissions)
  assert "a failed post-ref sync reports uncertainty and retains the committed batch"
    (code /= ExitSuccess && null output
      && contains "Git ref outcome is uncertain" errors
      && headAfter /= headBefore && commitsAfter == commitsBefore + 1
      && recordsAfter == recordsBefore + 1 && journalPresent)
    code output errors

processLockCheck :: FilePath -> IO ()
processLockCheck parent = do
  let root = parent </> "lock-repo"
  createDirectory root
  shellChecked ("git -C " <> quote root <> " init -q")
  entered <- newEmptyMVar
  release <- newEmptyMVar
  firstDone <- newEmptyMVar
  secondStarted <- newEmptyMVar
  secondEntered <- newEmptyMVar
  _ <- forkIO $ do
    result <- withArchiveWriteLock root $ \_ -> do
      putMVar entered ()
      takeMVar release
      pure (Right ())
    putMVar firstDone result
  takeMVar entered
  _ <- forkIO $ do
    putMVar secondStarted ()
    result <- withArchiveWriteLock root $ \_ -> pure (Right ())
    putMVar secondEntered result
  takeMVar secondStarted
  threadDelay 100000
  overlapping <- tryReadMVar secondEntered
  putMVar release ()
  first <- takeMVar firstDone
  second <- takeMVar secondEntered
  assert "archive lock serializes threads in one process"
    (overlapping == Nothing && first == Right () && second == Right ())
    ExitSuccess "" ""

interruptedInitCheck :: FilePath -> IO ()
interruptedInitCheck parent = do
  let root = parent </> "interrupted-init"
      environment = BootstrapEnvironment
        { runSubprocess = \_ _ _ -> throwIO UserInterrupt
        , runPinnedSubprocess = Nothing
        }
  result <- try (initArchiveWith environment "alice" root)
  rootIsDirectory <- doesDirectoryExist root
  remaining <- if rootIsDirectory then listDirectory root else pure ["missing"]
  assert "async interruption propagates and cleans entries through the pinned root"
    (result == Left UserInterrupt && null remaining) ExitSuccess "" ""

proposalChecks :: FilePath -> IO ()
proposalChecks parent = do
  (root, archiveId, initOutput) <- newProposalArchive parent "entity"
  objectId <- allocatedObjectId archiveId "archive.core/entity" "proposal-entity"
  let draft = entityDraft objectId
  expected <- revisionOf draft
  proposal <- writeAdmissionProposal root "entity" (manifestFor
    ["records/entity.json"]
    [(objectId, "archive.core/entity", "proposal-entity")]
    [(objectId, Nothing)]
    []) ["records/entity.json", draft]
  proposalAccepts "proposal admission predicts the exact canonical revision"
    root proposal [expected]
  dryRunDoesNotInvokeAnnex parent root proposal
  proposalRefuses "proposal admission refuses a nested working directory"
    (root </> "records") proposal

  artifactObject <- allocatedObjectId archiveId "archive.core/artifact" "proposal-artifact"
  let artifact = artifactDraft artifactObject
  artifactRevision <- revisionOf artifact
  artifactProposal <- writeAdmissionProposal root "artifact" (manifestFor
    ["records/artifact.json"]
    [(artifactObject, "archive.core/artifact", "proposal-artifact")]
    [(artifactObject, Nothing)]
    [(zeroDigest, 0, "SHA256E-s0--empty", "test-stage")])
    ["records/artifact.json", artifact]
  proposalAccepts "artifact metadata predicts without claiming content fixity"
    root artifactProposal [artifactRevision]
  missingContentProposal <- writeAdmissionProposal root "artifact-missing-content" (manifestFor
    ["records/artifact.json"]
    [(artifactObject, "archive.core/artifact", "proposal-artifact")]
    [(artifactObject, Nothing)] [])
    ["records/artifact.json", artifact]
  proposalRefusesContaining "an artifact's content declaration is required"
    "contents must match artifact content references exactly" root missingContentProposal
  wrongSizeProposal <- writeAdmissionProposal root "artifact-wrong-size" (manifestFor
    ["records/artifact.json"]
    [(artifactObject, "archive.core/artifact", "proposal-artifact")]
    [(artifactObject, Nothing)]
    [(zeroDigest, 1, "SHA256E-s1--wrong", "test-stage")])
    ["records/artifact.json", artifact]
  proposalRefusesContaining "an artifact's independently declared size must match"
    "content size disagrees with artifact payload" root wrongSizeProposal

  writeFile (proposal </> "records/extra.json") "this unlisted file is ignored"
  writeFile (proposal </> "operator-note") "this unlisted root file is ignored"
  proposalAccepts "the manifest is authoritative about listed records"
    root proposal [expected]

  symlinkProposal <- writeAdmissionProposal root "symlink" (manifestFor
    ["records/entity.json"]
    [(objectId, "archive.core/entity", "proposal-entity")]
    [(objectId, Nothing)]
    []) ["records/entity.json", draft]
  removeFile (symlinkProposal </> "records/entity.json")
  createFileLink (symlinkProposal </> "records/real.json")
    (symlinkProposal </> "records/entity.json")
  proposalRefuses "a listed symlink is refused with empty stdout" root symlinkProposal

  traversalProposal <- writeRawProposal root "traversal"
    (manifestFor ["records/../entity.json"] [] [] [])
  proposalRefuses "a listed traversal path is refused with empty stdout"
    root traversalProposal

  controlProposal <- writeRawProposal root "control-character"
    (manifestFor ["records/entity\x01.json"] [] [] [])
  proposalRefuses "a listed control character path is refused with empty stdout"
    root controlProposal

  firstRevision <- revisionOf draft
  let secondDraft = superseding objectId objectId firstRevision
      orderedDrafts = "[" <> draft <> "," <> secondDraft <> "]"
  orderedRevisions <- revisionsOf orderedDrafts
  orderedProposal <- writeAdmissionProposal root "ordered" (manifestFor
    ["records/first.json", "records/second.json"]
    [(objectId, "archive.core/entity", "proposal-entity")]
    [(objectId, Nothing)]
    [])
    ["records/first.json", draft, "records/second.json", secondDraft]
  proposalAccepts "multiple revisions are predicted in manifest order"
    root orderedProposal orderedRevisions

  mismatchProposal <- writeAdmissionProposal root "allocation-mismatch" (manifestFor
    ["records/entity.json"]
    [(objectId, "archive.core/wrong", "proposal-entity")]
    [(objectId, Nothing)]
    []) ["records/entity.json", draft]
  proposalRefuses "a UUID allocation mismatch is refused with empty stdout"
    root mismatchProposal

  operatorId <- requiredField "operator_id" initOutput
  operatorRevision <- recordRevisionForObject root operatorId
  staleDraft <- pure (superseding operatorId operatorId operatorRevision)
  staleProposal <- writeAdmissionProposal root "stale-head" (manifestFor
    ["records/entity.json"] [] [(operatorId, Just zeroDigest)]
    []) ["records/entity.json", staleDraft]
  proposalRefuses "a stale expected head is refused with empty stdout"
    root staleProposal

  let forkLeft = superseding operatorId operatorId operatorRevision
      forkRight = replace "\"label\":\"revised\"" "\"label\":\"revised again\"" forkLeft
  forkProposal <- writeAdmissionProposal root "supersession-fork" (manifestFor
    ["records/left.json", "records/right.json"] []
    [(operatorId, Just operatorRevision)] [])
    ["records/left.json", forkLeft, "records/right.json", forkRight]
  proposalRefusesContaining "two successors of one head are refused"
    "linear supersession head" root forkProposal

  retireTarget <- allocatedObjectId archiveId "archive.test/entity" "retire-target"
  retireTargetRevision <- commitEntityFixture root retireTarget
  retireObject <- allocatedObjectId archiveId "archive.transition/retire" "retire-target"
  retireContract <- contractCitation root "archive.transition/retire"
  let (_, retireVersion, _) = retireContract
  assert "the initialized retire fixture uses version 1"
    (retireVersion == "1") ExitSuccess retireVersion ""
  let retireRecord = retireDraft retireObject operatorId operatorRevision
        retireTarget retireTargetRevision retireContract
  retireProposal <- writeTransitionProposal root "retire" (transitionManifestFor
    ["records/retire.json"]
    [(retireObject, "archive.transition/retire", "retire-target")]
    [(retireObject, Nothing), (operatorId, Just operatorRevision)
    , (retireTarget, Just retireTargetRevision)])
    ["records/retire.json", retireRecord]
  transitionProposalAccepts "a single-record version 1 retire dry-run remains accepted"
    root retireProposal [retireTarget]
  dryRunDoesNotInvokeAnnexFor "transition dry-run never invokes git-annex"
    "transition" parent root retireProposal

  retireTargetTwo <- allocatedObjectId archiveId "archive.test/entity" "retire-target-two"
  retireTargetTwoRevision <- commitEntityFixture root retireTargetTwo
  retireObjectTwo <- allocatedObjectId archiveId "archive.transition/retire" "retire-target-two"
  let retireRecordTwo = retireDraft retireObjectTwo operatorId operatorRevision
        retireTargetTwo retireTargetTwoRevision retireContract
  twoRecordV1Proposal <- writeTransitionProposal root "two-record-v1"
    (transitionManifestFor
      ["records/retire-one.json", "records/retire-two.json"]
      [(retireObject, "archive.transition/retire", "retire-target")
      , (retireObjectTwo, "archive.transition/retire", "retire-target-two")]
      [(retireObject, Nothing), (retireObjectTwo, Nothing)
      , (operatorId, Just operatorRevision)
      , (retireTarget, Just retireTargetRevision)
      , (retireTargetTwo, Just retireTargetTwoRevision)])
    [ "records/retire-one.json", retireRecord
    , "records/retire-two.json", retireRecordTwo
    ]
  proposalRefusesContainingWith "transition"
    "a two-record version 1 transition requires version 2 contracts"
    "multi-record transition batches require version 2 retire/destroy contracts"
    root twoRecordV1Proposal

  retireV2 <- contractCitationVersion root "archive.transition/retire" "2"
  let retireV2First = retireDraft retireObject operatorId operatorRevision
        retireTarget retireTargetRevision retireV2
  retireV2FirstRevision <- canonicalRevisionOf retireV2First
  let retireV2Second = retireDraftWithPredecessor retireObjectTwo operatorId
        operatorRevision retireTargetTwo retireTargetTwoRevision retireV2
        retireObject retireV2FirstRevision
  twoRecordV2Proposal <- writeTransitionProposal root "two-record-v2"
    (transitionManifestFor
      ["records/retire-one.json", "records/retire-two.json"]
      [(retireObject, "archive.transition/retire", "retire-target")
      , (retireObjectTwo, "archive.transition/retire", "retire-target-two")]
      [(retireObject, Nothing), (retireObjectTwo, Nothing)
      , (operatorId, Just operatorRevision)
      , (retireTarget, Just retireTargetRevision)
      , (retireTargetTwo, Just retireTargetTwoRevision)])
    [ "records/retire-one.json", retireV2First
    , "records/retire-two.json", retireV2Second
    ]
  transitionProposalAccepts "a version 2 predecessor chain preserves batch order"
    root twoRecordV2Proposal [retireTarget, retireTargetTwo]

  let missingPredecessor = retireDraft retireObjectTwo operatorId operatorRevision
        retireTargetTwo retireTargetTwoRevision retireV2
  missingPredecessorProposal <- writeTransitionProposal root "missing-predecessor"
    (transitionManifestFor
      ["records/retire-one.json", "records/retire-two.json"]
      [(retireObject, "archive.transition/retire", "retire-target")
      , (retireObjectTwo, "archive.transition/retire", "retire-target-two")]
      [(retireObject, Nothing), (retireObjectTwo, Nothing)
      , (operatorId, Just operatorRevision)
      , (retireTarget, Just retireTargetRevision)
      , (retireTargetTwo, Just retireTargetTwoRevision)])
    ["records/retire-one.json", retireV2First
    , "records/retire-two.json", missingPredecessor]
  proposalRefusesContainingWith "transition"
    "a missing version 2 predecessor is refused"
    "transition_predecessor transition must contain exactly one reference"
    root missingPredecessorProposal

  let crossBatchPredecessor = retireDraftWithPredecessor retireObjectTwo operatorId
        operatorRevision retireTargetTwo retireTargetTwoRevision retireV2
        operatorId operatorRevision
  crossBatchProposal <- writeTransitionProposal root "cross-batch-predecessor"
    (transitionManifestFor
      ["records/retire-one.json", "records/retire-two.json"]
      [(retireObject, "archive.transition/retire", "retire-target")
      , (retireObjectTwo, "archive.transition/retire", "retire-target-two")]
      [(retireObject, Nothing), (retireObjectTwo, Nothing)
      , (operatorId, Just operatorRevision)
      , (retireTarget, Just retireTargetRevision)
      , (retireTargetTwo, Just retireTargetTwoRevision)])
    ["records/retire-one.json", retireV2First
    , "records/retire-two.json", crossBatchPredecessor]
  proposalRefusesContainingWith "transition"
    "a predecessor from an older batch is refused"
    "transition_predecessor must pin the immediately preceding draft"
    root crossBatchProposal

  retireTargetThree <- allocatedObjectId archiveId "archive.core/entity"
    "retire-target-three"
  retireTargetThreeRevision <- commitEntityFixture root retireTargetThree
  retireObjectThree <- allocatedObjectId archiveId
    "archive.transition/retire" "retire-target-three"
  let forkedThird = retireDraftWithPredecessor retireObjectThree operatorId
        operatorRevision retireTargetThree retireTargetThreeRevision retireV2
        retireObject retireV2FirstRevision
  forkProposalV2 <- writeTransitionProposal root "forked-predecessor"
    (transitionManifestFor
      ["records/retire-one.json", "records/retire-two.json", "records/retire-three.json"]
      [(retireObject, "archive.transition/retire", "retire-target")
      , (retireObjectTwo, "archive.transition/retire", "retire-target-two")
      , (retireObjectThree, "archive.transition/retire", "retire-target-three")]
      [(retireObject, Nothing), (retireObjectTwo, Nothing)
      , (retireObjectThree, Nothing), (operatorId, Just operatorRevision)
      , (retireTarget, Just retireTargetRevision)
      , (retireTargetTwo, Just retireTargetTwoRevision)
      , (retireTargetThree, Just retireTargetThreeRevision)])
    ["records/retire-one.json", retireV2First
    , "records/retire-two.json", retireV2Second
    , "records/retire-three.json", forkedThird]
  proposalRefusesContainingWith "transition"
    "a predecessor fork is refused"
    "transition_predecessor must pin the immediately preceding draft"
    root forkProposalV2

  forkedThirdRevision <- canonicalRevisionOf forkedThird
  let cycleFirst = retireDraftWithPredecessor retireObject operatorId
        operatorRevision retireTarget retireTargetRevision retireV2
        retireObjectThree forkedThirdRevision
  cycleProposal <- writeTransitionProposal root "cycle-shaped-predecessor"
    (transitionManifestFor
      ["records/retire-one.json", "records/retire-two.json", "records/retire-three.json"]
      [(retireObject, "archive.transition/retire", "retire-target")
      , (retireObjectTwo, "archive.transition/retire", "retire-target-two")
      , (retireObjectThree, "archive.transition/retire", "retire-target-three")]
      [(retireObject, Nothing), (retireObjectTwo, Nothing)
      , (retireObjectThree, Nothing), (operatorId, Just operatorRevision)
      , (retireTarget, Just retireTargetRevision)
      , (retireTargetTwo, Just retireTargetTwoRevision)
      , (retireTargetThree, Just retireTargetThreeRevision)])
    ["records/retire-one.json", cycleFirst
    , "records/retire-two.json", retireV2Second
    , "records/retire-three.json", forkedThird]
  proposalRefusesContainingWith "transition"
    "a cycle-shaped predecessor proposal is refused"
    "the first transition record must not have a transition_predecessor"
    root cycleProposal

  manifestOrderProposal <- writeTransitionProposal root "manifest-order-mismatch"
    (transitionManifestFor
      ["records/retire-two.json", "records/retire-one.json"]
      [(retireObject, "archive.transition/retire", "retire-target")
      , (retireObjectTwo, "archive.transition/retire", "retire-target-two")]
      [(retireObject, Nothing), (retireObjectTwo, Nothing)
      , (operatorId, Just operatorRevision)
      , (retireTarget, Just retireTargetRevision)
      , (retireTargetTwo, Just retireTargetTwoRevision)])
    ["records/retire-one.json", retireV2First
    , "records/retire-two.json", retireV2Second]
  proposalRefusesContainingWith "transition"
    "a manifest and predecessor-chain order mismatch is refused"
    "the first transition record must not have a transition_predecessor"
    root manifestOrderProposal

  let wrongPredecessor = retireDraftWithPredecessor retireObjectTwo operatorId
        operatorRevision retireTargetTwo retireTargetTwoRevision retireV2
        retireObject zeroDigest
  wrongChainProposal <- writeTransitionProposal root "wrong-v2-chain"
    (transitionManifestFor
      ["records/retire-one.json", "records/retire-two.json"]
      [(retireObject, "archive.transition/retire", "retire-target")
      , (retireObjectTwo, "archive.transition/retire", "retire-target-two")]
      [(retireObject, Nothing), (retireObjectTwo, Nothing)
      , (operatorId, Just operatorRevision)
      , (retireTarget, Just retireTargetRevision)
      , (retireTargetTwo, Just retireTargetTwoRevision)])
    [ "records/retire-one.json", retireV2First
    , "records/retire-two.json", wrongPredecessor
    ]
  proposalRefusesContainingWith "transition"
    "a version 2 predecessor must pin the immediately preceding draft"
    "transition_predecessor must pin the immediately preceding draft"
    root wrongChainProposal

  artifactTarget <- allocatedObjectId archiveId "archive.core/artifact" "destroy-target"
  artifactTargetRevision <- commitArtifactFixture root artifactTarget
  destroyContract <- contractCitationVersion root "archive.transition/destroy" "2"
  destroyRetireObject <- allocatedObjectId archiveId
    "archive.transition/retire" "destroy-retire"
  destroyObject <- allocatedObjectId archiveId
    "archive.transition/destroy" "destroy-content"
  let destroyRetire = retireDraft destroyRetireObject operatorId operatorRevision
        artifactTarget artifactTargetRevision retireV2
  destroyRetireRevision <- canonicalRevisionOf destroyRetire
  let destroyRecord = destroyDraftWithPredecessor destroyObject operatorId
        operatorRevision artifactTarget artifactTargetRevision zeroDigest 0
        destroyContract destroyRetireObject destroyRetireRevision
  destroyProposal <- writeTransitionProposal root "retire-then-destroy"
    (transitionManifestFor
      ["records/retire.json", "records/destroy.json"]
      [(destroyRetireObject, "archive.transition/retire", "destroy-retire")
      , (destroyObject, "archive.transition/destroy", "destroy-content")]
      [(destroyRetireObject, Nothing), (destroyObject, Nothing)
      , (operatorId, Just operatorRevision)
      , (artifactTarget, Just artifactTargetRevision)])
    ["records/retire.json", destroyRetire, "records/destroy.json", destroyRecord]
  transitionProposalAcceptsWithDestroyed
    "a version 2 retire-then-destroy dry-run preserves order and state"
    root destroyProposal artifactTarget zeroDigest
  dryRunDoesNotInvokeAnnexFor "transition destroy dry-run never invokes git-annex"
    "transition" parent root destroyProposal

  let destroyFirst = destroyDraft destroyObject operatorId operatorRevision
        artifactTarget artifactTargetRevision zeroDigest 0 destroyContract
  destroyFirstRevision <- canonicalRevisionOf destroyFirst
  let retireAfterDestroy = retireDraftWithPredecessor destroyRetireObject operatorId
        operatorRevision artifactTarget artifactTargetRevision retireV2
        destroyObject destroyFirstRevision
  destroyBeforeRetireProposal <- writeTransitionProposal root "destroy-before-retire"
    (transitionManifestFor
      ["records/destroy.json", "records/retire.json"]
      [(destroyObject, "archive.transition/destroy", "destroy-content")
      , (destroyRetireObject, "archive.transition/retire", "destroy-retire")]
      [(destroyObject, Nothing), (destroyRetireObject, Nothing)
      , (operatorId, Just operatorRevision)
      , (artifactTarget, Just artifactTargetRevision)])
    ["records/destroy.json", destroyFirst
    , "records/retire.json", retireAfterDestroy]
  proposalRefusesContainingWith "transition"
    "destroy before retire is refused by the legal pre-state check"
    "destroy target is not retired" root destroyBeforeRetireProposal

  dependentTarget <- allocatedObjectId archiveId "archive.core/artifact"
    "destroy-dependent"
  dependentRevision <- commitArtifactFixtureWithDigest root dependentTarget otherDigest
  let duplicateDependent =
        destroyDraftWithPredecessorAndDependencies destroyObject
          operatorId operatorRevision artifactTarget artifactTargetRevision zeroDigest 0
          destroyContract destroyRetireObject destroyRetireRevision
          [("dependent_source_gone", dependentTarget, dependentRevision)
          , ("dependent_source_gone", dependentTarget, dependentRevision)]
  duplicateDependentProposal <- writeTransitionProposal root "duplicate-dependent"
    (transitionManifestFor
      ["records/retire.json", "records/destroy.json"]
      [(destroyRetireObject, "archive.transition/retire", "destroy-retire")
      , (destroyObject, "archive.transition/destroy", "destroy-content")]
      [(destroyRetireObject, Nothing), (destroyObject, Nothing)
      , (operatorId, Just operatorRevision)
      , (artifactTarget, Just artifactTargetRevision)
      , (dependentTarget, Just dependentRevision)])
    ["records/retire.json", destroyRetire, "records/destroy.json", duplicateDependent]
  proposalRefusesContainingWith "transition"
    "duplicate dependent dispositions are refused"
    "no installed artifact contract defines dependent reachability"
    root duplicateDependentProposal

  let bothDependent = destroyDraftWithPredecessorAndDependencies destroyObject
        operatorId operatorRevision artifactTarget artifactTargetRevision zeroDigest 0
        destroyContract destroyRetireObject destroyRetireRevision
        [("dependent_destroy", dependentTarget, dependentRevision)
        , ("dependent_source_gone", dependentTarget, dependentRevision)]
  bothDependentProposal <- writeTransitionProposal root "both-dependent-dispositions"
    (transitionManifestFor
      ["records/retire.json", "records/destroy.json"]
      [(destroyRetireObject, "archive.transition/retire", "destroy-retire")
      , (destroyObject, "archive.transition/destroy", "destroy-content")]
      [(destroyRetireObject, Nothing), (destroyObject, Nothing)
      , (operatorId, Just operatorRevision)
      , (artifactTarget, Just artifactTargetRevision)
      , (dependentTarget, Just dependentRevision)])
    ["records/retire.json", destroyRetire, "records/destroy.json", bothDependent]
  proposalRefusesContainingWith "transition"
    "both dependent dispositions are refused"
    "no installed artifact contract defines dependent reachability"
    root bothDependentProposal

  let externalEvidence = destroyDraftWithPredecessorAndDependencies destroyObject
        operatorId operatorRevision artifactTarget artifactTargetRevision zeroDigest 0
        destroyContract destroyRetireObject destroyRetireRevision
        [("external_survivor", dependentTarget, dependentRevision)]
  externalEvidenceProposal <- writeTransitionProposal root "external-evidence"
    (transitionManifestFor
      ["records/retire.json", "records/destroy.json"]
      [(destroyRetireObject, "archive.transition/retire", "destroy-retire")
      , (destroyObject, "archive.transition/destroy", "destroy-content")]
      [(destroyRetireObject, Nothing), (destroyObject, Nothing)
      , (operatorId, Just operatorRevision)
      , (artifactTarget, Just artifactTargetRevision)
      , (dependentTarget, Just dependentRevision)])
    ["records/retire.json", destroyRetire, "records/destroy.json", externalEvidence]
  proposalRefusesContainingWith "transition"
    "external survivor evidence is refused without an installed evidence contract"
    "external survivor evidence contracts are not installed"
    root externalEvidenceProposal

  uncoveredRetire <- writeTransitionProposal root "retire-uncovered" (transitionManifestFor
    ["records/retire.json"]
    [(retireObject, "archive.transition/retire", "retire-target")]
    [(retireObject, Nothing)])
    ["records/retire.json", retireRecord]
  proposalRefusesContainingWith "transition"
    "transition expected heads cover every relied-on object"
    "expected_heads must cover exactly" root uncoveredRetire

  _ <- commitDraftFixture root retireRecord
  transitionProposalIsRetry
    "a committed version 1 transition is replayed as an exact retry"
    root retireProposal [retireTarget] []
  laterRetireProposal <- writeTransitionProposal root "later-retire"
    (transitionManifestFor
      ["records/retire.json"]
      [(retireObjectTwo, "archive.transition/retire", "retire-target-two")]
      [(retireObjectTwo, Nothing), (operatorId, Just operatorRevision)
      , (retireTargetTwo, Just retireTargetTwoRevision)])
    ["records/retire.json", retireRecordTwo]
  transitionProposalAccepts
    "a later transition validates against replayed retired state"
    root laterRetireProposal
    [retireTarget, retireTargetTwo]

  _ <- commitDraftBatchFixture root [destroyRetire, destroyRecord]
  transitionProposalIsRetry
    "a committed version 2 chain replays in durable predecessor order"
    root destroyProposal [retireTarget, artifactTarget] [zeroDigest]

  transitionObject <- allocatedObjectId archiveId "archive.transition/claim" "transition-claim"
  transitionDraft <- transitionDraftFromArchive root
    "archive.transition/operator_initialization" transitionObject
  transitionProposal <- writeAdmissionProposal root "transition-contract" (manifestFor
    ["records/claim.json"]
    [(transitionObject, "archive.transition/claim", "transition-claim")]
    [(transitionObject, Nothing)]
    []) ["records/claim.json", transitionDraft]
  proposalRefusesContaining "a transition contract is refused by generic admission"
    "transition contracts cannot be admitted generically" root transitionProposal

  unknownObject <- allocatedObjectId archiveId "archive.core/entity" "unknown-contract"
  let unknownDraft = citedClaim unknownObject
        ("archive.unknown/missing", "1", zeroDigest)
  unknownProposal <- writeAdmissionProposal root "unknown-contract" (manifestFor
    ["records/claim.json"]
    [(unknownObject, "archive.core/entity", "unknown-contract")]
    [(unknownObject, Nothing)]
    []) ["records/claim.json", unknownDraft]
  proposalRefuses "an unknown contract is refused with empty stdout"
    root unknownProposal

  (alteredRoot, alteredArchiveId, _) <- newProposalArchive parent "altered-contract"
  alteredCitation <- contractCitation alteredRoot "archive.transition/operator_initialization"
  alteredObject <- allocatedObjectId alteredArchiveId "archive.core/entity" "altered-contract"
  alteredProposal <- writeAdmissionProposal alteredRoot "proposal" (manifestFor
    ["records/claim.json"]
    [(alteredObject, "archive.core/entity", "altered-contract")]
    [(alteredObject, Nothing)]
    []) ["records/claim.json", citedClaim alteredObject alteredCitation]
  alterAndCommitContract alteredRoot alteredCitation
  proposalRefuses "an altered installed contract is refused with empty stdout"
    alteredRoot alteredProposal

  (alteredV2Root, alteredV2ArchiveId, _) <- newProposalArchive parent "altered-v2-contract"
  alteredV2Citation <- contractCitationVersion alteredV2Root
    "archive.transition/retire" "2"
  alteredV2Object <- allocatedObjectId alteredV2ArchiveId
    "archive.core/entity" "altered-v2-contract"
  alteredV2Proposal <- writeAdmissionProposal alteredV2Root "proposal" (manifestFor
    ["records/entity.json"]
    [(alteredV2Object, "archive.core/entity", "altered-v2-contract")]
    [(alteredV2Object, Nothing)] [])
    ["records/entity.json", entityDraft alteredV2Object]
  alterAndCommitContract alteredV2Root alteredV2Citation
  proposalRefuses "an altered version 2 bundle is refused by the exact catalog"
    alteredV2Root alteredV2Proposal

  (missingRoot, missingArchiveId, _) <- newProposalArchive parent "missing-contract"
  missingCitation <- contractCitation missingRoot "archive.transition/operator_initialization"
  missingContractObject <- allocatedObjectId missingArchiveId "archive.core/entity" "missing-contract"
  missingProposal <- writeAdmissionProposal missingRoot "proposal" (manifestFor
    ["records/claim.json"]
    [(missingContractObject, "archive.core/entity", "missing-contract")]
    [(missingContractObject, Nothing)]
    []) ["records/claim.json", citedClaim missingContractObject missingCitation]
  removeAndCommitContract missingRoot missingCitation
  proposalRefuses "a missing installed contract is refused with empty stdout"
    missingRoot missingProposal

controlPlaneInputLimitChecks :: FilePath -> IO ()
controlPlaneInputLimitChecks parent = do
  (root, archiveId, _) <- newProposalArchive parent "input-limits"
  objectId <- allocatedObjectId archiveId "archive.core/entity" "size-limited"
  let manifest = manifestFor ["records/entity.json"]
        [(objectId, "archive.core/entity", "size-limited")]
        [(objectId, Nothing)] []
      draft = entityDraft objectId
  oversizedManifest <- writeAdmissionProposal root "oversized-manifest" manifest
    ["records/entity.json", draft]
  writeFile (oversizedManifest </> "manifest.json") (replicate (1024 * 1024 + 1) ' ')
  proposalRefusesContaining "proposal manifest bytes are bounded"
    "exceeds the proposal size limit" root oversizedManifest
  oversizedRecord <- writeAdmissionProposal root "oversized-record" manifest
    ["records/entity.json", draft]
  writeFile (oversizedRecord </> "records/entity.json")
    (replicate (8 * 1024 * 1024 + 1) ' ')
  proposalRefusesContaining "proposal draft bytes are bounded"
    "exceeds the proposal size limit" root oversizedRecord

subprocessOutputLimitChecks :: FilePath -> IO ()
subprocessOutputLimitChecks parent = do
  (root, archiveId, _) <- newProposalArchive parent "output-limit"
  objectId <- allocatedObjectId archiveId "archive.core/entity" "output-limited"
  let draft = entityDraft objectId
      manifest = manifestFor ["records/entity.json"]
        [(objectId, "archive.core/entity", "output-limited")]
        [(objectId, Nothing)] []
  proposal <- writeAdmissionProposal root "output-limit" manifest
    ["records/entity.json", draft]
  realDoor <- findExecutable "door" >>= maybe
    (ioError (userError "output-limit fixture cannot find door")) pure
  let fakeBin = parent </> "output-flood-bin"
      fakeGit = fakeBin </> "git"
  createDirectoryIfMissing True fakeBin
  writeFile fakeGit
    "#!/bin/sh\nexec python3 -c 'import sys; sys.stdout.write(\"x\" * (64 * 1024 * 1024 + 1024))'\n"
  permissions <- getPermissions fakeGit
  setPermissions fakeGit (setOwnerExecutable True permissions)
  environment <- getEnvironment
  let clean = ("ARCHIVE_GIT_EXECUTABLE", fakeGit)
        : filter ((/= "ARCHIVE_GIT_EXECUTABLE") . fst) environment
      process = (proc realDoor ["admit", "--proposal", proposal, "--dry-run"])
        { cwd = Just root
        , env = Just clean
        }
  (code, output, errors) <- readCreateProcessWithExitCode process ""
  assert "Git control-plane stdout is capped and terminates the child"
    (code /= ExitSuccess && null output
      && contains "git stdout exceeded the control-plane output limit" errors)
    code output errors

subprocessCaptureInterruptionChecks :: FilePath -> IO ()
subprocessCaptureInterruptionChecks parent = do
  python <- findExecutable "python3" >>= maybe
    (ioError (userError "subprocess-interruption test cannot find python3")) pure
  let started = parent </> "subprocess-started"
      completed = parent </> "subprocess-completed"
      script = "import pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text('started'); "
        <> "time.sleep(0.5); pathlib.Path(sys.argv[2]).write_text('completed')"
      process = proc python ["-c", script, started, completed]
  resultVar <- newEmptyMVar
  thread <- forkIO $ do
    result <- try (runProcessBounded process Nothing 1024)
      :: IO (Either AsyncException (Either ProcessError
        (ExitCode, ByteString.ByteString, ByteString.ByteString)))
    putMVar resultVar result
  startedInTime <- waitForFile 50 started
  assert "subprocess interruption fixture started" startedInTime ExitSuccess "" ""
  throwTo thread UserInterrupt
  result <- takeMVar resultVar
  threadDelay 700000
  didComplete <- doesFileExist completed
  assert "interrupting bounded capture terminates and reaps its child"
    (result == Left UserInterrupt && not didComplete)
    ExitSuccess "" (show result)
  processGroupCleanupChecks parent
  where
    waitForFile :: Int -> FilePath -> IO Bool
    waitForFile 0 path = doesFileExist path
    waitForFile remaining path = do
      exists <- doesFileExist path
      if exists then pure True else threadDelay 20000 >> waitForFile (remaining - 1) path

processGroupCleanupChecks :: FilePath -> IO ()
processGroupCleanupChecks parent = do
  python <- findExecutable "python3" >>= maybe
    (ioError (userError "process-group test cannot find python3")) pure
  directoryFd <- openFd parent ReadOnly defaultFileFlags
  let run script arguments = runProcessBoundedAt directoryFd python ("-c" : script : arguments)
        [("PATH", "/usr/bin:/bin")] Nothing 1024
      inheritedPipeScript = "import subprocess,sys; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(10)'])"
  inherited <- timeout 3000000 (run inheritedPipeScript [])
  assert "pinned process group closes inherited pipes after wrapper exit"
    (case inherited of Just (Right (ExitSuccess, _, _)) -> True; _ -> False)
    ExitSuccess "" (show inherited)
  let marker = parent </> "process-group-child-completed"
      delayedChildScript = "import pathlib,sys,time; time.sleep(0.6); pathlib.Path(sys.argv[1]).write_text('late')" :: String
      closedPipeScript = "import subprocess,sys; subprocess.Popen([sys.executable, '-c', "
        <> reprPython delayedChildScript <> ", sys.argv[1]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); raise SystemExit(1)"
  exists <- doesFileExist marker
  if exists then removeFile marker else pure ()
  closed <- timeout 3000000 (run closedPipeScript [marker])
  threadDelay 900000
  wroteAfterFailure <- doesFileExist marker
  assert "pinned process group kills a detached-output child before failure returns"
    (case closed of Just (Right (ExitFailure 1, _, _)) -> not wroteAfterFailure; _ -> False)
    ExitSuccess "" (show (closed, wroteAfterFailure))
  bounded <- runProcessBoundedAt directoryFd python
    ["-c", "import sys;\nwhile True:\n sys.stdout.write('x' * 4096); sys.stdout.flush()"]
    [("PATH", "/usr/bin:/bin")] (Just 64) 1024
  assert "pinned bounded capture kills an infinite stdout writer at its limit"
    (bounded == Left (ProcessOutputLimitFailure StandardOutput 64))
    ExitSuccess "" (show bounded)
  missing <- runProcessBoundedAt directoryFd "/definitely/not/an/executable" []
    [("PATH", "/usr/bin:/bin")] Nothing 1024
  assert "pinned subprocess spawn failures retain their source category"
    (case missing of Left (ProcessSpawnFailure _) -> True; _ -> False)
    ExitSuccess "" (show missing)
  nonzero <- runProcessBoundedAt directoryFd python ["-c", "raise SystemExit(7)"]
    [("PATH", "/usr/bin:/bin")] Nothing 1024
  assert "nonzero child exits remain explicit results for caller policy"
    (case nonzero of Right (ExitFailure 7, _, _) -> True; _ -> False)
    ExitSuccess "" (show nonzero)
  closeFd directoryFd
  where
    reprPython value = show value

persistentTransitionChecks :: FilePath -> IO ()
persistentTransitionChecks parent = do
  (root, archiveId, initOutput) <- newProposalArchive parent "persistent-transition"
  operatorId <- requiredField "operator_id" initOutput
  operatorRevision <- recordRevisionForObject root operatorId
  target <- allocatedObjectId archiveId "archive.core/entity" "persistent-target"
  targetRevision <- commitEntityFixture root target
  transitionObject <- allocatedObjectId archiveId
    "archive.transition/retire" "persistent-retire"
  retireContract <- contractCitation root "archive.transition/retire"
  let retireRecord = retireDraft transitionObject operatorId operatorRevision
        target targetRevision retireContract
  plannedRetireRevision <- canonicalRevisionOf retireRecord
  proposal <- writeTransitionProposal root "persistent-retire"
    (transitionManifestFor
      ["records/retire.json"]
      [(transitionObject, "archive.transition/retire", "persistent-retire")]
      [(transitionObject, Nothing), (operatorId, Just operatorRevision)
      , (target, Just targetRevision)])
    ["records/retire.json", retireRecord]
  parsedTransition <- readTransitionProposal proposal
    >>= either (ioError . userError . Text.unpack) pure
  wrongPlanner <- dryRunAdmissionForCommit root parsedTransition
  wrongPlannerText <- dryRunAdmission root parsedTransition
  assert "admission planner preserves a proposal-kind refusal as a typed rejection"
    (wrongPlanner == Left (AdmissionProposalRejected
      "admission planning requires an admission proposal")
      && wrongPlannerText == Left "admission planning requires an admission proposal")
    ExitSuccess (show wrongPlanner <> " / " <> show wrongPlannerText) ""
  beforeHead <- gitHead root
  beforeCommits <- gitCommitCount root
  beforeRecords <- recordCount root
  (code, output, errors, annexCalled) <- persistentCommandWithFakeAnnex
    parent "single" root proposal
  afterHead <- gitHead root
  afterCommits <- gitCommitCount root
  afterRecords <- recordCount root
  assert "persistent one-record retire command succeeds"
    (code == ExitSuccess && null errors && not annexCalled
      && decodeUnique (LazyByteString.pack output) == Right (Aeson.object
        [ "status" .= ("committed" :: Text.Text)
        , "revision_ids" .= [plannedRetireRevision]
        , "retired_objects" .= [Text.pack target]
        , "destroyed_digests" .= ([] :: [Text.Text])
        , "retry" .= False
        ]))
    code output errors
  storedRevision <- recordRevisionForObject root transitionObject
  storedBytes <- readStoredRecord root storedRevision
  assert "persistent one-record retire creates one canonical commit"
    (code == ExitSuccess && null errors && not annexCalled
      && afterHead /= beforeHead && afterCommits == beforeCommits + 1
      && afterRecords == beforeRecords + 1
      && canonicalStored storedBytes
      && contains "\"at\":\"" (LazyByteString.unpack storedBytes))
    code output errors

  retryBytesBefore <- readStoredRecord root storedRevision
  retryHeadBefore <- gitHead root
  retryCommitsBefore <- gitCommitCount root
  retryRecordsBefore <- recordCount root
  (retryCode, retryOutput, retryErrors, retryAnnexCalled) <-
    persistentCommandWithFakeAnnex parent "retry" root proposal
  retryBytesAfter <- readStoredRecord root storedRevision
  retryHeadAfter <- gitHead root
  retryCommitsAfter <- gitCommitCount root
  retryRecordsAfter <- recordCount root
  assert "identical persistent retry creates no commit and preserves at and bytes"
    (retryCode == ExitSuccess && null retryErrors && not retryAnnexCalled
      && decodeUnique (LazyByteString.pack retryOutput) == Right (Aeson.object
        [ "status" .= ("committed" :: Text.Text)
        , "revision_ids" .= [plannedRetireRevision]
        , "retired_objects" .= [Text.pack target]
        , "destroyed_digests" .= ([] :: [Text.Text])
        , "retry" .= True
        ])
      && retryHeadAfter == retryHeadBefore
      && retryCommitsAfter == retryCommitsBefore
      && retryRecordsAfter == retryRecordsBefore
      && retryBytesAfter == retryBytesBefore)
    retryCode retryOutput retryErrors

  targetTwo <- allocatedObjectId archiveId "archive.core/entity"
    "persistent-untracked-target"
  targetTwoRevision <- commitEntityFixture root targetTwo
  transitionObjectTwo <- allocatedObjectId archiveId
    "archive.transition/retire" "persistent-untracked-retire"
  let retireRecordTwo = retireDraft transitionObjectTwo operatorId operatorRevision
        targetTwo targetTwoRevision retireContract
  plannedRetireRevisionTwo <- canonicalRevisionOf retireRecordTwo
  proposalTwo <- writeTransitionProposal root "persistent-untracked"
    (transitionManifestFor
      ["records/retire.json"]
      [(transitionObjectTwo, "archive.transition/retire", "persistent-untracked-retire")]
      [(transitionObjectTwo, Nothing), (operatorId, Just operatorRevision)
      , (targetTwo, Just targetTwoRevision)])
    ["records/retire.json", retireRecordTwo]
  failureHead <- gitHead root
  failureCommits <- gitCommitCount root
  failureRecords <- recordCount root
  (failureCode, failureOutput, failureErrors, failureAnnex) <-
    persistentCommandWithFailingCommit "transition" parent root proposalTwo
  failureAfterHead <- gitHead root
  failureAfterCommits <- gitCommitCount root
  failureAfterRecords <- recordCount root
  failedRecordPresent <- doesPathExist
    (root </> recordRelative plannedRetireRevisionTwo)
  temporaryIndexPresent <- doesPathExist (root </> ".git" </> "archive-door-index")
  assert "a Git commit failure removes every installed record and temporary index"
    (failureCode /= ExitSuccess && null failureOutput && not (null failureErrors)
      && not failureAnnex && failureAfterHead == failureHead
      && failureAfterCommits == failureCommits
      && failureAfterRecords == failureRecords
      && not failedRecordPresent && not temporaryIndexPresent)
    failureCode failureOutput failureErrors
  let unrelated = root </> "unrelated-untracked-note"
  writeFile unrelated "preserve me"
  _ <- checkedOutput "persistent fixture stages an unrelated file"
    ["-C", root, "add", "--", "unrelated-untracked-note"]
  stagedHead <- gitHead root
  stagedCommits <- gitCommitCount root
  stagedRecords <- recordCount root
  (stagedCode, stagedOutput, stagedErrors, stagedAnnex) <-
    persistentCommandWithFakeAnnex parent "staged" root proposalTwo
  stagedAfterHead <- gitHead root
  stagedAfterCommits <- gitCommitCount root
  stagedAfterRecords <- recordCount root
  assert "persistent transition refuses a pre-existing staged change without a prefix"
    (stagedCode /= ExitSuccess && null stagedOutput && not (null stagedErrors)
      && not stagedAnnex && stagedAfterHead == stagedHead
      && stagedAfterCommits == stagedCommits
      && stagedAfterRecords == stagedRecords)
    stagedCode stagedOutput stagedErrors
  _ <- checkedOutput "persistent fixture unstages the unrelated file"
    ["-C", root, "reset", "--", "unrelated-untracked-note"]
  untrackedHead <- gitHead root
  untrackedCommits <- gitCommitCount root
  (untrackedCode, untrackedOutput, untrackedErrors, untrackedAnnex) <-
    persistentCommandWithFakeAnnex parent "untracked" root proposalTwo
  untrackedAfterHead <- gitHead root
  untrackedAfterCommits <- gitCommitCount root
  (trackedCode, _trackedOutput, _trackedErrors) <- readProcessWithExitCode "git"
    ["-C", root, "ls-files", "--error-unmatch", "unrelated-untracked-note"] ""
  notePresent <- doesFileExist unrelated
  assert "persistent transition leaves unrelated untracked files unstaged"
    (untrackedCode == ExitSuccess && null untrackedErrors && not untrackedAnnex
      && untrackedAfterHead /= untrackedHead
      && untrackedAfterCommits == untrackedCommits + 1
      && notePresent && trackedCode /= ExitSuccess)
    untrackedCode untrackedOutput
      ("annexCalled=" <> show untrackedAnnex
        <> " notePresent=" <> show notePresent
        <> " trackedCode=" <> show trackedCode <> "\n" <> untrackedErrors)

  (v2Root, v2ArchiveId, v2InitOutput) <- newProposalArchive parent "persistent-v2"
  v2Operator <- requiredField "operator_id" v2InitOutput
  v2OperatorRevision <- recordRevisionForObject v2Root v2Operator
  artifact <- allocatedObjectId v2ArchiveId "archive.core/artifact" "persistent-destroy"
  artifactRevision <- commitArtifactFixture v2Root artifact
  retireV2 <- contractCitationVersion v2Root "archive.transition/retire" "2"
  destroyV2 <- contractCitationVersion v2Root "archive.transition/destroy" "2"
  retireV2Object <- allocatedObjectId v2ArchiveId
    "archive.transition/retire" "persistent-destroy-retire"
  destroyV2Object <- allocatedObjectId v2ArchiveId
    "archive.transition/destroy" "persistent-destroy-content"
  let retireV2Draft = retireDraft retireV2Object v2Operator v2OperatorRevision
        artifact artifactRevision retireV2
  retireV2Revision <- canonicalRevisionOf retireV2Draft
  let destroyV2Draft = destroyDraftWithPredecessor destroyV2Object v2Operator
        v2OperatorRevision artifact artifactRevision zeroDigest 0 destroyV2
        retireV2Object retireV2Revision
  destroyV2Revision <- canonicalRevisionOf destroyV2Draft
  v2Proposal <- writeTransitionProposal v2Root "persistent-v2"
    (transitionManifestFor
      ["records/retire.json", "records/destroy.json"]
      [(retireV2Object, "archive.transition/retire", "persistent-destroy-retire")
      , (destroyV2Object, "archive.transition/destroy", "persistent-destroy-content")]
      [(retireV2Object, Nothing), (destroyV2Object, Nothing)
      , (v2Operator, Just v2OperatorRevision)
      , (artifact, Just artifactRevision)])
    ["records/retire.json", retireV2Draft
    , "records/destroy.json", destroyV2Draft]
  v2Head <- gitHead v2Root
  v2Commits <- gitCommitCount v2Root
  v2Records <- recordCount v2Root
  (v2Code, v2Output, v2Errors, v2AnnexCalled) <-
    persistentCommandWithFakeAnnex parent "v2" v2Root v2Proposal
  v2AfterHead <- gitHead v2Root
  v2AfterCommits <- gitCommitCount v2Root
  v2AfterRecords <- recordCount v2Root
  assert "persistent version 2 transition command succeeds"
    (v2Code == ExitSuccess && null v2Errors && not v2AnnexCalled
      && decodeUnique (LazyByteString.pack v2Output) == Right (Aeson.object
        [ "status" .= ("committed" :: Text.Text)
        , "revision_ids" .= [retireV2Revision, destroyV2Revision]
        , "retired_objects" .= [Text.pack artifact]
        , "destroyed_digests" .= [Text.pack zeroDigest]
        , "retry" .= False
        ]))
    v2Code v2Output ("annexCalled=" <> show v2AnnexCalled <> "\n" <> v2Errors)
  storedRetireV2 <- recordRevisionForObject v2Root retireV2Object
  storedDestroyV2 <- recordRevisionForObject v2Root destroyV2Object
  retireV2Bytes <- readStoredRecord v2Root storedRetireV2
  destroyV2Bytes <- readStoredRecord v2Root storedDestroyV2
  assert "persistent version 2 retire and destroy share one commit"
    (v2Code == ExitSuccess && null v2Errors && not v2AnnexCalled
      && v2AfterHead /= v2Head && v2AfterCommits == v2Commits + 1
      && v2AfterRecords == v2Records + 2
      && canonicalStored retireV2Bytes && canonicalStored destroyV2Bytes)
    v2Code v2Output v2Errors

  (staleRoot, staleArchiveId, staleInitOutput) <- newProposalArchive parent "persistent-stale"
  staleOperator <- requiredField "operator_id" staleInitOutput
  staleOperatorRevision <- recordRevisionForObject staleRoot staleOperator
  staleTarget <- allocatedObjectId staleArchiveId "archive.core/entity" "persistent-stale-target"
  staleTargetRevision <- commitEntityFixture staleRoot staleTarget
  staleObject <- allocatedObjectId staleArchiveId
    "archive.transition/retire" "persistent-stale-retire"
  staleContract <- contractCitation staleRoot "archive.transition/retire"
  let staleRecord = retireDraft staleObject staleOperator staleOperatorRevision
        staleTarget staleTargetRevision staleContract
  staleProposal <- writeTransitionProposal staleRoot "persistent-stale"
    (transitionManifestFor
      ["records/retire.json"]
      [(staleObject, "archive.transition/retire", "persistent-stale-retire")]
      [(staleObject, Nothing), (staleOperator, Just staleOperatorRevision)
      , (staleTarget, Just staleTargetRevision)])
    ["records/retire.json", staleRecord]
  indexHead <- gitHead staleRoot
  indexCommits <- gitCommitCount staleRoot
  indexRecords <- recordCount staleRoot
  let hostileIndex = staleRoot </> ".git" </> "archive-door-index-hostile"
  writeFile hostileIndex "operator data"
  (hostileCode, hostileOutput, hostileErrors) <- doorCommandAt staleRoot
    ["transition", "--proposal", staleProposal] ""
  hostileAfterHead <- gitHead staleRoot
  hostileAfterCommits <- gitCommitCount staleRoot
  hostileAfterRecords <- recordCount staleRoot
  hostileIndexBytes <- readFile hostileIndex
  assert "mutation refuses a stale hostile index path and preserves it"
    (hostileCode /= ExitSuccess && null hostileOutput && not (null hostileErrors)
      && contains "stale Door index" hostileErrors
      && hostileAfterHead == indexHead
      && hostileAfterCommits == indexCommits && hostileAfterRecords == indexRecords
      && hostileIndexBytes == "operator data")
    hostileCode hostileOutput hostileErrors
  removeFile hostileIndex
  _ <- commitDraftFixture staleRoot (superseding staleTarget staleTarget staleTargetRevision)
  staleHead <- gitHead staleRoot
  staleCommits <- gitCommitCount staleRoot
  staleRecords <- recordCount staleRoot
  (staleCode, staleOutput, staleErrors) <- doorCommandAt staleRoot
    ["transition", "--proposal", staleProposal] ""
  staleAfterHead <- gitHead staleRoot
  staleAfterCommits <- gitCommitCount staleRoot
  staleAfterRecords <- recordCount staleRoot
  assert "stale persistent transition refuses without a successful prefix"
    (staleCode /= ExitSuccess && null staleOutput && not (null staleErrors)
      && staleAfterHead == staleHead
      && staleAfterCommits == staleCommits && staleAfterRecords == staleRecords)
    staleCode staleOutput staleErrors


historicalReplayChecks :: FilePath -> IO ()
historicalReplayChecks parent = do
  (root, archiveId, initOutput) <- newProposalArchive parent "historical-forward-reference"
  sourceObject <- allocatedObjectId archiveId "archive.core/entity" "forward-source"
  targetObject <- allocatedObjectId archiveId "archive.core/entity" "forward-target"
  let forwardSource = replace "\"refs\":[]"
        ("\"refs\":[{\"role\":\"about\",\"id\":"
          <> jsonString targetObject <> "}]")
        (entityDraft sourceObject)
  _ <- commitDraftFixture root forwardSource
  _ <- commitDraftFixture root (entityDraft targetObject)
  operatorId <- requiredField "operator_id" initOutput
  operatorRevision <- recordRevisionForObject root operatorId
  probeTarget <- allocatedObjectId archiveId "archive.core/entity" "forward-probe"
  probeRevision <- commitEntityFixture root probeTarget
  transitionObject <- allocatedObjectId archiveId
    "archive.transition/retire" "forward-probe-retire"
  retireContract <- contractCitation root "archive.transition/retire"
  let retireRecord = retireDraft transitionObject operatorId operatorRevision
        probeTarget probeRevision retireContract
  proposal <- writeTransitionProposal root "historical-forward-reference-probe"
    (transitionManifestFor
      ["records/retire.json"]
      [(transitionObject, "archive.transition/retire", "forward-probe-retire")]
      [(transitionObject, Nothing), (operatorId, Just operatorRevision)
      , (probeTarget, Just probeRevision)])
    ["records/retire.json", retireRecord]
  proposalRefusesContainingWith "transition"
    "a later commit cannot satisfy an earlier historical reference"
    "committed records cannot be replayed from their historical predecessors"
    root proposal

-- A proposal recheck race is intentionally not tested here: the command has no
-- deterministic pause between read and recheck.  A nondeterministic race test
-- would merely prove that the scheduler happened to win once.

gitObjectStoreTopologyChecks :: FilePath -> IO ()
gitObjectStoreTopologyChecks parent = do
  expectRefusal "object-store-alternate" installAlternate
    "Git alternate object stores are unsupported"
  expectRefusal "object-store-shallow" installShallow
    "shallow"
  expectRefusal "object-store-partial-clone" installPartialClone
    "partial-clone Git repositories are unsupported"
  expectRefusal "object-store-promisor-marker" installPromisorMarker
    "could not synchronize the reachable Git object-store baseline"
  expectRefusal "object-store-common-directory" installCommonDirectory
    "Git common-directory indirection is unsupported"
  expectRefusal "object-store-fsck-override" installFsckOverride
    "repository fsck configuration overrides are unsupported"
  where
    expectRefusal :: String -> (FilePath -> IO ()) -> String -> IO ()
    expectRefusal suffix configure expected = do
      (root, archiveId, _) <- newProposalArchive parent suffix
      objectId <- allocatedObjectId archiveId "archive.core/entity" suffix
      proposal <- writeAdmissionProposal root suffix (manifestFor
        ["records/entity.json"]
        [(objectId, "archive.core/entity", suffix)]
        [(objectId, Nothing)] []) ["records/entity.json", entityDraft objectId]
      headBefore <- gitHead root
      commitsBefore <- gitCommitCount root
      recordsBefore <- recordCount root
      configure root
      directTopologyRefusal <- if suffix == "object-store-shallow"
        then do
          direct <- withArchiveWriteLock root $ \lock ->
            commitRecordBatch lock (Text.pack headBefore) "topology fixture" [] (pure (Right ()))
          pure $ case direct of
            Left problem -> Just (Text.unpack (mutationErrorMessage problem))
            Right _ -> Nothing
        else pure Nothing
      (code, output, errors, annexCalled) <- persistentAdmissionCommandWithFakeAnnex
        parent suffix root proposal
      -- The unsupported topology itself prevents ordinary Git inspection.
      -- Remove this fixture after the Door refuses it, then verify no state
      -- advanced in the underlying repository.
      when (suffix == "object-store-common-directory") $
        removeFile (root </> ".git" </> "commondir")
      headAfter <- gitHead root
      commitsAfter <- gitCommitCount root
      recordsAfter <- recordCount root
      assert ("unsupported " <> suffix <> " topology refuses admission before ref advance")
        (code /= ExitSuccess && null output && not annexCalled
          && contains expected errors
          && headAfter == headBefore && commitsAfter == commitsBefore
          && recordsAfter == recordsBefore)
        code output errors
      if suffix == "object-store-shallow"
        then assert "RecordCommit independently refuses a shallow object-store baseline"
          (maybe False (contains "shallow Git repositories are unsupported") directTopologyRefusal)
          ExitSuccess (show directTopologyRefusal) ""
        else pure ()

    installAlternate root = do
      let alternate = root </> ".git" </> "objects" </> "info" </> "alternates"
      writeFile alternate (parent </> "external-object-store" <> "\n")

    installShallow root = do
      headValue <- gitHead root
      writeFile (root </> ".git" </> "shallow") (headValue <> "\n")

    installPartialClone root =
      shellChecked ("git -C " <> quote root
        <> " config extensions.partialClone origin")

    installPromisorMarker root = do
      packReachableHistoryFixture root
      packNames <- listDirectory (root </> ".git" </> "objects" </> "pack")
      case filter (\name -> length name >= 5 && drop (length name - 5) name == ".pack") packNames of
        [] -> ioError (userError "promisor fixture produced no Git pack")
        pack : _ -> writeFile
          (root </> ".git" </> "objects" </> "pack" </> take (length pack - 5) pack <> ".promisor") ""

    installCommonDirectory root = writeFile (root </> ".git" </> "commondir") "../shared\n"

    installFsckOverride root =
      shellChecked ("git -C " <> quote root <> " config --local fsck.skipList /dev/null")

-- Test-only fixture setup: create packed history while the lock is held so
-- RecordCommit exercises the packed baseline. This does not establish that
-- Git maintenance preserves earlier power-loss acknowledgements.
packReachableHistoryFixture :: FilePath -> IO ()
packReachableHistoryFixture root = do
  realGit <- findExecutable "git" >>= maybe
    (ioError (userError "coordinated Git maintenance fixture cannot find git")) pure
  result <- withArchiveWriteLock root $ \_ -> do
    (status, _, errors) <- readProcessWithExitCode realGit
      ["-C", root, "gc", "--prune=never"] ""
    pure $ if status == ExitSuccess
      then Right ()
      else Left (MutationOperationFailure
        (Text.pack ("packed-history fixture setup failed: " <> errors)))
  if result == Right ()
    then pure ()
    else ioError (userError (show result))

gitObjectStoreBarrierFailureChecks :: FilePath -> IO ()
gitObjectStoreBarrierFailureChecks parent = do
  corruptReachableObjectRefusal
  corruptedClosureRefusal
  pinnedDirectorySyncRefusal
  where
    setup suffix = do
      (root, archiveId, _) <- newProposalArchive parent suffix
      objectId <- allocatedObjectId archiveId "archive.core/entity" suffix
      proposal <- writeAdmissionProposal root suffix (manifestFor
        ["records/entity.json"]
        [(objectId, "archive.core/entity", suffix)]
        [(objectId, Nothing)] []) ["records/entity.json", entityDraft objectId]
      pure (root, proposal)

    runWithGitWrapper suffix root proposal wrapperBody = do
      realGit <- findExecutable "git" >>= maybe
        (ioError (userError "object-store failure fixture cannot find git")) pure
      let fakeBin = parent </> ("object-store-fake-git-" <> suffix)
          fakeGit = fakeBin </> "git"
      createDirectoryIfMissing True fakeBin
      writeFile fakeGit ("#!/bin/sh\n" <> wrapperBody realGit)
      permissions <- getPermissions fakeGit
      setPermissions fakeGit (setOwnerExecutable True permissions)
      environment <- getEnvironment
      let clean = ("ARCHIVE_GIT_EXECUTABLE", fakeGit)
            : filter ((/= "ARCHIVE_GIT_EXECUTABLE") . fst) environment
          process = (proc "door" ["admit", "--proposal", proposal])
            { cwd = Just root, env = Just clean }
      readCreateProcessWithExitCode process ""

    corruptReachableObjectRefusal = do
      (root, proposal) <- setup "object-store-corrupt-reachable-object"
      branch <- checkedOutput "corrupt-object fixture reads current branch"
        ["-C", root, "symbolic-ref", "-q", "HEAD"]
      tree <- checkedOutput "corrupt-object fixture reads reachable root tree"
        ["-C", root, "rev-parse", "HEAD^{tree}"]
      let refPath = root </> ".git" </> branch
          objectPath = root </> ".git" </> "objects" </> take 2 tree </> drop 2 tree
      refBefore <- readFile refPath
      objectExists <- doesFileExist objectPath
      assert "reachable corruption fixture uses a loose root tree"
        objectExists ExitSuccess objectPath ""
      setFileMode objectPath 0o600
      writeFile objectPath "corrupt"
      (code, output, errors, _) <- persistentAdmissionCommandWithFakeAnnex
        parent "object-store-corrupt-reachable-object" root proposal
      refAfter <- readFile refPath
      assert "a corrupt reachable object refuses admission before ref advance"
        (code /= ExitSuccess && null output && refAfter == refBefore)
        code output errors

    corruptedClosureRefusal = do
      (root, proposal) <- setup "object-store-fsck-failure"
      branch <- checkedOutput "object-integrity fixture reads current branch"
        ["-C", root, "symbolic-ref", "-q", "HEAD"]
      refPath <- pure (root </> ".git" </> branch)
      refBefore <- readFile refPath
      (code, output, errors) <- runWithGitWrapper "fsck-failure" root proposal $ \realGit ->
        "fsck=0\nfor argument in \"$@\"; do\n"
        <> "  if [ \"$argument\" = fsck ]; then fsck=1; fi\n"
        <> "done\n"
        <> "if [ \"$fsck\" = 1 ]; then\n"
        <> "  touch archive-door-fsck-fixture-ran\n"
        <> "  echo injected fsck failure >&2\n"
        <> "  exit 1\n"
        <> "fi\nexec " <> quote realGit <> " \"$@\"\n"
      refAfter <- readFile refPath
      marker <- doesFileExist (root </> ".git" </> "archive-door-fsck-fixture-ran")
      assert "an object-integrity check failure refuses before ref advance"
        (marker && code /= ExitSuccess && null output
          && contains "could not prove a complete, readable Git object closure" errors
          && refAfter == refBefore)
        code output errors

    pinnedDirectorySyncRefusal = do
      (root, proposal) <- setup "object-store-sync-error"
      headBefore <- gitHead root
      commitsBefore <- gitCommitCount root
      recordsBefore <- recordCount root
      (code, output, errors) <- runWithGitWrapper "sync-error" root proposal $ \realGit ->
        "fsck=0\nfor argument in \"$@\"; do\n"
        <> "  if [ \"$argument\" = fsck ]; then fsck=1; fi\n"
        <> "done\n" <> quote realGit <> " \"$@\"\nstatus=$?\n"
        <> "if [ \"$fsck\" = 1 ] && [ \"$status\" = 0 ]; then ln -s /dev/null objects/archive-door-invalid-entry; fi\n"
        <> "exit \"$status\"\n"
      removeFile (root </> ".git" </> "objects" </> "archive-door-invalid-entry")
      headAfter <- gitHead root
      commitsAfter <- gitCommitCount root
      recordsAfter <- recordCount root
      assert "a pinned object-store sync failure refuses before the branch ref advances"
        (code /= ExitSuccess && null output
          && contains "could not synchronize the reachable Git object-store baseline" errors
          && headAfter == headBefore && commitsAfter == commitsBefore
          && recordsAfter == recordsBefore)
        code output errors
worldContractAdmissionChecks :: FilePath -> IO ()
worldContractAdmissionChecks parent = do
  (root, archiveId, initOutput) <- newProposalArchive parent "world-contracts"
  let workNamespace = "archive.core/entity"
      workName = "world-fixture-paper-lanterns"
  workId <- allocatedObjectId archiveId workNamespace workName
  let work = envelope workId "entity" "{\"kind\":\"musical_work\",\"label\":\"Paper Lanterns\"}" "[]" ""
  workRevision <- revisionOf work
  workProposal <- writeAdmissionProposal root "world-work"
    (manifestFor ["records/work.json"] [(workId, workNamespace, workName)]
      [(workId, Nothing)] []) ["records/work.json", work]
  (workCode, workOut, workErr) <- doorCommandAt root ["admit", "--proposal", workProposal] ""
  assert "door admit commits a musical_work entity"
    (workCode == ExitSuccess && contains workRevision workOut)
    workCode workOut workErr

  correspondenceContract <- contractCitation root "archive.claim/work_musicbrainz_correspondence"
  let correspondenceNamespace = "archive.claim/work_musicbrainz_correspondence"
      correspondenceName = "world-fixture-paper-lanterns-musicbrainz"
  correspondenceId <- allocatedObjectId archiveId correspondenceNamespace correspondenceName
  let correspondence = musicBrainzClaim correspondenceId workId workRevision correspondenceContract
  correspondenceRevision <- canonicalRevisionOf correspondence
  correspondenceProposal <- writeAdmissionProposal root "world-correspondence"
    (manifestFor ["records/correspondence.json"]
      [(correspondenceId, correspondenceNamespace, correspondenceName)]
      [(correspondenceId, Nothing)] [])
    ["records/correspondence.json", correspondence]
  (mappingCode, mappingOut, mappingErr) <- doorCommandAt root
    ["admit", "--proposal", correspondenceProposal] ""
  assert "door admit commits a pinned MusicBrainz correspondence claim"
    (mappingCode == ExitSuccess && contains correspondenceRevision mappingOut)
    mappingCode mappingOut mappingErr

  performanceContract <- contractCitation root "archive.claim/performance_work"
  let performanceNamespace = "archive.claim/performance_work"
      performanceName = "world-fixture-performance-claim-shape-only"
  performanceId <- allocatedObjectId archiveId performanceNamespace performanceName
  -- The target is the already admitted correspondence claim solely to exercise
  -- the structural Claim-target rule. It is not evidence of a performance.
  let performance = performanceWorkClaim performanceId correspondenceId correspondenceRevision
        workId workRevision performanceContract
  performanceRevision <- canonicalRevisionOf performance
  performanceProposal <- writeAdmissionProposal root "world-performance-claim"
    (manifestFor ["records/performance.json"]
      [(performanceId, performanceNamespace, performanceName)]
      [(performanceId, Nothing)] [])
    ["records/performance.json", performance]
  (performanceOkCode, performanceOkOut, performanceOkErr) <- doorCommandAt root
    ["admit", "--proposal", performanceProposal] ""
  assert "door admit commits the pinned performance_work claim structure"
    (performanceOkCode == ExitSuccess && contains performanceRevision performanceOkOut)
    performanceOkCode performanceOkOut performanceOkErr

  let invalidPerformanceName = "world-fixture-invalid-performance-pin"
  invalidPerformanceId <- allocatedObjectId archiveId performanceNamespace invalidPerformanceName
  let invalidPerformance = performanceWorkClaim invalidPerformanceId workId workRevision
        workId workRevision performanceContract
  invalidPerformanceProposal <- writeAdmissionProposal root "world-invalid-performance"
    (manifestFor ["records/performance.json"]
      [(invalidPerformanceId, performanceNamespace, invalidPerformanceName)]
      [(invalidPerformanceId, Nothing)] [])
    ["records/performance.json", invalidPerformance]
  (performanceCode, performanceOut, performanceErr) <- doorCommandAt root
    ["admit", "--proposal", invalidPerformanceProposal] ""
  assert "performance_work rejects an Entity in the performance claim role"
    (performanceCode /= ExitSuccess && contains "performance reference must target a Claim revision" (performanceErr <> performanceOut))
    performanceCode performanceOut performanceErr

  operatorId <- requiredField "operator_id" initOutput
  operatorRevision <- recordRevisionForObject root operatorId
  let invalidMappingName = "world-fixture-wrong-work-kind"
  invalidMappingId <- allocatedObjectId archiveId correspondenceNamespace invalidMappingName
  let invalidMapping = musicBrainzClaim invalidMappingId operatorId operatorRevision correspondenceContract
  invalidMappingProposal <- writeAdmissionProposal root "world-invalid-work-kind"
    (manifestFor ["records/correspondence.json"]
      [(invalidMappingId, correspondenceNamespace, invalidMappingName)]
      [(invalidMappingId, Nothing)] [])
    ["records/correspondence.json", invalidMapping]
  (kindCode, kindOut, kindErr) <- doorCommandAt root
    ["admit", "--proposal", invalidMappingProposal] ""
  assert "work correspondence rejects a non-musical_work entity"
    (kindCode /= ExitSuccess && contains "work reference must target an Entity revision of kind musical_work" (kindErr <> kindOut))
    kindCode kindOut kindErr

  audioId <- allocatedObjectId archiveId "archive.core/artifact" "world-fixture-audio"
  audioRevision <- commitArtifactFixture root audioId
  selectionContract <- contractCitation root "archive.claim/audio_selection"
  let selectionNamespace = "archive.claim/audio_selection"
      selectionName = "world-fixture-selection"
  selectionId <- allocatedObjectId archiveId selectionNamespace selectionName
  let selection = envelope selectionId "claim"
        "{\"pieces\":[{\"start_sample\":10,\"end_sample\":200,\"sample_rate\":48000}]}"
        ("[" <> transitionReference "piece" audioId audioRevision <> "]")
        (",\"contracts\":[" <> citationJson selectionContract <> "]")
  selectionRevision <- canonicalRevisionOf selection
  selectionProposal <- writeAdmissionProposal root "world-audio-selection"
    (manifestFor ["records/selection.json"]
      [(selectionId, selectionNamespace, selectionName)] [(selectionId, Nothing)] [])
    ["records/selection.json", selection]
  (selectionCode, selectionOut, selectionErr) <- doorCommandAt root
    ["admit", "--proposal", selectionProposal] ""
  assert "door admit commits a selection pinned to exact Artifact audio"
    (selectionCode == ExitSuccess && contains selectionRevision selectionOut)
    selectionCode selectionOut selectionErr
  wrongSelectionId <- allocatedObjectId archiveId selectionNamespace "world-fixture-wrong-selection-target"
  let wrongSelection = replace selectionId wrongSelectionId
        (replace audioId workId (replace audioRevision workRevision selection))
  wrongSelectionProposal <- writeAdmissionProposal root "world-wrong-selection-target"
    (manifestFor ["records/selection.json"]
      [(wrongSelectionId, selectionNamespace, "world-fixture-wrong-selection-target")]
      [(wrongSelectionId, Nothing)] [])
    ["records/selection.json", wrongSelection]
  (wrongSelectionCode, wrongSelectionOut, wrongSelectionErr) <- doorCommandAt root
    ["admit", "--proposal", wrongSelectionProposal] ""
  assert "selection rejects an Entity instead of an Artifact source"
    (wrongSelectionCode /= ExitSuccess
      && contains "piece reference must target an Artifact revision"
        (wrongSelectionErr <> wrongSelectionOut))
    wrongSelectionCode wrongSelectionOut wrongSelectionErr

  occurrenceContract <- contractCitation root "archive.claim/song_occurrence"
  let occurrenceNamespace = "archive.claim/song_occurrence"
      occurrenceName = "world-fixture-occurrence"
  occurrenceId <- allocatedObjectId archiveId occurrenceNamespace occurrenceName
  let occurrence = envelope occurrenceId "claim" "{}"
        ("[" <> transitionReference "selection" selectionId selectionRevision <> "]")
        (",\"contracts\":[" <> citationJson occurrenceContract <> "]")
  occurrenceRevision <- canonicalRevisionOf occurrence
  occurrenceProposal <- writeAdmissionProposal root "world-song-occurrence"
    (manifestFor ["records/occurrence.json"]
      [(occurrenceId, occurrenceNamespace, occurrenceName)] [(occurrenceId, Nothing)] [])
    ["records/occurrence.json", occurrence]
  (occurrenceCode, occurrenceOut, occurrenceErr) <- doorCommandAt root
    ["admit", "--proposal", occurrenceProposal] ""
  assert "door admit commits an occurrence pinned to an audio selection"
    (occurrenceCode == ExitSuccess && contains occurrenceRevision occurrenceOut)
    occurrenceCode occurrenceOut occurrenceErr
  wrongOccurrenceId <- allocatedObjectId archiveId occurrenceNamespace "world-fixture-wrong-occurrence-target"
  let wrongOccurrence = replace occurrenceId wrongOccurrenceId
        (replace selectionId correspondenceId
          (replace selectionRevision correspondenceRevision occurrence))
  wrongOccurrenceProposal <- writeAdmissionProposal root "world-wrong-occurrence-target"
    (manifestFor ["records/occurrence.json"]
      [(wrongOccurrenceId, occurrenceNamespace, "world-fixture-wrong-occurrence-target")]
      [(wrongOccurrenceId, Nothing)] [])
    ["records/occurrence.json", wrongOccurrence]
  (wrongOccurrenceCode, wrongOccurrenceOut, wrongOccurrenceErr) <- doorCommandAt root
    ["admit", "--proposal", wrongOccurrenceProposal] ""
  assert "occurrence rejects a Claim without the audio selection contract"
    (wrongOccurrenceCode /= ExitSuccess
      && contains "selection reference must target a Claim citing archive.claim/audio_selection version 1"
        (wrongOccurrenceErr <> wrongOccurrenceOut))
    wrongOccurrenceCode wrongOccurrenceOut wrongOccurrenceErr

  performanceV2 <- contractCitationVersion root "archive.claim/performance_work" "2"
  let typedName = "world-fixture-typed-occurrence-work"
  typedId <- allocatedObjectId archiveId performanceNamespace typedName
  let typed = performanceWorkClaim typedId occurrenceId occurrenceRevision
        workId workRevision performanceV2
  typedRevision <- canonicalRevisionOf typed
  typedProposal <- writeAdmissionProposal root "world-typed-occurrence-work"
    (manifestFor ["records/link.json"]
      [(typedId, performanceNamespace, typedName)] [(typedId, Nothing)] [])
    ["records/link.json", typed]
  (typedCode, typedOut, typedErr) <- doorCommandAt root
    ["admit", "--proposal", typedProposal] ""
  assert "door admit commits typed occurrence-to-Work version 2 link"
    (typedCode == ExitSuccess && contains typedRevision typedOut)
    typedCode typedOut typedErr

  invalidTypedId <- allocatedObjectId archiveId performanceNamespace "world-fixture-v2-wrong-target"
  let invalidTyped = performanceWorkClaim invalidTypedId correspondenceId correspondenceRevision
        workId workRevision performanceV2
  invalidTypedProposal <- writeAdmissionProposal root "world-v2-wrong-target"
    (manifestFor ["records/link.json"]
      [(invalidTypedId, performanceNamespace, "world-fixture-v2-wrong-target")]
      [(invalidTypedId, Nothing)] [])
    ["records/link.json", invalidTyped]
  (invalidTypedCode, invalidTypedOut, invalidTypedErr) <- doorCommandAt root
    ["admit", "--proposal", invalidTypedProposal] ""
  assert "version 2 rejects an arbitrary Claim as a song occurrence"
    (invalidTypedCode /= ExitSuccess
      && contains "performance reference must target a Claim citing archive.claim/song_occurrence version 1"
        (invalidTypedErr <> invalidTypedOut))
    invalidTypedCode invalidTypedOut invalidTypedErr

  -- World's incremental refresh (ADR 0055) is proved against a real cut of
  -- this chain: the export here, and the export since here at the end.
  cut <- trim <$> gitHead root
  (cutCode, cutOut, cutErr) <- readCreateProcessWithExitCode
    ((proc "archive" ["export"]) {cwd = Just root}) ""
  assert "archive export carries the World chain before the review records"
    (cutCode == ExitSuccess && contains ("\"snapshot\":\"" <> cut <> "\"") cutOut)
    cutCode cutOut cutErr

  reviewRevisions <- reviewChainAdmissions root archiveId (audioId, audioRevision) (workId, workRevision)

  -- World reads only `archive export`. Admission above proves Door committed
  -- the chain; this proves every admitted revision crosses that boundary.
  -- Setting ARCHIVE_TEST_WORLD_EXPORT captures this output as World's
  -- world/tests/fixtures/door-world-chain-export.json.
  (exportCode, exportOut, exportErr) <- readCreateProcessWithExitCode
    ((proc "archive" ["export"]) {cwd = Just root}) ""
  assert "archive export carries the admitted World chain at 0.0.5"
    (exportCode == ExitSuccess
      && contains "\"status\":\"ok\"" exportOut
      && contains "\"spec\":\"archive/0.0.11\"" exportOut
      && all (`contains` exportOut)
        ([ workRevision, correspondenceRevision, performanceRevision, audioRevision
         , selectionRevision, occurrenceRevision, typedRevision ] <> reviewRevisions))
    exportCode exportOut exportErr
  (sinceCode, sinceOut, sinceErr) <- readCreateProcessWithExitCode
    ((proc "archive" ["export", "--since", cut]) {cwd = Just root}) ""
  assert "archive export --since carries the review records and pins the earlier chain"
    (sinceCode == ExitSuccess
      && contains ("\"since\":\"" <> cut <> "\"") sinceOut
      && all (`contains` sinceOut) reviewRevisions
      && all (`contains` sinceOut) [audioRevision, workRevision]
      && not (any (`contains` sinceOut) [correspondenceRevision, performanceRevision, typedRevision]))
    sinceCode sinceOut sinceErr
  -- Setting ARCHIVE_TEST_WORLD_CUT_EXPORT and ARCHIVE_TEST_WORLD_SINCE_EXPORT
  -- captures the cut and the export since it as World's
  -- door-world-chain-cut-export.json and door-world-chain-since-export.json.
  mapM_ (\(name, output) -> lookupEnv name >>= mapM_ (`writeFile` output))
    [ ("ARCHIVE_TEST_WORLD_EXPORT", exportOut)
    , ("ARCHIVE_TEST_WORLD_CUT_EXPORT", cutOut)
    , ("ARCHIVE_TEST_WORLD_SINCE_EXPORT", sinceOut)
    ]

citationJson :: (String, String, String) -> String
citationJson (identifier, version, digest) =
  "{\"id\":" <> jsonString identifier <> ",\"version\":" <> jsonString version
    <> ",\"digest\":" <> jsonString digest <> "}"

musicBrainzClaim :: String -> String -> String
                  -> (String, String, String) -> String
musicBrainzClaim objectId workId workRevision (identifier, version, digest) =
  envelope objectId "claim"
    "{\"external_work\":\"https://musicbrainz.org/work/11111111-1111-4111-8111-111111111111\",\"mapping\":\"skos:exactMatch\"}"
    ("[{\"role\":\"work\",\"id\":" <> jsonString workId
      <> ",\"revision_id\":" <> jsonString workRevision <> "}]")
    (",\"contracts\":[{\"id\":" <> jsonString identifier
      <> ",\"version\":" <> jsonString version
      <> ",\"digest\":" <> jsonString digest <> "}]")

performanceWorkClaim :: String -> String -> String -> String -> String
                     -> (String, String, String) -> String
performanceWorkClaim objectId performanceId performanceRevision workId workRevision
    (identifier, version, digest) =
  envelope objectId "claim" "{}"
    ("[{\"role\":\"performance\",\"id\":" <> jsonString performanceId
      <> ",\"revision_id\":" <> jsonString performanceRevision <> "},{\"role\":\"work\",\"id\":"
      <> jsonString workId <> ",\"revision_id\":" <> jsonString workRevision <> "}]")
    (",\"contracts\":[{\"id\":" <> jsonString identifier
      <> ",\"version\":" <> jsonString version
      <> ",\"digest\":" <> jsonString digest <> "}]")

-- | The pre-commit recheck reuses the plan only while HEAD still names the
-- planned commit, and never skips the proposal byte check.
admissionRecheckChecks :: FilePath -> IO ()
admissionRecheckChecks parent = do
  (root, archiveId, _) <- newProposalArchive parent "admission-recheck"
  objectId <- allocatedObjectId archiveId "archive.core/entity" "recheck-entity"
  proposal <- writeAdmissionProposal root "recheck-entity" (manifestFor
    ["records/entity.json"]
    [(objectId, "archive.core/entity", "recheck-entity")]
    [(objectId, Nothing)] []) ["records/entity.json", entityDraft objectId]
  parsed <- readAdmissionProposal proposal
    >>= either (ioError . userError . Text.unpack) pure
  realGit <- findExecutable "git" >>= maybe
    (ioError (userError "recheck fixture cannot find git")) pure
  let wrapperDirectory = parent </> "recheck-git"
      wrapper = wrapperDirectory </> "git"
      callLog = wrapperDirectory </> "calls"
  createDirectoryIfMissing True wrapperDirectory
  writeFile wrapper ("#!/bin/sh\nprintf '%s\\n' \"$*\" >> '" <> callLog
    <> "'\nexec '" <> realGit <> "' \"$@\"\n")
  setFileMode wrapper 0o755
  setEnv "ARCHIVE_GIT_EXECUTABLE" wrapper
  planned <- dryRunAdmissionForCommit root parsed
  plan <- either (ioError . userError . show) pure planned
  writeFile callLog ""
  unchanged <- recheckAdmissionForCommit root parsed plan
  unchangedCalls <- lines <$> readFile callLog
  setEnv "ARCHIVE_GIT_EXECUTABLE" realGit
  assert "an unchanged HEAD keeps the plan without reading the archive again"
    (unchanged == Right plan
      && not (null unchangedCalls)
      && all (\call -> "rev-parse " `isPrefixOfString` call) unchangedCalls)
    ExitSuccess (show unchanged <> "\n" <> unlines unchangedCalls) ""
  let recordFile = proposal </> "records" </> "entity.json"
  original <- readFile recordFile
  length original `seq` writeFile recordFile (original <> " ")
  changedBytes <- recheckAdmissionForCommit root parsed plan
  writeFile recordFile original
  assert "an unchanged HEAD still refuses changed proposal bytes"
    (changedBytes == Left (AdmissionProposalRejected
      "proposal recheck failed: proposal record bytes changed during operation")
      || changedBytes == Left (AdmissionProposalRejected
      "proposal record bytes changed during operation"))
    ExitSuccess (show changedBytes) ""
  other <- allocatedObjectId archiveId "archive.core/entity" "recheck-other"
  _ <- commitEntityFixture root other
  moved <- recheckAdmissionForCommit root parsed plan
  assert "a moved HEAD is planned again in full"
    (case moved of
      Right replanned -> plannedAdmissionSnapshot replanned /= plannedAdmissionSnapshot plan
      Left _ -> False)
    ExitSuccess (show moved) ""
  where
    isPrefixOfString prefix value = take (length prefix) value == prefix

-- | ADR 0052: a checkpoint lets Door skip work, never checks. Each test that
-- a checkpointed path skipped work runs under a Git wrapper that refuses the
-- work skipped (the history walk of a full read, and a whole-store fsck), and
-- each is anchored by the same call failing without the checkpoint.
checkpointChecks :: FilePath -> IO ()
checkpointChecks parent = do
  (root, archiveId, _) <- newProposalArchive parent "checkpoint"
  realGit <- findExecutable "git" >>= maybe
    (ioError (userError "checkpoint fixture cannot find git")) pure
  let bombDirectory = parent </> "checkpoint-git"
      bomb = bombDirectory </> "git"
      checkpointFile = root </> ".git" </> "archive-door-verified.json"
      proposalFor name = do
        objectId <- allocatedObjectId archiveId "archive.core/entity" name
        path <- writeAdmissionProposal root name (manifestFor
          ["records/entity.json"] [(objectId, "archive.core/entity", name)]
          [(objectId, Nothing)] []) ["records/entity.json", entityDraft objectId]
        readAdmissionProposal path >>= either (ioError . userError . Text.unpack) pure
      withBomb action = do
        setEnv "ARCHIVE_GIT_EXECUTABLE" bomb
        result <- action
        setEnv "ARCHIVE_GIT_EXECUTABLE" realGit
        pure result
      checkpointNames expected = do
        loaded <- readCheckpoint root
        current <- gitHead root
        pure (fmap checkpointCommit loaded == Just (Text.pack current) && current == expected)
  createDirectoryIfMissing True bombDirectory
  writeFile bomb ("#!/bin/sh\nfor argument in \"$@\"; do case \"$argument\" in log|fsck) exit 97;; esac; done\nexec "
    <> quote realGit <> " \"$@\"\n")
  setFileMode bomb 0o755

  firstProposal <- proposalFor "checkpoint-first"
  first <- commitAdmission root firstProposal
  firstHead <- gitHead root
  firstNamed <- checkpointNames firstHead
  firstCheckpoint <- readCheckpoint root
  committedRecords <- recordCount root
  assert "a fully verified admission records a checkpoint for the new HEAD"
    (either (const False) (const True) first && firstNamed
      && maybe False ((== committedRecords) . length . checkpointRecords) firstCheckpoint)
    ExitSuccess (show first <> " / " <> show firstCheckpoint) ""

  secondProposal <- proposalFor "checkpoint-second"
  plannedWithBomb <- withBomb (dryRunAdmissionForCommit root secondProposal)
  committedWithBomb <- withBomb (commitAdmission root secondProposal)
  secondHead <- gitHead root
  secondNamed <- checkpointNames secondHead
  assert "a checkpointed HEAD is planned and committed without a full re-read or whole-store fsck"
    (either (const False) (const True) plannedWithBomb
      && either (const False) (const True) committedWithBomb
      && secondHead /= firstHead && secondNamed)
    ExitSuccess (show plannedWithBomb <> " / " <> show committedWithBomb) ""

  additions <- filter ("archive-door-verified-after-" `isPrefixOfName`) <$> listDirectory (root </> ".git")
  assert "a commit on a checkpoint writes one small addition, not a new base (ADR 0054)"
    (additions == ["archive-door-verified-after-" <> firstHead <> ".json"])
    ExitSuccess (show additions) ""
  let additionFile = root </> ".git" </> ("archive-door-verified-after-" <> firstHead <> ".json")
  damagedAddition <- ByteString.readFile additionFile
  ByteString.writeFile additionFile
    (ByteString.map (\byte -> if byte == 58 then 59 else byte) damagedAddition)
  brokenChain <- readCheckpoint root
  assert "a damaged addition ends the chain at the last good tip"
    (fmap checkpointCommit brokenChain == Just (Text.pack firstHead))
    ExitSuccess (show (fmap checkpointCommit brokenChain)) ""
  ByteString.writeFile additionFile damagedAddition

  thirdProposal <- proposalFor "checkpoint-third"
  removeFile checkpointFile
  fromMemory <- withBomb (dryRunAdmissionForCommit root thirdProposal)
  assert "a process remembers the snapshot of its own commit without the checkpoint file (ADR 0054)"
    (either (const False) (const True) fromMemory)
    ExitSuccess (show fromMemory) ""
  forgetSnapshots
  withoutCheckpoint <- withBomb (dryRunAdmissionForCommit root thirdProposal)
  assert "without a checkpoint the same plan needs the full re-read (anchor)"
    (either (const True) (const False) withoutCheckpoint)
    ExitSuccess (show withoutCheckpoint) ""

  _ <- commitAdmission root thirdProposal
  bytes <- ByteString.readFile checkpointFile
  ByteString.writeFile checkpointFile (ByteString.map (\byte -> if byte == 58 then 59 else byte) bytes)
  torn <- readCheckpoint root
  forgetSnapshots
  tornPlan <- withBomb (dryRunAdmissionForCommit root =<< proposalFor "checkpoint-torn")
  assert "a damaged checkpoint is ignored and the archive is verified in full"
    (torn == Nothing && either (const True) (const False) tornPlan)
    ExitSuccess (show torn <> " / " <> show tornPlan) ""

  _ <- commitAdmission root =<< proposalFor "checkpoint-fourth"
  verifiedHead <- gitHead root
  stillNamed <- checkpointNames verifiedHead
  other <- allocatedObjectId archiveId "archive.core/entity" "checkpoint-outside"
  _ <- commitEntityFixture root other
  stale <- readCheckpoint root
  forgetSnapshots
  stalePlan <- withBomb (dryRunAdmissionForCommit root =<< proposalFor "checkpoint-stale")
  assert "a checkpoint for an older HEAD is not used"
    (stillNamed && fmap checkpointCommit stale == Just (Text.pack verifiedHead)
      && either (const True) (const False) stalePlan)
    ExitSuccess (show stalePlan) ""

  _ <- commitAdmission root =<< proposalFor "checkpoint-fifth"
  packedHead <- gitHead root
  beforePack <- checkpointNames packedHead
  shellChecked ("git -C " <> quote root <> " repack -q >/dev/null")
  afterPack <- readCheckpoint root
  assert "a changed pack set invalidates the checkpoint"
    (beforePack && afterPack == Nothing) ExitSuccess (show afterPack) ""

  _ <- commitAdmission root =<< proposalFor "checkpoint-compaction-base"
  mapM_ (\index -> commitAdmission root =<< proposalFor ("checkpoint-chain-" <> show index))
    [1 .. checkpointCompactAfter]
  chainHead <- gitHead root
  chainNamed <- checkpointNames chainHead
  compacted <- filter ("archive-door-verified-after-" `isPrefixOfName`) <$> listDirectory (root </> ".git")
  assert "a long chain is compacted into a new base"
    (chainNamed && length compacted < checkpointCompactAfter)
    ExitSuccess (show (length compacted)) ""

  _ <- commitAdmission root =<< proposalFor "checkpoint-sixth"
  beforePrune <- doesFileExist checkpointFile
  pruned <- prunePackedGitObjects root
  afterPrune <- doesFileExist checkpointFile
  afterPruneAdditions <- filter ("archive-door-verified-after-" `isPrefixOfName`) <$> listDirectory (root </> ".git")
  assert "coordinated maintenance removes the checkpoint and its additions"
    (beforePrune && pruned == Right () && not afterPrune && null afterPruneAdditions)
    ExitSuccess (show pruned) ""
  where
    isPrefixOfName prefix name = take (length prefix) name == prefix

-- | The snapshot a process extends after its own commits must be the one a
-- full read of the new commit builds (ADR 0054).
rememberedSnapshotChecks :: FilePath -> IO ()
rememberedSnapshotChecks parent = do
  (root, archiveId, _) <- newProposalArchive parent "remembered-snapshot"
  let admit name = do
        objectId <- allocatedObjectId archiveId "archive.core/entity" name
        path <- writeAdmissionProposal root name (manifestFor
          ["records/entity.json"] [(objectId, "archive.core/entity", name)]
          [(objectId, Nothing)] []) ["records/entity.json", entityDraft objectId]
        proposal <- readAdmissionProposal path >>= either (ioError . userError . Text.unpack) pure
        commitAdmission root proposal >>= either (ioError . userError . show) (const (pure ()))
      summary data' =
        ( snapshotCommitId data'
        , snapshotHeadsByObject data'
        , snapshotRevisionIntroducers data'
        , snapshotCommitOrder data'
        , snapshotImmutableAdditions data'
        , snapshotRecordSummaries data'
        , map (\entry -> (entryPath entry, entryRevision entry, entryObject entry)) (snapshotRecords data')
        , sort (show <$> CheckpointMap.toList (fmap (sort . map show) (revisionsByObject (snapshotStore data'))))
        )
      current = do
        resolved <- resolveSnapshot root >>= either (ioError . userError . show) pure
        loadSnapshot resolved >>= either (ioError . userError . show) pure
  mapM_ (\index -> admit ("remembered-" <> show (index :: Int))) [1 .. 5]
  remembered <- summary <$> current
  forgetSnapshots
  removeFile (root </> ".git" </> "archive-door-verified.json")
  fresh <- summary <$> current
  assert "a snapshot extended in memory equals a full read of the same commit"
    (remembered == fresh) ExitSuccess (show remembered <> "\n" <> show fresh) ""

-- | Door creates no executable file: Git records the executable bit, so a
-- file written 0700 is committed as 100755, or leaves the worktree differing
-- from a tree Door committed as 100644.
fileModeChecks :: FilePath -> IO ()
fileModeChecks parent = do
  (root, archiveId, _) <- newProposalArchive parent "file-modes"
  (_, initialTree, _) <- readProcessWithExitCode "git" ["-C", root, "ls-tree", "-r", "HEAD"] ""
  let initialModes = map (takeWhile (/= ' ')) (lines initialTree)
  assert "archive init commits no executable file"
    (not (null initialModes) && all (`elem` ["100644", "120000"]) initialModes)
    ExitSuccess initialTree ""
  objectId <- allocatedObjectId archiveId "archive.core/entity" "file-mode-entity"
  -- The real `door admit`: a fixture that writes and commits the record
  -- itself would not exercise Door's record writer.
  proposal <- writeAdmissionProposal root "file-modes" (manifestFor
    ["records/entity.json"] [(objectId, "archive.core/entity", "file-mode-entity")]
    [(objectId, Nothing)] []) ["records/entity.json", entityDraft objectId]
  (admitCode, admitOutput, admitErrors) <- doorCommandAt root ["admit", "--proposal", proposal] ""
  assert "file-mode fixture admits through Door" (admitCode == ExitSuccess) admitCode admitOutput admitErrors
  (_, tree, _) <- readProcessWithExitCode "git" ["-C", root, "ls-tree", "-r", "HEAD", "--", "records"] ""
  (_, status, _) <- readProcessWithExitCode "git" ["-C", root, "status", "--porcelain", "--untracked-files=no"] ""
  assert "an admitted record's worktree file matches its committed mode"
    (length (lines tree) >= 2 && all ("100644 " `isPrefixOf`) (lines tree) && null status)
    ExitSuccess tree status

-- | `door serve` answers exactly as the command line does, keeps planning
-- from memory, notices a HEAD it did not make, and stays private (ADR 0054).
serviceChecks :: FilePath -> IO ()
serviceChecks parent = do
  (root, archiveId, _) <- newProposalArchive parent "service"
  realGit <- findExecutable "git" >>= maybe
    (ioError (userError "service fixture cannot find git")) pure
  let socketDirectory = parent </> "service-socket"
      socketPath = socketDirectory </> "door.sock"
      wrapperDirectory = parent </> "service-git"
      wrapper = wrapperDirectory </> "git"
      armed = parent </> "service-git-armed"
      proposalNamed name = do
        objectId <- allocatedObjectId archiveId "archive.core/entity" name
        writeAdmissionProposal root name (manifestFor
          ["records/entity.json"] [(objectId, "archive.core/entity", name)]
          [(objectId, Nothing)] []) ["records/entity.json", entityDraft objectId]
      request command proposal = do
        connection <- Socket.socket Socket.AF_UNIX Socket.Stream Socket.defaultProtocol
        Socket.connect connection (Socket.SockAddrUnix socketPath)
        SocketBytes.sendAll connection (LazyByteString.toStrict (Aeson.encode (Aeson.object
          ["command" .= (command :: Text.Text), "proposal" .= proposal])) <> "\n")
        answer <- receiveAll connection ByteString.empty
        Socket.close connection
        pure (decodeUnique (LazyByteString.fromStrict answer))
      receiveAll connection collected = do
        chunk <- SocketBytes.recv connection 65536
        if ByteString.null chunk then pure collected else receiveAll connection (collected <> chunk)
      rawRequest bytes = do
        connection <- Socket.socket Socket.AF_UNIX Socket.Stream Socket.defaultProtocol
        Socket.connect connection (Socket.SockAddrUnix socketPath)
        SocketBytes.sendAll connection bytes
        Socket.shutdown connection Socket.ShutdownSend
        answer <- receiveAll connection ByteString.empty
        Socket.close connection
        pure (decodeUnique (LazyByteString.fromStrict answer))
      errorCode answer = case answer of
        Right (Aeson.Object fields)
          | KeyMap.lookup "ok" fields == Just (Aeson.Bool False)
          , Just (Aeson.Object problem) <- KeyMap.lookup "error" fields
          , Just (Aeson.String code) <- KeyMap.lookup "code" problem
          , Just (Aeson.String _) <- KeyMap.lookup "message" problem -> Just code
        _ -> Nothing :: Maybe Text.Text
      okResult answer = case answer of
        Right (Aeson.Object fields) | KeyMap.lookup "ok" fields == Just (Aeson.Bool True) ->
          KeyMap.lookup "result" fields
        _ -> Nothing
      waitForSocket tries = do
        present <- doesPathExist socketPath
        if present || tries <= (0 :: Int) then pure present
          else threadDelay 50000 >> waitForSocket (tries - 1)
  createDirectoryIfMissing True socketDirectory
  setFileMode socketDirectory 0o700
  createDirectoryIfMissing True wrapperDirectory
  -- Once armed, the wrapper refuses the work a remembered or checkpointed
  -- snapshot must not repeat: the history walk of a full read and the
  -- whole-store fsck.
  writeFile wrapper ("#!/bin/sh\nif [ -e " <> quote armed <> " ]; then for argument in \"$@\"; do case \"$argument\" in log|fsck) exit 97;; esac; done; fi\nexec "
    <> quote realGit <> " \"$@\"\n")
  setFileMode wrapper 0o755
  environment <- getEnvironment
  let serviceEnvironment = ("ARCHIVE_GIT_EXECUTABLE", wrapper)
        : filter ((/= "ARCHIVE_GIT_EXECUTABLE") . fst) environment
  -- withCreateProcess stops the service when an assertion exits early, so a
  -- failure cannot leave it running, holding the test's output open.
  withCreateProcess (proc "door" ["serve", "--socket", socketPath])
    { cwd = Just root, env = Just serviceEnvironment, std_out = NoStream, std_err = CreatePipe } $ \_ _ _ service -> do
    started <- waitForSocket 100
    socketStatus <- getFileStatus socketPath
    assert "door serve listens on an owner-only socket"
      (started && fileMode socketStatus `mod` 0o1000 == 0o600) ExitSuccess socketPath ""

    first <- proposalNamed "service-first"
    (_, cliOutput, _) <- readCreateProcessWithExitCode
      ((proc "door" ["admit", "--proposal", first, "--dry-run"]) { cwd = Just root }) ""
    servedDryRun <- request "admit-dry-run" first
    assert "the service answers a dry run exactly as the command line prints it"
      (okResult servedDryRun == either (const Nothing) Just (decodeUnique (LazyByteString.pack cliOutput)))
      ExitSuccess (show servedDryRun <> "\n" <> cliOutput) ""
    servedFirst <- request "admit" first
    firstHead <- gitHead root
    writeFile armed ""
    servedSecond <- request "admit" =<< proposalNamed "service-second"
    secondHead <- gitHead root
    assert "the service commits from memory without re-reading history or the whole store"
      (okResult servedFirst /= Nothing && okResult servedSecond /= Nothing && secondHead /= firstHead)
      ExitSuccess (show servedFirst <> "\n" <> show servedSecond) ""

    outside <- proposalNamed "service-outside"
    (outsideCode, outsideOutput, outsideErrors) <- readCreateProcessWithExitCode
      ((proc "door" ["admit", "--proposal", outside]) { cwd = Just root }) ""
    assert "the command line writes while the service runs" (outsideCode == ExitSuccess)
      outsideCode outsideOutput outsideErrors
    outsideHead <- gitHead root
    servedAfter <- request "admit" =<< proposalNamed "service-after"
    afterHead <- gitHead root
    (_, parentOfAfter, _) <- readProcessWithExitCode "git" ["-C", root, "rev-parse", "HEAD^"] ""
    assert "the service plans against a HEAD it did not make"
      (okResult servedAfter /= Nothing && afterHead /= outsideHead
        && takeWhile (/= '\n') parentOfAfter == outsideHead)
      ExitSuccess (show servedAfter) ""

    unknown <- request "remove-everything" first
    assert "the service refuses an unknown command as a malformed request"
      (errorCode unknown == Just "request_malformed") ExitSuccess (show unknown) ""

    -- Refusal codes (ADR 0058): each fixture differs from an accepted twin
    -- only in what the code names.
    firstId <- allocatedObjectId archiveId "archive.core/entity" "service-first"
    firstRevision <- revisionOf (entityDraft firstId)
    let revisionProposal name expected = writeAdmissionProposal root name (manifestFor
          ["records/entity.json"] [] [(firstId, Just expected)] [])
          ["records/entity.json", replace "\"label\":\"test\"" "\"label\":\"renamed\""
            (replace "\"supersedes\":[]" ("\"supersedes\":[" <> transitionReference "supersedes" firstId firstRevision <> "]")
              (entityDraft firstId))]
    stale <- request "admit-dry-run" =<< revisionProposal "service-stale" zeroDigest
    current <- request "admit-dry-run" =<< revisionProposal "service-current" firstRevision
    assert "a stale expected head answers expected_head_stale, and the current head is accepted (anchor)"
      (errorCode stale == Just "expected_head_stale" && okResult current /= Nothing)
      ExitSuccess (show stale <> "\n" <> show current) ""
    misallocatedId <- allocatedObjectId archiveId "archive.core/entity" "service-misallocated"
    misallocated <- request "admit-dry-run" =<< writeAdmissionProposal root "service-misallocated" (manifestFor
      ["records/entity.json"] [(misallocatedId, "archive.core/entity", "service-other-name")]
      [(misallocatedId, Nothing)] []) ["records/entity.json", entityDraft misallocatedId]
    allocated <- request "admit-dry-run" =<< proposalNamed "service-misallocated"
    assert "a proposal Door refuses answers proposal_refused, and its correctly allocated twin is accepted (anchor)"
      (errorCode misallocated == Just "proposal_refused" && okResult allocated /= Nothing)
      ExitSuccess (show misallocated <> "\n" <> show allocated) ""
    missingDirectory <- request "admit-dry-run" (root <> "-proposal-does-not-exist")
    assert "a proposal Door cannot read answers proposal_malformed"
      (errorCode missingDirectory == Just "proposal_malformed") ExitSuccess (show missingDirectory) ""
    oversized <- rawRequest (ByteString.replicate 70000 32 <> "\n")
    unterminated <- rawRequest "{\"command\":\"admit-dry-run\""
    -- Clients that connect and hang up at once, as a health probe does,
    -- must not stop the service: its answer then has nowhere to go.
    -- One at a time, so the listen backlog never fills.
    mapM_ (\_ -> do
      connection <- Socket.socket Socket.AF_UNIX Socket.Stream Socket.defaultProtocol
      Socket.connect connection (Socket.SockAddrUnix socketPath)
      Socket.close connection
      threadDelay 50000) [1 .. 10 :: Int]
    afterHangups <- request "admit-dry-run" =<< proposalNamed "service-after-hangups"
    assert "clients that hang up do not stop the service"
      (okResult afterHangups /= Nothing) ExitSuccess (show afterHangups) ""
    assert "an oversized or unterminated request line is refused, not parsed"
      (errorCode oversized == Just "request_malformed" && errorCode unterminated == Just "request_malformed"
        && contains "exceeds" (show oversized) && contains "closed before" (show unterminated))
      ExitSuccess (show oversized <> "\n" <> show unterminated) ""
    (secondCode, _, secondErrors) <- readCreateProcessWithExitCode
      ((proc "door" ["serve", "--socket", socketPath]) { cwd = Just root }) ""
    assert "a second service refuses an existing socket path"
      (secondCode /= ExitSuccess && contains "already exists" secondErrors) secondCode "" secondErrors
    let openDirectory = parent </> "service-open"
    createDirectoryIfMissing True openDirectory
    setFileMode openDirectory 0o755
    (openCode, _, openErrors) <- readCreateProcessWithExitCode
      ((proc "door" ["serve", "--socket", openDirectory </> "door.sock"]) { cwd = Just root }) ""
    assert "the service refuses a socket directory open to other users"
      (openCode /= ExitSuccess && contains "open to other users" openErrors) openCode "" openErrors

    terminateProcess service
    _ <- waitForProcess service
    remaining <- doesPathExist socketPath
    assert "a stopped service removes its socket" (not remaining) ExitSuccess socketPath ""

  -- Anchor: a new service with no checkpoint must re-read history, which the
  -- armed wrapper refuses, so the earlier successes did come from memory.
  removeFile (root </> ".git" </> "archive-door-verified.json")
  coldAnswer <- withCreateProcess (proc "door" ["serve", "--socket", socketPath])
    { cwd = Just root, env = Just serviceEnvironment, std_out = NoStream, std_err = CreatePipe } $ \_ _ _ fresh -> do
    _ <- waitForSocket 100
    answer <- request "admit" =<< proposalNamed "service-cold"
    terminateProcess fresh
    _ <- waitForProcess fresh
    pure answer
  assert "without memory or a checkpoint the armed wrapper stops the service's full read (anchor)"
    (okResult coldAnswer == Nothing) ExitSuccess (show coldAnswer) ""

-- | Git can start maintenance after a commit, detached in the background,
-- and repack a new archive outside its lock. Every Git command init runs
-- that could start it must turn it off (ADR 0034).
initMaintenanceChecks :: FilePath -> IO ()
initMaintenanceChecks parent = do
  realGit <- findExecutable "git" >>= maybe
    (ioError (userError "init maintenance fixture cannot find git")) pure
  let wrapperDirectory = parent </> "init-maintenance-git"
      wrapper = wrapperDirectory </> "git"
      logPath = parent </> "init-maintenance-git.log"
  createDirectoryIfMissing True wrapperDirectory
  writeFile wrapper ("#!/bin/sh\nprintf '%s ' \"$@\" >> " <> quote logPath <> "\necho >> " <> quote logPath
    <> "\nexec " <> quote realGit <> " \"$@\"\n")
  setFileMode wrapper 0o755
  (code, output, errors) <- archiveCommandWithPath wrapper ["init", "--operator", "alice", parent </> "archive-init-maintenance"] ""
  assert "init runs through the recording Git wrapper" (code == ExitSuccess) code output errors
  invocations <- lines <$> readFile logPath
  let starts = [ line | line <- invocations, any (`elem` words line) ["commit", "add"] ]
  assert "init runs Git commands that could start maintenance (anchor)" (not (null starts))
    ExitSuccess (unlines invocations) ""
  assert "every init commit and add turns off automatic maintenance"
    (all (\line -> contains "maintenance.auto=false" line && contains "gc.auto=0" line) starts)
    ExitSuccess (unlines starts) ""

persistentAdmissionChecks :: FilePath -> IO ()
persistentAdmissionChecks parent = do
  (root, archiveId, _) <- newProposalArchive parent "persistent-admission"
  realGit <- findExecutable "git" >>= maybe
    (ioError (userError "packed Git baseline fixture cannot find git")) pure
  maintenance <- withArchiveWriteLock root $ \_ -> do
    (status, _, errors) <- readProcessWithExitCode realGit
      ["-C", root, "gc", "--prune=never"] ""
    pure $ if status == ExitSuccess
      then Right ()
      else Left (MutationOperationFailure (Text.pack ("coordinated git gc failed: " <> errors)))
  assert "packed-history fixture can be prepared under the archive lock"
    (maintenance == Right ()) ExitSuccess (show maintenance) ""
  packedNames <- listDirectory (root </> ".git" </> "objects" </> "pack")
  assert "the persistent admission fixture has a packed reachable baseline"
    (any (\name -> length name >= 5 && drop (length name - 5) name == ".pack") packedNames)
    ExitSuccess (show packedNames) ""
  objectId <- allocatedObjectId archiveId "archive.core/entity" "persistent-entity"
  proposal <- writeAdmissionProposal root "persistent-entity" (manifestFor
    ["records/entity.json"]
    [(objectId, "archive.core/entity", "persistent-entity")]
    [(objectId, Nothing)] []) ["records/entity.json", entityDraft objectId]
  parsedAdmission <- readAdmissionProposal proposal
    >>= either (ioError . userError . Text.unpack) pure
  wrongPlanner <- dryRunTransitionForCommit root parsedAdmission
  wrongPlannerText <- dryRunTransition root parsedAdmission
  assert "transition planner preserves a proposal-kind refusal as a typed rejection"
    (wrongPlanner == Left (TransitionProposalRejected
      "transition planning requires a transition proposal")
      && wrongPlannerText == Left "transition planning requires a transition proposal")
    ExitSuccess (show wrongPlanner <> " / " <> show wrongPlannerText) ""
  plannedRevision <- revisionOf (entityDraft objectId)
  beforeHead <- gitHead root
  beforeCommits <- gitCommitCount root
  beforeRecords <- recordCount root
  (code, output, errors, annexCalled) <- persistentAdmissionCommandWithFakeAnnex
    parent "metadata" root proposal
  afterHead <- gitHead root
  afterCommits <- gitCommitCount root
  afterRecords <- recordCount root
  assert "persistent metadata-only admission succeeds with exact output"
    (code == ExitSuccess && null errors && not annexCalled
      && decodeUnique (LazyByteString.pack output) == Right (Aeson.object
        [ "status" .= ("committed" :: Text.Text)
        , "revision_ids" .= [plannedRevision]
        , "retry" .= False
        ]))
    code output errors
  stored <- readStoredRecord root plannedRevision
  assert "persistent metadata-only admission creates one commit"
    (afterHead /= beforeHead && afterCommits == beforeCommits + 1
      && afterRecords == beforeRecords + 1 && canonicalStored stored
      && contains "\"at\":\"" (LazyByteString.unpack stored))
    code output errors

  retryHead <- gitHead root
  retryCommits <- gitCommitCount root
  retryRecords <- recordCount root
  retryBytesBefore <- readStoredRecord root plannedRevision
  (retryCode, retryOutput, retryErrors, retryAnnex) <-
    persistentAdmissionCommandWithFakeAnnex parent "metadata-retry" root proposal
  retryAfterHead <- gitHead root
  retryAfterCommits <- gitCommitCount root
  retryAfterRecords <- recordCount root
  retryBytesAfter <- readStoredRecord root plannedRevision
  assert "persistent metadata-only retry is quiet and idempotent"
    (retryCode == ExitSuccess && null retryErrors && not retryAnnex
      && decodeUnique (LazyByteString.pack retryOutput) == Right (Aeson.object
        [ "status" .= ("committed" :: Text.Text)
        , "revision_ids" .= [plannedRevision]
        , "retry" .= True
        ])
      && retryAfterHead == retryHead
      && retryAfterCommits == retryCommits
      && retryAfterRecords == retryRecords
      && retryBytesAfter == retryBytesBefore)
    retryCode retryOutput retryErrors

  multiOne <- allocatedObjectId archiveId "archive.core/entity" "persistent-multi-one"
  multiTwo <- allocatedObjectId archiveId "archive.core/entity" "persistent-multi-two"
  let multiOneDraft = entityDraft multiOne
      multiTwoDraft = entityDraft multiTwo
  multiOneRevision <- revisionOf multiOneDraft
  multiTwoRevision <- revisionOf multiTwoDraft
  multiProposal <- writeAdmissionProposal root "persistent-multi" (manifestFor
    ["records/one.json", "records/two.json"]
    [(multiOne, "archive.core/entity", "persistent-multi-one")
    , (multiTwo, "archive.core/entity", "persistent-multi-two")]
    [(multiOne, Nothing), (multiTwo, Nothing)] [])
    ["records/one.json", multiOneDraft, "records/two.json", multiTwoDraft]
  failedHead <- gitHead root
  failedCommits <- gitCommitCount root
  failedRecords <- recordCount root
  (failedCode, failedOutput, failedErrors, failedAnnex) <-
    persistentCommandWithFailingCommit "admit" parent root multiProposal
  failedAfterHead <- gitHead root
  failedAfterCommits <- gitCommitCount root
  failedAfterRecords <- recordCount root
  assert "a failed multi-record admission leaves no successful prefix"
    (failedCode /= ExitSuccess && null failedOutput && not (null failedErrors)
      && not failedAnnex && failedAfterHead == failedHead
      && failedAfterCommits == failedCommits
      && failedAfterRecords == failedRecords)
    failedCode failedOutput failedErrors
  multiHead <- gitHead root
  multiCommits <- gitCommitCount root
  multiRecords <- recordCount root
  (multiCode, multiOutput, multiErrors, multiAnnex) <-
    persistentAdmissionCommandWithFakeAnnex parent "multi" root multiProposal
  multiAfterHead <- gitHead root
  multiAfterCommits <- gitCommitCount root
  multiAfterRecords <- recordCount root
  assert "persistent multi-record command succeeds before checking committed paths"
    (multiCode == ExitSuccess && null multiErrors)
    multiCode multiOutput multiErrors
  committedOne <- readCommittedRecord root multiOneRevision
  committedTwo <- readCommittedRecord root multiTwoRevision
  assert "persistent multi-record admission creates exactly one complete commit"
    (multiCode == ExitSuccess && null multiErrors && not multiAnnex
      && decodeUnique (LazyByteString.pack multiOutput) == Right (Aeson.object
        [ "status" .= ("committed" :: Text.Text)
        , "revision_ids" .= [multiOneRevision, multiTwoRevision]
        , "retry" .= False
        ])
      && multiAfterHead /= multiHead && multiAfterCommits == multiCommits + 1
      && multiAfterRecords == multiRecords + 2
      && canonicalStored committedOne && canonicalStored committedTwo)
    multiCode multiOutput multiErrors

  artifact <- allocatedObjectId archiveId "archive.core/artifact" "persistent-artifact"
  artifactRevision <- revisionOf (artifactDraft artifact)
  let artifactContents = [(zeroDigest, 0, "SHA256E-s0--0000000000000000000000000000000000000000000000000000000000000000", "staging")]
  artifactProposal <- writeAdmissionProposal root "persistent-artifact" (manifestFor
    ["records/artifact.json"]
    [(artifact, "archive.core/artifact", "persistent-artifact")]
    [(artifact, Nothing)] artifactContents)
    ["records/artifact.json", artifactDraft artifact]
  contentHead <- gitHead root
  contentCommits <- gitCommitCount root
  contentRecords <- recordCount root
  (contentCode, contentOutput, contentErrors, contentAnnex) <-
    persistentAdmissionCommandWithFakeAnnex parent "content" root artifactProposal
  contentAfterHead <- gitHead root
  contentAfterCommits <- gitCommitCount root
  contentAfterRecords <- recordCount root
  assert "content admission from an unconfigured staging remote refuses before mutation or annex"
    (contentCode /= ExitSuccess && null contentOutput
      && contains "staging remote is not a configured Git remote: staging" contentErrors
      && not contentAnnex && contentAfterHead == contentHead
      && contentAfterCommits == contentCommits && contentAfterRecords == contentRecords)
    contentCode contentOutput contentErrors
  _ <- commitDraftFixture root (artifactDraft artifact)
  (artifactRetryCode, artifactRetryOutput, artifactRetryErrors, artifactRetryAnnex) <-
    persistentAdmissionCommandWithFakeAnnex parent "content-retry" root artifactProposal
  assert "an exact existing artifact retry returns without annex"
    (artifactRetryCode == ExitSuccess && null artifactRetryErrors
      && not artifactRetryAnnex
      && decodeUnique (LazyByteString.pack artifactRetryOutput) == Right (Aeson.object
        [ "status" .= ("committed" :: Text.Text)
        , "revision_ids" .= [artifactRevision]
        , "retry" .= True
        ]))
    artifactRetryCode artifactRetryOutput artifactRetryErrors

  mixedObject <- allocatedObjectId archiveId "archive.core/entity" "persistent-mixed-entity"
  mixedArtifact <- allocatedObjectId archiveId "archive.core/artifact" "persistent-mixed-artifact"
  mixedProposal <- writeAdmissionProposal root "persistent-mixed" (manifestFor
    ["records/entity.json", "records/artifact.json"]
    [(mixedObject, "archive.core/entity", "persistent-mixed-entity")
    , (mixedArtifact, "archive.core/artifact", "persistent-mixed-artifact")]
    [(mixedObject, Nothing), (mixedArtifact, Nothing)] artifactContents)
    ["records/entity.json", entityDraft mixedObject
    , "records/artifact.json", artifactDraft mixedArtifact]
  mixedHead <- gitHead root
  mixedCommits <- gitCommitCount root
  mixedRecords <- recordCount root
  (mixedCode, mixedOutput, mixedErrors, mixedAnnex) <-
    persistentAdmissionCommandWithFakeAnnex parent "mixed" root mixedProposal
  mixedAfterHead <- gitHead root
  mixedAfterCommits <- gitCommitCount root
  mixedAfterRecords <- recordCount root
  assert "mixed metadata and content admission refuses without a prefix"
    (mixedCode /= ExitSuccess && null mixedOutput
      && contains "staging remote is not a configured Git remote: staging" mixedErrors
      && not mixedAnnex && mixedAfterHead == mixedHead
      && mixedAfterCommits == mixedCommits && mixedAfterRecords == mixedRecords)
    mixedCode mixedOutput mixedErrors

-- Content-bearing admission against a real git-annex staging repository,
-- added to the archive as an ordinary Git remote (ADR 0023). Digests come from
-- the system SHA-256 tool, not from the annex key names under test.
contentAdmissionChecks :: FilePath -> IO ()
contentAdmissionChecks parent = do
  realAnnex <- lookupEnv "ARCHIVE_GIT_ANNEX_EXECUTABLE" >>= maybe
    (ioError (userError "content admission tests need ARCHIVE_GIT_ANNEX_EXECUTABLE")) pure
  (root, archiveId, _) <- newProposalArchive parent "content-admission"
  let staging = parent </> "content-staging"
      identity = "GIT_AUTHOR_NAME=staging GIT_AUTHOR_EMAIL=staging@example.invalid "
        <> "GIT_COMMITTER_NAME=staging GIT_COMMITTER_EMAIL=staging@example.invalid "
  shellChecked ("set -e; mkdir " <> quote staging <> "; cd " <> quote staging
    <> "; git init -q; " <> identity <> quote realAnnex <> " init -q staging-host"
    <> "; printf 'rehearsal take\\000\\377\\n' > take.bin; : > empty.bin"
    <> "; printf 'mislabelled digest\\n' > wrong-digest.bin"
    <> "; printf 'mislabelled size\\n' > wrong-size.bin"
    <> "; " <> identity <> quote realAnnex <> " add -q take.bin empty.bin wrong-digest.bin wrong-size.bin"
    <> "; " <> identity <> "git commit -q -m stage")
  shellChecked ("git -C " <> quote root <> " remote add staging " <> quote staging)
  let stagedFile name = do
        key <- trim . (\(_, output, _) -> output) <$> shell
          ("cd " <> quote staging <> " && " <> quote realAnnex <> " lookupkey " <> quote name)
        (_, digestOutput, _) <- shell ("cd " <> quote staging <> " && (sha256sum "
          <> quote name <> " 2>/dev/null || shasum -a 256 " <> quote name <> ") | cut -c1-64")
        (_, sizeOutput, _) <- shell ("wc -c < " <> quote (staging </> name))
        pure (key, "sha256:" <> trim digestOutput, read (trim sizeOutput) :: Integer)
      contentArtifact objectId digest size = envelope objectId "artifact"
        ("{\"content\":{\"digest\":\"" <> digest <> "\",\"size\":" <> show size <> "}}") "[]" ""
      artifactProposal name digest size key remote = do
        objectId <- allocatedObjectId archiveId "archive.core/artifact" name
        let draft = contentArtifact objectId digest size
        proposal <- writeAdmissionProposal root name (manifestFor
          ["records/artifact.json"]
          [(objectId, "archive.core/artifact", name)]
          [(objectId, Nothing)] [(digest, size, key, remote)])
          ["records/artifact.json", draft]
        revision <- revisionOf draft
        pure (proposal, revision)
      annexRef = checkedOutput "content fixture reads the git-annex branch"
        ["-C", root, "rev-parse", "refs/heads/git-annex"]
      annexPresent key = (\(code, _, _) -> code == ExitSuccess) <$> shell
        ("cd " <> quote root <> " && " <> quote realAnnex <> " contentlocation " <> quote key)
      admitWith annex proposal = do
        environment <- getEnvironment
        let process = (proc "door" ["admit", "--proposal", proposal])
              { cwd = Just root
              , env = Just (("ARCHIVE_GIT_ANNEX_EXECUTABLE", annex)
                  : filter ((/= "ARCHIVE_GIT_ANNEX_EXECUTABLE") . fst) environment)
              }
        readCreateProcessWithExitCode process ""

  -- Every admission below runs through this wrapper, which forwards to the
  -- configured git-annex but fails if Door ever asks it to fsck: fsck writes
  -- activity.log on every run and cannot keep INV-5.
  let fsckBomb = parent </> "content-fsck-bomb" </> "git-annex"
      fsckMarker = parent </> "content-fsck-called"
  createDirectoryIfMissing True (takeDirectory fsckBomb)
  writeFile fsckBomb ("#!/bin/sh\nfor argument in \"$@\"; do\n  if [ \"$argument\" = fsck ]; then touch "
    <> quote fsckMarker <> "; exit 97; fi\ndone\nexec " <> quote realAnnex <> " \"$@\"\n")
  bombPermissions <- getPermissions fsckBomb
  setPermissions fsckBomb (setOwnerExecutable True bombPermissions)

  (takeKey, takeDigest, takeSize) <- stagedFile "take.bin"
  (takeProposal, takeRevision) <- artifactProposal "content-take" takeDigest takeSize takeKey "staging"
  let takePath = contentRelative takeDigest
  takePresentBefore <- annexPresent takeKey
  beforeHead <- gitHead root
  beforeCommits <- gitCommitCount root
  (code, output, errors) <- admitWith fsckBomb takeProposal
  afterCommits <- gitCommitCount root
  (_, tree, _) <- shell ("git -C " <> quote root <> " ls-tree HEAD -- " <> quote takePath)
  (_, changed, _) <- shell ("git -C " <> quote root <> " diff --name-only " <> quote beforeHead <> " HEAD")
  (_, status, _) <- shell ("git -C " <> quote root <> " status --porcelain")
  (_, located, _) <- shell ("cd " <> quote root <> " && " <> quote realAnnex <> " lookupkey " <> quote takePath)
  (_, worktreeDigest, _) <- shell ("cd " <> quote root <> " && (sha256sum "
    <> quote takePath <> " 2>/dev/null || shasum -a 256 " <> quote takePath <> ") | cut -c1-64")
  fsckCalled <- doesPathExist fsckMarker
  assert "content admission obtains, verifies, and commits records and content together"
    (code == ExitSuccess && null errors && not takePresentBefore && not fsckCalled
      && decodeUnique (LazyByteString.pack output) == Right (Aeson.object
        [ "status" .= ("committed" :: Text.Text)
        , "revision_ids" .= [takeRevision]
        , "retry" .= False
        ])
      && afterCommits == beforeCommits + 1
      && contains ("120000 blob ") tree
      && sort (lines changed) == sort [takePath, recordRelative takeRevision]
      && not (contains takePath status)
      && trim located == takeKey
      && "sha256:" <> trim worktreeDigest == takeDigest)
    code output (errors <> "\ntree: " <> tree <> "\nchanged: " <> changed <> "\nstatus: " <> status
      <> "\nlookupkey: " <> located <> "\nworktree digest: " <> worktreeDigest
      <> "\npresent before: " <> show takePresentBefore <> "\nfsck called: " <> show fsckCalled)

  -- The INV-5 bomb: an identical retry must not run git-annex at all, so the
  -- configured executable is replaced by one that fails on any invocation.
  retryAnnexRef <- annexRef
  retryHead <- gitHead root
  (retryCode, retryOutput, retryErrors, retryAnnexCalled) <-
    persistentAdmissionCommandWithFakeAnnex parent "content-identical-retry" root takeProposal
  retryAnnexAfter <- annexRef
  retryHeadAfter <- gitHead root
  assert "an identical content retry writes no commit, no git-annex branch entry, and never runs git-annex"
    (retryCode == ExitSuccess && null retryErrors && not retryAnnexCalled
      && decodeUnique (LazyByteString.pack retryOutput) == Right (Aeson.object
        [ "status" .= ("committed" :: Text.Text)
        , "revision_ids" .= [takeRevision]
        , "retry" .= True
        ])
      && retryHeadAfter == retryHead && retryAnnexAfter == retryAnnexRef)
    retryCode retryOutput retryErrors

  (emptyKey, emptyDigest, emptySize) <- stagedFile "empty.bin"
  (emptyProposal, emptyRevision) <- artifactProposal "content-empty" emptyDigest emptySize emptyKey "staging"
  (emptyCode, emptyOutput, emptyErrors) <- admitWith fsckBomb emptyProposal
  (_, emptyTree, _) <- shell ("git -C " <> quote root <> " ls-tree HEAD -- "
    <> quote (contentRelative emptyDigest))
  assert "zero-byte content is admitted like any other content"
    (emptyCode == ExitSuccess && emptySize == 0
      && contains emptyRevision emptyOutput && contains "120000 blob " emptyTree)
    emptyCode emptyOutput emptyErrors

  -- A second artifact citing already admitted bytes shares their location.
  sharedHead <- gitHead root
  (sharedProposal, sharedRevision) <- artifactProposal "content-shared" takeDigest takeSize takeKey "staging"
  (sharedCode, sharedOutput, sharedErrors) <- admitWith fsckBomb sharedProposal
  (_, sharedChanged, _) <- shell ("git -C " <> quote root <> " diff --name-only "
    <> quote sharedHead <> " HEAD")
  assert "an artifact citing admitted content commits only its record"
    (sharedCode == ExitSuccess && contains sharedRevision sharedOutput
      && lines sharedChanged == [recordRelative sharedRevision])
    sharedCode sharedOutput (sharedErrors <> "\nchanged: " <> sharedChanged)

  let refusedWithoutCommit label proposal expectedMessage key = do
        refusedHead <- gitHead root
        refusedCommits <- gitCommitCount root
        presentBefore <- annexPresent key
        (refusedCode, refusedOutput, refusedErrors) <- admitWith fsckBomb proposal
        refusedHeadAfter <- gitHead root
        refusedCommitsAfter <- gitCommitCount root
        presentAfter <- annexPresent key
        (_, refusedStatus, _) <- shell ("git -C " <> quote root <> " status --porcelain")
        assert label
          (refusedCode /= ExitSuccess && null refusedOutput
            && contains expectedMessage refusedErrors
            && not presentBefore && presentAfter
            && contains ("transferred content remains unadmitted storage: " <> key) refusedErrors
            && refusedHeadAfter == refusedHead && refusedCommitsAfter == refusedCommits
            && not (contains "content/" refusedStatus))
          refusedCode refusedOutput refusedErrors

  (wrongDigestKey, _, wrongDigestSize) <- stagedFile "wrong-digest.bin"
  (wrongDigestProposal, _) <- artifactProposal "content-wrong-digest" otherDigest wrongDigestSize wrongDigestKey "staging"
  refusedWithoutCommit
    "a declared digest that differs from the obtained bytes is refused and the content reported"
    wrongDigestProposal "does not match the declared SHA-256 digest" wrongDigestKey

  (wrongSizeKey, wrongSizeDigest, wrongSizeSize) <- stagedFile "wrong-size.bin"
  (wrongSizeProposal, _) <- artifactProposal "content-wrong-size" wrongSizeDigest (wrongSizeSize + 1) wrongSizeKey "staging"
  refusedWithoutCommit
    "a declared size that differs from the obtained bytes is refused and the content reported"
    wrongSizeProposal "does not match the declared byte size" wrongSizeKey

  missingHead <- gitHead root
  (missingProposal, _) <- artifactProposal "content-missing-remote" takeDigest takeSize takeKey "nonexistent-staging"
  (missingCode, missingOutput, missingErrors, missingAnnexCalled) <-
    persistentAdmissionCommandWithFakeAnnex parent "content-missing-remote" root missingProposal
  missingHeadAfter <- gitHead root
  assert "an unconfigured staging remote is refused before git-annex runs"
    (missingCode /= ExitSuccess && null missingOutput && not missingAnnexCalled
      && contains "staging remote is not a configured Git remote: nonexistent-staging" missingErrors
      && missingHeadAfter == missingHead)
    missingCode missingOutput missingErrors

  -- A content-bearing batch killed after its ref update: the next write
  -- recovers the ordinary index like after any other batch.
  shellChecked ("set -e; cd " <> quote staging
    <> "; printf 'interrupted take\\n' > interrupted.bin"
    <> "; " <> identity <> quote realAnnex <> " add -q interrupted.bin"
    <> "; " <> identity <> "git commit -q -m interrupted")
  (interruptedKey, interruptedDigest, interruptedSize) <- stagedFile "interrupted.bin"
  (interruptedProposal, interruptedRevision) <- artifactProposal "content-interrupted"
    interruptedDigest interruptedSize interruptedKey "staging"
  interruptedBefore <- gitHead root
  (interruptedCode, interruptedOut, interruptedErr) <- doorWithGitWrapper parent "content-interrupted" root ["admit", "--proposal", interruptedProposal]
    [("ARCHIVE_GIT_ANNEX_EXECUTABLE", fsckBomb)] (killedAfter RefUpdate)
  (_, interruptedTree, _) <- shell ("git -C " <> quote root <> " ls-tree -r HEAD -- "
    <> quote (contentRelative interruptedDigest) <> " " <> quote (recordRelative interruptedRevision))
  assert "the killed content-bearing batch committed its record and content location"
    (contains "120000 blob " interruptedTree && contains (recordRelative interruptedRevision) interruptedTree)
    interruptedCode interruptedOut (interruptedErr <> "\ntree: " <> interruptedTree)
  killedBatchRecovers "content-bearing batch" "content-interrupted-next" parent root archiveId interruptedBefore

operationJournalKillRecoveryCheck :: FilePath -> IO ()
operationJournalKillRecoveryCheck parent = do
  (root, archiveId, _) <- newProposalArchive parent "journal-kill-recovery"
  objectId <- allocatedObjectId archiveId "archive.core/entity" "journal-kill-recovery"
  let draft = entityDraft objectId
  plannedRevision <- revisionOf draft
  proposal <- writeAdmissionProposal root "journal-kill-recovery" (manifestFor
    ["records/entity.json"]
    [(objectId, "archive.core/entity", "journal-kill-recovery")]
    [(objectId, Nothing)] []) ["records/entity.json", draft]
  realGit <- findExecutable "git" >>= maybe
    (ioError (userError "journal crash fixture cannot find git")) pure
  let fakeBin = parent </> "journal-kill-fake-bin"
      fakeGit = fakeBin </> "git"
      marker = parent </> "journal-kill-after-link"
      record = root </> recordRelative plannedRevision
      journal = root </> ".git" </> "archive-door-operation"
  createDirectoryIfMissing True fakeBin
  writeFile fakeGit
    ("#!/bin/sh\ncase \"$*\" in\n"
      <> "  *'hash-object --no-filters -- /dev/fd/'*)\n"
      <> "    touch " <> quote marker <> "\n"
      <> "    kill -KILL \"$PPID\"\n"
      <> "    sleep 1\n"
      <> "    exit 91\n"
      <> "    ;;\n"
      <> "esac\nexec " <> quote realGit <> " \"$@\"\n")
  permissions <- getPermissions fakeGit
  setPermissions fakeGit (setOwnerExecutable True permissions)
  environment <- getEnvironment
  let clean = ("ARCHIVE_GIT_EXECUTABLE", fakeGit)
        : filter ((/= "ARCHIVE_GIT_EXECUTABLE") . fst) environment
      process = (proc "door" ["admit", "--proposal", proposal])
        { cwd = Just root, env = Just clean }
  headBefore <- gitHead root
  commitsBefore <- gitCommitCount root
  recordsBefore <- recordCount root
  _ <- readCreateProcessWithExitCode process ""
  killedAfterLink <- doesPathExist marker
  partialRecord <- doesPathExist record
  journalPresent <- doesPathExist journal
  headAfterKill <- gitHead root
  commitsAfterKill <- gitCommitCount root
  assert "a forced process kill after link leaves a journaled, uncommitted record"
    (killedAfterLink && partialRecord && journalPresent
      && headAfterKill == headBefore && commitsAfterKill == commitsBefore)
    ExitSuccess (show (killedAfterLink, partialRecord, journalPresent, headAfterKill, commitsAfterKill)) ""
  let preserved = record <> ".owned-inode"
  renameFile record preserved
  writeFile record "foreign operator data"
  (mismatchCode, mismatchOutput, mismatchErrors, _) <-
    persistentAdmissionCommandWithFakeAnnex parent "journal-kill-mismatch" root proposal
  foreignBytes <- readFile record
  headAfterMismatch <- gitHead root
  journalAfterMismatch <- doesPathExist journal
  assert "recovery refuses a replacement record inode and preserves its bytes"
    (mismatchCode /= ExitSuccess && null mismatchOutput
      && contains "device/inode" mismatchErrors
      && foreignBytes == "foreign operator data"
      && headAfterMismatch == headBefore && journalAfterMismatch)
    mismatchCode mismatchOutput mismatchErrors
  removeFile record
  renameFile preserved record
  (retryCode, retryOutput, retryErrors, retryAnnex) <-
    persistentAdmissionCommandWithFakeAnnex parent "journal-kill-retry" root proposal
  headAfterRetry <- gitHead root
  commitsAfterRetry <- gitCommitCount root
  recordsAfterRetry <- recordCount root
  journalAfterRetry <- doesPathExist journal
  assert "the next mutation reconciles a killed pre-ref install and commits once"
    (retryCode == ExitSuccess && null retryErrors && not retryAnnex
      && decodeUnique (LazyByteString.pack retryOutput) == Right (Aeson.object
        [ "status" .= ("committed" :: Text.Text)
        , "revision_ids" .= [plannedRevision]
        , "retry" .= False
        ])
      && headAfterRetry /= headBefore && commitsAfterRetry == commitsBefore + 1
      && recordsAfterRetry == recordsBefore + 1 && not journalAfterRetry)
    retryCode retryOutput retryErrors

-- | A Git step Door takes outside its temporary index: advancing the branch,
-- refreshing the ordinary index, or comparing the ordinary index with a
-- commit. A Door-run Git child without GIT_INDEX_FILE uses the ordinary index.
-- git-annex runs the configured Git too, and updates its own branch; only
-- Door's branch update passes --create-reflog.
data GitStep = RefUpdate | IndexRefresh | IndexComparison

gitStepTest :: GitStep -> String
gitStepTest RefUpdate = "[ \"$argument\" = --create-reflog ]"
gitStepTest IndexRefresh = "[ \"$argument\" = read-tree ] && [ -z \"$GIT_INDEX_FILE\" ]"
gitStepTest IndexComparison = "[ \"$argument\" = --cached ] && [ -z \"$GIT_INDEX_FILE\" ]"

-- | A Git wrapper that kills its parent Door once the step has succeeded.
killedAfter :: GitStep -> FilePath -> String
killedAfter step realGit =
  "#!/bin/sh\nhit=0\nfor argument in \"$@\"; do\n"
    <> "  if " <> gitStepTest step <> "; then hit=1; fi\n"
    <> "done\n" <> quote realGit <> " \"$@\"\nstatus=$?\n"
    <> "if [ \"$hit\" = 1 ] && [ \"$status\" = 0 ]; then\n"
    <> "  kill -KILL \"$PPID\"\n  sleep 1\nfi\nexit \"$status\"\n"

-- | A Git wrapper under which the step fails and everything else runs.
failingAt :: GitStep -> FilePath -> String
failingAt step realGit =
  "#!/bin/sh\nfor argument in \"$@\"; do\n"
    <> "  if " <> gitStepTest step <> "; then exit 3; fi\n"
    <> "done\nexec " <> quote realGit <> " \"$@\"\n"

-- | A Git wrapper under which the step exits 0 but floods stderr past Door's
-- capture bound, so Door gets no exit status for it: the comparison's
-- process-failure branch, distinct from a nonzero exit.
floodingAt :: GitStep -> FilePath -> String
floodingAt step realGit =
  "#!/bin/sh\nfor argument in \"$@\"; do\n"
    <> "  if " <> gitStepTest step <> "; then head -c 4194304 /dev/zero | tr '\\000' x >&2; exit 0; fi\n"
    <> "done\nexec " <> quote realGit <> " \"$@\"\n"

-- | Run the real Door with one of the wrappers above as its Git executable.
doorWithGitWrapper :: FilePath -> String -> FilePath -> [String] -> [(String, String)]
                   -> (FilePath -> String) -> IO (ExitCode, String, String)
doorWithGitWrapper parent suffix root arguments extra script = do
  realGit <- findExecutable "git" >>= maybe
    (ioError (userError "Git wrapper fixture cannot find git")) pure
  let fakeBin = parent </> ("git-wrapper-" <> suffix)
      fakeGit = fakeBin </> "git"
  createDirectoryIfMissing True fakeBin
  writeFile fakeGit (script realGit)
  setFileMode fakeGit 0o755
  environment <- getEnvironment
  let overrides = ("ARCHIVE_GIT_EXECUTABLE", fakeGit) : extra
      kept = filter ((`notElem` map fst overrides) . fst) environment
  readCreateProcessWithExitCode ((proc "door" arguments)
    { cwd = Just root, env = Just (overrides <> kept) }) ""

-- | Whether the ordinary index holds exactly a commit's tree.
indexMatches :: FilePath -> String -> IO Bool
indexMatches root commit = do
  (code, _, _) <- readProcessWithExitCode "git"
    ["-C", root, "diff", "--cached", "--quiet", trim commit, "--"] ""
  pure (code == ExitSuccess)

-- | A metadata admission of one new entity: an ordinary later write, with a
-- git-annex that fails if it is ever run.
admitFreshEntity :: FilePath -> FilePath -> String -> String -> IO (ExitCode, String, String, Bool)
admitFreshEntity parent root archiveId name = do
  (proposal, _) <- freshEntityProposal root archiveId name
  persistentAdmissionCommandWithFakeAnnex parent name root proposal

freshEntityProposal :: FilePath -> String -> String -> IO (FilePath, String)
freshEntityProposal root archiveId name = do
  objectId <- allocatedObjectId archiveId "archive.core/entity" name
  proposal <- writeAdmissionProposal root name (manifestFor
    ["records/entity.json"]
    [(objectId, "archive.core/entity", name)]
    [(objectId, Nothing)] []) ["records/entity.json", entityDraft objectId]
  pure (proposal, objectId)

-- | What a Door killed right after its ref update leaves (the anchor: the
-- branch moved, the journal remains, the ordinary index still holds the old
-- commit's tree and not the new one), then the next ordinary write: it must
-- recover the index to the committed batch and commit on top of it.
killedBatchRecovers :: String -> String -> FilePath -> FilePath -> String -> String -> IO ()
killedBatchRecovers label name parent root archiveId headBefore = do
  let journal = root </> ".git" </> "archive-door-operation"
  headAfterKill <- gitHead root
  journalAfterKill <- doesPathExist journal
  staleAtOld <- indexMatches root headBefore
  staleAtHead <- indexMatches root headAfterKill
  assert (label <> ": a Door killed after its ref update leaves the commit, its journal, and the old index")
    (trim headAfterKill /= trim headBefore && journalAfterKill && staleAtOld && not staleAtHead)
    ExitSuccess (show (headBefore, headAfterKill, journalAfterKill, staleAtOld, staleAtHead)) ""
  (code, output, errors, annexCalled) <- admitFreshEntity parent root archiveId name
  headAfter <- gitHead root
  parentOfHead <- checkedOutput "index recovery reads the new commit's parent"
    ["-C", root, "rev-parse", "HEAD^"]
  fresh <- indexMatches root headAfter
  journalAfter <- doesPathExist journal
  assert (label <> ": the next write recovers the index to the committed batch and commits on top of it")
    (code == ExitSuccess && null errors && not annexCalled && contains "\"retry\":false" output
      && trim parentOfHead == trim headAfterKill && fresh && not journalAfter)
    code output errors

-- | Door builds every batch in a temporary index and refreshes the ordinary
-- index only after the ref update, so a Door killed in between leaves an
-- index at the old tree. The journal says Door made it stale; the next
-- Door's recovery refreshes it, and nothing else.
indexRecoveryRecordBatchChecks :: FilePath -> IO ()
indexRecoveryRecordBatchChecks parent = do
  (root, archiveId, _) <- newProposalArchive parent "index-recovery-records"
  let journal = root </> ".git" </> "archive-door-operation"
      admitKilled step name = do
        (proposal, _) <- freshEntityProposal root archiveId name
        doorWithGitWrapper parent name root ["admit", "--proposal", proposal] [] (killedAfter step)
  killedBefore <- gitHead root
  _ <- admitKilled RefUpdate "index-recovery-killed"
  killedBatchRecovers "record batch" "index-recovery-next" parent root archiveId killedBefore

  refreshedBefore <- gitHead root
  _ <- admitKilled IndexRefresh "index-recovery-refreshed"
  refreshedHead <- gitHead root
  refreshedJournal <- doesPathExist journal
  refreshedIndex <- indexMatches root refreshedHead
  assert "a Door killed after its index refresh leaves its journal and an index already at the commit"
    (trim refreshedHead /= trim refreshedBefore && refreshedJournal && refreshedIndex)
    ExitSuccess (show (refreshedHead, refreshedJournal, refreshedIndex)) ""
  (code, output, errors, _) <- admitFreshEntity parent root archiveId "index-recovery-after-refresh"
  afterRefreshHead <- gitHead root
  afterRefreshParent <- checkedOutput "index recovery reads the parent after a refreshed kill"
    ["-C", root, "rev-parse", "HEAD^"]
  afterRefreshIndex <- indexMatches root afterRefreshHead
  afterRefreshJournal <- doesPathExist journal
  assert "the next write clears that journal and commits on the refreshed index"
    (code == ExitSuccess && null errors && trim afterRefreshParent == trim refreshedHead
      && afterRefreshIndex && not afterRefreshJournal)
    code output errors

  -- A change staged by hand after the kill is not Door's: recovery leaves
  -- the index alone and the staged-changes check still refuses the write.
  foreignBefore <- gitHead root
  _ <- admitKilled RefUpdate "index-recovery-foreign"
  foreignHead <- gitHead root
  writeFile (root </> "operator-note.txt") "staged by hand\n"
  shellChecked ("git -C " <> quote root <> " add operator-note.txt")
  (foreignCode, foreignOutput, foreignErrors, _) <-
    admitFreshEntity parent root archiveId "index-recovery-refused"
  foreignHeadAfter <- gitHead root
  staged <- checkedOutput "index recovery lists the staged paths"
    ["-C", root, "diff", "--cached", "--name-only", "HEAD", "--"]
  foreignAtHead <- indexMatches root foreignHead
  foreignAtOld <- indexMatches root foreignBefore
  assert "an index holding a change Door did not make is still refused after recovery, and kept"
    (foreignCode /= ExitSuccess && null foreignOutput
      && contains "archive index has staged changes" foreignErrors
      && trim foreignHeadAfter == trim foreignHead && trim foreignHead /= trim foreignBefore
      && "operator-note.txt" `elem` lines staged && not foreignAtHead && not foreignAtOld)
    foreignCode foreignOutput (foreignErrors <> "\nstaged: " <> staged)

-- | The branches where recovery does not refresh: a comparison or refresh
-- that fails keeps the journal and the index for the next Door, and a
-- branch moved past the journaled commit is not Door's state to repair.
indexRecoveryRefusalChecks :: FilePath -> IO ()
indexRecoveryRefusalChecks parent = do
  (root, archiveId, _) <- newProposalArchive parent "index-recovery-refusals"
  let journal = root </> ".git" </> "archive-door-operation"
      admitUnder name script = do
        (proposal, _) <- freshEntityProposal root archiveId name
        doorWithGitWrapper parent name root ["admit", "--proposal", proposal] [] script
  headBefore <- gitHead root
  _ <- admitUnder "index-refusal-killed" (killedAfter RefUpdate)
  headAfterKill <- gitHead root
  let refusedKeepingJournal label expected (code, output, errors) = do
        headNow <- gitHead root
        journalNow <- doesPathExist journal
        atOld <- indexMatches root headBefore
        assert label
          (code /= ExitSuccess && null output && contains expected errors
            && trim headNow == trim headAfterKill && trim headAfterKill /= trim headBefore
            && journalNow && atOld)
          code output errors
  admitUnder "index-refusal-compare" (failingAt IndexComparison) >>= refusedKeepingJournal
    "a recovery that cannot compare the index refuses and keeps its journal and the index"
    "could not compare the archive index with the journaled old commit"
  admitUnder "index-refusal-compare-process" (floodingAt IndexComparison) >>= refusedKeepingJournal
    "a recovery whose index comparison process fails without an exit status refuses and keeps its journal and the index"
    "could not compare the archive index with the journaled old commit; operation journal retained: subprocess stderr exceeded its byte limit"
  admitUnder "index-refusal-refresh" (failingAt IndexRefresh) >>= refusedKeepingJournal
    "a recovery whose index refresh fails refuses and keeps its journal and the index"
    "could not refresh the archive index to the committed batch"
  killedBatchRecovers "after refused recoveries" "index-refusal-next" parent root archiveId headBefore

  movedBefore <- gitHead root
  _ <- admitUnder "index-moved-killed" (killedAfter RefUpdate)
  movedTarget <- gitHead root
  bypass <- checkedOutput "index recovery writes a bypass commit"
    ["-C", root, "-c", "user.name=archive-test", "-c", "user.email=archive-test@example.invalid"
    , "commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", "bypass"]
  shellChecked ("git -C " <> quote root <> " update-ref HEAD " <> trim bypass)
  (movedCode, movedOutput, movedErrors, _) <- admitFreshEntity parent root archiveId "index-moved-next"
  movedHead <- gitHead root
  movedAtOld <- indexMatches root movedBefore
  movedJournal <- doesPathExist journal
  assert "a branch moved past the journaled commit is not refreshed, and the stale index is refused"
    (trim movedTarget /= trim movedBefore && trim bypass /= trim movedTarget
      && movedCode /= ExitSuccess && null movedOutput
      && contains "archive index has staged changes" movedErrors
      && trim movedHead == trim bypass && movedAtOld && not movedJournal)
    movedCode movedOutput movedErrors

-- | A transition goes through the same journal as an admission.
indexRecoveryTransitionCheck :: FilePath -> IO ()
indexRecoveryTransitionCheck parent = do
  (root, archiveId, initOutput) <- newProposalArchive parent "index-recovery-transition"
  operatorId <- requiredField "operator_id" initOutput
  operatorRevision <- recordRevisionForObject root operatorId
  target <- allocatedObjectId archiveId "archive.core/entity" "index-transition-target"
  targetRevision <- commitEntityFixture root target
  transitionObject <- allocatedObjectId archiveId
    "archive.transition/retire" "index-transition-retire"
  retireContract <- contractCitation root "archive.transition/retire"
  proposal <- writeTransitionProposal root "index-transition"
    (transitionManifestFor
      ["records/retire.json"]
      [(transitionObject, "archive.transition/retire", "index-transition-retire")]
      [(transitionObject, Nothing), (operatorId, Just operatorRevision)
      , (target, Just targetRevision)])
    ["records/retire.json", retireDraft transitionObject operatorId operatorRevision
      target targetRevision retireContract]
  headBefore <- gitHead root
  _ <- doorWithGitWrapper parent "index-transition-killed" root
    ["transition", "--proposal", proposal] [] (killedAfter RefUpdate)
  killedBatchRecovers "transition" "index-transition-next" parent root archiveId headBefore

operationJournalSizeCheck :: FilePath -> IO ()
operationJournalSizeCheck parent = do
  (root, archiveId, _) <- newProposalArchive parent "journal-size-limit"
  objectId <- allocatedObjectId archiveId "archive.core/entity" "journal-size-limit"
  let draft = entityDraft objectId
      proposalManifest = manifestFor ["records/entity.json"]
        [(objectId, "archive.core/entity", "journal-size-limit")]
        [(objectId, Nothing)] []
      journal = root </> ".git" </> "archive-door-operation"
  proposal <- writeAdmissionProposal root "journal-size-limit" proposalManifest
    ["records/entity.json", draft]
  headBefore <- gitHead root
  let oversized = replicate (16 * 1024 * 1024 + 1) 'x'
  writeFile journal oversized
  (code, output, errors, _) <- persistentAdmissionCommandWithFakeAnnex
    parent "journal-size-refusal" root proposal
  journalAfter <- readFile journal
  headAfter <- gitHead root
  assert "oversized Door journal is refused before mutation and preserved"
    (code /= ExitSuccess && null output
      && contains "exceeds the size limit" errors
      && journalAfter == oversized && headAfter == headBefore)
    code output errors

newProposalArchive :: FilePath -> String -> IO (FilePath, String, String)
newProposalArchive parent suffix = do
  let root = parent </> ("archive-" <> suffix)
  (code, output, errors) <- archiveCommand ["init", "--operator", "alice", root] ""
  assert ("proposal fixture archive initializes: " <> suffix)
    (code == ExitSuccess) code output errors
  archiveId <- requiredField "archive_id" output
  pure (root, archiveId, output)

persistentRecordParentSymlinkCheck :: FilePath -> IO ()
persistentRecordParentSymlinkCheck parent = do
  (root, archiveId, _) <- newProposalArchive parent "record-parent-symlink"
  objectId <- allocatedObjectId archiveId "archive.core/entity" "symlink-parent-record"
  proposal <- writeAdmissionProposal root "parent-symlink" (manifestFor
    ["records/entity.json"]
    [(objectId, "archive.core/entity", "symlink-parent-record")]
    [(objectId, Nothing)] []) ["records/entity.json", entityDraft objectId]
  let outside = parent </> "outside-records"
  createDirectory outside
  renameDirectory (root </> "records") (root </> ".records-original")
  createDirectoryLink outside (root </> "records")
  (code, output, errors, annexCalled) <- persistentAdmissionCommandWithFakeAnnex
    parent "parent-symlink" root proposal
  outsideEntries <- listDirectory outside
  assert "persistent admission refuses a symlinked records parent without writing outside"
    (code /= ExitSuccess && null output && not (null errors) && not annexCalled
      && null outsideEntries)
    code output errors

persistentRecordParentSwapCheck :: FilePath -> IO ()
persistentRecordParentSwapCheck parent = do
  (root, archiveId, _) <- newProposalArchive parent "record-parent-swap"
  objectId <- allocatedObjectId archiveId "archive.core/entity" "swapped-record-parent"
  proposal <- writeAdmissionProposal root "parent-swap" (manifestFor
    ["records/entity.json"]
    [(objectId, "archive.core/entity", "swapped-record-parent")]
    [(objectId, Nothing)] []) ["records/entity.json", entityDraft objectId]
  revision <- revisionOf (entityDraft objectId)
  realGit <- findExecutable "git" >>= maybe
    (ioError (userError "persistent fixture cannot find git")) pure
  let fakeBin = parent </> "persistent-record-parent-swap-bin"
      fakeGit = fakeBin </> "git"
      marker = parent </> "persistent-record-parent-swap-marker"
      outside = parent </> "outside-record-parent-swap"
      originalRecords = root </> ".records-original"
      installedRecord = originalRecords </> drop (length ("records/" :: String)) (recordRelative revision)
  createDirectoryIfMissing True fakeBin
  createDirectory outside
  initialRecords <- listDirectory (root </> "records")
  beforeHead <- gitHead root
  writeFile fakeGit
    ("#!/bin/sh\nfor argument in \"$@\"; do\n"
      <> "  if [ \"$argument\" = update-index ] && [ ! -e " <> quote marker <> " ]; then\n"
      <> "    mv " <> quote (root </> "records") <> " " <> quote originalRecords <> "\n"
      <> "    ln -s " <> quote outside <> " " <> quote (root </> "records") <> "\n"
      <> "    touch " <> quote marker <> "\n"
      <> "  fi\ndone\nexec " <> quote realGit <> " \"$@\"\n")
  permissions <- getPermissions fakeGit
  setPermissions fakeGit (setOwnerExecutable True permissions)
  environment <- getEnvironment
  let clean = ("ARCHIVE_GIT_EXECUTABLE", fakeGit)
        : filter ((/= "ARCHIVE_GIT_EXECUTABLE") . fst) environment
      process = (proc "door" ["admit", "--proposal", proposal])
        { cwd = Just root
        , env = Just clean
        }
  (code, output, errors) <- readCreateProcessWithExitCode process ""
  swapped <- doesPathExist marker
  outsideEntries <- listDirectory outside
  leaked <- doesPathExist installedRecord
  afterHead <- gitHead root
  originalEntries <- listDirectory originalRecords
  assert "a records parent swap at update-index refuses and cleanup stays on the pinned directory"
    (code /= ExitSuccess && null output && not (null errors)
      && swapped && null outsideEntries && not leaked
      && afterHead == beforeHead
      && length originalEntries == length initialRecords)
    code output errors

persistentRecordCleanupOwnershipCheck :: FilePath -> IO ()
persistentRecordCleanupOwnershipCheck parent = do
  (root, archiveId, _) <- newProposalArchive parent "record-cleanup-ownership"
  objectId <- allocatedObjectId archiveId "archive.core/entity" "cleanup-owned-record"
  draft <- pure (entityDraft objectId)
  revision <- revisionOf draft
  proposal <- writeAdmissionProposal root "cleanup-ownership" (manifestFor
    ["records/entity.json"]
    [(objectId, "archive.core/entity", "cleanup-owned-record")]
    [(objectId, Nothing)] []) ["records/entity.json", draft]
  realGit <- findExecutable "git" >>= maybe
    (ioError (userError "persistent fixture cannot find git")) pure
  let fakeBin = parent </> "persistent-record-cleanup-ownership-bin"
      fakeGit = fakeBin </> "git"
      target = root </> recordRelative revision
      marker = parent </> "persistent-record-cleanup-ownership-marker"
      foreignBytes = "foreign record installed during commit failure\n"
  createDirectoryIfMissing True fakeBin
  writeFile fakeGit
    ("#!/bin/sh\nfor argument in \"$@\"; do\n"
      <> "  if [ \"$argument\" = commit-tree ] && [ ! -e " <> quote marker <> " ]; then\n"
      <> "    rm -f " <> quote target <> "\n"
      <> "    printf '%s' " <> quote foreignBytes <> " > " <> quote target <> "\n"
      <> "    touch " <> quote marker <> "\n"
      <> "    exit 88\n"
      <> "  fi\ndone\nexec " <> quote realGit <> " \"$@\"\n")
  permissions <- getPermissions fakeGit
  setPermissions fakeGit (setOwnerExecutable True permissions)
  environment <- getEnvironment
  let clean = ("ARCHIVE_GIT_EXECUTABLE", fakeGit)
        : filter ((/= "ARCHIVE_GIT_EXECUTABLE") . fst) environment
      process = (proc "door" ["admit", "--proposal", proposal])
        { cwd = Just root
        , env = Just clean
        }
  beforeHead <- gitHead root
  (code, output, errors) <- readCreateProcessWithExitCode process ""
  afterHead <- gitHead root
  swapped <- doesPathExist marker
  targetPresent <- doesFileExist target
  retained <- if targetPresent then readFile target else pure ""
  assert "cleanup refuses a swapped record inode and preserves the foreign target"
    (code /= ExitSuccess && null output && contains "cleanup also failed" errors
      && contains "refusing to remove record target with changed device/inode" errors
      && swapped && targetPresent && retained == foreignBytes && afterHead == beforeHead)
    code output errors

persistentRecordCleanupFifoCheck :: FilePath -> IO ()
persistentRecordCleanupFifoCheck parent = do
  (root, archiveId, _) <- newProposalArchive parent "record-cleanup-fifo"
  objectId <- allocatedObjectId archiveId "archive.core/entity" "cleanup-fifo-record"
  draft <- pure (entityDraft objectId)
  revision <- revisionOf draft
  proposal <- writeAdmissionProposal root "cleanup-fifo" (manifestFor
    ["records/entity.json"]
    [(objectId, "archive.core/entity", "cleanup-fifo-record")]
    [(objectId, Nothing)] []) ["records/entity.json", draft]
  realGit <- findExecutable "git" >>= maybe
    (ioError (userError "persistent fixture cannot find git")) pure
  let fakeBin = parent </> "persistent-record-cleanup-fifo-bin"
      fakeGit = fakeBin </> "git"
      target = root </> recordRelative revision
      marker = parent </> "persistent-record-cleanup-fifo-marker"
  createDirectoryIfMissing True fakeBin
  writeFile fakeGit
    ("#!/bin/sh\nfor argument in \"$@\"; do\n"
      <> "  if [ \"$argument\" = commit-tree ] && [ ! -e " <> quote marker <> " ]; then\n"
      <> "    rm -f " <> quote target <> "\n"
      <> "    mkfifo " <> quote target <> "\n"
      <> "    touch " <> quote marker <> "\n"
      <> "    exit 88\n"
      <> "  fi\ndone\nexec " <> quote realGit <> " \"$@\"\n")
  permissions <- getPermissions fakeGit
  setPermissions fakeGit (setOwnerExecutable True permissions)
  environment <- getEnvironment
  let clean = ("ARCHIVE_GIT_EXECUTABLE", fakeGit)
        : filter ((/= "ARCHIVE_GIT_EXECUTABLE") . fst) environment
      process = (proc "door" ["admit", "--proposal", proposal])
        { cwd = Just root
        , env = Just clean
        }
  (code, output, errors) <- readCreateProcessWithExitCode process ""
  markerPresent <- doesPathExist marker
  targetStatus <- getFileStatus target
  assert "cleanup refuses a FIFO target without blocking or removing it"
    (code /= ExitSuccess && null output
      && contains "refusing to remove non-regular record target" errors
      && markerPresent && isNamedPipe targetStatus)
    code output errors

persistentGitDirectorySwapCheck :: FilePath -> IO ()
persistentGitDirectorySwapCheck parent = do
  (root, archiveId, _) <- newProposalArchive parent "git-directory-swap"
  objectId <- allocatedObjectId archiveId "archive.core/entity" "swapped-git-directory"
  proposal <- writeAdmissionProposal root "git-directory-swap" (manifestFor
    ["records/entity.json"]
    [(objectId, "archive.core/entity", "swapped-git-directory")]
    [(objectId, Nothing)] []) ["records/entity.json", entityDraft objectId]
  revision <- revisionOf (entityDraft objectId)
  realGit <- findExecutable "git" >>= maybe
    (ioError (userError "persistent fixture cannot find git")) pure
  let fakeBin = parent </> "persistent-git-directory-swap-bin"
      fakeGit = fakeBin </> "git"
      marker = parent </> "persistent-git-directory-swap-marker"
      originalGit = root </> ".git-pinned"
      decoyGit = root </> ".git-decoy"
      decoyMoved = root </> ".git-decoy-moved"
      installedRecord = root </> recordRelative revision
  createDirectoryIfMissing True fakeBin
  createDirectory decoyGit
  beforeHead <- gitHead root
  writeFile fakeGit
    ("#!/bin/sh\nfor argument in \"$@\"; do\n"
      <> "  if [ \"$argument\" = update-index ] && [ ! -e " <> quote marker <> " ]; then\n"
      <> "    mv " <> quote (root </> ".git") <> " " <> quote originalGit <> "\n"
      <> "    mv " <> quote decoyGit <> " " <> quote (root </> ".git") <> "\n"
      <> "    touch " <> quote marker <> "\n"
      <> "  fi\ndone\nexec " <> quote realGit <> " \"$@\"\n")
  permissions <- getPermissions fakeGit
  setPermissions fakeGit (setOwnerExecutable True permissions)
  environment <- getEnvironment
  let clean = ("ARCHIVE_GIT_EXECUTABLE", fakeGit)
        : filter ((/= "ARCHIVE_GIT_EXECUTABLE") . fst) environment
      process = (proc "door" ["admit", "--proposal", proposal])
        { cwd = Just root
        , env = Just clean
        }
  (code, output, errors) <- readCreateProcessWithExitCode process ""
  swapped <- doesPathExist marker
  if swapped
    then do
      renameDirectory (root </> ".git") decoyMoved
      renameDirectory originalGit (root </> ".git")
    else pure ()
  decoyEntries <- if swapped then listDirectory decoyMoved else pure ["not-swapped"]
  afterHead <- gitHead root
  recordPresent <- doesFileExist installedRecord
  assert "a .git pathname swap cannot redirect a pinned Git child into the replacement directory"
    (code /= ExitSuccess && null output && not (null errors)
      && swapped && null decoyEntries && afterHead == beforeHead && recordPresent)
    code output errors

persistentArchiveRootSwapCheck :: FilePath -> IO ()
persistentArchiveRootSwapCheck parent = do
  (root, archiveId, _) <- newProposalArchive parent "archive-root-swap"
  objectId <- allocatedObjectId archiveId "archive.core/entity" "swapped-archive-root"
  proposal <- writeAdmissionProposal root "archive-root-swap" (manifestFor
    ["records/entity.json"]
    [(objectId, "archive.core/entity", "swapped-archive-root")]
    [(objectId, Nothing)] []) ["records/entity.json", entityDraft objectId]
  revision <- revisionOf (entityDraft objectId)
  realGit <- findExecutable "git" >>= maybe
    (ioError (userError "persistent fixture cannot find git")) pure
  let fakeBin = parent </> "persistent-archive-root-swap-bin"
      fakeGit = fakeBin </> "git"
      marker = parent </> "persistent-archive-root-swap-marker"
      pinnedRoot = root <> "-pinned"
  createDirectoryIfMissing True fakeBin
  beforeHead <- gitHead root
  writeFile fakeGit
    ("#!/bin/sh\nfor argument in \"$@\"; do\n"
      <> "  if [ \"$argument\" = update-index ] && [ ! -e " <> quote marker <> " ]; then\n"
      <> "    mv " <> quote root <> " " <> quote pinnedRoot <> "\n"
      <> "    mkdir " <> quote root <> "\n"
      <> "    touch " <> quote marker <> "\n"
      <> "  fi\ndone\nexec " <> quote realGit <> " \"$@\"\n")
  permissions <- getPermissions fakeGit
  setPermissions fakeGit (setOwnerExecutable True permissions)
  environment <- getEnvironment
  let clean = ("ARCHIVE_GIT_EXECUTABLE", fakeGit)
        : filter ((/= "ARCHIVE_GIT_EXECUTABLE") . fst) environment
      process = (proc "door" ["admit", "--proposal", proposal])
        { cwd = Just root
        , env = Just clean
        }
  (code, output, errors) <- readCreateProcessWithExitCode process ""
  swapped <- doesPathExist marker
  if swapped
    then do
      removePathForcibly root
      renameDirectory pinnedRoot root
    else pure ()
  afterHead <- gitHead root
  recordPresent <- doesFileExist (root </> recordRelative revision)
  assert "archive root pathname replacement is detected while the child remains pinned to the opened root"
    (code /= ExitSuccess && null output && not (null errors)
      && swapped && afterHead == beforeHead && recordPresent)
    code output errors

pinnedGitPermissionCheck :: FilePath -> IO ()
pinnedGitPermissionCheck parent = do
  let root = parent </> "pinned-git-permission"
      gitDirectory = root </> ".git"
  createDirectory root
  createDirectory gitDirectory
  userId <- getEffectiveUserID
  if userId == 0
    then assert "pinned Git directory permission fixture requires an unprivileged user"
      False ExitSuccess "effective uid is root" ""
    else do
      setFileMode gitDirectory 0o500
      entered <- newEmptyMVar
      result <- withArchiveWriteLock root $ \_ -> do
        putMVar entered ()
        pure (Right ())
      setFileMode gitDirectory 0o700
      actionEntered <- tryReadMVar entered
      assert "a non-writable pinned Git directory refuses lock creation before the action"
        (case result of Left _ -> actionEntered == Nothing; Right _ -> False)
        ExitSuccess "" ""

writeAdmissionProposal :: FilePath -> String -> String
                    -> [String] -> IO FilePath
writeAdmissionProposal root name manifest recordPairs = do
  let proposal = root <> "-proposal-" <> name
      recordsRoot = proposal </> "records"
  createDirectoryIfMissing True recordsRoot
  writeFile (proposal </> "manifest.json") manifest
  writeRecordPairs recordsRoot recordPairs
  pure proposal
  where
    writeRecordPairs _ [] = pure ()
    writeRecordPairs recordsRoot (path : value : rest) = do
      let filename = recordsRoot </> drop 8 path
      createDirectoryIfMissing True (takeDirectory filename)
      writeFile filename value
      writeRecordPairs recordsRoot rest
    writeRecordPairs _ _ = assert "proposal fixture has path/value pairs" False ExitSuccess "" ""

writeRawProposal :: FilePath -> String -> String -> IO FilePath
writeRawProposal root name manifest = do
  let proposal = root <> "-proposal-" <> name
  createDirectoryIfMissing True (proposal </> "records")
  writeFile (proposal </> "manifest.json") manifest
  pure proposal

writeTransitionProposal :: FilePath -> String -> String -> [String] -> IO FilePath
writeTransitionProposal root name manifest recordPairs = do
  let proposal = root <> "-transition-" <> name
      recordsRoot = proposal </> "records"
  createDirectoryIfMissing True recordsRoot
  writeFile (proposal </> "manifest.json") manifest
  writeRecordPairs recordsRoot recordPairs
  pure proposal
  where
    writeRecordPairs _ [] = pure ()
    writeRecordPairs recordsRoot (path : value : rest) = do
      let filename = recordsRoot </> drop 8 path
      createDirectoryIfMissing True (takeDirectory filename)
      writeFile filename value
      writeRecordPairs recordsRoot rest
    writeRecordPairs _ _ = assert "transition fixture has path/value pairs" False ExitSuccess "" ""

infixr 5 </>
(</>) :: FilePath -> FilePath -> FilePath
left </> right = left <> "/" <> right

takeDirectory :: FilePath -> FilePath
takeDirectory = reverse . dropWhile (/= '/') . reverse

manifestFor :: [String] -> [(String, String, String)] -> [(String, Maybe String)]
            -> [(String, Integer, String, String)] -> String
manifestFor paths identities heads contents =
  "{\"protocol\":\"archive-admission/1\",\"records\":["
  <> joinWith "," (map jsonString paths)
  <> "],\"identities\":["
  <> joinWith "," (map identityJson identities)
  <> "],\"contents\":["
  <> joinWith "," (map contentJson contents)
  <> "],\"expected_heads\":["
  <> joinWith "," (map expectedJson heads)
  <> "]}"
  where
    identityJson (objectId, namespace, name) =
      "{\"object_id\":" <> jsonString objectId
      <> ",\"namespace\":" <> jsonString namespace
      <> ",\"name\":" <> jsonString name <> "}"
    expectedJson (objectId, revision) =
      "{\"object_id\":" <> jsonString objectId
      <> ",\"revision_id\":" <> maybe "null" jsonString revision <> "}"
    contentJson (digest, size, annexKey, remote) =
      "{\"digest\":" <> jsonString digest
      <> ",\"size\":" <> show size
      <> ",\"annex_key\":" <> jsonString annexKey
      <> ",\"staging_remote\":" <> jsonString remote <> "}"

transitionManifestFor :: [String] -> [(String, String, String)]
                      -> [(String, Maybe String)] -> String
transitionManifestFor paths identities heads =
  "{\"protocol\":\"archive-transition/1\",\"records\":["
  <> joinWith "," (map jsonString paths)
  <> "],\"identities\":["
  <> joinWith "," (map identityJson identities)
  <> "],\"expected_heads\":["
  <> joinWith "," (map expectedJson heads)
  <> "]}"
  where
    identityJson (objectId, namespace, name) =
      "{\"object_id\":" <> jsonString objectId
      <> ",\"namespace\":" <> jsonString namespace
      <> ",\"name\":" <> jsonString name <> "}"
    expectedJson (objectId, revision) =
      "{\"object_id\":" <> jsonString objectId
      <> ",\"revision_id\":" <> maybe "null" jsonString revision <> "}"
joinWith :: String -> [String] -> String
joinWith _ [] = ""
joinWith separator (value : values) = value <> concatMap (separator <>) values

jsonString :: String -> String
jsonString value = '"' : concatMap escape value <> "\""
  where
    escape '"' = "\\\""
    escape '\\' = "\\\\"
    escape character
      | fromEnum character < 0x20 = "\\u" <> fourHex (fromEnum character)
      | otherwise = [character]
    fourHex number = [hex (number `div` 4096), hex ((number `div` 256) `mod` 16)
                     , hex ((number `div` 16) `mod` 16), hex (number `mod` 16)]
    hex number | number < 10 = toEnum (fromEnum '0' + number)
              | otherwise = toEnum (fromEnum 'a' + number - 10)

doorCommandAt :: FilePath -> [String] -> String
             -> IO (ExitCode, String, String)
doorCommandAt root arguments input =
  readCreateProcessWithExitCode ((proc "door" arguments) {cwd = Just root})
    (input <> "\n")

persistentCommandWithFakeAnnex
  :: FilePath -> String -> FilePath -> FilePath
  -> IO (ExitCode, String, String, Bool)
persistentCommandWithFakeAnnex parent suffix root proposal = do
  let fakeBin = parent </> ("persistent-fake-annex-" <> suffix)
      fakeAnnex = fakeBin </> "git-annex"
      marker = parent </> ("persistent-annex-called-" <> suffix)
  createDirectoryIfMissing True fakeBin
  writeFile fakeAnnex ("#!/bin/sh\ntouch " <> quote marker <> "\nexit 99\n")
  permissions <- getPermissions fakeAnnex
  setPermissions fakeAnnex (setOwnerExecutable True permissions)
  environment <- getEnvironment
  let clean = ("ARCHIVE_GIT_ANNEX_EXECUTABLE", fakeAnnex)
        : filter ((/= "ARCHIVE_GIT_ANNEX_EXECUTABLE") . fst) environment
      process = (proc "door" ["transition", "--proposal", proposal])
        { cwd = Just root
        , env = Just clean
        }
  (code, output, errors) <- readCreateProcessWithExitCode process ""
  called <- doesPathExist marker
  pure (code, output, errors, called)

persistentAdmissionCommandWithFakeAnnex
  :: FilePath -> String -> FilePath -> FilePath
  -> IO (ExitCode, String, String, Bool)
persistentAdmissionCommandWithFakeAnnex parent suffix root proposal = do
  let fakeBin = parent </> ("persistent-admission-fake-annex-" <> suffix)
      fakeAnnex = fakeBin </> "git-annex"
      marker = parent </> ("persistent-admission-annex-called-" <> suffix)
  createDirectoryIfMissing True fakeBin
  writeFile fakeAnnex ("#!/bin/sh\ntouch " <> quote marker <> "\nexit 99\n")
  permissions <- getPermissions fakeAnnex
  setPermissions fakeAnnex (setOwnerExecutable True permissions)
  environment <- getEnvironment
  let clean = ("ARCHIVE_GIT_ANNEX_EXECUTABLE", fakeAnnex)
        : filter ((/= "ARCHIVE_GIT_ANNEX_EXECUTABLE") . fst) environment
      process = (proc "door" ["admit", "--proposal", proposal])
        { cwd = Just root
        , env = Just clean
        }
  (code, output, errors) <- readCreateProcessWithExitCode process ""
  called <- doesPathExist marker
  pure (code, output, errors, called)

persistentCommandWithFailingCommit
  :: String -> FilePath -> FilePath -> FilePath
  -> IO (ExitCode, String, String, Bool)
persistentCommandWithFailingCommit operation parent root proposal = do
  realGit <- findExecutable "git" >>= maybe
    (ioError (userError "persistent fixture cannot find git")) pure
  let fakeBin = parent </> ("persistent-failing-commit-" <> operation)
      fakeGit = fakeBin </> "git"
      fakeAnnex = fakeBin </> "git-annex"
      annexMarker = parent </> ("persistent-failing-commit-annex-" <> operation)
  createDirectoryIfMissing True fakeBin
  writeFile fakeGit
    ("#!/bin/sh\nfor argument in \"$@\"; do\n"
      <> "  if [ \"$argument\" = commit-tree ]; then exit 88; fi\n"
      <> "done\nexec " <> quote realGit <> " \"$@\"\n")
  writeFile fakeAnnex
    ("#!/bin/sh\ntouch " <> quote annexMarker <> "\nexit 99\n")
  mapM_ makeExecutable [fakeGit, fakeAnnex]
  environment <- getEnvironment
  let clean = ("ARCHIVE_GIT_EXECUTABLE", fakeGit)
        : ("ARCHIVE_GIT_ANNEX_EXECUTABLE", fakeAnnex)
        : filter ((/= "ARCHIVE_GIT_EXECUTABLE") . fst)
        (filter ((/= "ARCHIVE_GIT_ANNEX_EXECUTABLE") . fst) environment)
      process = (proc "door" [operation, "--proposal", proposal])
        { cwd = Just root
        , env = Just clean
        }
  (code, output, errors) <- readCreateProcessWithExitCode process ""
  annexCalled <- doesPathExist annexMarker
  pure (code, output, errors, annexCalled)
  where
    makeExecutable path = do
      permissions <- getPermissions path
      setPermissions path (setOwnerExecutable True permissions)

gitHead :: FilePath -> IO String
gitHead root = checkedOutput "persistent fixture reads HEAD"
  ["-C", root, "rev-parse", "HEAD"]

gitCommitCount :: FilePath -> IO Int
gitCommitCount root = do
  output <- checkedOutput "persistent fixture counts commits"
    ["-C", root, "rev-list", "--count", "HEAD"]
  pure (read (trim output))

recordCount :: FilePath -> IO Int
recordCount root = do
  (code, output, errors) <- readProcessWithExitCode "find"
    [root </> "records", "-type", "f"] ""
  assert "persistent fixture counts record files" (code == ExitSuccess)
    code output errors
  pure (length (lines output))

readStoredRecord :: FilePath -> String -> IO LazyByteString.ByteString
readStoredRecord root revision = LazyByteString.readFile
  (root </> recordRelative revision)

readCommittedRecord :: FilePath -> String -> IO LazyByteString.ByteString
readCommittedRecord root revision = do
  let path = recordRelative revision
  (code, output, errors) <- readProcessWithExitCode "git"
    ["-C", root, "show", "HEAD:" <> path] ""
  assert "persistent fixture reads committed record"
    (code == ExitSuccess) code output errors
  pure (LazyByteString.pack output)

canonicalStored :: LazyByteString.ByteString -> Bool
canonicalStored bytes = case decodeUnique bytes of
  Left _ -> False
  Right value -> encodeCanonical value == bytes

checkedOutput :: String -> [String] -> IO String
checkedOutput label arguments = do
  (code, output, errors) <- readProcessWithExitCode "git" arguments ""
  assert label (code == ExitSuccess) code output errors
  pure (trim output)

dryRunDoesNotInvokeAnnex :: FilePath -> FilePath -> FilePath -> IO ()
dryRunDoesNotInvokeAnnex =
  dryRunDoesNotInvokeAnnexFor "proposal dry-run never invokes git-annex" "admit"

dryRunDoesNotInvokeAnnexFor :: String -> String -> FilePath -> FilePath -> FilePath -> IO ()
dryRunDoesNotInvokeAnnexFor label operation parent root proposal = do
  let fakeBin = parent </> ("fake-annex-bin-" <> operation)
      fakeAnnex = fakeBin </> "git-annex"
      marker = parent </> ("git-annex-was-called-" <> operation)
  createDirectoryIfMissing True fakeBin
  writeFile fakeAnnex ("#!/bin/sh\ntouch " <> quote marker <> "\nexit 99\n")
  permissions <- getPermissions fakeAnnex
  setPermissions fakeAnnex (setOwnerExecutable True permissions)
  environment <- getEnvironment
  let clean = ("ARCHIVE_GIT_ANNEX_EXECUTABLE", fakeAnnex)
        : filter ((/= "ARCHIVE_GIT_ANNEX_EXECUTABLE") . fst) environment
      process = (proc "door" [operation, "--proposal", proposal, "--dry-run"])
        { cwd = Just root
        , env = Just clean
        }
  (code, output, errors) <- readCreateProcessWithExitCode process ""
  called <- doesPathExist marker
  assert label
    (code == ExitSuccess && not called) code output errors

proposalAccepts :: String -> FilePath -> FilePath -> [String] -> IO ()
proposalAccepts label root proposal revisions = do
  before <- gitState root
  (code, output, errors) <- doorCommandAt root
    ["admit", "--proposal", proposal, "--dry-run"] ""
  afterState <- gitState root
  let ordered = case revisions of
        [] -> True
        first : rest -> contains ("\"revision_id\":\"" <> first <> "\"") output
          && all (\revision -> contains ("\"revision_id\":\"" <> revision <> "\"") output)
            rest
      inOrder = case revisions of
        [] -> True
        first : second : _ -> case after first output of
          Nothing -> False
          Just suffix -> contains second suffix
        _ -> True
  assert label (code == ExitSuccess && ordered && inOrder && null errors
    && contains "\"status\":\"predicted\"" output
    && contains "\"content_verified\":false" output
    && before == afterState)
    code output errors

gitState :: FilePath -> IO String
gitState root = do
  (headCode, headOutput, headErrors) <- readProcessWithExitCode "git"
    ["-C", root, "rev-parse", "HEAD"] ""
  assert "proposal dry-run reads an existing Git head"
    (headCode == ExitSuccess) headCode headOutput headErrors
  (statusCode, statusOutput, statusErrors) <- readProcessWithExitCode "git"
    ["-C", root, "status", "--porcelain=v1"] ""
  assert "proposal dry-run can inspect a clean archive fixture"
    (statusCode == ExitSuccess) statusCode statusOutput statusErrors
  pure (headOutput <> "\0" <> statusOutput)

proposalRefuses :: String -> FilePath -> FilePath -> IO ()
proposalRefuses label root proposal = do
  (code, output, errors) <- doorCommandAt root
    ["admit", "--proposal", proposal, "--dry-run"] ""
  assert label (code /= ExitSuccess && null output && not (null errors))
    code output errors

proposalRefusesContaining :: String -> String -> FilePath -> FilePath -> IO ()
proposalRefusesContaining label expected root proposal = do
  (code, output, errors) <- doorCommandAt root
    ["admit", "--proposal", proposal, "--dry-run"] ""
  assert label (code /= ExitSuccess && null output && contains expected errors)
    code output errors

proposalRefusesContainingWith :: String -> String -> String -> FilePath -> FilePath -> IO ()
proposalRefusesContainingWith operation label expected root proposal = do
  (code, output, errors) <- doorCommandAt root
    [operation, "--proposal", proposal, "--dry-run"] ""
  assert label (code /= ExitSuccess && null output && contains expected errors)
    code output errors

transitionProposalAccepts :: String -> FilePath -> FilePath -> [String] -> IO ()
transitionProposalAccepts label root proposal retiredObjectIds = do
  before <- gitState root
  (code, output, errors) <- doorCommandAt root
    ["transition", "--proposal", proposal, "--dry-run"] ""
  afterState <- gitState root
  assert label
    (code == ExitSuccess && null errors && before == afterState
      && contains "\"status\":\"predicted\"" output
      && contains "\"authorization_recheck_required\":true" output
      && contains "\"state_recheck_required\":true" output
      && all (\retiredObject -> contains (jsonString retiredObject) output)
        retiredObjectIds)
    code output errors

transitionProposalAcceptsWithDestroyed :: String -> FilePath -> FilePath
                                      -> String -> String -> IO ()
transitionProposalAcceptsWithDestroyed label root proposal retiredObject digest = do
  before <- gitState root
  (code, output, errors) <- doorCommandAt root
    ["transition", "--proposal", proposal, "--dry-run"] ""
  afterState <- gitState root
  assert label
    (code == ExitSuccess && null errors && before == afterState
      && contains "\"status\":\"predicted\"" output
      && contains "\"authorization_recheck_required\":true" output
      && contains "\"state_recheck_required\":true" output
      && contains (jsonString retiredObject) output
      && contains ("\"destroyed_digests\":[\"" <> digest <> "\"]") output)
    code output errors

transitionProposalIsRetry :: String -> FilePath -> FilePath -> [String] -> [String] -> IO ()
transitionProposalIsRetry label root proposal retiredObjectIds digests = do
  before <- gitState root
  (code, output, errors) <- doorCommandAt root
    ["transition", "--proposal", proposal, "--dry-run"] ""
  afterState <- gitState root
  assert label
    (code == ExitSuccess && null errors && before == afterState
      && contains "\"status\":\"predicted\"" output
      && contains "\"retry\":true" output
      && all (\retiredObject -> contains (jsonString retiredObject) output)
        retiredObjectIds
      && all (\digest -> contains (jsonString digest) output) digests)
    code output errors

revisionsOf :: String -> IO [String]
revisionsOf input = do
  (code, output, errors) <- dryRun input
  assert "focused validator computes proposal fixture revisions"
    (code == ExitSuccess) code output errors
  pure [revision | line <- lines output
       , Just revision <- [jsonStringField "revision_id" line]]

requiredField :: String -> String -> IO String
requiredField name value = case jsonStringField name value of
  Just result -> pure result
  Nothing -> do
    putStrLn ("not ok - fixture output has no " <> name)
    exitFailure

allocatedObjectId :: String -> String -> String -> IO String
allocatedObjectId archiveId namespace name = do
  (code, output, errors) <- readProcessWithExitCode "python3"
    [ "-c"
    , "import sys,uuid; print('urn:uuid:'+str(uuid.uuid5(uuid.UUID(sys.argv[1][9:]), sys.argv[2]+'\\0'+sys.argv[3])))"
    , archiveId, namespace, name
    ] ""
  assert "proposal fixture uses UUIDv5 allocation" (code == ExitSuccess)
    code output errors
  pure (trim output)

recordRevisionForObject :: FilePath -> String -> IO String
recordRevisionForObject root objectId = do
  (code, output, errors) <- readProcessWithExitCode "python3"
    [ "-c"
    , "import glob,json,sys; found=[json.load(open(p))['revision_id'] for p in glob.glob(sys.argv[1]+'/records/*/*/*.json') if json.load(open(p))['object_id']==sys.argv[2]]; print(found[0] if found else '')"
    , root, objectId
    ] ""
  assert "proposal fixture finds the committed object head"
    (code == ExitSuccess && not (null (trim output))) code output errors
  pure (trim output)

contractCitation :: FilePath -> String -> IO (String, String, String)
contractCitation root contractId = contractCitationMatching root contractId Nothing

contractCitationVersion :: FilePath -> String -> String -> IO (String, String, String)
contractCitationVersion root contractId version =
  contractCitationMatching root contractId (Just version)

contractCitationMatching :: FilePath -> String -> Maybe String
                         -> IO (String, String, String)
contractCitationMatching root contractId wantedVersion = do
  (code, output, errors) <- readProcessWithExitCode "python3"
    [ "-c"
    , "import glob,json,os,sys; found=[(str(json.load(open(p))['version']),os.path.basename(p)[7:-5]) for p in glob.glob(sys.argv[1]+'/contracts/*.json') if json.load(open(p)).get('id')==sys.argv[2] and (not sys.argv[3] or str(json.load(open(p))['version'])==sys.argv[3])]; found.sort(); print(found[0][0]+' '+found[0][1] if found else '')"
    , root, contractId, maybe "" id wantedVersion
    ] ""
  let fields = words (trim output)
  assert "proposal fixture finds the installed contract"
    (code == ExitSuccess && length fields == 2) code output errors
  case fields of
    [version, digest] -> pure (contractId, version, "sha256:" <> digest)
    _ -> putStrLn "not ok - contract fixture has no version and digest" >> exitFailure

transitionDraftFromArchive :: FilePath -> String -> String -> IO String
transitionDraftFromArchive root contractId objectId = do
  (code, output, errors) <- readProcessWithExitCode "python3"
    [ "-c"
    , "import glob,json,sys; rows=[json.load(open(p)) for p in glob.glob(sys.argv[1]+'/records/*/*/*.json')]; row=next(r for r in rows if any(c['id']==sys.argv[2] for c in r.get('contracts',[]))); row.pop('revision_id',None); row.pop('at',None); row['object_id']=sys.argv[3]; print(json.dumps(row,separators=(',',':'),sort_keys=True))"
    , root, contractId, objectId
    ] ""
  assert "transition proposal fixture comes from the real writer"
    (code == ExitSuccess && not (null (trim output))) code output errors
  pure (trim output)

citedClaim :: String -> (String, String, String) -> String
citedClaim objectId (contractId, version, digest) =
  replace "archive.test/example" contractId
    (replace "\"version\":\"1\"" ("\"version\":\"" <> version <> "\"")
      (replace zeroDigest digest (claimDraft objectId objectId)))

retireDraft :: String -> String -> String -> String -> String
            -> (String, String, String) -> String
retireDraft objectId operatorId operatorRevision targetId targetRevision
    (contractId, version, digest) =
  "{\"spec\":\"archive/0.0.11\",\"object_id\":" <> jsonString objectId
  <> ",\"shape\":\"claim\",\"by\":" <> jsonString writer
  <> ",\"agent\":" <> jsonString operatorId
  <> ",\"payload\":{\"reason\":\"test retirement\"},\"refs\":["
  <> transitionReference "author" operatorId operatorRevision <> ","
  <> transitionReference "authorizer" operatorId operatorRevision <> ","
  <> transitionReference "retires" targetId targetRevision
  <> "],\"supersedes\":[],\"contracts\":[{\"id\":" <> jsonString contractId
  <> ",\"version\":" <> jsonString version <> ",\"digest\":" <> jsonString digest
  <> "}]}"

retireDraftWithPredecessor :: String -> String -> String -> String -> String
                           -> (String, String, String) -> String -> String -> String
retireDraftWithPredecessor objectId operatorId operatorRevision targetId targetRevision
    contract predecessorId predecessorRevision =
  replace "],\"supersedes\""
    ("," <> transitionReference "transition_predecessor"
      predecessorId predecessorRevision <> "],\"supersedes\"")
    (retireDraft objectId operatorId operatorRevision targetId targetRevision contract)

destroyDraft :: String -> String -> String -> String -> String -> String
             -> Integer -> (String, String, String) -> String
destroyDraft objectId operatorId operatorRevision targetId targetRevision digest size
    (contractId, version, contractDigest) =
  "{\"spec\":\"archive/0.0.11\",\"object_id\":" <> jsonString objectId
  <> ",\"shape\":\"claim\",\"by\":" <> jsonString writer
  <> ",\"agent\":" <> jsonString operatorId
  <> ",\"payload\":{\"content\":{\"digest\":" <> jsonString digest
  <> ",\"size\":" <> show size
  <> "},\"reason\":\"test destruction\",\"external_survivors\":[]},\"refs\":["
  <> transitionReference "author" operatorId operatorRevision <> ","
  <> transitionReference "authorizer" operatorId operatorRevision <> ","
  <> transitionReference "destroys" targetId targetRevision
  <> "],\"supersedes\":[],\"contracts\":[{\"id\":"
  <> jsonString contractId <> ",\"version\":" <> jsonString version
  <> ",\"digest\":" <> jsonString contractDigest <> "}]}"

destroyDraftWithPredecessor :: String -> String -> String -> String -> String
                             -> String -> Integer -> (String, String, String)
                             -> String -> String -> String
destroyDraftWithPredecessor objectId operatorId operatorRevision targetId targetRevision
    digest size contract predecessorId predecessorRevision =
  replace "],\"supersedes\""
    ("," <> transitionReference "transition_predecessor"
      predecessorId predecessorRevision <> "],\"supersedes\"")
    (destroyDraft objectId operatorId operatorRevision targetId targetRevision
      digest size contract)

destroyDraftWithPredecessorAndDependencies :: String -> String -> String
                                            -> String -> String -> String
                                            -> Integer -> (String, String, String)
                                            -> String -> String
                                            -> [(String, String, String)] -> String
destroyDraftWithPredecessorAndDependencies objectId operatorId operatorRevision
    targetId targetRevision digest size contract predecessorId predecessorRevision
    dependencies =
  replace "],\"supersedes\""
    (concatMap dependentReference dependencies
      <> "," <> transitionReference "transition_predecessor"
        predecessorId predecessorRevision
      <> "],\"supersedes\"")
    (destroyDraft objectId operatorId operatorRevision targetId targetRevision
      digest size contract)
  where
    dependentReference (role, dependentId, dependentRevision) =
      "," <> transitionReference role dependentId dependentRevision

transitionReference :: String -> String -> String -> String
transitionReference role objectId revision =
  "{\"role\":" <> jsonString role <> ",\"id\":" <> jsonString objectId
  <> ",\"revision_id\":" <> jsonString revision <> "}"

commitEntityFixture :: FilePath -> String -> IO String
commitEntityFixture root objectId = commitDraftFixture root (entityDraft objectId)

commitArtifactFixture :: FilePath -> String -> IO String
commitArtifactFixture root objectId = commitArtifactFixtureWithDigest root objectId zeroDigest

commitArtifactFixtureWithDigest :: FilePath -> String -> String -> IO String
commitArtifactFixtureWithDigest root objectId digest =
  commitDraftFixture root (replace zeroDigest digest (artifactDraft objectId))

commitDraftFixture :: FilePath -> String -> IO String
commitDraftFixture root draft = do
  value <- case decodeUnique (LazyByteString.pack draft) of
    Left problem -> putStrLn ("not ok - entity fixture JSON: " <> problem) >> exitFailure
    Right parsed -> pure parsed
  (revision, stored) <- case finalizeStored "2026-01-01T00:00:00Z" value of
    Left problem -> putStrLn ("not ok - entity fixture writer: " <> Text.unpack problem) >> exitFailure
    Right result -> pure result
  let relative = recordRelative (Text.unpack revision)
  createDirectoryIfMissing True (takeDirectory (root </> relative))
  LazyByteString.writeFile (root </> relative) (encodeCanonical stored)
  shellChecked ("git -C " <> quote root <> " add " <> quote relative
    <> " && git -C " <> quote root <> " -c user.name=archive-test"
    <> " -c user.email=archive-test@example.invalid commit -m fixture >/dev/null")
  pure (Text.unpack revision)

commitDraftBatchFixture :: FilePath -> [String] -> IO [String]
commitDraftBatchFixture root drafts = do
  written <- mapM writeOne drafts
  shellChecked ("git -C " <> quote root <> " add "
    <> unwords (map (quote . fst) written)
    <> " && git -C " <> quote root <> " -c user.name=archive-test"
    <> " -c user.email=archive-test@example.invalid commit -m fixture-batch >/dev/null")
  pure (map snd written)
  where
    writeOne draft = do
      value <- case decodeUnique (LazyByteString.pack draft) of
        Left problem -> putStrLn ("not ok - batch fixture JSON: " <> problem) >> exitFailure
        Right parsed -> pure parsed
      (revision, stored) <- case finalizeStored "2026-01-01T00:00:00Z" value of
        Left problem -> putStrLn ("not ok - batch fixture writer: " <> Text.unpack problem) >> exitFailure
        Right result -> pure result
      let relative = recordRelative (Text.unpack revision)
      createDirectoryIfMissing True (takeDirectory (root </> relative))
      LazyByteString.writeFile (root </> relative) (encodeCanonical stored)
      pure (relative, Text.unpack revision)

alterAndCommitContract :: FilePath -> (String, String, String) -> IO ()
alterAndCommitContract root (_, _, digest) = do
  (code, output, errors) <- readProcessWithExitCode "python3"
    [ "-c"
    , "import hashlib,json,os,sys; p=os.path.join(sys.argv[1],'contracts','sha256-'+sys.argv[2][7:]+'.json'); v=json.load(open(p)); v['context']['replacement_term']='arch:replacement_term'; b=json.dumps(v,separators=(',',':'),sort_keys=True).encode(); q=os.path.join(sys.argv[1],'contracts','sha256-'+hashlib.sha256(b).hexdigest()+'.json'); open(q,'wb').write(b); os.remove(p)"
    , root, digest
    ] ""
  assert "altered contract fixture remains a valid digest-addressed bundle"
    (code == ExitSuccess) code output errors
  shellChecked ("git -C " <> quote root <> " add -A contracts"
    <> " && git -C " <> quote root <> " -c user.name=archive-test"
    <> " -c user.email=archive-test@example.invalid commit -m altered >/dev/null")

removeAndCommitContract :: FilePath -> (String, String, String) -> IO ()
removeAndCommitContract root (_, _, digest) = do
  let path = root </> "contracts" </> ("sha256-" <> drop 7 digest <> ".json")
  shellChecked ("git -C " <> quote root <> " rm " <> quote path
    <> " >/dev/null && git -C " <> quote root <> " -c user.name=archive-test"
    <> " -c user.email=archive-test@example.invalid commit -m missing >/dev/null")

symlinkRootCheck :: FilePath -> IO ()
symlinkRootCheck parent = do
  let target = parent <> "/symlink-target"
      link = parent <> "/symlink-root"
  createDirectory target
  createDirectoryLink target link
  (code, output, errors) <- archiveCommand
    ["init", "--operator", "alice", link] ""
  shellAssert "a symlink archive root leaves its target empty"
    ("test -z \"$(find " <> quote target <> " -mindepth 1 -print -quit)\"")
  assert "archive init refuses a symlink root"
    (code /= ExitSuccess) code output errors

initChecks :: FilePath -> IO ()
initChecks root = do
  realAnnex <- findExecutable "git-annex" >>= maybe
    (ioError (userError "init fixture cannot find git-annex")) pure
  let fakeAnnex = root <> "-configured-annex"
      annexMarker = root <> "-configured-annex-called"
  writeFile fakeAnnex ("#!/bin/sh\ntouch " <> quote annexMarker <> "\nexec "
    <> quote realAnnex <> " \"$@\"\n")
  permissions <- getPermissions fakeAnnex
  setPermissions fakeAnnex (setOwnerExecutable True permissions)
  environment <- getEnvironment
  let childEnvironment = ("ARCHIVE_GIT_ANNEX_EXECUTABLE", fakeAnnex)
        : filter ((/= "ARCHIVE_GIT_ANNEX_EXECUTABLE") . fst) environment
      process = (proc "archive" ["init", "--operator", "alice", root])
        { env = Just childEnvironment }
  (code, output, errors) <- readCreateProcessWithExitCode process ""
  annexCalled <- doesPathExist annexMarker
  assert "init invokes the configured absolute git-annex executable"
    (code == ExitSuccess && annexCalled) code output errors
  assert "archive init creates a missing or empty root" (code == ExitSuccess) code output errors
  let hostileGitRoot = root <> "-ambient-git-dir"
      hostileTargetRoot = root <> "-ambient-work-tree"
      isolatedInitRoot = root <> "-isolated-from-git-environment"
  shellChecked ("mkdir " <> quote hostileGitRoot <> " && git -C " <> quote hostileGitRoot
    <> " init -q && git -C " <> quote hostileGitRoot
    <> " -c user.name=test -c user.email=test@example.invalid commit --allow-empty -q -m sentinel")
  (_, hostileHeadBefore, _) <- shell ("git -C " <> quote hostileGitRoot <> " rev-parse HEAD")
  inherited <- getEnvironment
  let withoutGitVariables = filter ((/= "GIT_") . take 4 . fst) inherited
      hostileEnvironment = ("GIT_DIR", hostileGitRoot)
        : ("GIT_WORK_TREE", hostileTargetRoot)
        : withoutGitVariables
      isolatedProcess = (proc "archive" ["init", "--operator", "alice", isolatedInitRoot])
        { env = Just hostileEnvironment }
  (isolatedCode, isolatedOutput, isolatedErrors) <- readCreateProcessWithExitCode isolatedProcess ""
  (_, hostileHeadAfter, _) <- shell ("git -C " <> quote hostileGitRoot <> " rev-parse HEAD")
  (_, isolatedCommitCount, _) <- shell
    ("git -C " <> quote isolatedInitRoot <> " rev-list --count HEAD")
  assert "archive init ignores inherited Git repository redirection"
    (isolatedCode == ExitSuccess && hostileHeadBefore == hostileHeadAfter
      && trim isolatedCommitCount == "1")
    isolatedCode isolatedOutput isolatedErrors
  verifyAllocatedOperator output
  shellAssert "archive init writes the exact manifest, layout, resources, and records" (initLayoutCheck root)
  shellAssert "archive init copies the exact specification resources" (exactResourcesCheck root)
  shellAssert "archive init makes one archival Git commit and initializes annex" ("test \"$(git -C " <> quote root <> " rev-list --count HEAD)\" = 1 && git -C " <> quote root <> " annex info --json >/dev/null")
  (_, beforeHead, _) <- shell ("git -C " <> quote root <> " rev-parse HEAD")
  (retryCode, retryOutput, retryErrors) <- archiveCommand ["init", "--operator", "alice", root] ""
  assert "identical archive init is quiet and idempotent" (retryCode == ExitSuccess && trim output == trim retryOutput) retryCode retryOutput retryErrors
  shellAssert "identical archive init creates no changes" ("test \"$(git -C " <> quote root <> " rev-parse HEAD)\" = \"" <> trim beforeHead <> "\" && test -z \"$(git -C " <> quote root <> " status --porcelain)\"")
  (differentCode, differentOutput, differentErrors) <- archiveCommand ["init", "--operator", "bob", root] ""
  assert "different operator is refused without mutation" (differentCode /= ExitSuccess) differentCode differentOutput differentErrors
  shellChecked ("test -z \"$(git -C " <> quote root <> " status --porcelain)\"")
  let invalid = root <> "-invalid-operator"
      unrelated = root <> "-unrelated"
      incomplete = root <> "-incomplete"
      missingRecord = root <> "-missing-record"
  (invalidCode, invalidOutput, invalidErrors) <- archiveCommand ["init", "--operator", "bad/name", invalid] ""
  assert "invalid operator is refused before mutation" (invalidCode /= ExitSuccess) invalidCode invalidOutput invalidErrors
  shellAssert "invalid operator leaves no root" ("test ! -e " <> quote invalid)
  shellChecked ("mkdir " <> quote unrelated <> " && printf preserved > " <> quote (unrelated <> "/unrelated"))
  (unrelatedCode, unrelatedOutput, unrelatedErrors) <- archiveCommand ["init", "--operator", "alice", unrelated] ""
  assert "nonempty unrelated root is refused" (unrelatedCode /= ExitSuccess) unrelatedCode unrelatedOutput unrelatedErrors
  shellAssert "nonempty unrelated root is preserved" ("test \"$(cat " <> quote (unrelated <> "/unrelated") <> ")\" = preserved")
  shellChecked ("mkdir " <> quote incomplete <> " && printf tampered > " <> quote (incomplete <> "/archive.json"))
  (incompleteCode, incompleteOutput, incompleteErrors) <- archiveCommand ["init", "--operator", "alice", incomplete] ""
  assert "incomplete archive is refused" (incompleteCode /= ExitSuccess) incompleteCode incompleteOutput incompleteErrors
  typedIncomplete <- initArchiveWith
    (BootstrapEnvironment
      { runSubprocess = \_ _ _ -> pure (ExitSuccess, "", "")
      , runPinnedSubprocess = Nothing
      })
    "alice" incomplete
  assert "existing incomplete archive is reported as a typed integrity failure"
    (case typedIncomplete of
      Left (MutationIntegrityFailure _) -> True
      _ -> False)
    ExitSuccess (show typedIncomplete) ""
  shellAssert "incomplete archive remains untouched" ("test \"$(cat " <> quote (incomplete <> "/archive.json") <> ")\" = tampered")
  shellChecked ("cp -R " <> quote root <> " " <> quote missingRecord)
  -- Records fan out under digest-prefix directories (ADR 0053), and two
  -- bootstrap records may share one, so remove exactly one record file and
  -- count files, never top-level entries.
  let countRecordFiles = do
        (_, counted, _) <- shell ("find " <> quote (missingRecord <> "/records")
          <> " -type f | wc -l | tr -d ' '")
        pure (read (trim counted) :: Int)
  recordFilesBefore <- countRecordFiles
  (_, firstRecord, _) <- shell ("find " <> quote (missingRecord <> "/records")
    <> " -type f | LC_ALL=C sort | head -n 1")
  assert "initialized fixture contains bootstrap records"
    (recordFilesBefore >= 2 && not (null (trim firstRecord))) ExitSuccess firstRecord ""
  removeFile (trim firstRecord)
  (missingCode, missingOutput, missingErrors) <- archiveCommand
    ["init", "--operator", "alice", missingRecord] ""
  assert "missing bootstrap record is refused as an incomplete archive"
    (missingCode /= ExitSuccess) missingCode missingOutput missingErrors
  recordFilesAfter <- countRecordFiles
  assert "missing bootstrap record refusal leaves the fixture unchanged"
    (recordFilesAfter == recordFilesBefore - 1) ExitSuccess (show (recordFilesBefore, recordFilesAfter)) ""
  shellChecked ("cp " <> quote (root <> "/archive.json") <> " " <> quote (root <> "/archive.json.before-tamper") <> " && printf tampered > " <> quote (root <> "/archive.json"))
  (tamperedCode, tamperedOutput, tamperedErrors) <- archiveCommand ["init", "--operator", "alice", root] ""
  assert "tampered archive is refused" (tamperedCode /= ExitSuccess) tamperedCode tamperedOutput tamperedErrors
  shellAssert "tampered archive is not repaired by refusal" ("test \"$(cat " <> quote (root <> "/archive.json") <> ")\" = tampered")

verifyAllocatedOperator :: String -> IO ()
verifyAllocatedOperator output = case
    (jsonStringField "archive_id" output, jsonStringField "operator_id" output) of
  (Just archiveId, Just operatorId) -> do
    (code, expected, errors) <- readProcessWithExitCode "python3"
      [ "-c"
      , "import sys,uuid; print('urn:uuid:'+str(uuid.uuid5(uuid.UUID(sys.argv[1][9:]), 'archive.core/entity\\0alice')))"
      , archiveId
      ] ""
    assert "archive init uses the specified UUIDv5 allocation"
      (code == ExitSuccess && trim expected == operatorId) code expected errors
  _ -> assert "archive init reports both allocated identifiers" False ExitSuccess output ""

jsonStringField :: String -> String -> Maybe String
jsonStringField name value =
  takeWhile (/= '"') <$> after ("\"" <> name <> "\":\"") value

initLayoutCheck :: FilePath -> String
initLayoutCheck root =
  "test -f " <> quote (root <> "/archive.json")
  <> " && grep -Eq '^\\{\"archive_id\":\"urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\",\"layout\":\"archive/2\",\"spec\":\"archive/0.0.11\"\\}$' " <> quote (root <> "/archive.json")
  <> " && test -f " <> quote (root <> "/spec/0.0.11/VERSION") <> " && test -f " <> quote (root <> "/spec/0.0.11/archive-spec.md") <> " && test -f " <> quote (root <> "/spec/0.0.11/context.jsonld")
  <> " && test \"$(find " <> quote (root <> "/contracts") <> " -type f | wc -l | tr -d ' ')\" = 69"
  <> " && test \"$(find " <> quote (root <> "/records") <> " -type f | wc -l | tr -d ' ')\" = 2"
  <> " && for f in " <> quote (root <> "/contracts") <> "/*.json; do test \"$(basename \"$f\" .json | cut -c8-)\" = \"$(shasum -a 256 \"$f\" | cut -d' ' -f1)\"; done"
  <> " && for f in " <> quote (root <> "/records") <> "/*/*/*.json; do grep -Eq '\"revision_id\":\"sha256:[0-9a-f]{64}\"' \"$f\"; done"
  <> " && grep -q '\"shape\":\"entity\"' " <> quote (root <> "/records") <> "/*/*/*.json" <> " && grep -q '\"shape\":\"claim\"' " <> quote (root <> "/records") <> "/*/*/*.json"
  <> " && grep -q '\"archive.transition/operator_initialization\"' " <> quote (root <> "/records") <> "/*/*/*.json"

exactResourcesCheck :: FilePath -> String
exactResourcesCheck root =
  "cmp spec/VERSION " <> quote (root <> "/spec/0.0.11/VERSION")
  <> " && cmp spec/archive-spec.md " <> quote (root <> "/spec/0.0.11/archive-spec.md")
  <> " && cmp spec/0.0.11/context.jsonld " <> quote (root <> "/spec/0.0.11/context.jsonld")
  <> " && diff -rq spec/contracts " <> quote (root <> "/contracts")

shellAssert :: String -> String -> IO ()
shellAssert label script = do
  (code, output, errors) <- shell script
  assert label (code == ExitSuccess) code output errors

shellChecked :: String -> IO ()
shellChecked script = do
  (code, output, errors) <- shell script
  assert "temporary test setup" (code == ExitSuccess) code output errors

shell :: String -> IO (ExitCode, String, String)
shell script = readProcessWithExitCode "sh" ["-c", script] ""

quote :: String -> String
quote value = "'" <> concatMap escape value <> "'"
  where escape '\'' = "'\\''"
        escape character = [character]

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

replace :: String -> String -> String -> String
replace needle replacement value = case after needle value of
  Nothing -> value
  Just suffix -> take (length value - length suffix - length needle) value <> replacement <> suffix

after :: String -> String -> Maybe String
after needle value = search value
  where search [] = if null needle then Just [] else Nothing
        search remaining | needle `prefixOf` remaining = Just (drop (length needle) remaining)
                         | otherwise = search (drop 1 remaining)

trim :: String -> String
trim = dropWhileEnd (`elem` [' ', '\n', '\r', '\t']) . dropWhile (`elem` [' ', '\n', '\r', '\t'])
dropWhileEnd :: (a -> Bool) -> [a] -> [a]
dropWhileEnd predicate = reverse . dropWhile predicate . reverse

assert :: String -> Bool -> ExitCode -> String -> String -> IO ()
assert label passed code output errors = if passed then putStrLn ("ok - " <> label) else do
  putStrLn ("not ok - " <> label)
  putStrLn ("exit: " <> show code)
  putStrLn ("stdout: " <> output)
  putStrLn ("stderr: " <> errors)
  exitFailure

contains :: String -> String -> Bool
contains needle haystack = any (needle `prefixOf`) (tails haystack)
prefixOf :: String -> String -> Bool
prefixOf [] _ = True
prefixOf _ [] = False
prefixOf (x:xs) (y:ys) = x == y && prefixOf xs ys
tails :: String -> [String]
tails [] = [[]]
tails value@(_:rest) = value : tails rest

-- Review application contracts (ADR 0048, specification 0.0.5).

-- | Admit one new record through `door admit`.  The draft is built from the
-- allocated object ID, so the proposal's identity row always matches.
admitNew :: FilePath -> String -> String -> String -> String
         -> (String -> String) -> IO (String, String)
admitNew root archiveId label namespace name draftFor = do
  objectId <- allocatedObjectId archiveId namespace name
  let draft = draftFor objectId
  revision <- canonicalRevisionOf draft
  (code, out, err) <- admitDraft root objectId namespace name draft
  assert label (code == ExitSuccess && contains revision out) code out err
  pure (objectId, revision)

-- | Propose one new record and require a refusal naming the expected reason.
-- Returns the refused draft so a test can anchor what it changed.
refuseNew :: FilePath -> String -> String -> String -> String
          -> (String -> String) -> String -> IO String
refuseNew root archiveId label namespace name draftFor expected = do
  objectId <- allocatedObjectId archiveId namespace name
  let draft = draftFor objectId
  (code, out, err) <- admitDraft root objectId namespace name draft
  assert label (code /= ExitSuccess && contains expected (err <> out)) code out err
  pure draft

-- | The refusal code of a Door reply line, @{"ok": false, "error": {...}}@.
replyCode :: String -> Maybe Aeson.Value
replyCode text = case Aeson.decode (LazyByteString.pack text) of
  Just (Aeson.Object reply)
    | Just (Aeson.Bool False) <- KeyMap.lookup "ok" reply
    , Just (Aeson.Object problem) <- KeyMap.lookup "error" reply -> KeyMap.lookup "code" problem
  _ -> Nothing

admitDraft :: FilePath -> String -> String -> String -> String
           -> IO (ExitCode, String, String)
admitDraft root objectId namespace name draft = do
  proposal <- writeAdmissionProposal root name
    (manifestFor ["records/record.json"] [(objectId, namespace, name)]
      [(objectId, Nothing)] [])
    ["records/record.json", draft]
  doorCommandAt root ["admit", "--proposal", proposal] ""

contractRecord :: String -> (String, String, String) -> String -> [String] -> String -> String
contractRecord shape contract payload refs objectId =
  envelope objectId shape payload ("[" <> joinWith "," refs <> "]")
    (",\"contracts\":[" <> citationJson contract <> "]")

pinned :: String -> (String, String) -> String
pinned role (objectId, revision) = transitionReference role objectId revision

entityRecord :: String -> String -> String -> String
entityRecord kind label objectId =
  envelope objectId "entity"
    ("{\"kind\":" <> jsonString kind <> ",\"label\":" <> jsonString label <> "}") "[]" ""

tracksPayload :: [[(Integer, Integer, Integer)]] -> String
tracksPayload tracks =
  "{\"tracks\":[" <> joinWith "," (map track tracks) <> "]}"
  where
    track pieces = "{\"pieces\":[" <> joinWith "," (map piece pieces) <> "]}"
    piece (start, end, rate) =
      "{\"end_sample\":" <> show end <> ",\"sample_rate\":" <> show rate
      <> ",\"start_sample\":" <> show start <> "}"

alignmentPayload :: [(Integer, Integer)] -> String
alignmentPayload anchors =
  "{\"aligned_rate\":48000,\"anchors\":[" <> joinWith "," (map anchor anchors)
  <> "],\"max_error_samples\":1,\"method\":{\"id\":\"manual\",\"version\":\"1\"}"
  <> ",\"reference_rate\":48000}"
  where
    anchor (reference, aligned) =
      "{\"aligned_sample\":" <> show aligned <> ",\"reference_sample\":" <> show reference <> "}"

stringField :: String -> String -> String
stringField name value = "{" <> jsonString name <> ":" <> jsonString value <> "}"

digestOf :: Char -> String
digestOf character = "sha256:" <> replicate 64 character

-- | The review chain World projects: two simultaneous sources aligned with
-- drift, a two-track selection, its kind, competing titles, an ordered credit,
-- a judgment of one title, and a session with its parts.
reviewChainAdmissions :: FilePath -> String -> (String, String) -> (String, String) -> IO [String]
reviewChainAdmissions root archiveId audio work = do
  lineInId <- allocatedObjectId archiveId "archive.core/artifact" "review-fixture-line-in"
  lineInRevision <- commitArtifactFixtureWithDigest root lineInId (digestOf '1')
  let lineIn = (lineInId, lineInRevision)
  alignmentContract <- contractCitation root "archive.claim/capture_alignment"
  alignment <- admitNew root archiveId "door admit commits a drift-aware capture alignment"
    "archive.claim/capture_alignment" "review-fixture-alignment"
    (contractRecord "claim" alignmentContract
      (alignmentPayload [(0, 0), (48000, 48001), (96000, 96003)])
      [pinned "reference" audio, pinned "aligned" lineIn])
  selectionContract <- contractCitationVersion root "archive.claim/audio_selection" "2"
  selection <- admitNew root archiveId "door admit commits a two-track selection"
    "archive.claim/audio_selection" "review-fixture-two-track-selection"
    (contractRecord "claim" selectionContract
      (tracksPayload [[(10, 200, 48000)], [(11, 201, 48000)]])
      [pinned "piece" audio, pinned "piece" lineIn, pinned "alignment" alignment])
  occurrenceContract <- contractCitationVersion root "archive.claim/song_occurrence" "2"
  occurrence <- admitNew root archiveId "door admit commits an occurrence of a two-track selection"
    "archive.claim/song_occurrence" "review-fixture-occurrence"
    (contractRecord "claim" occurrenceContract "{}" [pinned "selection" selection])
  linkContract <- contractCitationVersion root "archive.claim/performance_work" "3"
  link <- admitNew root archiveId "door admit links that occurrence to its Work"
    "archive.claim/performance_work" "review-fixture-occurrence-work"
    (contractRecord "claim" linkContract "{}" [pinned "performance" occurrence, pinned "work" work])
  kindContract <- contractCitation root "archive.claim/selection_kind"
  kind <- admitNew root archiveId "door admit commits a selection kind"
    "archive.claim/selection_kind" "review-fixture-kind"
    (contractRecord "claim" kindContract (stringField "kind" "original")
      [pinned "selection" selection])
  titleContract <- contractCitation root "archive.claim/selection_title"
  title <- admitNew root archiveId "door admit commits a selection title"
    "archive.claim/selection_title" "review-fixture-title"
    (contractRecord "claim" titleContract (stringField "title" "Paper Lanterns")
      [pinned "selection" selection])
  otherTitle <- admitNew root archiveId "door admit keeps a competing selection title"
    "archive.claim/selection_title" "review-fixture-other-title"
    (contractRecord "claim" titleContract (stringField "title" "Paper Lanterns (slow)")
      [pinned "selection" selection])
  band <- admitNew root archiveId "door admit commits a band Entity"
    "archive.core/entity" "review-fixture-band" (entityRecord "organisation" "The Band")
  guest <- admitNew root archiveId "door admit commits a guest Entity"
    "archive.core/entity" "review-fixture-guest" (entityRecord "person" "Guest")
  creditContract <- contractCitation root "archive.claim/artist_credit"
  credit <- admitNew root archiveId "door admit commits an ordered artist credit"
    "archive.claim/artist_credit" "review-fixture-credit"
    (contractRecord "claim" creditContract "{}"
      [pinned "selection" selection, pinned "artist" band, pinned "artist" guest])
  judgmentContract <- contractCitation root "archive.assessment/judgment"
  judgment <- admitNew root archiveId "door admit commits a judgment of one title"
    "archive.assessment/judgment" "review-fixture-judgment"
    (contractRecord "assessment" judgmentContract
      "{\"reason\":\"Checked against the set list\",\"verdict\":\"affirmed\"}"
      [pinned "assessment_of" title])
  session <- admitNew root archiveId "door admit commits a recording_session Entity"
    "archive.audio/session" ("session-" <> replicate 64 '0')
    (entityRecord "recording_session" "Tuesday rehearsal")
  partsContract <- contractCitation root "archive.claim/session_parts"
  parts <- admitNew root archiveId "door admit commits ordered session parts"
    "archive.claim/session_parts" "review-fixture-parts"
    (contractRecord "claim" partsContract "{}"
      [pinned "session" session, pinned "part" audio, pinned "part" lineIn])
  pure (map snd [ lineIn, alignment, selection, occurrence, link, kind, title, otherTitle, band, guest
                , credit, judgment, session, parts ])

-- | One accepted proposal per contract, then one refusal for each new rule.
-- Each refused fixture differs from its accepted twin only in the rule's field,
-- and the test checks that difference so a refusal cannot pass for another
-- reason hidden in an unrelated change.
reviewContractAdmissionChecks :: FilePath -> IO ()
reviewContractAdmissionChecks parent = do
  (root, archiveId, _) <- newProposalArchive parent "review-contracts"
  let artifactFixture name digest = do
        objectId <- allocatedObjectId archiveId "archive.core/artifact" name
        revision <- commitArtifactFixtureWithDigest root objectId digest
        pure (objectId, revision)
  first <- artifactFixture "review-a" zeroDigest
  second <- artifactFixture "review-b" (digestOf '1')
  sameBytes <- artifactFixture "review-a-copy" zeroDigest
  operator <- admitNew root archiveId "door admit commits the publication authorizer"
    "archive.core/entity" "review-operator" (entityRecord "person" "Operator")
  work <- admitNew root archiveId "door admit commits a first-identified song as a musical_work"
    "archive.audio/song" "song-6f1c1b2e-3c4d-4e5f-8a9b-0c1d2e3f4a5b"
    (entityRecord "musical_work" "Paper Lanterns")
  _ <- pure work

  selectionContract <- contractCitationVersion root "archive.claim/audio_selection" "2"
  let selectionDraft tracks refs = contractRecord "claim" selectionContract (tracksPayload tracks) refs
      acceptedTracks = [[(10, 200, 48000), (200, 300, 48000)], [(11, 201, 48000)]]
      overlappingTracks = [[(10, 200, 48000), (150, 300, 48000)], [(11, 201, 48000)]]
  selection <- admitNew root archiveId "selection v2 accepts consecutive spans on one track"
    "archive.claim/audio_selection" "review-selection"
    (selectionDraft acceptedTracks
      [pinned "piece" first, pinned "piece" first, pinned "piece" second])
  overlapping <- refuseNew root archiveId "selection v2 refuses overlapping spans within a track"
    "archive.claim/audio_selection" "review-selection-overlap"
    (selectionDraft overlappingTracks
      [pinned "piece" first, pinned "piece" first, pinned "piece" second])
    "spans of one track that pin the same Artifact must not overlap"
  assert "the overlap fixture differs only in the second span's start"
    (tracksPayload acceptedTracks /= tracksPayload overlappingTracks
      && contains "\"start_sample\":150" overlapping) ExitSuccess "" ""
  sameContent <- refuseNew root archiveId "selection v2 refuses the same content on two tracks"
    "archive.claim/audio_selection" "review-selection-same-content"
    (selectionDraft [[(10, 200, 48000)], [(10, 200, 48000)]]
      [pinned "piece" first, pinned "piece" sameBytes])
    "tracks must not pin the same content digest"
  assert "the same-content fixture pins two distinct Artifact objects"
    (fst first /= fst sameBytes && contains (fst sameBytes) sameContent) ExitSuccess "" ""
  _ <- admitNew root archiveId "selection v2 accepts different content on two tracks"
    "archive.claim/audio_selection" "review-selection-distinct-content"
    (selectionDraft [[(10, 200, 48000)], [(10, 200, 48000)]]
      [pinned "piece" first, pinned "piece" second])

  alignmentContract <- contractCitation root "archive.claim/capture_alignment"
  let alignmentDraft anchors refs = contractRecord "claim" alignmentContract (alignmentPayload anchors) refs
  _ <- admitNew root archiveId "capture alignment accepts strictly increasing anchors"
    "archive.claim/capture_alignment" "review-alignment"
    (alignmentDraft [(0, 0), (48000, 48001)] [pinned "reference" first, pinned "aligned" second])
  backwards <- refuseNew root archiveId "capture alignment refuses non-monotonic anchors"
    "archive.claim/capture_alignment" "review-alignment-backwards"
    (alignmentDraft [(0, 0), (48000, 48001), (47000, 48002)]
      [pinned "reference" first, pinned "aligned" second])
    "anchors must strictly increase"
  assert "the anchor fixture moves only one reference sample backwards"
    (contains "\"reference_sample\":47000" backwards) ExitSuccess "" ""
  selfAligned <- refuseNew root archiveId "capture alignment refuses one Artifact as both sides"
    "archive.claim/capture_alignment" "review-alignment-self"
    (alignmentDraft [(0, 0), (48000, 48001)] [pinned "reference" first, pinned "aligned" first])
    "must name different objects"
  assert "the self-alignment fixture names the same Artifact twice"
    (length (filter (== fst first) (wordsBy '"' selfAligned)) == 2) ExitSuccess "" ""

  kindContract <- contractCitation root "archive.claim/selection_kind"
  _ <- admitNew root archiveId "selection kind accepts a V1 kind"
    "archive.claim/selection_kind" "review-kind"
    (contractRecord "claim" kindContract (stringField "kind" "talking") [pinned "selection" selection])
  badKind <- refuseNew root archiveId "selection kind refuses a kind outside the vocabulary"
    "archive.claim/selection_kind" "review-kind-bad"
    (contractRecord "claim" kindContract (stringField "kind" "jazz") [pinned "selection" selection])
    "contract instance does not satisfy its schema"
  _ <- refuseNew root archiveId "a schema refusal names where the instance fails and the rule it breaks"
    "archive.claim/selection_kind" "review-kind-bad-named"
    (contractRecord "claim" kindContract (stringField "kind" "jazz") [pinned "selection" selection])
    "contract instance does not satisfy its schema: /payload/kind breaks enum of the contract schema at payload.kind"
  _ <- refuseNew root archiveId "a schema refusal names a member the schema does not allow"
    "archive.claim/selection_kind" "review-kind-extra"
    (contractRecord "claim" kindContract
      "{\"kind\":\"talking\",\"mood\":\"calm\"}" [pinned "selection" selection])
    "/payload/mood is a member the contract schema at payload does not allow"
  jsonRefusalObject <- allocatedObjectId archiveId "archive.claim/selection_kind" "review-kind-json"
  (jsonRefusalCode, jsonRefusalOut, jsonRefusalErr) <- admitDraft root jsonRefusalObject
    "archive.claim/selection_kind" "review-kind-json"
    (contractRecord "claim" kindContract (stringField "kind" "jazz") [pinned "selection" selection]
      jsonRefusalObject)
  assert "a command-line refusal says it on stderr for a person, then as the service's JSON reply"
    (jsonRefusalCode /= ExitSuccess && null jsonRefusalOut
      && case lines jsonRefusalErr of
           [human, reply] -> "door: contract instance does not satisfy its schema" `isPrefixOf` human
             && replyCode reply == Just (Aeson.String "proposal_refused")
           _ -> False)
    jsonRefusalCode jsonRefusalOut jsonRefusalErr
  assert "the kind fixture changes only the kind value"
    (contains "\"kind\":\"jazz\"" badKind) ExitSuccess "" ""
  v1Contract <- contractCitationVersion root "archive.claim/audio_selection" "1"
  v1Selection <- admitNew root archiveId "selection v1 remains admissible at 0.0.5"
    "archive.claim/audio_selection" "review-selection-v1"
    (contractRecord "claim" v1Contract
      "{\"pieces\":[{\"end_sample\":200,\"sample_rate\":48000,\"start_sample\":10}]}"
      [pinned "piece" first])
  _ <- refuseNew root archiveId "selection facts pin only version 2 selections"
    "archive.claim/selection_kind" "review-kind-v1-target"
    (contractRecord "claim" kindContract (stringField "kind" "talking") [pinned "selection" v1Selection])
    "selection reference must target a Claim citing archive.claim/audio_selection version 2"
  occurrenceV1Contract <- contractCitationVersion root "archive.claim/song_occurrence" "1"
  occurrenceV2Contract <- contractCitationVersion root "archive.claim/song_occurrence" "2"
  linkV3Contract <- contractCitationVersion root "archive.claim/performance_work" "3"
  occurrenceV2 <- admitNew root archiveId "occurrence v2 groups a v2 selection"
    "archive.claim/song_occurrence" "review-occurrence-v2"
    (contractRecord "claim" occurrenceV2Contract "{}" [pinned "selection" selection])
  _ <- admitNew root archiveId "performance_work v3 links an occurrence v2 to a musical_work"
    "archive.claim/performance_work" "review-link-v3"
    (contractRecord "claim" linkV3Contract "{}" [pinned "performance" occurrenceV2, pinned "work" work])
  _ <- refuseNew root archiveId "occurrence v2 refuses a v1 selection"
    "archive.claim/song_occurrence" "review-occurrence-v2-of-v1"
    (contractRecord "claim" occurrenceV2Contract "{}" [pinned "selection" v1Selection])
    "selection reference must target a Claim citing archive.claim/audio_selection version 2"
  occurrenceV1 <- admitNew root archiveId "occurrence v1 still groups a v1 selection"
    "archive.claim/song_occurrence" "review-occurrence-v1"
    (contractRecord "claim" occurrenceV1Contract "{}" [pinned "selection" v1Selection])
  _ <- refuseNew root archiveId "performance_work v3 refuses an occurrence v1"
    "archive.claim/performance_work" "review-link-v3-of-v1"
    (contractRecord "claim" linkV3Contract "{}" [pinned "performance" occurrenceV1, pinned "work" work])
    "performance reference must target a Claim citing archive.claim/song_occurrence version 2"

  titleContract <- contractCitation root "archive.claim/selection_title"
  let titleDraft text = contractRecord "claim" titleContract (stringField "title" text) [pinned "selection" selection]
  title <- admitNew root archiveId "selection title accepts exactly 200 characters"
    "archive.claim/selection_title" "review-title" (titleDraft (replicate 200 't'))
  _ <- refuseNew root archiveId "selection title refuses 201 characters"
    "archive.claim/selection_title" "review-title-long" (titleDraft (replicate 201 't'))
    "contract instance does not satisfy its schema"

  creditContract <- contractCitation root "archive.claim/artist_credit"
  band <- admitNew root archiveId "door admit commits an organisation Entity"
    "archive.core/entity" "review-band" (entityRecord "organisation" "The Band")
  let creditDraft artists = contractRecord "claim" creditContract "{}"
        (pinned "selection" selection : map (pinned "artist") artists)
  _ <- admitNew root archiveId "artist credit accepts a person and an organisation in order"
    "archive.claim/artist_credit" "review-credit" (creditDraft [band, operator])
  duplicate <- refuseNew root archiveId "artist credit refuses a duplicate artist"
    "archive.claim/artist_credit" "review-credit-duplicate" (creditDraft [band, band])
    "artist references must name different objects"
  assert "the duplicate-artist fixture repeats one Entity"
    (length (filter (== fst band) (wordsBy '"' duplicate)) == 2) ExitSuccess "" ""
  _ <- refuseNew root archiveId "artist credit refuses an Entity outside person or organisation"
    "archive.claim/artist_credit" "review-credit-work" (creditDraft [work])
    "artist reference must target an Entity revision of kind person or organisation"

  judgmentContract <- contractCitation root "archive.assessment/judgment"
  let judgmentDraft target = contractRecord "assessment" judgmentContract
        "{\"verdict\":\"withdrawn\"}" [pinned "assessment_of" target]
  _ <- admitNew root archiveId "judgment accepts a withdrawn verdict on a claim revision"
    "archive.assessment/judgment" "review-judgment" (judgmentDraft title)
  _ <- refuseNew root archiveId "judgment refuses a target that is not a claim"
    "archive.assessment/judgment" "review-judgment-entity" (judgmentDraft band)
    "assessment_of reference must target a Claim revision"

  approvalContract <- contractCitation root "archive.transition/publication_approval"
  withdrawalContract <- contractCitation root "archive.transition/publication_withdrawal"
  _ <- refuseNew root archiveId "door admit refuses a valid publication approval"
    "archive.transition/publication_approval" "review-approval"
    (contractRecord "claim" approvalContract
      "{\"decision\":\"approve\",\"destination\":\"media-library\"}"
      [pinned "authorizer" operator, pinned "covers" title])
    "unsupported: transition contracts cannot be admitted generically"
  _ <- refuseNew root archiveId "publication withdrawal refuses a target that is not an approval"
    "archive.transition/publication_withdrawal" "review-withdrawal"
    (contractRecord "claim" withdrawalContract "{}"
      [pinned "authorizer" operator, pinned "withdraws" title])
    "withdraws reference must target a Claim citing archive.transition/publication_approval version 1"

  session <- admitNew root archiveId "door admit commits a recording_session Entity"
    "archive.audio/session" ("session-" <> replicate 64 '0')
    (entityRecord "recording_session" "Tuesday rehearsal")
  partsContract <- contractCitation root "archive.claim/session_parts"
  let partsDraft parts = contractRecord "claim" partsContract "{}"
        (pinned "session" session : map (pinned "part") parts)
  _ <- admitNew root archiveId "session parts accept ordered Artifacts"
    "archive.claim/session_parts" "review-parts" (partsDraft [first, second])
  _ <- refuseNew root archiveId "session parts refuse a part that is not an Artifact"
    "archive.claim/session_parts" "review-parts-entity" (partsDraft [first, band])
    "part reference must target an Artifact revision"
  pure ()

-- | Pure checks of the 0.0.5 context and of code-point string length, which
-- `door admit` cannot isolate from the operator's locale.
reviewContractProfileChecks :: IO ()
reviewContractProfileChecks = do
  newBytes <- readFile "spec/0.0.5/context.jsonld"
  priorBytes <- readFile "spec/0.0.4/context.jsonld"
  let newContext = decodeUnique (LazyByteString.pack newBytes)
      priorContext = decodeUnique (LazyByteString.pack priorBytes)
  assert "0.0.5 accepts its recording_session context"
    (either (const False) ((== Right ()) . validateCoreContextForSpecification "archive/0.0.5") newContext)
    ExitSuccess "" ""
  assert "0.0.4 refuses the later recording_session context"
    (either (const False) (isLeft . validateCoreContextForSpecification "archive/0.0.4") newContext)
    ExitSuccess "" ""
  assert "0.0.5 refuses the earlier context without recording_session"
    (either (const False) (isLeft . validateCoreContextForSpecification "archive/0.0.5") priorContext)
    ExitSuccess "" ""
  refuses "specification 0.0.4 refuses the later recording_session kind"
    (replace "archive/0.0.11" "archive/0.0.4"
      (replace "{\"kind\":\"person\"" "{\"kind\":\"recording_session\"" (entityDraft entity)))
    "entity kind is not defined by the core context"
  titleBundle <- readFile "spec/contracts/sha256-0bec7932301de6f62b92ffc167f41236cf98c10e6a6175481400c64da502d8d9.json"
  case decodeUnique (LazyByteString.pack titleBundle) of
    Right (Aeson.Object fields) | Just schema <- KeyMap.lookup "schema" fields -> do
      let instanceWith title = Aeson.object
            [ "payload" .= Aeson.object ["title" .= title]
            , "refs" .= [Aeson.object
                [ "role" .= ("selection" :: Text.Text)
                , "id" .= ("urn:uuid:00000000-0000-4000-8000-000000000002" :: Text.Text)
                , "revision_id" .= Text.pack zeroDigest ]]
            ]
          accented = Text.replicate 200 (Text.singleton '\233')
      assert "maxLength counts code points, not UTF-8 bytes"
        (validateContractInstance schema (instanceWith accented) == Right ()
          && isLeft (validateContractInstance schema (instanceWith (accented <> "x"))))
        ExitSuccess "" ""
    _ -> assert "the installed title bundle decodes" False ExitSuccess titleBundle ""

wordsBy :: Char -> String -> [String]
wordsBy separator value = case break (== separator) value of
  (word, []) -> [word]
  (word, _ : rest) -> word : wordsBy separator rest

-- V2 migration route contracts (ADR 0049, specification 0.0.5).

-- | Admit several new records in one Door batch.  Rows are
-- (object ID, namespace, identity name, draft), in batch order.
admitBatch :: FilePath -> String -> String -> [(String, String, String, String)] -> IO ()
admitBatch root label name rows = do
  let paths = ["records/record-" <> show index <> ".json" | index <- [1 .. length rows]]
  revisions <- mapM (\(_, _, _, draft) -> canonicalRevisionOf draft) rows
  proposal <- writeAdmissionProposal root name
    (manifestFor paths [(objectId, namespace, idName) | (objectId, namespace, idName, _) <- rows]
      [(objectId, Nothing) | (objectId, _, _, _) <- rows] [])
    (concat [[path, draft] | (path, (_, _, _, draft)) <- zip paths rows])
  (code, out, err) <- doorCommandAt root ["admit", "--proposal", proposal] ""
  assert label (code == ExitSuccess && all (`contains` out) revisions) code out err

locatorJson :: String
locatorJson =
  "{\"length\":512,\"member\":\"records/audio-segments/seg_fixture.json\""
  <> ",\"member_sha256\":" <> jsonString (digestOf 'a')
  <> ",\"offset\":1024,\"sha256\":" <> jsonString (digestOf 'b') <> "}"

translationPayload :: String -> String -> String -> String
translationPayload rule schema sourceId =
  "{\"locator\":" <> locatorJson <> ",\"rule\":" <> jsonString rule
  <> ",\"source\":{\"id\":" <> jsonString sourceId <> ",\"schema\":" <> jsonString schema <> "}}"

selectionTranslationPayload :: String -> String -> String
selectionTranslationPayload start end =
  "{\"boundary_rule\":\"round-half-even/1\",\"chain_position\":0"
  <> ",\"chain_root\":\"seg_fixture\",\"locator\":" <> locatorJson
  <> ",\"probe\":{\"sample_rate\":48000,\"tool\":\"ffprobe\",\"tool_version\":\"7.1\"}"
  <> ",\"rule\":\"v2-audio-segment-chain/1\""
  <> ",\"source\":{\"id\":\"seg_fixture\",\"schema\":\"audio_segment\"}"
  <> ",\"source_seconds\":{\"end\":" <> jsonString end <> ",\"start\":" <> jsonString start <> "}}"

-- | Translated claims use native contracts, and each translation record pins
-- them and the frozen bundle Artifact in the same batch.
migrationContractAdmissionChecks :: FilePath -> IO ()
migrationContractAdmissionChecks parent = do
  (root, archiveId, _) <- newProposalArchive parent "migration-contracts"
  let artifactFixture name digest = do
        objectId <- allocatedObjectId archiveId "archive.core/artifact" name
        revision <- commitArtifactFixtureWithDigest root objectId digest
        pure (objectId, revision)
  bundle <- artifactFixture "migration-bundle" (digestOf 'c')
  audio <- artifactFixture "migration-audio" zeroDigest

  selectionContract <- contractCitationVersion root "archive.claim/audio_selection" "2"
  selectionTranslationContract <- contractCitation root "archive.migration/selection_translation"
  selectionId <- allocatedObjectId archiveId "archive.claim/audio_selection" "v2-segment-fixture"
  let selection = contractRecord "claim" selectionContract (tracksPayload [[(10, 201006, 48000)]])
        [pinned "piece" audio] selectionId
  selectionRevision <- canonicalRevisionOf selection
  selectionTranslationId <- allocatedObjectId archiveId "archive.migration/selection_translation" "translation-seg-fixture"
  let selectionTranslation = contractRecord "claim" selectionTranslationContract
        (selectionTranslationPayload "0.000208" "4.1876")
        [pinned "translated" (selectionId, selectionRevision), pinned "migration_source" bundle]
        selectionTranslationId
  admitBatch root "a native v2 selection and its selection translation admit in one batch"
    "migration-selection-batch"
    [ (selectionId, "archive.claim/audio_selection", "v2-segment-fixture", selection)
    , (selectionTranslationId, "archive.migration/selection_translation", "translation-seg-fixture", selectionTranslation)
    ]

  v1Contract <- contractCitationVersion root "archive.claim/audio_selection" "1"
  v1Selection <- admitNew root archiveId "a single-track v1 selection admits for an occurrence"
    "archive.claim/audio_selection" "migration-v1-selection"
    (contractRecord "claim" v1Contract
      "{\"pieces\":[{\"end_sample\":200,\"sample_rate\":48000,\"start_sample\":10}]}"
      [pinned "piece" audio])
  occurrenceContract <- contractCitationVersion root "archive.claim/song_occurrence" "2"
  translationContract <- contractCitation root "archive.migration/translation"
  occurrenceId <- allocatedObjectId archiveId "archive.claim/song_occurrence" "v2-performance-fixture"
  let occurrence = contractRecord "claim" occurrenceContract "{}"
        [pinned "selection" (selectionId, selectionRevision)] occurrenceId
  occurrenceRevision <- canonicalRevisionOf occurrence
  translationId <- allocatedObjectId archiveId "archive.migration/translation" "translation-performance-fixture"
  let translationDraft refs = contractRecord "claim" translationContract
        (translationPayload "v2-song-performance/1" "song_performance" "perf_fixture") refs
      withTranslated = [pinned "translated" (occurrenceId, occurrenceRevision), pinned "migration_source" bundle]
  admitBatch root "an occurrence and its translation admit in one batch" "migration-occurrence-batch"
    [ (occurrenceId, "archive.claim/song_occurrence", "v2-performance-fixture", occurrence)
    , (translationId, "archive.migration/translation", "translation-performance-fixture", translationDraft withTranslated translationId)
    ]

  missing <- refuseNew root archiveId "a translation without a translated claim is refused"
    "archive.migration/translation" "translation-missing-target"
    (translationDraft [pinned "migration_source" bundle])
    "contract instance does not satisfy its schema"
  assert "the missing-target fixture drops only the translated reference"
    (not (contains "\"role\":\"translated\"" missing) && contains "\"role\":\"migration_source\"" missing)
    ExitSuccess "" ""
  let partDraft position = contractRecord "claim" translationContract
        (replace "\"rule\":" ("\"part_position\":" <> show (position :: Integer) <> ",\"rule\":")
          (translationPayload "v2-song-performance/1" "song_performance" "perf_fixture_part"))
        [pinned "translated" v1Selection, pinned "migration_source" bundle]
  _ <- admitNew root archiveId "a translation admits a positive part_position"
    "archive.migration/translation" "translation-part-position" (partDraft 2)
  zeroPart <- refuseNew root archiveId "a translation refuses part_position 0"
    "archive.migration/translation" "translation-part-position-zero" (partDraft 0)
    "contract instance does not satisfy its schema"
  assert "the part_position fixtures differ only in the position"
    (contains "\"part_position\":0," zeroPart && partDraft 2 "x" /= partDraft 0 "x") ExitSuccess "" ""
  _ <- refuseNew root archiveId "a translation's migration source must be an Artifact"
    "archive.migration/translation" "translation-source-claim"
    (translationDraft [pinned "translated" v1Selection, pinned "migration_source" v1Selection])
    "migration_source reference must target an Artifact revision"

  work <- admitNew root archiveId "door admit commits a matched musical_work"
    "archive.audio/song" "song-0b6c2f9a-7d1e-4f3a-9c2b-5e8d7a6f4b3c" (entityRecord "musical_work" "Paper Lanterns")
  matchContract <- contractCitation root "archive.claim/work_match"
  let matchDraft corpus = contractRecord "claim" matchContract
        "{\"direction\":\"lower_is_closer\",\"method\":{\"id\":\"chroma-cover\",\"version\":\"1\"},\"scale\":\"distance\",\"score\":\"0.25\"}"
        ([pinned "occurrence" (occurrenceId, occurrenceRevision), pinned "work" work]
          <> map (pinned "corpus") corpus <> [pinned "run" v1Selection])
  _ <- refuseNew root archiveId "a work match refuses its compared occurrence inside its corpus"
    "archive.claim/work_match" "work-match-self-corpus"
    (matchDraft [(occurrenceId, occurrenceRevision)])
    "must name different objects"
  pure ()

-- | Schema-level checks of the migration bundles that need no run record:
-- decimal text, and a work match that is valid but cannot be admitted yet.
migrationContractProfileChecks :: IO ()
migrationContractProfileChecks = do
  let bundleSchema digest = do
        bytes <- readFile ("spec/contracts/sha256-" <> digest <> ".json")
        case decodeUnique (LazyByteString.pack bytes) of
          Right (Aeson.Object fields) | Just schema <- KeyMap.lookup "schema" fields -> pure schema
          _ -> assert "an installed migration bundle decodes" False ExitSuccess bytes "" >> pure Aeson.Null
      -- Each role names a different object, so the distinct-references rule
      -- holds and a refusal can only come from the field under test.
      ref role = Aeson.object
        [ "role" .= (role :: Text.Text)
        , "id" .= ("urn:uuid:00000000-0000-4000-8000-0000000000" <> roleSuffix role :: Text.Text)
        , "revision_id" .= Text.pack zeroDigest ]
      roleSuffix role = case role of
        "occurrence" -> "11"
        "work" -> "12"
        "corpus" -> "13"
        "run" -> "14"
        "translated" -> "15"
        _ -> "16"
  matchSchema <- bundleSchema "f01dfa681f7261be99e25518842f09c23fdca714f434f316cb86ae1cbf367512"
  let match score = Aeson.object
        [ "payload" .= Aeson.object
            [ "direction" .= ("lower_is_closer" :: Text.Text)
            , "method" .= Aeson.object ["id" .= ("chroma-cover" :: Text.Text), "version" .= ("1" :: Text.Text)]
            , "scale" .= ("distance" :: Text.Text)
            , "score" .= (score :: Text.Text) ]
        , "refs" .= [ ref "occurrence", ref "work", ref "corpus", ref "run" ]
        ]
  assert "a work match accepts exact signed decimal text"
    (all (\score -> isRightUnit (validateContractInstance matchSchema (match score))) ["0", "-0.25", "12.0625"])
    ExitSuccess "" ""
  assert "a work match refuses scores that are not exact decimal text"
    (all (\score -> isLeft (validateContractInstance matchSchema (match score))) ["01", "1e3", ".5", "1.", "+1", "NaN"])
    ExitSuccess "" ""
  selectionSchema <- bundleSchema "c7b635bb3313a8281e89d5ba736f30e4ebf246850f290b58dccc9cf664a0edc5"
  let seconds start = case decodeUnique (LazyByteString.pack (selectionTranslationPayload start "4.1876")) of
        Right payload -> Aeson.object ["payload" .= payload, "refs" .= [ref "translated", ref "migration_source"]]
        Left _ -> Aeson.Null
  assert "source seconds accept up to six decimal places"
    (isRightUnit (validateContractInstance selectionSchema (seconds "0.000208"))
      && isLeft (validateContractInstance selectionSchema (seconds "0.0002081"))
      && isLeft (validateContractInstance selectionSchema (seconds "-1")))
    ExitSuccess "" ""

isRightUnit :: Either Text.Text () -> Bool
isRightUnit = (== Right ())

-- Publication transitions (ADR 0051), through the real `door transition`.

-- | The dispatch table and the installed transition contracts name the same
-- (identifier, version) set, so no transition contract ships without a
-- handler and no handler outlives its contract.
-- | Every specification version's catalog loads, lists only bundles whose
-- bytes carry the listed identity, and a missing or malformed catalog is a
-- refusal, never an empty set (ADR 0057).
-- | The published service schema lists exactly Door's refusal codes, with
-- the same descriptions (ADR 0058).
refusalCodeSchemaChecks :: IO ()
refusalCodeSchemaChecks = do
  bytes <- LazyByteString.readFile "door/service.schema.json"
  let published = case decodeUnique bytes of
        Right (Aeson.Object schema)
          | Just (Aeson.Object definitions) <- KeyMap.lookup "$defs" schema
          , Just (Aeson.Object codes) <- KeyMap.lookup "refusal_code" definitions
          , Just (Aeson.Array options) <- KeyMap.lookup "oneOf" codes ->
              [ (code, description)
              | Aeson.Object option <- toList options
              , Just (Aeson.String code) <- [KeyMap.lookup "const" option]
              , Just (Aeson.String description) <- [KeyMap.lookup "description" option]
              ]
        _ -> []
      expected = [ (refusalCodeText code, refusalCodeDescription code) | code <- [minBound .. maxBound] ]
  assert "door/service.schema.json lists exactly Door's refusal codes and descriptions"
    (not (null published) && published == expected) ExitSuccess (show published) (show expected)

contractCatalogChecks :: IO ()
contractCatalogChecks = do
  versions <- filter (all (`elem` ("0123456789." :: String))) <$> listDirectory "spec"
  assert "every specification version directory is enumerated" (length versions >= 6)
    ExitSuccess (show versions) ""
  mapM_ checkVersion versions
  missing <- loadContractCatalog "archive/9.9.9"
  assert "a specification without a catalog is refused, not empty" (isLeft missing)
    ExitSuccess (show missing) ""
  let good = "{\"spec\":\"archive/0.0.1\",\"contracts\":[{\"id\":\"a\",\"version\":\"1\",\"digest\":\"sha256:" <> replicate 64 '0' <> "\"}]}"
      parsed = parseContractCatalog "archive/0.0.1" . LazyByteString.pack
  assert "a well-formed catalog parses" (either (const False) ((== 1) . length) (parsed good))
    ExitSuccess (show (parsed good)) ""
  mapM_ (\(label, text) -> assert label (isLeft (parsed text)) ExitSuccess (show (parsed text)) "")
    [ ("a catalog naming another specification is refused", replace "archive/0.0.1" "archive/0.0.2" good)
    , ("an empty catalog is refused", "{\"spec\":\"archive/0.0.1\",\"contracts\":[]}")
    , ("a catalog with an extra field is refused", replace "{\"spec\"" "{\"extra\":1,\"spec\"" good)
    , ("a catalog listing a contract twice is refused"
      , replace "}]}" ("}," <> drop 1 (takeWhile (/= ']') (dropWhile (/= '[') good)) <> "]}") good)
    , ("a catalog with a malformed digest is refused", replace "sha256:0" "sha256:g" good)
    ]
  where
    checkVersion version = do
      catalog <- loadContractCatalog (Text.pack ("archive/" <> version))
      case catalog of
        Left problem -> assert ("the catalog of " <> version <> " loads") False ExitSuccess (Text.unpack problem) ""
        Right entries -> mapM_ (checkEntry version) (toList entries)
    checkEntry version (identifier, contractVersion, digest) = do
      bytes <- ByteString.readFile ("spec/contracts/sha256-" <> drop 7 (Text.unpack digest) <> ".json")
      let actual = "sha256:" <> concatMap hex2 (ByteString.unpack (SHA256.hash bytes))
          fields = case decodeUnique (LazyByteString.fromStrict bytes) of
            Right (Aeson.Object object) -> (KeyMap.lookup "id" object, KeyMap.lookup "version" object)
            _ -> (Nothing, Nothing)
      assert (version <> " lists " <> Text.unpack identifier <> " " <> Text.unpack contractVersion <> " by its bytes' digest and identity")
        (actual == Text.unpack digest && fields == (Just (Aeson.String identifier), Just (Aeson.String contractVersion)))
        ExitSuccess actual (show fields)
    hex2 byte = let h = showHex byte "" in if length h == 1 then '0' : h else h

transitionDispatchCatalogChecks :: IO ()
transitionDispatchCatalogChecks = do
  files <- listDirectory "spec/contracts"
  installed <- fmap concat $ mapM (\name -> do
      bytes <- readFile ("spec/contracts" </> name)
      pure $ case decodeUnique (LazyByteString.pack bytes) of
        Right (Aeson.Object fields)
          | Just (Aeson.String identifier) <- KeyMap.lookup "id" fields
          , Just (Aeson.String version) <- KeyMap.lookup "version" fields
          , "archive.transition/" `Text.isPrefixOf` identifier -> [(identifier, version)]
        _ -> []) files
  let table = map fst transitionDispatchTable
      refused = transitionContractsWithoutHandler
  assert "the transition dispatch table and the refused list enumerate exactly the installed transition contracts"
    (not (null installed) && sort installed == sort (table <> refused)
      && length table == length (nubOrdered table)
      && all (`notElem` table) refused)
    ExitSuccess (show installed) (show (table, refused))
  where
    nubOrdered [] = []
    nubOrdered (value : rest) = value : nubOrdered (filter (/= value) rest)

transitionAt :: FilePath -> String -> [(String, String, String)] -> [(String, Maybe String)]
             -> String -> IO (ExitCode, String, String)
transitionAt root name identities heads draft = do
  proposal <- writeTransitionProposal root name
    (transitionManifestFor ["records/transition.json"] identities heads)
    ["records/transition.json", draft]
  doorCommandAt root ["transition", "--proposal", proposal] ""

publicationDraft :: (String, String, String) -> String -> [String] -> String -> String
publicationDraft contract payload refs objectId = contractRecord "claim" contract payload refs objectId

publicationTransitionChecks :: FilePath -> IO ()
publicationTransitionChecks parent = do
  (root, archiveId, initOutput) <- newProposalArchive parent "publication-transitions"
  operatorId <- requiredField "operator_id" initOutput
  operatorRevision <- recordRevisionForObject root operatorId
  let operator = (operatorId, operatorRevision)
  work <- admitNew root archiveId "publication fixture admits a musical_work"
    "archive.core/entity" "publication-work" (entityRecord "musical_work" "Paper Lanterns")
  correspondenceContract <- contractCitation root "archive.claim/work_musicbrainz_correspondence"
  covered <- admitNew root archiveId "publication fixture admits a claim to publish"
    "archive.claim/work_musicbrainz_correspondence" "publication-covered"
    (\objectId -> musicBrainzClaim objectId (fst work) (snd work) correspondenceContract)
  guest <- admitNew root archiveId "publication fixture admits a non-operator person"
    "archive.core/entity" "publication-guest" (entityRecord "person" "Guest")
  approvalContract <- contractCitation root "archive.transition/publication_approval"
  withdrawalContract <- contractCitation root "archive.transition/publication_withdrawal"
  let approvalNamespace = "archive.transition/publication_approval"
      withdrawalNamespace = "archive.transition/publication_withdrawal"
      decisionPayload decision =
        "{\"decision\":" <> jsonString decision <> ",\"destination\":\"Media Library (family)\"}"
      approvalFor decision authorizer coveredPin =
        publicationDraft approvalContract (decisionPayload decision)
          [pinned "authorizer" authorizer, pinned "covers" coveredPin]
      approvalHeads objectId coveredHead =
        [(objectId, Nothing), (operatorId, Just operatorRevision), (fst covered, Just coveredHead)]

  -- Approve; the destination is recorded exactly as written.
  approvalId <- allocatedObjectId archiveId approvalNamespace "approve-covered"
  let approval = approvalFor "approve" operator covered approvalId
  approvalRevision <- canonicalRevisionOf approval
  headBefore <- gitHead root
  (approveCode, approveOut, approveErr) <- transitionAt root "approve"
    [(approvalId, approvalNamespace, "approve-covered")] (approvalHeads approvalId (snd covered)) approval
  headAfter <- gitHead root
  assert "door transition commits a publication approval by the active operator"
    (approveCode == ExitSuccess && contains approvalRevision approveOut && headBefore /= headAfter)
    approveCode approveOut approveErr
  storedApproval <- readFile (root </> recordRelative approvalRevision)
  assert "the approval records its destination exactly"
    (contains "\"destination\":\"Media Library (family)\"" storedApproval) ExitSuccess storedApproval ""
  (retryCode, retryOut, retryErr) <- transitionAt root "approve-retry"
    [(approvalId, approvalNamespace, "approve-covered")] (approvalHeads approvalId (snd covered)) approval
  headAfterRetry <- gitHead root
  assert "an identical approval retry returns the same revision and writes nothing"
    (retryCode == ExitSuccess && contains approvalRevision retryOut && headAfterRetry == headAfter)
    retryCode retryOut retryErr

  -- Hold back passes the same checks and withdraws nothing.
  holdId <- allocatedObjectId archiveId approvalNamespace "hold-covered"
  (holdCode, holdOut, holdErr) <- transitionAt root "hold-back"
    [(holdId, approvalNamespace, "hold-covered")] (approvalHeads holdId (snd covered))
    (approvalFor "hold_back" operator covered holdId)
  assert "door transition commits a hold_back decision" (holdCode == ExitSuccess) holdCode holdOut holdErr

  -- Refusals, one per rule.
  guestId <- allocatedObjectId archiveId approvalNamespace "guest-approval"
  guestHeads <- pure [(guestId, Nothing), (fst guest, Just (snd guest)), (fst covered, Just (snd covered))]
  (guestCode, guestOut, guestErr) <- transitionAt root "guest-approval"
    [(guestId, approvalNamespace, "guest-approval")] guestHeads (approvalFor "approve" guest covered guestId)
  assert "a publication approval by a non-operator authorizer is refused"
    (guestCode /= ExitSuccess && contains "transition authorizer is not the active operator" (guestErr <> guestOut))
    guestCode guestOut guestErr

  missingId <- allocatedObjectId archiveId approvalNamespace "missing-head"
  (missingCode, missingOut, missingErr) <- transitionAt root "missing-head"
    [(missingId, approvalNamespace, "missing-head")]
    [(missingId, Nothing), (operatorId, Just operatorRevision)]
    (approvalFor "approve" operator covered missingId)
  assert "a publication approval whose covered object is not in expected_heads is refused"
    (missingCode /= ExitSuccess && contains "expected_heads must cover exactly" (missingErr <> missingOut))
    missingCode missingOut missingErr

  let revisedDraft = replace "\"supersedes\":[]"
        ("\"supersedes\":[" <> pinned "supersedes" covered <> "]")
        (replace "skos:exactMatch" "skos:closeMatch"
          (musicBrainzClaim (fst covered) (fst work) (snd work) correspondenceContract))
  revisedRevision <- canonicalRevisionOf revisedDraft
  revisedProposal <- writeAdmissionProposal root "publication-covered-revision"
    (manifestFor ["records/revised.json"] [] [(fst covered, Just (snd covered))] [])
    ["records/revised.json", revisedDraft]
  (reviseCode, reviseOut, reviseErr) <- doorCommandAt root ["admit", "--proposal", revisedProposal] ""
  assert "publication fixture revises the covered claim"
    (reviseCode == ExitSuccess && contains revisedRevision reviseOut) reviseCode reviseOut reviseErr
  staleId <- allocatedObjectId archiveId approvalNamespace "stale-cover"
  (staleCode, staleOut, staleErr) <- transitionAt root "stale-cover"
    [(staleId, approvalNamespace, "stale-cover")] (approvalHeads staleId revisedRevision)
    (approvalFor "approve" operator covered staleId)
  assert "a publication approval of a superseded revision is refused"
    (staleCode /= ExitSuccess && contains "transition reference must pin the current object head" (staleErr <> staleOut))
    staleCode staleOut staleErr
  assert "the superseded-revision fixture differs from the current head only by revision"
    (snd covered /= revisedRevision) ExitSuccess "" ""

  -- Withdraw once; a second withdrawal and a withdrawal of a non-approval fail.
  let withdrawal target = publicationDraft withdrawalContract "{\"reason\":\"Pulled by request\"}"
        [pinned "authorizer" operator, pinned "withdraws" target]
      withdrawalHeads objectId target = [(objectId, Nothing), (operatorId, Just operatorRevision), (fst target, Just (snd target))]
  withdrawId <- allocatedObjectId archiveId withdrawalNamespace "withdraw-approval"
  (withdrawCode, withdrawOut, withdrawErr) <- transitionAt root "withdraw"
    [(withdrawId, withdrawalNamespace, "withdraw-approval")]
    (withdrawalHeads withdrawId (approvalId, approvalRevision)) (withdrawal (approvalId, approvalRevision) withdrawId)
  assert "door transition withdraws a live publication approval"
    (withdrawCode == ExitSuccess) withdrawCode withdrawOut withdrawErr
  storedAfter <- readFile (root </> recordRelative approvalRevision)
  assert "a withdrawn approval stays in history unchanged"
    (storedAfter == storedApproval) ExitSuccess "" ""
  againId <- allocatedObjectId archiveId withdrawalNamespace "withdraw-again"
  (againCode, againOut, againErr) <- transitionAt root "withdraw-again"
    [(againId, withdrawalNamespace, "withdraw-again")]
    (withdrawalHeads againId (approvalId, approvalRevision)) (withdrawal (approvalId, approvalRevision) againId)
  assert "a second withdrawal of the same approval is refused"
    (againCode /= ExitSuccess && contains "publication approval is already withdrawn" (againErr <> againOut))
    againCode againOut againErr
  wrongId <- allocatedObjectId archiveId withdrawalNamespace "withdraw-claim"
  (wrongCode, wrongOut, wrongErr) <- transitionAt root "withdraw-claim"
    [(wrongId, withdrawalNamespace, "withdraw-claim")]
    (withdrawalHeads wrongId (fst covered, revisedRevision)) (withdrawal (fst covered, revisedRevision) wrongId)
  assert "withdrawing a claim that is not an approval is refused"
    (wrongCode /= ExitSuccess
      && contains "withdraws reference must target a Claim citing archive.transition/publication_approval version 1" (wrongErr <> wrongOut))
    wrongCode wrongOut wrongErr

  -- Version 2 (specification 0.0.10): an approval states its class, and
  -- Door refuses one beyond the circle that the archive's own rights forbid
  -- today. The Work the covered claim pins has no rights claim, so it
  -- reaches service and no further.
  approvalV2 <- contractCitationVersion root approvalNamespace "2"
  withdrawalV2 <- contractCitationVersion root withdrawalNamespace "2"
  let current = (fst covered, revisedRevision)
      classApproval exposure objectId = publicationDraft approvalV2
        ("{\"class\":" <> jsonString exposure <> ",\"decision\":\"approve\",\"destination\":\"A streaming service\"}")
        [pinned "authorizer" operator, pinned "covers" current] objectId
  openId <- allocatedObjectId archiveId approvalNamespace "open-v2"
  (openCode, openOut, openErr) <- transitionAt root "open-v2"
    [(openId, approvalNamespace, "open-v2")] (approvalHeads openId revisedRevision) (classApproval "open" openId)
  assert "a version 2 approval to open is refused, naming the Work without a rights claim"
    (openCode /= ExitSuccess && contains "publication approval beyond the circle is refused" (openErr <> openOut)
      && contains (fst work <> " has no rights claim") (openErr <> openOut))
    openCode openOut openErr
  serviceId <- allocatedObjectId archiveId approvalNamespace "service-v2"
  let serviceApproval = classApproval "service" serviceId
  serviceRevision <- canonicalRevisionOf serviceApproval
  (serviceCode, serviceOut, serviceErr) <- transitionAt root "service-v2"
    [(serviceId, approvalNamespace, "service-v2")] (approvalHeads serviceId revisedRevision) serviceApproval
  assert "a version 2 approval to a service is committed: unknown rights reach service"
    (serviceCode == ExitSuccess && contains serviceRevision serviceOut) serviceCode serviceOut serviceErr
  let withdrawalV2Of target = publicationDraft withdrawalV2 "{\"reason\":\"Pulled by request\"}"
        [pinned "authorizer" operator, pinned "withdraws" target]
  withdrawV2Id <- allocatedObjectId archiveId withdrawalNamespace "withdraw-v2"
  (withdrawV2Code, withdrawV2Out, withdrawV2Err) <- transitionAt root "withdraw-v2"
    [(withdrawV2Id, withdrawalNamespace, "withdraw-v2")]
    (withdrawalHeads withdrawV2Id (serviceId, serviceRevision)) (withdrawalV2Of (serviceId, serviceRevision) withdrawV2Id)
  assert "a version 2 withdrawal withdraws a version 2 approval"
    (withdrawV2Code == ExitSuccess) withdrawV2Code withdrawV2Out withdrawV2Err
  againV2Id <- allocatedObjectId archiveId withdrawalNamespace "withdraw-v1-again"
  (againV2Code, againV2Out, againV2Err) <- transitionAt root "withdraw-v1-again"
    [(againV2Id, withdrawalNamespace, "withdraw-v1-again")]
    (withdrawalHeads againV2Id (approvalId, approvalRevision)) (withdrawalV2Of (approvalId, approvalRevision) againV2Id)
  assert "a version 2 withdrawal of an approval a version 1 withdrawal withdrew is refused"
    (againV2Code /= ExitSuccess && contains "publication approval is already withdrawn" (againV2Err <> againV2Out))
    againV2Code againV2Out againV2Err

  _ <- refuseNew root archiveId "door admit still refuses a publication approval"
    approvalNamespace "admit-approval" (approvalFor "approve" operator (fst covered, revisedRevision))
    "unsupported: transition contracts cannot be admitted generically"

  -- The committed publication history replays: a later admission reads it.
  _ <- admitNew root archiveId "admission after publication transitions replays their history"
    "archive.core/entity" "publication-after" (entityRecord "person" "Later")
  pure ()

-- | Archive-relative record and content paths, from the layout module.
recordRelative :: String -> FilePath
recordRelative revision = either (error . Text.unpack) recordRelativePath
  (parseSha256Digest (Text.pack revision))

contentRelative :: String -> FilePath
contentRelative digest = either (error . Text.unpack) contentRelativePath
  (parseSha256Digest (Text.pack digest))

-- Derivation runs, audio facts, suggestions, and run-pinned matches and
-- alignments (ADRs 0064 to 0068, specification 0.0.9), through the real
-- `door admit`. Each refusal's draft differs from an accepted twin only in the
-- rule's field, and the test anchors that difference.

runName :: Int -> String
runName index = "run-00000000-0000-4000-8000-" <> replicate (12 - length digits) '0' <> digits
  where digits = show index

runPayloadAt :: String -> String -> String
runPayloadAt started ended =
  "{\"ended_at\":" <> jsonString ended
  <> ",\"method\":{\"id\":\"energy-spans\",\"version\":\"1\"}"
  <> ",\"software\":[{\"name\":\"ffmpeg\",\"version\":\"7.1\"},{\"name\":\"numpy\",\"version\":\"2.1.0\"}]"
  <> ",\"started_at\":" <> jsonString started <> "}"

runPayload :: String
runPayload = runPayloadAt "2026-10-16T10:00:00Z" "2026-10-16T10:00:05.250Z"

streamPayload :: Integer -> Integer -> Integer -> String
streamPayload rate count channels =
  "{\"bits_per_sample\":24,\"channel_count\":" <> show channels
  <> ",\"codec\":\"pcm_s24le\",\"sample_count\":" <> show count
  <> ",\"sample_rate\":" <> show rate <> ",\"stream_index\":0}"

spansPayload :: Integer -> [(Integer, Integer, String)] -> String
spansPayload rate spans =
  "{\"sample_rate\":" <> show rate <> ",\"spans\":[" <> joinWith "," (map span' spans) <> "]}"
  where
    span' (start, end, kind) =
      "{\"class\":" <> jsonString kind <> ",\"end_sample\":" <> show end
      <> ",\"start_sample\":" <> show start <> "}"

matchPayload :: String
matchPayload =
  "{\"direction\":\"lower_is_closer\",\"method\":{\"id\":\"chroma-cover\",\"version\":\"1\"},\"scale\":\"distance\",\"score\":\"0.25\"}"

derivationContractAdmissionChecks :: FilePath -> IO ()
derivationContractAdmissionChecks parent = do
  (root, archiveId, _) <- newProposalArchive parent "derivation-contracts"
  let artifactFixture name digest = do
        objectId <- allocatedObjectId archiveId "archive.core/artifact" name
        revision <- commitArtifactFixtureWithDigest root objectId digest
        pure (objectId, revision)
      runNamespace = "archive.derivation/run"
  audio <- artifactFixture "derivation-audio" zeroDigest
  other <- artifactFixture "derivation-other" (digestOf '1')
  parameters <- artifactFixture "derivation-parameters" (digestOf '2')
  work <- admitNew root archiveId "door admit commits a musical_work for matches"
    "archive.audio/song" "song-1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d" (entityRecord "musical_work" "Fixture Work")

  -- Runs (ADR 0064).
  runContract <- contractCitationVersion root "archive.claim/derivation_run" "1"
  let runDraft payload refs = contractRecord "claim" runContract payload refs
  run <- admitNew root archiveId "a derivation run with used inputs and parameters is admitted"
    runNamespace (runName 1)
    (runDraft runPayload [pinned "used" audio, pinned "parameters" parameters])
  _ <- admitNew root archiveId "a run that ends when it starts is admitted"
    runNamespace (runName 2)
    (runDraft (runPayloadAt "2026-10-16T10:00:00Z" "2026-10-16T10:00:00Z") [])
  _ <- admitNew root archiveId "a run may use an Entity and a Claim revision"
    runNamespace (runName 3)
    (runDraft runPayload [pinned "used" work, pinned "used" run])
  backwards <- refuseNew root archiveId "a run that ends before it starts is refused"
    runNamespace (runName 4)
    (runDraft (runPayloadAt "2026-10-16T10:00:00Z" "2026-10-16T09:59:59.999Z") [])
    "ended_at must not be before started_at"
  assert "the backwards run differs from an admitted run only in ended_at"
    (contains "\"ended_at\":\"2026-10-16T09:59:59.999Z\"" backwards
      && contains "\"started_at\":\"2026-10-16T10:00:00Z\"" backwards)
    ExitSuccess backwards ""
  offset <- refuseNew root archiveId "a run instant with a numeric offset is refused"
    runNamespace (runName 5)
    (runDraft (runPayloadAt "2026-10-16T10:00:00+00:00" "2026-10-16T10:00:05Z") [])
    "contract instance does not satisfy its schema"
  assert "the offset fixture changes only the started_at suffix"
    (contains "10:00:00+00:00" offset) ExitSuccess "" ""
  -- The pattern alone admits any digits; the date must exist.
  _ <- refuseNew root archiveId "a run instant on a day that does not exist is refused"
    runNamespace (runName 12)
    (runDraft (runPayloadAt "2026-02-30T10:00:00Z" "2026-10-16T10:00:05Z") [])
    "contract instance does not satisfy its schema"
  _ <- refuseNew root archiveId "a run instant at hour 24 is refused"
    runNamespace (runName 13)
    (runDraft (runPayloadAt "2026-10-16T10:00:00Z" "2026-10-16T24:00:00Z") [])
    "contract instance does not satisfy its schema"
  _ <- refuseNew root archiveId "a run refuses two parameters Artifacts"
    runNamespace (runName 6)
    (runDraft runPayload [pinned "parameters" parameters, pinned "parameters" other])
    "contract instance does not satisfy its schema"
  _ <- refuseNew root archiveId "a run's parameters must be an Artifact"
    runNamespace (runName 7)
    (runDraft runPayload [pinned "parameters" work])
    "parameters reference must target an Artifact revision"
  let software count = "{\"ended_at\":\"2026-10-16T10:00:05Z\",\"method\":{\"id\":\"energy-spans\",\"version\":\"1\"},\"software\":["
        <> joinWith "," (replicate count "{\"name\":\"lib\",\"version\":\"1\"}")
        <> "],\"started_at\":\"2026-10-16T10:00:00Z\"}"
  _ <- admitNew root archiveId "a run names up to 32 software components"
    runNamespace (runName 8) (runDraft (software 32) [])
  _ <- refuseNew root archiveId "a run refuses 33 software components"
    runNamespace (runName 9) (runDraft (software 33) [])
    "contract instance does not satisfy its schema"
  judgmentContract <- contractCitation root "archive.assessment/judgment"
  judgment <- admitNew root archiveId "door admit commits a judgment of a run"
    "archive.assessment/judgment" "derivation-judgment"
    (contractRecord "assessment" judgmentContract "{\"verdict\":\"affirmed\"}" [pinned "assessment_of" run])
  _ <- refuseNew root archiveId "a run may not use an Assessment revision"
    runNamespace (runName 10) (runDraft runPayload [pinned "used" judgment])
    "used reference must target a revision of shape artifact or claim or entity"

  -- Stream facts (ADR 0065), proposed with their run in one batch.
  streamContract <- contractCitationVersion root "archive.claim/audio_stream" "1"
  let streamDraft payload refs = contractRecord "claim" streamContract payload refs
  batchRunId <- allocatedObjectId archiveId runNamespace (runName 11)
  streamId <- allocatedObjectId archiveId "archive.claim/audio_stream" "derivation-stream"
  let batchRunDraft = runDraft runPayload [pinned "used" audio] batchRunId
  batchRunRevision <- canonicalRevisionOf batchRunDraft
  let batchRun = (batchRunId, batchRunRevision)
      acceptedStream = streamDraft (streamPayload 48000 480000 2) [pinned "artifact" audio, pinned "run" batchRun] streamId
  streamRevision <- canonicalRevisionOf acceptedStream
  admitBatch root "a run and the stream facts it made are admitted in one batch" "derivation-stream-batch"
    [ (batchRunId, runNamespace, runName 11, batchRunDraft)
    , (streamId, "archive.claim/audio_stream", "derivation-stream", acceptedStream) ]
  let stream = (streamId, streamRevision)
  _ <- admitNew root archiveId "a stream of 64 channels is admitted"
    "archive.claim/audio_stream" "derivation-stream-64"
    (streamDraft (streamPayload 48000 480000 64) [pinned "artifact" audio, pinned "run" run])
  wide <- refuseNew root archiveId "a stream of 65 channels is refused by its maximum"
    "archive.claim/audio_stream" "derivation-stream-65"
    (streamDraft (streamPayload 48000 480000 65) [pinned "artifact" audio, pinned "run" run])
    "contract instance does not satisfy its schema"
  assert "the channel fixtures differ only in channel_count"
    (contains "\"channel_count\":65," wide) ExitSuccess "" ""
  _ <- refuseNew root archiveId "a stream refuses a zero sample_count"
    "archive.claim/audio_stream" "derivation-stream-empty"
    (streamDraft (streamPayload 48000 0 2) [pinned "artifact" audio, pinned "run" run])
    "contract instance does not satisfy its schema"
  _ <- refuseNew root archiveId "a stream's run must be a derivation run"
    "archive.claim/audio_stream" "derivation-stream-not-run"
    (streamDraft (streamPayload 48000 480000 2) [pinned "artifact" audio, pinned "run" stream])
    "run reference must target a Claim citing archive.claim/derivation_run version 1"
  _ <- refuseNew root archiveId "a stream's subject must be an Artifact"
    "archive.claim/audio_stream" "derivation-stream-of-entity"
    (streamDraft (streamPayload 48000 480000 2) [pinned "artifact" work, pinned "run" run])
    "artifact reference must target an Artifact revision"
  _ <- refuseNew root archiveId "a stream without a run is refused"
    "archive.claim/audio_stream" "derivation-stream-no-run"
    (streamDraft (streamPayload 48000 480000 2) [pinned "artifact" audio])
    "contract instance does not satisfy its schema"

  -- Span suggestions (ADR 0066).
  suggestionsContract <- contractCitationVersion root "archive.claim/span_suggestions" "1"
  let suggestionsDraft payload refs = contractRecord "claim" suggestionsContract payload refs
      againstStream source stream' = [pinned "source" source, pinned "stream" stream', pinned "run" run]
      suggest label name spans expected = refuseNew root archiveId label
        "archive.claim/span_suggestions" name
        (suggestionsDraft (spansPayload 48000 spans) (againstStream audio stream)) expected
      accepted = [(0, 1000, "music"), (1000, 5000, "talk"), (6000, 480000, "quiet")]
  _ <- admitNew root archiveId "span suggestions in order, touching, and ending at sample_count are admitted"
    "archive.claim/span_suggestions" "derivation-suggestions"
    (suggestionsDraft (spansPayload 48000 accepted) (againstStream audio stream))
  overlap <- suggest "overlapping suggested spans are refused" "derivation-suggestions-overlap"
    [(0, 1000, "music"), (900, 5000, "talk"), (6000, 480000, "quiet")]
    "spans must be in order and must not overlap"
  assert "the overlap fixture moves only the second span's start"
    (contains "\"start_sample\":900" overlap) ExitSuccess "" ""
  _ <- suggest "suggested spans out of order are refused" "derivation-suggestions-order"
    [(1000, 5000, "talk"), (0, 1000, "music")]
    "spans must be in order and must not overlap"
  _ <- suggest "an empty suggested span is refused" "derivation-suggestions-empty-span"
    [(0, 1000, "music"), (5000, 5000, "talk")]
    "each span requires 0 <= start < end"
  past <- suggest "a suggested span past the stream's sample_count is refused" "derivation-suggestions-past"
    [(0, 1000, "music"), (1000, 5000, "talk"), (6000, 480001, "quiet")]
    "end_sample must not exceed the stream claim's sample_count"
  assert "the past-the-end fixture adds one sample to the last end"
    (contains "\"end_sample\":480001" past) ExitSuccess "" ""
  rate <- refuseNew root archiveId "suggestions at a rate other than the stream's are refused"
    "archive.claim/span_suggestions" "derivation-suggestions-rate"
    (suggestionsDraft (spansPayload 44100 accepted) (againstStream audio stream))
    "sample_rate must equal the stream claim's sample_rate"
  assert "the rate fixture changes only sample_rate"
    (contains "\"sample_rate\":44100" rate) ExitSuccess "" ""
  _ <- suggest "an unknown span class is refused" "derivation-suggestions-class"
    [(0, 1000, "noise")]
    "contract instance does not satisfy its schema"
  assert "the stream and the other source are different Artifacts"
    (fst audio /= fst other) ExitSuccess "" ""
  _ <- refuseNew root archiveId "suggestions about another Artifact than the stream's are refused"
    "archive.claim/span_suggestions" "derivation-suggestions-other-source"
    (suggestionsDraft (spansPayload 48000 accepted) (againstStream other stream))
    "stream claim must be about the same source revision"
  revisedId <- pure (fst audio)
  revisedRevision <- commitDraftFixture root
    (replace "\"supersedes\":[]" ("\"supersedes\":[" <> pinned "supersedes" audio <> "]")
      (replace zeroDigest (digestOf '3') (artifactDraft revisedId)))
  assert "the revised Artifact is a new revision of the stream's object"
    (revisedRevision /= snd audio) ExitSuccess revisedRevision (snd audio)
  _ <- refuseNew root archiveId "suggestions about another revision of the stream's Artifact are refused"
    "archive.claim/span_suggestions" "derivation-suggestions-other-revision"
    (suggestionsDraft (spansPayload 48000 accepted) (againstStream (revisedId, revisedRevision) stream))
    "stream claim must be about the same source revision"
  _ <- refuseNew root archiveId "suggestions must pin an audio stream claim"
    "archive.claim/span_suggestions" "derivation-suggestions-not-stream"
    (suggestionsDraft (spansPayload 48000 accepted) (againstStream audio run))
    "stream reference must target a Claim citing archive.claim/audio_stream version 1"
  _ <- refuseNew root archiveId "suggestions' run must be a derivation run"
    "archive.claim/span_suggestions" "derivation-suggestions-not-run"
    (suggestionsDraft (spansPayload 48000 accepted)
      [pinned "source" audio, pinned "stream" stream, pinned "run" stream])
    "run reference must target a Claim citing archive.claim/derivation_run version 1"
  let many count = [(index * 48, index * 48 + 48, "unknown") | index <- [0 .. count - 1]]
  _ <- admitNew root archiveId "10,000 suggested spans are admitted"
    "archive.claim/span_suggestions" "derivation-suggestions-most"
    (suggestionsDraft (spansPayload 48000 (many 10000)) (againstStream audio stream))
  _ <- refuseNew root archiveId "10,001 suggested spans are refused"
    "archive.claim/span_suggestions" "derivation-suggestions-too-many"
    (suggestionsDraft (spansPayload 48000 (many 10001)) (againstStream audio stream))
    "contract instance does not satisfy its schema"

  -- Matches and alignments pin their run (ADR 0067).
  selectionContract <- contractCitationVersion root "archive.claim/audio_selection" "2"
  occurrenceContract <- contractCitationVersion root "archive.claim/song_occurrence" "2"
  selection <- admitNew root archiveId "door admit commits a selection to match"
    "archive.claim/audio_selection" "derivation-selection"
    (contractRecord "claim" selectionContract (tracksPayload [[(10, 200, 48000)]]) [pinned "piece" audio])
  corpusSelection <- admitNew root archiveId "door admit commits a corpus selection"
    "archive.claim/audio_selection" "derivation-corpus-selection"
    (contractRecord "claim" selectionContract (tracksPayload [[(10, 200, 48000)]]) [pinned "piece" other])
  occurrence <- admitNew root archiveId "door admit commits an occurrence to match"
    "archive.claim/song_occurrence" "derivation-occurrence"
    (contractRecord "claim" occurrenceContract "{}" [pinned "selection" selection])
  corpus <- admitNew root archiveId "door admit commits a corpus occurrence"
    "archive.claim/song_occurrence" "derivation-corpus"
    (contractRecord "claim" occurrenceContract "{}" [pinned "selection" corpusSelection])
  v1SelectionContract <- contractCitationVersion root "archive.claim/audio_selection" "1"
  v1Selection <- admitNew root archiveId "door admit commits a version 1 selection"
    "archive.claim/audio_selection" "derivation-selection-v1"
    (contractRecord "claim" v1SelectionContract
      "{\"pieces\":[{\"end_sample\":200,\"sample_rate\":48000,\"start_sample\":10}]}" [pinned "piece" audio])
  matchContract <- contractCitationVersion root "archive.claim/work_match" "2"
  let matchDraft compared run' = contractRecord "claim" matchContract matchPayload
        (compared <> [pinned "work" work, pinned "corpus" corpus, pinned "parameters" parameters, pinned "run" run'])
  _ <- admitNew root archiveId "a version 2 match of an occurrence pins its run"
    "archive.claim/work_match" "derivation-match-occurrence"
    (matchDraft [pinned "occurrence" occurrence] run)
  _ <- admitNew root archiveId "a version 2 match of a selection pins its run"
    "archive.claim/work_match" "derivation-match-selection"
    (matchDraft [pinned "selection" selection] run)
  both <- refuseNew root archiveId "a match naming both an occurrence and a selection is refused"
    "archive.claim/work_match" "derivation-match-both"
    (matchDraft [pinned "occurrence" occurrence, pinned "selection" selection] run)
    "contract instance does not satisfy its schema"
  assert "the both fixture adds only the selection reference"
    (contains "\"role\":\"occurrence\"" both && contains "\"role\":\"selection\"" both) ExitSuccess "" ""
  _ <- refuseNew root archiveId "a match naming neither an occurrence nor a selection is refused"
    "archive.claim/work_match" "derivation-match-neither"
    (matchDraft [] run)
    "contract instance does not satisfy its schema"
  _ <- refuseNew root archiveId "a version 2 match refuses a run that is not a derivation run"
    "archive.claim/work_match" "derivation-match-not-run"
    (matchDraft [pinned "occurrence" occurrence] stream)
    "run reference must target a Claim citing archive.claim/derivation_run version 1"
  _ <- refuseNew root archiveId "a version 2 match compares only version 2 selections"
    "archive.claim/work_match" "derivation-match-v1-selection"
    (matchDraft [pinned "selection" v1Selection] run)
    "selection reference must target a Claim citing archive.claim/audio_selection version 2"
  _ <- refuseNew root archiveId "a version 2 match still refuses its occurrence inside its corpus"
    "archive.claim/work_match" "derivation-match-self-corpus"
    (contractRecord "claim" matchContract matchPayload
      [pinned "occurrence" corpus, pinned "work" work, pinned "corpus" corpus, pinned "run" run])
    "must name different objects"

  alignmentV2 <- contractCitationVersion root "archive.claim/capture_alignment" "2"
  alignmentV1 <- contractCitationVersion root "archive.claim/capture_alignment" "1"
  let alignment contract refs = contractRecord "claim" contract
        (alignmentPayload [(0, 0), (48000, 48001)]) ([pinned "reference" audio, pinned "aligned" other] <> refs)
  _ <- admitNew root archiveId "a version 2 alignment without a run is admitted"
    "archive.claim/capture_alignment" "derivation-alignment-unrun" (alignment alignmentV2 [])
  _ <- admitNew root archiveId "a version 2 alignment pins a derivation run"
    "archive.claim/capture_alignment" "derivation-alignment-run" (alignment alignmentV2 [pinned "run" run])
  _ <- refuseNew root archiveId "a version 2 alignment refuses a run that is not a derivation run"
    "archive.claim/capture_alignment" "derivation-alignment-not-run" (alignment alignmentV2 [pinned "run" stream])
    "run reference must target a Claim citing archive.claim/derivation_run version 1"
  _ <- admitNew root archiveId "a version 1 alignment keeps its untyped run"
    "archive.claim/capture_alignment" "derivation-alignment-v1" (alignment alignmentV1 [pinned "run" stream])
  pure ()

-- | On an archive already at this Door's release there is nothing later to
-- adopt: `door transition` refuses an upgrade to a version it has no
-- catalog for, and `door admit` refuses the contract as it refuses every
-- transition contract.
specificationUpgradeRefusalChecks :: FilePath -> IO ()
specificationUpgradeRefusalChecks parent = do
  (root, archiveId, initOutput) <- newProposalArchive parent "specification-upgrade"
  operatorId <- requiredField "operator_id" initOutput
  operatorRevision <- recordRevisionForObject root operatorId
  upgradeContract <- contractCitationVersion root "archive.transition/specification_upgrade" "1"
  let namespace = "archive.transition/specification_upgrade"
      upgrade = contractRecord "claim" upgradeContract
        "{\"from\":\"archive/0.0.11\",\"to\":\"archive/0.0.12\"}" [pinned "authorizer" (operatorId, operatorRevision)]
  upgradeId <- allocatedObjectId archiveId namespace "upgrade-0-0-12"
  headBefore <- gitHead root
  (code, out, err) <- transitionAt root "upgrade-0-0-12" [(upgradeId, namespace, "upgrade-0-0-12")]
    [(upgradeId, Nothing), (operatorId, Just operatorRevision)] (upgrade upgradeId)
  headAfter <- gitHead root
  assert "door transition refuses an upgrade to a version it has no catalog for"
    (code /= ExitSuccess && null out && headBefore == headAfter
      && contains "unsupported: unknown specification version archive/0.0.12" err)
    code out err
  _ <- refuseNew root archiveId "door admit refuses a specification upgrade"
    namespace "upgrade-admitted" upgrade
    "unsupported: transition contracts cannot be admitted generically"
  pure ()

-- | A 0.0.6 archive adopts 0.0.11 by one `door transition` (ADR 0068, D-15):
-- every refusal writes nothing, the upgrade is one commit that installs the
-- release and records the new version, a retry changes nothing, Door's
-- checkpoint describes the new commit, and the archive then admits a
-- derivation run, which only 0.0.11 installs.
specificationUpgradeChecks :: FilePath -> IO ()
specificationUpgradeChecks parent = do
  (root, archiveId, initOutput) <- newEarlierArchive parent "specification-upgrade-0-0-6"
  operatorId <- requiredField "operator_id" initOutput
  initialOperatorRevision <- recordRevisionForObject root operatorId
  previousCatalog <- either (const []) toList <$> loadContractCatalog "archive/0.0.6"
  targetCatalog <- either (const []) toList <$> loadContractCatalog "archive/0.0.11"
  let added = [entry | entry <- targetCatalog, entry `notElem` previousCatalog]
      cited identifier = case [(Text.unpack i, Text.unpack v, Text.unpack d) | (i, v, d) <- targetCatalog, i == identifier] of
        [entry] -> pure entry
        found -> assert ("the 0.0.11 catalog lists " <> Text.unpack identifier <> " once") False ExitSuccess (show found) ""
          >> pure ("", "", "")
  upgradeContract <- cited "archive.transition/specification_upgrade"
  runContract <- cited "archive.claim/derivation_run"
  let namespace = "archive.transition/specification_upgrade"
      atSpec specification draft = replace "\"spec\":\"archive/0.0.11\"" ("\"spec\":" <> jsonString specification) draft
      payload from to = "{\"from\":" <> jsonString from <> ",\"to\":" <> jsonString to <> "}"
      upgradeDraft contract from to authorizer =
        contractRecord "claim" contract (payload from to) [pinned "authorizer" authorizer]
      writeUpgrade name draftFor authorizer expectedAuthorizer = do
        objectId <- allocatedObjectId archiveId namespace name
        let draft = draftFor objectId
        proposal <- writeTransitionProposal root name
          (transitionManifestFor ["records/transition.json"] [(objectId, namespace, name)]
            [(objectId, Nothing), (fst authorizer, Just expectedAuthorizer)])
          ["records/transition.json", draft]
        pure (draft, proposal)
      unchangedAfter label action expected = do
        headBefore <- gitHead root
        manifestBefore <- ByteString.readFile (root </> "archive.json")
        (code, out, err) <- action
        headAfter <- gitHead root
        manifestAfter <- ByteString.readFile (root </> "archive.json")
        assert label
          (code /= ExitSuccess && null out && headBefore == headAfter
            && manifestBefore == manifestAfter && contains expected err)
          code out err
      refused label name draftFor authorizer expectedAuthorizer expected = do
        (_, proposal) <- writeUpgrade name draftFor authorizer expectedAuthorizer
        unchangedAfter label (doorCommandAt root ["transition", "--proposal", proposal] "") expected
      withDataDirectory directory proposal = do
        environment <- getEnvironment
        readCreateProcessWithExitCode ((proc "door" ["transition", "--proposal", proposal])
          { cwd = Just root
          , env = Just (("archive_datadir", directory) : filter ((/= "archive_datadir") . fst) environment)
          }) ""

  guest <- admitNew root archiveId "the 0.0.6 archive admits a person who is not the operator"
    "archive.core/entity" "upgrade-guest" (atSpec "archive/0.0.6" . entityRecord "person" "Guest")

  -- The operator's appointed person gains a revision; an upgrade must pin it.
  let revisedOperator = atSpec "archive/0.0.6" (replace "\"supersedes\":[]"
        ("\"supersedes\":[" <> pinned "supersedes" (operatorId, initialOperatorRevision) <> "]")
        (entityRecord "person" "Operator" operatorId))
  operatorRevision <- canonicalRevisionOf revisedOperator
  revisedProposal <- writeAdmissionProposal root "upgrade-operator-revision"
    (manifestFor ["records/operator.json"] [] [(operatorId, Just initialOperatorRevision)] [])
    ["records/operator.json", revisedOperator]
  (reviseCode, reviseOut, reviseErr) <- doorCommandAt root ["admit", "--proposal", revisedProposal] ""
  assert "the upgrade fixture revises the operator's person"
    (reviseCode == ExitSuccess && contains operatorRevision reviseOut) reviseCode reviseOut reviseErr
  assert "the operator's revisions differ, so a stale pin is detectable (anchor)"
    (operatorRevision /= initialOperatorRevision) ExitSuccess operatorRevision initialOperatorRevision
  let operator = (operatorId, operatorRevision)
      staleOperator = (operatorId, initialOperatorRevision)
      upgrade = upgradeDraft upgradeContract "archive/0.0.6" "archive/0.0.11"

  -- Refusals, one per rule; each leaves HEAD and the manifest unchanged.
  refused "an upgrade authorized by a person who is not the operator is refused"
    "upgrade-guest" (upgrade guest) guest (snd guest)
    "transition authorizer is not the active operator"
  refused "an upgrade whose expected head for the authorizer is stale is refused"
    "upgrade-stale-head" (upgrade staleOperator) staleOperator initialOperatorRevision
    "expected head is stale"
  refused "an upgrade pinning a superseded revision of the operator is refused"
    "upgrade-stale-pin" (upgrade staleOperator) staleOperator operatorRevision
    "transition reference must pin the current object head"
  refused "a downgrade is refused"
    "upgrade-downgrade" (atSpec "archive/0.0.5" . upgradeDraft upgradeContract "archive/0.0.6" "archive/0.0.5" operator)
    operator operatorRevision "unsupported: no downgrade exists: archive/0.0.5 is earlier than archive/0.0.6"
  refused "an upgrade to the archive's own version is refused"
    "upgrade-same" (atSpec "archive/0.0.6" . upgradeDraft upgradeContract "archive/0.0.6" "archive/0.0.6" operator)
    operator operatorRevision "unsupported: a specification upgrade must name a version later than archive/0.0.6"
  refused "an upgrade to a version this Door has no catalog for is refused"
    "upgrade-unknown" (atSpec "archive/0.0.12" . upgradeDraft upgradeContract "archive/0.0.6" "archive/0.0.12" operator)
    operator operatorRevision "unsupported: unknown specification version archive/0.0.12"
  refused "an upgrade that does not start at the archive's specification is refused"
    "upgrade-wrong-from" (upgradeDraft upgradeContract "archive/0.0.5" "archive/0.0.11" operator)
    operator operatorRevision "specification upgrade from archive/0.0.5 does not start at the archive's specification archive/0.0.6"
  refused "an upgrade record written under the old specification is refused"
    "upgrade-old-spec" (atSpec "archive/0.0.6" . upgrade operator)
    operator operatorRevision "must be written under its target specification archive/0.0.11"
  let (upgradeIdentifier, upgradeVersion, upgradeDigest) = upgradeContract
      foreignDigest = "sha256:" <> replicate 64 'e'
  assert "the foreign citation differs from the catalog's only in its digest (anchor)"
    (foreignDigest /= upgradeDigest) ExitSuccess foreignDigest upgradeDigest
  refused "an upgrade citing a bundle outside the target catalog is refused as not installed"
    "upgrade-foreign" (upgradeDraft (upgradeIdentifier, upgradeVersion, foreignDigest) "archive/0.0.6" "archive/0.0.11" operator)
    operator operatorRevision "proposed contract is not installed at this snapshot or in the catalog of archive/0.0.11"
  twoRecordsId <- allocatedObjectId archiveId namespace "upgrade-two-a"
  otherRecordId <- allocatedObjectId archiveId namespace "upgrade-two-b"
  twoRecords <- writeTransitionProposal root "upgrade-two-records"
    (transitionManifestFor ["records/a.json", "records/b.json"]
      [(twoRecordsId, namespace, "upgrade-two-a"), (otherRecordId, namespace, "upgrade-two-b")]
      [(twoRecordsId, Nothing), (otherRecordId, Nothing), (operatorId, Just operatorRevision)])
    ["records/a.json", upgrade operator twoRecordsId, "records/b.json", upgrade operator otherRecordId]
  unchangedAfter "an upgrade proposal with two records is refused"
    (doorCommandAt root ["transition", "--proposal", twoRecords] "")
    "a specification upgrade proposal must contain exactly one record"

  -- Non-additive releases, from a copy of Door's specification files.
  let dataDirectory = parent </> "upgrade-datadir"
      dataCatalog = dataDirectory </> "spec" </> "0.0.11" </> "contracts.json"
      dataContext = dataDirectory </> "spec" </> "0.0.11" </> "context.jsonld"
  shellChecked ("mkdir -p " <> quote dataDirectory <> " && cp -R spec " <> quote (dataDirectory </> "spec"))
  (_, additiveProposal) <- writeUpgrade "upgrade-datadir" (upgrade operator) operator operatorRevision
  catalogBytes <- ByteString.readFile dataCatalog
  case decodeUnique (LazyByteString.fromStrict catalogBytes) of
    Right (Aeson.Object fields) | Just (Aeson.Array entries) <- KeyMap.lookup "contracts" fields -> do
      let retireV1 entry = case entry of
            Aeson.Object item -> KeyMap.lookup "id" item == Just (Aeson.String "archive.transition/retire")
              && KeyMap.lookup "version" item == Just (Aeson.String "1")
            _ -> False
          kept = filter (not . retireV1) (toList entries)
      assert "the reduced catalog drops exactly one bundle the 0.0.6 catalog lists (anchor)"
        (length kept + 1 == length entries
          && any (\(i, v, _) -> i == "archive.transition/retire" && v == "1") previousCatalog)
        ExitSuccess (show (length kept)) ""
      removeFile dataCatalog
      LazyByteString.writeFile dataCatalog (encodeCanonical (Aeson.Object
        (KeyMap.insert "contracts" (Aeson.toJSON kept) fields)))
    _ -> assert "the 0.0.11 catalog decodes" False ExitSuccess "" ""
  unchangedAfter "an upgrade to a release whose catalog drops a bundle is refused"
    (withDataDirectory dataDirectory additiveProposal)
    "unsupported: archive/0.0.11 is not additive: its catalog lacks archive.transition/retire version 1"
  removeFile dataCatalog
  ByteString.writeFile dataCatalog catalogBytes
  contextBytes <- readFile dataContext
  let changedContext = replace "\"by\": \"as:generator\"" "\"by\": \"as:actor\"" contextBytes
  assert "the changed context differs from the release's in one mapping (anchor)"
    (length contextBytes == length changedContext + 4 && changedContext /= contextBytes)
    ExitSuccess "" ""
  length contextBytes `seq` removeFile dataContext
  writeFile dataContext changedContext
  unchangedAfter "an upgrade to a release whose core context changes a mapping is refused"
    (withDataDirectory dataDirectory additiveProposal)
    "unsupported: archive/0.0.11 is not additive: its core context drops or changes by"

  -- The dry run predicts the revision and writes nothing.
  (upgradeRecord, proposal) <- writeUpgrade "upgrade-0-0-7" (upgrade operator) operator operatorRevision
  upgradeRevision <- canonicalRevisionOf upgradeRecord
  headBefore <- gitHead root
  manifestBefore <- ByteString.readFile (root </> "archive.json")
  (dryCode, dryOut, dryErr) <- doorCommandAt root ["transition", "--proposal", proposal, "--dry-run"] ""
  headAfterDry <- gitHead root
  manifestAfterDry <- ByteString.readFile (root </> "archive.json")
  assert "the upgrade dry run predicts the revision and writes nothing"
    (dryCode == ExitSuccess && contains "\"status\":\"predicted\"" dryOut && contains upgradeRevision dryOut
      && contains "\"retry\":false" dryOut && headAfterDry == headBefore && manifestAfterDry == manifestBefore)
    dryCode dryOut dryErr

  -- The upgrade: one commit, the record, the release, and the new version.
  -- It commits in this process, so the checkpoint it writes belongs to this
  -- build and can be read back (ADR 0052); the service check below commits
  -- one through `door` itself.
  parsedProposal <- readTransitionProposal proposal >>= either (ioError . userError . Text.unpack) pure
  committed <- commitTransition root parsedProposal
  headAfter <- gitHead root
  assert "door transition commits the specification upgrade"
    (either (const False) (\plan -> plannedTransitionRevisions plan == [Text.pack upgradeRevision]
        && not (transitionRetry plan)) committed
      && headAfter /= headBefore)
    ExitSuccess (show (either Just (const Nothing) committed)) ""
  parentOfUpgrade <- checkedOutput "the upgrade fixture reads the upgrade's parent"
    ["-C", root, "rev-parse", "HEAD~1"]
  changes <- checkedOutput "the upgrade fixture lists the upgrade's changes"
    ["-C", root, "show", "--no-renames", "--name-status", "--format=", "HEAD"]
  let expectedChanges = sort
        ([ "M\tarchive.json"
         , "A\tspec/0.0.11/VERSION"
         , "A\tspec/0.0.11/archive-spec.md"
         , "A\tspec/0.0.11/context.jsonld"
         , "A\t" <> recordRelative upgradeRevision ]
        <> [ "A\tcontracts/sha256-" <> drop 7 (Text.unpack digest) <> ".json" | (_, _, digest) <- added ])
  assert "the upgrade is one commit holding its record, the release's documents and added bundles, and the new manifest"
    (trim parentOfUpgrade == headBefore && sort (lines changes) == expectedChanges && length added > 1)
    ExitSuccess changes (unlines expectedChanges)
  committedManifest <- checkedOutput "the upgrade fixture reads the committed manifest"
    ["-C", root, "show", "HEAD:archive.json"]
  worktreeManifest <- readFile (root </> "archive.json")
  assert "the archive records the new version where it records its version, committed and in the worktree"
    (contains "\"spec\":\"archive/0.0.11\"" committedManifest && worktreeManifest == committedManifest
      && contains "\"spec\":\"archive/0.0.6\"" (LazyByteString.unpack (LazyByteString.fromStrict manifestBefore)))
    ExitSuccess committedManifest worktreeManifest
  installedMatch <- mapM (\(_, _, digest) -> do
      let name = "sha256-" <> drop 7 (Text.unpack digest) <> ".json"
      installed <- ByteString.readFile (root </> "contracts" </> name)
      released <- ByteString.readFile ("spec/contracts" </> name)
      pure (installed == released)) added
  documentMatch <- mapM (\(installed, released) -> (==) <$> ByteString.readFile (root </> installed) <*> ByteString.readFile released)
    [ ("spec/0.0.11/VERSION", "spec/VERSION"), ("spec/0.0.11/archive-spec.md", "spec/archive-spec.md")
    , ("spec/0.0.11/context.jsonld", "spec/0.0.11/context.jsonld") ]
  status <- checkedOutput "the upgrade fixture reads the worktree status"
    ["-C", root, "status", "--porcelain", "--untracked-files=no"]
  assert "the installed bundles and documents are the release's bytes, and the worktree matches the commit"
    (and installedMatch && and documentMatch && null status) ExitSuccess status ""
  (statusCode, statusOut, statusErr) <- readCreateProcessWithExitCode
    ((proc "archive" ["status"]) {cwd = Just root}) ""
  assert "archive status reads the upgraded archive at 0.0.11"
    (statusCode == ExitSuccess && contains "\"spec\":\"archive/0.0.11\"" statusOut) statusCode statusOut statusErr

  -- A complete retry changes nothing.
  (retryCode, retryOut, retryErr) <- doorCommandAt root ["transition", "--proposal", proposal] ""
  headAfterRetry <- gitHead root
  assert "an identical upgrade retry returns the same revision and writes nothing"
    (retryCode == ExitSuccess && contains upgradeRevision retryOut && contains "\"retry\":true" retryOut
      && headAfterRetry == headAfter)
    retryCode retryOut retryErr

  -- Door's checkpoint describes the upgrade commit (ADR 0052): planning on
  -- it needs neither the history walk nor a whole-store check.
  checkpoint <- readCheckpoint root
  let checkpointAdds path = maybe False ((== Just (Text.pack headAfter)) . CheckpointMap.lookup path . checkpointAdditions) checkpoint
  assert "the checkpoint names the upgrade commit and the paths it added"
    (fmap checkpointCommit checkpoint == Just (Text.pack headAfter)
      && checkpointAdds "spec/0.0.11/context.jsonld"
      && all (\(_, _, digest) -> checkpointAdds ("contracts/sha256-" <> drop 7 (Text.unpack digest) <> ".json")) added
      && not (checkpointAdds "archive.json"))
    ExitSuccess (show (fmap checkpointCommit checkpoint)) headAfter
  realGit <- findExecutable "git" >>= maybe (ioError (userError "upgrade fixture cannot find git")) pure
  let bombDirectory = parent </> "upgrade-git"
      bomb = bombDirectory </> "git"
      withBomb action = do
        setEnv "ARCHIVE_GIT_EXECUTABLE" bomb
        result <- action
        setEnv "ARCHIVE_GIT_EXECUTABLE" realGit
        pure result
      runProposal name = do
        objectId <- allocatedObjectId archiveId "archive.derivation/run" name
        let draft = contractRecord "claim" runContract runPayload [] objectId
        path <- writeAdmissionProposal root name (manifestFor ["records/run.json"]
          [(objectId, "archive.derivation/run", name)] [(objectId, Nothing)] []) ["records/run.json", draft]
        parsed <- readAdmissionProposal path >>= either (ioError . userError . Text.unpack) pure
        revision <- canonicalRevisionOf draft
        pure (path, parsed, revision)
  createDirectoryIfMissing True bombDirectory
  writeFile bomb ("#!/bin/sh\nfor argument in \"$@\"; do case \"$argument\" in log|fsck) exit 97;; esac; done\nexec "
    <> quote realGit <> " \"$@\"\n")
  setFileMode bomb 0o755
  (_, firstRun, _) <- runProposal (runName 1)
  forgetSnapshots
  fromCheckpoint <- withBomb (dryRunAdmissionForCommit root firstRun)
  assert "a derivation run plans on the upgraded archive from the checkpoint alone"
    (either (const False) (const True) fromCheckpoint) ExitSuccess (show (either Just (const Nothing) fromCheckpoint)) ""
  removeFile (root </> ".git" </> "archive-door-verified.json")
  forgetSnapshots
  withoutCheckpoint <- withBomb (dryRunAdmissionForCommit root firstRun)
  assert "without the checkpoint the same plan needs the full read (anchor)"
    (either (const True) (const False) withoutCheckpoint) ExitSuccess "" ""

  -- The full verification of the upgraded history, then the checkpointed
  -- path, each admits a derivation run.
  (firstPath, _, firstRevision) <- runProposal (runName 2)
  (admitCode, admitOut, admitErr) <- doorCommandAt root ["admit", "--proposal", firstPath] ""
  assert "the upgraded archive, verified in full, admits a derivation run"
    (admitCode == ExitSuccess && contains firstRevision admitOut) admitCode admitOut admitErr
  (secondPath, _, secondRevision) <- runProposal (runName 3)
  (secondCode, secondOut, secondErr) <- doorCommandAt root ["admit", "--proposal", secondPath] ""
  assert "the upgraded archive, from its checkpoint, admits another derivation run"
    (secondCode == ExitSuccess && contains secondRevision secondOut) secondCode secondOut secondErr
  _ <- admitNew root archiveId "the upgraded archive still admits a record under 0.0.6"
    "archive.core/entity" "upgrade-after-0-0-6" (atSpec "archive/0.0.6" . entityRecord "person" "Earlier")

  -- No downgrade, and no second upgrade from the old version.
  refused "a downgrade of the upgraded archive is refused"
    "downgrade-0-0-6" (atSpec "archive/0.0.6" . upgradeDraft upgradeContract "archive/0.0.11" "archive/0.0.6" operator)
    operator operatorRevision "unsupported: no downgrade exists: archive/0.0.6 is earlier than archive/0.0.11"
  _ <- refuseNew root archiveId "door admit refuses a specification upgrade on the upgraded archive"
    namespace "upgrade-admitted" (upgrade operator)
    "unsupported: transition contracts cannot be admitted generically"
  refused "a second upgrade from the old version is refused"
    "upgrade-again" (upgrade operator) operator operatorRevision
    "specification upgrade from archive/0.0.6 does not start at the archive's specification archive/0.0.11"

  -- The runbook's checks: the whole-store check and Door's full
  -- verification both accept the upgraded history.
  (doctorCode, doctorOut, doctorErr) <- readCreateProcessWithExitCode
    ((proc "archive" ["doctor"]) {cwd = Just root}) ""
  assert "archive doctor passes on the upgraded archive"
    (doctorCode == ExitSuccess) doctorCode doctorOut doctorErr
  removeFile (root </> ".git" </> "archive-door-verified.json")
  (verifyCode, verifyOut, verifyErr) <- doorCommandAt root ["verify"] ""
  -- The checkpoint belongs to the `door` build that wrote it, so only its
  -- presence is checked here.
  verified <- doesFileExist (root </> ".git" </> "archive-door-verified.json")
  assert "door verify verifies the upgraded history in full and writes a checkpoint for it"
    (verifyCode == ExitSuccess && contains "\"status\":\"verified\"" verifyOut && verified)
    verifyCode verifyOut verifyErr
  pure ()

-- | A manifest changed outside a specification upgrade is not an archive
-- Door reads: the full verification refuses the history.
forgedUpgradeChecks :: FilePath -> IO ()
forgedUpgradeChecks parent = do
  (root, archiveId, _) <- newEarlierArchive parent "forged-upgrade"
  previousCatalog <- either (const []) toList <$> loadContractCatalog "archive/0.0.6"
  targetCatalog <- either (const []) toList <$> loadContractCatalog "archive/0.0.11"
  objectId <- allocatedObjectId archiveId "archive.core/entity" "forged-upgrade-person"
  proposal <- writeAdmissionProposal root "forged-upgrade" (manifestFor
    ["records/entity.json"] [(objectId, "archive.core/entity", "forged-upgrade-person")]
    [(objectId, Nothing)] []) ["records/entity.json", entityDraft objectId]
  (beforeCode, beforeOut, beforeErr) <- doorCommandAt root ["admit", "--proposal", proposal, "--dry-run"] ""
  assert "before the forgery the 0.0.6 archive refuses a 0.0.11 record for lack of its context (anchor)"
    (beforeCode /= ExitSuccess && contains "selects a core context this archive does not hold" beforeErr)
    beforeCode beforeOut beforeErr
  manifest <- readFile (root </> "archive.json")
  let upgraded = replace "\"spec\":\"archive/0.0.6\"" "\"spec\":\"archive/0.0.11\"" manifest
  length manifest `seq` removeFile (root </> "archive.json")
  writeFile (root </> "archive.json") upgraded
  createDirectoryIfMissing True (root </> "spec" </> "0.0.11")
  mapM_ (\(from, to) -> ByteString.readFile from >>= ByteString.writeFile (root </> to))
    [ ("spec/VERSION", "spec/0.0.11/VERSION"), ("spec/archive-spec.md", "spec/0.0.11/archive-spec.md")
    , ("spec/0.0.11/context.jsonld", "spec/0.0.11/context.jsonld") ]
  mapM_ (\(_, _, digest) -> do
      let name = "sha256-" <> drop 7 (Text.unpack digest) <> ".json"
      ByteString.readFile ("spec/contracts" </> name) >>= ByteString.writeFile (root </> "contracts" </> name))
    [entry | entry <- targetCatalog, entry `notElem` previousCatalog]
  shellChecked ("git -C " <> quote root <> " add archive.json spec contracts"
    <> " && git -C " <> quote root <> " -c user.name=archive-test -c user.email=archive-test@example.invalid"
    <> " commit -q -m forged")
  (code, out, err) <- doorCommandAt root ["admit", "--proposal", proposal, "--dry-run"] ""
  assert "a manifest changed without an upgrade record is refused as incomplete evidence"
    (code /= ExitSuccess && null out && contains "a specification upgrade commit must introduce exactly one record" err)
    code out err

-- | A forged upgrade commit that looks like Door's in every way but one is
-- refused by the full verification, so no single rule of the upgrade
-- history check can be dropped silently. Each case starts from the same
-- 0.0.6 archive; the first, a complete forgery, is the anchor that the
-- forging itself builds a history Door accepts.
forgedUpgradeHistoryChecks :: FilePath -> IO ()
forgedUpgradeHistoryChecks parent = do
  (root, archiveId, initOutput) <- newEarlierArchive parent "forged-upgrade-history"
  operatorId <- requiredField "operator_id" initOutput
  operatorRevision <- recordRevisionForObject root operatorId
  previousCatalog <- either (const []) toList <$> loadContractCatalog "archive/0.0.6"
  targetCatalog <- either (const []) toList <$> loadContractCatalog "archive/0.0.11"
  let added = [entry | entry <- targetCatalog, entry `notElem` previousCatalog]
      asString (identifier, version, digest) = (Text.unpack identifier, Text.unpack version, Text.unpack digest)
      cited identifier = case [asString entry | entry@(i, _, _) <- targetCatalog, i == identifier] of
        entry : _ -> entry
        [] -> ("", "", "")
      upgradeContract = cited "archive.transition/specification_upgrade"
      runContract = cited "archive.claim/derivation_run"
      bundleName (_, _, digest) = "sha256-" <> drop 7 (Text.unpack digest) <> ".json"
      isRun (identifier, _, _) = identifier == "archive.claim/derivation_run"
      namespace = "archive.transition/specification_upgrade"
      atSpec specification = replace "\"spec\":\"archive/0.0.11\"" ("\"spec\":" <> jsonString specification)
      payload from to = "{\"from\":" <> jsonString from <> ",\"to\":" <> jsonString to <> "}"
      commitAll message = shellChecked ("git -C " <> quote root <> " add -A archive.json spec contracts records"
        <> " && git -C " <> quote root <> " -c user.name=archive-test -c user.email=archive-test@example.invalid"
        <> " commit -q -m " <> quote message)
      writeRecord draft = do
        value <- either (\problem -> assert "a forged record decodes" False ExitSuccess problem "" >> pure Aeson.Null)
          pure (decodeUnique (LazyByteString.pack draft))
        case finalizeStored "2026-01-01T00:00:00Z" value of
          Left problem -> assert "a forged record finalizes" False ExitSuccess (Text.unpack problem) ""
          Right (revision, stored) -> do
            let relative = recordRelative (Text.unpack revision)
            createDirectoryIfMissing True (takeDirectory (root </> relative))
            LazyByteString.writeFile (root </> relative) (encodeCanonical stored)
      upgradeRecord name from to specification = do
        objectId <- allocatedObjectId archiveId namespace name
        pure (atSpec specification (contractRecord "claim" upgradeContract (payload from to)
          [pinned "authorizer" (operatorId, operatorRevision)] objectId))
      -- Everything Door's upgrade commit writes, each part adjustable.
      forgeUpgrade name adjustManifest record bundles = do
        manifest <- ByteString.readFile (root </> "archive.json")
        removeFile (root </> "archive.json")
        writeFile (root </> "archive.json") (adjustManifest (replace "\"spec\":\"archive/0.0.6\""
          "\"spec\":\"archive/0.0.11\"" (LazyByteString.unpack (LazyByteString.fromStrict manifest))))
        createDirectoryIfMissing True (root </> "spec" </> "0.0.11")
        mapM_ (\(from, to) -> ByteString.readFile from >>= ByteString.writeFile (root </> to))
          [ ("spec/VERSION", "spec/0.0.11/VERSION"), ("spec/archive-spec.md", "spec/0.0.11/archive-spec.md")
          , ("spec/0.0.11/context.jsonld", "spec/0.0.11/context.jsonld") ]
        mapM_ (\entry -> ByteString.readFile ("spec/contracts" </> bundleName entry)
          >>= ByteString.writeFile (root </> "contracts" </> bundleName entry)) bundles
        record >>= writeRecord
        commitAll name
      complete name = forgeUpgrade name id (upgradeRecord name "archive/0.0.6" "archive/0.0.11" "archive/0.0.11") added
  base <- gitHead root
  entityId <- allocatedObjectId archiveId "archive.core/entity" "forged-history-person"
  proposal <- writeAdmissionProposal root "forged-history" (manifestFor
    ["records/entity.json"] [(entityId, "archive.core/entity", "forged-history-person")]
    [(entityId, Nothing)] []) ["records/entity.json", atSpec "archive/0.0.6" (entityDraft entityId)]
  let reset = shellChecked ("git -C " <> quote root <> " reset -q --hard " <> base
        <> " && git -C " <> quote root <> " clean -fdq")
      readsAfter :: String -> IO () -> Maybe String -> IO ()
      readsAfter label forge expected = do
        forge
        (code, out, err) <- doorCommandAt root ["admit", "--proposal", proposal, "--dry-run"] ""
        assert label
          (case expected of
            Nothing -> code == ExitSuccess && contains "\"status\":\"predicted\"" out
            Just message -> code /= ExitSuccess && null out && contains message err)
          code out err
        reset

  readsAfter "a complete forged upgrade is read like Door's own (anchor)"
    (complete "forged-complete") Nothing
  readsAfter "an upgrade record whose from is not the manifest's previous specification is refused"
    (forgeUpgrade "forged-from" id (upgradeRecord "forged-from" "archive/0.0.5" "archive/0.0.11" "archive/0.0.11") added)
    (Just "a specification upgrade record does not name the manifest change it made")
  readsAfter "an upgrade record written under the old specification is refused"
    (forgeUpgrade "forged-spec" id (upgradeRecord "forged-spec" "archive/0.0.6" "archive/0.0.11" "archive/0.0.6") added)
    (Just "a specification upgrade record does not name the manifest change it made")
  readsAfter "an upgrade commit that is not an upgrade claim is refused"
    (forgeUpgrade "forged-claim" id (pure (contractRecord "claim" runContract runPayload []
      "urn:uuid:00000000-0000-4000-8000-0000000000f1")) added)
    (Just "a specification upgrade commit's record is not one upgrade claim")
  readsAfter "an upgrade that changes the archive identifier is refused"
    (forgeUpgrade "forged-identifier" (replace archiveId "urn:uuid:00000000-0000-4000-8000-0000000000f2")
      (upgradeRecord "forged-identifier" "archive/0.0.6" "archive/0.0.11" "archive/0.0.11") added)
    (Just "a specification upgrade changed the archive identifier or layout")
  readsAfter "an upgrade record committed outside an upgrade commit is refused"
    (complete "forged-first" >> upgradeRecord "forged-second" "archive/0.0.6" "archive/0.0.11" "archive/0.0.11"
      >>= writeRecord >> commitAll "forged-second")
    (Just "a specification upgrade record exists outside a specification upgrade commit")
  readsAfter "a bundle the root commit installed ahead of its release is refused"
    (do
      mapM_ (\entry -> ByteString.readFile ("spec/contracts" </> bundleName entry)
        >>= ByteString.writeFile (root </> "contracts" </> bundleName entry)) (filter isRun added)
      shellChecked ("git -C " <> quote root <> " add contracts && git -C " <> quote root
        <> " -c user.name=archive-test -c user.email=archive-test@example.invalid commit -q --amend --no-edit")
      forgeUpgrade "forged-early-bundle" id
        (upgradeRecord "forged-early-bundle" "archive/0.0.6" "archive/0.0.11" "archive/0.0.11")
        (filter (not . isRun) added))
    (Just "did not add exactly the bundles its catalog adds")
  readsAfter "a record citing a bundle before the upgrade installed it is refused"
    (do
      writeRecord (atSpec "archive/0.0.6" (contractRecord "claim" runContract runPayload []
        "urn:uuid:00000000-0000-4000-8000-0000000000f3"))
      commitAll "forged-early-citation"
      complete "forged-late-upgrade")
    (Just "before the archive held it")
  readsAfter "a record written under a specification before the upgrade installed it is refused"
    (do
      writeRecord (entityDraft "urn:uuid:00000000-0000-4000-8000-0000000000f4")
      commitAll "forged-early-specification"
      complete "forged-late-specification")
    (Just "before the archive held its core context")

-- | The service commits an upgrade like any transition, and then plans
-- against the new release without a restart (ADR 0054).
specificationUpgradeServiceChecks :: FilePath -> IO ()
specificationUpgradeServiceChecks parent = do
  (root, archiveId, initOutput) <- newEarlierArchive parent "upgrade-service"
  operatorId <- requiredField "operator_id" initOutput
  operatorRevision <- recordRevisionForObject root operatorId
  targetCatalog <- either (const []) toList <$> loadContractCatalog "archive/0.0.11"
  let cited identifier = case [(Text.unpack i, Text.unpack v, Text.unpack d) | (i, v, d) <- targetCatalog, i == identifier] of
        entry : _ -> entry
        [] -> ("", "", "")
      namespace = "archive.transition/specification_upgrade"
      socketDirectory = parent </> "upgrade-service-socket"
      socketPath = socketDirectory </> "door.sock"
      request command proposal = do
        connection <- Socket.socket Socket.AF_UNIX Socket.Stream Socket.defaultProtocol
        Socket.connect connection (Socket.SockAddrUnix socketPath)
        SocketBytes.sendAll connection (LazyByteString.toStrict (Aeson.encode (Aeson.object
          ["command" .= (command :: Text.Text), "proposal" .= proposal])) <> "\n")
        answer <- receiveAll connection ByteString.empty
        Socket.close connection
        pure (LazyByteString.unpack (LazyByteString.fromStrict answer))
      receiveAll connection collected = do
        chunk <- SocketBytes.recv connection 65536
        if ByteString.null chunk then pure collected else receiveAll connection (collected <> chunk)
      waitForSocket tries = do
        present <- doesPathExist socketPath
        if present || tries <= (0 :: Int) then pure present
          else threadDelay 50000 >> waitForSocket (tries - 1)
  upgradeId <- allocatedObjectId archiveId namespace "service-upgrade"
  let upgradeRecord = contractRecord "claim" (cited "archive.transition/specification_upgrade")
        "{\"from\":\"archive/0.0.6\",\"to\":\"archive/0.0.11\"}" [pinned "authorizer" (operatorId, operatorRevision)] upgradeId
  upgradeRevision <- canonicalRevisionOf upgradeRecord
  upgradeProposal <- writeTransitionProposal root "service-upgrade"
    (transitionManifestFor ["records/transition.json"] [(upgradeId, namespace, "service-upgrade")]
      [(upgradeId, Nothing), (operatorId, Just operatorRevision)])
    ["records/transition.json", upgradeRecord]
  runId <- allocatedObjectId archiveId "archive.derivation/run" (runName 7)
  let runRecord = contractRecord "claim" (cited "archive.claim/derivation_run") runPayload [] runId
  runRevision <- canonicalRevisionOf runRecord
  runProposal <- writeAdmissionProposal root "service-run" (manifestFor ["records/run.json"]
    [(runId, "archive.derivation/run", runName 7)] [(runId, Nothing)] []) ["records/run.json", runRecord]
  createDirectoryIfMissing True socketDirectory
  setFileMode socketDirectory 0o700
  withCreateProcess (proc "door" ["serve", "--socket", socketPath])
    { cwd = Just root, std_out = NoStream, std_err = CreatePipe } $ \_ _ _ _ -> do
    started <- waitForSocket 100
    beforeRun <- request "admit-dry-run" runProposal
    assert "the service refuses a derivation run before the upgrade (anchor)"
      (started && contains "\"ok\":false" beforeRun) ExitSuccess beforeRun ""
    upgraded <- request "transition" upgradeProposal
    assert "the service commits the specification upgrade"
      (contains "\"ok\":true" upgraded && contains upgradeRevision upgraded) ExitSuccess upgraded ""
    admitted <- request "admit" runProposal
    assert "the service admits a derivation run after the upgrade without a restart"
      (contains "\"ok\":true" admitted && contains runRevision admitted) ExitSuccess admitted ""

-- | A Door killed after its upgrade commit advanced the branch, and before
-- it wrote the worktree, leaves its operation journal. The next Door
-- recovers it: it verifies the committed manifest and release against the
-- journal, writes their worktree copies from the commit, refreshes the
-- ordinary index to it, and answers the identical proposal as a retry; a
-- later write then commits without the index refreshed by hand.
specificationUpgradeRecoveryChecks :: FilePath -> IO ()
specificationUpgradeRecoveryChecks parent = do
  (root, archiveId, initOutput) <- newEarlierArchive parent "upgrade-recovery"
  operatorId <- requiredField "operator_id" initOutput
  operatorRevision <- recordRevisionForObject root operatorId
  targetCatalog <- either (const []) toList <$> loadContractCatalog "archive/0.0.11"
  let upgradeContract = case [ (Text.unpack i, Text.unpack v, Text.unpack d)
                             | (i, v, d) <- targetCatalog, i == "archive.transition/specification_upgrade" ] of
        entry : _ -> entry
        [] -> ("", "", "")
      namespace = "archive.transition/specification_upgrade"
      journal = root </> ".git" </> "archive-door-operation"
  upgradeId <- allocatedObjectId archiveId namespace "recovered-upgrade"
  let upgradeRecord = contractRecord "claim" upgradeContract
        "{\"from\":\"archive/0.0.6\",\"to\":\"archive/0.0.11\"}" [pinned "authorizer" (operatorId, operatorRevision)] upgradeId
  upgradeRevision <- canonicalRevisionOf upgradeRecord
  proposal <- writeTransitionProposal root "recovered-upgrade"
    (transitionManifestFor ["records/transition.json"] [(upgradeId, namespace, "recovered-upgrade")]
      [(upgradeId, Nothing), (operatorId, Just operatorRevision)])
    ["records/transition.json", upgradeRecord]
  realGit <- findExecutable "git" >>= maybe
    (ioError (userError "upgrade recovery fixture cannot find git")) pure
  let fakeBin = parent </> "upgrade-recovery-fake-bin"
      fakeGit = fakeBin </> "git"
  createDirectoryIfMissing True fakeBin
  writeFile fakeGit
    ("#!/bin/sh\nupdate=0\nfor argument in \"$@\"; do\n"
      <> "  if [ \"$argument\" = update-ref ]; then update=1; fi\n"
      <> "done\n" <> quote realGit <> " \"$@\"\nstatus=$?\n"
      <> "if [ \"$update\" = 1 ] && [ \"$status\" = 0 ]; then\n"
      <> "  kill -KILL \"$PPID\"\n  sleep 1\n"
      <> "fi\nexit \"$status\"\n")
  setFileMode fakeGit 0o755
  environment <- getEnvironment
  headBefore <- gitHead root
  _ <- readCreateProcessWithExitCode ((proc "door" ["transition", "--proposal", proposal])
    { cwd = Just root
    , env = Just (("ARCHIVE_GIT_EXECUTABLE", fakeGit) : filter ((/= "ARCHIVE_GIT_EXECUTABLE") . fst) environment)
    }) ""
  headAfterKill <- gitHead root
  journalAfterKill <- doesPathExist journal
  manifestAfterKill <- readFile (root </> "archive.json")
  committedManifest <- checkedOutput "the recovery fixture reads the committed manifest"
    ["-C", root, "show", "HEAD:archive.json"]
  indexAtOld <- indexMatches root headBefore
  indexAtKilled <- indexMatches root headAfterKill
  assert "a Door killed after the upgrade's ref update leaves the commit, its journal, and the old worktree manifest and index"
    (headAfterKill /= headBefore && journalAfterKill
      && contains "\"spec\":\"archive/0.0.6\"" manifestAfterKill
      && contains "\"spec\":\"archive/0.0.11\"" committedManifest
      && indexAtOld && not indexAtKilled)
    ExitSuccess manifestAfterKill committedManifest
  length manifestAfterKill `seq` pure ()
  (code, out, err) <- doorCommandAt root ["transition", "--proposal", proposal] ""
  headAfter <- gitHead root
  journalAfter <- doesPathExist journal
  manifestAfter <- readFile (root </> "archive.json")
  context <- ByteString.readFile (root </> "spec" </> "0.0.11" </> "context.jsonld")
  released <- ByteString.readFile ("spec" </> "0.0.11" </> "context.jsonld")
  indexAfter <- indexMatches root headAfter
  assert "the next Door recovers the upgrade: the worktree and the index follow the commit and the proposal answers as a retry"
    (code == ExitSuccess && contains upgradeRevision out && contains "\"retry\":true" out
      && headAfter == headAfterKill && not journalAfter
      && manifestAfter == committedManifest && context == released && indexAfter)
    code out err
  (nextCode, nextOut, nextErr, nextAnnex) <- admitFreshEntity parent root archiveId "upgrade-recovery-next"
  nextHead <- gitHead root
  nextIndex <- indexMatches root nextHead
  assert "a write after the recovered upgrade commits without the index refreshed by hand"
    (nextCode == ExitSuccess && null nextErr && not nextAnnex && nextHead /= headAfter && nextIndex)
    nextCode nextOut nextErr

-- | The additive rule of ADR 0068, the layouts it compares, and the paths an
-- upgrade may write, without an archive. The real 0.0.6 and 0.0.11 releases
-- are the anchor; each refusal changes one thing about the target.
specificationUpgradeRuleChecks :: IO ()
specificationUpgradeRuleChecks = do
  previousCatalog <- either (const Set.empty) id <$> loadContractCatalog "archive/0.0.6"
  targetCatalog <- either (const Set.empty) id <$> loadContractCatalog "archive/0.0.11"
  let contextOf path = do
        bytes <- LazyByteString.readFile path
        pure $ case decodeUnique bytes of
          Right (Aeson.Object outer) | Just (Aeson.Object inner) <- KeyMap.lookup "@context" outer -> inner
          _ -> KeyMap.empty
  previousContext <- contextOf "spec/0.0.6/context.jsonld"
  targetContext <- contextOf "spec/0.0.11/context.jsonld"
  let previous = SpecificationRelease "archive/0.0.6" previousCatalog "archive/2" previousContext
      target = SpecificationRelease "archive/0.0.11" targetCatalog "archive/2" targetContext
      refusedWith label expected candidate = assert label
        (either (contains expected . Text.unpack) (const False) (additiveUpgrade previous candidate))
        ExitSuccess (show (additiveUpgrade previous candidate)) expected
      oneEntry = Set.findMin previousCatalog
      (oneId, oneVersion, _) = oneEntry
  assert "the real 0.0.6 to 0.0.11 upgrade is additive (anchor)"
    (additiveUpgrade previous target == Right () && not (Set.null previousCatalog)
      && previousCatalog `Set.isProperSubsetOf` targetCatalog && not (KeyMap.null previousContext))
    ExitSuccess (show (additiveUpgrade previous target)) ""
  refusedWith "a target catalog lacking a current bundle is not additive" "its catalog lacks"
    target { releaseCatalog = Set.delete oneEntry targetCatalog }
  refusedWith "a target catalog giving a current bundle other bytes is not additive" "its catalog lacks"
    target { releaseCatalog = Set.insert (oneId, oneVersion, Text.pack (digestOf 'f')) (Set.delete oneEntry targetCatalog) }
  refusedWith "a target catalog listing one version under two digests is not additive" "different bytes"
    target { releaseCatalog = Set.insert (oneId, oneVersion, Text.pack (digestOf 'f')) targetCatalog }
  refusedWith "a target with another layout is not additive" "its layout archive/1"
    target { releaseLayout = "archive/1" }
  refusedWith "a target core context that changes a mapping is not additive" "drops or changes label"
    target { releaseContext = KeyMap.insert "label" (Aeson.String "dcterms:title") targetContext }
  refusedWith "a target core context that drops a term is not additive" "drops or changes label"
    target { releaseContext = KeyMap.delete "label" targetContext }
  assert "a target core context may define more terms"
    (additiveUpgrade previous target { releaseContext = KeyMap.insert "extra_term" (Aeson.String "arch:extra") targetContext } == Right ())
    ExitSuccess "" ""
  assert "each known release has its layout, and a later one is not guessed"
    (map specificationLayout ["archive/0.0.5", "archive/0.0.6", "archive/0.0.7", "archive/0.0.8", "archive/0.0.9", "archive/0.0.10", "archive/0.0.11"] == [Right "archive/1", Right "archive/2", Right "archive/2", Right "archive/2", Right "archive/2", Right "archive/2", Right "archive/2"]
      && isLeft (specificationLayout "archive/0.0.12"))
    ExitSuccess "" ""
  assert "specification versions order numerically, not as text"
    (compareSpecifications "archive/0.0.11" "archive/0.0.9" == Right GT
      && compareSpecifications "archive/0.0.6" "archive/0.0.9" == Right LT
      && isLeft (compareSpecifications "archive/0.0" "archive/0.0.9"))
    ExitSuccess "" ""
  assert "an upgrade writes only the manifest, a release's three documents, and digest-named bundles"
    (all (isRight . validateArchiveFilePath)
        [ "archive.json", "spec/0.0.11/VERSION", "spec/0.0.11/archive-spec.md", "spec/0.0.11/context.jsonld"
        , "contracts/sha256-" <> replicate 64 'a' <> ".json" ]
      && all (isLeft . validateArchiveFilePath)
        [ "records/00/00/sha256-" <> replicate 64 '0' <> ".json", "spec/0.0.11/notes.md", "spec/latest/VERSION"
        , "contracts/sha256-" <> replicate 64 'A' <> ".json", "contracts/other.json", "../archive.json"
        , "./archive.json", "content/sha256/aa/aa/" <> replicate 64 'a' ])
    ExitSuccess "" ""

-- | A version 3 match's payload: the version 2 fields, the suggestions'
-- rate, and a window.
windowMatchPayload :: Integer -> Integer -> Integer -> String
windowMatchPayload rate start end =
  "{\"direction\":\"lower_is_closer\",\"method\":{\"id\":\"essentia-serra09\",\"version\":\"1\"},\"sample_rate\":"
  <> show rate <> ",\"scale\":\"distance\",\"score\":\"0.0612\",\"window\":{\"end_sample\":" <> show end
  <> ",\"start_sample\":" <> show start <> "}}"

-- | A work match version 3 compares a window of whole suggested spans
-- (ADR 0072, D-16), through the real Door: every fixture record is admitted
-- by Door's writer, each rule refuses its own violation and writes nothing,
-- and an admitted match is read back by `archive export`.
windowMatchAdmissionChecks :: FilePath -> IO ()
windowMatchAdmissionChecks parent = do
  (root, archiveId, _) <- newProposalArchive parent "window-matches"
  let artifactFixture name digest = do
        objectId <- allocatedObjectId archiveId "archive.core/artifact" name
        revision <- commitArtifactFixtureWithDigest root objectId digest
        pure (objectId, revision)
  audio <- artifactFixture "window-audio" zeroDigest
  other <- artifactFixture "window-other" (digestOf '1')
  parameters <- artifactFixture "window-parameters" (digestOf '2')
  work <- admitNew root archiveId "door admit commits a musical_work for window matches"
    "archive.audio/song" "song-2c3d4e5f-6a7b-4c8d-9e0f-1a2b3c4d5e6f" (entityRecord "musical_work" "Window Work")
  runContract <- contractCitationVersion root "archive.claim/derivation_run" "1"
  run <- admitNew root archiveId "door admit commits a run for window matches"
    "archive.derivation/run" (runName 1) (contractRecord "claim" runContract runPayload [pinned "used" audio])
  streamContract <- contractCitationVersion root "archive.claim/audio_stream" "1"
  stream <- admitNew root archiveId "door admit commits the stream the suggestions measure"
    "archive.claim/audio_stream" "window-stream"
    (contractRecord "claim" streamContract (streamPayload 48000 480000 2) [pinned "artifact" audio, pinned "run" run])
  suggestionsContract <- contractCitationVersion root "archive.claim/span_suggestions" "1"
  suggestions <- admitNew root archiveId "door admit commits the suggestions a window is cut from"
    "archive.claim/span_suggestions" "window-suggestions"
    (contractRecord "claim" suggestionsContract
      (spansPayload 48000 [(0, 1000, "music"), (1000, 5000, "talk"), (6000, 480000, "quiet")])
      [pinned "source" audio, pinned "stream" stream, pinned "run" run])
  selectionContract <- contractCitationVersion root "archive.claim/audio_selection" "2"
  occurrenceContract <- contractCitationVersion root "archive.claim/song_occurrence" "2"
  let corpusOccurrence name = do
        selection <- admitNew root archiveId ("door admit commits the selection of " <> name)
          "archive.claim/audio_selection" (name <> "-selection")
          (contractRecord "claim" selectionContract (tracksPayload [[(10, 200, 48000)]]) [pinned "piece" other])
        admitNew root archiveId ("door admit commits " <> name)
          "archive.claim/song_occurrence" name
          (contractRecord "claim" occurrenceContract "{}" [pinned "selection" selection])
  corpus <- corpusOccurrence "window-corpus"
  corpusTwo <- corpusOccurrence "window-corpus-two"
  matchContract <- contractCitationVersion root "archive.claim/work_match" "3"
  let windowRefs compared corpusRefs run' =
        [pinned "suggestions" compared, pinned "work" work] <> map (pinned "corpus") corpusRefs
          <> [pinned "parameters" parameters, pinned "run" run']
      windowDraft payload refs = contractRecord "claim" matchContract payload refs
      window start end = windowDraft (windowMatchPayload 48000 start end) (windowRefs suggestions [corpus, corpusTwo] run)
      refusedWindow label name draftFor expected = do
        headBefore <- gitHead root
        draft <- refuseNew root archiveId label "archive.claim/work_match" name draftFor expected
        headAfter <- gitHead root
        assert (label <> " and writes nothing") (headBefore == headAfter) ExitSuccess headBefore headAfter
        pure draft

  admitted <- admitNew root archiveId "a window of two touching suggested spans is admitted"
    "archive.claim/work_match" "window-two-spans" (window 0 5000)
  _ <- admitNew root archiveId "a window of one suggested span ending at the stream's end is admitted"
    "archive.claim/work_match" "window-last-span" (window 6000 480000)
  _ <- admitNew root archiveId "a window across a gap between suggested spans is admitted"
    "archive.claim/work_match" "window-every-span" (window 0 480000)

  inside <- refusedWindow "a window starting inside a suggested span is refused" "window-start-inside"
    (window 500 5000) "window start_sample must be the start_sample of one of the suggestions claim's spans"
  assert "the inside fixture moves only the window's start"
    (contains "\"start_sample\":500}" inside && contains "\"end_sample\":5000," inside) ExitSuccess inside ""
  _ <- refusedWindow "a window starting at a span's end in a gap is refused" "window-start-gap"
    (window 5000 480000) "window start_sample must be the start_sample of one of the suggestions claim's spans"
  _ <- refusedWindow "a window ending inside a suggested span is refused" "window-end-inside"
    (window 0 4000) "window end_sample must be the end_sample of its first span or of a later one"
  _ <- refusedWindow "a window ending at an earlier span's end is refused" "window-end-earlier"
    (window 1000 1000) "window end_sample must be the end_sample of its first span or of a later one"
  _ <- refusedWindow "a window ending past the last span is refused" "window-end-past"
    (window 0 480001) "window end_sample must be the end_sample of its first span or of a later one"
  rate <- refusedWindow "a window at a rate other than the suggestions' is refused" "window-rate"
    (windowDraft (windowMatchPayload 44100 0 5000) (windowRefs suggestions [corpus] run))
    "sample_rate must equal the suggestions claim's sample_rate"
  assert "the rate fixture changes only sample_rate"
    (contains "\"sample_rate\":44100," rate) ExitSuccess rate ""
  _ <- refusedWindow "a window of a claim that is not span suggestions is refused" "window-not-suggestions"
    (windowDraft (windowMatchPayload 48000 0 5000) (windowRefs stream [corpus] run))
    "suggestions reference must target a Claim citing archive.claim/span_suggestions version 1"
  _ <- refusedWindow "a window match refuses a run that is not a derivation run" "window-not-run"
    (windowDraft (windowMatchPayload 48000 0 5000) (windowRefs suggestions [corpus] stream))
    "run reference must target a Claim citing archive.claim/derivation_run version 1"
  _ <- refusedWindow "a window match naming one corpus occurrence twice is refused" "window-corpus-twice"
    (windowDraft (windowMatchPayload 48000 0 5000) (windowRefs suggestions [corpus, corpus] run))
    "corpus references must name different objects"
  _ <- refusedWindow "a window match without a corpus is refused" "window-no-corpus"
    (windowDraft (windowMatchPayload 48000 0 5000) (windowRefs suggestions [] run))
    "contract instance does not satisfy its schema"
  _ <- refusedWindow "a window match without its window is refused" "window-no-window"
    (windowDraft "{\"direction\":\"lower_is_closer\",\"method\":{\"id\":\"essentia-serra09\",\"version\":\"1\"},\"sample_rate\":48000,\"scale\":\"distance\",\"score\":\"0.0612\"}"
      (windowRefs suggestions [corpus] run))
    "contract instance does not satisfy its schema"
  _ <- refusedWindow "a version 3 match cannot compare an occurrence" "window-occurrence"
    (\objectId -> contractRecord "claim" matchContract (windowMatchPayload 48000 0 5000)
      ([pinned "occurrence" corpus] <> windowRefs suggestions [corpusTwo] run) objectId)
    "contract instance does not satisfy its schema"

  (exportCode, exportOut, exportErr) <- readCreateProcessWithExitCode ((proc "archive" ["export"]) {cwd = Just root}) ""
  assert "archive export reads back the admitted window match with its window and suggestions"
    (exportCode == ExitSuccess && contains (snd admitted) exportOut
      && contains "\"window\":{\"end_sample\":5000,\"start_sample\":0}" exportOut
      && contains ("\"revision_id\":\"" <> snd suggestions <> "\",\"role\":\"suggestions\"") exportOut)
    exportCode (take 400 exportOut) exportErr

-- | The span window rule is checked when a bundle loads: each malformed rule
-- is refused, and the released version 3 bundle accepts a valid instance.
windowMatchProfileChecks :: IO ()
windowMatchProfileChecks = do
  catalog <- either (const []) toList <$> loadContractCatalog "archive/0.0.9"
  schema <- case [digest | ("archive.claim/work_match", "3", digest) <- catalog] of
    [digest] -> do
      bytes <- readFile ("spec/contracts/sha256-" <> drop 7 (Text.unpack digest) <> ".json")
      case decodeUnique (LazyByteString.pack bytes) of
        Right (Aeson.Object fields) | Just value <- KeyMap.lookup "schema" fields -> pure value
        _ -> assert "the work_match 3 bundle decodes" False ExitSuccess bytes "" >> pure Aeson.Null
    found -> assert "the 0.0.9 catalog lists work_match version 3 once" False ExitSuccess (show found) "" >> pure Aeson.Null
  let ref role suffix = Aeson.object
        [ "role" .= (role :: Text.Text)
        , "id" .= ("urn:uuid:00000000-0000-4000-8000-0000000000" <> suffix :: Text.Text)
        , "revision_id" .= Text.pack zeroDigest ]
      decoded text = either (const Aeson.Null) id (decodeUnique (LazyByteString.pack text))
      valid = Aeson.object
        [ "payload" .= decoded (windowMatchPayload 48000 0 5000)
        , "refs" .= [ref "suggestions" "31", ref "work" "32", ref "corpus" "33", ref "corpus" "34", ref "run" "35"] ]
      rule = case schema of
        Aeson.Object fields | Just (Aeson.Object value) <- KeyMap.lookup "x-archive-span-window" fields -> value
        _ -> KeyMap.empty
      withRule value = case schema of
        Aeson.Object fields -> Aeson.Object (KeyMap.insert "x-archive-span-window" value fields)
        _ -> schema
      refusedOnLoad label candidate = assert label (isLeft (validateContractInstance candidate valid)) ExitSuccess
        (show (validateContractInstance candidate valid)) ""
  assert "the released work_match 3 bundle carries a span window rule and accepts a valid instance (anchor)"
    (not (KeyMap.null rule) && validateContractInstance schema valid == Right ())
    ExitSuccess (show (validateContractInstance schema valid)) ""
  refusedOnLoad "a span window rule missing its spans field is refused"
    (withRule (Aeson.Object (KeyMap.delete "spans" rule)))
  refusedOnLoad "a span window rule with an extra key is refused"
    (withRule (Aeson.Object (KeyMap.insert "count" (Aeson.String "sample_count") rule)))
  refusedOnLoad "a span window rule naming one bound field twice is refused"
    (withRule (Aeson.Object (KeyMap.insert "end" (Aeson.String "start_sample") rule)))
  refusedOnLoad "a span window rule whose role names many Claims is refused"
    (withRule (Aeson.Object (KeyMap.insert "role" (Aeson.String "corpus") rule)))
  refusedOnLoad "a span window rule whose role names an Entity is refused"
    (withRule (Aeson.Object (KeyMap.insert "role" (Aeson.String "work") rule)))
  refusedOnLoad "a span window rule whose role has no target rule is refused"
    (withRule (Aeson.Object (KeyMap.insert "role" (Aeson.String "window") rule)))
  refusedOnLoad "a span window rule with an empty field name is refused"
    (withRule (Aeson.Object (KeyMap.insert "spans" (Aeson.String "") rule)))
  refusedOnLoad "a span window rule that is not an object is refused"
    (withRule (Aeson.String "spans"))
  case schema of
    Aeson.Object fields | Just (Aeson.Object properties) <- KeyMap.lookup "properties" fields ->
      refusedOnLoad "a span window rule below the schema root is refused"
        (Aeson.Object (KeyMap.insert "properties"
          (Aeson.Object (KeyMap.insert "x-archive-span-window" (Aeson.Object rule) properties))
          (KeyMap.delete "x-archive-span-window" fields)))
    _ -> assert "the work_match 3 schema has properties" False ExitSuccess "" ""

-- | Specification 0.0.9's bundles and profile rules (D-17), without an
-- archive. The released bundles accept the fixture plan's own records (the
-- anchors); each malformed rule, keyword, or target rule is refused when the
-- bundle loads; and each new rule refuses its own violation. Door's agreement
-- with World on admission, through the real `door admit`, is tested by
-- world/tests/test_door_agreement.py on records Door admitted.
musicAndCreditProfileChecks :: IO ()
musicAndCreditProfileChecks = do
  catalog <- either (const []) toList <$> loadContractCatalog "archive/0.0.9"
  planBytes <- LazyByteString.readFile "fixture/plan.json"
  let decodedPlan = either (const Aeson.Null) id (decodeUnique planBytes)
      planRecords = case decodedPlan of
        Aeson.Object plan | Just (Aeson.Array steps) <- KeyMap.lookup "steps" plan ->
          [ record | Aeson.Object step <- toList steps, Just (Aeson.Array records) <- [KeyMap.lookup "records" step]
                   , Aeson.Object record <- toList records ]
        _ -> []
      planned key = case [record | record <- planRecords, KeyMap.lookup "key" record == Just (Aeson.String key)] of
        [record] -> record
        _ -> KeyMap.empty
      -- The plan's record with every pin replaced by a well-formed reference.
      instanceOf key = Aeson.object
        [ "payload" .= maybe Aeson.Null id (KeyMap.lookup "payload" (planned key))
        , "refs" .= [ Aeson.object [ "role" .= role
                                   , "id" .= ("urn:uuid:00000000-0000-4000-8000-0000000000" <> Text.pack (show (10 + index :: Int)) :: Text.Text)
                                   , "revision_id" .= Text.pack zeroDigest ]
                    | (index, Aeson.Object ref) <- zip [0 ..] (case KeyMap.lookup "refs" (planned key) of
                        Just (Aeson.Array refs) -> toList refs
                        _ -> [])
                    , Just (Aeson.String role) <- [KeyMap.lookup "role" ref] ] ]
      schemaOf identifier = do
        case [digest | (identifier', "1", digest) <- catalog, identifier' == identifier] of
          [digest] -> do
            bytes <- LazyByteString.readFile ("spec/contracts/sha256-" <> drop 7 (Text.unpack digest) <> ".json")
            case decodeUnique bytes of
              Right (Aeson.Object fields) | Just schema <- KeyMap.lookup "schema" fields -> pure schema
              _ -> assert ("the bundle of " <> Text.unpack identifier <> " decodes") False ExitSuccess "" "" >> pure Aeson.Null
          found -> assert ("the 0.0.9 catalog lists " <> Text.unpack identifier <> " version 1 once") False ExitSuccess (show found) ""
            >> pure Aeson.Null
      withKey name value (Aeson.Object fields) = Aeson.Object (KeyMap.insert name value fields)
      withKey _ _ other = other
      withoutKey name (Aeson.Object fields) = Aeson.Object (KeyMap.delete name fields)
      withoutKey _ other = other
      ruleOf name (Aeson.Object fields) = maybe Aeson.Null id (KeyMap.lookup name fields)
      ruleOf _ _ = Aeson.Null
      decoded text = either (const Aeson.Null) id (decodeUnique (LazyByteString.pack text))
      instanceAccepted label schema value = assert label (validateContractInstance schema value == Right ()) ExitSuccess
        (show (validateContractInstance schema value)) ""
      instanceRefused label schema value = assert label (isLeft (validateContractInstance schema value)) ExitSuccess
        (show (validateContractInstance schema value)) ""
      onPayload f (Aeson.Object fields) = Aeson.Object (maybe fields (\value -> KeyMap.insert "payload" (f value) fields)
        (KeyMap.lookup "payload" fields))
      onPayload _ other = other
  chords <- schemaOf "archive.claim/chord_stacks"
  meter <- schemaOf "archive.claim/meter_layers"
  contribution <- schemaOf "archive.claim/contribution"
  membership <- schemaOf "archive.claim/band_membership"
  let chordInstance = instanceOf "chords"
      chartInstance = instanceOf "chords-chart"
      meterInstance = instanceOf "meter-chart"
      spanInstance = instanceOf "contribution-span"
      membershipInstance = instanceOf "membership"
  -- Anchors: the released bundles accept the records the fixture admits.
  instanceAccepted "chord_stacks 1 accepts the fixture's machine chords (anchor)" chords chordInstance
  instanceAccepted "chord_stacks 1 accepts the fixture's chart chords (anchor)" chords chartInstance
  instanceAccepted "meter_layers 1 accepts the fixture's chart meter (anchor)" meter meterInstance
  instanceAccepted "contribution 1 accepts the fixture's span credit (anchor)" contribution spanInstance
  instanceAccepted "band_membership 1 accepts the fixture's membership (anchor)" membership membershipInstance

  -- Each new rule refuses its own violation, the twin otherwise unchanged.
  let reading f = onPayload (\payload -> case payload of
        Aeson.Object p | Just (Aeson.Array spans) <- KeyMap.lookup "spans" p, (Aeson.Object second : _) <- drop 1 (toList spans)
                       , Just (Aeson.Array readings) <- KeyMap.lookup "readings" second, (first : rest) <- toList readings ->
            let span' = Aeson.Object (KeyMap.insert "readings" (Aeson.toJSON (f first : rest)) second)
            in Aeson.Object (KeyMap.insert "spans" (Aeson.toJSON (take 1 (toList spans) <> [span'] <> drop 2 (toList spans))) p)
        other -> other)
  instanceRefused "x-archive-ascending-items refuses tones out of order" chords
    (reading (withKey "tones" (decoded "[4,1]")) chordInstance)
  instanceRefused "x-archive-pitch-stacks refuses an uncertain class that is a tone" chords
    (reading (withKey "uncertain" (decoded "[{\"pitch_classes\":[4,8],\"present\":\"exactly_one\"}]")) chordInstance)
  instanceRefused "x-archive-pitch-stacks refuses a voicing whose lowest note is not the bass" chords
    (reading (withKey "voicing" (decoded "[61,64,69]")) chordInstance)
  instanceRefused "anyOf refuses exactly one of a single uncertain class" chords
    (reading (withKey "uncertain" (decoded "[{\"pitch_classes\":[7],\"present\":\"exactly_one\"}]")) chordInstance)
  instanceRefused "if/then refuses a reading without its weight when the claim states a scale" chords
    (reading (withoutKey "weight") chordInstance)
  instanceRefused "if/else refuses an alignment without a document" chords
    (onPayload (withKey "alignment" (decoded "{\"method\":\"manual\"}")) chordInstance)
  let meterBar f = onPayload (\payload -> case payload of
        Aeson.Object p | Just (Aeson.Array layers) <- KeyMap.lookup "layers" p, (Aeson.Object layer : others) <- toList layers
                       , Just (Aeson.Array bars) <- KeyMap.lookup "bars" layer, (first : second : rest) <- toList bars ->
            let layer' = Aeson.Object (KeyMap.insert "bars" (Aeson.toJSON (first : f second : rest)) layer)
            in Aeson.Object (KeyMap.insert "layers" (Aeson.toJSON (layer' : others)) p)
        other -> other)
  instanceRefused "x-archive-bar-beats refuses a grouping that does not add up" meter
    (meterBar (withKey "grouping" (decoded "[3,2]")) meterInstance)
  instanceRefused "x-archive-bar-beats refuses a beat at the bar's end" meter
    (meterBar (withKey "beats" (decoded "[2000,4000]")) meterInstance)
  instanceRefused "x-archive-sequential-spans with within refuses overlapping bars in one layer" meter
    (meterBar (withKey "start_sample" (decoded "1900")) meterInstance)
  instanceRefused "x-archive-metric-modulations refuses a regrouping whose pulse does not move" meter
    (onPayload (withKey "modulations" (decoded "[{\"layer\":0,\"bar\":1,\"kind\":\"regrouping\",\"pulse_before\":{\"denominator\":4},\"pulse_after\":{\"denominator\":4,\"dots\":0}}]") . withoutKey "modulations") meterInstance)
  instanceRefused "x-archive-metric-modulations refuses a link of a beat-only layer" meter
    (onPayload (withKey "layers" (decoded "[{\"beats\":[0,1000]},{\"bars\":[{\"start_sample\":0,\"end_sample\":2000,\"numerator\":4,\"denominator\":4}]}]")
      . withKey "modulations" (decoded "[{\"layer\":0,\"bar\":1,\"kind\":\"equation\",\"before\":{\"denominator\":4},\"after\":{\"denominator\":8}}]")) meterInstance)
  instanceRefused "anyOf refuses a link with a ratio" meter
    (onPayload (withKey "modulations" (decoded "[{\"layer\":0,\"bar\":1,\"kind\":\"equation\",\"before\":{\"denominator\":4},\"after\":{\"denominator\":4,\"dots\":1},\"ratio\":[2,3]}]")) meterInstance)
  instanceRefused "x-archive-subset refuses a downbeat that is not a beat" meter
    (onPayload (withKey "layers" (decoded "[{\"beats\":[0,1000,2000],\"downbeats\":[0,1500]}]") . withoutKey "modulations") meterInstance)
  instanceRefused "x-archive-ascending-items refuses beats out of order" meter
    (onPayload (withKey "layers" (decoded "[{\"beats\":[0,2000,1000]}]") . withoutKey "modulations") meterInstance)
  instanceRefused "if/then refuses a felt layer without a written one" meter
    (onPayload (withKey "layers" (decoded "[{\"role\":\"felt\",\"beats\":[0,1000]}]") . withoutKey "modulations") meterInstance)
  instanceAccepted "a beat-only layer with downbeats and a beat unit is accepted" meter
    (onPayload (withKey "layers" (decoded "[{\"beats\":[0,1000,2000],\"downbeats\":[0,2000],\"beat_unit\":{\"denominator\":4}}]") . withoutKey "modulations") meterInstance)
  instanceRefused "x-archive-partial-dates refuses a membership that ends before it begins" membership
    (onPayload (withKey "to" (Aeson.String "2019-12")) membershipInstance)
  instanceRefused "x-archive-partial-dates refuses month 13" membership
    (onPayload (withKey "from" (Aeson.String "2021-13")) membershipInstance)
  instanceRefused "x-archive-ascending-items refuses attributes out of order" membership
    (onPayload (withKey "attributes" (decoded "[\"original\",\"additional\"]")) membershipInstance)
  instanceRefused "uniqueItems refuses an instrument twice" membership
    (onPayload (withKey "instruments" (decoded "[\"https://musicbrainz.org/instrument/f6100277-c7b8-4c8d-aa26-d8cd014b6761\",\"https://musicbrainz.org/instrument/f6100277-c7b8-4c8d-aa26-d8cd014b6761\"]")) membershipInstance)
  instanceRefused "a role from another scope is refused" contribution
    (onPayload (withKey "role" (decoded "{\"type\":\"composer\"}")) spanInstance)
  instanceRefused "x-archive-sequential-spans optional still orders a span claim's spans" contribution
    (onPayload (withKey "spans" (decoded "[{\"start_sample\":0,\"end_sample\":2000},{\"start_sample\":1999,\"end_sample\":3000}]")) spanInstance)
  instanceAccepted "a work credit has no spans, and optional sequential spans let it be" contribution (instanceOf "contribution-work")
  sequenceSchema <- schemaOf "archive.claim/chord_sequence"
  let sequenceInstance = instanceOf "sequence"
  instanceAccepted "chord_sequence 1 accepts the fixture's sequence (anchor)" sequenceSchema sequenceInstance
  instanceAccepted "chord_stacks 1 accepts the fixture's placed chords (anchor)" chords (instanceOf "chords-placed")
  instanceAccepted "chord_stacks 1 accepts the fixture's correction (anchor)" chords (instanceOf "chords-correction")
  instanceRefused "x-archive-document-order refuses an entry located before the one before it" sequenceSchema
    (onPayload (withKey "entries" (decoded "[{\"locator\":{\"line\":2},\"readings\":[{\"text\":\"C\"}]},{\"locator\":{\"line\":1,\"word\":4},\"readings\":[{\"text\":\"G\"}]}]") . withoutKey "repeats") sequenceInstance)
  instanceRefused "x-archive-nested-ranges refuses crossing repeats" sequenceSchema
    (onPayload (withKey "repeats" (decoded "[{\"first\":0,\"last\":1,\"times\":2},{\"first\":1,\"last\":2,\"times\":2}]")) sequenceInstance)
  instanceRefused "x-archive-nested-ranges refuses a repeat past the last entry" sequenceSchema
    (onPayload (withKey "repeats" (decoded "[{\"first\":0,\"last\":3,\"times\":2}]")) sequenceInstance)
  instanceAccepted "x-archive-nested-ranges accepts nested repeats, outer first" sequenceSchema
    (onPayload (withKey "repeats" (decoded "[{\"first\":0,\"last\":2,\"times\":2},{\"first\":0,\"last\":1,\"times\":3}]")) sequenceInstance)
  instanceRefused "not refuses a locator with both a word and a column" sequenceSchema
    (onPayload (withKey "entries" (decoded "[{\"locator\":{\"line\":2,\"word\":1,\"column\":4},\"readings\":[{\"text\":\"C\"}]}]") . withoutKey "repeats") sequenceInstance)
  instanceRefused "anyOf refuses a sequence with neither a Work nor a document" sequenceSchema
    (withKey "refs" (Aeson.toJSON ([] :: [Aeson.Value])) sequenceInstance)
  instanceRefused "if/then refuses a held span without a hold rule" chords
    (onPayload (withoutKey "hold_rule") (instanceOf "chords-placed"))
  instanceRefused "if/else refuses a hold rule with no held span" chords
    (onPayload (withKey "hold_rule" (Aeson.String "until the next")) chordInstance)
  instanceRefused "if/else refuses a sequence entry without a pinned sequence" chords
    (onPayload (withKey "spans" (decoded "[{\"start_sample\":0,\"end_sample\":1000,\"entry\":0,\"readings\":[{\"text\":\"C\"}]}]")) (instanceOf "chords-correction"))

  -- Each malformed rule, keyword, or target rule is refused when its bundle
  -- loads, whatever the instance.
  let refusedOnLoad label schema value = assert label (isLeft (validateContractInstance schema value)) ExitSuccess
        (show (validateContractInstance schema value)) ""
      withRule schema name f = withKey name (f (ruleOf name schema)) schema
      targets = ruleOf "x-archive-reference-targets" contribution
  refusedOnLoad "an ascending items rule with no paths is refused" chords
    (withRule chords "x-archive-ascending-items" (withKey "paths" (decoded "[]")))
  refusedOnLoad "an ascending items rule with a path that is not an array is refused" chords
    (withRule chords "x-archive-ascending-items" (withKey "paths" (decoded "[\"spans\"]")))
  refusedOnLoad "an ascending items rule repeating a path is refused" chords
    (withRule chords "x-archive-ascending-items" (withKey "paths" (decoded "[[\"spans\"],[\"spans\"]]")))
  refusedOnLoad "a pitch stacks rule missing its voicing field is refused" chords
    (withRule chords "x-archive-pitch-stacks" (withoutKey "voicing"))
  refusedOnLoad "a pitch stacks rule naming one field twice is refused" chords
    (withRule chords "x-archive-pitch-stacks" (withKey "voicing" (Aeson.String "tones")))
  refusedOnLoad "a bar beats rule with an extra key is refused" meter
    (withRule meter "x-archive-bar-beats" (withKey "count" (Aeson.String "beats")))
  refusedOnLoad "a metric modulations rule missing dots is refused" meter
    (withRule meter "x-archive-metric-modulations" (withoutKey "dots"))
  refusedOnLoad "a metric modulations rule still naming a ratio is refused" meter
    (withRule meter "x-archive-metric-modulations" (withKey "ratio" (Aeson.String "ratio")))
  refusedOnLoad "a subset rule comparing an array with itself is refused" meter
    (withRule meter "x-archive-subset" (withKey "of" (Aeson.String "downbeats")))
  refusedOnLoad "a target samples rule whose role pins an Artifact is refused" meter
    (withRule meter "x-archive-target-samples" (withKey "role" (Aeson.String "source")))
  refusedOnLoad "a target index rule whose path is not a path is refused" chords
    (withRule chords "x-archive-target-index" (withKey "path" (Aeson.String "spans")))
  refusedOnLoad "a document order rule with no within fields is refused" sequenceSchema
    (withRule sequenceSchema "x-archive-document-order" (withKey "within" (decoded "[]")))
  refusedOnLoad "a nested ranges rule naming one bound twice is refused" sequenceSchema
    (withRule sequenceSchema "x-archive-nested-ranges" (withKey "last" (Aeson.String "first")))
  refusedOnLoad "a sequential spans rule whose within is neither a name nor a path is refused" meter
    (withRule meter "x-archive-sequential-spans" (withKey "within" (decoded "[]")))
  refusedOnLoad "a same subject rule whose optional is false is refused" contribution
    (withRule contribution "x-archive-same-subject" (withKey "optional" (Aeson.Bool False)))
  refusedOnLoad "a target bound rule whose role pins an Artifact is refused" contribution
    (withRule contribution "x-archive-target-bound" (withKey "role" (Aeson.String "source")))
  refusedOnLoad "a partial dates rule ordering a field it does not name is refused" membership
    (withRule membership "x-archive-partial-dates" (withKey "end" (Aeson.String "ended")))
  refusedOnLoad "a partial dates rule ordering a field with itself is refused" membership
    (withRule membership "x-archive-partial-dates" (withKey "end" (Aeson.String "from")))
  refusedOnLoad "a target rule with one alternative is refused" contribution
    (withKey "x-archive-reference-targets" (withKey "recording" (decoded "{\"any\":[{\"shape\":\"artifact\"}]}") targets) contribution)
  refusedOnLoad "a target rule with nested alternatives is refused" contribution
    (withKey "x-archive-reference-targets" (withKey "recording"
      (decoded "{\"any\":[{\"shape\":\"artifact\"},{\"any\":[{\"shape\":\"entity\"},{\"shape\":\"claim\"}]}]}") targets) contribution)
  refusedOnLoad "a target alternative naming many is refused" contribution
    (withKey "x-archive-reference-targets" (withKey "recording"
      (decoded "{\"any\":[{\"shape\":\"artifact\",\"many\":true},{\"shape\":\"entity\",\"kind\":\"recording\"}]}") targets) contribution)
  refusedOnLoad "an empty anyOf is refused" chords (withKey "anyOf" (decoded "[]") chords)
  refusedOnLoad "then without if is refused" chords (withKey "then" (decoded "{}") chords)
  refusedOnLoad "uniqueItems that is not a boolean is refused" chords
    (withKey "properties" (withKey "refs" (withKey "uniqueItems" (Aeson.String "yes") (ruleOf "refs" (ruleOf "properties" chords))) (ruleOf "properties" chords)) chords)
  refusedOnLoad "a pitch stacks rule below the schema root is refused" chords
    (withKey "properties" (withKey "x-archive-pitch-stacks" (ruleOf "x-archive-pitch-stacks" chords) (ruleOf "properties" chords))
      (withoutKey "x-archive-pitch-stacks" chords))
  context <- either (const Aeson.Null) id . decodeUnique <$> LazyByteString.readFile "spec/0.0.9/context.jsonld"
  assert "the 0.0.9 core context, with release and recording, is 0.0.9's and not 0.0.8's"
    (validateCoreContextForSpecification "archive/0.0.9" context == Right ()
      && validateCoreContextForSpecification "archive/0.0.11" context == Right ()
      && isLeft (validateCoreContextForSpecification "archive/0.0.8" context))
    ExitSuccess "" ""
  refuses "specification 0.0.8 refuses the later release kind"
    (replace "archive/0.0.11" "archive/0.0.8" (replace "{\"kind\":\"person\"" "{\"kind\":\"release\"" (entityDraft entity)))
    "entity kind is not defined by the core context"
  succeeds "specification 0.0.11 keeps the recording kind of 0.0.9"
    (replace "{\"kind\":\"person\"" "{\"kind\":\"recording\"" (entityDraft entity))

-- | A 0.0.10 archive adopts 0.0.11 by one `door transition` (ADR 0068): the
-- release is additive (its core context is 0.0.10's), the commit installs the
-- six bundles of ADR 0113, export version 2 of ADR 0116, and the new version,
-- and they are installed only
-- after it. After the upgrade a sketch is admitted under its rules, no longer
-- refused as an uninstalled contract.
previousReleaseUpgradeChecks :: FilePath -> IO ()
previousReleaseUpgradeChecks parent = do
  previousCatalog <- either (const Set.empty) id <$> loadContractCatalog "archive/0.0.10"
  targetCatalog <- either (const Set.empty) id <$> loadContractCatalog "archive/0.0.11"
  let contextOf path = do
        bytes <- LazyByteString.readFile path
        pure $ case decodeUnique bytes of
          Right (Aeson.Object outer) | Just (Aeson.Object inner) <- KeyMap.lookup "@context" outer -> inner
          _ -> KeyMap.empty
  previousContext <- contextOf "spec/0.0.10/context.jsonld"
  targetContext <- contextOf "spec/0.0.11/context.jsonld"
  let previous = SpecificationRelease "archive/0.0.10" previousCatalog "archive/2" previousContext
      target = SpecificationRelease "archive/0.0.11" targetCatalog "archive/2" targetContext
      added = Set.toList (targetCatalog `Set.difference` previousCatalog)
  assert "the real 0.0.10 to 0.0.11 upgrade is additive (anchor)"
    (additiveUpgrade previous target == Right () && not (Set.null previousCatalog)
      && previousCatalog `Set.isProperSubsetOf` targetCatalog && not (KeyMap.null previousContext))
    ExitSuccess (show (additiveUpgrade previous target)) ""
  assert "0.0.11 adds exactly the contract versions of ADRs 0113 and 0116"
    (sort (map (\(identifier, version, _) -> (identifier, version)) added)
      == [ ("archive.claim/export", "2"), ("archive.claim/form", "2"), ("archive.claim/form_spans", "2"), ("archive.claim/lead_sheet", "2")
         , ("archive.claim/melody", "2"), ("archive.claim/meter_layers", "3"), ("archive.claim/underlay", "1") ])
    ExitSuccess (show added) ""
  assert "0.0.11's core context is 0.0.10's"
    (targetContext == previousContext) ExitSuccess "" ""

  (root, archiveId, initOutput) <- newArchiveAtRelease parent "previous-release-upgrade" "0.0.10"
  operatorId <- requiredField "operator_id" initOutput
  operatorRevision <- recordRevisionForObject root operatorId
  (statusBeforeCode, statusBeforeOut, statusBeforeErr) <- readCreateProcessWithExitCode
    ((proc "archive" ["status"]) {cwd = Just root}) ""
  assert "archive status reads the rewritten archive at 0.0.10"
    (statusBeforeCode == ExitSuccess && contains "\"spec\":\"archive/0.0.10\"" statusBeforeOut)
    statusBeforeCode statusBeforeOut statusBeforeErr
  let bundleOf name = case [entry | entry@(identifier, _, _) <- added, identifier == name] of
        [(identifier, version, digest)] -> (Text.unpack identifier, Text.unpack version, Text.unpack digest)
        _ -> ("", "", "")
      melodyBundle = bundleOf "archive.claim/melody"
      atPrevious draft = replace "\"spec\":\"archive/0.0.11\"" "\"spec\":\"archive/0.0.10\"" draft
  work <- admitNew root archiveId "the 0.0.10 archive admits a Work"
    "archive.audio/song" "song-4d5e6f7a-8b9c-4d0e-8f1a-2b3c4d5e6f7a" (atPrevious . entityRecord "musical_work" "A Work")
  _ <- refuseNew root archiveId "the 0.0.10 archive refuses a sketch before the upgrade"
    "archive.claim/melody" "previous-release-sketch"
    (atPrevious . contractRecord "claim" melodyBundle "{\"notes\":[{\"pitch\":64},{\"pitch\":67}]}" [pinned "work" work])
    "proposed contract is not installed at this snapshot: archive.claim/melody"
  upgradeContract <- contractCitationVersion root "archive.transition/specification_upgrade" "1"
  let namespace = "archive.transition/specification_upgrade"
  upgradeId <- allocatedObjectId archiveId namespace "upgrade-0-0-10-to-0-0-11"
  let upgrade = contractRecord "claim" upgradeContract "{\"from\":\"archive/0.0.10\",\"to\":\"archive/0.0.11\"}"
        [pinned "authorizer" (operatorId, operatorRevision)] upgradeId
  upgradeRevision <- canonicalRevisionOf upgrade
  headBefore <- gitHead root
  (code, out, err) <- transitionAt root "upgrade-0-0-10-to-0-0-11" [(upgradeId, namespace, "upgrade-0-0-10-to-0-0-11")]
    [(upgradeId, Nothing), (operatorId, Just operatorRevision)] upgrade
  headAfter <- gitHead root
  assert "door transition upgrades the 0.0.10 archive to 0.0.11"
    (code == ExitSuccess && contains upgradeRevision out && headAfter /= headBefore) code out err
  changes <- checkedOutput "the upgrade fixture lists the upgrade's changes"
    ["-C", root, "show", "--no-renames", "--name-status", "--format=", "HEAD"]
  let bundlePath (_, _, digest) = "contracts/sha256-" <> drop 7 (Text.unpack digest) <> ".json"
      expectedChanges = sort
        ([ "M\tarchive.json", "A\tspec/0.0.11/VERSION", "A\tspec/0.0.11/archive-spec.md"
         , "A\tspec/0.0.11/context.jsonld", "A\t" <> recordRelative upgradeRevision ]
         <> [ "A\t" <> bundlePath entry | entry <- added ])
  assert "the upgrade commit installs the seven bundles, the release's documents, the record, and the manifest"
    (sort (lines changes) == expectedChanges && length added == 7) ExitSuccess changes (unlines expectedChanges)
  installedMatch <- mapM (\entry -> (==) <$> ByteString.readFile (root </> bundlePath entry)
    <*> ByteString.readFile ("spec" </> bundlePath entry)) added
  (statusCode, statusOut, statusErr) <- readCreateProcessWithExitCode
    ((proc "archive" ["status"]) {cwd = Just root}) ""
  assert "the upgraded archive holds the released bundles and reads at 0.0.11"
    (and installedMatch && statusCode == ExitSuccess && contains "\"spec\":\"archive/0.0.11\"" statusOut)
    statusCode statusOut statusErr
  _ <- refuseNew root archiveId "a rest of unknown length is refused"
    "archive.claim/melody" "previous-release-sketch-rest"
    (contractRecord "claim" melodyBundle "{\"notes\":[{\"pitch\":64},{\"rest\":true}]}" [pinned "work" work])
    "contract instance does not satisfy its schema"
  _ <- admitNew root archiveId "the upgraded archive admits a sketch, whose rules Door checks"
    "archive.claim/melody" "previous-release-sketch-after"
    (contractRecord "claim" melodyBundle "{\"notes\":[{\"pitch\":64},{\"pitch\":67}]}" [pinned "work" work])
  pure ()

-- | Pure checks of the 0.0.9 profile: each malformed rule is refused when its
-- bundle loads, and the accepted bundles' rules are the anchors.
derivationContractProfileChecks :: IO ()
derivationContractProfileChecks = do
  catalog <- either (const []) toList <$> loadContractCatalog "archive/0.0.9"
  let digestFor identifier version =
        [digest | (identifier', version', digest) <- catalog, identifier' == identifier, version' == version]
      bundleSchema identifier version = case digestFor identifier version of
        [digest] -> do
          bytes <- readFile ("spec/contracts/sha256-" <> drop 7 (Text.unpack digest) <> ".json")
          case decodeUnique (LazyByteString.pack bytes) of
            Right (Aeson.Object fields) | Just schema <- KeyMap.lookup "schema" fields -> pure schema
            _ -> assert ("the bundle of " <> Text.unpack identifier <> " decodes") False ExitSuccess bytes "" >> pure Aeson.Null
        found -> assert ("the 0.0.9 catalog lists " <> Text.unpack identifier <> " " <> Text.unpack version)
          False ExitSuccess (show found) "" >> pure Aeson.Null
      ref role suffix = Aeson.object
        [ "role" .= (role :: Text.Text)
        , "id" .= ("urn:uuid:00000000-0000-4000-8000-0000000000" <> suffix :: Text.Text)
        , "revision_id" .= Text.pack zeroDigest ]
      withRoot schema key value = case schema of
        Aeson.Object fields -> Aeson.Object (KeyMap.insert key value fields)
        _ -> schema
      withoutRoot schema key = case schema of
        Aeson.Object fields -> Aeson.Object (KeyMap.delete key fields)
        _ -> schema
      instanceOf payload refs = Aeson.object ["payload" .= payload, "refs" .= (refs :: [Aeson.Value])]
      decoded text = either (const Aeson.Null) id (decodeUnique (LazyByteString.pack text))
  runSchema <- bundleSchema "archive.claim/derivation_run" "1"
  suggestionsSchema <- bundleSchema "archive.claim/span_suggestions" "1"
  let run = instanceOf (decoded runPayload) []
      suggestions = instanceOf (decoded (spansPayload 48000 [(0, 10, "music")]))
        [ref "source" "21", ref "stream" "22", ref "run" "23"]
  assert "the accepted 0.0.9 instances satisfy their bundles"
    (validateContractInstance runSchema run == Right ()
      && validateContractInstance suggestionsSchema suggestions == Right ())
    ExitSuccess "" ""
  let refusedOnLoad label schema instanceValue =
        assert label (isLeft (validateContractInstance schema instanceValue)) ExitSuccess
          (show (validateContractInstance schema instanceValue)) ""
      targets = case runSchema of
        Aeson.Object fields -> KeyMap.lookup "x-archive-reference-targets" fields
        _ -> Nothing
      withUsedRule rule = withRoot runSchema "x-archive-reference-targets"
        (case targets of
          Just (Aeson.Object roles) -> Aeson.Object (KeyMap.insert "used" rule roles)
          _ -> Aeson.Null)
  refusedOnLoad "a target rule naming a single shape in shapes is refused"
    (withUsedRule (Aeson.object ["shapes" .= ["claim" :: Text.Text], "many" .= True])) run
  refusedOnLoad "a target rule naming an unknown shape in shapes is refused"
    (withUsedRule (Aeson.object ["shapes" .= ["claim", "record" :: Text.Text], "many" .= True])) run
  refusedOnLoad "a target rule naming both shape and shapes is refused"
    (withUsedRule (Aeson.object ["shape" .= ("claim" :: Text.Text), "shapes" .= ["claim", "entity" :: Text.Text]])) run
  refusedOnLoad "a target rule with kinds for several shapes is refused"
    (withUsedRule (Aeson.object ["shapes" .= ["claim", "entity" :: Text.Text], "kind" .= ("person" :: Text.Text)])) run
  refusedOnLoad "an ordered instants rule naming one field twice is refused"
    (withRoot runSchema "x-archive-ordered-instants" (Aeson.object ["start" .= ("started_at" :: Text.Text), "end" .= ("started_at" :: Text.Text)])) run
  refusedOnLoad "an ordered instants rule with an extra key is refused"
    (withRoot runSchema "x-archive-ordered-instants" (Aeson.object ["start" .= ("started_at" :: Text.Text), "end" .= ("ended_at" :: Text.Text), "payload" .= ("x" :: Text.Text)])) run
  refusedOnLoad "a sequential spans rule missing its end field is refused"
    (withRoot suggestionsSchema "x-archive-sequential-spans" (Aeson.object ["payload" .= ("spans" :: Text.Text), "start" .= ("start_sample" :: Text.Text)])) suggestions
  refusedOnLoad "a target measure rule whose role has no Claim target is refused"
    (withRoot suggestionsSchema "x-archive-target-measure" (Aeson.object
      [ "role" .= ("source" :: Text.Text), "rate" .= ("sample_rate" :: Text.Text), "count" .= ("sample_count" :: Text.Text)
      , "payload" .= ("spans" :: Text.Text), "end" .= ("end_sample" :: Text.Text) ])) suggestions
  refusedOnLoad "a same subject rule comparing a role with itself is refused"
    (withRoot suggestionsSchema "x-archive-same-subject" (Aeson.object
      [ "role" .= ("stream" :: Text.Text), "subject" .= ("artifact" :: Text.Text), "equals" .= ("stream" :: Text.Text) ])) suggestions
  refusedOnLoad "a same subject rule whose role has no target rule is refused"
    (withRoot (withoutRoot suggestionsSchema "x-archive-target-measure") "x-archive-same-subject" (Aeson.object
      [ "role" .= ("run2" :: Text.Text), "subject" .= ("artifact" :: Text.Text), "equals" .= ("source" :: Text.Text) ])) suggestions
  refusedOnLoad "an unknown profile rule is still refused"
    (withRoot runSchema "x-archive-ordered-times" (Aeson.object [])) run
  refusedOnLoad "a non-numeric maximum is refused"
    (withRoot runSchema "maximum" (Aeson.String "64")) run
  assert "the sequential spans rule refuses a backwards span the schema accepts"
    (isLeft (validateContractInstance suggestionsSchema
      (instanceOf (decoded (spansPayload 48000 [(10, 10, "music")])) [ref "source" "21", ref "stream" "22", ref "run" "23"])))
    ExitSuccess "" ""

-- | An archive governed by specification 0.0.6, built from a fresh `archive
-- init` by rewriting its one initial commit with Door's own writer: the
-- 0.0.6 release copy and catalog, and the two bootstrap records finalized
-- again under `archive/0.0.6`. Door still admits into it with the 0.0.6
-- catalog, and refuses a contract it does not install.
earlierSpecificationChecks :: FilePath -> IO ()
earlierSpecificationChecks parent = do
  (root, archiveId, _) <- newEarlierArchive parent "earlier-specification"
  let previousSpec = "archive/0.0.6"
  previousCatalog <- either (const []) toList <$> loadContractCatalog (Text.pack previousSpec)
  currentCatalog <- either (const []) toList <$> loadContractCatalog "archive/0.0.11"
  let added = [entry | entry <- currentCatalog, entry `notElem` previousCatalog]
  (statusCode, statusOut, statusErr) <- readCreateProcessWithExitCode
    ((proc "archive" ["status"]) {cwd = Just root}) ""
  assert "archive status reads the 0.0.6 archive"
    (statusCode == ExitSuccess && contains ("\"spec\":" <> jsonString previousSpec) statusOut)
    statusCode statusOut statusErr
  let atPrevious draftFor objectId = replace "\"spec\":\"archive/0.0.11\"" ("\"spec\":" <> jsonString previousSpec) (draftFor objectId)
  work <- admitNew root archiveId "door admit commits a musical_work into the 0.0.6 archive"
    "archive.audio/song" "song-2b3c4d5e-6f7a-4b8c-9d0e-1f2a3b4c5d6e" (atPrevious (entityRecord "musical_work" "Earlier Work"))
  laterHead <- gitHead root
  _ <- refuseNew root archiveId "the 0.0.6 archive refuses a record under specification 0.0.11"
    "archive.core/entity" "later-specification-person" (entityRecord "person" "Later Person")
    "record spec archive/0.0.11 selects a core context this archive does not hold"
  laterHeadAfter <- gitHead root
  assert "the refused later-specification record wrote nothing"
    (laterHead == laterHeadAfter) ExitSuccess laterHead laterHeadAfter
  correspondence <- contractCitation root "archive.claim/work_musicbrainz_correspondence"
  _ <- admitNew root archiveId "door admit commits a 0.0.6 contract claim into the 0.0.6 archive"
    "archive.claim/work_musicbrainz_correspondence" "earlier-correspondence"
    (\objectId -> replace "\"spec\":\"archive/0.0.11\"" ("\"spec\":" <> jsonString previousSpec)
      (musicBrainzClaim objectId (fst work) (snd work) correspondence))
  case [entry | entry@(identifier, _, _) <- added, identifier == "archive.claim/derivation_run"] of
    [(identifier, version, digest)] -> do
      _ <- refuseNew root archiveId "the 0.0.6 archive refuses a contract it does not install"
        "archive.derivation/run" (runName 1)
        (atPrevious (contractRecord "claim" (Text.unpack identifier, Text.unpack version, Text.unpack digest) runPayload []))
        "proposed contract is not installed at this snapshot: archive.claim/derivation_run"
      pure ()
    found -> assert "0.0.11 lists exactly one derivation_run bundle the 0.0.6 catalog lacks" False ExitSuccess (show found) ""

-- | A fresh `archive init` rewritten into an archive governed by 0.0.6 (see
-- 'earlierSpecificationChecks'): the oldest way this build has to make one.
newEarlierArchive :: FilePath -> String -> IO (FilePath, String, String)
newEarlierArchive parent suffix = newArchiveAtRelease parent suffix "0.0.6"

-- | A fresh `archive init` rewritten into an archive governed by an earlier
-- release @previous@ whose catalog this build carries: its release copy and
-- catalog, and the two bootstrap records finalized again under it.
newArchiveAtRelease :: FilePath -> String -> String -> IO (FilePath, String, String)
newArchiveAtRelease parent suffix previous = do
  (root, archiveId, initOutput) <- newProposalArchive parent suffix
  let previousSpec = "archive/" <> previous
  manifest <- ByteString.readFile (root </> "archive.json")
  rewriteFile (root </> "archive.json") (LazyByteString.pack
    (replace "\"spec\":\"archive/0.0.11\"" ("\"spec\":" <> jsonString previousSpec)
      (LazyByteString.unpack (LazyByteString.fromStrict manifest))))
  shellChecked ("git -C " <> quote root <> " rm -r -q spec/0.0.11")
  createDirectoryIfMissing True (root </> "spec" </> previous)
  mapM_ (\name -> ByteString.readFile ("spec" </> previous </> name)
      >>= ByteString.writeFile (root </> "spec" </> previous </> name))
    ["VERSION", "archive-spec.md", "context.jsonld"]
  previousCatalog <- either (const []) toList <$> loadContractCatalog (Text.pack previousSpec)
  currentCatalog <- either (const []) toList <$> loadContractCatalog "archive/0.0.11"
  let keep = [drop 7 (Text.unpack digest) | (_, _, digest) <- previousCatalog]
      added = [entry | entry@(_, _, digest) <- currentCatalog, drop 7 (Text.unpack digest) `notElem` keep]
  assert ("the 0.0.11 catalog adds bundles to the " <> previous <> " catalog")
    (not (null previousCatalog) && not (null added) && length currentCatalog == length previousCatalog + length added)
    ExitSuccess (show added) ""
  installedNames <- listDirectory (root </> "contracts")
  mapM_ (\name -> shellChecked ("git -C " <> quote root <> " rm -q " <> quote ("contracts/" <> name)))
    [name | name <- installedNames, take 64 (drop 7 name) `notElem` keep]
  recordPaths <- shellLines ("cd " <> quote root <> " && git ls-files records")
  stored <- mapM (\path -> do
      bytes <- LazyByteString.readFile (root </> path)
      case decodeUnique bytes of
        Right (Aeson.Object fields) -> pure (path, fields)
        _ -> assert "a bootstrap record decodes" False ExitSuccess path "" >> pure (path, KeyMap.empty)) recordPaths
  let shapeOf fields = KeyMap.lookup "shape" fields
      entities = [item | item@(_, fields) <- stored, shapeOf fields == Just (Aeson.String "entity")]
      claims = [item | item@(_, fields) <- stored, shapeOf fields == Just (Aeson.String "claim")]
  case (entities, claims) of
    ([(entityPath, entityFields)], [(claimPath, claimFields)]) -> do
      (oldRevision, newRevision) <- refinalize previousSpec root entityPath entityFields id
      _ <- refinalize previousSpec root claimPath claimFields (retarget oldRevision newRevision)
      pure ()
    _ -> assert "init writes one entity and one initialization claim" False ExitSuccess (show recordPaths) ""
  shellChecked ("git -C " <> quote root <> " add archive.json spec records"
    <> " && git -C " <> quote root <> " -c user.name=archive-test -c user.email=archive-test@example.invalid"
    <> " commit -q --amend --no-edit")
  pure (root, archiveId, initOutput)
  where
    rewriteFile path bytes = do
      removeFile path
      LazyByteString.writeFile path bytes
    refinalize specification root' path fields adjust = do
      let oldRevision = case KeyMap.lookup "revision_id" fields of
            Just (Aeson.String value) -> value
            _ -> ""
          at = case KeyMap.lookup "at" fields of
            Just (Aeson.String value) -> value
            _ -> "2026-01-01T00:00:00Z"
          draft = adjust (KeyMap.insert "spec" (Aeson.String (Text.pack specification))
            (KeyMap.delete "at" (KeyMap.delete "revision_id" fields)))
      case finalizeStored at (Aeson.Object draft) of
        Left problem -> assert "a bootstrap record refinalizes" False ExitSuccess (Text.unpack problem) "" >> pure (oldRevision, oldRevision)
        Right (revision, value) -> do
          shellChecked ("git -C " <> quote root' <> " rm -q " <> quote path)
          let relative = recordRelative (Text.unpack revision)
          createDirectoryIfMissing True (takeDirectory (root' </> relative))
          LazyByteString.writeFile (root' </> relative) (encodeCanonical value)
          assert "the refinalized record is a different revision" (revision /= oldRevision) ExitSuccess "" ""
          pure (oldRevision, revision)
    retarget oldRevision newRevision fields = case KeyMap.lookup "refs" fields of
      Just (Aeson.Array refs) -> KeyMap.insert "refs" (Aeson.Array (fmap (repin oldRevision newRevision) refs)) fields
      _ -> fields
    repin oldRevision newRevision (Aeson.Object reference)
      | KeyMap.lookup "revision_id" reference == Just (Aeson.String oldRevision) =
          Aeson.Object (KeyMap.insert "revision_id" (Aeson.String newRevision) reference)
    repin _ _ reference = reference
    shellLines script = do
      (code, output, errors) <- shell script
      assert "temporary test setup" (code == ExitSuccess) code output errors
      pure (lines output)
