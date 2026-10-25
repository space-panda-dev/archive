{-# LANGUAGE OverloadedStrings #-}

-- | Checks that a reader reports an archive missing a contract, context,
-- version, specification or manifest as incomplete, and keeps the records it
-- can still read.
module IncompleteChecks
  ( tests
  ) where

import Support
import System.Directory (createDirectoryIfMissing, listDirectory, renameFile)
import System.Exit (ExitCode (ExitFailure))
import System.FilePath ((</>))
import Test.Tasty (TestTree, testGroup)
import Test.Tasty.HUnit (testCase)

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

-- | The incomplete archives checks, one case each.
tests :: TestTree
tests = testGroup "incomplete archives"
  [ testCase "missingContractIsIncomplete" (initialized missingContractIsIncomplete)
  , testCase "malformedRecordRetainsGoodRecords" (initialized malformedRecordRetainsGoodRecords)
  , testCase "missingContextIsIncomplete" (initialized missingContextIsIncomplete)
  , testCase "missingVersionIsIncomplete" (initialized missingVersionIsIncomplete)
  , testCase "missingSpecificationIsIncomplete" (initialized missingSpecificationIsIncomplete)
  , testCase "noncanonicalManifestIsIncomplete" (initialized noncanonicalManifestIsIncomplete)
  , testCase "malformedManifestRetainsRecords" (initialized malformedManifestRetainsRecords)
  , testCase "missingManifestRetainsRecords" (initialized missingManifestRetainsRecords)
  ]
