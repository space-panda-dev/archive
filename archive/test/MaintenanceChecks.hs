{-# LANGUAGE OverloadedStrings #-}

-- | Checks of `archive maintenance repack`: it packs loose objects through the
-- verified checkpoint without walking history.
module MaintenanceChecks
  ( tests
  ) where

import Data.Aeson ((.=))
import qualified Data.Aeson as Aeson
import qualified Data.Text as Text
import Support
import System.Directory
  ( createDirectoryIfMissing
  , doesFileExist
  , findExecutable
  , getPermissions
  , removePathForcibly
  , setOwnerExecutable
  , setPermissions
  )
import System.Exit (ExitCode (ExitSuccess))
import System.FilePath ((</>))
import System.Process (CreateProcess (cwd), proc, readCreateProcessWithExitCode)
import Test.Tasty (TestTree, testGroup)
import Test.Tasty.HUnit (testCase)

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

-- | The maintenance checks, one case each.
tests :: TestTree
tests = testGroup "maintenance"
  [ testCase "repackChecks" (initialized repackChecks)
  ]
