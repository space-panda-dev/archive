"""Exports rendered on request (ADR 0089).

An export is a view of exact revisions, rendered by an exporter the
operator installed on the relay's host, and filed under its key in the
derived-media root (`world/exports.py`'s layout). It is regenerable: losing
it loses nothing, and it is never admitted unless a person sends it. This
module runs the exporter and finds a made export; who may ask is decided
by the relay from World (`access.py`).
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Any

from practice import regular_size
from refusals import Refused
from world import exports

MAX_LOSS = 200


class Exporters:
    """Installed exporters, each run as `command... <input.json> <output>
    <losses.json>`. The input holds the request, its key, the records it
    renders and what they pin (as World reads them, limited to what the
    caller may read), and each joined Artifact's bytes by path. The exporter
    writes the file and a JSON list of what it dropped (ADR 0077's losses)."""

    def __init__(self, exporters: dict[str, dict[str, Any]], media_root: Path | None, timeout: float):
        self.exporters, self.media_root, self.timeout = exporters, media_root, timeout
        self.lock = threading.Lock()

    def command(self, exporter: dict[str, str]) -> list[str]:
        installed = self.exporters.get(exporter["id"])
        if installed is None or installed["version"] != exporter["version"]:
            raise Refused("exporter_unknown", f"no exporter {exporter['id']} version {exporter['version']} is installed")
        return installed["command"]

    def directory(self, key: str) -> Path:
        name = key.removeprefix("sha256:")
        return self.media_root / "exports" / name[:2] / name[2:4] / name

    def made(self, key: str) -> tuple[Path, dict[str, Any], list[str]] | None:
        """A made export: its file, its request, and its losses; None when
        there is none."""
        if self.media_root is None:
            return None
        directory = self.directory(key)
        try:
            request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
            losses = json.loads((directory / "losses.json").read_text(encoding="utf-8"))
            path = exports.export_path(self.media_root, key, request["format"])
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError, exports.ExportError) as exc:
            raise Refused("internal_error", f"the export {key} cannot be read: {exc}") from None
        if regular_size(path) is None:
            return None
        return path, request, losses

    def answer(self, request: dict[str, Any], key: str, path: Path, losses: list[str]) -> dict[str, Any]:
        return {"key": key, "format": request["format"], "media_type": exports.FORMATS[request["format"]][0],
                "bytes": regular_size(path), "losses": losses}

    def export(self, request: dict[str, Any], records: list[dict[str, Any]],
               joined: list[dict[str, Any]]) -> dict[str, Any]:
        """The export of this request, rendering it when it is missing."""
        if self.media_root is None:
            raise Refused("media_not_derived", "this relay has no derived-media root configured")
        command = self.command(request["exporter"])
        key = exports.export_key(request)
        with self.lock:
            found = self.made(key)
            if found is None:
                self.render(request, key, command, records, joined)
                found = self.made(key)
        path, _, losses = found
        return self.answer(request, key, path, losses)

    def render(self, request: dict[str, Any], key: str, command: list[str], records: list[dict[str, Any]],
               joined: list[dict[str, Any]]) -> None:
        path = exports.export_path(self.media_root, key, request["format"])
        directory = path.parent
        directory.mkdir(parents=True, exist_ok=True)
        given, partial, lost = (directory / ".input.json", directory / f".partial{path.suffix}",
                                directory / ".losses.json")
        try:
            given.write_text(json.dumps({"request": request, "key": key, "records": records, "joined": joined},
                                        ensure_ascii=False, sort_keys=True), encoding="utf-8")
            try:
                completed = subprocess.run([*command, str(given), str(partial), str(lost)], capture_output=True,
                                           text=True, timeout=self.timeout)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise Refused("export_failed", f"the exporter could not run: {exc}") from None
            if completed.returncode != 0:
                raise Refused("export_failed", f"the exporter exited {completed.returncode}: "
                                               f"{completed.stderr.strip()[-500:]}")
            if not regular_size(partial):
                raise Refused("export_failed", "the exporter wrote no file")
            try:
                losses = json.loads(lost.read_text(encoding="utf-8")) if lost.exists() else []
            except (OSError, ValueError):
                losses = None
            if (not isinstance(losses, list) or len(losses) > exports.MAX_LOSSES or len(set(map(str, losses))) != len(losses)
                    or not all(isinstance(item, str) and 1 <= len(item) <= MAX_LOSS for item in losses)):
                raise Refused("export_failed", "the exporter's losses are not a list of distinct short texts")
            (directory / "losses.json").write_text(json.dumps(losses), encoding="utf-8")
            (directory / "request.json").write_bytes(exports.canonical(request))
            os.replace(partial, path)
        finally:
            for leftover in (given, partial, lost):
                if os.path.lexists(leftover):
                    os.unlink(leftover)
