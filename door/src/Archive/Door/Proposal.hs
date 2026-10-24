{-# LANGUAGE OverloadedStrings #-}
{-# LANGUAGE ScopedTypeVariables #-}

-- | Side-effect-free loading and checking of proposal metadata.
--
-- This module stops at the proposal boundary. It does not inspect or change
-- an archive, invoke Git, or ask git-annex for content. The bytes returned
-- here are retained so a caller can check that the proposal did not change
-- before performing an authoritative operation.
module Archive.Door.Proposal
  ( ContentDeclaration (..)
  , ExpectedHead (..)
  , IdentityAllocation (..)
  , Proposal (..)
  , ProposalKind (..)
  , ProposalManifest (..)
  , ProposalPath (..)
  , ProposalRecord (..)
  , readAdmissionProposal
  , readProposal
  , readTransitionProposal
  , recheckProposal
  , validateProposal
  ) where

import Archive.Door.Json (decodeUnique)
import Archive.Door.ProposalProblem (ProposalProblem (..), proposalProblemMessage)
import Archive.Door.Types
  ( DraftEnvelope (..)
  , ObjectId
  , objectIdText
  , RecordBody (..)
  , parseDraftEnvelope
  )
import Control.Exception (IOException, try)
import Control.Monad.Trans.Except (ExceptT (..), except, runExceptT)
import Data.Aeson (Value (..))
import Data.Aeson.Key qualified as Key
import Data.Aeson.KeyMap qualified as KeyMap
import Data.Bifunctor (first)
import Data.ByteString qualified as ByteString
import Data.ByteString.Lazy qualified as LazyByteString
import Data.Char (isControl)
import Data.Foldable (toList)
import Data.List (sort)
import Data.Scientific (Scientific, floatingOrInteger)
import Data.Set qualified as Set
import Data.Text (Text)
import Data.Text qualified as Text
import Data.Word (Word64)
import System.Directory (makeAbsolute)
import System.Posix.Files (fileSize, getFdStatus, isDirectory, isRegularFile)
import System.Posix.IO
  ( OpenFileFlags (nofollow)
  , OpenMode (ReadOnly)
  , closeFd
  , defaultFileFlags
  , fdToHandle
  , openFd
  , openFdAt
  )
import System.Posix.Types (Fd)
import System.IO (Handle, hClose)

-- Proposal files are control-plane input. These bounds keep one proposal from
-- requiring unbounded memory while leaving committed archive content alone.
manifestLimit, recordLimit, proposalRecordsLimit :: Integer
manifestLimit = 1024 * 1024
recordLimit = 8 * 1024 * 1024
proposalRecordsLimit = 64 * 1024 * 1024

data ProposalKind = AdmissionProposal | TransitionProposal
  deriving (Eq, Show)

newtype ProposalPath = ProposalPath Text
  deriving (Eq, Ord, Show)

data IdentityAllocation = IdentityAllocation
  { identityObjectId :: Text
  , identityNamespace :: Text
  , identityName :: Text
  } deriving (Eq, Show)

data ContentDeclaration = ContentDeclaration
  { contentDigest :: Text
  , contentSize :: Word64
  , contentAnnexKey :: Text
  , contentStagingRemote :: Text
  } deriving (Eq, Show)

data ExpectedHead = ExpectedHead
  { expectedObjectId :: Text
  , expectedRevisionId :: Maybe Text
  } deriving (Eq, Show)

data ProposalManifest = ProposalManifest
  { manifestProtocol :: Text
  , manifestRecords :: [ProposalPath]
  , manifestIdentities :: [IdentityAllocation]
  , manifestContents :: [ContentDeclaration]
  , manifestExpectedHeads :: [ExpectedHead]
  } deriving (Eq, Show)

data ProposalRecord = ProposalRecord
  { proposalRecordPath :: ProposalPath
  , proposalRecordBytes :: ByteString.ByteString
  , proposalRecordValue :: Value
  , proposalRecordDraft :: DraftEnvelope
  } deriving (Eq, Show)

data Proposal = Proposal
  { proposalRoot :: FilePath
  , proposalKind :: ProposalKind
  , proposalManifestBytes :: ByteString.ByteString
  , proposalManifest :: ProposalManifest
  , proposalRecords :: [ProposalRecord]
  } deriving (Eq, Show)

readAdmissionProposal :: FilePath -> IO (Either Text Proposal)
readAdmissionProposal = readProposal AdmissionProposal

readTransitionProposal :: FilePath -> IO (Either Text Proposal)
readTransitionProposal = readProposal TransitionProposal

-- | Read and check a proposal, rendering any problem to its message.
readProposal :: ProposalKind -> FilePath -> IO (Either Text Proposal)
readProposal kind root = first proposalProblemMessage <$> readProposalChecked kind root

readProposalChecked :: ProposalKind -> FilePath -> IO (Either ProposalProblem Proposal)
readProposalChecked kind root = runExceptT $ do
  absoluteRoot <- ExceptT (tryIO (makeAbsolute root))
  rootFd <- ExceptT (tryIO (openProposalRoot absoluteRoot))
  ExceptT (finallyIO rootFd (readProposalFromFd kind absoluteRoot rootFd))

-- The descriptor traversal is authoritative for files named by the manifest.
-- The unix package exposes openat/nofollow but no directory-stream operation
-- relative to an already-open descriptor, so this module does not claim a
-- race-free stray-entry check.
readProposalFromFd :: ProposalKind -> FilePath -> Fd -> IO (Either ProposalProblem Proposal)
readProposalFromFd kind root rootFd = runExceptT $ do
  manifestBytes <- ExceptT (readRegularAt manifestLimit (Just rootFd) "manifest.json")
  manifest <- except (decodeValue "manifest.json" manifestBytes >>= parseManifest kind)
  recordsFd <- ExceptT (tryIO (openDirectoryAt (Just rootFd) "records"))
  ExceptT $ finallyIO recordsFd $ runExceptT $ do
    records <- ExceptT (readRecordsAt recordsFd (manifestRecords manifest))
    except (validateProposalAgainstFiles manifest records)
    pure Proposal
      { proposalRoot = root
      , proposalKind = kind
      , proposalManifestBytes = manifestBytes
      , proposalManifest = manifest
      , proposalRecords = records
      }

-- | Check again immediately before an operation. Comparison is byte-for-byte,
-- not semantic: whitespace and key-order changes are changes to the proposal.
recheckProposal :: Proposal -> IO (Either Text ())
recheckProposal original = do
  current <- readProposalChecked (proposalKind original) (proposalRoot original)
  pure $ first proposalProblemMessage $ case current of
    Left problem -> Left (RecheckFailed problem)
    Right proposal
      | proposalManifestBytes proposal /= proposalManifestBytes original ->
          Left ManifestChanged
      | sameRecords proposal original -> Right ()
      | otherwise -> Left RecordBytesChanged
  where
    sameRecords left right =
      map recordFingerprint (proposalRecords left)
        == map recordFingerprint (proposalRecords right)
    recordFingerprint record =
      (proposalRecordPath record, proposalRecordBytes record)

validateProposal :: Proposal -> Either Text ()
validateProposal proposal = first proposalProblemMessage $ do
  let manifest = proposalManifest proposal
  validateProtocol (proposalKind proposal) (manifestProtocol manifest)
  validateManifestLists (proposalKind proposal) manifest
  validateDraftRelationships (proposalKind proposal) manifest (proposalRecords proposal)

validateManifestLists :: ProposalKind -> ProposalManifest -> Either ProposalProblem ()
validateManifestLists kind manifest = do
  requireDistinct "manifest records path" (map pathText (manifestRecords manifest))
  requireDistinct "identity object_id" (map identityObjectId (manifestIdentities manifest))
  requireDistinct "expected_heads object_id" (map expectedObjectId (manifestExpectedHeads manifest))
  case kind of
    TransitionProposal
      | null (manifestRecords manifest)
          || null (manifestIdentities manifest)
          || null (manifestExpectedHeads manifest) ->
          Left TransitionArraysEmpty
    _ -> pure ()
  where
    pathText (ProposalPath path) = path

readRecordsAt :: Fd -> [ProposalPath] -> IO (Either ProposalProblem [ProposalRecord])
readRecordsAt recordsFd = go proposalRecordsLimit []
  where
    go _ records [] = pure (Right (reverse records))
    go remaining records (path : paths) = do
      result <- readRecordAt (min recordLimit remaining) recordsFd path
      case result of
        Left problem -> pure (Left problem)
        Right record -> go (remaining - fromIntegral (ByteString.length (proposalRecordBytes record)))
          (record : records) paths

readRecordAt :: Integer -> Fd -> ProposalPath -> IO (Either ProposalProblem ProposalRecord)
readRecordAt limit recordsFd path@(ProposalPath pathText) = do
  let filename = Text.unpack (Text.drop (Text.length "records/") pathText)
  result <- readRegularAt limit (Just recordsFd) filename
  pure $ result >>= \bytes -> do
    value <- decodeValue pathText bytes
    draft <- either (Left . InvalidDraft pathText) Right
      (parseDraftEnvelope value)
    pure ProposalRecord
      { proposalRecordPath = path
      , proposalRecordBytes = bytes
      , proposalRecordValue = value
      , proposalRecordDraft = draft
      }

readRegularAt :: Integer -> Maybe Fd -> FilePath -> IO (Either ProposalProblem ByteString.ByteString)
readRegularAt limit parent name = do
  opened <- tryIO (openFdAt parent name ReadOnly noFollowFlags)
  case opened of
    Left problem -> pure (Left (AtPath name problem))
    Right fd -> do
      status <- tryIO (getFdStatus fd)
      case status of
        Left problem -> closeFd fd >> pure (Left problem)
        Right value
          | not (isRegularFile value) -> closeFd fd >> pure (Left (AtPath name NotRegularFile))
          | toInteger (fileSize value) > limit ->
              closeFd fd >> pure (Left (AtPath name ExceedsSizeLimit))
          | otherwise -> do
              handleResult <- tryIO (fdToHandle fd)
              case handleResult of
                Left problem -> closeFd fd >> pure (Left problem)
                Right handle -> do
                  bytes <- tryIO (readAtMost (fromInteger limit + 1) handle)
                  _ <- tryIO (hClose handle)
                  pure $ do
                    content <- bytes
                    if toInteger (ByteString.length content) > limit
                      then Left (AtPath name ExceedsSizeLimit)
                      else Right content

readAtMost :: Int -> Handle -> IO ByteString.ByteString
readAtMost maximumBytes handle = loop maximumBytes []
  where
    loop remaining chunks
      | remaining <= 0 = pure (ByteString.concat (reverse chunks))
      | otherwise = do
          chunk <- ByteString.hGetSome handle (min remaining 32768)
          if ByteString.null chunk
            then pure (ByteString.concat (reverse chunks))
            else loop (remaining - ByteString.length chunk) (chunk : chunks)

noFollowFlags :: OpenFileFlags
noFollowFlags = defaultFileFlags { nofollow = True }

openDirectoryAt :: Maybe Fd -> FilePath -> IO Fd
openDirectoryAt parent name = do
  fd <- openFdAt parent name ReadOnly noFollowFlags
  status <- getFdStatus fd
  if isDirectory status then pure fd else closeFd fd >> ioError (userError "proposal entry must be a directory")

openProposalRoot :: FilePath -> IO Fd
openProposalRoot root = do
  absolute <- makeAbsolute root
  fd <- openFd absolute ReadOnly noFollowFlags
  status <- getFdStatus fd
  if isDirectory status
    then pure fd
    else closeFd fd >> ioError (userError "proposal root must be a directory")

validateProposalAgainstFiles
  :: ProposalManifest
  -> [ProposalRecord]
  -> Either ProposalProblem ()
validateProposalAgainstFiles manifest records = do
  let listed = manifestRecords manifest
      expected = Set.fromList listed
  if Set.size expected /= length listed
    then Left DuplicateManifestPath
    else if length records /= length listed
      then Left RecordCountMismatch
      else do
        validateProtocol kindFromManifest (manifestProtocol manifest)
        validateManifestLists kindFromManifest manifest
        validateDraftRelationships kindFromManifest manifest records
  where
    kindFromManifest
      | manifestProtocol manifest == "archive-transition/1" = TransitionProposal
      | otherwise = AdmissionProposal

validateDraftRelationships :: ProposalKind -> ProposalManifest -> [ProposalRecord] -> Either ProposalProblem ()
validateDraftRelationships kind manifest records = do
  let drafts = map proposalRecordDraft records
      draftObjects = map (objectText . envelopeObject) drafts
      identityObjects = map identityObjectId (manifestIdentities manifest)
      headObjects = map expectedObjectId (manifestExpectedHeads manifest)
      headSet = Set.fromList headObjects
      draftSet = Set.fromList draftObjects
  if not (all (\object -> Set.member object headSet) draftObjects)
    then Left HeadsOmitDraft
    else if kind == AdmissionProposal && headSet /= draftSet
      then Left HeadsNotExactlyDrafts
    else if not (all (\object -> Set.member object draftSet) identityObjects)
      then Left IdentitiesOutsideDrafts
      else if Set.fromList identityObjects /= Set.fromList
        [ expectedObjectId expected
        | expected <- manifestExpectedHeads manifest
        , expectedRevisionId expected == Nothing
        ]
        then Left IdentitiesNotNullHeads
      else case kind of
        AdmissionProposal -> validateAdmissionContent manifest drafts
        TransitionProposal -> pure ()

validateAdmissionContent :: ProposalManifest -> [DraftEnvelope] -> Either ProposalProblem ()
validateAdmissionContent manifest drafts = do
  let declared = manifestContents manifest
      declaredDigests = map contentDigest declared
  referenced <- concat <$> mapM contentReferences drafts
  let
      referencedDigests = map fst referenced
  requireDistinct "content digest" declaredDigests
  if Set.fromList declaredDigests /= Set.fromList referencedDigests
    then Left ContentsMismatch
    else mapM_ (checkContentReference declared) referenced
  where
    checkContentReference declarations (digest, size) =
      case findDigest digest declarations of
        Nothing -> Left ContentUndeclared
        Just declaration
          | contentSize declaration /= size -> Left ContentSizeDisagrees
          | otherwise -> Right ()

contentReferences :: DraftEnvelope -> Either ProposalProblem [(Text, Word64)]
contentReferences draft = case envelopeBody draft of
  ArtifactRecord (Object fields) -> case KeyMap.lookup "content" fields of
    Just (Object content) -> case (KeyMap.lookup "digest" content, KeyMap.lookup "size" content) of
      (Just (String digest), Just (Number number)) -> case integerNumber number of
        Just size -> Right [(digest, size)]
        Nothing -> Left ContentSizeNotInteger
      (Nothing, _) -> Left ContentDigestMissing
      (_, Nothing) -> Left ContentSizeMissing
      _ -> Left ContentFieldTypes
    _ -> Left ContentNotObject
  ArtifactRecord _ -> Left ArtifactPayloadNotObject
  _ -> Right []

findDigest :: Text -> [ContentDeclaration] -> Maybe ContentDeclaration
findDigest digest = go
  where
    go [] = Nothing
    go (declaration : rest)
      | contentDigest declaration == digest = Just declaration
      | otherwise = go rest

parseManifest :: ProposalKind -> Value -> Either ProposalProblem ProposalManifest
parseManifest kind (Object fields) = do
  let expected = case kind of
        AdmissionProposal -> ["protocol", "records", "identities", "contents", "expected_heads"]
        TransitionProposal -> ["protocol", "records", "identities", "expected_heads"]
  requireExactKeys "proposal manifest" expected fields
  protocol <- requiredText "protocol" fields
  validateProtocol kind protocol
  recordValues <- requiredArray "records" fields >>= mapM parseRecordPath
  identities <- requiredArray "identities" fields >>= mapM parseIdentityAllocation
  contents <- case kind of
    AdmissionProposal -> requiredArray "contents" fields >>= mapM parseContentDeclaration
    TransitionProposal -> pure []
  expectedHeads <- requiredArray "expected_heads" fields >>= mapM parseExpectedHead
  if kind == TransitionProposal
    && (null recordValues || null identities || null expectedHeads)
    then Left TransitionArraysEmpty
    else pure ProposalManifest
      { manifestProtocol = protocol
      , manifestRecords = recordValues
      , manifestIdentities = identities
      , manifestContents = contents
      , manifestExpectedHeads = expectedHeads
      }
parseManifest _ _ = Left ManifestNotObject

validateProtocol :: ProposalKind -> Text -> Either ProposalProblem ()
validateProtocol AdmissionProposal "archive-admission/1" = Right ()
validateProtocol TransitionProposal "archive-transition/1" = Right ()
validateProtocol _ _ = Left WrongProtocol

parseRecordPath :: Value -> Either ProposalProblem ProposalPath
parseRecordPath (String value) = do
  path <- parseSafePath value
  case Text.splitOn "/" value of
    ["records", filename]
      | Text.isSuffixOf ".json" filename && Text.length filename > 5 -> Right path
    _ -> Left RecordPathShape
parseRecordPath _ = Left RecordPathNotString

parseSafePath :: Text -> Either ProposalProblem ProposalPath
parseSafePath value
  | Text.null value = Left PathEmpty
  | Text.head value == '/' = Left PathAbsolute
  | Text.any (== '\0') value = Left PathHasNul
  | any invalidComponent components = Left PathUnsafeComponent
  | Text.intercalate "/" components /= value = Left PathNotNormalized
  | otherwise = Right (ProposalPath value)
  where
    components = Text.splitOn "/" value
    invalidComponent component =
      Text.null component || component == "." || component == ".."

parseIdentityAllocation :: Value -> Either ProposalProblem IdentityAllocation
parseIdentityAllocation (Object fields) = do
  requireExactKeys "identity allocation" ["object_id", "namespace", "name"] fields
  objectId <- requiredText "object_id" fields >>= validateObjectUrn
  namespace <- requiredText "namespace" fields >>= validateNamespace
  name <- requiredText "name" fields >>= validateIdentityName
  pure IdentityAllocation
    { identityObjectId = objectId
    , identityNamespace = namespace
    , identityName = name
    }
parseIdentityAllocation _ = Left AllocationNotObject

parseContentDeclaration :: Value -> Either ProposalProblem ContentDeclaration
parseContentDeclaration (Object fields) = do
  requireExactKeys "content declaration" ["digest", "size", "annex_key", "staging_remote"] fields
  digest <- requiredText "digest" fields >>= validateDigest
  size <- requiredNumber "size" fields >>= integerNumberOr DeclaredSizeNotInteger
  annexKey <- requiredText "annex_key" fields >>= processArgument "annex_key"
  remote <- requiredText "staging_remote" fields >>= processArgument "staging_remote"
  pure ContentDeclaration
    { contentDigest = digest
    , contentSize = size
    , contentAnnexKey = annexKey
    , contentStagingRemote = remote
    }
parseContentDeclaration _ = Left DeclarationNotObject

parseExpectedHead :: Value -> Either ProposalProblem ExpectedHead
parseExpectedHead (Object fields) = do
  requireExactKeys "expected head" ["object_id", "revision_id"] fields
  objectId <- requiredText "object_id" fields >>= validateObjectUrn
  revision <- case KeyMap.lookup "revision_id" fields of
    Just Null -> Right Nothing
    Just (String value) -> Just <$> validateDigest value
    _ -> Left ExpectedRevisionInvalid
  pure ExpectedHead
    { expectedObjectId = objectId
    , expectedRevisionId = revision
    }
parseExpectedHead _ = Left ExpectedHeadNotObject

requireExactKeys :: Text -> [Text] -> KeyMap.KeyMap Value -> Either ProposalProblem ()
requireExactKeys label expected fields =
  let actual = sort (map Key.toText (KeyMap.keys fields))
      wanted = sort expected
  in if actual == wanted
       then Right ()
       else Left (UnknownOrMissingFields label)

requiredText :: Text -> KeyMap.KeyMap Value -> Either ProposalProblem Text
requiredText name fields = case KeyMap.lookup (Key.fromText name) fields of
  Just (String value) | not (Text.null value) -> Right value
  Just (String _) -> Left (FieldEmpty name)
  Just _ -> Left (FieldNotString name)
  Nothing -> Left (FieldRequired name)

requiredArray :: Text -> KeyMap.KeyMap Value -> Either ProposalProblem [Value]
requiredArray name fields = case KeyMap.lookup (Key.fromText name) fields of
  Just (Array values) -> Right (toList values)
  Just _ -> Left (FieldNotArray name)
  Nothing -> Left (FieldRequired name)

requiredNumber :: Text -> KeyMap.KeyMap Value -> Either ProposalProblem Value
requiredNumber name fields = case KeyMap.lookup (Key.fromText name) fields of
  Just value@(Number _) -> Right value
  Just _ -> Left (FieldNotNumber name)
  Nothing -> Left (FieldRequired name)

integerNumberOr :: ProposalProblem -> Value -> Either ProposalProblem Word64
integerNumberOr message value = maybe (Left message) Right (integerNumberValue value)

integerNumber :: Scientific -> Maybe Word64
integerNumber number = case (floatingOrInteger number :: Either Double Integer) of
  Right value
    | value >= (0 :: Integer)
        && value <= fromIntegral (maxBound :: Word64) -> Just (fromIntegral value)
  _ -> Nothing

integerNumberValue :: Value -> Maybe Word64
integerNumberValue (Number number) = integerNumber number
integerNumberValue _ = Nothing

validateDigest :: Text -> Either ProposalProblem Text
validateDigest value
  | Text.length value == 71
      && Text.isPrefixOf "sha256:" value
      && Text.all isLowerHex (Text.drop 7 value) = Right value
  | otherwise = Left DigestInvalid
  where
    isLowerHex character = ('0' <= character && character <= '9')
      || ('a' <= character && character <= 'f')

validateObjectUrn :: Text -> Either ProposalProblem Text
validateObjectUrn value
  | Text.length value == 45
      && Text.isPrefixOf "urn:uuid:" value
      && validUuid uuid = Right value
  | otherwise = Left ObjectUrnInvalid
  where
    uuid = Text.drop 9 value
    validUuid candidate = Text.length candidate == 36
      && and [Text.index candidate index == '-' | index <- [8, 13, 18, 23]]
      && and [isLowerHex character
             | (index, character) <- zip [0 :: Int ..] (Text.unpack candidate)
             , index `notElem` [8, 13, 18, 23]]
    isLowerHex character = ('0' <= character && character <= '9')
      || ('a' <= character && character <= 'f')

validateNamespace :: Text -> Either ProposalProblem Text
validateNamespace value
  | all validSegment (Text.splitOn "/" value) = Right value
  | otherwise = Left NamespaceInvalid
  where
    validSegment segment = case Text.uncons segment of
      Just (initial, rest) -> isLowerLetter initial && Text.all validTail rest
      Nothing -> False
    validTail character = isLowerLetter character || isDigit character
      || character == '.' || character == '_' || character == '-'
    isLowerLetter character = 'a' <= character && character <= 'z'
    isDigit character = '0' <= character && character <= '9'

validateIdentityName :: Text -> Either ProposalProblem Text
validateIdentityName value
  | not (Text.null value) && Text.all valid value = Right value
  | otherwise = Left IdentityNameInvalid
  where
    valid character = ('a' <= character && character <= 'z')
      || ('A' <= character && character <= 'Z')
      || ('0' <= character && character <= '9')
      || character == '-' || character == '_'

processArgument :: Text -> Text -> Either ProposalProblem Text
processArgument field value
  | Text.any isControl value = Left (ControlCharacters field)
  | otherwise = Right value

requireDistinct :: Text -> [Text] -> Either ProposalProblem ()
requireDistinct label values
  | Set.size (Set.fromList values) == length values = Right ()
  | otherwise = Left (NotUnique label)

objectText :: ObjectId -> Text
objectText = objectIdText

decodeValue :: Text -> ByteString.ByteString -> Either ProposalProblem Value
decodeValue label bytes = case decodeUnique (LazyByteString.fromStrict bytes) of
  Left problem -> Left (InvalidJson label problem)
  Right value -> Right value

finallyIO :: Fd -> IO (Either ProposalProblem a) -> IO (Either ProposalProblem a)
finallyIO fd action = do
  result <- tryIO action
  cleanup <- tryIO (closeFd fd)
  case cleanup of
    Left problem -> pure (Left (DescriptorCloseFailed problem))
    Right () -> pure (either Left id result)

tryIO :: forall a. IO a -> IO (Either ProposalProblem a)
tryIO action = do
  result <- try action :: IO (Either IOException a)
  pure $ case result of
    Left problem -> Left (ProposalIoFailure (Text.pack (show problem)))
    Right value -> Right value
