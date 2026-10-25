-- | The archive write lock's representation, shared by the modules that
-- make up Door's writer.
--
-- The library lists this module privately ('other-modules'), and
-- "Archive.Door.RecordCommit" re-exports 'ArchiveWriteLock' without its
-- constructor, so code outside the writer cannot build a lock value without
-- acquiring the archive lock through 'withArchiveWriteLock'.
module Archive.Door.RecordCommit.Internal
  ( ArchiveWriteLock (..)
  , archiveWriteLockGitExecutableIdentity
  ) where

import Archive.Executables (Executable, executableIdentityText)
import Data.Text (Text)
import System.Posix.Types (Fd)

-- | The absolute archive root, its pinned descriptor, the pinned @.git@
-- descriptor, and the Git executable resolved when the lock was acquired.
data ArchiveWriteLock = ArchiveWriteLock FilePath Fd Fd Executable

archiveWriteLockGitExecutableIdentity :: ArchiveWriteLock -> Text
archiveWriteLockGitExecutableIdentity (ArchiveWriteLock _ _ _ executable) =
  executableIdentityText executable
