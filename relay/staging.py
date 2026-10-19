"""Documents staged through the relay, and admitted documents' bytes (ADR 0085).

The operator's client sends a document's bytes; the relay checks them
(`check_stage_request` in `world/documents.py`), adds them to its own
staging git-annex repository, and answers the row an admission manifest's
`contents` needs. Staging admits nothing: the client then sends an ordinary
admission, which the relay passes only when every content row is bytes it
staged for that caller (`staged_for`). The staging repository and its index
are operational state, kept in the relay's private staging directory:
losing them loses only what was not yet admitted, and entries are removed
seven days after they were staged.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import adapter
from refusals import Refused

KEEP_SECONDS = 7 * 24 * 60 * 60
REMOTE = "relay-documents"
# The file name a staged document has in the staging repository, and so its
# annex key's extension: one per media type (ADR 0085's list).
SUFFIXES = {
    "text/plain": ".txt",
    "text/markdown": ".md",
    "application/json": ".json",
    "application/pdf": ".pdf",
    "application/rtf": ".rtf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/vnd.recordare.musicxml+xml": ".musicxml",
    "application/vnd.recordare.musicxml": ".mxl",
    "application/x-guitar-pro": ".gp",
    "audio/midi": ".mid",
    "image/png": ".png",
    "image/jpeg": ".jpg",
}
GUITAR_PRO = re.compile(r"\.gp[3-8xp]?\Z")


def suffix(media_type: str, filename: str | None) -> str:
    """The staged file's suffix: the media type's, or a Guitar Pro file's own
    version suffix when its name gives one."""
    if media_type == "application/x-guitar-pro" and filename:
        found = GUITAR_PRO.search(filename.lower())
        if found:
            return found.group(0)
    return SUFFIXES[media_type]


def content_path(archive_root: Path, digest: str) -> Path:
    """Where layout archive/2 keeps an Artifact's bytes (D-1)."""
    name = digest.removeprefix("sha256:")
    return archive_root / "content" / "sha256" / name[0:2] / name[2:4] / name


def archive_bytes(archive_root: Path, digest: str) -> Path:
    """The file holding an admitted Artifact's bytes in the archive, followed
    through git-annex's link but never out of the archive."""
    path = content_path(archive_root, digest)
    try:
        resolved = path.resolve(strict=True)
    except (FileNotFoundError, RuntimeError):
        raise Refused("media_not_derived", "the archive holds no copy of this content here") from None
    if not resolved.is_relative_to(archive_root.resolve()) or not resolved.is_file():
        raise Refused("internal_error", "the archive's content path does not lead to a file in the archive")
    return resolved


class Documents:
    """The relay's document staging repository and its index."""

    def __init__(self, staging: Path, archive_root: Path, create_remote: bool):
        self.repository = staging / "documents"
        self.index = staging / "documents-staged"
        self.archive_root, self.create_remote = archive_root, create_remote

    def _staging(self) -> adapter.Staging:
        git, annex = os.environ.get("ARCHIVE_GIT_EXECUTABLE"), os.environ.get("ARCHIVE_GIT_ANNEX_EXECUTABLE")
        if not git or not annex:
            raise Refused("internal_error", "staging documents needs ARCHIVE_GIT_EXECUTABLE and "
                                            "ARCHIVE_GIT_ANNEX_EXECUTABLE in the relay's environment")
        env = dict(os.environ)
        for name, value in (("GIT_AUTHOR_NAME", "Archive relay"), ("GIT_AUTHOR_EMAIL", "relay@archive.invalid"),
                            ("GIT_COMMITTER_NAME", "Archive relay"), ("GIT_COMMITTER_EMAIL", "relay@archive.invalid")):
            env.setdefault(name, value)
        return adapter.Staging(self.repository, git, annex, env=env, remote=REMOTE)

    def _entry(self, digest: str, agent: str) -> Path:
        caller = hashlib.sha256(agent.encode("utf-8")).hexdigest()[:16]
        return self.index / f"{digest.removeprefix('sha256:')}-{caller}.json"

    def stage(self, media_type: str, data: bytes, filename: str | None, agent: str,
              artifacts: list[str]) -> dict[str, Any]:
        """Add checked document bytes to the staging repository and answer
        their manifest row, media type, and the Artifacts already carrying
        them."""
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        staging = self._staging()
        try:
            key = staging.stage(data, digest.removeprefix("sha256:") + suffix(media_type, filename))
            remote = staging.remote_name(self.archive_root, create=self.create_remote)
        except adapter.AdapterError as exc:
            raise Refused("internal_error", f"the document could not be staged: {exc}") from None
        row = {"digest": digest, "size": len(data), "annex_key": key, "staging_remote": remote}
        self.index.mkdir(mode=0o700, exist_ok=True)
        self._entry(digest, agent).write_text(json.dumps({"row": row, "media_type": media_type, "agent": agent,
                                                          "staged_at": time.time()}, sort_keys=True),
                                              encoding="utf-8")
        self.prune()
        return {**row, "media_type": media_type, "artifacts": artifacts}

    @staticmethod
    def _read(path: Path) -> dict[str, Any] | None:
        """One index entry, or None when it cannot be read or is not an
        entry this relay wrote: an object with a `row` naming a digest and a
        numeric `staged_at`. A malformed entry is never a staged document."""
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(entry, dict) or not isinstance(entry.get("row"), dict) \
                or not isinstance(entry["row"].get("digest"), str) \
                or not isinstance(entry.get("staged_at"), (int, float)) or isinstance(entry.get("staged_at"), bool):
            return None
        return entry

    def staged_for(self, agent: str) -> dict[str, dict[str, Any]]:
        """What this caller staged and may still admit, by digest."""
        found = {}
        for path in sorted(self.index.glob("*.json")) if self.index.is_dir() else []:
            entry = self._read(path)
            if entry is None:
                continue
            if entry.get("agent") == agent and time.time() - entry["staged_at"] <= KEEP_SECONDS:
                found[entry["row"]["digest"]] = entry
        return found

    def prune(self, now: float | None = None) -> list[str]:
        """Remove what was staged more than seven days ago, from the index and
        from the staging repository; returns the digests removed."""
        now = time.time() if now is None else now
        entries: dict[str, list[tuple[Path, dict[str, Any]]]] = {}
        for path in sorted(self.index.glob("*.json")) if self.index.is_dir() else []:
            # A malformed entry is old: its file goes, and it names no digest.
            entry = self._read(path) or {"row": {"digest": None}, "staged_at": 0}
            entries.setdefault(entry["row"]["digest"], []).append((path, entry))
        removed = []
        for digest, items in entries.items():
            if all(now - entry.get("staged_at", 0) <= KEEP_SECONDS for _, entry in items):
                continue
            for path, entry in items:
                if now - entry.get("staged_at", 0) > KEEP_SECONDS:
                    path.unlink(missing_ok=True)
            if digest and not any(now - entry.get("staged_at", 0) <= KEEP_SECONDS for _, entry in items):
                self._forget(digest)
                removed.append(digest)
        return removed

    def _forget(self, digest: str) -> None:
        name = digest.removeprefix("sha256:")
        try:
            staging = self._staging()
        except Refused:
            return
        for path in self.repository.glob(name + "*"):
            try:
                adapter._run([staging.annex, "drop", "--force", "-q", path.name], self.repository, "git-annex drop",
                             staging.env)
                adapter._run([staging.git, "rm", "-q", "-f", path.name], self.repository, "git rm", staging.env)
                adapter._run([staging.git, "commit", "-q", "-m", f"Remove {path.name}, staged over seven days ago"],
                             self.repository, "staging commit", staging.env)
            except adapter.AdapterError as exc:
                sys.stderr.write(f"relay: removing a staged document failed: {exc}\n")
