{-# LANGUAGE OverloadedStrings #-}
{-# LANGUAGE LambdaCase #-}

-- | Read-only access to records committed at Git HEAD.
--
-- The worktree is never an input. Missing or unsupported evidence is
-- represented explicitly, so it cannot be mistaken for an empty answer
-- (INV-24).
module Archive.Read.Core
  ( CommittedRecord (..)
  , ReadIssue (..)
  , ReadOutcome (..)
  , readCommittedRecords
  , readCommittedRecord
  , CommittedArchive (..)
  , readCommittedArchive
  , SinceOutcome (..)
  , readCommittedArchiveSince
  , verifyCommittedObjects
  ) where

import Archive.Door.Canonical (canonicalizeDraftStrict)
import qualified Archive.GitSnapshot as GitSnapshot
import Archive.GitSnapshot (GitSnapshot)
import Archive.GitSnapshot (snapshotCommit)
import Archive.Door.Contracts
  ( ContractBundle (contractBundleContext, contractBundleSchema)
  , validateContractBundle
  , validateCoreContext
  , validateContractInstance
  )
import Archive.Door.Json (decodeUnique)
import Archive.Door.Layout
  ( ArchiveManifest
  , Sha256Digest
  , archiveRoot
  , digestText
  , manifestSpec
  , parseArchiveManifest
  , parseRecordRelativePath
  , parseSha256Digest
  , recordRelativePath
  )
import Archive.Door.Types
  ( ContractCitation (contractDigest, contractId, contractVersion)
  , DraftEnvelope (envelopeContracts, envelopeReferences, envelopeSupersedes)
  , Reference (referenceRevision)
  , RevisionId, revisionIdText
  , StoredEnvelope (storedDraft, storedRevision)
  , parseStoredEnvelope
  )
import Archive.Process (ProcessError (..), ProcessStream (..), processErrorMessage)
import qualified Crypto.Hash.SHA256 as SHA256
import Data.Aeson (Value (Object))
import qualified Data.Aeson as Aeson
import qualified Data.Aeson.Key as Key
import qualified Data.Aeson.KeyMap as KeyMap
import qualified Data.ByteString as StrictByteString
import qualified Data.ByteString.Lazy as LazyByteString
import Data.Aeson.RFC8785 (encodeCanonical)
import qualified Data.Map.Strict as Map
import qualified Data.Set as Set
import Data.Text (Text)
import qualified Data.Text as Text
import qualified Data.Text.Encoding as Text
import Data.IORef (IORef, modifyIORef', newIORef, readIORef)
import System.IO.Unsafe (unsafePerformIO)

data ReadOutcome a
  = ReadComplete a
  | ReadIncomplete a [ReadIssue]
  | ReadFailed [ReadIssue]
  deriving (Eq, Show)

data ReadIssue
  = GitFailure Text
  | MissingTree FilePath
  | ParseFailure FilePath Text
  | UnsupportedFailure FilePath Text
  deriving (Eq, Show)

-- | A record includes its exact bytes and parsed raw value as well as Door's
-- strict typed envelope. Callers can inspect evidence without re-reading a
-- worktree path or losing fields at a typed boundary.
data CommittedRecord = CommittedRecord
  { committedPath :: FilePath
  , committedBytes :: LazyByteString.ByteString
  , committedValue :: Aeson.Value
  , committedEnvelope :: StoredEnvelope
  , committedContractsValid :: Bool
  }
  deriving (Eq, Show)

data CommittedArchive = CommittedArchive
  { committedSnapshotCommit :: Text
  , committedManifest :: Maybe ArchiveManifest
  , committedContext :: Maybe Aeson.Value
  , committedRecords :: [CommittedRecord]
  , committedContractBundles :: [Aeson.Value]
  -- | Every bundle the archive holds under @contracts/@, cited or not, each
  -- verified against its digest path and its own identity (ADR 0059).
  , committedInstalledBundles :: [Aeson.Value]
  }
  deriving (Eq, Show)

-- | Read the manifest, selected core context, and records from one committed
-- HEAD. A malformed manifest or context does not erase records which can
-- still be read; it is reported alongside them as an incomplete read.
readCommittedArchive :: FilePath -> IO (ReadOutcome CommittedArchive)
readCommittedArchive root = do
  snapshotResult <- GitSnapshot.resolveSnapshot root
  case snapshotResult of
    Left issue -> pure (ReadFailed [snapshotIssue issue])
    Right snapshot -> readCommittedArchiveAt root snapshot

-- | The result of reading HEAD relative to an earlier commit.
data SinceOutcome
  = SinceRefused Text
    -- ^ The earlier commit is malformed or not an ancestor of HEAD.
  | SinceRead (ReadOutcome CommittedArchive) (Set.Set FilePath)
    -- ^ HEAD read exactly as 'readCommittedArchive' reads it, and the record
    -- paths Git lists as added after the earlier commit.
  deriving (Eq, Show)

-- | Read one committed HEAD, and the record paths added after an earlier
-- commit of its history (ADR 0055). History only adds records, and the
-- history audit Door also uses ('GitSnapshot.archivePathHistory') refuses
-- anything else under @records/@ (only the manifest may change, in a
-- specification upgrade), so the paths added after the commit are exactly
-- the new records. Paths come from Git's listing; none is built here.
readCommittedArchiveSince :: FilePath -> Text -> IO SinceOutcome
readCommittedArchiveSince root since
  | not (validCommitText since) = pure (SinceRefused
      "since must be a full lowercase commit identifier")
  | otherwise = GitSnapshot.resolveSnapshot root >>= \case
      Left issue -> pure (failed issue)
      Right snapshot -> GitSnapshot.addedPathsSince snapshot since "records" >>= \case
        Left issue -> pure (failed issue)
        Right Nothing -> pure (SinceRefused "since is not an ancestor of HEAD")
        Right (Just paths) -> do
          -- Read only the added records and the earlier records they pin;
          -- a pinned record's path follows from its revision (ADR 0053).
          outcome <- readCommittedArchiveWith root snapshot (readAddedAndPinned paths)
          pure (SinceRead outcome (Set.fromList paths))
  where
    failed issue = SinceRead (ReadFailed [snapshotIssue issue]) Set.empty
    validCommitText value = Text.length value `elem` [40, 64]
      && Text.all (\character -> ('0' <= character && character <= '9')
        || ('a' <= character && character <= 'f')) value

readCommittedArchiveAt :: FilePath -> GitSnapshot -> IO (ReadOutcome CommittedArchive)
readCommittedArchiveAt root pinned =
  GitSnapshot.prefetchPaths pinned ["archive.json", "spec", "contracts", "records"] >>= \case
    Left issue -> pure (ReadFailed [snapshotIssue issue])
    Right () -> readCommittedArchiveWith root pinned (readCommittedRecordsAt root)

-- | The added records, then every earlier record they pin that was not itself
-- added, each read and checked exactly as a full read checks it.
readAddedAndPinned :: [FilePath] -> GitSnapshot -> IO (ReadOutcome [CommittedRecord])
readAddedAndPinned paths snapshot = do
  _ <- GitSnapshot.prefetchPaths snapshot paths
  added <- readMany "." snapshot (sortUnique paths)
  let addedRecords = outcomeValue added
      addedPaths = Set.fromList paths
      pinPaths = sortUnique
        [ recordRelativePath digest
        | record <- addedRecords
        , let draft = storedDraft (committedEnvelope record)
        , Just revision <- map referenceRevision (envelopeReferences draft <> envelopeSupersedes draft)
        , Right digest <- [parseSha256Digest (revisionIdText revision)]
        , not (recordRelativePath digest `Set.member` addedPaths)
        ]
  _ <- GitSnapshot.prefetchPaths snapshot pinPaths
  pinned <- readMany "." snapshot pinPaths
  pure (combine added pinned)
  where
    sortUnique = Set.toList . Set.fromList
    outcomeValue outcome = case outcome of
      ReadComplete value -> value
      ReadIncomplete value _ -> value
      ReadFailed _ -> []
    issuesOf outcome = case outcome of
      ReadComplete _ -> []
      ReadIncomplete _ issues -> issues
      ReadFailed issues -> issues
    combine first second = case (first, second) of
      (ReadFailed issues, other) -> ReadFailed (issues <> issuesOf other)
      (other, ReadFailed issues) -> ReadFailed (issuesOf other <> issues)
      _ -> let values = outcomeValue first <> outcomeValue second
               issues = issuesOf first <> issuesOf second
            in if null issues then ReadComplete values else ReadIncomplete values issues

readCommittedArchiveWith
  :: FilePath -> GitSnapshot -> (GitSnapshot -> IO (ReadOutcome [CommittedRecord]))
  -> IO (ReadOutcome CommittedArchive)
readCommittedArchiveWith root pinned readRecords = readArchiveAt pinned
  where
    readArchiveAt snapshot = do
      manifestResult <- readHeadManifest root snapshot
      case manifestResult of
        Left issue@(ParseFailure _ _) -> readWithoutManifest snapshot issue
        Left issue@(MissingTree _) -> readWithoutManifest snapshot issue
        Left issue -> pure (ReadFailed [issue])
        Right (manifestValue, byteIssues) -> readFromManifest snapshot byteIssues manifestValue

    readWithoutManifest snapshot manifestIssue = do
      recordsResult <- readRecords snapshot
      case recordsResult of
        ReadFailed issues -> pure (ReadFailed (manifestIssue : issues))
        ReadComplete records -> finish snapshot Nothing Nothing records [manifestIssue]
        ReadIncomplete records issues -> finish snapshot Nothing Nothing records (manifestIssue : issues)

    readFromManifest snapshot byteIssues manifestValue = do
      let (manifest, parseIssues) = case parseArchiveManifest manifestValue of
            Left problem -> (Nothing, [ParseFailure "archive.json" problem])
            Right parsed -> (Just parsed, [])
          manifestIssues = byteIssues <> parseIssues
      contextResult <- case manifest of
        Nothing -> pure (Right Nothing, [])
        Just parsed -> do
          let path = contextPath (manifestSpec parsed)
          result <- readHeadValue root snapshot path
          pure $ case result of
            Left issue -> (Right Nothing, [contextIssue path issue])
            Right value -> case validateCoreContext value of
              Left problem -> (Right Nothing, [ParseFailure path problem])
              Right () -> (Right (Just value), [])
      specificationIssues <- case manifest of
        Nothing -> pure []
        Just parsed -> validateSpecificationResources root snapshot (manifestSpec parsed)
      case contextResult of
        (Left issue, contextIssues) -> pure (ReadFailed
          (manifestIssues <> contextIssues <> specificationIssues <> [issue]))
        (Right context, contextIssues) -> do
          recordsResult <- readRecords snapshot
          case recordsResult of
            ReadFailed issues -> pure (ReadFailed
              (manifestIssues <> contextIssues <> specificationIssues <> issues))
            ReadComplete records -> finish snapshot manifest context records
              (manifestIssues <> contextIssues <> specificationIssues)
            ReadIncomplete records issues -> finish snapshot manifest context records
              (manifestIssues <> contextIssues <> specificationIssues <> issues)
    finish snapshot manifest context records issues = do
      (bundles, bundleIssues) <- readExportContractBundles root snapshot records
      (installed, installedIssues) <- readInstalledContractBundles snapshot
      let allIssues = issues <> bundleIssues <> installedIssues
          archive = CommittedArchive
            { committedSnapshotCommit = snapshotCommit snapshot
            , committedManifest = manifest
            , committedContext = context
            , committedRecords = records
            , committedContractBundles = bundles
            , committedInstalledBundles = installed
            }
      pure $ if null allIssues then ReadComplete archive else ReadIncomplete archive allIssues

    contextPath specification = case Text.stripPrefix "archive/" specification of
      Just version -> "spec/" <> Text.unpack version <> "/context.jsonld"
      Nothing -> "spec/invalid/context.jsonld"

    contextIssue path issue = case issue of
      GitFailure detail -> GitFailure detail
      MissingTree _ -> MissingTree path
      ParseFailure _ detail -> ParseFailure path detail
      UnsupportedFailure _ detail -> UnsupportedFailure path detail

-- | Check every Git object of the committed HEAD for decay (ADR 0052). A
-- failure is reported as an issue, never as an empty result.
verifyCommittedObjects :: FilePath -> IO [ReadIssue]
verifyCommittedObjects root = GitSnapshot.resolveSnapshot root >>= \case
  Left issue -> pure [snapshotIssue issue]
  Right snapshot -> GitSnapshot.verifyObjects snapshot >>= \case
    Left issue -> pure [objectIssue issue]
    Right () -> pure []
  where
    objectIssue issue = case snapshotIssue issue of
      GitFailure detail -> GitFailure ("Git object check failed: " <> detail)
      other -> other

-- | Return the canonical raw bundles cited by committed records. Each bundle
-- is revalidated at the same pinned snapshot used for the records and core
-- context, then included once in deterministic citation order.
readExportContractBundles
  :: FilePath -> GitSnapshot -> [CommittedRecord]
  -> IO ([Aeson.Value], [ReadIssue])
readExportContractBundles root snapshot records = go Set.empty [] [] citations
  where
    citations =
      [ citation
      | record <- records
      , citation <- envelopeContracts (storedDraft (committedEnvelope record))
      ]

    go _ values issues [] = pure (reverse values, reverse issues)
    go seen values issues (citation : rest)
      | key `Set.member` seen = go seen values issues rest
      | otherwise = do
          let identifier = contractId citation
              version = contractVersion citation
              digest = contractDigest citation
              path = contractPathFor digest
          validated <- loadCommittedContract root snapshot identifier version digest
          case validated of
            Left issue -> go (Set.insert key seen) values
              (classifyContractReadFailure path issue : issues) rest
            Right _ -> do
              result <- GitSnapshot.readPath snapshot path
              case result of
                Left issue -> go (Set.insert key seen) values
                  (classifyContractReadFailure path (snapshotIssue issue) : issues) rest
                Right bytes -> case decodeUnique bytes of
                  Left problem -> go (Set.insert key seen) values
                    (ParseFailure path (Text.pack problem) : issues) rest
                  Right value -> go (Set.insert key seen) (value : values) issues rest
      where
        key = (contractId citation, contractVersion citation, contractDigest citation)
        contractPathFor digest = "contracts/sha256-" <> Text.unpack (Text.drop 7 digest) <> ".json"

-- | Every contract bundle under @contracts/@ at the snapshot. Each is read
-- by its digest path and must match it, be canonical, and validate as a
-- bundle of the identifier and version it names. An archive with no
-- contracts is incomplete, never one that holds none.
readInstalledContractBundles :: GitSnapshot -> IO ([Aeson.Value], [ReadIssue])
readInstalledContractBundles snapshot = do
  listed <- GitSnapshot.listPaths snapshot "contracts"
  case listed of
    Left issue -> pure ([], [snapshotIssue issue])
    Right paths -> do
      results <- mapM readOne paths
      pure ([value | Right value <- results], [issue | Left issue <- results])
  where
    readOne path = case Text.stripSuffix ".json" =<< Text.stripPrefix "contracts/sha256-" (Text.pack path) of
      Nothing -> pure (Left (ParseFailure path "an installed contract path must be contracts/sha256-<digest>.json"))
      Just hexDigest -> do
        let digest = "sha256:" <> hexDigest
        bytes <- GitSnapshot.readPath snapshot path
        pure $ case bytes of
          Left issue -> Left (snapshotIssue issue)
          Right content -> case decodeUnique content of
            Left problem -> Left (ParseFailure path (Text.pack problem))
            Right value@(Object fields)
              | "sha256:" <> hex (SHA256.hashlazy content) /= digest ->
                  Left (ParseFailure path "contract bytes do not match their digest path")
              | encodeCanonical value /= content ->
                  Left (ParseFailure path "contract bytes are not RFC 8785 canonical")
              | Just (Aeson.String identifier) <- KeyMap.lookup "id" fields
              , Just (Aeson.String version) <- KeyMap.lookup "version" fields ->
                  case validateContractBundle identifier version digest value of
                    Left problem -> Left (classifyContractFailure path problem)
                    Right _ -> Right value
            Right _ -> Left (ParseFailure path "a contract bundle must be an object with id and version")

readHeadValue :: FilePath -> GitSnapshot -> FilePath -> IO (Either ReadIssue Aeson.Value)
readHeadValue _root snapshot path = do
  result <- GitSnapshot.readPath snapshot path
  pure $ case result of
    Left issue -> Left (snapshotIssue issue)
    Right bytes -> case decodeUnique bytes of
      Left problem -> Left (ParseFailure path (Text.pack problem))
      Right value -> Right value

readHeadManifest :: FilePath -> GitSnapshot -> IO (Either ReadIssue (Aeson.Value, [ReadIssue]))
readHeadManifest _root snapshot = do
  let path = "archive.json"
  result <- GitSnapshot.readPath snapshot path
  pure $ case result of
    Left issue -> Left (snapshotIssue issue)
    Right bytes -> case decodeUnique bytes of
      Left problem -> Left (ParseFailure path (Text.pack problem))
      Right value
        | encodeCanonical value == bytes -> Right (value, [])
        | otherwise -> Right (value,
            [ParseFailure path "committed JSON is not RFC 8785 canonical"])

validateSpecificationResources :: FilePath -> GitSnapshot -> Text -> IO [ReadIssue]
validateSpecificationResources _root snapshot specification =
  case Text.stripPrefix "archive/" specification of
    Nothing -> pure [ParseFailure "archive.json" "specification identifier is invalid"]
    Just version -> do
      let directory = "spec/" <> Text.unpack version
          versionPath = directory <> "/VERSION"
          documentPath = directory <> "/archive-spec.md"
      versionResult <- GitSnapshot.readPath snapshot versionPath
      documentResult <- GitSnapshot.readPath snapshot documentPath
      pure (checkVersion versionPath version versionResult <> checkDocument documentPath documentResult)
  where
    checkVersion path expected result = case result of
      Left issue -> [snapshotIssue issue]
      Right bytes -> case Text.decodeUtf8' (LazyByteString.toStrict bytes) of
        Right actual
          | Text.stripEnd actual == expected && not (Text.null actual) -> []
        _ -> [ParseFailure path "VERSION does not match archive.json"]
    checkDocument path result = case result of
      Left issue -> [snapshotIssue issue]
      Right bytes
        | LazyByteString.null bytes -> [ParseFailure path "copied specification is empty"]
        | otherwise -> []

-- | Read every record path listed by Git at HEAD, in deterministic lexical
-- order. An absent tree is incomplete, not an empty archive.
readCommittedRecords :: FilePath -> IO (ReadOutcome [CommittedRecord])
readCommittedRecords root = GitSnapshot.resolveSnapshot root >>= \case
  Left issue -> pure (ReadFailed [snapshotIssue issue])
  Right snapshot -> readCommittedRecordsAt root snapshot

readCommittedRecordsAt :: FilePath -> GitSnapshot -> IO (ReadOutcome [CommittedRecord])
readCommittedRecordsAt root snapshot = do
  case archiveRoot root of
    Left problem -> pure (ReadFailed [GitFailure problem])
    Right _ -> do
      namesResult <- GitSnapshot.listPaths snapshot "records"
      case namesResult of
        Left (GitSnapshot.GitMissingPath path) -> pure (ReadIncomplete [] [MissingTree path])
        Left issue -> pure (ReadFailed [snapshotIssue issue])
        Right paths -> GitSnapshot.prefetchPaths snapshot ["contracts", "records"] >>= \case
          Left issue -> pure (ReadFailed [snapshotIssue issue])
          Right () -> readMany root snapshot paths

readMany :: FilePath -> GitSnapshot -> [FilePath] -> IO (ReadOutcome [CommittedRecord])
readMany root snapshot = go [] []
  where
    go records issues []
      | null issues = pure (ReadComplete (reverse records))
      | otherwise = pure (ReadIncomplete (reverse records) (reverse issues))
    go records issues (path : rest) = do
      result <- readCommittedRecordAt root snapshot path
      case result of
        ReadComplete record -> go (record <> records) issues rest
        ReadIncomplete usable pathIssues ->
          go (usable <> records) (pathIssues <> issues) rest
        ReadFailed pathIssues ->
          go records (pathIssues <> issues) rest

-- | Read one digest-addressed record from HEAD. The relative path is checked
-- before invoking Git, so traversal and alternate names cannot be smuggled
-- into the object expression.
readCommittedRecord :: FilePath -> FilePath -> IO (ReadOutcome [CommittedRecord])
readCommittedRecord root path = GitSnapshot.resolveSnapshot root >>= \case
  Left issue -> pure (ReadFailed [snapshotIssue issue])
  Right snapshot -> readCommittedRecordAt root snapshot path

readCommittedRecordAt :: FilePath -> GitSnapshot -> FilePath -> IO (ReadOutcome [CommittedRecord])
readCommittedRecordAt root snapshot path = do
  case archiveRoot root of
    Left problem -> pure (ReadFailed [GitFailure problem])
    Right _ -> case expectedDigest path of
      Left problem -> pure (ReadIncomplete [] [ParseFailure path problem])
      Right _ -> do
        result <- GitSnapshot.readPath snapshot path
        case result of
          Left issue -> pure (ReadFailed [snapshotIssue issue])
          Right bytes -> parseAndVerify root snapshot path bytes

parseAndVerify :: FilePath -> GitSnapshot -> FilePath -> LazyByteString.ByteString -> IO (ReadOutcome [CommittedRecord])
parseAndVerify root snapshot path bytes =
  case expectedDigest path of
    Left problem -> pure (ReadIncomplete [] [ParseFailure path problem])
    Right expected -> case decodeUnique bytes of
      Left problem -> pure (ReadIncomplete [] [ParseFailure path (Text.pack problem)])
      Right value -> case parseStoredEnvelope value of
        Left problem -> pure (ReadIncomplete [] [ParseFailure path problem])
        Right envelope -> do
          let embedded = revisionText (storedRevision envelope)
          if embedded /= digestText expected
            then pure (ReadIncomplete [] [ParseFailure path
                    "record filename and embedded revision_id disagree"])
            else case recomputeRevision value of
              Left problem -> pure (ReadIncomplete [] [ParseFailure path problem])
              Right recomputed
                | recomputed /= embedded -> pure (ReadIncomplete [] [ParseFailure path
                    "record revision_id does not match its P-3 identity"])
                | otherwise -> do
                    issues <- validateCitedContracts root snapshot path value envelope
                    if null issues
                      then pure (ReadComplete [CommittedRecord
                        { committedPath = path
                        , committedBytes = bytes
                        , committedValue = value
                        , committedEnvelope = envelope
                        , committedContractsValid = True
                        }])
                      else pure (ReadIncomplete
                        [CommittedRecord
                          { committedPath = path
                          , committedBytes = bytes
                          , committedValue = value
                          , committedEnvelope = envelope
                          , committedContractsValid = False
                          }]
                        issues)

-- Recompute P-3 with Door's canonicalizer after removing exactly the two
-- derived/stamped fields. The raw value itself is retained in the result.
recomputeRevision :: Aeson.Value -> Either Text Text
recomputeRevision (Object fields) = do
  (revision, _) <- canonicalizeDraftStrict
    (Object (KeyMap.delete "at" (KeyMap.delete "revision_id" fields)))
  pure revision
recomputeRevision _ = Left "stored record must be a JSON object"

validateCitedContracts :: FilePath -> GitSnapshot -> FilePath -> Aeson.Value -> StoredEnvelope -> IO [ReadIssue]
validateCitedContracts root snapshot path rawValue envelope = collect [] [] citations
  where
    citations = envelopeContracts (storedDraft envelope)
    instanceValue = contractInstance rawValue

    collect issues contexts [] = pure (reverse issues <> contextCollisions contexts)
    collect issues contexts (citation : rest) = do
      let identifier = contractId citation
          version = contractVersion citation
          digest = contractDigest citation
      loaded <- loadCommittedContract root snapshot identifier version digest
      let issue = case loaded of
            Left problem -> Just (classifyContractReadFailure path problem)
            Right bundle -> case validateContractInstance (contractBundleSchema bundle) instanceValue of
              Left problem -> Just (ParseFailure path
                ("contract " <> identifier <> " rejected payload: " <> problem))
              Right () -> Nothing
      let nextContexts = case loaded of
            Right bundle -> (identifier, contractBundleContext bundle) : contexts
            Left _ -> contexts
      collect (maybe issues (: issues) issue) nextContexts rest

    contextCollisions contexts =
      [ UnsupportedFailure path ("contract context term collision: " <> term)
      | (term, owners) <- Map.toList (Map.fromListWith Set.union
          [ (Key.toText key, Set.singleton identifier)
          | (identifier, context) <- contexts
          , key <- KeyMap.keys context
          ])
      , Set.size owners > 1
      ]

contractInstance :: Aeson.Value -> Aeson.Value
contractInstance (Object fields) = Object (KeyMap.fromList
  [ ("payload", maybe Aeson.Null id (KeyMap.lookup "payload" fields))
  , ("refs", maybe Aeson.Null id (KeyMap.lookup "refs" fields))
  ])
contractInstance _ = Object KeyMap.empty

expectedDigest :: FilePath -> Either Text Sha256Digest
expectedDigest = parseRecordRelativePath

revisionText :: RevisionId -> Text
revisionText = revisionIdText

classifyContractFailure :: FilePath -> Text -> ReadIssue
classifyContractFailure path problem
  | Text.isInfixOf "unable to read" problem = UnsupportedFailure path problem
  | Text.isInfixOf "unsupported" problem = UnsupportedFailure path problem
  | Text.isInfixOf "remote $ref" problem = UnsupportedFailure path problem
  | otherwise = ParseFailure path problem

classifyContractReadFailure :: FilePath -> ReadIssue -> ReadIssue
classifyContractReadFailure path issue = case issue of
  GitFailure problem -> UnsupportedFailure path ("contract is unavailable at HEAD: " <> problem)
  MissingTree tree -> UnsupportedFailure path ("contract tree is unavailable at HEAD: " <> Text.pack tree)
  ParseFailure _ problem -> classifyContractFailure path problem
  UnsupportedFailure _ problem -> UnsupportedFailure path problem

-- | Each (commit, contract) is validated once per process: a contract file is
-- content-addressed and a commit is immutable, so the answer cannot change.
-- Before this, a read validated a contract again for every record citing it.
contractMemo :: IORef (Map.Map (Text, Text, Text, Text) (Either ReadIssue ContractBundle))
contractMemo = unsafePerformIO (newIORef Map.empty)
{-# NOINLINE contractMemo #-}

loadCommittedContract :: FilePath -> GitSnapshot -> Text -> Text -> Text -> IO (Either ReadIssue ContractBundle)
loadCommittedContract root snapshot expectedId expectedVersion expectedDigestText = do
  let key = (snapshotCommit snapshot, expectedId, expectedVersion, expectedDigestText)
  remembered <- Map.lookup key <$> readIORef contractMemo
  case remembered of
    Just result -> pure result
    Nothing -> do
      result <- loadCommittedContractOnce root snapshot expectedId expectedVersion expectedDigestText
      modifyIORef' contractMemo (Map.insert key result)
      pure result

loadCommittedContractOnce :: FilePath -> GitSnapshot -> Text -> Text -> Text -> IO (Either ReadIssue ContractBundle)
loadCommittedContractOnce _root snapshot expectedId expectedVersion expectedDigestText = do
  case parseSha256Digest expectedDigestText of
    Left problem -> pure (Left (ParseFailure "contracts" problem))
    Right digest -> do
      let path = "contracts/sha256-" <> Text.unpack (Text.drop 7 (digestText digest)) <> ".json"
      result <- GitSnapshot.readPath snapshot path
      pure $ case result of
        Left issue -> Left (snapshotIssue issue)
        Right bytes -> case decodeUnique bytes of
          Left problem -> Left (ParseFailure path (Text.pack problem))
          Right value -> do
            let actualDigest = "sha256:" <> hex (SHA256.hashlazy bytes)
            if actualDigest /= expectedDigestText
              then Left (ParseFailure path "contract bytes do not match cited digest")
              else if encodeCanonical value /= bytes
                then Left (ParseFailure path "contract bytes are not RFC 8785 canonical")
                else case validateContractBundle expectedId expectedVersion expectedDigestText value of
                  Left problem -> Left (classifyContractFailure path problem)
                  Right bundle -> Right bundle

snapshotIssue :: GitSnapshot.GitSnapshotError -> ReadIssue
snapshotIssue issue = case issue of
  GitSnapshot.GitProcessFailure detail -> GitFailure detail
  GitSnapshot.GitProcessExecutionFailure failure -> GitFailure
    (case failure of
      ProcessOutputLimitFailure StandardOutput _ -> "git stdout exceeded the control-plane output limit"
      ProcessOutputLimitFailure StandardError _ -> "git stderr exceeded the diagnostic output limit"
      _ -> processErrorMessage failure)
  GitSnapshot.GitMissingPath path -> MissingTree path
  GitSnapshot.GitInvalidPath path detail -> ParseFailure path detail
  GitSnapshot.GitInvalidCommit detail -> GitFailure detail
  GitSnapshot.GitIntroducerError path detail -> UnsupportedFailure path detail

hex :: StrictByteString.ByteString -> Text
hex = Text.pack . concatMap byteHex . StrictByteString.unpack
  where
    byteHex byte = [digits !! fromIntegral (byte `div` 16), digits !! fromIntegral (byte `mod` 16)]
    digits = "0123456789abcdef"
