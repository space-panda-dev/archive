{-# LANGUAGE OverloadedStrings #-}

-- | Persistent transition orchestration above the pure transition planner.
module Archive.Door.TransitionCommit
  ( commitTransition
  ) where

import Archive.Door.Canonical (finalizeStored)
import Archive.Door.Layout (archiveRoot, parseSha256Digest, recordPath)
import Archive.Door.MutationError (MutationError (..))
import Archive.Door.Proposal (Proposal)
import Archive.Door.AdmissionDryRun (rememberCommitted, snapshotVerification)
import Archive.Door.RecordCommit
  ( CommitVerification (..)
  , archiveWriteLockGitExecutableIdentity
  , commitRecordBatchWithContent
  , withArchiveWriteLock
  , writeCommitCheckpoint
  )
import Archive.Door.TransitionDryRun
  ( TransitionPlan (..)
  , TransitionPlanningError (..)
  , dryRunTransitionForCommit
  , dryRunTransitionVerified
  )
import Data.Aeson (Value)
import Data.Aeson.RFC8785 (encodeCanonical)
import qualified Data.ByteString.Lazy as LazyByteString
import Data.Text (Text)
import qualified Data.Text as Text
import Data.Time (defaultTimeLocale, formatTime, getCurrentTime)
import System.FilePath (makeRelative)

commitTransition :: FilePath -> Proposal -> IO (Either MutationError TransitionPlan)
commitTransition root proposal = withArchiveWriteLock root $ \archiveLock -> do
  initial <- dryRunTransitionVerified root proposal
  case initial of
    Left problem -> pure (Left (transitionMutationError problem))
    Right (plan, snapshot)
      | plannedGitExecutableIdentity plan /= archiveWriteLockGitExecutableIdentity archiveLock ->
          pure (Left (MutationConfigurationFailure "configured Git executable changed before commit"))
      | transitionRetry plan -> pure (Right plan)
      | otherwise -> do
          timestamp <- currentTimestamp
          case finalizePlan root timestamp plan of
            Left problem -> pure (Left (MutationIntegrityFailure problem))
            Right records -> do
              let (base, proven) = snapshotVerification snapshot
                  verification = CommitVerification { verifiedBase = base, verifiedObjectsProven = proven }
              committed <- commitRecordBatchWithContent archiveLock
                (plannedTransitionSnapshot plan)
                "Record archive transition"
                records
                []
                (Just verification)
                (finalCheck plan)
              case committed of
                Left problem -> pure (Left problem)
                Right commit -> do
                  chain <- writeCommitCheckpoint archiveLock verification commit records
                  rememberCommitted root snapshot commit chain records
                  pure (Right plan)
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
