{-# LANGUAGE OverloadedStrings #-}

-- | Checks of `archive init` (Archive.Door.Bootstrap): the layout it commits,
-- the roots it refuses, and its recovery from failure and interruption.
module BootstrapChecks
  ( tests
  ) where

import Archive.Door.Bootstrap (BootstrapEnvironment (..), initArchiveWith)
import Archive.Door.MutationError (MutationError (..))
import Control.Exception (AsyncException (UserInterrupt), throwIO, try)
import Data.IORef (newIORef, modifyIORef', readIORef)
import Fixtures
import Support
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
  , renameFile
  , renameDirectory
  , setOwnerExecutable
  , setPermissions
  )
import System.Environment (getEnvironment)
import System.Exit (ExitCode (ExitFailure, ExitSuccess))
import System.Posix.Files (setFileMode)
import System.Process
  ( CreateProcess (env)
  , proc
  , readCreateProcessWithExitCode
  , readProcessWithExitCode
  )
import Test.Tasty (TestTree, testGroup)

archiveCommandWithPath :: FilePath -> [String] -> String
                       -> IO (ExitCode, String, String)
archiveCommandWithPath prefix arguments input = do
  environment <- getEnvironment
  let withoutExecutable = filter ((/= "ARCHIVE_GIT_EXECUTABLE") . fst) environment
      process = (proc "archive" arguments)
        { env = Just (("ARCHIVE_GIT_EXECUTABLE", prefix) : withoutExecutable) }
  readCreateProcessWithExitCode process (input <> "\n")

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
  withEnv "ARCHIVE_GIT_ANNEX_EXECUTABLE" barrierAnnex $ do
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
  withEnv "ARCHIVE_GIT_ANNEX_EXECUTABLE" fakeAnnex $ do
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
  withEnv "ARCHIVE_GIT_ANNEX_EXECUTABLE" fakeAnnex $ do
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

-- | The bootstrap checks, one case per check or row.
tests :: IO TestTree
tests = fmap (testGroup "bootstrap") $ sequence
  [ caseOf "initChecks" (withTemporaryRoot initChecks)
  , caseOf "symlinkRootCheck" (withScratchRoot symlinkRootCheck)
  , caseOf "subprocessFailureCheck" (withScratchRoot subprocessFailureCheck)
  , caseOf "initMaintenanceChecks" (withProposalTemporaryRoot initMaintenanceChecks)
  , caseOf "interruptedInitCheck" (withProposalTemporaryRoot interruptedInitCheck)
  ]
