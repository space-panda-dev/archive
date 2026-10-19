{-# LANGUAGE OverloadedStrings #-}

-- | Checks of the rules the 0.0.10 amendments add to Door, on records built
-- here: the check beyond the circle (sensitivity, rights with the strictest
-- claim winning and their dates, and co-writers' consent), and the span
-- rules' list and path forms as the timed lyrics bundle states them. The
-- agreement test in world/tests drives the same rules through the real
-- `door transition` and `door admit` against the published fixture.
--
-- Each check is a (label, passed, detail) row; Main asserts them.
module BeyondCircle010 (beyondCircle010Checks) where

import Archive.Door.BeyondCircle (BeyondCircleRule, beyondCircleBlockers, beyondCircleRule)
import Archive.Door.Json (decodeUnique)
import Archive.Door.Reach (Held (..))
import Archive.Door.SpanRules
import Archive.Door.Types (parseDraftEnvelope)
import Data.Aeson (Value (..), object, (.=))
import Data.Aeson.KeyMap qualified as KeyMap
import Data.ByteString.Lazy.Char8 qualified as LazyByteString
import Data.Either (isLeft, isRight)
import Data.Text (Text)
import Data.Text qualified as Text
import Data.Time (Day, addDays, fromGregorian, showGregorian)

type Check = (String, Bool, String)

beyondCircle010Checks :: IO [Check]
beyondCircle010Checks = do
  lyrics <- bundleSchema "b32d0ee70b6bce86d246baf0167cf4b8d24f265c8b46cc3da54b79448be05bb8"
  approval <- bundleSchema "1b4df7d0f573782d67bbc5080447f94714fcd8d97b351c63806cc5e8a6581c60"
  pure (spanChecks lyrics <> ruleChecks approval)

bundleSchema :: String -> IO (KeyMap.KeyMap Value)
bundleSchema digest = do
  bytes <- LazyByteString.readFile ("spec/contracts/sha256-" <> digest <> ".json")
  pure $ case decodeUnique bytes of
    Right (Object bundle) | Just (Object schema) <- KeyMap.lookup "schema" bundle -> schema
    _ -> KeyMap.empty

-- ---------------------------------------------------------------------------
-- The span rules as a timed lyric uses them

spanChecks :: KeyMap.KeyMap Value -> [Check]
spanChecks schema =
  [ ("the timed lyrics bundle's span rules load as lists", isRight sequential && isRight contained, show (sequential, contained))
  , ("the sequential spans rules hold paths", fmap (map sequentialWithin) sequential
      == Right [WithinField "lines", WithinPath ["lines", "words"], WithinPath ["lines", "words"]], show sequential)
  , judge "syllables in order inside their word are accepted" True (lyric [syl 1 0 3 0 400, syl 2 3 7 400 1000])
  , judge "syllables that overlap in time are refused" False (lyric [syl 1 0 3 0 500, syl 2 3 7 400 1000])
  , judge "syllables whose characters overlap are refused" False (lyric [syl 1 0 4 0 400, syl 2 3 7 400 1000])
  , judge "an empty syllable is refused" False (lyric [syl 1 3 3 0 400])
  , judge "a syllable outside its word is refused (contained along a path)" False (lyric [syl 1 0 3 0 400, syl 2 3 7 400 1100])
  , judge "a word without syllables has none to order" True (lyricWords [word 1 0 1000 Nothing])
  , ("a sequential spans rule whose within is an empty path is refused",
      isLeft (sequentialSpansRule (object ["payload" .= ("a" :: Text), "start" .= ("s" :: Text), "end" .= ("e" :: Text), "within" .= ([] :: [Text])])), "")
  , ("an empty list of sequential spans rules is refused", isLeft (sequentialSpansRule (Array mempty)), "")
  , ("a contained spans rule naming one field twice is refused",
      isLeft (containedSpansRule (object ["outer" .= ("w" :: Text), "inner" .= ("w" :: Text), "start" .= ("s" :: Text), "end" .= ("e" :: Text)])), "")
  ]
  where
    sequential = maybe (Left "absent") sequentialSpansRule (KeyMap.lookup "x-archive-sequential-spans" schema)
    contained = maybe (Left "absent") containedSpansRule (KeyMap.lookup "x-archive-contained-spans" schema)
    judge label expected payload = case (sequential, contained, payload) of
      (Right s, Right c, Object fields) ->
        let result = validateSequentialSpans s fields >> validateContainedSpans c fields
        in (label, (if expected then isRight else isLeft) result, show result)
      _ -> (label, False, "the rules did not load")
    lyric syllables = lyricWords [word 1 0 1000 (Just syllables), word 2 1000 2000 Nothing]
    lyricWords ws = object ["lines" .= [object ["line" .= (1 :: Int), "start_sample" .= (0 :: Int), "end_sample" .= (2000 :: Int), "words" .= ws]]]
    word :: Int -> Int -> Int -> Maybe [Value] -> Value
    word n start end syllables = object (["word" .= n, "start_sample" .= start, "end_sample" .= end]
      <> maybe [] (\s -> ["syllables" .= s]) syllables)
    syl :: Int -> Int -> Int -> Int -> Int -> Value
    syl n startChar endChar start end = object
      ["syllable" .= n, "start_char" .= startChar, "end_char" .= endChar, "start_sample" .= start, "end_sample" .= end]

-- ---------------------------------------------------------------------------
-- The check beyond the circle

ruleChecks :: KeyMap.KeyMap Value -> [Check]
ruleChecks schema = case KeyMap.lookup "x-archive-beyond-circle" schema of
  Nothing -> [("the publication approval version 2 bundle declares the beyond-circle rule", False, "")]
  Just value -> case beyondCircleRule value of
    Left problem -> [("the beyond-circle rule loads", False, Text.unpack problem)]
    Right rule -> parseChecks value <> worldChecks rule

parseChecks :: Value -> [Check]
parseChecks value =
  [ ("a beyond-circle rule that checks the circle is refused", isLeft (beyondCircleRule (with "beyond" (toJSONList ["circle", "open"]))), "")
  , ("a beyond-circle rule naming one field twice is refused", isLeft (beyondCircleRule (with "destination" (String "class"))), "")
  , ("a beyond-circle rule with an extra key is refused", isLeft (beyondCircleRule (with "override" (String "yes"))), "")
  ]
  where
    with key new = case value of
      Object fields -> Object (KeyMap.insert key new fields)
      other -> other
    toJSONList = Array . foldMap (pure . String)

worldChecks :: BeyondCircleRule -> [Check]
worldChecks rule =
  [ blocked "the world as built differs from the operator's own approval only by class" False (approve "distribution" [coversOwn]) []
  , blocked "a hold-back is never checked" False (decide "hold_back" "distribution" [coversOwn]) [sensitivityBy operator artifact "RED" 1]
  , blocked "an approval to the circle is not checked here" False (approve "circle" [coversOwn]) [sensitivityBy operator artifact "RED" 1]
  , blocked "a GREEN limit keeps an Artifact from open" True (approve "open" [coversOwn]) [sensitivityBy operator artifact "GREEN" 1]
  , blocked "a GREEN limit lets it reach service" False (approve "service" [coversOwn]) [sensitivityBy operator artifact "GREEN" 1]
  , blocked "a person's later CLEAR lifts their own GREEN" False (approve "open" [coversOwn])
      [sensitivityBy operator artifact "GREEN" 1, sensitivityBy operator artifact "CLEAR" 2]
  , blocked "another person's GREEN still holds" True (approve "open" [coversOwn])
      [sensitivityBy guest artifact "GREEN" 1, sensitivityBy operator artifact "CLEAR" 2]
  , blocked "a limit on what the covered claim pins holds for it" True (approve "service" [coversOwn]) [sensitivityBy operator workOwn "AMBER" 1]
  , blocked "the strictest rights claim wins (NoC-NC beside NoC-US)" True (approve "distribution" [coversOwn]) [rightsOn artifact "NoC-NC" [] []]
  , blocked "NoC-NC permits open" False (approve "open" [coversOwn]) [rightsOn artifact "NoC-NC" [] []]
  , blocked "a personal-use licence keeps an Artifact from service" True (approve "service" [coversOwn]) [rightsOn artifact "personal-use-licence" [] []]
  , blocked "a Work without a rights claim goes no further than service" True (approve "open" [coversOther]) []
  , blocked "a Work without a rights claim may reach service" False (approve "service" [coversOther]) []
  , blocked "InC held by someone else permits only service" True (approve "open" [coversOther]) [rightsOn workOther "InC" [guest] []]
  , blocked "rights whose until has passed permit only service" True (approve "distribution" [coversOwn])
      [rightsOn artifact "NoC-US" [] ["until" .= day (-1)]]
  , blocked "rights in force until today still permit" False (approve "distribution" [coversOwn])
      [rightsOn artifact "NoC-US" [] ["until" .= day 0]]
  , blocked "rights not yet in force permit only service" True (approve "distribution" [coversOwn])
      [rightsOn artifact "NoC-US" [] ["from" .= day 1]]
  , blocked "a co-written Work without the co-writer's consent is refused" True (approve "service" [coversCo]) [origin]
  , blocked "the co-writer's consent to this destination, covering the revision, lets it go" False (approve "service" [coversCo])
      [origin, consent "approve" destination [coversCo]]
  , blocked "a consent to another destination does not" True (approve "service" [coversCo])
      [origin, consent "approve" "elsewhere" [coversCo]]
  , blocked "a refusal does not" True (approve "service" [coversCo]) [origin, consent "refuse" destination [coversCo]]
  , blocked "a consent covering another revision does not" True (approve "service" [coversCo])
      [origin, consent "approve" destination [coversOwn]]
  , blocked "a Work the operator wrote alone needs no consent" False (approve "service" [coversCo]) [ownOrigin]
  , named "the refusal names the label, statement, writer, and revision"
      (approve "open" [coversCo]) [origin, sensitivityBy operator workCo "GREEN" 1, rightsOn workCo "personal-use-licence" [] []]
      ["GREEN", "personal-use-licence", fst guest, snd coversCo]
  ]
  where
    blocked label expected draft extra = case draft of
      Left problem -> (label, False, "the draft does not parse: " <> Text.unpack problem)
      Right parsed ->
        let blockers = beyondCircleBlockers today (fst operator) (base <> heldAll extra) rule parsed
        in (label, not (null blockers) == expected, show blockers)
    named label draft extra wanted = case draft of
      Left problem -> (label, False, Text.unpack problem)
      Right parsed ->
        let blockers = Text.unwords (beyondCircleBlockers today (fst operator) (base <> heldAll extra) rule parsed)
        in (label, all (`Text.isInfixOf` blockers) wanted, Text.unpack blockers)
    approve = decide "approve"
    decide decision exposure covered = parseDraftEnvelope (draftValue (fst approvalObject) "claim" operator
      (object ["decision" .= (decision :: Text), "destination" .= destination, "class" .= (exposure :: Text)])
      (ref "authorizer" operator : map (ref "covers") covered) [citation "archive.transition/publication_approval" "2"])
    base = heldAll
      [ entity operator "person", entity guest "person", entity workOwn "musical_work", entity workOther "musical_work"
      , entity workCo "musical_work", (artifact, artifactValue)
      , (coversOwn, claimValue coversOwn [ref "work" workOwn, ref "source" artifact])
      , (coversOther, claimValue coversOther [ref "work" workOther])
      , (coversCo, claimValue coversCo [ref "work" workCo])
      , (rightsArtifact, rightsValue rightsArtifact artifact "NoC-US" [] [])
      , (rightsOwn, rightsValue rightsOwn workOwn "InC" [operator] [])
      , (rightsCo, rightsValue rightsCo workCo "InC" [operator] [])
      ]
    sensitivityBy agent subject label n = (held (100 + n) 'a', draftValue (fst (held (100 + n) 'a')) "claim" agent
      (object ["label" .= (label :: Text)]) [ref "subject" subject] [citation "archive.claim/sensitivity" "1"])
    rightsOn subject statement holders extra = let key = held 120 'b' in (key, rightsValue key subject statement holders extra)
    rightsValue key subject statement holders extra = draftValue (fst key) "claim" operator
      (object (["statement" .= (statement :: Text)] <> extra))
      (ref "subject" subject : map (ref "holder") holders) [citation "archive.claim/rights" "1"]
    origin = (originKey, draftValue (fst originKey) "claim" operator (object ["origin" .= ("co_written" :: Text)])
      [ref "work" workCo, ref "writer" operator, ref "writer" guest] [citation "archive.claim/work_origin" "1"])
    ownOrigin = (originKey, draftValue (fst originKey) "claim" operator (object ["origin" .= ("own" :: Text)])
      [ref "work" workCo, ref "writer" operator] [citation "archive.claim/work_origin" "1"])
    consent decision to covered = (held 131 'c', draftValue (fst (held 131 'c')) "claim" guest
      (object ["decision" .= (decision :: Text), "destination" .= (to :: Text)])
      (ref "origin" originKey : map (ref "covers") covered) [citation "archive.claim/sharing_consent" "1"])
    claimValue key refs = draftValue (fst key) "claim" operator (object ["label" .= ("covered" :: Text)]) refs
      [citation "archive.claim/lead_sheet" "1"]
    entity key kind = (key, draftValue (fst key) "entity" operator (object ["kind" .= (kind :: Text), "label" .= kind]) [] [])
    artifactValue = draftValue (fst artifact) "artifact" operator
      (object ["content" .= object ["digest" .= revisionOf 'f', "size" .= (1 :: Int)]]) [] []
    day :: Integer -> Text
    day offset = Text.pack (showGregorian (addDays offset today))

today :: Day
today = fromGregorian 2026 10 19

destination :: Text
destination = "A streaming service"

heldAll :: [((Text, Text), Value)] -> [Held]
heldAll = zipWith one [1 :: Int ..]
  where
    one n ((objectId, revision), value) = case parseDraftEnvelope value of
      Right draft -> Held objectId revision (stamp n) draft
      Left problem -> error (Text.unpack problem)
    stamp n = "2026-01-01T00:00:" <> Text.justifyRight 2 '0' (Text.pack (show (n `mod` 60))) <> "Z"

-- Objects and their one revision in this world.
operator, guest, workOwn, workOther, workCo, artifact, coversOwn, coversOther, coversCo,
  rightsArtifact, rightsOwn, rightsCo, originKey, approvalObject :: (Text, Text)
operator = held 1 'a'
guest = held 2 'b'
workOwn = held 3 'c'
workOther = held 4 'd'
workCo = held 5 'e'
artifact = held 6 '1'
coversOwn = held 7 '2'
coversOther = held 8 '3'
coversCo = held 9 '4'
rightsArtifact = held 10 '5'
rightsOwn = held 11 '6'
rightsCo = held 12 '7'
originKey = held 13 '8'
approvalObject = held 14 '9'

held :: Int -> Char -> (Text, Text)
held number digit = ("urn:uuid:00000000-0000-4000-8000-" <> Text.justifyRight 12 '0' (Text.pack (show number)),
  "sha256:" <> Text.replicate 60 (Text.singleton digit) <> Text.justifyRight 4 '0' (Text.pack (show number)))

revisionOf :: Char -> Text
revisionOf digit = "sha256:" <> Text.replicate 64 (Text.singleton digit)

ref :: Text -> (Text, Text) -> Value
ref role (objectId, revision) = object ["role" .= role, "id" .= objectId, "revision_id" .= revision]

citation :: Text -> Text -> Value
citation identifier version = object ["id" .= identifier, "version" .= version, "digest" .= revisionOf '0']

draftValue :: Text -> Text -> (Text, Text) -> Value -> [Value] -> [Value] -> Value
draftValue objectId shape agent payload refs contracts = object (
  [ "spec" .= ("archive/0.0.10" :: Text), "object_id" .= objectId, "shape" .= shape
  , "by" .= ("pkg:hackage/archive@0.0.1.0" :: Text), "agent" .= fst agent, "payload" .= payload
  , "refs" .= refs, "supersedes" .= ([] :: [Value]) ]
  <> ["contracts" .= contracts | not (null contracts)])
