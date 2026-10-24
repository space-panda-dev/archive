{-# LANGUAGE OverloadedStrings #-}

-- | The shared mutation seam for Door record batches.
--
-- Validation belongs to the admission and transition planners.  This module
-- serializes their final write, installs only new immutable record files, and
-- advances the current branch with an expected-parent compare-and-swap. A
-- specification upgrade (ADR 0068) also adds the new release's documents and
-- bundles and replaces the manifest, in the same commit.
--
-- The writer's parts live in the @Archive.Door.RecordCommit.*@ modules, each
-- named by its concept; this module is the API the rest of Door uses.
module Archive.Door.RecordCommit
  ( -- | Abstract: the constructor stays in the private Internal module so
    -- the persistent writer cannot be called without first acquiring the
    -- archive lock.
    ArchiveWriteLock
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

import Archive.Door.MutationError (MutationError (..), mutationErrorMessage)
import Archive.Door.RecordCommit.Durability
  ( durableBootstrapGitState
  , syncBaselineFrom
  , syncReferenceDirectories
  , validateGitObjectStoreTopology
  )
import Archive.Door.RecordCommit.Failure (tryIO)
import Archive.Door.RecordCommit.Git
  ( gitBlobForBytes
  , gitBlobForFileBytes
  , gitDirectoryChangedMessage
  , gitDirectoryMatches
  , gitFailureMessage
  , gitPinChanged
  , rootDirectoryChangedMessage
  , rootDirectoryMatches
  , runGit
  , runGitStatus
  , runGitTyped
  )
import Archive.Door.RecordCommit.IndexReservation
  ( IndexReservation (..)
  , runIndexGit
  , withNewIndexReservation
  )
import Archive.Door.RecordCommit.Internal
  ( ArchiveWriteLock (..)
  , archiveWriteLockGitExecutableIdentity
  )
import Archive.Door.RecordCommit.Journal
  ( JournalEntry (..)
  , JournalIndex (..)
  , OperationJournal (..)
  , createOperationJournal
  , replaceOperationJournal
  )
import Archive.Door.RecordCommit.Lock (withArchiveWriteLock, withArchiveWriteLockAtPinned)
import Archive.Door.RecordCommit.Maintenance
  ( proveObjectBaseline
  , prunePackedGitObjects
  , repackGitObjects
  , writeBaseCheckpoint
  )
import Archive.Door.RecordCommit.Paths
  ( ArchiveFile (..)
  , ContentLink (..)
  , manifestFilePath
  , uniqueSorted
  , validateArchiveFilePath
  , validateContentLink
  , validateContentPath
  , validateRecordPath
  )
import Archive.Door.RecordCommit.Records
  ( InstalledRecord (..)
  , cleanupPrepared
  , closePrepared
  , freshTemporaryName
  , installPreparedAll
  , prepareAll
  , preparedToJournal
  , syncRecordDirectoryAndBarrier
  , targetAbsent
  , verifyRecordsDirectory
  , withRecordsDirectory
  )
import Archive.Door.RecordCommit.Recovery
  ( reconcileOperationJournal
  , reconcileOperationJournalWithRecords
  )
import Archive.Door.RecordCommit.Worktree (installContentLink)
import Archive.Door.VerifiedCheckpoint
  ( VerifiedCheckpoint (..)
  , appendCheckpoint
  , extendCheckpoint
  , summarizeStoredBytes
  , writeCheckpoint
  )
import Archive.Executables (Executable, executablePath, executableStillMatches)
import Archive.Process (processErrorMessage, runProcessBoundedAt)
import Control.Applicative ((<|>))
import qualified Control.Exception as Exception
import Data.IORef (newIORef, readIORef, writeIORef)
import qualified Data.ByteString.Lazy as LazyByteString
import Data.List (sort)
import qualified Data.Map.Strict as Map
import Data.Text (Text)
import qualified Data.Text as Text
import qualified Data.Text.Encoding as TextEncoding
import qualified Data.Text.Encoding.Error as TextError
import System.Environment (getEnvironment)
import System.Exit (ExitCode (..))
import System.FilePath (takeDirectory)
import System.IO (hPutStrLn, stderr)
import System.Posix.IO (closeFd, dup)
import System.Posix.Types (Fd)

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


-- | Install and commit one exact batch of new record files.
--
-- Paths are relative to the archive root.  A temporary Git index constructs a
-- tree with exactly these additions; update-ref then proves HEAD still equals
-- the snapshot validated by the caller.
commitRecordBatch
  :: ArchiveWriteLock
  -> Text
  -> Text
  -> [(FilePath, LazyByteString.ByteString)]
  -> IO (Either MutationError ())
  -> IO (Either MutationError Text)
commitRecordBatch lock expectedHead message records =
  commitRecordBatchWithContent lock expectedHead message records [] Nothing


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
