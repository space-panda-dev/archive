{-# LANGUAGE OverloadedStrings #-}

-- | Shared helpers of door-test that need no Door library: the assertions,
-- the row machinery that makes independent checks separate cases, the
-- scratch directories and environment settings that are undone however a
-- case ends, shell and string helpers, and the draft builders.
module Support
  ( writer
  , person
  , entity
  , claim
  , assessment
  , zeroDigest
  , otherDigest
  , entityDraft
  , artifactDraft
  , claimDraft
  , envelope
  , caseOf
  , treeOf
  , rowsOf
  , releaseOf
  , checkGroup
  , Recorder
  , superseding
  , withTemporaryRoot
  , withScratchRoot
  , withScratchDirectory
  , withEnv
  , isLeft
  , withProposalTemporaryRoot
  , citationJson
  , musicBrainzClaim
  , GitStep (..)
  , gitStepTest
  , killedAfter
  , (</>)
  , takeDirectory
  , manifestFor
  , transitionManifestFor
  , joinWith
  , jsonString
  , retireDraft
  , destroyDraft
  , destroyDraftWithPredecessor
  , transitionReference
  , jsonStringField
  , shellChecked
  , shell
  , quote
  , replace
  , after
  , trim
  , assert
  , failCheck
  , contains
  , contractRecord
  , pinned
  , entityRecord
  , digestOf
  , locatorJson
  , selectionTranslationPayload
  , runName
  , runPayloadAt
  , runPayload
  , spansPayload
  , windowMatchPayload
  ) where

import Control.Exception
  ( SomeAsyncException
  , SomeException
  , bracket
  , bracket_
  , evaluate
  , fromException
  , throwIO
  , try
  )
import Control.Monad (unless, when)
import Data.IORef (newIORef, modifyIORef', readIORef)
import GHC.Stack (HasCallStack)
import System.Directory (removePathForcibly)
import System.Environment (lookupEnv, setEnv, unsetEnv)
import System.Exit (ExitCode (ExitSuccess))
import System.IO (hPutStrLn, stderr)
import System.Process (readProcessWithExitCode)
import Test.Tasty (TestName, TestTree, testGroup)
import Test.Tasty.HUnit (assertFailure, testCase)

writer, person, entity, claim, assessment, zeroDigest, otherDigest :: String
writer = "pkg:hackage/archive@0.0.1.0"
person = "urn:uuid:00000000-0000-4000-8000-000000000001"
entity = "urn:uuid:00000000-0000-4000-8000-000000000002"
claim = "urn:uuid:00000000-0000-4000-8000-000000000003"
assessment = "urn:uuid:00000000-0000-4000-8000-000000000004"
zeroDigest = "sha256:0000000000000000000000000000000000000000000000000000000000000000"
otherDigest = "sha256:1111111111111111111111111111111111111111111111111111111111111111"

entityDraft, artifactDraft :: String -> String
entityDraft objectId = envelope objectId "entity" "{\"kind\":\"person\",\"label\":\"test\"}" "[]" ""
artifactDraft objectId = envelope objectId "artifact" ("{\"content\":{\"digest\":\"" <> zeroDigest <> "\",\"size\":0}}") "[]" ""

claimDraft :: String -> String -> String
claimDraft objectId target = envelope objectId "claim" "{}"
  ("[{\"role\":\"about\",\"id\":\"" <> target <> "\"}]")
  (",\"contracts\":[{\"id\":\"archive.test/example\",\"version\":\"1\",\"digest\":\"" <> zeroDigest <> "\"}]")

envelope :: String -> String -> String -> String -> String -> String
envelope objectId shape payload refs extra =
  "{\"spec\":\"archive/0.0.12\",\"object_id\":\"" <> objectId
  <> "\",\"shape\":\"" <> shape <> "\",\"by\":\"" <> writer
  <> "\",\"agent\":\"" <> person <> "\",\"payload\":" <> payload
  <> ",\"refs\":" <> refs <> ",\"supersedes\":[]" <> extra <> "}"

-- | A case that needs nothing computed before the tree is fixed.
caseOf :: TestName -> IO () -> IO TestTree
caseOf name = pure . testCase name

-- | A tree that needs nothing computed before it is fixed.
treeOf :: TestTree -> IO TestTree
treeOf = pure

-- | One case per row a recording check records.
rowsOf :: TestName -> (Recorder -> IO ()) -> IO TestTree
rowsOf name = rowGroup name . recordedRows

-- | One case per row of a release module.
releaseOf :: TestName -> IO [(String, Bool, String)] -> IO TestTree
releaseOf name = rowGroup name . releaseRows

-- | One test case per row, so a failing row leaves the other rows' results.
checkGroup :: TestName -> [(String, Bool, String)] -> TestTree
checkGroup name rows = testGroup name
  [testCase label (assert label passed ExitSuccess detail "") | (label, passed, detail) <- rows]

-- | A check's result row: its label, whether it passed, and the exit code,
-- output and errors 'assert' reports when it did not.
type Row = (String, Bool, ExitCode, String, String)

-- | A release module's (label, passed, detail) rows as 'Row's.
releaseRows :: IO [(String, Bool, String)] -> IO [Row]
releaseRows = fmap (map (\(label, passed, detail) -> (label, passed, ExitSuccess, detail, "")))

-- | One test case per row of rows computed before the tree is fixed. An
-- exception while computing them, or while forcing their labels, becomes this
-- group's one failing case, so the rest of the suite still runs; a row whose
-- result throws fails its own case only.
rowGroup :: TestName -> IO [Row] -> IO TestTree
rowGroup name compute = do
  computed <- try (compute >>= \rows -> evaluate (sum (map rowLabelLength rows)) >> pure rows)
    :: IO (Either SomeException [Row])
  case computed of
    Left problem
      | Just (_ :: SomeAsyncException) <- fromException problem -> throwIO problem
      | otherwise -> pure (testGroup name
          [testCase "rows" (assertFailure ("computing the rows failed: " <> show problem))])
    Right rows -> pure (testGroup name
      [testCase label (assert label passed code output errors) | (label, passed, code, output, errors) <- rows])
  where
    rowLabelLength (label, _, _, _, _) = length label

-- | What a recording check is given in place of 'assert': the same
-- arguments, recorded as a 'Row' instead of failing at once.
type Recorder = String -> Bool -> ExitCode -> String -> String -> IO ()

-- | The rows a check records, in order. A check of independent results
-- records them, so each becomes its own case through 'rowGroup' while the
-- inputs they share are read once.
recordedRows :: (Recorder -> IO ()) -> IO [Row]
recordedRows recording = do
  recorded <- newIORef []
  recording (\label passed code output errors ->
    modifyIORef' recorded ((label, passed, code, output, errors) :))
  reverse <$> readIORef recorded

superseding :: String -> String -> String -> String
superseding objectId target revision = replace "\"supersedes\":[]" ("\"supersedes\":[{\"role\":\"supersedes\",\"id\":\"" <> target <> "\",\"revision_id\":\"" <> revision <> "\"}]") (replace "\"label\":\"test\"" "\"label\":\"revised\"" (entityDraft objectId))

-- | Runs the action on a root that does not exist yet and on an empty one,
-- both in a fresh scratch directory.
withTemporaryRoot :: (FilePath -> IO ()) -> IO ()
withTemporaryRoot action =
  withScratchRoot $ \parent -> do
    shellChecked ("mkdir " <> quote (parent <> "/empty"))
    action (parent <> "/missing")
    action (parent <> "/empty")

-- | A fresh scratch directory for checks that make their own paths in it.
withScratchRoot :: (FilePath -> IO ()) -> IO ()
withScratchRoot = withScratchDirectory "/tmp/archive-phase4-tests.XXXXXX"

-- | Runs the action in a fresh directory made by @mktemp -d TEMPLATE@ and
-- removes it however the action ends, because a failing case no longer stops
-- the run and would otherwise leave its directory behind. With
-- ARCHIVE_TEST_KEEP set to anything non-empty the directory is kept and its
-- path printed, so a failure can be inspected.
withScratchDirectory :: String -> (FilePath -> IO a) -> IO a
withScratchDirectory template action = do
  (code, output, errors) <- shell ("mktemp -d " <> template)
  when (code /= ExitSuccess) (assertFailure ("mktemp: " <> errors))
  bracket (pure (trim output)) release action
  where
    release scratch = do
      keep <- maybe False (not . null) <$> lookupEnv "ARCHIVE_TEST_KEEP"
      if keep
        then hPutStrLn stderr ("ARCHIVE_TEST_KEEP: kept " <> scratch)
        else removePathForcibly scratch

-- | Runs the action with the environment variable NAME set to VALUE, and
-- restores NAME's earlier value, or its absence, however the action ends:
-- the cases share the process environment, and a failing case no longer stops
-- the run, so a setting left behind would leak into the cases after it.
withEnv :: String -> String -> IO a -> IO a
withEnv name value action = do
  previous <- lookupEnv name
  bracket_ (setEnv name value) (maybe (unsetEnv name) (setEnv name) previous) action

isLeft :: Either a b -> Bool
isLeft result = case result of
  Left _ -> True
  Right _ -> False

-- Proposal tests deliberately use the command boundary.  The archive and
-- proposal are both made on disk so that these tests exercise the manifest,
-- descriptor traversal, UUID allocation, and committed-record checks together.
withProposalTemporaryRoot :: (FilePath -> IO ()) -> IO ()
withProposalTemporaryRoot = withScratchDirectory "/tmp/archive-admission-proposals.XXXXXX"

citationJson :: (String, String, String) -> String
citationJson (identifier, version, digest) =
  "{\"id\":" <> jsonString identifier <> ",\"version\":" <> jsonString version
    <> ",\"digest\":" <> jsonString digest <> "}"

musicBrainzClaim :: String -> String -> String
                  -> (String, String, String) -> String
musicBrainzClaim objectId workId workRevision (identifier, version, digest) =
  envelope objectId "claim"
    "{\"external_work\":\"https://musicbrainz.org/work/11111111-1111-4111-8111-111111111111\",\"mapping\":\"skos:exactMatch\"}"
    ("[{\"role\":\"work\",\"id\":" <> jsonString workId
      <> ",\"revision_id\":" <> jsonString workRevision <> "}]")
    (",\"contracts\":[{\"id\":" <> jsonString identifier
      <> ",\"version\":" <> jsonString version
      <> ",\"digest\":" <> jsonString digest <> "}]")

-- | A Git step Door takes outside its temporary index: advancing the branch,
-- refreshing the ordinary index, or comparing the ordinary index with a
-- commit. A Door-run Git child without GIT_INDEX_FILE uses the ordinary index.
-- git-annex runs the configured Git too, and updates its own branch; only
-- Door's branch update passes --create-reflog.
data GitStep = RefUpdate | IndexRefresh | IndexComparison

gitStepTest :: GitStep -> String
gitStepTest RefUpdate = "[ \"$argument\" = --create-reflog ]"
gitStepTest IndexRefresh = "[ \"$argument\" = read-tree ] && [ -z \"$GIT_INDEX_FILE\" ]"
gitStepTest IndexComparison = "[ \"$argument\" = --cached ] && [ -z \"$GIT_INDEX_FILE\" ]"

-- | A Git wrapper that kills its parent Door once the step has succeeded.
killedAfter :: GitStep -> FilePath -> String
killedAfter step realGit =
  "#!/bin/sh\nhit=0\nfor argument in \"$@\"; do\n"
    <> "  if " <> gitStepTest step <> "; then hit=1; fi\n"
    <> "done\n" <> quote realGit <> " \"$@\"\nstatus=$?\n"
    <> "if [ \"$hit\" = 1 ] && [ \"$status\" = 0 ]; then\n"
    <> "  kill -KILL \"$PPID\"\n  sleep 1\nfi\nexit \"$status\"\n"

infixr 5 </>
(</>) :: FilePath -> FilePath -> FilePath
left </> right = left <> "/" <> right

takeDirectory :: FilePath -> FilePath
takeDirectory = reverse . dropWhile (/= '/') . reverse

manifestFor :: [String] -> [(String, String, String)] -> [(String, Maybe String)]
            -> [(String, Integer, String, String)] -> String
manifestFor paths identities heads contents =
  "{\"protocol\":\"archive-admission/1\",\"records\":["
  <> joinWith "," (map jsonString paths)
  <> "],\"identities\":["
  <> joinWith "," (map identityJson identities)
  <> "],\"contents\":["
  <> joinWith "," (map contentJson contents)
  <> "],\"expected_heads\":["
  <> joinWith "," (map expectedJson heads)
  <> "]}"
  where
    identityJson (objectId, namespace, name) =
      "{\"object_id\":" <> jsonString objectId
      <> ",\"namespace\":" <> jsonString namespace
      <> ",\"name\":" <> jsonString name <> "}"
    expectedJson (objectId, revision) =
      "{\"object_id\":" <> jsonString objectId
      <> ",\"revision_id\":" <> maybe "null" jsonString revision <> "}"
    contentJson (digest, size, annexKey, remote) =
      "{\"digest\":" <> jsonString digest
      <> ",\"size\":" <> show size
      <> ",\"annex_key\":" <> jsonString annexKey
      <> ",\"staging_remote\":" <> jsonString remote <> "}"

transitionManifestFor :: [String] -> [(String, String, String)]
                      -> [(String, Maybe String)] -> String
transitionManifestFor paths identities heads =
  "{\"protocol\":\"archive-transition/1\",\"records\":["
  <> joinWith "," (map jsonString paths)
  <> "],\"identities\":["
  <> joinWith "," (map identityJson identities)
  <> "],\"expected_heads\":["
  <> joinWith "," (map expectedJson heads)
  <> "]}"
  where
    identityJson (objectId, namespace, name) =
      "{\"object_id\":" <> jsonString objectId
      <> ",\"namespace\":" <> jsonString namespace
      <> ",\"name\":" <> jsonString name <> "}"
    expectedJson (objectId, revision) =
      "{\"object_id\":" <> jsonString objectId
      <> ",\"revision_id\":" <> maybe "null" jsonString revision <> "}"

joinWith :: String -> [String] -> String
joinWith _ [] = ""
joinWith separator (value : values) = value <> concatMap (separator <>) values

jsonString :: String -> String
jsonString value = '"' : concatMap escape value <> "\""
  where
    escape '"' = "\\\""
    escape '\\' = "\\\\"
    escape character
      | fromEnum character < 0x20 = "\\u" <> fourHex (fromEnum character)
      | otherwise = [character]
    fourHex number = [hex (number `div` 4096), hex ((number `div` 256) `mod` 16)
                     , hex ((number `div` 16) `mod` 16), hex (number `mod` 16)]
    hex number | number < 10 = toEnum (fromEnum '0' + number)
              | otherwise = toEnum (fromEnum 'a' + number - 10)

retireDraft :: String -> String -> String -> String -> String
            -> (String, String, String) -> String
retireDraft objectId operatorId operatorRevision targetId targetRevision
    (contractId, version, digest) =
  "{\"spec\":\"archive/0.0.12\",\"object_id\":" <> jsonString objectId
  <> ",\"shape\":\"claim\",\"by\":" <> jsonString writer
  <> ",\"agent\":" <> jsonString operatorId
  <> ",\"payload\":{\"reason\":\"test retirement\"},\"refs\":["
  <> transitionReference "author" operatorId operatorRevision <> ","
  <> transitionReference "authorizer" operatorId operatorRevision <> ","
  <> transitionReference "retires" targetId targetRevision
  <> "],\"supersedes\":[],\"contracts\":[{\"id\":" <> jsonString contractId
  <> ",\"version\":" <> jsonString version <> ",\"digest\":" <> jsonString digest
  <> "}]}"

destroyDraft :: String -> String -> String -> String -> String -> String
             -> Integer -> (String, String, String) -> String
destroyDraft objectId operatorId operatorRevision targetId targetRevision digest size
    (contractId, version, contractDigest) =
  "{\"spec\":\"archive/0.0.12\",\"object_id\":" <> jsonString objectId
  <> ",\"shape\":\"claim\",\"by\":" <> jsonString writer
  <> ",\"agent\":" <> jsonString operatorId
  <> ",\"payload\":{\"content\":{\"digest\":" <> jsonString digest
  <> ",\"size\":" <> show size
  <> "},\"reason\":\"test destruction\",\"external_survivors\":[]},\"refs\":["
  <> transitionReference "author" operatorId operatorRevision <> ","
  <> transitionReference "authorizer" operatorId operatorRevision <> ","
  <> transitionReference "destroys" targetId targetRevision
  <> "],\"supersedes\":[],\"contracts\":[{\"id\":"
  <> jsonString contractId <> ",\"version\":" <> jsonString version
  <> ",\"digest\":" <> jsonString contractDigest <> "}]}"

destroyDraftWithPredecessor :: String -> String -> String -> String -> String
                             -> String -> Integer -> (String, String, String)
                             -> String -> String -> String
destroyDraftWithPredecessor objectId operatorId operatorRevision targetId targetRevision
    digest size contract predecessorId predecessorRevision =
  replace "],\"supersedes\""
    ("," <> transitionReference "transition_predecessor"
      predecessorId predecessorRevision <> "],\"supersedes\"")
    (destroyDraft objectId operatorId operatorRevision targetId targetRevision
      digest size contract)

transitionReference :: String -> String -> String -> String
transitionReference role objectId revision =
  "{\"role\":" <> jsonString role <> ",\"id\":" <> jsonString objectId
  <> ",\"revision_id\":" <> jsonString revision <> "}"

jsonStringField :: String -> String -> Maybe String
jsonStringField name value =
  takeWhile (/= '"') <$> after ("\"" <> name <> "\":\"") value

shellChecked :: String -> IO ()
shellChecked script = do
  (code, output, errors) <- shell script
  assert "temporary test setup" (code == ExitSuccess) code output errors

shell :: String -> IO (ExitCode, String, String)
shell script = readProcessWithExitCode "sh" ["-c", script] ""

quote :: String -> String
quote value = "'" <> concatMap escape value <> "'"
  where escape '\'' = "'\\''"
        escape character = [character]

replace :: String -> String -> String -> String
replace needle replacement value = case after needle value of
  Nothing -> value
  Just suffix -> take (length value - length suffix - length needle) value <> replacement <> suffix

after :: String -> String -> Maybe String
after needle value = search value
  where search [] = if null needle then Just [] else Nothing
        search remaining | needle `prefixOf` remaining = Just (drop (length needle) remaining)
                         | otherwise = search (drop 1 remaining)

trim :: String -> String
trim = dropWhileEnd (`elem` [' ', '\n', '\r', '\t']) . dropWhile (`elem` [' ', '\n', '\r', '\t'])

dropWhileEnd :: (a -> Bool) -> [a] -> [a]
dropWhileEnd predicate = reverse . dropWhile predicate . reverse

-- | Fails the running test case with the label and the command's result.
assert :: HasCallStack => String -> Bool -> ExitCode -> String -> String -> IO ()
assert label passed code output errors = unless passed (failCheck label code output errors)

-- | The failure 'assert' raises, for the fixtures that cannot go on.
failCheck :: HasCallStack => String -> ExitCode -> String -> String -> IO a
failCheck label code output errors = assertFailure (unlines
  [label, "exit: " <> show code, "stdout: " <> output, "stderr: " <> errors])

contains :: String -> String -> Bool
contains needle haystack = any (needle `prefixOf`) (tails haystack)

prefixOf :: String -> String -> Bool
prefixOf [] _ = True
prefixOf _ [] = False
prefixOf (x:xs) (y:ys) = x == y && prefixOf xs ys

tails :: String -> [String]
tails [] = [[]]
tails value@(_:rest) = value : tails rest

contractRecord :: String -> (String, String, String) -> String -> [String] -> String -> String
contractRecord shape contract payload refs objectId =
  envelope objectId shape payload ("[" <> joinWith "," refs <> "]")
    (",\"contracts\":[" <> citationJson contract <> "]")

pinned :: String -> (String, String) -> String
pinned role (objectId, revision) = transitionReference role objectId revision

entityRecord :: String -> String -> String -> String
entityRecord kind label objectId =
  envelope objectId "entity"
    ("{\"kind\":" <> jsonString kind <> ",\"label\":" <> jsonString label <> "}") "[]" ""

digestOf :: Char -> String
digestOf character = "sha256:" <> replicate 64 character

locatorJson :: String
locatorJson =
  "{\"length\":512,\"member\":\"records/audio-segments/seg_fixture.json\""
  <> ",\"member_sha256\":" <> jsonString (digestOf 'a')
  <> ",\"offset\":1024,\"sha256\":" <> jsonString (digestOf 'b') <> "}"

selectionTranslationPayload :: String -> String -> String
selectionTranslationPayload start end =
  "{\"boundary_rule\":\"round-half-even/1\",\"chain_position\":0"
  <> ",\"chain_root\":\"seg_fixture\",\"locator\":" <> locatorJson
  <> ",\"probe\":{\"sample_rate\":48000,\"tool\":\"ffprobe\",\"tool_version\":\"7.1\"}"
  <> ",\"rule\":\"v2-audio-segment-chain/1\""
  <> ",\"source\":{\"id\":\"seg_fixture\",\"schema\":\"audio_segment\"}"
  <> ",\"source_seconds\":{\"end\":" <> jsonString end <> ",\"start\":" <> jsonString start <> "}}"

-- Derivation runs, audio facts, suggestions, and run-pinned matches and
-- alignments (ADRs 0064 to 0068, specification 0.0.9), through the real
-- `door admit`. Each refusal's draft differs from an accepted twin only in the
-- rule's field, and the test anchors that difference.

runName :: Int -> String
runName index = "run-00000000-0000-4000-8000-" <> replicate (12 - length digits) '0' <> digits
  where digits = show index

runPayloadAt :: String -> String -> String
runPayloadAt started ended =
  "{\"ended_at\":" <> jsonString ended
  <> ",\"method\":{\"id\":\"energy-spans\",\"version\":\"1\"}"
  <> ",\"software\":[{\"name\":\"ffmpeg\",\"version\":\"7.1\"},{\"name\":\"numpy\",\"version\":\"2.1.0\"}]"
  <> ",\"started_at\":" <> jsonString started <> "}"

runPayload :: String
runPayload = runPayloadAt "2026-10-16T10:00:00Z" "2026-10-16T10:00:05.250Z"

spansPayload :: Integer -> [(Integer, Integer, String)] -> String
spansPayload rate spans =
  "{\"sample_rate\":" <> show rate <> ",\"spans\":[" <> joinWith "," (map span' spans) <> "]}"
  where
    span' (start, end, kind) =
      "{\"class\":" <> jsonString kind <> ",\"end_sample\":" <> show end
      <> ",\"start_sample\":" <> show start <> "}"

-- | A version 3 match's payload: the version 2 fields, the suggestions'
-- rate, and a window.
windowMatchPayload :: Integer -> Integer -> Integer -> String
windowMatchPayload rate start end =
  "{\"direction\":\"lower_is_closer\",\"method\":{\"id\":\"essentia-serra09\",\"version\":\"1\"},\"sample_rate\":"
  <> show rate <> ",\"scale\":\"distance\",\"score\":\"0.0612\",\"window\":{\"end_sample\":" <> show end
  <> ",\"start_sample\":" <> show start <> "}}"
