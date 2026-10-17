{-# LANGUAGE OverloadedStrings #-}

-- | Creation of an Archive root and its bootstrap records.
--
-- This module deliberately owns the small amount of mutation needed by
-- @archive init@.  All values which become archival evidence are made by
-- Door's normal canonicalizer before they are written.  The subprocess
-- runner is a parameter so tests can exercise failures without replacing Git
-- or git-annex on the host.
module Archive.Door.Bootstrap
  ( BootstrapEnvironment (..)
  , InitResult (..)
  , defaultBootstrapEnvironment
  , initArchive
  , initArchiveWith
  ) where

import Archive.Door.Canonical (canonicalizeDraftStrict, finalizeStored)
import Archive.Door.Catalog (loadContractCatalog)
import Archive.Door.MutationError
  ( MutationError (..)
  , mutationErrorMessage
  )
import Archive.Door.RecordCommit
  ( durableBootstrapGitState
  , withArchiveWriteLockAtPinned
  )
import Archive.Process (processErrorMessage, runProcessBoundedAt)
import Archive.Executables
  ( Executable
  , executablePath
  , executableStillMatches
  , resolveAnnexExecutable
  , resolveGitExecutable
  )
import Archive.Door.Contracts
  ( ContractBundle (contractBundleSchema)
  , validateContractBundle
  , validateContractInstance
  , validateCoreContextForSpecification
  )
import Archive.Door.Identity
  ( allocateObjectId
  , identityNameText
  , newArchiveId
  , parseArchiveId
  , parseIdentityName
  )
import Archive.Door.Json (decodeUnique)
import Archive.Door.Layout
  ( ArchiveManifest (..)
  , ArchiveRoot
  , Sha256Digest
  , archiveManifestBytes
  , archiveManifestPath
  , archiveRoot
  , contractPath
  , contractsDirectoryPath
  , digestText
  , makeArchiveManifest
  , manifestArchiveId
  , manifestValue
  , parseArchiveManifest
  , parseSha256Digest
  , recordPath
  , recordsDirectoryPath
  , specContextPath
  , specDirectoryPath
  , specDocumentPath
  , specVersionFilePath
  )
import Archive.Door.Types
  ( DraftEnvelope (..)
  , ObjectId
  , RecordBody (..)
  , RevisionId, revisionIdText
  , StoredEnvelope (..)
  , objectIdText
  , parseObjectId
  , parseStoredEnvelope
  )
import qualified Crypto.Hash.SHA256 as SHA256
import Control.Monad (unless, when)
import qualified Control.Exception as Exception
import Data.Aeson (Value (..), object, (.=))
import qualified Data.Aeson.Key as Key
import qualified Data.Aeson.KeyMap as KeyMap
import Data.Aeson.RFC8785 (encodeCanonical)
import qualified Data.ByteString.Lazy as LazyByteString
import qualified Data.ByteString as ByteString
import Data.IORef (IORef, newIORef, readIORef, writeIORef, modifyIORef')
import Data.List (sort, sortOn)
import qualified Data.Set as Set
import Data.Unique (hashUnique, newUnique)
import Data.Text (Text)
import qualified Data.Text as Text
import qualified Data.Text.Encoding as TextEncoding
import qualified Data.Text.Encoding.Error as TextError
import Data.Time (UTCTime, defaultTimeLocale, formatTime, getCurrentTime)
import Paths_archive (getDataFileName)
import System.Directory
  ( createDirectory
  , doesDirectoryExist
  , doesFileExist
  , listDirectory
  , pathIsSymbolicLink
  , removeDirectory
  )
import System.Exit (ExitCode (..))
import System.Environment (getEnvironment)
import System.FilePath (isAbsolute, (</>), makeRelative, splitDirectories, takeDirectory, takeFileName)
import System.IO (Handle, hClose, hFlush)
import System.IO.Error (catchIOError, isDoesNotExistError)
import System.Posix.IO
  ( OpenFileFlags (cloexec, creat, directory, exclusive, nofollow, nonBlock)
  , OpenMode (ReadOnly, WriteOnly)
  , closeFd
  , defaultFileFlags
  , dup
  , fdToHandle
  , openFd
  , openFdAt
  )
import System.Posix.Files (FileStatus, deviceID, fileID, getFdStatus, getSymbolicLinkStatus, isDirectory, isRegularFile, ownerModes, ownerReadMode, ownerWriteMode, unionFileModes)
import System.Posix.Types (DeviceID, Fd (..), FileID, FileMode)
import Foreign.C.Error (throwErrnoIfMinus1_)
import Foreign.C.String (CString, withCString)
import Foreign.C.Types (CInt (..), CUInt (..))

foreign import ccall safe "archive_fullsync_fd"
  c_fullsync :: CInt -> IO CInt

foreign import ccall safe "archive_fullsync_directory_fd"
  c_fullsyncDirectory :: CInt -> IO CInt

foreign import ccall unsafe "unlinkat"
  c_unlinkat :: CInt -> CString -> CInt -> IO CInt

foreign import ccall unsafe "archive_unlinkat_directory"
  c_unlinkatDirectory :: CInt -> CString -> IO CInt

foreign import ccall unsafe "mkdirat"
  c_mkdirat :: CInt -> CString -> CUInt -> IO CInt

foreign import ccall unsafe "linkat"
  c_linkat :: CInt -> CString -> CInt -> CString -> CInt -> IO CInt

data BootstrapEnvironment = BootstrapEnvironment
  { runSubprocess :: FilePath -> [String] -> Fd -> IO (ExitCode, Text, Text)
  , runPinnedSubprocess :: Maybe (FilePath -> [String] -> Fd -> Fd -> IO (ExitCode, Text, Text))
  }

data TrackedPath = TrackedPath
  { trackedPathName :: FilePath
  , trackedPathDevice :: DeviceID
  , trackedPathFile :: FileID
  }

-- This marker is ownership evidence only under the trusted local-operator
-- model also used by RecordCommit journals: it is reserved exclusively after
-- observing an empty, pinned root, and recovery binds it to that root inode.
-- It is not authentication against a hostile same-uid process that can edit
-- the archive filesystem.
data BootstrapJournal = BootstrapJournal
  { bootstrapJournalRootDevice :: Text
  , bootstrapJournalRootFile :: Text
  , bootstrapJournalArchiveId :: Text
  , bootstrapJournalOperatorId :: Text
  , bootstrapJournalFiles :: [(FilePath, Text)]
  } deriving (Eq, Show)

bootstrapJournalName :: FilePath
bootstrapJournalName = ".archive-init-journal"

data InitResult = InitResult
  { initArchiveId :: Text
  , initOperatorId :: Text
  } deriving (Eq, Show)

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

archiveVersion :: Text
archiveVersion = "archive/0.0.8"

archiveProgram :: Text
archiveProgram = "pkg:hackage/archive@0.0.1.0"

operatorNamespace :: Text
operatorNamespace = "archive.core/entity"

initializationNamespace :: Text
initializationNamespace = "archive.transition/operator_initialization"

initializationContractId :: Text
initializationContractId = "archive.transition/operator_initialization"

initializationContractVersion :: Text
initializationContractVersion = "1"

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
initArchiveWith environment operatorName root = do
  case parseIdentityName operatorName of
    Left problem -> pure (Left (MutationRejected problem))
    Right _ -> do
      rootStateResult <- tryIO (inspectRoot root)
      case rootStateResult of
        Left problem -> pure (Left problem)
        Right rootState -> case rootState of
          Left problem -> pure (Left (MutationRejected problem))
          Right (ExistingRoot existingPath existingRoot expectedDevice expectedFile) ->
            inspectExisting environment operatorName existingPath expectedDevice expectedFile existingRoot
          Right (NewRoot rootPath archiveRootValue rootCreatedParents expectedDevice expectedFile) -> do
            result <- tryBuild environment operatorName rootPath expectedDevice expectedFile archiveRootValue rootCreatedParents
            pure result

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
  tracker <- newIORef []
  result <- tryIO (makeParentsTracked tracker path)
    `Exception.onException` (readIORef tracker >>= rollback)
  case result of
    Right created -> pure created
    Left problem -> do
      paths <- readIORef tracker
      cleanup <- tryIO (rollback paths)
      failMutation (withCleanupFailure problem cleanup)

makeParentsTracked :: IORef [TrackedPath] -> FilePath -> IO [TrackedPath]
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
      modifyIORef' tracker (tracked :)
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
          tracker <- newIORef createdParents
          commitAttempted <- newIORef False
          result <- tryIO (build environment operator rootPath pinnedRoot root tracker commitAttempted)
            `Exception.onException`
              (cleanupBeforeCommit rootPath pinnedRoot tracker commitAttempted)
          case result of
            Right value -> pure (Right value)
            Left problem -> do
              attempted <- readIORef commitAttempted
              if attempted
                then pure (Left (MutationUncertain
                  ("Git commit outcome is uncertain; initialized files were retained: "
                    <> mutationErrorMessage problem)))
                else do
                  paths <- readIORef tracker
                  cleanup <- tryIO (rollbackAtPinnedRoot rootPath pinnedRoot paths)
                  pure (Left (withCleanupFailure problem cleanup))

cleanupBeforeCommit :: FilePath -> Fd -> IORef [TrackedPath] -> IORef Bool -> IO ()
cleanupBeforeCommit rootPath pinnedRoot tracker commitAttempted = do
  attempted <- readIORef commitAttempted
  if attempted then pure () else
    readIORef tracker >>= rollbackAtPinnedRoot rootPath pinnedRoot

build :: BootstrapEnvironment -> Text -> FilePath -> Fd -> ArchiveRoot -> IORef [TrackedPath] -> IORef Bool -> IO InitResult
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
          paths <- readIORef tracker
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

writeBootstrapJournal
  :: IORef [TrackedPath] -> Fd -> FilePath -> Text -> Text
  -> [(FilePath, LazyByteString.ByteString)] -> IO BootstrapJournal
writeBootstrapJournal tracker rootFd rootPath archiveId operatorId files = do
  verifyRootStillPinned rootPath rootFd
  entriesAtStart <- listDirectory rootPath
  unless (null entriesAtStart)
    (failIO "bootstrap ownership journal can only be created in an empty archive root")
  entries <- mapM journalEntry files
  status <- getFdStatus rootFd
  let journal = BootstrapJournal
        { bootstrapJournalRootDevice = Text.pack (show (deviceID status))
        , bootstrapJournalRootFile = Text.pack (show (fileID status))
        , bootstrapJournalArchiveId = archiveId
        , bootstrapJournalOperatorId = operatorId
        , bootstrapJournalFiles = sort entries
        }
      bytes = LazyByteString.toStrict (encodeCanonical (bootstrapJournalValue journal))
  when (ByteString.length bytes > bootstrapJournalByteLimit)
    (failIO "bootstrap ownership journal exceeds its size limit")
  opened <- openFdAt (Just rootFd) bootstrapJournalName WriteOnly
    defaultFileFlags
      { creat = Just ownerFileModes
      , exclusive = True
      , nofollow = True
      , cloexec = True
      }
  journalStatus <- getFdStatus opened
  modifyIORef' tracker
    (TrackedPath (rootPath </> bootstrapJournalName)
      (deviceID journalStatus) (fileID journalStatus) :)
  handle <- fdToHandle opened
  let writeAndSync = do
        ByteString.hPut handle bytes
        hFlush handle
        let Fd descriptor = opened
        throwErrnoIfMinus1_ "full fsync(bootstrap ownership journal)"
          (c_fullsync (fromIntegral descriptor))
  writeAndSync `Exception.onException` (do
      _ <- tryIO (safeClose handle)
      pure ())
  hClose handle
  syncDirectory rootFd "full fsync(bootstrap ownership journal directory)"
  verifyRootStillPinned rootPath rootFd
  pure journal
  where
    journalEntry (path, contents) = do
      let relative = makeRelative rootPath path
          bytes = LazyByteString.toStrict contents
      pure (relative, digestHex (SHA256.hash bytes))

bootstrapJournalByteLimit :: Int
bootstrapJournalByteLimit = 1024 * 1024

bootstrapJournalValue :: BootstrapJournal -> Value
bootstrapJournalValue journal = object
  [ "format" .= ("archive-init/1" :: Text)
  , "root_device" .= bootstrapJournalRootDevice journal
  , "root_file" .= bootstrapJournalRootFile journal
  , "archive_id" .= bootstrapJournalArchiveId journal
  , "operator_id" .= bootstrapJournalOperatorId journal
  , "files" .= map fileValue (bootstrapJournalFiles journal)
  ]
  where
    fileValue (path, digest) = object ["path" .= path, "sha256" .= digest]

removeBootstrapJournal :: IORef [TrackedPath] -> Fd -> FilePath -> IO ()
removeBootstrapJournal tracker rootFd rootPath = do
  removeCreatedPinned tracker rootFd rootPath bootstrapJournalName
  modifyIORef' tracker (filter ((/= rootPath </> bootstrapJournalName) . trackedPathName))
  syncDirectory rootFd "full fsync(bootstrap journal removal)"

syncDirectory :: Fd -> String -> IO ()
syncDirectory (Fd descriptor) label =
  throwErrnoIfMinus1_ label (c_fullsyncDirectory (fromIntegral descriptor))

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

data Resources = Resources
  { resourceVersion :: LazyByteString.ByteString
  , resourceSpecification :: LazyByteString.ByteString
  , resourceContext :: LazyByteString.ByteString
  , resourceContracts :: [InstalledContract]
  }

data InstalledContract = InstalledContract
  { installedContractId :: Text
  , installedContractVersion :: Text
  , installedContractDigest :: Sha256Digest
  , installedContractBytes :: LazyByteString.ByteString
  , installedContractSchema :: Value
  }

loadResources :: IO Resources
loadResources = do
  versionPath <- getDataFileName "spec/VERSION"
  specificationPath <- getDataFileName "spec/archive-spec.md"
  contextPath <- getDataFileName "spec/0.0.8/context.jsonld"
  contractDirectory <- getDataFileName "spec/contracts"
  versionBytes <- LazyByteString.readFile versionPath
  specificationBytes <- LazyByteString.readFile specificationPath
  contextBytes <- LazyByteString.readFile contextPath
  if versionBytes /= "0.0.8\n"
    then failIO "installed VERSION does not select archive/0.0.8"
    else pure ()
  context <- decodeResource contextBytes
  _ <- ioEither (validateCoreContextForSpecification archiveVersion context)
  -- The specification's own catalog says which bundles it installs
  -- (ADR 0057); no copy of it lives in this source. Each listed bundle is
  -- loaded by its digest path and must carry the listed identifier and
  -- version.
  catalog <- loadContractCatalog archiveVersion >>= ioEither
  contracts <- mapM (loadContractResource contractDirectory)
    [ "sha256-" <> Text.unpack (Text.drop 7 digest) <> ".json" | (_, _, digest) <- Set.toList catalog ]
  let installed = Set.fromList
        [ (installedContractId contract, installedContractVersion contract
          , digestText (installedContractDigest contract))
        | contract <- contracts
        ]
  if installed /= catalog
    then failIO "installed contract bundles are not the specification's catalog"
    else pure Resources
      { resourceVersion = versionBytes
      , resourceSpecification = specificationBytes
      , resourceContext = contextBytes
      , resourceContracts = sortOn installedContractId contracts
      }

loadContractResource :: FilePath -> FilePath -> IO InstalledContract
loadContractResource directory fileName = do
  bytes <- LazyByteString.readFile (directory </> fileName)
  value <- decodeResource bytes
  fields <- ioEither (objectFields "contract bundle" value)
  identifier <- ioEither (requiredString "id" fields)
  version <- ioEither (requiredString "version" fields)
  let digest = "sha256:" <> digestHex (SHA256.hashlazy bytes)
  parsedDigest <- ioEither (parseSha256Digest digest)
  if encodeCanonical value /= bytes
    then failIO "contract bundle is not RFC 8785 canonical"
    else do
      bundle <- ioEither (validateContractBundle identifier version digest value)
      let expectedName = "sha256-" <> Text.unpack (Text.drop 7 digest) <> ".json"
      if fileName /= expectedName
        then failIO "contract bundle filename does not match its digest"
        else pure InstalledContract
          { installedContractId = identifier
          , installedContractVersion = version
          , installedContractDigest = parsedDigest
          , installedContractBytes = bytes
          , installedContractSchema = contractBundleSchema bundle
          }

findInitializationContract :: Resources -> IO InstalledContract
findInitializationContract resources = case filter matches (resourceContracts resources) of
  [contract] -> pure contract
  _ -> failIO "operator initialization contract is not installed exactly once"
  where
    matches contract = installedContractId contract == initializationContractId
      && installedContractVersion contract == initializationContractVersion

personDraftFor :: Text -> IO Value
personDraftFor operatorId = pure (object
  [ "spec" .= archiveVersion
  , "object_id" .= operatorId
  , "shape" .= ("entity" :: Text)
  , "by" .= archiveProgram
  , "agent" .= operatorId
  , "payload" .= object ["kind" .= ("person" :: Text)]
  , "refs" .= ([] :: [Value])
  , "supersedes" .= ([] :: [Value])
  ])

initializationDraftFor :: Text -> Text -> Text -> InstalledContract -> IO Value
initializationDraftFor claimId operatorId personRevision contract = pure (object
  [ "spec" .= archiveVersion
  , "object_id" .= claimId
  , "shape" .= ("claim" :: Text)
  , "by" .= archiveProgram
  , "agent" .= operatorId
  , "payload" .= object []
  , "refs" .= [ reference "author", reference "authorizer", reference "appoints" ]
  , "supersedes" .= ([] :: [Value])
  , "contracts" .= [ object
      [ "id" .= installedContractId contract
      , "version" .= installedContractVersion contract
      , "digest" .= digestText (installedContractDigest contract)
      ]
    ]
  ])
  where
    reference role = object
      [ "role" .= (role :: Text)
      , "id" .= operatorId
      , "revision_id" .= personRevision
      ]

contractInstance :: Value -> Either Text Value
contractInstance (Object fields) = do
  payload <- maybe (Left "claim payload is required") Right (KeyMap.lookup "payload" fields)
  refs <- maybe (Left "claim refs are required") Right (KeyMap.lookup "refs" fields)
  pure (object ["payload" .= payload, "refs" .= refs])
contractInstance _ = Left "claim draft must be an object"

specDestinationFiles :: ArchiveRoot -> Resources -> IO [(FilePath, LazyByteString.ByteString)]
specDestinationFiles root resources = do
  specification <- ioEither (specDocumentPath root archiveVersion)
  version <- ioEither (specVersionFilePath root archiveVersion)
  context <- ioEither (specContextPath root archiveVersion)
  pure [ (version, resourceVersion resources)
       , (specification, resourceSpecification resources)
       , (context, resourceContext resources)
       ]

contractDestinationFiles :: ArchiveRoot -> Resources -> IO [(FilePath, LazyByteString.ByteString)]
contractDestinationFiles root resources = pure
  [ (contractPath root (installedContractDigest contract), installedContractBytes contract)
  | contract <- resourceContracts resources
  ]

recordPathFor :: ArchiveRoot -> Text -> Either Text FilePath
recordPathFor root revision = case parseSha256Digest revision of
  Right digest -> Right (recordPath root digest)
  Left problem -> Left problem

createDirectoriesPinned :: IORef [TrackedPath] -> Fd -> FilePath -> [FilePath] -> IO ()
createDirectoriesPinned tracker rootFd rootPath paths = Exception.mask_ $
  mapM_ createOne (sort (Set.toList (Set.fromList paths)))
  where
    createOne path = do
      components <- pathComponentsBelowRoot rootPath path
      ensureDirectories rootFd [] components
    ensureDirectories _ _ [] = pure ()
    ensureDirectories parent prefix (component : rest) = do
      let childPrefix = prefix <> [component]
          childRelative = joinComponents childPrefix
          childAbsolute = rootPath </> childRelative
      childFd <- openOrCreateDirectory tracker childAbsolute parent component
      Exception.bracket (pure childFd) closeFd $ \opened ->
        ensureDirectories opened childPrefix rest

createDirectoryPinned :: IORef [TrackedPath] -> Fd -> FilePath -> FilePath -> IO ()
createDirectoryPinned tracker rootFd rootPath relative = Exception.mask_ $ do
  createDirectoriesPinned tracker rootFd rootPath [rootPath </> relative]

openOrCreateDirectory :: IORef [TrackedPath] -> FilePath -> Fd -> FilePath -> IO Fd
openOrCreateDirectory tracker absolute parent component = do
  opened <- Exception.try (openDirectoryAt parent component)
    :: IO (Either Exception.IOException Fd)
  case opened of
    Right child -> do
      status <- getFdStatus child
      paths <- readIORef tracker
      case findTracked absolute paths of
        Just tracked | sameTrackedIdentity tracked status -> pure child
        _ -> closeFd child >> failIO ("refusing unexpected or replaced bootstrap directory: " <> Text.pack absolute)
    Left problem
      | isDoesNotExistError problem -> do
          mkdirAt parent component
          child <- openDirectoryAt parent component
          status <- getFdStatus child
          modifyIORef' tracker (TrackedPath absolute (deviceID status) (fileID status) :)
          pure child
      | otherwise -> ioError problem

openDirectoryAt :: Fd -> FilePath -> IO Fd
openDirectoryAt parent component = openFdAt (Just parent) component ReadOnly
  defaultFileFlags { directory = True, nofollow = True, cloexec = True }

mkdirAt :: Fd -> FilePath -> IO ()
mkdirAt (Fd descriptor) name = withCString name $ \nameCString ->
  throwErrnoIfMinus1_ "mkdirat" (c_mkdirat (fromIntegral descriptor) nameCString
    (fromIntegral ownerModes))

writeFilesPinned :: IORef [TrackedPath] -> Fd -> FilePath
                -> [(FilePath, LazyByteString.ByteString)] -> IO ()
writeFilesPinned tracker rootFd rootPath = mapM_ (Exception.mask_ . writeOne)
  where
    writeOne (path, bytes) = do
      components <- pathComponentsBelowRoot rootPath path
      case reverse components of
        [] -> failIO "bootstrap file path is empty"
        leaf : reversedParents -> do
          let parents = reverse reversedParents
          withKnownDirectory tracker rootFd rootPath parents $ \parent -> do
            writeAtomicAt tracker path parent leaf bytes

writeAtomicAt :: IORef [TrackedPath] -> FilePath -> Fd -> FilePath
              -> LazyByteString.ByteString -> IO ()
writeAtomicAt tracker destinationPath parent destination bytes = Exception.mask_ $ do
  unique <- newUnique
  let temporary = ".archive-init-" <> show (hashUnique unique)
  descriptor <- openFdAt (Just parent) temporary WriteOnly
    defaultFileFlags
      { creat = Just ownerFileModes
      , exclusive = True
      , nofollow = True
      , cloexec = True
      }
  handle <- fdToHandle descriptor
    `Exception.onException` unlinkAt parent temporary
  result <- tryIO (LazyByteString.hPut handle bytes >> hFlush handle
    >> (getFdStatus descriptor >>= \status ->
      modifyIORef' tracker (TrackedPath destinationPath (deviceID status) (fileID status) :))
    >> hClose handle
    >> linkAt parent temporary destination
    >> unlinkAt parent temporary)
    `Exception.onException` (do
      _ <- tryIO (safeClose handle)
      _ <- tryIO (unlinkAt parent temporary)
      pure ())
  case result of
    Right () -> pure ()
    Left problem -> do
      _ <- tryIO (safeClose handle)
      cleanup <- tryIO (unlinkAt parent temporary)
      failMutation (withCleanupFailure problem cleanup)

withKnownDirectory :: IORef [TrackedPath] -> Fd -> FilePath -> [FilePath]
                   -> (Fd -> IO a) -> IO a
withKnownDirectory tracker rootFd rootPath = go rootFd []
  where
    go directoryFd _ [] action = action directoryFd
    go directoryFd prefix (component : rest) action = do
      let nextPrefix = prefix <> [component]
          absolute = rootPath </> joinComponents nextPrefix
      Exception.bracket
        (openOrCreateVerifiedDirectory tracker absolute directoryFd component)
        closeFd
        (\childFd -> go childFd nextPrefix rest action)

openOrCreateVerifiedDirectory :: IORef [TrackedPath] -> FilePath -> Fd -> FilePath -> IO Fd
openOrCreateVerifiedDirectory tracker absolute parent component = do
  child <- openDirectoryAt parent component
  status <- getFdStatus child
  paths <- readIORef tracker
  case findTracked absolute paths of
    Just tracked | sameTrackedIdentity tracked status -> pure child
    _ -> closeFd child >> failIO ("refusing replaced bootstrap directory: " <> Text.pack absolute)

pathComponentsBelowRoot :: FilePath -> FilePath -> IO [FilePath]
pathComponentsBelowRoot rootPath path = case makeRelative rootPath path of
  relative -> safeRelativeComponents relative

safeRelativeComponents :: FilePath -> IO [FilePath]
safeRelativeComponents relative =
  let components = filter (`notElem` ["", "."]) (splitDirectories relative)
  in if isAbsolute relative || any (== "..") components
      then failIO "bootstrap path escapes the pinned archive root"
      else pure components

joinComponents :: [FilePath] -> FilePath
joinComponents [] = "."
joinComponents (first : rest) = foldl (</>) first rest

findTracked :: FilePath -> [TrackedPath] -> Maybe TrackedPath
findTracked path = findFirst . filter ((== path) . trackedPathName)
  where
    findFirst [] = Nothing
    findFirst (value : _) = Just value

withDirectoryComponents :: Fd -> [FilePath] -> (Fd -> IO a) -> IO a
withDirectoryComponents rootFd = go rootFd
  where
    go directoryFd [] action = action directoryFd
    go directoryFd (component : rest) action =
      Exception.bracket (openDirectoryAt directoryFd component) closeFd
        (\childFd -> go childFd rest action)

removeCreatedPinned :: IORef [TrackedPath] -> Fd -> FilePath -> FilePath -> IO ()
removeCreatedPinned tracker rootFd rootPath relative = do
  let absolute = rootPath </> relative
  paths <- readIORef tracker
  case findTracked absolute paths of
    Nothing -> failIO ("refusing to remove untracked bootstrap path: " <> Text.pack absolute)
    Just tracked -> do
      components <- safeRelativeComponents relative
      case reverse components of
        [] -> failIO "bootstrap lock path is empty"
        leaf : reversedParents -> withDirectoryComponents rootFd (reverse reversedParents) $ \parent ->
          removeTrackedEntryAt parent leaf tracked

syncCreatedParentEntries :: FilePath -> [TrackedPath] -> IO ()
syncCreatedParentEntries rootPath paths = mapM_ syncParent paths
  where
    syncParent tracked = do
      let path = trackedPathName tracked
          parentPath = takeDirectory path
          parentName = if parentPath == "" then "." else parentPath
          leaf = takeFileName path
      parentFd <- openFd parentName ReadOnly
        defaultFileFlags { directory = True, nofollow = True, cloexec = True }
      Exception.bracket (pure parentFd) closeFd $ \parent -> do
        opened <- Exception.try $ openFdAt (Just parent) leaf ReadOnly
          defaultFileFlags { nofollow = True, nonBlock = True, cloexec = True }
          :: IO (Either Exception.IOException Fd)
        case opened of
          Right entry -> Exception.bracket (pure entry) closeFd $ \fd -> do
            status <- getFdStatus fd
            unless (sameTrackedIdentity tracked status)
              (failIO ("bootstrap path changed before parent sync: " <> Text.pack path))
          Left problem
            | isDoesNotExistError problem
            , path == rootPath </> ".archive-init-lock" -> pure ()
            | isDoesNotExistError problem ->
                failIO ("required bootstrap path disappeared before parent sync: " <> Text.pack path)
            | otherwise -> ioError problem
        let Fd descriptor = parent
        throwErrnoIfMinus1_ "full fsync(bootstrap parent directory)"
          (c_fullsyncDirectory (fromIntegral descriptor))

syncExistingRootParent :: FilePath -> Fd -> IO ()
syncExistingRootParent rootPath rootFd = do
  let parentPath = takeDirectory rootPath
      parentName = if parentPath == "" then "." else parentPath
      leaf = takeFileName rootPath
  parentFd <- openFd parentName ReadOnly
    defaultFileFlags { directory = True, nofollow = True, cloexec = True }
  Exception.bracket (pure parentFd) closeFd $ \parent -> do
    child <- openDirectoryAt parent leaf
    Exception.bracket (pure child) closeFd $ \current -> do
      pinned <- getFdStatus rootFd
      status <- getFdStatus current
      unless (deviceID pinned == deviceID status && fileID pinned == fileID status)
        (failIO "archive root parent entry changed before durability sync")
    let Fd descriptor = parent
    throwErrnoIfMinus1_ "full fsync(archive root parent directory)"
      (c_fullsyncDirectory (fromIntegral descriptor))

linkAt :: Fd -> FilePath -> FilePath -> IO ()
linkAt (Fd descriptor) source destination =
  withCString source $ \sourceCString -> withCString destination $ \destinationCString ->
    throwErrnoIfMinus1_ "linkat" $ c_linkat (fromIntegral descriptor) sourceCString
      (fromIntegral descriptor) destinationCString 0

unlinkAt :: Fd -> FilePath -> IO ()
unlinkAt (Fd descriptor) name = withCString name $ \nameCString ->
  throwErrnoIfMinus1_ "unlinkat" (c_unlinkat (fromIntegral descriptor) nameCString 0)

readBootstrapJournal :: FilePath -> Fd -> IO (Maybe (BootstrapJournal, TrackedPath))
readBootstrapJournal rootPath rootFd = do
  opened <- Exception.try $ openFdAt (Just rootFd) bootstrapJournalName ReadOnly
    defaultFileFlags { nofollow = True, nonBlock = True, cloexec = True }
    :: IO (Either Exception.IOException Fd)
  case opened of
    Left problem
      | isDoesNotExistError problem -> pure Nothing
      | otherwise -> Exception.throwIO problem
    Right fd -> Exception.bracket (pure fd) closeFd $ \journalFd -> do
      status <- getFdStatus journalFd
      unless (isRegularFile status) (failIO "bootstrap ownership journal is not a regular file")
      duplicate <- dup journalFd
      handle <- fdToHandle duplicate
      bytes <- Exception.bracket (pure handle) hClose readBoundedBootstrapJournal
      value <- case decodeUnique (LazyByteString.fromStrict bytes) of
        Left problem -> failIO ("bootstrap ownership journal is malformed: " <> Text.pack problem)
        Right decoded -> pure decoded
      unless (encodeCanonical value == LazyByteString.fromStrict bytes)
        (failIO "bootstrap ownership journal is not canonical")
      journal <- ioEither (parseBootstrapJournal value)
      pure (Just (journal, TrackedPath (rootPath </> bootstrapJournalName)
        (deviceID status) (fileID status)))

readBoundedBootstrapJournal :: Handle -> IO ByteString.ByteString
readBoundedBootstrapJournal handle = collect [] 0
  where
    collect chunks total = do
      chunk <- ByteString.hGetSome handle (min 65536 (bootstrapJournalByteLimit + 1 - total))
      if ByteString.null chunk
        then pure (ByteString.concat (reverse chunks))
        else do
          let total' = total + ByteString.length chunk
          when (total' > bootstrapJournalByteLimit)
            (failIO "bootstrap ownership journal exceeds its size limit")
          collect (chunk : chunks) total'

parseBootstrapJournal :: Value -> Either Text BootstrapJournal
parseBootstrapJournal value = do
  fields <- objectFields "bootstrap ownership journal" value
  exactKeys ["format", "root_device", "root_file", "archive_id", "operator_id", "files"] fields
  format <- requiredString "format" fields
  when (format /= "archive-init/1") (Left "unsupported bootstrap ownership journal format")
  rootDevice <- requiredString "root_device" fields
  rootFile <- requiredString "root_file" fields
  archiveId <- requiredString "archive_id" fields
  _ <- parseArchiveId archiveId
  operatorId <- requiredString "operator_id" fields
  _ <- parseObjectId operatorId
  fileValue <- maybe (Left "bootstrap journal files are required")
    Right (KeyMap.lookup "files" fields)
  files <- case fileValue of
    Array values -> mapM parseBootstrapJournalFile (foldr (:) [] values)
    _ -> Left "bootstrap journal files must be an array"
  let paths = map fst files
  when (null files || paths /= sort paths || length paths /= Set.size (Set.fromList paths))
    (Left "bootstrap journal paths are empty, duplicated, or unsorted")
  when (bootstrapJournalName `elem` paths)
    (Left "bootstrap journal cannot list itself as an archive file")
  pure BootstrapJournal
    { bootstrapJournalRootDevice = rootDevice
    , bootstrapJournalRootFile = rootFile
    , bootstrapJournalArchiveId = archiveId
    , bootstrapJournalOperatorId = operatorId
    , bootstrapJournalFiles = files
    }
  where
    parseBootstrapJournalFile entry = do
      fields <- objectFields "bootstrap journal file" entry
      exactKeys ["path", "sha256"] fields
      path <- requiredString "path" fields
      digest <- requiredString "sha256" fields
      when (Text.length digest /= 64 || Text.any (not . isLowerHex) digest)
        (Left "bootstrap journal file digest is malformed")
      when (isAbsolute (Text.unpack path)
        || any (== "..") (splitDirectories (Text.unpack path)))
        (Left "bootstrap journal path escapes the archive root")
      pure (Text.unpack path, digest)
    isLowerHex character =
      (character >= '0' && character <= '9') || (character >= 'a' && character <= 'f')
    exactKeys expected fields =
      let actual = sort (map Key.toText (KeyMap.keys fields))
      in when (actual /= sort expected) (Left "bootstrap ownership journal has unknown or missing fields")

verifyBootstrapJournal
  :: BootstrapJournal -> Fd -> FilePath -> Text -> InitResult -> [FilePath] -> IO ()
verifyBootstrapJournal journal rootFd rootPath requestedOperator result expectedPaths = do
  status <- getFdStatus rootFd
  unless (bootstrapJournalRootDevice journal == Text.pack (show (deviceID status))
      && bootstrapJournalRootFile journal == Text.pack (show (fileID status)))
    (failIO "bootstrap ownership journal belongs to a different archive root")
  unless (bootstrapJournalArchiveId journal == initArchiveId result
      && bootstrapJournalOperatorId journal == initOperatorId result)
    (failIO "bootstrap ownership journal does not match the initialized archive")
  unless (bootstrapJournalOperatorId journal == requestedOperator)
    (failIO "requested operator differs from the bootstrap ownership journal")
  let journalPaths = map fst (bootstrapJournalFiles journal)
  unless (journalPaths == expectedPaths)
    (failIO "bootstrap ownership journal file list differs from the complete initialized archive")
  actualDigests <- mapM digestInstalledFile (bootstrapJournalFiles journal)
  unless (actualDigests == map snd (bootstrapJournalFiles journal))
    (failIO "an initialized archive file differs from its bootstrap ownership journal")
  where
    digestInstalledFile (relative, _) = do
      _ <- safeRelativeComponents relative
      let path = rootPath </> relative
      rejectSymlink "bootstrap file" path
      contents <- LazyByteString.readFile path
      verifyRootStillPinned rootPath rootFd
      pure (digestHex (SHA256.hash (LazyByteString.toStrict contents)))

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

removeBootstrapJournalAt :: FilePath -> Fd -> TrackedPath -> IO ()
removeBootstrapJournalAt rootPath rootFd tracked = do
  tracker <- newIORef [tracked]
  removeCreatedPinned tracker rootFd rootPath bootstrapJournalName
  syncDirectory rootFd "full fsync(bootstrap journal removal)"

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
  pure (digestHex (SHA256.hash (TextEncoding.encodeUtf8 contents)))

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

verifyExistingContents
  :: Bool -> Text -> FilePath -> ArchiveRoot -> IO (InitResult, [FilePath])
verifyExistingContents allowJournal requested rootPath root = do
  rootEntries <- listDirectory rootPath
  let expectedEntries = [".git", "archive.json", "spec", "contracts", "records", "content"]
        <> [bootstrapJournalName | allowJournal]
      expectedRoot = Set.fromList expectedEntries
  if Set.fromList rootEntries /= expectedRoot
    then failIO "non-empty root is not a complete Archive"
    else pure ()
  mapM_ (rejectSymlink rootPath)
    expectedEntries
  manifestBytes <- LazyByteString.readFile (archiveManifestPath root)
  manifest <- do
    value <- decodeResource manifestBytes
    ioEither (parseArchiveManifest value)
  if encodeCanonical (manifestValue manifest) /= manifestBytes
    then failIO "archive.json is not RFC 8785 canonical"
    else pure ()
  if manifestSpec manifest /= archiveVersion
    then failIO "archive uses a different specification"
    else pure ()
  resources <- loadResources
  verifyInstalledFiles rootPath root resources
  operatorIdentity <- ioEither (parseIdentityName requested)
  operatorId <- ioEither (allocateObjectId (manifestArchiveId manifest)
    operatorNamespace (identityNameText operatorIdentity))
  claimId <- ioEither (allocateObjectId (manifestArchiveId manifest)
    initializationNamespace "initial")
  operatorObjectId <- ioEither (parseObjectId operatorId)
  claimObjectId <- ioEither (parseObjectId claimId)
  records <- readBootstrapRecords root
  (personRevision, timestamp) <- verifyPersonRecord records operatorObjectId
  verifyClaimRecord records claimObjectId operatorObjectId personRevision timestamp resources
  versionPath <- ioEither (specVersionFilePath root archiveVersion)
  specificationPath <- ioEither (specDocumentPath root archiveVersion)
  contextPath <- ioEither (specContextPath root archiveVersion)
  let expectedPaths = sort (map (makeRelative rootPath)
        ([ archiveManifestPath root, versionPath, specificationPath, contextPath ]
          <> map (contractPath root . installedContractDigest) (resourceContracts resources)
          <> map bootstrapPath records))
  pure
    ( InitResult { initArchiveId = manifestArchiveId manifest, initOperatorId = operatorId }
    , expectedPaths
    )

verifyInstalledFiles :: FilePath -> ArchiveRoot -> Resources -> IO ()
verifyInstalledFiles rootPath root resources = do
  versionPath <- ioEither (specVersionFilePath root archiveVersion)
  specificationPath <- ioEither (specDocumentPath root archiveVersion)
  contextPath <- ioEither (specContextPath root archiveVersion)
  specDirectory <- ioEither (specDirectoryPath root archiveVersion)
  exact versionPath (resourceVersion resources)
  exact specificationPath (resourceSpecification resources)
  exact contextPath (resourceContext resources)
  mapM_ (rejectSymlink "installed directory")
    [ rootPath </> "spec"
    , specDirectory
    , contractsDirectoryPath root
    , recordsDirectoryPath root
    , rootPath </> "content"
    , rootPath </> "content" </> "sha256"
    ]
  specEntries <- listDirectory specDirectory
  if sort specEntries /= ["VERSION", "archive-spec.md", "context.jsonld"]
    then failIO "installed specification tree is incomplete or has extra files"
    else pure ()
  mapM_ (\contract -> exact
    (contractPath root (installedContractDigest contract))
    (installedContractBytes contract)) (resourceContracts resources)
  entries <- listDirectory (contractsDirectoryPath root)
  let expected = sort [takeFileName (contractPath root (installedContractDigest contract))
                      | contract <- resourceContracts resources]
  if sort entries /= expected
    then failIO "installed contract set is incomplete or has extra files"
    else pure ()
  contentEntries <- listDirectory (rootPath </> "content")
  if contentEntries /= ["sha256"]
    then failIO "installed content tree is incomplete or has extra files"
    else pure ()
  contentDigestEntries <- listDirectory (rootPath </> "content" </> "sha256")
  if not (null contentDigestEntries)
    then failIO "initialized content tree unexpectedly contains bytes"
    else pure ()
  where
    exact path bytes = do
      rejectSymlink "installed file" path
      existing <- LazyByteString.readFile path
      if existing == bytes then pure () else failIO ("installed file differs: " <> Text.pack path)

data BootstrapRecord = BootstrapRecord
  { bootstrapPath :: FilePath
  , bootstrapValue :: Value
  , bootstrapEnvelope :: StoredEnvelope
  }

readBootstrapRecords :: ArchiveRoot -> IO [BootstrapRecord]
readBootstrapRecords root = do
  entries <- sort <$> fannedEntries (recordsDirectoryPath root)
  if length entries /= 2 || any (not . isRecordName . takeFileName) entries
    then failIO "bootstrap record set is incomplete or has extra files"
    else mapM readOne entries
  where
    isRecordName name = Text.isPrefixOf "sha256-" (Text.pack name)
      && Text.isSuffixOf ".json" (Text.pack name)
    -- Files exactly two directories below records/ (ADR 0053); anything
    -- else, including a file at another depth, is an extra entry.
    fannedEntries directory = do
      firsts <- listDirectory directory
      fmap concat $ mapM (\first -> do
        let firstPath = directory </> first
        rejectSymlink "record directory" firstPath
        seconds <- listDirectory firstPath
        fmap concat $ mapM (\second -> do
          let secondPath = firstPath </> second
          rejectSymlink "record directory" secondPath
          map ((first </> second) </>) <$> listDirectory secondPath) seconds) firsts
    readOne name = do
      let path = recordsDirectoryPath root </> name
      rejectSymlink "record" path
      bytes <- LazyByteString.readFile path
      value <- decodeResource bytes
      envelope <- ioEither (parseStoredEnvelope value)
      let pathDigest = Text.pack (takeFileName name)
      revisionDigest <- case Text.stripPrefix "sha256-" pathDigest >>= Text.stripSuffix ".json" of
        Nothing -> failIO ("record path is not digest-addressed: " <> Text.pack path)
        Just hexValue -> ioEither (parseSha256Digest ("sha256:" <> hexValue))
      storedDigest <- ioEither (parseSha256Digest (revisionText (storedRevision envelope)))
      if encodeCanonical value /= bytes
        then failIO ("record is not canonical: " <> Text.pack path)
        else if revisionDigest /= storedDigest || recordPath root storedDigest /= path
          then failIO ("record path does not match its revision: " <> Text.pack path)
        else pure BootstrapRecord
          { bootstrapPath = path
          , bootstrapValue = value
          , bootstrapEnvelope = envelope
          }

verifyPersonRecord :: [BootstrapRecord] -> ObjectId -> IO (Text, Text)
verifyPersonRecord records operatorId = do
  matching <- pure [record | record <- records
    , envelopeObject (storedDraft (bootstrapEnvelope record)) == operatorId
    , envelopeBody (storedDraft (bootstrapEnvelope record)) == EntityRecord (object ["kind" .= ("person" :: Text)])]
  record <- case matching of
    [value] -> pure value
    _ -> failIO "bootstrap person record is missing or duplicated"
  let envelope = bootstrapEnvelope record
      at = storedAt envelope
      draftValue = removeStoredFields (bootstrapValue record)
  (revision, _) <- ioEither (canonicalizeDraftStrict draftValue)
  let storedRevisionText = revisionText (storedRevision envelope)
  if revision /= storedRevisionText
    then failIO "bootstrap person revision is inconsistent"
    else pure (revision, at)

verifyClaimRecord :: [BootstrapRecord] -> ObjectId -> ObjectId -> Text -> Text -> Resources -> IO ()
verifyClaimRecord records claimId operatorId personRevision timestamp resources = do
  matching <- pure [record | record <- records
    , envelopeObject (storedDraft (bootstrapEnvelope record)) == claimId]
  record <- case matching of
    [value] -> pure value
    _ -> failIO "bootstrap initialization claim is missing or duplicated"
  contract <- findInitializationContract resources
  expectedDraft <- initializationDraftFor (objectIdText claimId) (objectIdText operatorId) personRevision contract
  instanceValue <- ioEither (contractInstance expectedDraft)
  _ <- ioEither (validateContractInstance (installedContractSchema contract) instanceValue)
  (_, expectedRecord) <- ioEither (finalizeStored timestamp expectedDraft)
  if bootstrapValue record /= expectedRecord
    then failIO "bootstrap initialization claim differs from the accepted contract"
    else pure ()

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

verifyGitDirectoryStillPinned :: Fd -> Fd -> IO ()
verifyGitDirectoryStillPinned rootFd gitFd = do
  pinned <- getFdStatus gitFd
  currentFd <- openDirectoryAt rootFd ".git"
  Exception.bracket (pure currentFd) closeFd $ \current -> do
    status <- getFdStatus current
    unless (deviceID pinned == deviceID status && fileID pinned == fileID status)
      (failMutation (MutationRejected "Git directory changed during initialization"))

verifyRootStillPinned :: FilePath -> Fd -> IO ()
verifyRootStillPinned rootPath rootFd = do
  pinned <- getFdStatus rootFd
  current <- getSymbolicLinkStatus rootPath
  if sameDeviceAndFile pinned current
    then pure ()
    else failMutation (MutationRejected "archive root pathname changed during initialization")
  where
    sameDeviceAndFile left right = deviceID left == deviceID right && fileID left == fileID right

currentTimestamp :: IO Text
currentTimestamp = Text.pack . formatTime defaultTimeLocale "%Y-%m-%dT%H:%M:%S%QZ" <$> getCurrentTimeUTC

getCurrentTimeUTC :: IO UTCTime
getCurrentTimeUTC = getCurrentTime

revisionText :: RevisionId -> Text
revisionText = revisionIdText

removeStoredFields :: Value -> Value
removeStoredFields (Object fields) = Object (KeyMap.delete "at" (KeyMap.delete "revision_id" fields))
removeStoredFields value = value

objectFields :: Text -> Value -> Either Text (KeyMap.KeyMap Value)
objectFields _label (Object fields) = Right fields
objectFields label _ = Left (label <> " must be an object")

requiredString :: Text -> KeyMap.KeyMap Value -> Either Text Text
requiredString name fields = case KeyMap.lookup (Key.fromText name) fields of
  Just (String value) | not (Text.null value) -> Right value
  Just (String _) -> Left (name <> " must not be empty")
  Just _ -> Left (name <> " must be a string")
  Nothing -> Left (name <> " is required")

digestHex :: ByteString.ByteString -> Text
digestHex = Text.pack . concatMap byteHex . ByteString.unpack
  where
    byteHex byte = [digits !! fromIntegral (byte `div` 16), digits !! fromIntegral (byte `mod` 16)]
    digits = "0123456789abcdef"

ioEither :: Either Text a -> IO a
ioEither (Right value) = pure value
ioEither (Left problem) = failIO problem

decodeResource :: LazyByteString.ByteString -> IO Value
decodeResource bytes = case decodeUnique bytes of
  Left problem -> failIO (Text.pack problem)
  Right value -> pure value

trackedPathFor :: FilePath -> IO TrackedPath
trackedPathFor path = do
  status <- getSymbolicLinkStatus path
  pure (TrackedPath path (deviceID status) (fileID status))

safePathIsSymbolicLink :: FilePath -> IO Bool
safePathIsSymbolicLink path = pathIsSymbolicLink path `catchIOError` handler
  where
    handler problem
      | isDoesNotExistError problem = pure False
      | otherwise = failMutation (MutationIoFailure
          ("unable to inspect path " <> Text.pack path <> ": " <> Text.pack (show problem)))

rejectSymlink :: FilePath -> FilePath -> IO ()
rejectSymlink label path = do
  symlink <- safePathIsSymbolicLink path
  if symlink then failIO (Text.pack label <> " must not be a symbolic link") else pure ()

tryIO :: forall a. IO a -> IO (Either MutationError a)
tryIO action =
  (Right <$> action)
    `Exception.catch` (\(MutationAbort failure) -> pure (Left failure))
    `Exception.catch` (\(problem :: Exception.IOException) ->
      pure (Left (MutationIoFailure (Text.pack (show problem)))))

rollback :: [TrackedPath] -> IO ()
rollback paths = mapM_ removeTrackedPath (sortOn (negate . length . trackedPathName) paths)

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

removeTrackedEntryAt :: Fd -> FilePath -> TrackedPath -> IO ()
removeTrackedEntryAt directoryFd name tracked = Exception.bracket
  (openFdAt (Just directoryFd) name ReadOnly
    defaultFileFlags { nofollow = True, cloexec = True, nonBlock = True })
  closeFd
  $ \entryFd -> do
    status <- getFdStatus entryFd
    if sameTrackedIdentity tracked status
      then removeEntryAt directoryFd name (isDirectory status)
      else ioError (userError ("refusing to remove changed tracked path: " <> trackedPathName tracked))

removeEntryAt :: Fd -> FilePath -> Bool -> IO ()
removeEntryAt directoryFd name directory = withCString name $ \nameCString -> do
  let fdValue (Fd value) = fromIntegral value
  if directory
    then throwErrnoIfMinus1_ "unlinkat(AT_REMOVEDIR)" $
      c_unlinkatDirectory (fdValue directoryFd) nameCString
    else throwErrnoIfMinus1_ "unlinkat" $
      c_unlinkat (fdValue directoryFd) nameCString 0

removeTrackedPath :: TrackedPath -> IO ()
removeTrackedPath tracked = do
  statusResult <- Exception.try (getSymbolicLinkStatus path)
    :: IO (Either Exception.IOException FileStatus)
  case statusResult of
    Left problem
      | isDoesNotExistError problem -> pure ()
      | otherwise -> ioError problem
    Right status
      | not (sameTrackedIdentity tracked status) ->
          ioError (userError ("refusing to remove changed tracked path: " <> path))
      | otherwise -> removeDirectory path `catchIOError` ignoreMissing
  where
    path = trackedPathName tracked
    ignoreMissing problem
      | isDoesNotExistError problem = pure ()
      | otherwise = ioError problem

sameTrackedIdentity :: TrackedPath -> FileStatus -> Bool
sameTrackedIdentity tracked status =
  trackedPathDevice tracked == deviceID status
    && trackedPathFile tracked == fileID status

safeClose :: Handle -> IO ()
safeClose = hClose

withCleanupFailure :: MutationError -> Either MutationError () -> MutationError
withCleanupFailure original (Right ()) = original
withCleanupFailure original (Left cleanup) = MutationCleanupFailure original cleanup

newtype MutationAbort = MutationAbort MutationError
  deriving (Show)

instance Exception.Exception MutationAbort

failMutation :: MutationError -> IO a
failMutation = Exception.throwIO . MutationAbort

failIO :: Text -> IO a
failIO = failMutation . MutationRejected

nubOrdered :: Ord a => [a] -> [a]
nubOrdered = go Set.empty
  where
    go _ [] = []
    go seen (value : rest)
      | Set.member value seen = go seen rest
      | otherwise = value : go (Set.insert value seen) rest

-- | Files Door creates are the owner's to read and write, never executable:
-- Git records the executable bit, and a record is not a program.
ownerFileModes :: FileMode
ownerFileModes = ownerReadMode `unionFileModes` ownerWriteMode
