{-# LANGUAGE OverloadedStrings #-}

-- | Checks of `archive export --since` (ADR 0055) over records admitted by Door.
module ExportChecks
  ( tests
  ) where

import Data.Aeson ((.=))
import qualified Data.Aeson as Aeson
import qualified Data.Aeson.KeyMap as KeyMap
import qualified Data.ByteString.Lazy.Char8 as LazyChar8
import Data.Foldable (toList)
import qualified Data.Set as Set
import qualified Data.Text as Text
import Support
import System.Directory
  ( createDirectoryIfMissing
  , findExecutable
  , getPermissions
  , listDirectory
  , removePathForcibly
  , setOwnerExecutable
  , setPermissions
  )
import System.Environment (getEnvironment)
import System.Exit (ExitCode (ExitFailure, ExitSuccess))
import System.FilePath ((</>))
import System.Process
  ( CreateProcess (cwd, env)
  , proc
  , readCreateProcessWithExitCode
  )
import Test.Tasty (TestTree, testGroup)
import Test.Tasty.HUnit (testCase)

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

-- | The export checks, one case each.
tests :: TestTree
tests = testGroup "export"
  [ testCase "exportSinceChecks" (initialized exportSinceChecks)
  ]
