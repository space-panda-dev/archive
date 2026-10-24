{-# LANGUAGE OverloadedStrings #-}

-- | A concrete, read-only view of one immutable Git commit.
--
-- This is deliberately a Git seam, not a repository abstraction. Every
-- operation is tied to the commit resolved by 'resolveSnapshot'; it never
-- consults the worktree. Git's inherited GIT_* environment is removed so a
-- caller cannot redirect repository or object lookup through the environment.
module Archive.GitSnapshot
  ( GitSnapshot
  , snapshotRoot
  , snapshotCommit
  , snapshotExecutableIdentity
  , GitSnapshotError (..)
  , gitSnapshotErrorMessage
  , resolveSnapshot
  , readPath
  , listPaths
  , prefetchPaths
  , verifyObjects
  , addedPathsSince
  , uniqueIntroducer
  , linearHistory
  , immutableAdditions
  , archivePathHistory
  , readPathAt
  ) where

import Archive.Process
  ( ProcessError (..)
  , ProcessStream (..)
  , processErrorMessage
  , runProcessBoundedAt
  )
import Archive.Executables
  ( Executable
  , executableIdentityText
  , executablePath
  , executableStillMatches
  , resolveGitExecutable
  )
import Control.Concurrent.MVar (MVar, modifyMVar, newMVar, readMVar)
import Control.Exception (IOException, bracket, mask, onException, try)
import qualified Data.ByteString as StrictByteString
import qualified Data.ByteString.Lazy as LazyByteString
import Data.List (isPrefixOf, sort, unsnoc)
import Data.Map.Strict (Map)
import qualified Data.Map.Strict as Map
import Data.Text (Text)
import qualified Data.Text as Text
import qualified Data.Text.Encoding as Text
import qualified Data.Text.Read as Text
import qualified Data.Text.Encoding.Error as TextError
import System.Environment (getEnvironment)
import System.Directory (canonicalizePath)
import System.Exit (ExitCode (..))
import System.IO.Error (tryIOError)
import System.Posix.Files (deviceID, fileID, getFdStatus, isDirectory)
import System.Posix.IO (OpenFileFlags (cloexec, directory, nofollow, nonBlock), OpenMode (ReadOnly), closeFd, defaultFileFlags, openFd)
import System.Posix.Types (Fd)

data GitSnapshot = GitSnapshot
  { snapshotRoot :: FilePath
  , snapshotCommit :: Text
  , snapshotExecutable :: Executable
  , snapshotRootIdentity :: DirectoryIdentity
  , snapshotCache :: MVar SnapshotCache
  }

-- | Equality and display ignore the read cache: two values naming the same
-- commit, root, and Git executable are the same snapshot.
instance Eq GitSnapshot where
  left == right = (snapshotRoot left, snapshotCommit left, snapshotExecutable left, snapshotRootIdentity left)
    == (snapshotRoot right, snapshotCommit right, snapshotExecutable right, snapshotRootIdentity right)

instance Show GitSnapshot where
  showsPrec precedence snapshot = showParen (precedence > 10) $
    showString "GitSnapshot " . showsPrec 11 (snapshotRoot snapshot)
      . showString " " . showsPrec 11 (snapshotCommit snapshot)

-- | What has been read from the pinned commit. The commit is immutable, so
-- anything Git has returned for it stays true for the life of the snapshot.
-- The tree is listed once; file bytes are read in bulk by 'prefetchPaths' or
-- one at a time by 'readPath'.
data SnapshotCache = SnapshotCache
  { cachedTree :: Maybe (Map FilePath TreeEntry)
  , cachedBlobs :: Map Text StrictByteString.ByteString
  }

data TreeEntry = TreeEntry
  { entryType :: Text
  , entryObject :: Text
  , entrySize :: Maybe Int
  }

data DirectoryIdentity = DirectoryIdentity Integer Integer
  deriving (Eq, Show)

data GitSnapshotError
  = GitProcessFailure Text
  | GitProcessExecutionFailure ProcessError
  | GitMissingPath FilePath
  | GitInvalidPath FilePath Text
  | GitInvalidCommit Text
  | GitIntroducerError FilePath Text
  deriving (Eq, Show)

-- | Keep the established command-line wording while retaining the structured
-- subprocess error for callers that need to classify it.
gitSnapshotErrorMessage :: GitSnapshotError -> Text
gitSnapshotErrorMessage problem = "Git snapshot error: " <> case problem of
  GitProcessExecutionFailure failure -> Text.pack (show (GitProcessFailure
    (case failure of
      ProcessOutputLimitFailure StandardOutput _ -> "git stdout exceeded the control-plane output limit"
      ProcessOutputLimitFailure StandardError _ -> "git stderr exceeded the diagnostic output limit"
      _ -> processErrorMessage failure)))
  other -> Text.pack (show other)

resolveSnapshot :: FilePath -> IO (Either GitSnapshotError GitSnapshot)
resolveSnapshot root = mask $ \restore -> do
  executableResult <- resolveGitExecutable
  case executableResult of
    Left problem -> pure (Left (GitProcessFailure problem))
    Right executable -> do
      openedResult <- openArchiveRoot root
      case openedResult of
        Left issue -> pure (Left issue)
        Right (actualRoot, identity, directoryFd) -> bracket (pure directoryFd) closeFd
          (const (restore (resolveWith executable actualRoot identity)))
  where
    resolveWith executable actualRoot identity = do
      topLevelResult <- runGit executable actualRoot identity ["rev-parse", "--show-toplevel"]
      case topLevelResult >>= oneLine "Git returned an invalid repository root" of
        Left issue -> pure (Left issue)
        Right topLevel -> do
          topResult <- canonicalPath (Text.unpack topLevel)
          case topResult of
            Left issue -> pure (Left issue)
            Right repositoryRoot
              | actualRoot /= repositoryRoot ->
                  pure (Left (GitProcessFailure "command must run from the archive root"))
              | otherwise -> do
                  result <- runGit executable actualRoot identity ["rev-parse", "--verify", "HEAD^{commit}"]
                  let commitResult = case result of
                        Left issue -> Left issue
                        Right bytes -> case strictText bytes of
                          Left issue -> Left issue
                          Right output -> case lines (Text.unpack output) of
                            [commit]
                              | validCommit commit -> Right (Text.pack commit)
                            _ -> Left (GitInvalidCommit "git returned an invalid HEAD commit identifier")
                  case commitResult of
                    Left issue -> pure (Left issue)
                    Right commit -> do
                      cache <- newMVar (SnapshotCache Nothing Map.empty)
                      pure (Right GitSnapshot
                        { snapshotRoot = actualRoot
                        , snapshotCommit = commit
                        , snapshotExecutable = executable
                        , snapshotRootIdentity = identity
                        , snapshotCache = cache
                        })

snapshotExecutableIdentity :: GitSnapshot -> Text
snapshotExecutableIdentity = executableIdentityText . snapshotExecutable

oneLine :: Text -> LazyByteString.ByteString -> Either GitSnapshotError Text
oneLine problem bytes = do
  output <- strictText bytes
  case lines (Text.unpack output) of
    [value] | not (null value) -> Right (Text.pack value)
    _ -> Left (GitProcessFailure problem)

canonicalPath :: FilePath -> IO (Either GitSnapshotError FilePath)
canonicalPath path = do
  result <- try (canonicalizePath path) :: IO (Either IOException FilePath)
  pure $ case result of
    Left problem -> Left (GitProcessFailure (Text.pack (show problem)))
    Right value -> Right value

-- | Read one file from the pinned commit. Bytes already read by
-- 'prefetchPaths' are returned without running Git again.
readPath :: GitSnapshot -> FilePath -> IO (Either GitSnapshotError LazyByteString.ByteString)
readPath snapshot path = do
    -- Every listed path was validated when the tree was parsed, so only a
    -- path the tree does not hold needs validating, to report it precisely.
    treeResult <- snapshotTree snapshot
    case treeResult of
      Left issue -> pure (Left issue)
      Right tree -> case Map.lookup path tree of
        Nothing -> pure (Left (either id (const (GitMissingPath path)) (validatePath path)))
        Just entry
          | entryType entry /= "blob" -> pure (Left (GitMissingPath path))
          | otherwise -> do
              loaded <- loadBlobs snapshot [entry]
              pure $ do
                blobs <- loaded
                case Map.lookup (entryObject entry) blobs of
                  Just bytes -> Right (LazyByteString.fromStrict bytes)
                  Nothing -> Left (GitProcessFailure "git did not return a requested object")

-- | List the files at or beneath a path in the pinned commit, sorted. The
-- whole tree is listed once per snapshot. An empty result is a missing path,
-- never an empty directory.
listPaths :: GitSnapshot -> FilePath -> IO (Either GitSnapshotError [FilePath])
listPaths snapshot prefix = case validatePath prefix of
  Left issue -> pure (Left issue)
  Right () -> do
    treeResult <- snapshotTree snapshot
    pure $ do
      tree <- treeResult
      case filter (underPrefix prefix) (Map.keys tree) of
        [] -> Left (GitMissingPath prefix)
        names -> Right (sort names)

-- | Read every file at or beneath the given paths with a bounded number of Git
-- processes, so later 'readPath' calls for them are answered from memory.
-- A path with nothing beneath it is not an error here; the caller's own
-- 'listPaths' or 'readPath' reports it.
prefetchPaths :: GitSnapshot -> [FilePath] -> IO (Either GitSnapshotError ())
prefetchPaths snapshot prefixes = case mapM_ validatePath prefixes of
  Left issue -> pure (Left issue)
  Right () -> do
    treeResult <- snapshotTree snapshot
    case treeResult of
      Left issue -> pure (Left issue)
      Right tree -> do
        let wanted = [ entry
                     | (path, entry) <- Map.toList tree
                     , any (`underPrefix` path) prefixes
                     , entryType entry == "blob"
                     ]
        fmap (const ()) <$> loadBlobs snapshot wanted

-- | Check every object reachable from the pinned commit, and every object in
-- the store, with Git's strictest check. Door's checkpoint (ADR 0052) stops
-- repeating this on every admission, so it is routine verification here.
verifyObjects :: GitSnapshot -> IO (Either GitSnapshotError ())
verifyObjects snapshot = fmap (const ()) <$> runGitBytes snapshot
  [ "fsck", "--full", "--strict", "--no-reflogs", "--no-dangling", "--no-progress"
  , Text.unpack (snapshotCommit snapshot)
  ]

-- | The paths under a prefix that the pinned commit added after an earlier
-- commit, read from one tree comparison rather than from all of history.
-- 'Nothing' when the earlier commit is not an ancestor. Any change other than
-- an addition under the prefix is refused: archive paths are immutable.
addedPathsSince :: GitSnapshot -> Text -> FilePath -> IO (Either GitSnapshotError (Maybe [FilePath]))
addedPathsSince snapshot earlier prefix = case validatePath prefix of
  Left issue -> pure (Left issue)
  Right ()
    | not (validCommit (Text.unpack earlier)) -> pure (Left (GitInvalidCommit "earlier commit is malformed"))
    | otherwise -> do
        ancestry <- runGitBytes snapshot
          ["merge-base", "--is-ancestor", Text.unpack earlier, Text.unpack (snapshotCommit snapshot)]
        case ancestry of
          Left (GitProcessFailure detail)
            | "git exited 1:" `Text.isPrefixOf` detail
                || "Not a valid commit name" `Text.isInfixOf` detail -> pure (Right Nothing)
          Left issue -> pure (Left issue)
          Right _ -> do
            changes <- runGitBytes snapshot
              [ "diff-tree", "-r", "-z", "--no-renames", "--name-status"
              , Text.unpack earlier, Text.unpack (snapshotCommit snapshot), "--", prefix ]
            pure $ do
              bytes <- changes
              tokens <- mapM decode (filter (not . StrictByteString.null)
                (StrictByteString.split 0 (LazyByteString.toStrict bytes)))
              Just <$> additions tokens
  where
    decode raw = either (const (Left (GitProcessFailure "git path output is not valid UTF-8")))
      Right (Text.decodeUtf8' raw)
    additions (status : path : rest)
      | status == "A" = do
          validatePath (Text.unpack path)
          (Text.unpack path :) <$> additions rest
      | otherwise = Left (GitProcessFailure
          ("immutable archive path has Git status " <> status <> ": " <> path))
    additions [] = Right []
    additions _ = Left (GitProcessFailure "Git returned malformed path changes")

underPrefix :: FilePath -> FilePath -> Bool
underPrefix prefix path = path == prefix || (prefix <> "/") `isPrefixOf` path

-- | The pinned commit's full tree, listed by one Git process and kept.
snapshotTree :: GitSnapshot -> IO (Either GitSnapshotError (Map FilePath TreeEntry))
snapshotTree snapshot = do
  cache <- readMVar (snapshotCache snapshot)
  case cachedTree cache of
    Just tree -> pure (Right tree)
    Nothing -> do
      result <- runGitBytes snapshot
        ["ls-tree", "-z", "-r", "-l", "--full-tree", Text.unpack (snapshotCommit snapshot)]
      case result >>= parseTreeListing of
        Left issue -> pure (Left issue)
        Right tree -> modifyMVar (snapshotCache snapshot) $ \current ->
          pure (current { cachedTree = Just tree }, Right tree)

-- | Parse @git ls-tree -z -r -l@ output: @mode type object size\tpath@ per
-- entry. Sizes frame the bulk object read, so a malformed size is a refusal.
parseTreeListing :: LazyByteString.ByteString -> Either GitSnapshotError (Map FilePath TreeEntry)
parseTreeListing bytes = do
  entries <- mapM parseEntry (filter (not . StrictByteString.null)
    (StrictByteString.split 0 (LazyByteString.toStrict bytes)))
  pure (Map.fromList entries)
  where
    parseEntry raw = do
      let (header, rest) = StrictByteString.break (== 9) raw
          pathBytes = StrictByteString.drop 1 rest
      path <- either (const (Left (GitProcessFailure "git tree output is not valid UTF-8")))
        (Right . Text.unpack) (Text.decodeUtf8' pathBytes)
      case (map Text.decodeLatin1 (StrictByteString.split 32 (squeeze header)), StrictByteString.null rest) of
        ([_mode, kind, object, size], False) | validCommitText object -> do
          validatePath path
          parsedSize <- case (kind, Text.decimal size) of
            ("blob", Right (value, remaining)) | Text.null remaining -> Right (Just value)
            ("blob", _) -> Left (GitProcessFailure "git returned an invalid blob size")
            _ -> Right Nothing
          Right (path, TreeEntry kind object parsedSize)
        _ -> Left (GitProcessFailure "git returned a malformed tree entry")
    -- @ls-tree -l@ pads the size column with spaces.
    squeeze = StrictByteString.intercalate " " . filter (not . StrictByteString.null) . StrictByteString.split 32
    validCommitText value = validCommit (Text.unpack value)

-- | Return the bytes of the given blobs, reading the ones not yet cached.
-- Objects are passed to @git show@ in groups, and the concatenated output is
-- split by the sizes Git listed in the tree. Any disagreement between those
-- sizes and the bytes returned is a refusal, so a short or long read cannot
-- shift one file's bytes into another.
loadBlobs :: GitSnapshot -> [TreeEntry] -> IO (Either GitSnapshotError (Map Text StrictByteString.ByteString))
loadBlobs snapshot entries = do
  cache <- readMVar (snapshotCache snapshot)
  let missing = Map.toList (Map.fromList
        [ (entryObject entry, size)
        | entry <- entries
        , not (Map.member (entryObject entry) (cachedBlobs cache))
        , Just size <- [entrySize entry]
        ])
  fetched <- readGroups (chunksOf objectsPerProcess missing) Map.empty
  case fetched of
    Left issue -> pure (Left issue)
    Right new -> modifyMVar (snapshotCache snapshot) $ \current -> do
      let blobs = Map.union (cachedBlobs current) new
      pure (current { cachedBlobs = blobs }, Right blobs)
  where
    objectsPerProcess = 512
    readGroups [] acc = pure (Right acc)
    readGroups (group : rest) acc = do
      result <- runGitBytes snapshot
        ("show" : "--no-textconv" : map (Text.unpack . fst) group)
      case result >>= splitObjects group . LazyByteString.toStrict of
        Left issue -> pure (Left issue)
        Right blobs -> readGroups rest (Map.union acc blobs)

    splitObjects group output
      | StrictByteString.length output /= sum (map snd group) = Left (GitProcessFailure
          "git returned object bytes that disagree with the listed sizes")
      | otherwise = Right (go group output Map.empty)
    go [] _ acc = acc
    go ((object, size) : rest) remaining acc =
      let (bytes, after) = StrictByteString.splitAt size remaining
       in go rest after (Map.insert object bytes acc)

    chunksOf _ [] = []
    chunksOf size values = let (front, back) = splitAt size values in front : chunksOf size back

uniqueIntroducer :: GitSnapshot -> FilePath -> IO (Either GitSnapshotError Text)
uniqueIntroducer snapshot path = do
  case validatePath path of
    Left issue -> pure (Left issue)
    Right () -> do
      result <- runGitBytes snapshot
        [ "log", "--diff-filter=A", "--format=%H"
        , Text.unpack (snapshotCommit snapshot), "--", path
        ]
      pure $ case result >>= strictText of
        Left issue -> Left issue
        Right output -> case filter (not . null) (lines (Text.unpack output)) of
          [commit] | validCommit commit -> Right (Text.pack commit)
          [] -> Left (GitIntroducerError path "no commit uniquely introduced this path")
          _ -> Left (GitIntroducerError path "multiple commits introduced this path")

-- | Return the pinned commit's history from its root to the pinned commit.
--
-- Git's reverse traversal is only a useful history for this seam when every
-- commit has exactly one predecessor (except the root), and each predecessor
-- is the commit immediately before it in the output.  Checking that shape
-- here prevents callers from mistaking a traversal through a merge for a
-- linear history.
linearHistory :: GitSnapshot -> IO (Either GitSnapshotError [Text])
linearHistory snapshot = do
  shallow <- runGitBytes snapshot ["rev-parse", "--is-shallow-repository"]
  case shallow >>= oneLine "Git returned an invalid shallow-repository flag" of
    Left problem -> pure (Left problem)
    Right "true" -> pure (Left (GitProcessFailure
      "Git history is shallow and cannot prove the archive root"))
    Right "false" -> do
      result <- runGitBytes snapshot
        [ "rev-list", "--parents", "--reverse"
        , Text.unpack (snapshotCommit snapshot)
        ]
      pure $ do
        bytes <- result
        output <- strictText bytes
        parseLinearHistory (snapshotCommit snapshot) output
    Right _ -> pure (Left (GitProcessFailure
      "Git returned an invalid shallow-repository flag"))

-- | Audit immutable archive paths in one history traversal.
--
-- Every selected path must be added exactly once.  A modification, deletion,
-- rename, copy, or re-addition is a refusal; callers therefore cannot mistake
-- the current tree for an immutable history.
immutableAdditions :: GitSnapshot -> [FilePath]
                   -> IO (Either GitSnapshotError (Map FilePath Text))
immutableAdditions snapshot prefixes =
  fmap fst <$> archivePathHistory snapshot prefixes []

-- | 'immutableAdditions', except that the named paths may also be modified
-- in place after they were added. The second result lists each such
-- modification, as the path and the commit, in history order. Only the
-- archive manifest is ever named here: a specification upgrade records the
-- new version where the archive records its version (ADR 0068), and the
-- caller proves that every such commit is one.
archivePathHistory :: GitSnapshot -> [FilePath] -> [FilePath]
                   -> IO (Either GitSnapshotError (Map FilePath Text, [(FilePath, Text)]))
archivePathHistory snapshot prefixes mutable = do
  result <- runGitBytes snapshot
    ([ "log", "--reverse", "--format=%x1e%H%x00", "-z", "--name-status", "-z"
     , Text.unpack (snapshotCommit snapshot), "--"
     ] <> prefixes)
  pure $ do
    bytes <- result
    parseArchiveChanges mutable bytes

parseArchiveChanges :: [FilePath] -> LazyByteString.ByteString
                    -> Either GitSnapshotError (Map FilePath Text, [(FilePath, Text)])
parseArchiveChanges mutable bytes = do
  tokens <- mapM decodeToken
    (filter (not . StrictByteString.null)
      (StrictByteString.split 0 (LazyByteString.toStrict bytes)))
  changes <- parseCommits tokens
  additions <- foldl insertUnique (Right Map.empty) [(path, commit) | (True, path, commit) <- changes]
  let modifications = [(path, commit) | (False, path, commit) <- changes]
  -- History is listed oldest first, so a path is added before any change.
  mapM_ (\(path, commit) -> case Map.lookup path additions of
      Just added | added /= commit -> Right ()
      _ -> Left (GitProcessFailure
        ("archive path was modified without an earlier addition: " <> Text.pack path)))
    modifications
  pure (additions, modifications)
  where
    decodeToken raw = case Text.decodeUtf8' raw of
      Left _ -> Left (GitProcessFailure "Git history output is not valid UTF-8")
      Right value -> Right (Text.dropWhile (`elem` ['\n', '\r']) value)

    parseCommits [] = Right []
    parseCommits (marker : rest) = do
      commit <- case Text.stripPrefix "\x1e" marker of
        Just value | validCommitText value -> Right value
        _ -> Left (GitProcessFailure "Git returned an invalid history marker")
      let (changes, remaining) = break (Text.isPrefixOf "\x1e") rest
      parsed <- parseChanges commit changes
      (parsed <>) <$> parseCommits remaining

    -- True marks an addition, False an in-place modification of a path the
    -- caller allows to change.
    parseChanges _ [] = Right []
    parseChanges commit (status : path : rest)
      | status == "A" = do
          case validatePath (Text.unpack path) of
            Left problem -> Left problem
            Right () -> ((True, Text.unpack path, commit) :) <$> parseChanges commit rest
      | status == "M" && Text.unpack path `elem` mutable =
          ((False, Text.unpack path, commit) :) <$> parseChanges commit rest
      | otherwise = Left (GitProcessFailure
          ("immutable archive path has Git status " <> status <> ": " <> path))
    parseChanges _ _ = Left (GitProcessFailure "Git returned malformed path history")

    insertUnique (Left problem) _ = Left problem
    insertUnique (Right paths) (path, commit) = case Map.lookup path paths of
      Nothing -> Right (Map.insert path commit paths)
      Just _ -> Left (GitProcessFailure
        ("immutable archive path was introduced more than once: " <> Text.pack path))

    validCommitText value = validCommit (Text.unpack value)

-- | Read one file as a commit of the pinned history holds it. The commit is
-- named by its full identifier and must be the pinned commit or one of its
-- ancestors. The bytes are not cached.
readPathAt :: GitSnapshot -> Text -> FilePath -> IO (Either GitSnapshotError LazyByteString.ByteString)
readPathAt snapshot commit path = case validatePath path of
  Left issue -> pure (Left issue)
  Right ()
    | not (validCommit (Text.unpack commit)) -> pure (Left (GitInvalidCommit "commit identifier is malformed"))
    | otherwise -> do
        ancestry <- runGitBytes snapshot
          ["merge-base", "--is-ancestor", Text.unpack commit, Text.unpack (snapshotCommit snapshot)]
        case ancestry of
          Left (GitProcessFailure detail)
            | "git exited 1:" `Text.isPrefixOf` detail -> pure (Left (GitInvalidCommit
                "commit is not in the pinned history"))
          Left issue -> pure (Left issue)
          Right _ -> do
            listing <- runGitBytes snapshot ["ls-tree", "-z", Text.unpack commit, "--", path]
            case listing >>= strictText of
              Left issue -> pure (Left issue)
              Right output -> case Text.splitOn "\t" (Text.dropWhileEnd (== '\0') output) of
                [metadata, listed] | Text.unpack listed == path -> case Text.words metadata of
                  [_, "blob", object] | validCommit (Text.unpack object) ->
                    runGitBytes snapshot ["cat-file", "blob", Text.unpack object]
                  _ -> pure (Left (GitMissingPath path))
                _ -> pure (Left (GitMissingPath path))

parseLinearHistory :: Text -> Text -> Either GitSnapshotError [Text]
parseLinearHistory pinned output = do
  rows <- case filter (not . Text.null) (Text.lines output) of
    [] -> Left (GitProcessFailure "Git returned an empty history")
    values -> Right values
  commits <- mapM parseRow rows
  let identifiers = map fst commits
  case snd <$> unsnoc identifiers of
    Nothing -> Left (GitProcessFailure "Git returned an empty history")
    Just tip
      | tip /= pinned -> Left
          (GitProcessFailure "Git history does not end at the pinned snapshot")
      | otherwise -> validateContinuity commits
  where
    parseRow row = case Text.words row of
      [] -> Left (GitProcessFailure "Git returned an empty history row")
      commit : parents
        | not (validCommitText commit) -> Left
            (GitInvalidCommit "Git returned an invalid history commit identifier")
        | any (not . validCommitText) parents -> Left
            (GitInvalidCommit "Git returned an invalid history parent identifier")
        | length parents > 1 -> Left
            (GitProcessFailure "Git history contains a merge commit")
        | otherwise -> Right (commit, parents)

    validateContinuity commits = case commits of
      [] -> Left (GitProcessFailure "Git returned an empty history")
      (_, firstParents) : rest
        | not (null firstParents) -> Left
            (GitProcessFailure "Git history root has a parent")
        | otherwise -> do
            mapM_ checkPair (zip commits rest)
            pure (map fst commits)

    checkPair ((previous, _), (_, parents)) = case parents of
      [parent]
        | parent == previous -> Right ()
        | otherwise -> Left
            (GitProcessFailure "Git history parent continuity is broken")
      [] -> Left (GitProcessFailure "Git history has a disconnected root")
      _ -> Left (GitProcessFailure "Git history contains a merge commit")

    validCommitText value = validCommit (Text.unpack value)

validatePath :: FilePath -> Either GitSnapshotError ()
validatePath path
  | null path = Left (GitInvalidPath path "path must not be empty")
  | take 1 path == "/" = Left (GitInvalidPath path "path must be relative")
  | '\0' `elem` path = Left (GitInvalidPath path "path must not contain NUL")
  | any invalidComponent (components path) =
      Left (GitInvalidPath path "path contains an empty, dot, or dot-dot component")
  | any objectExpressionCharacter path =
      Left (GitInvalidPath path "path contains Git object-expression syntax")
  | otherwise = Right ()
  where
    components value = case break (== '/') value of
      (component, []) -> [component]
      (component, _ : rest) -> component : components rest
    invalidComponent component = null component || component == "." || component == ".."
    objectExpressionCharacter character = case character of
      '~' -> True
      '^' -> True
      ':' -> True
      '?' -> True
      '*' -> True
      '[' -> True
      '\\' -> True
      _ -> False

strictText :: LazyByteString.ByteString -> Either GitSnapshotError Text
strictText bytes = case Text.decodeUtf8' (LazyByteString.toStrict bytes) of
  Left _ -> Left (GitProcessFailure "git output is not valid UTF-8")
  Right value -> Right value

validCommit :: String -> Bool
validCommit value = length value `elem` [40, 64] && all isLowerHex value
  where
    isLowerHex character = ('0' <= character && character <= '9')
      || ('a' <= character && character <= 'f')

runGitBytes :: GitSnapshot -> [String] -> IO (Either GitSnapshotError LazyByteString.ByteString)
runGitBytes snapshot arguments = runGit (snapshotExecutable snapshot)
  (snapshotRoot snapshot) (snapshotRootIdentity snapshot) arguments

runGit :: Executable -> FilePath -> DirectoryIdentity -> [String]
       -> IO (Either GitSnapshotError LazyByteString.ByteString)
runGit executable root expectedIdentity arguments = mask $ \restore -> do
  openedResult <- openArchiveRoot root
  case openedResult of
    Left issue -> pure (Left issue)
    Right (_, identity, directoryFd) -> bracket (pure directoryFd) closeFd $ \pinnedFd ->
      restore $ if identity /= expectedIdentity
        then pure (Left (GitProcessFailure "archive root changed during operation"))
        else do
          unchanged <- executableStillMatches executable
          if not unchanged
            then pure (Left (GitProcessFailure "configured Git executable changed during operation"))
            else do
              environment <- getEnvironment
              let cleanEnvironment = filter (not . isGitVariable . fst) environment
              result <- runProcessBoundedAt pinnedFd (executablePath executable)
                arguments cleanEnvironment outputLimit (1024 * 1024)
              stillUnchanged <- executableStillMatches executable
              pure $ if not stillUnchanged
                then Left (GitProcessFailure "configured Git executable changed during operation")
                else case result of
                  Left problem -> Left (GitProcessExecutionFailure problem)
                  Right (ExitSuccess, outputBytes, _) -> Right (LazyByteString.fromStrict outputBytes)
                  Right (ExitFailure code, _, errorBytes) -> Left (GitProcessFailure
                    ("git exited " <> Text.pack (show code) <> ": "
                      <> Text.decodeUtf8With TextError.lenientDecode errorBytes))
  where
    outputLimit = if take 1 arguments == ["show"] then Nothing else Just (64 * 1024 * 1024)
    isGitVariable name = "GIT_" `Text.isPrefixOf` Text.pack name

openArchiveRoot :: FilePath
                -> IO (Either GitSnapshotError (FilePath, DirectoryIdentity, Fd))
openArchiveRoot root = do
  canonical <- canonicalPath root
  case canonical of
    Left issue -> pure (Left issue)
    Right actualRoot -> do
      opened <- tryIOError (openFd actualRoot ReadOnly noFollowFlags)
      case opened of
        Left problem -> pure (Left (GitProcessFailure (Text.pack (show problem))))
        Right directoryFd -> do
          statusResult <- tryIOError (getFdStatus directoryFd)
            `onException` closeFd directoryFd
          case statusResult of
            Left problem -> closeFd directoryFd >> pure
              (Left (GitProcessFailure (Text.pack (show problem))))
            Right status
              | not (isDirectory status) -> closeFd directoryFd >> pure
                  (Left (GitProcessFailure "archive root is not a directory"))
              | otherwise -> pure (Right
                  ( actualRoot
                  , DirectoryIdentity (fromIntegral (deviceID status))
                      (fromIntegral (fileID status))
                  , directoryFd
                  ))
  where
    noFollowFlags = defaultFileFlags
      { directory = True, nofollow = True, nonBlock = True, cloexec = True }
