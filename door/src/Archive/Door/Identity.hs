{-# LANGUAGE OverloadedStrings #-}

-- | Portable archive and object identifiers.
--
-- UUIDv5 is used only as a deterministic namespace.  The name is the exact
-- UTF-8 byte sequence specified by ADR 0021: namespace, a NUL byte, then
-- name.  The SHA-1 implementation is kept here so identifier allocation does
-- not acquire a second package dependency merely for a digest that is not an
-- integrity claim.
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
  ) where

import qualified Data.ByteString as ByteString
import Data.Bits
  ( Bits (complement, shiftL, shiftR, xor, (.&.), (.|.))
  , rotateL
  )
import Data.Char (ord)
import Data.Text (Text)
import qualified Data.Text as Text
import qualified Data.Text.Encoding as Text.Encoding
import Data.Word (Word32, Word8)
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
uuidV5 archiveUuid (IdentityNamespace namespace) (IdentityName name) = do
  archiveBytes <- parseUuidBytes archiveUuid
  let nameBytes = Text.Encoding.encodeUtf8 namespace
        <> ByteString.singleton 0
        <> Text.Encoding.encodeUtf8 name
      digest = sha1 (archiveBytes <> nameBytes)
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
        <> [hexDigit (byte `shiftR` 4), hexDigit (byte .&. 0x0f)]
    hexDigit value = "0123456789abcdef" !! fromIntegral value

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

-- SHA-1, with the algorithm used only for UUIDv5 namespacing.
sha1 :: ByteString.ByteString -> ByteString.ByteString
sha1 input = ByteString.pack (concatMap wordBytes finalState)
  where
    padded = pad input
    chunks = chunk64 padded
    finalState = stateWords (foldl processChunk initialState chunks)

pad :: ByteString.ByteString -> ByteString.ByteString
pad input = input <> ByteString.singleton 0x80 <> ByteString.replicate zeroCount 0
  <> word64Bytes (fromIntegral (ByteString.length input) * 8)
  where
    zeroCount = (56 - ((ByteString.length input + 1) `mod` 64)) `mod` 64

chunk64 :: ByteString.ByteString -> [ByteString.ByteString]
chunk64 bytes
  | ByteString.null bytes = []
  | otherwise = ByteString.take 64 bytes : chunk64 (ByteString.drop 64 bytes)

type Sha1State = (Word32, Word32, Word32, Word32, Word32)

initialState :: Sha1State
initialState = (0x67452301, 0xefcdab89, 0x98badcfe, 0x10325476, 0xc3d2e1f0)

stateWords :: Sha1State -> [Word32]
stateWords (a, b, c, d, e) = [a, b, c, d, e]

processChunk :: Sha1State -> ByteString.ByteString -> Sha1State
processChunk (h0, h1, h2, h3, h4) chunk =
  (add32 h0 a, add32 h1 b, add32 h2 c, add32 h3 d, add32 h4 e)
  where
    words16 = map bytesWord32 (chunk64Words chunk)
    words80 = words16 <> [rotateL (words80 !! (index - 3) `xor` words80 !! (index - 8)
      `xor` words80 !! (index - 14) `xor` words80 !! (index - 16)) 1 | index <- [16 .. 79]]
    (a, b, c, d, e) = foldl roundStep (h0, h1, h2, h3, h4) [0 .. 79]
    roundStep (a0, b0, c0, d0, e0) index =
      let (function, constant) = roundConstants index
          temp = add32 (rotateL a0 5) (add32 (function b0 c0 d0) (add32 e0 (add32 (words80 !! index) constant)))
      in (temp, a0, rotateL b0 30, c0, d0)

roundConstants :: Int -> ((Word32 -> Word32 -> Word32 -> Word32), Word32)
roundConstants index
  | index < 20 = (\b c d -> (b .&. c) .|. (complement b .&. d), 0x5a827999)
  | index < 40 = (\b c d -> b `xor` c `xor` d, 0x6ed9eba1)
  | index < 60 = (\b c d -> (b .&. c) .|. (b .&. d) .|. (c .&. d), 0x8f1bbcdc)
  | otherwise = (\b c d -> b `xor` c `xor` d, 0xca62c1d6)

chunk64Words :: ByteString.ByteString -> [ByteString.ByteString]
chunk64Words bytes
  | ByteString.null bytes = []
  | otherwise = ByteString.take 4 bytes : chunk64Words (ByteString.drop 4 bytes)

bytesWord32 :: ByteString.ByteString -> Word32
bytesWord32 bytes = foldl (\value byte -> shiftL value 8 .|. fromIntegral byte) 0 (ByteString.unpack bytes)

add32 :: Word32 -> Word32 -> Word32
add32 = (+)

wordBytes :: Word32 -> [Word8]
wordBytes word = [ fromIntegral (word `shiftR` 24)
                 , fromIntegral (word `shiftR` 16)
                 , fromIntegral (word `shiftR` 8)
                 , fromIntegral word
                 ]

word64Bytes :: Integer -> ByteString.ByteString
word64Bytes value = ByteString.pack
  [ fromIntegral (value `shiftR` 56), fromIntegral (value `shiftR` 48)
  , fromIntegral (value `shiftR` 40), fromIntegral (value `shiftR` 32)
  , fromIntegral (value `shiftR` 24), fromIntegral (value `shiftR` 16)
  , fromIntegral (value `shiftR` 8), fromIntegral value
  ]
