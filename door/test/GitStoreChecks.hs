{-# LANGUAGE OverloadedStrings #-}

-- | Checks of the Git object store's topology and its durability barrier
-- failures.
module GitStoreChecks
  ( tests
  ) where

import Archive.Door.MutationError (MutationError (..), mutationErrorMessage)
import Archive.Door.RecordCommit (commitRecordBatch, withArchiveWriteLock)
import Control.Monad (when)
import qualified Data.Text as Text
import Fixtures
import Support
import System.Directory
  ( createDirectoryIfMissing
  , doesFileExist
  , findExecutable
  , listDirectory
  , getPermissions
  , removeFile
  , setOwnerExecutable
  , setPermissions
  )
import System.Environment (getEnvironment)
import System.Exit (ExitCode (ExitSuccess))
import System.Posix.Files (setFileMode)
import System.Process
  ( CreateProcess (cwd, env)
  , proc
  , readCreateProcessWithExitCode
  , readProcessWithExitCode
  )
import Test.Tasty (TestTree, testGroup)

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

-- | The git object store checks, one case per check or row.
tests :: IO TestTree
tests = fmap (testGroup "git object store") $ sequence
  [ caseOf "gitObjectStoreTopologyChecks" (withProposalTemporaryRoot gitObjectStoreTopologyChecks)
  , caseOf "gitObjectStoreBarrierFailureChecks" (withProposalTemporaryRoot gitObjectStoreBarrierFailureChecks)
  ]
