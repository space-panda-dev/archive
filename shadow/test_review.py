import hashlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from migration.source_manifest import manifest_fingerprint, source_manifest
from shadow.review import Refusal, _read_json_snapshot, validate_checkpoint, validate_operation


class ReviewTest(unittest.TestCase):
    def _checkpoint_fixture(self, base: Path):
        source = base / "source"
        copy = base / "copy"
        content_source = base / "content-source"
        content_copy = base / "content-copy"
        source.mkdir()
        copy.mkdir()
        content_source.mkdir()
        content_copy.mkdir()
        payload = b'{"legacy_id":"old-7"}\n'
        (source / "records.json").write_bytes(payload)
        (copy / "records.json").write_bytes(payload)
        source_manifest_report = source_manifest(source.resolve())
        copy_manifest_report = source_manifest(copy.resolve())
        content_payload = b"original bytes"
        (content_source / "audio.bin").write_bytes(content_payload)
        (content_copy / "audio.bin").write_bytes(content_payload)
        content_source_manifest = source_manifest(content_source.resolve())
        content_copy_manifest = source_manifest(content_copy.resolve())
        file_digest = hashlib.sha256(payload).hexdigest()
        summary = {
            "census_complete": False,
            "missing_schema_entries": 1,
            "schema_less_sources": ["records.json"],
            "incomplete_diagnostics": {"missing_schema": 1},
        }
        comparison = {
            "status": "incomplete",
            "phase": "records",
            "source_manifest_sha256": manifest_fingerprint(source_manifest_report),
            "copy_manifest_sha256": manifest_fingerprint(copy_manifest_report),
            "migration_status": "unverified",
            "missing_paths": 0,
            "extra_paths": 0,
            "changed_path_digests_or_sizes": 0,
            "census_equal": True,
            "archive_identities_compared": False,
            "source_census": summary,
            "copy_census": summary,
        }
        content_comparison = {
            "status": "matched",
            "phase": "content",
            "source_manifest_sha256": manifest_fingerprint(content_source_manifest),
            "copy_manifest_sha256": manifest_fingerprint(content_copy_manifest),
            "missing_paths": 0,
            "extra_paths": 0,
            "changed_path_digests_or_sizes": 0,
            "census_equal": None,
        }
        record = {
            "protocol": "archive-shadow-review/1",
            "phase": "records",
            "reviewer": "operator",
            "reviewed_at": "2026-10-08T12:00:00Z",
            "decision": "accept",
            "checkpoint_results": {
                "records": {
                    "comparison_sha256": "a" * 64,
                    "status": "incomplete",
                    "source_snapshot": "records-snapshot-1",
                    "copy_snapshot": "records-copy-snapshot-1",
                    "source_files": 1,
                    "copy_files": 1,
                    "source_bytes": len(payload),
                    "copy_bytes": len(payload),
                },
                "content": {
                    "comparison_sha256": "c" * 64,
                    "status": "matched",
                    "source_snapshot": "content-snapshot-1",
                    "copy_snapshot": "content-copy-snapshot-1",
                    "source_files": 1,
                    "copy_files": 1,
                    "source_bytes": len(content_payload),
                    "copy_bytes": len(content_payload),
                },
            },
            "archive_inventory": {
                "snapshot": "archive-git-head-abc123",
                "status": "ok",
                "archive_id": "urn:uuid:00000000-0000-4000-8000-000000000001",
                "spec": "archive/0.0.2",
                "context_present": True,
                "record_count": 12,
                "shape_counts": {"artifact": 8, "entity": 4, "claim": 0, "assessment": 0},
            },
            "schema_less_files": [{
                "path": "records.json",
                "bytes": len(payload),
                "sha256": file_digest,
                "disposition": "unresolved_schema-less_source_file",
            }],
            "archive_identities_compared": False,
            "notes": "Counted separately; source route remains unresolved.",
        }
        return (
            record, comparison, source_manifest_report, copy_manifest_report,
            content_comparison, content_source_manifest, content_copy_manifest,
        )

    def test_review_records_schema_less_file_and_separate_archive_counts(self):
        with tempfile.TemporaryDirectory() as temporary:
            args = self._checkpoint_fixture(Path(temporary))

            result = validate_checkpoint(*args)

        self.assertEqual(result["status"], "reviewed")
        self.assertTrue(result["inventory_matched"])
        self.assertTrue(result["archive_inventory_counts_kept_separate"])
        self.assertEqual(result["schema_less_files_recorded"], 1)
        self.assertFalse(result["archive_identities_compared"])

    def test_review_refuses_schema_less_file_not_bound_to_both_manifests(self):
        with tempfile.TemporaryDirectory() as temporary:
            args = list(self._checkpoint_fixture(Path(temporary)))
            args[0]["schema_less_files"][0]["sha256"] = "b" * 64

            with self.assertRaisesRegex(Refusal, "schema_less_file_not_identical_in_both_manifests"):
                validate_checkpoint(*args)

    def test_forged_comparison_cannot_hide_equal_size_digest_mismatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            args = list(self._checkpoint_fixture(Path(temporary)))
            args[3]["files"][0]["sha256"] = "b" * 64
            args[1]["copy_manifest_sha256"] = manifest_fingerprint(args[3])

            with self.assertRaisesRegex(Refusal, "records_comparison_manifest_diff_mismatch"):
                validate_checkpoint(*args)

    def test_archive_inventory_requires_all_shapes_and_context_for_acceptance(self):
        with tempfile.TemporaryDirectory() as temporary:
            args = list(self._checkpoint_fixture(Path(temporary)))
            del args[0]["archive_inventory"]["shape_counts"]["assessment"]

            with self.assertRaisesRegex(Refusal, "archive_inventory_shape_counts_invalid"):
                validate_checkpoint(*args)

        with tempfile.TemporaryDirectory() as temporary:
            args = list(self._checkpoint_fixture(Path(temporary)))
            args[0]["archive_inventory"]["context_present"] = False

            with self.assertRaisesRegex(Refusal, "incomplete_archive_inventory_cannot_be_accepted"):
                validate_checkpoint(*args)

    def _operation(self):
        return {
            "protocol": "archive-shadow-operation-review/1",
            "operation_id": "refresh-2026-10-08-01",
            "kind": "records_refresh",
            "host": "archive-host.example",
            "source_roots": [{
                "path": "/srv/archive/records",
                "filesystem": "ext4",
                "snapshot_id": "source-snapshot-42",
            }],
            "destination": "/srv/archive-shadow/records",
            "snapshot_method": {
                "program": "snapshot-tool 1.2",
                "exact_command": "snapshot-tool create --root /srv/archive/records",
                "consistency_basis": "all-writers-paused",
                "identifier_method": "snapshot-tool show-id <snapshot>",
            },
            "copy": {
                "program": "rsync 3.4",
                "exact_command": "rsync -a --delete <frozen-source>/ <destination>/",
            },
            "writers": [{
                "unit": "archive-writer.timer",
                "evidence": "systemctl cat archive-writer.timer",
                "class": "direct",
                "prior_state": "active; no invocation running",
                "pause_action": "systemctl stop archive-writer.timer",
                "restore_action": "restart only if prior state was active",
                "active_run_check": "systemctl show archive-writer.service",
            }],
            "rollback": {
                "path": "/srv/archive-shadow/records.incomplete",
                "exact_action": "remove incomplete destination after capture review",
                "incomplete_copy_disposition": "quarantine; never use for reads",
            },
            "expected_effect": "one records-root snapshot copied to the named disposable destination",
            "decision": "approve",
            "approved_by": "operator",
            "approved_at": datetime.now(timezone.utc).isoformat(),
            "approval_scope": "one execution of this operation only",
        }

    def test_operation_review_is_concrete_and_never_executes_command(self):
        result = validate_operation(self._operation())

        self.assertEqual(result["status"], "reviewed")
        self.assertFalse(result["command_executed"])
        self.assertFalse(result["transfer_performed"])

    def test_operation_with_unresolved_writer_cannot_be_approved(self):
        record = self._operation()
        record["writers"][0]["class"] = "unresolved"

        with self.assertRaisesRegex(Refusal, "unresolved_writer_blocks_approval"):
            validate_operation(record)

    def test_operation_destination_cannot_overlap_source(self):
        record = self._operation()
        record["destination"] = "/srv/archive/records/copy"

        with self.assertRaisesRegex(Refusal, "operation_destination_overlaps_source"):
            validate_operation(record)

    def test_operation_rollback_cannot_overlap_source(self):
        record = self._operation()
        record["rollback"]["path"] = "/srv/archive/records/incomplete"

        with self.assertRaisesRegex(Refusal, "operation_rollback_overlaps_source"):
            validate_operation(record)

    def test_full_refresh_rejects_duplicate_source_roots(self):
        record = self._operation()
        record["kind"] = "full_refresh"
        record["source_roots"].append(dict(record["source_roots"][0]))

        with self.assertRaisesRegex(Refusal, "operation_source_roots_duplicate"):
            validate_operation(record)

    def test_full_refresh_rejects_nested_source_roots(self):
        record = self._operation()
        record["kind"] = "full_refresh"
        record["source_roots"].append({
            "path": "/srv/archive/records/originals",
            "filesystem": "btrfs",
            "snapshot_id": "source-snapshot-43",
        })

        with self.assertRaisesRegex(Refusal, "operation_source_roots_overlap"):
            validate_operation(record)

    def test_json_report_digest_comes_from_the_same_read_as_parsed_value(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "report.json"
            raw = b'{"status":"ok"}'
            path.write_bytes(raw)

            value, digest = _read_json_snapshot(path)

        self.assertEqual(value, {"status": "ok"})
        self.assertEqual(digest, hashlib.sha256(raw).hexdigest())

if __name__ == "__main__":
    unittest.main()
