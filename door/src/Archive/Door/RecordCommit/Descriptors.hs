{-# LANGUAGE OverloadedStrings #-}

-- | Descriptor-relative filesystem primitives for Door's writer.
--
-- Every path the writer touches is reached from a pinned directory
-- descriptor, one component at a time and never through a symlink, so a
-- replaced directory cannot redirect a write. The full-sync calls are the
-- durability barriers the writer orders its renames, links and unlinks
-- around; callers keep that order.
module Archive.Door.RecordCommit.Descriptors
  ( fdValue
  , linkAt
  , listDirectoryDescriptor
  , makeDirectoryAtReporting
  , openDirectoryAt
  , openDirectoryChain
  , openRegularForSync
  , ownerFileModes
  , readLinkAt
  , removeEmptyChain
  , renameAt
  , sameDeviceAndFile
  , showFd
  , syncDirectoryChain
  , syncDirectoryFdOrFail
  , syncFd
  , syncRegularRelative
  , syncStrongFdOrFail
  , unlinkAt
  , withDirectoryChain
  , withDuplicatedBinaryHandle
  , writeAll
  ) where

import Archive.Door.RecordCommit.Failure (failText, tryIO)
import qualified Control.Exception as Exception
import Control.Monad (unless, when)
import qualified Data.ByteString as ByteString
import Data.List (inits)
import Data.Text (Text)
import qualified Data.Text as Text
import System.IO (Handle, hClose, hSetBinaryMode)
import System.Posix.Files (FileStatus, deviceID, fileID, getFdStatus, isRegularFile, ownerReadMode, ownerWriteMode, unionFileModes)
import System.Posix.IO
  ( OpenFileFlags (cloexec, directory, nofollow, nonBlock)
  , OpenMode (ReadOnly, WriteOnly)
  , closeFd
  , defaultFileFlags
  , dup
  , fdToHandle
  , openFdAt
  )
import System.Posix.Types (CSsize (..), Fd (..), FileMode)
import System.Posix.IO.ByteString (fdWriteBuf)
import Foreign.Marshal.Alloc (allocaBytes)
import Foreign.Ptr (Ptr, castPtr, nullPtr, plusPtr)
import Foreign.C.Error (eEXIST, eNOENT, eNOTEMPTY, getErrno, throwErrnoIfMinus1_)
import Foreign.C.String (CString, peekCString, peekCStringLen, withCString)
import Foreign.C.Types (CInt (..), CSize (..), CUInt (..))

foreign import ccall unsafe "linkat"
  c_linkat :: CInt -> CString -> CInt -> CString -> CInt -> IO CInt

foreign import ccall unsafe "unlinkat"
  c_unlinkat :: CInt -> CString -> CInt -> IO CInt

foreign import ccall unsafe "archive_unlinkat_directory"
  c_unlinkatDirectory :: CInt -> CString -> IO CInt


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

foreign import ccall safe "archive_open_directory_stream"
  c_openDirectoryStream :: CInt -> IO (Ptr ())

foreign import ccall unsafe "archive_read_directory_stream"
  c_readDirectoryStream :: Ptr () -> CString -> CSize -> IO CInt

foreign import ccall unsafe "archive_close_directory_stream"
  c_closeDirectoryStream :: Ptr () -> IO CInt

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

withDuplicatedBinaryHandle :: Fd -> (Handle -> IO a) -> IO a
withDuplicatedBinaryHandle fd action = Exception.bracket acquire hClose $ \handle -> do
  hSetBinaryMode handle True
  action handle
  where
    acquire = do
      duplicate <- dup fd
      fdToHandle duplicate `Exception.onException` closeFd duplicate


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

showFd :: Fd -> String
showFd (Fd value) = show value

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

sameDeviceAndFile :: FileStatus -> FileStatus -> Bool
sameDeviceAndFile first second =
  deviceID first == deviceID second && fileID first == fileID second

-- | Files Door creates are the owner's to read and write, never executable:
-- Git records the executable bit, and a record is not a program.
ownerFileModes :: FileMode
ownerFileModes = ownerReadMode `unionFileModes` ownerWriteMode
