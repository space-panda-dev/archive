{-# LANGUAGE OverloadedStrings #-}

-- | Creation of an Archive root and its bootstrap records.
--
-- This module deliberately owns the small amount of mutation needed by
-- @archive init@.  All values which become archival evidence are made by
-- Door's normal canonicalizer before they are written.  The subprocess
-- runner is a parameter so tests can exercise failures without replacing Git
-- or git-annex on the host. The steps live under "Archive.Door.Bootstrap":
-- pinned file-system writes, the ownership journal, Git, the release files
-- and records, and the check of an existing archive; this module orders them
-- and owns rollback.
module Archive.Door.Bootstrap
  ( BootstrapEnvironment (..)
  , InitResult (..)
  , defaultBootstrapEnvironment
  , initArchive
  , initArchiveWith
  ) where

import Archive.Door.Bootstrap.Abort
  ( failIO
  , failMutation
  , ioEither
  , tryIO
  , withCleanupFailure
  )
import Archive.Door.Bootstrap.Contents
  ( InitResult (..)
  , InstalledContract (..)
  , archiveVersion
  , contractDestinationFiles
  , contractInstance
  , currentTimestamp
  , findInitializationContract
  , initializationDraftFor
  , initializationNamespace
  , loadResources
  , operatorNamespace
  , personDraftFor
  , recordPathFor
  , specDestinationFiles
  )
import Archive.Door.Bootstrap.Git
  ( BootstrapEnvironment (..)
  , bootstrapEnvironmentFor
  , bootstrapHeadIsUnborn
  , checkGit
  , commitBootstrap
  , defaultBootstrapEnvironment
  , noAutomaticMaintenance
  , runChecked
  , verifyBootstrapStaged
  , verifyCommittedBootstrapState
  )
import Archive.Door.Bootstrap.Journal
  ( bootstrapJournalName
  , readBootstrapJournal
  , removeBootstrapJournal
  , removeBootstrapJournalAt
  , verifyBootstrapJournal
  , writeBootstrapJournal
  )
import Archive.Door.Bootstrap.Pinned
  ( createDirectoriesPinned
  , createDirectoryPinned
  , openDirectoryAt
  , removeCreatedPinned
  , safePathIsSymbolicLink
  , syncCreatedParentEntries
  , syncExistingRootParent
  , verifyRootStillPinned
  , writeFilesPinned
  )
import Archive.Door.Bootstrap.Tracked
  ( PathTracker
  , TrackedPath (..)
  , findTracked
  , removeTrackedEntryAt
  , rollback
  , track
  , trackedPathFor
  , trackedPaths
  , withPathTracker
  )
import Archive.Door.Bootstrap.Verify (verifyExistingContents)
import Archive.Door.Canonical (finalizeStored)
import Archive.Door.Contracts (validateContractInstance)
import Archive.Door.Identity
  ( allocateObjectId
  , identityNameText
  , newArchiveId
  , parseArchiveId
  , parseIdentityName
  )
import Archive.Door.Layout
  ( ArchiveRoot
  , archiveManifestBytes
  , archiveManifestPath
  , archiveRoot
  , contractsDirectoryPath
  , makeArchiveManifest
  , recordsDirectoryPath
  , specDirectoryPath
  )
import Archive.Door.MutationError
  ( MutationError (..)
  , mutationErrorMessage
  )
import Archive.Door.RecordCommit
  ( durableBootstrapGitState
  , withArchiveWriteLockAtPinned
  )
import Archive.Executables (resolveAnnexExecutable, resolveGitExecutable)
import qualified Control.Exception as Exception
import Control.Monad.Trans.Except (ExceptT (..), except, runExceptT, throwE, withExceptT)
import Data.Aeson.RFC8785 (encodeCanonical)
import Data.IORef (IORef, newIORef, readIORef, writeIORef)
import Data.List (sortOn)
import qualified Data.Set as Set
import Data.Text (Text)
import System.Directory
  ( createDirectory
  , doesDirectoryExist
  , doesFileExist
  , listDirectory
  )
import System.FilePath (isAbsolute, (</>), makeRelative, splitDirectories, takeDirectory)
import System.IO.Error (catchIOError, isDoesNotExistError)
import System.Posix.Files (deviceID, fileID, getFdStatus, getSymbolicLinkStatus)
import System.Posix.IO
  ( OpenFileFlags (cloexec, directory, nofollow)
  , OpenMode (ReadOnly)
  , closeFd
  , defaultFileFlags
  , openFd
  , openFdAt
  )
import System.Posix.Types (DeviceID, Fd, FileID)

-- | The production entry point.  The root is checked before any random
-- identifier is allocated, so refusing an existing root cannot alter it.
initArchive :: Text -> FilePath -> IO (Either MutationError InitResult)
initArchive operator root = case parseIdentityName operator of
  Left problem -> pure (Left (MutationRejected problem))
  Right _ -> do
    gitResult <- resolveGitExecutable
    annexResult <- resolveAnnexExecutable
    pureEnvironment gitResult annexResult
  where
    pureEnvironment (Left problem) _ = pure (Left (MutationConfigurationFailure problem))
    pureEnvironment _ (Left problem) = pure (Left (MutationConfigurationFailure problem))
    pureEnvironment (Right git) (Right annex) =
      initArchiveWith (bootstrapEnvironmentFor git annex) operator root

initArchiveWith :: BootstrapEnvironment -> Text -> FilePath -> IO (Either MutationError InitResult)
initArchiveWith environment operatorName root = runExceptT $ do
  _ <- withExceptT MutationRejected (except (parseIdentityName operatorName))
  rootState <- ExceptT (tryIO (inspectRoot root))
  case rootState of
    Left problem -> throwE (MutationRejected problem)
    Right (ExistingRoot existingPath existingRoot expectedDevice expectedFile) -> ExceptT
      (inspectExisting environment operatorName existingPath expectedDevice expectedFile existingRoot)
    Right (NewRoot rootPath archiveRootValue rootCreatedParents expectedDevice expectedFile) -> ExceptT
      (tryBuild environment operatorName rootPath expectedDevice expectedFile archiveRootValue rootCreatedParents)

data RootState
  = ExistingRoot FilePath ArchiveRoot DeviceID FileID
  | NewRoot FilePath ArchiveRoot [TrackedPath] DeviceID FileID

inspectRoot :: FilePath -> IO (Either Text RootState)
inspectRoot path = case archiveRoot path of
  Left problem -> pure (Left problem)
  Right archive -> do
    symlink <- safePathIsSymbolicLink path
    if symlink
      then pure (Left "archive root must not be a symbolic link")
      else do
        directory <- doesDirectoryExist path
        file <- doesFileExist path
        if file && not directory
          then pure (Left "archive root exists and is not a directory")
          else if directory
            then do
              before <- getSymbolicLinkStatus path
              entries <- listDirectory path
              after <- getSymbolicLinkStatus path
              if deviceID before /= deviceID after || fileID before /= fileID after
                then pure (Left "archive root changed while it was being inspected")
                else if null entries
                  then pure (Right (NewRoot path archive [] (deviceID before) (fileID before)))
                  else pure (Right (ExistingRoot path archive (deviceID before) (fileID before)))
            else do
              parents <- makeParents path
              case findTracked path parents of
                Nothing -> pure (Left "new archive root identity was not recorded")
                Just tracked -> pure (Right (NewRoot path archive parents
                  (trackedPathDevice tracked) (trackedPathFile tracked)))

-- Create only missing ancestors.  The returned list is in creation order and
-- is used for rollback if initialization fails before the first commit.
makeParents :: FilePath -> IO [TrackedPath]
makeParents path = do
  result <- withPathTracker [] (\tracker -> trackedPaths tracker >>= rollback)
    (\tracker -> makeParentsTracked tracker path)
  case result of
    Right created -> pure created
    Left (problem, paths) -> do
      cleanup <- tryIO (rollback paths)
      failMutation (withCleanupFailure problem cleanup)

makeParentsTracked :: PathTracker -> FilePath -> IO [TrackedPath]
makeParentsTracked tracker path = do
  let parent = takeDirectory path
  if parent == path || parent == "." || parent == ""
    then createOne path
    else do
      parentExists <- doesDirectoryExist parent
      if parentExists
        then createOne path
        else do
          createdParents <- makeParentsTracked tracker parent
          created <- createOne path
          pure (createdParents <> created)
  where
    createOne value = do
      createDirectory value
      tracked <- trackedPathFor value
      track tracker tracked
      pure [tracked]

tryBuild :: BootstrapEnvironment -> Text -> FilePath -> DeviceID -> FileID -> ArchiveRoot -> [TrackedPath] -> IO (Either MutationError InitResult)
tryBuild environment operator rootPath expectedDevice expectedFile root createdParents = do
  rootFdResult <- tryIO (openFd rootPath ReadOnly
    defaultFileFlags { directory = True, nofollow = True, cloexec = True })
  case rootFdResult of
    Left problem -> pure (Left problem)
    Right rootFd -> Exception.bracket (pure rootFd) closeFd $ \pinnedRoot -> do
      pinnedStatus <- getFdStatus pinnedRoot
      if deviceID pinnedStatus /= expectedDevice || fileID pinnedStatus /= expectedFile
        then pure (Left (MutationRejected "archive root changed before initialization was pinned"))
        else do
          commitAttempted <- newIORef False
          result <- withPathTracker createdParents
            (cleanupBeforeCommit rootPath pinnedRoot commitAttempted)
            (\tracker -> build environment operator rootPath pinnedRoot root tracker commitAttempted)
          case result of
            Right value -> pure (Right value)
            Left (problem, paths) -> do
              attempted <- readIORef commitAttempted
              if attempted
                then pure (Left (MutationUncertain
                  ("Git commit outcome is uncertain; initialized files were retained: "
                    <> mutationErrorMessage problem)))
                else do
                  cleanup <- tryIO (rollbackAtPinnedRoot rootPath pinnedRoot paths)
                  pure (Left (withCleanupFailure problem cleanup))

cleanupBeforeCommit :: FilePath -> Fd -> IORef Bool -> PathTracker -> IO ()
cleanupBeforeCommit rootPath pinnedRoot commitAttempted tracker = do
  attempted <- readIORef commitAttempted
  if attempted then pure () else
    trackedPaths tracker >>= rollbackAtPinnedRoot rootPath pinnedRoot

build :: BootstrapEnvironment -> Text -> FilePath -> Fd -> ArchiveRoot -> PathTracker -> IORef Bool -> IO InitResult
build environment operator rootPath rootFd root tracker commitAttempted = do
  resources <- loadResources
  operatorIdentity <- ioEither (parseIdentityName operator)
  archiveId <- newArchiveId
  _ <- ioEither (parseArchiveId archiveId)
  manifest <- ioEither (makeArchiveManifest archiveId archiveVersion)
  operatorId <- ioEither (allocateObjectId archiveId operatorNamespace (identityNameText operatorIdentity))
  claimId <- ioEither (allocateObjectId archiveId initializationNamespace "initial")
  personDraft <- personDraftFor operatorId
  timestamp <- currentTimestamp
  (personRevision, personRecord) <- ioEither (finalizeStored timestamp personDraft)
  personRecordPath <- ioEither (recordPathFor root personRevision)
  contract <- findInitializationContract resources
  claimDraft <- initializationDraftFor claimId operatorId personRevision contract
  claimInstance <- ioEither (contractInstance claimDraft)
  _ <- ioEither (validateContractInstance (installedContractSchema contract) claimInstance)
  (claimRevision, claimRecord) <- ioEither (finalizeStored timestamp claimDraft)
  claimRecordPath <- ioEither (recordPathFor root claimRevision)
  let files = [ (archiveManifestPath root, archiveManifestBytes manifest)
              , (personRecordPath, encodeCanonical personRecord)
              , (claimRecordPath, encodeCanonical claimRecord)
              ]
  specFiles <- specDestinationFiles root resources
  contractFiles <- contractDestinationFiles root resources
  specDirectory <- ioEither (specDirectoryPath root archiveVersion)
  let allFiles = files <> specFiles <> contractFiles
      -- Each record's fan-out directories (ADR 0053), outermost first.
      recordDirectories = nubOrdered (concat
        [ [takeDirectory (takeDirectory path), takeDirectory path]
        | path <- [personRecordPath, claimRecordPath] ])
      directories = [ rootPath </> "spec"
                    , specDirectory
                    , contractsDirectoryPath root
                    , recordsDirectoryPath root
                    ] <> recordDirectories <>
                    [ rootPath </> "content"
                    , rootPath </> "content" </> "sha256"
                    ]
  journal <- writeBootstrapJournal tracker rootFd rootPath archiveId operatorId allFiles
  createDirectoryPinned tracker rootFd rootPath ".archive-init-lock"
  createDirectoriesPinned tracker rootFd rootPath directories
  writeFilesPinned tracker rootFd rootPath allFiles
  createDirectoryPinned tracker rootFd rootPath ".git"
  Exception.bracket (openDirectoryAt rootFd ".git") closeFd $ \gitFd -> do
    runChecked environment rootPath rootFd (Just gitFd) "git" ["init", "--quiet"]
    locked <- withArchiveWriteLockAtPinned rootPath rootFd gitFd $ \writeLock -> do
      runChecked environment rootPath rootFd (Just gitFd) "git-annex" ["init"]
      runChecked environment rootPath rootFd (Just gitFd) "git"
        (noAutomaticMaintenance <> [ "add", "--", ":/archive.json", ":/spec", ":/contracts", ":/records" ])
      removeCreatedPinned tracker rootFd rootPath ".archive-init-lock"
      writeIORef commitAttempted True
      runChecked environment rootPath rootFd (Just gitFd) "git"
        (noAutomaticMaintenance <>
        [ "-c", "user.name=Archive"
        , "-c", "user.email=archive@localhost"
        , "-c", "core.hooksPath=/dev/null"
        , "commit", "--quiet", "-m", "Initialize archive"
        ])
      verifyCommittedBootstrapState environment rootPath rootFd gitFd journal
      durable <- durableBootstrapGitState writeLock
      case durable of
        Left problem -> pure (Left (MutationIoFailure problem))
        Right () -> do
          removeBootstrapJournal tracker rootFd rootPath
          paths <- trackedPaths tracker
          syncCreatedParentEntries rootPath paths
          syncExistingRootParent rootPath rootFd
          pure (Right InitResult { initArchiveId = archiveId, initOperatorId = operatorId })
    either (failMutation . addBootstrapContext) pure locked
  where
    addBootstrapContext problem = case problem of
      MutationUncertain message -> MutationUncertain
        ("bootstrap archive lock or durability barrier failed: " <> message)
      _ -> MutationIoFailure
        ("bootstrap archive lock or durability barrier failed: " <> mutationErrorMessage problem)

inspectExisting :: BootstrapEnvironment -> Text -> FilePath -> DeviceID -> FileID -> ArchiveRoot -> IO (Either MutationError InitResult)
inspectExisting environment requested rootPath expectedDevice expectedFile root = do
  result <- tryIO $ do
    rootFd <- openFd rootPath ReadOnly
      defaultFileFlags { directory = True, nofollow = True, cloexec = True }
    Exception.bracket (pure rootFd) closeFd $ \pinnedRoot -> do
      pinnedStatus <- getFdStatus pinnedRoot
      if deviceID pinnedStatus /= expectedDevice || fileID pinnedStatus /= expectedFile
        then failIO "archive root changed before inspection was pinned"
        else pure ()
      verifyRootStillPinned rootPath pinnedRoot
      gitOpen <- Exception.try (openDirectoryAt pinnedRoot ".git")
        :: IO (Either Exception.IOException Fd)
      pinnedGit <- case gitOpen of
        Left problem
          | isDoesNotExistError problem -> failIO "non-empty root is not a complete Archive"
          | otherwise -> Exception.throwIO problem
        Right fd -> pure fd
      Exception.bracket (pure pinnedGit) closeFd $ \pinnedGitDirectory -> do
        journalResult <- tryIO (readBootstrapJournal rootPath pinnedRoot)
        case journalResult of
          Left problem -> failMutation (MutationIntegrityFailure
            ("bootstrap ownership journal cannot be read safely: " <> mutationErrorMessage problem))
          Right Nothing -> do
            initResult <- verifyExisting environment requested rootPath pinnedRoot pinnedGitDirectory root
            locked <- withArchiveWriteLockAtPinned rootPath pinnedRoot pinnedGitDirectory $ \writeLock -> do
              checkGit environment rootPath pinnedRoot pinnedGitDirectory
              durable <- durableBootstrapGitState writeLock
              case durable of
                Left problem -> pure (Left (MutationIoFailure problem))
                Right () -> do
                  syncExistingRootParent rootPath pinnedRoot
                  pure (Right initResult)
            case locked of
              Left problem -> failMutation (MutationUncertain
                ("could not re-establish the initialized archive durability baseline: "
                  <> mutationErrorMessage problem))
              Right value -> pure value
          Right (Just (journal, journalIdentity)) -> do
            locked <- withArchiveWriteLockAtPinned rootPath pinnedRoot pinnedGitDirectory $ \writeLock -> do
              (initResult, expectedPaths) <-
                verifyExistingContents True requested rootPath root
              verifyBootstrapJournal journal pinnedRoot rootPath (initOperatorId initResult)
                initResult expectedPaths
              verifyBootstrapStaged environment rootPath pinnedRoot pinnedGitDirectory expectedPaths
              unborn <- bootstrapHeadIsUnborn environment rootPath pinnedRoot pinnedGitDirectory
              if unborn
                then commitBootstrap environment rootPath pinnedRoot pinnedGitDirectory
                else pure ()
              verifyCommittedBootstrapState environment rootPath pinnedRoot pinnedGitDirectory journal
              durable <- durableBootstrapGitState writeLock
              case durable of
                Left problem -> pure (Left (MutationIoFailure problem))
                Right () -> do
                  removeBootstrapJournalAt rootPath pinnedRoot journalIdentity
                  checkGit environment rootPath pinnedRoot pinnedGitDirectory
                  syncExistingRootParent rootPath pinnedRoot
                  pure (Right initResult)
            case locked of
              Left problem -> failMutation (MutationUncertain
                ("bootstrap recovery could not establish a durable initialized archive: "
                  <> mutationErrorMessage problem))
              Right value -> pure value
  pure $ case result of
    Left (MutationRejected problem) -> Left (MutationIntegrityFailure problem)
    other -> other

verifyExisting :: BootstrapEnvironment -> Text -> FilePath -> Fd -> Fd -> ArchiveRoot -> IO InitResult
verifyExisting environment requested rootPath rootFd gitFd root = do
  (result, _) <- verifyExistingContents False requested rootPath root
  checkGit environment rootPath rootFd gitFd
  pure result

-- Rollback stays beneath the opened root descriptor.  A child can replace the
-- configured root pathname while init runs; resolving tracked absolute paths
-- afterwards could follow that replacement into an unrelated directory.
-- The root entry and any newly made ancestors are left in place, so cleanup
-- never needs to unlink a pathname whose identity cannot be checked atomically.
rollbackAtPinnedRoot :: FilePath -> Fd -> [TrackedPath] -> IO ()
rollbackAtPinnedRoot rootPath rootFd paths =
  -- Keep the ownership proof until every other tracked path has been removed.
  -- If cleanup encounters a Git-created or replaced entry, the journal must
  -- remain available to make the partial state explicit on the next attempt.
  mapM_ removeTrackedPathAt (sortOn rollbackOrder paths)
  where
    rollbackOrder tracked =
      (trackedPathName tracked == rootPath </> bootstrapJournalName,
        negate (length (trackedPathName tracked)))

    removeTrackedPathAt tracked = do
      let path = trackedPathName tracked
      let relative = makeRelative rootPath path
          pieces = filter (/= ".") (splitDirectories relative)
      if isAbsolute relative || any (== "..") pieces
        then ioError (userError "tracked rollback path escapes the pinned archive root")
        else case reverse pieces of
          [] -> pure ()
          leaf : reversedParents ->
            withParentDirectory rootFd (reverse reversedParents) $ \parentFd ->
              removeTrackedEntryAt parentFd leaf tracked `catchIOError` ignoreMissing
      where
        ignoreMissing problem
          | isDoesNotExistError problem = pure ()
          | otherwise = ioError problem

    withParentDirectory directoryFd [] action = action directoryFd
    withParentDirectory directoryFd (component : rest) action =
      Exception.bracket
        (openFdAt (Just directoryFd) component ReadOnly
          defaultFileFlags { directory = True, nofollow = True, cloexec = True })
        closeFd
        (\childFd -> withParentDirectory childFd rest action)

nubOrdered :: Ord a => [a] -> [a]
nubOrdered = go Set.empty
  where
    go _ [] = []
    go seen (value : rest)
      | Set.member value seen = go seen rest
      | otherwise = value : go (Set.insert value seen) rest
