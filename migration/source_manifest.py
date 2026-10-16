#!/usr/bin/env python3
"""Build a bounded, read-only source manifest for a frozen source tree.

The manifest contains relative paths, byte counts, and SHA-256 digests only; it
does not parse or print record contents. The caller must provide a disposable,
frozen or quiesced tree. This scanner detects many changes during a scan, but a
filesystem walk is not an atomic snapshot under concurrent writers.

Run with ``python3 -m migration.source_manifest <tree>``. A non-zero exit and
an ``incomplete`` JSON result mean no complete manifest was produced.
"""
from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import stat
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

FORMAT = "archive-source-manifest/1"
FINGERPRINT_FORMAT = "archive-source-manifest-json-fingerprint/1"
CHUNK_SIZE = 1024 * 1024
DEFAULT_MAX_FILES = 100_000
DEFAULT_MAX_BYTES = 1_099_511_627_776  # 1 TiB
DEFAULT_MAX_FILE_BYTES = 68_719_476_736  # 64 GiB
DEFAULT_MAX_DEPTH = 128


def manifest_fingerprint(manifest: dict[str, object]) -> str:
    """Hash the sorted-key, ASCII-escaped, compact JSON form of a manifest."""
    encoded = json.dumps(
        manifest,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


class ScanFailure(Exception):
    """A refusal safe to report without exposing file contents."""

    def __init__(self, kind: str, path: str | None = None):
        self.kind = kind
        self.path = path
        super().__init__(kind)


@dataclass(frozen=True)
class Limits:
    max_files: int = DEFAULT_MAX_FILES
    max_bytes: int = DEFAULT_MAX_BYTES
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    max_depth: int = DEFAULT_MAX_DEPTH

    def __post_init__(self) -> None:
        if min(self.max_files, self.max_bytes, self.max_file_bytes, self.max_depth) < 1:
            raise ValueError("all scan limits must be positive")


def _signature(info: os.stat_result) -> tuple[int, ...]:
    """Fields used to detect replacement or mutation while an item is read."""
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _open_flags(directory: bool = False) -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    return flags


def _safe_open_flags_available() -> bool:
    return hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY")


def open_root_without_symlinks(root: Path) -> tuple[int, os.stat_result]:
    """Open every root-path component without following an ancestor symlink.

    The caller owns the returned descriptor.  A relative path is anchored at
    the physical current directory; `..` is refused rather than normalised
    through a potentially linked component.
    """
    supplied = os.fspath(root)
    if not os.path.isabs(supplied):
        supplied = os.path.join(os.getcwd(), supplied)
    parts = supplied.split(os.sep)
    if ".." in parts:
        raise ScanFailure("unsafe_root_path")
    try:
        current = os.open(os.sep, _open_flags(directory=True))
    except OSError:
        raise ScanFailure("missing_or_unreadable_root") from None
    try:
        for part in parts:
            if part in ("", "."):
                continue
            try:
                expected = os.stat(part, dir_fd=current, follow_symlinks=False)
            except OSError:
                raise ScanFailure("missing_or_unreadable_root") from None
            if stat.S_ISLNK(expected.st_mode):
                raise ScanFailure("symlink_root_unsupported")
            if not stat.S_ISDIR(expected.st_mode):
                raise ScanFailure("unsupported_root")
            try:
                child = os.open(part, _open_flags(directory=True), dir_fd=current)
            except OSError:
                raise ScanFailure("missing_or_unreadable_root") from None
            if _signature(os.fstat(child)) != _signature(expected):
                os.close(child)
                raise ScanFailure("changed_during_scan")
            os.close(current)
            current = child
        return current, os.fstat(current)
    except BaseException:
        os.close(current)
        raise


def _display_path(parts: tuple[str, ...]) -> str:
    return "/".join(parts) if parts else "."


class _Scanner:
    def __init__(self, limits: Limits):
        self.limits = limits
        self.files: list[dict[str, object]] = []
        self.path_keys: set[str] = set()
        self.total_bytes = 0
        self.entry_count = 0

    def _register_path(self, parts: tuple[str, ...]) -> str:
        path = _display_path(parts)
        # Refuse names that collapse on a case-insensitive, Unicode-normalizing
        # destination such as the APFS volume commonly used for the copy.
        key = unicodedata.normalize("NFC", unicodedata.normalize("NFC", path).casefold())
        if key in self.path_keys:
            raise ScanFailure("duplicate_path", path)
        self.path_keys.add(key)
        return path

    def _file(self, parent_fd: int, name: str, parts: tuple[str, ...], expected: os.stat_result) -> None:
        path = self._register_path(parts)
        if not stat.S_ISREG(expected.st_mode):
            raise ScanFailure("special_file_unsupported", path)
        if expected.st_size < 0 or expected.st_size > self.limits.max_file_bytes:
            raise ScanFailure("file_size_limit_exceeded", path)
        if len(self.files) >= self.limits.max_files:
            raise ScanFailure("file_count_limit_exceeded", path)
        try:
            fd = os.open(name, _open_flags(), dir_fd=parent_fd)
        except OSError as exc:
            kind = "symlink_unsupported" if exc.errno == errno.ELOOP else "unreadable"
            raise ScanFailure(kind, path) from None
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or _signature(before) != _signature(expected):
                raise ScanFailure("changed_during_scan", path)
            digest = hashlib.sha256()
            size = 0
            while True:
                try:
                    chunk = os.read(fd, CHUNK_SIZE)
                except OSError:
                    raise ScanFailure("unreadable", path) from None
                if not chunk:
                    break
                size += len(chunk)
                if size > self.limits.max_file_bytes:
                    raise ScanFailure("file_size_limit_exceeded", path)
                if self.total_bytes + size > self.limits.max_bytes:
                    raise ScanFailure("total_size_limit_exceeded", path)
                digest.update(chunk)
            after = os.fstat(fd)
            if size != before.st_size or _signature(before) != _signature(after):
                raise ScanFailure("changed_during_scan", path)
        finally:
            os.close(fd)
        self.total_bytes += size
        self.files.append({"path": path, "bytes": size, "sha256": digest.hexdigest()})

    def _directory(self, fd: int, parts: tuple[str, ...], expected: os.stat_result, depth: int) -> None:
        path = _display_path(parts)
        if depth > self.limits.max_depth:
            raise ScanFailure("depth_limit_exceeded", path)
        before = os.fstat(fd)
        if not stat.S_ISDIR(before.st_mode) or _signature(before) != _signature(expected):
            raise ScanFailure("changed_during_scan", path)
        try:
            with os.scandir(fd) as iterator:
                names = sorted(entry.name for entry in iterator)
        except OSError:
            raise ScanFailure("enumeration_failed", path) from None
        for name in names:
            self.entry_count += 1
            child_parts = (*parts, name)
            child_path = _display_path(child_parts)
            if self.entry_count > self.limits.max_files * 2 + 1:
                raise ScanFailure("entry_count_limit_exceeded", child_path)
            try:
                child_info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            except OSError:
                raise ScanFailure("changed_during_scan", child_path) from None
            if stat.S_ISLNK(child_info.st_mode):
                self._register_path(child_parts)
                raise ScanFailure("symlink_unsupported", child_path)
            if stat.S_ISDIR(child_info.st_mode):
                self._register_path(child_parts)
                try:
                    child_fd = os.open(name, _open_flags(directory=True), dir_fd=fd)
                except OSError:
                    raise ScanFailure("changed_during_scan", child_path) from None
                try:
                    opened = os.fstat(child_fd)
                    if _signature(opened) != _signature(child_info):
                        raise ScanFailure("changed_during_scan", child_path)
                    self._directory(child_fd, child_parts, child_info, depth + 1)
                finally:
                    os.close(child_fd)
            else:
                self._file(fd, name, child_parts, child_info)
        after = os.fstat(fd)
        if _signature(before) != _signature(after):
            raise ScanFailure("changed_during_scan", path)

    def scan(self, root: Path) -> dict[str, object]:
        if not _safe_open_flags_available():
            raise ScanFailure("required_safe_open_flags_unavailable")
        root_fd, root_info = open_root_without_symlinks(root)
        try:
            opened_root = os.fstat(root_fd)
            if _signature(opened_root) != _signature(root_info):
                raise ScanFailure("changed_during_scan")
            self._directory(root_fd, (), root_info, 0)
        finally:
            os.close(root_fd)
        if not self.files:
            raise ScanFailure("empty_source")
        self.files.sort(key=lambda item: os.fsencode(str(item["path"])))
        return {
            "format": FORMAT,
            "status": "complete",
            "atomic_snapshot": False,
            "requires_frozen_source": True,
            "file_count": len(self.files),
            "total_bytes": self.total_bytes,
            "files": self.files,
        }


def source_manifest(root: Path, limits: Limits = Limits()) -> dict[str, object]:
    """Return a deterministic manifest or a content-free incomplete report."""
    try:
        return _Scanner(limits).scan(Path(root))
    except ScanFailure as failure:
        issue: dict[str, str] = {"kind": failure.kind}
        if failure.path is not None:
            issue["path"] = failure.path
        return {
            "format": FORMAT,
            "status": "incomplete",
            "atomic_snapshot": False,
            "requires_frozen_source": True,
            "issues": [issue],
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="frozen or quiesced copied source tree")
    parser.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--max-file-bytes", type=int, default=DEFAULT_MAX_FILE_BYTES)
    parser.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH)
    args = parser.parse_args(argv)
    try:
        report = source_manifest(
            args.root,
            Limits(args.max_files, args.max_bytes, args.max_file_bytes, args.max_depth),
        )
    except ValueError:
        report = {
            "format": FORMAT,
            "status": "incomplete",
            "atomic_snapshot": False,
            "requires_frozen_source": True,
            "issues": [{"kind": "invalid_limits"}],
        }
    json.dump(report, sys.stdout, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0 if report["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
