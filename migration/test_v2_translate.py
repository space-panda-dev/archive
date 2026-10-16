import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

from migration.v2_bundle import build_candidate, verify_candidate
from migration.v2_translate import (
    SheetError, TranslateError, allocate, jcs, load_citations, plan, probe_report, revision_of,
    seconds_to_samples, write_plan,
)


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_ID = "urn:uuid:3edd5cbf-d5f0-4e7d-b719-3cdd76150f61"
OPERATOR = {"object_id": "urn:uuid:f4cf179c-dcf2-5e3d-9d39-c0a50c4f3127",
            "revision_id": "sha256:" + "d" * 64}
BUNDLE = {"object_id": "urn:uuid:11111111-2222-5333-8444-555555555555", "revision_id": "sha256:" + "b" * 64}
CONTEXT = {"archive_id": ARCHIVE_ID, "spec": "archive/0.0.5", "by": "pkg:pypi/archive-migration@0.1.0",
           "operator": OPERATOR, "operator_logins": ["operator"], "bundle": BUNDLE}
ARTIFACTS = {
    "art_a": {"object_id": "urn:uuid:6f9208de-ab8c-52b1-8d34-e414a95f0abb", "revision_id": "sha256:" + "1" * 64,
              "digest": "sha256:" + "a" * 64},
    "art_b": {"object_id": "urn:uuid:7f9208de-ab8c-52b1-8d34-e414a95f0abb", "revision_id": "sha256:" + "2" * 64,
              "digest": "sha256:" + "c" * 64},
}
PROBES = {"art_a": {"sample_rate": 48000, "tool": "ffprobe", "tool_version": "7.1", "samples": 48000 * 600},
          "art_b": {"sample_rate": 48000, "tool": "ffprobe", "tool_version": "7.1"}}

_ORIGINAL_TEMPDIR = tempfile.tempdir


def setUpModule():
    tempfile.tempdir = os.path.realpath(tempfile.gettempdir())


def tearDownModule():
    tempfile.tempdir = _ORIGINAL_TEMPDIR


def citations():
    files = []
    for directory in ("spec/contracts",):
        for path in sorted((ROOT / directory).glob("*.json")):
            files.append((path.name, path.read_bytes()))
    return load_citations(files)


def segment(segment_id, start, end, artifact="art_a", supersedes=None, review="reviewed"):
    # Field shapes copied from the V2 census of real audio_segment records.
    return {"schema": "audio_segment", "schema_version": "1", "id": "row-" + segment_id,
            "segment_id": segment_id, "source_artifact_id": artifact, "source_record_id": "src-" + artifact,
            "source_content_hash_sha256": "0" * 64, "start_seconds": start, "end_seconds": end,
            "duration_seconds": 1, "review_state": review, "supersedes_segment_id": supersedes,
            "boundary_basis": "operator", "boundary_evidence": {}, "classification": "private",
            "clip_writes": False, "transcript_writes": False, "contracts": [], "created_at": "2026-07-14T00:00:00Z",
            "import_batch_id": "batch", "operator": "operator", "policy_scope": "private",
            "relative_path": "x.flac", "source_duration_seconds": None, "tool_name": "segment",
            "tool_version": "1", "trust_boundary": "local"}


def performance(performance_id, segment_ids, title, kind="cover", artists=("Band",), state="reviewed",
                artifact="art_a", start=1):
    return {"schema": "song_performance", "schema_version": "1", "id": "row-" + performance_id,
            "performance_id": performance_id, "source_artifact_id": artifact, "segment_ids": list(segment_ids),
            "title": title, "kind": kind, "artists": list(artists), "state": state,
            "supersedes_performance_id": None, "start_seconds": start, "end_seconds": start + 1,
            "basis": "operator", "classification": "private", "contracts": [],
            "created_at": "2026-07-14T00:00:00Z", "import_batch_id": "batch", "operator": "operator",
            "policy_scope": "private", "tool_name": "performance", "tool_version": "1", "trust_boundary": "local"}


def recorded_file(artifact_id, mtime_ns):
    """A V2 artifact and its import observation, shaped like the real records."""
    source = "src-" + artifact_id
    artifact = {"schema": "artifact", "schema_version": "1", "id": "row-" + artifact_id, "artifact_id": artifact_id,
                "source_record_id": source, "content_hash_sha256": "0" * 64, "byte_size": 1,
                "created_at": "2026-07-14T00:00:00Z", "media_type": "audio/flac", "immutable_original": True}
    observation = {"schema": "source_observation", "schema_version": "1", "id": "obs-" + artifact_id,
                   "source_record_id": source, "observed_mtime": str(mtime_ns), "observed_size": 1,
                   "original_filename": "ZOOM.WAV", "state": "imported", "created_at": "2026-07-14T00:00:00Z"}
    return [artifact] if mtime_ns is None else [artifact, observation]


def sheet(titles, artists=(("a1", "Band", "organisation"),), decisions=None, renditions=None):
    result = {"titles": {}, "artists": {}, "decisions": {}, "renditions": renditions or {}}
    for tid, key, song in titles:
        result["titles"][tid] = {"key": key, "spellings": {key: 1}, "suggested": key}
        result["decisions"][tid] = {"song": song, "same_as": None, "confirmed": True}
    for aid, name, kind in artists:
        result["artists"][aid] = {"name": name}
        result["decisions"][aid] = {"kind": kind, "same_as": None, "confirmed": True}
    for key, value in (decisions or {}).items():
        result["decisions"][key].update(value)
    return result


def bundle_rows(records, raw_overrides=None):
    """Run the real bundle writer over a synthetic V2 tree, one record per file."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for index, record in enumerate(records):
            folder = root / record["schema"].replace("_", "-")
            folder.mkdir(exist_ok=True)
            raw = (raw_overrides or {}).get(index) or json.dumps(record, sort_keys=True).encode()
            (folder / f"{index:03d}.json").write_bytes(raw)
        candidate = build_candidate(root)
    manifest = verify_candidate(candidate)
    with tarfile.open(fileobj=io.BytesIO(candidate), mode="r:") as archive:
        members = {name: archive.extractfile(name).read() for name in archive.getnames() if name != "manifest.json"}
    return [(row, members[row["member"]][row["offset"]:row["offset"] + row["length"]]) for row in manifest["records"]]


class Keys:
    def __init__(self):
        self.count = 0

    def __call__(self):
        self.count += 1
        return f"00000000-0000-4000-8000-{self.count:012d}"


def run(records, sheet_value, state=None, raw_overrides=None, new_key=None):
    return plan(bundle_rows(records, raw_overrides), CONTEXT, citations(), ARTIFACTS, PROBES, sheet_value,
                state or {}, new_key or Keys())


def drafts(result):
    return [json.loads(record["bytes"]) for batch in result["batches"] for record in batch["records"]]


def by_contract(result, contract_id):
    return [draft for draft in drafts(result)
            if any(citation["id"] == contract_id for citation in draft.get("contracts", []))]


class CanonicalTest(unittest.TestCase):
    def test_jcs_sorts_keys_by_utf16_and_refuses_fractions(self):
        self.assertEqual(jcs({"b": 1, "a": [True, None, "é\n"]}), '{"a":[true,null,"é\\n"],"b":1}'.encode())
        self.assertEqual(jcs({"\U0001f600": 1, "｡": 2}), '{"\U0001f600":1,"｡":2}'.encode())
        with self.assertRaises(TranslateError):
            jcs({"x": 1.5})

    def test_allocation_matches_door_fixture(self):
        # A real Door-admitted Work and its archive from the World fixture.
        export = json.loads((ROOT / "world/tests/fixtures/door-world-chain-export.json").read_text())
        [work] = [record for record in export["records"] if record["payload"].get("kind") == "musical_work"]
        self.assertEqual(allocate(export["archive_id"], "archive.core/entity", "world-fixture-paper-lanterns"),
                         work["object_id"])

    def test_revision_matches_door_admitted_record(self):
        export = json.loads((ROOT / "world/tests/fixtures/door-world-chain-export.json").read_text())
        for record in export["records"]:
            self.assertEqual(revision_of(record), record["revision_id"])

    def test_seconds_round_half_even_from_exact_text(self):
        self.assertEqual(seconds_to_samples("0.25", 2), ("0.25", 0))
        self.assertEqual(seconds_to_samples("0.75", 2), ("0.75", 2))
        self.assertEqual(seconds_to_samples("1e-06", 1000000), ("0.000001", 1))
        with self.assertRaises(TranslateError):
            seconds_to_samples("1.0000001", 48000)


class PlanTest(unittest.TestCase):
    def one_song(self, **sheet_kwargs):
        records = [segment("s1", 1.25, 8.5), performance("p1", ["s1"], "Paper Lanterns")]
        return records, sheet([("t1", "paper lanterns", "Paper Lanterns")], **sheet_kwargs)

    def test_single_reviewed_performance_translates_every_rule(self):
        records, sheet_value = self.one_song()
        result = run(records, sheet_value)
        names = [batch["name"].split("-", 1)[1] for batch in result["batches"]]
        self.assertEqual([name.split("-")[0] for name in names], ["entities", "selection", "performance", "credit", "work"])
        self.assertEqual(result["report"]["totals"], {"translated": 2})
        [selection] = by_contract(result, "archive.claim/audio_selection")
        self.assertEqual(selection["payload"]["tracks"][0]["pieces"][0],
                         {"start_sample": 60000, "end_sample": 408000, "sample_rate": 48000})
        [selection_translation] = by_contract(result, "archive.migration/selection_translation")
        self.assertEqual(selection_translation["payload"]["source_seconds"], {"start": "1.25", "end": "8.5"})
        self.assertEqual(selection_translation["agent"], CONTEXT["by"])
        rules = sorted(draft["payload"]["rule"] for draft in by_contract(result, "archive.migration/translation"))
        self.assertEqual(rules, sorted(["v2-song-performance/1", "v2-performance-kind/1", "v2-performance-title/1",
                                        "v2-performance-review/1", "v2-performance-artists/1",
                                        "v2-performance-review/1", "v2-operator-identification/1"]))
        judgments = by_contract(result, "archive.assessment/judgment")
        self.assertEqual(len(judgments), 4)  # occurrence, kind, title, credit
        self.assertTrue(all(draft["payload"] == {"verdict": "affirmed"} for draft in judgments))
        [link] = by_contract(result, "archive.claim/performance_work")
        self.assertEqual(link["agent"], OPERATOR["object_id"])

    def test_batches_only_reference_records_already_planned(self):
        records, sheet_value = self.one_song()
        result = run(records, sheet_value)
        known = {(ARTIFACTS["art_a"]["object_id"], ARTIFACTS["art_a"]["revision_id"]),
                 (BUNDLE["object_id"], BUNDLE["revision_id"])}
        for batch in result["batches"]:
            for record in batch["records"]:
                draft = json.loads(record["bytes"])
                for ref in draft["refs"] + draft["supersedes"]:
                    self.assertIn((ref["id"], ref["revision_id"]), known)
                known.add((record["object_id"], record["revision_id"]))
                self.assertEqual(record["revision_id"], revision_of(draft))

    def test_correction_chain_is_one_selection_with_ordered_revisions(self):
        records = [segment("s1", 1, 2), segment("s2", 1.5, 2.5, supersedes="s1"),
                   segment("s3", 1.75, 3, supersedes="s2"), performance("p1", ["s2"], "Paper Lanterns")]
        result = run(records, sheet([("t1", "paper lanterns", "Paper Lanterns")]))
        selection_batches = [batch for batch in result["batches"] if "-selection-" in batch["name"]]
        self.assertEqual(len(selection_batches), 3)
        revisions = []
        for index, batch in enumerate(selection_batches):
            head = batch["manifest"]["expected_heads"][0]
            self.assertEqual(head["revision_id"], revisions[-1] if revisions else None)
            self.assertEqual(len(batch["manifest"]["identities"]), 2 if index == 0 else 1)
            revisions.append(batch["records"][0]["revision_id"])
        [occurrence] = by_contract(result, "archive.claim/song_occurrence")
        self.assertEqual(occurrence["refs"][0]["revision_id"], revisions[1])
        self.assertNotEqual(revisions[1], revisions[2])

    def test_fork_and_missing_predecessor_leave_chains_unresolved(self):
        records = [segment("s1", 1, 2), segment("s2", 1, 3, supersedes="s1"), segment("s3", 1, 4, supersedes="s1"),
                   segment("s9", 5, 6, supersedes="gone")]
        result = run(records, sheet([("t1", "paper lanterns", "Paper Lanterns")]))
        reasons = {row["id"]: row["reasons"] for row in result["report"]["rows"]}
        self.assertTrue(all("chain_fork" in reasons[name] for name in ("s1", "s2", "s3")))
        self.assertIn("missing_predecessor", reasons["s9"])
        self.assertEqual(result["report"]["totals"], {"unresolved": 4})
        self.assertEqual(by_contract(result, "archive.claim/audio_selection"), [])

    def test_missing_kind_makes_no_kind_claim_and_no_work_link(self):
        records = [segment("s1", 1, 2), performance("p1", ["s1"], "Paper Lanterns", kind=None)]
        result = run(records, sheet([("t1", "paper lanterns", "Paper Lanterns")]))
        self.assertEqual(by_contract(result, "archive.claim/selection_kind"), [])
        self.assertEqual(by_contract(result, "archive.claim/performance_work"), [])
        [entry] = result["report"]["performances"]
        self.assertEqual(entry["parts"]["kind"], "not_proposed:kind_absent")
        self.assertEqual(entry["work"], "not_proposed:kind_is_not_a_song")

    def test_same_as_groups_two_titles_into_one_work(self):
        records = [segment("s1", 1, 2), segment("s2", 3, 4), performance("p1", ["s1"], "Paper Lanterns"),
                   performance("p2", ["s2"], "paper lanterns!")]
        titles = [("t1", "paper lanterns", "Paper Lanterns"), ("t2", "paper lanterns x", "Paper Lanterns")]
        records[3]["title"] = "Paper Lanterns X"
        result = run(records, sheet(titles, decisions={"t2": {"same_as": "t1"}}))
        works = [draft for draft in drafts(result) if draft["shape"] == "entity" and draft["payload"]["kind"] == "musical_work"]
        self.assertEqual(len(works), 1)
        links = by_contract(result, "archive.claim/performance_work")
        self.assertEqual({link["refs"][1]["id"] for link in links}, {works[0]["object_id"]})
        self.assertEqual(len(links), 2)

    def test_unconfirmed_sheet_proposes_only_selections(self):
        records, sheet_value = self.one_song()
        sheet_value["decisions"]["t1"]["confirmed"] = False
        result = run(records, sheet_value)
        self.assertEqual([batch["name"].split("-")[1] for batch in result["batches"]], ["selection"])
        self.assertEqual(result["report"]["sheet"]["unconfirmed"], ["t1"])
        performance_row = [row for row in result["report"]["rows"] if row["schema"] == "song_performance"][0]
        self.assertEqual(performance_row["reasons"], ["sheet_incomplete"])

    def test_rerun_with_state_is_byte_identical_and_state_prevents_reminting(self):
        records, sheet_value = self.one_song()
        first = run(records, sheet_value)
        again = run(records, sheet_value, state=first["state"], new_key=lambda: self.fail("re-minted a key"))
        self.assertEqual(first["batches"], again["batches"])
        fresh = run(records, sheet_value, new_key=lambda: "00000000-0000-4000-8000-0000000000ff")
        self.assertNotEqual(first["batches"][0]["records"][0]["object_id"], fresh["batches"][0]["records"][0]["object_id"])

    def test_suggested_is_not_proposed_and_accounting_conserves(self):
        records = [segment("s1", 1, 2), performance("p1", ["s1"], "Paper Lanterns", state="suggested"),
                   performance("p2", ["s1"], "Paper Lanterns", state="mystery")]
        result = run(records, sheet([("t1", "paper lanterns", "Paper Lanterns")]))
        self.assertEqual(result["report"]["totals"], {"not_proposed": 1, "translated": 1, "unresolved": 1})
        self.assertEqual(result["report"]["route_rows"], 3)

    def test_exponent_spelled_boundary_is_kept_as_decimal_text(self):
        records = [segment("s1", 1, 2), performance("p1", ["s1"], "Paper Lanterns")]
        raw = json.dumps(records[0], sort_keys=True).replace('"start_seconds": 1', '"start_seconds": 1e-06').encode()
        result = run(records, sheet([("t1", "paper lanterns", "Paper Lanterns")]), raw_overrides={0: raw})
        [translation] = by_contract(result, "archive.migration/selection_translation")
        self.assertEqual(translation["payload"]["source_seconds"]["start"], "0.000001")

    def test_boundary_past_probed_end_is_unresolved(self):
        records = [segment("s1", 1, 601), performance("p1", ["s1"], "Paper Lanterns")]
        result = run(records, sheet([("t1", "paper lanterns", "Paper Lanterns")]))
        reasons = {row["id"]: row["reasons"] for row in result["report"]["rows"]}
        self.assertIn("boundary_past_artifact_end", reasons["s1"])


class OperatorRenditionTest(unittest.TestCase):
    """ADR 0050: segues, confirmed absences, and operator-confirmed renditions."""

    def test_segue_links_one_occurrence_to_each_listed_work(self):
        records = [segment("s1", 1, 2), segment("s2", 3, 4), segment("s3", 5, 6),
                   performance("p1", ["s1"], "Paper Lanterns"), performance("p2", ["s2"], "Harbor Light"),
                   performance("p3", ["s3"], "Paper Lanterns > Harbor Light")]
        titles = [("t1", "paper lanterns", "Paper Lanterns"), ("t2", "harbor light", "Harbor Light"), ("t3", "paper lanterns harbor light", None)]
        result = run(records, sheet(titles, decisions={"t3": {"works": ["t1", "t2"]}}))
        works = {draft["payload"]["label"]: draft["object_id"] for draft in drafts(result)
                 if draft["shape"] == "entity" and draft["payload"]["kind"] == "musical_work"}
        self.assertEqual(set(works), {"Paper Lanterns", "Harbor Light"})
        occurrences = {draft["object_id"]: draft for draft in by_contract(result, "archive.claim/song_occurrence")}
        segue_links = [link for link in by_contract(result, "archive.claim/performance_work")
                       if link["refs"][0]["id"] == allocate(ARCHIVE_ID, "archive.migration/song_occurrence",
                                                           "v2-performance-" + "p3".encode().hex())]
        self.assertEqual({link["refs"][1]["id"] for link in segue_links}, set(works.values()))
        self.assertEqual(len(occurrences), 3)
        entry = [item for item in result["report"]["performances"] if item["performance_id"] == "p3"][0]
        self.assertEqual(entry["work"], "translated:2_works")

    def test_no_work_keeps_occurrence_title_and_kind_and_reports_confirmed_absence(self):
        records = [segment("s1", 1, 2), performance("p1", ["s1"], "Space Jam", kind="original")]
        result = run(records, sheet([("t1", "space jam", None)], decisions={"t1": {"no_work": True}}))
        self.assertEqual(by_contract(result, "archive.claim/performance_work"), [])
        self.assertEqual(len(by_contract(result, "archive.claim/song_occurrence")), 1)
        self.assertEqual(len(by_contract(result, "archive.claim/selection_title")), 1)
        self.assertEqual(len(by_contract(result, "archive.claim/selection_kind")), 1)
        [entry] = result["report"]["performances"]
        self.assertEqual(entry["work"], "confirmed_no_work")
        self.assertEqual(result["report"]["totals"], {"translated": 2})
        works = [draft for draft in drafts(result) if draft["shape"] == "entity" and draft["payload"]["kind"] == "musical_work"]
        self.assertEqual(works, [])

    def rendition_records(self, first_start=1, second_start=5):
        return [segment("s1", first_start, first_start + 1), segment("s2", second_start, second_start + 1),
                performance("p1", ["s1"], "Paper Lanterns part 1", start=first_start),
                performance("p2", ["s2"], "Paper Lanterns part 2", start=second_start)]

    def rendition_sheet(self, groups=("t1", "t2")):
        return sheet([("t1", "paper lanterns part 1", "Paper Lanterns"), ("t2", "paper lanterns part 2", "Paper Lanterns")],
                     decisions={"t2": {"same_as": "t1"}},
                     renditions={"r1": {"groups": list(groups), "confirmed": True}})

    def test_rendition_is_one_occurrence_pinning_each_part_with_a_translation_per_part(self):
        result = run(self.rendition_records(), self.rendition_sheet())
        [occurrence] = by_contract(result, "archive.claim/song_occurrence")
        self.assertEqual(occurrence["object_id"], allocate(ARCHIVE_ID, "archive.migration/song_occurrence",
                                                           "v2-performance-" + "p1".encode().hex()))
        self.assertEqual(occurrence["agent"], OPERATOR["object_id"])
        selections = {draft["object_id"] for draft in by_contract(result, "archive.claim/audio_selection")}
        self.assertEqual({ref["id"] for ref in occurrence["refs"]}, selections)
        keys = [(ref["id"], ref["revision_id"]) for ref in occurrence["refs"]]
        self.assertEqual(keys, sorted(keys))
        occurrence_revision = revision_of(occurrence)
        part_translations = [draft for draft in by_contract(result, "archive.migration/translation")
                             if draft["payload"]["rule"] == "v2-song-performance/1"]
        self.assertEqual({draft["payload"]["source"]["id"] for draft in part_translations}, {"row-p1", "row-p2"})
        self.assertTrue(all(draft["refs"][0]["revision_id"] == occurrence_revision for draft in part_translations))
        judged = {draft["refs"][0]["id"] for draft in by_contract(result, "archive.assessment/judgment")}
        self.assertNotIn(occurrence["object_id"], judged)
        self.assertEqual(len(by_contract(result, "archive.claim/performance_work")), 1)
        self.assertEqual(result["report"]["totals"], {"translated": 4})

    def test_rendition_parts_carry_their_position_and_single_parts_do_not(self):
        result = run(self.rendition_records(), self.rendition_sheet())
        positions = {draft["payload"]["source"]["id"]: draft["payload"].get("part_position")
                     for draft in by_contract(result, "archive.migration/translation")
                     if draft["payload"]["rule"] == "v2-song-performance/1"}
        self.assertEqual(positions, {"row-p1": 1, "row-p2": 2})
        other = [draft["payload"] for draft in by_contract(result, "archive.migration/translation")
                 if draft["payload"]["rule"] != "v2-song-performance/1"]
        self.assertTrue(all("part_position" not in payload for payload in other))
        single = run([segment("s1", 1, 2), performance("p1", ["s1"], "Paper Lanterns")], sheet([("t1", "paper lanterns", "Paper Lanterns")]))
        [translation] = [draft for draft in by_contract(single, "archive.migration/translation")
                         if draft["payload"]["rule"] == "v2-song-performance/1"]
        self.assertNotIn("part_position", translation["payload"])

    def split_records(self, first_mtime, second_mtime):
        # A recorder split: part 1 ends file A, part 2 starts file B near zero.
        return (recorded_file("art_a", first_mtime) + recorded_file("art_b", second_mtime)
                + [segment("s1", 500, 590, artifact="art_a"), segment("s2", 0.5, 60, artifact="art_b"),
                   performance("p1", ["s1"], "Paper Lanterns part 1", artifact="art_a", start=500),
                   performance("p2", ["s2"], "Paper Lanterns part 2", artifact="art_b", start=0.5)])

    def test_split_across_files_is_ordered_by_observed_modification_time(self):
        early, late = 1752451234000000000, 1752454834000000000
        result = run(self.split_records(early, late), self.rendition_sheet())
        positions = {draft["payload"]["source"]["id"]: draft["payload"]["part_position"]
                     for draft in by_contract(result, "archive.migration/translation")
                     if draft["payload"]["rule"] == "v2-song-performance/1"}
        self.assertEqual(positions, {"row-p1": 1, "row-p2": 2})
        self.assertEqual(result["report"]["flags"], [])
        with self.assertRaisesRegex(SheetError, "out of recording order"):
            run(self.split_records(late, early), self.rendition_sheet())

    def test_split_without_or_with_tied_modification_times_is_refused(self):
        with self.assertRaisesRegex(SheetError, "no single source_observation.observed_mtime"):
            run(self.split_records(1752451234000000000, None), self.rendition_sheet())
        with self.assertRaisesRegex(SheetError, "share one source_observation.observed_mtime"):
            run(self.split_records(1752451234000000000, 1752451234000000000), self.rendition_sheet())

    def test_rendition_out_of_recording_order_is_refused(self):
        with self.assertRaisesRegex(SheetError, "out of recording order"):
            run(self.rendition_records(first_start=9, second_start=1), self.rendition_sheet())

    def test_rendition_group_must_hold_exactly_one_reviewed_performance(self):
        records = self.rendition_records() + [segment("s3", 20, 21),
                                              performance("p3", ["s3"], "Paper Lanterns part 2", start=20)]
        with self.assertRaisesRegex(SheetError, "exactly one is required"):
            run(records, self.rendition_sheet())

    def test_unconfirmed_rendition_makes_the_sheet_incomplete(self):
        sheet_value = self.rendition_sheet()
        sheet_value["renditions"]["r1"]["confirmed"] = False
        result = run(self.rendition_records(), sheet_value)
        self.assertEqual(result["report"]["sheet"]["unconfirmed"], ["r1"])
        self.assertEqual(by_contract(result, "archive.claim/song_occurrence"), [])

    def test_titles_alone_never_group_parts(self):
        result = run(self.rendition_records(), sheet([("t1", "paper lanterns part 1", "Paper Lanterns"),
                                                      ("t2", "paper lanterns part 2", "Paper Lanterns")],
                                                     decisions={"t2": {"same_as": "t1"}}))
        self.assertEqual(len(by_contract(result, "archive.claim/song_occurrence")), 2)


class ProbeTest(unittest.TestCase):
    def test_probe_report_counts_samples_only_in_sample_time_base(self):
        report = probe_report({
            "a": {"streams": [{"codec_type": "audio", "sample_rate": "48000", "time_base": "1/48000",
                               "duration_ts": 96000}]},
            "b": {"streams": [{"codec_type": "audio", "sample_rate": "44100", "time_base": "1/1000",
                               "duration_ts": 5000}]},
        }, "7.1")
        self.assertEqual(report["a"], {"sample_rate": 48000, "tool": "ffprobe", "tool_version": "7.1", "samples": 96000})
        self.assertNotIn("samples", report["b"])
        with self.assertRaises(TranslateError):
            probe_report({"c": {"streams": []}}, "7.1")


@unittest.skipUnless(os.environ.get("ARCHIVE_TEST_DOOR") and os.environ.get("ARCHIVE_TEST_ARCHIVE"),
                     "set ARCHIVE_TEST_DOOR and ARCHIVE_TEST_ARCHIVE (absolute built door and archive paths) "
                     "to check revision prediction against Door's dry run")
class DoorPredictionTest(unittest.TestCase):
    def test_planned_entity_revisions_match_door_dry_run(self):
        door, archive = os.environ["ARCHIVE_TEST_DOOR"], os.environ["ARCHIVE_TEST_ARCHIVE"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "archive"
            subprocess.run([archive, "init", "--operator", "operator", str(root)], check=True, capture_output=True)
            export = json.loads(subprocess.run([archive, "export"], cwd=root, check=True,
                                               capture_output=True, text=True).stdout)
            person = [record for record in export["records"] if record["payload"].get("kind") == "person"][0]
            context = dict(CONTEXT, archive_id=export["archive_id"], spec=export["spec"],
                           operator={"object_id": person["object_id"], "revision_id": person["revision_id"]})
            # Only the entities batch cites nothing outside the installed release.
            result = plan([], context, citations(), ARTIFACTS, PROBES,
                          sheet([("t1", "paper lanterns", "Paper Lanterns")]), {}, Keys())
            entities = {"batches": [batch for batch in result["batches"] if batch["name"].endswith("entities")],
                        "report": result["report"]}
            self.assertEqual(len(entities["batches"]), 1)
            out = Path(directory) / "plan"
            write_plan(entities, out)
            proposal = out / entities["batches"][0]["name"]
            completed = subprocess.run([door, "admit", "--proposal", str(proposal), "--dry-run"], cwd=root,
                                       capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            planned = json.loads((proposal / "planned-revisions.json").read_text())
            self.assertEqual(json.loads(completed.stdout)["revision_ids"], planned)


if __name__ == "__main__":
    unittest.main()
