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
import Archive.Door.RecordCommit.Failure (tryIO, unlessEither)
import Archive.Door.RecordCommit.Git (runGit, runGitStatus)
import Archive.Door.RecordCommit.Internal (ArchiveWriteLock (..))
import Archive.Door.RecordCommit.Lock (withArchiveWriteLock)
import Archive.Door.VerifiedCheckpoint (VerifiedCheckpoint, removeCheckpoint, writeCheckpoint)
import Archive.Executables (Executable)
import qualified Control.Exception as Exception
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
repackGitObjects root = withArchiveWriteLock root $ \lock@(ArchiveWriteLock rootPath rootFd gitFd executable) -> do
  cleared <- removeCheckpoint gitFd (rootPath <> "/.git")
  case cleared of
    Left problem -> pure (Left (MutationOperationFailure ("could not remove the verification checkpoint: " <> problem)))
    Right () -> do
      packed <- runGitStatus executable rootPath rootFd gitFd Nothing
        ["repack", "-q", "-d", "--geometric=2", "--no-write-bitmap-index"]
      case packed of
        Left problem -> pure (Left (MutationOperationFailure ("git repack failed: " <> problem)))
        Right (ExitFailure code) -> pure (Left (MutationOperationFailure
          ("git repack exited " <> Text.pack (show code))))
        Right ExitSuccess -> prunePackedGitObjectsLocked lock

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
prunePackedGitObjectsLocked lock@(ArchiveWriteLock rootPath _ gitFd _) = do
  -- Maintenance changes the object store a checkpoint vouches for (ADR 0052).
  cleared <- removeCheckpoint gitFd (rootPath <> "/.git")
  opened <- either (pure . Left) (const (tryIO (openDirectoryChain gitFd ["objects", "pack"]))) cleared
  case opened of
    Left problem -> pure (Left (MutationOperationFailure
      ("could not pin Git pack directory: " <> problem)))
    Right packFd -> Exception.bracket (pure packFd) closeFd $ \pinnedPackFd -> do
      status <- tryIO (getFdStatus pinnedPackFd)
      case status of
        Left problem -> pure (Left (MutationOperationFailure
          ("could not inspect pinned Git pack directory: " <> problem)))
        Right details -> prunePackedGitObjectsWithPackDirectory lock pinnedPackFd
          (deviceID details, fileID details)

prunePackedGitObjectsWithPackDirectory
  :: ArchiveWriteLock -> Fd -> (DeviceID, FileID) -> IO (Either MutationError ())
prunePackedGitObjectsWithPackDirectory
  (ArchiveWriteLock rootPath rootFd gitFd executable) packFd packIdentity = do
  beforeRefs <- snapshotRefs executable rootPath rootFd gitFd
  case beforeRefs of
    Left problem -> operationFailure ("could not snapshot Git refs before prune-packed: " <> problem)
    Right refs -> do
      verifiedBefore <- verifyAllPacks executable rootPath rootFd gitFd packFd packIdentity
      case verifiedBefore of
        Left problem -> operationFailure ("could not verify Git packs before prune-packed: " <> problem)
        Right () -> do
          preflight <- syncRefBaselines executable rootPath rootFd gitFd refs
          case preflight of
            Left problem -> operationFailure
              ("could not establish the pre-prune Git durability baseline: " <> problem)
            Right () -> do
              packDirectoryBeforePrune <- checkPackDirectoryIdentity gitFd packIdentity
              pruned <- case packDirectoryBeforePrune of
                Left problem -> pure (Left problem)
                Right () -> runGitStatus executable rootPath rootFd gitFd Nothing ["prune-packed"]
              packDirectoryAfterPrune <- checkPackDirectoryIdentity gitFd packIdentity
              afterRefs <- snapshotRefs executable rootPath rootFd gitFd
              case afterRefs of
                Left problem -> operationFailure
                  ("could not snapshot Git refs after prune-packed: " <> problem)
                Right currentRefs -> do
                  verifiedAfter <- verifyAllPacks executable rootPath rootFd gitFd packFd packIdentity
                  postflight <- syncRefBaselines executable rootPath rootFd gitFd currentRefs
                  case (pruned, packDirectoryAfterPrune, currentRefs == refs, verifiedAfter, postflight) of
                    (Left problem, _, _, _, _) -> operationFailure
                      ("git prune-packed failed: " <> problem)
                    (_, Left problem, _, _, _) -> operationFailure
                      ("Git pack directory changed during prune-packed: " <> problem)
                    (Right (ExitFailure code), _, _, _, _) -> operationFailure
                      ("git prune-packed exited " <> Text.pack (show code))
                    (_, _, False, _, _) -> operationFailure
                      "Git refs changed while prune-packed held the archive lock"
                    (_, _, _, Left problem, _) -> operationFailure
                      ("could not verify Git packs after prune-packed: " <> problem)
                    (_, _, _, _, Left problem) -> operationFailure
                      ("could not establish the post-prune Git durability baseline: " <> problem)
                    (Right ExitSuccess, Right (), True, Right (), Right ()) -> pure (Right ())
  where
    operationFailure = pure . Left . MutationOperationFailure

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

syncRefBaselines
  :: Executable -> FilePath -> Fd -> Fd -> [(Text, Text)] -> IO (Either Text ())
syncRefBaselines executable rootPath rootFd gitFd refs = do
  let objects = [object | (name, object) <- refs, name /= "HEAD"]
  results <- mapM (syncGitObjectStoreBaseline executable rootPath rootFd gitFd) objects
  pure (sequence_ results)

verifyAllPacks
  :: Executable -> FilePath -> Fd -> Fd -> Fd -> (DeviceID, FileID) -> IO (Either Text ())
verifyAllPacks executable rootPath rootFd gitFd packFd packIdentity = do
  identityBefore <- checkPackDirectoryIdentity gitFd packIdentity
  case identityBefore of
    Left problem -> pure (Left problem)
    Right () -> do
      names <- listDirectoryDescriptor packFd
      case names of
        Left problem -> pure (Left ("could not enumerate pinned Git pack directory: " <> problem))
        Right entries -> do
          identityAfterListing <- checkPackDirectoryIdentity gitFd packIdentity
          case identityAfterListing of
            Left problem -> pure (Left problem)
            Right () -> do
              let indexes = filter (hasSuffix ".idx") entries
              results <- mapM verify indexes
              pure (sequence_ results)
  where
    verify index = do
      before <- checkPackDirectoryIdentity gitFd packIdentity
      case before of
        Left problem -> pure (Left problem)
        Right () -> do
          result <- runGitStatus executable rootPath rootFd gitFd Nothing
            ["verify-pack", "objects/pack/" <> index]
          after <- checkPackDirectoryIdentity gitFd packIdentity
          pure $ do
            either (Left . ("Git pack directory changed during verify-pack: " <>)) Right after
            case result of
              Left problem -> Left problem
              Right ExitSuccess -> Right ()
              Right (ExitFailure code) -> Left
                ("git verify-pack exited " <> Text.pack (show code) <> " for " <> Text.pack index)
    hasSuffix suffix value =
      length value >= length suffix && drop (length value - length suffix) value == suffix

checkPackDirectoryIdentity :: Fd -> (DeviceID, FileID) -> IO (Either Text ())
checkPackDirectoryIdentity gitFd (expectedDevice, expectedFile) = do
  current <- tryIO (openDirectoryChain gitFd ["objects", "pack"])
  case current of
    Left problem -> pure (Left ("could not reopen Git pack directory: " <> problem))
    Right currentFd -> Exception.bracket (pure currentFd) closeFd $ \directoryFd -> do
      status <- tryIO (getFdStatus directoryFd)
      pure $ do
        details <- status
        unlessEither (deviceID details == expectedDevice && fileID details == expectedFile)
          "pinned objects/pack directory identity changed"
        Right ()
