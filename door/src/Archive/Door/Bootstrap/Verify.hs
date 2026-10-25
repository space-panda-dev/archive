{-# LANGUAGE OverloadedStrings #-}

-- | Whether a non-empty root is exactly the archive this init would make.
--
-- Init on an initialized archive succeeds only when every file, record, and
-- directory is what this Door would have written for the requested
-- operator; anything else is refused, never repaired.
module Archive.Door.Bootstrap.Verify
  ( verifyExistingContents
  ) where

import Archive.Door.Bootstrap.Abort (failIO, ioEither)
import Archive.Door.Bootstrap.Contents
  ( InitResult (..)
  , InstalledContract (..)
  , Resources (..)
  , archiveVersion
  , contractInstance
  , decodeResource
  , findInitializationContract
  , initializationDraftFor
  , initializationNamespace
  , loadResources
  , operatorNamespace
  )
import Archive.Door.Bootstrap.Journal (bootstrapJournalName)
import Archive.Door.Bootstrap.Pinned (rejectSymlink)
import Archive.Door.Canonical (canonicalizeDraftStrict, finalizeStored)
import Archive.Door.Contracts (validateContractInstance)
import Archive.Door.Identity (allocateObjectId, identityNameText, parseIdentityName)
import Archive.Door.Layout
  ( ArchiveManifest (..)
  , ArchiveRoot
  , archiveManifestPath
  , contractPath
  , contractsDirectoryPath
  , manifestValue
  , parseArchiveManifest
  , parseSha256Digest
  , recordPath
  , recordsDirectoryPath
  , specContextPath
  , specDirectoryPath
  , specDocumentPath
  , specVersionFilePath
  )
import Archive.Door.Types
  ( DraftEnvelope (..)
  , ObjectId
  , RecordBody (..)
  , RevisionId
  , StoredEnvelope (..)
  , parseObjectId
  , parseStoredEnvelope
  , revisionIdText
  )
import Data.Aeson (Value (..), object, (.=))
import qualified Data.Aeson.KeyMap as KeyMap
import Data.Aeson.RFC8785 (encodeCanonical)
import qualified Data.ByteString.Lazy as LazyByteString
import Data.List (sort)
import qualified Data.Set as Set
import Data.Text (Text)
import qualified Data.Text as Text
import System.Directory (listDirectory)
import System.FilePath ((</>), makeRelative, takeFileName)

verifyExistingContents
  :: Bool -> Text -> FilePath -> ArchiveRoot -> IO (InitResult, [FilePath])
verifyExistingContents allowJournal requested rootPath root = do
  rootEntries <- listDirectory rootPath
  let expectedEntries = [".git", "archive.json", "spec", "contracts", "records", "content"]
        <> [bootstrapJournalName | allowJournal]
      expectedRoot = Set.fromList expectedEntries
  if Set.fromList rootEntries /= expectedRoot
    then failIO "non-empty root is not a complete Archive"
    else pure ()
  mapM_ (rejectSymlink rootPath)
    expectedEntries
  manifestBytes <- LazyByteString.readFile (archiveManifestPath root)
  manifest <- do
    value <- decodeResource manifestBytes
    ioEither (parseArchiveManifest value)
  if encodeCanonical (manifestValue manifest) /= manifestBytes
    then failIO "archive.json is not RFC 8785 canonical"
    else pure ()
  if manifestSpec manifest /= archiveVersion
    then failIO "archive uses a different specification"
    else pure ()
  resources <- loadResources
  verifyInstalledFiles rootPath root resources
  operatorIdentity <- ioEither (parseIdentityName requested)
  operatorId <- ioEither (allocateObjectId (manifestArchiveId manifest)
    operatorNamespace (identityNameText operatorIdentity))
  claimId <- ioEither (allocateObjectId (manifestArchiveId manifest)
    initializationNamespace "initial")
  -- allocateObjectId spells a lowercase UUIDv5 urn:uuid, which parseObjectId
  -- always accepts, so neither parse below can refuse. Types offers no
  -- unchecked constructor on purpose (every ObjectId passed its parser),
  -- so the identifiers are typed by parsing rather than by trust.
  operatorObjectId <- ioEither (parseObjectId operatorId)
  claimObjectId <- ioEither (parseObjectId claimId)
  records <- readBootstrapRecords root
  (personRevision, timestamp) <- verifyPersonRecord records operatorObjectId
  verifyClaimRecord records claimObjectId operatorObjectId personRevision timestamp resources
  versionPath <- ioEither (specVersionFilePath root archiveVersion)
  specificationPath <- ioEither (specDocumentPath root archiveVersion)
  contextPath <- ioEither (specContextPath root archiveVersion)
  let expectedPaths = sort (map (makeRelative rootPath)
        ([ archiveManifestPath root, versionPath, specificationPath, contextPath ]
          <> map (contractPath root . installedContractDigest) (resourceContracts resources)
          <> map bootstrapPath records))
  pure
    ( InitResult { initArchiveId = manifestArchiveId manifest, initOperatorId = operatorId }
    , expectedPaths
    )

verifyInstalledFiles :: FilePath -> ArchiveRoot -> Resources -> IO ()
verifyInstalledFiles rootPath root resources = do
  versionPath <- ioEither (specVersionFilePath root archiveVersion)
  specificationPath <- ioEither (specDocumentPath root archiveVersion)
  contextPath <- ioEither (specContextPath root archiveVersion)
  specDirectory <- ioEither (specDirectoryPath root archiveVersion)
  exact versionPath (resourceVersion resources)
  exact specificationPath (resourceSpecification resources)
  exact contextPath (resourceContext resources)
  mapM_ (rejectSymlink "installed directory")
    [ rootPath </> "spec"
    , specDirectory
    , contractsDirectoryPath root
    , recordsDirectoryPath root
    , rootPath </> "content"
    , rootPath </> "content" </> "sha256"
    ]
  specEntries <- listDirectory specDirectory
  if sort specEntries /= ["VERSION", "archive-spec.md", "context.jsonld"]
    then failIO "installed specification tree is incomplete or has extra files"
    else pure ()
  mapM_ (\contract -> exact
    (contractPath root (installedContractDigest contract))
    (installedContractBytes contract)) (resourceContracts resources)
  entries <- listDirectory (contractsDirectoryPath root)
  let expected = sort [takeFileName (contractPath root (installedContractDigest contract))
                      | contract <- resourceContracts resources]
  if sort entries /= expected
    then failIO "installed contract set is incomplete or has extra files"
    else pure ()
  contentEntries <- listDirectory (rootPath </> "content")
  if contentEntries /= ["sha256"]
    then failIO "installed content tree is incomplete or has extra files"
    else pure ()
  contentDigestEntries <- listDirectory (rootPath </> "content" </> "sha256")
  if not (null contentDigestEntries)
    then failIO "initialized content tree unexpectedly contains bytes"
    else pure ()
  where
    exact path bytes = do
      rejectSymlink "installed file" path
      existing <- LazyByteString.readFile path
      if existing == bytes then pure () else failIO ("installed file differs: " <> Text.pack path)

data BootstrapRecord = BootstrapRecord
  { bootstrapPath :: FilePath
  , bootstrapValue :: Value
  , bootstrapEnvelope :: StoredEnvelope
  }

readBootstrapRecords :: ArchiveRoot -> IO [BootstrapRecord]
readBootstrapRecords root = do
  entries <- sort <$> fannedEntries (recordsDirectoryPath root)
  if length entries /= 2 || any (not . isRecordName . takeFileName) entries
    then failIO "bootstrap record set is incomplete or has extra files"
    else mapM readOne entries
  where
    isRecordName name = Text.isPrefixOf "sha256-" (Text.pack name)
      && Text.isSuffixOf ".json" (Text.pack name)
    -- Files exactly two directories below records/ (ADR 0053); anything
    -- else, including a file at another depth, is an extra entry.
    fannedEntries directory = do
      firsts <- listDirectory directory
      fmap concat $ mapM (\first -> do
        let firstPath = directory </> first
        rejectSymlink "record directory" firstPath
        seconds <- listDirectory firstPath
        fmap concat $ mapM (\second -> do
          let secondPath = firstPath </> second
          rejectSymlink "record directory" secondPath
          map ((first </> second) </>) <$> listDirectory secondPath) seconds) firsts
    readOne name = do
      let path = recordsDirectoryPath root </> name
      rejectSymlink "record" path
      bytes <- LazyByteString.readFile path
      value <- decodeResource bytes
      envelope <- ioEither (parseStoredEnvelope value)
      let pathDigest = Text.pack (takeFileName name)
      revisionDigest <- case Text.stripPrefix "sha256-" pathDigest >>= Text.stripSuffix ".json" of
        Nothing -> failIO ("record path is not digest-addressed: " <> Text.pack path)
        Just hexValue -> ioEither (parseSha256Digest ("sha256:" <> hexValue))
      storedDigest <- ioEither (parseSha256Digest (revisionText (storedRevision envelope)))
      if encodeCanonical value /= bytes
        then failIO ("record is not canonical: " <> Text.pack path)
        else if revisionDigest /= storedDigest || recordPath root storedDigest /= path
          then failIO ("record path does not match its revision: " <> Text.pack path)
        else pure BootstrapRecord
          { bootstrapPath = path
          , bootstrapValue = value
          , bootstrapEnvelope = envelope
          }

verifyPersonRecord :: [BootstrapRecord] -> ObjectId -> IO (RevisionId, Text)
verifyPersonRecord records operatorId = do
  matching <- pure [record | record <- records
    , envelopeObject (storedDraft (bootstrapEnvelope record)) == operatorId
    , envelopeBody (storedDraft (bootstrapEnvelope record)) == EntityRecord (object ["kind" .= ("person" :: Text)])]
  record <- case matching of
    [value] -> pure value
    _ -> failIO "bootstrap person record is missing or duplicated"
  let envelope = bootstrapEnvelope record
      at = storedAt envelope
      draftValue = removeStoredFields (bootstrapValue record)
  (revision, _) <- ioEither (canonicalizeDraftStrict draftValue)
  let storedRevisionText = revisionText (storedRevision envelope)
  if revision /= storedRevisionText
    then failIO "bootstrap person revision is inconsistent"
    else pure (storedRevision envelope, at)

verifyClaimRecord :: [BootstrapRecord] -> ObjectId -> ObjectId -> RevisionId -> Text -> Resources -> IO ()
verifyClaimRecord records claimId operatorId personRevision timestamp resources = do
  matching <- pure [record | record <- records
    , envelopeObject (storedDraft (bootstrapEnvelope record)) == claimId]
  record <- case matching of
    [value] -> pure value
    _ -> failIO "bootstrap initialization claim is missing or duplicated"
  contract <- findInitializationContract resources
  expectedDraft <- initializationDraftFor claimId operatorId personRevision contract
  instanceValue <- ioEither (contractInstance expectedDraft)
  _ <- ioEither (validateContractInstance (installedContractSchema contract) instanceValue)
  (_, expectedRecord) <- ioEither (finalizeStored timestamp expectedDraft)
  if bootstrapValue record /= expectedRecord
    then failIO "bootstrap initialization claim differs from the accepted contract"
    else pure ()

revisionText :: RevisionId -> Text
revisionText = revisionIdText

removeStoredFields :: Value -> Value
removeStoredFields (Object fields) = Object (KeyMap.delete "at" (KeyMap.delete "revision_id" fields))
removeStoredFields value = value
