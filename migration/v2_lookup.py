"""Read exact V2 source records from a verified migration bundle.

This is a source-evidence lookup only. It does not interpret a V2 row as an
Archive record or claim. Open a bundle once, then use its manifest locators to
read individual records without holding the PAX stream in memory.
"""
from __future__ import annotations

import hashlib
import os
import stat
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from migration.v2_bundle import BundleError, verify_candidate_file


@dataclass(frozen=True)
class SourceRecord:
    """One exact byte range and the metadata asserted by the verified manifest."""

    data: bytes
    metadata: dict[str, Any]


def _stat_key(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class VerifiedBundleFile:
    """A fully verified, seekable view of a V2 bundle file.

    Verification streams the entire tar file and keeps only its bounded
    manifest in memory. Subsequent lookups seek to addressed members and read
    only the requested source record bytes. Keep this object open for batches
    of lookups so the bundle is verified once.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._closed = False
        try:
            before = self.path.stat(follow_symlinks=False)
        except OSError as exc:
            raise BundleError("candidate file unavailable") from exc
        if not stat.S_ISREG(before.st_mode):
            raise BundleError("candidate must be a regular file")

        verified = verify_candidate_file(self.path)
        try:
            self._fd = os.open(
                self.path,
                os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
            )
            opened = os.fstat(self._fd)
            if _stat_key(before) != _stat_key(opened):
                raise BundleError("candidate changed during lookup setup")
            self._handle = os.fdopen(self._fd, "rb", closefd=True)
            self._fd = -1
            self._tar = tarfile.open(fileobj=self._handle, mode="r:")
        except BundleError:
            if getattr(self, "_handle", None) is not None:
                self._handle.close()
            if getattr(self, "_fd", -1) >= 0:
                os.close(self._fd)
            raise
        except (OSError, tarfile.TarError) as exc:
            if getattr(self, "_handle", None) is not None:
                self._handle.close()
            if getattr(self, "_fd", -1) >= 0:
                os.close(self._fd)
            raise BundleError("candidate file unavailable") from exc

        if _stat_key(opened) != _stat_key(os.fstat(self._handle.fileno())):
            self.close()
            raise BundleError("candidate changed during lookup setup")
        self._stat = _stat_key(opened)
        self.manifest = verified["manifest"]
        self._records: dict[tuple[str, int, int, str], dict[str, Any]] = {}
        for entry in self.manifest["records"]:
            key = (entry["member"], entry["offset"], entry["length"], entry["sha256"])
            self._records[key] = entry
        self._files = {entry["member"]: entry for entry in self.manifest["files"]}

    def close(self) -> None:
        if not getattr(self, "_closed", True):
            self._tar.close()
            self._handle.close()
            self._closed = True

    def __enter__(self) -> "VerifiedBundleFile":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def lookup(self, locator: dict[str, Any]) -> SourceRecord:
        """Return source bytes only when all locator fields match the manifest."""
        if self._closed:
            raise BundleError("verified bundle is closed")
        if not isinstance(locator, dict):
            raise BundleError("record locator is invalid")
        member, offset, length, digest = (
            locator.get("member"), locator.get("offset"), locator.get("length"), locator.get("sha256")
        )
        if (
            not isinstance(member, str)
            or not isinstance(offset, int) or isinstance(offset, bool) or offset < 0
            or not isinstance(length, int) or isinstance(length, bool) or length < 1
            or not isinstance(digest, str)
        ):
            raise BundleError("record locator is invalid")
        key = (member, offset, length, digest)
        metadata = self._records.get(key)
        if metadata is None or locator != metadata:
            raise BundleError("record locator does not match verified manifest")
        try:
            current = os.fstat(self._handle.fileno())
        except OSError as exc:
            raise BundleError("candidate file unavailable") from exc
        if _stat_key(current) != self._stat:
            raise BundleError("candidate changed after verification")
        source_file = self._files.get(member)
        if source_file is None or offset + length > source_file["bytes"]:
            raise BundleError("record locator exceeds source member")
        try:
            archive_member = self._tar.getmember(member)
            if not archive_member.isfile() or archive_member.size != source_file["bytes"]:
                raise BundleError("source member does not match verified manifest")
            stream = self._tar.extractfile(archive_member)
            if stream is None:
                raise BundleError("source member is unavailable")
            with stream:
                stream.seek(offset)
                data = stream.read(length)
        except (OSError, tarfile.TarError) as exc:
            raise BundleError("source member is unavailable") from exc
        if len(data) != length or "sha256:" + hashlib.sha256(data).hexdigest() != digest:
            raise BundleError("source record digest mismatch")
        if _stat_key(os.fstat(self._handle.fileno())) != self._stat:
            raise BundleError("candidate changed during lookup")
        return SourceRecord(data=data, metadata=dict(metadata))


def open_verified_bundle(path: Path) -> VerifiedBundleFile:
    """Verify a PAX candidate from disk and return a streaming lookup handle."""
    return VerifiedBundleFile(Path(path))
