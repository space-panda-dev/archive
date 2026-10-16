#!/usr/bin/env python3
"""Read-only census of JSON and JSONL source fixtures.

This reports source structure only. It does not validate records, assign Archive
destinations, change records, interpret timestamp values, or certify translation
or writer/reader coverage. `complete` means every discovered source unit was
read and classified under the fixed source-schema catalog, with no unknown or
missing schema. Catalog membership is not record validation or migration support.
Top-level source contract path stamps are counted without interpreting record
payloads or inferring Archive contract identities. A complete report requires at
least one schema-tagged source record. Callers must provide an immutable source
tree; this scan does not establish a stable snapshot.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

from migration.catalog import SOURCE_SCHEMAS, UNRESOLVED_REASON
from migration.contracts import ContractCitationCensus
from migration.source_manifest import (
    FINGERPRINT_FORMAT as SOURCE_MANIFEST_FINGERPRINT_FORMAT,
    FORMAT as SOURCE_MANIFEST_FORMAT,
    ScanFailure,
    manifest_fingerprint,
    open_root_without_symlinks,
    source_manifest as scan_source_manifest,
)

BUCKETS = ("record", "unknown_schema", "missing_schema", "non_record", "malformed", "unreadable", "unsupported")
SUPPORTED_SUFFIXES = {".json", ".jsonl"}
TIMESTAMP_KEYS = {"at", "timestamp"}
MAX_SOURCE_MANIFEST_BYTES = 32 * 1024 * 1024
INCOMPLETE_KINDS = {"empty_source", "no_source_records", "enumeration_error", "missing_root", "unsupported_root", "root_symlink_unsupported", "unknown_schema", "missing_schema", "non_record", "unreadable", "invalid_utf8", "malformed_json", "malformed_jsonl", "duplicate_json_key", "non_finite_json_number", "unsupported_file", "symlink_unsupported", "blank_line", "malformed_contract_citation", "source_manifest_invalid", "source_manifest_mismatch"}
SOURCE_COVERAGE_FAILURES = {"empty_source", "no_source_records", "enumeration_error", "missing_root", "unsupported_root", "root_symlink_unsupported", "non_record", "unreadable", "invalid_utf8", "malformed_json", "malformed_jsonl", "duplicate_json_key", "non_finite_json_number", "unsupported_file", "symlink_unsupported", "blank_line"}


class DuplicateJSONKey(ValueError):
    def __init__(self, key: str):
        self.key = key
        super().__init__(f"duplicate object key: {key}")


class NonFiniteJSONNumber(ValueError):
    def __init__(self, value: str):
        self.value = value
        super().__init__(f"non-finite number is not valid JSON: {value}")


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJSONKey(key)
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    raise NonFiniteJSONNumber(value)


def _parse_finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise NonFiniteJSONNumber(value)
    return number


def _load_json(text: str) -> Any:
    return json.loads(
        text,
        object_pairs_hook=_object_without_duplicate_keys,
        parse_constant=_reject_constant,
        parse_float=_parse_finite_float,
    )


def _walk(root: Path) -> tuple[list[Path], list[dict[str, str]]]:
    """Enumerate files without following symlinks; preserve enumeration errors."""
    files: list[Path] = []
    errors: list[dict[str, str]] = []

    def visit(directory: Path) -> None:
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as exc:
            errors.append({"source": str(directory), "error": f"{type(exc).__name__}: {exc}"})
            return
        for entry in entries:
            path = Path(entry.path)
            try:
                if entry.is_symlink() or entry.is_file(follow_symlinks=False):
                    files.append(path)
                elif entry.is_dir(follow_symlinks=False):
                    visit(path)
                else:
                    files.append(path)
            except OSError as exc:
                errors.append({"source": str(path), "error": f"{type(exc).__name__}: {exc}"})
    visit(root)
    return files, errors


def _manifest_entries(path: Path) -> tuple[dict[str, dict[str, Any]] | None, str | None, str | None]:
    """Load and validate one complete source-manifest report."""
    try:
        if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_NONBLOCK"):
            return None, "source_manifest_invalid", None
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return None, "source_manifest_invalid", None
            with os.fdopen(fd, "rb", closefd=False) as handle:
                raw = handle.read(MAX_SOURCE_MANIFEST_BYTES + 1)
        finally:
            os.close(fd)
        if len(raw) > MAX_SOURCE_MANIFEST_BYTES:
            return None, "source_manifest_invalid", None
        value = _load_json(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, RecursionError):
        return None, "source_manifest_invalid", None
    if not isinstance(value, dict) or set(value) != {
        "format", "status", "atomic_snapshot", "requires_frozen_source",
        "file_count", "total_bytes", "files",
    }:
        return None, "source_manifest_invalid", None
    if (
        value["format"] != SOURCE_MANIFEST_FORMAT
        or value["status"] != "complete"
        or value["atomic_snapshot"] is not False
        or value["requires_frozen_source"] is not True
    ):
        return None, "source_manifest_invalid", None
    files = value["files"]
    if not isinstance(files, list) or not files:
        return None, "source_manifest_invalid", None
    entries: dict[str, dict[str, Any]] = {}
    folded: set[str] = set()
    total_bytes = 0
    ordered_paths: list[str] = []
    for entry in files:
        if not isinstance(entry, dict) or set(entry) != {"path", "bytes", "sha256"}:
            return None, "source_manifest_invalid", None
        relative = entry["path"]
        size = entry["bytes"]
        digest = entry["sha256"]
        if (
            not isinstance(relative, str)
            or not relative
            or relative == "."
            or relative.startswith("/")
            or "\\" in relative
            or "\x00" in relative
            or any(part in {"", ".", ".."} for part in relative.split("/"))
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or relative in entries
        ):
            return None, "source_manifest_invalid", None
        try:
            os.fsencode(relative)
        except UnicodeError:
            return None, "source_manifest_invalid", None
        path_key = unicodedata.normalize("NFC", relative.casefold())
        if path_key in folded:
            return None, "source_manifest_invalid", None
        folded.add(path_key)
        entries[relative] = entry
        ordered_paths.append(relative)
        total_bytes += size
    if (
        ordered_paths != sorted(ordered_paths, key=os.fsencode)
        or value["file_count"] != len(entries)
        or isinstance(value["file_count"], bool)
        or not isinstance(value["file_count"], int)
        or value["total_bytes"] != total_bytes
        or isinstance(value["total_bytes"], bool)
        or not isinstance(value["total_bytes"], int)
    ):
        return None, "source_manifest_invalid", None
    return entries, None, manifest_fingerprint(value)


def _signature(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
        info.st_size, info.st_mtime_ns, info.st_ctime_ns,
    )


def _read_manifest_bound(root_fd: int, relative: str, expected: dict[str, Any]) -> bytes:
    """Read one manifested file through directory descriptors without following links."""
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise OSError("safe descriptor-relative reads unavailable")
    parts = relative.split("/")
    current_fd = os.dup(root_fd)
    try:
        for component in parts[:-1]:
            before = os.stat(component, dir_fd=current_fd, follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode):
                raise OSError("source path changed")
            child_fd = os.open(
                component,
                os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_DIRECTORY", 0),
                dir_fd=current_fd,
            )
            opened = os.fstat(child_fd)
            if _signature(before) != _signature(opened):
                os.close(child_fd)
                raise OSError("source path changed")
            os.close(current_fd)
            current_fd = child_fd
        name = parts[-1]
        before = os.stat(name, dir_fd=current_fd, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            raise OSError("source file changed")
        fd = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=current_fd,
        )
        try:
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode) or _signature(before) != _signature(opened):
                raise OSError("source file changed")
            chunks: list[bytes] = []
            bytes_read = 0
            while True:
                chunk = os.read(fd, 1024 * 1024)
                if not chunk:
                    break
                bytes_read += len(chunk)
                if bytes_read > expected["bytes"]:
                    raise ValueError("source manifest mismatch")
                chunks.append(chunk)
            raw = b"".join(chunks)
            after = os.fstat(fd)
            if _signature(opened) != _signature(after):
                raise OSError("source file changed")
        finally:
            os.close(fd)
    finally:
        os.close(current_fd)
    if len(raw) != expected["bytes"] or hashlib.sha256(raw).hexdigest() != expected["sha256"]:
        raise ValueError("source manifest mismatch")
    return raw


def _timestamp_fields(value: Any) -> int:
    found = 0
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            found += sum(1 for key, item in current.items() if key in TIMESTAMP_KEYS and isinstance(item, str))
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return found


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    raise TypeError(f"unsupported parsed JSON value: {type(value).__name__}")


def _observe_top_level_fields(value: dict[str, Any], schema: str, observed: dict[str, dict[str, Any]]) -> None:
    schema_observed = observed.setdefault(schema, {"row_count": 0, "fields": {}})
    schema_observed["row_count"] += 1
    for field, field_value in value.items():
        field_observed = schema_observed["fields"].setdefault(
            field, {"present_count": 0, "null_count": 0, "json_type_counts": Counter()}
        )
        field_observed["present_count"] += 1
        json_type = _json_type(field_value)
        field_observed["json_type_counts"][json_type] += 1
        if json_type == "null":
            field_observed["null_count"] += 1


def _render_top_level_fields(observed: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        schema: {
            "row_count": schema_observed["row_count"],
            "fields": {
                field: {
                    "present_count": field_observed["present_count"],
                    "null_count": field_observed["null_count"],
                    "json_type_counts": dict(sorted(field_observed["json_type_counts"].items())),
                }
                for field, field_observed in sorted(schema_observed["fields"].items())
            },
        }
        for schema, schema_observed in sorted(observed.items())
    }


def _classify(value: Any, known: set[str], source: str, counts: Counter[str], schemas: Counter[str], diagnostics: list[dict[str, Any]], field_observations: dict[str, dict[str, Any]], *, line: int | None = None, item_index: int | None = None) -> None:
    locator: dict[str, Any] = {"source": source}
    if line is not None:
        locator["line"] = line
    if item_index is not None:
        locator["item_index"] = item_index
    if not isinstance(value, dict):
        counts["non_record"] += 1
        locator: dict[str, Any] = {"source": source, "kind": "non_record"}
        if line is not None:
            locator["line"] = line
        if item_index is not None:
            locator["item_index"] = item_index
        diagnostics.append(locator)
        return
    schema = value.get("schema")
    if not isinstance(schema, str) or not schema:
        counts["missing_schema"] += 1
        diagnostics.append({**locator, "kind": "missing_schema"})
    elif schema in known:
        counts["record"] += 1
        schemas[schema] += 1
        _observe_top_level_fields(value, schema, field_observations)
    else:
        counts["unknown_schema"] += 1
        schemas[schema] += 1
        _observe_top_level_fields(value, schema, field_observations)
        diagnostics.append({**locator, "kind": "unknown_schema", "schema": schema})


def census(root: Path, source_manifest: Path | None = None) -> dict[str, Any]:
    """Count every discovered file and JSON value, retaining failures explicitly."""
    root = Path(root)
    manifest_entries: dict[str, dict[str, Any]] | None = None
    manifest_failure: str | None = None
    manifest_sha256: str | None = None
    if source_manifest is not None:
        manifest_entries, manifest_failure, manifest_sha256 = _manifest_entries(Path(source_manifest))
    known = set(SOURCE_SCHEMAS)
    counts: Counter[str] = Counter({bucket: 0 for bucket in BUCKETS})
    schemas: Counter[str] = Counter()
    field_observations: dict[str, dict[str, Any]] = {}
    file_counts: Counter[str] = Counter()
    diagnostics: list[dict[str, Any]] = []
    timestamp_fields = 0
    contract_census = ContractCitationCensus()
    try:
        checked_root_fd, _ = open_root_without_symlinks(root)
    except ScanFailure as failure:
        files, walk_errors = [], []
        kind = (
            "root_symlink_unsupported" if failure.kind == "symlink_root_unsupported"
            else "missing_root" if failure.kind == "missing_or_unreadable_root"
            else "unsupported_root"
        )
        diagnostics.append({"source": str(root), "kind": kind})
        counts["unsupported"] += 1
    else:
        os.close(checked_root_fd)
        files, walk_errors = _walk(root)
        diagnostics.extend({**error, "kind": "enumeration_error"} for error in walk_errors)
        counts["unsupported"] += len(walk_errors)

    binding_failed = manifest_failure is not None
    root_fd: int | None = None
    if manifest_entries is not None:
        actual_paths = {path.relative_to(root).as_posix() for path in files}
        if actual_paths != set(manifest_entries) or walk_errors:
            binding_failed = True
        current_manifest = scan_source_manifest(root)
        if current_manifest.get("status") != "complete" or current_manifest.get("files") != [
            manifest_entries[path] for path in sorted(manifest_entries, key=os.fsencode)
        ]:
            binding_failed = True
        try:
            root_fd, root_info = open_root_without_symlinks(root)
            if _signature(root_info) != _signature(os.fstat(root_fd)):
                raise ScanFailure("changed_during_scan")
        except (OSError, ScanFailure):
            binding_failed = True

    files_accounted = 0
    blank_lines_observed = 0
    for path in files:
        source = str(path.relative_to(root))
        files_accounted += 1
        if path.is_symlink():
            binding_failed = binding_failed or manifest_entries is not None
            counts["unsupported"] += 1
            diagnostics.append({"source": source, "kind": "symlink_unsupported"})
            continue
        suffix = path.suffix.lower()
        raw: bytes | None = None
        if manifest_entries is not None:
            expected = manifest_entries.get(source)
            if expected is None or root_fd is None:
                binding_failed = True
            else:
                try:
                    raw = _read_manifest_bound(root_fd, source, expected)
                except (OSError, ValueError):
                    binding_failed = True
        if suffix not in SUPPORTED_SUFFIXES:
            counts["unsupported"] += 1
            diagnostics.append({"source": source, "kind": "unsupported_file"})
            continue
        file_counts[suffix] += 1
        if source_manifest is not None and (manifest_entries is None or root_fd is None):
            counts["unreadable"] += 1
            diagnostics.append({"source": source, "kind": "unreadable"})
            continue
        try:
            if source_manifest is not None and raw is None:
                raise OSError("source manifest mismatch")
            raw = raw if raw is not None else path.read_bytes()
        except OSError as exc:
            counts["unreadable"] += 1
            diagnostics.append({"source": source, "kind": "unreadable", "error": f"{type(exc).__name__}: {exc}"})
            continue
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            counts["malformed"] += 1
            diagnostics.append({"source": source, "kind": "invalid_utf8", "byte_offset": exc.start})
            continue
        if suffix == ".json":
            try:
                value = _load_json(content)
            except DuplicateJSONKey as exc:
                counts["malformed"] += 1
                diagnostics.append({"source": source, "kind": "duplicate_json_key", "key": exc.key})
                continue
            except NonFiniteJSONNumber as exc:
                counts["malformed"] += 1
                diagnostics.append({"source": source, "kind": "non_finite_json_number", "value": exc.value})
                continue
            except (json.JSONDecodeError, ValueError, RecursionError) as exc:
                counts["malformed"] += 1
                location = {"line": exc.lineno, "column": exc.colno} if isinstance(exc, json.JSONDecodeError) else {"error": f"{type(exc).__name__}: {exc}"}
                diagnostics.append({"source": source, "kind": "malformed_json", **location})
                continue
            values = value if isinstance(value, list) else [value]
            for index, item in enumerate(values, start=1):
                timestamp_fields += _timestamp_fields(item)
                _classify(item, known, source, counts, schemas, diagnostics, field_observations, item_index=index if isinstance(value, list) else None)
                locator: dict[str, Any] = {"source": source}
                if isinstance(value, list):
                    locator["item_index"] = index
                contract_census.add(item, locator)
        else:
            for line_number, line_text in enumerate(content.splitlines(), start=1):
                if not line_text.strip():
                    blank_lines_observed += 1
                    diagnostics.append({"source": source, "line": line_number, "kind": "blank_line"})
                    continue
                try:
                    value = _load_json(line_text)
                except DuplicateJSONKey as exc:
                    counts["malformed"] += 1
                    diagnostics.append({"source": source, "line": line_number, "kind": "duplicate_json_key", "key": exc.key})
                    continue
                except NonFiniteJSONNumber as exc:
                    counts["malformed"] += 1
                    diagnostics.append({"source": source, "line": line_number, "kind": "non_finite_json_number", "value": exc.value})
                    continue
                except (json.JSONDecodeError, ValueError, RecursionError) as exc:
                    counts["malformed"] += 1
                    location = {"column": exc.colno} if isinstance(exc, json.JSONDecodeError) else {"error": f"{type(exc).__name__}: {exc}"}
                    diagnostics.append({"source": source, "line": line_number, "kind": "malformed_jsonl", **location})
                    continue
                timestamp_fields += _timestamp_fields(value)
                _classify(value, known, source, counts, schemas, diagnostics, field_observations, line=line_number)
                contract_census.add(value, {"source": source, "line": line_number})

    if root_fd is not None:
        os.close(root_fd)
    if source_manifest is not None:
        if manifest_failure is not None:
            diagnostics.append({"source": str(source_manifest), "kind": manifest_failure})
        elif manifest_entries is not None:
            final_manifest = scan_source_manifest(root)
            if final_manifest.get("status") != "complete" or final_manifest.get("files") != [
                manifest_entries[path] for path in sorted(manifest_entries, key=os.fsencode)
            ]:
                binding_failed = True
            if binding_failed:
                diagnostics.append({"source": str(source_manifest), "kind": "source_manifest_mismatch"})
    entries_accounted = sum(counts.values())
    if entries_accounted == 0:
        diagnostics.append({"source": str(root), "kind": "empty_source"})
    catalog_counts = {schema: schemas.get(schema, 0) for schema in SOURCE_SCHEMAS}
    unknown_schema_counts = {
        schema: count for schema, count in sorted(schemas.items()) if schema not in known
    }
    catalog_total = sum(catalog_counts.values())
    unknown_total = sum(unknown_schema_counts.values())
    schema_tagged_total = counts["record"] + counts["unknown_schema"]
    if schema_tagged_total == 0:
        diagnostics.append({"source": str(root), "kind": "no_source_records"})
    contract_report = contract_census.report()
    source_coverage_complete = not any(item.get("kind") in SOURCE_COVERAGE_FAILURES for item in diagnostics)
    contract_report["source_coverage_complete"] = source_coverage_complete
    contract_report["complete"] = source_coverage_complete and contract_report["complete"]
    diagnostics.extend(contract_report["diagnostics"])
    complete = not any(item.get("kind") in INCOMPLETE_KINDS for item in diagnostics)
    return {
        "source_root": str(root),
        "source_manifest_binding": (
            {
                "status": "not_requested",
                "format": SOURCE_MANIFEST_FORMAT,
                "atomic_snapshot": False,
                "requires_frozen_source": True,
                "manifest_fingerprint_format": SOURCE_MANIFEST_FINGERPRINT_FORMAT,
                "manifest_sha256": None,
            }
            if source_manifest is None
            else {
                "status": "invalid" if manifest_failure is not None else "mismatch" if binding_failed else "matched",
                "format": SOURCE_MANIFEST_FORMAT,
                "atomic_snapshot": False,
                "requires_frozen_source": True,
                "manifest_fingerprint_format": SOURCE_MANIFEST_FINGERPRINT_FORMAT,
                "manifest_sha256": manifest_sha256,
            }
        ),
        "source_schema_catalog": list(SOURCE_SCHEMAS),
        "complete": complete,
        "files_discovered": len(files),
        "files_accounted": files_accounted,
        "files_by_suffix": dict(sorted(file_counts.items())),
        "blank_lines_observed": blank_lines_observed,
        "counts": {bucket: counts[bucket] for bucket in BUCKETS},
        "entries_accounted": entries_accounted,
        "schemas_observed": dict(sorted(schemas.items())),
        "top_level_fields_by_schema": _render_top_level_fields(field_observations),
        "schema_routes": {
            schema: {
                "live_record_count": count,
                "status": "unresolved" if schema in known else "unsupported",
                "reason": UNRESOLVED_REASON if schema in known else "schema is outside the fixed source catalog",
            }
            for schema, count in sorted(schemas.items())
        },
        "unknown_schemas": unknown_schema_counts,
        "count_conservation": {
            "scope": "internal_partition_consistency",
            "catalog_record_count": catalog_total,
            "unknown_schema_record_count": unknown_total,
            "schema_tagged_record_count": schema_tagged_total,
            "all_schema_tagged_records_accounted_for": catalog_total + unknown_total == schema_tagged_total,
            "migration_supported": False,
        },
        "source_contract_stamps": contract_report,
        "timestamp_fields_observed": timestamp_fields,
        "diagnostics": diagnostics,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Census JSON/JSONL source fixtures without translating them")
    parser.add_argument("root", type=Path, help="directory containing source fixtures")
    parser.add_argument("--source-manifest", type=Path, help="complete report from migration.source_manifest for this frozen source tree")
    args = parser.parse_args(argv)
    report = census(args.root, args.source_manifest)
    rendered = json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    try:
        sys.stdout.write(rendered)
    except OSError as exc:
        print(f"census report write failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    counts = report["count_conservation"]
    routes_clear = counts["catalog_record_count"] == 0 and counts["unknown_schema_record_count"] == 0
    return 0 if report["complete"] and routes_clear else 1


if __name__ == "__main__":
    raise SystemExit(main())
