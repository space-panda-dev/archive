{-# LANGUAGE OverloadedStrings #-}

-- | Why a committed snapshot does not verify.
--
-- Loading a snapshot, checking its immutable history, and deriving its
-- object heads return these values; the planners render them with
-- 'snapshotProblemMessage' when they report an invalid committed snapshot.
-- Persistent admission and transition report that text under their own
-- refusal codes, and the Python suites compare it byte for byte, so this
-- function is the one place it is spelled.
module Archive.Door.Snapshot.Problem
  ( SnapshotProblem (..)
  , snapshotProblemMessage
  , snapshotCheck
  ) where

import Archive.Door.ContractRuleProblem (ContractRuleProblem, contractRuleMessage)
import Data.Bifunctor (first)
import Data.Text (Text)
import qualified Data.Text as Text

data SnapshotProblem
  -- Reading files from the pinned commit.
  = InvalidJson FilePath String
    -- ^ the decoder's own message
  | NotCanonical FilePath
  | NotUtf8 FilePath
  | NotUnique Text
    -- ^ what repeats, such as @record revision@
  -- Immutable path history.
  | HistoryOmitted FilePath
  | NotIntroducedWithSpecification FilePath
  | PathHistoryMismatch
  | WrittenBeforeContext FilePath Text
    -- ^ the record's path and its specification
  | ContractNotInstalled Text
  | CitedBeforeHeld FilePath Text
    -- ^ the record's path and the cited contract
  -- The governing specification and its upgrades (ADR 0068).
  | VersionFileMismatch
  | SpecificationEmpty
  | CatalogMismatch
  | LastUpgradeNotCurrent
  | NoRootManifest
  | UpgradeRecordOutsideUpgrade
  | ManifestNotCanonicalAt Text
    -- ^ the commit
  | CoreContextMissing Text
    -- ^ the specification
  | AdoptedBundlesMismatch
  | UpgradeChangedIdentity
  | UpgradeNotLater
  | UpgradeDocumentsMismatch
  | UpgradeRecordNotClaim
  | UpgradeRecordMismatch
  | UpgradeNotOneRecord
  -- Contract bundles and records.
  | ContractPathShape
  | ContractPathDigest
  | DigestPathMismatch FilePath
  | BundleNotObject
  | StoredRecordNotObject
  | EmbeddedRevisionDisagrees FilePath
  | RevisionMismatch FilePath
  | CitationRefused ContractRuleProblem
  -- Object heads and supersession.
  | NoUnsupersededHead Text
    -- ^ the object
  | ForkedHeads Text
    -- ^ the object
  | SupersessionCycle
  -- A checkpoint (ADR 0052) that does not match its commit.
  | CheckpointPathsMismatch
  | CheckpointUnknownShape
  | MissingFromCheckpoint FilePath
  -- | A check owned by another module (the archive layout, the envelope
  -- parser, Contracts, the specification files) failed; its own message
  -- passes through unchanged.
  | SnapshotCheck Text
  deriving (Eq, Show)

-- | Lift another module's 'Text' failure into this family unchanged.
snapshotCheck :: Either Text a -> Either SnapshotProblem a
snapshotCheck = first SnapshotCheck

snapshotProblemMessage :: SnapshotProblem -> Text
snapshotProblemMessage problem = case problem of
  InvalidJson path decoder -> Text.pack path <> ": invalid JSON: " <> Text.pack decoder
  NotCanonical path -> Text.pack path <> " is not RFC 8785 canonical"
  NotUtf8 path -> Text.pack path <> " is not valid UTF-8"
  NotUnique label -> label <> " must be unique"
  HistoryOmitted path -> "immutable history omitted " <> Text.pack path
  NotIntroducedWithSpecification path -> Text.pack path <> " was not introduced with the archive specification"
  PathHistoryMismatch -> "immutable archive path history does not match the committed tree"
  WrittenBeforeContext path specification -> Text.pack path <> " is written under " <> specification
    <> " before the archive held its core context"
  ContractNotInstalled identifier -> "contract is not installed at this snapshot: " <> identifier
  CitedBeforeHeld path identifier -> Text.pack path <> " cites " <> identifier <> " before the archive held it"
  VersionFileMismatch -> "specification VERSION does not match archive.json"
  SpecificationEmpty -> "copied specification is empty"
  CatalogMismatch -> "installed contract catalog does not match the selected specification"
  LastUpgradeNotCurrent -> "the last specification upgrade does not name the archive's specification"
  NoRootManifest -> "archive history has no root manifest"
  UpgradeRecordOutsideUpgrade -> "a specification upgrade record exists outside a specification upgrade commit"
  ManifestNotCanonicalAt commit -> "archive.json at " <> commit <> " is not RFC 8785 canonical"
  CoreContextMissing specification -> "the core context of " <> specification <> " has no @context object"
  AdoptedBundlesMismatch ->
    "a commit that adopted a specification did not add exactly the bundles its catalog adds"
  UpgradeChangedIdentity -> "a specification upgrade changed the archive identifier or layout"
  UpgradeNotLater -> "a specification upgrade does not name a later specification"
  UpgradeDocumentsMismatch -> "a specification upgrade commit did not add exactly its release's documents"
  UpgradeRecordNotClaim -> "a specification upgrade commit's record is not one upgrade claim"
  UpgradeRecordMismatch -> "a specification upgrade record does not name the manifest change it made"
  UpgradeNotOneRecord -> "a specification upgrade commit must introduce exactly one record"
  ContractPathShape -> "contract path must be contracts/sha256-<digest>.json"
  ContractPathDigest -> "contract path has an invalid digest"
  DigestPathMismatch path -> Text.pack path <> " does not match its digest path"
  BundleNotObject -> "contract bundle must be an object"
  StoredRecordNotObject -> "stored record must be an object"
  EmbeddedRevisionDisagrees path -> Text.pack path <> " disagrees with embedded revision_id"
  RevisionMismatch path -> Text.pack path <> " does not match its P-3 revision"
  CitationRefused rule -> contractRuleMessage rule
  NoUnsupersededHead object -> "object has no unsuperseded head: " <> object
  ForkedHeads object -> "object has forked unsuperseded heads: " <> object
  SupersessionCycle -> "supersession cycle detected"
  CheckpointPathsMismatch -> "checkpoint paths do not match the committed tree"
  CheckpointUnknownShape -> "checkpoint names an unknown record shape"
  MissingFromCheckpoint path -> Text.pack path <> " is missing from the checkpoint"
  SnapshotCheck message -> message
