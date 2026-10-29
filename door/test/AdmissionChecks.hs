{-# LANGUAGE OverloadedStrings #-}

-- | Checks of admission through the real `door admit`: proposals, their input
-- limits, persistent admission, the recheck before commit, and content
-- (Archive.Door.Proposal, AdmissionDryRun, AdmissionCommit).
module AdmissionChecks
  ( tests
  ) where

import Archive.Door.AdmissionDryRun
  ( AdmissionPlanningError (..)
  , dryRunAdmissionForCommit
  , recheckAdmissionForCommit
  )
import Archive.Door.AdmissionPlan (AdmissionPlan (plannedAdmissionSnapshot))
import Archive.Door.Canonical (finalizeStored)
import Archive.Door.Json (decodeUnique)
import Archive.Door.Layout (contentRelativePath, parseSha256Digest)
import Archive.Door.MutationError (MutationError (..))
import Archive.Door.Proposal (readAdmissionProposal)
import Archive.Door.RecordCommit (withArchiveWriteLock)
import Archive.Door.TransitionDryRun
  ( TransitionPlanningError (..)
  , dryRunTransition
  , dryRunTransitionForCommit
  )
import Data.Aeson ((.=))
import qualified Data.Aeson as Aeson
import Data.Aeson.RFC8785 (encodeCanonical)
import qualified Data.ByteString.Lazy.Char8 as LazyByteString
import Data.List (sort)
import qualified Data.Text as Text
import Fixtures
import Support
import System.Directory
  ( createDirectoryIfMissing
  , createFileLink
  , doesPathExist
  , findExecutable
  , listDirectory
  , getPermissions
  , removeFile
  , setOwnerExecutable
  , setPermissions
  )
import System.Environment (getEnvironment, lookupEnv)
import System.Exit (ExitCode (ExitSuccess))
import System.Posix.Files (setFileMode)
import System.Process
  ( CreateProcess (cwd, env)
  , proc
  , readCreateProcessWithExitCode
  , readProcessWithExitCode
  )
import Test.Tasty (TestTree, testGroup)
import Test.Tasty.HUnit (assertFailure)

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
  (plan, unchanged, unchangedCalls) <- withEnv "ARCHIVE_GIT_EXECUTABLE" wrapper $ do
    planned <- dryRunAdmissionForCommit root parsed
    plan <- either (ioError . userError . show) pure planned
    writeFile callLog ""
    unchanged <- recheckAdmissionForCommit root parsed plan
    unchangedCalls <- lines <$> readFile callLog
    pure (plan, unchanged, unchangedCalls)
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

-- Reference guards are checked in the real planner and final recheck, while
-- complete retries remain quiet even after the referenced object's head moves.
referenceHeadChecks :: FilePath -> IO ()
referenceHeadChecks parent = do
  (root, archiveId, _) <- newProposalArchive parent "reference-heads"
  target <- allocatedObjectId archiveId "archive.core/entity" "reference-target"
  revision <- commitEntityFixture root target
  objectId <- allocatedObjectId archiveId "archive.core/entity" "guarded-review"
  second <- allocatedObjectId archiveId "archive.core/entity" "guarded-review-second"
  pendingObject <- allocatedObjectId archiveId "archive.core/entity" "guarded-pending"
  let pin = "{\"role\":\"subject\",\"id\":\"" <> target <> "\",\"revision_id\":\"" <> revision <> "\"}"
      draft object = replace "\"refs\":[]" ("\"refs\":[" <> pin <> "]") (entityDraft object)
      row object rev = "{\"object_id\":\"" <> object <> "\",\"revision_id\":" <> rev <> "}"
      valid = row target (jsonString revision)
      guarded manifest rows = init manifest <> ",\"expected_reference_heads\":" <> rows <> "}"
      base = manifestFor ["records/review.json"] [(objectId, "archive.core/entity", "guarded-review")]
               [(objectId, Nothing)] []
  mapM_ (\(label, rows, problem) -> do
    bad <- writeAdmissionProposal root ("reference-" <> label) (guarded base rows)
      ["records/review.json", draft objectId]
    proposalRefusesContaining label problem root bad)
    [ ("null", "[" <> row target "null" <> "]", "non-null digest")
    , ("duplicate", "[" <> valid <> "," <> valid <> "]", "must be unique")
    , ("drafted", "[" <> row objectId (jsonString revision) <> "]", "must not name drafted objects")
    , ("unrelated", "[" <> row pendingObject (jsonString revision) <> "]", "explicitly referenced objects")
    , ("disagrees", "[" <> row target (jsonString zeroDigest) <> "]", "agree with every explicit pin")
    ]
  proposal <- writeAdmissionProposal root "guarded-batch" (guarded
    (manifestFor ["records/review.json", "records/second.json"]
      [(objectId, "archive.core/entity", "guarded-review"), (second, "archive.core/entity", "guarded-review-second")]
      [(objectId, Nothing), (second, Nothing)] []) ("[" <> valid <> "]"))
    ["records/review.json", draft objectId, "records/second.json", draft second]
  expected <- mapM canonicalRevisionOf [draft objectId, draft second]
  proposalAccepts "a guarded batch predicts both exact revisions" root proposal expected
  pending <- writeAdmissionProposal root "guarded-pending" (guarded
    (manifestFor ["records/pending.json"] [(pendingObject, "archive.core/entity", "guarded-pending")]
      [(pendingObject, Nothing)] []) ("[" <> valid <> "]")) ["records/pending.json", draft pendingObject]
  parsed <- readAdmissionProposal pending >>= either (ioError . userError . Text.unpack) pure
  planned <- dryRunAdmissionForCommit root parsed >>= either (ioError . userError . show) pure
  let manifestPath = pending </> "manifest.json"
  original <- readFile manifestPath
  length original `seq` writeFile manifestPath (replace revision zeroDigest original)
  changed <- recheckAdmissionForCommit root parsed planned
  assert "a guarded manifest remains subject to retained-byte recheck"
    (case changed of Left (AdmissionProposalRejected _) -> True; _ -> False)
    ExitSuccess (show changed) ""
  writeFile manifestPath original
  (saved, _, saveErrors, saveAnnex) <- persistentAdmissionCommandWithFakeAnnex parent "guard-save" root proposal
  assert "a valid guarded batch commits atomically without annex"
    (saved == ExitSuccess && not saveAnnex) saved "" saveErrors
  competing <- writeAdmissionProposal root "guard-competing" (manifestFor
    ["records/target.json"] [] [(target, Just revision)] [])
    ["records/target.json", superseding target target revision]
  (moved, _, moveErrors, _) <- persistentAdmissionCommandWithFakeAnnex parent "guard-move" root competing
  assert "another writer advances the referenced object's head" (moved == ExitSuccess) moved "" moveErrors
  beforeHead <- gitHead root
  beforeRecords <- recordCount root
  beforeCommits <- gitCommitCount root
  rechecked <- recheckAdmissionForCommit root parsed planned
  assert "a reference moved since dry run is refused by final recheck"
    (rechecked == Left (AdmissionProposalStale "expected reference head is stale"))
    ExitSuccess (show rechecked) ""
  (stale, _, staleErrors, staleAnnex) <- persistentAdmissionCommandWithFakeAnnex parent "guard-stale" root pending
  assert "a stale reference is refused before content handling"
    (stale /= ExitSuccess && not staleAnnex && contains "expected reference head is stale" staleErrors)
    stale "" staleErrors
  (retried, retryOutput, retryErrors, retryAnnex) <- persistentAdmissionCommandWithFakeAnnex parent "guard-retry" root proposal
  afterHead <- gitHead root
  afterRecords <- recordCount root
  afterCommits <- gitCommitCount root
  assert "a complete guarded retry ignores later target heads and writes nothing"
    (retried == ExitSuccess && not retryAnnex && contains "\"retry\":true" retryOutput
      && (beforeHead, beforeRecords, beforeCommits) == (afterHead, afterRecords, afterCommits))
    retried retryOutput retryErrors

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

writeRawProposal :: FilePath -> String -> String -> IO FilePath
writeRawProposal root name manifest = do
  let proposal = root <> "-proposal-" <> name
  createDirectoryIfMissing True (proposal </> "records")
  writeFile (proposal </> "manifest.json") manifest
  pure proposal

readCommittedRecord :: FilePath -> String -> IO LazyByteString.ByteString
readCommittedRecord root revision = do
  let path = recordRelative revision
  (code, output, errors) <- readProcessWithExitCode "git"
    ["-C", root, "show", "HEAD:" <> path] ""
  assert "persistent fixture reads committed record"
    (code == ExitSuccess) code output errors
  pure (LazyByteString.pack output)

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

retireDraftWithPredecessor :: String -> String -> String -> String -> String
                           -> (String, String, String) -> String -> String -> String
retireDraftWithPredecessor objectId operatorId operatorRevision targetId targetRevision
    contract predecessorId predecessorRevision =
  replace "],\"supersedes\""
    ("," <> transitionReference "transition_predecessor"
      predecessorId predecessorRevision <> "],\"supersedes\"")
    (retireDraft objectId operatorId operatorRevision targetId targetRevision contract)

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
        Left problem -> assertFailure ("batch fixture JSON: " <> problem)
        Right parsed -> pure parsed
      (revision, stored) <- case finalizeStored "2026-01-01T00:00:00Z" value of
        Left problem -> assertFailure ("batch fixture writer: " <> Text.unpack problem)
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

contentRelative :: String -> FilePath
contentRelative digest = either (error . Text.unpack) contentRelativePath
  (parseSha256Digest (Text.pack digest))

-- | The admission checks, one case per check or row.
tests :: IO TestTree
tests = fmap (testGroup "admission") $ sequence
  [ caseOf "proposalChecks" (withProposalTemporaryRoot proposalChecks)
  , caseOf "controlPlaneInputLimitChecks" (withProposalTemporaryRoot controlPlaneInputLimitChecks)
  , caseOf "referenceHeadChecks" (withProposalTemporaryRoot referenceHeadChecks)
  , caseOf "persistentAdmissionChecks" (withProposalTemporaryRoot persistentAdmissionChecks)
  , caseOf "admissionRecheckChecks" (withProposalTemporaryRoot admissionRecheckChecks)
  , caseOf "contentAdmissionChecks" (withProposalTemporaryRoot contentAdmissionChecks)
  ]
