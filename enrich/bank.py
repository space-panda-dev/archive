"""The feature bank: its layout, keys, and shapes, and a reader (ADRs 0073, 0074).

The feature bank keeps what extractors measure from content, decoded once and
read by every enrichment that needs it. It is regenerable: losing it loses
nothing, and every reader must survive an entry being absent
(docs/state-model.md). Nothing here reads or writes an archive, and nothing
in the bank is a claim; a claim made from features is proposed through the
adapter like any other enrichment result.

The format is specified in docs/feature-bank.md so that a program in another
language can read and write it. This module needs only the standard library,
so the runner can run anywhere; reading a track as an array imports numpy.

An entry lives at `sha256/<ab>/<cd>/<hex>/bank/<extractor>/<key hex>/` under
a bank root, beside ADR 0069's derived media of the same content. It holds
`entry.json` and its outputs' files, and appears whole, by one rename, or not
at all.
"""

from __future__ import annotations

import array
import hashlib
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import derived

EXTRACTOR_CONTRACT = "archive-extractor/1"
REQUEST_CONTRACT = "archive-extractor-request/1"
RESULT_CONTRACT = "archive-extractor-result/1"
ENTRY_CONTRACT = "archive-feature-bank-entry/1"
KEY_CONTRACT = "archive-feature-bank-key/1"

EXTRACTORS = Path(__file__).resolve().parent / "extractors"
MANIFEST = "extractor.json"
ENTRY = "entry.json"
RESULT = "result.json"

ID = re.compile(r"[a-z][a-z0-9-]{0,62}")
VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
OUTPUT = re.compile(r"[a-z][a-z0-9_]{0,62}")
FEATURE = re.compile(r"([a-z][a-z0-9-]{0,62})/([a-z][a-z0-9_]{0,62})")
MEDIA_TYPE = re.compile(r"(\*|[a-z0-9][a-z0-9.+-]*)/(\*|[a-z0-9][a-z0-9.+-]*)")
PROGRAM = re.compile(r"\$\{([a-z][a-z0-9-]{0,62})\}")
SUFFIX = re.compile(r"\.[a-z0-9]{1,10}")
KEY = re.compile(r"sha256:[0-9a-f]{64}")

# Little-endian element types of a track, and their sizes in bytes.
DTYPES = {"f32": 4, "f64": 8, "i16": 2, "i32": 4, "i64": 8, "u8": 1}
_ARRAY_CODES = {"f32": "f", "f64": "d"}  # the float types, checked for finite values
KINDS = ("track", "scalar", "events", "segments", "file")
MAX_SOFTWARE = 32


class BankError(Exception):
    """A manifest, request, result, or entry is not the shape the contract
    names, or the bank cannot be read. The message says which and why."""


# -- canonical JSON and digests ---------------------------------------------

def canonical(value: Any) -> bytes:
    """Canonical JSON: sorted keys, no spaces, UTF-8. Parameters hold no
    non-integer numbers (see `check_parameters`), so every language encodes
    them to the same bytes."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def digest_of(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def file_digest(path: Path) -> str:
    """The SHA-256 of a file's bytes, as Archive writes a content digest."""
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 22), b""):
            hasher.update(block)
    return "sha256:" + hasher.hexdigest()


def check_parameters(value: Any, where: str = "parameters") -> None:
    """Parameters are JSON whose numbers are integers: a fraction is written
    as decimal text, as a derivation run's parameters are (ADR 0064)."""
    if isinstance(value, bool) or value is None or isinstance(value, (int, str)):
        return
    if isinstance(value, float):
        raise BankError(f"{where} is the number {value!r}; write a non-integer as decimal text")
    if isinstance(value, list):
        for index, item in enumerate(value):
            check_parameters(item, f"{where}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            check_parameters(item, f"{where}.{key}")
        return
    raise BankError(f"{where} is not JSON")


def key_of(extractor_id: str, version: str, parameters: Mapping[str, Any], content: str,
           needs: Mapping[str, str]) -> str:
    """An entry's key: the digest of what decides its outputs. `needs` maps
    each needed feature to the key of the entry holding it, so a change
    anywhere upstream is a new key here."""
    if not derived.is_digest(content):
        raise BankError(f"not a canonical content digest: {content!r}")
    return digest_of(canonical({"contract": KEY_CONTRACT, "extractor": extractor_id, "version": version,
                                "parameters": digest_of(canonical(parameters)), "content": content,
                                "needs": dict(needs)}))


# -- layout ------------------------------------------------------------------

def entry_directory(root: Path, content: str, extractor_id: str, key: str) -> Path:
    """Where the entry with this key lives: beside the content's derived media."""
    if not KEY.fullmatch(key) or not ID.fullmatch(extractor_id):
        raise BankError(f"not an entry key and extractor: {key!r}, {extractor_id!r}")
    return derived.directory(root, content) / "bank" / extractor_id / key.removeprefix("sha256:")


def failure_path(root: Path, content: str, extractor_id: str, key: str) -> Path:
    """Where the last failure to make an entry is recorded (operational)."""
    directory = entry_directory(root, content, extractor_id, key)
    return directory.with_name(directory.name + ".failed.json")


def output_file(name: str, declared: Mapping[str, Any]) -> str | None:
    """The file name an output's data has in an entry, or None for a scalar,
    whose value is in the manifest."""
    kind = declared["kind"]
    if kind == "track":
        return f"{name}.{declared['dtype']}"
    if kind in ("events", "segments"):
        return f"{name}.json"
    if kind == "file":
        return name + declared["suffix"]
    return None


# -- the extractor manifest --------------------------------------------------

@dataclass(frozen=True)
class Extractor:
    """One extractor, as its manifest declares it."""
    directory: Path
    manifest: dict[str, Any]

    @property
    def id(self) -> str:
        return self.manifest["id"]

    @property
    def version(self) -> str:
        return self.manifest["version"]

    @property
    def needs(self) -> list[str]:
        return list(self.manifest.get("needs", []))

    @property
    def outputs(self) -> dict[str, dict[str, Any]]:
        return self.manifest["outputs"]

    def accepts(self, media_type: str) -> bool:
        kind, _, sub = media_type.partition("/")
        for pattern in self.manifest["accepts"]:
            a, _, b = pattern.partition("/")
            if a in ("*", kind) and b in ("*", sub):
                return True
        return False


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise BankError(message)


def check_output_declaration(where: str, name: str, declared: Any) -> None:
    _require(isinstance(name, str) and OUTPUT.fullmatch(name) is not None, f"{where}: output name {name!r}")
    _require(isinstance(declared, dict), f"{where}: output {name} is not an object")
    kind = declared.get("kind")
    _require(kind in KINDS, f"{where}: output {name} has kind {kind!r}, not one of {', '.join(KINDS)}")
    allowed = {"kind", "unit", "description"}
    if kind == "track":
        allowed |= {"dtype", "columns", "grid"}
        _require(declared.get("dtype") in DTYPES, f"{where}: track {name} dtype must be one of {sorted(DTYPES)}")
        columns = declared.get("columns")
        _require(isinstance(columns, int) and not isinstance(columns, bool) and columns >= 1,
                 f"{where}: track {name} needs a positive integer `columns`")
        grid = declared.get("grid")
        _require(grid is None or (isinstance(grid, str) and OUTPUT.fullmatch(grid) is not None),
                 f"{where}: track {name} grid {grid!r}")
    if kind == "file":
        allowed |= {"media_type", "suffix"}
        _require(isinstance(declared.get("media_type"), str) and MEDIA_TYPE.fullmatch(declared["media_type"])
                 is not None and "*" not in declared["media_type"], f"{where}: file {name} needs a media_type")
        _require(isinstance(declared.get("suffix"), str) and SUFFIX.fullmatch(declared["suffix"]) is not None,
                 f"{where}: file {name} needs a suffix such as .flac")
    for field in ("unit", "description"):
        _require(field not in declared or isinstance(declared[field], str), f"{where}: output {name} {field}")
    extra = set(declared) - allowed
    _require(not extra, f"{where}: output {name} has unknown fields {sorted(extra)}")


def check_manifest(manifest: Any, where: str) -> dict[str, Any]:
    """An extractor manifest, refused unless it is exactly the contract's shape."""
    _require(isinstance(manifest, dict), f"{where}: not a JSON object")
    _require(manifest.get("contract") == EXTRACTOR_CONTRACT, f"{where}: contract must be {EXTRACTOR_CONTRACT}")
    known = {"contract", "id", "version", "description", "command", "accepts", "requires", "needs", "parameters",
             "outputs"}
    extra = set(manifest) - known
    _require(not extra, f"{where}: unknown fields {sorted(extra)}")
    _require(isinstance(manifest.get("id"), str) and ID.fullmatch(manifest["id"]) is not None,
             f"{where}: id must match {ID.pattern}")
    _require(isinstance(manifest.get("version"), str) and VERSION.fullmatch(manifest["version"]) is not None,
             f"{where}: version must match {VERSION.pattern}")
    command = manifest.get("command")
    _require(isinstance(command, list) and command and all(isinstance(word, str) and word for word in command),
             f"{where}: command must be a non-empty list of words")
    accepts = manifest.get("accepts")
    _require(isinstance(accepts, list) and accepts and all(isinstance(p, str) and MEDIA_TYPE.fullmatch(p)
                                                           for p in accepts),
             f"{where}: accepts must list media types such as audio/* ")
    requires = manifest.get("requires", [])
    _require(isinstance(requires, list) and all(isinstance(r, str) and r for r in requires),
             f"{where}: requires must list capability names")
    needs = manifest.get("needs", [])
    _require(isinstance(needs, list) and all(isinstance(n, str) and FEATURE.fullmatch(n) for n in needs)
             and len(set(needs)) == len(needs), f"{where}: needs must list distinct features <extractor>/<output>")
    _require(isinstance(manifest.get("parameters", {}), dict), f"{where}: parameters must be an object")
    check_parameters(manifest.get("parameters", {}), f"{where}: parameters")
    outputs = manifest.get("outputs")
    _require(isinstance(outputs, dict) and outputs, f"{where}: outputs must declare at least one output")
    for name, declared in outputs.items():
        check_output_declaration(where, name, declared)
    files = [output_file(name, declared) for name, declared in outputs.items()]
    files = [f for f in files if f is not None]
    _require(len(set(files)) == len(files) and RESULT not in files and ENTRY not in files,
             f"{where}: two outputs would share a file name")
    return manifest


class Catalog:
    """The extractors found in some directories, each in its own
    subdirectory with an `extractor.json`. Every need must name an output
    some extractor here declares, and needs may not form a cycle."""

    def __init__(self, extractors: Iterable[Extractor]):
        self.extractors: dict[str, Extractor] = {}
        for extractor in extractors:
            if extractor.id in self.extractors:
                raise BankError(f"two extractors are called {extractor.id}: "
                                f"{self.extractors[extractor.id].directory} and {extractor.directory}")
            self.extractors[extractor.id] = extractor
        for extractor in self.extractors.values():
            for need in extractor.needs:
                source, output = FEATURE.fullmatch(need).groups()  # type: ignore[union-attr]
                if source not in self.extractors or output not in self.extractors[source].outputs:
                    raise BankError(f"{extractor.id} needs {need}, which no extractor here declares")
        for extractor_id in self.extractors:
            self.order([extractor_id])

    @classmethod
    def load(cls, directories: Iterable[Path] = (EXTRACTORS,)) -> "Catalog":
        found = []
        for directory in directories:
            if not Path(directory).is_dir():
                raise BankError(f"no extractor directory at {directory}")
            for manifest_path in sorted(Path(directory).glob(f"*/{MANIFEST}")):
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    raise BankError(f"{manifest_path}: unreadable: {exc}") from exc
                found.append(Extractor(manifest_path.parent.resolve(), check_manifest(manifest, str(manifest_path))))
        return cls(found)

    def __getitem__(self, extractor_id: str) -> Extractor:
        if extractor_id not in self.extractors:
            raise BankError(f"no extractor called {extractor_id}; known: {', '.join(sorted(self.extractors))}")
        return self.extractors[extractor_id]

    def upstream(self, extractor_id: str) -> list[str]:
        """The extractors whose outputs this one needs, in need order, once each."""
        seen: list[str] = []
        for need in self[extractor_id].needs:
            source = need.split("/", 1)[0]
            if source not in seen:
                seen.append(source)
        return seen

    def order(self, targets: Iterable[str]) -> list[str]:
        """The targets and everything they need, each after what it needs."""
        ordered: list[str] = []
        visiting: list[str] = []

        def visit(extractor_id: str) -> None:
            if extractor_id in ordered:
                return
            if extractor_id in visiting:
                cycle = visiting[visiting.index(extractor_id):] + [extractor_id]
                raise BankError(f"needs form a cycle: {' -> '.join(cycle)}")
            visiting.append(extractor_id)
            for source in self.upstream(extractor_id):
                visit(source)
            visiting.pop()
            ordered.append(extractor_id)

        for target in targets:
            visit(target)
        return ordered

    def keys(self, content: str, targets: Iterable[str],
             parameters: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, str]:
        """Each extractor's entry key for this content, upstream first.
        `parameters` replaces an extractor's declared defaults, field by field."""
        keys: dict[str, str] = {}
        for extractor_id in self.order(targets):
            extractor = self[extractor_id]
            needs = {need: keys[need.split("/", 1)[0]] for need in extractor.needs}
            keys[extractor_id] = key_of(extractor.id, extractor.version,
                                        self.parameters(extractor_id, parameters), content, needs)
        return keys

    def parameters(self, extractor_id: str,
                   overrides: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any]:
        merged = {**self[extractor_id].manifest.get("parameters", {}), **((overrides or {}).get(extractor_id, {}))}
        check_parameters(merged, f"{extractor_id} parameters")
        return merged


# -- results and entries -----------------------------------------------------

def _software(value: Any, where: str) -> list[dict[str, str]]:
    _require(isinstance(value, list) and len(value) <= MAX_SOFTWARE
             and all(isinstance(item, dict) and set(item) == {"name", "version"}
                     and all(isinstance(item[f], str) and 0 < len(item[f]) <= 200 for f in item) for item in value),
             f"{where}: software must list at most {MAX_SOFTWARE} {{name, version}} pairs")
    return value


def _grids(value: Any, where: str) -> dict[str, dict[str, int]]:
    _require(isinstance(value, dict), f"{where}: grids must be an object")
    for name, grid in value.items():
        _require(OUTPUT.fullmatch(name) is not None and isinstance(grid, dict), f"{where}: grid {name!r}")
        _require(set(grid) <= {"sample_rate", "hop", "frame", "first_centre"}
                 and {"sample_rate", "hop", "first_centre"} <= set(grid), f"{where}: grid {name} fields")
        for field, number in grid.items():
            _require(isinstance(number, int) and not isinstance(number, bool), f"{where}: grid {name} {field}")
        _require(grid["sample_rate"] > 0 and grid["hop"] > 0 and grid.get("frame", 1) > 0,
                 f"{where}: grid {name} must have a positive rate, hop, and frame")
    return value


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def check_events(value: Any, where: str) -> None:
    """Events: a list of `{"time", "value"?, "label"?}` in non-decreasing time."""
    _require(isinstance(value, list), f"{where}: events must be a list")
    previous = -math.inf
    for index, event in enumerate(value):
        _require(isinstance(event, dict) and "time" in event and set(event) <= {"time", "value", "label"},
                 f"{where}[{index}]: an event is {{time, value?, label?}}")
        _require(_finite_number(event["time"]) and event["time"] >= previous,
                 f"{where}[{index}]: times must be finite and non-decreasing")
        _require("value" not in event or _finite_number(event["value"]), f"{where}[{index}]: value")
        _require("label" not in event or isinstance(event["label"], str), f"{where}[{index}]: label")
        previous = event["time"]


def check_segments(value: Any, where: str) -> None:
    """Segments: a list of `{"start", "end", "value"?, "label"?}`, start <= end."""
    _require(isinstance(value, list), f"{where}: segments must be a list")
    for index, segment in enumerate(value):
        _require(isinstance(segment, dict) and {"start", "end"} <= set(segment)
                 and set(segment) <= {"start", "end", "value", "label"},
                 f"{where}[{index}]: a segment is {{start, end, value?, label?}}")
        _require(_finite_number(segment["start"]) and _finite_number(segment["end"])
                 and segment["start"] <= segment["end"], f"{where}[{index}]: start and end")
        _require("value" not in segment or _finite_number(segment["value"]), f"{where}[{index}]: value")
        _require("label" not in segment or isinstance(segment["label"], str), f"{where}[{index}]: label")


def _check_track_values(path: Path, dtype: str, where: str) -> None:
    code = _ARRAY_CODES.get(dtype)
    if code is None:
        return
    values = array.array(code)
    with open(path, "rb") as handle:
        while block := handle.read(1 << 22):
            values = array.array(code, block)
            if sys.byteorder != "little":
                values.byteswap()
            if not all(math.isfinite(v) for v in values):
                raise BankError(f"{where}: holds a value that is not finite")


def check_result(directory: Path, extractor: Extractor) -> dict[str, Any]:
    """What an extractor left in its output directory, checked against its
    manifest. Returns the outputs as an entry describes them."""
    where = f"{extractor.id} result"
    try:
        result = json.loads((directory / RESULT).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise BankError(f"{where}: the extractor wrote no {RESULT}") from None
    except (OSError, ValueError) as exc:
        raise BankError(f"{where}: {RESULT} is unreadable: {exc}") from exc
    _require(isinstance(result, dict) and result.get("contract") == RESULT_CONTRACT,
             f"{where}: contract must be {RESULT_CONTRACT}")
    _require(set(result) <= {"contract", "software", "grids", "outputs"}, f"{where}: unknown fields")
    software = _software(result.get("software", []), where)
    grids = _grids(result.get("grids", {}), where)
    reported = result.get("outputs")
    _require(isinstance(reported, dict) and set(reported) == set(extractor.outputs),
             f"{where}: outputs must be exactly {sorted(extractor.outputs)}")
    expected_files = {RESULT}
    described: dict[str, Any] = {}
    for name, declared in extractor.outputs.items():
        out = reported[name]
        _require(isinstance(out, dict), f"{where}: output {name} is not an object")
        kind = declared["kind"]
        entry: dict[str, Any] = {key: declared[key] for key in declared if key != "description"}
        file_name = output_file(name, declared)
        if file_name is not None:
            expected_files.add(file_name)
            path = directory / file_name
            _require(path.is_file() and not path.is_symlink(), f"{where}: output {name} has no file {file_name}")
            entry["file"] = file_name
        if kind == "track":
            _require(set(out) == {"shape"}, f"{where}: track {name} reports exactly its shape")
            shape = out["shape"]
            _require(isinstance(shape, list) and len(shape) == 2 and all(isinstance(n, int) and n >= 0
                                                                         for n in shape)
                     and shape[1] == declared["columns"],
                     f"{where}: track {name} shape {shape!r} is not [rows, {declared['columns']}]")
            size = shape[0] * shape[1] * DTYPES[declared["dtype"]]
            actual = (directory / file_name).stat().st_size  # type: ignore[operator]
            _require(actual == size, f"{where}: track {name} is {actual} bytes; its shape needs {size}")
            if declared.get("grid") is not None:
                _require(declared["grid"] in grids, f"{where}: track {name} names grid {declared['grid']}, "
                                                    "which the result does not describe")
            _check_track_values(directory / file_name, declared["dtype"], f"{where}: track {name}")  # type: ignore
            entry["shape"] = shape
        elif kind == "scalar":
            _require(set(out) == {"value"}, f"{where}: scalar {name} reports exactly its value")
            value = out["value"]
            _require(_finite_number(value) or isinstance(value, (str, bool)),
                     f"{where}: scalar {name} must be a finite number, text, or a boolean")
            entry["value"] = value
        else:
            _require(out == {}, f"{where}: {kind} {name} reports nothing but its file")
            if kind in ("events", "segments"):
                try:
                    value = json.loads((directory / file_name).read_text(encoding="utf-8"))  # type: ignore
                except (OSError, ValueError) as exc:
                    raise BankError(f"{where}: {kind} {name} is unreadable: {exc}") from exc
                (check_events if kind == "events" else check_segments)(value, f"{where}: {name}")
            else:
                _require((directory / file_name).stat().st_size > 0, f"{where}: file {name} is empty")  # type: ignore
        if file_name is not None:
            entry["bytes"] = (directory / file_name).stat().st_size
            entry["sha256"] = file_digest(directory / file_name)
        described[name] = entry
    present = {path.name for path in directory.iterdir()}
    extra = present - expected_files
    _require(not extra, f"{where}: undeclared files {sorted(extra)}")
    for name in present:
        _require((directory / name).is_file() and not (directory / name).is_symlink(),
                 f"{where}: {name} is not a plain file")
    return {"software": software, "grids": grids, "outputs": described}


def check_entry(manifest: Any, where: str) -> dict[str, Any]:
    """An entry manifest as the runner writes it; a reader refuses another shape."""
    _require(isinstance(manifest, dict) and manifest.get("contract") == ENTRY_CONTRACT,
             f"{where}: contract must be {ENTRY_CONTRACT}")
    fields = {"contract", "key", "extractor", "parameters", "content", "media_type", "needs", "software", "grids",
              "outputs", "started_at", "ended_at", "runner"}
    _require(set(manifest) == fields, f"{where}: fields must be {sorted(fields)}")
    _require(isinstance(manifest["key"], str) and KEY.fullmatch(manifest["key"]) is not None, f"{where}: key")
    _require(derived.is_digest(manifest["content"]), f"{where}: content")
    extractor = manifest["extractor"]
    _require(isinstance(extractor, dict) and set(extractor) == {"id", "version"}, f"{where}: extractor")
    _require(isinstance(manifest["needs"], dict) and all(FEATURE.fullmatch(k) and isinstance(v, str)
                                                         and KEY.fullmatch(v) for k, v in manifest["needs"].items()),
             f"{where}: needs")
    _require(manifest["key"] == key_of(extractor["id"], extractor["version"], manifest["parameters"],
                                       manifest["content"], manifest["needs"]),
             f"{where}: key does not match its extractor, parameters, content, and needs")
    _require(isinstance(manifest["outputs"], dict), f"{where}: outputs")
    for name, output in manifest["outputs"].items():
        _require(isinstance(output, dict) and output.get("kind") in KINDS, f"{where}: output {name}")
    return manifest


# -- reading -----------------------------------------------------------------

@dataclass(frozen=True)
class Entry:
    """One complete entry of the bank."""
    path: Path
    manifest: dict[str, Any]

    def output(self, name: str) -> dict[str, Any]:
        if name not in self.manifest["outputs"]:
            raise BankError(f"{self.manifest['extractor']['id']} has no output {name}")
        return self.manifest["outputs"][name]

    def track(self, name: str) -> Any:
        """A track as a read-only numpy array of shape [rows, columns]."""
        import numpy
        output = self.output(name)
        if output["kind"] != "track":
            raise BankError(f"{name} is a {output['kind']}, not a track")
        dtype = numpy.dtype("<" + {"f32": "f4", "f64": "f8", "i16": "i2", "i32": "i4", "i64": "i8",
                                   "u8": "u1"}[output["dtype"]])
        values = numpy.fromfile(self.path / output["file"], dtype=dtype)
        if values.size != output["shape"][0] * output["shape"][1]:
            raise BankError(f"{self.path / output['file']} does not hold its shape {output['shape']}")
        values = values.reshape(output["shape"])
        values.flags.writeable = False
        return values

    def grid(self, name: str) -> dict[str, int] | None:
        grid = self.output(name).get("grid")
        return None if grid is None else self.manifest["grids"][grid]

    def scalar(self, name: str) -> Any:
        output = self.output(name)
        if output["kind"] != "scalar":
            raise BankError(f"{name} is a {output['kind']}, not a scalar")
        return output["value"]

    def items(self, name: str) -> list[dict[str, Any]]:
        """Events or segments."""
        output = self.output(name)
        if output["kind"] not in ("events", "segments"):
            raise BankError(f"{name} is a {output['kind']}, not events or segments")
        return json.loads((self.path / output["file"]).read_text(encoding="utf-8"))

    def file(self, name: str) -> Path:
        output = self.output(name)
        if output["kind"] != "file":
            raise BankError(f"{name} is a {output['kind']}, not a file")
        return self.path / output["file"]


def read_entry(directory: Path) -> Entry | None:
    """The entry in this directory, or None when there is none. A directory
    whose manifest is unreadable or the wrong shape is refused, not taken for
    absent: the runner never leaves one, so it is damage."""
    path = directory / ENTRY
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        if directory.exists():
            raise BankError(f"{directory} exists without {ENTRY}; the runner never leaves one") from None
        return None
    try:
        manifest = json.loads(text)
    except ValueError as exc:
        raise BankError(f"{path}: unreadable: {exc}") from exc
    return Entry(directory, check_entry(manifest, str(path)))


class Bank:
    """A bank root, read through a catalog of extractors: an entry is found by
    the key the installed extractors would give it."""

    def __init__(self, root: Path, catalog: Catalog | None = None,
                 parameters: Mapping[str, Mapping[str, Any]] | None = None):
        self.root = Path(root)
        self.catalog = catalog if catalog is not None else Catalog.load()
        self.parameters = parameters

    def entry(self, content: str, extractor_id: str) -> Entry | None:
        """The current entry of one extractor for one content digest, or None."""
        key = self.catalog.keys(content, [extractor_id], self.parameters)[extractor_id]
        found = read_entry(entry_directory(self.root, content, extractor_id, key))
        if found is not None and found.manifest["key"] != key:
            raise BankError(f"{found.path} holds key {found.manifest['key']}, not {key}")
        return found

    def feature(self, content: str, feature: str) -> tuple[Entry, str] | None:
        """The entry holding `<extractor>/<output>`, and the output's name."""
        match = FEATURE.fullmatch(feature)
        if match is None:
            raise BankError(f"not a feature name: {feature!r}")
        extractor_id, output = match.groups()
        found = self.entry(content, extractor_id)
        if found is None:
            return None
        found.output(output)
        return found, output


def environment_root() -> Path | None:
    """The bank root named by ARCHIVE_FEATURE_BANK, if any."""
    value = os.environ.get("ARCHIVE_FEATURE_BANK")
    return Path(value) if value else None
