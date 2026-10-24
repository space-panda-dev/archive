{-# LANGUAGE OverloadedStrings #-}

-- | File-system steps beneath the pinned archive root descriptor.
--
-- Every directory and file bootstrap makes is created relative to an open
-- descriptor of the root it inspected, never by resolving the root's
-- pathname again, so a root replaced while init runs cannot redirect a write
-- into an unrelated directory. Each created path is tracked for rollback.
module Archive.Door.Bootstrap.Pinned
  ( syncDirectory
  , createDirectoriesPinned
  , createDirectoryPinned
  , openDirectoryAt
  , writeFilesPinned
  , removeCreatedPinned
  , syncCreatedParentEntries
  , syncExistingRootParent
  , safeRelativeComponents
  , verifyRootStillPinned
  , verifyGitDirectoryStillPinned
  , safePathIsSymbolicLink
  , rejectSymlink
  , ownerFileModes
  ) where

import Archive.Door.Bootstrap.Abort
  ( failIO
  , failMutation
  , safeClose
  , tryIO
  , withCleanupFailure
  )
import Archive.Door.Bootstrap.Tracked
  ( PathTracker
  , TrackedPath (..)
  , findTracked
  , removeTrackedEntryAt
  , sameTrackedIdentity
  , track
  , trackedPaths
  )
import Archive.Door.MutationError (MutationError (..))
import Control.Monad (unless)
import qualified Control.Exception as Exception
import qualified Data.ByteString.Lazy as LazyByteString
import Data.List (sort)
import qualified Data.Set as Set
import qualified Data.Text as Text
import Data.Unique (hashUnique, newUnique)
import Foreign.C.Error (throwErrnoIfMinus1_)
import Foreign.C.String (CString, withCString)
import Foreign.C.Types (CInt (..), CUInt (..))
import System.Directory (pathIsSymbolicLink)
import System.FilePath (isAbsolute, (</>), makeRelative, splitDirectories, takeDirectory, takeFileName)
import System.IO (hClose, hFlush)
import System.IO.Error (catchIOError, isDoesNotExistError)
import System.Posix.Files (deviceID, fileID, getFdStatus, getSymbolicLinkStatus, ownerModes, ownerReadMode, ownerWriteMode, unionFileModes)
import System.Posix.IO
  ( OpenFileFlags (cloexec, creat, directory, exclusive, nofollow, nonBlock)
  , OpenMode (ReadOnly, WriteOnly)
  , closeFd
  , defaultFileFlags
  , fdToHandle
  , openFd
  , openFdAt
  )
import System.Posix.Types (Fd (..), FileMode)

foreign import ccall safe "archive_fullsync_directory_fd"
  c_fullsyncDirectory :: CInt -> IO CInt

foreign import ccall unsafe "unlinkat"
  c_unlinkat :: CInt -> CString -> CInt -> IO CInt

foreign import ccall unsafe "mkdirat"
  c_mkdirat :: CInt -> CString -> CUInt -> IO CInt

foreign import ccall unsafe "linkat"
  c_linkat :: CInt -> CString -> CInt -> CString -> CInt -> IO CInt

syncDirectory :: Fd -> String -> IO ()
syncDirectory (Fd descriptor) label =
  throwErrnoIfMinus1_ label (c_fullsyncDirectory (fromIntegral descriptor))

createDirectoriesPinned :: PathTracker -> Fd -> FilePath -> [FilePath] -> IO ()
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

createDirectoryPinned :: PathTracker -> Fd -> FilePath -> FilePath -> IO ()
createDirectoryPinned tracker rootFd rootPath relative = Exception.mask_ $ do
  createDirectoriesPinned tracker rootFd rootPath [rootPath </> relative]

openOrCreateDirectory :: PathTracker -> FilePath -> Fd -> FilePath -> IO Fd
openOrCreateDirectory tracker absolute parent component = do
  opened <- Exception.try (openDirectoryAt parent component)
    :: IO (Either Exception.IOException Fd)
  case opened of
    Right child -> do
      status <- getFdStatus child
      paths <- trackedPaths tracker
      case findTracked absolute paths of
        Just tracked | sameTrackedIdentity tracked status -> pure child
        _ -> closeFd child >> failIO ("refusing unexpected or replaced bootstrap directory: " <> Text.pack absolute)
    Left problem
      | isDoesNotExistError problem -> do
          mkdirAt parent component
          child <- openDirectoryAt parent component
          status <- getFdStatus child
          track tracker (TrackedPath absolute (deviceID status) (fileID status))
          pure child
      | otherwise -> ioError problem

openDirectoryAt :: Fd -> FilePath -> IO Fd
openDirectoryAt parent component = openFdAt (Just parent) component ReadOnly
  defaultFileFlags { directory = True, nofollow = True, cloexec = True }

mkdirAt :: Fd -> FilePath -> IO ()
mkdirAt (Fd descriptor) name = withCString name $ \nameCString ->
  throwErrnoIfMinus1_ "mkdirat" (c_mkdirat (fromIntegral descriptor) nameCString
    (fromIntegral ownerModes))

writeFilesPinned :: PathTracker -> Fd -> FilePath
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

writeAtomicAt :: PathTracker -> FilePath -> Fd -> FilePath
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
      track tracker (TrackedPath destinationPath (deviceID status) (fileID status)))
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

withKnownDirectory :: PathTracker -> Fd -> FilePath -> [FilePath]
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

openOrCreateVerifiedDirectory :: PathTracker -> FilePath -> Fd -> FilePath -> IO Fd
openOrCreateVerifiedDirectory tracker absolute parent component = do
  child <- openDirectoryAt parent component
  status <- getFdStatus child
  paths <- trackedPaths tracker
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

withDirectoryComponents :: Fd -> [FilePath] -> (Fd -> IO a) -> IO a
withDirectoryComponents rootFd = go rootFd
  where
    go directoryFd [] action = action directoryFd
    go directoryFd (component : rest) action =
      Exception.bracket (openDirectoryAt directoryFd component) closeFd
        (\childFd -> go childFd rest action)

removeCreatedPinned :: PathTracker -> Fd -> FilePath -> FilePath -> IO ()
removeCreatedPinned tracker rootFd rootPath relative = do
  let absolute = rootPath </> relative
  paths <- trackedPaths tracker
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

-- | Files Door creates are the owner's to read and write, never executable:
-- Git records the executable bit, and a record is not a program.
ownerFileModes :: FileMode
ownerFileModes = ownerReadMode `unionFileModes` ownerWriteMode
