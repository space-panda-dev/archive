{-# LANGUAGE OverloadedStrings #-}

-- | Checks of the specification 0.0.11 bundles in Door's own evaluator
-- (D-19, ADR 0113): the catalog adds exactly the six contract versions; every
-- bundle loads; every profile rule the release adds is declared by some
-- bundle and refused when malformed; and the JSON Schema and record rules of
-- each new version accept and refuse the shapes the ADR states: a sketch, an
-- underlay, an open repeat, a span's passes, and a lead sheet's underlay.
--
-- Each check is a (label, passed, detail) row; Main asserts them.
module Specification011 (specification011Checks) where

import Archive.Door.Catalog (loadContractCatalog)
import Archive.Door.Contracts (validateContractBundle, validateContractInstance)
import Archive.Door.Json (decodeUnique)
import Archive.Door.RuleSyntax (release011RuleNames, releaseRuleNames)
import qualified Data.Aeson.Key as Key
import Data.Aeson (Value (..), (.=))
import qualified Data.Aeson as Aeson
import qualified Data.Aeson.KeyMap as KeyMap
import qualified Data.ByteString.Lazy.Char8 as LazyByteString
import Data.Either (isLeft, isRight)
import Data.Foldable (toList)
import Data.List (sort)
import qualified Data.Set as Set
import Data.Text (Text)
import qualified Data.Text as Text

type Check = (String, Bool, String)

specification011Checks :: IO [Check]
specification011Checks = do
  current <- either (const []) toList <$> loadContractCatalog "archive/0.0.11"
  previous <- either (const []) toList <$> loadContractCatalog "archive/0.0.10"
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
      schemaOf (identifier, version, _) = schema identifier version
      rootOf value = case value of
        Object root -> root
        _ -> KeyMap.empty
      declared names value = [Key.toText key | key <- KeyMap.keys (rootOf value), Key.toText key `elem` names]
      loadWith name version change = case bundleValue name version of
        Object bundle | Just (Object root) <- KeyMap.lookup "schema" bundle ->
          validateContractBundle name version zeroDigest (Object (KeyMap.insert "schema" (Object (change root)) bundle))
        _ -> Left "the bundle is not in the catalog"
      malformedRules =
        [ (identifier <> " " <> name, loadWith identifier version (adjust spoil (Key.fromText name)))
        | entry@(identifier, version, _) <- added, name <- declared release011RuleNames (schemaOf entry) ]
  pure $
    [ ("the 0.0.11 catalog adds exactly ADR 0113's six contract versions to 0.0.10's and keeps every 0.0.10 entry",
        sort [(identifier, version) | (identifier, version, _) <- added]
          == [ ("archive.claim/form", "2"), ("archive.claim/form_spans", "2"), ("archive.claim/lead_sheet", "2")
             , ("archive.claim/melody", "2"), ("archive.claim/meter_layers", "3"), ("archive.claim/underlay", "1") ]
          && all (`elem` current) previous, show added)
    , ("every 0.0.11 bundle loads by its digest",
        not (null loads) && all (isRight . snd) loads, show [entry | (entry, Left _) <- loads])
    , ("every rule 0.0.11 adds is declared by an added bundle, and no 0.0.10 bundle declares one",
        Set.fromList release011RuleNames == Set.fromList (concatMap (declared release011RuleNames . schemaOf) added)
          && not (any (not . null . declared release011RuleNames . schemaOf) previous), show release011RuleNames)
    , ("0.0.11 adds no rule name 0.0.10 already has",
        not (any (`elem` releaseRuleNames) release011RuleNames), "")
    , ("every declared 0.0.11 rule with an unknown key added is refused when its bundle loads",
        not (null malformedRules) && all (\(_, result) -> isLeft result) malformedRules,
        show [name | (name, Right _) <- malformedRules])
    , ("a pass lists rule naming no path is refused when the bundle loads",
        isLeft (loadWith "archive.claim/underlay" "1" (KeyMap.insert "x-archive-pass-lists"
          (Aeson.object ["paths" .= ([] :: [Value])]))), "")
    , ("a placed items rule naming one field twice is refused when the bundle loads",
        isLeft (loadWith "archive.claim/underlay" "1" (KeyMap.insert "x-archive-placed-items"
          (Aeson.object ["lines" .= ("lines" :: Text), "words" .= ("lines" :: Text), "syllables" .= ("syllables" :: Text), "items" .= ("items" :: Text)]))), "")
    , ("the underlay bundle loads as released (anchor)",
        isRight (loadWith "archive.claim/underlay" "1" id), "")
    ]
    <> melodyChecks accepts refuses
    <> underlayChecks accepts refuses
    <> formChecks accepts refuses
    <> formSpansChecks accepts refuses
    <> leadSheetChecks accepts refuses

-- | A rule with an unknown key.
spoil :: Value -> Value
spoil (Object fields) = Object (KeyMap.insert "unknown" (String "x") fields)
spoil (Array items) = Array (fmap spoil items)
spoil value = value

adjust :: (Value -> Value) -> Key.Key -> KeyMap.KeyMap Value -> KeyMap.KeyMap Value
adjust change key fields = maybe fields (\value -> KeyMap.insert key (change value) fields) (KeyMap.lookup key fields)

zeroDigest :: Text
zeroDigest = "sha256:" <> Text.replicate 64 "0"

bundlePath :: Text -> FilePath
bundlePath digest = "spec/contracts/sha256-" <> drop 7 (Text.unpack digest) <> ".json"

ref :: Text -> Text -> Value
ref role suffix = Aeson.object
  [ "role" .= role
  , "id" .= ("urn:uuid:00000000-0000-4000-8000-0000000000" <> suffix)
  , "revision_id" .= ("sha256:" <> Text.replicate 62 "0" <> suffix :: Text) ]

instanceOf :: String -> [Value] -> Value
instanceOf payload refs = Aeson.object ["payload" .= decoded payload, "refs" .= refs]

decoded :: String -> Value
decoded text = either (const Null) id (decodeUnique (LazyByteString.pack text))

type Judge = String -> Text -> Text -> Value -> Check

melodyChecks :: Judge -> Judge -> [Check]
melodyChecks accepts refuses =
  [ accepts "a melody version 2 sketch leaves out its pitches' durations" "archive.claim/melody" "2"
      (instanceOf sketch [ref "work" "01"])
  , refuses "a sketch pins no meter" "archive.claim/melody" "2"
      (instanceOf sketch [ref "work" "01", ref "meter" "02"])
  , refuses "a sketch has no start" "archive.claim/melody" "2"
      (instanceOf (Text.unpack (Text.replace "{\"notes\"" "{\"start\":{\"layer\":0,\"bar\":0},\"notes\"" (Text.pack sketch)))
        [ref "work" "01"])
  , refuses "a rest always has a duration" "archive.claim/melody" "2"
      (instanceOf "{\"notes\":[{\"pitch\":64},{\"rest\":true}]}" [ref "work" "01"])
  , accepts "a melody version 2 with every duration known is laid on its meter" "archive.claim/melody" "2"
      (instanceOf measured [ref "work" "01", ref "meter" "02"])
  , accepts "an unmeasured pitch may carry articulation" "archive.claim/melody" "2"
      (instanceOf "{\"notes\":[{\"pitch\":64,\"articulation\":[\"fermata\"]}]}" [ref "work" "01"])
  , refuses "a melody version 1 refuses an unmeasured pitch (anchor)" "archive.claim/melody" "1"
      (instanceOf sketch [ref "work" "01"])
  ]
  where
    sketch = "{\"notes\":[{\"pitch\":64},{\"pitch\":67},{\"pitch\":69,\"duration\":{\"count\":1,\"unit\":2}},"
      <> "{\"rest\":true,\"duration\":{\"count\":1,\"unit\":4}},{\"pitch\":67}]}"
    measured = "{\"start\":{\"layer\":0,\"bar\":0},\"notes\":[{\"pitch\":64,\"duration\":{\"count\":1,\"unit\":4}}]}"

underlayChecks :: Judge -> Judge -> [Check]
underlayChecks accepts refuses =
  [ accepts "an underlay places syllables under melody items, under the passes of a pinned form" "archive.claim/underlay" "1"
      (instanceOf (verse passes2) withForm)
  , accepts "an underlay with no passes pins no form" "archive.claim/underlay" "1"
      (instanceOf (verse "") plain)
  , refuses "passes need a pinned form" "archive.claim/underlay" "1"
      (instanceOf (verse passes2) plain)
  , refuses "a pinned form needs passes" "archive.claim/underlay" "1"
      (instanceOf (verse "") withForm)
  , refuses "an underlay pins exactly one melody" "archive.claim/underlay" "1"
      (instanceOf (verse "") [ref "document" "02"])
  , refuses "an underlay pins exactly one document" "archive.claim/underlay" "1"
      (instanceOf (verse "") [ref "melody" "01"])
  , refuses "an underlay pins no work: its subject is its melody's" "archive.claim/underlay" "1"
      (instanceOf (verse "") (plain <> [ref "work" "04"]))
  , refuses "a word holds items or syllables, not both" "archive.claim/underlay" "1"
      (instanceOf (oneLine "{\"word\":1,\"items\":[0],\"syllables\":[{\"syllable\":1,\"start_char\":0,\"end_char\":3,\"items\":[1]}]}") plain)
  , refuses "items name melody indices, never a fraction" "archive.claim/underlay" "1"
      (instanceOf (oneLine "{\"word\":1,\"items\":[0.5]}") plain)
  , refuses "two words never share a note" "archive.claim/underlay" "1"
      (instanceOf (oneLine "{\"word\":1,\"items\":[0,1]},{\"word\":2,\"items\":[1]}") plain)
  , refuses "words never cross notes across lines" "archive.claim/underlay" "1"
      (instanceOf ("{\"lines\":[{\"line\":1,\"words\":[{\"word\":1,\"items\":[4]}]},"
        <> "{\"line\":2,\"words\":[{\"word\":1,\"items\":[3]}]}]}") plain)
  , refuses "a melisma's items strictly increase" "archive.claim/underlay" "1"
      (instanceOf (oneLine "{\"word\":1,\"items\":[2,1]}") plain)
  , refuses "lines strictly increase" "archive.claim/underlay" "1"
      (instanceOf ("{\"lines\":[{\"line\":2,\"words\":[{\"word\":1,\"items\":[0]}]},"
        <> "{\"line\":2,\"words\":[{\"word\":2,\"items\":[1]}]}]}") plain)
  , refuses "words strictly increase within a line" "archive.claim/underlay" "1"
      (instanceOf (oneLine "{\"word\":2,\"items\":[0]},{\"word\":1,\"items\":[1]}") plain)
  , refuses "a syllable's characters are a non-empty span" "archive.claim/underlay" "1"
      (instanceOf (oneLine "{\"word\":1,\"syllables\":[{\"syllable\":1,\"start_char\":3,\"end_char\":3,\"items\":[0]}]}") plain)
  , refuses "syllables of a word do not overlap" "archive.claim/underlay" "1"
      (instanceOf (oneLine ("{\"word\":1,\"syllables\":[{\"syllable\":1,\"start_char\":0,\"end_char\":3,\"items\":[0]},"
        <> "{\"syllable\":2,\"start_char\":2,\"end_char\":5,\"items\":[1]}]}")) plain)
  , accepts "an open repeat's passes may end with last" "archive.claim/underlay" "1"
      (instanceOf (verse ",\"passes\":{\"repeat\":0,\"passes\":[1,3,\"last\"]}") withForm)
  , accepts "an underlay may name the last pass alone" "archive.claim/underlay" "1"
      (instanceOf (verse ",\"passes\":{\"repeat\":0,\"passes\":[\"last\"]}") withForm)
  , refuses "last comes only at the end of the passes" "archive.claim/underlay" "1"
      (instanceOf (verse ",\"passes\":{\"repeat\":0,\"passes\":[\"last\",2]}") withForm)
  , refuses "passes are distinct and ascending" "archive.claim/underlay" "1"
      (instanceOf (verse ",\"passes\":{\"repeat\":0,\"passes\":[2,1]}") withForm)
  , refuses "a pass is a positive number" "archive.claim/underlay" "1"
      (instanceOf (verse ",\"passes\":{\"repeat\":0,\"passes\":[0]}") withForm)
  , refuses "a label is at most 200 characters" "archive.claim/underlay" "1"
      (instanceOf (Text.unpack (Text.replace "Verse 2" (Text.replicate 201 "a") (Text.pack (verse "")))) plain)
  ]
  where
    plain = [ref "melody" "01", ref "document" "02"]
    withForm = plain <> [ref "form" "03"]
    passes2 = ",\"passes\":{\"repeat\":0,\"passes\":[2]}"
    verse extra = "{\"label\":\"Verse 2\"" <> extra <> ",\"lines\":[{\"line\":5,\"words\":["
      <> "{\"word\":1,\"items\":[0]},"
      <> "{\"word\":2,\"syllables\":[{\"syllable\":1,\"start_char\":0,\"end_char\":3,\"items\":[1]},"
      <> "{\"syllable\":2,\"start_char\":3,\"end_char\":7,\"items\":[2,3]}]}]}]}"
    oneLine placed = "{\"lines\":[{\"line\":1,\"words\":[" <> placed <> "]}]}"

formChecks :: Judge -> Judge -> [Check]
formChecks accepts refuses =
  [ accepts "a form version 2 holds an open repeat with a default and a last-time ending" "archive.claim/form" "2"
      (form (repeat' "\"instruction\":\"open for solos, head out\",\"endings\":[{\"first\":3,\"last\":3},{\"first\":4,\"last\":4,\"passes\":[\"last\"]}]"))
  , accepts "an open vamp has no endings" "archive.claim/form" "2"
      (form (repeat' "\"instruction\":\"vamp until cue\""))
  , accepts "an open repeat may number its first passes beside the default and the last" "archive.claim/form" "2"
      (form (repeat' "\"endings\":[{\"first\":2,\"last\":2,\"passes\":[1]},{\"first\":3,\"last\":3},{\"first\":4,\"last\":4,\"passes\":[\"last\"]}]"))
  , refuses "an open repeat has exactly one default ending" "archive.claim/form" "2"
      (form (repeat' "\"endings\":[{\"first\":2,\"last\":2},{\"first\":3,\"last\":3},{\"first\":4,\"last\":4,\"passes\":[\"last\"]}]"))
  , refuses "an open repeat has a last-time ending" "archive.claim/form" "2"
      (form (repeat' "\"endings\":[{\"first\":3,\"last\":3},{\"first\":4,\"last\":4,\"passes\":[1]}]"))
  , refuses "an open repeat's numbered endings name distinct passes" "archive.claim/form" "2"
      (form "{\"first\":0,\"last\":4,\"endings\":[{\"first\":1,\"last\":1,\"passes\":[1]},{\"first\":2,\"last\":2,\"passes\":[1]},{\"first\":3,\"last\":3},{\"first\":4,\"last\":4,\"passes\":[\"last\"]}]}")
  , refuses "a last-time ending names no numbers" "archive.claim/form" "2"
      (form (repeat' "\"endings\":[{\"first\":3,\"last\":3},{\"first\":4,\"last\":4,\"passes\":[2,\"last\"]}]"))
  , accepts "a fixed repeat keeps version 1's endings and may carry an instruction" "archive.claim/form" "2"
      (form (repeat' "\"times\":2,\"instruction\":\"2x\",\"endings\":[{\"first\":3,\"last\":3,\"passes\":[1]},{\"first\":4,\"last\":4,\"passes\":[2]}]"))
  , refuses "a fixed repeat has no default ending" "archive.claim/form" "2"
      (form (repeat' "\"times\":2,\"endings\":[{\"first\":3,\"last\":3,\"passes\":[1]},{\"first\":4,\"last\":4}]"))
  , refuses "a fixed repeat has no last-time ending" "archive.claim/form" "2"
      (form (repeat' "\"times\":2,\"endings\":[{\"first\":3,\"last\":3,\"passes\":[1]},{\"first\":4,\"last\":4,\"passes\":[\"last\"]}]"))
  , refuses "an instruction is at most 200 characters" "archive.claim/form" "2"
      (form (repeat' ("\"instruction\":\"" <> replicate 201 'a' <> "\"")))
  , refuses "a form version 1 refuses an open repeat (anchor)" "archive.claim/form" "1"
      (form (repeat' "\"endings\":[{\"first\":3,\"last\":3},{\"first\":4,\"last\":4,\"passes\":[\"last\"]}]"))
  ]
  where
    form repeats = instanceOf ("{\"sections\":[{\"label\":\"Intro\",\"kind\":\"intro\"},{\"label\":\"A\",\"kind\":\"head\"},"
      <> "{\"label\":\"B\",\"kind\":\"head\"},{\"label\":\"Solos on the form\",\"kind\":\"solo\"},{\"label\":\"Last time\"},"
      <> "{\"label\":\"Coda\",\"kind\":\"coda\"}],\"repeats\":[" <> repeats <> "]}") [ref "work" "01"]
    repeat' fields = "{\"first\":1,\"last\":4," <> fields <> "}"

formSpansChecks :: Judge -> Judge -> [Check]
formSpansChecks accepts refuses =
  [ accepts "a form spans version 2 span names its passes and its return" "archive.claim/form_spans" "2"
      (spans "\"precision\":\"approximate\"" ",\"section\":1,\"passes\":[3],\"return\":true" withForm)
  , accepts "a departure names no section in a claim that pins a form" "archive.claim/form_spans" "2"
      (spans "" ",\"label\":\"Extra chorus\"" withForm)
  , refuses "passes need a section" "archive.claim/form_spans" "2"
      (spans "" ",\"label\":\"Solo\",\"passes\":[1]" withForm)
  , refuses "a return needs a section" "archive.claim/form_spans" "2"
      (spans "" ",\"label\":\"Head out\",\"return\":true" withForm)
  , refuses "a return is only ever true" "archive.claim/form_spans" "2"
      (spans "" ",\"section\":1,\"return\":false" withForm)
  , refuses "a pass is a positive number" "archive.claim/form_spans" "2"
      (spans "" ",\"section\":1,\"passes\":[0]" withForm)
  , refuses "a precision is rough, approximate, or exact" "archive.claim/form_spans" "2"
      (spans "\"precision\":\"close\"" ",\"section\":1" withForm)
  , refuses "a form spans version 1 span names no passes (anchor)" "archive.claim/form_spans" "1"
      (spans "" ",\"section\":1,\"passes\":[3]" withForm)
  ]
  where
    withForm = [ref "source" "01", ref "stream" "02", ref "form" "03"]
    spans precision span' refs = instanceOf ("{\"sample_rate\":48000,\"alignment\":{\"method\":\"manual\""
      <> (if null precision then "" else "," <> precision) <> "},\"spans\":[{\"start_sample\":0,\"end_sample\":480000"
      <> span' <> "}]}") refs

leadSheetChecks :: Judge -> Judge -> [Check]
leadSheetChecks accepts refuses =
  [ accepts "a lead sheet version 2 pins underlays beside its lyrics" "archive.claim/lead_sheet" "2"
      (instanceOf sheet (base <> [ref "lyrics" "03", ref "underlay" "04", ref "underlay" "05"]))
  , refuses "an underlay needs the lyrics" "archive.claim/lead_sheet" "2"
      (instanceOf sheet (base <> [ref "underlay" "04"]))
  , refuses "a lead sheet pins at most eight underlays" "archive.claim/lead_sheet" "2"
      (instanceOf sheet (base <> [ref "lyrics" "03"] <> [ref "underlay" (Text.pack (show n)) | n <- [10 .. 18 :: Int]]))
  , refuses "a lead sheet version 1 pins no underlay (anchor)" "archive.claim/lead_sheet" "1"
      (instanceOf sheet (base <> [ref "lyrics" "03", ref "underlay" "04"]))
  ]
  where
    sheet = "{\"label\":\"A chart\"}"
    base = [ref "work" "01", ref "melody" "02"]
