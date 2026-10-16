import io
import hashlib
import json
import math
import os
import tarfile
import tempfile
import unittest
from pathlib import Path

from migration.audio_route import AudioRouteError, analyze_bundle, analyze_records
from migration.v2_bundle import build_candidate, verify_candidate


_ORIGINAL_TEMPDIR = tempfile.tempdir


def setUpModule():
    tempfile.tempdir = os.path.realpath(tempfile.gettempdir())


def tearDownModule():
    tempfile.tempdir = _ORIGINAL_TEMPDIR


def _digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


class AudioRouteTest(unittest.TestCase):
    def _bundle(self, records):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "records.json").write_bytes(b"[" + b",".join(records) + b"]")
            bundle = build_candidate(root)
        manifest = verify_candidate(bundle)
        with tarfile.open(fileobj=io.BytesIO(bundle), mode="r:") as archive:
            members = {name: archive.extractfile(name).read() for name in archive.getnames()
                       if name != "manifest.json"}
        return manifest, members

    def test_bundle_reports_reviewed_relationship_with_exact_source_locators(self):
        records = [
            b'{"schema":"artifact","artifact_id":"a1"}',
            b'{"schema":"audio_segment","segment_id":"s1","source_artifact_id":"a1","review_state":"reviewed","start_seconds":1.25,"end_seconds":8.5}',
            b'{"schema":"song_performance","performance_id":"p1","source_artifact_id":"a1","segment_ids":["s1"],"title":"Song title","state":"reviewed"}',
        ]
        manifest, members = self._bundle(records)

        report = analyze_bundle(manifest, members)

        self.assertEqual(report["eligible_relationship_count"], 1)
        relation = report["relationships"][0]
        self.assertEqual(relation["eligibility"], "eligible_reviewed_source_relationship")
        self.assertEqual(relation["archive_route"], "unresolved")
        self.assertEqual(relation["song_entity_id"], None)
        self.assertFalse(report["archive_claims_proposed"])
        self.assertFalse(report["archive_entity_ids_allocated"])
        self.assertEqual(relation["performance"]["offset"], manifest["records"][2]["offset"])
        self.assertEqual(relation["segments"][0]["locator"]["sha256"], manifest["records"][1]["sha256"])

    def test_real_bundle_manifest_source_identity_is_cross_checked(self):
        manifest, members = self._bundle([
            b'{"schema":"artifact","id":"row-a1","artifact_id":"a1"}',
            b'{"schema":"audio_segment","id":"row-s1","segment_id":"s1","source_artifact_id":"a1","review_state":"reviewed","start_seconds":1.25,"end_seconds":8.5}',
            b'{"schema":"song_performance","id":"row-p1","performance_id":"p1","source_artifact_id":"a1","segment_ids":["s1"],"title":"Song","state":"reviewed"}',
        ])

        report = analyze_bundle(manifest, members)

        self.assertEqual(report["eligible_relationship_count"], 1)
        relation = report["relationships"][0]
        self.assertEqual(relation["performance"]["id"], "p1")
        self.assertEqual(relation["performance"]["source"], {"schema": "song_performance", "id": "row-p1"})
        self.assertEqual(relation["segments"][0]["locator"]["id"], "s1")
        self.assertEqual(relation["segments"][0]["locator"]["source"], {"schema": "audio_segment", "id": "row-s1"})

        manifest["records"][2]["source"]["id"] = "different-source-row"
        with self.assertRaisesRegex(AudioRouteError, "manifest source identity does not match source bytes"):
            analyze_bundle(manifest, members)

    def test_real_bundle_invalid_boundaries_are_unresolved_and_preserved(self):
        invalid_segments = [
            ({"start_seconds": 1.0}, "segment_end_boundary_missing"),
            ({"end_seconds": 2.0}, "segment_start_boundary_missing"),
            ({"start_seconds": "1", "end_seconds": 2.0}, "segment_start_boundary_not_numeric"),
            ({"start_seconds": -1.0, "end_seconds": 2.0}, "segment_start_boundary_negative"),
            ({"start_seconds": 3.0, "end_seconds": 2.0}, "segment_boundaries_reversed"),
            ({"start_seconds": 2.0, "end_seconds": 2.0}, "segment_boundaries_empty"),
        ]
        for index, (boundaries, reason) in enumerate(invalid_segments):
            with self.subTest(boundaries=boundaries):
                segment = {
                    "schema": "audio_segment", "id": f"source-s{index}", "segment_id": "s1",
                    "source_artifact_id": "a1", "review_state": "reviewed",
                    **boundaries,
                }
                manifest, members = self._bundle([
                    b'{"schema":"artifact","id":"source-a1","artifact_id":"a1"}',
                    json.dumps(segment, separators=(",", ":")).encode(),
                    b'{"schema":"song_performance","id":"source-p1","performance_id":"p1","source_artifact_id":"a1","segment_ids":["s1"],"title":"Song","state":"reviewed"}',
                ])

                report = analyze_bundle(manifest, members)

                relation = report["relationships"][0]
                self.assertEqual(relation["eligibility"], "unresolved")
                self.assertIn(reason, relation["eligibility_reasons"])
                self.assertEqual(relation["segments"][0]["start_seconds"], boundaries.get("start_seconds"))
                self.assertEqual(relation["segments"][0]["end_seconds"], boundaries.get("end_seconds"))
                self.assertFalse(report["archive_claims_proposed"])
                self.assertFalse(report["archive_entity_ids_allocated"])

    def test_nonfinite_boundaries_are_unresolved_without_coercion(self):
        for value, reason in ((math.inf, "segment_start_boundary_not_finite"),
                              (math.nan, "segment_end_boundary_not_finite")):
            with self.subTest(value=value):
                direct = [
                    {"record": {"schema": "artifact", "artifact_id": "a1"},
                     "locator": {"member": "source/a.json", "offset": 0, "length": 20,
                                 "sha256": "sha256:" + "0" * 64, "schema": "artifact", "id": "a1"}},
                    {"record": {"schema": "audio_segment", "segment_id": "s1", "source_artifact_id": "a1",
                                 "review_state": "reviewed", "start_seconds": value if math.isinf(value) else 1.0,
                                 "end_seconds": value if math.isnan(value) else 2.0},
                     "locator": {"member": "source/s.json", "offset": 0, "length": 20,
                                 "sha256": "sha256:" + "0" * 64, "schema": "audio_segment", "id": "s1"}},
                    {"record": {"schema": "song_performance", "performance_id": "p1", "source_artifact_id": "a1",
                                 "segment_ids": ["s1"], "title": "Song", "state": "reviewed"},
                     "locator": {"member": "source/p.json", "offset": 0, "length": 20,
                                 "sha256": "sha256:" + "0" * 64, "schema": "song_performance", "id": "p1"}},
                ]
                report = analyze_records(direct)
                relation = report["relationships"][0]
                self.assertEqual(relation["eligibility"], "unresolved")
                self.assertIn(reason, relation["eligibility_reasons"])
                boundary = "start_seconds" if math.isinf(value) else "end_seconds"
                if math.isnan(value):
                    self.assertTrue(math.isnan(relation["segments"][0][boundary]))
                else:
                    self.assertEqual(relation["segments"][0][boundary], value)

    def test_every_performance_state_and_missing_reference_stays_visible(self):
        records = [
            b'{"schema":"artifact","artifact_id":"a1"}',
            b'{"schema":"audio_segment","segment_id":"s1","source_artifact_id":"a1","review_state":"suggested"}',
            b'{"schema":"song_performance","performance_id":"reviewed-missing-segment","source_artifact_id":"a1","segment_ids":["gone"],"state":"reviewed"}',
            b'{"schema":"song_performance","performance_id":"suggested","source_artifact_id":"a1","segment_ids":["s1"],"state":"suggested"}',
            b'{"schema":"song_performance","performance_id":"retracted","source_artifact_id":"missing-artifact","segment_ids":["s1"],"state":"retracted"}',
        ]
        manifest, members = self._bundle(records)

        report = analyze_bundle(manifest, members)

        self.assertEqual(report["source_row_count"], 5)
        self.assertEqual(len(report["relationships"]), 3)
        indexed = {row["state"]: row for row in report["relationships"]}
        self.assertIn("referenced_segment_missing", indexed["reviewed"]["eligibility_reasons"])
        self.assertEqual(indexed["suggested"]["eligibility"], "unresolved")
        self.assertIn("referenced_segment_not_reviewed", indexed["suggested"]["eligibility_reasons"])
        self.assertIn("performance_is_retracted", indexed["retracted"]["eligibility_reasons"])
        self.assertIn("source_artifact_missing", indexed["retracted"]["eligibility_reasons"])

    def test_manifest_and_record_byte_mismatches_fail_closed(self):
        manifest, members = self._bundle([b'{"schema":"song_performance","performance_id":"p1","state":"reviewed"}'])
        wrong = dict(members)
        wrong["source/records.json"] += b" "
        with self.assertRaises(AudioRouteError):
            analyze_bundle(manifest, wrong)

        manifest, members = self._bundle([b'{"schema":"song_performance","performance_id":"p1","state":"reviewed"}'])
        manifest["records"][0]["source"] = {"schema": "song_performance", "id": "different"}
        with self.assertRaises(AudioRouteError):
            analyze_bundle(manifest, members)

    def test_unrelated_bundle_rows_are_verified_then_skipped(self):
        records = [
            b'{"schema":"job","job_id":"job1"}',
            b'{"schema":"artifact","artifact_id":"a1"}',
            b'{"schema":"audio_segment","segment_id":"s1","source_artifact_id":"a1","review_state":"reviewed","start_seconds":0,"end_seconds":1}',
            b'{"schema":"song_performance","performance_id":"p1","source_artifact_id":"a1","segment_ids":["s1"],"title":"Song","state":"reviewed"}',
        ]
        manifest, members = self._bundle(records)

        report = analyze_bundle(manifest, members)

        self.assertEqual(report["source_row_count"], 3)
        self.assertEqual(report["eligible_relationship_count"], 1)

    def test_performance_successor_is_preserved_and_never_called_current(self):
        direct = []
        for performance_id, supersedes in (("p1", None), ("p2", "p1")):
            record = {"schema": "song_performance", "performance_id": performance_id,
                      "source_artifact_id": "a1", "segment_ids": ["s1"],
                      "title": "Song", "state": "reviewed"}
            if supersedes:
                record["supersedes_performance_id"] = supersedes
            direct.append({
                "record": record,
                "locator": {"member": "source/p.jsonl", "offset": len(direct) * 100,
                            "length": 90, "sha256": "sha256:" + "0" * 64,
                            "schema": "song_performance", "id": performance_id},
            })
        direct.extend([
            {"record": {"schema": "artifact", "artifact_id": "a1"},
             "locator": {"member": "source/a.json", "offset": 0, "length": 20,
                         "sha256": "sha256:" + "0" * 64, "schema": "artifact", "id": "a1"}},
            {"record": {"schema": "audio_segment", "segment_id": "s1", "source_artifact_id": "a1",
                         "review_state": "reviewed", "start_seconds": 0, "end_seconds": 1},
             "locator": {"member": "source/s.json", "offset": 0, "length": 20,
                         "sha256": "sha256:" + "0" * 64, "schema": "audio_segment", "id": "s1"}},
        ])

        report = analyze_records(direct)

        previous = report["relationships"][0]
        self.assertEqual(previous["superseded_by_performance_ids"], ["p2"])
        self.assertIn("performance_has_a_source_successor_currentness_unresolved", previous["eligibility_reasons"])
        self.assertEqual(report["eligible_relationship_count"], 1)

    def test_direct_safe_inputs_use_the_same_conservative_relationship_rules(self):
        direct = [{
            "record": {"schema": "song_performance", "performance_id": "p1",
                       "source_artifact_id": "a1", "segment_ids": ["s1"],
                       "title": "Evidence only", "state": "reviewed"},
            "locator": {"member": "source/p.json", "offset": 0, "length": 100,
                        "sha256": "sha256:" + "0" * 64, "schema": "song_performance", "id": "p1"},
        }]

        report = analyze_records(direct, artifact_ids=["a1"])

        self.assertEqual(report["relationships"][0]["eligibility"], "unresolved")
        self.assertIn("referenced_segment_missing", report["relationships"][0]["eligibility_reasons"])
        self.assertIsNone(report["relationships"][0]["song_entity_id"])


if __name__ == "__main__":
    unittest.main()
