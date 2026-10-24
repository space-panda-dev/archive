{-# LANGUAGE OverloadedStrings #-}

-- | The Git durability baseline Door's writer establishes and checks.
--
-- Door supports only a self-contained object store (no alternates, grafts,
-- replacement refs, shallow or partial clones), proves the closure of a
-- commit readable before it synchronizes the store, and synchronizes the
-- branch ref and its log with their directories after a ref update.
module Archive.Door.RecordCommit.Durability
  ( durableBootstrapGitState
  , syncBaselineFrom
  , syncGitObjectStoreBaseline
  , syncReferenceDirectories
  , validateGitObjectStoreTopology
  ) where

import Archive.Door.Hex (hexText)
import Archive.Door.RecordCommit.Descriptors
  ( fdValue
  , openDirectoryAt
  , openRegularForSync
  , syncDirectoryChain
  , syncDirectoryFdOrFail
  , syncRegularRelative
  , syncStrongFdOrFail
  , withDuplicatedBinaryHandle
  )
import Archive.Door.RecordCommit.Failure (failText, tryIO, unlessEither)
import Archive.Door.RecordCommit.Git (runGit, runGitStatus)
import Archive.Door.RecordCommit.Internal (ArchiveWriteLock (..))
import Archive.Door.RecordCommit.Paths (uniqueSorted)
import Archive.Executables (Executable)
import qualified Codec.Compression.Zlib as Zlib
import qualified Control.Exception as Exception
import qualified Crypto.Hash.SHA1 as SHA1
import qualified Crypto.Hash.SHA256 as SHA256
import qualified Data.ByteString as ByteString
import qualified Data.ByteString.Lazy as LazyByteString
import Data.List (sort)
import Data.Text (Text)
import qualified Data.Text as Text
import qualified Data.Text.Encoding as TextEncoding
import System.Exit (ExitCode (..))
import System.IO.Error (isDoesNotExistError)
import System.Posix.IO
  ( OpenFileFlags (cloexec, nofollow, nonBlock)
  , OpenMode (ReadOnly)
  , closeFd
  , defaultFileFlags
  , openFdAt
  )
import System.Posix.Types (Fd)
import Text.Read (readMaybe)
import Foreign.C.Error (throwErrnoIfMinus1_)
import Foreign.C.Types (CInt (..))

foreign import ccall safe "archive_sync_object_store"
  c_syncObjectStore :: CInt -> CInt -> IO CInt

foreign import ccall safe "archive_sync_tree"
  c_syncTree :: CInt -> IO CInt

-- | Establish the post-commit durability baseline for a newly initialized
-- archive. The lock must remain held until the caller has also synchronized
-- the archive files and directory tree.
durableBootstrapGitState :: ArchiveWriteLock -> IO (Either Text ())
durableBootstrapGitState (ArchiveWriteLock rootPath rootFd gitFd executable) = do
  branchResult <- runGit executable rootPath rootFd gitFd Nothing ["symbolic-ref", "-q", "HEAD"]
  headResult <- runGit executable rootPath rootFd gitFd Nothing ["rev-parse", "--verify", "HEAD^{commit}"]
  annexResult <- runGit executable rootPath rootFd gitFd Nothing
    ["rev-parse", "--verify", "refs/heads/git-annex^{commit}"]
  refsResult <- runGit executable rootPath rootFd gitFd Nothing
    ["for-each-ref", "--format=%(refname)"]
  checked <- pure $ do
    branch <- Text.strip <$> branchResult
    unlessEither ("refs/heads/" `Text.isPrefixOf` branch)
      "initialized Git HEAD is not a local branch"
    headCommit <- Text.strip <$> headResult
    annexCommit <- Text.strip <$> annexResult
    refs <- refsResult
    let names = sort (filter (not . Text.null) (Text.lines refs))
    unlessEither ("refs/heads/git-annex" `elem` names)
      "git-annex initialization ref is missing"
    unlessEither (branch `elem` names)
      "initialized HEAD branch ref is missing"
    unlessEither (all ("refs/heads/" `Text.isPrefixOf`) names)
      "initialized repository has an unsupported non-branch ref"
    Right (headCommit, annexCommit, names)
  case checked of
    Left problem -> pure (Left problem)
    Right (headCommit, annexCommit, refs) -> do
      -- Validate and synchronize the complete closure of each initial ref.
      -- The object-store walker synchronizes every loose object and pack, so
      -- the second pass is intentionally validation-only when both refs share
      -- storage; it keeps the ref closure check explicit.
      headSync <- syncGitObjectStoreBaseline executable rootPath rootFd gitFd headCommit
      case headSync of
        Left problem -> pure (Left problem)
        Right () -> do
          annexSync <- syncGitObjectStoreBaseline executable rootPath rootFd gitFd annexCommit
          case annexSync of
            Left problem -> pure (Left problem)
            Right () -> do
              refSync <- mapM (syncReferenceDirectories gitFd) refs
              case sequence_ refSync of
                Left problem -> pure (Left problem)
                Right () -> do
                  checkedFiles <- tryIO $ do
                    syncRegularRelative gitFd ["HEAD"]
                    syncRegularRelative gitFd ["config"]
                    throwErrnoIfMinus1_ "archive sync initialized archive tree"
                      (c_syncTree (fdValue rootFd))
                  pure $ case checkedFiles of
                    Left problem -> Left ("could not synchronize initialized archive files and Git-annex state: " <> problem)
                    Right () -> Right ()

validateGitObjectStoreTopology :: Executable -> FilePath -> Fd -> Fd -> IO (Either Text ())
validateGitObjectStoreTopology executable rootPath rootFd gitFd = do
  commonDirectory <- absentAt gitFd "commondir"
    "Git common-directory indirection is unsupported for durable Door commits"
  case commonDirectory of
    Left problem -> pure (Left problem)
    Right () -> do
      objectsResult <- tryIO (openDirectoryAt gitFd "objects")
      case objectsResult of
        Left problem -> pure (Left ("could not pin Git object directory: " <> problem))
        Right objectsFd -> Exception.bracket (pure objectsFd) closeFd $ \objectsDirectory -> do
          infoResult <- Exception.try (openDirectoryAt objectsDirectory "info")
            :: IO (Either Exception.IOException Fd)
          alternateStore <- case infoResult of
            Left problem
              | isDoesNotExistError problem -> pure (Right ())
              | otherwise -> pure (Left ("could not pin Git object info directory: " <> Text.pack (show problem)))
            Right infoFd -> Exception.bracket (pure infoFd) closeFd $ \infoDirectory ->
              absentAt infoDirectory "alternates"
                "Git alternate object stores are unsupported for durable Door commits"
          case alternateStore of
            Left problem -> pure (Left problem)
            Right () -> do
              shallow <- runGit executable rootPath rootFd gitFd Nothing
                ["rev-parse", "--is-shallow-repository"]
              partialClone <- runGit executable rootPath rootFd gitFd Nothing
                ["config", "--null", "--default=__archive_no_partial_clone__"
                , "--get", "extensions.partialClone"]
              grafts <- do
                graftInfoResult <- tryIO (openDirectoryAt gitFd "info")
                case graftInfoResult of
                  Left problem -> pure (Left ("could not inspect Git info directory: " <> problem))
                  Right infoFd -> Exception.bracket (pure infoFd) closeFd $ \infoDirectory ->
                    absentAt infoDirectory "grafts"
                      "Git grafts are unsupported for durable Door commits"
              replaceRefs <- runGit executable rootPath rootFd gitFd Nothing
                ["for-each-ref", "--format=%(refname)", "refs/replace"]
              fsckOverrides <- runGitStatus executable rootPath rootFd gitFd Nothing
                [ "config", "--local", "--null", "--name-only", "--get-regexp"
                , "^fsck\\."
                ]
              pure $ do
                shallowValue <- Text.strip <$> shallow
                unlessEither (shallowValue == "false")
                  "shallow Git repositories are unsupported for durable Door commits"
                partialValue <- partialClone
                unlessEither (partialValue == "__archive_no_partial_clone__\NUL")
                  "partial-clone Git repositories are unsupported for durable Door commits"
                either (Left . ("could not verify absence of Git grafts: " <>)) Right grafts
                replacements <- Text.strip <$> replaceRefs
                unlessEither (Text.null replacements)
                  "Git replacement refs are unsupported for durable Door commits"
                fsckSettings <- fsckOverrides
                unlessEither (fsckSettings == ExitFailure 1)
                  "repository fsck configuration overrides are unsupported for durable Door commits"
                Right ()
  where
    absentAt parent name refusal = do
      opened <- Exception.try (openFdAt (Just parent) name ReadOnly
        defaultFileFlags { nofollow = True, nonBlock = True, cloexec = True })
        :: IO (Either Exception.IOException Fd)
      case opened of
        Left problem
          | isDoesNotExistError problem -> pure (Right ())
          | otherwise -> pure (Left ("could not inspect Git object-store topology: " <> Text.pack (show problem)))
        Right fd -> closeFd fd >> pure (Left refusal)

syncGitObjectStoreBaseline
  :: Executable -> FilePath -> Fd -> Fd -> Text -> IO (Either Text ())
syncGitObjectStoreBaseline executable rootPath rootFd gitFd targetCommit = do
  topology <- validateGitObjectStoreTopology executable rootPath rootFd gitFd
  case topology of
    Left problem -> pure (Left problem)
    Right () -> do
      objectFormat <- runGit executable rootPath rootFd gitFd Nothing
        ["rev-parse", "--show-object-format=storage"]
      closure <- runGitStatus executable rootPath rootFd gitFd Nothing
        [ "rev-list", "--objects", "--missing=error", "--quiet"
        , Text.unpack targetCommit
        ]
      fsck <- runGitStatus executable rootPath rootFd gitFd Nothing
        [ "fsck", "--full", "--strict", "--no-reflogs", "--no-dangling", "--no-progress"
        , Text.unpack targetCommit
        ]
      checked <- pure $ do
        format <- Text.strip <$> objectFormat
        objectTailLength <- (case format of
          "sha1" -> Right 38
          "sha256" -> Right 62
          _ -> Left "Git object format is unsupported for durable Door commits") :: Either Text Int
        closureStatus <- closure
        unlessEither (closureStatus == ExitSuccess)
          "Git could not traverse every object reachable from the proposed commit"
        fsckStatus <- fsck
        unlessEither (fsckStatus == ExitSuccess)
          "Git fsck could not verify every reachable object and its contents"
        Right objectTailLength
      case checked of
        Left problem -> pure (Left ("could not prove a complete, readable Git object closure: " <> problem))
        Right objectTailLength -> do
          objectsResult <- tryIO (openDirectoryAt gitFd "objects")
          case objectsResult of
            Left problem -> pure (Left ("could not pin Git object directory for synchronization: " <> problem))
            Right objectsFd -> Exception.bracket (pure objectsFd) closeFd $ \objectsDirectory -> do
              synced <- tryIO $ do
                throwErrnoIfMinus1_ "archive sync Git object store"
                  (c_syncObjectStore (fdValue objectsDirectory) (fromIntegral objectTailLength))
                syncDirectoryFdOrFail gitFd
              pure $ case synced of
                Left problem -> Left ("could not synchronize the reachable Git object-store baseline: " <> problem)
                Right () -> Right ()
  where

-- | Establish the object-store baseline for a commit Door made on top of a
-- checkpointed commit (ADR 0052). The objects reachable from the base were
-- traversed, checked, and synchronized when the base was acknowledged, and
-- only coordinated maintenance under the archive lock can replace them. So
-- only the objects new in the target are checked here: Git traverses them
-- (parsing each new commit and tree, and refusing a missing object), and each
-- new loose object is inflated and hashed against its name before it is
-- synchronized. 'Right False' asks the caller for the full baseline instead,
-- for example when a new object is not a loose object.
syncGitObjectStoreIncrement
  :: Executable -> FilePath -> Fd -> Fd -> Text -> Text -> IO (Either Text Bool)
syncGitObjectStoreIncrement executable rootPath rootFd gitFd baseCommit targetCommit = do
  -- The caller proved the object-store topology under this same archive lock
  -- before writing the batch ('prepareCommit'); only maintenance under the
  -- lock could change it.
  ancestry <- runGitStatus executable rootPath rootFd gitFd Nothing
    ["merge-base", "--is-ancestor", Text.unpack baseCommit, Text.unpack targetCommit]
  objectFormat <- runGit executable rootPath rootFd gitFd Nothing
    ["rev-parse", "--show-object-format=storage"]
  listing <- runGit executable rootPath rootFd gitFd Nothing
    [ "rev-list", "--objects", "--missing=error"
    , Text.unpack targetCommit, "^" <> Text.unpack baseCommit
    ]
  case (,,) <$> ancestry <*> objectFormat <*> listing of
    Left problem -> pure (Left ("could not prove the new Git objects: " <> problem))
    Right (ExitSuccess, format, output) -> do
      let objects = [object | line <- Text.lines output, object : _ <- [Text.words line]]
      case Text.strip format of
        "sha1" -> verifyAndSync SHA1.hashlazy 40 objects
        "sha256" -> verifyAndSync SHA256.hashlazy 64 objects
        _ -> pure (Left "Git object format is unsupported for durable Door commits")
    Right _ -> pure (Right False)
  where
    verifyAndSync hashLazy width objects
      | null objects || any (\object -> Text.length object /= width) objects = pure (Right False)
      | otherwise = do
          objectsResult <- tryIO (openDirectoryAt gitFd "objects")
          case objectsResult of
            Left problem -> pure (Left ("could not pin Git object directory for synchronization: " <> problem))
            Right objectsFd -> Exception.bracket (pure objectsFd) closeFd $ \objectsDirectory -> do
              checked <- mapM (checkLoose hashLazy objectsDirectory) objects
              case sequence checked of
                Left problem -> pure (Left problem)
                Right found
                  | not (and found) -> pure (Right False)
                  | otherwise -> do
                      synced <- tryIO $ do
                        mapM_ (syncFanout objectsDirectory) (sort (uniqueSorted (map (Text.take 2) objects)))
                        syncDirectoryFdOrFail objectsDirectory
                        syncDirectoryFdOrFail gitFd
                      pure $ case synced of
                        Left problem -> Left ("could not synchronize the new Git objects: " <> problem)
                        Right () -> Right True

    checkLoose hashLazy objectsDirectory object = do
      let fanout = Text.unpack (Text.take 2 object)
          name = Text.unpack (Text.drop 2 object)
      opened <- Exception.try (openFdAt (Just objectsDirectory) (fanout <> "/" <> name) ReadOnly
        defaultFileFlags { nofollow = True, nonBlock = True, cloexec = True })
        :: IO (Either Exception.IOException Fd)
      case opened of
        Left problem
          | isDoesNotExistError problem -> pure (Right False)
          | otherwise -> pure (Left ("could not open new Git object " <> object <> ": " <> Text.pack (show problem)))
        Right fd -> Exception.bracket (pure fd) closeFd $ \objectFd -> do
          contents <- tryIO $ do
            compressed <- withDuplicatedBinaryHandle objectFd ByteString.hGetContents
            inflated <- Exception.evaluate (LazyByteString.toStrict
              (Zlib.decompress (LazyByteString.fromStrict compressed)))
            syncStrongFdOrFail objectFd
            pure inflated
          pure $ case contents of
            Left problem -> Left ("new Git object " <> object <> " is unreadable: " <> problem)
            Right inflated
              | not (wellFormed inflated) -> Left ("new Git object " <> object <> " has a malformed header")
              | hexText (hashLazy (LazyByteString.fromStrict inflated)) /= object ->
                  Left ("new Git object " <> object <> " does not match its name")
              | otherwise -> Right True

    wellFormed inflated =
      let (header, rest) = ByteString.break (== 0) inflated
       in case Text.words (TextEncoding.decodeLatin1 header) of
            [kind, size] -> kind `elem` ["blob", "tree", "commit", "tag"]
              && readMaybe (Text.unpack size) == Just (ByteString.length rest - 1)
            _ -> False

    syncFanout objectsDirectory fanout = do
      fanoutFd <- openDirectoryAt objectsDirectory (Text.unpack fanout)
      Exception.bracket (pure fanoutFd) closeFd syncDirectoryFdOrFail

-- | The object-store baseline for a new commit: only its new objects when a
-- checkpoint proved the base's objects, otherwise every reachable object.
syncBaselineFrom :: Executable -> FilePath -> Fd -> Fd -> Maybe Text -> Text -> IO (Either Text ())
syncBaselineFrom executable rootPath rootFd gitFd provenBase target = case provenBase of
  Nothing -> syncGitObjectStoreBaseline executable rootPath rootFd gitFd target
  Just base -> do
    incremental <- syncGitObjectStoreIncrement executable rootPath rootFd gitFd base target
    case incremental of
      Right True -> pure (Right ())
      Right False -> syncGitObjectStoreBaseline executable rootPath rootFd gitFd target
      Left problem -> pure (Left problem)


syncReferenceDirectories :: Fd -> Text -> IO (Either Text ())
syncReferenceDirectories gitFd branch = do
  result <- tryIO $ do
    components <- either failText pure (referenceComponents branch)
    let referencePath = "refs" : "heads" : drop 2 components
        logPath = "logs" : referencePath
    refFd <- openRegularForSync gitFd referencePath
    Exception.bracket (pure refFd) closeFd $ \referenceFile -> do
      logFd <- openRegularForSync gitFd logPath
      Exception.bracket (pure logFd) closeFd $ \logFile -> do
        syncStrongFdOrFail logFile
        syncStrongFdOrFail referenceFile
        syncDirectoryChain gitFd (init referencePath)
        syncDirectoryChain gitFd (init logPath)
  pure $ case result of
    Left problem -> Left ("could not sync Git reference directories: " <> problem)
    Right () -> Right ()
  where
    referenceComponents value = do
      let components = map Text.unpack (Text.splitOn "/" value)
      unlessEither ("refs/heads/" `Text.isPrefixOf` value)
        "Git branch is outside refs/heads"
      unlessEither (length components >= 3 && all safeComponent components)
        "Git branch has unsafe path components"
      Right components
    safeComponent component = not (null component)
      && component /= "." && component /= ".."
      && '/' `notElem` component && '\\' `notElem` component
