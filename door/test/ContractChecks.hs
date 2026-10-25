{-# LANGUAGE OverloadedStrings #-}

-- | Checks of the core contexts, the contract catalogs and the installed
-- bundles' profiles in Door's own evaluator (Archive.Door.Contracts,
-- Catalog), with the release modules' rows.
module ContractChecks
  ( tests
  ) where

import Archive.Door.Catalog (loadContractCatalog, parseContractCatalog)
import Archive.Door.Contracts
  ( validateCoreContextForSpecification
  , validateContractInstance
  )
import Archive.Door.Json (decodeUnique)
import BeyondCircle010 (beyondCircle010Checks)
import qualified Crypto.Hash.SHA256 as SHA256
import Data.Aeson ((.=))
import qualified Data.Aeson as Aeson
import qualified Data.Aeson.KeyMap as KeyMap
import qualified Data.ByteString as ByteString
import qualified Data.ByteString.Lazy.Char8 as LazyByteString
import Data.Foldable (toList)
import qualified Data.Text as Text
import Fixtures
import Numeric (showHex)
import Rules010 (rules010Checks)
import Rules011 (rules011Checks)
import Specification010 (specification010Checks)
import Specification011 (specification011Checks)
import Support
import System.Directory (listDirectory)
import System.Exit (ExitCode (ExitSuccess))
import Test.Tasty (TestTree, testGroup)

versionedContextChecks :: Recorder -> IO ()
versionedContextChecks check = do
  oldBytes <- readFile "spec/0.0.2/context.jsonld"
  priorBytes <- readFile "spec/0.0.3/context.jsonld"
  newBytes <- readFile "spec/0.0.4/context.jsonld"
  let oldContext = decodeUnique (LazyByteString.pack oldBytes)
      priorContext = decodeUnique (LazyByteString.pack priorBytes)
      newContext = decodeUnique (LazyByteString.pack newBytes)
  check "the immutable 0.0.2 context remains selected by archive/0.0.2"
    (case oldContext of
      Right value -> validateCoreContextForSpecification "archive/0.0.2" value == Right ()
      Left _ -> False)
    ExitSuccess "" ""
  check "0.0.2 refuses the later musical_work context mapping"
    (case newContext of
      Right value -> case validateCoreContextForSpecification "archive/0.0.2" value of
        Left _ -> True
        Right () -> False
      Left _ -> False)
    ExitSuccess "" ""
  check "0.0.4 accepts its versioned musical_work context"
    (case newContext of
      Right value -> validateCoreContextForSpecification "archive/0.0.4" value == Right ()
      Left _ -> False)
    ExitSuccess "" ""
  check "0.0.3 retains its own versioned context"
    (case priorContext of
      Right value -> validateCoreContextForSpecification "archive/0.0.3" value == Right ()
      Left _ -> False)
    ExitSuccess "" ""

audioContractChecks :: Recorder -> IO ()
audioContractChecks check = do
  selectionSchema <- installedSchema "spec/contracts/sha256-bfde7fb0daaf7ab9af80eabe8d4e684e07f3a9dbf5cbec68266ddf4b9493cc28.json"
  occurrenceSchema <- installedSchema "spec/contracts/sha256-990e8f3a0938bb7ddd25f42a112f135b08460514038096807b573a6101a1a16f.json"
  let pin role objectId revision = Aeson.object
        ["role" .= role, "id" .= objectId, "revision_id" .= revision]
      piece start end = Aeson.object
        ["start_sample" .= (start :: Int), "end_sample" .= (end :: Int), "sample_rate" .= (48000 :: Int)]
      selection pieces refs = Aeson.object ["payload" .= Aeson.object ["pieces" .= pieces], "refs" .= refs]
      occurrence refs = Aeson.object ["payload" .= Aeson.object [], "refs" .= refs]
      first = pin ("piece" :: Text.Text) (Text.pack entity) (Text.pack zeroDigest)
      second = pin ("piece" :: Text.Text) (Text.pack claim) (Text.pack otherDigest)
      one = pin ("selection" :: Text.Text) (Text.pack entity) (Text.pack zeroDigest)
      two = pin ("selection" :: Text.Text) (Text.pack claim) (Text.pack otherDigest)
  check "selection profile accepts ordered pieces and aligned Artifact pins"
    (validateContractInstance selectionSchema (selection [piece 0 50, piece 75 100] [first, second]) == Right ()) ExitSuccess "" ""
  check "selection profile rejects misaligned pieces and pins"
    (isLeft (validateContractInstance selectionSchema (selection [piece 0 50, piece 75 100] [first]))) ExitSuccess "" ""
  check "selection profile rejects inverted sample bounds"
    (isLeft (validateContractInstance selectionSchema (selection [piece 50 50] [first]))) ExitSuccess "" ""
  check "occurrence profile accepts canonical membership"
    (validateContractInstance occurrenceSchema (occurrence [one, two]) == Right ()) ExitSuccess "" ""
  check "occurrence profile rejects duplicate membership"
    (isLeft (validateContractInstance occurrenceSchema (occurrence [one, one]))) ExitSuccess "" ""
  check "occurrence profile rejects reordered membership"
    (isLeft (validateContractInstance occurrenceSchema (occurrence [two, one]))) ExitSuccess "" ""

installedSchema :: FilePath -> IO Aeson.Value
installedSchema path = do
  bytes <- readFile path
  case decodeUnique (LazyByteString.pack bytes) of
    Right (Aeson.Object bundle) -> case KeyMap.lookup "schema" bundle of
      Just schema -> pure schema
      Nothing -> ioError (userError (path <> " has no schema"))
    _ -> ioError (userError (path <> " is not a bundle"))

-- | Pure checks of the 0.0.5 context and of code-point string length, which
-- `door admit` cannot isolate from the operator's locale.
reviewContractProfileChecks :: Recorder -> IO ()
reviewContractProfileChecks check = do
  newBytes <- readFile "spec/0.0.5/context.jsonld"
  priorBytes <- readFile "spec/0.0.4/context.jsonld"
  let newContext = decodeUnique (LazyByteString.pack newBytes)
      priorContext = decodeUnique (LazyByteString.pack priorBytes)
  check "0.0.5 accepts its recording_session context"
    (either (const False) ((== Right ()) . validateCoreContextForSpecification "archive/0.0.5") newContext)
    ExitSuccess "" ""
  check "0.0.4 refuses the later recording_session context"
    (either (const False) (isLeft . validateCoreContextForSpecification "archive/0.0.4") newContext)
    ExitSuccess "" ""
  check "0.0.5 refuses the earlier context without recording_session"
    (either (const False) (isLeft . validateCoreContextForSpecification "archive/0.0.5") priorContext)
    ExitSuccess "" ""
  (kindCode, kindOutput, kindErrors) <- dryRun
    (replace "archive/0.0.12" "archive/0.0.4"
      (replace "{\"kind\":\"person\"" "{\"kind\":\"recording_session\"" (entityDraft entity)))
  check "specification 0.0.4 refuses the later recording_session kind"
    (kindCode /= ExitSuccess && "entity kind is not defined by the core context" `contains` kindErrors)
    kindCode kindOutput kindErrors
  titleBundle <- readFile "spec/contracts/sha256-0bec7932301de6f62b92ffc167f41236cf98c10e6a6175481400c64da502d8d9.json"
  case decodeUnique (LazyByteString.pack titleBundle) of
    Right (Aeson.Object fields) | Just schema <- KeyMap.lookup "schema" fields -> do
      let instanceWith title = Aeson.object
            [ "payload" .= Aeson.object ["title" .= title]
            , "refs" .= [Aeson.object
                [ "role" .= ("selection" :: Text.Text)
                , "id" .= ("urn:uuid:00000000-0000-4000-8000-000000000002" :: Text.Text)
                , "revision_id" .= Text.pack zeroDigest ]]
            ]
          accented = Text.replicate 200 (Text.singleton '\233')
      check "maxLength counts code points, not UTF-8 bytes"
        (validateContractInstance schema (instanceWith accented) == Right ()
          && isLeft (validateContractInstance schema (instanceWith (accented <> "x"))))
        ExitSuccess "" ""
    _ -> check "the installed title bundle decodes" False ExitSuccess titleBundle ""

-- | Schema-level checks of the migration bundles that need no run record:
-- decimal text, and a work match that is valid but cannot be admitted yet.
migrationContractProfileChecks :: Recorder -> IO ()
migrationContractProfileChecks check = do
  let bundleSchema digest = do
        bytes <- readFile ("spec/contracts/sha256-" <> digest <> ".json")
        case decodeUnique (LazyByteString.pack bytes) of
          Right (Aeson.Object fields) | Just schema <- KeyMap.lookup "schema" fields -> pure schema
          _ -> check "an installed migration bundle decodes" False ExitSuccess bytes "" >> pure Aeson.Null
      -- Each role names a different object, so the distinct-references rule
      -- holds and a refusal can only come from the field under test.
      ref role = Aeson.object
        [ "role" .= (role :: Text.Text)
        , "id" .= ("urn:uuid:00000000-0000-4000-8000-0000000000" <> roleSuffix role :: Text.Text)
        , "revision_id" .= Text.pack zeroDigest ]
      roleSuffix role = case role of
        "occurrence" -> "11"
        "work" -> "12"
        "corpus" -> "13"
        "run" -> "14"
        "translated" -> "15"
        _ -> "16"
  matchSchema <- bundleSchema "f01dfa681f7261be99e25518842f09c23fdca714f434f316cb86ae1cbf367512"
  let match score = Aeson.object
        [ "payload" .= Aeson.object
            [ "direction" .= ("lower_is_closer" :: Text.Text)
            , "method" .= Aeson.object ["id" .= ("chroma-cover" :: Text.Text), "version" .= ("1" :: Text.Text)]
            , "scale" .= ("distance" :: Text.Text)
            , "score" .= (score :: Text.Text) ]
        , "refs" .= [ ref "occurrence", ref "work", ref "corpus", ref "run" ]
        ]
  check "a work match accepts exact signed decimal text"
    (all (\score -> isRightUnit (validateContractInstance matchSchema (match score))) ["0", "-0.25", "12.0625"])
    ExitSuccess "" ""
  check "a work match refuses scores that are not exact decimal text"
    (all (\score -> isLeft (validateContractInstance matchSchema (match score))) ["01", "1e3", ".5", "1.", "+1", "NaN"])
    ExitSuccess "" ""
  selectionSchema <- bundleSchema "c7b635bb3313a8281e89d5ba736f30e4ebf246850f290b58dccc9cf664a0edc5"
  let seconds start = case decodeUnique (LazyByteString.pack (selectionTranslationPayload start "4.1876")) of
        Right payload -> Aeson.object ["payload" .= payload, "refs" .= [ref "translated", ref "migration_source"]]
        Left _ -> Aeson.Null
  check "source seconds accept up to six decimal places"
    (isRightUnit (validateContractInstance selectionSchema (seconds "0.000208"))
      && isLeft (validateContractInstance selectionSchema (seconds "0.0002081"))
      && isLeft (validateContractInstance selectionSchema (seconds "-1")))
    ExitSuccess "" ""

isRightUnit :: Either Text.Text () -> Bool
isRightUnit = (== Right ())

-- | Every specification version's catalog loads, lists only bundles whose
-- bytes carry the listed identity, and a missing or malformed catalog is a
-- refusal, never an empty set (ADR 0057).
contractCatalogChecks :: Recorder -> IO ()
contractCatalogChecks check = do
  versions <- filter (all (`elem` ("0123456789." :: String))) <$> listDirectory "spec"
  check "every specification version directory is enumerated" (length versions >= 6)
    ExitSuccess (show versions) ""
  mapM_ checkVersion versions
  missing <- loadContractCatalog "archive/9.9.9"
  check "a specification without a catalog is refused, not empty" (isLeft missing)
    ExitSuccess (show missing) ""
  let good = "{\"spec\":\"archive/0.0.1\",\"contracts\":[{\"id\":\"a\",\"version\":\"1\",\"digest\":\"sha256:" <> replicate 64 '0' <> "\"}]}"
      parsed = parseContractCatalog "archive/0.0.1" . LazyByteString.pack
  check "a well-formed catalog parses" (either (const False) ((== 1) . length) (parsed good))
    ExitSuccess (show (parsed good)) ""
  mapM_ (\(label, text) -> check label (isLeft (parsed text)) ExitSuccess (show (parsed text)) "")
    [ ("a catalog naming another specification is refused", replace "archive/0.0.1" "archive/0.0.2" good)
    , ("an empty catalog is refused", "{\"spec\":\"archive/0.0.1\",\"contracts\":[]}")
    , ("a catalog with an extra field is refused", replace "{\"spec\"" "{\"extra\":1,\"spec\"" good)
    , ("a catalog listing a contract twice is refused"
      , replace "}]}" ("}," <> drop 1 (takeWhile (/= ']') (dropWhile (/= '[') good)) <> "]}") good)
    , ("a catalog with a malformed digest is refused", replace "sha256:0" "sha256:g" good)
    ]
  where
    checkVersion version = do
      catalog <- loadContractCatalog (Text.pack ("archive/" <> version))
      case catalog of
        Left problem -> check ("the catalog of " <> version <> " loads") False ExitSuccess (Text.unpack problem) ""
        Right entries -> mapM_ (checkEntry version) (toList entries)
    checkEntry version (identifier, contractVersion, digest) = do
      bytes <- ByteString.readFile ("spec/contracts/sha256-" <> drop 7 (Text.unpack digest) <> ".json")
      let actual = "sha256:" <> concatMap hex2 (ByteString.unpack (SHA256.hash bytes))
          fields = case decodeUnique (LazyByteString.fromStrict bytes) of
            Right (Aeson.Object object) -> (KeyMap.lookup "id" object, KeyMap.lookup "version" object)
            _ -> (Nothing, Nothing)
      check (version <> " lists " <> Text.unpack identifier <> " " <> Text.unpack contractVersion <> " by its bytes' digest and identity")
        (actual == Text.unpack digest && fields == (Just (Aeson.String identifier), Just (Aeson.String contractVersion)))
        ExitSuccess actual (show fields)
    hex2 byte = let h = showHex byte "" in if length h == 1 then '0' : h else h

-- | The span window rule is checked when a bundle loads: each malformed rule
-- is refused, and the released version 3 bundle accepts a valid instance.
windowMatchProfileChecks :: Recorder -> IO ()
windowMatchProfileChecks check = do
  catalog <- either (const []) toList <$> loadContractCatalog "archive/0.0.9"
  schema <- case [digest | ("archive.claim/work_match", "3", digest) <- catalog] of
    [digest] -> do
      bytes <- readFile ("spec/contracts/sha256-" <> drop 7 (Text.unpack digest) <> ".json")
      case decodeUnique (LazyByteString.pack bytes) of
        Right (Aeson.Object fields) | Just value <- KeyMap.lookup "schema" fields -> pure value
        _ -> check "the work_match 3 bundle decodes" False ExitSuccess bytes "" >> pure Aeson.Null
    found -> check "the 0.0.9 catalog lists work_match version 3 once" False ExitSuccess (show found) "" >> pure Aeson.Null
  let ref role suffix = Aeson.object
        [ "role" .= (role :: Text.Text)
        , "id" .= ("urn:uuid:00000000-0000-4000-8000-0000000000" <> suffix :: Text.Text)
        , "revision_id" .= Text.pack zeroDigest ]
      decoded text = either (const Aeson.Null) id (decodeUnique (LazyByteString.pack text))
      valid = Aeson.object
        [ "payload" .= decoded (windowMatchPayload 48000 0 5000)
        , "refs" .= [ref "suggestions" "31", ref "work" "32", ref "corpus" "33", ref "corpus" "34", ref "run" "35"] ]
      rule = case schema of
        Aeson.Object fields | Just (Aeson.Object value) <- KeyMap.lookup "x-archive-span-window" fields -> value
        _ -> KeyMap.empty
      withRule value = case schema of
        Aeson.Object fields -> Aeson.Object (KeyMap.insert "x-archive-span-window" value fields)
        _ -> schema
      refusedOnLoad label candidate = check label (isLeft (validateContractInstance candidate valid)) ExitSuccess
        (show (validateContractInstance candidate valid)) ""
  check "the released work_match 3 bundle carries a span window rule and accepts a valid instance (anchor)"
    (not (KeyMap.null rule) && validateContractInstance schema valid == Right ())
    ExitSuccess (show (validateContractInstance schema valid)) ""
  refusedOnLoad "a span window rule missing its spans field is refused"
    (withRule (Aeson.Object (KeyMap.delete "spans" rule)))
  refusedOnLoad "a span window rule with an extra key is refused"
    (withRule (Aeson.Object (KeyMap.insert "count" (Aeson.String "sample_count") rule)))
  refusedOnLoad "a span window rule naming one bound field twice is refused"
    (withRule (Aeson.Object (KeyMap.insert "end" (Aeson.String "start_sample") rule)))
  refusedOnLoad "a span window rule whose role names many Claims is refused"
    (withRule (Aeson.Object (KeyMap.insert "role" (Aeson.String "corpus") rule)))
  refusedOnLoad "a span window rule whose role names an Entity is refused"
    (withRule (Aeson.Object (KeyMap.insert "role" (Aeson.String "work") rule)))
  refusedOnLoad "a span window rule whose role has no target rule is refused"
    (withRule (Aeson.Object (KeyMap.insert "role" (Aeson.String "window") rule)))
  refusedOnLoad "a span window rule with an empty field name is refused"
    (withRule (Aeson.Object (KeyMap.insert "spans" (Aeson.String "") rule)))
  refusedOnLoad "a span window rule that is not an object is refused"
    (withRule (Aeson.String "spans"))
  case schema of
    Aeson.Object fields | Just (Aeson.Object properties) <- KeyMap.lookup "properties" fields ->
      refusedOnLoad "a span window rule below the schema root is refused"
        (Aeson.Object (KeyMap.insert "properties"
          (Aeson.Object (KeyMap.insert "x-archive-span-window" (Aeson.Object rule) properties))
          (KeyMap.delete "x-archive-span-window" fields)))
    _ -> check "the work_match 3 schema has properties" False ExitSuccess "" ""

-- | Specification 0.0.9's bundles and profile rules (D-17), without an
-- archive. The released bundles accept the fixture plan's own records (the
-- anchors); each malformed rule, keyword, or target rule is refused when the
-- bundle loads; and each new rule refuses its own violation. Door's agreement
-- with World on admission, through the real `door admit`, is tested by
-- world/tests/test_door_agreement.py on records Door admitted.
musicAndCreditProfileChecks :: Recorder -> IO ()
musicAndCreditProfileChecks check = do
  catalog <- either (const []) toList <$> loadContractCatalog "archive/0.0.9"
  planBytes <- LazyByteString.readFile "fixture/plan.json"
  let decodedPlan = either (const Aeson.Null) id (decodeUnique planBytes)
      planRecords = case decodedPlan of
        Aeson.Object plan | Just (Aeson.Array steps) <- KeyMap.lookup "steps" plan ->
          [ record | Aeson.Object step <- toList steps, Just (Aeson.Array records) <- [KeyMap.lookup "records" step]
                   , Aeson.Object record <- toList records ]
        _ -> []
      planned key = case [record | record <- planRecords, KeyMap.lookup "key" record == Just (Aeson.String key)] of
        [record] -> record
        _ -> KeyMap.empty
      -- The plan's record with every pin replaced by a well-formed reference.
      instanceOf key = Aeson.object
        [ "payload" .= maybe Aeson.Null id (KeyMap.lookup "payload" (planned key))
        , "refs" .= [ Aeson.object [ "role" .= role
                                   , "id" .= ("urn:uuid:00000000-0000-4000-8000-0000000000" <> Text.pack (show (10 + index :: Int)) :: Text.Text)
                                   , "revision_id" .= Text.pack zeroDigest ]
                    | (index, Aeson.Object ref) <- zip [0 ..] (case KeyMap.lookup "refs" (planned key) of
                        Just (Aeson.Array refs) -> toList refs
                        _ -> [])
                    , Just (Aeson.String role) <- [KeyMap.lookup "role" ref] ] ]
      schemaOf identifier = do
        case [digest | (identifier', "1", digest) <- catalog, identifier' == identifier] of
          [digest] -> do
            bytes <- LazyByteString.readFile ("spec/contracts/sha256-" <> drop 7 (Text.unpack digest) <> ".json")
            case decodeUnique bytes of
              Right (Aeson.Object fields) | Just schema <- KeyMap.lookup "schema" fields -> pure schema
              _ -> check ("the bundle of " <> Text.unpack identifier <> " decodes") False ExitSuccess "" "" >> pure Aeson.Null
          found -> check ("the 0.0.9 catalog lists " <> Text.unpack identifier <> " version 1 once") False ExitSuccess (show found) ""
            >> pure Aeson.Null
      withKey name value (Aeson.Object fields) = Aeson.Object (KeyMap.insert name value fields)
      withKey _ _ other = other
      withoutKey name (Aeson.Object fields) = Aeson.Object (KeyMap.delete name fields)
      withoutKey _ other = other
      ruleOf name (Aeson.Object fields) = maybe Aeson.Null id (KeyMap.lookup name fields)
      ruleOf _ _ = Aeson.Null
      decoded text = either (const Aeson.Null) id (decodeUnique (LazyByteString.pack text))
      instanceAccepted label schema value = check label (validateContractInstance schema value == Right ()) ExitSuccess
        (show (validateContractInstance schema value)) ""
      instanceRefused label schema value = check label (isLeft (validateContractInstance schema value)) ExitSuccess
        (show (validateContractInstance schema value)) ""
      onPayload f (Aeson.Object fields) = Aeson.Object (maybe fields (\value -> KeyMap.insert "payload" (f value) fields)
        (KeyMap.lookup "payload" fields))
      onPayload _ other = other
  chords <- schemaOf "archive.claim/chord_stacks"
  meter <- schemaOf "archive.claim/meter_layers"
  contribution <- schemaOf "archive.claim/contribution"
  membership <- schemaOf "archive.claim/band_membership"
  let chordInstance = instanceOf "chords"
      chartInstance = instanceOf "chords-chart"
      meterInstance = instanceOf "meter-chart"
      spanInstance = instanceOf "contribution-span"
      membershipInstance = instanceOf "membership"
  -- Anchors: the released bundles accept the records the fixture admits.
  instanceAccepted "chord_stacks 1 accepts the fixture's machine chords (anchor)" chords chordInstance
  instanceAccepted "chord_stacks 1 accepts the fixture's chart chords (anchor)" chords chartInstance
  instanceAccepted "meter_layers 1 accepts the fixture's chart meter (anchor)" meter meterInstance
  instanceAccepted "contribution 1 accepts the fixture's span credit (anchor)" contribution spanInstance
  instanceAccepted "band_membership 1 accepts the fixture's membership (anchor)" membership membershipInstance

  -- Each new rule refuses its own violation, the twin otherwise unchanged.
  let reading f = onPayload (\payload -> case payload of
        Aeson.Object p | Just (Aeson.Array spans) <- KeyMap.lookup "spans" p, (Aeson.Object second : _) <- drop 1 (toList spans)
                       , Just (Aeson.Array readings) <- KeyMap.lookup "readings" second, (first : rest) <- toList readings ->
            let span' = Aeson.Object (KeyMap.insert "readings" (Aeson.toJSON (f first : rest)) second)
            in Aeson.Object (KeyMap.insert "spans" (Aeson.toJSON (take 1 (toList spans) <> [span'] <> drop 2 (toList spans))) p)
        other -> other)
  instanceRefused "x-archive-ascending-items refuses tones out of order" chords
    (reading (withKey "tones" (decoded "[4,1]")) chordInstance)
  instanceRefused "x-archive-pitch-stacks refuses an uncertain class that is a tone" chords
    (reading (withKey "uncertain" (decoded "[{\"pitch_classes\":[4,8],\"present\":\"exactly_one\"}]")) chordInstance)
  instanceRefused "x-archive-pitch-stacks refuses a voicing whose lowest note is not the bass" chords
    (reading (withKey "voicing" (decoded "[61,64,69]")) chordInstance)
  instanceRefused "anyOf refuses exactly one of a single uncertain class" chords
    (reading (withKey "uncertain" (decoded "[{\"pitch_classes\":[7],\"present\":\"exactly_one\"}]")) chordInstance)
  instanceRefused "if/then refuses a reading without its weight when the claim states a scale" chords
    (reading (withoutKey "weight") chordInstance)
  instanceRefused "if/else refuses an alignment without a document" chords
    (onPayload (withKey "alignment" (decoded "{\"method\":\"manual\"}")) chordInstance)
  let meterBar f = onPayload (\payload -> case payload of
        Aeson.Object p | Just (Aeson.Array layers) <- KeyMap.lookup "layers" p, (Aeson.Object layer : others) <- toList layers
                       , Just (Aeson.Array bars) <- KeyMap.lookup "bars" layer, (first : second : rest) <- toList bars ->
            let layer' = Aeson.Object (KeyMap.insert "bars" (Aeson.toJSON (first : f second : rest)) layer)
            in Aeson.Object (KeyMap.insert "layers" (Aeson.toJSON (layer' : others)) p)
        other -> other)
  instanceRefused "x-archive-bar-beats refuses a grouping that does not add up" meter
    (meterBar (withKey "grouping" (decoded "[3,2]")) meterInstance)
  instanceRefused "x-archive-bar-beats refuses a beat at the bar's end" meter
    (meterBar (withKey "beats" (decoded "[2000,4000]")) meterInstance)
  instanceRefused "x-archive-sequential-spans with within refuses overlapping bars in one layer" meter
    (meterBar (withKey "start_sample" (decoded "1900")) meterInstance)
  instanceRefused "x-archive-metric-modulations refuses a regrouping whose pulse does not move" meter
    (onPayload (withKey "modulations" (decoded "[{\"layer\":0,\"bar\":1,\"kind\":\"regrouping\",\"pulse_before\":{\"denominator\":4},\"pulse_after\":{\"denominator\":4,\"dots\":0}}]") . withoutKey "modulations") meterInstance)
  instanceRefused "x-archive-metric-modulations refuses a link of a beat-only layer" meter
    (onPayload (withKey "layers" (decoded "[{\"beats\":[0,1000]},{\"bars\":[{\"start_sample\":0,\"end_sample\":2000,\"numerator\":4,\"denominator\":4}]}]")
      . withKey "modulations" (decoded "[{\"layer\":0,\"bar\":1,\"kind\":\"equation\",\"before\":{\"denominator\":4},\"after\":{\"denominator\":8}}]")) meterInstance)
  instanceRefused "anyOf refuses a link with a ratio" meter
    (onPayload (withKey "modulations" (decoded "[{\"layer\":0,\"bar\":1,\"kind\":\"equation\",\"before\":{\"denominator\":4},\"after\":{\"denominator\":4,\"dots\":1},\"ratio\":[2,3]}]")) meterInstance)
  instanceRefused "x-archive-subset refuses a downbeat that is not a beat" meter
    (onPayload (withKey "layers" (decoded "[{\"beats\":[0,1000,2000],\"downbeats\":[0,1500]}]") . withoutKey "modulations") meterInstance)
  instanceRefused "x-archive-ascending-items refuses beats out of order" meter
    (onPayload (withKey "layers" (decoded "[{\"beats\":[0,2000,1000]}]") . withoutKey "modulations") meterInstance)
  instanceRefused "if/then refuses a felt layer without a written one" meter
    (onPayload (withKey "layers" (decoded "[{\"role\":\"felt\",\"beats\":[0,1000]}]") . withoutKey "modulations") meterInstance)
  instanceAccepted "a beat-only layer with downbeats and a beat unit is accepted" meter
    (onPayload (withKey "layers" (decoded "[{\"beats\":[0,1000,2000],\"downbeats\":[0,2000],\"beat_unit\":{\"denominator\":4}}]") . withoutKey "modulations") meterInstance)
  instanceRefused "x-archive-partial-dates refuses a membership that ends before it begins" membership
    (onPayload (withKey "to" (Aeson.String "2019-12")) membershipInstance)
  instanceRefused "x-archive-partial-dates refuses month 13" membership
    (onPayload (withKey "from" (Aeson.String "2021-13")) membershipInstance)
  instanceRefused "x-archive-ascending-items refuses attributes out of order" membership
    (onPayload (withKey "attributes" (decoded "[\"original\",\"additional\"]")) membershipInstance)
  instanceRefused "uniqueItems refuses an instrument twice" membership
    (onPayload (withKey "instruments" (decoded "[\"https://musicbrainz.org/instrument/f6100277-c7b8-4c8d-aa26-d8cd014b6761\",\"https://musicbrainz.org/instrument/f6100277-c7b8-4c8d-aa26-d8cd014b6761\"]")) membershipInstance)
  instanceRefused "a role from another scope is refused" contribution
    (onPayload (withKey "role" (decoded "{\"type\":\"composer\"}")) spanInstance)
  instanceRefused "x-archive-sequential-spans optional still orders a span claim's spans" contribution
    (onPayload (withKey "spans" (decoded "[{\"start_sample\":0,\"end_sample\":2000},{\"start_sample\":1999,\"end_sample\":3000}]")) spanInstance)
  instanceAccepted "a work credit has no spans, and optional sequential spans let it be" contribution (instanceOf "contribution-work")
  sequenceSchema <- schemaOf "archive.claim/chord_sequence"
  let sequenceInstance = instanceOf "sequence"
  instanceAccepted "chord_sequence 1 accepts the fixture's sequence (anchor)" sequenceSchema sequenceInstance
  instanceAccepted "chord_stacks 1 accepts the fixture's placed chords (anchor)" chords (instanceOf "chords-placed")
  instanceAccepted "chord_stacks 1 accepts the fixture's correction (anchor)" chords (instanceOf "chords-correction")
  instanceRefused "x-archive-document-order refuses an entry located before the one before it" sequenceSchema
    (onPayload (withKey "entries" (decoded "[{\"locator\":{\"line\":2},\"readings\":[{\"text\":\"C\"}]},{\"locator\":{\"line\":1,\"word\":4},\"readings\":[{\"text\":\"G\"}]}]") . withoutKey "repeats") sequenceInstance)
  instanceRefused "x-archive-nested-ranges refuses crossing repeats" sequenceSchema
    (onPayload (withKey "repeats" (decoded "[{\"first\":0,\"last\":1,\"times\":2},{\"first\":1,\"last\":2,\"times\":2}]")) sequenceInstance)
  instanceRefused "x-archive-nested-ranges refuses a repeat past the last entry" sequenceSchema
    (onPayload (withKey "repeats" (decoded "[{\"first\":0,\"last\":3,\"times\":2}]")) sequenceInstance)
  instanceAccepted "x-archive-nested-ranges accepts nested repeats, outer first" sequenceSchema
    (onPayload (withKey "repeats" (decoded "[{\"first\":0,\"last\":2,\"times\":2},{\"first\":0,\"last\":1,\"times\":3}]")) sequenceInstance)
  instanceRefused "not refuses a locator with both a word and a column" sequenceSchema
    (onPayload (withKey "entries" (decoded "[{\"locator\":{\"line\":2,\"word\":1,\"column\":4},\"readings\":[{\"text\":\"C\"}]}]") . withoutKey "repeats") sequenceInstance)
  instanceRefused "anyOf refuses a sequence with neither a Work nor a document" sequenceSchema
    (withKey "refs" (Aeson.toJSON ([] :: [Aeson.Value])) sequenceInstance)
  instanceRefused "if/then refuses a held span without a hold rule" chords
    (onPayload (withoutKey "hold_rule") (instanceOf "chords-placed"))
  instanceRefused "if/else refuses a hold rule with no held span" chords
    (onPayload (withKey "hold_rule" (Aeson.String "until the next")) chordInstance)
  instanceRefused "if/else refuses a sequence entry without a pinned sequence" chords
    (onPayload (withKey "spans" (decoded "[{\"start_sample\":0,\"end_sample\":1000,\"entry\":0,\"readings\":[{\"text\":\"C\"}]}]")) (instanceOf "chords-correction"))

  -- Each malformed rule, keyword, or target rule is refused when its bundle
  -- loads, whatever the instance.
  let refusedOnLoad label schema value = check label (isLeft (validateContractInstance schema value)) ExitSuccess
        (show (validateContractInstance schema value)) ""
      withRule schema name f = withKey name (f (ruleOf name schema)) schema
      targets = ruleOf "x-archive-reference-targets" contribution
  refusedOnLoad "an ascending items rule with no paths is refused" chords
    (withRule chords "x-archive-ascending-items" (withKey "paths" (decoded "[]")))
  refusedOnLoad "an ascending items rule with a path that is not an array is refused" chords
    (withRule chords "x-archive-ascending-items" (withKey "paths" (decoded "[\"spans\"]")))
  refusedOnLoad "an ascending items rule repeating a path is refused" chords
    (withRule chords "x-archive-ascending-items" (withKey "paths" (decoded "[[\"spans\"],[\"spans\"]]")))
  refusedOnLoad "a pitch stacks rule missing its voicing field is refused" chords
    (withRule chords "x-archive-pitch-stacks" (withoutKey "voicing"))
  refusedOnLoad "a pitch stacks rule naming one field twice is refused" chords
    (withRule chords "x-archive-pitch-stacks" (withKey "voicing" (Aeson.String "tones")))
  refusedOnLoad "a bar beats rule with an extra key is refused" meter
    (withRule meter "x-archive-bar-beats" (withKey "count" (Aeson.String "beats")))
  refusedOnLoad "a metric modulations rule missing dots is refused" meter
    (withRule meter "x-archive-metric-modulations" (withoutKey "dots"))
  refusedOnLoad "a metric modulations rule still naming a ratio is refused" meter
    (withRule meter "x-archive-metric-modulations" (withKey "ratio" (Aeson.String "ratio")))
  refusedOnLoad "a subset rule comparing an array with itself is refused" meter
    (withRule meter "x-archive-subset" (withKey "of" (Aeson.String "downbeats")))
  refusedOnLoad "a target samples rule whose role pins an Artifact is refused" meter
    (withRule meter "x-archive-target-samples" (withKey "role" (Aeson.String "source")))
  refusedOnLoad "a target index rule whose path is not a path is refused" chords
    (withRule chords "x-archive-target-index" (withKey "path" (Aeson.String "spans")))
  refusedOnLoad "a document order rule with no within fields is refused" sequenceSchema
    (withRule sequenceSchema "x-archive-document-order" (withKey "within" (decoded "[]")))
  refusedOnLoad "a nested ranges rule naming one bound twice is refused" sequenceSchema
    (withRule sequenceSchema "x-archive-nested-ranges" (withKey "last" (Aeson.String "first")))
  refusedOnLoad "a sequential spans rule whose within is neither a name nor a path is refused" meter
    (withRule meter "x-archive-sequential-spans" (withKey "within" (decoded "[]")))
  refusedOnLoad "a same subject rule whose optional is false is refused" contribution
    (withRule contribution "x-archive-same-subject" (withKey "optional" (Aeson.Bool False)))
  refusedOnLoad "a target bound rule whose role pins an Artifact is refused" contribution
    (withRule contribution "x-archive-target-bound" (withKey "role" (Aeson.String "source")))
  refusedOnLoad "a partial dates rule ordering a field it does not name is refused" membership
    (withRule membership "x-archive-partial-dates" (withKey "end" (Aeson.String "ended")))
  refusedOnLoad "a partial dates rule ordering a field with itself is refused" membership
    (withRule membership "x-archive-partial-dates" (withKey "end" (Aeson.String "from")))
  refusedOnLoad "a target rule with one alternative is refused" contribution
    (withKey "x-archive-reference-targets" (withKey "recording" (decoded "{\"any\":[{\"shape\":\"artifact\"}]}") targets) contribution)
  refusedOnLoad "a target rule with nested alternatives is refused" contribution
    (withKey "x-archive-reference-targets" (withKey "recording"
      (decoded "{\"any\":[{\"shape\":\"artifact\"},{\"any\":[{\"shape\":\"entity\"},{\"shape\":\"claim\"}]}]}") targets) contribution)
  refusedOnLoad "a target alternative naming many is refused" contribution
    (withKey "x-archive-reference-targets" (withKey "recording"
      (decoded "{\"any\":[{\"shape\":\"artifact\",\"many\":true},{\"shape\":\"entity\",\"kind\":\"recording\"}]}") targets) contribution)
  refusedOnLoad "an empty anyOf is refused" chords (withKey "anyOf" (decoded "[]") chords)
  refusedOnLoad "then without if is refused" chords (withKey "then" (decoded "{}") chords)
  refusedOnLoad "uniqueItems that is not a boolean is refused" chords
    (withKey "properties" (withKey "refs" (withKey "uniqueItems" (Aeson.String "yes") (ruleOf "refs" (ruleOf "properties" chords))) (ruleOf "properties" chords)) chords)
  refusedOnLoad "a pitch stacks rule below the schema root is refused" chords
    (withKey "properties" (withKey "x-archive-pitch-stacks" (ruleOf "x-archive-pitch-stacks" chords) (ruleOf "properties" chords))
      (withoutKey "x-archive-pitch-stacks" chords))
  context <- either (const Aeson.Null) id . decodeUnique <$> LazyByteString.readFile "spec/0.0.9/context.jsonld"
  check "the 0.0.9 core context, with release and recording, is 0.0.9's and not 0.0.8's"
    (validateCoreContextForSpecification "archive/0.0.9" context == Right ()
      && validateCoreContextForSpecification "archive/0.0.11" context == Right ()
      && isLeft (validateCoreContextForSpecification "archive/0.0.8" context))
    ExitSuccess "" ""
  refuses "specification 0.0.8 refuses the later release kind"
    (replace "archive/0.0.12" "archive/0.0.8" (replace "{\"kind\":\"person\"" "{\"kind\":\"release\"" (entityDraft entity)))
    "entity kind is not defined by the core context"
  succeeds "specification 0.0.11 keeps the recording kind of 0.0.9"
    (replace "{\"kind\":\"person\"" "{\"kind\":\"recording\"" (entityDraft entity))

-- | Pure checks of the 0.0.9 profile: each malformed rule is refused when its
-- bundle loads, and the accepted bundles' rules are the anchors.
derivationContractProfileChecks :: Recorder -> IO ()
derivationContractProfileChecks check = do
  catalog <- either (const []) toList <$> loadContractCatalog "archive/0.0.9"
  let digestFor identifier version =
        [digest | (identifier', version', digest) <- catalog, identifier' == identifier, version' == version]
      bundleSchema identifier version = case digestFor identifier version of
        [digest] -> do
          bytes <- readFile ("spec/contracts/sha256-" <> drop 7 (Text.unpack digest) <> ".json")
          case decodeUnique (LazyByteString.pack bytes) of
            Right (Aeson.Object fields) | Just schema <- KeyMap.lookup "schema" fields -> pure schema
            _ -> check ("the bundle of " <> Text.unpack identifier <> " decodes") False ExitSuccess bytes "" >> pure Aeson.Null
        found -> check ("the 0.0.9 catalog lists " <> Text.unpack identifier <> " " <> Text.unpack version)
          False ExitSuccess (show found) "" >> pure Aeson.Null
      ref role suffix = Aeson.object
        [ "role" .= (role :: Text.Text)
        , "id" .= ("urn:uuid:00000000-0000-4000-8000-0000000000" <> suffix :: Text.Text)
        , "revision_id" .= Text.pack zeroDigest ]
      withRoot schema key value = case schema of
        Aeson.Object fields -> Aeson.Object (KeyMap.insert key value fields)
        _ -> schema
      withoutRoot schema key = case schema of
        Aeson.Object fields -> Aeson.Object (KeyMap.delete key fields)
        _ -> schema
      instanceOf payload refs = Aeson.object ["payload" .= payload, "refs" .= (refs :: [Aeson.Value])]
      decoded text = either (const Aeson.Null) id (decodeUnique (LazyByteString.pack text))
  runSchema <- bundleSchema "archive.claim/derivation_run" "1"
  suggestionsSchema <- bundleSchema "archive.claim/span_suggestions" "1"
  let run = instanceOf (decoded runPayload) []
      suggestions = instanceOf (decoded (spansPayload 48000 [(0, 10, "music")]))
        [ref "source" "21", ref "stream" "22", ref "run" "23"]
  check "the accepted 0.0.9 instances satisfy their bundles"
    (validateContractInstance runSchema run == Right ()
      && validateContractInstance suggestionsSchema suggestions == Right ())
    ExitSuccess "" ""
  let refusedOnLoad label schema instanceValue =
        check label (isLeft (validateContractInstance schema instanceValue)) ExitSuccess
          (show (validateContractInstance schema instanceValue)) ""
      targets = case runSchema of
        Aeson.Object fields -> KeyMap.lookup "x-archive-reference-targets" fields
        _ -> Nothing
      withUsedRule rule = withRoot runSchema "x-archive-reference-targets"
        (case targets of
          Just (Aeson.Object roles) -> Aeson.Object (KeyMap.insert "used" rule roles)
          _ -> Aeson.Null)
  refusedOnLoad "a target rule naming a single shape in shapes is refused"
    (withUsedRule (Aeson.object ["shapes" .= ["claim" :: Text.Text], "many" .= True])) run
  refusedOnLoad "a target rule naming an unknown shape in shapes is refused"
    (withUsedRule (Aeson.object ["shapes" .= ["claim", "record" :: Text.Text], "many" .= True])) run
  refusedOnLoad "a target rule naming both shape and shapes is refused"
    (withUsedRule (Aeson.object ["shape" .= ("claim" :: Text.Text), "shapes" .= ["claim", "entity" :: Text.Text]])) run
  refusedOnLoad "a target rule with kinds for several shapes is refused"
    (withUsedRule (Aeson.object ["shapes" .= ["claim", "entity" :: Text.Text], "kind" .= ("person" :: Text.Text)])) run
  refusedOnLoad "an ordered instants rule naming one field twice is refused"
    (withRoot runSchema "x-archive-ordered-instants" (Aeson.object ["start" .= ("started_at" :: Text.Text), "end" .= ("started_at" :: Text.Text)])) run
  refusedOnLoad "an ordered instants rule with an extra key is refused"
    (withRoot runSchema "x-archive-ordered-instants" (Aeson.object ["start" .= ("started_at" :: Text.Text), "end" .= ("ended_at" :: Text.Text), "payload" .= ("x" :: Text.Text)])) run
  refusedOnLoad "a sequential spans rule missing its end field is refused"
    (withRoot suggestionsSchema "x-archive-sequential-spans" (Aeson.object ["payload" .= ("spans" :: Text.Text), "start" .= ("start_sample" :: Text.Text)])) suggestions
  refusedOnLoad "a target measure rule whose role has no Claim target is refused"
    (withRoot suggestionsSchema "x-archive-target-measure" (Aeson.object
      [ "role" .= ("source" :: Text.Text), "rate" .= ("sample_rate" :: Text.Text), "count" .= ("sample_count" :: Text.Text)
      , "payload" .= ("spans" :: Text.Text), "end" .= ("end_sample" :: Text.Text) ])) suggestions
  refusedOnLoad "a same subject rule comparing a role with itself is refused"
    (withRoot suggestionsSchema "x-archive-same-subject" (Aeson.object
      [ "role" .= ("stream" :: Text.Text), "subject" .= ("artifact" :: Text.Text), "equals" .= ("stream" :: Text.Text) ])) suggestions
  refusedOnLoad "a same subject rule whose role has no target rule is refused"
    (withRoot (withoutRoot suggestionsSchema "x-archive-target-measure") "x-archive-same-subject" (Aeson.object
      [ "role" .= ("run2" :: Text.Text), "subject" .= ("artifact" :: Text.Text), "equals" .= ("source" :: Text.Text) ])) suggestions
  refusedOnLoad "an unknown profile rule is still refused"
    (withRoot runSchema "x-archive-ordered-times" (Aeson.object [])) run
  refusedOnLoad "a non-numeric maximum is refused"
    (withRoot runSchema "maximum" (Aeson.String "64")) run
  check "the sequential spans rule refuses a backwards span the schema accepts"
    (isLeft (validateContractInstance suggestionsSchema
      (instanceOf (decoded (spansPayload 48000 [(10, 10, "music")])) [ref "source" "21", ref "stream" "22", ref "run" "23"])))
    ExitSuccess "" ""

-- | The contracts checks, one case per check or row.
tests :: IO TestTree
tests = fmap (testGroup "contracts") $ sequence
  [ rowsOf "versionedContextChecks" versionedContextChecks
  , rowsOf "audioContractChecks" audioContractChecks
  , rowsOf "reviewContractProfileChecks" reviewContractProfileChecks
  , rowsOf "migrationContractProfileChecks" migrationContractProfileChecks
  , rowsOf "derivationContractProfileChecks" derivationContractProfileChecks
  , rowsOf "windowMatchProfileChecks" windowMatchProfileChecks
  , rowsOf "musicAndCreditProfileChecks" musicAndCreditProfileChecks
  , releaseOf "specification010Checks" specification010Checks
  , releaseOf "rules010Checks" rules010Checks
  , releaseOf "beyondCircle010Checks" beyondCircle010Checks
  , releaseOf "specification011Checks" specification011Checks
  , releaseOf "rules011Checks" rules011Checks
  , rowsOf "captureStartChecks" captureStartChecks
  , rowsOf "contractCatalogChecks" contractCatalogChecks
  ]

captureStartChecks :: Recorder -> IO ()
captureStartChecks check = do
  schema <- installedSchema "spec/contracts/sha256-4455669535970080270e51d738140e63d4f3a5d89b8c8f2781ca610d74b4a194.json"
  let refs = [Aeson.object ["role" .= ("subject" :: Text.Text), "id" .= Text.pack entity, "revision_id" .= Text.pack zeroDigest]]
      instanceOf fields = Aeson.object ["payload" .= Aeson.object fields, "refs" .= refs]
      basis = ["basis" .= ("grammar" :: Text.Text)]
      clock time kind = instanceOf (basis <> ["started" .= (time :: Text.Text), "clock" .= (kind :: Text.Text)])
      judge label expected value = check label ((validateContractInstance schema value == Right ()) == expected) ExitSuccess (show (validateContractInstance schema value)) ""
  judge "capture start admits explicit unknown" True (instanceOf basis)
  judge "capture start admits local leap-day clock" True (clock "2000-02-29T12:34:56" "local")
  judge "capture start admits a zoned clock" True (clock "2001-01-01T12:34:56+01:00" "zoned")
  judge "capture start refuses an impossible calendar date" False (clock "2001-02-29T12:34:56" "local")
  judge "capture start refuses a zoned value labelled local" False (clock "2001-01-01T12:34:56Z" "local")
  judge "capture start refuses a local value labelled zoned" False (clock "2001-01-01T12:34:56" "zoned")
  judge "capture start refuses an unpaired clock" False (instanceOf (basis <> ["clock" .= ("local" :: Text.Text)]))
