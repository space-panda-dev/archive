{-# LANGUAGE OverloadedStrings #-}

-- | Checks of the specification 0.0.10 bundles in Door's own evaluator
-- (D-18): every bundle loads; the JSON Schema each one states accepts and
-- refuses what the World module of its record does; the object keywords the
-- release adds evaluate and are refused when malformed; and every bundle that
-- declares a profile rule this Door does not enforce yet is refused for it,
-- while no earlier bundle is.
--
-- Each check is a (label, passed, detail) row; Main asserts them.
module Specification010 (specification010Checks) where

import Archive.Door.Catalog (loadContractCatalog)
import Archive.Door.Contracts (validateContractBundle, validateContractInstance)
import Archive.Door.Json (decodeUnique)
import Archive.Door.PendingRules (refusePendingRules)
import Data.Aeson (Value (..), (.=))
import qualified Data.Aeson as Aeson
import qualified Data.Aeson.KeyMap as KeyMap
import qualified Data.ByteString.Lazy.Char8 as LazyByteString
import Data.Either (isLeft, isRight)
import Data.Foldable (toList)
import qualified Data.Set as Set
import Data.Text (Text)
import qualified Data.Text as Text

type Check = (String, Bool, String)

specification010Checks :: IO [Check]
specification010Checks = do
  current <- either (const []) toList <$> loadContractCatalog "archive/0.0.10"
  previous <- either (const []) toList <$> loadContractCatalog "archive/0.0.9"
  values <- mapM (\entry@(_, _, digest) -> do
    bytes <- LazyByteString.readFile (bundlePath digest)
    pure (entry, either (const Null) id (decodeUnique bytes))) current
  let previousSet = Set.fromList previous
      added = [entry | (entry, _) <- values, not (Set.member entry previousSet)]
      loads = [(entry, validateContractBundle identifier version digest value)
              | (entry@(identifier, version, digest), value) <- values]
      bundleValue name version = case [value | ((identifier, version', _), value) <- values
                                              , identifier == name, version' == version] of
        [value] -> value
        _ -> Null
      schema name version = case bundleValue name version of
        Object fields | Just value <- KeyMap.lookup "schema" fields -> value
        _ -> Null
      accepts label name version instance' =
        (label, isRight (validateContractInstance (schema name version) instance'),
         show (validateContractInstance (schema name version) instance'))
      refuses label name version instance' =
        (label, isLeft (validateContractInstance (schema name version) instance'),
         show (validateContractInstance (schema name version) instance'))
      -- The sensitivity bundle with its agent rule moved into the payload schema.
      buried = case bundleValue "archive.claim/sensitivity" "1" of
        Object bundle | Just (Object root) <- KeyMap.lookup "schema" bundle
                      , Just rule <- KeyMap.lookup "x-archive-agent" root
                      , Just (Object properties) <- KeyMap.lookup "properties" root
                      , Just (Object payload) <- KeyMap.lookup "payload" properties ->
          Object (KeyMap.insert "schema" (Object (KeyMap.insert "properties"
            (Object (KeyMap.insert "payload" (Object (KeyMap.insert "x-archive-agent" rule payload)) properties))
            (KeyMap.delete "x-archive-agent" root))) bundle)
        _ -> Null
      malformedNames = case bundleValue "archive.claim/export" "1" of
        Object bundle -> Object (KeyMap.insert "schema" (replaceOptions (KeyMap.lookup "schema" bundle)) bundle)
        _ -> Null
  pure $
    [ ("the 0.0.10 catalog adds twenty-four bundles to 0.0.9's and keeps every 0.0.9 entry",
        length added == 24 && all (`elem` current) previous, show added)
    , ("every 0.0.10 bundle loads by its digest",
        not (null loads) && all (isRight . snd) loads, show [entry | (entry, Left _) <- loads])
    , ("every added bundle but three declares a profile rule this Door does not enforce yet, and is refused for it",
        [ (identifier, version) | (identifier, version, _) <- added
        , isRight (refusePendingRules identifier version (schema identifier version)) ]
          == [ ("archive.claim/catalogue_correspondence", "1"), ("archive.claim/form_spans", "1")
             , ("archive.claim/rights", "1") ],
        show [ (identifier, version) | (identifier, version, _) <- added
             , isRight (refusePendingRules identifier version (schema identifier version)) ])
    , ("no 0.0.9 bundle declares a pending rule (anchor)",
        not (null previous)
          && all (\(identifier, version, _) -> isRight (refusePendingRules identifier version (schema identifier version))) previous,
        "")
    , ("a pending rule below the schema root is refused when the bundle loads",
        buried /= Null && isLeft (validateContractBundle "archive.claim/sensitivity" "1" zeroDigest buried),
        show (validateContractBundle "archive.claim/sensitivity" "1" zeroDigest buried))
    , ("a propertyNames that is not a schema is refused when the bundle loads",
        malformedNames /= Null && isLeft (validateContractBundle "archive.claim/export" "1" zeroDigest malformedNames),
        show (validateContractBundle "archive.claim/export" "1" zeroDigest malformedNames))
    ]
    <> exportChecks accepts refuses
    <> chordChecks accepts refuses
    <> meterChecks accepts refuses
    <> keyChecks accepts refuses
    <> catalogueChecks accepts refuses

zeroDigest :: Text
zeroDigest = "sha256:" <> Text.replicate 64 "0"

bundlePath :: Text -> FilePath
bundlePath digest = "spec/contracts/sha256-" <> drop 7 (Text.unpack digest) <> ".json"

-- | The export schema with its options' propertyNames made a string.
replaceOptions :: Maybe Value -> Value
replaceOptions (Just (Object root))
  | Just (Object properties) <- KeyMap.lookup "properties" root
  , Just (Object payload) <- KeyMap.lookup "payload" properties
  , Just (Object fields) <- KeyMap.lookup "properties" payload
  , Just (Object options) <- KeyMap.lookup "options" fields =
      let options' = KeyMap.insert "propertyNames" (String "lowercase") options
          payload' = KeyMap.insert "properties" (Object (KeyMap.insert "options" (Object options') fields)) payload
      in Object (KeyMap.insert "properties" (Object (KeyMap.insert "payload" (Object payload') properties)) root)
replaceOptions _ = Null

ref :: Text -> Text -> Value
ref role suffix = Aeson.object
  [ "role" .= role
  , "id" .= ("urn:uuid:00000000-0000-4000-8000-0000000000" <> suffix)
  , "revision_id" .= ("sha256:" <> Text.replicate 64 "0" :: Text) ]

instanceOf :: Value -> [Value] -> Value
instanceOf payload refs = Aeson.object ["payload" .= payload, "refs" .= refs]

decoded :: String -> Value
decoded text = either (const Null) id (decodeUnique (LazyByteString.pack text))

type Judge = String -> Text -> Text -> Value -> Check

exportChecks :: Judge -> Judge -> [Check]
exportChecks accepts refuses =
  [ accepts "an export with named scalar options satisfies its bundle" "archive.claim/export" "1" (export options)
  , refuses "an export with an option name that is not a lowercase name is refused" "archive.claim/export" "1"
      (export "{\"Paper\":\"a4\"}")
  , refuses "an export with more than 32 options is refused" "archive.claim/export" "1"
      (export ("{" <> concatMap (\n -> "\"o" <> show n <> "\":true,") [1 .. 32 :: Int] <> "\"o33\":true}"))
  , refuses "an export with a fractional option is refused" "archive.claim/export" "1" (export "{\"scale\":1.5}")
  , refuses "an export whose media type is not its format's is refused" "archive.claim/export" "1"
      (instanceOf (decoded (exportPayload "pdf" "application/json" options)) exportRefs)
  ]
  where
    options = "{\"paper\":\"a4\",\"transpose\":-2,\"parts\":true}"
    export opts = instanceOf (decoded (exportPayload "pdf" "application/pdf" opts)) exportRefs
    exportRefs = [ref "artifact" "01", ref "rendered" "02"]
    exportPayload format media opts = "{\"format\":\"" <> format <> "\",\"media_type\":\"" <> media
      <> "\",\"exporter\":{\"id\":\"lead-sheet\",\"version\":\"1\"},\"key\":\"sha256:" <> replicate 64 'a'
      <> "\",\"options\":" <> opts <> "}"

chordChecks :: Judge -> Judge -> [Check]
chordChecks accepts refuses =
  [ accepts "a chord stacks version 2 span may hold a bass-only reading beside a stack" "archive.claim/chord_stacks" "2"
      (stacks "[{\"bass\":4,\"tones\":[3,7]},{\"bass\":4,\"root\":0,\"text\":\"/E\"}]" "")
  , refuses "a bass-only reading takes no voicing" "archive.claim/chord_stacks" "2"
      (stacks "[{\"bass\":4,\"voicing\":[40]}]" "")
  , refuses "a stack's voicing is still checked beside a bass-only reading (anchor)" "archive.claim/chord_stacks" "2"
      (stacks "[{\"bass\":4,\"tones\":[3,7],\"voicing\":[41,52,55]},{\"bass\":4}]" "")
  , accepts "a chord stacks version 2 claim may cite a bank stem" "archive.claim/chord_stacks" "2"
      (stacks "[{\"bass\":4}]" stem)
  , refuses "a bank stem beside an analysed Artifact is refused" "archive.claim/chord_stacks" "2"
      (instanceOf (decoded (stacksPayload "[{\"bass\":4}]" stem)) (stacksRefs <> [ref "analysed" "09"]))
  , refuses "a stem output that is not a bank output name is refused" "archive.claim/chord_stacks" "2"
      (stacks "[{\"bass\":4}]" (",\"stem\":{\"extractor\":\"stems\",\"key\":\"sha256:" <> replicate 64 'b'
        <> "\",\"output\":\"Bass\",\"sha256\":\"sha256:" <> replicate 64 'c' <> "\"}"))
  , refuses "a chord stacks version 1 span refuses a bass-only reading (anchor)" "archive.claim/chord_stacks" "1"
      (stacks "[{\"bass\":4}]" "")
  ]
  where
    stem = ",\"stem\":{\"extractor\":\"stems\",\"key\":\"sha256:" <> replicate 64 'b'
      <> "\",\"output\":\"bass\",\"sha256\":\"sha256:" <> replicate 64 'c' <> "\"}"
    stacks readings extra = instanceOf (decoded (stacksPayload readings extra)) stacksRefs
    stacksRefs = [ref "source" "01", ref "stream" "02"]
    stacksPayload readings extra = "{\"sample_rate\":48000,\"spans\":[{\"start_sample\":0,\"end_sample\":10,\"readings\":"
      <> readings <> "}]" <> extra <> "}"

meterChecks :: Judge -> Judge -> [Check]
meterChecks accepts refuses =
  [ accepts "an untimed meter layers version 2 claim holds written bars about a Work" "archive.claim/meter_layers" "2"
      (instanceOf (decoded untimed) [ref "work" "01"])
  , refuses "an untimed claim needs a Work or a document" "archive.claim/meter_layers" "2"
      (instanceOf (decoded untimed) [ref "run" "01"])
  , refuses "an untimed bar's section needs a pinned form" "archive.claim/meter_layers" "2"
      (instanceOf (decoded "{\"layers\":[{\"bars\":[{\"numerator\":4,\"denominator\":4,\"section\":0}]}]}") [ref "work" "01"])
  , accepts "a timed meter layers version 2 claim is version 1's" "archive.claim/meter_layers" "2"
      (instanceOf (decoded (timed "")) [ref "source" "01", ref "stream" "02"])
  , refuses "a placed bar needs a pinned meter claim" "archive.claim/meter_layers" "2"
      (instanceOf (decoded (timed ",\"placed\":{\"layer\":0,\"bar\":0}")) [ref "source" "01", ref "stream" "02"])
  , accepts "a placed bar with its meter claim and alignment is accepted" "archive.claim/meter_layers" "2"
      (instanceOf (decoded (Text.unpack (Text.replace "\"layers\"" "\"alignment\":{\"method\":\"manual\"},\"layers\""
        (Text.pack (timed ",\"placed\":{\"layer\":0,\"bar\":0}")))))
        [ref "source" "01", ref "stream" "02", ref "meter" "03"])
  ]
  where
    untimed = "{\"layers\":[{\"role\":\"written\",\"bars\":[{\"numerator\":4,\"denominator\":4},"
      <> "{\"numerator\":3,\"denominator\":4,\"grouping\":[2,1]}]}]}"
    timed extra = "{\"sample_rate\":48000,\"layers\":[{\"bars\":[{\"start_sample\":0,\"end_sample\":10,"
      <> "\"numerator\":4,\"denominator\":4" <> extra <> "}]}]}"

keyChecks :: Judge -> Judge -> [Check]
keyChecks accepts refuses =
  [ accepts "a tuning a quarter-tone flat is -50 cents" "archive.claim/tuning" "1" (tuning "-50")
  , accepts "a tuning just under 50 cents is accepted" "archive.claim/tuning" "1" (tuning "49.99")
  , refuses "a tuning of 50 cents is refused" "archive.claim/tuning" "1" (tuning "50")
  , refuses "a tuning below -50 cents is refused" "archive.claim/tuning" "1" (tuning "-50.5")
  , accepts "a dorian key names its collection on the tonic" "archive.claim/key_sequence" "1"
      (key "{\"collection\":[0,2,3,5,7,9,10],\"tonic\":0,\"mode\":\"dorian\"}")
  , refuses "a dorian key with another collection is refused" "archive.claim/key_sequence" "1"
      (key "{\"collection\":[0,2,4,5,7,9,11],\"tonic\":0,\"mode\":\"dorian\"}")
  , accepts "minor is any of the three minor collections" "archive.claim/key_sequence" "1"
      (key "{\"collection\":[0,2,3,5,7,8,11],\"tonic\":0,\"mode\":\"minor\"}")
  , refuses "a tonic outside its collection is refused" "archive.claim/key_sequence" "1"
      (key "{\"collection\":[0,2,4],\"tonic\":1}")
  , refuses "a mode without a tonic is refused" "archive.claim/key_sequence" "1"
      (key "{\"collection\":[0,2,4],\"mode\":\"blues\"}")
  , accepts "an unchecked mode name is the asserter's" "archive.claim/key_sequence" "1"
      (key "{\"collection\":[0,3,5,6,7,10],\"tonic\":0,\"mode\":\"blues\"}")
  ]
  where
    tuning cents = instanceOf (decoded ("{\"sample_rate\":48000,\"reference_cents\":\"" <> cents <> "\"}"))
      [ref "source" "01", ref "stream" "02"]
    key reading = instanceOf (decoded ("{\"regions\":[{\"readings\":[" <> reading <> "]}]}")) [ref "work" "01"]

catalogueChecks :: Judge -> Judge -> [Check]
catalogueChecks accepts refuses =
  [ accepts "a release maps to a MusicBrainz release group as narrower than it" "archive.claim/catalogue_correspondence" "1"
      (mapping "release" ("https://musicbrainz.org/release-group/" <> mbid) "skos:broadMatch")
  , refuses "a release is never the exact match of a release group" "archive.claim/catalogue_correspondence" "1"
      (mapping "release" ("https://musicbrainz.org/release-group/" <> mbid) "skos:exactMatch")
  , refuses "a recording does not map to a release group" "archive.claim/catalogue_correspondence" "1"
      (mapping "recording" ("https://musicbrainz.org/release-group/" <> mbid) "skos:broadMatch")
  , accepts "an artist maps to a Discogs artist" "archive.claim/catalogue_correspondence" "1"
      (mapping "artist" "https://www.discogs.com/artist/123" "skos:exactMatch")
  , refuses "a correspondence pins exactly one subject" "archive.claim/catalogue_correspondence" "1"
      (instanceOf (decoded (payload "https://www.discogs.com/artist/123" "skos:exactMatch"))
        [ref "artist" "01", ref "release" "02"])
  ]
  where
    mbid = "0f0e0d0c-0b0a-4908-8706-050403020100"
    payload external relation = "{\"external\":\"" <> external <> "\",\"mapping\":\"" <> relation <> "\"}"
    mapping role external relation = instanceOf (decoded (payload external relation)) [ref (Text.pack role) "01"]
