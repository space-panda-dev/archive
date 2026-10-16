{-# LANGUAGE OverloadedStrings #-}

module Archive.Door.Canonical
  ( canonicalizeDraft
  , canonicalizeDraftStrict
  , finalizeStored
  ) where

import qualified Crypto.Hash.SHA256 as SHA256
import Archive.Door.Types (parseStoredEnvelope, validateDraftEnvelope)
import Data.Aeson (Value (Array, Bool, Null, Number, Object, String))
import qualified Data.Aeson.KeyMap as KeyMap
import Data.Aeson.RFC8785 (encodeCanonical)
import qualified Data.ByteString as ByteString
import Data.Char (intToDigit)
import Data.Ratio (denominator, numerator, (%))
import Data.Scientific (Scientific, scientific, toRealFloat)
import Data.Text (Text)
import qualified Data.Text as Text
import Data.Word (Word8)
import GHC.Float (castDoubleToWord64, castWord64ToDouble)

canonicalizeDraft :: Value -> Either Text (Text, Value)
canonicalizeDraft (Object submitted) =
  canonicalizeWith validateDraftEnvelope (Object submitted)
canonicalizeDraft _ = Left "record must be a JSON object"

canonicalizeDraftStrict :: Value -> Either Text (Text, Value)
canonicalizeDraftStrict (Object submitted) =
  canonicalizeWith validateDraftEnvelope (Object submitted)
canonicalizeDraftStrict _ = Left "record must be a JSON object"

-- | Add Door's recording time after hashing.  The input is a strict draft;
-- the returned value is a fully validated stored envelope.
finalizeStored :: Text -> Value -> Either Text (Text, Value)
finalizeStored at value = do
  validateDraftEnvelope value
  (revision, withoutAt) <- canonicalizeDraftStrict value
  stored <- case withoutAt of
    Object object -> pure (Object (KeyMap.insert "at" (String at) object))
    _ -> Left "record must be a JSON object"
  _ <- parseStoredEnvelope stored
  pure (revision, stored)

canonicalizeWith :: (Value -> Either Text ()) -> Value -> Either Text (Text, Value)
canonicalizeWith validate (Object submitted) = do
  validate (Object submitted)
  normalized <- normalizeIJson (Object submitted)
  normalizedObject <- case normalized of
    Object object -> pure object
    _ -> Left "record must be a JSON object"
  let identityInput = Object (foldl' (flip KeyMap.delete) normalizedObject ["revision_id", "at"])
      revision = "sha256:" <> hex (SHA256.hashlazy (encodeCanonical identityInput))
  case KeyMap.lookup "revision_id" normalizedObject of
    Just (String supplied) | supplied /= revision -> Left "revision_id does not match the submitted record"
    Just (String _) -> pure ()
    Just _ -> Left "revision_id must be a string"
    Nothing -> pure ()
  let record = Object (KeyMap.insert "revision_id" (String revision)
                     (KeyMap.delete "at" normalizedObject))
  pure (revision, record)
canonicalizeWith _ _ = Left "record must be a JSON object"

hex :: ByteString.ByteString -> Text
hex = Text.pack . concatMap byteHex . ByteString.unpack

byteHex :: Word8 -> String
byteHex byte = [intToDigit (fromIntegral byte `div` 16), intToDigit (fromIntegral byte `mod` 16)]

normalizeIJson :: Value -> Either Text Value
normalizeIJson Null = Right Null
normalizeIJson value@(Bool _) = Right value
normalizeIJson value@(String _) = Right value
normalizeIJson (Array values) = Array <$> traverse normalizeIJson values
normalizeIJson (Object object) = Object <$> traverse normalizeIJson object
normalizeIJson (Number number) =
  let value = toRealFloat number :: Double
  in if isInfinite value || isNaN value
       then Left "number is outside the finite IEEE 754 binary64 range required by RFC 8785"
       else Number <$> binary64Scientific value

-- | Return the shortest decimal that rounds to this binary64 value under
-- round-to-nearest, ties-to-even.  Scientific.fromFloatDigits and aeson's
-- ToJSON Double intentionally preserve GHC's Show spelling in boundary
-- cases, which is not the ECMAScript Number::toString result required by JCS.
binary64Scientific :: Double -> Either Text Scientific
binary64Scientific value = case shortestDecimal value of
  Nothing -> Left "could not find a shortest decimal for an IEEE 754 binary64 number"
  Just (coefficient, decimalPower) ->
    let signedCoefficient = if value < 0 then negate coefficient else coefficient
    in Right (scientific signedCoefficient decimalPower)

-- The decimal search uses exact rationals.  A decimal candidate round-trips
-- precisely when it falls inside the binary64 value's rounding interval.
-- The interval includes its endpoints only when the value's significand is
-- even.  Seventeen significant decimal digits suffice for every binary64.
shortestDecimal :: Double -> Maybe (Integer, Int)
shortestDecimal value
  | value == 0 = Just (0, 0)
  | otherwise = choose 1
  where
    magnitude = abs value
    exact = toRational magnitude
    bits = castDoubleToWord64 magnitude
    previous = toRational (castWord64ToDouble (bits - 1))
    lower = (previous + exact) / 2
    upper
      | bits == 0x7fefffffffffffff = exact + (exact - previous) / 2
      | otherwise = (exact + toRational (castWord64ToDouble (bits + 1))) / 2
    inclusive = even bits
    decimalExponent = floorLog10 exact

    choose precision
      | precision > 17 = Nothing
      | otherwise =
          let decimalPower = decimalExponent - precision + 1
              scale = powerOfTen decimalPower
              nearest = roundTiesToEven (exact / scale)
              least =
                if inclusive
                  then ceiling (lower / scale)
                  else floor (lower / scale) + 1
              greatest =
                if inclusive
                  then floor (upper / scale)
                  else ceiling (upper / scale) - 1
          in if least > greatest
               then choose (precision + 1)
               else
                 let coefficient = max least (min nearest greatest)
                 in Just (removeTrailingZeros coefficient decimalPower)

removeTrailingZeros :: Integer -> Int -> (Integer, Int)
removeTrailingZeros coefficient decimalPower
  | coefficient `mod` 10 == 0 =
      removeTrailingZeros (coefficient `quot` 10) (decimalPower + 1)
  | otherwise = (coefficient, decimalPower)

powerOfTen :: Int -> Rational
powerOfTen decimalPower
  | decimalPower >= 0 = fromInteger (10 ^ decimalPower)
  | otherwise = 1 % (10 ^ negate decimalPower)

floorLog10 :: Rational -> Int
floorLog10 value =
  let estimate = length (show (numerator value)) - length (show (denominator value))
      belowEstimate =
        if estimate >= 0
          then numerator value < denominator value * 10 ^ estimate
          else numerator value * 10 ^ negate estimate < denominator value
  in if belowEstimate then estimate - 1 else estimate

roundTiesToEven :: Rational -> Integer
roundTiesToEven value =
  let (whole, remainder) = numerator value `quotRem` denominator value
  in case compare (2 * remainder) (denominator value) of
       LT -> whole
       GT -> whole + 1
       EQ -> if even whole then whole else whole + 1
