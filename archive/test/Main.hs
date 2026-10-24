{-# LANGUAGE OverloadedStrings #-}

module Main (main) where

import Control.Exception (bracket)
import Data.Aeson ((.=))
import qualified Data.Aeson as Aeson
import qualified Data.Aeson.Key as Key
import qualified Data.Aeson.KeyMap as KeyMap
import qualified Data.ByteString.Lazy.Char8 as LazyChar8
import Data.Foldable (toList)
import qualified Data.Set as Set
import Archive.Door.RecordCommit (withArchiveWriteLock)
import Archive.Door.MutationError (MutationError (..))
import qualified Data.Text as Text
import Data.List (sort)
import GHC.Stack (HasCallStack)
import System.Directory
  ( createDirectory
  , createDirectoryIfMissing
  , copyFile
  , doesFileExist
  , findExecutable
  , getPermissions
  , getTemporaryDirectory
  , listDirectory
  , removeFile
  , removePathForcibly
  , renameDirectory
  , renameFile
  , setOwnerExecutable
  , setOwnerWritable
  , setPermissions
  )
import System.Environment (getEnvironment, setEnv)
import System.Exit (ExitCode (ExitFailure, ExitSuccess))
import System.FilePath ((</>))
import System.FilePath (takeDirectory)
import System.IO (hClose, openTempFile)
import System.Process
  ( CreateProcess (cwd, env)
  , proc
  , readCreateProcessWithExitCode
  , readProcessWithExitCode
  )
import System.Timeout (timeout)
import Test.Tasty (defaultMain, testGroup)
import Test.Tasty.HUnit (assertFailure, testCase)

main :: IO ()
main = do
  gitPath <- findExecutable "git" >>= maybe
    (ioError (userError "tests require an installed Git executable")) pure
  annexPath <- findExecutable "git-annex" >>= maybe
    (ioError (userError "tests require an installed git-annex executable")) pure
  setEnv "ARCHIVE_GIT_EXECUTABLE" gitPath
  setEnv "ARCHIVE_GIT_ANNEX_EXECUTABLE" annexPath
  -- One case per check, in the old order, each on its own initialized
  -- archive except the reader commands, which share one. The cases share the
  -- process environment, so they run one at a time (no -threaded).
  defaultMain $ testGroup "archive-test"
    [ testCase "currentBoundary" currentBoundary
    , testCase "sharedArchiveChecks" (withArchive sharedArchiveChecks)
    , testCase "exportSinceChecks" (initialized exportSinceChecks)
    , testCase "repackChecks" (initialized repackChecks)
    , testCase "missingContractIsIncomplete" (initialized missingContractIsIncomplete)
    , testCase "malformedRecordRetainsGoodRecords" (initialized malformedRecordRetainsGoodRecords)
    , testCase "missingContextIsIncomplete" (initialized missingContextIsIncomplete)
    , testCase "missingVersionIsIncomplete" (initialized missingVersionIsIncomplete)
    , testCase "missingSpecificationIsIncomplete" (initialized missingSpecificationIsIncomplete)
    , testCase "noncanonicalManifestIsIncomplete" (initialized noncanonicalManifestIsIncomplete)
    , testCase "malformedManifestRetainsRecords" (initialized malformedManifestRetainsRecords)
    , testCase "missingManifestRetainsRecords" (initialized missingManifestRetainsRecords)
    , testCase "noisyGitDoesNotDeadlock" (initialized noisyGitDoesNotDeadlock)
    , testCase "decayedObjectIsReported" (initialized decayedObjectIsReported)
    , testCase "inheritedGitEnvironmentIsIgnored" $ withArchive $ \first -> withArchive $ \second -> do
        initialize first
        initialize second
        inheritedGitEnvironmentIsIgnored first second
    ]

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

-- | A check on a freshly initialized archive of its own.
initialized :: (FilePath -> IO ()) -> IO ()
initialized check = withArchive $ \root -> initialize root >> check root

withArchive :: (FilePath -> IO ()) -> IO ()
withArchive action = do
  temporary <- getTemporaryDirectory
  bracket (newDirectory temporary) removePathForcibly action
  where
    newDirectory directory = do
      (path, handle) <- openTempFile directory "archive-reader"
      hClose handle
      removeFile path
      createDirectory path
      pure path

initialize :: FilePath -> IO ()
initialize root = do
  (code, output, errors) <- command (parent root) ["init", "--operator", "alice", root]
  assert "archive init succeeds" (code == ExitSuccess) output errors

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

missingContractIsIncomplete :: FilePath -> IO ()
missingContractIsIncomplete root = do
  names <- listDirectory (root </> "contracts")
  gitChecked root (["rm", "--"] <> map ("contracts/" <>) names)
  gitChecked root ["commit", "-m", "test missing committed contracts"]
  (code, output, errors) <- command root ["status"]
  assert "missing committed contracts are incomplete and retain usable records"
    (code == ExitFailure 2
      && contains "\"status\":\"incomplete\"" output
      && contains "\"record_count\":2" output
      && contains "contract tree is unavailable at HEAD" output)
    output errors
  (exportCode, exportOutput, exportErrors) <- command root ["export"]
  assert "export fails without partial stdout when a cited contract is missing"
    (exportCode == ExitFailure 2 && null exportOutput && not (null exportErrors))
    exportOutput exportErrors

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

malformedRecordRetainsGoodRecords :: FilePath -> IO ()
malformedRecordRetainsGoodRecords root = do
  let relative = "records/aa/aa/sha256-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.json"
  createDirectoryIfMissing True (root </> "records" </> "aa" </> "aa")
  writeFile (root </> relative) "not JSON\n"
  gitChecked root ["add", "--", relative]
  gitChecked root ["commit", "-m", "test malformed committed record"]
  (code, output, errors) <- command root ["ls"]
  assert "one malformed committed record does not erase usable records"
    (code == ExitFailure 2
      && contains "\"status\":\"incomplete\"" output
      && contains "\"record_count\":2" output
      && contains "\"kind\":\"parse\"" output)
    output errors

missingContextIsIncomplete :: FilePath -> IO ()
missingContextIsIncomplete root = do
  gitChecked root ["rm", "--", "spec/0.0.11/context.jsonld"]
  gitChecked root ["commit", "-m", "test missing committed context"]
  (code, output, errors) <- command root ["status"]
  assert "a missing selected context is incomplete rather than absent"
    (code == ExitFailure 2
      && contains "\"context_present\":false" output
      && contains "\"record_count\":2" output
      && contains "\"kind\":\"missing_tree\"" output)
    output errors

missingVersionIsIncomplete :: FilePath -> IO ()
missingVersionIsIncomplete root = do
  gitChecked root ["rm", "--", "spec/0.0.11/VERSION"]
  gitChecked root ["commit", "-m", "test missing committed VERSION"]
  (code, output, errors) <- command root ["status"]
  assert "a missing committed VERSION is an incomplete archive"
    (code == ExitFailure 2
      && contains "spec/0.0.11/VERSION" output
      && contains "\"record_count\":2" output)
    output errors

missingSpecificationIsIncomplete :: FilePath -> IO ()
missingSpecificationIsIncomplete root = do
  gitChecked root ["rm", "--", "spec/0.0.11/archive-spec.md"]
  gitChecked root ["commit", "-m", "test missing committed specification"]
  (code, output, errors) <- command root ["status"]
  assert "a missing copied specification is an incomplete archive"
    (code == ExitFailure 2
      && contains "spec/0.0.11/archive-spec.md" output
      && contains "\"record_count\":2" output)
    output errors

noncanonicalManifestIsIncomplete :: FilePath -> IO ()
noncanonicalManifestIsIncomplete root = do
  original <- readFile (root </> "archive.json")
  let replacement = root </> "archive.json.test-replacement"
  writeFile replacement (" " <> original)
  renameFile replacement (root </> "archive.json")
  gitChecked root ["add", "--", "archive.json"]
  gitChecked root ["commit", "-m", "test noncanonical manifest"]
  (code, output, errors) <- command root ["status"]
  assert "a noncanonical committed manifest is incomplete and records remain visible"
    (code == ExitFailure 2
      && contains "committed JSON is not RFC 8785 canonical" output
      && contains "\"record_count\":2" output)
    output errors

malformedManifestRetainsRecords :: FilePath -> IO ()
malformedManifestRetainsRecords root = do
  let replacement = root </> "archive.json.test-replacement"
  writeFile replacement "not JSON\n"
  renameFile replacement (root </> "archive.json")
  gitChecked root ["add", "--", "archive.json"]
  gitChecked root ["commit", "-m", "test malformed manifest"]
  (code, output, errors) <- command root ["status"]
  assert "a malformed manifest is incomplete without erasing committed records"
    (code == ExitFailure 2
      && contains "\"kind\":\"parse\"" output
      && contains "\"record_count\":2" output)
    output errors

missingManifestRetainsRecords :: FilePath -> IO ()
missingManifestRetainsRecords root = do
  gitChecked root ["rm", "--", "archive.json"]
  gitChecked root ["commit", "-m", "test missing manifest"]
  (code, output, errors) <- command root ["status"]
  assert "a missing manifest is incomplete without erasing committed records"
    (code == ExitFailure 2
      && contains "\"kind\":\"missing_tree\"" output
      && contains "\"record_count\":2" output)
    output errors

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

command :: FilePath -> [String] -> IO (ExitCode, String, String)
command root arguments = readCreateProcessWithExitCode
  ((proc "archive" arguments) { cwd = Just root }) ""

gitChecked :: FilePath -> [String] -> IO ()
gitChecked root arguments = do
  (code, output, errors) <- readCreateProcessWithExitCode
    ((proc "git" arguments) { cwd = Just root }) ""
  assert ("git " <> unwords arguments) (code == ExitSuccess) output errors

parent :: FilePath -> FilePath
parent = takeDirectory

-- | Fails the running test case with the label and the command's output.
assert :: HasCallStack => String -> Bool -> String -> String -> IO ()
assert label condition output errors
  | condition = pure ()
  | otherwise = assertFailure (label <> "\nstdout: " <> output <> "\nstderr: " <> errors)

jsonStringField :: String -> String -> Maybe String
jsonStringField name value =
  takeWhile (/= '"') <$> after ("\"" <> name <> "\":\"") value

after :: String -> String -> Maybe String
after needle value = search value
  where
    search [] = if null needle then Just [] else Nothing
    search remaining
      | needle `prefixOf` remaining = Just (drop (length needle) remaining)
      | otherwise = search (drop 1 remaining)

shellQuote :: String -> String
shellQuote value = "'" <> concatMap escape value <> "'"
  where
    escape '\'' = "'\\''"
    escape character = [character]

contains :: String -> String -> Bool
contains needle haystack = any (needle `prefixOf`) (tails haystack)

prefixOf :: String -> String -> Bool
prefixOf [] _ = True
prefixOf _ [] = False
prefixOf (first:rest) (other:remaining) = first == other && prefixOf rest remaining

tails :: [a] -> [[a]]
tails [] = [[]]
tails values@(_:rest) = values : tails rest

-- | `archive export --since` (ADR 0055). Every record is admitted by Door, so
-- each admission is one commit and every pin is one Door accepted.
exportSinceChecks :: FilePath -> IO ()
exportSinceChecks root = do
  manifest <- readFile (root </> "archive.json")
  archiveId <- maybe (failWith "the manifest names the archive" manifest) pure
    (jsonStringField "archive_id" manifest)
  initial <- headCommit root
  (work, workRevision) <- admit root archiveId "archive.core/entity" "since-work"
    (\objectId -> draft objectId "entity" (Aeson.object
      ["kind" .= ("musical_work" :: Text.Text), "label" .= ("Paper Lanterns" :: Text.Text)]) [] [])
  afterWork <- headCommit root
  correspondenceContract <- citation root "archive.claim/work_musicbrainz_correspondence" "1"
  (correspondence, correspondenceRevision) <- admit root archiveId
    "archive.claim/work_musicbrainz_correspondence" "since-correspondence"
    (\objectId -> draft objectId "claim" (Aeson.object
      [ "external_work" .= ("https://musicbrainz.org/work/11111111-1111-4111-8111-111111111111" :: Text.Text)
      , "mapping" .= ("skos:exactMatch" :: Text.Text) ])
      [pin "work" work workRevision] [correspondenceContract])
  afterCorrespondence <- headCommit root
  performanceContract <- citation root "archive.claim/performance_work" "1"
  (_, performanceRevision) <- admit root archiveId "archive.claim/performance_work" "since-performance"
    (\objectId -> draft objectId "claim" (Aeson.object [])
      [pin "performance" correspondence correspondenceRevision, pin "work" work workRevision]
      [performanceContract])
  afterPerformance <- headCommit root
  (_, otherRevision) <- admit root archiveId "archive.core/entity" "since-other-work"
    (\objectId -> draft objectId "entity" (Aeson.object
      ["kind" .= ("musical_work" :: Text.Text), "label" .= ("Other" :: Text.Text)]) [] [])
  final <- headCommit root
  -- Anchor: each admission moved HEAD, or the cuts below would coincide.
  assert "each admission is its own commit"
    (Set.size (Set.fromList [initial, afterWork, afterCorrespondence, afterPerformance, final]) == 5)
    (unwords [initial, afterWork, afterCorrespondence, afterPerformance, final]) ""

  full <- exportValue root ["export"]
  installedFiles <- listDirectory (root </> "contracts")
  let exportedContracts = case field "contracts" full of
        Just (Aeson.Array bundles) -> [ (identifier, version)
                                      | Aeson.Object bundle <- toList bundles
                                      , Just (Aeson.String identifier) <- [KeyMap.lookup "id" bundle]
                                      , Just (Aeson.String version) <- [KeyMap.lookup "version" bundle] ]
        _ -> []
  -- Anchor: no record cites publication approval, so it is exported only
  -- because it is installed.
  assert "a full export carries every installed contract, cited or not (ADR 0059)"
    (length exportedContracts == length installedFiles && length installedFiles > 3
      && ("archive.transition/publication_approval", "1") `elem` exportedContracts
      && not (contains "archive.transition/publication_approval" (show (field "records" full))))
    (show exportedContracts) (show installedFiles)
  assert "a full export carries neither since nor pinned records"
    (field "since" full == Nothing && field "pinned_records" full == Nothing
      && Set.size (revisions "records" full) == 6)
    (show full) ""

  sinceCorrespondence <- exportValue root ["export", "--since", afterCorrespondence]
  assert "export --since emits only added records and, separately, what they pin"
    (revisions "records" sinceCorrespondence == Set.fromList [performanceRevision, otherRevision]
      && revisions "pinned_records" sinceCorrespondence
           == Set.fromList [workRevision, correspondenceRevision]
      && contractIds sinceCorrespondence == Set.fromList
           ["archive.claim/work_musicbrainz_correspondence", "archive.claim/performance_work"]
      && field "since" sinceCorrespondence == Just (Aeson.String (Text.pack afterCorrespondence))
      && field "snapshot" sinceCorrespondence == Just (Aeson.String (Text.pack final))
      && field "archive_id" sinceCorrespondence == field "archive_id" full
      && field "spec" sinceCorrespondence == field "spec" full
      && field "context" sinceCorrespondence == field "context" full)
    (show sinceCorrespondence) ""

  sincePerformance <- exportValue root ["export", "--since", afterPerformance]
  assert "export --since cites only the contracts of the emitted records"
    (revisions "records" sincePerformance == Set.fromList [otherRevision]
      && Set.null (revisions "pinned_records" sincePerformance)
      && Set.null (contractIds sincePerformance)
      && not (Set.null (contractIds full)))
    (show sincePerformance) ""

  sinceWork <- exportValue root ["export", "--since", afterWork]
  assert "a pinned record added after the cut is an added record, not a pinned one"
    (revisions "records" sinceWork
       == Set.fromList [correspondenceRevision, performanceRevision, otherRevision]
      && revisions "pinned_records" sinceWork == Set.fromList [workRevision])
    (show sinceWork) ""

  sinceInitial <- exportValue root ["export", "--since", initial]
  initialRecords <- revisions "records" <$> exportValueAt initial
  assert "the records added since a cut and those at the cut make up the full export"
    (Set.union (revisions "records" sinceInitial) initialRecords == revisions "records" full
      && Set.null (Set.intersection (revisions "records" sinceInitial) initialRecords)
      && Set.size initialRecords == 2
      && Set.null (revisions "pinned_records" sinceInitial))
    (show sinceInitial) ""

  -- A right answer at the right price: export --since reads the record
  -- blobs it emits and nothing else, and walks no history.
  realGit <- findExecutable "git" >>= maybe (failWith "the cost fixture finds git" "") pure
  let wrapperDirectory = root <> "-since-git"
      wrapper = wrapperDirectory </> "git"
      callLog = wrapperDirectory </> "calls"
  createDirectoryIfMissing True wrapperDirectory
  writeFile wrapper ("#!/bin/sh\nprintf '%s\\n' \"$*\" >> " <> shellQuote callLog
    <> "\nexec " <> shellQuote realGit <> " \"$@\"\n")
  wrapperPermissions <- getPermissions wrapper
  setPermissions wrapper (setOwnerExecutable True wrapperPermissions)
  environment <- getEnvironment
  (_, recordListing, _) <- readCreateProcessWithExitCode
    ((proc "git" ["ls-tree", "-r", "HEAD", "--", "records"]) { cwd = Just root }) ""
  let recordBlobs = [ (path, words meta !! 2)
                    | line <- lines recordListing, let (meta, rest) = break (== '\t') line, let path = drop 1 rest ]
  (_, otherPathOutput, _) <- readCreateProcessWithExitCode
    ((proc "git" ["diff-tree", "-r", "--name-only", "--diff-filter=A", afterPerformance, final, "--", "records"]) { cwd = Just root }) ""
  writeFile callLog ""
  (pricedCode, pricedOutput, pricedErrors) <- readCreateProcessWithExitCode
    ((proc "archive" ["export", "--since", afterPerformance])
      { cwd = Just root
      , env = Just (("ARCHIVE_GIT_EXECUTABLE", wrapper) : filter ((/= "ARCHIVE_GIT_EXECUTABLE") . fst) environment)
      }) ""
  calls <- lines <$> readFile callLog
  let shownObjects = concat [ drop 2 (words call) | call <- calls, take 5 call == "show " ]
      readRecordPaths = [ path | (path, blob) <- recordBlobs, blob `elem` shownObjects ]
  assert "export --since reads only the record blobs it emits and walks no history"
    (pricedCode == ExitSuccess
      && readRecordPaths == lines otherPathOutput
      && not (any (\call -> any (`elem` words call) ["log", "rev-list"]) calls))
    (pricedOutput <> "\n" <> unlines calls) pricedErrors
  writeFile callLog ""
  (fullCode, _, _) <- readCreateProcessWithExitCode
    ((proc "archive" ["export"])
      { cwd = Just root
      , env = Just (("ARCHIVE_GIT_EXECUTABLE", wrapper) : filter ((/= "ARCHIVE_GIT_EXECUTABLE") . fst) environment)
      }) ""
  fullCalls <- lines <$> readFile callLog
  let fullShown = concat [ drop 2 (words call) | call <- fullCalls, take 5 call == "show " ]
  assert "a full export through the same wrapper is seen reading every record (anchor)"
    (fullCode == ExitSuccess && all ((`elem` fullShown) . snd) recordBlobs && length recordBlobs > 2)
    (unlines fullCalls) ""

  sinceHead <- exportValue root ["export", "--since", final]
  assert "export --since HEAD has no records"
    (Set.null (revisions "records" sinceHead) && Set.null (revisions "pinned_records" sinceHead)
      && field "status" sinceHead == Just (Aeson.String "ok"))
    (show sinceHead) ""

  unrelated <- gitOutput root ["commit-tree", final <> "^{tree}", "-m", "unrelated root"]
  mapM_ (refused "is not an ancestor of HEAD") [unrelated, replicate 40 '0']
  mapM_ (refused "full lowercase commit identifier")
    ["HEAD", take 12 final, map toUpperHex final, final <> "\n", ""]
  where
    refused expected since = do
      (code, output, errors) <- command root ["export", "--since", since]
      assert ("export --since refuses " <> show since)
        (code == ExitFailure 1 && contains "\"status\":\"failed\"" output
          && contains expected output && not (contains "\"records\"" output))
        output errors
    toUpperHex character = if character >= 'a' && character <= 'f'
      then toEnum (fromEnum character - 32) else character
    -- The export at an earlier commit, read from a clone checked out there.
    exportValueAt commit = do
      let clone = root <> "-at-" <> take 12 commit
      gitChecked root ["clone", "--quiet", root, clone]
      gitChecked clone ["checkout", "--quiet", "-B", "cut", commit]
      value <- exportValue clone ["export"]
      removePathForcibly clone
      pure value

headCommit :: FilePath -> IO String
headCommit root = gitOutput root ["rev-parse", "--verify", "HEAD^{commit}"]

gitOutput :: FilePath -> [String] -> IO String
gitOutput root arguments = do
  (code, output, errors) <- readCreateProcessWithExitCode
    ((proc "git" arguments) { cwd = Just root }) ""
  assert ("git " <> unwords arguments) (code == ExitSuccess) output errors
  pure (takeWhile (/= '\n') output)

exportValue :: FilePath -> [String] -> IO Aeson.Value
exportValue root arguments = do
  (code, output, errors) <- command root arguments
  assert ("archive " <> unwords arguments <> " succeeds") (code == ExitSuccess) output errors
  maybe (failWith "export output is JSON" output) pure
    (Aeson.decode (LazyChar8.pack output))

field :: Text.Text -> Aeson.Value -> Maybe Aeson.Value
field name value = case value of
  Aeson.Object fields -> KeyMap.lookup (Key.fromText name) fields
  _ -> Nothing

revisions :: Text.Text -> Aeson.Value -> Set.Set String
revisions name value = case field name value of
  Just (Aeson.Array items) -> Set.fromList
    [ Text.unpack revision | item <- toList items
    , Just (Aeson.String revision) <- [field "revision_id" item] ]
  _ -> Set.singleton ("missing " <> Text.unpack name)

contractIds :: Aeson.Value -> Set.Set Text.Text
contractIds value = case field "contracts" value of
  Just (Aeson.Array items) -> Set.fromList
    [ identifier | item <- toList items, Just (Aeson.String identifier) <- [field "id" item] ]
  _ -> Set.singleton "missing contracts"

draft :: String -> Text.Text -> Aeson.Value -> [Aeson.Value] -> [Aeson.Value] -> Aeson.Value
draft objectId shape payload refs contracts = Aeson.object
  ([ "spec" .= ("archive/0.0.11" :: Text.Text)
   , "object_id" .= objectId
   , "shape" .= shape
   , "by" .= ("pkg:hackage/archive@0.0.1.0" :: Text.Text)
   , "agent" .= ("urn:uuid:00000000-0000-4000-8000-000000000001" :: Text.Text)
   , "payload" .= payload
   , "refs" .= refs
   , "supersedes" .= ([] :: [Aeson.Value])
   ] <> [ "contracts" .= contracts | not (null contracts) ])

pin :: Text.Text -> String -> String -> Aeson.Value
pin role objectId revision = Aeson.object
  ["role" .= role, "id" .= objectId, "revision_id" .= revision]

-- | A contract citation for an installed contract, found by reading the
-- committed contract bundles; the digest is the bundle file's name.
citation :: FilePath -> Text.Text -> Text.Text -> IO Aeson.Value
citation root identifier version = do
  names <- listDirectory (root </> "contracts")
  found <- fmap concat $ mapM (\name -> do
    bytes <- LazyChar8.readFile (root </> "contracts" </> name)
    pure [ name | Just bundle <- [Aeson.decode bytes :: Maybe Aeson.Value]
                , field "id" bundle == Just (Aeson.String identifier)
                , field "version" bundle == Just (Aeson.String version) ]) names
  case found of
    [name] | "sha256-" `prefixOf` name -> pure (Aeson.object
      [ "id" .= identifier, "version" .= version
      , "digest" .= ("sha256:" <> takeWhile (/= '.') (drop 7 name)) ])
    _ -> failWith ("one installed contract " <> Text.unpack identifier) (show found)

-- | Admit one new record through `door admit` and return its object and
-- revision. Door, not the test, chooses where the record lives.
-- | `archive maintenance repack` packs loose objects through the verified
-- prune and leaves a fresh checkpoint, so the next admission is still cheap
-- (ADR 0034, ADR 0052).
repackChecks :: FilePath -> IO ()
repackChecks root = do
  manifest <- readFile (root </> "archive.json")
  archiveId <- maybe (failWith "the manifest names the archive" manifest) pure
    (jsonStringField "archive_id" manifest)
  let entity name objectId = draft objectId "entity" (Aeson.object
        ["kind" .= ("person" :: Text.Text), "label" .= (Text.pack name)]) [] []
  mapM_ (\name -> admit root archiveId "archive.core/entity" name (entity name))
    ["repack-one", "repack-two", "repack-three"]
  looseBefore <- looseObjects
  (code, output, errors) <- command root ["maintenance", "repack"]
  looseAfter <- looseObjects
  removedCheckpoint <- not <$> doesFileExist (root </> ".git" </> "archive-door-verified.json")
  (verifyCode, verifyOutput, verifyErrors) <- readCreateProcessWithExitCode
    ((proc "door" ["verify"]) { cwd = Just root }) ""
  assert "door verify writes a new checkpoint after maintenance removed it"
    (removedCheckpoint && verifyCode == ExitSuccess && contains "\"status\":\"verified\"" verifyOutput)
    verifyOutput verifyErrors
  (_, packs, _) <- readCreateProcessWithExitCode
    ((proc "git" ["count-objects", "-v"]) { cwd = Just root }) ""
  checkpoint <- doesFileExist (root </> ".git" </> "archive-door-verified.json")
  assert "repack packs every loose object and leaves a checkpoint"
    (code == ExitSuccess && contains "\"status\":\"ok\"" output
      && looseBefore > 0 && looseAfter == 0 && contains "packs: 1" packs && checkpoint)
    (output <> "\nloose before " <> show looseBefore <> " after " <> show looseAfter <> "\n" <> packs) errors
  (doctorCode, doctorOutput, doctorErrors) <- command root ["doctor"]
  assert "doctor passes after repack" (doctorCode == ExitSuccess) doctorOutput doctorErrors
  realGit <- findExecutable "git" >>= maybe (failWith "the repack fixture finds git" "") pure
  let wrapperDirectory = root <> "-repack-git"
      wrapper = wrapperDirectory </> "git"
  createDirectoryIfMissing True wrapperDirectory
  writeFile wrapper ("#!/bin/sh\nfor argument in \"$@\"; do case \"$argument\" in log|fsck) exit 97;; esac; done\nexec "
    <> shellQuote realGit <> " \"$@\"\n")
  permissions <- getPermissions wrapper
  setPermissions wrapper (setOwnerExecutable True permissions)
  _ <- admitWith [("ARCHIVE_GIT_EXECUTABLE", wrapper)] root archiveId "archive.core/entity"
    "repack-after" (entity "repack-after")
  removePathForcibly wrapperDirectory
  where
    looseObjects = do
      (_, counts, _) <- readCreateProcessWithExitCode
        ((proc "git" ["count-objects", "-v"]) { cwd = Just root }) ""
      pure (case [ read (drop 7 line) :: Int | line <- lines counts, take 7 line == "count: " ] of
        value : _ -> value
        [] -> -1)

admit :: FilePath -> String -> String -> String -> (String -> Aeson.Value)
      -> IO (String, String)
admit = admitWith []

-- | 'admit' with extra environment for Door, such as a Git wrapper.
admitWith :: [(String, String)] -> FilePath -> String -> String -> String -> (String -> Aeson.Value)
          -> IO (String, String)
admitWith extra root archiveId namespace name draftFor = do
  (allocCode, allocated, allocErrors) <- readProcessWithExitCode "python3"
    [ "-c"
    , "import sys,uuid; print('urn:uuid:'+str(uuid.uuid5(uuid.UUID(sys.argv[1][9:]), sys.argv[2]+'\\0'+sys.argv[3])))"
    , archiveId, namespace, name ] ""
  assert "object identifiers are allocated as UUIDv5" (allocCode == ExitSuccess) allocated allocErrors
  let objectId = takeWhile (/= '\n') allocated
      proposal = root <> "-proposal-" <> name
  createDirectoryIfMissing True (proposal </> "records")
  LazyChar8.writeFile (proposal </> "records" </> "record.json") (Aeson.encode (draftFor objectId))
  LazyChar8.writeFile (proposal </> "manifest.json") (Aeson.encode (Aeson.object
    [ "protocol" .= ("archive-admission/1" :: Text.Text)
    , "records" .= ["records/record.json" :: Text.Text]
    , "identities" .= [Aeson.object
        ["object_id" .= objectId, "namespace" .= namespace, "name" .= name]]
    , "contents" .= ([] :: [Aeson.Value])
    , "expected_heads" .= [Aeson.object
        ["object_id" .= objectId, "revision_id" .= Aeson.Null]]
    ]))
  environment <- getEnvironment
  let doorEnvironment = extra <> filter ((`notElem` map fst extra) . fst) environment
  (code, output, errors) <- readCreateProcessWithExitCode
    ((proc "door" ["admit", "--proposal", proposal]) { cwd = Just root, env = Just doorEnvironment }) ""
  removePathForcibly proposal
  assert ("door admits " <> name) (code == ExitSuccess) output errors
  case Aeson.decode (LazyChar8.pack output) >>= field "revision_ids" of
    Just (Aeson.Array items) | [Aeson.String revision] <- toList items ->
      pure (objectId, Text.unpack revision)
    _ -> failWith ("door reports the revision of " <> name) output

failWith :: HasCallStack => String -> String -> IO a
failWith label output = assertFailure (label <> "\nstdout: " <> output)
