{-# LANGUAGE OverloadedStrings #-}

-- | Checks of the Git snapshot reader (Archive.GitSnapshot) and the configured
-- Git executables (Archive.Executables).
module GitSnapshotChecks
  ( tests
  ) where

import Archive.Door.MutationError (MutationError (..), mutationErrorMessage)
import Archive.Executables (resolveAnnexExecutable, resolveGitExecutable)
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
import Archive.Process (ProcessError (..))
import qualified Data.Text as Text
import Support
import System.Directory
  ( createDirectoryIfMissing
  , createDirectoryLink
  , doesPathExist
  , findExecutable
  , getPermissions
  , removeFile
  , renameFile
  , renameDirectory
  , setOwnerExecutable
  , setPermissions
  )
import System.Environment (lookupEnv)
import System.Exit (ExitCode (ExitFailure, ExitSuccess))
import System.Process (readProcessWithExitCode)
import Test.Tasty (TestTree, testGroup)

typedPlannerErrorChecks :: IO ()
typedPlannerErrorChecks = do
  let processFailure = ProcessSpawnFailure "could not spawn selected Git"
      snapshotFailure = GitProcessExecutionFailure processFailure
      legacyText = "Git snapshot error: GitProcessFailure \"could not spawn selected Git\""
  assert "Git snapshot retains the process failure and preserves its CLI text"
    (gitSnapshotErrorMessage snapshotFailure == legacyText
      && mutationErrorMessage (MutationSnapshotFailure snapshotFailure) == legacyText)
    ExitSuccess (show snapshotFailure <> " / " <> Text.unpack (gitSnapshotErrorMessage snapshotFailure)) ""

withGitHistoryRoot :: (FilePath -> IO ()) -> IO ()
withGitHistoryRoot = withScratchDirectory "/tmp/archive-git-history-tests.XXXXXX"

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
      failCheck "history fixture resolves as a Git snapshot" (ExitFailure 1) "" (show problem)
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
      failCheck "shallow fixture resolves as a Git snapshot" (ExitFailure 1) "" (show problem)
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
      failCheck "merge fixture resolves as a Git snapshot" (ExitFailure 1) "" (show problem)
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
  realGit <- findExecutable "git" >>= maybe
    (ioError (userError "snapshot root-swap fixture cannot find Git")) pure
  withEnv "ARCHIVE_GIT_EXECUTABLE" realGit $ do
      resolved <- resolveSnapshot root
      snapshot <- case resolved of
        Left problem -> do
          failCheck "root-swap fixture resolves before pathname replacement"
            (ExitFailure 1) "" (show problem)
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
  withEnv "PATH" (decoyBin <> ":" <> maybe "" id oldPath) $
      withEnv "ARCHIVE_GIT_EXECUTABLE" realGit $ do
          result <- resolveSnapshot root
          assert "ambient PATH Git cannot substitute for operator configuration"
            (not (isLeft result)) ExitSuccess (show result) ""
          ambientCalled <- doesPathExist ambientMarker
          assert "ambient PATH Git shim was not invoked" (not ambientCalled)
            ExitSuccess (show ambientCalled) ""
  withEnv "ARCHIVE_GIT_EXECUTABLE" selected $ do
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
  withEnv "ARCHIVE_GIT_EXECUTABLE" "git" $ do
      result <- resolveGitExecutable
      assert "relative Git executable configuration fails closed" (isLeft result)
        ExitSuccess (show result) ""
  withEnv "ARCHIVE_GIT_ANNEX_EXECUTABLE" "git-annex" $ do
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

-- | The git snapshot checks, one case per check or row.
tests :: IO TestTree
tests = fmap (testGroup "git snapshot") $ sequence
  [ caseOf "typedPlannerErrorChecks" typedPlannerErrorChecks
  , caseOf "gitSnapshotHistoryChecks" (withGitHistoryRoot gitSnapshotHistoryChecks)
  , caseOf "gitSnapshotRootSwapChecks" (withGitHistoryRoot gitSnapshotRootSwapChecks)
  , caseOf "configuredGitExecutableChecks" (withGitHistoryRoot configuredGitExecutableChecks)
  ]
