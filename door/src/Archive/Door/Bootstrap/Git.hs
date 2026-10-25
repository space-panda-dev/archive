{-# LANGUAGE OverloadedStrings #-}

-- | Git and git-annex as bootstrap runs and checks them.
--
-- Bootstrap may run only the configured Git and git-annex (ADR 0033), with
-- the caller's GIT_ variables removed and the pinned Git directory named
-- explicitly. The runner is a parameter so tests can make it fail without
-- replacing either program on the host.
module Archive.Door.Bootstrap.Git
  ( BootstrapEnvironment (..)
  , defaultBootstrapEnvironment
  , bootstrapEnvironmentFor
  , commitBootstrap
  , noAutomaticMaintenance
  , runChecked
  , checkGit
  , verifyBootstrapStaged
  , bootstrapHeadIsUnborn
  , verifyCommittedBootstrapState
  ) where

import Archive.Door.Bootstrap.Abort (failIO, failMutation)
import Archive.Door.Bootstrap.Journal (BootstrapJournal (..), bootstrapJournalName)
import Archive.Door.Bootstrap.Pinned (verifyGitDirectoryStillPinned, verifyRootStillPinned)
import Archive.Door.Hex (hexText)
import Archive.Door.MutationError (MutationError (..))
import Archive.Executables
  ( Executable
  , executablePath
  , executableStillMatches
  , resolveAnnexExecutable
  , resolveGitExecutable
  )
import Archive.Process (processErrorMessage, runProcessBoundedAt)
import qualified Crypto.Hash.SHA256 as SHA256
import Control.Monad (unless, when)
import Data.List (sort)
import Data.Text (Text)
import qualified Data.Text as Text
import qualified Data.Text.Encoding as TextEncoding
import qualified Data.Text.Encoding.Error as TextError
import System.Environment (getEnvironment)
import System.Exit (ExitCode (..))
import System.FilePath ((</>))
import System.IO.Error (catchIOError, isDoesNotExistError)
import System.Posix.Files (getSymbolicLinkStatus)
import System.Posix.Types (Fd)

data BootstrapEnvironment = BootstrapEnvironment
  { runSubprocess :: FilePath -> [String] -> Fd -> IO (ExitCode, Text, Text)
  , runPinnedSubprocess :: Maybe (FilePath -> [String] -> Fd -> Fd -> IO (ExitCode, Text, Text))
  }

defaultBootstrapEnvironment :: BootstrapEnvironment
defaultBootstrapEnvironment = BootstrapEnvironment
  { runSubprocess = \program arguments rootFd -> do
      executableResult <- case program of
        "git" -> resolveGitExecutable
        "git-annex" -> resolveAnnexExecutable
        _ -> pure (Left "bootstrap may invoke only configured Git and git-annex")
      case executableResult of
        Left problem -> pure (ExitFailure 125, "", problem)
        Right executable -> runExecutableAt executable arguments rootFd []
  , runPinnedSubprocess = Just $ \program arguments _rootFd gitFd -> do
      executableResult <- case program of
        "git" -> resolveGitExecutable
        "git-annex" -> resolveAnnexExecutable
        _ -> pure (Left "bootstrap may invoke only configured Git and git-annex")
      case executableResult of
        Left problem -> pure (ExitFailure 125, "", problem)
        Right executable -> runExecutableAt executable (pinnedGitArguments program arguments) gitFd
          [("GIT_DIR", "."), ("GIT_WORK_TREE", "..")]
  }

bootstrapEnvironmentFor :: Executable -> Executable -> BootstrapEnvironment
bootstrapEnvironmentFor git annex = BootstrapEnvironment
  { runSubprocess = \program arguments rootFd -> case program of
      "git" -> runExecutableAt git arguments rootFd []
      "git-annex" -> runExecutableAt annex arguments rootFd []
      _ -> pure (ExitFailure 125, "", "bootstrap may invoke only configured Git and git-annex")
  , runPinnedSubprocess = Just $ \program arguments _rootFd gitFd -> case program of
      "git" -> runExecutableAt git (pinnedGitArguments program arguments) gitFd [("GIT_DIR", "."), ("GIT_WORK_TREE", "..")]
      "git-annex" -> runExecutableAt annex arguments gitFd [("GIT_DIR", "."), ("GIT_WORK_TREE", "..")]
      _ -> pure (ExitFailure 125, "", "bootstrap may invoke only configured Git and git-annex")
  }

pinnedGitArguments :: FilePath -> [String] -> [String]
pinnedGitArguments "git" arguments = ["--git-dir=.", "--work-tree=.."] <> arguments
pinnedGitArguments _ arguments = arguments

runExecutableAt :: Executable -> [String] -> Fd -> [(String, String)] -> IO (ExitCode, Text, Text)
runExecutableAt executable arguments workingDirectory extraEnvironment = do
  unchanged <- executableStillMatches executable
  if not unchanged
    then pure (ExitFailure 125, "", "configured executable changed during operation")
    else do
      inherited <- cleanBootstrapEnvironment <$> getEnvironment
      let childEnvironment = extraEnvironment <> filter ((`notElem` map fst extraEnvironment) . fst) inherited
      result <- runProcessBoundedAt workingDirectory (executablePath executable) arguments childEnvironment
        (Just (8 * 1024 * 1024)) (1024 * 1024)
      stillUnchanged <- executableStillMatches executable
      pure $ if not stillUnchanged
        then (ExitFailure 125, "", "configured executable changed during operation")
        else case result of
          Left problem -> (ExitFailure 125, "", processErrorMessage problem)
          Right (status, stdoutBytes, stderrBytes) ->
            ( status
            , TextEncoding.decodeUtf8With TextError.lenientDecode stdoutBytes
            , TextEncoding.decodeUtf8With TextError.lenientDecode stderrBytes
            )

cleanBootstrapEnvironment :: [(String, String)] -> [(String, String)]
cleanBootstrapEnvironment = filter (not . isGitVariable . fst)
  where
    isGitVariable name = "GIT_" `Text.isPrefixOf` Text.pack name

commitBootstrap :: BootstrapEnvironment -> FilePath -> Fd -> Fd -> IO ()
commitBootstrap environment rootPath rootFd gitFd = runChecked environment rootPath rootFd
  (Just gitFd) "git"
  (noAutomaticMaintenance <>
  [ "-c", "user.name=Archive"
  , "-c", "user.email=archive@localhost"
  , "-c", "core.hooksPath=/dev/null"
  , "commit", "--quiet", "-m", "Initialize archive"
  ])

-- | Git may otherwise start maintenance after a commit, detached in the
-- background, which repacks the object store outside the archive lock
-- (ADR 0034). Door's own commits already pass these settings.
noAutomaticMaintenance :: [String]
noAutomaticMaintenance = ["-c", "maintenance.auto=false", "-c", "gc.auto=0", "-c", "gc.autoPackLimit=0"]

verifyBootstrapStaged
  :: BootstrapEnvironment -> FilePath -> Fd -> Fd -> [FilePath] -> IO ()
verifyBootstrapStaged environment rootPath rootFd gitFd expectedPaths = do
  staged <- runCheckedOutput environment rootPath rootFd (Just gitFd) "git"
    ["ls-files", "--stage", "--full-name", "--", ":/"]
  let rows = filter (not . Text.null) (Text.lines staged)
      parsedRows = map parseStageRow rows
      actualPaths = sort [Text.unpack path | Right (_, path) <- parsedRows]
      regularFiles = all isRegularFileMode parsedRows
  unless regularFiles
    (failIO "bootstrap journal index contains a non-regular or conflicted entry")
  unless (actualPaths == expectedPaths)
    (failIO ("bootstrap journal index paths differ from its complete initialized archive: "
      <> Text.pack (show (actualPaths, expectedPaths))))
  (worktreeStatus, _, _) <- runBootstrapSubprocess environment "git"
    ["diff", "--quiet", "--", ":/"] rootFd (Just gitFd)
  verifyRootStillPinned rootPath rootFd
  verifyGitDirectoryStillPinned rootFd gitFd
  unless (worktreeStatus == ExitSuccess)
    (failIO "bootstrap journal worktree differs from its staged files")
  untracked <- runCheckedOutput environment rootPath rootFd (Just gitFd) "git"
    ["ls-files", "--others", "--exclude-standard", "--full-name", "--", ":/"]
  unless (Text.lines (Text.stripEnd untracked) == [Text.pack bootstrapJournalName])
    (failIO "bootstrap journal root contains untracked files requiring operator inspection")
  where
    parseStageRow row =
      let (metadata, pathWithTab) = Text.breakOn "\t" row
          path = Text.drop 1 pathWithTab
      in if Text.null pathWithTab || Text.null path
           then Left ()
           else Right (metadata, path)
    isRegularFileMode (Left _) = False
    isRegularFileMode (Right (metadata, _)) = case Text.words metadata of
      [mode, _object, "0"] -> mode == "100644" || mode == "100755"
      _ -> False

bootstrapHeadIsUnborn :: BootstrapEnvironment -> FilePath -> Fd -> Fd -> IO Bool
bootstrapHeadIsUnborn environment rootPath rootFd gitFd = do
  branch <- Text.strip <$> runCheckedOutput environment rootPath rootFd (Just gitFd)
    "git" ["symbolic-ref", "-q", "HEAD"]
  unless ("refs/heads/" `Text.isPrefixOf` branch)
    (failIO "bootstrap Git HEAD is not a symbolic branch")
  let branchName = Text.unpack branch
      lockPaths =
        [ rootPath </> ".git" </> "index.lock"
        , rootPath </> ".git" </> "packed-refs.lock"
        , rootPath </> ".git" </> branchName <> ".lock"
        ]
  lockExists <- or <$> mapM pathExistsStrict lockPaths
  when lockExists (failIO "bootstrap Git ref state has an outstanding lock")
  (refStatus, _, _) <- runBootstrapSubprocess environment "git"
    ["show-ref", "--verify", "--quiet", branchName] rootFd (Just gitFd)
  case refStatus of
    ExitFailure 1 -> pure True
    ExitSuccess -> pure False
    ExitFailure _ -> failIO "could not prove bootstrap Git ref state"
  where
    pathExistsStrict path = catchIOError
      (getSymbolicLinkStatus path >> pure True)
      (\problem -> if isDoesNotExistError problem then pure False else ioError problem)

verifyCommittedBootstrapState
  :: BootstrapEnvironment -> FilePath -> Fd -> Fd -> BootstrapJournal -> IO ()
verifyCommittedBootstrapState environment rootPath rootFd gitFd journal = do
  (indexStatus, _, _) <- runBootstrapSubprocess environment "git"
    ["diff", "--cached", "--quiet", "--", ":/"] rootFd (Just gitFd)
  verifyRootStillPinned rootPath rootFd
  verifyGitDirectoryStillPinned rootFd gitFd
  unless (indexStatus == ExitSuccess)
    (failIO "bootstrap branch ref advanced without committing the journaled index")
  count <- runCheckedOutput environment rootPath rootFd (Just gitFd) "git"
    ["rev-list", "--count", "HEAD^{commit}"]
  unless (Text.strip count == "1")
    (failIO "journaled bootstrap ref does not contain exactly one initialization commit")
  treeEntries <- runCheckedOutput environment rootPath rootFd (Just gitFd) "git"
    ["ls-tree", "-r", "--full-tree", "HEAD"]
  let parsedEntries = map parseTreeEntry (filter (not . Text.null) (Text.lines treeEntries))
      actualPaths = sort [path | Right (_, path) <- parsedEntries]
      regularBlobs = all isRegularBlob parsedEntries
      expectedFiles = bootstrapJournalFiles journal
      expectedPaths = sort (map (Text.pack . fst) expectedFiles)
  unless regularBlobs
    (failIO "journaled bootstrap commit tree contains a non-regular entry")
  unless (actualPaths == expectedPaths)
    (failIO "journaled bootstrap commit tree contains paths outside the ownership journal")
  actualDigests <- mapM (committedFileDigest environment rootPath rootFd gitFd) expectedFiles
  unless (actualDigests == map snd expectedFiles)
    (failIO "journaled bootstrap commit tree differs from the ownership journal")
  where
    parseTreeEntry line =
      let (metadata, pathWithTab) = Text.breakOn "\t" line
          path = Text.drop 1 pathWithTab
      in case Text.words metadata of
        [mode, "blob", _object]
          | not (Text.null pathWithTab) && not (Text.null path) -> Right (mode, path)
        _ -> Left ()
    isRegularBlob (Right (mode, _)) = mode == "100644" || mode == "100755"
    isRegularBlob (Left ()) = False

committedFileDigest
  :: BootstrapEnvironment -> FilePath -> Fd -> Fd -> (FilePath, Text) -> IO Text
committedFileDigest environment rootPath rootFd gitFd (path, _expectedDigest) = do
  contents <- runCheckedOutput environment rootPath rootFd (Just gitFd) "git"
    ["show", "HEAD:" <> path]
  pure (hexText (SHA256.hash (TextEncoding.encodeUtf8 contents)))

checkGit :: BootstrapEnvironment -> FilePath -> Fd -> Fd -> IO ()
checkGit environment rootPath rootFd gitFd = do
  status <- runCheckedOutput environment rootPath rootFd (Just gitFd) "git" ["status", "--porcelain", "--untracked-files=all"]
  if not (Text.null status) then failIO "initialized Archive has uncommitted changes" else pure ()
  count <- runCheckedOutput environment rootPath rootFd (Just gitFd) "git"
    ["rev-list", "--count", "HEAD^{commit}"]
  if Text.strip count /= "1" then failIO "initialized Archive does not have one archival commit" else pure ()
  _ <- runCheckedOutput environment rootPath rootFd (Just gitFd) "git-annex" ["info", "--json"]
  pure ()

runChecked :: BootstrapEnvironment -> FilePath -> Fd -> Maybe Fd -> FilePath -> [String] -> IO ()
runChecked environment rootPath rootFd gitFd program arguments = do
  (status, _stdoutText, stderrText) <- runBootstrapSubprocess environment program arguments rootFd gitFd
  verifyRootStillPinned rootPath rootFd
  maybe (pure ()) (verifyGitDirectoryStillPinned rootFd) gitFd
  case status of
    ExitSuccess -> pure ()
    ExitFailure code -> failMutation (MutationSubprocessFailure
      (Text.pack program <> " failed (" <> Text.pack (show code) <> "): " <> stderrText))

runCheckedOutput :: BootstrapEnvironment -> FilePath -> Fd -> Maybe Fd -> FilePath -> [String] -> IO Text
runCheckedOutput environment rootPath rootFd gitFd program arguments = do
  (status, stdoutText, stderrText) <- runBootstrapSubprocess environment program arguments rootFd gitFd
  verifyRootStillPinned rootPath rootFd
  maybe (pure ()) (verifyGitDirectoryStillPinned rootFd) gitFd
  case status of
    ExitSuccess -> pure stdoutText
    ExitFailure code -> failMutation (MutationSubprocessFailure
      (Text.pack program <> " failed (" <> Text.pack (show code) <> "): " <> stderrText))

runBootstrapSubprocess
  :: BootstrapEnvironment -> FilePath -> [String] -> Fd -> Maybe Fd
  -> IO (ExitCode, Text, Text)
runBootstrapSubprocess environment program arguments rootFd gitFd = case gitFd of
  Just pinnedGit -> case runPinnedSubprocess environment of
    Just runner -> runner program arguments rootFd pinnedGit
    Nothing -> runSubprocess environment program arguments rootFd
  Nothing -> runSubprocess environment program arguments rootFd
