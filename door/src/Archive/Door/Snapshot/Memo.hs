{-# LANGUAGE OverloadedStrings #-}

-- | The snapshot this process last loaded or extended (ADR 0054).
module Archive.Door.Snapshot.Memo
  ( snapshotMemo
  , forgetSnapshots
  , remember
  , rememberCommitted
  ) where

import Archive.Door.References (insertRecord)
import Archive.Door.Snapshot.Data
  ( AdmissionSnapshotLoadError (..)
  , RecordEntry (..)
  , SnapshotData (..)
  )
import Archive.Door.Snapshot.Read
  ( decodeBytes
  , expectedRecordRevision
  , mapIntegrity
  , objectText
  , revisionText
  )
import Archive.Door.Types
  ( StoredEnvelope (..)
  , envelopeObject
  , parseStoredEnvelope
  )
import Archive.Door.VerifiedCheckpoint (summarizeEnvelope)
import Archive.Read.Core (CommittedRecord (..))
import qualified Data.ByteString.Lazy as LazyByteString
import Data.IORef (IORef, newIORef, writeIORef)
import Data.List (sortOn)
import qualified Data.Map.Strict as Map
import Data.Text (Text)
import qualified Data.Text as Text
import System.Directory (canonicalizePath)
import System.IO.Unsafe (unsafePerformIO)

-- | The last snapshot this process loaded or extended, keyed by archive root
-- and commit (ADR 0054). A commit is immutable, so its snapshot never goes
-- stale; a long-running Door answers each request on an unchanged HEAD from
-- memory and extends the snapshot after its own commits. It is never written
-- anywhere and is lost with the process.
snapshotMemo :: IORef (Maybe (FilePath, Text, SnapshotData))
snapshotMemo = unsafePerformIO (newIORef Nothing)
{-# NOINLINE snapshotMemo #-}

forgetSnapshots :: IO ()
forgetSnapshots = writeIORef snapshotMemo Nothing

remember :: FilePath -> SnapshotData -> IO ()
remember root data' = writeIORef snapshotMemo (Just (root, snapshotCommitId data', data'))

-- | Extend the remembered snapshot with a batch this process just committed
-- on top of it. Every added record was checked by this batch's plan; the
-- extended snapshot is what a full read of the new commit would build.
rememberCommitted :: FilePath -> SnapshotData -> Text -> Maybe Int
                  -> [(FilePath, LazyByteString.ByteString)] -> IO ()
rememberCommitted rootPath base commit chain records = do
  canonical <- canonicalizePath rootPath
  case extendSnapshot base commit chain records of
    Left _ -> forgetSnapshots
    Right extended -> remember canonical extended

extendSnapshot :: SnapshotData -> Text -> Maybe Int -> [(FilePath, LazyByteString.ByteString)]
               -> Either AdmissionSnapshotLoadError SnapshotData
extendSnapshot base commit chain records = do
  added <- mapM entryFor records
  let store = foldl' (\index entry -> insertRecord (storedRevision (entryEnvelope entry))
        (storedDraft (entryEnvelope entry)) index) (snapshotStore base) added
      heads = foldl' (\current entry -> Map.insert (entryObject entry) (entryRevision entry) current)
        (snapshotHeadsByObject base) added
  pure base
    { snapshotCommitId = commit
    , snapshotRecords = mergeByPath (snapshotRecords base) (sortOn entryPath added)
    , snapshotStore = store
    , snapshotHeadsByObject = heads
    , snapshotRevisionIntroducers = foldl' (\current entry -> Map.insert (entryRevision entry) commit current)
        (snapshotRevisionIntroducers base) added
    , snapshotCommitOrder = snapshotCommitOrder base <> [commit]
    , snapshotImmutableAdditions = foldl' (\current entry -> Map.insert (entryPath entry) commit current)
        (snapshotImmutableAdditions base) added
    , snapshotRecordSummaries = foldl' (\current entry -> Map.insert (entryPath entry)
        (summarizeEnvelope (entryEnvelope entry)) current) (snapshotRecordSummaries base) added
    , snapshotCheckpointChain = chain
    , snapshotFromCheckpoint = True
    }
  where
    entryFor (path, bytes) = do
      revision <- mapIntegrity (expectedRecordRevision path)
      value <- mapIntegrity (decodeBytes path bytes)
      envelope <- mapIntegrity (parseStoredEnvelope value)
      if revisionText (storedRevision envelope) /= revision
        then Left (AdmissionSnapshotIntegrityError (Text.pack path <> " disagrees with embedded revision_id"))
        else Right RecordEntry
          { entryPath = path
          , entryRevision = revision
          , entryObject = objectText (envelopeObject (storedDraft envelope))
          , entryEnvelope = envelope
          , entryCommitted = CommittedRecord
              { committedPath = path
              , committedBytes = bytes
              , committedValue = value
              , committedEnvelope = envelope
              , committedContractsValid = True
              }
          }
    mergeByPath left [] = left
    mergeByPath [] right = right
    mergeByPath (l : ls) (r : rs)
      | entryPath l <= entryPath r = l : mergeByPath ls (r : rs)
      | otherwise = r : mergeByPath (l : ls) rs
