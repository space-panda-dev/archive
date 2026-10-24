{-# LANGUAGE OverloadedStrings #-}

-- | Staging, installing, synchronizing and removing a batch's record files.
--
-- A record is written to a staging file under @.git@, hashed by Git, and
-- hard-linked into @records/@; each step re-proves through @fstat@ that the
-- path still names the inode this operation created, and removal happens
-- only while the path, inode and Git blob all still match.
module Archive.Door.RecordCommit.Records
  ( InstalledRecord (..)
  , PreparedRecord (..)
  , cleanupInstalled
  , cleanupPrepared
  , closePrepared
  , freshTemporaryName
  , installPreparedAll
  , openRecordTargetForHash
  , prepareAll
  , preparedToJournal
  , syncRecordDirectoryAndBarrier
  , targetAbsent
  , verifyRecordsDirectory
  , withPinnedRecords
  , withRecordsDirectory
  ) where

import Archive.Door.RecordCommit.Descriptors
  ( linkAt
  , ownerFileModes
  , removeEmptyChain
  , sameDeviceAndFile
  , syncDirectoryFdOrFail
  , syncFd
  , syncStrongFdOrFail
  , unlinkAt
  , withDirectoryChain
  , writeAll
  )
import Archive.Door.RecordCommit.Failure (failText, tryIO)
import Archive.Door.RecordCommit.Git (gitBlobForFd, gitBlobsForFds, runGit)
import Archive.Door.RecordCommit.Journal (JournalEntry (..))
import Archive.Door.RecordCommit.Paths (RecordLocation (..), uniqueSorted, validateRecordPath)
import Archive.Executables (Executable)
import qualified Control.Exception as Exception
import Control.Monad (unless)
import qualified Data.ByteString.Lazy as LazyByteString
import Data.IORef (modifyIORef, newIORef, readIORef)
import Data.List (inits, sortOn)
import Data.Text (Text)
import qualified Data.Text as Text
import Data.Unique (hashUnique, newUnique)
import System.IO.Error (isDoesNotExistError)
import System.Posix.Files (FileStatus, deviceID, fileID, getFdStatus, isRegularFile)
import System.Posix.IO
  ( FdOption (CloseOnExec)
  , OpenFileFlags (cloexec, creat, directory, exclusive, nofollow, nonBlock)
  , OpenMode (ReadOnly, WriteOnly)
  , closeFd
  , defaultFileFlags
  , openFdAt
  , setFdOption
  )
import System.Posix.Process (getProcessID)
import System.Posix.Types (DeviceID, Fd, FileID)

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
