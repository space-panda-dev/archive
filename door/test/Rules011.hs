{-# LANGUAGE OverloadedStrings #-}

-- | Checks of the 0.0.11 rules that read other records (D-19, ADR 0113)
-- through admission's own entry, 'validateReleaseAdmission', on records built
-- here: a lead sheet version 2's underlays place exactly its lyric document
-- under a melody it pins, a version 1 lead sheet's next revision may be
-- version 2, and ADR 0088's member-contract rule holds for melody version 2.
--
-- Each check is a (label, passed, detail) row; Main makes each a test case.
module Rules011 (rules011Checks) where

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

rules011Checks :: IO [Check]
rules011Checks = do
  catalog <- either (const []) toList <$> loadContractCatalog "archive/0.0.11"
  let bundleOf name version = case [entry | entry@(i, v, _) <- catalog, i == name, v == version] of
        [(identifier, version', digest)] -> do
          bytes <- LazyByteString.readFile ("spec/contracts/sha256-" <> drop 7 (Text.unpack digest) <> ".json")
          pure (either (const Nothing) Just (decodeUnique bytes >>= either (Left . Text.unpack) Right
            . validateContractBundle identifier version' digest))
        _ -> pure Nothing
  leadSheet <- bundleOf "archive.claim/lead_sheet" "2"
  melody <- bundleOf "archive.claim/melody" "2"
  pure (maybe [("the lead sheet version 2 and melody version 2 bundles load", False, "")] id
    (checks <$> leadSheet <*> melody))

checks :: ContractBundle -> ContractBundle -> [Check]
checks leadSheet melody =
  [ judge "a lead sheet whose underlay places its lyrics under a melody it pins is admitted" True leadSheet
      (sheet [ref "underlay" underlayHere] [])
  , judge "an underlay of another lyric document is refused" False leadSheet
      (sheet [ref "underlay" underlayOtherDocument] [])
  , judge "an underlay under a melody the lead sheet does not pin is refused" False leadSheet
      (sheet [ref "underlay" underlayElsewhere] [])
  , judge "a version 1 lead sheet's next revision may be version 2, keeping its agent and Work" True leadSheet
      (sheet [] [ref "supersedes" previousSheet])
  , judge "a version 2 revision of another person's version 1 lead sheet is refused" False leadSheet
      (sheetBy otherPerson [] [ref "supersedes" previousSheet])
  , judge "a revision of one's own sketch is admitted" True melody
      (sketch person)
  , judge "a revision of another person's sketch is refused (ADR 0088): completing it is a correction" False melody
      (sketch otherPerson)
  ]
  where
    judge label expected bundle draft = case draft of
      Left problem -> (label, False, "the draft does not parse: " <> Text.unpack problem)
      Right parsed ->
        let result = validateReleaseAdmission world parsed [bundle]
        in (label, (if expected then isRight else isLeft) result, show result)
    world = map heldOf
      [ entity person "person", entity otherPerson "person", entity work "musical_work"
      , (document, artifactDraft document 'f'), (otherDocument, artifactDraft otherDocument 'e')
      , (melodyClaim, melodyValue melodyClaim person [])
      , (otherMelody, melodyValue otherMelody person [])
      , (underlayHere, underlayValue underlayHere melodyClaim document)
      , (underlayOtherDocument, underlayValue underlayOtherDocument melodyClaim otherDocument)
      , (underlayElsewhere, underlayValue underlayElsewhere otherMelody document)
      , (previousSheet, draftWith "0.0.10" (fst previousSheet) "claim" person (object ["label" .= ("A chart" :: Text)])
          [ref "work" work, ref "melody" melodyClaim] [] [citation "archive.claim/lead_sheet" "1"])
      ]
    heldOf ((objectId, revision), value) = case parseDraftEnvelope value of
      Right draft -> (objectId, revision, draft)
      Left problem -> error (Text.unpack problem)
    sheet = sheetBy person
    sheetBy agent refs supersedes = parseDraftEnvelope (draftWith "0.0.11" (fst previousSheet) "claim" agent
      (object ["label" .= ("A chart" :: Text)])
      ([ref "work" work, ref "melody" melodyClaim, ref "lyrics" document] <> refs) supersedes
      [citation "archive.claim/lead_sheet" "2"])
    sketch agent = parseDraftEnvelope (melodyValue melodyClaim agent [ref "supersedes" melodyClaim])
    melodyValue key agent supersedes = draftWith "0.0.11" (fst key) "claim" agent
      (object ["notes" .= [object ["pitch" .= (64 :: Int)]]]) [ref "work" work] supersedes
      [citation "archive.claim/melody" "2"]
    underlayValue key melodyRef documentRef = draftWith "0.0.11" (fst key) "claim" person
      (object ["lines" .= [object ["line" .= (1 :: Int), "words" .= [object ["word" .= (1 :: Int), "items" .= [0 :: Int]]]]]])
      [ref "melody" melodyRef, ref "document" documentRef] [] [citation "archive.claim/underlay" "1"]
    entity key kind = (key, draftWith "0.0.11" (fst key) "entity" person (object ["kind" .= (kind :: Text), "label" .= kind]) [] [] [])
    artifactDraft key digit = draftWith "0.0.11" (fst key) "artifact" person
      (object ["content" .= object ["digest" .= revisionOf digit, "size" .= (1 :: Int)]]) [] [] []

-- Objects and their one revision in this world.
person, otherPerson, work, document, otherDocument, melodyClaim, otherMelody, underlayHere,
  underlayOtherDocument, underlayElsewhere, previousSheet :: (Text, Text)
person = held 1 'a'
otherPerson = held 2 'b'
work = held 3 'c'
document = held 4 'd'
otherDocument = held 5 '1'
melodyClaim = held 6 '2'
otherMelody = held 7 '3'
underlayHere = held 8 '4'
underlayOtherDocument = held 9 '5'
underlayElsewhere = held 10 '6'
previousSheet = held 11 '7'

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
