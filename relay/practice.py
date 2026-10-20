"""Practice audio: bank stems by recording digest and feature name, and
practice renders made on request (ADR 0089).

The stems a client mixes live are `file` features of the feature bank
(ADRs 0073, 0075), found only by the key the installed extractors give
(`enrich/bank.py`). A practice render is one file of a mix of those stems,
made by a renderer the operator installed on the relay's own host: a local
program, run one render at a time in the background, never on the seedbox
and never separating stems itself. Both are regenerable: the bank and the
derived-media root may be deleted at any time.

Which features are served is configuration; to whom is the recording's
answer, decided by `practice_refusal` in `world/exports.py`.

When the bank has a scheduler (`bank_scheduler`, ADR 0112), a served feature
that is not made is asked for: the relay files a bank request for the entry
the installed extractors would make of the whole recording, and answers that
it is queued, never an empty result (INV-24). The request is the only thing
the relay writes in the bank, and asking again is the same file.
"""

from __future__ import annotations

import json
import os
import queue
import stat
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Mapping

import bank
import derived
from refusals import Refused
from world import exports

RENDER_MEDIA_TYPE = "audio/flac"


def check_renderers(value: Any, what: str) -> dict[str, dict[str, Any]]:
    """`{<id>: {version, command}}`: installed renderers or exporters, each a
    local program named by an absolute path (ADR 0033), whose version stands
    for its code as an extractor's does."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{what} must map each id to {{version, command}}")
    for name, entry in value.items():
        if not exports.EXPORTER_ID.fullmatch(name):
            raise ValueError(f"{what}: {name!r} is not an id of lowercase letters, digits, and hyphens")
        if not isinstance(entry, dict) or set(entry) != {"version", "command"}:
            raise ValueError(f"{what}: {name} must be exactly {{version, command}}")
        if not isinstance(entry["version"], str) or not exports.EXPORTER_VERSION.fullmatch(entry["version"]):
            raise ValueError(f"{what}: {name} version must be 1 to 40 letters, digits, and dots")
        command = entry["command"]
        if (not isinstance(command, list) or not command or not all(isinstance(a, str) and a for a in command)
                or not os.path.isabs(command[0])):
            raise ValueError(f"{what}: {name} command must be a list whose first item is an absolute path")
    return value


class Features:
    """The configured practice features, checked against the installed
    extractors, and the bank they are read from."""

    def __init__(self, root: Path | None, names: tuple[str, ...], parameters: Mapping[str, Any] | None,
                 scheduler: bool = False):
        self.names = frozenset(names)
        self.scheduler = scheduler
        if len(self.names) != len(names):
            raise ValueError("practice_features repeats a feature")
        if not names:
            self.bank = None
            return
        if root is None:
            raise ValueError("practice_features needs a bank_root to read them from")
        try:
            catalog = bank.Catalog.load()
            catalog.check_overrides(parameters or {}, "bank_parameters")
        except bank.BankError as exc:
            raise ValueError(str(exc)) from None
        self.bank = bank.Bank(root, catalog, parameters)
        for name in names:
            if exports.practice_refusal(name, self.declared(name), self.names, "-", "-", None) is not None:
                raise ValueError(f"practice_features names {name}, which no installed extractor declares "
                                 "as an audio file")

    def declared(self, name: str) -> dict[str, Any] | None:
        """The installed extractor's declaration of a feature, or None."""
        if self.bank is None:
            return None
        try:
            extractor, output = exports.feature_name(name)
        except exports.ExportError as exc:
            raise Refused("request_malformed", str(exc)) from None
        found = self.bank.catalog.extractors.get(extractor)
        return found.outputs.get(output) if found is not None else None

    def served(self, name: str) -> None:
        """ADR 0089's first condition: the name is configured and declared
        as an audio file; otherwise `feature_not_served`."""
        if exports.practice_refusal(name, self.declared(name), self.names, "-", "-", None) is not None:
            raise Refused("feature_not_served", f"this relay does not serve {name}")

    def find(self, digest: str, name: str) -> tuple[Path, dict[str, Any], str] | None:
        """A served feature of this content: its file, its description in
        the entry, and the entry's key; None when it has not been made."""
        try:
            found = self.bank.feature(digest, name)
        except bank.BankError as exc:
            raise Refused("internal_error", f"the feature bank could not be read: {exc}") from None
        if found is None:
            return None
        entry, output = found
        described = entry.output(output)
        if described.get("kind") != "file":
            raise Refused("feature_not_served", f"{name} is not a file")
        return entry.path / described["file"], described, entry.manifest["key"]

    def ask(self, digest: str, extractor: str) -> str | None:
        """With a scheduler, ask for the extractor's entry of the whole
        recording and return its key; None without one."""
        if not self.scheduler:
            return None
        try:
            _, key, _ = bank.ask(self.bank, digest, extractor, "relay")
        except (bank.BankError, OSError) as exc:
            raise Refused("internal_error", f"the feature bank request could not be filed: {exc}") from None
        return key

    def listing(self, digest: str) -> dict[str, Any]:
        """Which configured features exist for this content; with a
        scheduler, those not made are asked for and listed as `queued`, each
        with the key its entry will have."""
        features, missing, queued = [], [], []
        for name in sorted(self.names):
            found = self.find(digest, name)
            if found is None:
                missing.append(name)
                key = self.ask(digest, name.split("/", 1)[0])
                if key is not None:
                    queued.append({"name": name, "key": key})
                continue
            _, described, key = found
            features.append({"name": name, "media_type": described["media_type"], "bytes": described["bytes"],
                             "sha256": described["sha256"], "key": key})
        answer = {"content": digest, "features": features, "missing": missing}
        if self.scheduler:
            answer["queued"] = queued
        return answer

    def stems(self, request: dict[str, Any]) -> dict[str, Path] | None:
        """The stem files a practice request mixes, from the entry it names.
        The entry must be the one the installed extractors give the
        recording; otherwise its stems are `stems_not_made` here. With a
        scheduler, an entry not made yet whose key is the one it will have
        is asked for, and None says it is queued."""
        extractor, key = request["stems"]["extractor"], request["stems"]["key"]
        inputs = {}
        for output in sorted(request["gains"]):
            found = self.find(request["content"], f"{extractor}/{output}")
            if found is None and self.scheduler and self.ask(request["content"], extractor) == key:
                return None
            if found is None or found[2] != key:
                raise Refused("stems_not_made", f"the bank holds no {extractor} entry {key} for this recording")
            inputs[output] = found[0]
        return inputs


def render_path(media_root: Path, digest: str, key: str) -> Path:
    """Where a practice render of this recording with this key lives."""
    return derived.directory(media_root, digest) / "practice" / (key.removeprefix("sha256:") + ".flac")


def regular_size(path: Path) -> int | None:
    """The size of a regular file at `path`, never through a link; None when
    there is none."""
    try:
        found = os.lstat(path)
    except FileNotFoundError:
        return None
    return found.st_size if stat.S_ISREG(found.st_mode) else None


class Renderer:
    """Practice renders, one at a time, in the background (D11.5).

    A renderer is a local program: it is run as `command... <input.json>
    <output.flac>`, where the input holds the checked request, its key, the
    path of each stem it mixes, and the stems it pitch-shifts. It writes one
    16-bit FLAC file at the stems' rate. The relay files it under its key
    with the request beside it, so a render appears whole or not at all."""

    def __init__(self, renderers: dict[str, dict[str, Any]], media_root: Path | None, timeout: float):
        self.renderers, self.media_root, self.timeout = renderers, media_root, timeout
        self.work: queue.Queue = queue.Queue()
        self.pending: set[str] = set()
        self.failed: dict[str, str] = {}
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None

    def command(self, renderer: dict[str, str]) -> list[str]:
        installed = self.renderers.get(renderer["id"])
        if installed is None or installed["version"] != renderer["version"]:
            raise Refused("renderer_unknown", f"no renderer {renderer['id']} version {renderer['version']} is installed")
        return installed["command"]

    def queued(self, checked: dict[str, Any]) -> dict[str, Any]:
        """The answer while the stems a render mixes are queued in the bank:
        the client asks again, as it does while rendering."""
        self.command(checked["renderer"])
        return {**self.answer(checked, exports.practice_key(checked), None), "status": "queued"}

    def answer(self, request: dict[str, Any], key: str, size: int | None) -> dict[str, Any]:
        answer = {"key": key, "status": "ready" if size is not None else "rendering",
                  "media_type": RENDER_MEDIA_TYPE, "speed": request["speed"], "semitones": request["semitones"]}
        if size is not None:
            answer["bytes"] = size
        return answer

    def request(self, checked: dict[str, Any], inputs: dict[str, Path]) -> dict[str, Any]:
        """Answer a practice request: ready when the render exists, else begun
        (or already queued) and rendering. A render that failed is reported
        once, and the next request tries again."""
        command = self.command(checked["renderer"])
        key = exports.practice_key(checked)
        path = render_path(self.media_root, checked["content"], key)
        size = regular_size(path)
        if size is not None:
            return self.answer(checked, key, size)
        with self.lock:
            if key in self.failed:
                raise Refused("render_failed", self.failed.pop(key))
            if key not in self.pending:
                self.pending.add(key)
                self.work.put((checked, key, path, inputs, command))
                if self.thread is None or not self.thread.is_alive():
                    self.thread = threading.Thread(target=self._work, name="relay-renderer", daemon=True)
                    self.thread.start()
        return self.answer(checked, key, None)

    def _work(self) -> None:
        while True:
            item = self.work.get()
            if item is None:
                self.work.task_done()
                return
            checked, key, path, inputs, command = item
            try:
                self.render(checked, key, path, inputs, command)
            except Exception as exc:  # noqa: BLE001 - the failure is reported to the next request
                sys.stderr.write(f"relay: render {key} failed: {type(exc).__name__}: {exc}\n")
                with self.lock:
                    self.failed[key] = f"the render failed: {exc}"
            finally:
                with self.lock:
                    self.pending.discard(key)
                self.work.task_done()

    def render(self, checked: dict[str, Any], key: str, path: Path, inputs: dict[str, Path],
               command: list[str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        name = key.removeprefix("sha256:")
        partial, given = path.with_name(f".{name}.partial.flac"), path.with_name(f".{name}.input.json")
        try:
            given.write_text(json.dumps({"request": checked, "key": key,
                                         "inputs": {output: str(p) for output, p in inputs.items()},
                                         "shifted": exports.shifted_outputs(checked)}, sort_keys=True),
                             encoding="utf-8")
            completed = subprocess.run([*command, str(given), str(partial)], capture_output=True, text=True,
                                       timeout=self.timeout)
            if completed.returncode != 0:
                raise RuntimeError(f"the renderer exited {completed.returncode}: {completed.stderr.strip()[-500:]}")
            if not regular_size(partial):
                raise RuntimeError("the renderer wrote no file")
            path.with_suffix(".json").write_bytes(exports.canonical(checked))
            os.replace(partial, path)
        finally:
            for leftover in (partial, given):
                if os.path.lexists(leftover):
                    os.unlink(leftover)

    def stop(self) -> None:
        if self.thread is not None and self.thread.is_alive():
            self.work.put(None)
