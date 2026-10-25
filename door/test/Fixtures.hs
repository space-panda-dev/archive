{-# LANGUAGE OverloadedStrings #-}

-- | Shared fixtures of door-test that drive Door: the dry-run validator,
-- archives and proposals made on disk, and the `door` and `archive`
-- commands run against them.
module Fixtures
  ( doorCommand
  , archiveCommand
  , dryRun
  , succeeds
  , refuses
  , refusesSilently
  , revisionOf
  , canonicalRevisionOf
  , doorWithGitWrapper
  , indexMatches
  , admitFreshEntity
  , freshEntityProposal
  , killedBatchRecovers
  , newProposalArchive
  , writeAdmissionProposal
  , writeTransitionProposal
  , doorCommandAt
  , persistentCommandWithFakeAnnex
  , persistentAdmissionCommandWithFakeAnnex
  , persistentCommandWithFailingCommit
  , gitHead
  , gitCommitCount
  , recordCount
  , readStoredRecord
  , canonicalStored
  , checkedOutput
  , proposalRefusesContainingWith
  , requiredField
  , allocatedObjectId
  , recordRevisionForObject
  , contractCitation
  , contractCitationVersion
  , commitEntityFixture
  , commitArtifactFixture
  , commitArtifactFixtureWithDigest
  , commitDraftFixture
  , admitNew
  , refuseNew
  , admitDraft
  , transitionAt
  , recordRelative
  ) where

import Archive.Door.Canonical (canonicalizeDraft, finalizeStored)
import Archive.Door.Json (decodeUnique)
import Archive.Door.Layout (parseSha256Digest, recordRelativePath)
import Archive.Door.References
  ( StoreIndex (..)
  , emptyIndex
  , insertRecord
  , validateReferences
  )
import Archive.Door.Types
  ( DraftEnvelope (envelopeBody)
  , RecordBody (ClaimRecord)
  , parseRevisionId
  , parseEnvelope
  )
import Data.Aeson ((.=))
import qualified Data.Aeson as Aeson
import Data.Aeson.RFC8785 (encodeCanonical)
import qualified Data.ByteString.Lazy.Char8 as LazyByteString
import Data.Foldable (foldlM, toList)
import qualified Data.Text as Text
import Support
import System.Directory
  ( createDirectoryIfMissing
  , doesPathExist
  , findExecutable
  , getPermissions
  , setOwnerExecutable
  , setPermissions
  )
import System.Environment (getEnvironment)
import System.Exit (ExitCode (ExitFailure, ExitSuccess))
import System.Posix.Files (setFileMode)
import System.Process
  ( CreateProcess (cwd, env)
  , proc
  , readCreateProcessWithExitCode
  , readProcessWithExitCode
  )
import Test.Tasty.HUnit (assertFailure)

-- The target object is deliberately different from the object named by the
-- supersession reference.
doorCommand, archiveCommand :: [String] -> String -> IO (ExitCode, String, String)
doorCommand arguments input = readProcessWithExitCode "door" arguments (input <> "\n")
archiveCommand arguments input = readProcessWithExitCode "archive" arguments (input <> "\n")

dryRun :: String -> IO (ExitCode, String, String)
dryRun input = pure $ case validateDraftBatch input of
  Left problem -> (ExitFailure 1, "", "door: " <> Text.unpack problem <> "\n")
  Right rows -> (ExitSuccess, concatMap ((<> "\n") . LazyByteString.unpack . Aeson.encode) rows, "")

validateDraftBatch :: String -> Either Text.Text [Aeson.Value]
validateDraftBatch input = do
  value <- either (Left . ("invalid JSON: " <>) . Text.pack) Right
    (decodeUnique (LazyByteString.pack input))
  let values = case value of
        Aeson.Array array -> toList array
        _ -> [value]
  if null values
    then Left "admission batch must contain at least one record"
    else pure ()
  (_, rows, unresolvedClaims) <- foldlM validateOne (emptyIndex, [], False) values
  if unresolvedClaims
    then Left "unsupported: claim contracts require an initialized archive"
    else Right (reverse rows)

validateOne :: (StoreIndex, [Aeson.Value], Bool) -> Aeson.Value
            -> Either Text.Text (StoreIndex, [Aeson.Value], Bool)
validateOne (index, rows, unresolvedClaims) value = do
  parsedEnvelope <- parseEnvelope value
  (revision, record) <- canonicalizeDraft value
  revisionId <- parseRevisionId revision
  validateReferences index revisionId parsedEnvelope
  let row = Aeson.object
        [ "ok" .= True
        , "revision_id" .= revision
        , "record" .= record
        ]
      next = insertRecord revisionId parsedEnvelope index
      hasClaim = case envelopeBody parsedEnvelope of
        ClaimRecord _ -> True
        _ -> False
  pure (next, row : rows, unresolvedClaims || hasClaim)

succeeds :: String -> String -> IO ()
succeeds label input = do
  (code, output, errors) <- dryRun input
  assert label (code == ExitSuccess && "sha256:" `contains` output) code output errors

refuses, refusesSilently :: String -> String -> String -> IO ()
refuses label input expectedError = do
  (code, output, errors) <- dryRun input
  assert label (code /= ExitSuccess && expectedError `contains` errors) code output errors
refusesSilently label input expectedError = do
  (code, output, errors) <- dryRun input
  assert label (code /= ExitSuccess && null output && expectedError `contains` errors) code output errors

revisionOf :: String -> IO String
revisionOf input = do
  (code, output, errors) <- dryRun input
  if code /= ExitSuccess then do
    assertFailure ("cannot compute revision: " <> errors)
  else case after "\"revision_id\":\"" output of
    Nothing -> assertFailure "command did not return revision_id"
    Just suffix -> pure (takeWhile (/= '"') suffix)

canonicalRevisionOf :: String -> IO String
canonicalRevisionOf input = do
  value <- case decodeUnique (LazyByteString.pack input) of
    Left problem -> assertFailure ("revision fixture JSON: " <> problem)
    Right parsed -> pure parsed
  case canonicalizeDraft value of
    Left problem -> assertFailure ("cannot canonicalize revision fixture: "
      <> Text.unpack problem)
    Right (revision, _) -> pure (Text.unpack revision)

-- | Run the real Door with one of the wrappers above as its Git executable.
doorWithGitWrapper :: FilePath -> String -> FilePath -> [String] -> [(String, String)]
                   -> (FilePath -> String) -> IO (ExitCode, String, String)
doorWithGitWrapper parent suffix root arguments extra script = do
  realGit <- findExecutable "git" >>= maybe
    (ioError (userError "Git wrapper fixture cannot find git")) pure
  let fakeBin = parent </> ("git-wrapper-" <> suffix)
      fakeGit = fakeBin </> "git"
  createDirectoryIfMissing True fakeBin
  writeFile fakeGit (script realGit)
  setFileMode fakeGit 0o755
  environment <- getEnvironment
  let overrides = ("ARCHIVE_GIT_EXECUTABLE", fakeGit) : extra
      kept = filter ((`notElem` map fst overrides) . fst) environment
  readCreateProcessWithExitCode ((proc "door" arguments)
    { cwd = Just root, env = Just (overrides <> kept) }) ""

-- | Whether the ordinary index holds exactly a commit's tree.
indexMatches :: FilePath -> String -> IO Bool
indexMatches root commit = do
  (code, _, _) <- readProcessWithExitCode "git"
    ["-C", root, "diff", "--cached", "--quiet", trim commit, "--"] ""
  pure (code == ExitSuccess)

-- | A metadata admission of one new entity: an ordinary later write, with a
-- git-annex that fails if it is ever run.
admitFreshEntity :: FilePath -> FilePath -> String -> String -> IO (ExitCode, String, String, Bool)
admitFreshEntity parent root archiveId name = do
  (proposal, _) <- freshEntityProposal root archiveId name
  persistentAdmissionCommandWithFakeAnnex parent name root proposal

freshEntityProposal :: FilePath -> String -> String -> IO (FilePath, String)
freshEntityProposal root archiveId name = do
  objectId <- allocatedObjectId archiveId "archive.core/entity" name
  proposal <- writeAdmissionProposal root name (manifestFor
    ["records/entity.json"]
    [(objectId, "archive.core/entity", name)]
    [(objectId, Nothing)] []) ["records/entity.json", entityDraft objectId]
  pure (proposal, objectId)

-- | What a Door killed right after its ref update leaves (the anchor: the
-- branch moved, the journal remains, the ordinary index still holds the old
-- commit's tree and not the new one), then the next ordinary write: it must
-- recover the index to the committed batch and commit on top of it.
killedBatchRecovers :: String -> String -> FilePath -> FilePath -> String -> String -> IO ()
killedBatchRecovers label name parent root archiveId headBefore = do
  let journal = root </> ".git" </> "archive-door-operation"
  headAfterKill <- gitHead root
  journalAfterKill <- doesPathExist journal
  staleAtOld <- indexMatches root headBefore
  staleAtHead <- indexMatches root headAfterKill
  assert (label <> ": a Door killed after its ref update leaves the commit, its journal, and the old index")
    (trim headAfterKill /= trim headBefore && journalAfterKill && staleAtOld && not staleAtHead)
    ExitSuccess (show (headBefore, headAfterKill, journalAfterKill, staleAtOld, staleAtHead)) ""
  (code, output, errors, annexCalled) <- admitFreshEntity parent root archiveId name
  headAfter <- gitHead root
  parentOfHead <- checkedOutput "index recovery reads the new commit's parent"
    ["-C", root, "rev-parse", "HEAD^"]
  fresh <- indexMatches root headAfter
  journalAfter <- doesPathExist journal
  assert (label <> ": the next write recovers the index to the committed batch and commits on top of it")
    (code == ExitSuccess && null errors && not annexCalled && contains "\"retry\":false" output
      && trim parentOfHead == trim headAfterKill && fresh && not journalAfter)
    code output errors

newProposalArchive :: FilePath -> String -> IO (FilePath, String, String)
newProposalArchive parent suffix = do
  let root = parent </> ("archive-" <> suffix)
  (code, output, errors) <- archiveCommand ["init", "--operator", "alice", root] ""
  assert ("proposal fixture archive initializes: " <> suffix)
    (code == ExitSuccess) code output errors
  archiveId <- requiredField "archive_id" output
  pure (root, archiveId, output)

writeAdmissionProposal :: FilePath -> String -> String
                    -> [String] -> IO FilePath
writeAdmissionProposal root name manifest recordPairs = do
  let proposal = root <> "-proposal-" <> name
      recordsRoot = proposal </> "records"
  createDirectoryIfMissing True recordsRoot
  writeFile (proposal </> "manifest.json") manifest
  writeRecordPairs recordsRoot recordPairs
  pure proposal
  where
    writeRecordPairs _ [] = pure ()
    writeRecordPairs recordsRoot (path : value : rest) = do
      let filename = recordsRoot </> drop 8 path
      createDirectoryIfMissing True (takeDirectory filename)
      writeFile filename value
      writeRecordPairs recordsRoot rest
    writeRecordPairs _ _ = assert "proposal fixture has path/value pairs" False ExitSuccess "" ""

writeTransitionProposal :: FilePath -> String -> String -> [String] -> IO FilePath
writeTransitionProposal root name manifest recordPairs = do
  let proposal = root <> "-transition-" <> name
      recordsRoot = proposal </> "records"
  createDirectoryIfMissing True recordsRoot
  writeFile (proposal </> "manifest.json") manifest
  writeRecordPairs recordsRoot recordPairs
  pure proposal
  where
    writeRecordPairs _ [] = pure ()
    writeRecordPairs recordsRoot (path : value : rest) = do
      let filename = recordsRoot </> drop 8 path
      createDirectoryIfMissing True (takeDirectory filename)
      writeFile filename value
      writeRecordPairs recordsRoot rest
    writeRecordPairs _ _ = assert "transition fixture has path/value pairs" False ExitSuccess "" ""

doorCommandAt :: FilePath -> [String] -> String
             -> IO (ExitCode, String, String)
doorCommandAt root arguments input =
  readCreateProcessWithExitCode ((proc "door" arguments) {cwd = Just root})
    (input <> "\n")

persistentCommandWithFakeAnnex
  :: FilePath -> String -> FilePath -> FilePath
  -> IO (ExitCode, String, String, Bool)
persistentCommandWithFakeAnnex parent suffix root proposal = do
  let fakeBin = parent </> ("persistent-fake-annex-" <> suffix)
      fakeAnnex = fakeBin </> "git-annex"
      marker = parent </> ("persistent-annex-called-" <> suffix)
  createDirectoryIfMissing True fakeBin
  writeFile fakeAnnex ("#!/bin/sh\ntouch " <> quote marker <> "\nexit 99\n")
  permissions <- getPermissions fakeAnnex
  setPermissions fakeAnnex (setOwnerExecutable True permissions)
  environment <- getEnvironment
  let clean = ("ARCHIVE_GIT_ANNEX_EXECUTABLE", fakeAnnex)
        : filter ((/= "ARCHIVE_GIT_ANNEX_EXECUTABLE") . fst) environment
      process = (proc "door" ["transition", "--proposal", proposal])
        { cwd = Just root
        , env = Just clean
        }
  (code, output, errors) <- readCreateProcessWithExitCode process ""
  called <- doesPathExist marker
  pure (code, output, errors, called)

persistentAdmissionCommandWithFakeAnnex
  :: FilePath -> String -> FilePath -> FilePath
  -> IO (ExitCode, String, String, Bool)
persistentAdmissionCommandWithFakeAnnex parent suffix root proposal = do
  let fakeBin = parent </> ("persistent-admission-fake-annex-" <> suffix)
      fakeAnnex = fakeBin </> "git-annex"
      marker = parent </> ("persistent-admission-annex-called-" <> suffix)
  createDirectoryIfMissing True fakeBin
  writeFile fakeAnnex ("#!/bin/sh\ntouch " <> quote marker <> "\nexit 99\n")
  permissions <- getPermissions fakeAnnex
  setPermissions fakeAnnex (setOwnerExecutable True permissions)
  environment <- getEnvironment
  let clean = ("ARCHIVE_GIT_ANNEX_EXECUTABLE", fakeAnnex)
        : filter ((/= "ARCHIVE_GIT_ANNEX_EXECUTABLE") . fst) environment
      process = (proc "door" ["admit", "--proposal", proposal])
        { cwd = Just root
        , env = Just clean
        }
  (code, output, errors) <- readCreateProcessWithExitCode process ""
  called <- doesPathExist marker
  pure (code, output, errors, called)

persistentCommandWithFailingCommit
  :: String -> FilePath -> FilePath -> FilePath
  -> IO (ExitCode, String, String, Bool)
persistentCommandWithFailingCommit operation parent root proposal = do
  realGit <- findExecutable "git" >>= maybe
    (ioError (userError "persistent fixture cannot find git")) pure
  let fakeBin = parent </> ("persistent-failing-commit-" <> operation)
      fakeGit = fakeBin </> "git"
      fakeAnnex = fakeBin </> "git-annex"
      annexMarker = parent </> ("persistent-failing-commit-annex-" <> operation)
  createDirectoryIfMissing True fakeBin
  writeFile fakeGit
    ("#!/bin/sh\nfor argument in \"$@\"; do\n"
      <> "  if [ \"$argument\" = commit-tree ]; then exit 88; fi\n"
      <> "done\nexec " <> quote realGit <> " \"$@\"\n")
  writeFile fakeAnnex
    ("#!/bin/sh\ntouch " <> quote annexMarker <> "\nexit 99\n")
  mapM_ makeExecutable [fakeGit, fakeAnnex]
  environment <- getEnvironment
  let clean = ("ARCHIVE_GIT_EXECUTABLE", fakeGit)
        : ("ARCHIVE_GIT_ANNEX_EXECUTABLE", fakeAnnex)
        : filter ((/= "ARCHIVE_GIT_EXECUTABLE") . fst)
        (filter ((/= "ARCHIVE_GIT_ANNEX_EXECUTABLE") . fst) environment)
      process = (proc "door" [operation, "--proposal", proposal])
        { cwd = Just root
        , env = Just clean
        }
  (code, output, errors) <- readCreateProcessWithExitCode process ""
  annexCalled <- doesPathExist annexMarker
  pure (code, output, errors, annexCalled)
  where
    makeExecutable path = do
      permissions <- getPermissions path
      setPermissions path (setOwnerExecutable True permissions)

gitHead :: FilePath -> IO String
gitHead root = checkedOutput "persistent fixture reads HEAD"
  ["-C", root, "rev-parse", "HEAD"]

gitCommitCount :: FilePath -> IO Int
gitCommitCount root = do
  output <- checkedOutput "persistent fixture counts commits"
    ["-C", root, "rev-list", "--count", "HEAD"]
  pure (read (trim output))

recordCount :: FilePath -> IO Int
recordCount root = do
  (code, output, errors) <- readProcessWithExitCode "find"
    [root </> "records", "-type", "f"] ""
  assert "persistent fixture counts record files" (code == ExitSuccess)
    code output errors
  pure (length (lines output))

readStoredRecord :: FilePath -> String -> IO LazyByteString.ByteString
readStoredRecord root revision = LazyByteString.readFile
  (root </> recordRelative revision)

canonicalStored :: LazyByteString.ByteString -> Bool
canonicalStored bytes = case decodeUnique bytes of
  Left _ -> False
  Right value -> encodeCanonical value == bytes

checkedOutput :: String -> [String] -> IO String
checkedOutput label arguments = do
  (code, output, errors) <- readProcessWithExitCode "git" arguments ""
  assert label (code == ExitSuccess) code output errors
  pure (trim output)

proposalRefusesContainingWith :: String -> String -> String -> FilePath -> FilePath -> IO ()
proposalRefusesContainingWith operation label expected root proposal = do
  (code, output, errors) <- doorCommandAt root
    [operation, "--proposal", proposal, "--dry-run"] ""
  assert label (code /= ExitSuccess && null output && contains expected errors)
    code output errors

requiredField :: String -> String -> IO String
requiredField name value = case jsonStringField name value of
  Just result -> pure result
  Nothing -> do
    assertFailure ("fixture output has no " <> name)

allocatedObjectId :: String -> String -> String -> IO String
allocatedObjectId archiveId namespace name = do
  (code, output, errors) <- readProcessWithExitCode "python3"
    [ "-c"
    , "import sys,uuid; print('urn:uuid:'+str(uuid.uuid5(uuid.UUID(sys.argv[1][9:]), sys.argv[2]+'\\0'+sys.argv[3])))"
    , archiveId, namespace, name
    ] ""
  assert "proposal fixture uses UUIDv5 allocation" (code == ExitSuccess)
    code output errors
  pure (trim output)

recordRevisionForObject :: FilePath -> String -> IO String
recordRevisionForObject root objectId = do
  (code, output, errors) <- readProcessWithExitCode "python3"
    [ "-c"
    , "import glob,json,sys; found=[json.load(open(p))['revision_id'] for p in glob.glob(sys.argv[1]+'/records/*/*/*.json') if json.load(open(p))['object_id']==sys.argv[2]]; print(found[0] if found else '')"
    , root, objectId
    ] ""
  assert "proposal fixture finds the committed object head"
    (code == ExitSuccess && not (null (trim output))) code output errors
  pure (trim output)

contractCitation :: FilePath -> String -> IO (String, String, String)
contractCitation root contractId = contractCitationMatching root contractId Nothing

contractCitationVersion :: FilePath -> String -> String -> IO (String, String, String)
contractCitationVersion root contractId version =
  contractCitationMatching root contractId (Just version)

contractCitationMatching :: FilePath -> String -> Maybe String
                         -> IO (String, String, String)
contractCitationMatching root contractId wantedVersion = do
  (code, output, errors) <- readProcessWithExitCode "python3"
    [ "-c"
    , "import glob,json,os,sys; found=[(str(json.load(open(p))['version']),os.path.basename(p)[7:-5]) for p in glob.glob(sys.argv[1]+'/contracts/*.json') if json.load(open(p)).get('id')==sys.argv[2] and (not sys.argv[3] or str(json.load(open(p))['version'])==sys.argv[3])]; found.sort(); print(found[0][0]+' '+found[0][1] if found else '')"
    , root, contractId, maybe "" id wantedVersion
    ] ""
  let fields = words (trim output)
  assert "proposal fixture finds the installed contract"
    (code == ExitSuccess && length fields == 2) code output errors
  case fields of
    [version, digest] -> pure (contractId, version, "sha256:" <> digest)
    _ -> assertFailure "contract fixture has no version and digest"

commitEntityFixture :: FilePath -> String -> IO String
commitEntityFixture root objectId = commitDraftFixture root (entityDraft objectId)

commitArtifactFixture :: FilePath -> String -> IO String
commitArtifactFixture root objectId = commitArtifactFixtureWithDigest root objectId zeroDigest

commitArtifactFixtureWithDigest :: FilePath -> String -> String -> IO String
commitArtifactFixtureWithDigest root objectId digest =
  commitDraftFixture root (replace zeroDigest digest (artifactDraft objectId))

commitDraftFixture :: FilePath -> String -> IO String
commitDraftFixture root draft = do
  value <- case decodeUnique (LazyByteString.pack draft) of
    Left problem -> assertFailure ("entity fixture JSON: " <> problem)
    Right parsed -> pure parsed
  (revision, stored) <- case finalizeStored "2026-01-01T00:00:00Z" value of
    Left problem -> assertFailure ("entity fixture writer: " <> Text.unpack problem)
    Right result -> pure result
  let relative = recordRelative (Text.unpack revision)
  createDirectoryIfMissing True (takeDirectory (root </> relative))
  LazyByteString.writeFile (root </> relative) (encodeCanonical stored)
  shellChecked ("git -C " <> quote root <> " add " <> quote relative
    <> " && git -C " <> quote root <> " -c user.name=archive-test"
    <> " -c user.email=archive-test@example.invalid commit -m fixture >/dev/null")
  pure (Text.unpack revision)

-- Review application contracts (ADR 0048, specification 0.0.5).

-- | Admit one new record through `door admit`.  The draft is built from the
-- allocated object ID, so the proposal's identity row always matches.
admitNew :: FilePath -> String -> String -> String -> String
         -> (String -> String) -> IO (String, String)
admitNew root archiveId label namespace name draftFor = do
  objectId <- allocatedObjectId archiveId namespace name
  let draft = draftFor objectId
  revision <- canonicalRevisionOf draft
  (code, out, err) <- admitDraft root objectId namespace name draft
  assert label (code == ExitSuccess && contains revision out) code out err
  pure (objectId, revision)

-- | Propose one new record and require a refusal naming the expected reason.
-- Returns the refused draft so a test can anchor what it changed.
refuseNew :: FilePath -> String -> String -> String -> String
          -> (String -> String) -> String -> IO String
refuseNew root archiveId label namespace name draftFor expected = do
  objectId <- allocatedObjectId archiveId namespace name
  let draft = draftFor objectId
  (code, out, err) <- admitDraft root objectId namespace name draft
  assert label (code /= ExitSuccess && contains expected (err <> out)) code out err
  pure draft

admitDraft :: FilePath -> String -> String -> String -> String
           -> IO (ExitCode, String, String)
admitDraft root objectId namespace name draft = do
  proposal <- writeAdmissionProposal root name
    (manifestFor ["records/record.json"] [(objectId, namespace, name)]
      [(objectId, Nothing)] [])
    ["records/record.json", draft]
  doorCommandAt root ["admit", "--proposal", proposal] ""

transitionAt :: FilePath -> String -> [(String, String, String)] -> [(String, Maybe String)]
             -> String -> IO (ExitCode, String, String)
transitionAt root name identities heads draft = do
  proposal <- writeTransitionProposal root name
    (transitionManifestFor ["records/transition.json"] identities heads)
    ["records/transition.json", draft]
  doorCommandAt root ["transition", "--proposal", proposal] ""

-- | Archive-relative record and content paths, from the layout module.
recordRelative :: String -> FilePath
recordRelative revision = either (error . Text.unpack) recordRelativePath
  (parseSha256Digest (Text.pack revision))
