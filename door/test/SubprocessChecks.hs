{-# LANGUAGE OverloadedStrings #-}

-- | Checks of bounded subprocess capture (Archive.Process): output limits and
-- interruption.
module SubprocessChecks
  ( tests
  ) where

import Archive.Process
  ( ProcessError (..)
  , ProcessStream (..)
  , runProcessBounded
  , runProcessBoundedAt
  )
import Control.Concurrent (forkIO, threadDelay, throwTo)
import Control.Concurrent.MVar (newEmptyMVar, putMVar, takeMVar)
import Control.Exception (AsyncException (UserInterrupt), try)
import qualified Data.ByteString as ByteString
import Fixtures
import Support
import System.Directory
  ( createDirectoryIfMissing
  , doesFileExist
  , findExecutable
  , getPermissions
  , removeFile
  , setOwnerExecutable
  , setPermissions
  )
import System.Environment (getEnvironment)
import System.Exit (ExitCode (ExitFailure, ExitSuccess))
import System.Posix.IO (OpenMode (ReadOnly), closeFd, defaultFileFlags, openFd)
import System.Process
  ( CreateProcess (cwd, env)
  , proc
  , readCreateProcessWithExitCode
  )
import System.Timeout (timeout)
import Test.Tasty (TestTree, testGroup)
import Test.Tasty.HUnit (testCase)

subprocessCaptureLimitChecks :: TestTree
subprocessCaptureLimitChecks = testGroup "subprocessCaptureLimitChecks"
  [ testCase "bounded subprocess capture refuses oversized control output" $ do
      python <- python3
      result <- runProcessBounded
        (proc python ["-c", "import sys; sys.stdout.write('x' * 65536)"]) (Just 1024) 1024
      assert "bounded subprocess capture refuses oversized control output"
        (result == Left (ProcessOutputLimitFailure StandardOutput 1024))
        ExitSuccess "" (show result)
  , testCase "bounded subprocess capture types oversized diagnostics separately" $ do
      python <- python3
      stderrResult <- runProcessBounded
        (proc python ["-c", "import sys; sys.stderr.write('e' * 65536)"])
        Nothing 32
      assert "bounded subprocess capture types oversized diagnostics separately"
        (stderrResult == Left (ProcessOutputLimitFailure StandardError 32))
        ExitSuccess "" (show stderrResult)
  ]
  where
    python3 = findExecutable "python3" >>= maybe
      (ioError (userError "subprocess-limit test cannot find python3")) pure

subprocessOutputLimitChecks :: FilePath -> IO ()
subprocessOutputLimitChecks parent = do
  (root, archiveId, _) <- newProposalArchive parent "output-limit"
  objectId <- allocatedObjectId archiveId "archive.core/entity" "output-limited"
  let draft = entityDraft objectId
      manifest = manifestFor ["records/entity.json"]
        [(objectId, "archive.core/entity", "output-limited")]
        [(objectId, Nothing)] []
  proposal <- writeAdmissionProposal root "output-limit" manifest
    ["records/entity.json", draft]
  realDoor <- findExecutable "door" >>= maybe
    (ioError (userError "output-limit fixture cannot find door")) pure
  let fakeBin = parent </> "output-flood-bin"
      fakeGit = fakeBin </> "git"
  createDirectoryIfMissing True fakeBin
  writeFile fakeGit
    "#!/bin/sh\nexec python3 -c 'import sys; sys.stdout.write(\"x\" * (64 * 1024 * 1024 + 1024))'\n"
  permissions <- getPermissions fakeGit
  setPermissions fakeGit (setOwnerExecutable True permissions)
  environment <- getEnvironment
  let clean = ("ARCHIVE_GIT_EXECUTABLE", fakeGit)
        : filter ((/= "ARCHIVE_GIT_EXECUTABLE") . fst) environment
      process = (proc realDoor ["admit", "--proposal", proposal, "--dry-run"])
        { cwd = Just root
        , env = Just clean
        }
  (code, output, errors) <- readCreateProcessWithExitCode process ""
  assert "Git control-plane stdout is capped and terminates the child"
    (code /= ExitSuccess && null output
      && contains "git stdout exceeded the control-plane output limit" errors)
    code output errors

subprocessCaptureInterruptionChecks :: FilePath -> IO ()
subprocessCaptureInterruptionChecks parent = do
  python <- findExecutable "python3" >>= maybe
    (ioError (userError "subprocess-interruption test cannot find python3")) pure
  let started = parent </> "subprocess-started"
      completed = parent </> "subprocess-completed"
      script = "import pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text('started'); "
        <> "time.sleep(0.5); pathlib.Path(sys.argv[2]).write_text('completed')"
      process = proc python ["-c", script, started, completed]
  resultVar <- newEmptyMVar
  thread <- forkIO $ do
    result <- try (runProcessBounded process Nothing 1024)
      :: IO (Either AsyncException (Either ProcessError
        (ExitCode, ByteString.ByteString, ByteString.ByteString)))
    putMVar resultVar result
  startedInTime <- waitForFile 50 started
  assert "subprocess interruption fixture started" startedInTime ExitSuccess "" ""
  throwTo thread UserInterrupt
  result <- takeMVar resultVar
  threadDelay 700000
  didComplete <- doesFileExist completed
  assert "interrupting bounded capture terminates and reaps its child"
    (result == Left UserInterrupt && not didComplete)
    ExitSuccess "" (show result)
  processGroupCleanupChecks parent
  where
    waitForFile :: Int -> FilePath -> IO Bool
    waitForFile 0 path = doesFileExist path
    waitForFile remaining path = do
      exists <- doesFileExist path
      if exists then pure True else threadDelay 20000 >> waitForFile (remaining - 1) path

processGroupCleanupChecks :: FilePath -> IO ()
processGroupCleanupChecks parent = do
  python <- findExecutable "python3" >>= maybe
    (ioError (userError "process-group test cannot find python3")) pure
  directoryFd <- openFd parent ReadOnly defaultFileFlags
  let run script arguments = runProcessBoundedAt directoryFd python ("-c" : script : arguments)
        [("PATH", "/usr/bin:/bin")] Nothing 1024
      inheritedPipeScript = "import subprocess,sys; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(10)'])"
  inherited <- timeout 3000000 (run inheritedPipeScript [])
  assert "pinned process group closes inherited pipes after wrapper exit"
    (case inherited of Just (Right (ExitSuccess, _, _)) -> True; _ -> False)
    ExitSuccess "" (show inherited)
  let marker = parent </> "process-group-child-completed"
      delayedChildScript = "import pathlib,sys,time; time.sleep(0.6); pathlib.Path(sys.argv[1]).write_text('late')" :: String
      closedPipeScript = "import subprocess,sys; subprocess.Popen([sys.executable, '-c', "
        <> reprPython delayedChildScript <> ", sys.argv[1]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); raise SystemExit(1)"
  exists <- doesFileExist marker
  if exists then removeFile marker else pure ()
  closed <- timeout 3000000 (run closedPipeScript [marker])
  threadDelay 900000
  wroteAfterFailure <- doesFileExist marker
  assert "pinned process group kills a detached-output child before failure returns"
    (case closed of Just (Right (ExitFailure 1, _, _)) -> not wroteAfterFailure; _ -> False)
    ExitSuccess "" (show (closed, wroteAfterFailure))
  bounded <- runProcessBoundedAt directoryFd python
    ["-c", "import sys;\nwhile True:\n sys.stdout.write('x' * 4096); sys.stdout.flush()"]
    [("PATH", "/usr/bin:/bin")] (Just 64) 1024
  assert "pinned bounded capture kills an infinite stdout writer at its limit"
    (bounded == Left (ProcessOutputLimitFailure StandardOutput 64))
    ExitSuccess "" (show bounded)
  missing <- runProcessBoundedAt directoryFd "/definitely/not/an/executable" []
    [("PATH", "/usr/bin:/bin")] Nothing 1024
  assert "pinned subprocess spawn failures retain their source category"
    (case missing of Left (ProcessSpawnFailure _) -> True; _ -> False)
    ExitSuccess "" (show missing)
  nonzero <- runProcessBoundedAt directoryFd python ["-c", "raise SystemExit(7)"]
    [("PATH", "/usr/bin:/bin")] Nothing 1024
  assert "nonzero child exits remain explicit results for caller policy"
    (case nonzero of Right (ExitFailure 7, _, _) -> True; _ -> False)
    ExitSuccess "" (show nonzero)
  closeFd directoryFd
  where
    reprPython value = show value

-- | The subprocess checks, one case per check or row.
tests :: IO TestTree
tests = fmap (testGroup "subprocess") $ sequence
  [ treeOf subprocessCaptureLimitChecks
  , caseOf "subprocessOutputLimitChecks" (withProposalTemporaryRoot subprocessOutputLimitChecks)
  , caseOf "subprocessCaptureInterruptionChecks" (withProposalTemporaryRoot subprocessCaptureInterruptionChecks)
  ]
