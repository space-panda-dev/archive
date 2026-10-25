{-# LANGUAGE OverloadedStrings #-}

-- | Checks of transitions (Archive.Door.TransitionDryRun, TransitionCommit):
-- the dispatch table, publication transitions, historical replay and
-- persistent transitions.
module TransitionChecks
  ( tests
  ) where

import Archive.Door.AdmissionDryRun
  ( AdmissionPlanningError (..)
  , dryRunAdmission
  , dryRunAdmissionForCommit
  )
import Archive.Door.Json (decodeUnique)
import Archive.Door.Proposal (readTransitionProposal)
import Archive.Door.TransitionDryRun
  ( transitionDispatchTable
  , transitionContractsWithoutHandler
  )
import Data.Aeson ((.=))
import qualified Data.Aeson as Aeson
import qualified Data.Aeson.KeyMap as KeyMap
import qualified Data.ByteString.Lazy.Char8 as LazyByteString
import Data.List (sort)
import qualified Data.Text as Text
import Fixtures
import Support
import System.Directory
  ( doesFileExist
  , doesPathExist
  , listDirectory
  , removeFile
  )
import System.Exit (ExitCode (ExitSuccess))
import System.Process (readProcessWithExitCode)
import Test.Tasty (TestTree, testGroup)

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

-- | The dispatch table and the installed transition contracts name the same
-- (identifier, version) set, so no transition contract ships without a
-- handler and no handler outlives its contract.
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

publicationDraft :: (String, String, String) -> String -> [String] -> String -> String
publicationDraft contract payload refs objectId = contractRecord "claim" contract payload refs objectId

-- | Publication transitions (ADR 0051), through the real `door transition`.
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

-- | The transitions checks, one case per check or row.
tests :: IO TestTree
tests = fmap (testGroup "transitions") $ sequence
  [ caseOf "transitionDispatchCatalogChecks" transitionDispatchCatalogChecks
  , caseOf "publicationTransitionChecks" (withProposalTemporaryRoot publicationTransitionChecks)
  , caseOf "historicalReplayChecks" (withProposalTemporaryRoot historicalReplayChecks)
  , caseOf "persistentTransitionChecks" (withProposalTemporaryRoot persistentTransitionChecks)
  ]
