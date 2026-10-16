{-# LANGUAGE OverloadedStrings #-}

module Archive.Door.Json
  ( decodeUnique
  ) where

import Data.Aeson (Value)
import Data.Aeson.Decoding (toEitherValue)
import Data.Aeson.Decoding.ByteString.Lazy (lbsToTokens)
import Data.Aeson.Decoding.Tokens
  ( TkArray (TkArrayEnd, TkArrayErr, TkItem)
  , TkRecord (TkPair, TkRecordEnd, TkRecordErr)
  , Tokens (TkArrayOpen, TkErr, TkLit, TkNumber, TkRecordOpen, TkText)
  )
import qualified Data.Aeson.Key as Key
import qualified Data.ByteString.Lazy as LazyByteString
import qualified Data.Set as Set
import Data.Word (Word8)

decodeUnique :: LazyByteString.ByteString -> Either String Value
decodeUnique input = do
  let tokens = lbsToTokens input
  rest <- inspectTokens tokens
  if LazyByteString.all isJsonSpace rest
    then fst <$> toEitherValue tokens
    else Left "trailing bytes after JSON value"

inspectTokens :: Tokens continuation String -> Either String continuation
inspectTokens (TkLit _ continuation) = Right continuation
inspectTokens (TkText _ continuation) = Right continuation
inspectTokens (TkNumber _ continuation) = Right continuation
inspectTokens (TkArrayOpen items) = inspectArray items
inspectTokens (TkRecordOpen pairs) = inspectRecord Set.empty pairs
inspectTokens (TkErr problem) = Left problem

inspectArray :: TkArray continuation String -> Either String continuation
inspectArray (TkItem tokens) = inspectTokens tokens >>= inspectArray
inspectArray (TkArrayEnd continuation) = Right continuation
inspectArray (TkArrayErr problem) = Left problem

inspectRecord :: Set.Set Key.Key -> TkRecord continuation String -> Either String continuation
inspectRecord seen (TkPair key tokens)
  | Set.member key seen = Left ("duplicate JSON object key: " <> Key.toString key)
  | otherwise = inspectTokens tokens >>= inspectRecord (Set.insert key seen)
inspectRecord _ (TkRecordEnd continuation) = Right continuation
inspectRecord _ (TkRecordErr problem) = Left problem

isJsonSpace :: Word8 -> Bool
isJsonSpace byte = byte `elem` [0x20, 0x09, 0x0a, 0x0d]
