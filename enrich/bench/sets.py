"""Evaluation sets: the manifest, its validation, and the files it names.

A set lives outside the repository, under the bench root
(`ARCHIVE_BENCH_ROOT`), as `sets/<name>.json`. Every file it names is given
by a path relative to that root and the file's sha256, and a run first
checks every one: a missing or changed file stops the run before any variant
starts, so it can never score as a zero.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SET_CONTRACT = "archive-bench-set/1"
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
SCORING_KEYS = ("onset_tolerance_s", "pitch_tolerance_cents", "offset_ratio", "offset_min_tolerance_s")


class BenchError(Exception):
    """Anything that must stop a run loudly rather than score as zero."""


def bench_root(explicit: str | None = None) -> Path:
    raw = explicit or os.environ.get("ARCHIVE_BENCH_ROOT")
    if not raw:
        raise BenchError("no bench root: pass --root or set ARCHIVE_BENCH_ROOT")
    root = Path(raw).expanduser()
    if not root.is_dir():
        raise BenchError(f"the bench root {root} is not a directory")
    return root


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 22), b""):
            hasher.update(block)
    return "sha256:" + hasher.hexdigest()


@dataclass(frozen=True)
class FileRef:
    path: str
    sha256: str
    seconds: float | None = None


@dataclass(frozen=True)
class Item:
    id: str
    label: str
    subset: str
    primary: str
    inputs: dict[str, FileRef]
    reference: FileRef


@dataclass(frozen=True)
class BenchSet:
    name: str
    task: str
    digest: str
    scoring: dict[str, float]
    items: tuple[Item, ...]

    def input_name(self, item: Item, requested: str | None) -> str:
        return requested or item.primary


def _file(where: str, raw: Any, errors: list[str], audio: bool) -> FileRef | None:
    if not isinstance(raw, dict):
        errors.append(f"{where}: must be an object with path and sha256")
        return None
    path, digest, seconds = raw.get("path"), raw.get("sha256"), raw.get("seconds")
    ok = True
    if not isinstance(path, str) or not path or Path(path).is_absolute() or ".." in Path(path).parts:
        errors.append(f"{where}.path: must be a relative path inside the bench root, not {path!r}")
        ok = False
    if not isinstance(digest, str) or not DIGEST.match(digest):
        errors.append(f"{where}.sha256: must be sha256:<64 hex>, not {digest!r}")
        ok = False
    if audio and (not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds <= 0):
        errors.append(f"{where}.seconds: an audio input needs its length in seconds, not {seconds!r}")
        ok = False
    return FileRef(path, digest, float(seconds) if audio else None) if ok else None


def parse_set(name: str, raw: Any, digest: str = "") -> BenchSet:
    """Validate a manifest; every problem is reported at once."""
    errors: list[str] = []
    if not isinstance(raw, dict):
        raise BenchError(f"set {name}: the manifest must be a JSON object")
    if raw.get("contract") != SET_CONTRACT:
        errors.append(f"contract: must be {SET_CONTRACT!r}, not {raw.get('contract')!r}")
    if raw.get("set") != name:
        errors.append(f"set: names {raw.get('set')!r}, but the file is {name!r}")
    if raw.get("task") != "notes":
        errors.append(f"task: only 'notes' is scored, not {raw.get('task')!r}")
    scoring = raw.get("scoring")
    if not isinstance(scoring, dict) or not all(
            isinstance(scoring.get(k), (int, float)) and not isinstance(scoring.get(k), bool) and scoring[k] > 0
            for k in SCORING_KEYS):
        errors.append(f"scoring: needs positive numbers {', '.join(SCORING_KEYS)}")
        scoring = {}
    items_raw = raw.get("items")
    if not isinstance(items_raw, list) or not items_raw:
        errors.append("items: must be a non-empty list")
        items_raw = []
    items, seen = [], set()
    for n, it in enumerate(items_raw):
        where = f"items[{n}]"
        if not isinstance(it, dict):
            errors.append(f"{where}: must be an object")
            continue
        label = it.get("label") if isinstance(it.get("label"), str) else where
        where = f"items[{n}] ({label})"
        for key in ("id", "subset", "primary"):
            if not isinstance(it.get(key), str) or not it.get(key):
                errors.append(f"{where}.{key}: must be a non-empty string")
        inputs_raw = it.get("inputs")
        if not isinstance(inputs_raw, dict) or not inputs_raw:
            errors.append(f"{where}.inputs: must name at least one audio input")
            inputs_raw = {}
        inputs = {k: _file(f"{where}.inputs.{k}", v, errors, audio=True) for k, v in inputs_raw.items()}
        reference = _file(f"{where}.reference", it.get("reference"), errors, audio=False)
        primary = it.get("primary")
        if inputs_raw and primary not in inputs_raw:
            errors.append(f"{where}.primary: {primary!r} is not one of its inputs")
        elif inputs.get(primary) and it.get("id") != inputs[primary].sha256:
            errors.append(f"{where}.id: must be the sha256 of its primary input")
        if it.get("id") in seen:
            errors.append(f"{where}.id: listed twice")
        seen.add(it.get("id"))
        if not errors:
            items.append(Item(it["id"], label, it["subset"], primary, inputs, reference))
    if errors:
        raise BenchError(f"set {name} is not valid:\n  " + "\n  ".join(errors))
    return BenchSet(name, raw["task"], digest, {k: float(scoring[k]) for k in SCORING_KEYS}, tuple(items))


def load_set(root: Path, name: str) -> BenchSet:
    path = root / "sets" / f"{name}.json"
    if not path.is_file():
        raise BenchError(f"no set {name}: {path} does not exist")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BenchError(f"{path} is not JSON: {exc}") from exc
    return parse_set(name, raw, sha256_file(path))


def resolve(root: Path, ref: FileRef) -> Path:
    return root / ref.path


def verify(root: Path, bench_set: BenchSet, input_name: str | None = None) -> list[str]:
    """Check every file a run would read: present, and with its listed digest.
    Raises with every missing or changed file; returns the items' input names."""
    problems, names = [], []
    for item in bench_set.items:
        name = bench_set.input_name(item, input_name)
        names.append(name)
        if name not in item.inputs:
            problems.append(f"{item.label}: has no input {name!r} (it has {', '.join(sorted(item.inputs))})")
            continue
        for what, ref in ((f"input {name}", item.inputs[name]), ("reference", item.reference)):
            path = resolve(root, ref)
            if not path.is_file():
                problems.append(f"{item.label}: the {what} {path} is missing")
            elif sha256_file(path) != ref.sha256:
                problems.append(f"{item.label}: the {what} {path} has changed (its digest is not {ref.sha256})")
    if problems:
        raise BenchError(f"set {bench_set.name} cannot run:\n  " + "\n  ".join(problems))
    return names
