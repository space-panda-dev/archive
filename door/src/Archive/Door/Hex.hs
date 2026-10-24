-- | Lowercase base16, the one spelling the archive uses for digests and UUID
-- bytes.
--
-- Every digest Door writes or compares (revision ids, contract digests, Git
-- object names, checkpoint digests) is lowercase hexadecimal, so one total
-- encoder serves them all; a nibble is masked rather than looked up so no
-- byte value can index out of range.
module Archive.Door.Hex
  ( byteHex
  , hexText
  ) where

import Data.Bits (shiftR, (.&.))
import qualified Data.ByteString as ByteString
import Data.Char (chr, ord)
import Data.Text (Text)
import qualified Data.Text as Text
import Data.Word (Word8)

-- | Bytes as lowercase hexadecimal, two characters per byte.
hexText :: ByteString.ByteString -> Text
hexText = Text.pack . concatMap byteHex . ByteString.unpack

-- | One byte as its two lowercase hexadecimal characters, high nibble first.
byteHex :: Word8 -> String
byteHex byte = [nibble (byte `shiftR` 4), nibble (byte .&. 0x0f)]

nibble :: Word8 -> Char
nibble value
  | value < 10 = chr (ord '0' + fromIntegral value)
  | otherwise = chr (ord 'a' + fromIntegral value - 10)
