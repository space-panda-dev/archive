"""The relay's aggregate metrics, in the Prometheus text format (ADR 0118).

Only counts, sizes, durations, and information series leave: every label is
drawn from a fixed or code-defined set (shapes, contracts, methods,
extractors, kinds, features, stores, routes), and none names an archive, an
object, a revision, a digest, a person, or a path. The counts are collected
in the background and answered at once; the request durations are the
relay's own. Nothing here writes anywhere.
"""

from __future__ import annotations

import json
import math
import os
import stat
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

import bank
import derived

CONTENT_TYPE = "text/plain; version=0.0.4"
# How often the collector counts again once the first scrape started it.
COLLECT_SECONDS = 300.0
DURATION_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0)
# State-model class of each store (docs/state-model.md).
STORE_CLASSES = {"records": "evidence", "content": "evidence", "world": "regenerable", "media": "regenerable",
                 "bank": "regenerable", "staging": "operational"}
# The bank's own top-level directories beside the content directories.
BANK_TOP = frozenset({bank.REQUESTS, bank.EQUIVALENCE, "tmp"})
FAILED = ".failed.json"


@dataclass
class Family:
    """One metric family: its samples are (suffix, labels, value)."""

    name: str
    type: str
    help: str
    samples: list[tuple[str, dict[str, str], float]] = field(default_factory=list)


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _number(value: float) -> str:
    if value == math.inf:
        return "+Inf"
    if isinstance(value, int) or (isinstance(value, float) and value.is_integer() and abs(value) < 2 ** 53):
        return str(int(value))
    return repr(float(value))


def exposition(families: Iterable[Family]) -> str:
    """The text format, version 0.0.4: HELP and TYPE, then each sample."""
    lines = []
    for family in families:
        lines.append(f"# HELP {family.name} {family.help}")
        lines.append(f"# TYPE {family.name} {family.type}")
        for suffix, labels, number in family.samples:
            rendered = ",".join(f'{key}="{_escape(str(text))}"' for key, text in labels.items())
            lines.append(f"{family.name}{suffix}{{{rendered}}} {_number(number)}" if rendered
                         else f"{family.name}{suffix} {_number(number)}")
    return "\n".join(lines) + "\n"


def outcome(status: int) -> str:
    """`ok` below 400, `refused` for 4xx, `failed` for 5xx."""
    return "ok" if status < 400 else ("refused" if status < 500 else "failed")


class Histogram:
    """A cumulative histogram by a fixed tuple of label names."""

    def __init__(self, name: str, help_text: str, label_names: tuple[str, ...],
                 buckets: tuple[float, ...] = DURATION_BUCKETS):
        self.name, self.help, self.label_names, self.buckets = name, help_text, label_names, buckets
        self.lock = threading.Lock()
        self.series: dict[tuple[str, ...], list[float]] = {}

    def observe(self, labels: tuple[str, ...], seconds: float) -> None:
        with self.lock:
            counts = self.series.setdefault(labels, [0.0] * (len(self.buckets) + 2))
            for index, bound in enumerate(self.buckets):
                if seconds <= bound:
                    counts[index] += 1
            counts[-2] += 1  # count, also the +Inf bucket
            counts[-1] += seconds

    def family(self) -> Family:
        family = Family(self.name, "histogram", self.help)
        with self.lock:
            series = {labels: list(counts) for labels, counts in sorted(self.series.items())}
        for labels, counts in series.items():
            named = dict(zip(self.label_names, labels))
            for index, bound in enumerate(self.buckets):
                family.samples.append(("_bucket", {**named, "le": _number(bound)}, counts[index]))
            family.samples.append(("_bucket", {**named, "le": "+Inf"}, counts[-2]))
            family.samples.append(("_sum", named, counts[-1]))
            family.samples.append(("_count", named, counts[-2]))
        return family


# -- what World's selected generation says ----------------------------------------

def _contract_ids(item: dict[str, Any]) -> set[str]:
    return {c["id"] for c in item.get("contracts") or [] if isinstance(c, dict) and isinstance(c.get("id"), str)}


def _digest(payload: Any) -> str | None:
    content = payload.get("content") if isinstance(payload, dict) else None
    found = content.get("digest") if isinstance(content, dict) else None
    return found if derived.is_digest(found) else None


def archive_counts(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts from every record revision of one generation, as
    world/view_records.py reads them. Plain JSON values, so they cross from
    the query worker. Coverage reads head revisions; record counts read all.

    An audio Artifact is one an audio stream claim describes whose content
    no destroy transition names."""
    shapes: dict[str, int] = {}
    contracts: dict[str, int] = {}
    methods: dict[str, int] = {}
    artifact_digest: dict[str, str] = {}
    described: set[str] = set()
    destroyed: set[str] = set()
    works: set[str] = set()
    sheeted: set[str] = set()
    occurrences: set[str] = set()
    matched: set[str] = set()
    for item in records:
        shape = item.get("shape")
        if isinstance(shape, str):
            shapes[shape] = shapes.get(shape, 0) + 1
        cited = _contract_ids(item)
        for contract in cited:
            contracts[contract] = contracts.get(contract, 0) + 1
        payload, head = item.get("payload"), item.get("head", True)
        refs = [r for r in item.get("refs") or [] if isinstance(r, dict)]
        if shape == "artifact" and _digest(payload) is not None and (head or item["object_id"] not in artifact_digest):
            artifact_digest[item["object_id"]] = _digest(payload)
        if "archive.claim/derivation_run" in cited:
            method = payload.get("method") if isinstance(payload, dict) else None
            if isinstance(method, dict) and isinstance(method.get("id"), str):
                methods[method["id"]] = methods.get(method["id"], 0) + 1
        if "archive.claim/audio_stream" in cited:
            described.update(r["id"] for r in refs if r.get("role") == "artifact")
        if "archive.transition/destroy" in cited and _digest(payload) is not None:
            destroyed.add(_digest(payload))
        if not head:
            continue
        if shape == "entity" and item.get("kind") == "musical_work":
            works.add(item["object_id"])
        if "archive.claim/lead_sheet" in cited:
            sheeted.update(r["id"] for r in refs if r.get("role") == "work")
        if "archive.claim/song_occurrence" in cited:
            occurrences.add(item["object_id"])
        if "archive.claim/work_match" in cited:
            matched.update(r["id"] for r in refs if r.get("role") == "occurrence")
    audio = sorted({artifact_digest[o] for o in described if o in artifact_digest} - destroyed)
    return {"shapes": shapes, "contracts": contracts, "methods": methods, "audio": audio,
            "works": len(works), "works_with_lead_sheet": len(works & sheeted),
            "occurrences": len(occurrences), "occurrences_matched": len(occurrences & matched)}


# -- the feature bank --------------------------------------------------------------

@dataclass(frozen=True)
class InstalledExtractors:
    """Each installed extractor's version, and the stem separators: those
    declaring an audio file output."""

    versions: dict[str, str]
    separators: frozenset[str]


def installed_extractors() -> InstalledExtractors:
    catalog = bank.Catalog.load()
    versions, separators = {}, set()
    for extractor_id, extractor in catalog.extractors.items():
        versions[extractor_id] = extractor.version
        if any(o.get("kind") == "file" and str(o.get("media_type", "")).startswith("audio/")
               for o in extractor.outputs.values()):
            separators.add(extractor_id)
    return InstalledExtractors(versions, frozenset(separators))


@dataclass
class BankCounts:
    made: dict[str, int] = field(default_factory=dict)
    failed: dict[str, int] = field(default_factory=dict)
    outdated: dict[str, int] = field(default_factory=dict)
    requests: int = 0
    unreadable: int = 0
    with_stems: set[str] = field(default_factory=set)


def _entry_version(path: Path) -> str | None:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    version = manifest.get("extractor", {}).get("version") if isinstance(manifest, dict) else None
    return version if isinstance(version, str) else None


def _directories(path: Path) -> list[os.DirEntry]:
    try:
        with os.scandir(path) as found:
            return [item for item in found if item.is_dir(follow_symlinks=False)]
    except FileNotFoundError:
        return []


class BankCounter:
    """Counts a bank's entries. Entries are never rewritten, so each entry's
    version is read once and remembered while the entry is there."""

    def __init__(self) -> None:
        self.versions: dict[str, str | None] = {}

    def count(self, root: Path, installed: Any) -> BankCounts:
        counts = BankCounts()
        seen: dict[str, str | None] = {}
        for ab in _directories(root / "sha256"):
            for cd in _directories(Path(ab.path)):
                for content in _directories(Path(cd.path)):
                    digest = "sha256:" + content.name
                    for extractor in _directories(Path(content.path) / "bank"):
                        self._extractor(Path(extractor.path), extractor.name, digest, installed, counts, seen)
        self.versions = seen
        try:
            with os.scandir(root / bank.REQUESTS) as found:
                counts.requests = sum(1 for item in found if item.name.endswith(".json") and item.is_file())
        except FileNotFoundError:
            pass
        return counts

    def _extractor(self, directory: Path, extractor_id: str, digest: str, installed: Any, counts: BankCounts,
                   seen: dict[str, str | None]) -> None:
        with os.scandir(directory) as found:
            items = list(found)
        for item in items:
            if item.name.endswith(FAILED) and item.is_file(follow_symlinks=False):
                counts.failed[extractor_id] = counts.failed.get(extractor_id, 0) + 1
                continue
            if not item.is_dir(follow_symlinks=False) or item.name.startswith("."):
                continue
            counts.made[extractor_id] = counts.made.get(extractor_id, 0) + 1
            if extractor_id in installed.separators:
                counts.with_stems.add(digest)
            if item.path in self.versions:
                version = self.versions[item.path]
            else:
                try:
                    version = _entry_version(Path(item.path) / bank.ENTRY)
                except (OSError, ValueError):
                    version = None
            seen[item.path] = version
            if version is None:
                counts.unreadable += 1
            elif installed.versions.get(extractor_id) != version:
                counts.outdated[extractor_id] = counts.outdated.get(extractor_id, 0) + 1


# -- derived media and stores ---------------------------------------------------------

@dataclass
class MediaCounts:
    derived: dict[str, int]
    with_practice: set[str]


def media_counts(root: Path, audio: Iterable[str]) -> MediaCounts:
    """For each audio Artifact, which derived-media kinds exist, and whether
    a practice render of it does (ADRs 0069, 0089)."""
    made = {kind: 0 for kind in derived.KINDS}
    practice = set()
    for digest in audio:
        directory = derived.directory(root, digest)
        for kind in derived.KINDS:
            if (directory / derived.KINDS[kind].file_name).is_file():
                made[kind] += 1
        try:
            with os.scandir(directory / "practice") as found:
                if any(item.name.endswith(".flac") for item in found):
                    practice.add(digest)
        except (FileNotFoundError, NotADirectoryError):
            pass
    return MediaCounts(made, practice)


def _walk_bytes(root: Path, place: Callable[[tuple[str, ...]], str]) -> dict[str, int]:
    """Bytes of the regular files under root, never through a link, summed
    by what `place` names for each file's path relative to root."""
    totals: dict[str, int] = {}
    for directory, subdirectories, files in os.walk(root, followlinks=False):
        relative = Path(directory).relative_to(root).parts
        for name in files:
            try:
                status = os.lstat(os.path.join(directory, name))
            except FileNotFoundError:
                continue
            if stat.S_ISREG(status.st_mode):
                where = place(relative)
                totals[where] = totals.get(where, 0) + status.st_size
    return totals


def tree_bytes(root: Path) -> int | None:
    """Bytes of the regular files under root; None when root is absent."""
    if not root.is_dir():
        return None
    return _walk_bytes(root, lambda _: "all").get("all", 0)


def derived_bytes(media_root: Path | None, bank_root: Path | None) -> dict[str, int]:
    """Bytes of derived media and of the bank, which may share one root:
    a content directory's `bank/` and the bank's top-level directories are
    the bank's, everything else the media's."""
    totals: dict[str, int] = {}
    roots = {}
    for name, root in (("media", media_root), ("bank", bank_root)):
        if root is not None:
            roots.setdefault(Path(root).resolve(), set()).add(name)
            totals[name] = 0
    for root, names in roots.items():
        def place(parts: tuple[str, ...], names: set[str] = names) -> str:
            if "bank" in names and ((len(parts) >= 5 and parts[0] == "sha256" and parts[4] == "bank")
                                    or (parts and parts[0] in BANK_TOP)):
                return "bank"
            return "media" if "media" in names else "bank"
        if root.is_dir():
            for name, size in _walk_bytes(root, place).items():
                totals[name] += size
    return totals


# -- the collector -------------------------------------------------------------------

class Collector:
    """Collects the archive's counts in a background thread, started by the
    first scrape, and renders them with the relay's own series.

    `world_counts` returns archive_counts for World's selected generation;
    `paths` names the configured roots."""

    def __init__(self, world_counts: Callable[[], dict[str, Any]], archive_root: Path, world_store: Path,
                 staging: Path, media_root: Path | None, bank_root: Path | None,
                 installed: Callable[[], Any] = installed_extractors):
        self.world_counts, self.installed = world_counts, installed
        self.archive_root, self.world_store, self.staging = archive_root, world_store, staging
        self.media_root, self.bank_root = media_root, bank_root
        self.requests = Histogram("archive_relay_request_duration_seconds",
                                  "Time the relay took to answer, by described route and outcome.",
                                  ("route", "outcome"))
        self.bank = BankCounter()
        self.families: list[Family] = []
        self.lock = threading.Lock()
        self.collected = threading.Event()
        self.stopping = threading.Event()
        self.thread: threading.Thread | None = None

    def observe(self, route: str, status: int, seconds: float) -> None:
        self.requests.observe((route, outcome(status)), seconds)

    def start(self) -> None:
        with self.lock:
            if self.thread is None and not self.stopping.is_set():
                self.thread = threading.Thread(target=self._forever, name="relay-metrics", daemon=True)
                self.thread.start()

    def stop(self) -> None:
        self.stopping.set()
        if self.thread is not None:
            self.thread.join(timeout=30)

    def _forever(self) -> None:
        while not self.stopping.is_set():
            try:
                self.collect()
            except Exception as exc:  # noqa: BLE001 - metrics never stop the relay
                _log(f"collection failed: {type(exc).__name__}: {exc}")
            self.stopping.wait(COLLECT_SECONDS)

    def collect(self) -> None:
        """Count everything once. A part that cannot be read is logged and
        left out of this collection; the others still answer."""
        started = time.monotonic()
        families: list[Family] = []
        audio: list[str] | None = None
        counts = _part("World", self.world_counts)
        if counts is not None:
            audio = counts["audio"]
            families += _world_families(counts)
        with_stems = None
        if self.bank_root is not None:
            installed = _part("the installed extractors", self.installed)
            found = _part("the feature bank", lambda: self.bank.count(self.bank_root, installed)) if installed else None
            if found is not None:
                families += _bank_families(found)
                with_stems = found.with_stems
        media = None
        if self.media_root is not None and audio is not None:
            media = _part("derived media", lambda: media_counts(self.media_root, audio))
        if audio is not None:
            families += _coverage_families(audio, counts, media, with_stems)
        if media is not None:
            families.append(Family("archive_media", "gauge",
                                   "Audio Artifacts with each derived-media kind derived or missing.",
                                   [("", {"kind": kind, "state": state}, n)
                                    for kind, made in sorted(media.derived.items())
                                    for state, n in (("derived", made), ("missing", len(audio) - made))]))
        sizes = _part("store sizes", self._store_bytes)
        if sizes:
            families.append(Family("archive_store_bytes", "gauge",
                                   "Bytes of regular files in each store, by state-model class.",
                                   [("", {"store": name, "class": STORE_CLASSES[name]}, size)
                                    for name, size in sorted(sizes.items())]))
        families.append(Family("archive_metrics_collected_timestamp_seconds", "gauge",
                               "When the archive's counts were collected.", [("", {}, round(time.time(), 3))]))
        families.append(Family("archive_metrics_collection_seconds", "gauge",
                               "How long collecting the archive's counts took.",
                               [("", {}, round(time.monotonic() - started, 3))]))
        with self.lock:
            self.families = families
        self.collected.set()

    def _store_bytes(self) -> dict[str, int]:
        sizes: dict[str, int] = {}
        git = self.archive_root / ".git"
        for name, root in (("records", self.archive_root / "records"), ("content", git / "annex" / "objects"),
                           ("world", self.world_store), ("staging", self.staging)):
            size = tree_bytes(root)
            if size is not None:
                sizes[name] = size
            elif name == "content" and git.is_dir():
                sizes[name] = 0  # an archive with no content held here
        sizes.update(derived_bytes(self.media_root, self.bank_root))
        return sizes

    def render(self, own: list[Family]) -> str:
        """The relay's own families, the latest counts, and the request durations."""
        self.start()
        with self.lock:
            families = list(self.families)
        return exposition([*own, *families, self.requests.family()])


def _log(message: str) -> None:
    sys.stderr.write(f"relay: metrics: {message}\n")


def _part(what: str, work: Callable[[], Any]) -> Any:
    try:
        return work()
    except Exception as exc:  # noqa: BLE001 - one part's failure leaves the others
        _log(f"{what} could not be counted: {type(exc).__name__}: {exc}")
        return None


def _world_families(counts: dict[str, Any]) -> list[Family]:
    return [
        Family("archive_records", "gauge", "Record revisions in World's selected generation, by shape.",
               [("", {"shape": shape}, n) for shape, n in sorted(counts["shapes"].items())]),
        Family("archive_records_by_contract", "gauge", "Record revisions citing each contract, any version.",
               [("", {"contract": contract}, n) for contract, n in sorted(counts["contracts"].items())]),
        Family("archive_derivation_runs", "gauge", "Derivation runs recorded in the archive, by method.",
               [("", {"method": method}, n) for method, n in sorted(counts["methods"].items())]),
    ]


def _bank_families(found: BankCounts) -> list[Family]:
    extractors = sorted(set(found.made) | set(found.failed))
    return [
        Family("archive_bank_entries", "gauge",
               "Feature-bank entries made, and outstanding failure records, by extractor.",
               [("", {"extractor": e, "outcome": o}, n) for e in extractors
                for o, n in (("made", found.made.get(e, 0)), ("failed", found.failed.get(e, 0)))]),
        Family("archive_bank_entries_outdated", "gauge",
               "Feature-bank entries made by a version other than the installed one, by extractor.",
               [("", {"extractor": e}, found.outdated.get(e, 0)) for e in sorted(found.made)]),
        Family("archive_bank_entries_unreadable", "gauge", "Feature-bank entries whose manifest could not be read.",
               [("", {}, found.unreadable)]),
        Family("archive_bank_requests", "gauge", "Bank requests waiting for the scheduler.",
               [("", {}, found.requests)]),
    ]


def _coverage_families(audio: list[str], counts: dict[str, Any], media: MediaCounts | None,
                       with_stems: set[str] | None) -> list[Family]:
    rows = [("lead_sheet", counts["works_with_lead_sheet"], counts["works"]),
            ("cover_match", counts["occurrences_matched"], counts["occurrences"])]
    if with_stems is not None:
        rows.append(("stems", len(with_stems & set(audio)), len(audio)))
    if media is not None:
        rows.append(("practice_render", len(media.with_practice), len(audio)))
    return [Family("archive_coverage", "gauge",
                   "Audio Artifacts with stems and practice renders, musical Works with a lead sheet, and song "
                   "occurrences with a cover match: covered or uncovered.",
                   [("", {"feature": feature, "state": state}, n) for feature, covered, total in sorted(rows)
                    for state, n in (("covered", covered), ("uncovered", total - covered))])]
