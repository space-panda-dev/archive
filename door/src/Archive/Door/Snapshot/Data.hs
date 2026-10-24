{-# LANGUAGE OverloadedStrings #-}

-- | What Door knows about one committed snapshot once it has been read.
--
-- Admission and transition planning, the remembered snapshot, and the
-- verification checkpoint all share these values, so they live apart from
-- the code that reads them from Git. Nothing here reads or writes anything.
module Archive.Door.Snapshot.Data
  ( Installed (..)
  , RecordEntry (..)
  , SnapshotData (..)
  , AdmissionSnapshotLoadError (..)
  , snapshotVerification
  , transitionKeys
  , firstKey
  , installedMap
  ) where

import Archive.Door.Contracts (ContractBundle)
import Archive.Door.References (StoreIndex)
import Archive.Door.Snapshot.Problem (SnapshotProblem)
import Archive.Door.Types (ObjectId, RevisionId, StoredEnvelope)
import Archive.Door.VerifiedCheckpoint
  ( RecordSummary
  , VerifiedCheckpoint (..)
  )
import Archive.GitSnapshot (GitSnapshotError)
import Archive.Read.Core (CommittedRecord)
import qualified Data.Map.Strict as Map
import qualified Data.Set as Set
import Data.Text (Text)
import qualified Data.Text as Text

data Installed = Installed
  { installedPath :: FilePath
  , installedKey :: (Text, Text, Text)
  , installedBundle :: ContractBundle
  }

-- | One committed record. Its revision and object are parsed where the
-- snapshot is loaded, so nothing downstream compares unchecked text.
data RecordEntry = RecordEntry
  { entryPath :: FilePath
  , entryRevision :: RevisionId
  , entryObject :: ObjectId
  , entryEnvelope :: StoredEnvelope
  , entryCommitted :: CommittedRecord
  }

data SnapshotData = SnapshotData
  { snapshotCommitId :: Text
  , snapshotManifestId :: Text
  , snapshotRecords :: [RecordEntry]
  , snapshotInstalled :: Map.Map (Text, Text, Text) ContractBundle
  , snapshotStore :: StoreIndex
  , snapshotHeadsByObject :: Map.Map ObjectId RevisionId
  , snapshotRevisionIntroducers :: Map.Map RevisionId Text
  -- ^ the commit that introduced each revision
  , snapshotCommitOrder :: [Text]
  , snapshotImmutableAdditions :: Map.Map FilePath Text
  , snapshotTransitionKeys :: Set.Set (Text, Text, Text)
  , snapshotRecordSummaries :: Map.Map FilePath RecordSummary
  , snapshotCheckpointChain :: Maybe Int
  , snapshotFromCheckpoint :: Bool
  -- ^ True when a checkpoint (ADR 0052), not a full read, vouched for this
  -- snapshot and the objects beneath it.
  }

data AdmissionSnapshotLoadError
  = AdmissionSnapshotGitError GitSnapshotError
  | AdmissionSnapshotIntegrityError SnapshotProblem
  deriving (Eq, Show)

-- | What a snapshot says was verified about its commit, for a commit made on
-- top of it, and whether a checkpoint (not just a fresh read) vouches for the
-- objects beneath it.
snapshotVerification :: SnapshotData -> (VerifiedCheckpoint, Bool)
snapshotVerification data' =
  ( VerifiedCheckpoint
      { checkpointCommit = snapshotCommitId data'
      , checkpointAdditions = snapshotImmutableAdditions data'
      , checkpointHistory = snapshotCommitOrder data'
      , checkpointRecords = snapshotRecordSummaries data'
      , checkpointChain = snapshotCheckpointChain data'
      }
  , snapshotFromCheckpoint data'
  )

transitionKeys :: [Installed] -> Set.Set (Text, Text, Text)
transitionKeys installed = Set.fromList
  [ installedKey item
  | item <- installed
  , Text.isPrefixOf "archive.transition/" (firstKey (installedKey item))
  ]

firstKey :: (Text, Text, Text) -> Text
firstKey (value, _, _) = value

installedMap :: [Installed] -> Map.Map (Text, Text, Text) ContractBundle
installedMap = Map.fromList . map (\item -> (installedKey item, installedBundle item))
