{-# LANGUAGE OverloadedStrings #-}

-- | Checks of the specification upgrade (Archive.Door.SpecificationUpgrade):
-- its additive rule, its refusals, forged upgrades in history, the service
-- and recovery.
module UpgradeChecks
  ( tests
  ) where

import Archive.Door.AdmissionDryRun (dryRunAdmissionForCommit, forgetSnapshots)
import Archive.Door.Canonical (finalizeStored)
import Archive.Door.Catalog (loadContractCatalog)
import Archive.Door.Json (decodeUnique)
import Archive.Door.Layout (specificationLayout)
import Archive.Door.Proposal (readAdmissionProposal, readTransitionProposal)
import Archive.Door.RecordCommit (validateArchiveFilePath)
import Archive.Door.SpecificationUpgrade
  ( SpecificationRelease (..)
  , additiveUpgrade
  , compareSpecifications
  )
import Archive.Door.TransitionCommit (commitTransition)
import Archive.Door.TransitionDryRun (TransitionPlan (..))
import Archive.Door.VerifiedCheckpoint (VerifiedCheckpoint (..), readCheckpoint)
import Control.Concurrent (threadDelay)
import Data.Aeson ((.=))
import qualified Data.Aeson as Aeson
import qualified Data.Aeson.KeyMap as KeyMap
import Data.Aeson.RFC8785 (encodeCanonical)
import qualified Data.ByteString as ByteString
import qualified Data.ByteString.Lazy.Char8 as LazyByteString
import Data.Foldable (toList)
import Data.List (sort)
import qualified Data.Map.Strict as CheckpointMap
import qualified Data.Set as Set
import qualified Data.Text as Text
import Fixtures
import qualified Network.Socket as Socket
import qualified Network.Socket.ByteString as SocketBytes
import Support
import System.Directory
  ( createDirectoryIfMissing
  , doesFileExist
  , doesPathExist
  , findExecutable
  , listDirectory
  , removeFile
  )
import System.Environment (getEnvironment)
import System.Exit (ExitCode (ExitSuccess))
import System.Posix.Files (setFileMode)
import System.Process
  ( CreateProcess (cwd, env, std_err, std_out)
  , StdStream (CreatePipe, NoStream)
  , proc
  , readCreateProcessWithExitCode
  , withCreateProcess
  )
import Test.Tasty (TestTree, testGroup)

isRight :: Either a b -> Bool
isRight = not . isLeft

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
        "{\"from\":\"archive/0.0.12\",\"to\":\"archive/0.0.13\"}" [pinned "authorizer" (operatorId, operatorRevision)]
  upgradeId <- allocatedObjectId archiveId namespace "upgrade-0-0-13"
  headBefore <- gitHead root
  (code, out, err) <- transitionAt root "upgrade-0-0-13" [(upgradeId, namespace, "upgrade-0-0-13")]
    [(upgradeId, Nothing), (operatorId, Just operatorRevision)] (upgrade upgradeId)
  headAfter <- gitHead root
  assert "door transition refuses an upgrade to a version it has no catalog for"
    (code /= ExitSuccess && null out && headBefore == headAfter
      && contains "unsupported: unknown specification version archive/0.0.13" err)
    code out err
  _ <- refuseNew root archiveId "door admit refuses a specification upgrade"
    namespace "upgrade-admitted" upgrade
    "unsupported: transition contracts cannot be admitted generically"
  pure ()

-- | A 0.0.6 archive adopts 0.0.12 by one `door transition` (ADR 0068, D-15):
-- every refusal writes nothing, the upgrade is one commit that installs the
-- release and records the new version, a retry changes nothing, Door's
-- checkpoint describes the new commit, and the archive then admits a
-- derivation run, which only 0.0.12 installs.
specificationUpgradeChecks :: FilePath -> IO ()
specificationUpgradeChecks parent = do
  (root, archiveId, initOutput) <- newEarlierArchive parent "specification-upgrade-0-0-6"
  operatorId <- requiredField "operator_id" initOutput
  initialOperatorRevision <- recordRevisionForObject root operatorId
  previousCatalog <- either (const []) toList <$> loadContractCatalog "archive/0.0.6"
  targetCatalog <- either (const []) toList <$> loadContractCatalog "archive/0.0.12"
  let added = [entry | entry <- targetCatalog, entry `notElem` previousCatalog]
      cited identifier = case [(Text.unpack i, Text.unpack v, Text.unpack d) | (i, v, d) <- targetCatalog, i == identifier] of
        [entry] -> pure entry
        found -> assert ("the 0.0.12 catalog lists " <> Text.unpack identifier <> " once") False ExitSuccess (show found) ""
          >> pure ("", "", "")
  upgradeContract <- cited "archive.transition/specification_upgrade"
  runContract <- cited "archive.claim/derivation_run"
  let namespace = "archive.transition/specification_upgrade"
      atSpec specification draft = replace "\"spec\":\"archive/0.0.12\"" ("\"spec\":" <> jsonString specification) draft
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
      upgrade = upgradeDraft upgradeContract "archive/0.0.6" "archive/0.0.12"

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
    "upgrade-unknown" (atSpec "archive/0.0.13" . upgradeDraft upgradeContract "archive/0.0.6" "archive/0.0.13" operator)
    operator operatorRevision "unsupported: unknown specification version archive/0.0.13"
  refused "an upgrade that does not start at the archive's specification is refused"
    "upgrade-wrong-from" (upgradeDraft upgradeContract "archive/0.0.5" "archive/0.0.12" operator)
    operator operatorRevision "specification upgrade from archive/0.0.5 does not start at the archive's specification archive/0.0.6"
  refused "an upgrade record written under the old specification is refused"
    "upgrade-old-spec" (atSpec "archive/0.0.6" . upgrade operator)
    operator operatorRevision "must be written under its target specification archive/0.0.12"
  let (upgradeIdentifier, upgradeVersion, upgradeDigest) = upgradeContract
      foreignDigest = "sha256:" <> replicate 64 'e'
  assert "the foreign citation differs from the catalog's only in its digest (anchor)"
    (foreignDigest /= upgradeDigest) ExitSuccess foreignDigest upgradeDigest
  refused "an upgrade citing a bundle outside the target catalog is refused as not installed"
    "upgrade-foreign" (upgradeDraft (upgradeIdentifier, upgradeVersion, foreignDigest) "archive/0.0.6" "archive/0.0.12" operator)
    operator operatorRevision "proposed contract is not installed at this snapshot or in the catalog of archive/0.0.12"
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
      dataCatalog = dataDirectory </> "spec" </> "0.0.12" </> "contracts.json"
      dataContext = dataDirectory </> "spec" </> "0.0.12" </> "context.jsonld"
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
    _ -> assert "the 0.0.12 catalog decodes" False ExitSuccess "" ""
  unchangedAfter "an upgrade to a release whose catalog drops a bundle is refused"
    (withDataDirectory dataDirectory additiveProposal)
    "unsupported: archive/0.0.12 is not additive: its catalog lacks archive.transition/retire version 1"
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
    "unsupported: archive/0.0.12 is not additive: its core context drops or changes by"

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
         , "A\tspec/0.0.12/VERSION"
         , "A\tspec/0.0.12/archive-spec.md"
         , "A\tspec/0.0.12/context.jsonld"
         , "A\t" <> recordRelative upgradeRevision ]
        <> [ "A\tcontracts/sha256-" <> drop 7 (Text.unpack digest) <> ".json" | (_, _, digest) <- added ])
  assert "the upgrade is one commit holding its record, the release's documents and added bundles, and the new manifest"
    (trim parentOfUpgrade == headBefore && sort (lines changes) == expectedChanges && length added > 1)
    ExitSuccess changes (unlines expectedChanges)
  committedManifest <- checkedOutput "the upgrade fixture reads the committed manifest"
    ["-C", root, "show", "HEAD:archive.json"]
  worktreeManifest <- readFile (root </> "archive.json")
  assert "the archive records the new version where it records its version, committed and in the worktree"
    (contains "\"spec\":\"archive/0.0.12\"" committedManifest && worktreeManifest == committedManifest
      && contains "\"spec\":\"archive/0.0.6\"" (LazyByteString.unpack (LazyByteString.fromStrict manifestBefore)))
    ExitSuccess committedManifest worktreeManifest
  installedMatch <- mapM (\(_, _, digest) -> do
      let name = "sha256-" <> drop 7 (Text.unpack digest) <> ".json"
      installed <- ByteString.readFile (root </> "contracts" </> name)
      released <- ByteString.readFile ("spec/contracts" </> name)
      pure (installed == released)) added
  documentMatch <- mapM (\(installed, released) -> (==) <$> ByteString.readFile (root </> installed) <*> ByteString.readFile released)
    [ ("spec/0.0.12/VERSION", "spec/VERSION"), ("spec/0.0.12/archive-spec.md", "spec/archive-spec.md")
    , ("spec/0.0.12/context.jsonld", "spec/0.0.12/context.jsonld") ]
  status <- checkedOutput "the upgrade fixture reads the worktree status"
    ["-C", root, "status", "--porcelain", "--untracked-files=no"]
  assert "the installed bundles and documents are the release's bytes, and the worktree matches the commit"
    (and installedMatch && and documentMatch && null status) ExitSuccess status ""
  (statusCode, statusOut, statusErr) <- readCreateProcessWithExitCode
    ((proc "archive" ["status"]) {cwd = Just root}) ""
  assert "archive status reads the upgraded archive at 0.0.12"
    (statusCode == ExitSuccess && contains "\"spec\":\"archive/0.0.12\"" statusOut) statusCode statusOut statusErr

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
      && checkpointAdds "spec/0.0.12/context.jsonld"
      && all (\(_, _, digest) -> checkpointAdds ("contracts/sha256-" <> drop 7 (Text.unpack digest) <> ".json")) added
      && not (checkpointAdds "archive.json"))
    ExitSuccess (show (fmap checkpointCommit checkpoint)) headAfter
  realGit <- findExecutable "git" >>= maybe (ioError (userError "upgrade fixture cannot find git")) pure
  let bombDirectory = parent </> "upgrade-git"
      bomb = bombDirectory </> "git"
      withBomb action = withEnv "ARCHIVE_GIT_EXECUTABLE" bomb action
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
    "downgrade-0-0-6" (atSpec "archive/0.0.6" . upgradeDraft upgradeContract "archive/0.0.12" "archive/0.0.6" operator)
    operator operatorRevision "unsupported: no downgrade exists: archive/0.0.6 is earlier than archive/0.0.12"
  _ <- refuseNew root archiveId "door admit refuses a specification upgrade on the upgraded archive"
    namespace "upgrade-admitted" (upgrade operator)
    "unsupported: transition contracts cannot be admitted generically"
  refused "a second upgrade from the old version is refused"
    "upgrade-again" (upgrade operator) operator operatorRevision
    "specification upgrade from archive/0.0.6 does not start at the archive's specification archive/0.0.12"

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
  targetCatalog <- either (const []) toList <$> loadContractCatalog "archive/0.0.12"
  objectId <- allocatedObjectId archiveId "archive.core/entity" "forged-upgrade-person"
  proposal <- writeAdmissionProposal root "forged-upgrade" (manifestFor
    ["records/entity.json"] [(objectId, "archive.core/entity", "forged-upgrade-person")]
    [(objectId, Nothing)] []) ["records/entity.json", entityDraft objectId]
  (beforeCode, beforeOut, beforeErr) <- doorCommandAt root ["admit", "--proposal", proposal, "--dry-run"] ""
  assert "before the forgery the 0.0.6 archive refuses a 0.0.12 record for lack of its context (anchor)"
    (beforeCode /= ExitSuccess && contains "selects a core context this archive does not hold" beforeErr)
    beforeCode beforeOut beforeErr
  manifest <- readFile (root </> "archive.json")
  let upgraded = replace "\"spec\":\"archive/0.0.6\"" "\"spec\":\"archive/0.0.12\"" manifest
  length manifest `seq` removeFile (root </> "archive.json")
  writeFile (root </> "archive.json") upgraded
  createDirectoryIfMissing True (root </> "spec" </> "0.0.12")
  mapM_ (\(from, to) -> ByteString.readFile from >>= ByteString.writeFile (root </> to))
    [ ("spec/VERSION", "spec/0.0.12/VERSION"), ("spec/archive-spec.md", "spec/0.0.12/archive-spec.md")
    , ("spec/0.0.12/context.jsonld", "spec/0.0.12/context.jsonld") ]
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
  targetCatalog <- either (const []) toList <$> loadContractCatalog "archive/0.0.12"
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
      atSpec specification = replace "\"spec\":\"archive/0.0.12\"" ("\"spec\":" <> jsonString specification)
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
          "\"spec\":\"archive/0.0.12\"" (LazyByteString.unpack (LazyByteString.fromStrict manifest))))
        createDirectoryIfMissing True (root </> "spec" </> "0.0.12")
        mapM_ (\(from, to) -> ByteString.readFile from >>= ByteString.writeFile (root </> to))
          [ ("spec/VERSION", "spec/0.0.12/VERSION"), ("spec/archive-spec.md", "spec/0.0.12/archive-spec.md")
          , ("spec/0.0.12/context.jsonld", "spec/0.0.12/context.jsonld") ]
        mapM_ (\entry -> ByteString.readFile ("spec/contracts" </> bundleName entry)
          >>= ByteString.writeFile (root </> "contracts" </> bundleName entry)) bundles
        record >>= writeRecord
        commitAll name
      complete name = forgeUpgrade name id (upgradeRecord name "archive/0.0.6" "archive/0.0.12" "archive/0.0.12") added
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
    (forgeUpgrade "forged-from" id (upgradeRecord "forged-from" "archive/0.0.5" "archive/0.0.12" "archive/0.0.12") added)
    (Just "a specification upgrade record does not name the manifest change it made")
  readsAfter "an upgrade record written under the old specification is refused"
    (forgeUpgrade "forged-spec" id (upgradeRecord "forged-spec" "archive/0.0.6" "archive/0.0.12" "archive/0.0.6") added)
    (Just "a specification upgrade record does not name the manifest change it made")
  readsAfter "an upgrade commit that is not an upgrade claim is refused"
    (forgeUpgrade "forged-claim" id (pure (contractRecord "claim" runContract runPayload []
      "urn:uuid:00000000-0000-4000-8000-0000000000f1")) added)
    (Just "a specification upgrade commit's record is not one upgrade claim")
  readsAfter "an upgrade that changes the archive identifier is refused"
    (forgeUpgrade "forged-identifier" (replace archiveId "urn:uuid:00000000-0000-4000-8000-0000000000f2")
      (upgradeRecord "forged-identifier" "archive/0.0.6" "archive/0.0.12" "archive/0.0.12") added)
    (Just "a specification upgrade changed the archive identifier or layout")
  readsAfter "an upgrade record committed outside an upgrade commit is refused"
    (complete "forged-first" >> upgradeRecord "forged-second" "archive/0.0.6" "archive/0.0.12" "archive/0.0.12"
      >>= writeRecord >> commitAll "forged-second")
    (Just "a specification upgrade record exists outside a specification upgrade commit")
  readsAfter "a bundle the root commit installed ahead of its release is refused"
    (do
      mapM_ (\entry -> ByteString.readFile ("spec/contracts" </> bundleName entry)
        >>= ByteString.writeFile (root </> "contracts" </> bundleName entry)) (filter isRun added)
      shellChecked ("git -C " <> quote root <> " add contracts && git -C " <> quote root
        <> " -c user.name=archive-test -c user.email=archive-test@example.invalid commit -q --amend --no-edit")
      forgeUpgrade "forged-early-bundle" id
        (upgradeRecord "forged-early-bundle" "archive/0.0.6" "archive/0.0.12" "archive/0.0.12")
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
  targetCatalog <- either (const []) toList <$> loadContractCatalog "archive/0.0.12"
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
        "{\"from\":\"archive/0.0.6\",\"to\":\"archive/0.0.12\"}" [pinned "authorizer" (operatorId, operatorRevision)] upgradeId
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
  targetCatalog <- either (const []) toList <$> loadContractCatalog "archive/0.0.12"
  let upgradeContract = case [ (Text.unpack i, Text.unpack v, Text.unpack d)
                             | (i, v, d) <- targetCatalog, i == "archive.transition/specification_upgrade" ] of
        entry : _ -> entry
        [] -> ("", "", "")
      namespace = "archive.transition/specification_upgrade"
      journal = root </> ".git" </> "archive-door-operation"
  upgradeId <- allocatedObjectId archiveId namespace "recovered-upgrade"
  let upgradeRecord = contractRecord "claim" upgradeContract
        "{\"from\":\"archive/0.0.6\",\"to\":\"archive/0.0.12\"}" [pinned "authorizer" (operatorId, operatorRevision)] upgradeId
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
      && contains "\"spec\":\"archive/0.0.12\"" committedManifest
      && indexAtOld && not indexAtKilled)
    ExitSuccess manifestAfterKill committedManifest
  length manifestAfterKill `seq` pure ()
  (code, out, err) <- doorCommandAt root ["transition", "--proposal", proposal] ""
  headAfter <- gitHead root
  journalAfter <- doesPathExist journal
  manifestAfter <- readFile (root </> "archive.json")
  context <- ByteString.readFile (root </> "spec" </> "0.0.12" </> "context.jsonld")
  released <- ByteString.readFile ("spec" </> "0.0.12" </> "context.jsonld")
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
-- upgrade may write, without an archive. The real 0.0.6 and 0.0.12 releases
-- are the anchor; each refusal changes one thing about the target.
specificationUpgradeRuleChecks :: Recorder -> IO ()
specificationUpgradeRuleChecks check = do
  previousCatalog <- either (const Set.empty) id <$> loadContractCatalog "archive/0.0.6"
  targetCatalog <- either (const Set.empty) id <$> loadContractCatalog "archive/0.0.12"
  let contextOf path = do
        bytes <- LazyByteString.readFile path
        pure $ case decodeUnique bytes of
          Right (Aeson.Object outer) | Just (Aeson.Object inner) <- KeyMap.lookup "@context" outer -> inner
          _ -> KeyMap.empty
  previousContext <- contextOf "spec/0.0.6/context.jsonld"
  targetContext <- contextOf "spec/0.0.12/context.jsonld"
  let previous = SpecificationRelease "archive/0.0.6" previousCatalog "archive/2" previousContext
      target = SpecificationRelease "archive/0.0.12" targetCatalog "archive/2" targetContext
      refusedWith label expected candidate = check label
        (either (contains expected . Text.unpack) (const False) (additiveUpgrade previous candidate))
        ExitSuccess (show (additiveUpgrade previous candidate)) expected
      oneEntry = Set.findMin previousCatalog
      (oneId, oneVersion, _) = oneEntry
  check "the real 0.0.6 to 0.0.12 upgrade is additive (anchor)"
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
  check "a target core context may define more terms"
    (additiveUpgrade previous target { releaseContext = KeyMap.insert "extra_term" (Aeson.String "arch:extra") targetContext } == Right ())
    ExitSuccess "" ""
  check "each known release has its layout, and a later one is not guessed"
    (map specificationLayout ["archive/0.0.5", "archive/0.0.6", "archive/0.0.7", "archive/0.0.8", "archive/0.0.9", "archive/0.0.10", "archive/0.0.11", "archive/0.0.12"] == [Right "archive/1", Right "archive/2", Right "archive/2", Right "archive/2", Right "archive/2", Right "archive/2", Right "archive/2", Right "archive/2"]
      && isLeft (specificationLayout "archive/0.0.13"))
    ExitSuccess "" ""
  check "specification versions order numerically, not as text"
    (compareSpecifications "archive/0.0.12" "archive/0.0.9" == Right GT
      && compareSpecifications "archive/0.0.6" "archive/0.0.9" == Right LT
      && isLeft (compareSpecifications "archive/0.0" "archive/0.0.9"))
    ExitSuccess "" ""
  check "an upgrade writes only the manifest, a release's three documents, and digest-named bundles"
    (all (isRight . validateArchiveFilePath)
        [ "archive.json", "spec/0.0.12/VERSION", "spec/0.0.12/archive-spec.md", "spec/0.0.12/context.jsonld"
        , "contracts/sha256-" <> replicate 64 'a' <> ".json" ]
      && all (isLeft . validateArchiveFilePath)
        [ "records/00/00/sha256-" <> replicate 64 '0' <> ".json", "spec/0.0.12/notes.md", "spec/latest/VERSION"
        , "contracts/sha256-" <> replicate 64 'A' <> ".json", "contracts/other.json", "../archive.json"
        , "./archive.json", "content/sha256/aa/aa/" <> replicate 64 'a' ])
    ExitSuccess "" ""

-- | A 0.0.10 archive adopts 0.0.12 by one `door transition` (ADR 0068): the
-- release is additive (its core context is 0.0.10's), the commit installs the
-- six bundles of ADR 0113, export version 2 of ADR 0116, the three
-- session evidence bundles of ADRs 0107, 0120, and 0121, and the new version,
-- and they are installed only
-- after it. After the upgrade a sketch is admitted under its rules, no longer
-- refused as an uninstalled contract.
previousReleaseUpgradeChecks :: FilePath -> IO ()
previousReleaseUpgradeChecks parent = do
  previousCatalog <- either (const Set.empty) id <$> loadContractCatalog "archive/0.0.10"
  targetCatalog <- either (const Set.empty) id <$> loadContractCatalog "archive/0.0.12"
  let contextOf path = do
        bytes <- LazyByteString.readFile path
        pure $ case decodeUnique bytes of
          Right (Aeson.Object outer) | Just (Aeson.Object inner) <- KeyMap.lookup "@context" outer -> inner
          _ -> KeyMap.empty
  previousContext <- contextOf "spec/0.0.10/context.jsonld"
  targetContext <- contextOf "spec/0.0.12/context.jsonld"
  let previous = SpecificationRelease "archive/0.0.10" previousCatalog "archive/2" previousContext
      target = SpecificationRelease "archive/0.0.12" targetCatalog "archive/2" targetContext
      added = Set.toList (targetCatalog `Set.difference` previousCatalog)
  assert "the real 0.0.10 to 0.0.12 upgrade is additive (anchor)"
    (additiveUpgrade previous target == Right () && not (Set.null previousCatalog)
      && previousCatalog `Set.isProperSubsetOf` targetCatalog && not (KeyMap.null previousContext))
    ExitSuccess (show (additiveUpgrade previous target)) ""
  assert "0.0.12 adds exactly the contract versions of ADRs 0107, 0113, 0116, 0120, and 0121"
    (sort (map (\(identifier, version, _) -> (identifier, version)) added)
      == [ ("archive.claim/capture_start", "1"), ("archive.claim/export", "2"), ("archive.claim/form", "2"), ("archive.claim/form_spans", "2"), ("archive.claim/lead_sheet", "2")
         , ("archive.claim/melody", "2"), ("archive.claim/meter_layers", "3"), ("archive.claim/session_ensemble", "1"), ("archive.claim/underlay", "1")
         , ("archive.migration/session_translation", "1") ])
    ExitSuccess (show added) ""
  assert "0.0.12's core context is 0.0.10's"
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
      atPrevious draft = replace "\"spec\":\"archive/0.0.12\"" "\"spec\":\"archive/0.0.10\"" draft
  work <- admitNew root archiveId "the 0.0.10 archive admits a Work"
    "archive.audio/song" "song-4d5e6f7a-8b9c-4d0e-8f1a-2b3c4d5e6f7a" (atPrevious . entityRecord "musical_work" "A Work")
  _ <- refuseNew root archiveId "the 0.0.10 archive refuses a sketch before the upgrade"
    "archive.claim/melody" "previous-release-sketch"
    (atPrevious . contractRecord "claim" melodyBundle "{\"notes\":[{\"pitch\":64},{\"pitch\":67}]}" [pinned "work" work])
    "proposed contract is not installed at this snapshot: archive.claim/melody"
  upgradeContract <- contractCitationVersion root "archive.transition/specification_upgrade" "1"
  let namespace = "archive.transition/specification_upgrade"
  upgradeId <- allocatedObjectId archiveId namespace "upgrade-0-0-10-to-0-0-11"
  let upgrade = contractRecord "claim" upgradeContract "{\"from\":\"archive/0.0.10\",\"to\":\"archive/0.0.12\"}"
        [pinned "authorizer" (operatorId, operatorRevision)] upgradeId
  upgradeRevision <- canonicalRevisionOf upgrade
  headBefore <- gitHead root
  (code, out, err) <- transitionAt root "upgrade-0-0-10-to-0-0-11" [(upgradeId, namespace, "upgrade-0-0-10-to-0-0-11")]
    [(upgradeId, Nothing), (operatorId, Just operatorRevision)] upgrade
  headAfter <- gitHead root
  assert "door transition upgrades the 0.0.10 archive to 0.0.12"
    (code == ExitSuccess && contains upgradeRevision out && headAfter /= headBefore) code out err
  changes <- checkedOutput "the upgrade fixture lists the upgrade's changes"
    ["-C", root, "show", "--no-renames", "--name-status", "--format=", "HEAD"]
  let bundlePath (_, _, digest) = "contracts/sha256-" <> drop 7 (Text.unpack digest) <> ".json"
      expectedChanges = sort
        ([ "M\tarchive.json", "A\tspec/0.0.12/VERSION", "A\tspec/0.0.12/archive-spec.md"
         , "A\tspec/0.0.12/context.jsonld", "A\t" <> recordRelative upgradeRevision ]
         <> [ "A\t" <> bundlePath entry | entry <- added ])
  assert "the upgrade commit installs the ten bundles, the release's documents, the record, and the manifest"
    (sort (lines changes) == expectedChanges && length added == 7) ExitSuccess changes (unlines expectedChanges)
  installedMatch <- mapM (\entry -> (==) <$> ByteString.readFile (root </> bundlePath entry)
    <*> ByteString.readFile ("spec" </> bundlePath entry)) added
  (statusCode, statusOut, statusErr) <- readCreateProcessWithExitCode
    ((proc "archive" ["status"]) {cwd = Just root}) ""
  assert "the upgraded archive holds the released bundles and reads at 0.0.12"
    (and installedMatch && statusCode == ExitSuccess && contains "\"spec\":\"archive/0.0.12\"" statusOut)
    statusCode statusOut statusErr
  _ <- refuseNew root archiveId "a rest of unknown length is refused"
    "archive.claim/melody" "previous-release-sketch-rest"
    (contractRecord "claim" melodyBundle "{\"notes\":[{\"pitch\":64},{\"rest\":true}]}" [pinned "work" work])
    "contract instance does not satisfy its schema"
  _ <- admitNew root archiveId "the upgraded archive admits a sketch, whose rules Door checks"
    "archive.claim/melody" "previous-release-sketch-after"
    (contractRecord "claim" melodyBundle "{\"notes\":[{\"pitch\":64},{\"pitch\":67}]}" [pinned "work" work])
  pure ()

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
  currentCatalog <- either (const []) toList <$> loadContractCatalog "archive/0.0.12"
  let added = [entry | entry <- currentCatalog, entry `notElem` previousCatalog]
  (statusCode, statusOut, statusErr) <- readCreateProcessWithExitCode
    ((proc "archive" ["status"]) {cwd = Just root}) ""
  assert "archive status reads the 0.0.6 archive"
    (statusCode == ExitSuccess && contains ("\"spec\":" <> jsonString previousSpec) statusOut)
    statusCode statusOut statusErr
  let atPrevious draftFor objectId = replace "\"spec\":\"archive/0.0.12\"" ("\"spec\":" <> jsonString previousSpec) (draftFor objectId)
  work <- admitNew root archiveId "door admit commits a musical_work into the 0.0.6 archive"
    "archive.audio/song" "song-2b3c4d5e-6f7a-4b8c-9d0e-1f2a3b4c5d6e" (atPrevious (entityRecord "musical_work" "Earlier Work"))
  laterHead <- gitHead root
  _ <- refuseNew root archiveId "the 0.0.6 archive refuses a record under specification 0.0.12"
    "archive.core/entity" "later-specification-person" (entityRecord "person" "Later Person")
    "record spec archive/0.0.12 selects a core context this archive does not hold"
  laterHeadAfter <- gitHead root
  assert "the refused later-specification record wrote nothing"
    (laterHead == laterHeadAfter) ExitSuccess laterHead laterHeadAfter
  correspondence <- contractCitation root "archive.claim/work_musicbrainz_correspondence"
  _ <- admitNew root archiveId "door admit commits a 0.0.6 contract claim into the 0.0.6 archive"
    "archive.claim/work_musicbrainz_correspondence" "earlier-correspondence"
    (\objectId -> replace "\"spec\":\"archive/0.0.12\"" ("\"spec\":" <> jsonString previousSpec)
      (musicBrainzClaim objectId (fst work) (snd work) correspondence))
  case [entry | entry@(identifier, _, _) <- added, identifier == "archive.claim/derivation_run"] of
    [(identifier, version, digest)] -> do
      _ <- refuseNew root archiveId "the 0.0.6 archive refuses a contract it does not install"
        "archive.derivation/run" (runName 1)
        (atPrevious (contractRecord "claim" (Text.unpack identifier, Text.unpack version, Text.unpack digest) runPayload []))
        "proposed contract is not installed at this snapshot: archive.claim/derivation_run"
      pure ()
    found -> assert "0.0.12 lists exactly one derivation_run bundle the 0.0.6 catalog lacks" False ExitSuccess (show found) ""

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
    (replace "\"spec\":\"archive/0.0.12\"" ("\"spec\":" <> jsonString previousSpec)
      (LazyByteString.unpack (LazyByteString.fromStrict manifest))))
  shellChecked ("git -C " <> quote root <> " rm -r -q spec/0.0.12")
  createDirectoryIfMissing True (root </> "spec" </> previous)
  mapM_ (\name -> ByteString.readFile ("spec" </> previous </> name)
      >>= ByteString.writeFile (root </> "spec" </> previous </> name))
    ["VERSION", "archive-spec.md", "context.jsonld"]
  previousCatalog <- either (const []) toList <$> loadContractCatalog (Text.pack previousSpec)
  currentCatalog <- either (const []) toList <$> loadContractCatalog "archive/0.0.12"
  let keep = [drop 7 (Text.unpack digest) | (_, _, digest) <- previousCatalog]
      added = [entry | entry@(_, _, digest) <- currentCatalog, drop 7 (Text.unpack digest) `notElem` keep]
  assert ("the 0.0.12 catalog adds bundles to the " <> previous <> " catalog")
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

-- | The specification upgrade checks, one case per check or row.
tests :: IO TestTree
tests = fmap (testGroup "specification upgrade") $ sequence
  [ caseOf "previousReleaseUpgradeChecks" (withProposalTemporaryRoot previousReleaseUpgradeChecks)
  , caseOf "specificationUpgradeRefusalChecks" (withProposalTemporaryRoot specificationUpgradeRefusalChecks)
  , caseOf "earlierSpecificationChecks" (withProposalTemporaryRoot earlierSpecificationChecks)
  , rowsOf "specificationUpgradeRuleChecks" specificationUpgradeRuleChecks
  , caseOf "specificationUpgradeChecks" (withProposalTemporaryRoot specificationUpgradeChecks)
  , caseOf "forgedUpgradeChecks" (withProposalTemporaryRoot forgedUpgradeChecks)
  , caseOf "forgedUpgradeHistoryChecks" (withProposalTemporaryRoot forgedUpgradeHistoryChecks)
  , caseOf "specificationUpgradeServiceChecks" (withProposalTemporaryRoot specificationUpgradeServiceChecks)
  , caseOf "specificationUpgradeRecoveryChecks" (withProposalTemporaryRoot specificationUpgradeRecoveryChecks)
  ]
