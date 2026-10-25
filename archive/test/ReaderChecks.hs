{-# LANGUAGE OverloadedStrings #-}

-- | Checks of the read-only commands (status, export, doctor, ls, show) and
-- `archive maintenance prune-packed` over a real archive, with the Git they
-- run: a pinned HEAD, noisy output, an inherited Git environment and a
-- decayed object.
module ReaderChecks
  ( tests
  ) where

import Archive.Door.MutationError (MutationError (..))
import Archive.Door.RecordCommit (withArchiveWriteLock)
import Data.List (sort)
import qualified Data.Text as Text
import Support
import System.Directory
  ( createDirectory
  , createDirectoryIfMissing
  , copyFile
  , doesFileExist
  , findExecutable
  , getPermissions
  , listDirectory
  , removeFile
  , removePathForcibly
  , renameDirectory
  , renameFile
  , setOwnerExecutable
  , setOwnerWritable
  , setPermissions
  )
import System.Environment (getEnvironment)
import System.Exit (ExitCode (ExitFailure, ExitSuccess))
import System.FilePath ((</>))
import System.Process
  ( CreateProcess (cwd, env)
  , proc
  , readCreateProcessWithExitCode
  , readProcessWithExitCode
  )
import System.Timeout (timeout)
import Test.Tasty (TestTree, testGroup)
import Test.Tasty.HUnit (testCase)

-- | The reader commands in sequence over one archive: each reads what the
-- one before it left, so they stay one case.
sharedArchiveChecks :: FilePath -> IO ()
sharedArchiveChecks root = do
  initialize root
  statusReadsHead root
  exportIsCompleteAndPinned root
  doctorIsPublic root
  prunePackedCommand root
  lsIsPublic root
  showEmptyIsComplete root
  showFindsCommittedObject root
  pinnedHeadIsUsed root

statusReadsHead :: FilePath -> IO ()
statusReadsHead root = do
  writeFile (root </> "archive.json") "not a committed manifest\n"
  createDirectoryIfMissing True (root </> "records")
  writeFile (root </> "records" </> "sha256-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.json") "not a record\n"
  (code, output, errors) <- command root ["status"]
  assert "status ignores worktree mutations" (code == ExitSuccess
    && contains "\"record_count\":2" output) output errors

doctorIsPublic :: FilePath -> IO ()
doctorIsPublic root = do
  (code, output, errors) <- command root ["doctor"]
  assert "doctor is a complete public projection" (code == ExitSuccess
    && contains "\"issues\":[]" output
    && contains "\"contracts\":{\"status\":\"passed\"}" output
    && contains "\"current\":{\"detail\":\"no current rule is defined\"" output) output errors

-- | Door no longer re-checks old objects on every admission (ADR 0052), so
-- doctor must find a decayed one.
decayedObjectIsReported :: FilePath -> IO ()
decayedObjectIsReported root = do
  (beforeCode, beforeOutput, beforeErrors) <- command root ["doctor"]
  assert "doctor passes before an object decays (anchor)" (beforeCode == ExitSuccess)
    beforeOutput beforeErrors
  (_, recordPaths, _) <- readCreateProcessWithExitCode
    ((proc "git" ["ls-tree", "-r", "--name-only", "HEAD", "--", "records"]) { cwd = Just root }) ""
  -- Git may pack objects on its own; corrupt the first record object that is
  -- still a loose file.
  candidates <- mapM (\path -> do
      (_, blobOutput, _) <- readCreateProcessWithExitCode
        ((proc "git" ["rev-parse", "HEAD:" <> path]) { cwd = Just root }) ""
      let blob = takeWhile (/= '\n') blobOutput
          file = root </> ".git" </> "objects" </> take 2 blob </> drop 2 blob
      present <- doesFileExist file
      pure [file | present]) (lines recordPaths)
  objectFile <- case concat candidates of
    file : _ -> pure file
    [] -> do
      assert "the decay fixture finds a loose record object" False recordPaths ""
      pure ""
  permissions <- getPermissions objectFile
  setPermissions objectFile (setOwnerWritable True permissions)
  writeFile objectFile "not a zlib stream"
  (code, output, errors) <- command root ["doctor"]
  assert "doctor reports a decayed Git object" (code /= ExitSuccess
    && contains "Git object check failed" output) output errors

lsIsPublic :: FilePath -> IO ()
lsIsPublic root = do
  (code, output, errors) <- command root ["ls"]
  assert "ls emits the committed records" (code == ExitSuccess
    && contains "\"records\":[" output
    && contains "\"shape\":\"entity\"" output) output errors

showEmptyIsComplete :: FilePath -> IO ()
showEmptyIsComplete root = do
  (code, output, errors) <- command root
    ["show", "urn:uuid:00000000-0000-4000-8000-000000000099"]
  assert "show distinguishes a complete empty result" (code == ExitSuccess
    && contains "\"records\":[]" output) output errors

showFindsCommittedObject :: FilePath -> IO ()
showFindsCommittedObject root = do
  (listCode, listOutput, listErrors) <- command root ["ls"]
  assert "ls succeeds before show lookup" (listCode == ExitSuccess) listOutput listErrors
  case jsonStringField "object_id" listOutput of
    Nothing -> assert "ls exposes an object identifier for show" False listOutput listErrors
    Just objectId -> do
      (code, output, errors) <- command root ["show", objectId]
      assert "show returns a committed object from the public ls result"
        (code == ExitSuccess && contains ("\"object_id\":\"" <> objectId <> "\"") output)
        output errors

exportIsCompleteAndPinned :: FilePath -> IO ()
exportIsCompleteAndPinned root = do
  (code, output, errors) <- command root ["export"]
  assert "export includes a pinned complete snapshot and raw evidence"
    (code == ExitSuccess
      && contains "\"status\":\"ok\"" output
      && contains "\"snapshot\":" output
      && contains "\"archive_id\":\"urn:uuid:" output
      && contains "\"context\":{" output
      && contains "\"records\":[{" output
      && contains "\"contracts\":[{" output
      && null errors)
    output errors

prunePackedCommand :: FilePath -> IO ()
prunePackedCommand root = do
  gitPath <- findExecutable "git" >>= maybe
    (ioError (userError "prune-packed test cannot find Git")) pure
  prepared <- withArchiveWriteLock root $ \_ -> do
    (code, _, errors) <- readProcessWithExitCode gitPath ["-C", root, "gc", "--prune=never"] ""
    case code of
      ExitSuccess -> duplicatePackedObjects root gitPath
      _ -> pure (Left (MutationOperationFailure (Text.pack errors)))
  assert "packed and duplicate loose fixture is prepared while holding the archive lock"
    (prepared == Right ()) (show prepared) ""
  indexes <- listDirectory (root </> ".git" </> "objects" </> "pack")
  assert "maintenance fixture has at least one pack index"
    (any (endsWith ".idx") indexes) (show indexes) ""
  before <- refsSnapshot root
  looseBefore <- looseObjectCount root
  assert "fixture has loose duplicates of the packed objects" (looseBefore > 0)
    (show looseBefore) ""
  preflightFailure <- commandWithFakeGit root "prune-verify-failure"
    ("for argument in \"$@\"; do\n"
      <> "  if [ \"$argument\" = verify-pack ]; then echo injected verify-pack failure >&2; exit 71; fi\n"
      <> "done\nexec " <> shellQuote gitPath <> " \"$@\"\n")
  refsAfterPreflightFailure <- refsSnapshot root
  looseAfterPreflightFailure <- looseObjectCount root
  assert "verify-pack failure refuses before pruning loose duplicates and preserves refs"
    (fst3 preflightFailure /= ExitSuccess
      && contains "could not verify Git packs before prune-packed" (snd3 preflightFailure)
      && before == refsAfterPreflightFailure && looseBefore == looseAfterPreflightFailure)
    (show preflightFailure <> "\nloose=" <> show looseAfterPreflightFailure
      <> "\n" <> refsAfterPreflightFailure) ""
  let movedPackDirectory = root </> ".git" </> "objects" </> "pack-prune-fixture-original"
      swappedMarker = root </> ".fixture-pack-directory-swapped"
  packSwapFailure <- commandWithFakeGit root "prune-pack-directory-swap"
    ("verified=0\nfor argument in \"$@\"; do\n"
      <> "  if [ \"$argument\" = verify-pack ]; then verified=1; fi\n"
      <> "done\n" <> shellQuote gitPath <> " \"$@\"\nstatus=$?\n"
      <> "if [ \"$verified\" = 1 ] && [ \"$status\" = 0 ] && [ ! -e "
      <> shellQuote swappedMarker <> " ]; then\n"
      <> "  mv objects/pack objects/pack-prune-fixture-original\n"
      <> "  mkdir objects/pack\n  touch " <> shellQuote swappedMarker <> "\nfi\n"
      <> "exit \"$status\"\n")
  removePathForcibly (root </> ".git" </> "objects" </> "pack")
  renameDirectory movedPackDirectory (root </> ".git" </> "objects" </> "pack")
  removeFile swappedMarker
  refsAfterPackSwap <- refsSnapshot root
  looseAfterPackSwap <- looseObjectCount root
  assert "pack-directory replacement is detected before prune-packed can run"
    (fst3 packSwapFailure /= ExitSuccess
      && contains "pinned objects/pack directory identity changed" (snd3 packSwapFailure)
      && before == refsAfterPackSwap && looseBefore == looseAfterPackSwap)
    (show packSwapFailure <> "\nloose=" <> show looseAfterPackSwap
      <> "\n" <> refsAfterPackSwap) ""
  pruneFailure <- commandWithFakeGit root "prune-command-failure"
    ("pruned=0\nfor argument in \"$@\"; do\n"
      <> "  if [ \"$argument\" = prune-packed ]; then pruned=1; fi\n"
      <> "done\n" <> shellQuote gitPath <> " \"$@\"\nstatus=$?\n"
      <> "if [ \"$pruned\" = 1 ] && [ \"$status\" = 0 ]; then exit 74; fi\n"
      <> "exit \"$status\"\n")
  refsAfterPruneFailure <- refsSnapshot root
  looseAfterPruneFailure <- looseObjectCount root
  assert "a prune-packed failure after deletion is reported with refs unchanged"
    (fst3 pruneFailure /= ExitSuccess
      && contains "git prune-packed exited 74" (snd3 pruneFailure)
      && before == refsAfterPruneFailure && looseAfterPruneFailure == 0)
    (show pruneFailure <> "\nloose=" <> show looseAfterPruneFailure
      <> "\n" <> refsAfterPruneFailure) ""
  duplicatedAgain <- withArchiveWriteLock root $ \_ -> duplicatePackedObjects root gitPath
  assert "packed duplicates can be recreated for the successful prune"
    (duplicatedAgain == Right ()) (show duplicatedAgain) ""
  looseBeforeSuccess <- looseObjectCount root
  let verificationLog = root </> ".fixture-verify-pack-calls"
  writeFile verificationLog ""
  (code, output, errors) <- commandWithFakeGit root "prune-verify-all-packs"
    ("verify=0\npack_index=\nfor argument in \"$@\"; do\n"
      <> "  if [ \"$argument\" = verify-pack ]; then verify=1; pack_index=; elif [ \"$verify\" = 1 ]; then pack_index=\"$argument\"; verify=2; fi\n"
      <> "done\nif [ \"$verify\" = 2 ]; then echo \"$pack_index\" >> "
      <> shellQuote verificationLog <> "; fi\nexec " <> shellQuote gitPath <> " \"$@\"\n")
  verifiedPaths <- lines <$> readFile verificationLog
  removeFile verificationLog
  refsAfterSuccess <- refsSnapshot root
  let expectedVerifiedPaths = concat (replicate 2
        ["objects/pack/" <> index | index <- filter (endsWith ".idx") indexes])
  assert "archive maintenance prune-packed succeeds with verified packs"
    (code == ExitSuccess && contains "\"status\":\"ok\"" output
      && before == refsAfterSuccess && looseBeforeSuccess > 0
      && sort verifiedPaths == sort expectedVerifiedPaths)
    (output <> "\n" <> show before <> "\n" <> show refsAfterSuccess
      <> "\nverified=" <> show verifiedPaths) errors
  looseAfterSuccess <- looseObjectCount root
  assert "successful prune-packed removes duplicate loose copies"
    (looseAfterSuccess == 0) (show looseAfterSuccess) ""
  duplicatedAgainForCut <- withArchiveWriteLock root $ \_ -> duplicatePackedObjects root gitPath
  assert "packed duplicates can be recreated for post-prune barrier failure"
    (duplicatedAgainForCut == Right ()) (show duplicatedAgainForCut) ""
  postflightFailure <- commandWithFakeGit root "prune-post-barrier-failure"
    ("pruned=0\nfor argument in \"$@\"; do\n"
      <> "  if [ \"$argument\" = prune-packed ]; then pruned=1; fi\n"
      <> "done\n" <> shellQuote gitPath <> " \"$@\"\nstatus=$?\n"
      <> "if [ \"$pruned\" = 1 ] && [ \"$status\" = 0 ]; then ln -s /dev/null objects/prune-packed-fixture-invalid; fi\n"
      <> "exit \"$status\"\n")
  removeFile (root </> ".git" </> "objects" </> "prune-packed-fixture-invalid")
  refsAfterPostflightFailure <- refsSnapshot root
  looseAfterPostflightFailure <- looseObjectCount root
  assert "post-prune barrier failure is reported after the prune and refs remain unchanged"
    (fst3 postflightFailure /= ExitSuccess
      && contains "could not establish the post-prune Git durability baseline" (snd3 postflightFailure)
      && before == refsAfterPostflightFailure && looseAfterPostflightFailure == 0)
    (show postflightFailure <> "\nloose=" <> show looseAfterPostflightFailure
      <> "\n" <> refsAfterPostflightFailure) ""
  (retryCode, retryOutput, retryErrors) <- command root ["maintenance", "prune-packed"]
  assert "maintenance retry succeeds after the post-prune barrier fault is removed"
    (retryCode == ExitSuccess && contains "\"status\":\"ok\"" retryOutput)
    retryOutput retryErrors
  branch <- gitLine root gitPath ["symbolic-ref", "-q", "HEAD"]
  headObject <- gitLine root gitPath ["rev-parse", "--verify", "HEAD^{commit}"]
  alternateObject <- gitLine root gitPath ["rev-parse", "--verify", "refs/heads/git-annex^{commit}"]
  assert "ref-change fixture has a distinct valid commit to install"
    (headObject /= alternateObject) (headObject <> "\n" <> alternateObject) ""
  refChange <- commandWithFakeGit root "prune-ref-change"
    ("pruned=0\nfor argument in \"$@\"; do\n"
      <> "  if [ \"$argument\" = prune-packed ]; then pruned=1; fi\n"
      <> "done\n" <> shellQuote gitPath <> " \"$@\"\nstatus=$?\n"
      <> "if [ \"$pruned\" = 1 ] && [ \"$status\" = 0 ]; then "
      <> shellQuote gitPath <> " --git-dir=. update-ref " <> shellQuote branch
      <> " " <> shellQuote alternateObject <> "; fi\nexit \"$status\"\n")
  refSnapshotDuringChange <- refsSnapshot root
  (restoreCode, _, restoreErrors) <- readProcessWithExitCode gitPath
    ["-C", root, "update-ref", branch, headObject] ""
  refsAfterRestoration <- refsSnapshot root
  assert "a ref change during prune-packed is refused and detected"
    (fst3 refChange /= ExitSuccess
      && contains "Git refs changed while prune-packed held the archive lock" (snd3 refChange)
      && refSnapshotDuringChange /= before
      && restoreCode == ExitSuccess && refsAfterRestoration == before)
    (show refChange <> "\nchanged=" <> refSnapshotDuringChange
      <> "\nrestored=" <> refsAfterRestoration) restoreErrors
  (finalCode, finalOutput, finalErrors) <- command root ["maintenance", "prune-packed"]
  assert "maintenance succeeds after the ref-change fixture restores the ref"
    (finalCode == ExitSuccess && contains "\"status\":\"ok\"" finalOutput)
    finalOutput finalErrors
  gitChecked root ["rev-list", "--objects", "--missing=error", "--quiet", "--all"]
  gitChecked root ["fsck", "--full", "--strict", "--no-reflogs", "--no-dangling", "--no-progress"]
  where
    endsWith suffix value = reverse suffix `prefixOf` reverse value
    fst3 (code, _, _) = code
    snd3 (_, output, errors) = output <> errors

duplicatePackedObjects :: FilePath -> FilePath -> IO (Either MutationError ())
duplicatePackedObjects root gitPath = do
  let packDirectory = root </> ".git" </> "objects" </> "pack"
  names <- listDirectory packDirectory
  case filter (endsWith ".pack") names of
    [] -> pure (Left (MutationOperationFailure (Text.pack "fixture has no pack to duplicate")))
    packName : _ -> do
      let indexName = take (length packName - length (".pack" :: String)) packName <> ".idx"
          packPath = packDirectory </> packName
          indexPath = packDirectory </> indexName
          savedPack = root </> ".fixture-prune-packed.pack"
          savedIndex = root </> ".fixture-prune-packed.idx"
          unpack = shellQuote gitPath <> " -C " <> shellQuote root
            <> " unpack-objects -r < " <> shellQuote savedPack
      copyFile packPath savedPack
      copyFile indexPath savedIndex
      removeFile packPath
      removeFile indexPath
      (code, _, errors) <- readCreateProcessWithExitCode (proc "sh" ["-c", unpack]) ""
      renameFile savedPack packPath
      renameFile savedIndex indexPath
      pure $ if code == ExitSuccess then Right ()
        else Left (MutationOperationFailure (Text.pack errors))
  where
    endsWith suffix value = reverse suffix `prefixOf` reverse value

looseObjectCount :: FilePath -> IO Int
looseObjectCount root = do
  (code, output, errors) <- readCreateProcessWithExitCode
    (proc "git" ["-C", root, "count-objects", "-v"]) ""
  assert "git count-objects succeeds" (code == ExitSuccess) output errors
  case [drop (length ("count: " :: String)) line | line <- lines output, "count: " `prefixOf` line] of
    count : _ -> case reads count of
      [(number, "")] -> pure number
      _ -> assert "git count-objects returns a numeric count" False output "" >> pure 0
    [] -> assert "git count-objects reports its loose object count" False output "" >> pure 0

commandWithFakeGit :: FilePath -> String -> String
                   -> IO (ExitCode, String, String)
commandWithFakeGit root label wrapperBody = do
  let fakeDirectory = root </> (".test-" <> label)
      fakeGit = fakeDirectory </> "git"
  createDirectoryIfMissing True fakeDirectory
  writeFile fakeGit ("#!/bin/sh\n" <> wrapperBody)
  permissions <- getPermissions fakeGit
  setPermissions fakeGit (setOwnerExecutable True permissions)
  environment <- getEnvironment
  let clean = ("ARCHIVE_GIT_EXECUTABLE", fakeGit)
        : filter ((/= "ARCHIVE_GIT_EXECUTABLE") . fst) environment
      process = (proc "archive" ["maintenance", "prune-packed"])
        { cwd = Just root, env = Just clean }
  result <- readCreateProcessWithExitCode process ""
  removePathForcibly fakeDirectory
  pure result

refsSnapshot :: FilePath -> IO String
refsSnapshot root = do
  (refCode, refs, refErrors) <- readCreateProcessWithExitCode
    (proc "git" ["-C", root, "for-each-ref", "--format=%(refname) %(objectname)"]) ""
  (headCode, headName, headErrors) <- readCreateProcessWithExitCode
    (proc "git" ["-C", root, "symbolic-ref", "-q", "HEAD"]) ""
  (objectCode, headObject, objectErrors) <- readCreateProcessWithExitCode
    (proc "git" ["-C", root, "rev-parse", "--verify", "HEAD^{commit}"]) ""
  assert "ref snapshot commands succeed" (refCode == ExitSuccess
    && headCode == ExitSuccess && objectCode == ExitSuccess)
    (refs <> headName <> headObject) (refErrors <> headErrors <> objectErrors)
  pure (refs <> "HEAD " <> headName <> headObject)

gitLine :: FilePath -> FilePath -> [String] -> IO String
gitLine root gitPath arguments = do
  (code, output, errors) <- readCreateProcessWithExitCode
    (proc gitPath ("-C" : root : arguments)) ""
  assert ("git " <> unwords arguments <> " succeeds") (code == ExitSuccess) output errors
  pure (takeWhile (/= '\n') output)

noisyGitDoesNotDeadlock :: FilePath -> IO ()
noisyGitDoesNotDeadlock root = do
  let fakeBin = root </> ".test-noisy-git"
      fakeGit = fakeBin </> "git"
  createDirectory fakeBin
  (whichCode, realGitOutput, whichErrors) <- readCreateProcessWithExitCode
    (proc "which" ["git"]) ""
  assert "the noisy Git test finds the real Git executable"
    (whichCode == ExitSuccess) realGitOutput whichErrors
  let realGit = takeWhile (`notElem` ['\n', '\r']) realGitOutput
  writeFile fakeGit ("#!/bin/sh\nhead -c 131072 /dev/zero >&2\nexec "
    <> shellQuote realGit <> " \"$@\"\n")
  permissions <- getPermissions fakeGit
  setPermissions fakeGit (setOwnerExecutable True permissions)
  environment <- getEnvironment
  let clean = ("ARCHIVE_GIT_EXECUTABLE", fakeGit)
        : filter ((/= "ARCHIVE_GIT_EXECUTABLE") . fst) environment
      process = (proc "archive" ["status"])
        { cwd = Just root
        , env = Just clean
        }
  result <- timeout 5000000 (readCreateProcessWithExitCode process "")
  case result of
    Nothing -> assert "Git stdout and stderr are drained without deadlock" False "" "timed out"
    Just (code, output, errors) ->
      assert "Git stdout and stderr are drained without deadlock"
        (code == ExitSuccess) output errors
  removePathForcibly fakeBin

inheritedGitEnvironmentIsIgnored :: FilePath -> FilePath -> IO ()
inheritedGitEnvironmentIsIgnored first second = do
  (_, firstOutput, firstErrors) <- command first ["status"]
  (_, secondOutput, secondErrors) <- command second ["status"]
  case (jsonStringField "archive_id" firstOutput, jsonStringField "archive_id" secondOutput) of
    (Just firstId, Just secondId) -> do
      assert "the Git-environment test uses distinct archives"
        (firstId /= secondId) (firstOutput <> secondOutput) (firstErrors <> secondErrors)
      environment <- getEnvironment
      let clean = filter ((/= "GIT_DIR") . fst) environment
          process = (proc "archive" ["status"])
            { cwd = Just first
            , env = Just (("GIT_DIR", second </> ".git") : clean)
            }
      (code, output, errors) <- readCreateProcessWithExitCode process ""
      assert "reader ignores inherited Git repository redirection"
        (code == ExitSuccess
          && contains ("\"archive_id\":\"" <> firstId <> "\"") output
          && not (contains ("\"archive_id\":\"" <> secondId <> "\"") output))
        output errors
    _ -> assert "both Git-environment archives expose identifiers" False
      (firstOutput <> secondOutput) (firstErrors <> secondErrors)

pinnedHeadIsUsed :: FilePath -> IO ()
pinnedHeadIsUsed root = do
  let fakeBin = root </> ".test-git-wrapper"
      fakeGit = fakeBin </> "git"
      logPath = fakeBin </> "calls"
  createDirectory fakeBin
  (whichCode, realGitOutput, whichErrors) <- readCreateProcessWithExitCode
    (proc "which" ["git"]) ""
  assert "the snapshot test finds the real Git executable"
    (whichCode == ExitSuccess) realGitOutput whichErrors
  let realGit = takeWhile (`notElem` ['\n', '\r']) realGitOutput
  writeFile fakeGit ("#!/bin/sh\nprintf '%s\\n' \"$*\" >> " <> shellQuote logPath
    <> "\nexec " <> shellQuote realGit <> " \"$@\"\n")
  permissions <- getPermissions fakeGit
  setPermissions fakeGit (setOwnerExecutable True permissions)
  environment <- getEnvironment
  let clean = ("ARCHIVE_GIT_EXECUTABLE", fakeGit)
        : filter ((/= "ARCHIVE_GIT_EXECUTABLE") . fst) environment
      process = (proc "archive" ["status"])
        { cwd = Just root
        , env = Just clean
        }
  (code, output, errors) <- readCreateProcessWithExitCode process ""
  calls <- readFile logPath
  (headCode, headOutput, headErrors) <- readCreateProcessWithExitCode
    ((proc realGit ["rev-parse", "--verify", "HEAD^{commit}"]) { cwd = Just root }) ""
  assert "the snapshot test resolves the committed head"
    (headCode == ExitSuccess) headOutput headErrors
  let snapshot = takeWhile (`notElem` ['\n', '\r']) headOutput
  (treeCode, treeOutput, treeErrors) <- readCreateProcessWithExitCode
    ((proc realGit ["ls-tree", "-r", "--full-tree", "--object-only", snapshot]) { cwd = Just root }) ""
  assert "the snapshot test lists the committed tree"
    (treeCode == ExitSuccess) treeOutput treeErrors
  let pinnedObjects = lines treeOutput
      callLines = lines calls
      treeCalls = filter ("ls-tree " `prefixOf`) callLines
      objectCalls = filter ("show " `prefixOf`) callLines
      objectArguments = concatMap (drop 2 . words) objectCalls
  -- Every file is read from the one resolved commit: its tree is listed by
  -- that commit, and file bytes are read only by the object names in that
  -- tree. Reads are in bulk: one listing and one object read for the whole
  -- archive, not one subprocess per file.
  assert "one resolved commit pins every reader subprocess"
    (code == ExitSuccess
      && treeCalls == ["ls-tree -z -r -l --full-tree " <> snapshot]
      && length objectCalls == 1
      && all ("show --no-textconv " `prefixOf`) objectCalls
      && not (null objectArguments)
      && all (`elem` pinnedObjects) objectArguments
      && not (contains "HEAD:" calls)
      && not (contains (snapshot <> ":") calls))
    (output <> "\ngit calls:\n" <> calls) errors
  removePathForcibly fakeBin

currentBoundary :: IO ()
currentBoundary = do
  (code, output, errors) <- command "." ["current"]
  assert "current fails at the documented boundary" (code == ExitFailure 2
    && contains "unsupported: no current rule is defined" output
    && null errors) output errors

-- | The readers checks, one case each.
tests :: TestTree
tests = testGroup "readers"
  [ testCase "currentBoundary" currentBoundary
  , testCase "sharedArchiveChecks" (withArchive sharedArchiveChecks)
  , testCase "noisyGitDoesNotDeadlock" (initialized noisyGitDoesNotDeadlock)
  , testCase "decayedObjectIsReported" (initialized decayedObjectIsReported)
  , testCase "inheritedGitEnvironmentIsIgnored" $ withArchive $ \first -> withArchive $ \second -> do
      initialize first
      initialize second
      inheritedGitEnvironmentIsIgnored first second
  ]
