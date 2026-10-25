{-# LANGUAGE OverloadedStrings #-}

-- | Checks of the operation journal and recovery after a killed or failed Git
-- step (Archive.Door.RecordCommit's journal).
module JournalRecoveryChecks
  ( tests
  ) where

import Archive.Door.Json (decodeUnique)
import Data.Aeson ((.=))
import qualified Data.Aeson as Aeson
import qualified Data.ByteString.Lazy.Char8 as LazyByteString
import qualified Data.Text as Text
import Fixtures
import Support
import System.Directory
  ( createDirectoryIfMissing
  , doesPathExist
  , findExecutable
  , getPermissions
  , removeFile
  , renameFile
  , setOwnerExecutable
  , setPermissions
  )
import System.Environment (getEnvironment)
import System.Exit (ExitCode (ExitSuccess))
import System.Process
  ( CreateProcess (cwd, env)
  , proc
  , readCreateProcessWithExitCode
  )
import Test.Tasty (TestTree, testGroup)

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

-- | The journal recovery checks, one case per check or row.
tests :: IO TestTree
tests = fmap (testGroup "journal recovery") $ sequence
  [ caseOf "operationJournalSizeCheck" (withProposalTemporaryRoot operationJournalSizeCheck)
  , caseOf "operationJournalKillRecoveryCheck" (withProposalTemporaryRoot operationJournalKillRecoveryCheck)
  , caseOf "indexRecoveryRecordBatchChecks" (withProposalTemporaryRoot indexRecoveryRecordBatchChecks)
  , caseOf "indexRecoveryRefusalChecks" (withProposalTemporaryRoot indexRecoveryRefusalChecks)
  , caseOf "indexRecoveryTransitionCheck" (withProposalTemporaryRoot indexRecoveryTransitionCheck)
  ]
