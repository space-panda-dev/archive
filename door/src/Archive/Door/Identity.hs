{-# LANGUAGE OverloadedStrings #-}

-- | Portable archive and object identifiers.
--
-- UUIDv5 is used only as a deterministic namespace.  The name is the exact
-- UTF-8 byte sequence specified by ADR 0021: namespace, a NUL byte, then
-- name.  SHA-1 here is only RFC 9562's UUIDv5 name hash, not an integrity
-- claim; it comes from cryptohash-sha1, which Door already uses to check
-- Git object names, so allocation carries no digest code of its own.
module Archive.Door.Identity
  ( IdentityName
  , IdentityNamespace
  , allocateObjectId
  , identityNameText
  , identityNamespaceText
  , newArchiveId
  , parseArchiveId
  , parseIdentityName
  , parseIdentityNamespace
  , uuidV4
  , uuidV5
  , uuidV5Name
  ) where

import Archive.Door.Hex (byteHex)
import qualified Crypto.Hash.SHA1 as SHA1
import qualified Data.ByteString as ByteString
import Data.Bits (Bits ((.&.), (.|.)))
import Data.Char (ord)
import Data.Text (Text)
import qualified Data.Text as Text
import qualified Data.Text.Encoding as Text.Encoding
import Data.Word (Word8)
import System.IO (IOMode (ReadMode), withBinaryFile)

newtype IdentityNamespace = IdentityNamespace Text
  deriving (Eq, Ord, Show)

newtype IdentityName = IdentityName Text
  deriving (Eq, Ord, Show)

identityNamespaceText :: IdentityNamespace -> Text
identityNamespaceText (IdentityNamespace value) = value

identityNameText :: IdentityName -> Text
identityNameText (IdentityName value) = value

parseIdentityNamespace :: Text -> Either Text IdentityNamespace
parseIdentityNamespace value
  | Text.null value = Left "identity namespace must not be empty"
  | all validSegment (Text.splitOn "/" value) = Right (IdentityNamespace value)
  | otherwise = Left "identity namespace has an invalid segment"
  where
    validSegment segment = case Text.uncons segment of
      Nothing -> False
      Just (first, rest) -> isLowerAsciiLetter first && Text.all validTail rest
    validTail character = isLowerAsciiLetter character
      || isAsciiDigit character
      || character == '.'
      || character == '_'
      || character == '-'

parseIdentityName :: Text -> Either Text IdentityName
parseIdentityName value
  | Text.null value = Left "identity name must not be empty"
  | Text.all validCharacter value = Right (IdentityName value)
  | otherwise = Left "identity name must contain only ASCII letters, digits, hyphen, or underscore"
  where
    validCharacter character = isAsciiLetter character
      || isAsciiDigit character
      || character == '-'
      || character == '_'

-- | Allocate an object identifier from an archive identifier and its
-- contract-owned identity allocation.
allocateObjectId :: Text -> Text -> Text -> Either Text Text
allocateObjectId archive namespace name = do
  archiveUuid <- parseArchiveId archive
  identityNamespace <- parseIdentityNamespace namespace
  identityName <- parseIdentityName name
  uuidV5 archiveUuid identityNamespace identityName

-- | Derive a portable object URN. The namespace and name are validated types;
-- the archive UUID is checked here as well.
uuidV5 :: Text -> IdentityNamespace -> IdentityName -> Either Text Text
uuidV5 archiveUuid (IdentityNamespace namespace) (IdentityName name) =
  uuidV5Name archiveUuid (Text.Encoding.encodeUtf8 namespace
    <> ByteString.singleton 0
    <> Text.Encoding.encodeUtf8 name)

-- | RFC 9562 UUIDv5 of raw name bytes under a namespace UUID, as a URN.
-- 'uuidV5' fixes the name's spelling; this is the bare algorithm, exported
-- so the RFC's own known-answer vectors can check it.
uuidV5Name :: Text -> ByteString.ByteString -> Either Text Text
uuidV5Name namespaceUuid nameBytes = do
  namespaceBytes <- parseUuidBytes namespaceUuid
  let digest = SHA1.hash (namespaceBytes <> nameBytes)
  ("urn:uuid:" <>) <$> formatUuid (setVersionAndVariant (ByteString.take 16 digest))

-- | Generate a random canonical lowercase RFC 9562 UUIDv4 and wrap it as an
-- archive identifier.
newArchiveId :: IO Text
newArchiveId = ("urn:uuid:" <>) <$> uuidV4

uuidV4 :: IO Text
uuidV4 = withBinaryFile "/dev/urandom" ReadMode $ \handle -> do
  bytes <- ByteString.hGet handle 16
  if ByteString.length bytes /= 16
    then ioError (userError "unable to read 16 random bytes for UUIDv4")
    else either (ioError . userError . Text.unpack) pure
      (formatUuid (setVersionAndVariantV4 bytes))

parseArchiveId :: Text -> Either Text Text
parseArchiveId value = do
  uuid <- case Text.stripPrefix "urn:uuid:" value of
    Just suffix -> Right suffix
    Nothing -> Left "archive_id must have the form urn:uuid:<uuid>"
  _ <- parseUuidBytes uuid
  pure uuid

parseUuidBytes :: Text -> Either Text ByteString.ByteString
parseUuidBytes value
  | Text.length value /= 36 = Left "UUID must have 36 canonical characters"
  | map (Text.index value) [8, 13, 18, 23] /= replicate 4 '-' = Left "UUID has an invalid hyphen position"
  | Text.length (Text.filter (/= '-') value) /= 32 = Left "UUID has an invalid hyphen position"
  | otherwise = do
      pairs <- mapM parsePair [0, 2 .. 30]
      pure (ByteString.pack pairs)
  where
    compact = Text.filter (/= '-') value
    parsePair offset = do
      high <- hexValue (Text.index compact offset)
      low <- hexValue (Text.index compact (offset + 1))
      pure (high * 16 + low)

hexValue :: Char -> Either Text Word8
hexValue character
  | '0' <= character && character <= '9' = Right (fromIntegral (ord character - ord '0'))
  | 'a' <= character && character <= 'f' = Right (fromIntegral (ord character - ord 'a' + 10))
  | otherwise = Left "UUID must use lowercase hexadecimal characters"

formatUuid :: ByteString.ByteString -> Either Text Text
formatUuid bytes
  | ByteString.length bytes /= 16 = Left "UUID formatting requires 16 bytes"
  | otherwise = Right (Text.pack
      (concatMap byteHexWithIndex (zip [0 :: Int ..] (ByteString.unpack bytes))))
  where
    byteHexWithIndex (index, byte) =
      (if index `elem` [4, 6, 8, 10] then "-" else "")
        <> byteHex byte

setVersionAndVariant :: ByteString.ByteString -> ByteString.ByteString
setVersionAndVariant bytes = setByte 8 ((ByteString.index bytes 8 .&. 0x3f) .|. 0x80)
  (setByte 6 ((ByteString.index bytes 6 .&. 0x0f) .|. 0x50) bytes)

setVersionAndVariantV4 :: ByteString.ByteString -> ByteString.ByteString
setVersionAndVariantV4 bytes = setByte 8 ((ByteString.index bytes 8 .&. 0x3f) .|. 0x80)
  (setByte 6 ((ByteString.index bytes 6 .&. 0x0f) .|. 0x40) bytes)

setByte :: Int -> Word8 -> ByteString.ByteString -> ByteString.ByteString
setByte offset value bytes = ByteString.take offset bytes
  <> ByteString.singleton value
  <> ByteString.drop (offset + 1) bytes

isAsciiLetter :: Char -> Bool
isAsciiLetter character = isLowerAsciiLetter character || ('A' <= character && character <= 'Z')

isLowerAsciiLetter :: Char -> Bool
isLowerAsciiLetter character = 'a' <= character && character <= 'z'

isAsciiDigit :: Char -> Bool
isAsciiDigit character = '0' <= character && character <= '9'
