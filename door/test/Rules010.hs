{-# LANGUAGE OverloadedStrings #-}

-- | Checks of the 0.0.10 rules that read other records (D-18) through
-- admission's own entry, 'validateReleaseAdmission', on records built here:
-- what a supersession keeps, ADR 0088's member-contract rule, and a placed
-- track's recording. The fixture agreement test (world/tests) drives every
-- other rule through the real Door; these are the rules a new object, which
-- that test proposes, never reaches.
--
-- Each check is a (label, passed, detail) row; Main asserts them.
module Rules010 (rules010Checks) where

import Archive.Door.Catalog (loadContractCatalog)
import Archive.Door.Contracts (ContractBundle, validateContractBundle)
import Archive.Door.Json (decodeUnique)
import Archive.Door.ReleaseAdmission (validateReleaseAdmission)
import Archive.Door.Types (parseDraftEnvelope)
import Data.Aeson (Value (..), object, (.=))
import qualified Data.ByteString.Lazy.Char8 as LazyByteString
import Data.Either (isLeft, isRight)
import Data.Foldable (toList)
import Data.Text (Text)
import qualified Data.Text as Text

type Check = (String, Bool, String)

rules010Checks :: IO [Check]
rules010Checks = do
  catalog <- either (const []) toList <$> loadContractCatalog "archive/0.0.10"
  let bundleOf name version = case [entry | entry@(i, v, _) <- catalog, i == name, v == version] of
        [(identifier, version', digest)] -> do
          bytes <- LazyByteString.readFile ("spec/contracts/sha256-" <> drop 7 (Text.unpack digest) <> ".json")
          pure (either (const Nothing) Just (decodeUnique bytes >>= either (Left . Text.unpack) Right
            . validateContractBundle identifier version' digest))
        _ -> pure Nothing
  leadSheet <- bundleOf "archive.claim/lead_sheet" "1"
  chords <- bundleOf "archive.claim/chord_stacks" "1"
  audio <- bundleOf "archive.claim/recording_audio" "1"
  pure (maybe [("the lead sheet, chord stacks, and recording audio bundles load", False, "")] id
    (checks <$> leadSheet <*> chords <*> audio))

checks :: ContractBundle -> ContractBundle -> ContractBundle -> [Check]
checks leadSheet chords audio =
  [ judge "a lead sheet revision that keeps its agent, Work, and source is admitted" True leadSheet
      (sheet "0.0.10" person [ref "work" workA] [superseding])
  , judge "a lead sheet revision of another Work is refused" False leadSheet
      (sheet "0.0.10" person [ref "work" workB] [superseding])
  , judge "a lead sheet revision by another person is refused" False leadSheet
      (sheet "0.0.10" otherPerson [ref "work" workA] [superseding])
  , judge "a lead sheet revision that adds a source is refused" False leadSheet
      (sheet "0.0.10" person [ref "work" workA, ref "chords" sequenceClaim, ref "source" recording] [superseding])
  , judge "a 0.0.10 chord stacks revision by its own asserter is admitted" True chords
      (stacks "0.0.10" person)
  , judge "a 0.0.10 chord stacks revision by another agent is refused (ADR 0088)" False chords
      (stacks "0.0.10" otherPerson)
  , judge "a 0.0.9 chord stacks revision by another agent is not held to ADR 0088" True chords
      (stacks "0.0.9" otherPerson)
  , judge "audio placed on the track that carries its recording is admitted" True audio
      (placed recordingEntity 1)
  , judge "audio placed on a track the tracklist lists, carrying another recording, is refused" False audio
      (placed otherRecording 1)
  , judge "audio placed on a track the tracklist does not list is refused" False audio
      (placed recordingEntity 2)
  ]
  where
    judge label expected bundle draft = case draft of
      Left problem -> (label, False, "the draft does not parse: " <> Text.unpack problem)
      Right parsed ->
        let result = validateReleaseAdmission world parsed [bundle]
        in (label, (if expected then isRight else isLeft) result, show result)
    world = map heldOf
      [ entity person "person", entity otherPerson "person", entity workA "musical_work"
      , entity workB "musical_work", entity recordingEntity "recording", entity otherRecording "recording"
      , (recording, artifactDraft recording)
      , (sequenceClaim, draftValue sequenceClaim "claim" person (object ["entries" .= ([] :: [Value])])
          [ref "work" workA] [] [citation "archive.claim/chord_sequence" "1"])
      , (previousSheet, sheetValue "0.0.10" person [ref "work" workA] [])
      , (previousStacks, stacksValue "0.0.10" person [])
      , (tracklist, draftValue tracklist "claim" person
          (object ["media" .= [object ["position" .= (1 :: Int), "tracks" .= [object ["position" .= (1 :: Int)]]]]])
          [ref "release" workB, ref "track" recordingEntity] [] [citation "archive.claim/tracklist" "1"])
      ]
    heldOf ((objectId, revision), value) = case parseDraftEnvelope value of
      Right draft -> (objectId, revision, draft)
      Left problem -> error (Text.unpack problem)
    superseding = ref "supersedes" previousSheet
    sheet spec agent refs supersedes = parseDraftEnvelope (sheetValue spec agent refs supersedes)
    sheetValue spec agent refs supersedes =
      draftWith spec (fst previousSheet) "claim" agent (object ["label" .= ("A chart" :: Text)]) refs supersedes
        [citation "archive.claim/lead_sheet" "1"]
    stacks spec agent = parseDraftEnvelope (stacksValue spec agent [ref "supersedes" previousStacks])
    stacksValue spec agent supersedes =
      draftWith spec (fst previousStacks) "claim" agent (object ["sample_rate" .= (8000 :: Int), "spans" .= ([] :: [Value])])
        [] supersedes [citation "archive.claim/chord_stacks" "1"]
    placed recordingRef track = parseDraftEnvelope (draftWith "0.0.10" (fst newObject) "claim" person
      (object ["extent" .= ("whole" :: Text), "medium" .= (1 :: Int), "track" .= (track :: Int)])
      [ref "recording" recordingRef, ref "audio" recording, ref "tracklist" tracklist] []
      [citation "archive.claim/recording_audio" "1"])
    entity key kind = (key, draftValue key "entity" person (object ["kind" .= (kind :: Text), "label" .= kind]) [] [] [])
    artifactDraft key = draftValue key "artifact" person
      (object ["content" .= object ["digest" .= revisionOf 'f', "size" .= (1 :: Int)]]) [] [] []
    draftValue key shape agent payload refs supersedes contracts =
      draftWith "0.0.10" (fst key) shape agent payload refs supersedes contracts

-- Objects and their one revision in this world.
person, otherPerson, workA, workB, recordingEntity, otherRecording, recording, sequenceClaim,
  previousSheet, previousStacks, tracklist, newObject :: (Text, Text)
person = held 1 'a'
otherPerson = held 2 'b'
workA = held 3 'c'
workB = held 4 'd'
recordingEntity = held 5 'e'
otherRecording = held 6 '1'
recording = held 7 '2'
sequenceClaim = held 8 '3'
previousSheet = held 9 '4'
previousStacks = held 10 '5'
tracklist = held 11 '6'
newObject = held 12 '7'

held :: Int -> Char -> (Text, Text)
held number digit = ("urn:uuid:00000000-0000-4000-8000-" <> Text.justifyRight 12 '0' (Text.pack (show number)), revisionOf digit)

revisionOf :: Char -> Text
revisionOf digit = "sha256:" <> Text.replicate 64 (Text.singleton digit)

ref :: Text -> (Text, Text) -> Value
ref role (objectId, revision) = object ["role" .= role, "id" .= objectId, "revision_id" .= revision]

citation :: Text -> Text -> Value
citation identifier version = object ["id" .= identifier, "version" .= version, "digest" .= revisionOf '0']

draftWith :: Text -> Text -> Text -> (Text, Text) -> Value -> [Value] -> [Value] -> [Value] -> Value
draftWith spec objectId shape agent payload refs supersedes contracts = object (
  [ "spec" .= ("archive/" <> spec), "object_id" .= objectId, "shape" .= shape
  , "by" .= ("pkg:hackage/archive@0.0.1.0" :: Text), "agent" .= fst agent, "payload" .= payload
  , "refs" .= refs, "supersedes" .= supersedes ]
  <> ["contracts" .= contracts | not (null contracts)])
