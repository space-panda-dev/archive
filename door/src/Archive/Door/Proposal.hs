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
import Archive.Door.Types
  ( DraftEnvelope (..)
  , ObjectId
  , objectIdText
  , RecordBody (..)
  , parseDraftEnvelope
  )
import Control.Exception (IOException, try)
import Data.Aeson (Value (..))
import Data.Aeson.Key qualified as Key
import Data.Aeson.KeyMap qualified as KeyMap
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

readProposal :: ProposalKind -> FilePath -> IO (Either Text Proposal)
readProposal kind root = do
  absoluteResult <- tryIO (makeAbsolute root)
  case absoluteResult of
    Left problem -> pure (Left problem)
    Right absoluteRoot -> do
      rootResult <- tryIO (openProposalRoot absoluteRoot)
      case rootResult of
        Left problem -> pure (Left problem)
        Right rootFd -> finallyIO rootFd (readProposalFromFd kind absoluteRoot rootFd)

-- The descriptor traversal is authoritative for files named by the manifest.
-- The unix package exposes openat/nofollow but no directory-stream operation
-- relative to an already-open descriptor, so this module does not claim a
-- race-free stray-entry check.
readProposalFromFd :: ProposalKind -> FilePath -> Fd -> IO (Either Text Proposal)
readProposalFromFd kind root rootFd = do
  manifestResult <- readRegularAt manifestLimit (Just rootFd) "manifest.json"
  case manifestResult of
    Left problem -> pure (Left problem)
    Right manifestBytes ->
      case decodeValue "manifest.json" manifestBytes >>= parseManifest kind of
        Left problem -> pure (Left problem)
        Right manifest -> do
          recordsFdResult <- tryIO (openDirectoryAt (Just rootFd) "records")
          case recordsFdResult of
            Left problem -> pure (Left problem)
            Right recordsFd -> finallyIO recordsFd $ do
              recordsResult <- readRecordsAt recordsFd (manifestRecords manifest)
              case recordsResult of
                Left problem -> pure (Left problem)
                Right records ->
                  pure $ validateProposalAgainstFiles manifest records
                    >>= \() -> Right Proposal
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
  current <- readProposal (proposalKind original) (proposalRoot original)
  pure $ case current of
    Left problem -> Left ("proposal recheck failed: " <> problem)
    Right proposal
      | proposalManifestBytes proposal /= proposalManifestBytes original ->
          Left "proposal manifest changed during operation"
      | sameRecords proposal original -> Right ()
      | otherwise -> Left "proposal record bytes changed during operation"
  where
    sameRecords left right =
      map recordFingerprint (proposalRecords left)
        == map recordFingerprint (proposalRecords right)
    recordFingerprint record =
      (proposalRecordPath record, proposalRecordBytes record)

validateProposal :: Proposal -> Either Text ()
validateProposal proposal = do
  let manifest = proposalManifest proposal
  validateProtocol (proposalKind proposal) (manifestProtocol manifest)
  validateManifestLists (proposalKind proposal) manifest
  validateDraftRelationships (proposalKind proposal) manifest (proposalRecords proposal)

validateManifestLists :: ProposalKind -> ProposalManifest -> Either Text ()
validateManifestLists kind manifest = do
  requireDistinct "manifest records path" (map pathText (manifestRecords manifest))
  requireDistinct "identity object_id" (map identityObjectId (manifestIdentities manifest))
  requireDistinct "expected_heads object_id" (map expectedObjectId (manifestExpectedHeads manifest))
  case kind of
    TransitionProposal
      | null (manifestRecords manifest)
          || null (manifestIdentities manifest)
          || null (manifestExpectedHeads manifest) ->
          Left "transition manifest arrays must be non-empty"
    _ -> pure ()
  where
    pathText (ProposalPath path) = path

readRecordsAt :: Fd -> [ProposalPath] -> IO (Either Text [ProposalRecord])
readRecordsAt recordsFd = go proposalRecordsLimit []
  where
    go _ records [] = pure (Right (reverse records))
    go remaining records (path : paths) = do
      result <- readRecordAt (min recordLimit remaining) recordsFd path
      case result of
        Left problem -> pure (Left problem)
        Right record -> go (remaining - fromIntegral (ByteString.length (proposalRecordBytes record)))
          (record : records) paths

readRecordAt :: Integer -> Fd -> ProposalPath -> IO (Either Text ProposalRecord)
readRecordAt limit recordsFd path@(ProposalPath pathText) = do
  let filename = Text.unpack (Text.drop (Text.length "records/") pathText)
  result <- readRegularAt limit (Just recordsFd) filename
  pure $ result >>= \bytes -> do
    value <- decodeValue pathText bytes
    draft <- either (\problem -> Left ("invalid draft " <> pathText <> ": " <> problem)) Right
      (parseDraftEnvelope value)
    pure ProposalRecord
      { proposalRecordPath = path
      , proposalRecordBytes = bytes
      , proposalRecordValue = value
      , proposalRecordDraft = draft
      }

readRegularAt :: Integer -> Maybe Fd -> FilePath -> IO (Either Text ByteString.ByteString)
readRegularAt limit parent name = do
  opened <- tryIO (openFdAt parent name ReadOnly noFollowFlags)
  case opened of
    Left problem -> pure (Left (pathMessage name problem))
    Right fd -> do
      status <- tryIO (getFdStatus fd)
      case status of
        Left problem -> closeFd fd >> pure (Left problem)
        Right value
          | not (isRegularFile value) -> closeFd fd >> pure (Left (pathMessage name "must be a regular file"))
          | toInteger (fileSize value) > limit ->
              closeFd fd >> pure (Left (pathMessage name "exceeds the proposal size limit"))
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
                      then Left (pathMessage name "exceeds the proposal size limit")
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
  -> Either Text ()
validateProposalAgainstFiles manifest records = do
  let listed = manifestRecords manifest
      expected = Set.fromList listed
  if Set.size expected /= length listed
    then Left "manifest records contains a duplicate path"
    else if length records /= length listed
      then Left "proposal record count does not match manifest"
      else do
        validateProtocol kindFromManifest (manifestProtocol manifest)
        validateManifestLists kindFromManifest manifest
        validateDraftRelationships kindFromManifest manifest records
  where
    kindFromManifest
      | manifestProtocol manifest == "archive-transition/1" = TransitionProposal
      | otherwise = AdmissionProposal

validateDraftRelationships :: ProposalKind -> ProposalManifest -> [ProposalRecord] -> Either Text ()
validateDraftRelationships kind manifest records = do
  let drafts = map proposalRecordDraft records
      draftObjects = map (objectText . envelopeObject) drafts
      identityObjects = map identityObjectId (manifestIdentities manifest)
      headObjects = map expectedObjectId (manifestExpectedHeads manifest)
      headSet = Set.fromList headObjects
      draftSet = Set.fromList draftObjects
  if not (all (\object -> Set.member object headSet) draftObjects)
    then Left "expected_heads must include every drafted object"
    else if kind == AdmissionProposal && headSet /= draftSet
      then Left "expected_heads must contain exactly the drafted objects"
    else if not (all (\object -> Set.member object draftSet) identityObjects)
      then Left "identities must refer to drafted objects"
      else if Set.fromList identityObjects /= Set.fromList
        [ expectedObjectId expected
        | expected <- manifestExpectedHeads manifest
        , expectedRevisionId expected == Nothing
        ]
        then Left "identities must equal the objects with null expected heads"
      else case kind of
        AdmissionProposal -> validateAdmissionContent manifest drafts
        TransitionProposal -> pure ()

validateAdmissionContent :: ProposalManifest -> [DraftEnvelope] -> Either Text ()
validateAdmissionContent manifest drafts = do
  let declared = manifestContents manifest
      declaredDigests = map contentDigest declared
  referenced <- concat <$> mapM contentReferences drafts
  let
      referencedDigests = map fst referenced
  requireDistinct "content digest" declaredDigests
  if Set.fromList declaredDigests /= Set.fromList referencedDigests
    then Left "contents must match artifact content references exactly"
    else mapM_ (checkContentReference declared) referenced
  where
    checkContentReference declarations (digest, size) =
      case findDigest digest declarations of
        Nothing -> Left "artifact content is not declared in contents"
        Just declaration
          | contentSize declaration /= size -> Left "content size disagrees with artifact payload"
          | otherwise -> Right ()

contentReferences :: DraftEnvelope -> Either Text [(Text, Word64)]
contentReferences draft = case envelopeBody draft of
  ArtifactRecord (Object fields) -> case KeyMap.lookup "content" fields of
    Just (Object content) -> case (KeyMap.lookup "digest" content, KeyMap.lookup "size" content) of
      (Just (String digest), Just (Number number)) -> case integerNumber number of
        Just size -> Right [(digest, size)]
        Nothing -> Left "artifact content size is not a non-negative integer"
      (Nothing, _) -> Left "artifact content digest is missing"
      (_, Nothing) -> Left "artifact content size is missing"
      _ -> Left "artifact content digest and size have invalid types"
    _ -> Left "artifact content is not an object"
  ArtifactRecord _ -> Left "artifact payload is not an object"
  _ -> Right []

findDigest :: Text -> [ContentDeclaration] -> Maybe ContentDeclaration
findDigest digest = go
  where
    go [] = Nothing
    go (declaration : rest)
      | contentDigest declaration == digest = Just declaration
      | otherwise = go rest

parseManifest :: ProposalKind -> Value -> Either Text ProposalManifest
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
    then Left "transition manifest arrays must be non-empty"
    else pure ProposalManifest
      { manifestProtocol = protocol
      , manifestRecords = recordValues
      , manifestIdentities = identities
      , manifestContents = contents
      , manifestExpectedHeads = expectedHeads
      }
parseManifest _ _ = Left "proposal manifest must be a JSON object"

validateProtocol :: ProposalKind -> Text -> Either Text ()
validateProtocol AdmissionProposal "archive-admission/1" = Right ()
validateProtocol TransitionProposal "archive-transition/1" = Right ()
validateProtocol _ _ = Left "proposal manifest has the wrong protocol"

parseRecordPath :: Value -> Either Text ProposalPath
parseRecordPath (String value) = do
  path <- parseSafePath value
  case Text.splitOn "/" value of
    ["records", filename]
      | Text.isSuffixOf ".json" filename && Text.length filename > 5 -> Right path
    _ -> Left "record path must be records/<name>.json"
parseRecordPath _ = Left "manifest records entries must be strings"

parseSafePath :: Text -> Either Text ProposalPath
parseSafePath value
  | Text.null value = Left "proposal path must not be empty"
  | Text.head value == '/' = Left "proposal path must be relative"
  | Text.any (== '\0') value = Left "proposal path must not contain NUL"
  | any invalidComponent components = Left "proposal path contains an unsafe component"
  | Text.intercalate "/" components /= value = Left "proposal path is not normalized"
  | otherwise = Right (ProposalPath value)
  where
    components = Text.splitOn "/" value
    invalidComponent component =
      Text.null component || component == "." || component == ".."

parseIdentityAllocation :: Value -> Either Text IdentityAllocation
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
parseIdentityAllocation _ = Left "identity allocation must be an object"

parseContentDeclaration :: Value -> Either Text ContentDeclaration
parseContentDeclaration (Object fields) = do
  requireExactKeys "content declaration" ["digest", "size", "annex_key", "staging_remote"] fields
  digest <- requiredText "digest" fields >>= validateDigest
  size <- requiredNumber "size" fields >>= integerNumberOr "size must be a non-negative integer"
  annexKey <- requiredText "annex_key" fields >>= processArgument "annex_key"
  remote <- requiredText "staging_remote" fields >>= processArgument "staging_remote"
  pure ContentDeclaration
    { contentDigest = digest
    , contentSize = size
    , contentAnnexKey = annexKey
    , contentStagingRemote = remote
    }
parseContentDeclaration _ = Left "content declaration must be an object"

parseExpectedHead :: Value -> Either Text ExpectedHead
parseExpectedHead (Object fields) = do
  requireExactKeys "expected head" ["object_id", "revision_id"] fields
  objectId <- requiredText "object_id" fields >>= validateObjectUrn
  revision <- case KeyMap.lookup "revision_id" fields of
    Just Null -> Right Nothing
    Just (String value) -> Just <$> validateDigest value
    _ -> Left "expected_heads revision_id must be null or a digest"
  pure ExpectedHead
    { expectedObjectId = objectId
    , expectedRevisionId = revision
    }
parseExpectedHead _ = Left "expected head must be an object"

requireExactKeys :: Text -> [Text] -> KeyMap.KeyMap Value -> Either Text ()
requireExactKeys label expected fields =
  let actual = sort (map Key.toText (KeyMap.keys fields))
      wanted = sort expected
  in if actual == wanted
       then Right ()
       else Left (label <> " has unknown or missing fields")

requiredText :: Text -> KeyMap.KeyMap Value -> Either Text Text
requiredText name fields = case KeyMap.lookup (Key.fromText name) fields of
  Just (String value) | not (Text.null value) -> Right value
  Just (String _) -> Left (name <> " must not be empty")
  Just _ -> Left (name <> " must be a string")
  Nothing -> Left (name <> " is required")

requiredArray :: Text -> KeyMap.KeyMap Value -> Either Text [Value]
requiredArray name fields = case KeyMap.lookup (Key.fromText name) fields of
  Just (Array values) -> Right (toList values)
  Just _ -> Left (name <> " must be an array")
  Nothing -> Left (name <> " is required")

requiredNumber :: Text -> KeyMap.KeyMap Value -> Either Text Value
requiredNumber name fields = case KeyMap.lookup (Key.fromText name) fields of
  Just value@(Number _) -> Right value
  Just _ -> Left (name <> " must be a number")
  Nothing -> Left (name <> " is required")

integerNumberOr :: Text -> Value -> Either Text Word64
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

validateDigest :: Text -> Either Text Text
validateDigest value
  | Text.length value == 71
      && Text.isPrefixOf "sha256:" value
      && Text.all isLowerHex (Text.drop 7 value) = Right value
  | otherwise = Left "digest must be sha256 followed by 64 lowercase hexadecimal characters"
  where
    isLowerHex character = ('0' <= character && character <= '9')
      || ('a' <= character && character <= 'f')

validateObjectUrn :: Text -> Either Text Text
validateObjectUrn value
  | Text.length value == 45
      && Text.isPrefixOf "urn:uuid:" value
      && validUuid uuid = Right value
  | otherwise = Left "object_id must be a lowercase urn:uuid identifier"
  where
    uuid = Text.drop 9 value
    validUuid candidate = Text.length candidate == 36
      && and [Text.index candidate index == '-' | index <- [8, 13, 18, 23]]
      && and [isLowerHex character
             | (index, character) <- zip [0 :: Int ..] (Text.unpack candidate)
             , index `notElem` [8, 13, 18, 23]]
    isLowerHex character = ('0' <= character && character <= '9')
      || ('a' <= character && character <= 'f')

validateNamespace :: Text -> Either Text Text
validateNamespace value
  | all validSegment (Text.splitOn "/" value) = Right value
  | otherwise = Left "identity namespace has an invalid segment"
  where
    validSegment segment = case Text.uncons segment of
      Just (first, rest) -> isLowerLetter first && Text.all validTail rest
      Nothing -> False
    validTail character = isLowerLetter character || isDigit character
      || character == '.' || character == '_' || character == '-'
    isLowerLetter character = 'a' <= character && character <= 'z'
    isDigit character = '0' <= character && character <= '9'

validateIdentityName :: Text -> Either Text Text
validateIdentityName value
  | not (Text.null value) && Text.all valid value = Right value
  | otherwise = Left "identity name has invalid characters"
  where
    valid character = ('a' <= character && character <= 'z')
      || ('A' <= character && character <= 'Z')
      || ('0' <= character && character <= '9')
      || character == '-' || character == '_'

processArgument :: Text -> Text -> Either Text Text
processArgument field value
  | Text.any isControl value = Left (field <> " must not contain control characters")
  | otherwise = Right value

requireDistinct :: Text -> [Text] -> Either Text ()
requireDistinct label values
  | Set.size (Set.fromList values) == length values = Right ()
  | otherwise = Left (label <> " must be unique")

objectText :: ObjectId -> Text
objectText = objectIdText

decodeValue :: Text -> ByteString.ByteString -> Either Text Value
decodeValue label bytes = case decodeUnique (LazyByteString.fromStrict bytes) of
  Left problem -> Left ("invalid JSON in " <> label <> ": " <> Text.pack problem)
  Right value -> Right value

pathMessage :: FilePath -> Text -> Text
pathMessage path message = Text.pack path <> ": " <> message

finallyIO :: Fd -> IO (Either Text a) -> IO (Either Text a)
finallyIO fd action = do
  result <- tryIO action
  cleanup <- tryIO (closeFd fd)
  case cleanup of
    Left problem -> pure (Left ("proposal descriptor close failed: " <> problem))
    Right () -> pure (either Left id result)

tryIO :: forall a. IO a -> IO (Either Text a)
tryIO action = do
  result <- try action :: IO (Either IOException a)
  pure $ case result of
    Left problem -> Left ("proposal I/O failure: " <> Text.pack (show problem))
    Right value -> Right value
