{-# LANGUAGE OverloadedStrings #-}

-- | Worktree copies of what a committed batch holds: content locations and
-- specification files.
--
-- Both are written only after the commit, from it, so they are regenerable
-- (D-1); a failure here is reported and never undoes the commit.
module Archive.Door.RecordCommit.Worktree
  ( installContentLink
  , installWorktreeFile
  ) where

import Archive.Door.RecordCommit.Descriptors
  ( fdValue
  , openDirectoryAt
  , ownerFileModes
  , readLinkAt
  , renameAt
  , syncDirectoryFdOrFail
  , syncStrongFdOrFail
  , withDirectoryChain
  , writeAll
  )
import Archive.Door.RecordCommit.Failure (failText)
import Archive.Door.RecordCommit.Paths
  ( ArchiveFile (..)
  , ContentLink (..)
  , validateArchiveFilePath
  , validateContentPath
  )
import qualified Control.Exception as Exception
import Control.Monad (unless)
import qualified Data.ByteString.Lazy as LazyByteString
import qualified Data.Text as Text
import Data.Unique (hashUnique, newUnique)
import System.FilePath (splitDirectories)
import System.Posix.IO
  ( OpenFileFlags (cloexec, creat, exclusive, nofollow)
  , OpenMode (WriteOnly)
  , closeFd
  , defaultFileFlags
  , openFdAt
  )
import System.Posix.Process (getProcessID)
import System.Posix.Types (Fd)
import Foreign.C.Error (eEXIST, getErrno, throwErrnoIfMinus1_)
import Foreign.C.String (CString, withCString)
import Foreign.C.Types (CInt (..))

foreign import ccall unsafe "symlinkat"
  c_symlinkat :: CString -> CInt -> CString -> IO CInt

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
