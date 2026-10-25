{-# LANGUAGE OverloadedStrings #-}

-- | Checks that admit claims under the world, review, migration, derivation and
-- window-match contracts through the real `door admit`, so their profile
-- rules are exercised against admitted records.
module ContractAdmissionChecks
  ( tests
  ) where

import qualified Data.Aeson as Aeson
import qualified Data.Aeson.KeyMap as KeyMap
import qualified Data.ByteString.Lazy.Char8 as LazyByteString
import Data.List (isPrefixOf)
import Fixtures
import Support
import System.Environment (lookupEnv)
import System.Exit (ExitCode (ExitSuccess))
import System.Process (CreateProcess (cwd), proc, readCreateProcessWithExitCode)
import Test.Tasty (TestTree, testGroup)

worldContractAdmissionChecks :: FilePath -> IO ()
worldContractAdmissionChecks parent = do
  (root, archiveId, initOutput) <- newProposalArchive parent "world-contracts"
  let workNamespace = "archive.core/entity"
      workName = "world-fixture-paper-lanterns"
  workId <- allocatedObjectId archiveId workNamespace workName
  let work = envelope workId "entity" "{\"kind\":\"musical_work\",\"label\":\"Paper Lanterns\"}" "[]" ""
  workRevision <- revisionOf work
  workProposal <- writeAdmissionProposal root "world-work"
    (manifestFor ["records/work.json"] [(workId, workNamespace, workName)]
      [(workId, Nothing)] []) ["records/work.json", work]
  (workCode, workOut, workErr) <- doorCommandAt root ["admit", "--proposal", workProposal] ""
  assert "door admit commits a musical_work entity"
    (workCode == ExitSuccess && contains workRevision workOut)
    workCode workOut workErr

  correspondenceContract <- contractCitation root "archive.claim/work_musicbrainz_correspondence"
  let correspondenceNamespace = "archive.claim/work_musicbrainz_correspondence"
      correspondenceName = "world-fixture-paper-lanterns-musicbrainz"
  correspondenceId <- allocatedObjectId archiveId correspondenceNamespace correspondenceName
  let correspondence = musicBrainzClaim correspondenceId workId workRevision correspondenceContract
  correspondenceRevision <- canonicalRevisionOf correspondence
  correspondenceProposal <- writeAdmissionProposal root "world-correspondence"
    (manifestFor ["records/correspondence.json"]
      [(correspondenceId, correspondenceNamespace, correspondenceName)]
      [(correspondenceId, Nothing)] [])
    ["records/correspondence.json", correspondence]
  (mappingCode, mappingOut, mappingErr) <- doorCommandAt root
    ["admit", "--proposal", correspondenceProposal] ""
  assert "door admit commits a pinned MusicBrainz correspondence claim"
    (mappingCode == ExitSuccess && contains correspondenceRevision mappingOut)
    mappingCode mappingOut mappingErr

  performanceContract <- contractCitation root "archive.claim/performance_work"
  let performanceNamespace = "archive.claim/performance_work"
      performanceName = "world-fixture-performance-claim-shape-only"
  performanceId <- allocatedObjectId archiveId performanceNamespace performanceName
  -- The target is the already admitted correspondence claim solely to exercise
  -- the structural Claim-target rule. It is not evidence of a performance.
  let performance = performanceWorkClaim performanceId correspondenceId correspondenceRevision
        workId workRevision performanceContract
  performanceRevision <- canonicalRevisionOf performance
  performanceProposal <- writeAdmissionProposal root "world-performance-claim"
    (manifestFor ["records/performance.json"]
      [(performanceId, performanceNamespace, performanceName)]
      [(performanceId, Nothing)] [])
    ["records/performance.json", performance]
  (performanceOkCode, performanceOkOut, performanceOkErr) <- doorCommandAt root
    ["admit", "--proposal", performanceProposal] ""
  assert "door admit commits the pinned performance_work claim structure"
    (performanceOkCode == ExitSuccess && contains performanceRevision performanceOkOut)
    performanceOkCode performanceOkOut performanceOkErr

  let invalidPerformanceName = "world-fixture-invalid-performance-pin"
  invalidPerformanceId <- allocatedObjectId archiveId performanceNamespace invalidPerformanceName
  let invalidPerformance = performanceWorkClaim invalidPerformanceId workId workRevision
        workId workRevision performanceContract
  invalidPerformanceProposal <- writeAdmissionProposal root "world-invalid-performance"
    (manifestFor ["records/performance.json"]
      [(invalidPerformanceId, performanceNamespace, invalidPerformanceName)]
      [(invalidPerformanceId, Nothing)] [])
    ["records/performance.json", invalidPerformance]
  (performanceCode, performanceOut, performanceErr) <- doorCommandAt root
    ["admit", "--proposal", invalidPerformanceProposal] ""
  assert "performance_work rejects an Entity in the performance claim role"
    (performanceCode /= ExitSuccess && contains "performance reference must target a Claim revision" (performanceErr <> performanceOut))
    performanceCode performanceOut performanceErr

  operatorId <- requiredField "operator_id" initOutput
  operatorRevision <- recordRevisionForObject root operatorId
  let invalidMappingName = "world-fixture-wrong-work-kind"
  invalidMappingId <- allocatedObjectId archiveId correspondenceNamespace invalidMappingName
  let invalidMapping = musicBrainzClaim invalidMappingId operatorId operatorRevision correspondenceContract
  invalidMappingProposal <- writeAdmissionProposal root "world-invalid-work-kind"
    (manifestFor ["records/correspondence.json"]
      [(invalidMappingId, correspondenceNamespace, invalidMappingName)]
      [(invalidMappingId, Nothing)] [])
    ["records/correspondence.json", invalidMapping]
  (kindCode, kindOut, kindErr) <- doorCommandAt root
    ["admit", "--proposal", invalidMappingProposal] ""
  assert "work correspondence rejects a non-musical_work entity"
    (kindCode /= ExitSuccess && contains "work reference must target an Entity revision of kind musical_work" (kindErr <> kindOut))
    kindCode kindOut kindErr

  audioId <- allocatedObjectId archiveId "archive.core/artifact" "world-fixture-audio"
  audioRevision <- commitArtifactFixture root audioId
  selectionContract <- contractCitation root "archive.claim/audio_selection"
  let selectionNamespace = "archive.claim/audio_selection"
      selectionName = "world-fixture-selection"
  selectionId <- allocatedObjectId archiveId selectionNamespace selectionName
  let selection = envelope selectionId "claim"
        "{\"pieces\":[{\"start_sample\":10,\"end_sample\":200,\"sample_rate\":48000}]}"
        ("[" <> transitionReference "piece" audioId audioRevision <> "]")
        (",\"contracts\":[" <> citationJson selectionContract <> "]")
  selectionRevision <- canonicalRevisionOf selection
  selectionProposal <- writeAdmissionProposal root "world-audio-selection"
    (manifestFor ["records/selection.json"]
      [(selectionId, selectionNamespace, selectionName)] [(selectionId, Nothing)] [])
    ["records/selection.json", selection]
  (selectionCode, selectionOut, selectionErr) <- doorCommandAt root
    ["admit", "--proposal", selectionProposal] ""
  assert "door admit commits a selection pinned to exact Artifact audio"
    (selectionCode == ExitSuccess && contains selectionRevision selectionOut)
    selectionCode selectionOut selectionErr
  wrongSelectionId <- allocatedObjectId archiveId selectionNamespace "world-fixture-wrong-selection-target"
  let wrongSelection = replace selectionId wrongSelectionId
        (replace audioId workId (replace audioRevision workRevision selection))
  wrongSelectionProposal <- writeAdmissionProposal root "world-wrong-selection-target"
    (manifestFor ["records/selection.json"]
      [(wrongSelectionId, selectionNamespace, "world-fixture-wrong-selection-target")]
      [(wrongSelectionId, Nothing)] [])
    ["records/selection.json", wrongSelection]
  (wrongSelectionCode, wrongSelectionOut, wrongSelectionErr) <- doorCommandAt root
    ["admit", "--proposal", wrongSelectionProposal] ""
  assert "selection rejects an Entity instead of an Artifact source"
    (wrongSelectionCode /= ExitSuccess
      && contains "piece reference must target an Artifact revision"
        (wrongSelectionErr <> wrongSelectionOut))
    wrongSelectionCode wrongSelectionOut wrongSelectionErr

  occurrenceContract <- contractCitation root "archive.claim/song_occurrence"
  let occurrenceNamespace = "archive.claim/song_occurrence"
      occurrenceName = "world-fixture-occurrence"
  occurrenceId <- allocatedObjectId archiveId occurrenceNamespace occurrenceName
  let occurrence = envelope occurrenceId "claim" "{}"
        ("[" <> transitionReference "selection" selectionId selectionRevision <> "]")
        (",\"contracts\":[" <> citationJson occurrenceContract <> "]")
  occurrenceRevision <- canonicalRevisionOf occurrence
  occurrenceProposal <- writeAdmissionProposal root "world-song-occurrence"
    (manifestFor ["records/occurrence.json"]
      [(occurrenceId, occurrenceNamespace, occurrenceName)] [(occurrenceId, Nothing)] [])
    ["records/occurrence.json", occurrence]
  (occurrenceCode, occurrenceOut, occurrenceErr) <- doorCommandAt root
    ["admit", "--proposal", occurrenceProposal] ""
  assert "door admit commits an occurrence pinned to an audio selection"
    (occurrenceCode == ExitSuccess && contains occurrenceRevision occurrenceOut)
    occurrenceCode occurrenceOut occurrenceErr
  wrongOccurrenceId <- allocatedObjectId archiveId occurrenceNamespace "world-fixture-wrong-occurrence-target"
  let wrongOccurrence = replace occurrenceId wrongOccurrenceId
        (replace selectionId correspondenceId
          (replace selectionRevision correspondenceRevision occurrence))
  wrongOccurrenceProposal <- writeAdmissionProposal root "world-wrong-occurrence-target"
    (manifestFor ["records/occurrence.json"]
      [(wrongOccurrenceId, occurrenceNamespace, "world-fixture-wrong-occurrence-target")]
      [(wrongOccurrenceId, Nothing)] [])
    ["records/occurrence.json", wrongOccurrence]
  (wrongOccurrenceCode, wrongOccurrenceOut, wrongOccurrenceErr) <- doorCommandAt root
    ["admit", "--proposal", wrongOccurrenceProposal] ""
  assert "occurrence rejects a Claim without the audio selection contract"
    (wrongOccurrenceCode /= ExitSuccess
      && contains "selection reference must target a Claim citing archive.claim/audio_selection version 1"
        (wrongOccurrenceErr <> wrongOccurrenceOut))
    wrongOccurrenceCode wrongOccurrenceOut wrongOccurrenceErr

  performanceV2 <- contractCitationVersion root "archive.claim/performance_work" "2"
  let typedName = "world-fixture-typed-occurrence-work"
  typedId <- allocatedObjectId archiveId performanceNamespace typedName
  let typed = performanceWorkClaim typedId occurrenceId occurrenceRevision
        workId workRevision performanceV2
  typedRevision <- canonicalRevisionOf typed
  typedProposal <- writeAdmissionProposal root "world-typed-occurrence-work"
    (manifestFor ["records/link.json"]
      [(typedId, performanceNamespace, typedName)] [(typedId, Nothing)] [])
    ["records/link.json", typed]
  (typedCode, typedOut, typedErr) <- doorCommandAt root
    ["admit", "--proposal", typedProposal] ""
  assert "door admit commits typed occurrence-to-Work version 2 link"
    (typedCode == ExitSuccess && contains typedRevision typedOut)
    typedCode typedOut typedErr

  invalidTypedId <- allocatedObjectId archiveId performanceNamespace "world-fixture-v2-wrong-target"
  let invalidTyped = performanceWorkClaim invalidTypedId correspondenceId correspondenceRevision
        workId workRevision performanceV2
  invalidTypedProposal <- writeAdmissionProposal root "world-v2-wrong-target"
    (manifestFor ["records/link.json"]
      [(invalidTypedId, performanceNamespace, "world-fixture-v2-wrong-target")]
      [(invalidTypedId, Nothing)] [])
    ["records/link.json", invalidTyped]
  (invalidTypedCode, invalidTypedOut, invalidTypedErr) <- doorCommandAt root
    ["admit", "--proposal", invalidTypedProposal] ""
  assert "version 2 rejects an arbitrary Claim as a song occurrence"
    (invalidTypedCode /= ExitSuccess
      && contains "performance reference must target a Claim citing archive.claim/song_occurrence version 1"
        (invalidTypedErr <> invalidTypedOut))
    invalidTypedCode invalidTypedOut invalidTypedErr

  -- World's incremental refresh (ADR 0055) is proved against a real cut of
  -- this chain: the export here, and the export since here at the end.
  cut <- trim <$> gitHead root
  (cutCode, cutOut, cutErr) <- readCreateProcessWithExitCode
    ((proc "archive" ["export"]) {cwd = Just root}) ""
  assert "archive export carries the World chain before the review records"
    (cutCode == ExitSuccess && contains ("\"snapshot\":\"" <> cut <> "\"") cutOut)
    cutCode cutOut cutErr

  reviewRevisions <- reviewChainAdmissions root archiveId (audioId, audioRevision) (workId, workRevision)

  -- World reads only `archive export`. Admission above proves Door committed
  -- the chain; this proves every admitted revision crosses that boundary.
  -- Setting ARCHIVE_TEST_WORLD_EXPORT captures this output as World's
  -- world/tests/fixtures/door-world-chain-export.json.
  (exportCode, exportOut, exportErr) <- readCreateProcessWithExitCode
    ((proc "archive" ["export"]) {cwd = Just root}) ""
  assert "archive export carries the admitted World chain at 0.0.5"
    (exportCode == ExitSuccess
      && contains "\"status\":\"ok\"" exportOut
      && contains "\"spec\":\"archive/0.0.12\"" exportOut
      && all (`contains` exportOut)
        ([ workRevision, correspondenceRevision, performanceRevision, audioRevision
         , selectionRevision, occurrenceRevision, typedRevision ] <> reviewRevisions))
    exportCode exportOut exportErr
  (sinceCode, sinceOut, sinceErr) <- readCreateProcessWithExitCode
    ((proc "archive" ["export", "--since", cut]) {cwd = Just root}) ""
  assert "archive export --since carries the review records and pins the earlier chain"
    (sinceCode == ExitSuccess
      && contains ("\"since\":\"" <> cut <> "\"") sinceOut
      && all (`contains` sinceOut) reviewRevisions
      && all (`contains` sinceOut) [audioRevision, workRevision]
      && not (any (`contains` sinceOut) [correspondenceRevision, performanceRevision, typedRevision]))
    sinceCode sinceOut sinceErr
  -- Setting ARCHIVE_TEST_WORLD_CUT_EXPORT and ARCHIVE_TEST_WORLD_SINCE_EXPORT
  -- captures the cut and the export since it as World's
  -- door-world-chain-cut-export.json and door-world-chain-since-export.json.
  mapM_ (\(name, output) -> lookupEnv name >>= mapM_ (`writeFile` output))
    [ ("ARCHIVE_TEST_WORLD_EXPORT", exportOut)
    , ("ARCHIVE_TEST_WORLD_CUT_EXPORT", cutOut)
    , ("ARCHIVE_TEST_WORLD_SINCE_EXPORT", sinceOut)
    ]

performanceWorkClaim :: String -> String -> String -> String -> String
                     -> (String, String, String) -> String
performanceWorkClaim objectId performanceId performanceRevision workId workRevision
    (identifier, version, digest) =
  envelope objectId "claim" "{}"
    ("[{\"role\":\"performance\",\"id\":" <> jsonString performanceId
      <> ",\"revision_id\":" <> jsonString performanceRevision <> "},{\"role\":\"work\",\"id\":"
      <> jsonString workId <> ",\"revision_id\":" <> jsonString workRevision <> "}]")
    (",\"contracts\":[{\"id\":" <> jsonString identifier
      <> ",\"version\":" <> jsonString version
      <> ",\"digest\":" <> jsonString digest <> "}]")

-- | The refusal code of a Door reply line, @{"ok": false, "error": {...}}@.
replyCode :: String -> Maybe Aeson.Value
replyCode text = case Aeson.decode (LazyByteString.pack text) of
  Just (Aeson.Object reply)
    | Just (Aeson.Bool False) <- KeyMap.lookup "ok" reply
    , Just (Aeson.Object problem) <- KeyMap.lookup "error" reply -> KeyMap.lookup "code" problem
  _ -> Nothing

tracksPayload :: [[(Integer, Integer, Integer)]] -> String
tracksPayload tracks =
  "{\"tracks\":[" <> joinWith "," (map track tracks) <> "]}"
  where
    track pieces = "{\"pieces\":[" <> joinWith "," (map piece pieces) <> "]}"
    piece (start, end, rate) =
      "{\"end_sample\":" <> show end <> ",\"sample_rate\":" <> show rate
      <> ",\"start_sample\":" <> show start <> "}"

alignmentPayload :: [(Integer, Integer)] -> String
alignmentPayload anchors =
  "{\"aligned_rate\":48000,\"anchors\":[" <> joinWith "," (map anchor anchors)
  <> "],\"max_error_samples\":1,\"method\":{\"id\":\"manual\",\"version\":\"1\"}"
  <> ",\"reference_rate\":48000}"
  where
    anchor (reference, aligned) =
      "{\"aligned_sample\":" <> show aligned <> ",\"reference_sample\":" <> show reference <> "}"

stringField :: String -> String -> String
stringField name value = "{" <> jsonString name <> ":" <> jsonString value <> "}"

-- | The review chain World projects: two simultaneous sources aligned with
-- drift, a two-track selection, its kind, competing titles, an ordered credit,
-- a judgment of one title, and a session with its parts.
reviewChainAdmissions :: FilePath -> String -> (String, String) -> (String, String) -> IO [String]
reviewChainAdmissions root archiveId audio work = do
  lineInId <- allocatedObjectId archiveId "archive.core/artifact" "review-fixture-line-in"
  lineInRevision <- commitArtifactFixtureWithDigest root lineInId (digestOf '1')
  let lineIn = (lineInId, lineInRevision)
  alignmentContract <- contractCitation root "archive.claim/capture_alignment"
  alignment <- admitNew root archiveId "door admit commits a drift-aware capture alignment"
    "archive.claim/capture_alignment" "review-fixture-alignment"
    (contractRecord "claim" alignmentContract
      (alignmentPayload [(0, 0), (48000, 48001), (96000, 96003)])
      [pinned "reference" audio, pinned "aligned" lineIn])
  selectionContract <- contractCitationVersion root "archive.claim/audio_selection" "2"
  selection <- admitNew root archiveId "door admit commits a two-track selection"
    "archive.claim/audio_selection" "review-fixture-two-track-selection"
    (contractRecord "claim" selectionContract
      (tracksPayload [[(10, 200, 48000)], [(11, 201, 48000)]])
      [pinned "piece" audio, pinned "piece" lineIn, pinned "alignment" alignment])
  occurrenceContract <- contractCitationVersion root "archive.claim/song_occurrence" "2"
  occurrence <- admitNew root archiveId "door admit commits an occurrence of a two-track selection"
    "archive.claim/song_occurrence" "review-fixture-occurrence"
    (contractRecord "claim" occurrenceContract "{}" [pinned "selection" selection])
  linkContract <- contractCitationVersion root "archive.claim/performance_work" "3"
  link <- admitNew root archiveId "door admit links that occurrence to its Work"
    "archive.claim/performance_work" "review-fixture-occurrence-work"
    (contractRecord "claim" linkContract "{}" [pinned "performance" occurrence, pinned "work" work])
  kindContract <- contractCitation root "archive.claim/selection_kind"
  kind <- admitNew root archiveId "door admit commits a selection kind"
    "archive.claim/selection_kind" "review-fixture-kind"
    (contractRecord "claim" kindContract (stringField "kind" "original")
      [pinned "selection" selection])
  titleContract <- contractCitation root "archive.claim/selection_title"
  title <- admitNew root archiveId "door admit commits a selection title"
    "archive.claim/selection_title" "review-fixture-title"
    (contractRecord "claim" titleContract (stringField "title" "Paper Lanterns")
      [pinned "selection" selection])
  otherTitle <- admitNew root archiveId "door admit keeps a competing selection title"
    "archive.claim/selection_title" "review-fixture-other-title"
    (contractRecord "claim" titleContract (stringField "title" "Paper Lanterns (slow)")
      [pinned "selection" selection])
  band <- admitNew root archiveId "door admit commits a band Entity"
    "archive.core/entity" "review-fixture-band" (entityRecord "organisation" "The Band")
  guest <- admitNew root archiveId "door admit commits a guest Entity"
    "archive.core/entity" "review-fixture-guest" (entityRecord "person" "Guest")
  creditContract <- contractCitation root "archive.claim/artist_credit"
  credit <- admitNew root archiveId "door admit commits an ordered artist credit"
    "archive.claim/artist_credit" "review-fixture-credit"
    (contractRecord "claim" creditContract "{}"
      [pinned "selection" selection, pinned "artist" band, pinned "artist" guest])
  judgmentContract <- contractCitation root "archive.assessment/judgment"
  judgment <- admitNew root archiveId "door admit commits a judgment of one title"
    "archive.assessment/judgment" "review-fixture-judgment"
    (contractRecord "assessment" judgmentContract
      "{\"reason\":\"Checked against the set list\",\"verdict\":\"affirmed\"}"
      [pinned "assessment_of" title])
  session <- admitNew root archiveId "door admit commits a recording_session Entity"
    "archive.audio/session" ("session-" <> replicate 64 '0')
    (entityRecord "recording_session" "Tuesday rehearsal")
  partsContract <- contractCitation root "archive.claim/session_parts"
  parts <- admitNew root archiveId "door admit commits ordered session parts"
    "archive.claim/session_parts" "review-fixture-parts"
    (contractRecord "claim" partsContract "{}"
      [pinned "session" session, pinned "part" audio, pinned "part" lineIn])
  pure (map snd [ lineIn, alignment, selection, occurrence, link, kind, title, otherTitle, band, guest
                , credit, judgment, session, parts ])

-- | Review application contracts (ADR 0048, specification 0.0.5): one
-- accepted proposal per contract, then one refusal for each new rule.
-- Each refused fixture differs from its accepted twin only in the rule's field,
-- and the test checks that difference so a refusal cannot pass for another
-- reason hidden in an unrelated change.
reviewContractAdmissionChecks :: FilePath -> IO ()
reviewContractAdmissionChecks parent = do
  (root, archiveId, _) <- newProposalArchive parent "review-contracts"
  let artifactFixture name digest = do
        objectId <- allocatedObjectId archiveId "archive.core/artifact" name
        revision <- commitArtifactFixtureWithDigest root objectId digest
        pure (objectId, revision)
  first <- artifactFixture "review-a" zeroDigest
  second <- artifactFixture "review-b" (digestOf '1')
  sameBytes <- artifactFixture "review-a-copy" zeroDigest
  operator <- admitNew root archiveId "door admit commits the publication authorizer"
    "archive.core/entity" "review-operator" (entityRecord "person" "Operator")
  work <- admitNew root archiveId "door admit commits a first-identified song as a musical_work"
    "archive.audio/song" "song-6f1c1b2e-3c4d-4e5f-8a9b-0c1d2e3f4a5b"
    (entityRecord "musical_work" "Paper Lanterns")
  _ <- pure work

  selectionContract <- contractCitationVersion root "archive.claim/audio_selection" "2"
  let selectionDraft tracks refs = contractRecord "claim" selectionContract (tracksPayload tracks) refs
      acceptedTracks = [[(10, 200, 48000), (200, 300, 48000)], [(11, 201, 48000)]]
      overlappingTracks = [[(10, 200, 48000), (150, 300, 48000)], [(11, 201, 48000)]]
  selection <- admitNew root archiveId "selection v2 accepts consecutive spans on one track"
    "archive.claim/audio_selection" "review-selection"
    (selectionDraft acceptedTracks
      [pinned "piece" first, pinned "piece" first, pinned "piece" second])
  overlapping <- refuseNew root archiveId "selection v2 refuses overlapping spans within a track"
    "archive.claim/audio_selection" "review-selection-overlap"
    (selectionDraft overlappingTracks
      [pinned "piece" first, pinned "piece" first, pinned "piece" second])
    "spans of one track that pin the same Artifact must not overlap"
  assert "the overlap fixture differs only in the second span's start"
    (tracksPayload acceptedTracks /= tracksPayload overlappingTracks
      && contains "\"start_sample\":150" overlapping) ExitSuccess "" ""
  sameContent <- refuseNew root archiveId "selection v2 refuses the same content on two tracks"
    "archive.claim/audio_selection" "review-selection-same-content"
    (selectionDraft [[(10, 200, 48000)], [(10, 200, 48000)]]
      [pinned "piece" first, pinned "piece" sameBytes])
    "tracks must not pin the same content digest"
  assert "the same-content fixture pins two distinct Artifact objects"
    (fst first /= fst sameBytes && contains (fst sameBytes) sameContent) ExitSuccess "" ""
  _ <- admitNew root archiveId "selection v2 accepts different content on two tracks"
    "archive.claim/audio_selection" "review-selection-distinct-content"
    (selectionDraft [[(10, 200, 48000)], [(10, 200, 48000)]]
      [pinned "piece" first, pinned "piece" second])

  alignmentContract <- contractCitation root "archive.claim/capture_alignment"
  let alignmentDraft anchors refs = contractRecord "claim" alignmentContract (alignmentPayload anchors) refs
  _ <- admitNew root archiveId "capture alignment accepts strictly increasing anchors"
    "archive.claim/capture_alignment" "review-alignment"
    (alignmentDraft [(0, 0), (48000, 48001)] [pinned "reference" first, pinned "aligned" second])
  backwards <- refuseNew root archiveId "capture alignment refuses non-monotonic anchors"
    "archive.claim/capture_alignment" "review-alignment-backwards"
    (alignmentDraft [(0, 0), (48000, 48001), (47000, 48002)]
      [pinned "reference" first, pinned "aligned" second])
    "anchors must strictly increase"
  assert "the anchor fixture moves only one reference sample backwards"
    (contains "\"reference_sample\":47000" backwards) ExitSuccess "" ""
  selfAligned <- refuseNew root archiveId "capture alignment refuses one Artifact as both sides"
    "archive.claim/capture_alignment" "review-alignment-self"
    (alignmentDraft [(0, 0), (48000, 48001)] [pinned "reference" first, pinned "aligned" first])
    "must name different objects"
  assert "the self-alignment fixture names the same Artifact twice"
    (length (filter (== fst first) (wordsBy '"' selfAligned)) == 2) ExitSuccess "" ""

  kindContract <- contractCitation root "archive.claim/selection_kind"
  _ <- admitNew root archiveId "selection kind accepts a V1 kind"
    "archive.claim/selection_kind" "review-kind"
    (contractRecord "claim" kindContract (stringField "kind" "talking") [pinned "selection" selection])
  badKind <- refuseNew root archiveId "selection kind refuses a kind outside the vocabulary"
    "archive.claim/selection_kind" "review-kind-bad"
    (contractRecord "claim" kindContract (stringField "kind" "jazz") [pinned "selection" selection])
    "contract instance does not satisfy its schema"
  _ <- refuseNew root archiveId "a schema refusal names where the instance fails and the rule it breaks"
    "archive.claim/selection_kind" "review-kind-bad-named"
    (contractRecord "claim" kindContract (stringField "kind" "jazz") [pinned "selection" selection])
    "contract instance does not satisfy its schema: /payload/kind breaks enum of the contract schema at payload.kind"
  _ <- refuseNew root archiveId "a schema refusal names a member the schema does not allow"
    "archive.claim/selection_kind" "review-kind-extra"
    (contractRecord "claim" kindContract
      "{\"kind\":\"talking\",\"mood\":\"calm\"}" [pinned "selection" selection])
    "/payload/mood is a member the contract schema at payload does not allow"
  jsonRefusalObject <- allocatedObjectId archiveId "archive.claim/selection_kind" "review-kind-json"
  (jsonRefusalCode, jsonRefusalOut, jsonRefusalErr) <- admitDraft root jsonRefusalObject
    "archive.claim/selection_kind" "review-kind-json"
    (contractRecord "claim" kindContract (stringField "kind" "jazz") [pinned "selection" selection]
      jsonRefusalObject)
  assert "a command-line refusal says it on stderr for a person, then as the service's JSON reply"
    (jsonRefusalCode /= ExitSuccess && null jsonRefusalOut
      && case lines jsonRefusalErr of
           [human, reply] -> "door: contract instance does not satisfy its schema" `isPrefixOf` human
             && replyCode reply == Just (Aeson.String "proposal_refused")
           _ -> False)
    jsonRefusalCode jsonRefusalOut jsonRefusalErr
  assert "the kind fixture changes only the kind value"
    (contains "\"kind\":\"jazz\"" badKind) ExitSuccess "" ""
  v1Contract <- contractCitationVersion root "archive.claim/audio_selection" "1"
  v1Selection <- admitNew root archiveId "selection v1 remains admissible at 0.0.5"
    "archive.claim/audio_selection" "review-selection-v1"
    (contractRecord "claim" v1Contract
      "{\"pieces\":[{\"end_sample\":200,\"sample_rate\":48000,\"start_sample\":10}]}"
      [pinned "piece" first])
  _ <- refuseNew root archiveId "selection facts pin only version 2 selections"
    "archive.claim/selection_kind" "review-kind-v1-target"
    (contractRecord "claim" kindContract (stringField "kind" "talking") [pinned "selection" v1Selection])
    "selection reference must target a Claim citing archive.claim/audio_selection version 2"
  occurrenceV1Contract <- contractCitationVersion root "archive.claim/song_occurrence" "1"
  occurrenceV2Contract <- contractCitationVersion root "archive.claim/song_occurrence" "2"
  linkV3Contract <- contractCitationVersion root "archive.claim/performance_work" "3"
  occurrenceV2 <- admitNew root archiveId "occurrence v2 groups a v2 selection"
    "archive.claim/song_occurrence" "review-occurrence-v2"
    (contractRecord "claim" occurrenceV2Contract "{}" [pinned "selection" selection])
  _ <- admitNew root archiveId "performance_work v3 links an occurrence v2 to a musical_work"
    "archive.claim/performance_work" "review-link-v3"
    (contractRecord "claim" linkV3Contract "{}" [pinned "performance" occurrenceV2, pinned "work" work])
  _ <- refuseNew root archiveId "occurrence v2 refuses a v1 selection"
    "archive.claim/song_occurrence" "review-occurrence-v2-of-v1"
    (contractRecord "claim" occurrenceV2Contract "{}" [pinned "selection" v1Selection])
    "selection reference must target a Claim citing archive.claim/audio_selection version 2"
  occurrenceV1 <- admitNew root archiveId "occurrence v1 still groups a v1 selection"
    "archive.claim/song_occurrence" "review-occurrence-v1"
    (contractRecord "claim" occurrenceV1Contract "{}" [pinned "selection" v1Selection])
  _ <- refuseNew root archiveId "performance_work v3 refuses an occurrence v1"
    "archive.claim/performance_work" "review-link-v3-of-v1"
    (contractRecord "claim" linkV3Contract "{}" [pinned "performance" occurrenceV1, pinned "work" work])
    "performance reference must target a Claim citing archive.claim/song_occurrence version 2"

  titleContract <- contractCitation root "archive.claim/selection_title"
  let titleDraft text = contractRecord "claim" titleContract (stringField "title" text) [pinned "selection" selection]
  title <- admitNew root archiveId "selection title accepts exactly 200 characters"
    "archive.claim/selection_title" "review-title" (titleDraft (replicate 200 't'))
  _ <- refuseNew root archiveId "selection title refuses 201 characters"
    "archive.claim/selection_title" "review-title-long" (titleDraft (replicate 201 't'))
    "contract instance does not satisfy its schema"

  creditContract <- contractCitation root "archive.claim/artist_credit"
  band <- admitNew root archiveId "door admit commits an organisation Entity"
    "archive.core/entity" "review-band" (entityRecord "organisation" "The Band")
  let creditDraft artists = contractRecord "claim" creditContract "{}"
        (pinned "selection" selection : map (pinned "artist") artists)
  _ <- admitNew root archiveId "artist credit accepts a person and an organisation in order"
    "archive.claim/artist_credit" "review-credit" (creditDraft [band, operator])
  duplicate <- refuseNew root archiveId "artist credit refuses a duplicate artist"
    "archive.claim/artist_credit" "review-credit-duplicate" (creditDraft [band, band])
    "artist references must name different objects"
  assert "the duplicate-artist fixture repeats one Entity"
    (length (filter (== fst band) (wordsBy '"' duplicate)) == 2) ExitSuccess "" ""
  _ <- refuseNew root archiveId "artist credit refuses an Entity outside person or organisation"
    "archive.claim/artist_credit" "review-credit-work" (creditDraft [work])
    "artist reference must target an Entity revision of kind person or organisation"

  judgmentContract <- contractCitation root "archive.assessment/judgment"
  let judgmentDraft target = contractRecord "assessment" judgmentContract
        "{\"verdict\":\"withdrawn\"}" [pinned "assessment_of" target]
  _ <- admitNew root archiveId "judgment accepts a withdrawn verdict on a claim revision"
    "archive.assessment/judgment" "review-judgment" (judgmentDraft title)
  _ <- refuseNew root archiveId "judgment refuses a target that is not a claim"
    "archive.assessment/judgment" "review-judgment-entity" (judgmentDraft band)
    "assessment_of reference must target a Claim revision"

  approvalContract <- contractCitation root "archive.transition/publication_approval"
  withdrawalContract <- contractCitation root "archive.transition/publication_withdrawal"
  _ <- refuseNew root archiveId "door admit refuses a valid publication approval"
    "archive.transition/publication_approval" "review-approval"
    (contractRecord "claim" approvalContract
      "{\"decision\":\"approve\",\"destination\":\"media-library\"}"
      [pinned "authorizer" operator, pinned "covers" title])
    "unsupported: transition contracts cannot be admitted generically"
  _ <- refuseNew root archiveId "publication withdrawal refuses a target that is not an approval"
    "archive.transition/publication_withdrawal" "review-withdrawal"
    (contractRecord "claim" withdrawalContract "{}"
      [pinned "authorizer" operator, pinned "withdraws" title])
    "withdraws reference must target a Claim citing archive.transition/publication_approval version 1"

  session <- admitNew root archiveId "door admit commits a recording_session Entity"
    "archive.audio/session" ("session-" <> replicate 64 '0')
    (entityRecord "recording_session" "Tuesday rehearsal")
  partsContract <- contractCitation root "archive.claim/session_parts"
  let partsDraft parts = contractRecord "claim" partsContract "{}"
        (pinned "session" session : map (pinned "part") parts)
  _ <- admitNew root archiveId "session parts accept ordered Artifacts"
    "archive.claim/session_parts" "review-parts" (partsDraft [first, second])
  _ <- refuseNew root archiveId "session parts refuse a part that is not an Artifact"
    "archive.claim/session_parts" "review-parts-entity" (partsDraft [first, band])
    "part reference must target an Artifact revision"
  pure ()

wordsBy :: Char -> String -> [String]
wordsBy separator value = case break (== separator) value of
  (word, []) -> [word]
  (word, _ : rest) -> word : wordsBy separator rest

-- V2 migration route contracts (ADR 0049, specification 0.0.5).

-- | Admit several new records in one Door batch.  Rows are
-- (object ID, namespace, identity name, draft), in batch order.
admitBatch :: FilePath -> String -> String -> [(String, String, String, String)] -> IO ()
admitBatch root label name rows = do
  let paths = ["records/record-" <> show index <> ".json" | index <- [1 .. length rows]]
  revisions <- mapM (\(_, _, _, draft) -> canonicalRevisionOf draft) rows
  proposal <- writeAdmissionProposal root name
    (manifestFor paths [(objectId, namespace, idName) | (objectId, namespace, idName, _) <- rows]
      [(objectId, Nothing) | (objectId, _, _, _) <- rows] [])
    (concat [[path, draft] | (path, (_, _, _, draft)) <- zip paths rows])
  (code, out, err) <- doorCommandAt root ["admit", "--proposal", proposal] ""
  assert label (code == ExitSuccess && all (`contains` out) revisions) code out err

translationPayload :: String -> String -> String -> String
translationPayload rule schema sourceId =
  "{\"locator\":" <> locatorJson <> ",\"rule\":" <> jsonString rule
  <> ",\"source\":{\"id\":" <> jsonString sourceId <> ",\"schema\":" <> jsonString schema <> "}}"

-- | Translated claims use native contracts, and each translation record pins
-- them and the frozen bundle Artifact in the same batch.
migrationContractAdmissionChecks :: FilePath -> IO ()
migrationContractAdmissionChecks parent = do
  (root, archiveId, _) <- newProposalArchive parent "migration-contracts"
  let artifactFixture name digest = do
        objectId <- allocatedObjectId archiveId "archive.core/artifact" name
        revision <- commitArtifactFixtureWithDigest root objectId digest
        pure (objectId, revision)
  bundle <- artifactFixture "migration-bundle" (digestOf 'c')
  audio <- artifactFixture "migration-audio" zeroDigest

  selectionContract <- contractCitationVersion root "archive.claim/audio_selection" "2"
  selectionTranslationContract <- contractCitation root "archive.migration/selection_translation"
  selectionId <- allocatedObjectId archiveId "archive.claim/audio_selection" "v2-segment-fixture"
  let selection = contractRecord "claim" selectionContract (tracksPayload [[(10, 201006, 48000)]])
        [pinned "piece" audio] selectionId
  selectionRevision <- canonicalRevisionOf selection
  selectionTranslationId <- allocatedObjectId archiveId "archive.migration/selection_translation" "translation-seg-fixture"
  let selectionTranslation = contractRecord "claim" selectionTranslationContract
        (selectionTranslationPayload "0.000208" "4.1876")
        [pinned "translated" (selectionId, selectionRevision), pinned "migration_source" bundle]
        selectionTranslationId
  admitBatch root "a native v2 selection and its selection translation admit in one batch"
    "migration-selection-batch"
    [ (selectionId, "archive.claim/audio_selection", "v2-segment-fixture", selection)
    , (selectionTranslationId, "archive.migration/selection_translation", "translation-seg-fixture", selectionTranslation)
    ]

  v1Contract <- contractCitationVersion root "archive.claim/audio_selection" "1"
  v1Selection <- admitNew root archiveId "a single-track v1 selection admits for an occurrence"
    "archive.claim/audio_selection" "migration-v1-selection"
    (contractRecord "claim" v1Contract
      "{\"pieces\":[{\"end_sample\":200,\"sample_rate\":48000,\"start_sample\":10}]}"
      [pinned "piece" audio])
  occurrenceContract <- contractCitationVersion root "archive.claim/song_occurrence" "2"
  translationContract <- contractCitation root "archive.migration/translation"
  occurrenceId <- allocatedObjectId archiveId "archive.claim/song_occurrence" "v2-performance-fixture"
  let occurrence = contractRecord "claim" occurrenceContract "{}"
        [pinned "selection" (selectionId, selectionRevision)] occurrenceId
  occurrenceRevision <- canonicalRevisionOf occurrence
  translationId <- allocatedObjectId archiveId "archive.migration/translation" "translation-performance-fixture"
  let translationDraft refs = contractRecord "claim" translationContract
        (translationPayload "v2-song-performance/1" "song_performance" "perf_fixture") refs
      withTranslated = [pinned "translated" (occurrenceId, occurrenceRevision), pinned "migration_source" bundle]
  admitBatch root "an occurrence and its translation admit in one batch" "migration-occurrence-batch"
    [ (occurrenceId, "archive.claim/song_occurrence", "v2-performance-fixture", occurrence)
    , (translationId, "archive.migration/translation", "translation-performance-fixture", translationDraft withTranslated translationId)
    ]

  missing <- refuseNew root archiveId "a translation without a translated claim is refused"
    "archive.migration/translation" "translation-missing-target"
    (translationDraft [pinned "migration_source" bundle])
    "contract instance does not satisfy its schema"
  assert "the missing-target fixture drops only the translated reference"
    (not (contains "\"role\":\"translated\"" missing) && contains "\"role\":\"migration_source\"" missing)
    ExitSuccess "" ""
  let partDraft position = contractRecord "claim" translationContract
        (replace "\"rule\":" ("\"part_position\":" <> show (position :: Integer) <> ",\"rule\":")
          (translationPayload "v2-song-performance/1" "song_performance" "perf_fixture_part"))
        [pinned "translated" v1Selection, pinned "migration_source" bundle]
  _ <- admitNew root archiveId "a translation admits a positive part_position"
    "archive.migration/translation" "translation-part-position" (partDraft 2)
  zeroPart <- refuseNew root archiveId "a translation refuses part_position 0"
    "archive.migration/translation" "translation-part-position-zero" (partDraft 0)
    "contract instance does not satisfy its schema"
  assert "the part_position fixtures differ only in the position"
    (contains "\"part_position\":0," zeroPart && partDraft 2 "x" /= partDraft 0 "x") ExitSuccess "" ""
  _ <- refuseNew root archiveId "a translation's migration source must be an Artifact"
    "archive.migration/translation" "translation-source-claim"
    (translationDraft [pinned "translated" v1Selection, pinned "migration_source" v1Selection])
    "migration_source reference must target an Artifact revision"

  work <- admitNew root archiveId "door admit commits a matched musical_work"
    "archive.audio/song" "song-0b6c2f9a-7d1e-4f3a-9c2b-5e8d7a6f4b3c" (entityRecord "musical_work" "Paper Lanterns")
  matchContract <- contractCitation root "archive.claim/work_match"
  let matchDraft corpus = contractRecord "claim" matchContract
        "{\"direction\":\"lower_is_closer\",\"method\":{\"id\":\"chroma-cover\",\"version\":\"1\"},\"scale\":\"distance\",\"score\":\"0.25\"}"
        ([pinned "occurrence" (occurrenceId, occurrenceRevision), pinned "work" work]
          <> map (pinned "corpus") corpus <> [pinned "run" v1Selection])
  _ <- refuseNew root archiveId "a work match refuses its compared occurrence inside its corpus"
    "archive.claim/work_match" "work-match-self-corpus"
    (matchDraft [(occurrenceId, occurrenceRevision)])
    "must name different objects"
  pure ()

streamPayload :: Integer -> Integer -> Integer -> String
streamPayload rate count channels =
  "{\"bits_per_sample\":24,\"channel_count\":" <> show channels
  <> ",\"codec\":\"pcm_s24le\",\"sample_count\":" <> show count
  <> ",\"sample_rate\":" <> show rate <> ",\"stream_index\":0}"

matchPayload :: String
matchPayload =
  "{\"direction\":\"lower_is_closer\",\"method\":{\"id\":\"chroma-cover\",\"version\":\"1\"},\"scale\":\"distance\",\"score\":\"0.25\"}"

derivationContractAdmissionChecks :: FilePath -> IO ()
derivationContractAdmissionChecks parent = do
  (root, archiveId, _) <- newProposalArchive parent "derivation-contracts"
  let artifactFixture name digest = do
        objectId <- allocatedObjectId archiveId "archive.core/artifact" name
        revision <- commitArtifactFixtureWithDigest root objectId digest
        pure (objectId, revision)
      runNamespace = "archive.derivation/run"
  audio <- artifactFixture "derivation-audio" zeroDigest
  other <- artifactFixture "derivation-other" (digestOf '1')
  parameters <- artifactFixture "derivation-parameters" (digestOf '2')
  work <- admitNew root archiveId "door admit commits a musical_work for matches"
    "archive.audio/song" "song-1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d" (entityRecord "musical_work" "Fixture Work")

  -- Runs (ADR 0064).
  runContract <- contractCitationVersion root "archive.claim/derivation_run" "1"
  let runDraft payload refs = contractRecord "claim" runContract payload refs
  run <- admitNew root archiveId "a derivation run with used inputs and parameters is admitted"
    runNamespace (runName 1)
    (runDraft runPayload [pinned "used" audio, pinned "parameters" parameters])
  _ <- admitNew root archiveId "a run that ends when it starts is admitted"
    runNamespace (runName 2)
    (runDraft (runPayloadAt "2026-10-16T10:00:00Z" "2026-10-16T10:00:00Z") [])
  _ <- admitNew root archiveId "a run may use an Entity and a Claim revision"
    runNamespace (runName 3)
    (runDraft runPayload [pinned "used" work, pinned "used" run])
  backwards <- refuseNew root archiveId "a run that ends before it starts is refused"
    runNamespace (runName 4)
    (runDraft (runPayloadAt "2026-10-16T10:00:00Z" "2026-10-16T09:59:59.999Z") [])
    "ended_at must not be before started_at"
  assert "the backwards run differs from an admitted run only in ended_at"
    (contains "\"ended_at\":\"2026-10-16T09:59:59.999Z\"" backwards
      && contains "\"started_at\":\"2026-10-16T10:00:00Z\"" backwards)
    ExitSuccess backwards ""
  offset <- refuseNew root archiveId "a run instant with a numeric offset is refused"
    runNamespace (runName 5)
    (runDraft (runPayloadAt "2026-10-16T10:00:00+00:00" "2026-10-16T10:00:05Z") [])
    "contract instance does not satisfy its schema"
  assert "the offset fixture changes only the started_at suffix"
    (contains "10:00:00+00:00" offset) ExitSuccess "" ""
  -- The pattern alone admits any digits; the date must exist.
  _ <- refuseNew root archiveId "a run instant on a day that does not exist is refused"
    runNamespace (runName 12)
    (runDraft (runPayloadAt "2026-02-30T10:00:00Z" "2026-10-16T10:00:05Z") [])
    "contract instance does not satisfy its schema"
  _ <- refuseNew root archiveId "a run instant at hour 24 is refused"
    runNamespace (runName 13)
    (runDraft (runPayloadAt "2026-10-16T10:00:00Z" "2026-10-16T24:00:00Z") [])
    "contract instance does not satisfy its schema"
  _ <- refuseNew root archiveId "a run refuses two parameters Artifacts"
    runNamespace (runName 6)
    (runDraft runPayload [pinned "parameters" parameters, pinned "parameters" other])
    "contract instance does not satisfy its schema"
  _ <- refuseNew root archiveId "a run's parameters must be an Artifact"
    runNamespace (runName 7)
    (runDraft runPayload [pinned "parameters" work])
    "parameters reference must target an Artifact revision"
  let software count = "{\"ended_at\":\"2026-10-16T10:00:05Z\",\"method\":{\"id\":\"energy-spans\",\"version\":\"1\"},\"software\":["
        <> joinWith "," (replicate count "{\"name\":\"lib\",\"version\":\"1\"}")
        <> "],\"started_at\":\"2026-10-16T10:00:00Z\"}"
  _ <- admitNew root archiveId "a run names up to 32 software components"
    runNamespace (runName 8) (runDraft (software 32) [])
  _ <- refuseNew root archiveId "a run refuses 33 software components"
    runNamespace (runName 9) (runDraft (software 33) [])
    "contract instance does not satisfy its schema"
  judgmentContract <- contractCitation root "archive.assessment/judgment"
  judgment <- admitNew root archiveId "door admit commits a judgment of a run"
    "archive.assessment/judgment" "derivation-judgment"
    (contractRecord "assessment" judgmentContract "{\"verdict\":\"affirmed\"}" [pinned "assessment_of" run])
  _ <- refuseNew root archiveId "a run may not use an Assessment revision"
    runNamespace (runName 10) (runDraft runPayload [pinned "used" judgment])
    "used reference must target a revision of shape artifact or claim or entity"

  -- Stream facts (ADR 0065), proposed with their run in one batch.
  streamContract <- contractCitationVersion root "archive.claim/audio_stream" "1"
  let streamDraft payload refs = contractRecord "claim" streamContract payload refs
  batchRunId <- allocatedObjectId archiveId runNamespace (runName 11)
  streamId <- allocatedObjectId archiveId "archive.claim/audio_stream" "derivation-stream"
  let batchRunDraft = runDraft runPayload [pinned "used" audio] batchRunId
  batchRunRevision <- canonicalRevisionOf batchRunDraft
  let batchRun = (batchRunId, batchRunRevision)
      acceptedStream = streamDraft (streamPayload 48000 480000 2) [pinned "artifact" audio, pinned "run" batchRun] streamId
  streamRevision <- canonicalRevisionOf acceptedStream
  admitBatch root "a run and the stream facts it made are admitted in one batch" "derivation-stream-batch"
    [ (batchRunId, runNamespace, runName 11, batchRunDraft)
    , (streamId, "archive.claim/audio_stream", "derivation-stream", acceptedStream) ]
  let stream = (streamId, streamRevision)
  _ <- admitNew root archiveId "a stream of 64 channels is admitted"
    "archive.claim/audio_stream" "derivation-stream-64"
    (streamDraft (streamPayload 48000 480000 64) [pinned "artifact" audio, pinned "run" run])
  wide <- refuseNew root archiveId "a stream of 65 channels is refused by its maximum"
    "archive.claim/audio_stream" "derivation-stream-65"
    (streamDraft (streamPayload 48000 480000 65) [pinned "artifact" audio, pinned "run" run])
    "contract instance does not satisfy its schema"
  assert "the channel fixtures differ only in channel_count"
    (contains "\"channel_count\":65," wide) ExitSuccess "" ""
  _ <- refuseNew root archiveId "a stream refuses a zero sample_count"
    "archive.claim/audio_stream" "derivation-stream-empty"
    (streamDraft (streamPayload 48000 0 2) [pinned "artifact" audio, pinned "run" run])
    "contract instance does not satisfy its schema"
  _ <- refuseNew root archiveId "a stream's run must be a derivation run"
    "archive.claim/audio_stream" "derivation-stream-not-run"
    (streamDraft (streamPayload 48000 480000 2) [pinned "artifact" audio, pinned "run" stream])
    "run reference must target a Claim citing archive.claim/derivation_run version 1"
  _ <- refuseNew root archiveId "a stream's subject must be an Artifact"
    "archive.claim/audio_stream" "derivation-stream-of-entity"
    (streamDraft (streamPayload 48000 480000 2) [pinned "artifact" work, pinned "run" run])
    "artifact reference must target an Artifact revision"
  _ <- refuseNew root archiveId "a stream without a run is refused"
    "archive.claim/audio_stream" "derivation-stream-no-run"
    (streamDraft (streamPayload 48000 480000 2) [pinned "artifact" audio])
    "contract instance does not satisfy its schema"

  -- Span suggestions (ADR 0066).
  suggestionsContract <- contractCitationVersion root "archive.claim/span_suggestions" "1"
  let suggestionsDraft payload refs = contractRecord "claim" suggestionsContract payload refs
      againstStream source stream' = [pinned "source" source, pinned "stream" stream', pinned "run" run]
      suggest label name spans expected = refuseNew root archiveId label
        "archive.claim/span_suggestions" name
        (suggestionsDraft (spansPayload 48000 spans) (againstStream audio stream)) expected
      accepted = [(0, 1000, "music"), (1000, 5000, "talk"), (6000, 480000, "quiet")]
  _ <- admitNew root archiveId "span suggestions in order, touching, and ending at sample_count are admitted"
    "archive.claim/span_suggestions" "derivation-suggestions"
    (suggestionsDraft (spansPayload 48000 accepted) (againstStream audio stream))
  overlap <- suggest "overlapping suggested spans are refused" "derivation-suggestions-overlap"
    [(0, 1000, "music"), (900, 5000, "talk"), (6000, 480000, "quiet")]
    "spans must be in order and must not overlap"
  assert "the overlap fixture moves only the second span's start"
    (contains "\"start_sample\":900" overlap) ExitSuccess "" ""
  _ <- suggest "suggested spans out of order are refused" "derivation-suggestions-order"
    [(1000, 5000, "talk"), (0, 1000, "music")]
    "spans must be in order and must not overlap"
  _ <- suggest "an empty suggested span is refused" "derivation-suggestions-empty-span"
    [(0, 1000, "music"), (5000, 5000, "talk")]
    "each span requires 0 <= start < end"
  past <- suggest "a suggested span past the stream's sample_count is refused" "derivation-suggestions-past"
    [(0, 1000, "music"), (1000, 5000, "talk"), (6000, 480001, "quiet")]
    "end_sample must not exceed the stream claim's sample_count"
  assert "the past-the-end fixture adds one sample to the last end"
    (contains "\"end_sample\":480001" past) ExitSuccess "" ""
  rate <- refuseNew root archiveId "suggestions at a rate other than the stream's are refused"
    "archive.claim/span_suggestions" "derivation-suggestions-rate"
    (suggestionsDraft (spansPayload 44100 accepted) (againstStream audio stream))
    "sample_rate must equal the stream claim's sample_rate"
  assert "the rate fixture changes only sample_rate"
    (contains "\"sample_rate\":44100" rate) ExitSuccess "" ""
  _ <- suggest "an unknown span class is refused" "derivation-suggestions-class"
    [(0, 1000, "noise")]
    "contract instance does not satisfy its schema"
  assert "the stream and the other source are different Artifacts"
    (fst audio /= fst other) ExitSuccess "" ""
  _ <- refuseNew root archiveId "suggestions about another Artifact than the stream's are refused"
    "archive.claim/span_suggestions" "derivation-suggestions-other-source"
    (suggestionsDraft (spansPayload 48000 accepted) (againstStream other stream))
    "stream claim must be about the same source revision"
  revisedId <- pure (fst audio)
  revisedRevision <- commitDraftFixture root
    (replace "\"supersedes\":[]" ("\"supersedes\":[" <> pinned "supersedes" audio <> "]")
      (replace zeroDigest (digestOf '3') (artifactDraft revisedId)))
  assert "the revised Artifact is a new revision of the stream's object"
    (revisedRevision /= snd audio) ExitSuccess revisedRevision (snd audio)
  _ <- refuseNew root archiveId "suggestions about another revision of the stream's Artifact are refused"
    "archive.claim/span_suggestions" "derivation-suggestions-other-revision"
    (suggestionsDraft (spansPayload 48000 accepted) (againstStream (revisedId, revisedRevision) stream))
    "stream claim must be about the same source revision"
  _ <- refuseNew root archiveId "suggestions must pin an audio stream claim"
    "archive.claim/span_suggestions" "derivation-suggestions-not-stream"
    (suggestionsDraft (spansPayload 48000 accepted) (againstStream audio run))
    "stream reference must target a Claim citing archive.claim/audio_stream version 1"
  _ <- refuseNew root archiveId "suggestions' run must be a derivation run"
    "archive.claim/span_suggestions" "derivation-suggestions-not-run"
    (suggestionsDraft (spansPayload 48000 accepted)
      [pinned "source" audio, pinned "stream" stream, pinned "run" stream])
    "run reference must target a Claim citing archive.claim/derivation_run version 1"
  let many count = [(index * 48, index * 48 + 48, "unknown") | index <- [0 .. count - 1]]
  _ <- admitNew root archiveId "10,000 suggested spans are admitted"
    "archive.claim/span_suggestions" "derivation-suggestions-most"
    (suggestionsDraft (spansPayload 48000 (many 10000)) (againstStream audio stream))
  _ <- refuseNew root archiveId "10,001 suggested spans are refused"
    "archive.claim/span_suggestions" "derivation-suggestions-too-many"
    (suggestionsDraft (spansPayload 48000 (many 10001)) (againstStream audio stream))
    "contract instance does not satisfy its schema"

  -- Matches and alignments pin their run (ADR 0067).
  selectionContract <- contractCitationVersion root "archive.claim/audio_selection" "2"
  occurrenceContract <- contractCitationVersion root "archive.claim/song_occurrence" "2"
  selection <- admitNew root archiveId "door admit commits a selection to match"
    "archive.claim/audio_selection" "derivation-selection"
    (contractRecord "claim" selectionContract (tracksPayload [[(10, 200, 48000)]]) [pinned "piece" audio])
  corpusSelection <- admitNew root archiveId "door admit commits a corpus selection"
    "archive.claim/audio_selection" "derivation-corpus-selection"
    (contractRecord "claim" selectionContract (tracksPayload [[(10, 200, 48000)]]) [pinned "piece" other])
  occurrence <- admitNew root archiveId "door admit commits an occurrence to match"
    "archive.claim/song_occurrence" "derivation-occurrence"
    (contractRecord "claim" occurrenceContract "{}" [pinned "selection" selection])
  corpus <- admitNew root archiveId "door admit commits a corpus occurrence"
    "archive.claim/song_occurrence" "derivation-corpus"
    (contractRecord "claim" occurrenceContract "{}" [pinned "selection" corpusSelection])
  v1SelectionContract <- contractCitationVersion root "archive.claim/audio_selection" "1"
  v1Selection <- admitNew root archiveId "door admit commits a version 1 selection"
    "archive.claim/audio_selection" "derivation-selection-v1"
    (contractRecord "claim" v1SelectionContract
      "{\"pieces\":[{\"end_sample\":200,\"sample_rate\":48000,\"start_sample\":10}]}" [pinned "piece" audio])
  matchContract <- contractCitationVersion root "archive.claim/work_match" "2"
  let matchDraft compared run' = contractRecord "claim" matchContract matchPayload
        (compared <> [pinned "work" work, pinned "corpus" corpus, pinned "parameters" parameters, pinned "run" run'])
  _ <- admitNew root archiveId "a version 2 match of an occurrence pins its run"
    "archive.claim/work_match" "derivation-match-occurrence"
    (matchDraft [pinned "occurrence" occurrence] run)
  _ <- admitNew root archiveId "a version 2 match of a selection pins its run"
    "archive.claim/work_match" "derivation-match-selection"
    (matchDraft [pinned "selection" selection] run)
  both <- refuseNew root archiveId "a match naming both an occurrence and a selection is refused"
    "archive.claim/work_match" "derivation-match-both"
    (matchDraft [pinned "occurrence" occurrence, pinned "selection" selection] run)
    "contract instance does not satisfy its schema"
  assert "the both fixture adds only the selection reference"
    (contains "\"role\":\"occurrence\"" both && contains "\"role\":\"selection\"" both) ExitSuccess "" ""
  _ <- refuseNew root archiveId "a match naming neither an occurrence nor a selection is refused"
    "archive.claim/work_match" "derivation-match-neither"
    (matchDraft [] run)
    "contract instance does not satisfy its schema"
  _ <- refuseNew root archiveId "a version 2 match refuses a run that is not a derivation run"
    "archive.claim/work_match" "derivation-match-not-run"
    (matchDraft [pinned "occurrence" occurrence] stream)
    "run reference must target a Claim citing archive.claim/derivation_run version 1"
  _ <- refuseNew root archiveId "a version 2 match compares only version 2 selections"
    "archive.claim/work_match" "derivation-match-v1-selection"
    (matchDraft [pinned "selection" v1Selection] run)
    "selection reference must target a Claim citing archive.claim/audio_selection version 2"
  _ <- refuseNew root archiveId "a version 2 match still refuses its occurrence inside its corpus"
    "archive.claim/work_match" "derivation-match-self-corpus"
    (contractRecord "claim" matchContract matchPayload
      [pinned "occurrence" corpus, pinned "work" work, pinned "corpus" corpus, pinned "run" run])
    "must name different objects"

  alignmentV2 <- contractCitationVersion root "archive.claim/capture_alignment" "2"
  alignmentV1 <- contractCitationVersion root "archive.claim/capture_alignment" "1"
  let alignment contract refs = contractRecord "claim" contract
        (alignmentPayload [(0, 0), (48000, 48001)]) ([pinned "reference" audio, pinned "aligned" other] <> refs)
  _ <- admitNew root archiveId "a version 2 alignment without a run is admitted"
    "archive.claim/capture_alignment" "derivation-alignment-unrun" (alignment alignmentV2 [])
  _ <- admitNew root archiveId "a version 2 alignment pins a derivation run"
    "archive.claim/capture_alignment" "derivation-alignment-run" (alignment alignmentV2 [pinned "run" run])
  _ <- refuseNew root archiveId "a version 2 alignment refuses a run that is not a derivation run"
    "archive.claim/capture_alignment" "derivation-alignment-not-run" (alignment alignmentV2 [pinned "run" stream])
    "run reference must target a Claim citing archive.claim/derivation_run version 1"
  _ <- admitNew root archiveId "a version 1 alignment keeps its untyped run"
    "archive.claim/capture_alignment" "derivation-alignment-v1" (alignment alignmentV1 [pinned "run" stream])
  pure ()

-- | A work match version 3 compares a window of whole suggested spans
-- (ADR 0072, D-16), through the real Door: every fixture record is admitted
-- by Door's writer, each rule refuses its own violation and writes nothing,
-- and an admitted match is read back by `archive export`.
windowMatchAdmissionChecks :: FilePath -> IO ()
windowMatchAdmissionChecks parent = do
  (root, archiveId, _) <- newProposalArchive parent "window-matches"
  let artifactFixture name digest = do
        objectId <- allocatedObjectId archiveId "archive.core/artifact" name
        revision <- commitArtifactFixtureWithDigest root objectId digest
        pure (objectId, revision)
  audio <- artifactFixture "window-audio" zeroDigest
  other <- artifactFixture "window-other" (digestOf '1')
  parameters <- artifactFixture "window-parameters" (digestOf '2')
  work <- admitNew root archiveId "door admit commits a musical_work for window matches"
    "archive.audio/song" "song-2c3d4e5f-6a7b-4c8d-9e0f-1a2b3c4d5e6f" (entityRecord "musical_work" "Window Work")
  runContract <- contractCitationVersion root "archive.claim/derivation_run" "1"
  run <- admitNew root archiveId "door admit commits a run for window matches"
    "archive.derivation/run" (runName 1) (contractRecord "claim" runContract runPayload [pinned "used" audio])
  streamContract <- contractCitationVersion root "archive.claim/audio_stream" "1"
  stream <- admitNew root archiveId "door admit commits the stream the suggestions measure"
    "archive.claim/audio_stream" "window-stream"
    (contractRecord "claim" streamContract (streamPayload 48000 480000 2) [pinned "artifact" audio, pinned "run" run])
  suggestionsContract <- contractCitationVersion root "archive.claim/span_suggestions" "1"
  suggestions <- admitNew root archiveId "door admit commits the suggestions a window is cut from"
    "archive.claim/span_suggestions" "window-suggestions"
    (contractRecord "claim" suggestionsContract
      (spansPayload 48000 [(0, 1000, "music"), (1000, 5000, "talk"), (6000, 480000, "quiet")])
      [pinned "source" audio, pinned "stream" stream, pinned "run" run])
  selectionContract <- contractCitationVersion root "archive.claim/audio_selection" "2"
  occurrenceContract <- contractCitationVersion root "archive.claim/song_occurrence" "2"
  let corpusOccurrence name = do
        selection <- admitNew root archiveId ("door admit commits the selection of " <> name)
          "archive.claim/audio_selection" (name <> "-selection")
          (contractRecord "claim" selectionContract (tracksPayload [[(10, 200, 48000)]]) [pinned "piece" other])
        admitNew root archiveId ("door admit commits " <> name)
          "archive.claim/song_occurrence" name
          (contractRecord "claim" occurrenceContract "{}" [pinned "selection" selection])
  corpus <- corpusOccurrence "window-corpus"
  corpusTwo <- corpusOccurrence "window-corpus-two"
  matchContract <- contractCitationVersion root "archive.claim/work_match" "3"
  let windowRefs compared corpusRefs run' =
        [pinned "suggestions" compared, pinned "work" work] <> map (pinned "corpus") corpusRefs
          <> [pinned "parameters" parameters, pinned "run" run']
      windowDraft payload refs = contractRecord "claim" matchContract payload refs
      window start end = windowDraft (windowMatchPayload 48000 start end) (windowRefs suggestions [corpus, corpusTwo] run)
      refusedWindow label name draftFor expected = do
        headBefore <- gitHead root
        draft <- refuseNew root archiveId label "archive.claim/work_match" name draftFor expected
        headAfter <- gitHead root
        assert (label <> " and writes nothing") (headBefore == headAfter) ExitSuccess headBefore headAfter
        pure draft

  admitted <- admitNew root archiveId "a window of two touching suggested spans is admitted"
    "archive.claim/work_match" "window-two-spans" (window 0 5000)
  _ <- admitNew root archiveId "a window of one suggested span ending at the stream's end is admitted"
    "archive.claim/work_match" "window-last-span" (window 6000 480000)
  _ <- admitNew root archiveId "a window across a gap between suggested spans is admitted"
    "archive.claim/work_match" "window-every-span" (window 0 480000)

  inside <- refusedWindow "a window starting inside a suggested span is refused" "window-start-inside"
    (window 500 5000) "window start_sample must be the start_sample of one of the suggestions claim's spans"
  assert "the inside fixture moves only the window's start"
    (contains "\"start_sample\":500}" inside && contains "\"end_sample\":5000," inside) ExitSuccess inside ""
  _ <- refusedWindow "a window starting at a span's end in a gap is refused" "window-start-gap"
    (window 5000 480000) "window start_sample must be the start_sample of one of the suggestions claim's spans"
  _ <- refusedWindow "a window ending inside a suggested span is refused" "window-end-inside"
    (window 0 4000) "window end_sample must be the end_sample of its first span or of a later one"
  _ <- refusedWindow "a window ending at an earlier span's end is refused" "window-end-earlier"
    (window 1000 1000) "window end_sample must be the end_sample of its first span or of a later one"
  _ <- refusedWindow "a window ending past the last span is refused" "window-end-past"
    (window 0 480001) "window end_sample must be the end_sample of its first span or of a later one"
  rate <- refusedWindow "a window at a rate other than the suggestions' is refused" "window-rate"
    (windowDraft (windowMatchPayload 44100 0 5000) (windowRefs suggestions [corpus] run))
    "sample_rate must equal the suggestions claim's sample_rate"
  assert "the rate fixture changes only sample_rate"
    (contains "\"sample_rate\":44100," rate) ExitSuccess rate ""
  _ <- refusedWindow "a window of a claim that is not span suggestions is refused" "window-not-suggestions"
    (windowDraft (windowMatchPayload 48000 0 5000) (windowRefs stream [corpus] run))
    "suggestions reference must target a Claim citing archive.claim/span_suggestions version 1"
  _ <- refusedWindow "a window match refuses a run that is not a derivation run" "window-not-run"
    (windowDraft (windowMatchPayload 48000 0 5000) (windowRefs suggestions [corpus] stream))
    "run reference must target a Claim citing archive.claim/derivation_run version 1"
  _ <- refusedWindow "a window match naming one corpus occurrence twice is refused" "window-corpus-twice"
    (windowDraft (windowMatchPayload 48000 0 5000) (windowRefs suggestions [corpus, corpus] run))
    "corpus references must name different objects"
  _ <- refusedWindow "a window match without a corpus is refused" "window-no-corpus"
    (windowDraft (windowMatchPayload 48000 0 5000) (windowRefs suggestions [] run))
    "contract instance does not satisfy its schema"
  _ <- refusedWindow "a window match without its window is refused" "window-no-window"
    (windowDraft "{\"direction\":\"lower_is_closer\",\"method\":{\"id\":\"essentia-serra09\",\"version\":\"1\"},\"sample_rate\":48000,\"scale\":\"distance\",\"score\":\"0.0612\"}"
      (windowRefs suggestions [corpus] run))
    "contract instance does not satisfy its schema"
  _ <- refusedWindow "a version 3 match cannot compare an occurrence" "window-occurrence"
    (\objectId -> contractRecord "claim" matchContract (windowMatchPayload 48000 0 5000)
      ([pinned "occurrence" corpus] <> windowRefs suggestions [corpusTwo] run) objectId)
    "contract instance does not satisfy its schema"

  (exportCode, exportOut, exportErr) <- readCreateProcessWithExitCode ((proc "archive" ["export"]) {cwd = Just root}) ""
  assert "archive export reads back the admitted window match with its window and suggestions"
    (exportCode == ExitSuccess && contains (snd admitted) exportOut
      && contains "\"window\":{\"end_sample\":5000,\"start_sample\":0}" exportOut
      && contains ("\"revision_id\":\"" <> snd suggestions <> "\",\"role\":\"suggestions\"") exportOut)
    exportCode (take 400 exportOut) exportErr

-- | The contract admission checks, one case per check or row.
tests :: IO TestTree
tests = fmap (testGroup "contract admission") $ sequence
  [ caseOf "worldContractAdmissionChecks" (withProposalTemporaryRoot worldContractAdmissionChecks)
  , caseOf "reviewContractAdmissionChecks" (withProposalTemporaryRoot reviewContractAdmissionChecks)
  , caseOf "migrationContractAdmissionChecks" (withProposalTemporaryRoot migrationContractAdmissionChecks)
  , caseOf "derivationContractAdmissionChecks" (withProposalTemporaryRoot derivationContractAdmissionChecks)
  , caseOf "windowMatchAdmissionChecks" (withProposalTemporaryRoot windowMatchAdmissionChecks)
  ]
