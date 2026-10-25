{-# LANGUAGE OverloadedStrings #-}

-- | Checks of the verified checkpoint and the remembered snapshot
-- (Archive.Door.VerifiedCheckpoint, ADR 0052): they let Door skip work,
-- never checks.
module CheckpointChecks
  ( tests
  ) where

import Archive.Door.AdmissionCommit (commitAdmission)
import Archive.Door.AdmissionDryRun
  ( dryRunAdmissionForCommit
  , forgetSnapshots
  , loadSnapshot
  , RecordEntry (..)
  , SnapshotData (..)
  )
import Archive.Door.Proposal (readAdmissionProposal)
import Archive.Door.RecordCommit (prunePackedGitObjects)
import Archive.Door.References (StoreIndex (..))
import Archive.Door.VerifiedCheckpoint
  ( VerifiedCheckpoint (..)
  , checkpointCompactAfter
  , readCheckpoint
  )
import Archive.GitSnapshot (resolveSnapshot)
import qualified Data.ByteString as ByteString
import Data.List (sort)
import qualified Data.Map.Strict as CheckpointMap
import qualified Data.Text as Text
import Fixtures
import Support
import System.Directory
  ( createDirectoryIfMissing
  , doesFileExist
  , findExecutable
  , listDirectory
  , removeFile
  )
import System.Exit (ExitCode (ExitSuccess))
import System.Posix.Files (setFileMode)
import Test.Tasty (TestTree, testGroup)

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
      withBomb action = withEnv "ARCHIVE_GIT_EXECUTABLE" bomb action
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

-- | The checkpoint checks, one case per check or row.
tests :: IO TestTree
tests = fmap (testGroup "checkpoint") $ sequence
  [ caseOf "checkpointChecks" (withProposalTemporaryRoot checkpointChecks)
  , caseOf "rememberedSnapshotChecks" (withProposalTemporaryRoot rememberedSnapshotChecks)
  ]
