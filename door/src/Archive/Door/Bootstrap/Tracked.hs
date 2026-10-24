-- | Paths bootstrap created, bound to the inode it created, for rollback.
--
-- A failed initialization removes only what it made, and only while each
-- path still names the file or directory it made: a path someone replaced
-- is refused, never removed.
module Archive.Door.Bootstrap.Tracked
  ( TrackedPath (..)
  , findTracked
  , trackedPathFor
  , sameTrackedIdentity
  , rollback
  , removeTrackedEntryAt
  ) where

import qualified Control.Exception as Exception
import Data.List (sortOn)
import Foreign.C.Error (throwErrnoIfMinus1_)
import Foreign.C.String (CString, withCString)
import Foreign.C.Types (CInt (..))
import System.Directory (removeDirectory)
import System.IO.Error (catchIOError, isDoesNotExistError)
import System.Posix.Files (FileStatus, deviceID, fileID, getFdStatus, getSymbolicLinkStatus, isDirectory)
import System.Posix.IO
  ( OpenFileFlags (cloexec, nofollow, nonBlock)
  , OpenMode (ReadOnly)
  , closeFd
  , defaultFileFlags
  , openFdAt
  )
import System.Posix.Types (DeviceID, Fd (..), FileID)

foreign import ccall unsafe "unlinkat"
  c_unlinkat :: CInt -> CString -> CInt -> IO CInt

foreign import ccall unsafe "archive_unlinkat_directory"
  c_unlinkatDirectory :: CInt -> CString -> IO CInt

data TrackedPath = TrackedPath
  { trackedPathName :: FilePath
  , trackedPathDevice :: DeviceID
  , trackedPathFile :: FileID
  }

findTracked :: FilePath -> [TrackedPath] -> Maybe TrackedPath
findTracked path = findFirst . filter ((== path) . trackedPathName)
  where
    findFirst [] = Nothing
    findFirst (value : _) = Just value

trackedPathFor :: FilePath -> IO TrackedPath
trackedPathFor path = do
  status <- getSymbolicLinkStatus path
  pure (TrackedPath path (deviceID status) (fileID status))

rollback :: [TrackedPath] -> IO ()
rollback paths = mapM_ removeTrackedPath (sortOn (negate . length . trackedPathName) paths)

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
