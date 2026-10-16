{-# LANGUAGE OverloadedStrings #-}
{-# LANGUAGE ScopedTypeVariables #-}

-- | The shared mutation seam for Door record batches.
--
-- Validation belongs to the admission and transition planners.  This module
-- serializes their final write, installs only new immutable record files, and
-- advances the current branch with an expected-parent compare-and-swap. A
-- specification upgrade (ADR 0068) also adds the new release's documents and
-- bundles and replaces the manifest, in the same commit.
module Archive.Door.RecordCommit
  ( ArchiveWriteLock
  , ContentLink (..)
  , ArchiveFile (..)
  , archiveWriteLockGitExecutableIdentity
  , commitRecordBatch
  , commitRecordBatchWithContent
  , commitRecordBatchWithFiles
  , CommitVerification (..)
  , writeCommitCheckpoint
  , writeCommitBaseCheckpoint
  , validateArchiveFilePath
  , configuredRemoteNames
  , runAnnexLocked
  , validateContentPath
  , durableBootstrapGitState
  , prunePackedGitObjects
  , repackGitObjects
  , writeBaseCheckpoint
  , proveObjectBaseline
  , validateRecordPath
  , withArchiveWriteLock
  , withArchiveWriteLockAtPinned
  ) where

import Archive.Door.Layout
  ( contentRelativePath
  , digestText
  , specVersion
  , parseContentRelativePath
  , parseRecordRelativePath
  , recordDirectoryChain
  , recordRelativePath
  )
import Archive.Door.MutationError (MutationError (..), mutationErrorMessage)
import Archive.Door.VerifiedCheckpoint
  ( VerifiedCheckpoint (..)
  , appendCheckpoint
  , extendCheckpoint
  , removeCheckpoint
  , summarizeStoredBytes
  , writeCheckpoint
  )
import qualified Codec.Compression.Zlib as Zlib
import qualified Crypto.Hash.SHA1 as SHA1
import qualified Crypto.Hash.SHA256 as SHA256
import Control.Applicative ((<|>))
import qualified Control.Exception as Exception
import Control.Monad (unless, when)
import Archive.Process (ProcessError, processErrorMessage, runProcessBoundedAt)
import Archive.Executables
  ( Executable
  , executableIdentityText
  , executablePath
  , executableStillMatches
  , resolveGitExecutable
  )
import Control.Concurrent.MVar (MVar, newMVar, withMVar)
import Data.IORef (IORef, modifyIORef, newIORef, readIORef, writeIORef)
import qualified Data.Map.Strict as Map
import qualified Data.ByteString.Lazy as LazyByteString
import qualified Data.ByteString as ByteString
import Data.Unique (hashUnique, newUnique)
import Data.List (inits, sort, sortOn)
import Data.Text (Text)
import qualified Data.Text as Text
import qualified Data.Text.Encoding as TextEncoding
import qualified Data.Text.Encoding.Error as TextError
import System.Directory
  ( makeAbsolute
  )
import System.Environment (getEnvironment)
import System.Exit (ExitCode (..))
import System.FilePath (isAbsolute, splitDirectories, takeDirectory)
import System.IO (Handle, SeekMode (AbsoluteSeek), hClose, hPutStrLn, hSetBinaryMode, stderr)
import System.IO.Error (isAlreadyExistsError, isDoesNotExistError)
import Text.Read (readMaybe)
import System.Posix.Files (FileStatus, deviceID, fileID, getFdStatus, isRegularFile, linkCount, ownerReadMode, ownerWriteMode, unionFileModes)
import System.Posix.IO
  ( LockRequest (WriteLock)
  , FdOption (CloseOnExec)
  , OpenFileFlags (cloexec, creat, directory, exclusive, nofollow, nonBlock)
  , OpenMode (ReadOnly, ReadWrite, WriteOnly)
  , closeFd
  , createPipe
  , defaultFileFlags
  , dup
  , fdToHandle
  , openFd
  , openFdAt
  , queryFdOption
  , setLock
  , setFdOption
  )
import System.Posix.Types (CSsize (..), DeviceID, Fd (..), FileID, FileMode)
import System.Posix.Process (getProcessID)
import System.Posix.IO.ByteString (fdWriteBuf)
import Foreign.Marshal.Alloc (allocaBytes)
import Foreign.Ptr (Ptr, castPtr, nullPtr, plusPtr)
import Foreign.C.Error (eEXIST, eNOENT, eNOTEMPTY, getErrno, throwErrnoIfMinus1_)
import Foreign.C.String (CString, peekCString, peekCStringLen, withCString)
import Foreign.C.Types (CInt (..), CSize (..), CUInt (..))
import System.IO.Unsafe (unsafePerformIO)

foreign import ccall unsafe "linkat"
  c_linkat :: CInt -> CString -> CInt -> CString -> CInt -> IO CInt

foreign import ccall unsafe "unlinkat"
  c_unlinkat :: CInt -> CString -> CInt -> IO CInt

foreign import ccall unsafe "archive_unlinkat_directory"
  c_unlinkatDirectory :: CInt -> CString -> IO CInt

foreign import ccall unsafe "symlinkat"
  c_symlinkat :: CString -> CInt -> CString -> IO CInt

foreign import ccall unsafe "mkdirat"
  c_mkdirat :: CInt -> CString -> CUInt -> IO CInt

foreign import ccall unsafe "readlinkat"
  c_readlinkat :: CInt -> CString -> CString -> CSize -> IO CSsize

foreign import ccall unsafe "renameat"
  c_renameat :: CInt -> CString -> CInt -> CString -> IO CInt

foreign import ccall unsafe "archive_fullsync_fd"
  c_fullsync :: CInt -> IO CInt

foreign import ccall unsafe "archive_fullsync_directory_fd"
  c_fullsyncDirectory :: CInt -> IO CInt

foreign import ccall unsafe "archive_has_entry_prefix"
  c_hasEntryPrefix :: CInt -> CString -> IO CInt

foreign import ccall safe "archive_sync_object_store"
  c_syncObjectStore :: CInt -> CInt -> IO CInt

foreign import ccall safe "archive_sync_tree"
  c_syncTree :: CInt -> IO CInt

foreign import ccall safe "archive_open_directory_stream"
  c_openDirectoryStream :: CInt -> IO (Ptr ())

foreign import ccall unsafe "archive_read_directory_stream"
  c_readDirectoryStream :: Ptr () -> CString -> CSize -> IO CInt

foreign import ccall unsafe "archive_close_directory_stream"
  c_closeDirectoryStream :: Ptr () -> IO CInt


-- The constructor stays private so the persistent writer cannot be called
-- without first acquiring the archive lock.
data ArchiveWriteLock = ArchiveWriteLock FilePath Fd Fd Executable

-- Git atomically replaces its index file. Keep the prior descriptor so a
-- replacement is accepted only when Git removed the reserved inode and the
-- new path is a regular file. The reference is created for this operation.
-- This ownership proof assumes the configured Git executable is trusted and
-- there is no hostile same-uid process racing inside .git; POSIX offers no
-- conditional unlink-by-inode for Git's pathname-based index replacement.
-- We still recheck before and after every child, and fail closed on mismatch.
data IndexReservation = IndexReservation FilePath
  (IORef (Fd, (DeviceID, FileID))) (IORef (Maybe (IORef OperationJournal)))

-- The record path is removable only while it still names the inode installed
-- by this operation and that inode still hashes to the Git blob installed in
-- the temporary index.
data InstalledRecord = InstalledRecord
  { installedRecordPath :: FilePath
  , installedRecordBlob :: Text
  , installedRecordDevice :: DeviceID
  , installedRecordFile :: FileID
  }

data PreparedRecord = PreparedRecord
  { preparedRecordPath :: FilePath
  , preparedRecordBlob :: Text
  , preparedRecordTemporary :: FilePath
  , preparedRecordFd :: Fd
  , preparedRecordDevice :: DeviceID
  , preparedRecordFile :: FileID
  }

data OperationJournal = OperationJournal
  { journalOldHead :: Text
  , journalBranch :: Text
  , journalPhase :: Text
  , journalTargetCommit :: Maybe Text
  , journalIndex :: JournalIndex
  , journalEntries :: [JournalEntry]
  -- | Content locations this batch adds as git-annex symlink entries, with
  -- their exact link blobs. They have no worktree file before the commit, so
  -- recovery only verifies them in a committed tree.
  , journalContent :: [(FilePath, Text)]
  -- | Specification files a specification upgrade adds or replaces, with
  -- their exact blobs. Like content links they exist only in the temporary
  -- index before the commit, so recovery only verifies them in a committed
  -- tree.
  , journalFiles :: [(FilePath, Text)]
  } deriving (Eq, Show)

data JournalIndex = JournalIndex
  { journalIndexName :: FilePath
  , journalIndexDevice :: DeviceID
  , journalIndexFile :: FileID
  } deriving (Eq, Show)

data JournalEntry = JournalEntry
  { journalPath :: FilePath
  , journalTemporary :: FilePath
  , journalBlob :: Maybe Text
  , journalDevice :: Maybe DeviceID
  , journalFile :: Maybe FileID
  } deriving (Eq, Show)

data JournalIdentity = JournalIdentity DeviceID FileID deriving (Eq, Show)

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

archiveWriteLockGitExecutableIdentity :: ArchiveWriteLock -> Text
archiveWriteLockGitExecutableIdentity (ArchiveWriteLock _ _ _ executable) =
  executableIdentityText executable

-- | Remove loose objects whose packed copies have been verified and synced.
-- The archive lock must cover the complete preflight, prune, and postflight.
prunePackedGitObjects :: FilePath -> IO (Either MutationError ())
prunePackedGitObjects root = withArchiveWriteLock root $ \lock ->
  prunePackedGitObjectsLocked lock

-- | Pack loose Git objects under the archive lock (ADR 0034), keeping the
-- number of packs logarithmic, then remove the loose copies through the
-- verified prune: every pack is checked and the reachable baseline
-- synchronized before any loose object is removed. The checkpoint is removed
-- first, because maintenance changes the store it vouches for (ADR 0052).
repackGitObjects :: FilePath -> IO (Either MutationError ())
repackGitObjects root = withArchiveWriteLock root $ \lock@(ArchiveWriteLock rootPath rootFd gitFd executable) -> do
  cleared <- removeCheckpoint gitFd (rootPath <> "/.git")
  case cleared of
    Left problem -> pure (Left (MutationOperationFailure ("could not remove the verification checkpoint: " <> problem)))
    Right () -> do
      packed <- runGitStatus executable rootPath rootFd gitFd Nothing
        ["repack", "-q", "-d", "--geometric=2", "--no-write-bitmap-index"]
      case packed of
        Left problem -> pure (Left (MutationOperationFailure ("git repack failed: " <> problem)))
        Right (ExitFailure code) -> pure (Left (MutationOperationFailure
          ("git repack exited " <> Text.pack (show code))))
        Right ExitSuccess -> prunePackedGitObjectsLocked lock

-- | Prove and synchronize every object reachable from a commit (topology,
-- traversal, strict fsck, and synchronization), under the archive lock.
proveObjectBaseline :: ArchiveWriteLock -> Text -> IO (Either Text ())
proveObjectBaseline (ArchiveWriteLock rootPath rootFd gitFd executable) commit =
  syncGitObjectStoreBaseline executable rootPath rootFd gitFd commit

-- | Write a new base checkpoint for a commit verified in full while holding
-- the archive lock, after maintenance proved and synchronized its objects.
writeBaseCheckpoint :: ArchiveWriteLock -> VerifiedCheckpoint -> IO (Either Text ())
writeBaseCheckpoint (ArchiveWriteLock rootPath _ gitFd _) checkpoint =
  writeCheckpoint gitFd (rootPath <> "/.git") checkpoint

prunePackedGitObjectsLocked :: ArchiveWriteLock -> IO (Either MutationError ())
prunePackedGitObjectsLocked lock@(ArchiveWriteLock rootPath _ gitFd _) = do
  -- Maintenance changes the object store a checkpoint vouches for (ADR 0052).
  cleared <- removeCheckpoint gitFd (rootPath <> "/.git")
  opened <- either (pure . Left) (const (tryIO (openDirectoryChain gitFd ["objects", "pack"]))) cleared
  case opened of
    Left problem -> pure (Left (MutationOperationFailure
      ("could not pin Git pack directory: " <> problem)))
    Right packFd -> Exception.bracket (pure packFd) closeFd $ \pinnedPackFd -> do
      status <- tryIO (getFdStatus pinnedPackFd)
      case status of
        Left problem -> pure (Left (MutationOperationFailure
          ("could not inspect pinned Git pack directory: " <> problem)))
        Right details -> prunePackedGitObjectsWithPackDirectory lock pinnedPackFd
          (deviceID details, fileID details)

prunePackedGitObjectsWithPackDirectory
  :: ArchiveWriteLock -> Fd -> (DeviceID, FileID) -> IO (Either MutationError ())
prunePackedGitObjectsWithPackDirectory
  (ArchiveWriteLock rootPath rootFd gitFd executable) packFd packIdentity = do
  beforeRefs <- snapshotRefs executable rootPath rootFd gitFd
  case beforeRefs of
    Left problem -> operationFailure ("could not snapshot Git refs before prune-packed: " <> problem)
    Right refs -> do
      verifiedBefore <- verifyAllPacks executable rootPath rootFd gitFd packFd packIdentity
      case verifiedBefore of
        Left problem -> operationFailure ("could not verify Git packs before prune-packed: " <> problem)
        Right () -> do
          preflight <- syncRefBaselines executable rootPath rootFd gitFd refs
          case preflight of
            Left problem -> operationFailure
              ("could not establish the pre-prune Git durability baseline: " <> problem)
            Right () -> do
              packDirectoryBeforePrune <- checkPackDirectoryIdentity gitFd packIdentity
              pruned <- case packDirectoryBeforePrune of
                Left problem -> pure (Left problem)
                Right () -> runGitStatus executable rootPath rootFd gitFd Nothing ["prune-packed"]
              packDirectoryAfterPrune <- checkPackDirectoryIdentity gitFd packIdentity
              afterRefs <- snapshotRefs executable rootPath rootFd gitFd
              case afterRefs of
                Left problem -> operationFailure
                  ("could not snapshot Git refs after prune-packed: " <> problem)
                Right currentRefs -> do
                  verifiedAfter <- verifyAllPacks executable rootPath rootFd gitFd packFd packIdentity
                  postflight <- syncRefBaselines executable rootPath rootFd gitFd currentRefs
                  case (pruned, packDirectoryAfterPrune, currentRefs == refs, verifiedAfter, postflight) of
                    (Left problem, _, _, _, _) -> operationFailure
                      ("git prune-packed failed: " <> problem)
                    (_, Left problem, _, _, _) -> operationFailure
                      ("Git pack directory changed during prune-packed: " <> problem)
                    (Right (ExitFailure code), _, _, _, _) -> operationFailure
                      ("git prune-packed exited " <> Text.pack (show code))
                    (_, _, False, _, _) -> operationFailure
                      "Git refs changed while prune-packed held the archive lock"
                    (_, _, _, Left problem, _) -> operationFailure
                      ("could not verify Git packs after prune-packed: " <> problem)
                    (_, _, _, _, Left problem) -> operationFailure
                      ("could not establish the post-prune Git durability baseline: " <> problem)
                    (Right ExitSuccess, Right (), True, Right (), Right ()) -> pure (Right ())
  where
    operationFailure = pure . Left . MutationOperationFailure

-- Snapshot both the complete ref namespace and the checked-out symbolic head.
-- for-each-ref includes packed refs; HEAD itself is recorded even if detached.
snapshotRefs :: Executable -> FilePath -> Fd -> Fd -> IO (Either Text [(Text, Text)])
snapshotRefs executable rootPath rootFd gitFd = do
  refs <- runGit executable rootPath rootFd gitFd Nothing
    ["for-each-ref", "--format=%(refname)%00%(objectname)"]
  headName <- runGit executable rootPath rootFd gitFd Nothing ["symbolic-ref", "-q", "HEAD"]
  headObject <- runGit executable rootPath rootFd gitFd Nothing ["rev-parse", "--verify", "HEAD^{commit}"]
  pure $ do
    output <- refs
    symbolicHead <- headName
    object <- headObject
    parsed <- traverse parseRefLine (filter (not . Text.null) (Text.splitOn "\n" output))
    Right (("HEAD", Text.strip symbolicHead <> " " <> Text.strip object) : parsed)
  where
    parseRefLine line = case Text.splitOn "\NUL" line of
      [name, object] | not (Text.null name) && not (Text.null object) -> Right (name, object)
      _ -> Left "Git returned a malformed ref snapshot"

syncRefBaselines
  :: Executable -> FilePath -> Fd -> Fd -> [(Text, Text)] -> IO (Either Text ())
syncRefBaselines executable rootPath rootFd gitFd refs = do
  let objects = [object | (name, object) <- refs, name /= "HEAD"]
  results <- mapM (syncGitObjectStoreBaseline executable rootPath rootFd gitFd) objects
  pure (sequence_ results)

verifyAllPacks
  :: Executable -> FilePath -> Fd -> Fd -> Fd -> (DeviceID, FileID) -> IO (Either Text ())
verifyAllPacks executable rootPath rootFd gitFd packFd packIdentity = do
  identityBefore <- checkPackDirectoryIdentity gitFd packIdentity
  case identityBefore of
    Left problem -> pure (Left problem)
    Right () -> do
      names <- listDirectoryDescriptor packFd
      case names of
        Left problem -> pure (Left ("could not enumerate pinned Git pack directory: " <> problem))
        Right entries -> do
          identityAfterListing <- checkPackDirectoryIdentity gitFd packIdentity
          case identityAfterListing of
            Left problem -> pure (Left problem)
            Right () -> do
              let indexes = filter (hasSuffix ".idx") entries
              results <- mapM verify indexes
              pure (sequence_ results)
  where
    verify index = do
      before <- checkPackDirectoryIdentity gitFd packIdentity
      case before of
        Left problem -> pure (Left problem)
        Right () -> do
          result <- runGitStatus executable rootPath rootFd gitFd Nothing
            ["verify-pack", "objects/pack/" <> index]
          after <- checkPackDirectoryIdentity gitFd packIdentity
          pure $ do
            either (Left . ("Git pack directory changed during verify-pack: " <>)) Right after
            case result of
              Left problem -> Left problem
              Right ExitSuccess -> Right ()
              Right (ExitFailure code) -> Left
                ("git verify-pack exited " <> Text.pack (show code) <> " for " <> Text.pack index)
    hasSuffix suffix value =
      length value >= length suffix && drop (length value - length suffix) value == suffix

checkPackDirectoryIdentity :: Fd -> (DeviceID, FileID) -> IO (Either Text ())
checkPackDirectoryIdentity gitFd (expectedDevice, expectedFile) = do
  current <- tryIO (openDirectoryChain gitFd ["objects", "pack"])
  case current of
    Left problem -> pure (Left ("could not reopen Git pack directory: " <> problem))
    Right currentFd -> Exception.bracket (pure currentFd) closeFd $ \directoryFd -> do
      status <- tryIO (getFdStatus directoryFd)
      pure $ do
        details <- status
        unlessEither (deviceID details == expectedDevice && fileID details == expectedFile)
          "pinned objects/pack directory identity changed"
        Right ()

listDirectoryDescriptor :: Fd -> IO (Either Text [FilePath])
listDirectoryDescriptor directoryFd = tryIO $ do
  stream <- c_openDirectoryStream (fdValue directoryFd)
  when (stream == nullPtr) (failText "could not open pinned directory stream")
  Exception.bracket (pure stream) closeStream $ \directoryStream ->
    allocaBytes 4096 $ \nameBuffer -> readEntries directoryStream nameBuffer []
  where
    closeStream directoryStream = do
      result <- c_closeDirectoryStream directoryStream
      throwErrnoIfMinus1_ "close pinned directory stream" (pure result)
    readEntries directoryStream nameBuffer entries = do
      result <- c_readDirectoryStream directoryStream nameBuffer 4096
      case result of
        0 -> pure (reverse entries)
        1 -> do
          name <- peekCString nameBuffer
          if name == "." || name == ".."
            then readEntries directoryStream nameBuffer entries
            else readEntries directoryStream nameBuffer (name : entries)
        _ -> throwErrnoIfMinus1_ "read pinned directory stream" (pure result)
          >> failText "pinned directory stream returned an invalid status"

-- POSIX record locks are owned by a process, so a second thread in this
-- process can acquire the same lock. Serialize those callers locally too.
{-# NOINLINE processWriteLock #-}
processWriteLock :: MVar ()
processWriteLock = unsafePerformIO (newMVar ())

-- | Run one Door mutation while holding the archive-wide advisory lock.
-- The lock file is operational state under .git and never enters the archive.
withArchiveWriteLock
  :: FilePath
  -> (ArchiveWriteLock -> IO (Either MutationError a))
  -> IO (Either MutationError a)
withArchiveWriteLock root action = withMVar processWriteLock $ \_ -> do
  executableResult <- resolveGitExecutable
  case executableResult of
    Left problem -> pure (Left (MutationConfigurationFailure problem))
    Right executable -> do
      absolute <- tryIO (makeAbsolute root)
      case absolute of
        Left problem -> pure (Left (MutationIoFailure problem))
        Right absoluteRoot -> do
          result <- tryIO $ Exception.bracket (openArchiveDirectories absoluteRoot)
            closeArchiveDirectories $ \(rootFd, gitFd) ->
              Exception.bracket (acquire gitFd) closeFd
                (\_ -> do
                  topology <- validateGitObjectStoreTopology executable absoluteRoot rootFd gitFd
                  case topology of
                    Left problem -> pure (Left (MutationOperationFailure problem))
                    Right () -> do
                      recovered <- reconcileOperationJournal executable absoluteRoot rootFd gitFd
                      case recovered of
                        Left problem -> pure (Left (MutationOperationFailure problem))
                        Right () -> action (ArchiveWriteLock absoluteRoot rootFd gitFd executable))
          pure $ case result of
            Left problem -> Left (MutationIoFailure ("archive lock failed: " <> problem))
            Right value -> value
  where
    acquire gitFd = do
      fd <- openFdAt (Just gitFd) "archive-door.lock" ReadWrite
        defaultFileFlags { creat = Just ownerFileModes, cloexec = True, nofollow = True }
      locked <- tryIO (setLock fd (WriteLock, AbsoluteSeek, 0, 0))
      case locked of
        Right () -> pure fd
        Left problem -> closeFd fd >> failText
          ("another Door operation holds the archive lock: " <> problem)

-- | Run initialization work under the same process and filesystem lock as
-- ordinary Door mutations. Both directory descriptors are supplied by the
-- caller, so acquiring the lock does not resolve a possibly replaced root or
-- .git pathname a second time.
withArchiveWriteLockAtPinned
  :: FilePath
  -> Fd
  -> Fd
  -> (ArchiveWriteLock -> IO (Either MutationError a))
  -> IO (Either MutationError a)
withArchiveWriteLockAtPinned root rootFd gitFd action =
  withMVar processWriteLock $ \_ -> do
    executableResult <- resolveGitExecutable
    case executableResult of
      Left problem -> pure (Left (MutationConfigurationFailure problem))
      Right executable -> do
        absolute <- tryIO (makeAbsolute root)
        case absolute of
          Left problem -> pure (Left (MutationIoFailure problem))
          Right absoluteRoot -> do
            pinned <- tryIO $ (,) <$> rootDirectoryMatches absoluteRoot rootFd
              <*> gitDirectoryMatches rootFd gitFd
            case pinned of
              Left problem -> pure (Left (MutationIoFailure problem))
              Right (False, _) -> pure (Left (MutationRejected
                "archive root changed before the pinned archive lock was acquired"))
              Right (_, False) -> pure (Left (MutationRejected
                "Git directory changed before the pinned archive lock was acquired"))
              Right (True, True) -> do
                result <- tryIO $ Exception.bracket (acquire gitFd) closeFd $ \_ -> do
                  topology <- validateGitObjectStoreTopology executable absoluteRoot rootFd gitFd
                  case topology of
                    Left problem -> pure (Left (MutationOperationFailure problem))
                    Right () -> do
                      recovered <- reconcileOperationJournal executable absoluteRoot rootFd gitFd
                      case recovered of
                        Left problem -> pure (Left (MutationOperationFailure problem))
                        Right () -> action (ArchiveWriteLock absoluteRoot rootFd gitFd executable)
                pure $ case result of
                  Left problem -> Left (MutationIoFailure ("archive lock failed: " <> problem))
                  Right (Left failure) -> Left failure
                  Right (Right value) -> Right value
  where
    acquire directoryFd = do
      fd <- openFdAt (Just directoryFd) "archive-door.lock" ReadWrite
        defaultFileFlags { creat = Just ownerFileModes, cloexec = True, nofollow = True }
      locked <- tryIO (setLock fd (WriteLock, AbsoluteSeek, 0, 0))
      case locked of
        Right () -> pure fd
        Left problem -> closeFd fd >> failText
          ("another Door operation holds the archive lock: " <> problem)

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

openArchiveDirectories :: FilePath -> IO (Fd, Fd)
openArchiveDirectories root = do
  rootFd <- openFd root ReadOnly directoryFlags
  gitResult <- Exception.try (openFdAt (Just rootFd) ".git" ReadOnly directoryFlags)
    :: IO (Either Exception.IOException Fd)
  case gitResult of
    Left problem -> closeFd rootFd >> Exception.throwIO problem
    Right gitFd -> pure (rootFd, gitFd)
  where
    directoryFlags = defaultFileFlags
      { directory = True, nofollow = True, cloexec = True }

closeArchiveDirectories :: (Fd, Fd) -> IO ()
closeArchiveDirectories (rootFd, gitFd) = do
  closeFd gitFd
  closeFd rootFd

operationJournalName :: FilePath
operationJournalName = "archive-door-operation"

operationJournalByteLimit :: Int
operationJournalByteLimit = 16 * 1024 * 1024

reconcileOperationJournal :: Executable -> FilePath -> Fd -> Fd -> IO (Either Text ())
reconcileOperationJournal executable rootPath rootFd gitFd =
  reconcileOperationJournalWithRecords executable rootPath rootFd gitFd Nothing Nothing

-- | The optional base names a commit whose objects a checkpoint proved
-- (ADR 0052); a journaled batch on top of it needs only its new objects
-- synchronized again.
reconcileOperationJournalWithRecords :: Executable -> FilePath -> Fd -> Fd -> Maybe Fd -> Maybe Text
                                      -> IO (Either Text ())
reconcileOperationJournalWithRecords executable rootPath rootFd gitFd pinnedRecords provenBase = do
  opened <- Exception.try $ openFdAt (Just gitFd) operationJournalName ReadOnly
    defaultFileFlags { nofollow = True, nonBlock = True, cloexec = True }
    :: IO (Either Exception.IOException Fd)
  case opened of
    Left problem
      | isDoesNotExistError problem -> pure (Right ())
      | otherwise -> pure (Left ("could not open Door operation journal: " <> Text.pack (show problem)))
    Right fd -> Exception.bracket (pure fd) closeFd $ \journalFd -> do
      inspected <- tryIO $ do
        status <- getFdStatus journalFd
        unless (isRegularFile status) (failText "Door operation journal is not a regular file")
        bytes <- withDuplicatedBinaryHandle journalFd readBoundedJournal
          >>= either failText pure
        pure (status, bytes)
      case inspected of
        Left problem -> pure (Left ("could not read Door operation journal: " <> problem))
        Right (status, bytes) -> case parseOperationJournal bytes of
          Left problem -> pure (Left ("Door operation journal requires operator inspection: " <> problem))
          Right journal -> do
            let identity = JournalIdentity (deviceID status) (fileID status)
            reconciledResult <- tryIO (reconcileJournal executable rootPath rootFd gitFd pinnedRecords provenBase journal)
            let reconciled = case reconciledResult of
                  Left problem -> Left ("journal recovery could not complete safely: " <> problem)
                  Right value -> value
            case reconciled of
              Left problem -> pure (Left problem)
              Right () -> clearJournalIfSame gitFd identity journal

reconcileJournal :: Executable -> FilePath -> Fd -> Fd -> Maybe Fd -> Maybe Text -> OperationJournal
                 -> IO (Either Text ())
reconcileJournal executable rootPath rootFd gitFd pinnedRecords provenBase journal = do
  currentBranch <- runGit executable rootPath rootFd gitFd Nothing ["symbolic-ref", "-q", "HEAD"]
  currentHead <- runGit executable rootPath rootFd gitFd Nothing ["rev-parse", "--verify", "HEAD^{commit}"]
  pureBranch <- pure $ do
    branch <- Text.strip <$> currentBranch
    whenEither (branch == journalBranch journal) "archive branch changed while a Door operation journal remained"
    headValue <- Text.strip <$> currentHead
    Right (Text.strip headValue)
  case pureBranch of
    Left problem -> pure (Left problem)
    Right headValue -> case journalPhase journal of
      "preparing" -> reconcilePreparing headValue
      "installing" -> reconcileInstalling headValue
      _ -> pure (Left "unsupported Door operation journal phase")
  where
    reconcilePreparing headValue
      | headValue /= journalOldHead journal = pure (Left "archive HEAD changed during record preparation; operation journal retained")
      | otherwise = do
          history <- oldHistoryClear
          case history of
            Left problem -> pure (Left problem)
            Right () -> do
              absent <- mapM (targetAbsent rootFd . journalPath) (journalEntries journal)
              case sequence absent of
                Left problem -> pure (Left ("record path appeared before journaled installation: " <> problem))
                Right _ -> do
                  temps <- mapM (removePreparingTemporary gitFd . journalTemporary) (journalEntries journal)
                  case sequence_ temps of
                    Left problem -> pure (Left problem)
                    Right () -> cleanupJournalIndex gitFd (journalIndex journal)

    reconcileInstalling headValue = do
      case journalTargetCommit journal of
        Nothing
          | headValue /= journalOldHead journal -> pure (Left
              "archive HEAD differs from the journaled old commit without a proposed commit; operation journal retained")
          | otherwise -> cleanupUncommitted
        Just target
          | headValue == journalOldHead journal -> cleanupUncommitted
          | otherwise -> do
              committed <- verifyCommittedBatch headValue target
              case committed of
                Left problem -> pure (Left problem)
                Right () -> do
                  recordsVerified <- withPinnedRecords rootFd pinnedRecords $ \recordsFd -> do
                    verified <- verifyCommittedRecords recordsFd (journalEntries journal)
                    case verified of
                      Left problem -> pure (Left problem)
                      Right () -> syncCommittedState gitFd recordsFd target
                        (journalBranch journal) (journalEntries journal)
                  case recordsVerified of
                    Left problem -> pure (Left
                      ("committed batch durability barrier failed; operation journal retained: " <> problem))
                    Right () -> do
                      installCommittedFiles
                      cleaned <- cleanupTemporariesOnly
                      case cleaned of
                        Left problem -> pure (Left problem)
                        Right () -> refreshCommittedIndex headValue target

    -- The worktree copies of a committed upgrade's specification files,
    -- from the committed blobs. This runs after every committed batch, also
    -- one an interrupted process left, so the worktree manifest follows the
    -- commit. The commit is the authority: a failure is reported, not undone.
    installCommittedFiles = mapM_ installCommittedFile (journalFiles journal)
    installCommittedFile (path, blob) = do
      contents <- runGitBytesTyped executable rootPath rootFd gitFd Nothing ["cat-file", "blob", Text.unpack blob]
      installed <- case contents of
        Left problem -> pure (Left (gitFailureMessage problem))
        Right bytes
          | not (blobMatches blob bytes) -> pure (Left "committed blob does not hash to its name")
          | otherwise -> tryIO (installWorktreeFile rootFd (ArchiveFile path (LazyByteString.fromStrict bytes)))
      case installed of
        Left problem -> hPutStrLn stderr ("door: warning: archive commit succeeded but "
          <> path <> " could not be written to the worktree: " <> Text.unpack problem)
        Right () -> pure ()

    -- Door builds a batch in a temporary index, so once the ref advances the
    -- ordinary index still holds the old commit's tree: Door made it stale,
    -- and recovery refreshes it before the journal goes, whether the process
    -- that committed is the one finishing or a later Door after a kill.
    -- Only that exact state is refreshed. An index already at the commit
    -- needs nothing; any other index holds changes Door did not make, and a
    -- branch moved past the journaled commit is not Door's state either.
    -- Both are left alone for the staged-changes check to refuse. A refresh
    -- that fails retains the journal, so the next Door tries again.
    refreshCommittedIndex headValue target
      | headValue /= target = pure (Right ())
      | otherwise = do
          compared <- runGitStatus executable rootPath rootFd gitFd Nothing
            ["diff", "--cached", "--quiet", Text.unpack (journalOldHead journal), "--"]
          case compared of
            Left problem -> pure (Left (indexComparisonFailure problem))
            Right (ExitFailure 1) -> pure (Right ())
            Right (ExitFailure code) -> pure (Left (indexComparisonFailure
              ("git diff exited " <> Text.pack (show code))))
            Right ExitSuccess -> do
              refreshed <- runGit executable rootPath rootFd gitFd Nothing
                ["-c", "core.hooksPath=/dev/null", "read-tree", Text.unpack target]
              pure $ case refreshed of
                Left problem -> Left
                  ("could not refresh the archive index to the committed batch; operation journal retained: " <> problem)
                Right _ -> Right ()
    indexComparisonFailure problem =
      "could not compare the archive index with the journaled old commit; operation journal retained: " <> problem

    cleanupUncommitted = withPinnedRecords rootFd pinnedRecords $ \recordsFd -> do
      history <- oldHistoryClear
      case history of
        Left problem -> pure (Left problem)
        Right () -> do
          let records = map journalToInstalled (journalEntries journal)
          cleaned <- cleanupInstalled executable rootPath rootFd gitFd recordsFd records
          case cleaned of
            Left problem -> pure (Left ("could not prove cleanup ownership for interrupted record batch: " <> problem))
            Right () -> cleanupTemporariesOnly

    -- The manifest is the one journaled path the old commit holds: an
    -- upgrade replaces it. Every other journaled path must be new.
    oldHistoryClear = do
      contents <- runGit executable rootPath rootFd gitFd Nothing
        (["ls-tree", "-r", "--name-only", Text.unpack (journalOldHead journal), "--"]
          <> map journalPath (journalEntries journal)
          <> map fst (journalContent journal)
          <> filter (/= manifestFilePath) (map fst (journalFiles journal)))
      pure $ do
        paths <- contents
        whenEither (Text.null (Text.strip paths))
          "journal record path already exists in the pinned old commit"

    cleanupTemporariesOnly = do
      results <- mapM (cleanupJournalTemporary executable rootPath rootFd gitFd) (journalEntries journal)
      case sequence_ results of
        Left problem -> pure (Left problem)
        Right () -> cleanupJournalIndex gitFd (journalIndex journal)

    verifyCommittedBatch currentHead target = do
      parent <- runGit executable rootPath rootFd gitFd Nothing ["rev-parse", "--verify", Text.unpack target <> "^"]
      diff <- runGit executable rootPath rootFd gitFd Nothing
        ["diff-tree", "--no-commit-id", "--no-renames", "--name-status", "-r", Text.unpack (journalOldHead journal), Text.unpack target]
      ancestor <- runGitStatus executable rootPath rootFd gitFd Nothing
        ["merge-base", "--is-ancestor", Text.unpack target, Text.unpack currentHead]
      listing <- treeEntries target
        (map journalPath (journalEntries journal) <> map fst (journalContent journal)
          <> map fst (journalFiles journal))
      let treeBlobsResult = map (\entry -> listing >>= treeBlob entry) (journalEntries journal)
          contentBlobsResult = map (\link -> listing >>= contentBlob link) (journalContent journal)
          fileBlobsResult = map (\file -> listing >>= fileBlob file) (journalFiles journal)
      pure $ do
        parentValue <- Text.strip <$> parent
        whenEither (parentValue == journalOldHead journal)
          "journaled commit is not a direct child of the pinned old commit"
        status <- ancestor
        whenEither (status == ExitSuccess) "journaled commit is not in the current branch history"
        diffValue <- diff
        changes <- traverse parseDiffLine (filter (not . Text.null) (Text.lines diffValue))
        treeBlobs <- sequence treeBlobsResult
        contentBlobs <- sequence contentBlobsResult
        whenEither (contentBlobs == [blob | (_, blob) <- journalContent journal])
          "journaled commit tree contains a content location with a different link"
        fileBlobs <- sequence fileBlobsResult
        whenEither (fileBlobs == [blob | (_, blob) <- journalFiles journal])
          "journaled commit tree contains a specification file with a different blob"
        let expectedPaths = sort (map (Text.pack . journalPath) (journalEntries journal)
              <> map (Text.pack . fst) (journalContent journal)
              <> map (Text.pack . fst) (journalFiles journal))
            actualPaths = sort changes
            expectedBlobs = sort [(Text.pack (journalPath entry), journalBlob entry) | entry <- journalEntries journal]
            actualBlobs = sort treeBlobs
        whenEither (actualPaths == expectedPaths)
          "journaled commit tree does not contain exactly the expected record additions"
        whenEither (actualBlobs == expectedBlobs)
          "journaled commit tree contains a record with a different Git blob"

    -- One listing of every journaled path in the proposed commit, keyed by
    -- the exact path Git printed.
    treeEntries _ [] = pure (Right [])
    treeEntries target paths = do
      output <- runGit executable rootPath rootFd gitFd Nothing
        (["ls-tree", Text.unpack target, "--"] <> paths)
      pure $ do
        listing <- output
        mapM splitEntry (filter (not . Text.null) (Text.lines listing))
    splitEntry line = case Text.splitOn "\t" line of
      [metadata, listed] -> Right (listed, Text.words metadata)
      _ -> Left "journaled commit has a malformed tree entry"

    contentBlob (path, _) listing = case lookup (Text.pack path) listing of
      Just ["120000", "blob", blob] -> Right blob
      Just _ -> Left "journaled commit has an unexpected content tree entry"
      Nothing -> Left "journaled commit is missing a content tree entry"

    fileBlob (path, _) listing = case lookup (Text.pack path) listing of
      Just ["100644", "blob", blob] -> Right blob
      Just _ -> Left "journaled commit has an unexpected specification file entry"
      Nothing -> Left "journaled commit is missing a specification file entry"

    treeBlob entry listing = case lookup (Text.pack (journalPath entry)) listing of
      Just ["100644", "blob", blob] -> Right (Text.pack (journalPath entry), Just blob)
      Just _ -> Left "journaled commit has an unexpected record tree entry"
      Nothing -> Left "journaled commit is missing a record tree entry"

    syncCommittedState syncGitFd recordsFd commit branch entries = do
      baselineSync <- syncBaselineFrom executable rootPath rootFd syncGitFd
        (if provenBase == Just (journalOldHead journal) then provenBase else Nothing) commit
      case baselineSync of
        Left problem -> pure (Left problem)
        Right () -> do
          recordSync <- syncRecordDirectoryAndBarrier recordsFd entries
          case recordSync of
            Left problem -> pure (Left problem)
            Right () -> syncReferenceDirectories syncGitFd branch

    -- Hash every committed record file through a pinned descriptor in one
    -- Git process, and compare each with its journaled blob.
    verifyCommittedRecords recordsFd entries = do
      opened <- newIORef []
      result <- tryIO (do
          fds <- mapM (openCommitted opened recordsFd) entries
          blobs <- gitBlobsForFds executable rootPath rootFd gitFd fds >>= either failText pure
          mapM_ compareBlob (zip entries blobs))
        `Exception.finally` (readIORef opened >>= mapM_ closeFd)
      pure result

    openCommitted opened recordsFd entry = case validateRecordPath (journalPath entry) of
      Left problem -> failText problem
      Right filename -> do
        fd <- Exception.try (openRecordTargetForHash recordsFd filename)
          >>= either (\(problem :: Exception.IOException) -> failText
                ("committed record path is unavailable: " <> Text.pack (show problem))) pure
        modifyIORef opened (fd :)
        status <- getFdStatus fd
        unless (isRegularFile status) (failText "committed record path is not a regular file")
        pure fd

    compareBlob (entry, actual) = unless (Just actual == journalBlob entry) $
      failText ("committed record file differs from its journaled Git blob: " <> Text.pack (journalPath entry))

    parseDiffLine line = case Text.splitOn "\t" line of
      ["A", path] -> case findEntry path (journalEntries journal) of
        Just _ -> Right path
        Nothing
          | path `elem` map (Text.pack . fst) (journalContent journal) -> Right path
          | path /= Text.pack manifestFilePath
              && path `elem` map (Text.pack . fst) (journalFiles journal) -> Right path
          | otherwise -> Left "journaled commit contains an unexpected record path"
      ["M", path]
        | path == Text.pack manifestFilePath
            && path `elem` map (Text.pack . fst) (journalFiles journal) -> Right path
      _ -> Left "journaled commit contains a non-addition or malformed path change"

    findEntry _ [] = Nothing
    findEntry path (entry : rest)
      | Text.pack (journalPath entry) == path = Just entry
      | otherwise = findEntry path rest

journalToInstalled :: JournalEntry -> InstalledRecord
journalToInstalled entry = InstalledRecord
  { installedRecordPath = journalPath entry
  , installedRecordBlob = maybe "" id (journalBlob entry)
  , installedRecordDevice = maybe 0 id (journalDevice entry)
  , installedRecordFile = maybe 0 id (journalFile entry)
  }

whenEither :: Bool -> Text -> Either Text ()
whenEither True _ = Right ()
whenEither False problem = Left problem

createOperationJournal :: Fd -> OperationJournal -> IO (Either Text ())
createOperationJournal gitFd journal = case encodeOperationJournal journal of
  Left problem -> pure (Left problem)
  Right bytes -> do
    opened <- Exception.try $ openFdAt (Just gitFd) operationJournalName WriteOnly
      defaultFileFlags { creat = Just ownerFileModes, exclusive = True, nofollow = True, cloexec = True }
      :: IO (Either Exception.IOException Fd)
    case opened of
      Left problem -> pure (Left ("could not reserve Door operation journal: " <> Text.pack (show problem)))
      Right fd -> Exception.bracket (pure fd) closeFd $ \journalFd -> do
        written <- tryIO (writeAll journalFd bytes >> syncStrongFdOrFail journalFd >> syncDirectoryFdOrFail gitFd)
        pure (written >> Right ())

replaceOperationJournal :: Fd -> OperationJournal -> OperationJournal -> IO (Either Text ())
replaceOperationJournal gitFd expected replacement = do
  current <- readOperationJournal gitFd
  case current of
    Left problem -> pure (Left problem)
    Right Nothing -> pure (Left "Door operation journal disappeared during mutation")
    Right (Just (journal, _))
      | journal /= expected -> pure (Left "Door operation journal changed during mutation")
      | otherwise -> case encodeOperationJournal replacement of
          Left problem -> pure (Left problem)
          Right bytes -> do
            temporary <- freshJournalTemporaryName
            opened <- Exception.try $ openFdAt (Just gitFd) temporary WriteOnly
              defaultFileFlags { creat = Just ownerFileModes, exclusive = True, nofollow = True, cloexec = True }
              :: IO (Either Exception.IOException Fd)
            case opened of
              Left problem -> pure (Left ("could not create replacement operation journal: " <> Text.pack (show problem)))
              Right fd -> Exception.bracket (pure fd) closeFd $ \temporaryFd -> do
                written <- tryIO (writeAll temporaryFd bytes >> syncStrongFdOrFail temporaryFd)
                case written of
                  Left problem -> pure (Left problem)
                  Right () -> do
                    renamed <- tryIO (renameAt gitFd temporary operationJournalName)
                    case renamed of
                      Left problem -> pure (Left problem)
                      Right () -> do
                        synced <- syncFd gitFd
                        pure $ case synced of
                          Left problem -> Left ("could not sync operation journal directory: " <> problem)
                          Right () -> Right ()

clearJournalIfSame :: Fd -> JournalIdentity -> OperationJournal -> IO (Either Text ())
clearJournalIfSame gitFd identity expected = do
  current <- readOperationJournal gitFd
  case current of
    Left problem -> pure (Left problem)
    Right Nothing -> pure (Left "Door operation journal disappeared before cleanup")
    Right (Just (journal, observedIdentity))
      | journal /= expected -> pure (Left "Door operation journal changed before cleanup")
      | observedIdentity /= identity -> pure (Left "Door operation journal inode changed before cleanup")
      | otherwise -> do
          opened <- Exception.try $ openFdAt (Just gitFd) operationJournalName ReadOnly
            defaultFileFlags { nofollow = True, nonBlock = True, cloexec = True }
            :: IO (Either Exception.IOException Fd)
          case opened of
            Left problem -> pure (Left ("Door operation journal path changed before cleanup: " <> Text.pack (show problem)))
            Right fd -> Exception.bracket (pure fd) closeFd $ \journalFd -> do
              status <- tryIO (getFdStatus journalFd)
              case status of
                Left problem -> pure (Left problem)
                Right fileStatus
                  | JournalIdentity (deviceID fileStatus) (fileID fileStatus) /= identity ->
                      pure (Left "Door operation journal inode changed immediately before unlink")
                  | otherwise -> do
                      removed <- tryIO (unlinkAt gitFd operationJournalName)
                      case removed of
                        Left problem -> pure (Left problem)
                        Right () -> syncFd gitFd

readOperationJournal :: Fd -> IO (Either Text (Maybe (OperationJournal, JournalIdentity)))
readOperationJournal gitFd = do
  opened <- Exception.try $ openFdAt (Just gitFd) operationJournalName ReadOnly
    defaultFileFlags { nofollow = True, nonBlock = True, cloexec = True }
    :: IO (Either Exception.IOException Fd)
  case opened of
    Left problem
      | isDoesNotExistError problem -> pure (Right Nothing)
      | otherwise -> pure (Left ("could not open Door operation journal: " <> Text.pack (show problem)))
    Right fd -> Exception.bracket (pure fd) closeFd $ \journalFd -> do
      result <- tryIO $ do
        status <- getFdStatus journalFd
        unless (isRegularFile status) (failText "Door operation journal is not regular")
        bytes <- withDuplicatedBinaryHandle journalFd readBoundedJournal
          >>= either failText pure
        pure (status, bytes)
      pure $ do
        (status, bytes) <- result
        journal <- parseOperationJournal bytes
        Right (Just (journal, JournalIdentity (deviceID status) (fileID status)))

withDuplicatedBinaryHandle :: Fd -> (Handle -> IO a) -> IO a
withDuplicatedBinaryHandle fd action = Exception.bracket acquire hClose $ \handle -> do
  hSetBinaryMode handle True
  action handle
  where
    acquire = do
      duplicate <- dup fd
      fdToHandle duplicate `Exception.onException` closeFd duplicate

readBoundedJournal :: Handle -> IO (Either Text ByteString.ByteString)
readBoundedJournal handle = collect [] 0
  where
    collect chunks total = do
      let remaining = operationJournalByteLimit + 1 - total
      chunk <- ByteString.hGetSome handle (min 32768 remaining)
      if ByteString.null chunk
        then pure (Right (ByteString.concat (reverse chunks)))
        else
          let nextTotal = total + ByteString.length chunk
          in if nextTotal > operationJournalByteLimit
            then pure (Left "Door operation journal exceeds the size limit")
            else collect (chunk : chunks) nextTotal

encodeOperationJournal :: OperationJournal -> Either Text ByteString.ByteString
encodeOperationJournal journal = do
  validateJournal journal
  let header =
        [ "archive-door-operation\t1"
        , "old\t" <> journalOldHead journal
        , "branch\t" <> journalBranch journal
        , "phase\t" <> journalPhase journal
        , "target\t" <> maybe "-" id (journalTargetCommit journal)
        , "index\t" <> Text.pack (journalIndexName (journalIndex journal))
          <> "\t" <> Text.pack (show (journalIndexDevice (journalIndex journal)))
          <> "\t" <> Text.pack (show (journalIndexFile (journalIndex journal)))
        ]
      entryLines = map encodeEntry (journalEntries journal)
        <> [ "content\t" <> Text.pack path <> "\t" <> blob | (path, blob) <- journalContent journal ]
        <> [ "file\t" <> Text.pack path <> "\t" <> blob | (path, blob) <- journalFiles journal ]
      encodeEntry entry = case (journalBlob entry, journalDevice entry, journalFile entry) of
        (Nothing, Nothing, Nothing) -> "entry-preparing\t" <> Text.pack (journalPath entry)
          <> "\t" <> Text.pack (journalTemporary entry)
        (Just blob, Just device, Just inode) -> "entry-ready\t" <> Text.pack (journalPath entry)
          <> "\t" <> blob <> "\t" <> Text.pack (show device) <> "\t"
          <> Text.pack (show inode) <> "\t" <> Text.pack (journalTemporary entry)
        _ -> "entry-invalid"
      body = Text.intercalate "\n" (header <> entryLines) <> "\n"
  let bytes = TextEncoding.encodeUtf8 body
  unlessEither (ByteString.length bytes <= operationJournalByteLimit)
    "Door operation journal exceeds the size limit"
  pure bytes

parseOperationJournal :: ByteString.ByteString -> Either Text OperationJournal
parseOperationJournal bytes = do
  decoded <- case TextEncoding.decodeUtf8' bytes of
    Left _ -> Left "journal is not UTF-8"
    Right value -> Right value
  unlessEither (Text.isSuffixOf "\n" decoded) "journal is missing its final newline"
  journal <- case Text.lines decoded of
    "archive-door-operation\t1" : oldLine : branchLine : phaseLine : targetLine : indexLine : entryLines -> do
      oldHead <- field "old" oldLine
      branch <- field "branch" branchLine
      phase <- field "phase" phaseLine
      targetText <- field "target" targetLine
      target <- if targetText == "-" then Right Nothing else Right (Just targetText)
      index <- case Text.splitOn "\t" indexLine of
        ["index", name, deviceText, inodeText] -> do
          device <- maybe (Left "journal index device identifier is invalid") Right (readMaybe (Text.unpack deviceText))
          inode <- maybe (Left "journal index inode identifier is invalid") Right (readMaybe (Text.unpack inodeText))
          Right (JournalIndex (Text.unpack name) device inode)
        _ -> Left "journal index ownership is malformed"
      let (recordLines, linkLines) = break (\line -> "content\t" `Text.isPrefixOf` line
            || "file\t" `Text.isPrefixOf` line) entryLines
          (contentLines, fileLines) = break ("file\t" `Text.isPrefixOf`) linkLines
      entries <- traverse parseEntry recordLines
      content <- traverse parseContent contentLines
      files <- traverse parseFile fileLines
      Right (OperationJournal oldHead branch phase target index entries content files)
    _ -> Left "journal header or field order is invalid"
  validateJournal journal
  case encodeOperationJournal journal of
    Left problem -> Left problem
    Right canonical | canonical /= bytes -> Left "journal encoding is not canonical"
    Right _ -> Right journal
  where
    field name line = case Text.splitOn "\t" line of
      [actual, value] | actual == name -> Right value
      _ -> Left ("journal field is malformed: " <> name)
    parseEntry line = case Text.splitOn "\t" line of
      ["entry-preparing", path, temporary] -> Right (JournalEntry
        (Text.unpack path) (Text.unpack temporary) Nothing Nothing Nothing)
      ["entry-ready", path, blob, deviceText, inodeText, temporary] -> do
        device <- maybe (Left "journal device identifier is invalid") Right (readMaybe (Text.unpack deviceText))
        inode <- maybe (Left "journal inode identifier is invalid") Right (readMaybe (Text.unpack inodeText))
        Right (JournalEntry (Text.unpack path) (Text.unpack temporary) (Just blob) (Just device) (Just inode))
      _ -> Left "journal record entry is malformed"
    parseContent line = case Text.splitOn "\t" line of
      ["content", path, blob] -> Right (Text.unpack path, blob)
      _ -> Left "journal content entry is malformed"
    parseFile line = case Text.splitOn "\t" line of
      ["file", path, blob] -> Right (Text.unpack path, blob)
      _ -> Left "journal specification file entry is malformed"

validateJournal :: OperationJournal -> Either Text ()
validateJournal journal = do
  unlessEither (validObjectId (journalOldHead journal)) "journal old HEAD is invalid"
  unlessEither ("refs/heads/" `Text.isPrefixOf` journalBranch journal
    && not (Text.any (`elem` ['\n', '\r', '\t', '\0']) (journalBranch journal)))
    "journal branch is invalid"
  unlessEither (journalPhase journal `elem` ["preparing", "installing"])
    "journal phase is unsupported"
  case journalTargetCommit journal of
    Nothing -> Right ()
    Just target -> unlessEither (validObjectId target && journalPhase journal == "installing")
      "journal target commit is invalid"
  unlessEither (isIndexReservationName (journalIndexName (journalIndex journal)))
    "journal index name is invalid"
  let entries = journalEntries journal
      paths = map journalPath entries
      temporaries = map journalTemporary entries
  unlessEither (not (null entries)) "journal has no record entries"
  unlessEither (length paths == length (uniqueSorted paths)) "journal has duplicate record paths"
  unlessEither (length temporaries == length (uniqueSorted temporaries)) "journal has duplicate temporary names"
  mapM_ validateEntry entries
  let contentPaths = map fst (journalContent journal)
  unlessEither (length contentPaths == length (uniqueSorted contentPaths)) "journal has duplicate content paths"
  mapM_ validateContent (journalContent journal)
  let filePaths = map fst (journalFiles journal)
  unlessEither (length filePaths == length (uniqueSorted filePaths)) "journal has duplicate specification file paths"
  mapM_ validateFile (journalFiles journal)
  where
    validateFile (path, blob) = do
      validateArchiveFilePath path
      unlessEither (validObjectId blob) "journal specification file blob identifier is invalid"
    validateContent (path, blob) = do
      _ <- validateContentPath path
      unlessEither (validObjectId blob) "journal content blob identifier is invalid"
    validateEntry entry = do
      _ <- validateRecordPath (journalPath entry)
      unlessEither (isRecordTemporaryName (journalTemporary entry)) "journal temporary name is invalid"
      case (journalPhase journal, journalBlob entry, journalDevice entry, journalFile entry) of
        ("preparing", Nothing, Nothing, Nothing) -> Right ()
        ("installing", Just blob, Just _, Just _) -> unlessEither (validObjectId blob) "journal blob identifier is invalid"
        _ -> Left "journal phase and record ownership fields disagree"

validObjectId :: Text -> Bool
validObjectId value = Text.length value `elem` [40, 64]
  && Text.all (\character -> ('0' <= character && character <= '9')
        || ('a' <= character && character <= 'f')) value

isRecordTemporaryName :: FilePath -> Bool
isRecordTemporaryName value = ".archive-door-record-" `prefixOf` value
  && let suffix = drop (length (".archive-door-record-" :: String)) value
     in not (null suffix) && all (\character -> character >= '0' && character <= '9'
          || character >= 'a' && character <= 'z' || character == '-') suffix
  && '/' `notElem` value
  where
    prefixOf prefix text = take (length prefix) text == prefix

isIndexReservationName :: FilePath -> Bool
isIndexReservationName value =
  "archive-door-index-" `prefixOf` value
    && let suffix = drop (length ("archive-door-index-" :: String)) value
       in not (null suffix) && all (\character -> character >= '0' && character <= '9'
            || character >= 'a' && character <= 'z' || character == '-') suffix
    && '/' `notElem` value
  where
    prefixOf prefix text = take (length prefix) text == prefix

uniqueSorted :: Ord a => [a] -> [a]
uniqueSorted = foldr keep [] . sort
  where
    keep value [] = [value]
    keep value values@(next : _)
      | value == next = values
      | otherwise = value : values

unlessEither :: Bool -> Text -> Either Text ()
unlessEither True _ = Right ()
unlessEither False problem = Left problem

freshJournalTemporaryName :: IO FilePath
freshJournalTemporaryName = do
  processId <- getProcessID
  unique <- newUnique
  pure (".archive-door-journal-" <> show processId <> "-" <> show (hashUnique unique))

renameAt :: Fd -> FilePath -> FilePath -> IO ()
renameAt directoryFd source target =
  withCString source $ \sourceCString -> withCString target $ \targetCString ->
    throwErrnoIfMinus1_ "renameat" $ c_renameat (fdValue directoryFd) sourceCString
      (fdValue directoryFd) targetCString

syncFd :: Fd -> IO (Either Text ())
syncFd fd = tryIO (syncDirectoryFdOrFail fd)

syncDirectoryFdOrFail :: Fd -> IO ()
syncDirectoryFdOrFail (Fd descriptor) =
  throwErrnoIfMinus1_ "archive full fsync directory" (c_fullsyncDirectory descriptor)

syncStrongFdOrFail :: Fd -> IO ()
syncStrongFdOrFail (Fd descriptor) =
  throwErrnoIfMinus1_ "archive full fsync" (c_fullsync descriptor)

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

    hexText digest = Text.pack (concatMap byteHex (ByteString.unpack digest))
    byteHex byte = [hexDigit (byte `div` 16), hexDigit (byte `mod` 16)]
    hexDigit value = "0123456789abcdef" !! fromIntegral value

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

syncRecordDirectoryAndBarrier :: Fd -> [JournalEntry] -> IO (Either Text ())
syncRecordDirectoryAndBarrier recordsFd entries = do
  result <- tryIO $ do
    mapM_ syncEntry entries
    -- Every fan-out directory on a record's path, deepest first, then
    -- records/ itself: each holds an entry this batch may have added.
    locations <- either failText pure (mapM (validateRecordPath . journalPath) entries)
    let chains = uniqueSorted (concat [ drop 1 (inits (recordLocationDirectories location)) | location <- locations ])
    mapM_ (\chain -> withDirectoryChain recordsFd chain False syncDirectoryFdOrFail)
      (sortOn (negate . length) chains)
    syncDirectoryFdOrFail recordsFd
  pure $ case result of
    Left problem -> Left ("could not sync installed record files and directory: " <> problem)
    Right () -> Right ()
  where
    syncEntry entry = case validateRecordPath (journalPath entry) of
      Left problem -> failText problem
      Right filename -> do
        fd <- withDirectoryChain recordsFd (recordLocationDirectories filename) False $ \directoryFd ->
          openFdAt (Just directoryFd) (recordLocationFile filename) WriteOnly
            defaultFileFlags { nofollow = True, nonBlock = True, cloexec = True }
        Exception.bracket (pure fd) closeFd $ \recordFd -> do
          status <- getFdStatus recordFd
          unless (isRegularFile status) (failText "installed record is not a regular file")
          case (journalDevice entry, journalFile entry) of
            (Just device, Just inode)
              | deviceID status == device && fileID status == inode -> pure ()
              | otherwise -> failText "installed record identity changed before full fsync"
            _ -> failText "installed record ownership is incomplete before full fsync"
          syncStrongFdOrFail recordFd

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

openRegularForSync :: Fd -> [FilePath] -> IO Fd
openRegularForSync base components = do
  when (null components) (failText "cannot sync an empty Git file path")
  parentFd <- openDirectoryChain base (init components)
  Exception.bracket (pure parentFd) closeFd $ \parent -> do
    fd <- openFdAt (Just parent) (last components) WriteOnly
      defaultFileFlags { nofollow = True, nonBlock = True, cloexec = True }
    status <- getFdStatus fd `Exception.onException` closeFd fd
    unless (isRegularFile status) (closeFd fd >> failText "Git sync target is not a regular file")
    pure fd

syncRegularRelative :: Fd -> [FilePath] -> IO ()
syncRegularRelative base components = do
  fd <- openRegularForSync base components
  Exception.bracket (pure fd) closeFd syncStrongFdOrFail

openDirectoryAt :: Fd -> FilePath -> IO Fd
openDirectoryAt parent component = openFdAt (Just parent) component ReadOnly
  defaultFileFlags { directory = True, nofollow = True, cloexec = True }

openDirectoryChain :: Fd -> [FilePath] -> IO Fd
openDirectoryChain base [] = dup base
openDirectoryChain base [component] = openDirectoryAt base component
openDirectoryChain base (component : rest) =
  Exception.bracket (openDirectoryAt base component) closeFd $ \child ->
    openDirectoryChain child rest

syncDirectoryChain :: Fd -> [FilePath] -> IO ()
syncDirectoryChain current [] = syncDirectoryFdOrFail current
syncDirectoryChain parent (component : rest) = do
  child <- openDirectoryAt parent component
  Exception.bracket (pure child) closeFd $ \opened -> do
    syncDirectoryChain opened rest
    syncDirectoryFdOrFail opened
    syncDirectoryFdOrFail parent

cleanupPrepared :: Fd -> [PreparedRecord] -> IO (Either Text ())
cleanupPrepared gitFd prepared = do
  results <- mapM removeOne prepared
  case sequence_ results of
    Left problem -> pure (Left problem)
    Right () -> syncFd gitFd
  where
    removeOne record = do
      opened <- Exception.try $ openFdAt (Just gitFd) (preparedRecordTemporary record) ReadOnly
        defaultFileFlags { nofollow = True, nonBlock = True, cloexec = True }
        :: IO (Either Exception.IOException Fd)
      case opened of
        Left problem
          | isDoesNotExistError problem -> pure (Right ())
          | otherwise -> pure (Left (Text.pack (show problem)))
        Right fd -> Exception.bracket (pure fd) closeFd $ \temporaryFd -> do
          status <- tryIO (getFdStatus temporaryFd)
          case status of
            Left problem -> pure (Left problem)
            Right fileStatus
              | not (isRegularFile fileStatus) -> pure (Left "Door record temporary is not regular")
              | deviceID fileStatus /= preparedRecordDevice record
                  || fileID fileStatus /= preparedRecordFile record ->
                    pure (Left "Door record temporary inode changed before cleanup")
              | otherwise -> do
                  stillCurrent <- Exception.try $ openFdAt (Just gitFd) (preparedRecordTemporary record)
                    ReadOnly defaultFileFlags { nofollow = True, nonBlock = True, cloexec = True }
                    :: IO (Either Exception.IOException Fd)
                  case stillCurrent of
                    Left problem -> pure (Left ("Door record temporary path changed before cleanup: " <> Text.pack (show problem)))
                    Right currentFd -> Exception.bracket (pure currentFd) closeFd $ \_ -> do
                      currentStatus <- tryIO (getFdStatus currentFd)
                      case currentStatus of
                        Left problem -> pure (Left problem)
                        Right current
                          | deviceID current /= preparedRecordDevice record
                              || fileID current /= preparedRecordFile record ->
                                pure (Left "Door record temporary inode changed before unlink")
                          | otherwise -> tryIO (unlinkAt gitFd (preparedRecordTemporary record))

removePreparingTemporary :: Fd -> FilePath -> IO (Either Text ())
removePreparingTemporary gitFd name = do
  opened <- Exception.try $ openFdAt (Just gitFd) name ReadOnly
    defaultFileFlags { nofollow = True, nonBlock = True, cloexec = True }
    :: IO (Either Exception.IOException Fd)
  case opened of
    Left problem
      | isDoesNotExistError problem -> pure (Right ())
      | otherwise -> pure (Left (Text.pack (show problem)))
    Right fd -> do
      closeFd fd
      pure (Left ("temporary file exists without a recorded inode; operation journal requires operator inspection: " <> Text.pack name))

cleanupUninstalledTemporary :: Fd -> Fd -> FilePath -> FileStatus -> IO (Either Text ())
cleanupUninstalledTemporary gitFd ownedFd name ownedStatus = Exception.finally remove closeFdOwned
  where
    ownedIdentity = (deviceID ownedStatus, fileID ownedStatus)
    closeFdOwned = closeFd ownedFd
    remove = do
      checked <- tryIO $ do
        opened <- openFdAt (Just gitFd) name ReadOnly
          defaultFileFlags { nofollow = True, nonBlock = True, cloexec = True }
        Exception.bracket (pure opened) closeFd $ \fd -> do
          before <- getFdStatus fd
          unless (isRegularFile before && (deviceID before, fileID before) == ownedIdentity) $
            failText "record staging path no longer names its owned inode"
          current <- openFdAt (Just gitFd) name ReadOnly
            defaultFileFlags { nofollow = True, nonBlock = True, cloexec = True }
          Exception.bracket (pure current) closeFd $ \currentFd -> do
            after <- getFdStatus currentFd
            unless (isRegularFile after && (deviceID after, fileID after) == ownedIdentity) $
              failText "record staging path changed before cleanup"
            unlinkAt gitFd name
            syncDirectoryFdOrFail gitFd
      pure checked

discardAfterFailure :: IO (Either Text ()) -> IO ()
discardAfterFailure cleanup = do
  _ <- cleanup
  pure ()

cleanupJournalIndex :: Fd -> JournalIndex -> IO (Either Text ())
cleanupJournalIndex gitFd entry = do
  opened <- Exception.try $ openFdAt (Just gitFd) (journalIndexName entry) ReadOnly
    defaultFileFlags { nofollow = True, nonBlock = True, cloexec = True }
    :: IO (Either Exception.IOException Fd)
  case opened of
    Left problem
      | isDoesNotExistError problem -> pure (Right ())
      | otherwise -> pure (Left ("could not inspect reserved Git index: " <> Text.pack (show problem)))
    Right fd -> Exception.bracket (pure fd) closeFd $ \indexFd -> do
      status <- tryIO (getFdStatus indexFd)
      case status of
        Left problem -> pure (Left problem)
        Right fileStatus
          | not (isRegularFile fileStatus) -> pure (Left "reserved Git index is not regular")
          | deviceID fileStatus /= journalIndexDevice entry
              || fileID fileStatus /= journalIndexFile entry ->
                pure (Left "reserved Git index inode changed before recovery")
          | otherwise -> do
              current <- Exception.try $ openFdAt (Just gitFd) (journalIndexName entry) ReadOnly
                defaultFileFlags { nofollow = True, nonBlock = True, cloexec = True }
                :: IO (Either Exception.IOException Fd)
              case current of
                Left problem -> pure (Left ("reserved Git index path changed before recovery: " <> Text.pack (show problem)))
                Right currentFd -> Exception.bracket (pure currentFd) closeFd $ \_ -> do
                  currentStatus <- tryIO (getFdStatus currentFd)
                  case currentStatus of
                    Left problem -> pure (Left problem)
                    Right now
                      | deviceID now /= journalIndexDevice entry || fileID now /= journalIndexFile entry ->
                          pure (Left "reserved Git index inode changed immediately before unlink")
                      | otherwise -> do
                          removed <- tryIO (unlinkAt gitFd (journalIndexName entry))
                          case removed of
                            Left problem -> pure (Left problem)
                            Right () -> syncFd gitFd

cleanupJournalTemporary :: Executable -> FilePath -> Fd -> Fd -> JournalEntry
                        -> IO (Either Text ())
cleanupJournalTemporary executable rootPath rootFd gitFd entry = do
  opened <- Exception.try $ openFdAt (Just gitFd) (journalTemporary entry) ReadOnly
    defaultFileFlags { nofollow = True, nonBlock = True, cloexec = False }
    :: IO (Either Exception.IOException Fd)
  case opened of
    Left problem
      | isDoesNotExistError problem -> pure (Right ())
      | otherwise -> pure (Left (Text.pack (show problem)))
    Right fd -> do
      setFdOption fd CloseOnExec False
      Exception.bracket (pure fd) closeFd $ \temporaryFd -> do
        status <- tryIO (getFdStatus temporaryFd)
        case status of
          Left problem -> pure (Left problem)
          Right fileStatus -> case (journalBlob entry, journalDevice entry, journalFile entry) of
            (Just expectedBlob, Just expectedDevice, Just expectedFile)
              | isRegularFile fileStatus
                  && deviceID fileStatus == expectedDevice
                  && fileID fileStatus == expectedFile -> do
                    blob <- gitBlobForFd executable rootPath rootFd gitFd temporaryFd
                    case blob of
                      Left problem -> pure (Left problem)
                      Right actual
                        | Text.strip actual /= expectedBlob -> pure (Left "record temporary blob changed before cleanup")
                        | otherwise -> do
                            current <- Exception.try $ openFdAt (Just gitFd) (journalTemporary entry) ReadOnly
                              defaultFileFlags { nofollow = True, nonBlock = True, cloexec = True }
                              :: IO (Either Exception.IOException Fd)
                            case current of
                              Left problem -> pure (Left ("record temporary path changed before cleanup: " <> Text.pack (show problem)))
                              Right currentFd -> Exception.bracket (pure currentFd) closeFd $ \_ -> do
                                currentStatus <- tryIO (getFdStatus currentFd)
                                case currentStatus of
                                  Left problem -> pure (Left problem)
                                  Right now
                                    | deviceID now /= expectedDevice || fileID now /= expectedFile ->
                                        pure (Left "record temporary inode changed before unlink")
                                    | otherwise -> tryIO (unlinkAt gitFd (journalTemporary entry))
            _ -> pure (Left ("temporary ownership is incomplete; operation journal retained: " <> Text.pack (journalTemporary entry)))

-- | Install and commit one exact batch of new record files.
--
-- Paths are relative to the archive root.  A temporary Git index constructs a
-- tree with exactly these additions; update-ref then proves HEAD still equals
-- the snapshot validated by the caller.
-- | Record that a commit Door just acknowledged on top of a verified base is
-- itself verified (ADR 0052, ADR 0054): the base's verification and this
-- batch's own checks together cover it. The checkpoint is operational, so a
-- failure to write it is only a warning; the result is the length of the
-- chain now on disk, when one was written.
writeCommitCheckpoint :: ArchiveWriteLock -> CommitVerification -> Text
                      -> [(FilePath, LazyByteString.ByteString)] -> IO (Maybe Int)
writeCommitCheckpoint (ArchiveWriteLock rootPath _ gitFd _) verified commit records = do
  written <- case traverse (\(path, bytes) -> (,) path <$> summarizeStoredBytes bytes) records of
    Left problem -> pure (Left problem)
    Right summaries -> appendCheckpoint gitFd (rootPath <> "/.git") (verifiedBase verified) commit summaries
  case written of
    Left problem -> do
      hPutStrLn stderr ("door: warning: verification checkpoint was not written: " <> Text.unpack problem)
      pure Nothing
    Right chain -> pure (Just chain)

-- | Record an upgrade commit Door made on top of a verified commit as a new
-- base checkpoint (ADR 0052). A per-commit addition lists only records
-- (ADR 0054); this commit also adds specification paths, so the base names
-- them as added by this commit. The manifest stays named by the commit that
-- added it: an upgrade replaces it in place.
writeCommitBaseCheckpoint :: ArchiveWriteLock -> CommitVerification -> Text
                          -> [(FilePath, LazyByteString.ByteString)] -> [FilePath] -> IO (Maybe Int)
writeCommitBaseCheckpoint (ArchiveWriteLock rootPath _ gitFd _) verified commit records paths = do
  written <- case traverse (\(path, bytes) -> (,) path <$> summarizeStoredBytes bytes) records of
    Left problem -> pure (Left problem)
    Right summaries -> do
      let extended = extendCheckpoint (verifiedBase verified) commit summaries
          added = foldr (\path -> Map.insert path commit) (checkpointAdditions extended)
            (filter (/= manifestFilePath) paths)
      writeCheckpoint gitFd (rootPath <> "/.git") extended { checkpointAdditions = added }
  case written of
    Left problem -> do
      hPutStrLn stderr ("door: warning: verification checkpoint was not written: " <> Text.unpack problem)
      pure Nothing
    Right () -> pure (Just 0)

-- | What the caller verified about the expected head before committing on top
-- of it (ADR 0052). 'verifiedObjectsProven' is true only when a checkpoint,
-- not just a fresh read of the records, vouches for the objects beneath it.
data CommitVerification = CommitVerification
  { verifiedBase :: VerifiedCheckpoint
  , verifiedObjectsProven :: Bool
  }

commitRecordBatch
  :: ArchiveWriteLock
  -> Text
  -> Text
  -> [(FilePath, LazyByteString.ByteString)]
  -> IO (Either MutationError ())
  -> IO (Either MutationError Text)
commitRecordBatch lock expectedHead message records =
  commitRecordBatchWithContent lock expectedHead message records [] Nothing

-- | A git-annex location for content already present and verified locally.
-- The commit stores it as the symlink git-annex itself would create; Door
-- never reads the bytes it names.
data ContentLink = ContentLink
  { contentLinkPath :: FilePath
  , contentLinkTarget :: FilePath
  } deriving (Eq, Show)

-- | 'commitRecordBatch', plus git-annex symlink entries for admitted content.
--
-- The symlink entries are added only to the temporary index, so a failure
-- before the ref update leaves at most an unreferenced Git blob. A content
-- path already committed with the same link is shared, not re-added; one
-- with a different entry is refused. Worktree symlinks are created after the
-- ref update: they are locations, regenerable from the commit (D-1).
commitRecordBatchWithContent
  :: ArchiveWriteLock
  -> Text
  -> Text
  -> [(FilePath, LazyByteString.ByteString)]
  -> [ContentLink]
  -> Maybe CommitVerification
  -> IO (Either MutationError ())
  -> IO (Either MutationError Text)
commitRecordBatchWithContent lock expectedHead message records contentLinks =
  commitRecordBatchWithFiles lock expectedHead message records contentLinks []

-- | A file outside @records/@ that a specification upgrade writes in the same
-- commit as its record (ADR 0068): the replaced manifest, or a document or
-- bundle of the new release. Nothing else may be written this way.
data ArchiveFile = ArchiveFile
  { archiveFilePath :: FilePath
  , archiveFileBytes :: LazyByteString.ByteString
  } deriving (Eq, Show)

manifestFilePath :: FilePath
manifestFilePath = "archive.json"

-- | The manifest, one of a release's three documents under
-- @spec/<version>/@, or a bundle under its digest path.
validateArchiveFilePath :: FilePath -> Either Text ()
validateArchiveFilePath path = case splitDirectories path of
  ["archive.json"] | path == manifestFilePath -> Right ()
  ["spec", version, name]
    | name `elem` ["VERSION", "archive-spec.md", "context.jsonld"]
    , path == "spec/" <> version <> "/" <> name -> () <$ specVersion ("archive/" <> Text.pack version)
  ["contracts", name]
    | path == "contracts/" <> name
    , Just hex <- Text.stripSuffix ".json" =<< Text.stripPrefix "sha256-" (Text.pack name)
    , Text.length hex == 64
    , Text.all (\character -> ('0' <= character && character <= '9') || ('a' <= character && character <= 'f')) hex -> Right ()
  _ -> Left ("not a specification file path: " <> Text.pack path)

-- | 'commitRecordBatchWithContent', plus specification files. The manifest
-- must already be committed and is replaced; every other file must be new.
-- Like content links, the files enter only the temporary index before the
-- ref update; their worktree copies are written from the commit when the
-- operation journal is reconciled after it.
commitRecordBatchWithFiles
  :: ArchiveWriteLock
  -> Text
  -> Text
  -> [(FilePath, LazyByteString.ByteString)]
  -> [ContentLink]
  -> [ArchiveFile]
  -> Maybe CommitVerification
  -> IO (Either MutationError ())
  -> IO (Either MutationError Text)
commitRecordBatchWithFiles (ArchiveWriteLock rootPath rootFd gitFd executable) expectedHead message records contentLinks archiveFiles verification finalCheck =
  case ( traverse (validateRecordPath . fst) records
       , traverse validateContentLink contentLinks
       , traverse (validateArchiveFilePath . archiveFilePath) archiveFiles ) of
    (Left problem, _, _) -> pure (Left (MutationRejected problem))
    (_, Left problem, _) -> pure (Left (MutationRejected problem))
    (_, _, Left problem) -> pure (Left (MutationRejected problem))
    _
      | length (uniqueSorted (map archiveFilePath archiveFiles)) /= length archiveFiles ->
          pure (Left (MutationRejected "a specification file is named twice"))
      | otherwise -> commitAt
  where
    commitAt = Exception.mask $ \restore -> do
      pinnedRecordsRef <- newIORef Nothing
      explicitFailureRef <- newIORef Nothing
      result <- restore (runCommit pinnedRecordsRef explicitFailureRef)
        `Exception.onException` closePinnedRecords pinnedRecordsRef
      pinnedRecords <- readIORef pinnedRecordsRef
      recovered <- case pinnedRecords of
        Nothing -> reconcileOperationJournal executable rootPath rootFd gitFd
        Just fd -> Exception.bracket_ (pure ()) (closeFd fd)
          (reconcileOperationJournalWithRecords executable rootPath rootFd gitFd (Just fd) provenBase)
      explicitFailure <- readIORef explicitFailureRef
      let resultError problem = maybe (MutationOperationFailure problem) id explicitFailure
      case (result, recovered) of
        (Left problem, Left cleanupProblem) -> pure (Left
          (MutationCleanupFailure (resultError problem) (MutationOperationFailure cleanupProblem)))
        (Left problem, Right ()) -> pure (Left (resultError problem))
        (Right _, Left problem) -> pure (Left (MutationUncertain
          ("archive commit succeeded but recovery could not finish: " <> problem)))
        (Right commit, Right ()) -> pure (Right commit)

    provenBase = case verification of
      Just verified
        | verifiedObjectsProven verified
            && checkpointCommit (verifiedBase verified) == expectedHead -> Just expectedHead
      _ -> Nothing

    runCommit pinnedRecordsRef explicitFailureRef = do
        prepared <- prepareCommit
        case prepared of
          Left problem -> pure (Left problem)
          Right (branch, newLinks, newFiles) ->
            withNewIndexReservation gitFd $ \reservation -> tryIO $ do
              checked <- finalCheck
              case checked of
                Left problem -> do
                  writeIORef explicitFailureRef (Just problem)
                  pure (Left (mutationErrorMessage problem))
                Right () -> withRecordsDirectory rootFd $ \recordsFd -> do
                  Exception.mask_ $ do
                    duplicate <- dup recordsFd
                    writeIORef pinnedRecordsRef (Just duplicate)
                  temporaryNames <- mapM (const freshTemporaryName) records
                  (indexName, indexIdentity) <- case reservation of
                    IndexReservation name identityRef _ -> do
                      (_, identity) <- readIORef identityRef
                      pure (name, identity)
                  let indexJournal = JournalIndex indexName (fst indexIdentity) (snd indexIdentity)
                      preparing = OperationJournal expectedHead branch "preparing" Nothing indexJournal
                        (zipWith (\(path, _) name -> JournalEntry path name Nothing Nothing Nothing)
                          records temporaryNames)
                        newLinks
                        newFiles
                  journalState <- newIORef preparing
                  case reservation of
                    IndexReservation _ _ writerRef -> writeIORef writerRef (Just journalState)
                  reserved <- createOperationJournal gitFd preparing
                  case reserved of
                    Left problem -> do
                      case reservation of
                        IndexReservation _ _ writerRef -> writeIORef writerRef Nothing
                      pure (Left problem)
                    Right () -> do
                      staged <- prepareAll executable rootPath rootFd gitFd
                        (zip temporaryNames records)
                      case staged of
                        Left problem -> pure (Left problem)
                        Right preparedRecords -> Exception.bracket_
                          (pure ()) (closePrepared preparedRecords) $ do
                            let installing = preparing
                                  { journalPhase = "installing"
                                  , journalEntries = map preparedToJournal preparedRecords
                                  }
                            journaled <- replaceOperationJournal gitFd preparing installing
                            case journaled of
                              Left problem -> do
                                cleaned <- cleanupPrepared gitFd preparedRecords
                                pure $ case cleaned of
                                  Left cleanupProblem -> Left
                                    (problem <> "; staged source cleanup also failed: " <> cleanupProblem)
                                  Right () -> Left problem
                              Right () -> do
                                writeIORef journalState installing
                                installed <- installPreparedAll executable rootPath rootFd gitFd
                                  recordsFd preparedRecords
                                case installed of
                                  Left problem -> pure (Left problem)
                                  Right ownedRecords -> do
                                    journal <- readIORef journalState
                                    synced <- syncRecordDirectoryAndBarrier recordsFd
                                      (journalEntries journal)
                                    case synced of
                                      Left problem -> pure (Left problem)
                                      Right () -> do
                                        committed <- buildAndAdvance recordsFd branch reservation
                                          ownedRecords newLinks newFiles journalState explicitFailureRef
                                        case committed of
                                          Left problem -> pure (Left problem)
                                          Right commit -> do
                                            temps <- cleanupPrepared gitFd preparedRecords
                                            case temps of
                                              Left problem -> pure (Left
                                                ("archive commit succeeded; operation journal retained because staged source cleanup failed: " <> problem))
                                              -- The ordinary index follows the commit when
                                              -- the journal is reconciled below: the one path
                                              -- a killed process's successor also takes.
                                              Right () -> do
                                                installWorktreeLinks
                                                pure (Right commit)

    closePinnedRecords reference = do
      pinned <- readIORef reference
      writeIORef reference Nothing
      maybe (pure ()) closeFd pinned

    prepareCommit = do
      objectStoreTopology <- validateGitObjectStoreTopology executable rootPath rootFd gitFd
      targetsClear <- mapM (targetAbsent rootFd . fst) records
      case sequence targetsClear of
        Left problem -> pure (Left problem)
        Right _ -> do
          refFormatResult <- runGit executable rootPath rootFd gitFd Nothing
            ["rev-parse", "--show-ref-format"]
          headResult <- runGit executable rootPath rootFd gitFd Nothing ["rev-parse", "--verify", "HEAD^{commit}"]
          stagedResult <- runGitStatus executable rootPath rootFd gitFd Nothing
            ["diff", "--cached", "--quiet", Text.unpack expectedHead, "--"]
          branchResult <- runGit executable rootPath rootFd gitFd Nothing ["symbolic-ref", "-q", "HEAD"]
          checked <- pure $ do
            either Left Right objectStoreTopology
            refFormat <- Text.strip <$> refFormatResult
            if refFormat == "files"
              then Right ()
              else Left "Door durability barrier currently requires Git's files ref backend"
            headValue <- Text.strip <$> headResult
            if headValue == expectedHead
              then Right ()
              else Left "archive HEAD changed after validation"
            staged <- stagedResult
            if staged == ExitSuccess
              then Right ()
              else Left "archive index has staged changes"
            branch <- Text.strip <$> branchResult
            if Text.isPrefixOf "refs/heads/" branch
              then Right ()
              else Left "archive HEAD is detached"
            Right branch
          case checked of
            Left problem -> pure (Left problem)
            Right branch -> do
              classified <- mapM classifyLink contentLinks
              files <- mapM classifyFile archiveFiles
              pure $ case (sequence classified, sequence files) of
                (Left problem, _) -> Left problem
                (_, Left problem) -> Left problem
                (Right links, Right fileBlobs) -> Right (branch, [link | Just link <- links], fileBlobs)

    -- A link already committed at this exact path with the same target is
    -- content another artifact admitted earlier; the tree keeps it unchanged.
    classifyLink link = do
      blob <- gitBlobForBytes executable rootPath rootFd gitFd
        (TextEncoding.encodeUtf8 (Text.pack (contentLinkTarget link)))
      existing <- runGit executable rootPath rootFd gitFd Nothing
        ["ls-tree", "-z", Text.unpack expectedHead, "--", contentLinkPath link]
      pure $ do
        blobId <- blob
        listing <- existing
        case filter (not . Text.null) (Text.splitOn "\0" listing) of
          [] -> Right (Just (contentLinkPath link, blobId))
          [entry]
            | entry == "120000 blob " <> blobId <> "\t" <> Text.pack (contentLinkPath link) ->
                Right Nothing
          _ -> Left ("content path already holds a different entry: " <> Text.pack (contentLinkPath link))

    -- The manifest must be committed already, as a regular file, and is
    -- replaced; any other specification file must not exist yet. Archives
    -- bootstrapped by early Door builds committed it as 100755, which Door
    -- reads as a regular file everywhere else, so either mode is accepted
    -- here; the replacement is committed as 100644.
    classifyFile file = do
      let bytes = LazyByteString.toStrict (archiveFileBytes file)
          path = archiveFilePath file
      blob <- gitBlobForFileBytes executable rootPath rootFd gitFd bytes
      existing <- runGit executable rootPath rootFd gitFd Nothing
        ["ls-tree", "-z", Text.unpack expectedHead, "--", path]
      pure $ do
        blobId <- blob
        listing <- existing
        case (path == manifestFilePath, filter (not . Text.null) (Text.splitOn "\0" listing)) of
          (False, []) -> Right (path, blobId)
          (False, _) -> Left ("specification file already exists: " <> Text.pack path)
          (True, [entry])
            | Just listed <- Text.stripSuffix ("\t" <> Text.pack path) entry
            , Just old <- Text.stripPrefix "100644 blob " listed <|> Text.stripPrefix "100755 blob " listed
            , old /= blobId -> Right (path, blobId)
          (True, _) -> Left "the committed manifest is missing, not a regular file, or unchanged"

    installWorktreeLinks = do
      installed <- tryIO (mapM_ (installContentLink rootFd) contentLinks)
      case installed of
        Left problem -> hPutStrLn stderr
          ("door: warning: archive commit succeeded but a content location could not be created: " <> Text.unpack problem)
        Right () -> pure ()

    buildAndAdvance recordsFd branch reservation hashed newLinks newFiles journalState explicitFailureRef = do
          readTree <- runIndexGit executable rootPath rootFd gitFd reservation
            ["-c", "core.hooksPath=/dev/null", "read-tree", Text.unpack expectedHead]
          buildFromTree recordsFd branch reservation hashed newLinks newFiles journalState explicitFailureRef readTree

    buildFromTree recordsFd branch reservation hashed newLinks newFiles journalState explicitFailureRef readTree =
      case readTree of
        Left problem -> pure (Left problem)
        Right _ -> do
          let paths = map installedRecordPath hashed <> map fst newLinks <> map fst newFiles
          indexed <- updateIndexEntries executable rootPath rootFd gitFd reservation
            ([("100644", installedRecordBlob record, installedRecordPath record) | record <- hashed]
              <> [("120000", blob, path) | (path, blob) <- newLinks]
              <> [("100644", blob, path) | (path, blob) <- newFiles])
          case indexed of
            Left problem -> pure (Left problem)
            Right _ -> do
              staged <- runIndexGit executable rootPath rootFd gitFd reservation
                (["diff", "--cached", "--name-only", Text.unpack expectedHead, "--"] <> paths)
              case staged of
                Left problem -> pure (Left problem)
                Right names
                  | sort (filter (not . Text.null) (Text.lines names))
                      /= sort (map Text.pack paths) ->
                      pure (Left "temporary Git index does not contain exactly the record, content, and specification batch")
                  | otherwise -> do
                      tree <- runIndexGit executable rootPath rootFd gitFd reservation ["write-tree"]
                      case tree of
                        Left problem -> pure (Left problem)
                        Right treeId -> do
                          let treeObject = Text.strip treeId
                          commit <- runIndexGit executable rootPath rootFd gitFd reservation
                            [ "-c", "user.name=Archive"
                            , "-c", "user.email=archive@localhost"
                            , "commit-tree", Text.unpack treeObject
                            , "-p", Text.unpack expectedHead
                            , "-m", Text.unpack message
                            ]
                          case commit of
                            Left problem -> pure (Left problem)
                            Right commitId -> do
                              let newCommit = Text.strip commitId
                              baselineSynced <- syncBaselineFrom executable rootPath
                                rootFd gitFd provenBase newCommit
                              case baselineSynced of
                                Left problem -> pure (Left problem)
                                Right () -> do
                                  journal <- readIORef journalState
                                  syncedRecords <- syncRecordDirectoryAndBarrier recordsFd
                                    (journalEntries journal)
                                  case syncedRecords of
                                    Left problem -> pure (Left problem)
                                    Right () -> do
                                      verified <- verifyRecordsDirectory rootFd recordsFd
                                      case verified of
                                        Left problem -> pure (Left problem)
                                        Right () -> do
                                          let withTarget = journal { journalTargetCommit = Just newCommit }
                                          journaled <- replaceOperationJournal gitFd journal withTarget
                                          case journaled of
                                            Left problem -> pure (Left
                                              ("could not durably record proposed commit before ref update: " <> problem))
                                            Right () -> do
                                              writeIORef journalState withTarget
                                              advanced <- runGitTyped executable rootPath rootFd gitFd Nothing
                                                [ "-c", "core.hooksPath=/dev/null"
                                                , "update-ref", "--create-reflog", Text.unpack branch
                                                , Text.unpack newCommit, Text.unpack expectedHead
                                                ]
                                              case advanced of
                                                Right _ -> do
                                                  syncedRef <- syncReferenceDirectories gitFd branch
                                                  case syncedRef of
                                                    Left problem -> uncertainFailure explicitFailureRef problem
                                                    Right () -> do
                                                      syncedRecordBarrier <- syncRecordDirectoryAndBarrier recordsFd
                                                        (journalEntries withTarget)
                                                      case syncedRecordBarrier of
                                                        Left problem -> uncertainFailure explicitFailureRef problem
                                                        Right () -> pure (Right newCommit)
                                                Left problem -> do
                                                  observed <- if gitPinChanged problem
                                                    then pure (Left problem)
                                                    else runGitTyped executable rootPath rootFd gitFd Nothing
                                                      ["rev-parse", "--verify", Text.unpack branch]
                                                  case observed of
                                                    Right current | Text.strip current == expectedHead ->
                                                      pure (Left (gitFailureMessage problem))
                                                    _ -> uncertainFailure explicitFailureRef
                                                      (gitFailureMessage problem)

    uncertainFailure explicitFailureRef problem = do
      let errorMessage = uncertainRefOutcomeMessage <> ": " <> problem
      writeIORef explicitFailureRef (Just (MutationUncertain errorMessage))
      pure (Left errorMessage)

-- | Add every record and content link of a batch to the temporary index in
-- one Git process: one @--cacheinfo mode,blob,path@ per entry.
updateIndexEntries :: Executable -> FilePath -> Fd -> Fd -> IndexReservation
                   -> [(String, Text, FilePath)] -> IO (Either Text Text)
updateIndexEntries executable rootPath rootFd gitFd reservation entries =
  runIndexGit executable rootPath rootFd gitFd reservation
    ([ "-c", "core.hooksPath=/dev/null", "update-index", "--add" ]
      <> concat [ ["--cacheinfo", mode <> "," <> Text.unpack blob <> "," <> path]
                | (mode, blob, path) <- entries ])

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
  40 -> hex (SHA1.hash framed) == name
  64 -> hex (SHA256.hash framed) == name
  _ -> False
  where
    framed = "blob " <> TextEncoding.encodeUtf8 (Text.pack (show (ByteString.length bytes))) <> "\0" <> bytes
    hex = Text.pack . concatMap byteHex . ByteString.unpack
    byteHex byte = ["0123456789abcdef" !! fromIntegral (div byte 16), "0123456789abcdef" !! fromIntegral (mod byte 16)]

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

-- | Content paths are the D-1 layout's `content/sha256/<h0h1>/<h2h3>/<digest>`
-- (ADR 0053) and nothing else; their link targets stay inside this
-- repository's annex object store. The result is the fan-out directories and
-- the link's name.
validateContentPath :: FilePath -> Either Text ([FilePath], FilePath)
validateContentPath path = do
  digest <- parseContentRelativePath path
  let hex = Text.unpack (Text.drop 7 (digestText digest))
  if contentRelativePath digest == path
    then Right ([take 2 hex, take 2 (drop 2 hex)], hex)
    else Left "content path must be content/sha256/<h0h1>/<h2h3>/<digest>"

validateContentLink :: ContentLink -> Either Text ()
validateContentLink link = do
  _ <- validateContentPath (contentLinkPath link)
  let target = contentLinkTarget link
  case splitDirectories target of
    ".." : ".." : ".." : ".." : ".git" : "annex" : "objects" : rest
      | not (null rest)
      , ".." `notElem` rest
      , "." `notElem` rest
      , '\0' `notElem` target -> Right ()
    _ -> Left "content link must point into this archive's git-annex object store"

-- | Create one worktree location for a committed content link. An existing
-- symlink with the same target is the same location; anything else is left
-- untouched and reported.
installContentLink :: Fd -> ContentLink -> IO ()
installContentLink rootFd link = do
  (fanOutDirectories, digest) <- either failText pure (validateContentPath (contentLinkPath link))
  Exception.bracket (openDirectoryAt rootFd "content") closeFd $ \contentFd ->
    withDirectoryChain contentFd ("sha256" : fanOutDirectories) True $ \shaFd ->
      withCString (contentLinkTarget link) $ \targetCString -> withCString digest $ \nameCString -> do
        result <- c_symlinkat targetCString (fdValue shaFd) nameCString
        if result == 0
          then syncDirectoryFdOrFail shaFd
          else do
            errno <- getErrno
            if errno /= eEXIST
              then throwErrnoIfMinus1_ "symlinkat" (pure result)
              else do
                existing <- readLinkAt shaFd digest
                unless (existing == contentLinkTarget link) $
                  failText ("content location already exists with another target: " <> Text.pack (contentLinkPath link))

-- | Write the worktree copy of a committed specification file through the
-- pinned root: a new file in the same directory, synchronized, then renamed
-- over the name, so a reader sees the old or the new bytes.
installWorktreeFile :: Fd -> ArchiveFile -> IO ()
installWorktreeFile rootFd file = do
  either failText pure (validateArchiveFilePath (archiveFilePath file))
  let components = splitDirectories (archiveFilePath file)
      directories = take (length components - 1) components
      name = last components
  withDirectoryChain rootFd directories True $ \directoryFd -> do
    unique <- hashUnique <$> newUnique
    processId <- getProcessID
    let temporary = ".archive-door-file-" <> show processId <> "-" <> show unique
    Exception.bracket
      (openFdAt (Just directoryFd) temporary WriteOnly
        defaultFileFlags { creat = Just ownerFileModes, exclusive = True, nofollow = True, cloexec = True })
      closeFd
      (\fd -> writeAll fd (LazyByteString.toStrict (archiveFileBytes file)) >> syncStrongFdOrFail fd)
    renameAt directoryFd temporary name
    syncDirectoryFdOrFail directoryFd

-- | Run an action in a directory reached from a pinned descriptor through
-- each named child, never following a symlink. With 'True', missing
-- directories are created and each directory a child was created in is
-- synchronized, so the new path survives power loss with its entry.
withDirectoryChain :: Fd -> [FilePath] -> Bool -> (Fd -> IO a) -> IO a
withDirectoryChain parent [] _ action = action parent
withDirectoryChain parent (name : rest) create action = do
  when create $ do
    created <- makeDirectoryAtReporting parent name
    when created (syncDirectoryFdOrFail parent)
  Exception.bracket (openDirectoryAt parent name) closeFd $ \child ->
    withDirectoryChain child rest create action

-- | Remove the fan-out directories of a removed record, deepest first, while
-- they are empty. A directory another record still uses stays.
removeEmptyChain :: Fd -> [FilePath] -> IO ()
removeEmptyChain parent chain = mapM_ removeOne (reverse (drop 1 (inits chain)))
  where
    removeOne directories = case reverse directories of
      [] -> pure ()
      name : outerReversed ->
        withDirectoryChain parent (reverse outerReversed) False $ \directoryFd ->
          withCString name $ \nameCString -> do
            result <- c_unlinkatDirectory (fdValue directoryFd) nameCString
            if result == 0
              then syncDirectoryFdOrFail directoryFd
              else do
                errno <- getErrno
                unless (errno `elem` [eNOTEMPTY, eEXIST, eNOENT]) $
                  throwErrnoIfMinus1_ "unlinkat directory" (pure result)

makeDirectoryAtReporting :: Fd -> FilePath -> IO Bool
makeDirectoryAtReporting directoryFd name = withCString name $ \nameCString -> do
  result <- c_mkdirat (fdValue directoryFd) nameCString 0o755
  if result == 0
    then pure True
    else do
      errno <- getErrno
      unless (errno == eEXIST) $ throwErrnoIfMinus1_ "mkdirat" (pure result)
      pure False

readLinkAt :: Fd -> FilePath -> IO FilePath
readLinkAt directoryFd name = withCString name $ \nameCString ->
  allocaBytes 4096 $ \buffer -> do
    count <- c_readlinkat (fdValue directoryFd) nameCString buffer 4096
    when (count < 0 || count >= 4096) $
      failText ("could not read content location link: " <> Text.pack name)
    peekCStringLen (buffer, fromIntegral count)

reserveIndex :: Fd -> IO (Either Text IndexReservation)
reserveIndex gitFd = do
  -- A kill after O_EXCL reservation but before the operation journal is
  -- published leaves no inode proof. The next mutation refuses this stale
  -- private-prefix artifact for operator inspection; it never guesses ownership.
  scan <- hasStaleIndex gitFd
  case scan of
    Left problem -> pure (Left problem)
    Right True -> pure (Left "stale Door index artifact requires operator inspection")
    Right False -> tryNames
  where
    tryNames = do
      processId <- getProcessID
      unique <- newUnique
      let name = "archive-door-index-" <> show processId <> "-" <> show (hashUnique unique)
      opened <- Exception.try $ openFdAt (Just gitFd) name WriteOnly
        defaultFileFlags
          { creat = Just ownerFileModes
          , exclusive = True
          , nofollow = True
          , cloexec = True
          }
        :: IO (Either Exception.IOException Fd)
      case opened of
        Left problem
          | isAlreadyExistsError problem -> tryNames
          | otherwise -> pure (Left (Text.pack (show problem)))
        Right fd -> do
          status <- tryIO (getFdStatus fd)
          case status of
            Left problem -> closeFd fd >> pure (Left problem)
            Right fileStatus -> do
              identity <- newIORef (fd, (deviceID fileStatus, fileID fileStatus))
              journalWriter <- newIORef Nothing
              pure (Right (IndexReservation name identity journalWriter))

hasStaleIndex :: Fd -> IO (Either Text Bool)
hasStaleIndex (Fd descriptor) =
  withCString "archive-door-index" $ \prefix -> do
    result <- c_hasEntryPrefix descriptor prefix
    pure $ if result < 0
      then Left ("could not inspect stale Door indexes: " <> Text.pack (show result))
      else Right (result == 1)

runIndexGit :: Executable -> FilePath -> Fd -> Fd -> IndexReservation -> [String]
            -> IO (Either Text Text)
runIndexGit executable rootPath rootFd gitFd reservation arguments = do
  before <- verifyIndexReservation gitFd reservation
  case before of
    Left problem -> pure (Left problem)
    Right () -> do
      result <- runGit executable rootPath rootFd gitFd (Just name) arguments
      after <- verifyIndexReservation gitFd reservation
      case after of
        Left problem -> pure (Left problem)
        Right () -> do
          persisted <- persistIndexJournal gitFd reservation
          pure $ case persisted of
            Left problem -> Left problem
            Right () -> result
  where
    name = case reservation of IndexReservation indexName _ _ -> indexName

persistIndexJournal :: Fd -> IndexReservation -> IO (Either Text ())
persistIndexJournal gitFd reservation = case reservation of
  IndexReservation name identityRef writerRef -> do
    writer <- readIORef writerRef
    case writer of
      Nothing -> pure (Right ())
      Just journalRef -> do
        (_, (device, inode)) <- readIORef identityRef
        journal <- readIORef journalRef
        let updated = journal { journalIndex = JournalIndex name device inode }
        if updated == journal
          then pure (Right ())
          else do
            replaced <- replaceOperationJournal gitFd journal updated
            case replaced of
              Left problem -> pure (Left problem)
              Right () -> writeIORef journalRef updated >> pure (Right ())

verifyIndexReservation :: Fd -> IndexReservation -> IO (Either Text ())
verifyIndexReservation gitFd (IndexReservation name identityRef _) = do
  previous <- readIORef identityRef
  let (previousFd, previousIdentity) = previous
  opened <- Exception.try $ openFdAt (Just gitFd) name ReadOnly
    defaultFileFlags { nofollow = True, nonBlock = True, cloexec = True }
    :: IO (Either Exception.IOException Fd)
  case opened of
    Left problem -> pure (Left ("Door index identity is unavailable: " <> Text.pack (show problem)))
    Right currentFd -> do
      inspected <- tryIO $ do
        current <- getFdStatus currentFd
        unlessRegular current
        let currentIdentity = (deviceID current, fileID current)
        if currentIdentity == previousIdentity
          then pure False
          else do
            previousStatus <- getFdStatus previousFd
            if linkCount previousStatus == 0
              then writeIORef identityRef (currentFd, currentIdentity) >> closeFd previousFd >> pure True
              else failText "Door index changed without unlinking the owned index inode"
      case inspected of
        Left problem -> closeFd currentFd >> pure (Left problem)
        Right False -> closeFd currentFd >> pure (Right ())
        Right True -> pure (Right ())
  where
    unlessRegular status = if isRegularFile status
      then pure ()
      else failText "Door index path is not a regular file"

cleanupIndexReservation :: Fd -> IndexReservation -> IO (Either Text ())
cleanupIndexReservation gitFd reservation@(IndexReservation name identityRef _) = do
  verified <- verifyIndexReservation gitFd reservation
  case verified of
    Left problem -> do
      (ownedFd, _) <- readIORef identityRef
      closeFd ownedFd
      pure (Left ("could not prove Door index ownership during cleanup: " <> problem))
    Right () -> do
      (ownedFd, _) <- readIORef identityRef
      removed <- Exception.try (unlinkAt gitFd name)
        :: IO (Either Exception.IOException ())
      case removed of
        Left problem -> closeFd ownedFd >> pure (Left (Text.pack (show problem)))
        Right () -> closeFd ownedFd >> pure (Right ())

withNewIndexReservation :: Fd
                        -> (IndexReservation -> IO (Either Text (Either Text Text)))
                        -> IO (Either Text Text)
withNewIndexReservation gitFd action = Exception.mask $ \restore -> do
  reserved <- reserveIndex gitFd
  case reserved of
    Left problem -> pure (Left problem)
    Right reservation -> do
      outcome <- Exception.try (restore (action reservation))
        :: IO (Either Exception.SomeException (Either Text (Either Text Text)))
      cleaned <- cleanupIndexReservation gitFd reservation
      case outcome of
        Left exception -> Exception.throwIO exception
        Right (Left problem) -> pure $ case cleaned of
          Left cleanupProblem -> Left (problem <> "; cleanup also failed: " <> cleanupProblem)
          Right () -> Left problem
        Right (Right value) -> pure $ case cleaned of
          Left cleanupProblem -> appendCleanupResult value cleanupProblem
          Right () -> value

-- | Validate the exact record path grammar at the last boundary before any
-- filesystem access. Writers only install digest-addressed immutable records.
-- | Where a record file lives below @records/@: its fan-out directories and
-- its filename (ADR 0053).
data RecordLocation = RecordLocation
  { recordLocationDirectories :: [FilePath]
  , recordLocationFile :: FilePath
  } deriving (Eq, Show)

validateRecordPath :: FilePath -> Either Text RecordLocation
validateRecordPath path
  | null path = Left "record path must not be empty"
  | '\0' `elem` path = Left "record path must not contain NUL"
  | isAbsolute path = Left "record path must be relative"
  | otherwise = do
      digest <- parseRecordRelativePath path
      if recordRelativePath digest /= path
        then Left "record path must be records/<h0h1>/<h2h3>/sha256-<digest>.json"
        else Right RecordLocation
          { recordLocationDirectories = recordDirectoryChain digest
          , recordLocationFile = "sha256-" <> Text.unpack (Text.drop 7 (digestText digest)) <> ".json"
          }

targetAbsent :: Fd -> FilePath -> IO (Either Text ())
targetAbsent rootFd relative = case validateRecordPath relative of
  Left problem -> pure (Left problem)
  Right filename -> do
    checked <- tryIO $ withRecordsDirectory rootFd $ \recordsFd -> do
      opened <- Exception.try (openRecordTarget recordsFd filename)
        :: IO (Either Exception.IOException Fd)
      case opened of
        Left problem
          | isDoesNotExistError problem -> pure (Right ())
          | otherwise -> pure (Left (Text.pack (show problem)))
        Right fd -> do
          closeFd fd
          pure (Left ("record target already exists: " <> Text.pack relative))
    pure (checked >>= id)

withRecordsDirectory :: Fd -> (Fd -> IO a) -> IO a
withRecordsDirectory rootFd action = Exception.bracket (openRecords rootFd) closeFd action
  where
    directoryFlags = defaultFileFlags
      { directory = True, nofollow = True, cloexec = True }
    openRecords directoryFd = openFdAt (Just directoryFd) "records" ReadOnly directoryFlags

withPinnedRecords :: Fd -> Maybe Fd -> (Fd -> IO a) -> IO a
withPinnedRecords rootFd pinned action = case pinned of
  Just fd -> action fd
  Nothing -> withRecordsDirectory rootFd action

verifyRecordsDirectory :: Fd -> Fd -> IO (Either Text ())
verifyRecordsDirectory rootFd expectedFd = do
  result <- tryIO $ Exception.bracket (openRecords rootFd) closeFd $ \currentFd -> do
    expected <- getFdStatus expectedFd
    current <- getFdStatus currentFd
    whenDifferent (deviceID expected == deviceID current
      && fileID expected == fileID current)
  pure result
  where
    openRecords fd = openFdAt (Just fd) "records" ReadOnly
      defaultFileFlags { directory = True, nofollow = True, cloexec = True }
    whenDifferent True = pure ()
    whenDifferent False = failText "records directory changed during Door mutation"

openRecordTarget :: Fd -> RecordLocation -> IO Fd
openRecordTarget recordsFd location =
  withDirectoryChain recordsFd (recordLocationDirectories location) False $ \directoryFd ->
    openFdAt (Just directoryFd) (recordLocationFile location) ReadOnly
      defaultFileFlags { nofollow = True, nonBlock = True, cloexec = True }

-- Git hashes this descriptor through /dev/fd while it is open. Keeping the
-- descriptor inheritable is limited to the short hash subprocess; callers
-- still compare its fstat identity with the nofollow pathname before unlink.
openRecordTargetForHash :: Fd -> RecordLocation -> IO Fd
openRecordTargetForHash recordsFd location = do
  fd <- withDirectoryChain recordsFd (recordLocationDirectories location) False $ \directoryFd ->
    openFdAt (Just directoryFd) (recordLocationFile location) ReadOnly
      defaultFileFlags { nofollow = True, nonBlock = True, cloexec = False }
  setFdOption fd CloseOnExec False
  pure fd

gitBlobForFd :: Executable -> FilePath -> Fd -> Fd -> Fd -> IO (Either Text Text)
gitBlobForFd executable rootPath rootFd gitFd fd = do
  closesOnExec <- queryFdOption fd CloseOnExec
  if closesOnExec
    then pure (Left "record descriptor is marked close-on-exec")
    else runGit executable rootPath rootFd gitFd Nothing
      ["hash-object", "--no-filters", "--", "/dev/fd/" <> showFd fd]

showFd :: Fd -> String
showFd (Fd value) = show value

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

-- | Stage every record of a batch: write and synchronize each staging file,
-- write all of them as Git blobs in one Git process, then synchronize each
-- again and confirm it is still the inode this operation created. A failure
-- removes every staging file this call created.
prepareAll :: Executable -> FilePath -> Fd -> Fd
           -> [(FilePath, (FilePath, LazyByteString.ByteString))]
           -> IO (Either Text [PreparedRecord])
prepareAll executable rootPath rootFd gitFd items = Exception.mask $ \restore -> do
  stagedRef <- newIORef []
  result <- tryIO (restore (do
      mapM_ (stageOne stagedRef) items
      staged <- reverse <$> readIORef stagedRef
      output <- if null staged
        then pure ""
        else runGit executable rootPath rootFd gitFd Nothing
          (["hash-object", "-w", "--no-filters", "--"] <> [temporary | (temporary, _, _, _) <- staged])
          >>= either failText pure
      let blobs = filter (not . Text.null) (map Text.strip (Text.lines output))
      unless (length blobs == length staged) $
        failText "Git did not write every staged record"
      mapM confirm (zip staged blobs)))
    `Exception.onException` (readIORef stagedRef >>= mapM_ (discardAfterFailure . cleanupStaged))
  case result of
    Right prepared -> pure (Right prepared)
    Left problem -> do
      staged <- readIORef stagedRef
      cleaned <- mapM cleanupStaged staged
      pure $ case sequence_ cleaned of
        Left cleanupProblem -> Left (problem <> "; staged source cleanup also failed: " <> cleanupProblem)
        Right () -> Left problem
  where
    cleanupStaged (temporary, _, temporaryFd, ownedStatus) =
      cleanupUninstalledTemporary gitFd temporaryFd temporary ownedStatus

    stageOne stagedRef (temporary, (relative, bytes)) = do
      _ <- either failText pure (validateRecordPath relative)
      temporaryFd <- Exception.mask_ $ do
        fd <- openFdAt (Just gitFd) temporary WriteOnly defaultFileFlags
          { creat = Just ownerFileModes, exclusive = True, nofollow = True, cloexec = True }
        ownedStatus <- getFdStatus fd `Exception.onException` closeFd fd
        unless (isRegularFile ownedStatus) $
          (closeFd fd >> failText "record staging file is not regular")
        modifyIORef stagedRef ((temporary, relative, fd, ownedStatus) :)
        pure fd
      writeAll temporaryFd (LazyByteString.toStrict bytes)
      syncStrongFdOrFail temporaryFd

    confirm ((temporary, relative, temporaryFd, ownedStatus), blob) = do
      syncStrongFdOrFail temporaryFd
      status <- getFdStatus temporaryFd
      unless (isRegularFile status && sameDeviceAndFile ownedStatus status) $
        failText "record staging file changed while preparing"
      pure PreparedRecord
        { preparedRecordPath = relative
        , preparedRecordBlob = blob
        , preparedRecordTemporary = temporary
        , preparedRecordFd = temporaryFd
        , preparedRecordDevice = deviceID status
        , preparedRecordFile = fileID status
        }

freshTemporaryName :: IO FilePath
freshTemporaryName = do
  processId <- getProcessID
  unique <- newUnique
  pure (".archive-door-record-" <> show processId <> "-" <> show (hashUnique unique))

preparedToJournal :: PreparedRecord -> JournalEntry
preparedToJournal prepared = JournalEntry
  { journalPath = preparedRecordPath prepared
  , journalTemporary = preparedRecordTemporary prepared
  , journalBlob = Just (preparedRecordBlob prepared)
  , journalDevice = Just (preparedRecordDevice prepared)
  , journalFile = Just (preparedRecordFile prepared)
  }

closePrepared :: [PreparedRecord] -> IO ()
closePrepared = mapM_ (closeFd . preparedRecordFd)

installPreparedAll :: Executable -> FilePath -> Fd -> Fd -> Fd -> [PreparedRecord]
                   -> IO (Either Text [InstalledRecord])
installPreparedAll executable rootPath rootFd gitFd recordsFd prepared = do
  opened <- newIORef []
  result <- tryIO (go opened) `Exception.finally` (readIORef opened >>= mapM_ closeFd)
  pure result
  where
    -- Link every record, then hash all linked targets through their pinned
    -- descriptors in one Git process, then confirm each path still names its
    -- prepared inode.
    go opened = do
      linked <- mapM (linkOne opened) prepared
      blobs <- gitBlobsForFds executable rootPath rootFd gitFd [fd | (_, _, _, fd) <- linked]
        >>= either failText pure
      mapM confirm (zip linked blobs)

    linkOne opened record = case validateRecordPath (preparedRecordPath record) of
      Left problem -> failText problem
      Right filename -> do
        withDirectoryChain recordsFd (recordLocationDirectories filename) True $ \directoryFd ->
          linkAt gitFd (preparedRecordTemporary record) directoryFd (recordLocationFile filename)
        sourceStatus <- getFdStatus (preparedRecordFd record)
        installedFd <- openRecordTargetForHash recordsFd filename
        modifyIORef opened (installedFd :)
        status <- getFdStatus installedFd
        unless (isRegularFile status && sameDeviceAndFile sourceStatus status) $
          failText "record target does not match its prepared inode"
        pure (record, filename, sourceStatus, installedFd)

    confirm ((record, filename, sourceStatus, _), actual)
      | actual /= preparedRecordBlob record = failText "linked record blob changed during installation"
      | otherwise = do
          current <- Exception.try (openRecordTarget recordsFd filename)
            :: IO (Either Exception.IOException Fd)
          case current of
            Left problem -> failText
              ("record target changed during installation: " <> Text.pack (show problem))
            Right currentFd -> Exception.bracket (pure currentFd) closeFd $ \_ -> do
              currentStatus <- getFdStatus currentFd
              unless (sameDeviceAndFile sourceStatus currentStatus) $
                failText "record target changed during installation"
              pure InstalledRecord
                { installedRecordPath = preparedRecordPath record
                , installedRecordBlob = preparedRecordBlob record
                , installedRecordDevice = deviceID sourceStatus
                , installedRecordFile = fileID sourceStatus
                }

writeAll :: Fd -> ByteString.ByteString -> IO ()
writeAll fd bytes = ByteString.useAsCStringLen bytes $ \(buffer, lengthBytes) ->
  writeRemaining (castPtr buffer) (fromIntegral lengthBytes)
  where
    writeRemaining _ 0 = pure ()
    writeRemaining buffer remaining = do
      written <- fdWriteBuf fd buffer remaining
      if written <= 0
        then failText "short write while staging a record"
        else writeRemaining (buffer `plusPtr` fromIntegral written)
          (remaining - written)

linkAt :: Fd -> FilePath -> Fd -> FilePath -> IO ()
linkAt sourceDirectory sourceName targetDirectory targetName =
  withCString sourceName $ \sourceCString -> withCString targetName $ \targetCString ->
    throwErrnoIfMinus1_ "linkat" $ c_linkat (fdValue sourceDirectory) sourceCString
      (fdValue targetDirectory) targetCString 0

unlinkAt :: Fd -> FilePath -> IO ()
unlinkAt directoryFd filename = withCString filename $ \targetCString ->
  throwErrnoIfMinus1_ "unlinkat" $ c_unlinkat
    (fdValue directoryFd) targetCString 0

fdValue :: Fd -> CInt
fdValue (Fd value) = fromIntegral value

cleanupInstalled :: Executable -> FilePath -> Fd -> Fd -> Fd -> [InstalledRecord]
                 -> IO (Either Text ())
cleanupInstalled executable rootPath rootFd gitFd recordsFd installed = do
  results <- mapM removeOne installed
  case sequence_ results of
    Left problem -> pure (Left problem)
    Right () -> syncFd recordsFd
  where
    removeOne record = case validateRecordPath relative of
      Left problem -> pure (Left problem)
      Right filename -> do
        result <- tryIO $ do
          opened <- Exception.try (openRecordTargetForHash recordsFd filename)
            :: IO (Either Exception.IOException Fd)
          case opened of
            Left problem
              | isDoesNotExistError problem -> pure ()
              | otherwise -> failText (Text.pack (show problem))
            Right fd -> Exception.bracket (pure fd) closeFd $ \_ -> do
              before <- getFdStatus fd
              unless (isRegularFile before) $
                failText ("refusing to remove non-regular record target: " <> Text.pack relative)
              unless (sameInstalledIdentity record before) $
                failText ("refusing to remove record target with changed device/inode: " <> Text.pack relative)
              blob <- gitBlobForFd executable rootPath rootFd gitFd fd
              case blob of
                Left problem -> failText
                  ("refusing to remove record target because its blob identity could not be checked: " <> problem)
                Right actual | Text.strip actual /= installedRecordBlob record ->
                  failText ("refusing to remove record target with changed blob identity: " <> Text.pack relative)
                Right _ -> do
                  current <- Exception.try (openRecordTarget recordsFd filename)
                    :: IO (Either Exception.IOException Fd)
                  case current of
                    Left problem ->
                      failText ("refusing to remove record target after its path changed: " <> Text.pack (show problem))
                    Right currentFd -> Exception.bracket (pure currentFd) closeFd $ \_ -> do
                      after <- getFdStatus currentFd
                      unless (sameInstalledIdentity record after) $
                        failText ("refusing to remove record target with changed device/inode: " <> Text.pack relative)
                      withDirectoryChain recordsFd (recordLocationDirectories filename) False $ \directoryFd ->
                        unlinkAt directoryFd (recordLocationFile filename)
                      removeEmptyChain recordsFd (recordLocationDirectories filename)
        pure result
      where
        relative = installedRecordPath record

    sameInstalledIdentity record status =
      deviceID status == installedRecordDevice record
        && fileID status == installedRecordFile record

sameDeviceAndFile :: FileStatus -> FileStatus -> Bool
sameDeviceAndFile first second =
  deviceID first == deviceID second && fileID first == fileID second

appendCleanupResult :: Either Text Text -> Text -> Either Text Text
appendCleanupResult result cleanupProblem = case result of
  Left problem -> Left (problem <> "; cleanup also failed: " <> cleanupProblem)
  Right _ -> Left ("Door committed but could not remove its owned index: " <> cleanupProblem)

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

uncertainRefOutcomeMessage :: Text
uncertainRefOutcomeMessage = "Git ref outcome is uncertain; installed record files were retained"

-- | The Git remotes configured for this locked archive.
configuredRemoteNames :: ArchiveWriteLock -> IO (Either Text [Text])
configuredRemoteNames (ArchiveWriteLock rootPath rootFd gitFd executable) =
  fmap (filter (not . Text.null) . map Text.strip . Text.lines)
    <$> runGit executable rootPath rootFd gitFd Nothing ["remote"]

-- | Run the operator's configured git-annex inside this locked archive.
--
-- The child runs at the pinned root with an explicit Git directory and work
-- tree, and finds the configured Git first on PATH, so git-annex's own Git
-- calls use the same program (ADR 0033). Both executables and the root and
-- Git directory pins are checked on each side of the call, as for Git. A
-- nonzero exit is returned rather than refused, because some git-annex
-- commands report absence that way.
runAnnexLocked :: ArchiveWriteLock -> Executable -> [String]
               -> IO (Either MutationError (ExitCode, Text, Text))
runAnnexLocked (ArchiveWriteLock rootPath rootFd gitFd git) annex arguments = do
  environment <- annexEnvironment
  before <- pinsHold
  case before of
    Left problem -> pure (Left problem)
    Right () -> do
      result <- runProcessBoundedAt rootFd (executablePath annex) arguments environment
        (Just (8 * 1024 * 1024)) (1024 * 1024)
      after <- pinsHold
      pure $ do
        after
        case result of
          Left problem -> Left (MutationSubprocessFailure (processErrorMessage problem))
          Right (status, stdoutBytes, stderrBytes) -> Right
            (status, decode stdoutBytes, decode stderrBytes)
  where
    decode = TextEncoding.decodeUtf8With TextError.lenientDecode
    pinsHold = do
      annexUnchanged <- executableStillMatches annex
      gitUnchanged <- executableStillMatches git
      rootPinned <- rootDirectoryMatches rootPath rootFd
      gitPinned <- gitDirectoryMatches rootFd gitFd
      pure $ if not (annexUnchanged && gitUnchanged)
        then Left (MutationConfigurationFailure "configured Git or git-annex executable changed during admission")
        else if not rootPinned
          then Left (MutationUncertain rootDirectoryChangedMessage)
        else if not gitPinned
          then Left (MutationUncertain gitDirectoryChangedMessage)
          else Right ()
    annexEnvironment = do
      environment <- getEnvironment
      let clean = filter (\(name, _) -> not (isGitVariable name) && name /= "PATH") environment
          inheritedPath = maybe "" (":" <>) (lookup "PATH" environment)
      pure $ ("PATH", takeDirectory (executablePath git) <> inheritedPath)
        : ("GIT_DIR", ".git")
        : ("GIT_WORK_TREE", ".")
        : ("GIT_CONFIG_NOSYSTEM", "1")
        : ("GIT_CONFIG_GLOBAL", "/dev/null")
        : ("GIT_AUTHOR_NAME", "Archive")
        : ("GIT_AUTHOR_EMAIL", "archive@localhost")
        : ("GIT_COMMITTER_NAME", "Archive")
        : ("GIT_COMMITTER_EMAIL", "archive@localhost")
        : clean
    isGitVariable name = "GIT_" `Text.isPrefixOf` Text.pack name

tryIO :: forall a. IO a -> IO (Either Text a)
tryIO action = do
  result <- Exception.try action :: IO (Either Exception.IOException a)
  pure (either (Left . Text.pack . show) Right result)

failText :: Text -> IO a
failText = Exception.throwIO . userError . Text.unpack

-- | Files Door creates are the owner's to read and write, never executable:
-- Git records the executable bit, and a record is not a program.
ownerFileModes :: FileMode
ownerFileModes = ownerReadMode `unionFileModes` ownerWriteMode
