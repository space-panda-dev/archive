import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from migration.catalog import SOURCE_SCHEMAS
from migration.census import census
from migration.source_manifest import manifest_fingerprint, source_manifest

_original_tempdir = tempfile.tempdir


def setUpModule():
    tempfile.tempdir = os.path.realpath(tempfile.gettempdir())


def tearDownModule():
    tempfile.tempdir = _original_tempdir


class CensusTest(unittest.TestCase):
    def test_symlink_in_root_parent_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            actual = base / "actual"
            root = actual / "records"
            root.mkdir(parents=True)
            (root / "record.json").write_text('{"schema":"artifact"}', encoding="utf-8")
            (base / "alias").symlink_to(actual, target_is_directory=True)

            report = census(base / "alias" / "records")

        self.assertFalse(report["complete"])
        self.assertTrue(any(item["kind"] == "root_symlink_unsupported" for item in report["diagnostics"]))
        self.assertEqual(report["counts"]["record"], 0)

    def _write_source_manifest(self, root: Path, destination: Path) -> None:
        destination.write_text(json.dumps(source_manifest(root)), encoding="utf-8")

    def test_census_uses_the_exact_source_catalog_and_keeps_all_routes_unresolved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "records.json").write_text(
                json.dumps([{"schema": "artifact"}, {"schema": "session"}]),
                encoding="utf-8",
            )
            report = census(root)

        self.assertEqual(len(SOURCE_SCHEMAS), 46)
        self.assertEqual(report["source_schema_catalog"], list(SOURCE_SCHEMAS))
        self.assertEqual(set(report["schema_routes"]), {"artifact", "session"})
        self.assertEqual(report["schema_routes"]["artifact"]["status"], "unresolved")
        self.assertEqual(report["schema_routes"]["session"]["status"], "unresolved")
        self.assertEqual(report["count_conservation"]["catalog_record_count"], 2)
        self.assertTrue(report["count_conservation"]["all_schema_tagged_records_accounted_for"])
        self.assertEqual(report["count_conservation"]["scope"], "internal_partition_consistency")
        self.assertFalse(report["count_conservation"]["migration_supported"])

    def test_source_catalog_matches_the_v2_vocabulary_fingerprint(self):
        # Fingerprint of PRODUCTION_SCHEMAS in the read-only V2 disposition.py.
        fingerprint = hashlib.sha256(("\n".join(SOURCE_SCHEMAS) + "\n").encode()).hexdigest()
        self.assertEqual(fingerprint, "011c240c8fc71450bc7d979104ee93ded983f606ff845abc37ccf260c2bbdfa0")

    def test_cli_uses_catalog_and_fails_for_unresolved_or_unknown_schemas(self):
        from migration.census import main

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "known.json").write_text('{"schema":"artifact"}', encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                known_status = main([str(root)])
            self.assertEqual(known_status, 1)
            known_report = json.loads(output.getvalue())
            self.assertEqual(known_report["source_schema_catalog"], list(SOURCE_SCHEMAS))
            self.assertEqual(known_report["schema_routes"]["artifact"]["status"], "unresolved")

            (root / "known.json").write_text('{"schema":"future"}', encoding="utf-8")
            (root / "missing.json").write_text("{}", encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                unknown_status = main([str(root)])
            self.assertEqual(unknown_status, 1)
            self.assertEqual(json.loads(output.getvalue())["schema_routes"]["future"]["status"], "unsupported")
            self.assertIn("missing_schema", [item["kind"] for item in json.loads(output.getvalue())["diagnostics"]])

    def test_real_cli_dispatch_counts_distinct_top_level_source_contract_stamps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records = root / "records.jsonl"
            citation = "specs/changes/2000-01-01-synthetic/requirements.md"
            records.write_text("\n".join([
                json.dumps({"schema": "job", "contracts": [citation, citation]}),
                json.dumps({"schema": "job", "contracts": [citation]}),
            ]) + "\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-m", "migration.census", str(root)],
                cwd=Path(__file__).resolve().parents[1],
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.returncode, 1)  # known source routes remain unresolved
        report = json.loads(result.stdout)
        self.assertEqual(report["source_contract_stamps"]["records_with_plan_contracts"], 2)
        self.assertEqual(report["source_contract_stamps"]["citations_by_contract"], {citation: 2})
        self.assertEqual(report["source_contract_stamps"]["records_by_schema"], {"job": 2})
        self.assertTrue(report["source_contract_stamps"]["complete"])
        self.assertTrue(report["source_contract_stamps"]["source_coverage_complete"])

    def test_complete_source_manifest_binds_exact_paths_bytes_and_hashes(self):
        with tempfile.TemporaryDirectory() as source_directory, tempfile.TemporaryDirectory() as report_directory:
            root = Path(source_directory)
            (root / "nested").mkdir()
            (root / "nested" / "records.json").write_text('{"schema":"artifact"}', encoding="utf-8")
            manifest_path = Path(report_directory) / "source-manifest.json"
            source_report = source_manifest(root)
            self._write_source_manifest(root, manifest_path)

            report = census(root, manifest_path)
            expected_manifest_sha256 = manifest_fingerprint(source_report)

        self.assertEqual(report["source_manifest_binding"], {
            "status": "matched",
            "format": "archive-source-manifest/1",
            "atomic_snapshot": False,
            "requires_frozen_source": True,
            "manifest_fingerprint_format": "archive-source-manifest-json-fingerprint/1",
            "manifest_sha256": expected_manifest_sha256,
        })
        self.assertEqual(report["files_discovered"], 1)
        self.assertEqual(report["counts"]["record"], 1)
        self.assertTrue(report["complete"])

    def test_source_manifest_binding_detects_changed_bytes(self):
        with tempfile.TemporaryDirectory() as source_directory, tempfile.TemporaryDirectory() as report_directory:
            root = Path(source_directory)
            source = root / "records.json"
            source.write_text('{"schema":"artifact"}', encoding="utf-8")
            manifest_path = Path(report_directory) / "manifest.json"
            self._write_source_manifest(root, manifest_path)
            source.write_text('{"schema":"claim"}', encoding="utf-8")

            report = census(root, manifest_path)

        self.assertFalse(report["complete"])
        self.assertEqual(report["source_manifest_binding"]["status"], "mismatch")
        self.assertIn("source_manifest_mismatch", [item["kind"] for item in report["diagnostics"]])

    def test_source_manifest_binding_detects_omitted_and_extra_files(self):
        with tempfile.TemporaryDirectory() as source_directory, tempfile.TemporaryDirectory() as report_directory:
            root = Path(source_directory)
            (root / "one.json").write_text('{"schema":"artifact"}', encoding="utf-8")
            (root / "two.json").write_text('{"schema":"claim"}', encoding="utf-8")
            manifest_path = Path(report_directory) / "manifest.json"
            complete = source_manifest(root)
            complete["files"] = complete["files"][:1]
            complete["file_count"] = 1
            complete["total_bytes"] = complete["files"][0]["bytes"]
            manifest_path.write_text(json.dumps(complete), encoding="utf-8")

            omitted = census(root, manifest_path)
            self._write_source_manifest(root, manifest_path)
            (root / "three.json").write_text('{"schema":"entity"}', encoding="utf-8")
            extra = census(root, manifest_path)

        self.assertEqual(omitted["source_manifest_binding"]["status"], "mismatch")
        self.assertEqual(extra["source_manifest_binding"]["status"], "mismatch")
        self.assertFalse(omitted["complete"])
        self.assertFalse(extra["complete"])

    def test_invalid_source_manifest_fails_closed(self):
        with tempfile.TemporaryDirectory() as source_directory, tempfile.TemporaryDirectory() as report_directory:
            root = Path(source_directory)
            (root / "records.json").write_text('{"schema":"artifact"}', encoding="utf-8")
            manifest_path = Path(report_directory) / "manifest.json"
            manifest_path.write_text('{"format":"archive-source-manifest/1","format":"bad"}', encoding="utf-8")

            report = census(root, manifest_path)

        self.assertFalse(report["complete"])
        self.assertEqual(report["source_manifest_binding"]["status"], "invalid")
        self.assertIn("source_manifest_invalid", [item["kind"] for item in report["diagnostics"]])

    def test_real_cli_dispatch_accepts_source_manifest_binding(self):
        with tempfile.TemporaryDirectory() as source_directory, tempfile.TemporaryDirectory() as report_directory:
            root = Path(source_directory)
            (root / "records.json").write_text('{"schema":"artifact"}', encoding="utf-8")
            manifest_path = Path(report_directory) / "manifest.json"
            self._write_source_manifest(root, manifest_path)
            result = subprocess.run(
                [sys.executable, "-m", "migration.census", str(root), "--source-manifest", str(manifest_path)],
                cwd=Path(__file__).resolve().parents[1],
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.returncode, 1)  # bound successfully; known routes remain unresolved
        report = json.loads(result.stdout)
        self.assertEqual(report["source_manifest_binding"]["status"], "matched")
        self.assertFalse(report["source_manifest_binding"]["atomic_snapshot"])

    def test_source_manifest_fifo_is_refused_without_blocking(self):
        with tempfile.TemporaryDirectory() as source_directory, tempfile.TemporaryDirectory() as report_directory:
            root = Path(source_directory)
            (root / "records.json").write_text('{"schema":"artifact"}', encoding="utf-8")
            manifest_path = Path(report_directory) / "manifest.json"
            os.mkfifo(manifest_path)
            result = subprocess.run(
                [sys.executable, "-m", "migration.census", str(root), "--source-manifest", str(manifest_path)],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["source_manifest_binding"]["status"], "invalid")

    def test_source_manifest_binding_detects_file_added_after_initial_rescan(self):
        with tempfile.TemporaryDirectory() as source_directory, tempfile.TemporaryDirectory() as report_directory:
            root = Path(source_directory)
            (root / "records.json").write_text('{"schema":"artifact"}', encoding="utf-8")
            manifest_path = Path(report_directory) / "manifest.json"
            self._write_source_manifest(root, manifest_path)
            scans = 0

            def add_after_first_scan(path: Path):
                nonlocal scans
                report = source_manifest(path)
                scans += 1
                if scans == 1:
                    (root / "late.json").write_text('{"schema":"claim"}', encoding="utf-8")
                return report

            with patch("migration.census.scan_source_manifest", side_effect=add_after_first_scan):
                report = census(root, manifest_path)
        self.assertEqual(scans, 2)
        self.assertFalse(report["complete"])
        self.assertEqual(report["source_manifest_binding"]["status"], "mismatch")

    def test_contract_citation_census_refuses_malformed_source_stamps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            citation = "specs/changes/2000-01-01-synthetic/requirements.md"
            (root / "records.json").write_text(json.dumps([
                {"schema": "job", "contracts": [citation]},
                {"schema": "job", "contracts": ["specs/changes/no-requirements.txt"]},
                {"schema": "job", "contracts": [citation, 7]},
                {"schema": "job", "contracts": citation},
                {"schema": "job", "contracts": ["specs/changes/../requirements.md"]},
                {"schema": "job", "contracts": ["specs/changes/has space/requirements.md"]},
                {"schema": "job", "contracts": ["specs/changes/has\nnewline/requirements.md"]},
                {"schema": "github_metadata", "body": citation},
                {"schema": "record_revision", "snapshot": {"contracts": [citation]}},
            ]), encoding="utf-8")
            report = census(root)

        contract_report = report["source_contract_stamps"]
        kinds = [item["kind"] for item in contract_report["diagnostics"]]
        self.assertFalse(contract_report["complete"])
        self.assertEqual(kinds.count("malformed_contract_citation"), 6)
        self.assertEqual(contract_report["records_with_plan_contracts"], 2)
        self.assertEqual(contract_report["citations_by_contract"], {citation: 2})
        self.assertFalse(report["complete"])

    def test_safe_specs_markdown_citations_are_not_plan_stamps_or_malformed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan_stamp = "specs/changes/2000-01-01-synthetic/requirements.md"
            source_citations = [
                "specs/provenance.md",
                "specs/artifact-model.md",
                "specs/decisions/0012-inherited-meaning.md",
            ]
            (root / "records.json").write_text(json.dumps([
                {"schema": "job", "contracts": [*source_citations, plan_stamp]},
            ]), encoding="utf-8")
            report = census(root)

        contract_report = report["source_contract_stamps"]
        self.assertTrue(contract_report["complete"])
        self.assertEqual(contract_report["records_with_plan_contracts"], 1)
        self.assertEqual(contract_report["citations_by_contract"], {plan_stamp: 1})
        self.assertEqual(contract_report["diagnostics"], [])
        self.assertTrue(report["complete"])

    def test_contract_citation_rejects_unsafe_non_stamp_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unsafe = [
                "specs/../secrets.md",
                "specs//provenance.md",
                "specs/changes/../../requirements.md",
                "/specs/provenance.md",
                "https://example.test/specs/provenance.md",
                "specs/provenance.txt",
            ]
            (root / "records.json").write_text(json.dumps([
                {"schema": "job", "contracts": unsafe},
            ]), encoding="utf-8")
            report = census(root)

        contract_report = report["source_contract_stamps"]
        self.assertFalse(contract_report["complete"])
        self.assertEqual(
            [item["kind"] for item in contract_report["diagnostics"]],
            ["malformed_contract_citation"] * len(unsafe),
        )
        self.assertEqual(contract_report["records_with_plan_contracts"], 0)
        self.assertEqual(contract_report["citations_by_contract"], {})

    def test_json_jsonl_timestamps_unknown_missing_schema_and_non_record_are_accounted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "records.json").write_text(json.dumps([
                {"schema": SOURCE_SCHEMAS[0], "at": "2030-01-02T03:04:05Z"},
                {"schema": "future", "timestamp": "later"},
                {"at": "2030-01-02"},
                7,
            ]), encoding="utf-8")
            (root / "rows.jsonl").write_text(
                f'{{"schema":"{SOURCE_SCHEMAS[0]}"}}\nnot-json\nfalse\n', encoding="utf-8"
            )
            report = census(root)

        self.assertFalse(report["complete"])
        self.assertEqual(report["files_discovered"], report["files_accounted"])
        self.assertEqual(report["files_by_suffix"], {".json": 1, ".jsonl": 1})
        self.assertEqual(report["counts"], {
            "record": 2,
            "unknown_schema": 1,
            "missing_schema": 1,
            "non_record": 2,
            "malformed": 1,
            "unreadable": 0,
            "unsupported": 0,
        })
        self.assertEqual(report["schemas_observed"], {"future": 1, SOURCE_SCHEMAS[0]: 2})
        self.assertEqual(report["schema_routes"][SOURCE_SCHEMAS[0]]["status"], "unresolved")
        self.assertEqual(report["unknown_schemas"], {"future": 1})
        self.assertEqual(report["count_conservation"]["schema_tagged_record_count"], 3)
        self.assertTrue(report["count_conservation"]["all_schema_tagged_records_accounted_for"])
        self.assertFalse(report["count_conservation"]["migration_supported"])
        self.assertEqual(report["timestamp_fields_observed"], 3)
        self.assertIn({"source": "rows.jsonl", "line": 2, "kind": "malformed_jsonl", "column": 1}, report["diagnostics"])

    def test_unsupported_file_and_missing_schema_are_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "timestamp.txt").write_text("2030-01-02T03:04:05Z", encoding="utf-8")
            (root / "record.json").write_text('{"schema": null}', encoding="utf-8")
            report = census(root)

        self.assertFalse(report["complete"])
        self.assertEqual(report["counts"]["missing_schema"], 1)
        self.assertEqual(report["counts"]["unsupported"], 1)
        self.assertEqual(report["files_discovered"], report["files_accounted"])
        self.assertIn("unsupported_file", [item["kind"] for item in report["diagnostics"]])

    def test_unknown_and_missing_schema_are_incomplete_without_parse_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "unknown.json").write_text('{"schema":"future"}', encoding="utf-8")
            (root / "missing.json").write_text('{"schema":42}', encoding="utf-8")
            report = census(root)

        self.assertFalse(report["complete"])
        self.assertEqual(report["counts"]["unknown_schema"], 1)
        self.assertEqual(report["counts"]["missing_schema"], 1)
        self.assertEqual(report["counts"]["malformed"], 0)
        self.assertEqual(report["counts"]["unsupported"], 0)
        self.assertEqual(report["entries_accounted"], 2)

    def test_top_level_field_shapes_count_optional_fields_without_leaking_values_or_ids(self):
        private_values = (
            "private-title-a9f4",
            "private-title-b2d1",
            "private-unknown-c7e3",
            "private-nested-value-d5f8",
            "record-id-f6a0",
            "record-id-19bc",
            "record-id-48de",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = [
                {
                    "schema": "artifact",
                    "id": private_values[4],
                    "title": private_values[0],
                    "optional": None,
                    "nested": {"private": private_values[3]},
                    "sequence": 17,
                },
                {
                    "schema": "artifact",
                    "id": private_values[5],
                    "title": private_values[1],
                    "nested": ["private-nested-array-719b"],
                    "sequence": True,
                },
                {
                    "schema": "future-schema",
                    "id": private_values[6],
                    "optional": private_values[2],
                    "extra": None,
                },
                {"without": "schema-private-value-5f12"},
                29,
            ]
            (root / "records.json").write_text(json.dumps(rows), encoding="utf-8")

            report = census(root)
            repeated_report = census(root)

        shapes = report["top_level_fields_by_schema"]
        self.assertEqual(report["entries_accounted"], 5)
        self.assertEqual(report["counts"]["record"] + report["counts"]["unknown_schema"], 3)
        self.assertEqual({schema: item["row_count"] for schema, item in shapes.items()}, {
            "artifact": 2,
            "future-schema": 1,
        })
        self.assertEqual(shapes["artifact"]["fields"]["optional"], {
            "present_count": 1,
            "null_count": 1,
            "json_type_counts": {"null": 1},
        })
        self.assertEqual(shapes["artifact"]["fields"]["title"], {
            "present_count": 2,
            "null_count": 0,
            "json_type_counts": {"string": 2},
        })
        self.assertEqual(shapes["artifact"]["fields"]["sequence"]["json_type_counts"], {
            "boolean": 1,
            "number": 1,
        })
        self.assertEqual(shapes["artifact"]["fields"]["nested"]["json_type_counts"], {
            "array": 1,
            "object": 1,
        })
        self.assertEqual(shapes["future-schema"]["fields"]["optional"], {
            "present_count": 1,
            "null_count": 0,
            "json_type_counts": {"string": 1},
        })
        rendered = json.dumps(report, sort_keys=True)
        self.assertEqual(rendered, json.dumps(repeated_report, sort_keys=True))
        for value in private_values + ("private-nested-array-719b", "schema-private-value-5f12"):
            self.assertNotIn(value, rendered)

    def test_zero_entry_scans_are_not_success_shaped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            empty_directory_report = census(root)
            (root / "empty.json").write_text("[]", encoding="utf-8")
            empty_array_report = census(root)

        self.assertFalse(empty_directory_report["complete"])
        self.assertIn("empty_source", [item["kind"] for item in empty_directory_report["diagnostics"]])
        self.assertFalse(empty_array_report["complete"])
        self.assertEqual(empty_array_report["entries_accounted"], 0)
        self.assertIn("empty_source", [item["kind"] for item in empty_array_report["diagnostics"]])

    def test_valid_non_record_json_is_incomplete_without_source_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "value.json").write_text("17", encoding="utf-8")
            report = census(root)

        self.assertFalse(report["complete"])
        self.assertEqual(report["counts"]["non_record"], 1)
        self.assertEqual(report["entries_accounted"], 1)
        self.assertIn("no_source_records", [item["kind"] for item in report["diagnostics"]])

    def test_mixed_source_records_and_non_record_units_are_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "records.json").write_text('[{"schema":"artifact"},17]', encoding="utf-8")
            report = census(root)

        self.assertEqual(report["counts"]["record"], 1)
        self.assertEqual(report["counts"]["non_record"], 1)
        self.assertFalse(report["complete"])
        self.assertIn("non_record", [item["kind"] for item in report["diagnostics"]])
        self.assertFalse(report["source_contract_stamps"]["source_coverage_complete"])

    def test_source_contract_stamp_coverage_is_incomplete_when_a_file_cannot_be_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "record.json").write_text('{"schema":"artifact","contracts":[]}', encoding="utf-8")
            (root / "unreadable.json").write_text('{"schema":"claim","contracts":[]}', encoding="utf-8")
            original_read_bytes = Path.read_bytes

            def read_bytes(path):
                if path.name == "unreadable.json":
                    raise PermissionError("denied")
                return original_read_bytes(path)

            with patch.object(Path, "read_bytes", read_bytes):
                report = census(root)

        self.assertFalse(report["complete"])
        self.assertFalse(report["source_contract_stamps"]["complete"])
        self.assertFalse(report["source_contract_stamps"]["source_coverage_complete"])

    def test_duplicate_object_keys_are_malformed_at_top_level_and_nested_in_json_and_jsonl(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "duplicate.json").write_text('{"schema":"artifact","schema":"claim","nested":{"id":"a","id":"b"}}', encoding="utf-8")
            (root / "duplicate.jsonl").write_text('{"schema":"artifact","meta":{"key":1,"key":2}}\n', encoding="utf-8")
            report = census(root)

        self.assertEqual(report["counts"]["malformed"], 2)
        self.assertFalse(report["complete"])
        self.assertEqual([item["kind"] for item in report["diagnostics"]].count("duplicate_json_key"), 2)
        self.assertEqual(report["schemas_observed"], {})

    def test_non_finite_values_and_blank_jsonl_lines_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "record.json").write_text('{"schema":"artifact","value":NaN}', encoding="utf-8")
            (root / "records.jsonl").write_text('{"schema":"artifact"}\n\n{"schema":"claim","value":Infinity}\n{"schema":"claim","value":1e999}\n', encoding="utf-8")
            report = census(root)

        self.assertFalse(report["complete"])
        self.assertEqual(report["counts"]["malformed"], 3)
        self.assertEqual(report["blank_lines_observed"], 1)
        kinds = [item["kind"] for item in report["diagnostics"]]
        self.assertEqual(kinds.count("non_finite_json_number"), 3)
        self.assertEqual(kinds.count("blank_line"), 1)

    def test_unreadable_file_is_not_reported_as_an_empty_census(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "record.json"
            source.write_text('{"schema":"known"}', encoding="utf-8")
            with patch.object(Path, "read_bytes", side_effect=PermissionError("denied")):
                report = census(root)

        self.assertFalse(report["complete"])
        self.assertEqual(report["counts"]["unreadable"], 1)
        self.assertEqual(report["files_discovered"], report["files_accounted"])
        self.assertIn("unreadable", [item["kind"] for item in report["diagnostics"]])


if __name__ == "__main__":
    unittest.main()
