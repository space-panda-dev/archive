{-# LANGUAGE ForeignFunctionInterface #-}
{-# LANGUAGE OverloadedStrings #-}

-- | Checks of the record writer (Archive.Door.RecordCommit): durability
-- barriers, record paths and modes, pathname swaps, index reservation, the
-- ref outcome, and the archive lock.
module RecordCommitChecks
  ( tests
  ) where

import Archive.Door.Json (decodeUnique)
import Archive.Door.MutationError (MutationError (..), mutationErrorMessage)
import Archive.Door.Proposal (readTransitionProposal)
import Archive.Door.RecordCommit
  ( commitRecordBatch
  , validateRecordPath
  , withArchiveWriteLock
  )
import Archive.Door.TransitionCommit (commitTransition)
import Control.Concurrent (forkIO, threadDelay)
import Control.Concurrent.MVar (newEmptyMVar, putMVar, takeMVar, tryReadMVar)
import Control.Exception (AsyncException (UserInterrupt), throwIO, try)
import Data.Aeson ((.=))
import qualified Data.Aeson as Aeson
import qualified Data.ByteString.Lazy.Char8 as LazyByteString
import Data.List (isPrefixOf)
import qualified Data.Text as Text
import Fixtures
import Foreign.C.Types (CInt (..))
import Support
import System.Directory
  ( createDirectory
  , createDirectoryIfMissing
  , createDirectoryLink
  , doesFileExist
  , doesPathExist
  , findExecutable
  , listDirectory
  , pathIsSymbolicLink
  , getPermissions
  , removePathForcibly
  , renameDirectory
  , setOwnerExecutable
  , setOwnerWritable
  , setPermissions
  )
import System.Environment (getEnvironment)
import System.Exit (ExitCode (ExitSuccess))
import System.Posix.Files (getFileStatus, isNamedPipe, setFileMode)
import System.Posix.IO
  ( OpenFileFlags (directory)
  , OpenMode (ReadOnly)
  , closeFd
  , defaultFileFlags
  , openFd
  )
import System.Posix.Types (Fd (..))
import System.Posix.User (getEffectiveUserID)
import System.Process
  ( CreateProcess (cwd, env)
  , proc
  , readCreateProcessWithExitCode
  , readProcessWithExitCode
  )
import Test.Tasty (TestTree, testGroup)

foreign import ccall safe "archive_fullsync_fd"
  c_fullsyncFd :: CInt -> IO CInt

foreign import ccall safe "archive_fullsync_directory_fd"
  c_fullsyncDirectoryFd :: CInt -> IO CInt

foreign import ccall safe "archive_sync_tree"
  c_syncTree :: CInt -> IO CInt

recordPathChecks :: Recorder -> IO ()
recordPathChecks check = do
  let valid = recordRelative zeroDigest
  check "the record layout fans out by the digest's first two character pairs (ADR 0053)"
    (valid == "records/00/00/sha256-0000000000000000000000000000000000000000000000000000000000000000.json")
    ExitSuccess valid ""
  check "RecordCommit accepts a digest-addressed relative record path"
    (either (const False) (const True) (validateRecordPath valid))
    ExitSuccess "" ""
  check "RecordCommit refuses the flat archive/1 record path"
    (isLeft (validateRecordPath "records/sha256-0000000000000000000000000000000000000000000000000000000000000000.json"))
    ExitSuccess "" ""
  check "RecordCommit refuses a fan-out that does not match the digest"
    (isLeft (validateRecordPath "records/01/00/sha256-0000000000000000000000000000000000000000000000000000000000000000.json"))
    ExitSuccess "" ""
  check "RecordCommit rejects traversal at the mutation seam"
    (isLeft (validateRecordPath "records/../outside.json"))
    ExitSuccess "" ""
  check "RecordCommit rejects absolute paths at the mutation seam"
    (isLeft (validateRecordPath "/tmp/outside.json"))
    ExitSuccess "" ""
  check "RecordCommit rejects a malformed digest path at the mutation seam"
    (isLeft (validateRecordPath "records/not-a-digest.json"))
    ExitSuccess "" ""

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
  headBefore <- gitHead root
  commitsBefore <- gitCommitCount root
  recordsBefore <- recordCount root
  loadedProposal <- readTransitionProposal proposal >>= either
    (ioError . userError . Text.unpack) pure
  typedResult <- withEnv "ARCHIVE_GIT_EXECUTABLE" fakeGit
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

-- | The record commit checks, one case per check or row.
tests :: IO TestTree
tests = fmap (testGroup "record commit") $ sequence
  [ caseOf "directoryBarrierChecks" (withTemporaryRoot directoryBarrierChecks)
  , rowsOf "recordPathChecks" recordPathChecks
  , caseOf "fileModeChecks" (withProposalTemporaryRoot fileModeChecks)
  , caseOf "persistentRecordParentSymlinkCheck" (withProposalTemporaryRoot persistentRecordParentSymlinkCheck)
  , caseOf "persistentRecordParentSwapCheck" (withProposalTemporaryRoot persistentRecordParentSwapCheck)
  , caseOf "persistentRecordCleanupOwnershipCheck" (withProposalTemporaryRoot persistentRecordCleanupOwnershipCheck)
  , caseOf "persistentRecordCleanupFifoCheck" (withProposalTemporaryRoot persistentRecordCleanupFifoCheck)
  , caseOf "persistentGitDirectorySwapCheck" (withProposalTemporaryRoot persistentGitDirectorySwapCheck)
  , caseOf "persistentArchiveRootSwapCheck" (withProposalTemporaryRoot persistentArchiveRootSwapCheck)
  , caseOf "pinnedGitPermissionCheck" (withProposalTemporaryRoot pinnedGitPermissionCheck)
  , caseOf "indexReservationInterruptionCheck" (withProposalTemporaryRoot indexReservationInterruptionCheck)
  , caseOf "indexReservationIdentityCheck" (withProposalTemporaryRoot indexReservationIdentityCheck)
  , caseOf "uncertainRefOutcomeCheck" (withProposalTemporaryRoot uncertainRefOutcomeCheck)
  , caseOf "postRefSyncFailureCheck" (withProposalTemporaryRoot postRefSyncFailureCheck)
  , caseOf "processLockCheck" (withProposalTemporaryRoot processLockCheck)
  ]
