"""The feature bank: its layout, keys, and shapes, and a reader (ADRs 0073, 0074).

The feature bank keeps what extractors measure from content, decoded once and
read by every enrichment that needs it. It is regenerable: losing it loses
nothing, and every reader must survive an entry being absent
(docs/state-model.md). Nothing here reads or writes an archive, and nothing
in the bank is a claim; a claim made from features is proposed through the
adapter like any other enrichment result.

The format is specified in docs/feature-bank.md so that a program in another
language can read and write it. This module needs only the standard library,
so the runner can run anywhere; reading a series as an array imports numpy.

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
import stat
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
# Spool's version grammar ([A-Za-z0-9.]+), short enough that
# `archive-extractor.<id>@<version>` stays within its 128 characters.
VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9.]{0,39}")
OUTPUT = re.compile(r"[a-z][a-z0-9_]{0,62}")
FEATURE = re.compile(r"([a-z][a-z0-9-]{0,62})/([a-z][a-z0-9_]{0,62})")
MEDIA_TYPE = re.compile(r"(\*|[a-z0-9][a-z0-9.+-]*)/(\*|[a-z0-9][a-z0-9.+-]*)")
PROGRAM = re.compile(r"\$\{([a-z][a-z0-9-]{0,62})\}")
SUFFIX = re.compile(r"\.[a-z0-9]{1,10}")
KEY = re.compile(r"sha256:[0-9a-f]{64}")
SPOOL_CAPABILITY = re.compile(r"[A-Za-z0-9._-]+@[A-Za-z0-9.]+")
# The integers every JSON reader holds exactly (an IEEE double's), as RFC 8785 requires.
MAX_INTEGER = 2 ** 53 - 1

# Little-endian element types of a series, and their sizes in bytes.
DTYPES = {"f32": 4, "f64": 8, "i16": 2, "i32": 4, "i64": 8, "u8": 1}
_ARRAY_CODES = {"f32": "f", "f64": "d"}  # the float types, checked for finite values
KINDS = ("series", "scalar", "events", "spans", "file")
MAX_SOFTWARE = 32


class BankError(Exception):
    """A manifest, request, result, or entry is not the shape the contract
    names, or the bank cannot be read. The message says which and why."""


# -- canonical JSON and digests ---------------------------------------------

def canonical(value: Any) -> bytes:
    """Canonical JSON: sorted keys, no spaces, UTF-8. For values that
    `check_parameters` accepts (integers within 2^53, ASCII object keys, no
    lone surrogates) these are exactly RFC 8785's bytes, so a program in any
    language with an RFC 8785 encoder computes the same keys."""
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
    as decimal text, as a derivation run's parameters are (ADR 0064).
    Integers stay within +-(2^53 - 1), object keys are ASCII, and text holds
    no lone surrogates, so their canonical JSON is RFC 8785's in any language
    (docs/feature-bank.md)."""
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, int):
        if abs(value) > MAX_INTEGER:
            raise BankError(f"{where} is {value}, beyond +-(2^53 - 1); write it as decimal text")
        return
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise BankError(f"{where} holds a lone surrogate, which is not text") from None
        return
    if isinstance(value, float):
        raise BankError(f"{where} is the number {value!r}; write a non-integer as decimal text")
    if isinstance(value, list):
        for index, item in enumerate(value):
            check_parameters(item, f"{where}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or not key.isascii():
                raise BankError(f"{where} has the key {key!r}; object keys are ASCII text")
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
    """Where the entry with this key lives: beside the content's derived media.
    For an entry made from a file feature (a stem), `content` is the digest of
    the content that feature was made from, not the stem's own (ADR 0075)."""
    if not KEY.fullmatch(key) or not ID.fullmatch(extractor_id):
        raise BankError(f"not an entry key and extractor: {key!r}, {extractor_id!r}")
    return derived.directory(root, content) / "bank" / extractor_id / key.removeprefix("sha256:")


def failure_path(root: Path, content: str, extractor_id: str, key: str) -> Path:
    """Where the last failure to make an entry is recorded (operational)."""
    directory = entry_directory(root, content, extractor_id, key)
    return directory.with_name(directory.name + ".failed.json")


def lock_path(root: Path, content: str, extractor_id: str, key: str) -> Path:
    """The empty file a runner locks while it makes this entry (operational),
    so two runners on one bank never pay for the same extractor twice."""
    directory = entry_directory(root, content, extractor_id, key)
    return directory.with_name(directory.name + ".lock")


def code_digest(directory: Path) -> str:
    """The digest of an extractor's files: every regular file under its
    directory, by relative path, except Python's byte-code caches. Not part
    of any key; the tests pin it per shipped version, so an edit that is not
    a new version fails a test instead of serving stale entries."""
    files = {}
    for path in sorted(Path(directory).rglob("*")):
        relative = path.relative_to(directory)
        if "__pycache__" in relative.parts or not path.is_file():
            continue
        files[relative.as_posix()] = file_digest(path)
    return digest_of(canonical(files))


def output_file(name: str, declared: Mapping[str, Any]) -> str | None:
    """The file name an output's data has in an entry, or None for a scalar,
    whose value is in the manifest."""
    kind = declared["kind"]
    if kind == "series":
        return f"{name}.{declared['dtype']}"
    if kind in ("events", "spans"):
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

    @property
    def prefers(self) -> list[dict[str, Any]]:
        """The file features this extractor measures by default where the
        content has them, in order, each with the parameters it takes there."""
        return [{"parameters": {}, **preference} for preference in self.manifest.get("prefers", [])]

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
    if kind == "series":
        allowed |= {"dtype", "columns", "grid"}
        _require(declared.get("dtype") in DTYPES, f"{where}: series {name} dtype must be one of {sorted(DTYPES)}")
        columns = declared.get("columns")
        _require(isinstance(columns, int) and not isinstance(columns, bool) and columns >= 1,
                 f"{where}: series {name} needs a positive integer `columns`")
        grid = declared.get("grid")
        _require(grid is None or (isinstance(grid, str) and OUTPUT.fullmatch(grid) is not None),
                 f"{where}: series {name} grid {grid!r}")
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
    known = {"contract", "id", "version", "description", "command", "accepts", "requires", "needs", "prefers",
             "parameters", "outputs"}
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
    prefers = manifest.get("prefers", [])
    _require(isinstance(prefers, list), f"{where}: prefers must list preferred inputs")
    for preference in prefers:
        _require(isinstance(preference, dict) and set(preference) <= {"input", "parameters"}
                 and isinstance(preference.get("input"), str) and FEATURE.fullmatch(preference["input"]) is not None,
                 f"{where}: each preference is {{\"input\": \"<extractor>/<output>\", \"parameters\": {{...}}}}")
        fields = preference.get("parameters", {})
        _require(isinstance(fields, dict), f"{where}: a preference's parameters must be an object")
        check_parameters(fields, f"{where}: parameters preferred for {preference['input']}")
        unknown = set(fields) - set(manifest.get("parameters", {}))
        _require(not unknown, f"{where}: parameters preferred for {preference['input']} that it does not declare: "
                              f"{sorted(unknown)}")
    _require(len({p["input"] for p in prefers}) == len(prefers), f"{where}: a feature is preferred twice")
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
        for extractor in self.extractors.values():
            for preference in extractor.prefers:
                source, output = FEATURE.fullmatch(preference["input"]).groups()  # type: ignore[union-attr]
                if source not in self.extractors:
                    continue  # a preference is never needed: one no extractor here can make is never present
                if self[source].outputs.get(output, {}).get("kind") != "file":
                    raise BankError(f"{extractor.id} prefers {preference['input']}, which is not a file feature; "
                                    f"only a file can be content")
                if source in self.order([extractor.id]):
                    raise BankError(f"{extractor.id} prefers {preference['input']}, which it makes or needs")

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

    def check_overrides(self, overrides: Mapping[str, Any], where: str = "parameters") -> None:
        """Configured parameters must name an extractor here and only the
        fields its manifest declares: a misspelt name would otherwise be
        ignored, and the bank would serve the defaults' entries."""
        for extractor_id, fields in overrides.items():
            if extractor_id not in self.extractors:
                raise BankError(f"{where} sets parameters for {extractor_id!r}, which is not an extractor here; "
                                f"known: {', '.join(sorted(self.extractors))}")
            if not isinstance(fields, dict):
                raise BankError(f"{where}: {extractor_id}'s parameters must be an object")
            unknown = set(fields) - set(self[extractor_id].manifest.get("parameters", {}))
            if unknown:
                raise BankError(f"{where} sets {extractor_id} parameters it does not declare: {sorted(unknown)}")

    def overrides_for(self, feature: str,
                      overrides: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
        """The parameter overrides for measuring a file feature as content:
        each extractor that prefers `feature` takes the parameters its
        preference names, and the operator's configured fields replace those
        in turn. Every measure of the feature, by preference or by `--from`,
        and every reader of one, keys with these."""
        merged = {extractor_id: dict(fields) for extractor_id, fields in (overrides or {}).items()}
        for extractor in self.extractors.values():
            for preference in extractor.prefers:
                if preference["input"] == feature and preference["parameters"]:
                    merged[extractor.id] = {**preference["parameters"], **merged.get(extractor.id, {})}
        return merged

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


def check_spans(value: Any, where: str) -> None:
    """Spans: a list of `{"start", "end", "value"?, "label"?}`, start <= end."""
    _require(isinstance(value, list), f"{where}: spans must be a list")
    for index, span in enumerate(value):
        _require(isinstance(span, dict) and {"start", "end"} <= set(span)
                 and set(span) <= {"start", "end", "value", "label"},
                 f"{where}[{index}]: a span is {{start, end, value?, label?}}")
        _require(_finite_number(span["start"]) and _finite_number(span["end"])
                 and span["start"] <= span["end"], f"{where}[{index}]: start and end")
        _require("value" not in span or _finite_number(span["value"]), f"{where}[{index}]: value")
        _require("label" not in span or isinstance(span["label"], str), f"{where}[{index}]: label")


def _check_series_values(path: Path, dtype: str, where: str) -> None:
    """Refuse a floating-point series holding NaN or an infinity. A sum is
    finite only if every term is, so a block is scanned value by value only
    when its sum is not (which finite values far from zero can also cause)."""
    code = _ARRAY_CODES.get(dtype)
    if code is None:
        return
    with open(path, "rb") as handle:
        while block := handle.read(1 << 22):
            values = array.array(code, block)
            if sys.byteorder != "little":
                values.byteswap()
            if not math.isfinite(sum(values)) and not all(math.isfinite(v) for v in values):
                raise BankError(f"{where}: holds a value that is not finite")


def check_result(directory: Path, extractor: Extractor) -> dict[str, Any]:
    """What an extractor left in its output directory, checked against its
    manifest. Returns the outputs as an entry describes them."""
    where = f"{extractor.id} result"
    # Every file first: nothing is read, hashed, or later chmod-ed through a
    # link. A hard link shares its inode with a file outside the entry
    # (perhaps the archive's content, which enrichment never changes).
    present = {path.name for path in directory.iterdir()}
    for name in present:
        status = os.lstat(directory / name)
        _require(stat.S_ISREG(status.st_mode), f"{where}: {name} is not a plain file")
        _require(status.st_nlink == 1, f"{where}: {name} is a hard link ({status.st_nlink} names); write a copy")
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
        if kind == "series":
            _require(set(out) == {"shape"}, f"{where}: series {name} reports exactly its shape")
            shape = out["shape"]
            _require(isinstance(shape, list) and len(shape) == 2 and all(isinstance(n, int) and n >= 0
                                                                         for n in shape)
                     and shape[1] == declared["columns"],
                     f"{where}: series {name} shape {shape!r} is not [rows, {declared['columns']}]")
            size = shape[0] * shape[1] * DTYPES[declared["dtype"]]
            actual = (directory / file_name).stat().st_size  # type: ignore[operator]
            _require(actual == size, f"{where}: series {name} is {actual} bytes; its shape needs {size}")
            if declared.get("grid") is not None:
                _require(declared["grid"] in grids, f"{where}: series {name} names grid {declared['grid']}, "
                                                    "which the result does not describe")
            _check_series_values(directory / file_name, declared["dtype"], f"{where}: series {name}")  # type: ignore
            entry["shape"] = shape
        elif kind == "scalar":
            _require(set(out) == {"value"}, f"{where}: scalar {name} reports exactly its value")
            value = out["value"]
            _require(_finite_number(value) or isinstance(value, (str, bool)),
                     f"{where}: scalar {name} must be a finite number, text, or a boolean")
            entry["value"] = value
        else:
            _require(out == {}, f"{where}: {kind} {name} reports nothing but its file")
            if kind in ("events", "spans"):
                try:
                    value = json.loads((directory / file_name).read_text(encoding="utf-8"))  # type: ignore
                except (OSError, ValueError) as exc:
                    raise BankError(f"{where}: {kind} {name} is unreadable: {exc}") from exc
                (check_events if kind == "events" else check_spans)(value, f"{where}: {name}")
            else:
                _require((directory / file_name).stat().st_size > 0, f"{where}: file {name} is empty")  # type: ignore
        if file_name is not None:
            entry["bytes"] = (directory / file_name).stat().st_size
            entry["sha256"] = file_digest(directory / file_name)
        described[name] = entry
    extra = present - expected_files
    _require(not extra, f"{where}: undeclared files {sorted(extra)}")
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

    def series(self, name: str) -> Any:
        """A series as a read-only numpy array of shape [rows, columns]."""
        import numpy
        output = self.output(name)
        if output["kind"] != "series":
            raise BankError(f"{name} is a {output['kind']}, not a series")
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
        """Events or spans."""
        output = self.output(name)
        if output["kind"] not in ("events", "spans"):
            raise BankError(f"{name} is a {output['kind']}, not events or spans")
        return json.loads((self.path / output["file"]).read_text(encoding="utf-8"))

    def file(self, name: str) -> Path:
        output = self.output(name)
        if output["kind"] != "file":
            raise BankError(f"{name} is a {output['kind']}, not a file")
        return self.path / output["file"]

    def provenance(self, name: str) -> dict[str, Any]:
        """What a derivation run that read this output must record (ADR 0073):
        the feature, the method that made it, the entry's key, and the
        software the entry recorded. The software is copied because the entry
        is regenerable and may be deleted; the run is evidence and is not."""
        self.output(name)
        extractor = self.manifest["extractor"]
        return {"feature": f"{extractor['id']}/{name}", "extractor": dict(extractor),
                "entry": self.manifest["key"], "software": [dict(item) for item in self.manifest["software"]]}


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

    def entry(self, content: str, extractor_id: str, filed_under: str | None = None,
              feature: str | None = None) -> Entry | None:
        """The current entry of one extractor for one content digest, or None.
        `filed_under` is the digest whose directory holds it, when the
        content is a file feature of other content (a stem of a recording),
        and `feature` names that feature (see `input_of` and `entry_from`):
        an extractor that prefers a feature takes other parameters there, so
        one that does is refused without it rather than missed."""
        if filed_under is not None and feature is None and any(
                p["parameters"] for p in self.catalog[extractor_id].prefers):
            raise BankError(f"{extractor_id} takes other parameters on a feature it prefers; "
                            f"say which feature {content} is")
        parameters = self.parameters if feature is None else self.catalog.overrides_for(feature, self.parameters)
        key = self.catalog.keys(content, [extractor_id], parameters)[extractor_id]
        found = read_entry(entry_directory(self.root, filed_under or content, extractor_id, key))
        if found is not None and found.manifest["key"] != key:
            raise BankError(f"{found.path} holds key {found.manifest['key']}, not {key}")
        return found

    def feature(self, content: str, feature: str, filed_under: str | None = None) -> tuple[Entry, str] | None:
        """The entry holding `<extractor>/<output>`, and the output's name."""
        match = FEATURE.fullmatch(feature)
        if match is None:
            raise BankError(f"not a feature name: {feature!r}")
        extractor_id, output = match.groups()
        found = self.entry(content, extractor_id, filed_under)
        if found is None:
            return None
        found.output(output)
        return found, output

    def input_of(self, content: str, feature: str) -> tuple[Path, str, str] | None:
        """A file feature of this content as content in its own right: its
        path, its digest, and its media type, or None when it is not in the
        bank. Every extractor measures it as it measures any content; the
        entries are filed under `content`'s directory with `filed_under`, so
        deleting a content's directory deletes everything made from it
        (ADR 0075)."""
        found = self.feature(content, feature)
        if found is None:
            return None
        entry, output = found
        described = entry.output(output)
        if described["kind"] != "file":
            raise BankError(f"{feature} is a {described['kind']}, not a file; only a file can be content")
        return entry.path / described["file"], described["sha256"], described["media_type"]

    def entry_from(self, content: str, feature: str, extractor_id: str) -> Entry | None:
        """The entry an extractor made of a file feature of this content (the
        melody of a recording's vocal stem), or None when the feature or the
        measure of it is not in the bank."""
        found = self.input_of(content, feature)
        if found is None:
            return None
        return self.entry(found[1], extractor_id, filed_under=content, feature=feature)

    def default_input(self, content: str, extractor_id: str) -> str | None:
        """The file feature an extractor measures by default for this
        content: the first it prefers that the bank holds for it, or None,
        for the content itself. The runner and every reader decide by this,
        so they agree on which entry is the content's."""
        for preference in self.catalog[extractor_id].prefers:
            source = preference["input"].split("/", 1)[0]
            if source in self.catalog.extractors and self.input_of(content, preference["input"]) is not None:
                return preference["input"]
        return None

    def default_entry(self, content: str, extractor_id: str) -> tuple[Entry | None, str | None]:
        """The entry an extractor makes of this content by default, and the
        feature it measured (None for the content itself): the measure of
        the first preferred feature the bank holds, else of the content. The
        entry is None when that measure is not in the bank; it is never the
        content's own entry in place of a preferred feature's."""
        feature = self.default_input(content, extractor_id)
        if feature is None:
            return self.entry(content, extractor_id), None
        return self.entry_from(content, feature, extractor_id), feature


def environment_root() -> Path | None:
    """The bank root named by ARCHIVE_FEATURE_BANK, if any."""
    value = os.environ.get("ARCHIVE_FEATURE_BANK")
    return Path(value) if value else None
