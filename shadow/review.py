#!/usr/bin/env python3
"""Validate human shadow checkpoint and refresh review records.

This tool reads local JSON reports and review records. It never opens an archive
root, runs a command recorded in a review, contacts a host, or transfers data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shadow.compare import Refusal, _validate_manifest
from migration.source_manifest import manifest_fingerprint


SHA256 = re.compile(r"^[0-9a-f]{64}$")
MAX_BYTES = 16 * 1024 * 1024
CHECKPOINT_KEYS = {
    "protocol", "phase", "reviewer", "reviewed_at", "decision",
    "checkpoint_results", "archive_inventory", "schema_less_files",
    "archive_identities_compared", "notes",
}
CHECKPOINT_RESULT_KEYS = {
    "comparison_sha256", "status", "source_snapshot", "copy_snapshot",
    "source_files", "copy_files", "source_bytes", "copy_bytes",
}
INVENTORY_KEYS = {
    "snapshot", "status", "archive_id", "spec", "context_present",
    "record_count", "shape_counts",
}
FILE_KEYS = {"path", "bytes", "sha256", "disposition"}
OPERATION_KEYS = {
    "protocol", "operation_id", "kind", "host", "source_roots",
    "destination", "snapshot_method", "copy", "writers", "rollback",
    "expected_effect", "decision", "approved_by", "approved_at",
    "approval_scope",
}


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _constant(_value: str) -> None:
    raise ValueError("non_finite_json_number")


def _read_json_snapshot(path: Path) -> tuple[Any, str]:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_NONBLOCK"):
        raise Refusal("safe_report_open_unavailable")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise Refusal("report_not_regular_file")
            with os.fdopen(fd, "rb", closefd=False) as handle:
                raw = handle.read(MAX_BYTES + 1)
        finally:
            os.close(fd)
    except Refusal:
        raise
    except OSError as exc:
        raise Refusal("report_unreadable") from exc
    if len(raw) > MAX_BYTES:
        raise Refusal("report_size_limit_exceeded")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise Refusal("report_json_invalid") from exc
    return value, hashlib.sha256(raw).hexdigest()


def _read_json(path: Path) -> Any:
    return _read_json_snapshot(path)[0]


def _closed(value: Any, keys: set[str], name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise Refusal(f"{name}_shape_invalid")
    return value


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Refusal(f"{name}_invalid")
    return value


def _timestamp(value: Any, name: str) -> datetime:
    text = _text(value, name)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise Refusal(f"{name}_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise Refusal(f"{name}_timezone_required")
    return parsed.astimezone(timezone.utc)


def _safe_relative_path(value: Any) -> str:
    path = _text(value, "schema_less_path")
    if path.startswith("/") or "\\" in path or "\x00" in path or any(
        part in {"", ".", ".."} for part in path.split("/")
    ):
        raise Refusal("schema_less_path_unsafe")
    return path


def _manifest_differences(source: dict[str, Any], copy: dict[str, Any]) -> tuple[int, int, int]:
    source_rows = {row["path"]: (row["bytes"], row["sha256"]) for row in source["files"]}
    copy_rows = {row["path"]: (row["bytes"], row["sha256"]) for row in copy["files"]}
    missing = len(set(source_rows) - set(copy_rows))
    extra = len(set(copy_rows) - set(source_rows))
    changed = sum(
        source_rows[path] != copy_rows[path]
        for path in set(source_rows) & set(copy_rows)
    )
    return missing, extra, changed


def validate_checkpoint(record: Any, records_comparison: Any,
                        records_source_manifest: Any, records_copy_manifest: Any,
                        content_comparison: Any, content_source_manifest: Any,
                        content_copy_manifest: Any) -> dict[str, Any]:
    value = _closed(record, CHECKPOINT_KEYS, "checkpoint_review")
    if value["protocol"] != "archive-shadow-review/1" or value["phase"] != "records":
        raise Refusal("checkpoint_protocol_or_phase_unsupported")
    _text(value["reviewer"], "reviewer")
    _timestamp(value["reviewed_at"], "reviewed_at")
    if value["decision"] not in {"accept", "hold"}:
        raise Refusal("checkpoint_decision_invalid")
    if value["archive_identities_compared"] is not False:
        raise Refusal("checkpoint_identity_comparison_not_explicitly_false")
    results = _closed(value["checkpoint_results"], {"records", "content"}, "checkpoint_results")

    def validate_result(name: str, comparison: Any, source_report: Any,
                        copy_report: Any, expected_phase: str) -> dict[str, Any]:
        result = _closed(results[name], CHECKPOINT_RESULT_KEYS, f"{name}_result")
        _text(result["source_snapshot"], f"{name}_source_snapshot")
        _text(result["copy_snapshot"], f"{name}_copy_snapshot")
        if result["source_snapshot"] == result["copy_snapshot"]:
            raise Refusal(f"{name}_snapshots_must_be_distinct")
        digest = result["comparison_sha256"]
        if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
            raise Refusal(f"{name}_comparison_sha256_invalid")
        if not isinstance(comparison, dict) or comparison.get("phase") != expected_phase:
            raise Refusal(f"{name}_comparison_scope_invalid")
        if comparison.get("status") != result["status"]:
            raise Refusal(f"{name}_comparison_status_mismatch")
        if result["status"] not in ({"incomplete", "matched"} if name == "records" else {"matched"}):
            raise Refusal(f"{name}_comparison_not_accepted_status")
        if (
            comparison.get("missing_paths") != 0
            or comparison.get("extra_paths") != 0
            or comparison.get("changed_path_digests_or_sizes") != 0
        ):
            raise Refusal(f"{name}_inventory_not_matched")
        if name == "records":
            if comparison.get("migration_status") != "unverified" or comparison.get("census_equal") is not True:
                raise Refusal("records_comparison_scope_invalid")
            if comparison.get("archive_identities_compared") is not False:
                raise Refusal("checkpoint_identity_comparison_not_explicitly_false")
        elif comparison.get("census_equal") is not None:
            raise Refusal("content_comparison_census_unsupported")
        source_value = _validate_manifest(source_report)
        copy_value = _validate_manifest(copy_report)
        if (
            comparison.get("source_manifest_sha256") != manifest_fingerprint(source_value)
            or comparison.get("copy_manifest_sha256") != manifest_fingerprint(copy_value)
        ):
            raise Refusal(f"{name}_comparison_manifest_binding_mismatch")
        for side, manifest in (("source", source_value), ("copy", copy_value)):
            for unit in ("files", "bytes"):
                recorded = result[f"{side}_{unit}"]
                expected = manifest["file_count"] if unit == "files" else manifest["total_bytes"]
                if type(recorded) is not int or recorded != expected:
                    raise Refusal(f"{name}_{side}_manifest_totals_mismatch")
        missing, extra, changed = _manifest_differences(source_value, copy_value)
        for field, count in {
            "missing_paths": missing,
            "extra_paths": extra,
            "changed_path_digests_or_sizes": changed,
        }.items():
            reported = comparison.get(field)
            if type(reported) is not int or reported != count:
                raise Refusal(f"{name}_comparison_manifest_diff_mismatch")
        if missing or extra or changed:
            raise Refusal(f"{name}_manifest_files_differ")
        return {"comparison": comparison, "source": source_value, "copy": copy_value}

    records = validate_result(
        "records", records_comparison, records_source_manifest, records_copy_manifest, "records"
    )
    content = validate_result(
        "content", content_comparison, content_source_manifest, content_copy_manifest, "content"
    )

    inventory = _closed(value["archive_inventory"], INVENTORY_KEYS, "archive_inventory")
    for key in ("snapshot", "archive_id", "spec"):
        _text(inventory[key], f"archive_inventory_{key}")
    if inventory["status"] not in {"ok", "incomplete", "unsupported"}:
        raise Refusal("archive_inventory_status_invalid")
    if not isinstance(inventory["context_present"], bool):
        raise Refusal("archive_inventory_context_present_invalid")
    if not isinstance(inventory["record_count"], int) or isinstance(inventory["record_count"], bool) or inventory["record_count"] < 0:
        raise Refusal("archive_inventory_record_count_invalid")
    shape_counts = inventory["shape_counts"]
    required_shapes = {"artifact", "entity", "claim", "assessment"}
    if not isinstance(shape_counts, dict) or set(shape_counts) != required_shapes or any(
        not isinstance(count, int) or isinstance(count, bool) or count < 0
        for count in shape_counts.values()
    ):
        raise Refusal("archive_inventory_shape_counts_invalid")
    if sum(shape_counts.values()) != inventory["record_count"]:
        raise Refusal("archive_inventory_shape_counts_do_not_match_total")

    source = records["source"]
    copy = records["copy"]
    source_rows = {row["path"]: (row["bytes"], row["sha256"]) for row in source["files"]}
    copy_rows = {row["path"]: (row["bytes"], row["sha256"]) for row in copy["files"]}
    unresolved = value["schema_less_files"]
    if not isinstance(unresolved, list):
        raise Refusal("schema_less_files_invalid")
    comparison = records["comparison"]
    source_summary = comparison.get("source_census")
    copy_summary = comparison.get("copy_census")
    if not isinstance(source_summary, dict) or not isinstance(copy_summary, dict) or source_summary != copy_summary:
        raise Refusal("checkpoint_census_summaries_not_equal")
    expected_sources = source_summary.get("schema_less_sources")
    missing_schema = source_summary.get("missing_schema_entries")
    diagnostics = source_summary.get("incomplete_diagnostics")
    if not isinstance(expected_sources, list) or not isinstance(missing_schema, int) or isinstance(missing_schema, bool):
        raise Refusal("checkpoint_schema_less_summary_invalid")
    if comparison["status"] == "incomplete":
        if (
            not expected_sources
            or missing_schema < 1
            or diagnostics != {"missing_schema": missing_schema}
            or source_summary.get("census_complete") is not False
        ):
            raise Refusal("checkpoint_incompleteness_is_not_only_schema_less_file")
    elif (
        expected_sources
        or missing_schema != 0
        or diagnostics != {}
        or source_summary.get("census_complete") is not True
    ):
        raise Refusal("matched_records_census_has_unresolved_schema_less_rows")
    seen: set[str] = set()
    for item in unresolved:
        row = _closed(item, FILE_KEYS, "schema_less_file")
        path = _safe_relative_path(row["path"])
        size, digest = row["bytes"], row["sha256"]
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise Refusal("schema_less_file_bytes_invalid")
        if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
            raise Refusal("schema_less_file_sha256_invalid")
        if row["disposition"] != "unresolved_schema-less_source_file":
            raise Refusal("schema_less_file_disposition_invalid")
        if path in seen:
            raise Refusal("schema_less_file_duplicate")
        seen.add(path)
        if source_rows.get(path) != (size, digest) or copy_rows.get(path) != (size, digest):
            raise Refusal("schema_less_file_not_identical_in_both_manifests")
    if seen != set(expected_sources):
        raise Refusal("schema_less_file_list_does_not_match_census")
    if value["decision"] == "accept" and (
        inventory["status"] != "ok" or inventory["context_present"] is not True
    ):
        raise Refusal("incomplete_archive_inventory_cannot_be_accepted")
    if not isinstance(value["notes"], str):
        raise Refusal("checkpoint_notes_invalid")
    return {
        "status": "reviewed" if value["decision"] == "accept" else "held",
        "phase": "records",
        "records_comparison_status": records["comparison"]["status"],
        "content_comparison_status": content["comparison"]["status"],
        "inventory_matched": True,
        "migration_status": "unverified",
        "archive_inventory_snapshot": inventory["snapshot"],
        "archive_inventory_counts_kept_separate": True,
        "schema_less_files_recorded": len(unresolved),
        "archive_identities_compared": False,
    }


def _absolute_path(value: Any, name: str) -> str:
    path = _text(value, name)
    if not os.path.isabs(path) or os.path.normpath(path) != path:
        raise Refusal(f"{name}_must_be_canonical_absolute_path")
    return path


def _paths_overlap(left: str, right: str) -> bool:
    common = os.path.commonpath((left, right))
    return common == left or common == right


def validate_operation(record: Any) -> dict[str, Any]:
    value = _closed(record, OPERATION_KEYS, "operation_review")
    if value["protocol"] != "archive-shadow-operation-review/1":
        raise Refusal("operation_protocol_unsupported")
    for key in ("operation_id", "host", "expected_effect", "approved_by", "approval_scope"):
        _text(value[key], key)
    if value["kind"] not in {"records_refresh", "content_refresh", "full_refresh"}:
        raise Refusal("operation_kind_unsupported")
    if value["decision"] not in {"approve", "hold"}:
        raise Refusal("operation_decision_invalid")
    _timestamp(value["approved_at"], "approved_at")
    roots = value["source_roots"]
    if not isinstance(roots, list) or not roots:
        raise Refusal("operation_source_roots_required")
    source_paths: list[str] = []
    for root in roots:
        row = _closed(root, {"path", "filesystem", "snapshot_id"}, "operation_source_root")
        source_paths.append(_absolute_path(row["path"], "source_root"))
        _text(row["filesystem"], "source_filesystem")
        _text(row["snapshot_id"], "source_snapshot_id")
    expected_roots = {"records_refresh": 1, "content_refresh": 1, "full_refresh": 2}[value["kind"]]
    if len(roots) != expected_roots:
        raise Refusal("operation_source_root_count_invalid")
    if len(set(source_paths)) != len(source_paths):
        raise Refusal("operation_source_roots_duplicate")
    for index, left in enumerate(source_paths):
        for right in source_paths[index + 1:]:
            if _paths_overlap(left, right):
                raise Refusal("operation_source_roots_overlap")
    _absolute_path(value["destination"], "destination")
    if any(_paths_overlap(value["destination"], source) for source in source_paths):
        raise Refusal("operation_destination_overlaps_source")
    snapshot = _closed(value["snapshot_method"], {"program", "exact_command", "consistency_basis", "identifier_method"}, "snapshot_method")
    copy = _closed(value["copy"], {"program", "exact_command"}, "copy_method")
    for obj, prefix in ((snapshot, "snapshot"), (copy, "copy")):
        for field in obj:
            _text(obj[field], f"{prefix}_{field}")
    if not isinstance(value["writers"], list) or not value["writers"]:
        raise Refusal("operation_writer_inventory_required")
    unresolved_writers = 0
    for writer in value["writers"]:
        row = _closed(writer, {"unit", "evidence", "class", "prior_state", "pause_action", "restore_action", "active_run_check"}, "operation_writer")
        for key in row:
            _text(row[key], f"writer_{key}")
        if row["class"] not in {"direct", "indirect", "read_only", "unresolved"}:
            raise Refusal("operation_writer_class_invalid")
        unresolved_writers += row["class"] == "unresolved"
    rollback = _closed(value["rollback"], {"path", "exact_action", "incomplete_copy_disposition"}, "operation_rollback")
    rollback_path = _absolute_path(rollback["path"], "rollback_path")
    if any(_paths_overlap(rollback_path, source) for source in source_paths):
        raise Refusal("operation_rollback_overlaps_source")
    for key in ("exact_action", "incomplete_copy_disposition"):
        _text(rollback[key], f"rollback_{key}")
    if value["decision"] == "approve" and unresolved_writers:
        raise Refusal("unresolved_writer_blocks_approval")
    return {
        "status": "reviewed" if value["decision"] == "approve" else "held",
        "operation_id": value["operation_id"],
        "kind": value["kind"],
        "source_roots": len(roots),
        "writers_reviewed": len(value["writers"]),
        "unresolved_writers": unresolved_writers,
        "command_executed": False,
        "transfer_performed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    checkpoint = subparsers.add_parser("checkpoint", help="validate a human review of the records checkpoint")
    checkpoint.add_argument("--record", required=True, type=Path)
    checkpoint.add_argument("--records-comparison", required=True, type=Path)
    checkpoint.add_argument("--records-source-manifest", required=True, type=Path)
    checkpoint.add_argument("--records-copy-manifest", required=True, type=Path)
    checkpoint.add_argument("--content-comparison", required=True, type=Path)
    checkpoint.add_argument("--content-source-manifest", required=True, type=Path)
    checkpoint.add_argument("--content-copy-manifest", required=True, type=Path)
    operation = subparsers.add_parser("operation", help="validate one exact refresh operation review")
    operation.add_argument("--record", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "checkpoint":
            record = _read_json(args.record)
            records_comparison, records_comparison_digest = _read_json_snapshot(args.records_comparison)
            content_comparison, content_comparison_digest = _read_json_snapshot(args.content_comparison)
            checkpoint_results = record.get("checkpoint_results", {})
            for name, digest in (
                ("records", records_comparison_digest),
                ("content", content_comparison_digest),
            ):
                result = checkpoint_results.get(name, {}) if isinstance(checkpoint_results, dict) else {}
                if result.get("comparison_sha256") != digest:
                    raise Refusal(f"{name}_comparison_report_digest_mismatch")
            result = validate_checkpoint(
                record,
                records_comparison,
                _read_json(args.records_source_manifest),
                _read_json(args.records_copy_manifest),
                content_comparison,
                _read_json(args.content_source_manifest),
                _read_json(args.content_copy_manifest),
            )
        else:
            result = validate_operation(_read_json(args.record))
    except (Refusal, OSError, AttributeError) as exc:
        reason = str(exc) or "review_record_invalid"
        print(json.dumps({"status": "refused", "reason": reason}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "reviewed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
