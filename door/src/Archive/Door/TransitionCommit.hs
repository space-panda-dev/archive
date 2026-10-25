{-# LANGUAGE OverloadedStrings #-}

-- | Persistent transition orchestration above the pure transition planner.
module Archive.Door.TransitionCommit
  ( commitTransition
  ) where

import Archive.Door.Canonical (finalizeStored)
import Archive.Door.Layout (archiveRoot, parseSha256Digest, recordPath)
import Archive.Door.MutationError (MutationError (..))
import Archive.Door.Proposal (Proposal)
import Archive.Door.AdmissionDryRun (forgetSnapshots, rememberCommitted, snapshotVerification)
import Archive.Door.RecordCommit
  ( ArchiveFile (..)
  , CommitVerification (..)
  , archiveWriteLockGitExecutableIdentity
  , commitRecordBatchWithFiles
  , withArchiveWriteLock
  , writeCommitBaseCheckpoint
  , writeCommitCheckpoint
  )
import Archive.Door.TransitionDryRun
  ( TransitionPlan (..)
  , TransitionPlanningError (..)
  , dryRunTransitionForCommit
  , dryRunTransitionVerified
  )
import Control.Monad (unless, when)
import Control.Monad.Trans.Class (lift)
import Control.Monad.Trans.Except (ExceptT (..), except, runExceptT, throwE, withExceptT)
import Data.Aeson (Value)
import Data.Aeson.RFC8785 (encodeCanonical)
import qualified Data.ByteString.Lazy as LazyByteString
import Data.Text (Text)
import qualified Data.Text as Text
import Data.Time (defaultTimeLocale, formatTime, getCurrentTime)
import System.FilePath (makeRelative)

commitTransition :: FilePath -> Proposal -> IO (Either MutationError TransitionPlan)
commitTransition root proposal = withArchiveWriteLock root $ \archiveLock -> runExceptT $ do
  (plan, snapshot) <- withExceptT transitionMutationError
    (ExceptT (dryRunTransitionVerified root proposal))
  when (plannedGitExecutableIdentity plan /= archiveWriteLockGitExecutableIdentity archiveLock)
    (throwE (MutationConfigurationFailure "configured Git executable changed before commit"))
  unless (transitionRetry plan) $ do
    timestamp <- lift currentTimestamp
    records <- withExceptT MutationIntegrityFailure (except (finalizePlan root timestamp plan))
    let (base, proven) = snapshotVerification snapshot
        verification = CommitVerification { verifiedBase = base, verifiedObjectsProven = proven }
        files = [ArchiveFile path bytes | (path, bytes) <- plannedSpecificationFiles plan]
    commit <- ExceptT $ commitRecordBatchWithFiles archiveLock
      (plannedTransitionSnapshot plan)
      (if null files then "Record archive transition" else "Upgrade archive specification")
      records
      []
      files
      (Just verification)
      (finalCheck plan)
    if null files
      then lift $ do
        chain <- writeCommitCheckpoint archiveLock verification commit records
        rememberCommitted root snapshot commit chain records
      -- An upgrade adds specification paths and bundles that a
      -- per-commit addition and the remembered snapshot do not
      -- carry, so it is recorded as a new base checkpoint, and
      -- the next request loads the new commit from it.
      else lift $ do
        _ <- writeCommitBaseCheckpoint archiveLock verification commit records
          (map archiveFilePath files)
        forgetSnapshots
  pure plan
  where
    finalCheck plan = do
      final <- dryRunTransitionForCommit root proposal
      pure $ case final of
        Left problem -> Left (transitionMutationError problem)
        Right rechecked
          | rechecked /= plan -> Left (MutationRejected "transition state changed before commit")
          | otherwise -> Right ()

transitionMutationError :: TransitionPlanningError -> MutationError
transitionMutationError problem = case problem of
  TransitionSnapshotFailure issue -> MutationSnapshotFailure issue
  TransitionSnapshotIntegrityFailure message -> MutationIntegrityFailure message
  TransitionProposalRejected message -> MutationRejected message
  TransitionProposalStale message -> MutationStale message

finalizePlan
  :: FilePath
  -> Text
  -> TransitionPlan
  -> Either Text [(FilePath, LazyByteString.ByteString)]
finalizePlan root timestamp plan = do
  archive <- archiveRoot root
  mapM (finalizeOne archive) (plannedTransitionRecords plan)
  where
    finalizeOne archive (plannedRevision, draft) = do
      (actualRevision, stored) <- finalizeStored timestamp draft
      if actualRevision == plannedRevision
        then pure ()
        else Left "finalized transition revision differs from its plan"
      digest <- parseSha256Digest plannedRevision
      let relative = makeRelative root (recordPath archive digest)
      pure (relative, encodeCanonical (stored :: Value))

currentTimestamp :: IO Text
currentTimestamp = Text.pack
  . formatTime defaultTimeLocale "%Y-%m-%dT%H:%M:%S%QZ"
  <$> getCurrentTime
