{-# LANGUAGE OverloadedStrings #-}

-- | Properties of the base16 helper ("Archive.Door.Hex").
--
-- Door has no hex decoder of its own: digests are compared as text. So the
-- round trip goes through a decoder written here, and through the digest
-- parser that reads back what 'hexText' wrote into a revision id; a wrong
-- nibble or a capital letter would fork every digest Door writes.
module HexProperties (hexProperties) where

import Archive.Door.Hex (byteHex, hexText)
import Archive.Door.Layout (digestText, parseSha256Digest)
import qualified Data.ByteString as ByteString
import Data.Char (digitToInt, isDigit)
import qualified Data.Text as Text
import Data.Word (Word8)
import Numeric (showHex)
import Test.Tasty (TestTree, testGroup)
import Test.Tasty.QuickCheck
  ( Property
  , arbitrary
  , counterexample
  , forAll
  , listOf
  , testProperty
  , vectorOf
  , (===)
  )

hexProperties :: TestTree
hexProperties = testGroup "hexProperties"
  [ testProperty "decoding hexText gives back the bytes" $
      forAll (ByteString.pack <$> listOf arbitrary) $ \bytes ->
        decodeHex (Text.unpack (hexText bytes)) === Just bytes
  , testProperty "byteHex is two lowercase digits, high nibble first" byteSpelling
  , testProperty "a 32-byte hexText is a digest the layout parser accepts" $
      forAll (ByteString.pack <$> vectorOf 32 arbitrary) $ \bytes ->
        let spelled = "sha256:" <> hexText bytes
        in fmap digestText (parseSha256Digest spelled) === Right spelled
  ]

byteSpelling :: Property
byteSpelling = forAll arbitrary $ \byte ->
  let expected = (if byte < 16 then ('0' :) else id) (showHex (byte :: Word8) "")
  in counterexample (show byte) (byteHex byte === expected)

-- | Lowercase only: an uppercase digit is not a spelling Door writes, so it
-- must not decode.
decodeHex :: String -> Maybe ByteString.ByteString
decodeHex = fmap ByteString.pack . go
  where
    go [] = Just []
    go (high : low : rest) = do
      byte <- (\h l -> fromIntegral (h * 16 + l)) <$> digit high <*> digit low
      (byte :) <$> go rest
    go [_] = Nothing
    digit character
      | isDigit character || ('a' <= character && character <= 'f') = Just (digitToInt character)
      | otherwise = Nothing
