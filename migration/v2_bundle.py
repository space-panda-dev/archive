#!/usr/bin/env python3
"""Build and verify synthetic candidates for the proposed V2 evidence bundle.

This module is deliberately a preparation tool only. It has no Archive or Door
writer, and its bundle format remains proposed by ADR 0036. Callers must pass a
frozen disposable source tree. Use ``build_candidate_file`` and
``verify_candidate_file`` for large trees; the bytes-returning API is capped for
small synthetic fixtures.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from migration.catalog import SOURCE_SCHEMAS
from migration.census import DuplicateJSONKey, NonFiniteJSONNumber, _load_json, _read_manifest_bound
from migration.source_manifest import (
    Limits as ScanLimits,
    ScanFailure,
    open_root_without_symlinks,
    source_manifest,
)

FORMAT = "archive.v2-migration-bundle/1"
BLOCK = 512
USTAR_MAX_SIZE = 0o77777777777
DEFAULT_MAX_FILES = 200_000
DEFAULT_MAX_BYTES = 2 * 1024 * 1024 * 1024 * 1024
DEFAULT_MAX_FILE_BYTES = USTAR_MAX_SIZE
DEFAULT_MAX_RECORDS = 250_000
MAX_PARSE_FILE_BYTES = 128 * 1024 * 1024
MAX_MANIFEST_BYTES = 256 * 1024 * 1024
MAX_BUNDLE_BYTES = 4 * 1024 * 1024 * 1024 * 1024
MAX_IN_MEMORY_BUNDLE_BYTES = 128 * 1024 * 1024
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_KNOWN = frozenset(SOURCE_SCHEMAS)


class BundleError(ValueError):
    """A fail-closed refusal while preparing or verifying a candidate."""


@dataclass(frozen=True)
class BundleLimits:
    max_files: int = DEFAULT_MAX_FILES
    max_bytes: int = DEFAULT_MAX_BYTES
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    max_records: int = DEFAULT_MAX_RECORDS
    max_depth: int = 128

    def __post_init__(self) -> None:
        if min(self.max_files, self.max_bytes, self.max_file_bytes, self.max_records, self.max_depth) < 1:
            raise ValueError("bundle limits must be positive")

    def scan_limits(self) -> ScanLimits:
        return ScanLimits(self.max_files, self.max_bytes, self.max_file_bytes, self.max_depth)


def _canonical_json(value: Any) -> bytes:
    # The manifest schema contains only ASCII keys, strings, arrays, objects,
    # integers and booleans. This is RFC 8785-compatible for that closed subset.
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _path_bytes(path: str) -> bytes:
    try:
        encoded = path.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise BundleError("member path is not valid UTF-8") from exc
    if not encoded or path.startswith("/") or "\x00" in path or "\n" in path or "\r" in path:
        raise BundleError("unsafe member path")
    if any(part in {"", ".", ".."} for part in path.split("/")):
        raise BundleError("unsafe member path component")
    return encoded


def _octal(value: int, width: int) -> bytes:
    rendered = format(value, "o").encode("ascii")
    if value < 0 or len(rendered) > width - 1:
        raise BundleError("USTAR numeric field overflow")
    return b"0" * (width - 1 - len(rendered)) + rendered + b"\x00"


def _header(name: str, size: int, kind: bytes) -> bytes:
    if size < 0 or size > USTAR_MAX_SIZE:
        raise BundleError("member exceeds USTAR size limit")
    name_raw = name.encode("ascii")
    if len(name_raw) > 100 or len(kind) != 1:
        raise BundleError("invalid USTAR header field")
    out = bytearray(BLOCK)
    out[0:100] = name_raw + b"\x00" * (100 - len(name_raw))
    out[100:108] = _octal(0o644, 8)
    out[108:116] = _octal(0, 8)
    out[116:124] = _octal(0, 8)
    out[124:136] = _octal(size, 12)
    out[136:148] = _octal(0, 12)
    out[148:156] = b" " * 8
    out[156:157] = kind
    out[257:263] = b"ustar\x00"
    out[263:265] = b"00"
    checksum = sum(out)
    out[148:156] = f"{checksum:06o}".encode("ascii") + b"\x00 "
    return bytes(out)


def _pax_path(path: str) -> bytes:
    value = _path_bytes(path)
    suffix = b" path=" + value + b"\n"
    length = len(suffix) + 1
    while True:
        record = str(length).encode("ascii") + suffix
        if len(record) == length:
            return record
        length = len(record)


def _tar(entries: list[tuple[str, bytes]]) -> bytes:
    out = bytearray()
    for number, (path, data) in enumerate(entries):
        _path_bytes(path)
        if len(data) > USTAR_MAX_SIZE:
            raise BundleError("member exceeds USTAR size limit")
        number_text = f"{number:08d}"
        pax = _pax_path(path)
        out.extend(_header(f"PaxHeaders/{number_text}", len(pax), b"x"))
        out.extend(pax)
        out.extend(b"\x00" * (-len(pax) % BLOCK))
        out.extend(_header(f"PaxFiles/{number_text}", len(data), b"0"))
        out.extend(data)
        out.extend(b"\x00" * (-len(data) % BLOCK))
    out.extend(b"\x00" * (2 * BLOCK))
    return bytes(out)


def _records_for_json(raw: bytes) -> tuple[list[tuple[int, int, dict[str, Any]]], str | None]:
    """Return byte-exact top-level JSON units and an inventory failure code."""
    try:
        text = raw.decode("utf-8")
        decoder = json.JSONDecoder(
            object_pairs_hook=lambda pairs: _unique_pairs(pairs),
            parse_constant=lambda value: _reject_constant(value),
            parse_float=lambda value: _finite_float(value),
        )
        start = len(text) - len(text.lstrip())
        value, end = decoder.raw_decode(text, start)
        if text[end:].strip():
            return [], "malformed_json"
        units: list[tuple[int, int, Any]] = []
        if isinstance(value, list):
            cursor = start + 1
            byte_cursor = len(text[:cursor].encode("utf-8"))
            while True:
                before_space = cursor
                while cursor < len(text) and text[cursor].isspace():
                    cursor += 1
                byte_cursor += len(text[before_space:cursor].encode("utf-8"))
                if cursor >= len(text):
                    return [], "malformed_json"
                if text[cursor] == "]":
                    break
                item_start = cursor
                item, cursor = decoder.raw_decode(text, cursor)
                item_bytes = len(text[item_start:cursor].encode("utf-8"))
                units.append((byte_cursor, byte_cursor + item_bytes, item))
                byte_cursor += item_bytes
                before_space = cursor
                while cursor < len(text) and text[cursor].isspace():
                    cursor += 1
                byte_cursor += len(text[before_space:cursor].encode("utf-8"))
                if text[cursor:cursor + 1] == ",":
                    cursor += 1
                    byte_cursor += 1
                elif text[cursor:cursor + 1] != "]":
                    return [], "malformed_json"
        else:
            units.append((len(text[:start].encode("utf-8")), len(text[:end].encode("utf-8")), value))
        if not units:
            return [], "unrecognized_record_inventory"
    except UnicodeDecodeError:
        return [], "invalid_utf8"
    except (json.JSONDecodeError, ValueError, RecursionError, DuplicateJSONKey, NonFiniteJSONNumber):
        return [], "malformed_json"
    records: list[tuple[int, int, dict[str, Any]]] = []
    for offset, stop, item in units:
        if not isinstance(item, dict) or not isinstance(item.get("schema"), str) or not item["schema"]:
            return records, "unrecognized_record_inventory"
        records.append((offset, stop - offset, item))
    return records, None


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    from migration.census import _object_without_duplicate_keys
    return _object_without_duplicate_keys(pairs)


def _reject_constant(value: str) -> Any:
    raise NonFiniteJSONNumber(value)


def _finite_float(value: str) -> float:
    from migration.census import _parse_finite_float
    return _parse_finite_float(value)


def _records_for_jsonl(raw: bytes) -> tuple[list[tuple[int, int, dict[str, Any]]], str | None]:
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        return [], "invalid_utf8"
    records: list[tuple[int, int, dict[str, Any]]] = []
    offset = 0
    for line in raw.splitlines(keepends=True):
        content = line.rstrip(b"\r\n")
        if not content.strip():
            return records, "blank_line"
        try:
            value = _load_json(content.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError, DuplicateJSONKey, NonFiniteJSONNumber):
            return records, "malformed_jsonl"
        if not isinstance(value, dict) or not isinstance(value.get("schema"), str) or not value["schema"]:
            return records, "unrecognized_record_inventory"
        records.append((offset, len(content), value))
        offset += len(line)
    if offset < len(raw):
        # splitlines omits no data, but retain this guard if line handling changes.
        return records, "malformed_jsonl"
    if not records:
        return [], "unrecognized_record_inventory"
    return records, None


def _accounting(schema: str) -> dict[str, str]:
    if schema not in _KNOWN:
        return {"status": "unresolved", "reason": "unsupported_source_schema"}
    return {"status": "unresolved", "reason": "no_accepted_archive_route"}


def _safe_component_path(path: str) -> None:
    raw = _path_bytes(path)
    key = unicodedata.normalize("NFC", path.casefold())
    if len(raw) == 0:
        raise BundleError("empty source path")


def _inventory_member(member: str, raw: bytes, member_digest: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if len(raw) > MAX_PARSE_FILE_BYTES:
        return {"status": "incomplete", "reason": "record_file_size_limit"}, []
    suffix = Path(member).suffix.lower()
    if suffix == ".json":
        located, reason = _records_for_json(raw)
    elif suffix == ".jsonl":
        located, reason = _records_for_jsonl(raw)
    else:
        located, reason = [], "unsupported_file_inventory"
    if reason is None and any(value["schema"] not in _KNOWN for _, _, value in located):
        reason = "unsupported_source_schema"
    records: list[dict[str, Any]] = []
    for offset, length, value in located:
        schema = value["schema"]
        record: dict[str, Any] = {
            "member": member,
            "member_sha256": member_digest,
            "offset": offset,
            "length": length,
            "sha256": "sha256:" + hashlib.sha256(raw[offset:offset + length]).hexdigest(),
            "accounting": _accounting(schema),
        }
        if isinstance(value.get("id"), str) and value["id"]:
            source: dict[str, str] = {"schema": schema, "id": value["id"]}
            if isinstance(value.get("revision_id"), str) and value["revision_id"]:
                source["revision_id"] = value["revision_id"]
            record["source"] = source
        records.append(record)
    inventory = (
        {"status": "complete", "count": len(located)} if reason is None
        else {"status": "incomplete", "reason": reason}
    )
    return inventory, records


def _signature(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
        info.st_size, info.st_mtime_ns, info.st_ctime_ns,
    )


def _open_bound_source(root_fd: int, relative: str, expected: dict[str, Any]) -> tuple[int, int, tuple[int, ...]]:
    """Open one manifest-bound regular file without following path symlinks."""
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise BundleError("safe descriptor-relative reads unavailable")
    parts = relative.split("/")
    current_fd = os.dup(root_fd)
    try:
        for component in parts[:-1]:
            before = os.stat(component, dir_fd=current_fd, follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode):
                raise BundleError("source path changed")
            child = os.open(
                component,
                os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | os.O_NOFOLLOW | os.O_DIRECTORY,
                dir_fd=current_fd,
            )
            if _signature(before) != _signature(os.fstat(child)):
                os.close(child)
                raise BundleError("source path changed")
            os.close(current_fd)
            current_fd = child
        name = parts[-1]
        before = os.stat(name, dir_fd=current_fd, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or before.st_size != expected["bytes"]:
            raise BundleError("source file changed")
        fd = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | os.O_NOFOLLOW,
            dir_fd=current_fd,
        )
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or _signature(before) != _signature(opened):
            os.close(fd)
            raise BundleError("source file changed")
        return fd, current_fd, _signature(opened)
    except BaseException:
        try:
            os.close(current_fd)
        except OSError:
            pass
        raise


def _close_source(fd: int, parent_fd: int) -> None:
    try:
        os.close(fd)
    finally:
        os.close(parent_fd)


class _HashingWriter:
    def __init__(self, handle: Any):
        self.handle = handle
        self.digest = hashlib.sha256()
        self.size = 0

    def write(self, data: bytes) -> None:
        count = self.handle.write(data)
        if count != len(data):
            raise BundleError("short write while producing bundle")
        self.digest.update(data)
        self.size += count


def _write_tar_member(writer: _HashingWriter, number: int, path: str, size: int) -> None:
    if size > USTAR_MAX_SIZE:
        raise BundleError("member exceeds USTAR size limit")
    pax = _pax_path(path)
    number_text = f"{number:08d}"
    writer.write(_header(f"PaxHeaders/{number_text}", len(pax), b"x"))
    writer.write(pax)
    writer.write(b"\x00" * (-len(pax) % BLOCK))
    writer.write(_header(f"PaxFiles/{number_text}", size, b"0"))


def _write_bound_source(writer: _HashingWriter, root_fd: int, item: dict[str, Any]) -> None:
    member = item["member"]
    relative = member.removeprefix("source/")
    fd, parent_fd, opened_signature = _open_bound_source(root_fd, relative, item)
    digest = hashlib.sha256()
    read_size = 0
    try:
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            read_size += len(chunk)
            if read_size > item["bytes"]:
                raise BundleError("source changed while packaging")
            digest.update(chunk)
            writer.write(chunk)
        after = os.fstat(fd)
        if read_size != item["bytes"] or opened_signature != _signature(after):
            raise BundleError("source changed while packaging")
    finally:
        _close_source(fd, parent_fd)
    if "sha256:" + digest.hexdigest() != item["sha256"]:
        raise BundleError("source digest changed while packaging")
    writer.write(b"\x00" * (-read_size % BLOCK))


def _prepare_stream_manifest(root: Path, limits: BundleLimits) -> tuple[dict[str, Any], dict[str, Any]]:
    before = source_manifest(root, limits.scan_limits())
    if before.get("status") != "complete":
        raise BundleError("source scan incomplete")
    files: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    folded: set[str] = set()
    try:
        root_fd, _ = open_root_without_symlinks(root)
    except ScanFailure as exc:
        raise BundleError("source root unavailable") from exc
    try:
        for scanned in before["files"]:
            relative = scanned["path"]
            member = "source/" + relative
            _safe_component_path(member)
            key = unicodedata.normalize("NFC", member.casefold())
            if key in folded:
                raise BundleError("member path collision")
            folded.add(key)
            digest = "sha256:" + scanned["sha256"]
            file_entry: dict[str, Any] = {
                "member": member,
                "bytes": scanned["bytes"],
                "sha256": digest,
            }
            if scanned["bytes"] > MAX_PARSE_FILE_BYTES:
                inventory, located = {"status": "incomplete", "reason": "record_file_size_limit"}, []
            else:
                try:
                    raw = _read_manifest_bound(root_fd, relative, scanned)
                except (OSError, ValueError) as exc:
                    raise BundleError("source changed or could not be read") from exc
                inventory, located = _inventory_member(member, raw, digest)
            file_entry["record_inventory"] = inventory
            files.append(file_entry)
            records.extend(located)
            if len(records) > limits.max_records:
                raise BundleError("record locator limit exceeded")
    finally:
        os.close(root_fd)
    records.sort(key=lambda item: (item["member"].encode("utf-8"), item["offset"], item["length"], item["sha256"]))
    manifest = {
        "format": FORMAT,
        "source_file_count": len(files),
        "record_locator_count": len(records),
        "record_inventory": "complete" if all(item["record_inventory"]["status"] == "complete" for item in files) else "incomplete",
        "total_source_bytes": sum(item["bytes"] for item in files),
        "files": files,
        "records": records,
    }
    encoded = _canonical_json(manifest)
    if len(encoded) > MAX_MANIFEST_BYTES:
        raise BundleError("manifest size limit exceeded")
    return manifest, before


def build_candidate_file(
    root: Path,
    destination: Path,
    limits: BundleLimits = BundleLimits(),
) -> dict[str, Any]:
    """Stream a candidate bundle to a new disposable file and return its digest.

    The destination must be outside the frozen source tree and must not exist.
    Source members are read and written one at a time; the complete tar stream
    is never held in memory. This function writes no Archive state.
    """
    root = Path(root)
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise BundleError("candidate destination already exists")
    try:
        destination.parent.stat()
    except OSError as exc:
        raise BundleError("candidate destination parent unavailable") from exc
    manifest, before = _prepare_stream_manifest(root, limits)
    physical_root = root.resolve(strict=True)
    if destination.resolve(strict=False).is_relative_to(physical_root):
        raise BundleError("candidate destination must be outside source tree")
    manifest_bytes = _canonical_json(manifest)
    temp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=destination.parent, prefix=".v2-bundle-", delete=False) as handle:
            temp_path = handle.name
            writer = _HashingWriter(handle)
            members = [("manifest.json", manifest_bytes)]
            members.extend((item["member"], item) for item in manifest["files"])
            members.sort(key=lambda pair: pair[0].encode("utf-8"))
            try:
                root_fd, _ = open_root_without_symlinks(root)
            except ScanFailure as exc:
                raise BundleError("source root unavailable") from exc
            try:
                for number, (member, value) in enumerate(members):
                    size = len(value) if isinstance(value, bytes) else value["bytes"]
                    _write_tar_member(writer, number, member, size)
                    if isinstance(value, bytes):
                        writer.write(value)
                        writer.write(b"\x00" * (-size % BLOCK))
                    else:
                        _write_bound_source(writer, root_fd, value)
                writer.write(b"\x00" * (2 * BLOCK))
            finally:
                os.close(root_fd)
            if writer.size > MAX_BUNDLE_BYTES:
                raise BundleError("bundle size limit exceeded")
            handle.flush()
            os.fsync(handle.fileno())
        after = source_manifest(root, limits.scan_limits())
        if after.get("status") != "complete" or before["files"] != after.get("files"):
            raise BundleError("source changed during candidate preparation")
        checked = verify_candidate_file(Path(temp_path))
        if checked["bytes"] != writer.size or checked["sha256"] != "sha256:" + writer.digest.hexdigest():
            raise BundleError("streamed candidate verification mismatch")
        os.link(temp_path, destination)
        os.unlink(temp_path)
        temp_path = None
        return {
            "path": str(destination),
            "bytes": writer.size,
            "sha256": "sha256:" + writer.digest.hexdigest(),
            "manifest": manifest,
        }
    finally:
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass


def build_candidate(root: Path, limits: BundleLimits = BundleLimits()) -> bytes:
    """Return a deterministic PAX candidate from one frozen disposable tree.

    The output is bytes only. This function does not touch an Archive root or
    invoke Door. Incomplete source-record inventories remain explicit in the
    in-bundle manifest and are never promoted to complete accounting.
    """
    root = Path(root)
    before = source_manifest(root, limits.scan_limits())
    if before.get("status") != "complete":
        raise BundleError("source scan incomplete")
    if before["total_bytes"] > MAX_IN_MEMORY_BUNDLE_BYTES:
        raise BundleError("source exceeds in-memory candidate limit; use build_candidate_file")
    files: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    source_data: dict[str, bytes] = {}
    folded: set[str] = set()
    try:
        root_fd, _ = open_root_without_symlinks(root)
    except ScanFailure as exc:
        raise BundleError("source root unavailable") from exc
    try:
        for file in before["files"]:
            relative = file["path"]
            member = "source/" + relative
            _safe_component_path(member)
            key = unicodedata.normalize("NFC", member.casefold())
            if key in folded:
                raise BundleError("member path collision")
            folded.add(key)
            try:
                raw = _read_manifest_bound(root_fd, relative, file)
            except (OSError, ValueError) as exc:
                raise BundleError("source changed or could not be read") from exc
            source_data[member] = raw
            suffix = Path(relative).suffix.lower()
            located: list[tuple[int, int, dict[str, Any]]] = []
            reason: str | None
            if suffix == ".json":
                located, reason = _records_for_json(raw)
            elif suffix == ".jsonl":
                located, reason = _records_for_jsonl(raw)
            else:
                reason = "unsupported_file_inventory"
            if reason is None and any(value["schema"] not in _KNOWN for _, _, value in located):
                reason = "unsupported_source_schema"
            for offset, length, value in located:
                schema = value["schema"]
                record: dict[str, Any] = {
                    "member": member,
                    "member_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
                    "offset": offset,
                    "length": length,
                    "sha256": "sha256:" + hashlib.sha256(raw[offset:offset + length]).hexdigest(),
                    "accounting": _accounting(schema),
                }
                if isinstance(value.get("id"), str) and value["id"]:
                    source: dict[str, str] = {"schema": schema, "id": value["id"]}
                    if isinstance(value.get("revision_id"), str) and value["revision_id"]:
                        source["revision_id"] = value["revision_id"]
                    record["source"] = source
                records.append(record)
                if len(records) > limits.max_records:
                    raise BundleError("record locator limit exceeded")
            inventory = (
                {"status": "complete", "count": len(located)}
                if reason is None
                else {"status": "incomplete", "reason": reason}
            )
            files.append({
                "member": member,
                "bytes": len(raw),
                "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
                "record_inventory": inventory,
            })
    finally:
        os.close(root_fd)
    after = source_manifest(root, limits.scan_limits())
    if after.get("status") != "complete" or before["files"] != after.get("files"):
        raise BundleError("source changed during candidate preparation")
    record_inventory = "complete" if all(item["record_inventory"]["status"] == "complete" for item in files) else "incomplete"
    records.sort(key=lambda item: (item["member"].encode("utf-8"), item["offset"], item["length"], item["sha256"]))
    manifest = {
        "format": FORMAT,
        "source_file_count": len(files),
        "record_locator_count": len(records),
        "record_inventory": record_inventory,
        "total_source_bytes": sum(item["bytes"] for item in files),
        "files": files,
        "records": records,
    }
    manifest_bytes = _canonical_json(manifest)
    if len(manifest_bytes) > MAX_MANIFEST_BYTES:
        raise BundleError("manifest size limit exceeded")
    entries = [("manifest.json", manifest_bytes)]
    entries.extend((item["member"], source_data[item["member"]]) for item in files)
    entries.sort(key=lambda pair: pair[0].encode("utf-8"))
    bundle = _tar(entries)
    if len(bundle) > MAX_IN_MEMORY_BUNDLE_BYTES:
        raise BundleError("bundle size limit exceeded")
    verify_candidate(bundle)
    return bundle


def _parse_octal(field: bytes) -> int:
    stripped = field.strip(b"\x00 ")
    if not stripped or any(byte not in b"01234567" for byte in stripped):
        raise BundleError("malformed USTAR numeric field")
    return int(stripped, 8)


def _verify_header(header: bytes, expected_name: str, expected_type: bytes, expected_size: int) -> None:
    if len(header) != BLOCK or header != _header(expected_name, expected_size, expected_type):
        raise BundleError("non-canonical USTAR member header")


def _read_pax_path(data: bytes) -> str:
    match = re.fullmatch(rb"([1-9][0-9]*) path=(.*)\n", data, re.DOTALL)
    if match is None or int(match.group(1)) != len(data):
        raise BundleError("malformed PAX path record")
    try:
        path = match.group(2).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BundleError("PAX path is not UTF-8") from exc
    _path_bytes(path)
    return path


def verify_candidate(bundle: bytes) -> dict[str, Any]:
    """Verify canonical tar bytes, manifest accounting, and every source digest."""
    if not isinstance(bundle, bytes) or len(bundle) < 2 * BLOCK or len(bundle) > MAX_IN_MEMORY_BUNDLE_BYTES or len(bundle) % BLOCK:
        raise BundleError("bundle is not a complete block-aligned tar stream")
    entries: list[tuple[str, bytes]] = []
    cursor = 0
    number = 0
    while cursor < len(bundle):
        if bundle[cursor:cursor + 2 * BLOCK] == b"\x00" * (2 * BLOCK):
            if cursor + 2 * BLOCK != len(bundle):
                raise BundleError("tar must end with exactly two zero blocks")
            break
        if cursor + BLOCK > len(bundle):
            raise BundleError("truncated PAX header")
        pax_name = f"PaxHeaders/{number:08d}"
        file_name = f"PaxFiles/{number:08d}"
        pax_header = bundle[cursor:cursor + BLOCK]
        pax_size = _parse_octal(pax_header[124:136])
        _verify_header(pax_header, pax_name, b"x", pax_size)
        cursor += BLOCK
        pax = bundle[cursor:cursor + pax_size]
        if len(pax) != pax_size:
            raise BundleError("truncated PAX record")
        path = _read_pax_path(pax)
        cursor += pax_size + (-pax_size % BLOCK)
        file_header = bundle[cursor:cursor + BLOCK]
        file_size = _parse_octal(file_header[124:136])
        _verify_header(file_header, file_name, b"0", file_size)
        cursor += BLOCK
        data = bundle[cursor:cursor + file_size]
        if len(data) != file_size:
            raise BundleError("truncated file member")
        cursor += file_size + (-file_size % BLOCK)
        entries.append((path, data))
        number += 1
    if not entries or entries[0][0] != "manifest.json":
        raise BundleError("manifest.json must be present")
    if entries != sorted(entries, key=lambda pair: pair[0].encode("utf-8")):
        raise BundleError("logical members are not UTF-8 byte sorted")
    member_names = [path for path, _ in entries]
    if len(set(member_names)) != len(member_names) or any(not path.startswith("source/") for path in member_names[1:]):
        raise BundleError("source members are missing or duplicated")
    if len(member_names) < 2:
        raise BundleError("bundle must contain at least one source member")
    folded_members = [unicodedata.normalize("NFC", path.casefold()) for path in member_names]
    if len(set(folded_members)) != len(folded_members):
        raise BundleError("member path collision")
    if _tar(entries) != bundle:
        raise BundleError("tar stream is not canonical")
    manifest_raw = entries[0][1]
    try:
        manifest = json.loads(manifest_raw.decode("utf-8"), object_pairs_hook=_unique_pairs, parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError, RecursionError, DuplicateJSONKey, NonFiniteJSONNumber) as exc:
        raise BundleError("manifest is malformed") from exc
    if not isinstance(manifest, dict) or set(manifest) != {
        "format", "source_file_count", "record_locator_count", "record_inventory",
        "total_source_bytes", "files", "records",
    } or manifest.get("format") != FORMAT or _canonical_json(manifest) != manifest_raw:
        raise BundleError("manifest shape or canonical encoding is invalid")
    files = manifest["files"]
    records = manifest["records"]
    source_entries = entries[1:]
    if not isinstance(files, list) or not isinstance(records, list) or len(files) != len(source_entries):
        raise BundleError("manifest inventory count mismatch")
    if not isinstance(manifest["source_file_count"], int) or isinstance(manifest["source_file_count"], bool) or manifest["source_file_count"] != len(files):
        raise BundleError("source_file_count mismatch")
    member_data = {path: data for path, data in source_entries}
    if [item.get("member") if isinstance(item, dict) else None for item in files] != [path for path, _ in source_entries]:
        raise BundleError("file member listing mismatch")
    total = 0
    per_member: dict[str, int] = {path: 0 for path in member_data}
    for item in files:
        if not isinstance(item, dict) or set(item) != {"member", "bytes", "sha256", "record_inventory"}:
            raise BundleError("file manifest entry is not closed")
        member = item["member"]
        data = member_data[member]
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        if not isinstance(item["bytes"], int) or isinstance(item["bytes"], bool) or item["bytes"] != len(data) or item["sha256"] != digest:
            raise BundleError("source file length or digest mismatch")
        inventory = item["record_inventory"]
        if not isinstance(inventory, dict) or inventory.get("status") not in {"complete", "incomplete"}:
            raise BundleError("invalid per-file record inventory")
        if inventory["status"] == "complete":
            if set(inventory) != {"status", "count"} or not isinstance(inventory["count"], int) or isinstance(inventory["count"], bool) or inventory["count"] < 0:
                raise BundleError("invalid complete record inventory")
        elif set(inventory) != {"status", "reason"} or not isinstance(inventory["reason"], str) or not inventory["reason"]:
            raise BundleError("invalid incomplete record inventory")
        total += len(data)
    if not isinstance(manifest["total_source_bytes"], int) or isinstance(manifest["total_source_bytes"], bool) or manifest["total_source_bytes"] != total:
        raise BundleError("total_source_bytes mismatch")
    prior_key: tuple[bytes, int, int, str] | None = None
    for record in records:
        required = {"member", "member_sha256", "offset", "length", "sha256", "accounting"}
        if not isinstance(record, dict) or not required <= set(record) or set(record) - (required | {"source"}):
            raise BundleError("record locator is not closed")
        member = record["member"]
        if member not in member_data or not member.startswith("source/"):
            raise BundleError("record locator names a missing member")
        data = member_data[member]
        start, length = record["offset"], record["length"]
        if any(not isinstance(x, int) or isinstance(x, bool) for x in (start, length)) or start < 0 or length <= 0 or start + length > len(data):
            raise BundleError("record locator is out of bounds")
        if record["member_sha256"] != "sha256:" + hashlib.sha256(data).hexdigest() or record["sha256"] != "sha256:" + hashlib.sha256(data[start:start + length]).hexdigest():
            raise BundleError("record locator digest mismatch")
        accounting = record["accounting"]
        if not isinstance(accounting, dict) or accounting.get("status") == "candidate_route":
            raise BundleError("unsupported candidate route")
        if accounting.get("status") == "raw_only":
            if set(accounting) != {"status"}:
                raise BundleError("invalid raw_only accounting")
        elif accounting.get("status") == "unresolved":
            if set(accounting) != {"status", "reason"} or not isinstance(accounting["reason"], str) or not accounting["reason"]:
                raise BundleError("invalid unresolved accounting")
        else:
            raise BundleError("unknown record accounting status")
        if "source" in record:
            source = record["source"]
            if not isinstance(source, dict) or not {"schema", "id"} <= set(source) or set(source) - {"schema", "id", "revision_id"} or not all(isinstance(value, str) and value for value in source.values()):
                raise BundleError("invalid source locator identity")
        key = (member.encode("utf-8"), start, length, record["sha256"])
        if prior_key is not None and key < prior_key:
            raise BundleError("record locators are not sorted")
        prior_key = key
        per_member[member] += 1
    for item in files:
        inventory = item["record_inventory"]
        if inventory["status"] == "complete" and inventory["count"] != per_member[item["member"]]:
            raise BundleError("per-file record count mismatch")
    expected_inventory = "complete" if all(item["record_inventory"]["status"] == "complete" for item in files) else "incomplete"
    if (
        manifest["record_inventory"] != expected_inventory
        or not isinstance(manifest["record_locator_count"], int)
        or isinstance(manifest["record_locator_count"], bool)
        or manifest["record_locator_count"] != len(records)
    ):
        raise BundleError("record inventory summary mismatch")
    if len(records) > DEFAULT_MAX_RECORDS:
        raise BundleError("record locator limit exceeded")
    expected_records: list[dict[str, Any]] = []
    expected_file_inventories: dict[str, dict[str, Any]] = {}
    for item in files:
        member = item["member"]
        raw = member_data[member]
        suffix = Path(member).suffix.lower()
        if suffix == ".json":
            located, reason = _records_for_json(raw)
        elif suffix == ".jsonl":
            located, reason = _records_for_jsonl(raw)
        else:
            located, reason = [], "unsupported_file_inventory"
        if reason is None and any(value["schema"] not in _KNOWN for _, _, value in located):
            reason = "unsupported_source_schema"
        expected_file_inventories[member] = (
            {"status": "complete", "count": len(located)} if reason is None
            else {"status": "incomplete", "reason": reason}
        )
        for start, length, value in located:
            schema = value["schema"]
            locator: dict[str, Any] = {
                "member": member,
                "member_sha256": item["sha256"],
                "offset": start,
                "length": length,
                "sha256": "sha256:" + hashlib.sha256(raw[start:start + length]).hexdigest(),
                "accounting": _accounting(schema),
            }
            if isinstance(value.get("id"), str) and value["id"]:
                source: dict[str, str] = {"schema": schema, "id": value["id"]}
                if isinstance(value.get("revision_id"), str) and value["revision_id"]:
                    source["revision_id"] = value["revision_id"]
                locator["source"] = source
            expected_records.append(locator)
    expected_records.sort(key=lambda item: (item["member"].encode("utf-8"), item["offset"], item["length"], item["sha256"]))
    if expected_records != records or any(item["record_inventory"] != expected_file_inventories[item["member"]] for item in files):
        raise BundleError("record accounting does not match source bytes")
    return manifest


def _stream_exact(handle: Any, size: int, digest: Any) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = handle.read(min(1024 * 1024, remaining))
        if not chunk:
            raise BundleError("truncated bundle stream")
        digest.update(chunk)
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _stream_padding(handle: Any, size: int, digest: Any) -> None:
    padding = -size % BLOCK
    if padding and _stream_exact(handle, padding, digest) != b"\x00" * padding:
        raise BundleError("nonzero tar padding")


def _read_stream_member(handle: Any, digest: Any, number: int, expected_path: str | None = None) -> tuple[str, int, Any]:
    pax_header = _stream_exact(handle, BLOCK, digest)
    pax_size = _parse_octal(pax_header[124:136])
    if pax_size < 1 or pax_size > 1024 * 1024:
        raise BundleError("PAX path size limit exceeded")
    _verify_header(pax_header, f"PaxHeaders/{number:08d}", b"x", pax_size)
    pax = _stream_exact(handle, pax_size, digest)
    _stream_padding(handle, pax_size, digest)
    path = _read_pax_path(pax)
    if expected_path is not None and path != expected_path:
        raise BundleError("unexpected logical member path")
    file_header = _stream_exact(handle, BLOCK, digest)
    size = _parse_octal(file_header[124:136])
    _verify_header(file_header, f"PaxFiles/{number:08d}", b"0", size)
    return path, size, digest


def verify_candidate_file(path: Path) -> dict[str, Any]:
    """Verify a candidate from disk without loading the tar stream in memory.

    The manifest is bounded to 256 MiB, and at most one source member (up to
    256 MiB) is buffered for JSON record-locator verification at a time.
    Source members and the complete tar stream are read incrementally.
    """
    path = Path(path)
    try:
        fd = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        )
        info = os.fstat(fd)
    except OSError as exc:
        raise BundleError("candidate file unavailable") from exc
    if not stat.S_ISREG(info.st_mode):
        os.close(fd)
        raise BundleError("candidate must be a regular file")
    total_size = info.st_size
    if total_size < 2 * BLOCK or total_size > MAX_BUNDLE_BYTES or total_size % BLOCK:
        os.close(fd)
        raise BundleError("bundle is not a bounded block-aligned tar stream")
    tar_digest = hashlib.sha256()
    with os.fdopen(fd, "rb", closefd=True) as handle:
        first_path, first_size, _ = _read_stream_member(handle, tar_digest, 0)
        if first_path != "manifest.json" or first_size > MAX_MANIFEST_BYTES:
            raise BundleError("manifest.json is missing or exceeds its size limit")
        manifest_raw = _stream_exact(handle, first_size, tar_digest)
        _stream_padding(handle, first_size, tar_digest)
        try:
            manifest = json.loads(
                manifest_raw.decode("utf-8"),
                object_pairs_hook=_unique_pairs,
                parse_constant=_reject_constant,
            )
        except (UnicodeDecodeError, ValueError, RecursionError, DuplicateJSONKey, NonFiniteJSONNumber) as exc:
            raise BundleError("manifest is malformed") from exc
        if not isinstance(manifest, dict) or set(manifest) != {
            "format", "source_file_count", "record_locator_count", "record_inventory",
            "total_source_bytes", "files", "records",
        } or manifest.get("format") != FORMAT or _canonical_json(manifest) != manifest_raw:
            raise BundleError("manifest shape or canonical encoding is invalid")
        files = manifest["files"]
        records = manifest["records"]
        if not isinstance(files, list) or not files or not isinstance(records, list):
            raise BundleError("manifest must account for source files and records")
        if len(files) > DEFAULT_MAX_FILES or len(records) > DEFAULT_MAX_RECORDS:
            raise BundleError("manifest count limit exceeded")
        if not isinstance(manifest["source_file_count"], int) or isinstance(manifest["source_file_count"], bool) or manifest["source_file_count"] != len(files):
            raise BundleError("source_file_count mismatch")
        if not isinstance(manifest["record_locator_count"], int) or isinstance(manifest["record_locator_count"], bool) or manifest["record_locator_count"] != len(records):
            raise BundleError("record_locator_count mismatch")
        if not isinstance(manifest["total_source_bytes"], int) or isinstance(manifest["total_source_bytes"], bool) or not 0 <= manifest["total_source_bytes"] <= DEFAULT_MAX_BYTES:
            raise BundleError("total_source_bytes is invalid")
        expected_file_names: list[str] = []
        total_source_bytes = 0
        folded: set[str] = {unicodedata.normalize("NFC", "manifest.json".casefold())}
        for item in files:
            if not isinstance(item, dict) or set(item) != {"member", "bytes", "sha256", "record_inventory"}:
                raise BundleError("file manifest entry is not closed")
            member = item["member"]
            if not isinstance(member, str) or not member.startswith("source/"):
                raise BundleError("invalid source member path")
            _path_bytes(member)
            key = unicodedata.normalize("NFC", member.casefold())
            if key in folded:
                raise BundleError("member path collision")
            folded.add(key)
            expected_file_names.append(member)
            size = item["bytes"]
            if not isinstance(size, int) or isinstance(size, bool) or size < 0 or size > USTAR_MAX_SIZE:
                raise BundleError("source file size is invalid")
            if not isinstance(item["sha256"], str) or _HEX.fullmatch(item["sha256"].removeprefix("sha256:")) is None or not item["sha256"].startswith("sha256:"):
                raise BundleError("source file digest is invalid")
            inventory = item["record_inventory"]
            if not isinstance(inventory, dict) or inventory.get("status") not in {"complete", "incomplete"}:
                raise BundleError("invalid source record inventory")
            if inventory["status"] == "complete":
                if set(inventory) != {"status", "count"} or not isinstance(inventory["count"], int) or isinstance(inventory["count"], bool) or inventory["count"] < 0:
                    raise BundleError("invalid complete source inventory")
            elif set(inventory) != {"status", "reason"} or not isinstance(inventory["reason"], str) or not inventory["reason"]:
                raise BundleError("invalid incomplete source inventory")
            total_source_bytes += size
        if expected_file_names != sorted(expected_file_names, key=lambda value: value.encode("utf-8")):
            raise BundleError("manifest files are not UTF-8 byte sorted")
        if total_source_bytes != manifest["total_source_bytes"]:
            raise BundleError("total_source_bytes mismatch")
        if total_source_bytes > DEFAULT_MAX_BYTES:
            raise BundleError("source byte limit exceeded")
        expected_records: list[dict[str, Any]] = []
        per_file_inventory: dict[str, dict[str, Any]] = {}
        for number, item in enumerate(files, start=1):
            member, declared_size, _ = _read_stream_member(handle, tar_digest, number, item["member"])
            if declared_size != item["bytes"]:
                raise BundleError("source member size mismatch")
            source_digest = hashlib.sha256()
            raw_buffer: bytearray | None = bytearray() if declared_size <= MAX_PARSE_FILE_BYTES else None
            remaining = declared_size
            while remaining:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise BundleError("truncated source member")
                tar_digest.update(chunk)
                source_digest.update(chunk)
                if raw_buffer is not None:
                    raw_buffer.extend(chunk)
                remaining -= len(chunk)
            _stream_padding(handle, declared_size, tar_digest)
            member_digest = "sha256:" + source_digest.hexdigest()
            if member_digest != item["sha256"]:
                raise BundleError("source member digest mismatch")
            if raw_buffer is None:
                inventory, located = {"status": "incomplete", "reason": "record_file_size_limit"}, []
            else:
                inventory, located = _inventory_member(member, bytes(raw_buffer), member_digest)
            if item["record_inventory"] != inventory:
                raise BundleError("source inventory does not match member bytes")
            per_file_inventory[member] = inventory
            expected_records.extend(located)
            if len(expected_records) > DEFAULT_MAX_RECORDS:
                raise BundleError("record locator limit exceeded")
        trailing = _stream_exact(handle, 2 * BLOCK, tar_digest)
        if trailing != b"\x00" * (2 * BLOCK) or handle.read(1):
            raise BundleError("tar must end with exactly two zero blocks")
        expected_records.sort(key=lambda item: (item["member"].encode("utf-8"), item["offset"], item["length"], item["sha256"]))
        if records != expected_records:
            raise BundleError("record locators do not match source bytes")
        expected_inventory = "complete" if all(item["status"] == "complete" for item in per_file_inventory.values()) else "incomplete"
        if manifest["record_inventory"] != expected_inventory:
            raise BundleError("record inventory summary mismatch")
        return {
            "manifest": manifest,
            "bytes": total_size,
            "sha256": "sha256:" + tar_digest.hexdigest(),
        }
