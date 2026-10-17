{-# LANGUAGE OverloadedStrings #-}

module Archive.Door.Types
  ( AgentId
  , parseAgentId
  , agentIdText
  , ContractCitation (..)
  , DraftEnvelope (..)
  , ObjectId
  , parseObjectId
  , objectIdText
  , ProgramId
  , parseProgramId
  , programIdText
  , RecordBody (..)
  , Reference (..)
  , RevisionId
  , parseRevisionId
  , revisionIdText
  , Shape (..)
  , StoredEnvelope (..)
  , parseEnvelope
  , parseDraftEnvelope
  , parseStoredEnvelope
  , validateEnvelope
  , validateDraftEnvelope
  , validateStoredEnvelope
  ) where

import Data.Aeson (Value (Array, Number, Object, String))
import qualified Data.Aeson.Key as Key
import qualified Data.Aeson.KeyMap as KeyMap
import Data.Char (isAlphaNum, isDigit)
import Data.Foldable (toList)
import Data.List (sort)
import Data.Maybe (isNothing)
import Data.Text (Text)
import qualified Data.Text as Text
import Data.Scientific (floatingOrInteger)

newtype ObjectId = ObjectId Text
  deriving (Eq, Ord, Show)

newtype RevisionId = RevisionId Text
  deriving (Eq, Ord, Show)

newtype ProgramId = ProgramId Text
  deriving (Eq, Show)

newtype AgentId = AgentId Text
  deriving (Eq, Show)

data Shape = Artifact | Entity | Claim | Assessment
  deriving (Eq, Show)

data Reference = Reference
  { referenceRole :: Text
  , referenceObject :: ObjectId
  , referenceRevision :: Maybe RevisionId
  } deriving (Eq, Show)

data ContractCitation = ContractCitation
  { contractId :: Text
  , contractVersion :: Text
  , contractDigest :: Text
  } deriving (Eq, Show)

data RecordBody
  = ArtifactRecord Value
  | EntityRecord Value
  | ClaimRecord Value
  | AssessmentRecord Value
  deriving (Eq, Show)

data DraftEnvelope = DraftEnvelope
  { envelopeSpec :: Text
  , envelopeObject :: ObjectId
  , envelopeWriter :: ProgramId
  , envelopeAgent :: AgentId
  , envelopeBody :: RecordBody
  , envelopeReferences :: [Reference]
  , envelopeSupersedes :: [Reference]
  , envelopeSource :: Maybe Value
  , envelopeContracts :: [ContractCitation]
  , envelopeSuppliedRevision :: Maybe RevisionId
  , envelopeSuppliedAt :: Maybe Text
  } deriving (Eq, Show)

-- | A stored envelope is a validated draft with Door's two final fields.
-- Keeping this distinct from 'DraftEnvelope' prevents a caller from treating
-- a proposal as archival state before Door has assigned its revision and time.
data StoredEnvelope = StoredEnvelope
  { storedDraft :: DraftEnvelope
  , storedRevision :: RevisionId
  , storedAt :: Text
  } deriving (Eq, Show)

validateEnvelope :: Value -> Either Text ()
validateEnvelope value = () <$ parseEnvelope value

-- | Parse the strict proposal form from D-3.  A draft has neither
-- 'revision_id' nor 'at'; Door adds those only when finalizing a stored
-- record.
parseDraftEnvelope :: Value -> Either Text DraftEnvelope
parseDraftEnvelope value@(Object object) = do
  parsed <- parseEnvelopeBase value
  rejectPresent "revision_id" (parsedEnvelopeSuppliedRevision parsed)
  rejectPresent "at" (parsedEnvelopeSuppliedAt parsed)
  validateParsedBeforeStrict object parsed
  validateStrictParsed object parsed
  draft <- finalizeParsedEnvelope object parsed
  pure draft { envelopeSuppliedRevision = Nothing, envelopeSuppliedAt = Nothing }
parseDraftEnvelope _ = Left "record must be a JSON object"

parseStoredEnvelope :: Value -> Either Text StoredEnvelope
parseStoredEnvelope value@(Object object) = do
  parsed <- parseEnvelopeBase value
  revision <- case parsedEnvelopeSuppliedRevision parsed of
    Nothing -> Left "stored record requires revision_id"
    Just value' -> parseRevisionId value'
  at <- case parsedEnvelopeSuppliedAt parsed of
    Nothing -> Left "stored record requires at"
    Just value' -> validateTimestamp value'
  validateParsedBeforeStrict object parsed
  validateStrictParsed object parsed
  draft <- finalizeParsedEnvelope object parsed
  pure StoredEnvelope
    { storedDraft = draft
    , storedRevision = revision
    , storedAt = at
    }
parseStoredEnvelope _ = Left "record must be a JSON object"

validateDraftEnvelope :: Value -> Either Text ()
validateDraftEnvelope value = () <$ parseDraftEnvelope value

validateStoredEnvelope :: Value -> Either Text ()
validateStoredEnvelope value = () <$ parseStoredEnvelope value

rejectPresent :: Text -> Maybe a -> Either Text ()
rejectPresent field value = case value of
  Nothing -> Right ()
  Just _ -> Left ("draft envelope must omit " <> field)

parseEnvelope :: Value -> Either Text DraftEnvelope
parseEnvelope = parseDraftEnvelope

data ParsedEnvelope = ParsedEnvelope
  { parsedEnvelopeSpec :: Text
  , parsedEnvelopeObject :: Text
  , parsedEnvelopeWriter :: ProgramId
  , parsedEnvelopeAgent :: AgentId
  , parsedEnvelopeBody :: RecordBody
  , parsedEnvelopeReferences :: [ParsedReference]
  , parsedEnvelopeSupersedes :: [ParsedReference]
  , parsedEnvelopeSource :: Maybe Value
  , parsedEnvelopeContracts :: [ContractCitation]
  , parsedEnvelopeSuppliedRevision :: Maybe Text
  , parsedEnvelopeSuppliedAt :: Maybe Text
  }

parseEnvelopeBase :: Value -> Either Text ParsedEnvelope
parseEnvelopeBase (Object object) = do
  let extra = sort (filter (`notElem` allowedFields) (map Key.toText (KeyMap.keys object)))
  case extra of
    [] -> pure ()
    fields -> Left ("not allowed in an envelope: " <> Text.intercalate ", " fields)
  spec <- requiredText "spec" object >>= validateSpec
  objectIdTextValue <- requiredText "object_id" object
  objectIdCandidate <- checkObjectIdHostname objectIdTextValue
  writer <- requiredText "by" object >>= parseProgramId
  agent <- requiredText "agent" object >>= parseAgentId
  shape <- requiredText "shape" object >>= parseShape
  payload <- requiredValue "payload" object
  refs <- requiredArray "refs" object >>= mapM (parseReference "refs")
  supersedes <- requiredArray "supersedes" object >>= mapM parseSupersedes
  let assessmentTargets = filter ((== "assessment_of") . parsedReferenceRole) refs
  contracts <- case KeyMap.lookup "contracts" object of
    Just (Array values) -> mapM parseContract (toList values)
    Just _ -> Left "contracts must be an array"
    Nothing -> pure []
  case shape of
    Claim | null contracts -> Left "claim requires at least one contract citation"
    _ -> pure ()
  case shape of
    Assessment
      | not (null assessmentTargets) && all parsedHasExactRevision assessmentTargets -> pure ()
      | otherwise -> Left "assessment requires a pinned assessment_of reference"
    _ -> pure ()
  suppliedRevision <- optionalRevision object
  suppliedAt <- optionalAt object
  let body = case shape of
        Artifact -> ArtifactRecord payload
        Entity -> EntityRecord payload
        Claim -> ClaimRecord payload
        Assessment -> AssessmentRecord payload
  pure ParsedEnvelope
    { parsedEnvelopeSpec = spec
    , parsedEnvelopeObject = objectIdCandidate
    , parsedEnvelopeWriter = writer
    , parsedEnvelopeAgent = agent
    , parsedEnvelopeBody = body
    , parsedEnvelopeReferences = refs
    , parsedEnvelopeSupersedes = supersedes
    , parsedEnvelopeSource = KeyMap.lookup "source" object
    , parsedEnvelopeContracts = contracts
    , parsedEnvelopeSuppliedRevision = suppliedRevision
    , parsedEnvelopeSuppliedAt = suppliedAt
    }
parseEnvelopeBase _ = Left "record must be a JSON object"

finalizeParsedEnvelope :: KeyMap.KeyMap Value -> ParsedEnvelope -> Either Text DraftEnvelope
finalizeParsedEnvelope object parsed = do
  objectId <- parseObjectId (parsedEnvelopeObject parsed)
  validateContractPresenceFields object
    (parsedEnvelopeBody parsed) (parsedEnvelopeContracts parsed)
  references <- mapM toReference (parsedEnvelopeReferences parsed)
  supersedes <- mapM toReference (parsedEnvelopeSupersedes parsed)
  suppliedRevision <- traverse parseRevisionId (parsedEnvelopeSuppliedRevision parsed)
  pure DraftEnvelope
    { envelopeSpec = parsedEnvelopeSpec parsed
    , envelopeObject = objectId
    , envelopeWriter = parsedEnvelopeWriter parsed
    , envelopeAgent = parsedEnvelopeAgent parsed
    , envelopeBody = parsedEnvelopeBody parsed
    , envelopeReferences = references
    , envelopeSupersedes = supersedes
    , envelopeSource = parsedEnvelopeSource parsed
    , envelopeContracts = parsedEnvelopeContracts parsed
    , envelopeSuppliedRevision = suppliedRevision
    , envelopeSuppliedAt = parsedEnvelopeSuppliedAt parsed
    }

-- Preserve the established order of envelope errors while keeping raw
-- revision spellings out of the public envelope types. These checks mirror
-- the work previously done by finalization and the strict validator before
-- parsed candidates are converted into opaque identifiers.
validateParsedBeforeStrict :: KeyMap.KeyMap Value -> ParsedEnvelope -> Either Text ()
validateParsedBeforeStrict object parsed = do
  _ <- parseObjectId (parsedEnvelopeObject parsed)
  validateContractPresenceFields object
    (parsedEnvelopeBody parsed) (parsedEnvelopeContracts parsed)
  mapM_ validateParsedReferenceObject
    (parsedEnvelopeReferences parsed <> parsedEnvelopeSupersedes parsed)

validateStrictParsed :: KeyMap.KeyMap Value -> ParsedEnvelope -> Either Text ()
validateStrictParsed object parsed = do
  objectId <- requiredText "object_id" object
  _ <- validateObjectUrn objectId
  writer <- requiredText "by" object
  validatePackageUrl "by" writer
  agent <- requiredText "agent" object
  validateAgentIdentifier agent
  validateContractPresenceFields object
    (parsedEnvelopeBody parsed) (parsedEnvelopeContracts parsed)
  mapM_ validateStrictParsedReference
    (parsedEnvelopeReferences parsed <> parsedEnvelopeSupersedes parsed)
  validateSource object
  validateCorePayload (parsedEnvelopeSpec parsed) (parsedEnvelopeBody parsed)
validateParsedReferenceObject :: ParsedReference -> Either Text ()
validateParsedReferenceObject parsed = do
  _ <- parseObjectId (parsedReferenceObject parsed)
  pure ()

validateStrictParsedReference :: ParsedReference -> Either Text ()
validateStrictParsedReference parsed = do
  _ <- validateObjectUrn (parsedReferenceObject parsed)
  case parsedReferenceRevision parsed of
    Nothing -> Right ()
    Just revision -> parseRevisionId revision >> pure ()

allowedFields :: [Text]
allowedFields =
  [ "spec", "revision_id", "object_id", "shape", "by", "agent", "at"
  , "payload", "refs", "supersedes", "source", "contracts"
  ]

requiredValue :: Text -> KeyMap.KeyMap Value -> Either Text Value
requiredValue name object = case KeyMap.lookup (Key.fromText name) object of
  Nothing -> Left (name <> " is required")
  Just value -> Right value

requiredText :: Text -> KeyMap.KeyMap Value -> Either Text Text
requiredText name object = case KeyMap.lookup (Key.fromText name) object of
  Nothing -> Left (name <> " is required")
  Just (String value) | Text.null value -> Left (name <> " must not be empty")
  Just (String value) -> Right value
  Just _ -> Left (name <> " must be a string")

parseShape :: Text -> Either Text Shape
parseShape "artifact" = Right Artifact
parseShape "entity" = Right Entity
parseShape "claim" = Right Claim
parseShape "assessment" = Right Assessment
parseShape _ = Left "shape must be artifact, entity, claim, or assessment"

validateSpec :: Text -> Either Text Text
validateSpec value = case Text.stripPrefix "archive/" value of
  Just version ->
    let components = Text.splitOn "." version
    in if length components == 3
         && all (not . Text.null) components
         && Text.all (\character -> isDigit character || character == '.') version
       then Right value
       else invalid
  _ -> Left "spec must be archive/ followed by a three-part numeric version"
  where
    invalid = Left "spec must be archive/ followed by a three-part numeric version"

-- | Construct an object identifier only from the accepted lowercase UUID URN.
parseObjectId :: Text -> Either Text ObjectId
parseObjectId value
  | containsHostname value = Left "object_id must not contain a hostname"
  | otherwise = validateObjectUrn value

-- Envelope parsing keeps its historic error ordering: reject hostnames while
-- reading fields, then defer UUID syntax until after the rest of the envelope
-- has been parsed. This returns plain text, never an unvalidated ObjectId.
checkObjectIdHostname :: Text -> Either Text Text
checkObjectIdHostname value
  | containsHostname value = Left "object_id must not contain a hostname"
  | otherwise = Right value

-- | Read the serialized spelling of a validated object identifier.
objectIdText :: ObjectId -> Text
objectIdText (ObjectId value) = value

parseAgentId :: Text -> Either Text AgentId
parseAgentId value = do
  validateAgentIdentifier value
  pure (AgentId value)

-- | Read the serialized spelling of a validated agent identifier.
agentIdText :: AgentId -> Text
agentIdText (AgentId value) = value

-- | Construct a program identifier only when it is a canonical Package URL.
parseProgramId :: Text -> Either Text ProgramId
parseProgramId value = do
  validatePackageUrl "by" value
  pure (ProgramId value)

-- | Read the serialized spelling of a validated program identifier.
programIdText :: ProgramId -> Text
programIdText (ProgramId value) = value

validateObjectUrn :: Text -> Either Text ObjectId
validateObjectUrn value
  | validUuidUrn value = Right (ObjectId value)
  | otherwise = Left "object_id must be a lowercase urn:uuid identifier"

validUuidUrn :: Text -> Bool
validUuidUrn value =
  case Text.stripPrefix "urn:uuid:" value of
    Nothing -> False
    Just uuid -> Text.length uuid == 36
      && and [ Text.index uuid index == '-' | index <- [8, 13, 18, 23] ]
      && Text.all isLowerHex (Text.filter (/= '-') uuid)
  where
    isLowerHex character = ('0' <= character && character <= '9')
      || ('a' <= character && character <= 'f')

validateAgentIdentifier :: Text -> Either Text ()
validateAgentIdentifier value
  | validUuidUrn value = Right ()
  | otherwise = validatePackageUrl "agent" value

validateContractPresenceFields :: KeyMap.KeyMap Value -> RecordBody -> [ContractCitation] -> Either Text ()
validateContractPresenceFields object body contracts = do
  case KeyMap.lookup "contracts" object of
    Nothing -> pure ()
    Just (Array values)
      | null (toList values) -> Left "contracts must be non-empty when supplied"
      | otherwise -> pure ()
    Just _ -> Left "contracts must be an array"
  case body of
    ClaimRecord _ | null contracts ->
      Left "claim requires at least one contract citation"
    AssessmentRecord _ | null contracts ->
      Left "assessment requires at least one contract citation"
    _ -> pure ()

validatePackageUrl :: Text -> Text -> Either Text ()
validatePackageUrl field value =
  case Text.stripPrefix "pkg:" value of
    Nothing -> Left (field <> " must be a canonical Package URL")
    Just rest
      | Text.null rest -> invalid
      | Text.any (`elem` ['?', '#', ':']) rest -> invalid
      | Text.isInfixOf "//" rest -> invalid
      | any Text.null components -> invalid
      | any hasBadCharacter components -> invalid
      | any looksLikeHostname components -> invalid
      | otherwise -> Right ()
      where
        invalid = Left (field <> " must be a canonical Package URL")
        components = Text.splitOn "/" rest
        hasBadCharacter component = not (Text.all packageCharacter component)
        packageCharacter character = isAlphaNum character || character `elem` ("._-~%@" :: String)
        looksLikeHostname component =
          let labels = Text.splitOn "." component
          in length labels > 1 && all validDnsLabel labels
        validDnsLabel label = not (Text.null label)
          && Text.head label /= '-'
          && Text.last label /= '-'
          && Text.all (\character -> isAlphaNum character || character == '-') label

-- | Construct a revision identifier only from the canonical digest spelling.
parseRevisionId :: Text -> Either Text RevisionId
parseRevisionId value
  | validDigest value = Right (RevisionId value)
  | otherwise = Left "revision_id must be sha256 followed by 64 lowercase hexadecimal characters"

-- | Read the serialized spelling of a validated revision identifier.
revisionIdText :: RevisionId -> Text
revisionIdText (RevisionId value) = value

validateTimestamp :: Text -> Either Text Text
validateTimestamp value
  | validRfc3339Utc value = Right value
  | otherwise = Left "at must be an RFC 3339 UTC timestamp ending in Z"

validRfc3339Utc :: Text -> Bool
validRfc3339Utc value =
  let chars = Text.unpack value
      (base, suffix) = case reverse chars of
        ('Z':rest) -> (reverse rest, True)
        _ -> (chars, False)
      (datePart, timeWithSeparator) = break (== 'T') base
      timePart = case timeWithSeparator of
        ('T':rest) -> rest
        _ -> []
      datePieces = splitAtHyphens datePart
      timePieces = splitAtColons timePart
  in suffix
      && length datePart == 10
      && datePieces == Just (4, 2, 2)
      && length timePart >= 8
      && timePieces == Just (2, 2, 2)
      && validDate datePart
      && validClock timePart

splitAtHyphens :: String -> Maybe (Int, Int, Int)
splitAtHyphens value = case splitOnChar '-' value of
  [year, month, day] -> Just (length year, length month, length day)
  _ -> Nothing

splitAtColons :: String -> Maybe (Int, Int, Int)
splitAtColons value = case splitOnChar ':' (take 8 value) of
  [hour, minute, second] -> Just (length hour, length minute, length second)
  _ -> Nothing

splitOnChar :: Char -> String -> [String]
splitOnChar separator = foldr step [[]]
  where
    step character (part:parts)
      | character == separator = [] : part : parts
      | otherwise = (character : part) : parts
    step _ [] = []

validDate :: String -> Bool
validDate value =
  case splitOnChar '-' value of
    [year, month, day]
      | allDigits year && allDigits month && allDigits day ->
          let yearNumber = read year :: Int
              monthNumber = read month :: Int
              dayNumber = read day :: Int
              leap = yearNumber `mod` 4 == 0 && (yearNumber `mod` 100 /= 0 || yearNumber `mod` 400 == 0)
              days = [31, if leap then 29 else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
          in monthNumber >= 1 && monthNumber <= 12 && dayNumber >= 1
             && dayNumber <= days !! (monthNumber - 1)
    _ -> False

validClock :: String -> Bool
validClock value =
  let clock = take 8 value
      fractional = drop 8 value
  in allDigits (take 2 clock)
      && clock !! 2 == ':'
      && allDigits (take 2 (drop 3 clock))
      && clock !! 5 == ':'
      && allDigits (take 2 (drop 6 clock))
      && validFraction fractional
      && read (take 2 clock) <= (23 :: Int)
      && read (take 2 (drop 3 clock)) <= (59 :: Int)
      && read (take 2 (drop 6 clock)) <= (60 :: Int)

validFraction :: String -> Bool
validFraction [] = True
validFraction ('.':digits) = not (null digits) && allDigits digits
validFraction _ = False

allDigits :: String -> Bool
allDigits value = not (null value) && all isDigit value

validateSource :: KeyMap.KeyMap Value -> Either Text ()
validateSource object = case KeyMap.lookup "source" object of
  Nothing -> Right ()
  Just (Object source) -> do
    let extra = sort (filter (`notElem` ["schema", "id", "revision_id"])
                      (map Key.toText (KeyMap.keys source)))
    case extra of
      [] -> pure ()
      fields -> Left ("not allowed in source: " <> Text.intercalate ", " fields)
    schema <- requiredText "schema" source
    if validNamespace schema
      then pure ()
      else Left "source schema must be a portable namespace identifier"
    _ <- requiredText "id" source
    case KeyMap.lookup "revision_id" source of
      Nothing -> pure ()
      Just (String value) -> parseRevisionId value >> pure ()
      Just _ -> Left "source revision_id must be a string"
  Just _ -> Left "source must be an object"

validateCorePayload :: Text -> RecordBody -> Either Text ()
validateCorePayload _ (ArtifactRecord value) = validateArtifactPayload value
validateCorePayload specification (EntityRecord value) = validateEntityPayload specification value
validateCorePayload _ (ClaimRecord value) = requireObjectPayload "claim" value
validateCorePayload _ (AssessmentRecord value) = requireObjectPayload "assessment" value

requireObjectPayload :: Text -> Value -> Either Text ()
requireObjectPayload _shape (Object _) = Right ()
requireObjectPayload shape _ = Left (shape <> " payload must be an object")

validateArtifactPayload :: Value -> Either Text ()
validateArtifactPayload (Object object) = do
  requireOnly ["content"] object "artifact payload"
  content <- case KeyMap.lookup "content" object of
    Just (Object value) -> Right value
    Just _ -> Left "artifact payload content must be an object"
    Nothing -> Left "artifact payload content is required"
  requireOnly ["digest", "size"] content "artifact content"
  digest <- requiredText "digest" content
  if validDigest digest
    then pure ()
    else Left "artifact content digest must be sha256 followed by 64 lowercase hexadecimal characters"
  size <- case KeyMap.lookup "size" content of
    Just (Number value) -> Right value
    Just _ -> Left "artifact content size must be a non-negative integer"
    Nothing -> Left "artifact content size is required"
  case (floatingOrInteger size :: Either Double Integer) of
    Right value | value >= 0 -> pure ()
    _ -> Left "artifact content size must be a non-negative integer"
validateArtifactPayload _ = Left "artifact payload must be an object"

validateEntityPayload :: Text -> Value -> Either Text ()
validateEntityPayload specification (Object object) = do
  requireOnly ["kind", "label"] object "entity payload"
  kind <- requiredText "kind" object
  let installedKinds = ["person", "organisation", "software", "model", "process"]
        <> (if specification `elem` ["archive/0.0.3", "archive/0.0.4", "archive/0.0.5", "archive/0.0.6", "archive/0.0.7", "archive/0.0.8"] then ["musical_work"] else [])
        <> (if specification `elem` ["archive/0.0.5", "archive/0.0.6", "archive/0.0.7", "archive/0.0.8"] then ["recording_session"] else [])
  if kind `elem` installedKinds
    then pure ()
    else Left "entity kind is not defined by the core context"
  case KeyMap.lookup "label" object of
    Nothing -> pure ()
    Just (String value) | not (Text.null value) -> pure ()
    Just _ -> Left "entity label must be a non-empty string"
validateEntityPayload _ _ = Left "entity payload must be an object"

requireOnly :: [Text] -> KeyMap.KeyMap Value -> Text -> Either Text ()
requireOnly allowed object label =
  case sort (filter (`notElem` allowed) (map Key.toText (KeyMap.keys object))) of
    [] -> pure ()
    fields -> Left ("not allowed in " <> label <> ": " <> Text.intercalate ", " fields)

validNamespace :: Text -> Bool
validNamespace value =
  case Text.splitOn "/" value of
    [] -> False
    components -> all validComponent components
  where
    validComponent component = case Text.uncons component of
      Nothing -> False
      Just (first, rest) -> isLowerAsciiLetter first
        && Text.all (\character -> isLowerAsciiLetter character || isDigit character
          || character `elem` ("._-" :: String)) rest
    isLowerAsciiLetter character = character >= 'a' && character <= 'z'

containsHostname :: Text -> Bool
containsHostname value =
  "://" `Text.isInfixOf` value || any isDomainName candidates
  where
    candidates = value : suffixes "@" value <> suffixes ":" value
    suffixes separator text = case Text.splitOn separator text of
      [_] -> []
      parts -> [last parts]
    isDomainName candidate = case Text.splitOn "." candidate of
      [_] -> False
      labels -> all validLabel labels
    validLabel label = not (Text.null label)
      && Text.head label /= '-'
      && Text.last label /= '-'
      && Text.all (\character -> isAlphaNum character || character == '-') label

requiredArray :: Text -> KeyMap.KeyMap Value -> Either Text [Value]
requiredArray name object = case KeyMap.lookup (Key.fromText name) object of
  Nothing -> Left (name <> " is required")
  Just (Array values) -> Right (toList values)
  Just _ -> Left (name <> " must be an array")

parseSupersedes :: Value -> Either Text ParsedReference
parseSupersedes value = do
  reference <- parseReference "supersedes" value
  if parsedReferenceRole reference /= "supersedes"
    then Left "supersedes entries must have role supersedes"
    else if not (parsedHasExactRevision reference)
      then Left "supersedes reference must pin revision_id"
      else pure reference

data ParsedReference = ParsedReference
  { parsedReferenceRole :: Text
  , parsedReferenceObject :: Text
  , parsedReferenceRevision :: Maybe Text
  }

parseReference :: Text -> Value -> Either Text ParsedReference
parseReference field (Object object) = do
  let extra = sort (filter (`notElem` ["role", "id", "revision_id"])
                    (map Key.toText (KeyMap.keys object)))
  case extra of
    [] -> pure ()
    fields -> Left ("not allowed in a " <> field <> " reference: " <> Text.intercalate ", " fields)
  role <- requiredText "role" object
  objectIdTextValue <- requiredText "id" object
  objectId <- checkObjectIdHostname objectIdTextValue
  revision <- case KeyMap.lookup "revision_id" object of
    Nothing -> pure Nothing
    Just (String value) | not (Text.null value) -> pure (Just value)
    Just _ -> Left "reference revision_id must be a non-empty string"
  pure ParsedReference
    { parsedReferenceRole = role
    , parsedReferenceObject = objectId
    , parsedReferenceRevision = revision
    }
parseReference field _ = Left (field <> " reference must be an object")

toReference :: ParsedReference -> Either Text Reference
toReference parsed = do
  objectId <- parseObjectId (parsedReferenceObject parsed)
  revision <- traverse parseRevisionId (parsedReferenceRevision parsed)
  pure (Reference (parsedReferenceRole parsed) objectId revision)

parsedHasExactRevision :: ParsedReference -> Bool
parsedHasExactRevision = not . isNothing . parsedReferenceRevision

optionalRevision :: KeyMap.KeyMap Value -> Either Text (Maybe Text)
optionalRevision object = case KeyMap.lookup "revision_id" object of
  Nothing -> Right Nothing
  Just (String value) | not (Text.null value) -> Right (Just value)
  Just _ -> Left "revision_id must be a non-empty string"

optionalAt :: KeyMap.KeyMap Value -> Either Text (Maybe Text)
optionalAt object = case KeyMap.lookup "at" object of
  Nothing -> Right Nothing
  Just (String value) | not (Text.null value) -> Right (Just value)
  Just _ -> Left "at must be a non-empty string when supplied"

parseContract :: Value -> Either Text ContractCitation
parseContract (Object object) = do
  let extra = sort (filter (`notElem` ["id", "version", "digest"])
                    (map Key.toText (KeyMap.keys object)))
  case extra of
    [] -> pure ()
    fields -> Left ("not allowed in a contract citation: " <> Text.intercalate ", " fields)
  identifier <- requiredText "id" object
  version <- requiredText "version" object
  digest <- requiredText "digest" object
  if validDigest digest
    then pure (ContractCitation identifier version digest)
    else Left "contract digest must be sha256 followed by 64 lowercase hexadecimal characters"
parseContract _ = Left "contract citation must be an object"

validDigest :: Text -> Bool
validDigest digest =
  Text.length digest == 71
    && Text.isPrefixOf "sha256:" digest
    && Text.all isLowerHex (Text.drop 7 digest)
  where
    isLowerHex character = character `elem` ['0' .. '9'] || character `elem` ['a' .. 'f']
