import argparse
import json
import tempfile
import unittest
from pathlib import Path

from migration.census import census
from migration.source_manifest import source_manifest
from shadow.compare import (
    HARD_MAX_REPORT_BYTES,
    Refusal,
    _read_report,
    _report_size_limit,
    compare,
)

_original_tempdir = tempfile.tempdir


def setUpModule():
    tempfile.tempdir = str(Path(tempfile.gettempdir()).resolve())


def tearDownModule():
    tempfile.tempdir = _original_tempdir


class CompareTest(unittest.TestCase):
    def test_report_reader_accepts_explicit_bound_and_refuses_over_bound(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "report.json"
            path.write_text('{"ok":true}', encoding="utf-8")

            self.assertEqual(_read_report(path, max_bytes=11), {"ok": True})
            with self.assertRaisesRegex(Refusal, "report_size_limit_exceeded"):
                _read_report(path, max_bytes=10)

    def test_report_size_limit_is_positive_and_hard_capped(self):
        self.assertEqual(_report_size_limit("180000000"), 180000000)
        self.assertLessEqual(180000000, HARD_MAX_REPORT_BYTES)
        for value in ("0", str(HARD_MAX_REPORT_BYTES + 1), "many"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                _report_size_limit(value)

    def _reports(self, root: Path, label: str):
        root.mkdir()
        (root / "record.json").write_text(
            json.dumps({"schema": "artifact", "label": label}),
            encoding="utf-8",
        )
        manifest = source_manifest(root)
        manifest_path = root.parent / f"{root.name}-manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return manifest, census(root, manifest_path)

    def test_matching_manifest_and_census_reports_do_not_verify_semantics(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "source"
            manifest, census_report = self._reports(root, "same bytes")

            result = compare("records", manifest, manifest, census_report, census_report)

        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["migration_status"], "unverified")
        self.assertFalse(result["reports_authenticated"])
        self.assertFalse(result["source_bytes_verified_by_comparator"])
        self.assertEqual(result["source_census"]["unresolved_route_records"], 1)
        self.assertFalse(result["claims_translated"])

    def test_census_from_same_counts_but_different_manifest_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source_manifest_report, source_census = self._reports(base / "source", "first label")
            stale_manifest_report, stale_census = self._reports(base / "stale", "other label")
            self.assertEqual(source_census["schemas_observed"], stale_census["schemas_observed"])

            with self.assertRaisesRegex(Refusal, "census_manifest_binding_not_matched"):
                compare(
                    "records",
                    source_manifest_report,
                    source_manifest_report,
                    stale_census,
                    source_census,
                )
            self.assertNotEqual(source_manifest_report, stale_manifest_report)

    def test_edited_empty_census_cannot_claim_complete_migration(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest, report = self._reports(Path(temporary) / "source", "actual record")
            report["counts"] = {key: 0 for key in report["counts"]}
            report["entries_accounted"] = 0
            report["schemas_observed"] = {}
            report["schema_routes"] = {}
            report["top_level_fields_by_schema"] = {}
            report["count_conservation"].update({
                "catalog_record_count": 0,
                "unknown_schema_record_count": 0,
                "schema_tagged_record_count": 0,
            })

            result = compare("records", manifest, manifest, report, report)

        self.assertEqual(result["migration_status"], "unverified")
        self.assertFalse(result["reports_authenticated"])


if __name__ == "__main__":
    unittest.main()
