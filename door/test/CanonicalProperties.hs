{-# LANGUAGE OverloadedStrings #-}

-- | Properties of RFC 8785 canonicalisation ("Archive.Door.Canonical").
--
-- A revision id is the digest of a record's canonical form, so two spellings
-- of one record must canonicalise to the same bytes, and canonicalising an
-- already-canonical record must change nothing, or rehashing a stored record
-- would not reproduce its own revision id. Generated JSON goes in a claim's
-- payload, the one place a draft carries arbitrary values.
module CanonicalProperties (canonicalProperties) where

import Archive.Door.Canonical (canonicalizeDraft)
import Archive.Door.Json (decodeUnique)
import qualified Data.Aeson as Aeson
import qualified Data.Aeson.Key as Key
import qualified Data.Aeson.KeyMap as KeyMap
import Data.Aeson ((.=))
import Data.Aeson.RFC8785 (encodeCanonical)
import qualified Data.ByteString.Lazy.Char8 as LazyByteString
import Data.Text (Text)
import qualified Data.Text as Text
import qualified Data.Vector as Vector
import GHC.Float (castWord64ToDouble)
import Rfc8785Vectors (rfc8785Numbers)
import Test.Tasty (TestTree, testGroup)
import Test.Tasty.QuickCheck
  ( Gen
  , Property
  , arbitrary
  , choose
  , counterexample
  , elements
  , forAll
  , listOf
  , oneof
  , resize
  , shuffle
  , sized
  , suchThat
  , testProperty
  , vectorOf
  , (===)
  )

canonicalProperties :: TestTree
canonicalProperties = testGroup "canonicalProperties"
  [ testProperty "canonicalising a canonical record changes nothing" idempotent
  , testProperty "the canonical bytes do not depend on key order" keyOrderIndependent
  , testProperty "the Appendix B numbers keep their spelling in any payload" vectorNumbers
  , testProperty "a canonical number reads back as the same binary64" numberRoundTrip
  ]

-- | A claim draft as the fixed checks write it, with a generated payload.
claimDraft :: Aeson.Value -> Aeson.Value
claimDraft payload = Aeson.object
  [ "spec" .= ("archive/0.0.11" :: Text)
  , "object_id" .= ("urn:uuid:00000000-0000-4000-8000-000000000003" :: Text)
  , "shape" .= ("claim" :: Text)
  , "by" .= ("pkg:hackage/archive@0.0.1.0" :: Text)
  , "agent" .= ("urn:uuid:00000000-0000-4000-8000-000000000001" :: Text)
  , "payload" .= payload
  , "refs" .= [Aeson.object
      [ "role" .= ("about" :: Text)
      , "id" .= ("urn:uuid:00000000-0000-4000-8000-000000000002" :: Text) ]]
  , "supersedes" .= ([] :: [Aeson.Value])
  , "contracts" .= [Aeson.object
      [ "id" .= ("archive.test/example" :: Text)
      , "version" .= ("1" :: Text)
      , "digest" .= ("sha256:" <> Text.replicate 64 "0") ]]
  ]

genText :: Gen Text
genText = Text.pack <$> resize 6 (listOf (elements "az09 _\"\\/\n\t\x7f\xe9\x2028\x1f600"))

genNumber :: Gen Double
genNumber = oneof
  [ fromIntegral <$> choose (-100000, 100000 :: Int)
  , arbitrary `suchThat` (\value -> not (isNaN value || isInfinite value))
  , castWord64ToDouble . fst <$> elements rfc8785Numbers
  ]

-- | JSON values a few levels deep; objects get distinct keys by construction
-- of 'KeyMap'.
genValue :: Gen Aeson.Value
genValue = sized (go . min 3)
  where
    go :: Int -> Gen Aeson.Value
    go depth = oneof (leaves <> if depth > 0 then branches (depth - 1) else [])
    leaves =
      [ pure Aeson.Null
      , Aeson.Bool <$> arbitrary
      , Aeson.toJSON <$> genNumber
      , Aeson.String <$> genText
      ]
    branches depth =
      [ Aeson.Array . Vector.fromList <$> (choose (0, 3) >>= (`vectorOf` go depth))
      , Aeson.Object . KeyMap.fromList <$> (choose (0, 4) >>= (`vectorOf` field depth))
      ]
    field depth = (,) . Key.fromText <$> genText <*> go depth

genPayload :: Gen Aeson.Value
genPayload = Aeson.Object . KeyMap.fromList <$> (choose (0, 4) >>= (`vectorOf` field))
  where
    field = (,) . Key.fromText <$> genText <*> genValue

idempotent :: Property
idempotent = forAll genPayload $ \payload ->
  case canonicalizeDraft (claimDraft payload) of
    Left problem -> counterexample (Text.unpack problem) False
    Right (revision, normalized) ->
      canonicalizeDraft (withoutRevision normalized) === Right (revision, normalized)
  where
    -- A draft may not carry its revision id, so the canonical record goes
    -- back in as the draft it would have been.
    withoutRevision (Aeson.Object fields) = Aeson.Object (KeyMap.delete "revision_id" fields)
    withoutRevision other = other

-- | The same value written twice with every object's keys in a fresh order:
-- after decoding, both canonicalise to the same revision and bytes.
keyOrderIndependent :: Property
keyOrderIndependent = forAll genPayload $ \payload ->
  forAll (renderShuffled (claimDraft payload)) $ \first ->
  forAll (renderShuffled (claimDraft payload)) $ \second ->
    let canonical bytes = do
          value <- either (Left . Text.pack) Right (decodeUnique bytes)
          (revision, normalized) <- canonicalizeDraft value
          pure (revision, encodeCanonical normalized)
    in counterexample (LazyByteString.unpack first <> "\n" <> LazyByteString.unpack second)
         (canonical first === canonical second)

-- | JSON text with each object's members in a random order.
renderShuffled :: Aeson.Value -> Gen LazyByteString.ByteString
renderShuffled value = case value of
  Aeson.Object fields -> do
    members <- shuffle (KeyMap.toList fields)
    rendered <- mapM (\(key, member) -> do
        inner <- renderShuffled member
        pure (Aeson.encode (Key.toText key) <> ":" <> inner)) members
    pure ("{" <> LazyByteString.intercalate "," rendered <> "}")
  Aeson.Array values -> do
    rendered <- mapM renderShuffled (Vector.toList values)
    pure ("[" <> LazyByteString.intercalate "," rendered <> "]")
  _ -> pure (Aeson.encode value)

-- | Each RFC 8785 Appendix B number, beside generated payload members, keeps
-- the spelling the fixed check pins.
vectorNumbers :: Property
vectorNumbers = forAll (elements rfc8785Numbers) $ \(bits, expected) ->
  forAll genPayload $ \payload ->
    let number = Aeson.toJSON (castWord64ToDouble bits)
        withNumber = case payload of
          Aeson.Object fields -> Aeson.Object (KeyMap.insert "jcs_number" number fields)
          other -> other
    in fmap encodeCanonical (canonicalNumber (claimDraft withNumber))
         === Right (LazyByteString.pack expected)

canonicalNumber :: Aeson.Value -> Either Text Aeson.Value
canonicalNumber draft = do
  (_, normalized) <- canonicalizeDraft draft
  case normalized of
    Aeson.Object fields
      | Just (Aeson.Object payload) <- KeyMap.lookup "payload" fields
      , Just number <- KeyMap.lookup "jcs_number" payload -> Right number
    _ -> Left "normalized record lost its jcs_number"

-- | The shortest spelling JCS chooses still names the value it came from.
numberRoundTrip :: Property
numberRoundTrip = forAll genNumber $ \value ->
  let draft = claimDraft (Aeson.object ["jcs_number" .= value])
      spelled = fmap (LazyByteString.unpack . encodeCanonical) (canonicalNumber draft)
  in counterexample (show spelled) $
       fmap (readDouble . filter (/= '+')) spelled === Right (if value == 0 then 0 else value)
  where
    readDouble :: String -> Double
    readDouble = read
