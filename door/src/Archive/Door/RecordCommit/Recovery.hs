{-# LANGUAGE OverloadedStrings #-}

-- | Finishing or undoing the write an operation journal describes.
--
-- Every Door mutation reconciles a journal it finds before doing anything
-- else, and a batch reconciles its own journal when it ends, so a killed
-- process and a finishing one take the same path. A batch whose commit is
-- on the branch is made durable and its leftovers removed; one whose commit
-- is not has its owned files removed, each only while its identity is still
-- proven. Anything else retains the journal for operator inspection.
module Archive.Door.RecordCommit.Recovery
  ( reconcileOperationJournal
  , reconcileOperationJournalWithRecords
  ) where

import Archive.Door.RecordCommit.Descriptors
  ( syncFd
  , unlinkAt
  , withDuplicatedBinaryHandle
  , withTrackedDescriptors
  )
import Archive.Door.RecordCommit.Durability (syncBaselineFrom, syncReferenceDirectories)
import Archive.Door.RecordCommit.Failure (failText, orFail, require, tryIO, tryPresent, unlessEither)
import Archive.Door.RecordCommit.Git
  ( blobMatches
  , gitBlobForFd
  , gitBlobsForFds
  , gitFailureMessage
  , runGit
  , runGitBytesTyped
  , runGitStatus
  )
import Archive.Door.RecordCommit.Journal
  ( JournalEntry
  , JournalEntryOf (..)
  , JournalIdentity (..)
  , JournalIndex (..)
  , OperationJournal
  , OperationJournalOf (..)
  , clearJournalIfSame
  , operationJournalName
  , parseOperationJournal
  , readBoundedJournal
  )
import Archive.Door.RecordCommit.ObjectName (gitObjectNameText)
import Archive.Door.RecordCommit.Paths (ArchiveFile (..), manifestFilePath, validateRecordPath)
import Archive.Door.RecordCommit.Records
  ( InstalledRecord (..)
  , cleanupInstalled
  , openRecordTargetForHash
  , syncRecordDirectoryAndBarrier
  , targetAbsent
  , withPinnedRecords
  )
import Archive.Door.RecordCommit.Worktree (installWorktreeFile)
import Archive.Executables (Executable)
import qualified Control.Exception as Exception
import Control.Monad (unless)
import Control.Monad.Trans.Class (lift)
import Control.Monad.Trans.Except (ExceptT (..), except, runExceptT, throwE, withExceptT)
import qualified Data.ByteString.Lazy as LazyByteString
import Data.List (sort)
import Data.Text (Text)
import qualified Data.Text as Text
import System.Exit (ExitCode (..))
import System.IO (hPutStrLn, stderr)
import System.Posix.Files (deviceID, fileID, getFdStatus, isRegularFile)
import System.Posix.IO
  ( FdOption (CloseOnExec)
  , OpenFileFlags (cloexec, nofollow, nonBlock)
  , OpenMode (ReadOnly)
  , closeFd
  , defaultFileFlags
  , openFdAt
  , setFdOption
  )
import System.Posix.Types (Fd)

reconcileOperationJournal :: Executable -> FilePath -> Fd -> Fd -> IO (Either Text ())
reconcileOperationJournal executable rootPath rootFd gitFd =
  reconcileOperationJournalWithRecords executable rootPath rootFd gitFd Nothing Nothing

-- | The optional base names a commit whose objects a checkpoint proved
-- (ADR 0052); a journaled batch on top of it needs only its new objects
-- synchronized again.
reconcileOperationJournalWithRecords :: Executable -> FilePath -> Fd -> Fd -> Maybe Fd -> Maybe Text
                                      -> IO (Either Text ())
reconcileOperationJournalWithRecords executable rootPath rootFd gitFd pinnedRecords provenBase = runExceptT $ do
  opened <- orFail ("could not open Door operation journal: " <>) $ tryPresent $
    openFdAt (Just gitFd) operationJournalName ReadOnly
      defaultFileFlags { nofollow = True, nonBlock = True, cloexec = True }
  case opened of
    Nothing -> pure ()
    Just fd -> ExceptT $ Exception.bracket (pure fd) closeFd $ \journalFd -> runExceptT $ do
      (status, bytes) <- orFail ("could not read Door operation journal: " <>) $ tryIO $ do
        status <- getFdStatus journalFd
        unless (isRegularFile status) (failText "Door operation journal is not a regular file")
        bytes <- withDuplicatedBinaryHandle journalFd readBoundedJournal
          >>= either failText pure
        pure (status, bytes)
      journal <- withExceptT ("Door operation journal requires operator inspection: " <>)
        (except (parseOperationJournal bytes))
      let identity = JournalIdentity (deviceID status) (fileID status)
      reconciled <- orFail ("journal recovery could not complete safely: " <>)
        (tryIO (reconcileJournal executable rootPath rootFd gitFd pinnedRecords provenBase journal))
      except reconciled
      ExceptT (clearJournalIfSame gitFd identity journal)

reconcileJournal :: Executable -> FilePath -> Fd -> Fd -> Maybe Fd -> Maybe Text -> OperationJournal
                 -> IO (Either Text ())
reconcileJournal executable rootPath rootFd gitFd pinnedRecords provenBase journal = runExceptT $ do
  currentBranch <- lift $ runGit executable rootPath rootFd gitFd Nothing ["symbolic-ref", "-q", "HEAD"]
  currentHead <- lift $ runGit executable rootPath rootFd gitFd Nothing ["rev-parse", "--verify", "HEAD^{commit}"]
  headValue <- except $ do
    branch <- Text.strip <$> currentBranch
    unlessEither (branch == journalBranch journal) "archive branch changed while a Door operation journal remained"
    headValue <- Text.strip <$> currentHead
    Right (Text.strip headValue)
  ExceptT $ case journalPhase journal of
    "preparing" -> reconcilePreparing headValue
    "installing" -> reconcileInstalling headValue
    _ -> pure (Left "unsupported Door operation journal phase")
  where
    -- Recovery compares the journaled names with Git's output as text.
    oldHead = gitObjectNameText (journalOldHead journal)

    reconcilePreparing headValue
      | headValue /= oldHead = pure (Left "archive HEAD changed during record preparation; operation journal retained")
      | otherwise = runExceptT $ do
          ExceptT oldHistoryClear
          -- Every path and staging name is checked before any verdict.
          absent <- lift $ mapM (targetAbsent rootFd . journalPath) (journalEntries journal)
          _ <- withExceptT ("record path appeared before journaled installation: " <>)
            (except (sequence absent))
          temps <- lift $ mapM (removePreparingTemporary gitFd . journalTemporary) (journalEntries journal)
          except (sequence_ temps)
          ExceptT (cleanupJournalIndex gitFd (journalIndex journal))

    reconcileInstalling headValue =
      case gitObjectNameText <$> journalTargetCommit journal of
        Nothing
          | headValue /= oldHead -> pure (Left
              "archive HEAD differs from the journaled old commit without a proposed commit; operation journal retained")
          | otherwise -> cleanupUncommitted
        Just target
          | headValue == oldHead -> cleanupUncommitted
          | otherwise -> runExceptT $ do
              ExceptT (verifyCommittedBatch headValue target)
              orFail ("committed batch durability barrier failed; operation journal retained: " <>) $
                withPinnedRecords rootFd pinnedRecords $ \recordsFd -> runExceptT $ do
                  ExceptT (verifyCommittedRecords recordsFd (journalEntries journal))
                  ExceptT (syncCommittedState gitFd recordsFd target
                    (journalBranch journal) (journalEntries journal))
              lift installCommittedFiles
              ExceptT cleanupTemporariesOnly
              ExceptT (refreshCommittedIndex headValue target)

    -- The worktree copies of a committed upgrade's specification files,
    -- from the committed blobs. This runs after every committed batch, also
    -- one an interrupted process left, so the worktree manifest follows the
    -- commit. The commit is the authority: a failure is reported, not undone.
    installCommittedFiles = mapM_ installCommittedFile (journalFiles journal)
    installCommittedFile (path, blob) = do
      contents <- runGitBytesTyped executable rootPath rootFd gitFd Nothing ["cat-file", "blob", Text.unpack (gitObjectNameText blob)]
      installed <- case contents of
        Left problem -> pure (Left (gitFailureMessage problem))
        Right bytes
          | not (blobMatches (gitObjectNameText blob) bytes) -> pure (Left "committed blob does not hash to its name")
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
            ["diff", "--cached", "--quiet", Text.unpack (oldHead), "--"]
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

    cleanupUncommitted = withPinnedRecords rootFd pinnedRecords $ \recordsFd -> runExceptT $ do
      ExceptT oldHistoryClear
      records <- except (traverse journalToInstalled (journalEntries journal))
      orFail ("could not prove cleanup ownership for interrupted record batch: " <>)
        (cleanupInstalled executable rootPath rootFd gitFd recordsFd records)
      ExceptT cleanupTemporariesOnly

    -- The manifest is the one journaled path the old commit holds: an
    -- upgrade replaces it. Every other journaled path must be new.
    oldHistoryClear = do
      contents <- runGit executable rootPath rootFd gitFd Nothing
        (["ls-tree", "-r", "--name-only", Text.unpack (oldHead), "--"]
          <> map journalPath (journalEntries journal)
          <> map fst (journalContent journal)
          <> filter (/= manifestFilePath) (map fst (journalFiles journal)))
      pure $ do
        paths <- contents
        unlessEither (Text.null (Text.strip paths))
          "journal record path already exists in the pinned old commit"

    -- Every staging file is attempted before any verdict.
    cleanupTemporariesOnly = runExceptT $ do
      results <- lift $ mapM (cleanupJournalTemporary executable rootPath rootFd gitFd) (journalEntries journal)
      except (sequence_ results)
      ExceptT (cleanupJournalIndex gitFd (journalIndex journal))

    verifyCommittedBatch currentHead target = do
      parent <- runGit executable rootPath rootFd gitFd Nothing ["rev-parse", "--verify", Text.unpack target <> "^"]
      diff <- runGit executable rootPath rootFd gitFd Nothing
        ["diff-tree", "--no-commit-id", "--no-renames", "--name-status", "-r", Text.unpack (oldHead), Text.unpack target]
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
        unlessEither (parentValue == oldHead)
          "journaled commit is not a direct child of the pinned old commit"
        status <- ancestor
        unlessEither (status == ExitSuccess) "journaled commit is not in the current branch history"
        diffValue <- diff
        changes <- traverse parseDiffLine (filter (not . Text.null) (Text.lines diffValue))
        treeBlobs <- sequence treeBlobsResult
        contentBlobs <- sequence contentBlobsResult
        unlessEither (contentBlobs == [gitObjectNameText blob | (_, blob) <- journalContent journal])
          "journaled commit tree contains a content location with a different link"
        fileBlobs <- sequence fileBlobsResult
        unlessEither (fileBlobs == [gitObjectNameText blob | (_, blob) <- journalFiles journal])
          "journaled commit tree contains a specification file with a different blob"
        let expectedPaths = sort (map (Text.pack . journalPath) (journalEntries journal)
              <> map (Text.pack . fst) (journalContent journal)
              <> map (Text.pack . fst) (journalFiles journal))
            actualPaths = sort changes
            expectedBlobs = sort [(Text.pack (journalPath entry), gitObjectNameText <$> journalBlob entry)
              | entry <- journalEntries journal]
            actualBlobs = sort treeBlobs
        unlessEither (actualPaths == expectedPaths)
          "journaled commit tree does not contain exactly the expected record additions"
        unlessEither (actualBlobs == expectedBlobs)
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

    syncCommittedState syncGitFd recordsFd commit branch entries = runExceptT $ do
      ExceptT $ syncBaselineFrom executable rootPath rootFd syncGitFd
        (if provenBase == Just (oldHead) then provenBase else Nothing) commit
      ExceptT (syncRecordDirectoryAndBarrier recordsFd entries)
      ExceptT (syncReferenceDirectories syncGitFd branch)

    -- Hash every committed record file through a pinned descriptor in one
    -- Git process, and compare each with its journaled blob.
    verifyCommittedRecords recordsFd entries = withTrackedDescriptors $ \track -> tryIO $ do
      fds <- mapM (openCommitted track recordsFd) entries
      blobs <- gitBlobsForFds executable rootPath rootFd gitFd fds >>= either failText pure
      mapM_ compareBlob (zip entries blobs)

    openCommitted :: (Fd -> IO ()) -> Fd -> JournalEntry -> IO Fd
    openCommitted track recordsFd entry = case validateRecordPath (journalPath entry) of
      Left problem -> failText problem
      Right filename -> do
        fd <- Exception.try (openRecordTargetForHash recordsFd filename)
          >>= either (\(problem :: Exception.IOException) -> failText
                ("committed record path is unavailable: " <> Text.pack (show problem))) pure
        track fd
        status <- getFdStatus fd
        unless (isRegularFile status) (failText "committed record path is not a regular file")
        pure fd

    compareBlob (entry, actual) = unless (Just actual == (gitObjectNameText <$> journalBlob entry)) $
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

-- | An installing journal's entry as the record it installed. Validation
-- gave every such entry its blob, device and inode, and only an installing
-- journal reaches here; an entry without them is refused in the
-- validator's own words rather than given placeholder values.
journalToInstalled :: JournalEntry -> Either Text InstalledRecord
journalToInstalled entry = case (journalBlob entry, journalDevice entry, journalFile entry) of
  (Just blob, Just device, Just inode) -> Right InstalledRecord
    { installedRecordPath = journalPath entry
    , installedRecordBlob = blob
    , installedRecordDevice = device
    , installedRecordFile = inode
    }
  _ -> Left "journal phase and record ownership fields disagree"

removePreparingTemporary :: Fd -> FilePath -> IO (Either Text ())
removePreparingTemporary gitFd name = runExceptT $ do
  opened <- ExceptT $ tryPresent $ openFdAt (Just gitFd) name ReadOnly
    defaultFileFlags { nofollow = True, nonBlock = True, cloexec = True }
  case opened of
    Nothing -> pure ()
    Just fd -> do
      lift (closeFd fd)
      throwE ("temporary file exists without a recorded inode; operation journal requires operator inspection: " <> Text.pack name)

-- | Remove the reserved index the journal names only while it is still that
-- inode, re-proved through a fresh open immediately before the unlink.
cleanupJournalIndex :: Fd -> JournalIndex -> IO (Either Text ())
cleanupJournalIndex gitFd entry = runExceptT $ do
  opened <- orFail ("could not inspect reserved Git index: " <>) $ tryPresent $
    openFdAt (Just gitFd) (journalIndexName entry) ReadOnly
      defaultFileFlags { nofollow = True, nonBlock = True, cloexec = True }
  case opened of
    Nothing -> pure ()
    Just fd -> ExceptT $ Exception.bracket (pure fd) closeFd $ \indexFd -> runExceptT $ do
      fileStatus <- ExceptT (tryIO (getFdStatus indexFd))
      require (isRegularFile fileStatus) "reserved Git index is not regular"
      require (isReserved fileStatus) "reserved Git index inode changed before recovery"
      currentFd <- orFail ("reserved Git index path changed before recovery: " <>) $ tryIO $
        openFdAt (Just gitFd) (journalIndexName entry) ReadOnly
          defaultFileFlags { nofollow = True, nonBlock = True, cloexec = True }
      ExceptT $ Exception.bracket (pure currentFd) closeFd $ \_ -> runExceptT $ do
        now <- ExceptT (tryIO (getFdStatus currentFd))
        require (isReserved now) "reserved Git index inode changed immediately before unlink"
        ExceptT (tryIO (unlinkAt gitFd (journalIndexName entry)))
        ExceptT (syncFd gitFd)
  where
    isReserved status = deviceID status == journalIndexDevice entry
      && fileID status == journalIndexFile entry

-- | Remove a journaled staging file only while it is still the recorded
-- inode holding the recorded blob, re-proved through a fresh open
-- immediately before the unlink.
cleanupJournalTemporary :: Executable -> FilePath -> Fd -> Fd -> JournalEntry
                        -> IO (Either Text ())
cleanupJournalTemporary executable rootPath rootFd gitFd entry = runExceptT $ do
  opened <- ExceptT $ tryPresent $ openFdAt (Just gitFd) (journalTemporary entry) ReadOnly
    defaultFileFlags { nofollow = True, nonBlock = True, cloexec = False }
  case opened of
    Nothing -> pure ()
    Just fd -> do
      lift (setFdOption fd CloseOnExec False)
      ExceptT $ Exception.bracket (pure fd) closeFd $ \temporaryFd -> runExceptT $ do
        fileStatus <- ExceptT (tryIO (getFdStatus temporaryFd))
        case (journalBlob entry, journalDevice entry, journalFile entry) of
          (Just expectedBlob, Just expectedDevice, Just expectedFile)
            | isRegularFile fileStatus
                && deviceID fileStatus == expectedDevice
                && fileID fileStatus == expectedFile -> do
                  actual <- ExceptT (gitBlobForFd executable rootPath rootFd gitFd temporaryFd)
                  require (Text.strip actual == gitObjectNameText expectedBlob) "record temporary blob changed before cleanup"
                  currentFd <- orFail ("record temporary path changed before cleanup: " <>) $ tryIO $
                    openFdAt (Just gitFd) (journalTemporary entry) ReadOnly
                      defaultFileFlags { nofollow = True, nonBlock = True, cloexec = True }
                  ExceptT $ Exception.bracket (pure currentFd) closeFd $ \_ -> runExceptT $ do
                    now <- ExceptT (tryIO (getFdStatus currentFd))
                    require (deviceID now == expectedDevice && fileID now == expectedFile)
                      "record temporary inode changed before unlink"
                    ExceptT (tryIO (unlinkAt gitFd (journalTemporary entry)))
          _ -> throwE ("temporary ownership is incomplete; operation journal retained: " <> Text.pack (journalTemporary entry))
