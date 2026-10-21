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
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping

import derived

EXTRACTOR_CONTRACT = "archive-extractor/1"
REQUEST_CONTRACT = "archive-extractor-request/1"
RESULT_CONTRACT = "archive-extractor-result/1"
ENTRY_CONTRACT = "archive-feature-bank-entry/1"
KEY_CONTRACT = "archive-feature-bank-key/1"
EQUIVALENCE_CONTRACT = "archive-feature-bank-equivalence/1"
# A region entry (ADR 0112): made over `[start, end)` of its content, keyed by
# `/1`'s members and the region; every whole-content key stays a `/1` key.
REGION_ENTRY_CONTRACT = "archive-feature-bank-entry/2"
REGION_KEY_CONTRACT = "archive-feature-bank-key/2"
BANK_REQUEST_CONTRACT = "archive-feature-bank-request/1"
REQUESTS = "requests"

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
DECIMAL = re.compile(r"-?[0-9]{1,15}(\.[0-9]{1,15})?")
# The integers every JSON reader holds exactly (an IEEE double's), as RFC 8785 requires.
MAX_INTEGER = 2 ** 53 - 1

# Little-endian element types of a series, and their sizes in bytes.
DTYPES = {"f32": 4, "f64": 8, "i16": 2, "i32": 4, "i64": 8, "u8": 1}
_ARRAY_CODES = {"f32": "f", "f64": "d"}  # the float types, checked for finite values
KINDS = ("series", "scalar", "events", "spans", "file")
MAX_SOFTWARE = 32
# Where equivalence records live under a bank root (ADR 0090's amendment).
EQUIVALENCE = "equivalence"
# The downstream measures an `interchangeable` declaration may name, and the
# extractor whose entries each compares; enrich/equivalence.py implements
# them, and a test holds the two lists equal.
MEASURES = {"note-f1": "notes-basic-pitch", "f0-cents-p99": "f0-pesto", "voicing-agreement": "f0-pesto"}
PARAMETER_PATH = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}(\.[A-Za-z_][A-Za-z0-9_]{0,62}){0,7}")


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
           needs: Mapping[str, str], region: Mapping[str, int] | None = None) -> str:
    """An entry's key: the digest of what decides its outputs. `needs` maps
    each needed feature to the key of the entry holding it, so a change
    anywhere upstream is a new key here. With a `region`, the key is a
    region entry's (`archive-feature-bank-key/2`); without one it is the
    whole content's `/1` key, exactly as before regions existed."""
    if not derived.is_digest(content):
        raise BankError(f"not a canonical content digest: {content!r}")
    described = {"contract": KEY_CONTRACT, "extractor": extractor_id, "version": version,
                 "parameters": digest_of(canonical(parameters)), "content": content, "needs": dict(needs)}
    if region is not None:
        described = {**described, "contract": REGION_KEY_CONTRACT, "region": check_region(region)}
    return digest_of(canonical(described))


def check_region(region: Any, where: str = "region") -> dict[str, int]:
    """`{start, end, rate}`: `[start, end)` in samples at the content's
    stream rate, `0 <= start < end`, all integers (ADR 0112)."""
    _require(isinstance(region, dict) and set(region) == {"start", "end", "rate"},
             f"{where} must be exactly {{start, end, rate}}")
    _require(all(isinstance(region[k], int) and not isinstance(region[k], bool) for k in region),
             f"{where}: start, end, and rate are integers")
    _require(0 <= region["start"] < region["end"] <= MAX_INTEGER and 0 < region["rate"] <= 10_000_000,
             f"{where}: needs 0 <= start < end and a positive rate")
    return {"start": region["start"], "end": region["end"], "rate": region["rate"]}


def region_contains(region: Mapping[str, int], start: int, end: int, rate: int) -> bool:
    """Whether `[start, end)` at `rate` lies wholly inside the region, compared
    exactly across rates (no rounding)."""
    return region["start"] * rate <= start * region["rate"] and end * region["rate"] <= region["end"] * rate


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
        content has them, in order, each with the parameters it takes there
        and the minimums its measure there must reach to be the default,
        and the probe that may pass it over before it is measured."""
        return [{"parameters": {}, "at_least": {}, "probe": None, **preference}
                for preference in self.manifest.get("prefers", [])]

    @property
    def interchangeable(self) -> dict[str, Any] | None:
        """The parameter whose declared values make entries that may stand
        in for each other under a passing equivalence record, or None."""
        return self.manifest.get("interchangeable")

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


def parameter_at(parameters: Mapping[str, Any], path: str) -> Any:
    """The value at a dotted path of parameters (`apply.device`), or None."""
    value: Any = parameters
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def with_parameter(parameters: Mapping[str, Any], path: str, value: Any) -> dict[str, Any]:
    """A copy of parameters with the dotted path set to value."""
    copy = json.loads(json.dumps(parameters))
    target = copy
    *parents, last = path.split(".")
    for part in parents:
        target = target[part]
    target[last] = value
    return copy


def check_interchangeable(declared: Any, manifest: Mapping[str, Any], where: str) -> None:
    """`interchangeable` (ADR 0090's amendment): one parameter, the values of
    it whose entries may stand in for each other once an equivalence record
    passes, the software a record must match, and the downstream agreement
    that decides it."""
    _require(isinstance(declared, dict) and set(declared) == {"parameter", "values", "software", "agree"},
             f"{where}: interchangeable is {{parameter, values, software, agree}}")
    path = declared["parameter"]
    _require(isinstance(path, str) and PARAMETER_PATH.fullmatch(path) is not None,
             f"{where}: interchangeable.parameter is a dotted path of the parameters, such as apply.device")
    values = declared["values"]
    _require(isinstance(values, list) and len(values) >= 2 and len(set(map(str, values))) == len(values)
             and all(isinstance(v, str) and ID.fullmatch(v) for v in values),
             f"{where}: interchangeable.values lists at least two distinct names, such as cpu and mps")
    _require(parameter_at(manifest.get("parameters", {}), path) in values,
             f"{where}: the parameter {path} must be declared, with one of {values}")
    software = declared["software"]
    _require(isinstance(software, list) and software and len(set(software)) == len(software)
             and all(isinstance(name, str) and 0 < len(name) <= 200 for name in software),
             f"{where}: interchangeable.software lists the software names a record must match")
    agree = declared["agree"]
    _require(isinstance(agree, list) and agree, f"{where}: interchangeable.agree lists at least one measure")
    outputs = manifest.get("outputs") if isinstance(manifest.get("outputs"), dict) else {}
    seen = set()
    for item in agree:
        _require(isinstance(item, dict) and set(item) in ({"measure", "outputs", "at_least"},
                                                          {"measure", "outputs", "at_most"}),
                 f"{where}: each agreement is {{measure, outputs, at_least or at_most}}")
        _require(item["measure"] in MEASURES, f"{where}: no measure {item['measure']!r}; known: {sorted(MEASURES)}")
        _require(item["measure"] not in seen, f"{where}: the measure {item['measure']} is named twice")
        seen.add(item["measure"])
        _require(isinstance(item["outputs"], list) and item["outputs"]
                 and all(outputs.get(name, {}).get("kind") == "file" for name in item["outputs"]),
                 f"{where}: {item['measure']} names file outputs this extractor declares")
        bound = item.get("at_least", item.get("at_most"))
        _require(isinstance(bound, str) and DECIMAL.fullmatch(bound) is not None,
                 f"{where}: {item['measure']}'s threshold is decimal text, such as \"0.98\"")


PROBE_FIELDS = {"series", "column", "at_or_above", "share_at_least"}


def check_probe(probe: Any, where: str) -> None:
    """A preference's probe: `{"series": "<extractor>/<output>", "column":
    <n>, "at_or_above": "<decimal>", "share_at_least": "<decimal>"}`, the
    share of that series' rows whose column is at or above a value, read
    from another extractor's measure of the preferred feature."""
    _require(isinstance(probe, dict) and set(probe) == PROBE_FIELDS
             and isinstance(probe["series"], str) and FEATURE.fullmatch(probe["series"]) is not None
             and isinstance(probe["column"], int) and not isinstance(probe["column"], bool) and probe["column"] >= 0
             and all(isinstance(probe[f], str) and DECIMAL.fullmatch(probe[f]) is not None
                     for f in ("at_or_above", "share_at_least")),
             f"{where} is {{\"series\": \"<extractor>/<output>\", \"column\": <n>, "
             f"\"at_or_above\": \"<decimal>\", \"share_at_least\": \"<decimal>\"}}")


def check_manifest(manifest: Any, where: str) -> dict[str, Any]:
    """An extractor manifest, refused unless it is exactly the contract's shape."""
    _require(isinstance(manifest, dict), f"{where}: not a JSON object")
    _require(manifest.get("contract") == EXTRACTOR_CONTRACT, f"{where}: contract must be {EXTRACTOR_CONTRACT}")
    known = {"contract", "id", "version", "description", "command", "accepts", "requires", "needs", "prefers",
             "parameters", "outputs", "interchangeable"}
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
        _require(isinstance(preference, dict) and set(preference) <= {"input", "parameters", "at_least", "probe"}
                 and isinstance(preference.get("input"), str) and FEATURE.fullmatch(preference["input"]) is not None,
                 f"{where}: each preference is {{\"input\": \"<extractor>/<output>\", \"parameters\": {{...}}, "
                 f"\"at_least\": {{...}}}}")
        minimums = preference.get("at_least", {})
        _require(isinstance(minimums, dict) and all(
            isinstance(value, str) and DECIMAL.fullmatch(value) is not None for value in minimums.values()),
                 f"{where}: a preference's at_least maps outputs to decimal text, such as \"0.2\"")
        _require(all(isinstance(manifest.get("outputs"), dict)
                     and manifest["outputs"].get(name, {}).get("kind") == "scalar" for name in minimums),
                 f"{where}: at_least for {preference['input']} names only scalar outputs it declares")
        if "probe" in preference:
            check_probe(preference["probe"], f"{where}: the probe of {preference['input']}")
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
    if "interchangeable" in manifest:
        check_interchangeable(manifest["interchangeable"], manifest, where)
        _require(not needs, f"{where}: an extractor whose entries stand in for each other needs nothing")
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
        # The content and region a region run keys its entries over (see `within`).
        self.region: tuple[str, dict[str, int]] | None = None
        # A file feature made over a region, measured as content: its digest,
        # the feature, and the key of the entry that holds it (see `placed_by`).
        self.placement: tuple[str, str, str] | None = None
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
                if preference["probe"] is not None:
                    self.check_probe(extractor.id, preference)

    def check_probe(self, extractor_id: str, preference: Mapping[str, Any]) -> None:
        """A probe reads a series that another extractor here declares, with
        the column it names. One whose extractor is not here is never present,
        and the preference is measured as if it had none."""
        probe = preference["probe"]
        source, output = FEATURE.fullmatch(probe["series"]).groups()  # type: ignore[union-attr]
        if source == extractor_id:
            raise BankError(f"{extractor_id} probes its own {output}; its own measure is what at_least reads")
        if source not in self.extractors:
            return
        declared = self[source].outputs.get(output, {})
        if declared.get("kind") != "series" or declared.get("dtype") not in _ARRAY_CODES:
            raise BankError(f"{extractor_id} probes {probe['series']} for {preference['input']}, "
                            f"which is not a floating-point series")
        if probe["column"] >= declared["columns"]:
            raise BankError(f"{extractor_id} probes column {probe['column']} of {probe['series']}, "
                            f"which has {declared['columns']}")

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
        `parameters` replaces an extractor's declared defaults, field by field.
        In a view made by `within`, the keys of that content are its region
        entries' (needs over the same region); any other content, such as a
        stem made over the region, is keyed whole."""
        region = self.region_of(content)
        keys: dict[str, str] = {}
        for extractor_id in self.order(targets):
            extractor = self[extractor_id]
            needs = {**{need: keys[need.split("/", 1)[0]] for need in extractor.needs}, **self.placed_needs(content)}
            keys[extractor_id] = key_of(extractor.id, extractor.version,
                                        self.parameters(extractor_id, parameters), content, needs, region)
        return keys

    def within(self, content: str, region: Mapping[str, int] | None) -> "Catalog":
        """This catalog, keying `content` over `region` (ADR 0112). Every
        reader and the runner take a region through this view, so `Bank`'s
        lookups and the runner's graph find region entries unchanged. A
        region of None is the whole content."""
        view = Catalog.__new__(Catalog)
        view.extractors, view.placement = self.extractors, self.placement
        view.region = None if region is None else (content, check_region(region))
        return view

    def placed_by(self, content: str, feature: str, key: str) -> "Catalog":
        """This catalog, keying the measures of `content`, a file feature made
        over a region (a stem), with the entry that holds it as a need: their
        outputs lie on the whole run's grid from where the region starts
        (ADR 0112), so the stem's region entry key, which carries the region,
        is in theirs. Identical stems of two regions are then measured twice,
        and a whole stem's measures keep their keys."""
        view = Catalog.__new__(Catalog)
        view.extractors, view.region = self.extractors, self.region
        view.placement = (content, feature, key)
        return view

    def placed_needs(self, content: str) -> dict[str, str]:
        """The need `placed_by` adds to the keys of `content`'s entries, if any."""
        if self.placement is not None and self.placement[0] == content:
            return {self.placement[1]: self.placement[2]}
        return {}

    def region_of(self, content: str) -> dict[str, int] | None:
        """The region this view keys `content` over, or None for whole."""
        if self.region is not None and self.region[0] == content:
            return self.region[1]
        return None

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

    def alternatives(self, extractor_id: str,
                     overrides: Mapping[str, Mapping[str, Any]] | None = None) -> list[tuple[str, dict[str, Any]]]:
        """The parameters whose entries may stand in for the configured
        ones', each with its declared value, in the manifest's order: the
        configured parameters with the interchangeable field set to each
        other declared value. None when the extractor declares nothing
        interchangeable or is configured with a value it does not declare."""
        declared = self[extractor_id].interchangeable
        if declared is None:
            return []
        configured = self.parameters(extractor_id, overrides)
        if parameter_at(configured, declared["parameter"]) not in declared["values"]:
            return []
        return [(value, with_parameter(configured, declared["parameter"], value)) for value in declared["values"]
                if value != parameter_at(configured, declared["parameter"])]


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
    """An entry manifest as the runner writes it; a reader refuses another
    shape. A region entry (`archive-feature-bank-entry/2`) has `/1`'s fields
    and `region` and `cut`, the digest of the FLAC cut its programs read."""
    _require(isinstance(manifest, dict) and manifest.get("contract") in (ENTRY_CONTRACT, REGION_ENTRY_CONTRACT),
             f"{where}: contract must be {ENTRY_CONTRACT} or {REGION_ENTRY_CONTRACT}")
    fields = {"contract", "key", "extractor", "parameters", "content", "media_type", "needs", "software", "grids",
              "outputs", "started_at", "ended_at", "runner"}
    region = None
    if manifest["contract"] == REGION_ENTRY_CONTRACT:
        fields |= {"region", "cut"}
        _require(set(manifest) == fields, f"{where}: fields must be {sorted(fields)}")
        region = check_region(manifest["region"], f"{where}: region")
        _require(isinstance(manifest["cut"], str) and KEY.fullmatch(manifest["cut"]) is not None, f"{where}: cut")
    _require(set(manifest) == fields, f"{where}: fields must be {sorted(fields)}")
    _require(isinstance(manifest["key"], str) and KEY.fullmatch(manifest["key"]) is not None, f"{where}: key")
    _require(derived.is_digest(manifest["content"]), f"{where}: content")
    extractor = manifest["extractor"]
    _require(isinstance(extractor, dict) and set(extractor) == {"id", "version"}, f"{where}: extractor")
    _require(isinstance(manifest["needs"], dict) and all(FEATURE.fullmatch(k) and isinstance(v, str)
                                                         and KEY.fullmatch(v) for k, v in manifest["needs"].items()),
             f"{where}: needs")
    _require(manifest["key"] == key_of(extractor["id"], extractor["version"], manifest["parameters"],
                                       manifest["content"], manifest["needs"], region),
             f"{where}: key does not match its extractor, parameters, content, needs, and region")
    _require(isinstance(manifest["outputs"], dict), f"{where}: outputs")
    for name, output in manifest["outputs"].items():
        _require(isinstance(output, dict) and output.get("kind") in KINDS, f"{where}: output {name}")
    return manifest


# -- equivalence records (ADR 0090's amendment) ----------------------------------

def pair_digest(extractor_id: str, version: str, first: Mapping[str, Any], second: Mapping[str, Any]) -> str:
    """What names a pair of an extractor's parameters, in either order."""
    digests = sorted([digest_of(canonical(first)), digest_of(canonical(second))])
    return digest_of(canonical({"contract": EQUIVALENCE_CONTRACT, "extractor": extractor_id, "version": version,
                                "parameters": digests}))


def equivalence_directory(root: Path, extractor_id: str, pair: str) -> Path:
    """Where the records of one pair live: one file a software pairing."""
    if not KEY.fullmatch(pair) or not ID.fullmatch(extractor_id):
        raise BankError(f"not a pair digest and extractor: {pair!r}, {extractor_id!r}")
    return Path(root) / EQUIVALENCE / extractor_id / pair.removeprefix("sha256:")


def named_software(software: Iterable[Mapping[str, str]], names: Iterable[str]) -> list[dict[str, str]]:
    """The named software of an entry, by name: what a record must match."""
    wanted = set(names)
    return sorted(({"name": item["name"], "version": item["version"]} for item in software
                   if item["name"] in wanted), key=lambda item: (item["name"], item["version"]))


def decided_value(measure: str, values: list[tuple[float, Mapping[str, Any]]]) -> float | None:
    """The value a measure of one output is decided by, over every input
    that measured it (value and detail): `note-f1` pooled over all their
    notes, `voicing-agreement` over all their voiced rows, and
    `f0-cents-p99` the worst input's. Pooling keeps one borderline note of a
    short clip from deciding alone; every input's own value is recorded
    beside it. None when no input measured it."""
    if not values:
        return None
    if measure == "note-f1":
        matched = sum(int(detail["matched"]) for _, detail in values)
        notes = sum(int(detail["notes"][0]) + int(detail["notes"][1]) for _, detail in values)
        return 2 * matched / notes if notes else None
    if measure == "voicing-agreement":
        agreeing = sum(int(detail["agreeing"]) for _, detail in values)
        either = sum(int(detail["voiced_in_either"]) for _, detail in values)
        return agreeing / either if either else None
    if measure == "f0-cents-p99":
        return max(value for value, _ in values)
    raise BankError(f"no measure {measure!r}")


def decisions(agree: list[dict[str, Any]], inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each declared measure of each output it names: the value it is
    decided by (`decided_value`), how many inputs measured it, and whether
    it is within its threshold. A measure measured on no input fails: an
    empty comparison (a stem with no confident pitch) is no evidence."""
    found = []
    for item in agree:
        for output in item["outputs"]:
            values = [(m["value"], m["detail"]) for i in inputs for m in i["measured"]
                      if m["measure"] == item["measure"] and m["output"] == output and m["value"] is not None]
            try:
                value = decided_value(item["measure"], values)
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise BankError(f"{item['measure']} of {output}: a detail lacks what decides it ({exc})") from exc
            within = value is not None and (
                Decimal(repr(value)) >= Decimal(item["at_least"]) if "at_least" in item
                else Decimal(repr(value)) <= Decimal(item["at_most"]))
            found.append({"measure": item["measure"], "output": output, "value": value,
                          "measured_on": len(values), "pass": within})
    return found


def agreement_passes(agree: list[dict[str, Any]], inputs: list[dict[str, Any]]) -> bool:
    """Whether every declared measure of every output passes (`decisions`)."""
    return all(decision["pass"] for decision in decisions(agree, inputs))


def check_equivalence(record: Any, where: str) -> dict[str, Any]:
    """An equivalence record as `extract.py equivalence` writes it; a reader
    refuses another shape, and a `pass` that its own values do not give."""
    _require(isinstance(record, dict) and record.get("contract") == EQUIVALENCE_CONTRACT,
             f"{where}: contract must be {EQUIVALENCE_CONTRACT}")
    fields = {"contract", "extractor", "parameter", "sides", "agree", "inputs", "pass", "made_at"}
    _require(set(record) == fields, f"{where}: fields must be {sorted(fields)}")
    extractor = record["extractor"]
    _require(isinstance(extractor, dict) and set(extractor) == {"id", "version"}
             and isinstance(extractor["id"], str) and ID.fullmatch(extractor["id"]) is not None
             and isinstance(extractor["version"], str), f"{where}: extractor")
    _require(isinstance(record["parameter"], str) and PARAMETER_PATH.fullmatch(record["parameter"]) is not None,
             f"{where}: parameter")
    sides = record["sides"]
    _require(isinstance(sides, list) and len(sides) == 2 and all(
        isinstance(side, dict) and set(side) == {"value", "parameters", "software"}
        and isinstance(side["value"], str) and isinstance(side["parameters"], str)
        and KEY.fullmatch(side["parameters"]) is not None for side in sides)
        and sides[0]["parameters"] != sides[1]["parameters"], f"{where}: two sides of other parameters")
    for side in sides:
        _software(side["software"], f"{where}: side {side['value']}")
    agree = record["agree"]
    _require(isinstance(agree, list) and agree and all(
        isinstance(item, dict) and item.get("measure") in MEASURES and isinstance(item.get("outputs"), list)
        and set(item) in ({"measure", "outputs", "at_least"}, {"measure", "outputs", "at_most"})
        and DECIMAL.fullmatch(str(item.get("at_least", item.get("at_most")))) is not None for item in agree),
             f"{where}: agree")
    inputs = record["inputs"]
    _require(isinstance(inputs, list) and inputs, f"{where}: inputs")
    for index, item in enumerate(inputs):
        _require(isinstance(item, dict) and set(item) == {"content", "entries", "measured", "evidence"}
                 and derived.is_digest(item["content"]) and isinstance(item["entries"], list)
                 and len(item["entries"]) == 2 and all(isinstance(k, str) and KEY.fullmatch(k) for k in item["entries"])
                 and isinstance(item["evidence"], dict), f"{where}: inputs[{index}]")
        _require(isinstance(item["measured"], list) and all(
            isinstance(m, dict) and set(m) == {"measure", "output", "value", "detail"}
            and isinstance(m["output"], str) and isinstance(m["detail"], dict)
            and (m["value"] is None or (isinstance(m["value"], (int, float)) and not isinstance(m["value"], bool)
                                        and math.isfinite(m["value"]))) for m in item["measured"]),
                 f"{where}: inputs[{index}].measured")
    _require(isinstance(record["pass"], bool), f"{where}: pass")
    _require(record["pass"] == agreement_passes(agree, inputs),
             f"{where}: pass is {record['pass']}, which its measured values do not give")
    _require(isinstance(record["made_at"], str), f"{where}: made_at")
    return record


def read_equivalences(directory: Path) -> list[dict[str, Any]]:
    """Every record of one pair. A record that is unreadable or the wrong
    shape is refused, never read as absent or as failing."""
    try:
        paths = sorted(directory.glob("*.json"))
    except OSError as exc:
        raise BankError(f"{directory}: cannot list its records: {exc}") from exc
    records = []
    for path in paths:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise BankError(f"{path}: unreadable: {exc}") from exc
        records.append(check_equivalence(record, str(path)))
    return records


def write_equivalence(root: Path, record: dict[str, Any], first: Mapping[str, Any],
                      second: Mapping[str, Any]) -> Path:
    """File a record under its pair, named by its sides' software; a record
    for the same software replaces the last, by one rename."""
    check_equivalence(record, "the equivalence record")
    extractor = record["extractor"]
    directory = equivalence_directory(root, extractor["id"],
                                      pair_digest(extractor["id"], extractor["version"], first, second))
    sides = {side["parameters"] for side in record["sides"]}
    _require(sides == {digest_of(canonical(first)), digest_of(canonical(second))},
             "the record's sides are not the parameters it is filed under")
    name = digest_of(canonical(sorted([[s["parameters"], s["software"]] for s in record["sides"]])))
    directory.mkdir(parents=True, exist_ok=True)
    final = directory / (name.removeprefix("sha256:") + ".json")
    temporary = final.with_name(final.name + f".{os.getpid()}.tmp")
    temporary.write_bytes(json.dumps(record, indent=1, sort_keys=True).encode("utf-8") + b"\n")
    os.replace(temporary, final)
    return final


# -- reading -----------------------------------------------------------------

@dataclass(frozen=True)
class Entry:
    """One complete entry of the bank. `placed` is the region of the
    recording whose timeline this entry's times are on when that is not its
    own: a measure of a stem made over a region (ADR 0112), as
    `Bank.measured_over` found it."""
    path: Path
    manifest: dict[str, Any]
    placed: dict[str, int] | None = None

    @property
    def region(self) -> dict[str, int] | None:
        """The region a region entry was made over, or None for whole content."""
        return self.manifest.get("region")

    @property
    def timeline(self) -> dict[str, int] | None:
        """The region of the recording this entry's times start at: its own
        region, or the region of the stem it measured; None for the whole."""
        return self.region if self.region is not None else self.placed

    @property
    def offset(self) -> float:
        """Seconds to add to this entry's times to place them on its
        recording's timeline: a region entry's outputs, and those of a
        measure of a stem made over a region, are on the cut's (ADR 0112)."""
        region = self.timeline
        return 0.0 if region is None else region["start"] / region["rate"]

    def shift(self, rate: int) -> int:
        """`offset` in samples at `rate`, exactly when the region is at that
        rate (it is at the content's stream rate), else rounded half to even."""
        region = self.timeline
        if region is None:
            return 0
        if region["rate"] == rate:
            return region["start"]
        return int((Decimal(region["start"]) * rate / region["rate"]).quantize(Decimal(1), ROUND_HALF_EVEN))

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
                 parameters: Mapping[str, Mapping[str, Any]] | None = None, exact: bool = False):
        self.root = Path(root)
        self.catalog = catalog if catalog is not None else Catalog.load()
        self.parameters = parameters
        # Exact: the configured key alone, never an entry that stands in for it.
        self.exact = exact

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
        return next(self.candidates(content, extractor_id, filed_under, feature), None)

    def candidates(self, content: str, extractor_id: str, filed_under: str | None = None,
                   feature: str | None = None) -> Iterable[Entry]:
        """The entries that answer for one extractor and content, best
        first: the configured parameters' entry, then, unless the bank is
        exact, each entry of another interchangeable value in the manifest's
        order that a passing equivalence record lets stand in (ADR 0090's
        amendment). Without such a record there is only the first."""
        if filed_under is not None and feature is None and any(
                p["parameters"] for p in self.catalog[extractor_id].prefers):
            raise BankError(f"{extractor_id} takes other parameters on a feature it prefers; "
                            f"say which feature {content} is")
        parameters = self.parameters if feature is None else self.catalog.overrides_for(feature, self.parameters)
        found = self._at(content, extractor_id, filed_under, parameters)
        if found is not None:
            yield found
        if self.exact:
            return
        extractor = self.catalog[extractor_id]
        configured = self.catalog.parameters(extractor_id, parameters)
        for _value, other in self.catalog.alternatives(extractor_id, parameters):
            standing = self._at(content, extractor_id, filed_under, {**(parameters or {}), extractor_id: other})
            if standing is not None and self.stands_in(extractor, configured, standing):
                yield standing

    def _at(self, content: str, extractor_id: str, filed_under: str | None,
            parameters: Mapping[str, Mapping[str, Any]] | None) -> Entry | None:
        key = self.catalog.keys(content, [extractor_id], parameters)[extractor_id]
        found = read_entry(entry_directory(self.root, filed_under or content, extractor_id, key))
        if found is not None and found.manifest["key"] != key:
            raise BankError(f"{found.path} holds key {found.manifest['key']}, not {key}")
        return found

    def stands_in(self, extractor: Extractor, configured: Mapping[str, Any], standing: Entry) -> bool:
        """Whether a passing record of this pair names the software the
        standing entry was made with. A record for other software, a failing
        record, or none, answers no; a damaged one is refused."""
        declared = extractor.interchangeable
        assert declared is not None
        other = standing.manifest["parameters"]
        directory = equivalence_directory(self.root, extractor.id,
                                          pair_digest(extractor.id, extractor.version, configured, other))
        made_with = named_software(standing.manifest["software"], declared["software"])
        other_digest = digest_of(canonical(other))
        for record in read_equivalences(directory):
            side = next((s for s in record["sides"] if s["parameters"] == other_digest), None)
            if record["pass"] and side is not None and side["software"] == made_with:
                return True
        return False

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
        return next((measured for _, measured in self.measured_over(content, feature, [extractor_id])
                     if measured[extractor_id] is not None), {extractor_id: None})[extractor_id]

    def measured_over(self, content: str, feature: str,
                      extractor_ids: Iterable[str]) -> list[tuple[Entry, dict[str, Entry | None]]]:
        """For each entry that answers for the feature's extractor, best
        first (`candidates`), that entry and each extractor's measure of its
        file feature, or None. A reader that combines measures takes them
        over one entry, so what it cites is what it read."""
        match = FEATURE.fullmatch(feature)
        if match is None:
            raise BankError(f"not a feature name: {feature!r}")
        source, output = match.groups()
        extractor_ids = list(extractor_ids)
        found = []
        for entry in self.candidates(content, source):
            described = entry.output(output)
            if described["kind"] != "file":
                raise BankError(f"{feature} is a {described['kind']}, not a file; only a file can be content")
            measures = {}
            for extractor_id in extractor_ids:
                reader = self if entry.timeline is None else Bank(
                    self.root, self.catalog.placed_by(described["sha256"], feature, entry.manifest["key"]),
                    self.parameters, exact=self.exact)
                measured = reader.entry(described["sha256"], extractor_id, filed_under=content, feature=feature)
                if measured is not None and entry.timeline is not None and measured.timeline is None:
                    # A stem made over a region is content of its own, measured
                    # whole, so its measures' times are on the region's cut.
                    measured = replace(measured, placed=entry.timeline)
                measures[extractor_id] = measured
            found.append((entry, measures))
        return found

    def default_input(self, content: str, extractor_id: str) -> str | None:
        """The file feature an extractor measures by default for this
        content: the first it prefers that the bank holds for it, or None,
        for the content itself. A preference with `at_least` is passed over
        once its measure of the feature is in the bank and any of those
        scalars falls below its minimum (a vocal stem that is mostly
        unvoiced); until then, it is the default. The runner and every
        reader decide by this, so they agree on which entry is the
        content's."""
        for preference in self.catalog[extractor_id].prefers:
            source = preference["input"].split("/", 1)[0]
            if source not in self.catalog.extractors or self.input_of(content, preference["input"]) is None:
                continue
            if ((preference["at_least"] or preference["probe"] is not None)
                    and self.falls_short(content, preference, extractor_id)):
                continue
            return preference["input"]
        return None

    def falls_short(self, content: str, preference: Mapping[str, Any], extractor_id: str) -> bool:
        """Whether the measure of a preferred feature is in the bank and one
        of the scalars its preference names is below the minimum given; or,
        while that measure is not in the bank, whether the preference's
        probe is and falls short (`probe_falls_short`). The extractor's own
        measure, once made, decides alone. A value that is not a number is
        refused rather than read as either."""
        measured = self.entry_from(content, preference["input"], extractor_id)
        if measured is None:
            return self.probe_falls_short(content, preference) is not None
        for name, minimum in preference["at_least"].items():
            value = measured.scalar(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
                raise BankError(f"{measured.path}: {name} is {value!r}, not a number to compare with {minimum}")
            if Decimal(repr(value)) < Decimal(minimum):
                return True
        return False

    def probe_falls_short(self, content: str, preference: Mapping[str, Any]) -> Entry | None:
        """The probe's entry when it is in the bank and falls short, else
        None: the share of the probed series' rows whose column is at or
        above `at_or_above`, read from the probing extractor's measure of
        the preferred feature (`f0-pesto`'s confidence on the vocal stem), is
        below `share_at_least`. A series of no rows falls short. A probe
        that is not in the bank says nothing, so the preference is measured.
        It is read with the standard library, like the rest of the runner."""
        probe = preference.get("probe")
        if probe is None:
            return None
        source, output = FEATURE.fullmatch(probe["series"]).groups()  # type: ignore[union-attr]
        if source not in self.catalog.extractors:
            return None
        found = self.input_of(content, preference["input"])
        if found is None:
            return None
        entry = self.entry(found[1], source, filed_under=content, feature=preference["input"])
        if entry is None:
            return None
        described = entry.output(output)
        rows, columns = described["shape"]
        values = array.array(_ARRAY_CODES[described["dtype"]], (entry.path / described["file"]).read_bytes())
        if sys.byteorder != "little":
            values.byteswap()
        if len(values) != rows * columns:
            raise BankError(f"{entry.path / described['file']} does not hold its shape {described['shape']}")
        threshold = float(Decimal(probe["at_or_above"]))
        reaching = sum(1 for value in values[probe["column"]::columns] if value >= threshold)
        if rows and Decimal(reaching) / Decimal(rows) >= Decimal(probe["share_at_least"]):
            return None
        return entry

    def default_entry(self, content: str, extractor_id: str) -> tuple[Entry | None, str | None]:
        """The entry an extractor makes of this content by default, and the
        feature it measured (None for the content itself): the measure of
        the first preferred feature the bank holds whose measure, if made,
        reaches the preference's `at_least`, else of the content. The entry
        is None when that measure is not in the bank; it is never the
        content's own entry in place of a preferred feature's."""
        feature = self.default_input(content, extractor_id)
        if feature is None:
            return self.entry(content, extractor_id), None
        return self.entry_from(content, feature, extractor_id), feature


# -- regions and requests (ADR 0112) -------------------------------------------
#
# Beside `Bank.entry`, which finds the whole-content entry (or, in a view made
# by `Catalog.within`, one region's): `covering` answers what covers a span,
# and a bank request asks the scheduler for a key nothing holds yet.

class RegionView(Bank):
    """One region of a content read as the whole is read (ADR 0112): every
    lookup of `content` finds the whole-content entry first, else the entry
    made over `region` whose key the installed extractors give that region.
    Other content (a stem made over the region) is keyed whole, as the
    runner keys it. A reader takes times from `Entry.offset`/`Entry.shift`:
    region entries and the measures of their stems are on the cut's."""

    def __init__(self, base: Bank, content: str, region: Mapping[str, int]):
        super().__init__(base.root, base.catalog, base.parameters, base.exact)
        self.content = content
        self.region = check_region(region)
        self.view = base.catalog.within(content, self.region)

    def _at(self, content: str, extractor_id: str, filed_under: str | None,
            parameters: Mapping[str, Mapping[str, Any]] | None) -> Entry | None:
        found = super()._at(content, extractor_id, filed_under, parameters)
        if found is not None or content != self.content or filed_under not in (None, content):
            return found
        key = self.view.keys(content, [extractor_id], parameters)[extractor_id]
        found = read_entry(entry_directory(self.root, content, extractor_id, key))
        if found is not None and found.manifest["key"] != key:
            raise BankError(f"{found.path} holds key {found.manifest['key']}, not {key}")
        return found


def region_entries(reader: Bank, content: str, extractor_id: str) -> list[Entry]:
    """The current region entries of one extractor for this content: those
    whose key is the one the installed extractor, parameters, and needs give
    their region (an entry of another version is never served), by region."""
    directory = derived.directory(reader.root, content) / "bank" / extractor_id
    try:
        names = sorted(child.name for child in directory.iterdir() if child.is_dir())
    except FileNotFoundError:
        return []
    found: list[Entry] = []
    for name in names:
        entry = read_entry(directory / name)
        if entry is None or entry.region is None:
            continue
        current = reader.catalog.within(content, entry.region).keys(content, [extractor_id], reader.parameters)
        if current[extractor_id] == entry.manifest["key"]:
            found.append(entry)
    return sorted(found, key=lambda e: (e.region["start"] / e.region["rate"], e.region["end"] / e.region["rate"]))


def regions(reader: Bank, content: str) -> list[dict[str, int]]:
    """Every region some current region entry of this content was made over,
    in order of start, each once."""
    directory = derived.directory(reader.root, content) / "bank"
    try:
        extractor_ids = sorted(child.name for child in directory.iterdir()
                               if child.is_dir() and child.name in reader.catalog.extractors)
    except FileNotFoundError:
        return []
    found: list[dict[str, int]] = []
    for extractor_id in extractor_ids:
        for entry in region_entries(reader, content, extractor_id):
            if entry.region not in found:
                found.append(dict(entry.region))
    return sorted(found, key=lambda r: (r["start"] / r["rate"], r["end"] / r["rate"]))


def covering(reader: Bank, content: str, extractor_id: str, start: int, end: int, rate: int) -> Entry | None:
    """The entry of the installed extractor that covers `[start, end)` of this
    content (samples at `rate`): the whole-content entry, else a region entry
    whose region contains the span and whose key is the one the installed
    extractor, parameters, and needs give that region (so an entry of another
    version is never served), the narrowest first. None when nothing covers
    it: the span is then a request. Region entries are never stitched."""
    _require(isinstance(rate, int) and rate > 0 and 0 <= start < end, "a span needs 0 <= start < end at a rate")
    whole = reader.entry(content, extractor_id)
    if whole is not None:
        return whole
    found = [entry for entry in region_entries(reader, content, extractor_id)
             if region_contains(entry.region, start, end, rate)]
    if not found:
        return None
    return min(found, key=lambda e: (e.region["end"] - e.region["start"]) / e.region["rate"])


def request_path(root: Path, key: str) -> Path:
    """Where the request for one key is filed: `requests/<key hex>.json`."""
    if not isinstance(key, str) or not KEY.fullmatch(key):
        raise BankError(f"not an entry key: {key!r}")
    return Path(root) / REQUESTS / (key.removeprefix("sha256:") + ".json")


def check_request(request: Any, where: str = "bank request") -> dict[str, Any]:
    """A bank request as `ask` writes it; the scheduler refuses another shape."""
    fields = {"contract", "key", "content", "extractor", "region", "feature", "asked_by"}
    _require(isinstance(request, dict) and request.get("contract") == BANK_REQUEST_CONTRACT,
             f"{where}: contract must be {BANK_REQUEST_CONTRACT}")
    _require(set(request) == fields, f"{where}: fields must be {sorted(fields)}")
    _require(isinstance(request["key"], str) and KEY.fullmatch(request["key"]) is not None, f"{where}: key")
    _require(derived.is_digest(request["content"]), f"{where}: content")
    extractor = request["extractor"]
    _require(isinstance(extractor, dict) and set(extractor) == {"id", "version"}
             and isinstance(extractor["id"], str) and ID.fullmatch(extractor["id"]) is not None
             and isinstance(extractor["version"], str) and VERSION.fullmatch(extractor["version"]) is not None,
             f"{where}: extractor is {{id, version}}")
    if request["region"] is not None:
        check_region(request["region"], f"{where}: region")
    _require(request["feature"] is None or (isinstance(request["feature"], str)
                                            and FEATURE.fullmatch(request["feature"]) is not None),
             f"{where}: feature is null or <extractor>/<output>")
    _require(isinstance(request["asked_by"], str) and 0 < len(request["asked_by"]) <= 200, f"{where}: asked_by")
    return request


def ask(reader: Bank, content: str, extractor_id: str, asked_by: str, region: Mapping[str, int] | None = None,
        feature: str | None = None) -> tuple[Path, str, bool]:
    """Ask for the entry the installed extractor would make of this content
    (or of `region` of it): write a bank request named by that key, unless
    one is there. Returns its path, the key, and whether this call wrote it.
    Asking again is the same file, and the first asker's request stands.
    `feature` names the file feature measured when the content is a preferred
    input (the vocal stem `melody` measures), for the scheduler to find it."""
    catalog = reader.catalog.within(content, region)
    parameters = reader.parameters if feature is None else catalog.overrides_for(feature, reader.parameters)
    key = catalog.keys(content, [extractor_id], parameters)[extractor_id]
    extractor = reader.catalog[extractor_id]
    request = check_request({"contract": BANK_REQUEST_CONTRACT, "key": key, "content": content,
                             "extractor": {"id": extractor.id, "version": extractor.version},
                             "region": None if region is None else check_region(region), "feature": feature,
                             "asked_by": asked_by})
    path = request_path(reader.root, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.stem}.{os.getpid()}.{id(request):x}.partial")
    try:
        temporary.write_bytes(canonical(request))
        try:
            os.link(temporary, path)
        except FileExistsError:
            return path, key, False
        return path, key, True
    finally:
        temporary.unlink(missing_ok=True)


def requests(root: Path) -> list[dict[str, Any]]:
    """Every bank request filed, in key order; one whose file is damaged or
    misnamed is refused, not skipped (the scheduler would otherwise lose it)."""
    directory = Path(root) / REQUESTS
    try:
        paths = sorted(p for p in directory.iterdir() if p.suffix == ".json" and not p.name.startswith("."))
    except FileNotFoundError:
        return []
    found = []
    for path in paths:
        try:
            request = check_request(json.loads(path.read_text(encoding="utf-8")), str(path))
        except ValueError as exc:
            raise BankError(f"{path}: unreadable: {exc}") from exc
        if request_path(root, request["key"]) != path:
            raise BankError(f"{path} holds the request for {request['key']}, not its own name")
        found.append(request)
    return found


def environment_root() -> Path | None:
    """The bank root named by ARCHIVE_FEATURE_BANK, if any."""
    value = os.environ.get("ARCHIVE_FEATURE_BANK")
    return Path(value) if value else None
