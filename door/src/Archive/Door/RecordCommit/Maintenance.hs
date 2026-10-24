{-# LANGUAGE OverloadedStrings #-}

-- | Object-store maintenance under the archive lock (ADR 0034, ADR 0052).
--
-- Packing and pruning change the store a verification checkpoint vouches
-- for, so each removes the checkpoint first, verifies every pack, and
-- re-establishes the reachable durability baseline on both sides of the
-- prune before any loose object is gone.
module Archive.Door.RecordCommit.Maintenance
  ( proveObjectBaseline
  , prunePackedGitObjects
  , repackGitObjects
  , writeBaseCheckpoint
  ) where

import Archive.Door.MutationError (MutationError (..))
import Archive.Door.RecordCommit.Descriptors (listDirectoryDescriptor, openDirectoryChain)
import Archive.Door.RecordCommit.Durability (syncGitObjectStoreBaseline)
import Archive.Door.RecordCommit.Failure (orFail, tryIO, unlessEither)
import Archive.Door.RecordCommit.Git (runGit, runGitStatus)
import Archive.Door.RecordCommit.Internal (ArchiveWriteLock (..))
import Archive.Door.RecordCommit.Lock (withArchiveWriteLock)
import Archive.Door.VerifiedCheckpoint (VerifiedCheckpoint, removeCheckpoint, writeCheckpoint)
import Archive.Executables (Executable)
import qualified Control.Exception as Exception
import Control.Monad.Trans.Class (lift)
import Control.Monad.Trans.Except (ExceptT (..), except, runExceptT, throwE)
import Data.Text (Text)
import qualified Data.Text as Text
import System.Exit (ExitCode (..))
import System.Posix.Files (deviceID, fileID, getFdStatus)
import System.Posix.IO (closeFd)
import System.Posix.Types (DeviceID, Fd, FileID)

-- | Remove loose objects whose packed copies have been verified and synced.
-- The archive lock must cover the complete preflight, prune, and postflight.
prunePackedGitObjects :: FilePath -> IO (Either MutationError ())
prunePackedGitObjects root = withArchiveWriteLock root $ \lock ->
  prunePackedGitObjectsLocked lock

-- | Pack loose Git objects under the archive lock (ADR 0034), keeping the
-- number of packs logarithmic, then remove the loose copies through the
-- verified prune: every pack is checked and the reachable baseline
-- synchronized before any loose object is removed. The checkpoint is removed
-- first, because maintenance changes the store it vouches for (ADR 0052).
repackGitObjects :: FilePath -> IO (Either MutationError ())
repackGitObjects root = withArchiveWriteLock root $ \lock@(ArchiveWriteLock rootPath rootFd gitFd executable) -> runExceptT $ do
  orFail (operationFailure "could not remove the verification checkpoint: ")
    (removeCheckpoint gitFd (rootPath <> "/.git"))
  packed <- orFail (operationFailure "git repack failed: ") $
    runGitStatus executable rootPath rootFd gitFd Nothing
      ["repack", "-q", "-d", "--geometric=2", "--no-write-bitmap-index"]
  case packed of
    ExitFailure code -> throwE (MutationOperationFailure
      ("git repack exited " <> Text.pack (show code)))
    ExitSuccess -> ExceptT (prunePackedGitObjectsLocked lock)

-- | A lower-layer diagnostic, prefixed with what Door was doing.
operationFailure :: Text -> Text -> MutationError
operationFailure context problem = MutationOperationFailure (context <> problem)

-- | Prove and synchronize every object reachable from a commit (topology,
-- traversal, strict fsck, and synchronization), under the archive lock.
proveObjectBaseline :: ArchiveWriteLock -> Text -> IO (Either Text ())
proveObjectBaseline (ArchiveWriteLock rootPath rootFd gitFd executable) commit =
  syncGitObjectStoreBaseline executable rootPath rootFd gitFd commit

-- | Write a new base checkpoint for a commit verified in full while holding
-- the archive lock, after maintenance proved and synchronized its objects.
writeBaseCheckpoint :: ArchiveWriteLock -> VerifiedCheckpoint -> IO (Either Text ())
writeBaseCheckpoint (ArchiveWriteLock rootPath _ gitFd _) checkpoint =
  writeCheckpoint gitFd (rootPath <> "/.git") checkpoint

prunePackedGitObjectsLocked :: ArchiveWriteLock -> IO (Either MutationError ())
prunePackedGitObjectsLocked lock@(ArchiveWriteLock rootPath _ gitFd _) = runExceptT $ do
  -- Maintenance changes the object store a checkpoint vouches for (ADR 0052).
  packFd <- orFail (operationFailure "could not pin Git pack directory: ") $ runExceptT $ do
    ExceptT (removeCheckpoint gitFd (rootPath <> "/.git"))
    ExceptT (tryIO (openDirectoryChain gitFd ["objects", "pack"]))
  ExceptT $ Exception.bracket (pure packFd) closeFd $ \pinnedPackFd -> runExceptT $ do
    details <- orFail (operationFailure "could not inspect pinned Git pack directory: ")
      (tryIO (getFdStatus pinnedPackFd))
    ExceptT (prunePackedGitObjectsWithPackDirectory lock pinnedPackFd
      (deviceID details, fileID details))

-- | The prune itself. The preflight stops at its first failure; once
-- prune-packed has run, every postflight check runs whatever the prune
-- reported, and the first failure in a fixed order is the one reported.
prunePackedGitObjectsWithPackDirectory
  :: ArchiveWriteLock -> Fd -> (DeviceID, FileID) -> IO (Either MutationError ())
prunePackedGitObjectsWithPackDirectory
  (ArchiveWriteLock rootPath rootFd gitFd executable) packFd packIdentity = runExceptT $ do
  refs <- orFail (operationFailure "could not snapshot Git refs before prune-packed: ")
    (snapshotRefs executable rootPath rootFd gitFd)
  orFail (operationFailure "could not verify Git packs before prune-packed: ")
    (verifyAllPacks executable rootPath rootFd gitFd packFd packIdentity)
  orFail (operationFailure "could not establish the pre-prune Git durability baseline: ")
    (syncRefBaselines executable rootPath rootFd gitFd refs)
  packDirectoryBeforePrune <- lift (checkPackDirectoryIdentity gitFd packIdentity)
  pruned <- lift $ case packDirectoryBeforePrune of
    Left problem -> pure (Left problem)
    Right () -> runGitStatus executable rootPath rootFd gitFd Nothing ["prune-packed"]
  packDirectoryAfterPrune <- lift (checkPackDirectoryIdentity gitFd packIdentity)
  currentRefs <- orFail (operationFailure "could not snapshot Git refs after prune-packed: ")
    (snapshotRefs executable rootPath rootFd gitFd)
  verifiedAfter <- lift (verifyAllPacks executable rootPath rootFd gitFd packFd packIdentity)
  postflight <- lift (syncRefBaselines executable rootPath rootFd gitFd currentRefs)
  case (pruned, packDirectoryAfterPrune, currentRefs == refs, verifiedAfter, postflight) of
    (Left problem, _, _, _, _) -> refuse
      ("git prune-packed failed: " <> problem)
    (_, Left problem, _, _, _) -> refuse
      ("Git pack directory changed during prune-packed: " <> problem)
    (Right (ExitFailure code), _, _, _, _) -> refuse
      ("git prune-packed exited " <> Text.pack (show code))
    (_, _, False, _, _) -> refuse
      "Git refs changed while prune-packed held the archive lock"
    (_, _, _, Left problem, _) -> refuse
      ("could not verify Git packs after prune-packed: " <> problem)
    (_, _, _, _, Left problem) -> refuse
      ("could not establish the post-prune Git durability baseline: " <> problem)
    (Right ExitSuccess, Right (), True, Right (), Right ()) -> pure ()
  where
    refuse = throwE . MutationOperationFailure

-- Snapshot both the complete ref namespace and the checked-out symbolic head.
-- for-each-ref includes packed refs; HEAD itself is recorded even if detached.
snapshotRefs :: Executable -> FilePath -> Fd -> Fd -> IO (Either Text [(Text, Text)])
snapshotRefs executable rootPath rootFd gitFd = do
  refs <- runGit executable rootPath rootFd gitFd Nothing
    ["for-each-ref", "--format=%(refname)%00%(objectname)"]
  headName <- runGit executable rootPath rootFd gitFd Nothing ["symbolic-ref", "-q", "HEAD"]
  headObject <- runGit executable rootPath rootFd gitFd Nothing ["rev-parse", "--verify", "HEAD^{commit}"]
  pure $ do
    output <- refs
    symbolicHead <- headName
    object <- headObject
    parsed <- traverse parseRefLine (filter (not . Text.null) (Text.splitOn "\n" output))
    Right (("HEAD", Text.strip symbolicHead <> " " <> Text.strip object) : parsed)
  where
    parseRefLine line = case Text.splitOn "\NUL" line of
      [name, object] | not (Text.null name) && not (Text.null object) -> Right (name, object)
      _ -> Left "Git returned a malformed ref snapshot"

-- | Every ref's baseline is attempted, even after one fails; the first
-- failure is reported.
syncRefBaselines
  :: Executable -> FilePath -> Fd -> Fd -> [(Text, Text)] -> IO (Either Text ())
syncRefBaselines executable rootPath rootFd gitFd refs = do
  let objects = [object | (name, object) <- refs, name /= "HEAD"]
  results <- mapM (syncGitObjectStoreBaseline executable rootPath rootFd gitFd) objects
  pure (sequence_ results)

-- | Verify every pack index in the pinned pack directory, re-proving the
-- directory's identity around the listing and around each verify-pack.
-- Every index is verified even after one fails; the first failure is
-- reported.
verifyAllPacks
  :: Executable -> FilePath -> Fd -> Fd -> Fd -> (DeviceID, FileID) -> IO (Either Text ())
verifyAllPacks executable rootPath rootFd gitFd packFd packIdentity = runExceptT $ do
  ExceptT (checkPackDirectoryIdentity gitFd packIdentity)
  entries <- orFail ("could not enumerate pinned Git pack directory: " <>)
    (listDirectoryDescriptor packFd)
  ExceptT (checkPackDirectoryIdentity gitFd packIdentity)
  let indexes = filter (hasSuffix ".idx") entries
  results <- lift (mapM verify indexes)
  except (sequence_ results)
  where
    verify index = runExceptT $ do
      ExceptT (checkPackDirectoryIdentity gitFd packIdentity)
      result <- lift $ runGitStatus executable rootPath rootFd gitFd Nothing
        ["verify-pack", "objects/pack/" <> index]
      after <- lift (checkPackDirectoryIdentity gitFd packIdentity)
      except $ do
        either (Left . ("Git pack directory changed during verify-pack: " <>)) Right after
        case result of
          Left problem -> Left problem
          Right ExitSuccess -> Right ()
          Right (ExitFailure code) -> Left
            ("git verify-pack exited " <> Text.pack (show code) <> " for " <> Text.pack index)
    hasSuffix suffix value =
      length value >= length suffix && drop (length value - length suffix) value == suffix

checkPackDirectoryIdentity :: Fd -> (DeviceID, FileID) -> IO (Either Text ())
checkPackDirectoryIdentity gitFd (expectedDevice, expectedFile) = runExceptT $ do
  currentFd <- orFail ("could not reopen Git pack directory: " <>)
    (tryIO (openDirectoryChain gitFd ["objects", "pack"]))
  ExceptT $ Exception.bracket (pure currentFd) closeFd $ \directoryFd -> do
    status <- tryIO (getFdStatus directoryFd)
    pure $ do
      details <- status
      unlessEither (deviceID details == expectedDevice && fileID details == expectedFile)
        "pinned objects/pack directory identity changed"
      Right ()
