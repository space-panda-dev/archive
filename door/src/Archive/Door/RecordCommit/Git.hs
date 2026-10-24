{-# LANGUAGE OverloadedStrings #-}

-- | How Door's writer runs Git.
--
-- Every child runs at the pinned @.git@ descriptor with a cleaned
-- environment and explicit durability settings, and the executable and both
-- directory pins are checked on each side of the call, so a replaced Git,
-- root or @.git@ is reported rather than trusted. Blob hashing reads the
-- writer's bytes through inherited descriptors instead of pathnames.
module Archive.Door.RecordCommit.Git
  ( GitFailure (..)
  , blobMatches
  , gitBlobForBytes
  , gitBlobForFd
  , gitBlobForFileBytes
  , gitBlobsForFds
  , gitDirectoryChangedMessage
  , gitDirectoryMatches
  , gitFailureMessage
  , gitPinChanged
  , rootDirectoryChangedMessage
  , rootDirectoryMatches
  , runGit
  , runGitBytesTyped
  , runGitStatus
  , runGitTyped
  ) where

import Archive.Door.Hex (hexText)
import Archive.Door.RecordCommit.Descriptors (ownerFileModes, showFd, unlinkAt, writeAll)
import Archive.Door.RecordCommit.Failure (tryIO)
import Archive.Executables (Executable, executablePath, executableStillMatches)
import Archive.Process (ProcessError, processErrorMessage, runProcessBoundedAt)
import qualified Control.Exception as Exception
import qualified Crypto.Hash.SHA1 as SHA1
import qualified Crypto.Hash.SHA256 as SHA256
import qualified Data.ByteString as ByteString
import Data.Text (Text)
import qualified Data.Text as Text
import qualified Data.Text.Encoding as TextEncoding
import qualified Data.Text.Encoding.Error as TextError
import Data.Unique (hashUnique, newUnique)
import System.Environment (getEnvironment)
import System.Exit (ExitCode (..))
import System.Posix.Files (deviceID, fileID, getFdStatus)
import System.Posix.IO
  ( FdOption (CloseOnExec)
  , OpenFileFlags (cloexec, creat, directory, exclusive, nofollow, nonBlock)
  , OpenMode (ReadOnly, WriteOnly)
  , closeFd
  , createPipe
  , defaultFileFlags
  , openFd
  , openFdAt
  , queryFdOption
  , setFdOption
  )
import System.Posix.Process (getProcessID)
import System.Posix.Types (Fd)

-- | Source-typed failures from the Git execution seam. The rest of
-- RecordCommit still has Text-returning helpers; keep this type local to the
-- command boundary until those helpers can be migrated without losing their
-- existing diagnostics.
data GitFailure
  = GitProcessFailure ProcessError
  | GitExitFailure Int Text
  | GitExecutableChanged
  | GitRootChanged
  | GitDirectoryChanged
  deriving (Eq, Show)

gitFailureMessage :: GitFailure -> Text
gitFailureMessage failure = case failure of
  GitProcessFailure problem -> processErrorMessage problem
  GitExitFailure code detail -> "git exited " <> Text.pack (show code) <> ": " <> detail
  GitExecutableChanged -> "configured Git executable changed during operation"
  GitRootChanged -> rootDirectoryChangedMessage
  GitDirectoryChanged -> gitDirectoryChangedMessage

-- | Hash exact bytes as a loose Git blob without a temporary file: Git reads
-- them from an inherited pipe descriptor, as 'gitBlobForFd' reads records.
gitBlobForBytes :: Executable -> FilePath -> Fd -> Fd -> ByteString.ByteString
                -> IO (Either Text Text)
gitBlobForBytes executable rootPath rootFd gitFd bytes = do
  opened <- tryIO createPipe
  case opened of
    Left problem -> pure (Left problem)
    Right (readFd, writeFd) -> Exception.bracket (pure readFd) closeFd $ \_ -> do
      written <- tryIO (writeAll writeFd bytes `Exception.finally` closeFd writeFd)
      case written of
        Left problem -> pure (Left problem)
        Right () -> do
          setFdOption readFd CloseOnExec False
          fmap Text.strip <$> runGit executable rootPath rootFd gitFd Nothing
            ["hash-object", "-w", "--no-filters", "--", "/dev/fd/" <> showFd readFd]

-- | Whether bytes hash, as a Git blob, to a name, in the object format the
-- name's length selects.
blobMatches :: Text -> ByteString.ByteString -> Bool
blobMatches name bytes = case Text.length name of
  40 -> hexText (SHA1.hash framed) == name
  64 -> hexText (SHA256.hash framed) == name
  _ -> False
  where
    framed = "blob " <> TextEncoding.encodeUtf8 (Text.pack (show (ByteString.length bytes))) <> "\0" <> bytes

-- | Hash bytes of any size as a loose Git blob. A pipe holds only a bounded
-- amount before its reader starts, and Git starts after the write, so the
-- bytes go to an unnamed file in the Git directory instead: written, opened
-- again for reading from its start, and unlinked before Git reads it.
gitBlobForFileBytes :: Executable -> FilePath -> Fd -> Fd -> ByteString.ByteString
                    -> IO (Either Text Text)
gitBlobForFileBytes executable rootPath rootFd gitFd bytes = do
  unique <- hashUnique <$> newUnique
  processId <- getProcessID
  let name = ".archive-door-blob-" <> show processId <> "-" <> show unique
      create = openFdAt (Just gitFd) name WriteOnly
        defaultFileFlags { creat = Just ownerFileModes, exclusive = True, nofollow = True, cloexec = True }
      reopen = openFdAt (Just gitFd) name ReadOnly
        defaultFileFlags { nofollow = True, nonBlock = True, cloexec = False }
      removeName = Exception.try (unlinkAt gitFd name) :: IO (Either Exception.IOException ())
  prepared <- tryIO (Exception.bracket create closeFd (`writeAll` bytes)
    >> (reopen `Exception.finally` removeName))
  case prepared of
    Left problem -> do
      _ <- removeName
      pure (Left problem)
    Right fd -> Exception.bracket (pure fd) closeFd $ \readFd ->
      runGit executable rootPath rootFd gitFd Nothing
        ["hash-object", "-w", "--no-filters", "--", "/dev/fd/" <> showFd readFd]
        >>= pure . fmap Text.strip

gitBlobForFd :: Executable -> FilePath -> Fd -> Fd -> Fd -> IO (Either Text Text)
gitBlobForFd executable rootPath rootFd gitFd fd = do
  closesOnExec <- queryFdOption fd CloseOnExec
  if closesOnExec
    then pure (Left "record descriptor is marked close-on-exec")
    else runGit executable rootPath rootFd gitFd Nothing
      ["hash-object", "--no-filters", "--", "/dev/fd/" <> showFd fd]


-- | 'gitBlobForFd' for several open descriptors in one Git process. Git
-- prints one object name per input, in order; any other count is a refusal.
gitBlobsForFds :: Executable -> FilePath -> Fd -> Fd -> [Fd] -> IO (Either Text [Text])
gitBlobsForFds _ _ _ _ [] = pure (Right [])
gitBlobsForFds executable rootPath rootFd gitFd fds = do
  closesOnExec <- mapM (`queryFdOption` CloseOnExec) fds
  if or closesOnExec
    then pure (Left "record descriptor is marked close-on-exec")
    else do
      output <- runGit executable rootPath rootFd gitFd Nothing
        (["hash-object", "--no-filters", "--"] <> map (("/dev/fd/" <>) . showFd) fds)
      pure $ do
        blobs <- filter (not . Text.null) . map Text.strip . Text.lines <$> output
        if length blobs == length fds
          then Right blobs
          else Left "Git did not hash every record descriptor"

runGit :: Executable -> FilePath -> Fd -> Fd -> Maybe FilePath -> [String] -> IO (Either Text Text)
runGit executable rootPath rootFd gitFd indexPath arguments =
  either (Left . gitFailureMessage) Right
    <$> runGitTyped executable rootPath rootFd gitFd indexPath arguments

runGitTyped :: Executable -> FilePath -> Fd -> Fd -> Maybe FilePath -> [String]
            -> IO (Either GitFailure Text)
runGitTyped executable rootPath rootFd gitFd indexPath arguments =
  fmap (TextEncoding.decodeUtf8With TextError.lenientDecode)
    <$> runGitBytesTyped executable rootPath rootFd gitFd indexPath arguments

-- | 'runGitTyped' with Git's standard output as exact bytes.
runGitBytesTyped :: Executable -> FilePath -> Fd -> Fd -> Maybe FilePath -> [String]
                 -> IO (Either GitFailure ByteString.ByteString)
runGitBytesTyped executable rootPath rootFd gitFd indexPath arguments = do
  environment <- cleanEnvironment indexPath
  unchanged <- executableStillMatches executable
  rootPinned <- rootDirectoryMatches rootPath rootFd
  pinned <- gitDirectoryMatches rootFd gitFd
  result <- if not unchanged
    then pure (Left GitExecutableChanged)
    else if not rootPinned
      then pure (Left GitRootChanged)
    else if not pinned
      then pure (Left GitDirectoryChanged)
      else fmap (either (Left . GitProcessFailure) Right) $
        runProcessBoundedAt gitFd (executablePath executable)
          ("--git-dir=." : gitDurabilityConfig <> arguments) environment
          (Just (8 * 1024 * 1024)) (1024 * 1024)
  stillUnchanged <- executableStillMatches executable
  stillRootPinned <- rootDirectoryMatches rootPath rootFd
  stillPinned <- gitDirectoryMatches rootFd gitFd
  pure $ do
    if stillUnchanged then Right ()
      else Left GitExecutableChanged
    if stillRootPinned then Right ()
      else Left GitRootChanged
    if stillPinned then Right ()
      else Left GitDirectoryChanged
    (status, stdoutBytes, stderrBytes) <- result
    case status of
      ExitSuccess -> Right stdoutBytes
      ExitFailure code -> Left
        (GitExitFailure code
          (TextEncoding.decodeUtf8With TextError.lenientDecode stderrBytes))

gitDurabilityConfig :: [String]
-- Explicit per-child settings override ambient defaults. The descriptor
-- barriers below cover the batch's loose files and namespace changes only;
-- existing packed history still needs its own established durability baseline.
gitDurabilityConfig =
  [ "-c", "core.fsync=all"
  , "-c", "core.fsyncMethod=batch"
  , "-c", "core.fsmonitor=false"
  , "-c", "core.commitGraph=false"
  , "-c", "core.multiPackIndex=false"
  , "-c", "pack.useBitmaps=false"
  , "-c", "maintenance.auto=false"
  , "-c", "gc.auto=0"
  , "-c", "gc.autoPackLimit=0"
  ]

runGitStatus :: Executable -> FilePath -> Fd -> Fd -> Maybe FilePath -> [String] -> IO (Either Text ExitCode)
runGitStatus executable rootPath rootFd gitFd indexPath arguments =
  either (Left . gitFailureMessage) Right
    <$> runGitStatusTyped executable rootPath rootFd gitFd indexPath arguments

runGitStatusTyped :: Executable -> FilePath -> Fd -> Fd -> Maybe FilePath -> [String]
                  -> IO (Either GitFailure ExitCode)
runGitStatusTyped executable rootPath rootFd gitFd indexPath arguments = do
  environment <- cleanEnvironment indexPath
  unchanged <- executableStillMatches executable
  rootPinned <- rootDirectoryMatches rootPath rootFd
  pinned <- gitDirectoryMatches rootFd gitFd
  result <- if not unchanged
    then pure (Left GitExecutableChanged)
    else if not pinned
      then pure (Left GitDirectoryChanged)
    else if not rootPinned
      then pure (Left GitRootChanged)
      else fmap (either (Left . GitProcessFailure) Right) $
        runProcessBoundedAt gitFd (executablePath executable)
          ("--git-dir=." : gitDurabilityConfig <> arguments) environment
          (Just (8 * 1024 * 1024)) (1024 * 1024)
  stillUnchanged <- executableStillMatches executable
  stillRootPinned <- rootDirectoryMatches rootPath rootFd
  stillPinned <- gitDirectoryMatches rootFd gitFd
  if not stillUnchanged
    then pure (Left GitExecutableChanged)
    else if not stillPinned
      then pure (Left GitDirectoryChanged)
    else if not stillRootPinned
      then pure (Left GitRootChanged)
    else pure (fmap (\(status, _, _) -> status) result)

cleanEnvironment :: Maybe FilePath -> IO [(String, String)]
cleanEnvironment indexPath = do
  environment <- getEnvironment
  let clean = filter (not . isGitVariable . fst) environment
      durableGitEnvironment =
        ("GIT_CONFIG_NOSYSTEM", "1")
          : ("GIT_CONFIG_GLOBAL", "/dev/null")
          : ("GIT_NO_REPLACE_OBJECTS", "1")
          : ("GIT_NO_LAZY_FETCH", "1")
          : clean
  pure $ maybe durableGitEnvironment (\path -> ("GIT_INDEX_FILE", path) : durableGitEnvironment) indexPath
  where
    isGitVariable name = "GIT_" `Text.isPrefixOf` Text.pack name

gitDirectoryMatches :: Fd -> Fd -> IO Bool
gitDirectoryMatches rootFd gitFd = do
  current <- Exception.try (openFdAt (Just rootFd) ".git" ReadOnly
    defaultFileFlags { directory = True, nofollow = True, cloexec = True })
    :: IO (Either Exception.IOException Fd)
  case current of
    Left _ -> pure False
    Right currentFd -> Exception.bracket (pure currentFd) closeFd $ \fd -> do
      expected <- getFdStatus gitFd
      observed <- getFdStatus fd
      pure (deviceID expected == deviceID observed && fileID expected == fileID observed)

rootDirectoryMatches :: FilePath -> Fd -> IO Bool
rootDirectoryMatches rootPath rootFd = do
  current <- Exception.try (openFd rootPath ReadOnly
    defaultFileFlags { directory = True, nofollow = True, cloexec = True })
    :: IO (Either Exception.IOException Fd)
  case current of
    Left _ -> pure False
    Right currentFd -> Exception.bracket (pure currentFd) closeFd $ \fd -> do
      expected <- getFdStatus rootFd
      observed <- getFdStatus fd
      pure (deviceID expected == deviceID observed && fileID expected == fileID observed)

rootDirectoryChangedMessage :: Text
rootDirectoryChangedMessage =
  "archive root path changed during Git operation; outcome may be uncertain"

gitDirectoryChangedMessage :: Text
gitDirectoryChangedMessage =
  "archive .git directory changed during Git operation; outcome may be uncertain"

gitPinChanged :: GitFailure -> Bool
gitPinChanged failure = case failure of
  GitRootChanged -> True
  GitDirectoryChanged -> True
  _ -> False
