#!/usr/bin/env python3
"""Compare source-tree manifests and read-only census reports."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from migration.catalog import SOURCE_SCHEMAS, UNRESOLVED_REASON
from migration.census import BUCKETS, census
from migration.source_manifest import (
    FINGERPRINT_FORMAT as MANIFEST_FINGERPRINT_FORMAT,
    FORMAT as MANIFEST_FORMAT,
    manifest_fingerprint,
    source_manifest,
)


MAX_REPORT_BYTES = 64 * 1024 * 1024
HARD_MAX_REPORT_BYTES = 256 * 1024 * 1024
SHA256 = re.compile(r"^[0-9a-f]{64}$")
MANIFEST_KEYS = {
    "format", "status", "atomic_snapshot", "requires_frozen_source",
    "file_count", "total_bytes", "files",
}
CENSUS_KEYS = {
    "source_root", "source_manifest_binding", "source_schema_catalog", "complete",
    "files_discovered", "files_accounted", "files_by_suffix", "blank_lines_observed",
    "counts", "entries_accounted", "schemas_observed", "top_level_fields_by_schema",
    "schema_routes", "unknown_schemas", "count_conservation", "source_contract_stamps",
    "timestamp_fields_observed", "diagnostics",
}
CONTENT_PHASE = "content"
RECORDS_PHASE = "records"


class Refusal(Exception):
    """A malformed, incomplete, or unsupported report that cannot be compared."""


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("non_finite_json_number")


def _read_report(path: Path, max_bytes: int = MAX_REPORT_BYTES) -> Any:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_NONBLOCK"):
        raise Refusal("safe_report_open_unavailable")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise Refusal("report_not_regular_file")
            with os.fdopen(fd, "rb", closefd=False) as handle:
                raw = handle.read(max_bytes + 1)
        finally:
            os.close(fd)
    except Refusal:
        raise
    except OSError as exc:
        raise Refusal("report_unreadable") from exc
    if len(raw) > max_bytes:
        raise Refusal("report_size_limit_exceeded")
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_json_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise Refusal("report_json_invalid") from exc


def _closed(value: Any, keys: set[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise Refusal(f"{where}_shape_invalid")
    return value


def _natural(value: Any, where: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise Refusal(f"{where}_invalid")
    return value


def _string(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise Refusal(f"{where}_invalid")
    return value


def _validate_manifest(value: Any) -> dict[str, Any]:
    manifest = _closed(value, MANIFEST_KEYS, "manifest")
    if (
        manifest["format"] != MANIFEST_FORMAT
        or manifest["status"] != "complete"
        or manifest["atomic_snapshot"] is not False
        or manifest["requires_frozen_source"] is not True
    ):
        raise Refusal("manifest_incomplete_or_unsupported")
    files = manifest["files"]
    if not isinstance(files, list) or not files:
        raise Refusal("manifest_files_invalid")
    paths: set[str] = set()
    folded: set[str] = set()
    ordered: list[str] = []
    total_bytes = 0
    for item in files:
        entry = _closed(item, {"path", "bytes", "sha256"}, "manifest_entry")
        path = _string(entry["path"], "manifest_path")
        if (
            path == "." or path.startswith("/") or "\\" in path or "\x00" in path
            or any(part in {"", ".", ".."} for part in path.split("/"))
        ):
            raise Refusal("manifest_path_unsafe")
        size = _natural(entry["bytes"], "manifest_bytes")
        digest = _string(entry["sha256"], "manifest_sha256")
        if SHA256.fullmatch(digest) is None:
            raise Refusal("manifest_sha256_invalid")
        path_key = unicodedata.normalize("NFC", path.casefold())
        if path in paths or path_key in folded:
            raise Refusal("manifest_path_duplicate_or_colliding")
        paths.add(path)
        folded.add(path_key)
        ordered.append(path)
        total_bytes += size
    if ordered != sorted(ordered, key=os.fsencode):
        raise Refusal("manifest_paths_not_canonical")
    if _natural(manifest["file_count"], "manifest_file_count") != len(files):
        raise Refusal("manifest_file_count_mismatch")
    if _natural(manifest["total_bytes"], "manifest_total_bytes") != total_bytes:
        raise Refusal("manifest_total_bytes_mismatch")
    return manifest


def _validate_census(value: Any, expected_manifest_sha256: str) -> dict[str, Any]:
    report = _closed(value, CENSUS_KEYS, "census")
    _string(report["source_root"], "census_source_root")
    binding = _closed(
        report["source_manifest_binding"],
        {"status", "format", "atomic_snapshot", "requires_frozen_source", "manifest_fingerprint_format", "manifest_sha256"},
        "census_manifest_binding",
    )
    if binding != {
        "status": "matched",
        "format": MANIFEST_FORMAT,
        "atomic_snapshot": False,
        "requires_frozen_source": True,
        "manifest_fingerprint_format": MANIFEST_FINGERPRINT_FORMAT,
        "manifest_sha256": expected_manifest_sha256,
    }:
        raise Refusal("census_manifest_binding_not_matched")
    if report["source_schema_catalog"] != list(SOURCE_SCHEMAS):
        raise Refusal("census_schema_catalog_unsupported")
    if not isinstance(report["complete"], bool):
        raise Refusal("census_completeness_flag_invalid")
    for field in ("files_discovered", "files_accounted", "blank_lines_observed", "entries_accounted", "timestamp_fields_observed"):
        _natural(report[field], f"census_{field}")
    if report["files_discovered"] < 1 or report["files_discovered"] != report["files_accounted"]:
        raise Refusal("census_file_accounting_mismatch")
    suffixes = report["files_by_suffix"]
    if not isinstance(suffixes, dict) or any(k not in {".json", ".jsonl"} for k in suffixes):
        raise Refusal("census_suffixes_unsupported")
    if sum(_natural(v, "census_suffix_count") for v in suffixes.values()) != report["files_accounted"]:
        raise Refusal("census_suffix_count_mismatch")
    counts = _closed(report["counts"], set(BUCKETS), "census_counts")
    for key in BUCKETS:
        _natural(counts[key], f"census_count_{key}")
    if sum(counts.values()) != report["entries_accounted"]:
        raise Refusal("census_entry_count_mismatch")
    schemas = report["schemas_observed"]
    if not isinstance(schemas, dict) or any(not isinstance(name, str) or not name for name in schemas):
        raise Refusal("census_schema_rows_invalid")
    if any(_natural(count, "census_schema_count") < 1 for count in schemas.values()):
        raise Refusal("census_schema_count_invalid")
    if sum(count for name, count in schemas.items() if name in SOURCE_SCHEMAS) != counts["record"]:
        raise Refusal("census_schema_count_mismatch")
    unknown = {name: count for name, count in sorted(schemas.items()) if name not in SOURCE_SCHEMAS}
    if report["unknown_schemas"] != unknown or sum(unknown.values()) != counts["unknown_schema"]:
        raise Refusal("census_unknown_schemas_mismatch")
    diagnostics = report["diagnostics"]
    if not isinstance(diagnostics, list):
        raise Refusal("census_diagnostics_invalid")
    for diagnostic in diagnostics:
        if not isinstance(diagnostic, dict) or not isinstance(diagnostic.get("kind"), str):
            raise Refusal("census_diagnostic_shape_invalid")
        if "source" not in diagnostic or not isinstance(diagnostic["source"], str):
            raise Refusal("census_diagnostic_source_invalid")

    conservation = _closed(
        report["count_conservation"],
        {"scope", "catalog_record_count", "unknown_schema_record_count", "schema_tagged_record_count", "all_schema_tagged_records_accounted_for", "migration_supported"},
        "census_count_conservation",
    )
    if (
        conservation["scope"] != "internal_partition_consistency"
        or conservation["catalog_record_count"] != counts["record"]
        or conservation["unknown_schema_record_count"] != counts["unknown_schema"]
        or conservation["schema_tagged_record_count"] != counts["record"] + counts["unknown_schema"]
        or not isinstance(conservation["all_schema_tagged_records_accounted_for"], bool)
        or conservation["migration_supported"] is not False
    ):
        raise Refusal("census_count_conservation_invalid")
    if conservation["all_schema_tagged_records_accounted_for"] is not True:
        raise Refusal("census_count_conservation_flag_invalid")

    routes = report["schema_routes"]
    if not isinstance(routes, dict) or set(routes) != set(schemas):
        raise Refusal("census_schema_routes_mismatch")
    for schema, count in schemas.items():
        route = _closed(routes[schema], {"live_record_count", "status", "reason"}, "census_schema_route")
        expected = (
            {"live_record_count": count, "status": "unresolved", "reason": UNRESOLVED_REASON}
            if schema in SOURCE_SCHEMAS
            else {"live_record_count": count, "status": "unsupported", "reason": "schema is outside the fixed source catalog"}
        )
        if route != expected:
            raise Refusal("census_route_shape_or_count_invalid")
    fields = report["top_level_fields_by_schema"]
    if not isinstance(fields, dict) or set(fields) != set(schemas):
        raise Refusal("census_field_observation_schemas_mismatch")
    allowed_types = {"null", "boolean", "number", "string", "array", "object"}
    for schema, observation in fields.items():
        observed = _closed(observation, {"row_count", "fields"}, "census_field_observation")
        if observed["row_count"] != schemas[schema] or not isinstance(observed["fields"], dict):
            raise Refusal("census_field_observation_invalid")
        for field, field_observation in observed["fields"].items():
            _string(field, "census_field_name")
            detail = _closed(field_observation, {"present_count", "null_count", "json_type_counts"}, "census_field_detail")
            present = _natural(detail["present_count"], "census_field_present_count")
            nulls = _natural(detail["null_count"], "census_field_null_count")
            types = detail["json_type_counts"]
            if not isinstance(types, dict) or set(types) - allowed_types:
                raise Refusal("census_field_types_invalid")
            if sum(_natural(v, "census_field_type_count") for v in types.values()) != present:
                raise Refusal("census_field_type_count_mismatch")
            if types.get("null", 0) != nulls or present > schemas[schema]:
                raise Refusal("census_field_counts_invalid")

    contract = _closed(
        report["source_contract_stamps"],
        {"records_with_plan_contracts", "citations_by_contract", "records_by_schema", "diagnostics", "source_coverage_complete", "complete"},
        "census_contract_stamps",
    )
    _natural(contract["records_with_plan_contracts"], "census_contract_record_count")
    if contract["records_with_plan_contracts"] > counts["record"]:
        raise Refusal("census_contract_record_count_invalid")
    for key in ("citations_by_contract", "records_by_schema"):
        mapping = contract[key]
        if not isinstance(mapping, dict):
            raise Refusal(f"census_{key}_invalid")
        for name, count in mapping.items():
            _string(name, f"census_{key}_name")
            _natural(count, f"census_{key}_count")
    if not isinstance(contract["diagnostics"], list):
        raise Refusal("census_contract_diagnostics_invalid")
    for diagnostic in contract["diagnostics"]:
        if not isinstance(diagnostic, dict) or not isinstance(diagnostic.get("kind"), str):
            raise Refusal("census_contract_diagnostic_shape_invalid")
    if not isinstance(contract["source_coverage_complete"], bool) or not isinstance(contract["complete"], bool):
        raise Refusal("census_contract_completeness_flag_invalid")
    if report["complete"] and (diagnostics or not contract["complete"]):
        raise Refusal("census_completeness_inconsistent")
    if report["complete"] and (
        report["blank_lines_observed"] != 0
        or any(counts[key] for key in ("unknown_schema", "missing_schema", "non_record", "malformed", "unreadable", "unsupported"))
        or not contract["source_coverage_complete"]
    ):
        raise Refusal("census_completeness_flag_inconsistent")
    return report


def _manifest_index(manifest: dict[str, Any]) -> dict[str, tuple[int, str]]:
    return {item["path"]: (item["bytes"], item["sha256"]) for item in manifest["files"]}


def _census_projection(report: dict[str, Any]) -> dict[str, Any]:
    projection = dict(report)
    # Absolute root names are local operation details, not comparison keys or output.
    projection.pop("source_root")
    projection["diagnostics"] = [{k: v for k, v in item.items() if k != "source"} for item in report["diagnostics"]]
    contract = dict(projection["source_contract_stamps"])
    contract["diagnostics"] = [{k: v for k, v in item.items() if k != "source"} for item in contract["diagnostics"]]
    projection["source_contract_stamps"] = contract
    return projection


def _census_summary(report: dict[str, Any]) -> dict[str, Any]:
    counts = report["counts"]
    routes = report["schema_routes"]
    incomplete_diagnostics: dict[str, int] = {}
    schema_less_sources: set[str] = set()
    for item in report["diagnostics"]:
        kind = item.get("kind")
        if isinstance(kind, str):
            incomplete_diagnostics[kind] = incomplete_diagnostics.get(kind, 0) + 1
            if kind == "missing_schema" and isinstance(item.get("source"), str):
                schema_less_sources.add(item["source"])
    return {
        "census_complete": report["complete"],
        "files_discovered": report["files_discovered"],
        "records_by_schema": report["schemas_observed"],
        "unresolved_route_records": sum(route["live_record_count"] for route in routes.values() if route["status"] == "unresolved"),
        "unsupported_schema_records": sum(route["live_record_count"] for route in routes.values() if route["status"] == "unsupported"),
        "missing_schema_entries": counts["missing_schema"],
        "schema_less_sources": sorted(schema_less_sources),
        "incomplete_diagnostics": incomplete_diagnostics,
        "malformed_or_unreadable_entries": counts["malformed"] + counts["unreadable"],
        "unknown_schema_entries": counts["unknown_schema"],
        "unsupported_entries": counts["unsupported"],
        "diagnostic_count": len(report["diagnostics"]),
    }


def compare(phase: str, source_manifest_report: Any, copy_manifest_report: Any,
            source_census_report: Any | None = None, copy_census_report: Any | None = None) -> dict[str, Any]:
    if phase not in {RECORDS_PHASE, CONTENT_PHASE}:
        raise Refusal("phase_unsupported")
    source_manifest_value = _validate_manifest(source_manifest_report)
    copy_manifest_value = _validate_manifest(copy_manifest_report)
    source_files = _manifest_index(source_manifest_value)
    copy_files = _manifest_index(copy_manifest_value)
    missing = len(set(source_files) - set(copy_files))
    extra = len(set(copy_files) - set(source_files))
    changed = sum(1 for path in set(source_files) & set(copy_files) if source_files[path] != copy_files[path])
    if phase == RECORDS_PHASE:
        if source_census_report is None or copy_census_report is None:
            raise Refusal("records_phase_requires_two_census_reports")
        source_census_value = _validate_census(
            source_census_report,
            manifest_fingerprint(source_manifest_value),
        )
        copy_census_value = _validate_census(
            copy_census_report,
            manifest_fingerprint(copy_manifest_value),
        )
        census_equal = _census_projection(source_census_value) == _census_projection(copy_census_value)
    else:
        if source_census_report is not None or copy_census_report is not None:
            raise Refusal("content_phase_does_not_accept_census_reports")
        census_equal = None
    inventory_matched = missing == 0 and extra == 0 and changed == 0 and (census_equal is not False)
    source_summary = _census_summary(source_census_value) if phase == RECORDS_PHASE else None
    copy_summary = _census_summary(copy_census_value) if phase == RECORDS_PHASE else None
    # These reports are caller-supplied. Their hashes bind each census to its
    # supplied manifest, but do not attest that either report came from a tree.
    # In particular, self-consistent edited reports cannot establish that a
    # semantic route is complete.
    semantic_status = "unverified"
    if not inventory_matched:
        status = "mismatch"
    elif phase == RECORDS_PHASE and (not source_summary["census_complete"] or not copy_summary["census_complete"]):
        status = "incomplete"
    else:
        status = "matched"
    return {
        "status": status,
        "scope": "reported_source_copy_inventory",
        "phase": phase,
        "source_manifest_sha256": manifest_fingerprint(source_manifest_value),
        "copy_manifest_sha256": manifest_fingerprint(copy_manifest_value),
        "source_files": source_manifest_value["file_count"],
        "copy_files": copy_manifest_value["file_count"],
        "source_bytes": source_manifest_value["total_bytes"],
        "copy_bytes": copy_manifest_value["total_bytes"],
        "missing_paths": missing,
        "extra_paths": extra,
        "changed_path_digests_or_sizes": changed,
        "census_equal": census_equal,
        "migration_status": semantic_status,
        "reports_authenticated": False,
        "source_bytes_verified_by_comparator": False,
        "source_census": source_summary,
        "copy_census": copy_summary,
        "archive_identities_compared": False,
        "claims_translated": False,
        "durability_proven": False,
        "inv_5_proven": False,
    }


def _synthetic_reports(phase: str, mismatch: bool = False) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    """Create temp source trees and use the repository's existing read-only tools."""
    from migration.catalog import SOURCE_SCHEMAS

    with tempfile.TemporaryDirectory(
        prefix="archive-shadow-fixture-", dir=os.path.realpath(tempfile.gettempdir())
    ) as temporary:
        base = Path(temporary)
        source_root, copy_root = base / "source", base / "copy"
        source_root.mkdir()
        copy_root.mkdir()
        if phase == RECORDS_PHASE:
            rows = b'{"schema":"artifact"}\n{"schema":"session"}\n'
            (source_root / "records.jsonl").write_bytes(rows)
            (copy_root / "records.jsonl").write_bytes(rows if not mismatch else rows + b'{"schema":"job"}\n')
        else:
            (source_root / "a.bin").write_bytes(b"synthetic source content\x00\x01")
            (copy_root / "a.bin").write_bytes(b"different synthetic content" if mismatch else b"synthetic source content\x00\x01")
        manifest_paths = []
        census_paths = []
        for root, label in ((source_root, "source"), (copy_root, "copy")):
            manifest = source_manifest(root)
            manifest_path = base / f"{label}-manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            manifest_paths.append(manifest_path)
            if phase == RECORDS_PHASE:
                census_report = census(root, manifest_path)
                # The fixed catalog keeps every known route unresolved by design.
                if census_report["source_schema_catalog"] != list(SOURCE_SCHEMAS):
                    raise Refusal("synthetic_census_catalog_unexpected")
                census_path = base / f"{label}-census.json"
                census_path.write_text(json.dumps(census_report), encoding="utf-8")
                census_paths.append(census_path)
        if phase == RECORDS_PHASE:
            return (
                _read_report(manifest_paths[0]), _read_report(manifest_paths[1]),
                _read_report(census_paths[0]), _read_report(census_paths[1]),
            )
        return _read_report(manifest_paths[0]), _read_report(manifest_paths[1]), None, None


def _report_size_limit(value: str) -> int:
    try:
        size = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a whole number of bytes") from exc
    if size < 1 or size > HARD_MAX_REPORT_BYTES:
        raise argparse.ArgumentTypeError(
            f"must be between 1 and {HARD_MAX_REPORT_BYTES} bytes"
        )
    return size


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=(RECORDS_PHASE, CONTENT_PHASE))
    parser.add_argument("--source-manifest", type=Path)
    parser.add_argument("--source-census", type=Path)
    parser.add_argument("--copy-manifest", type=Path)
    parser.add_argument("--copy-census", type=Path)
    parser.add_argument(
        "--max-report-bytes",
        type=_report_size_limit,
        default=MAX_REPORT_BYTES,
        metavar="BYTES",
        help=(
            "maximum size of each input report (default: %(default)s; "
            f"hard maximum: {HARD_MAX_REPORT_BYTES})"
        ),
    )
    parser.add_argument("--fixture", choices=(RECORDS_PHASE, CONTENT_PHASE, "mismatch"))
    args = parser.parse_args(argv)
    try:
        if args.fixture is not None:
            if any((args.phase, args.source_manifest, args.source_census, args.copy_manifest, args.copy_census)):
                raise Refusal("fixture_and_report_arguments_cannot_be_combined")
            if args.max_report_bytes != MAX_REPORT_BYTES:
                raise Refusal("fixture_and_report_size_limit_cannot_be_combined")
            phase = CONTENT_PHASE if args.fixture == CONTENT_PHASE else RECORDS_PHASE
            reports = _synthetic_reports(phase, mismatch=args.fixture == "mismatch")
            result = compare(phase, *reports)
        else:
            if args.phase is None or args.source_manifest is None or args.copy_manifest is None:
                raise Refusal("phase_and_source_and_copy_manifests_required")
            if args.phase == RECORDS_PHASE and (args.source_census is None or args.copy_census is None):
                raise Refusal("records_phase_requires_source_and_copy_census")
            if args.phase == CONTENT_PHASE and (args.source_census is not None or args.copy_census is not None):
                raise Refusal("content_phase_does_not_accept_census")
            source_manifest_report = _read_report(args.source_manifest, args.max_report_bytes)
            copy_manifest_report = _read_report(args.copy_manifest, args.max_report_bytes)
            source_census_report = _read_report(args.source_census, args.max_report_bytes) if args.source_census else None
            copy_census_report = _read_report(args.copy_census, args.max_report_bytes) if args.copy_census else None
            result = compare(
                args.phase, source_manifest_report, copy_manifest_report,
                source_census_report, copy_census_report,
            )
    except Refusal as exc:
        print(json.dumps({"status": "refused", "scope": "reported_source_copy_inventory", "reason": str(exc)}, sort_keys=True))
        return 2
    except OSError:
        print(json.dumps({"status": "refused", "scope": "reported_source_copy_inventory", "reason": "report_unreadable"}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "matched" else 1


if __name__ == "__main__":
    raise SystemExit(main())
