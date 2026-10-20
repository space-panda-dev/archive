"""`enrich backfill`: fill the feature bank for every recording, and propose
what it holds (ADR 0112; ADR 0090 for the Spool fan-out).

    enrich.py backfill --archive <dir> --bank <dir> [--config <file>]
        [--mode songs|eager|on-demand | --songs | --eager | --on-demand] [--cores n] [--memory-gb n]
        [--spool "<spool> --dir <dir>"] [--commit] [--retry-failed]
        [--proposers a,b,...] [--world-store <dir> [--world-python <python>]
        [--conformance-python <python>]] [--log <file>]
        [--door door] [--archive-command archive] [--staging <dir>] [--ffmpeg ..] [--ffprobe ..]

A dry run unless `--commit`: it probes nothing, runs nothing, and prints the
plan, what each recording still needs, and the projected cost from the
recorded costs. With `--commit`, in order:

1. `enrich probe` every audio or video Artifact `enrich propose --all` lists
   as unprobed (one `audio_stream` claim each, through Door);
2. plan: for every Artifact with an audio stream claim, the units of the
   configuration (an extractor on the content, or on a file feature of it
   with `--from`) whose entries the bank lacks, read through `bank.Bank` as
   every reader reads them (an entry made on another device counts once a
   passing equivalence record lets it stand in, ADR 0090's amendment); and
   every bank request (`requests/`, ADR 0112), first; the bank is the
   ledger, and nothing else is kept;
3. run them: many recordings at once, each extractor in copies of one or two
   threads (`layout`), as many as the cores and memory allow; a unit routed
   to Spool (`"route": "spool"`, first `stems`) is put with `remote.py put`
   and collected as results arrive, and what measures its output waits for
   the collect; a task the spool failed for good, or could not put, runs
   here;
4. `enrich propose --all` (dry run, commit, and a rerun that must answer
   unchanged);
5. with `--world-store`, an incremental World refresh and conformance.

Modes (ADR 0112; `regions.plan` decides). `eager`: every unit over every
recording, whole. `songs` (the default): a cheap unit runs whole; an
expensive unit (recorded cost above `expensive_above` seconds per
audio-second) runs over the recording's song regions (`regions.regions_for`:
song occurrences, else selections, else the detector's `music` spans, else
`structure/playing` from the bank; widened, snapped and merged) as region
entries (one region that is the whole recording: whole), and where there
are none it is left for on demand; a unit on a
file feature follows that feature's maker (whole where it is made whole).
`on-demand`: only the bank requests. Requests are served in every mode.
A unit routed to Spool runs whole only: a task carries no region yet.

With `remote_parameters` on a routed extractor (`stems` with
`apply.device` `mps` on the Mac), the worker's entries have another key and
count here as stand-ins; the backfill refuses to start unless the bank holds
a passing equivalence record for that pair, and runs here any recording
whose worker entry does not stand in (other software than the record's).

Resumable: each entry is the runner's own (lock, check, rename), so a
killed backfill loses at most the runs in flight, and a rerun plans only
what is missing. A failure is recorded beside where the entry would be and
is not retried without `--retry-failed`. A rerun over an unchanged archive
and bank runs nothing.

On the x86-64 laptop, run it as a low-priority user unit, so the machine
stays usable (see docs/shadow-runbook.md):

    systemd-run --user --unit=archive-backfill -p Nice=10 -p IOSchedulingClass=idle \\
        -p CPUWeight=20 <python> enrich/enrich.py backfill ... --commit --log backfill.jsonl

Stopping the unit stops every run in it (the unit's control group); a rerun
continues. One JSON line per event goes to standard output and to `--log`;
the last is `done`. It exits 0 when nothing failed, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import adapter  # noqa: E402
import bank  # noqa: E402
import extract  # noqa: E402
import regions  # noqa: E402
import remote  # noqa: E402
from regions import Region  # noqa: E402

STREAM = "archive.claim/audio_stream"
MODES = regions.MODES  # songs, eager, on-demand
# The 0.0.10 backfill's set; not timed-lyrics, which answers not_yet.
PROPOSERS = "chords-jiang,chords-chordino,beats,bass,key,tuning,notes,bass-line,sections"

# What the 0.0.10 backfill made of every recording: an extractor on the
# content, or (a second element) on a file feature of it.
# f0-pesto's measure of the vocal stem comes before melody, whose probe reads
# it (ADR 0076's second amendment); `unit_order` keeps that for any configuration.
UNITS = (("stems",), ("rhythm",), ("tonal",), ("chords-jiang",), ("chords-chordino",), ("structure",),
         ("f0-pesto", "stems/vocals"), ("melody",), ("tonal", "stems/bass"), ("f0-pesto", "stems/bass"),
         ("notes-basic-pitch", "stems/bass"))

# Per extractor: threads per copy, memory per copy, and the recorded cost in
# seconds per audio-second on the x86-64 laptop (the backfill profile of
# 2026-10-19, run serially; until the bench-cost suite records them).
# Several narrow copies beat one wide one (1.3-5.7x), so one thread each,
# and two for stems (4 x 2 beat 8 x 1 there).
SETTINGS: dict[str, dict[str, Any]] = {
    "stems": {"threads": 2, "memory_gb": 3.0, "cost": 0.576, "remote_cost": 0.100},
    "melody": {"cost": 0.079},
    "f0-pesto": {"cost": 0.062, "memory_gb": 1.5},
    "chords-jiang": {"cost": 0.060, "memory_gb": 2.0},
    "notes-basic-pitch": {"cost": 0.045, "memory_gb": 1.5},
    "rhythm": {"cost": 0.031},
    "chords-chordino": {"cost": 0.027},
    "tonal": {"cost": 0.022},
    "structure": {"cost": 0.001},
}
DEFAULT_SETTING = {"threads": 1, "memory_gb": 1.0, "cost": None, "remote_cost": None, "route": None,
                   "remote_parameters": None}
SCHEDULE = {"expensive_above": regions.EXPENSIVE_THRESHOLD, "parallel_gain": 1.9, "poll_seconds": 30.0,
            "memory_reserve_gb": 2.0, "local_when_away_seconds": {"songs": None, "eager": 900, "on-demand": None}}
THREAD_VARIABLES = ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS",
                    "VECLIB_MAXIMUM_THREADS", "NUMBA_NUM_THREADS")


class BackfillError(Exception):
    pass


# -- the configuration ------------------------------------------------------------

@dataclass(frozen=True)
class Unit:
    extractor: str
    source: str | None = None  # a file feature (`stems/bass`), measured as content

    @property
    def name(self) -> str:
        return self.extractor + (f" --from {self.source}" if self.source else "")

    @property
    def producer(self) -> str | None:
        return self.source.split("/", 1)[0] if self.source else None


@dataclass
class Schedule:
    units: list[Unit]
    settings: dict[str, dict[str, Any]]
    expensive_above: float
    parallel_gain: float
    poll_seconds: float
    memory_reserve_gb: float
    local_when_away_seconds: dict[str, float | None]

    def setting(self, extractor_id: str) -> dict[str, Any]:
        return {**DEFAULT_SETTING, **self.settings.get(extractor_id, {})}

    def costs(self) -> dict[str, float]:
        """The recorded cost of each extractor that has one."""
        return {name: float(value["cost"]) for name, value in self.settings.items() if value.get("cost") is not None}

    def expensive(self, extractor_id: str) -> bool:
        return regions.expensive(extractor_id, self.costs(), self.expensive_above)


def schedule_of(config: dict[str, Any], catalog: bank.Catalog) -> Schedule:
    """The configuration's `backfill` object over the defaults: `units` (a
    list of [extractor] or [extractor, feature]), `extractors` (per extractor
    `threads`, `memory_gb`, `cost`, `remote_cost`, `route`), and the
    schedule's numbers. Unknown names are refused, not ignored."""
    given = config.get("backfill", {})
    if not isinstance(given, dict):
        raise BackfillError("the configuration's backfill is an object")
    unknown = set(given) - {"units", "extractors", *SCHEDULE}
    if unknown:
        raise BackfillError(f"the configuration's backfill names what it does not know: {sorted(unknown)}")
    units = []
    for item in given.get("units", UNITS):
        if not (isinstance(item, (list, tuple)) and 1 <= len(item) <= 2 and all(isinstance(w, str) for w in item)):
            raise BackfillError(f"a backfill unit is [extractor] or [extractor, feature], not {item!r}")
        unit = Unit(*item)
        catalog[unit.extractor]
        if unit.source is not None:
            match = bank.FEATURE.fullmatch(unit.source)
            if match is None or catalog[match.group(1)].outputs.get(match.group(2), {}).get("kind") != "file":
                raise BackfillError(f"{unit.name}: only a file feature can be measured as content")
        units.append(unit)
    settings = {name: dict(value) for name, value in SETTINGS.items()}
    for name, value in given.get("extractors", {}).items():
        catalog[name]
        if not isinstance(value, dict) or set(value) - set(DEFAULT_SETTING):
            raise BackfillError(f"backfill extractor {name}: only {sorted(DEFAULT_SETTING)}")
        if value.get("route") not in (None, "spool"):
            raise BackfillError(f"backfill extractor {name}: route is \"spool\" or null")
        if value.get("remote_parameters") is not None and not isinstance(value["remote_parameters"], dict):
            raise BackfillError(f"backfill extractor {name}: remote_parameters is an object of its parameters")
        settings[name] = {**settings.get(name, {}), **value}
    numbers = {**SCHEDULE, **{k: v for k, v in given.items() if k in SCHEDULE}}
    away = numbers["local_when_away_seconds"]
    if not isinstance(away, dict):
        away = {mode: away for mode in MODES}
    return Schedule(units, settings, float(numbers["expensive_above"]), float(numbers["parallel_gain"]),
                    float(numbers["poll_seconds"]), float(numbers["memory_reserve_gb"]),
                    {mode: away.get(mode) for mode in MODES})


def width_of(catalog: bank.Catalog, extractor_id: str, schedule: Schedule,
             parameters: dict[str, Any]) -> int:
    """Threads one copy uses: the extractor's own fixed count where its
    parameters fix one (chords-jiang's 3), else the configured count."""
    fixed = catalog.parameters(extractor_id, parameters).get("threads")
    if isinstance(fixed, int) and not isinstance(fixed, bool) and fixed > 0:
        return fixed
    return int(schedule.setting(extractor_id)["threads"])


def copies(cores: int, memory_gb: float, threads: int, memory_per_copy_gb: float) -> int:
    """How many copies of one extractor run at once: as many as the cores
    hold at `threads` each, bounded by memory, and at least one."""
    by_cores = cores // max(1, threads)
    by_memory = math.floor(memory_gb / memory_per_copy_gb) if memory_per_copy_gb > 0 else by_cores
    return max(1, min(by_cores, by_memory))


def layout(catalog: bank.Catalog, schedule: Schedule, parameters: dict[str, Any], cores: int,
           memory_gb: float) -> dict[str, dict[str, Any]]:
    """Per extractor of the units: threads per copy and copies at once."""
    answer = {}
    for extractor_id in sorted({unit.extractor for unit in schedule.units}):
        threads = min(cores, width_of(catalog, extractor_id, schedule, parameters))
        memory = float(schedule.setting(extractor_id)["memory_gb"])
        answer[extractor_id] = {"threads": threads, "memory_gb": memory,
                                "copies": copies(cores, memory_gb, threads, memory)}
    return answer


def thread_environment(threads: int) -> dict[str, str]:
    """The variables that hold a copy's numeric libraries to its threads."""
    return {name: str(threads) for name in THREAD_VARIABLES}


def machine_memory_gb() -> float:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2 ** 30
    except (ValueError, OSError, AttributeError):
        return 8.0


# -- the archive's recordings -------------------------------------------------------

@dataclass
class Recording:
    object_id: str
    digest: str
    path: Path
    seconds: float | None  # from the audio_stream claim; None while unprobed
    songs: bool  # whether it has song regions (`regions.regions_for`)
    media_type: str | None = None
    regions: list[Region] = field(default_factory=list)
    span_source: str = "none"  # which source its song spans came from
    length: int | None = None  # samples of its first audio stream at the regions' rate


def heads(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    superseded = {r["revision_id"] for x in records for r in x.get("supersedes", [])}
    return {x["object_id"]: x for x in records if x["revision_id"] not in superseded}


def cites(record: dict[str, Any], contract: str) -> bool:
    return any(c.get("id") == contract for c in record.get("contracts", []))


def content_path(archive: Path, digest: str) -> Path:
    hexdigest = digest.removeprefix("sha256:")
    return archive / "content" / "sha256" / hexdigest[:2] / hexdigest[2:4] / hexdigest


def recordings_of(records: list[dict[str, Any]], archive: Path, unprobed: set[str]) -> list[Recording]:
    """Every current Artifact with an audio stream claim, and those listed
    as unprobed: as the 0.0.10 backfill found them."""
    current = heads(records)
    seconds: dict[str, float | None] = {}
    for record in current.values():
        if record["shape"] == "claim" and cites(record, STREAM):
            payload = record.get("payload", {})
            for ref in record.get("refs", []):
                if ref.get("role") == "artifact" and ref["id"] not in seconds:
                    rate, count = payload.get("sample_rate"), payload.get("sample_count")
                    seconds[ref["id"]] = count / rate if isinstance(rate, int) and isinstance(count, int) \
                        and rate > 0 else None
    found = []
    for record in current.values():
        if record["shape"] != "artifact" or not (record["object_id"] in seconds or record["object_id"] in unprobed):
            continue
        digest = record["payload"]["content"]["digest"]
        found.append(Recording(record["object_id"], digest, content_path(archive, digest),
                               seconds.get(record["object_id"]), False))
    return found


def find_regions(recordings: list[Recording], export: dict[str, Any], archive: Path, reader: bank.Bank,
                 note: Callable[..., None]) -> None:
    """Each recording's song regions by `regions.regions_for`'s precedence,
    with `structure/playing` from the bank as the last source. A recording
    the export cannot place (no audio stream claim yet: a dry run before
    its probe) has none, and says why."""
    records = adapter.ArchiveReader.from_export(archive, export)
    for recording in recordings:
        artifact = records.heads.get(recording.object_id)
        try:
            playing = None
            structure = reader.entry(recording.digest, "structure") if "structure" in reader.catalog.extractors \
                else None
            if structure is not None and "playing" in structure.manifest["outputs"]:
                playing = structure.items("playing")
            found = regions.regions_for(records, artifact, playing)
        except (regions.RegionError, bank.BankError, KeyError, TypeError) as exc:
            note(step="no song regions", recording=recording.object_id, error=str(exc)[-300:])
            continue
        recording.regions, recording.span_source = found["regions"], found["source"]
        recording.length = found["length"]
        recording.songs = bool(found["regions"])


# -- what a unit still needs ---------------------------------------------------------

DONE, TODO, WAITING, FAILED, SKIPPED, UNCONFIGURED, DAMAGED = (
    "done", "todo", "waiting", "failed_before", "skipped", "unconfigured", "damaged")


class Planner:
    """Reads the bank as every reader does (`bank.Bank`), so what it counts
    as made is what a reader would find: an entry another device made
    counts once a passing equivalence record lets it stand in (ADR 0090's
    amendment), and a region entry counts over its region (ADR 0112)."""

    def __init__(self, catalog: bank.Catalog, root: Path, parameters: dict[str, Any], programs: dict[str, str],
                 retry_failed: bool = False):
        self.catalog, self.root, self.parameters, self.programs = catalog, root, parameters, programs
        self.reader = bank.Bank(root, catalog, parameters)
        self.retry_failed = retry_failed

    def view(self, recording: Recording, region: Region | None) -> bank.Bank:
        """The reader that keys this recording over the region, or whole."""
        if region is None:
            return self.reader
        return bank.Bank(self.root, self.catalog.within(recording.digest, region.as_json()), self.parameters)

    def state(self, recording: Recording, unit: Unit, region: Region | None = None) -> tuple[str, str | None]:
        """Done, to do, waiting for its input feature, failed before, not
        applicable, not configured here, or damaged; with a detail. Over a
        region, the whole entry, or a region entry of a wider region,
        counts as done (`bank.covering`)."""
        extractor = self.catalog[unit.extractor]
        order = self.catalog.order([unit.extractor])
        for extractor_id in order:
            missing = extract.unconfigured(self.catalog[extractor_id], self.programs)
            if missing:
                return UNCONFIGURED, f"${{{missing}}} is not configured"
        if recording.media_type and unit.source is None and not extractor.accepts(recording.media_type):
            return SKIPPED, f"does not accept {recording.media_type}"
        reader = self.view(recording, region)
        try:
            if region is not None and unit.source is None and not extractor.prefers:
                if bank.covering(self.reader, recording.digest, unit.extractor, region.start, region.end,
                                 region.rate) is not None:
                    return DONE, None
            elif region is not None and self.state(recording, unit)[0] == DONE:
                return DONE, None
            feature = unit.source
            if feature is None and extractor.prefers:
                feature = reader.default_input(recording.digest, unit.extractor)
            if feature is None:
                if reader.entry(recording.digest, unit.extractor) is not None:
                    return DONE, None
                keys = reader.catalog.keys(recording.digest, order, self.parameters)
            else:
                found = reader.input_of(recording.digest, feature)
                if found is None:
                    return WAITING, f"{feature} is not in the bank" + (" over this region" if region else "")
                if reader.entry_from(recording.digest, feature, unit.extractor) is not None:
                    return DONE, None
                keys = reader.catalog.keys(found[1], order, self.catalog.overrides_for(feature, self.parameters))
        except bank.BankError as exc:
            return DAMAGED, str(exc)
        if not self.retry_failed:
            for extractor_id in order:
                if bank.failure_path(self.root, recording.digest, extractor_id, keys[extractor_id]).exists():
                    return FAILED, f"{extractor_id} failed before (see its failure record; --retry-failed)"
        return TODO, None


def probe_units(catalog: bank.Catalog, unit: Unit) -> list[Unit]:
    """The units whose measures a unit's preferences probe (ADR 0076's
    second amendment): melody's probe reads f0-pesto's measure of the vocal
    stem, so that measure is made first, or the probe saves nothing."""
    if unit.source is not None:
        return []
    return [Unit(p["probe"]["series"].split("/", 1)[0], p["input"])
            for p in catalog[unit.extractor].prefers if p.get("probe")]


def unit_order(units: list[Unit], catalog: bank.Catalog) -> list[Unit]:
    """The units, each probe's unit moved ahead of the first unit that probes with it."""
    ordered = list(units)
    for unit in list(ordered):
        for probe in probe_units(catalog, unit):
            if probe in ordered and ordered.index(probe) > ordered.index(unit):
                ordered.remove(probe)
                ordered.insert(ordered.index(unit), probe)
    return ordered


# -- the plan ------------------------------------------------------------------------

@dataclass(eq=False)
class Job:
    recording: Recording
    unit: Unit
    order: int
    threads: int = 1
    memory_gb: float = 1.0
    routed: bool = False
    after: list["Job"] = field(default_factory=list)
    dependents: int = 0
    status: str = "pending"  # pending, put, running, done, failed, blocked
    detail: str | None = None
    since: float = 0.0
    cpu_s: float = 0.0
    wall_s: float = 0.0
    region: Region | None = None  # made over this region of the recording, or whole
    requested: bool = False  # asked for by a bank request

    @property
    def seconds(self) -> float | None:
        """The audio this job measures: its region, or the whole recording."""
        if self.region is not None:
            return (self.region.end - self.region.start) / self.region.rate
        return self.recording.seconds

    def describe(self) -> dict[str, Any]:
        return {"recording": self.recording.object_id, "content": self.recording.digest[7:19],
                "unit": self.unit.name, **({"region": self.region.as_json()} if self.region else {})}


@dataclass
class Plan:
    jobs: list[Job]
    deferred: list[tuple[Recording, Unit, str]]
    settled: dict[str, int]  # done, skipped, failed_before, ... counts
    held: list[tuple[Recording, Unit, str, str | None]]  # failed before, unconfigured, damaged, blocked
    unplaced: list[tuple[str, str]] = field(default_factory=list)  # a request's key, and why it is not planned


def plan(recordings: list[Recording], schedule: Schedule, planner: Planner, mode: str,
         shape: dict[str, dict[str, Any]], spool: bool,
         asked: list[dict[str, Any]] | None = None) -> Plan:
    """The jobs the mode asks for that the bank lacks, and the bank requests
    (`asked`) in every mode, first; then song recordings first in `songs`
    mode. What each extractor is made over is `regions.plan`'s: whole, a
    recording's song regions, or nothing until asked for. A unit on a file
    feature goes where that feature's maker goes (whole where it is already
    made whole). Each job waits for the job in this plan that makes the
    feature it measures, the feature it would prefer, or the measure its
    preference probes, over the same region."""
    if mode not in MODES:
        raise BackfillError(f"mode is one of {', '.join(MODES)}, not {mode!r}")
    units = unit_order(schedule.units, planner.catalog)
    known = {u.extractor for u in units} | {u.producer for u in units if u.producer}
    costs = schedule.costs()
    jobs: list[Job] = []
    deferred: list[tuple[Recording, Unit, str]] = []
    held: list[tuple[Recording, Unit, str, str | None]] = []
    unplaced: list[tuple[str, str]] = []
    settled: dict[str, int] = {}
    planned: dict[tuple[str, Unit, Region | None], Job] = {}
    seen: set[tuple[str, Unit, Region | None]] = set()

    def want(recording: Recording, unit: Unit, region: Region | None, requested: bool = False,
             state: tuple[str, str | None] | None = None) -> Job | None:
        mark = (recording.object_id, unit, region)
        if mark in planned:
            return planned[mark]
        if mark in seen:
            return None
        seen.add(mark)
        status, detail = state or planner.state(recording, unit, region)
        if status in (DONE, SKIPPED):
            settled[status] = settled.get(status, 0) + 1
            return None
        if status in (FAILED, UNCONFIGURED, DAMAGED):
            held.append((recording, unit, status, detail))
            return None
        catalog = planner.catalog
        after: list[Job] = []
        if unit.producer is not None:
            if status == WAITING and requested:
                # A request for a measure of a feature asks for the feature too.
                made = want(recording, Unit(unit.producer), region, True)
                after += [made] if made is not None else []
            else:
                after += [planned[k] for k in [(recording.object_id, Unit(unit.producer), region)] if k in planned]
        else:
            producers = [p["input"].split("/", 1)[0] for p in catalog[unit.extractor].prefers] \
                + catalog.upstream(unit.extractor)
            after += [planned[k] for k in [(recording.object_id, Unit(p), region) for p in producers]
                      if k in planned]
            after += [planned[k] for k in [(recording.object_id, probe, region)
                                           for probe in probe_units(catalog, unit)] if k in planned]
        if status == WAITING and not after:
            held.append((recording, unit, "blocked", detail))
            return None
        setting = schedule.setting(unit.extractor)
        job = Job(recording, unit, len(jobs), threads=shape[unit.extractor]["threads"],
                  memory_gb=shape[unit.extractor]["memory_gb"], after=after, region=region, requested=requested,
                  # a Spool task carries no region yet (ADR 0112): a region job runs here
                  routed=bool(spool and setting["route"] == "spool" and unit.source is None and region is None
                              and not catalog[unit.extractor].needs))
        for producer in after:
            producer.dependents += 1
        planned[mark] = job
        jobs.append(job)
        return job

    by_digest = {r.digest: r for r in recordings}
    for request in asked or []:
        recording = by_digest.get(request["content"])
        installed = planner.catalog.extractors.get(request["extractor"]["id"])
        if recording is None:
            unplaced.append((request["key"], "its content is not a recording here"))
        elif installed is None or installed.version != request["extractor"]["version"]:
            unplaced.append((request["key"], f"asks for {request['extractor']['id']} "
                                             f"{request['extractor']['version']}, which is not installed"))
        else:
            want(recording, Unit(installed.id, request["feature"]),
                 None if request["region"] is None else Region(**request["region"]), True)

    ordered = sorted(recordings, key=lambda r: (not r.songs,)) if mode == "songs" else list(recordings)
    for recording in ordered:
        choice = regions.plan(mode, known, recording.regions, costs, schedule.expensive_above, recording.length)
        for unit in units:
            whole = planner.state(recording, unit)
            if whole[0] in (DONE, SKIPPED, UNCONFIGURED, DAMAGED):
                want(recording, unit, None, state=whole)
                continue
            over = choice[unit.extractor]
            if unit.producer is not None and over is not None:
                # Where its feature is made: whole once made whole, else as its maker is made.
                over = "whole" if whole[0] != WAITING else choice[unit.producer]
            if over is None:
                why = ("on-demand mode: made when asked" if mode == "on-demand"
                       else "expensive, and the recording has no song regions: on demand"
                       if choice[unit.extractor] is None
                       else f"measures {unit.source}, which is left for on demand")
                deferred.append((recording, unit, why))
                continue
            if over == "whole":
                want(recording, unit, None, state=whole)
            else:
                for region in over:
                    want(recording, unit, region)
    return Plan(jobs, deferred, settled, held, unplaced)


def projection(jobs: list[Job], schedule: Schedule) -> dict[str, Any]:
    """The plan's cost from the recorded costs: compute here, on the worker,
    and the wall time with parallel copies."""
    local = remote_s = audio = 0.0
    unmeasured: set[str] = set()
    unknown_length = 0
    recordings: dict[str, float | None] = {}
    for job in jobs:
        seconds = job.seconds
        recordings[job.recording.object_id] = job.recording.seconds
        setting = schedule.setting(job.unit.extractor)
        if seconds is None:
            continue
        if job.routed and setting["remote_cost"] is not None:
            remote_s += setting["remote_cost"] * seconds
        elif setting["cost"] is not None:
            local += setting["cost"] * seconds
        else:
            unmeasured.add(job.unit.extractor)
    for seconds in recordings.values():
        if seconds is None:
            unknown_length += 1
        else:
            audio += seconds
    wall = max(local / schedule.parallel_gain, remote_s)
    return {"audio_hours": round(audio / 3600, 2), "compute_hours_here": round(local / 3600, 2),
            "compute_hours_on_workers": round(remote_s / 3600, 2), "projected_wall_hours": round(wall / 3600, 2),
            "projected_days": round(wall / 86400, 2),
            "seconds_per_audio_second": round(wall / audio, 3) if audio else None,
            "recordings_of_unknown_length": unknown_length, "unmeasured": sorted(unmeasured)}


# -- running the plan ----------------------------------------------------------------

def run_extract(job: Job, root: Path, config: str | None, ffmpeg: str | None = None
                ) -> tuple[dict[str, Any], float]:
    """`extract.py run` of one unit in a process of its own, held to the
    job's threads; its answer and the CPU seconds it and its children took."""
    command = [sys.executable, str(HERE / "extract.py"), "run", job.unit.extractor,
               *(["--from", job.unit.source] if job.unit.source else []),
               *(["--region", f"{job.region.start}:{job.region.end}:{job.region.rate}"] if job.region else []),
               *(["--program", f"ffmpeg={ffmpeg}"] if job.region and ffmpeg else []),
               "--content", str(job.recording.path), "--digest", job.recording.digest,
               "--bank", str(root), "--jobs", "1", *(["--config", config] if config else [])]
    environment = {**os.environ, **thread_environment(job.threads)}
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        process = subprocess.Popen(command, stdout=out, stderr=err, stdin=subprocess.DEVNULL, env=environment)
        _, status, usage = os.wait4(process.pid, 0)
        process.returncode = os.waitstatus_to_exitcode(status)
        out.seek(0)
        err.seek(0)
        text, errors = out.read().decode("utf-8", "replace"), err.read().decode("utf-8", "replace")
    try:
        answer = json.loads(text or errors)
    except ValueError:
        answer = {"status": "failed", "detail": (text + errors)[-1500:]}
    if process.returncode != 0 and answer.get("status") == "ok":
        answer["status"] = "failed"
    return answer, usage.ru_utime + usage.ru_stime


def first_error(answer: dict[str, Any]) -> str | None:
    if answer.get("detail"):
        return str(answer["detail"])[-600:]
    for name, outcome in answer.get("extractors", {}).items():
        if outcome.get("status") in ("failed", "blocked"):
            return f"{name}: {outcome.get('error') or outcome.get('reason')}"[-600:]
    return None


def remote_parameters(parameters: dict[str, Any], extractor_id: str, schedule: Schedule) -> dict[str, Any]:
    """The parameters a unit routed to Spool is put with: the configured
    ones, with its `remote_parameters` (the Mac's `apply` with device mps)."""
    given = schedule.setting(extractor_id)["remote_parameters"]
    if not given:
        return parameters
    return {**parameters, extractor_id: {**parameters.get(extractor_id, {}), **given}}


def check_remote(catalog: bank.Catalog, root: Path, parameters: dict[str, Any], schedule: Schedule) -> None:
    """A unit put with other parameters than the configured ones makes
    entries under another key, which count here only as stand-ins (ADR
    0090's amendment). So the other parameters must differ only in the
    extractor's interchangeable parameter, and the bank must hold a passing
    equivalence record for the pair; otherwise every routed recording would
    be made twice, once on the worker and once here. Refused before anything
    runs."""
    for unit in schedule.units:
        setting = schedule.setting(unit.extractor)
        if setting["remote_parameters"] is None or unit.source is not None:
            continue
        if setting["route"] != "spool":
            raise BackfillError(f"backfill extractor {unit.extractor}: remote_parameters needs route \"spool\"")
        extractor = catalog[unit.extractor]
        configured = catalog.parameters(unit.extractor, parameters)
        other = catalog.parameters(unit.extractor, remote_parameters(parameters, unit.extractor, schedule))
        if other == configured:
            continue
        if other not in [alternative for _, alternative in catalog.alternatives(unit.extractor, parameters)]:
            raise BackfillError(f"{unit.extractor}'s remote_parameters differ from its configured parameters in "
                                f"more than its interchangeable parameter, so what a worker makes could never "
                                f"stand in here")
        directory = bank.equivalence_directory(root, extractor.id,
                                               bank.pair_digest(extractor.id, extractor.version, configured, other))
        if not any(record["pass"] for record in bank.read_equivalences(directory)):
            value = bank.parameter_at(other, extractor.interchangeable["parameter"])
            raise BackfillError(f"{unit.extractor} is routed with {extractor.interchangeable['parameter']} {value}, "
                                f"but the bank holds no passing equivalence record for that pair; measure one "
                                f"first (extract.py equivalence {unit.extractor} --against {value} ...)")


class Runner:
    """A pool of copies over the plan: a job starts when what it waits for
    is done, its extractor has a free copy, and its threads and memory fit;
    the earliest job that does not fit holds the rest, so a wide one is
    never starved by narrow ones."""

    def __init__(self, jobs: list[Job], planner: Planner, schedule: Schedule, shape: dict[str, dict[str, Any]],
                 cores: int, memory_gb: float, mode: str, note: Callable[..., None],
                 run_unit: Callable[[Job], tuple[dict[str, Any], float]],
                 spool: remote.Spool | None = None, config: str | None = None):
        self.jobs, self.planner, self.schedule, self.shape = jobs, planner, schedule, shape
        self.cores, self.memory_gb, self.mode, self.note = cores, memory_gb, mode, note
        self.run_unit, self.spool, self.config = run_unit, spool, config
        self.changed = threading.Condition()
        self.free_cores, self.free_memory = cores, memory_gb
        self.copies: dict[str, int] = {}

    # -- Spool --

    def put_routed(self) -> None:
        for job in self.jobs:
            if not job.routed:
                continue
            try:
                answer = remote.put(self.planner.catalog, self.planner.root, self.spool, job.unit.extractor,
                                    job.recording.path, job.recording.media_type
                                    or extract.media_type_of(job.recording.path), job.recording.digest,
                                    remote_parameters(self.planner.parameters, job.unit.extractor, self.schedule))
            except (remote.RemoteError, bank.BankError, OSError) as exc:
                job.routed = False
                self.note(step="spool put failed; runs here", **job.describe(), error=str(exc)[-600:])
                continue
            job.detail = answer["task_id"]
            if answer["status"] == "hit":
                job.status = "done"
            else:
                job.status, job.since = "put", time.monotonic()
                self.note(step="spool put", **job.describe(), status=answer["status"], task=answer["task_id"])

    def collect(self) -> None:
        try:
            answer = remote.collect(self.planner.catalog, self.planner.root, self.spool)
        except (remote.RemoteError, bank.BankError, OSError) as exc:
            self.note(step="spool collect failed", error=str(exc)[-600:])
            return
        if answer.get("tasks") or answer.get("failed"):
            self.note(step="spool collect", status=answer["status"], failed=answer.get("failed"),
                      tasks={k[:17]: v.get("status") for k, v in answer.get("tasks", {}).items()})
        away = self.schedule.local_when_away_seconds.get(self.mode)
        for job in self.jobs:
            if job.status != "put":
                continue
            if self.planner.state(job.recording, job.unit)[0] == DONE:
                job.status = "done"
                job.wall_s = time.monotonic() - job.since
                self.note(step="filed from the spool", **job.describe(), waited_s=round(job.wall_s, 1))
                continue
            task = job.detail
            refused = (answer.get("tasks") or {}).get(task, {}).get("status") == "refused"
            if self.made_elsewhere(job):
                job.status, job.routed = "pending", False
                self.note(step="made on the worker, but it does not stand in here; runs here", **job.describe(),
                          task=task, detail="no passing equivalence record names the software it was made with")
            elif task in (answer.get("failed") or {}) or refused:
                job.status, job.routed = "pending", False
                self.note(step="the spool gave the task up; runs here", **job.describe(), task=task)
            elif away is not None and time.monotonic() - job.since > away:
                job.status, job.routed = "pending", False
                self.note(step="no worker took the task in time; runs here too", **job.describe(), task=task)

    def made_elsewhere(self, job: Job) -> bool:
        """Whether the worker's entry, under the parameters it was put with,
        is in the bank although it does not count here."""
        parameters = remote_parameters(self.planner.parameters, job.unit.extractor, self.schedule)
        if parameters is self.planner.parameters:
            return False
        exact = bank.Bank(self.planner.root, self.planner.catalog, parameters, exact=True)
        return exact.entry(job.recording.digest, job.unit.extractor) is not None

    # -- the pool --

    def fits(self, job: Job) -> bool:
        copies = self.shape[job.unit.extractor]["copies"]
        return (self.copies.get(job.unit.extractor, 0) < copies and job.threads <= self.free_cores
                and (job.memory_gb <= self.free_memory or self.free_cores == self.cores))

    def start(self, job: Job) -> None:
        job.status, job.since = "running", time.monotonic()
        self.free_cores -= job.threads
        self.free_memory -= job.memory_gb
        self.copies[job.unit.extractor] = self.copies.get(job.unit.extractor, 0) + 1
        threading.Thread(target=self.work, args=(job,), daemon=True).start()

    def work(self, job: Job) -> None:
        try:
            answer, cpu = self.run_unit(job)
        except Exception as exc:  # noqa: BLE001  (the job fails; the backfill goes on)
            answer, cpu = {"status": "failed", "detail": f"{type(exc).__name__}: {exc}"}, 0.0
        state = self.planner.state(job.recording, job.unit, job.region)[0] if answer.get("status") == "ok" else None
        with self.changed:
            job.wall_s, job.cpu_s = time.monotonic() - job.since, cpu
            if state == DONE:
                job.status = "done"
            else:
                job.status = "failed"
                job.detail = first_error(answer) if state is None else f"ran, but the bank has no entry ({state})"
            self.free_cores += job.threads
            self.free_memory += job.memory_gb
            self.copies[job.unit.extractor] -= 1
            self.note(step="ran" if job.status == "done" else "failed", **job.describe(), s=round(job.wall_s, 1), cpu_s=round(cpu, 1),
                      error=job.detail if job.status == "failed" else None)
            self.changed.notify_all()

    def run(self) -> None:
        if self.spool is not None:
            self.put_routed()
        last_collect = time.monotonic()
        with self.changed:
            while True:
                for job in self.jobs:
                    if job.status == "pending" and any(a.status in ("failed", "blocked") for a in job.after):
                        job.status, job.detail = "blocked", "what it measures was not made"
                        self.note(step="blocked", **job.describe())
                ready = sorted((j for j in self.jobs if j.status == "pending"
                                and all(a.status == "done" for a in j.after)),
                               key=lambda j: (j.dependents == 0, j.order))
                for job in ready:
                    if not self.fits(job):
                        if self.copies.get(job.unit.extractor, 0) < self.shape[job.unit.extractor]["copies"]:
                            break  # it waits for cores or memory: nothing behind it jumps ahead
                        continue  # its extractor's copies are all busy: the next may start
                    self.start(job)
                if not any(j.status in ("pending", "put", "running") for j in self.jobs):
                    return
                waiting = [j for j in self.jobs if j.status == "put"]
                if not waiting and not any(j.status == "running" for j in self.jobs):
                    # With nothing running every ready job fits, so a pending one waits on itself.
                    raise BackfillError("nothing can start: the plan waits on itself")
                if waiting and time.monotonic() - last_collect >= self.schedule.poll_seconds:
                    self.changed.release()
                    try:
                        self.collect()
                    finally:
                        self.changed.acquire()
                    last_collect = time.monotonic()
                    continue
                self.changed.wait(timeout=min(1.0, self.schedule.poll_seconds) if waiting else 5.0)


# -- the command ---------------------------------------------------------------------

class Log:
    def __init__(self, path: str | None):
        self.handle = open(path, "a", encoding="utf-8") if path else None
        self.lock = threading.Lock()

    def __call__(self, **entry: Any) -> None:
        entry = {"at": time.strftime("%H:%M:%S"), **{k: v for k, v in entry.items() if v is not None}}
        line = json.dumps(entry, sort_keys=False)
        with self.lock:
            if self.handle:
                self.handle.write(line + "\n")
                self.handle.flush()
            if len(line) > 1000:  # standard output stays one JSON object a line; --log has it whole
                line = json.dumps({k: entry[k] for k in ("at", "step", "recording", "unit", "jobs", "status")
                                   if k in entry} | {"cut": True})
            print(line, flush=True)


def add_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--bank", required=True, help="the feature bank root")
    command.add_argument("--config", help="extract.py's configuration, with an optional backfill object")
    modes = command.add_mutually_exclusive_group()
    modes.add_argument("--mode", choices=MODES, default="songs")
    modes.add_argument("--songs", dest="mode", action="store_const", const="songs")
    modes.add_argument("--eager", dest="mode", action="store_const", const="eager")
    modes.add_argument("--on-demand", dest="mode", action="store_const", const="on-demand")
    command.add_argument("--cores", type=int, help="cores to use (default: all)")
    command.add_argument("--memory-gb", type=float, help="memory to use (default: the machine's, less a reserve)")
    command.add_argument("--spool", help="the command that reaches a spool, for units routed to it")
    command.add_argument("--commit", action="store_true", help="probe, run, and propose (default: a dry run)")
    command.add_argument("--retry-failed", action="store_true", help="run again what failed before")
    command.add_argument("--proposers", default=PROPOSERS, help="for propose --all; empty proposes nothing")
    command.add_argument("--world-store", help="refresh this World store and check its conformance at the end")
    command.add_argument("--world-python", default=sys.executable)
    command.add_argument("--conformance-python")
    command.add_argument("--log", help="append every event, one JSON line each, to this file")


class Steps:
    """The archive-side steps, each `enrich.py` or World in a process of its
    own as the 0.0.10 backfill ran them."""

    def __init__(self, args: argparse.Namespace, note: Callable[..., None]):
        self.args, self.note, self.failures = args, note, 0

    def common(self) -> list[str]:
        a = self.args
        return ["--archive", a.archive, "--door", a.door, "--archive-command", a.archive_command,
                "--ffmpeg", a.ffmpeg, "--ffprobe", a.ffprobe, *(["--staging", a.staging] if a.staging else [])]

    def run(self, label: str, command: list[str], ok: tuple[int, ...] = (0,)) -> Any:
        started = time.monotonic()
        done = subprocess.run(command, capture_output=True, text=True)
        try:
            answer = json.loads(done.stdout) if done.stdout.strip() else None
        except ValueError:
            answer = done.stdout[-1500:]
        good = done.returncode in ok
        self.failures += 0 if good else 1
        brief = answer if not isinstance(answer, dict) or len(json.dumps(answer)) < 3000 else {
            k: answer.get(k) for k in ("status", "detail", "code")}
        self.note(step=label, exit=done.returncode, ok=good, s=round(time.monotonic() - started, 1), answer=brief,
                  stderr=done.stderr[-600:] if not good else None)
        return answer

    def enrich(self, label: str, *words: str, ok: tuple[int, ...] = (0,)) -> Any:
        return self.run(label, [sys.executable, str(HERE / "enrich.py"), *words, *self.common()], ok)

    def propose(self, label: str, *extra: str, proposers: str | None = None) -> Any:
        a = self.args
        return self.enrich(label, "propose", "--all", "--proposers", proposers or a.proposers, "--bank", a.bank,
                           *(["--config", a.config] if a.config else []), *extra, ok=(0, 3))


def export_of(args: argparse.Namespace, env: dict[str, str] | None) -> dict[str, Any]:
    done = subprocess.run([args.archive_command, "export"], cwd=args.archive, capture_output=True, text=True,
                          env=env)
    try:
        answer = json.loads(done.stdout)
    except ValueError:
        answer = None
    if done.returncode != 0 or not isinstance(answer, dict) or answer.get("status") != "ok":
        raise BackfillError(f"archive export failed: {done.stdout[-500:]} {done.stderr[-500:]}")
    return answer


def main(args: argparse.Namespace, env: dict[str, str] | None = None,
         run_unit: Callable[[Job], tuple[dict[str, Any], float]] | None = None) -> int:
    """The command; `env` is Door's environment (`enrich.environment`), and
    `run_unit` replaces `extract.py run` (tests)."""
    if args.commit and args.dry_run:
        raise BackfillError("--commit and --dry-run cannot go together; a backfill is a dry run by default")
    note = Log(args.log)
    started = time.monotonic()
    try:
        config = extract.load_config(args.config)
    except extract.RunError as exc:
        raise BackfillError(str(exc)) from exc
    catalog = extract.catalog_of(argparse.Namespace(extractors=None), config)
    parameters = config.get("parameters", {})
    catalog.check_overrides(parameters, f"configuration {args.config}")
    schedule = schedule_of(config, catalog)
    root = Path(args.bank).resolve()
    if args.spool:
        check_remote(catalog, root, parameters, schedule)
    programs = {"python": sys.executable, "sh": "/bin/sh", **config.get("programs", {})}
    cores = args.cores or os.cpu_count() or 1
    memory_gb = args.memory_gb or max(1.0, machine_memory_gb() - schedule.memory_reserve_gb)
    if cores < 1 or memory_gb <= 0:
        raise BackfillError("--cores and --memory-gb must be positive")
    spool = remote.Spool(args.spool) if args.spool else None
    commit = args.commit
    steps = Steps(args, note)
    note(step="start", mode=args.mode, commit=commit, cores=cores, memory_gb=round(memory_gb, 1), bank=str(root))

    names = args.proposers.strip()
    root.mkdir(parents=True, exist_ok=True)  # an empty bank is a bank; propose refuses a missing one
    # `propose --all` is what lists the unprobed; a failure here is not "none
    # unprobed", so it stops the backfill rather than planning without them.
    before = steps.propose("propose --all (dry run: which Artifacts are unprobed)", "--dry-run",
                           proposers=names or PROPOSERS)
    if not isinstance(before, dict) or before.get("status") == "failed":
        raise BackfillError("propose --all --dry-run failed, so which Artifacts are unprobed is not known")
    unprobed = [u["artifact"]["id"] for u in before.get("unprobed", [])]
    note(step="unprobed audio or video Artifacts", count=len(unprobed))
    for artifact in unprobed:
        steps.enrich(f"probe {artifact}", "probe", "--artifact", artifact, *([] if commit else ["--dry-run"]),
                     ok=(0, 3))

    export = export_of(args, env)
    recordings = recordings_of(export["records"], Path(args.archive), set(unprobed))
    present = []
    for recording in recordings:
        try:
            recording.media_type = extract.media_type_of(recording.path)
            present.append(recording)
        except (OSError, extract.RunError) as exc:
            note(step="content not here; left out", recording=recording.object_id, error=str(exc)[-300:])
    planner = Planner(catalog, root, parameters, programs, args.retry_failed)
    find_regions(present, export, Path(args.archive), planner.reader, note)
    shape = layout(catalog, schedule, parameters, cores, memory_gb)
    # Requests are read in every mode; a damaged one stops the backfill (bank.requests refuses it).
    asked = bank.requests(root)
    planned = plan(present, schedule, planner, args.mode, shape, spool is not None, asked)
    sources: dict[str, int] = {}
    for recording in present:
        sources[recording.span_source] = sources.get(recording.span_source, 0) + 1
    note(step="recordings", count=len(recordings), here=len(present),
         with_songs=sum(1 for r in present if r.songs), song_spans_from=sources, requests=len(asked))
    note(step="layout", extractors=shape)
    by_unit: dict[str, int] = {}
    for job in planned.jobs:
        by_unit[job.unit.name] = by_unit.get(job.unit.name, 0) + 1
    note(step="plan", jobs=len(planned.jobs), by_unit=by_unit, routed=sum(1 for j in planned.jobs if j.routed),
         regions=sum(1 for j in planned.jobs if j.region is not None),
         requested=sum(1 for j in planned.jobs if j.requested), unplaced=len(planned.unplaced),
         already=planned.settled, deferred=len(planned.deferred), held=len(planned.held),
         projection=projection(planned.jobs, schedule))
    for recording in present:
        units = [j.unit.name for j in planned.jobs if j.recording is recording]
        later = [u.name for r, u, _ in planned.deferred if r is recording]
        if units or later or not commit:
            note(step="recording", recording=recording.object_id, content=recording.digest[7:19],
                 seconds=None if recording.seconds is None else round(recording.seconds),
                 songs=recording.songs, span_source=recording.span_source,
                 regions=[r.as_json() for r in recording.regions] or None, runs=units, on_demand=later or None)
    for key, why in planned.unplaced:
        note(step="request not planned", key=key, detail=why)
    for recording, unit, state, detail in planned.held:
        note(step="held", recording=recording.object_id, content=recording.digest[7:19], unit=unit.name,
             state=state, detail=detail)

    failures = sum(1 for _, _, state, _ in planned.held if state in (DAMAGED,))
    if commit and planned.jobs:
        runner = Runner(planned.jobs, planner, schedule, shape, cores, memory_gb, args.mode, note,
                        run_unit or (lambda job: run_extract(job, root, args.config, args.ffmpeg)), spool, args.config)
        made_from = time.monotonic()
        runner.run()
        wall = time.monotonic() - made_from
        failures += sum(1 for j in planned.jobs if j.status in ("failed", "blocked"))
        complete = {j.recording.object_id: j.recording.seconds for j in planned.jobs}  # recordings all of whose jobs ran
        for job in planned.jobs:
            if job.status != "done":
                complete.pop(job.recording.object_id, None)
        audio = sum(s for s in complete.values() if s)
        cost: dict[str, dict[str, float]] = {}
        for job in planned.jobs:
            if job.status == "done" and not job.routed and job.seconds:
                item = cost.setdefault(job.unit.extractor, {"cpu_s": 0.0, "audio_s": 0.0})
                item["cpu_s"] += job.cpu_s
                item["audio_s"] += job.seconds
        note(step="made", wall_s=round(wall, 1), recordings_complete=len(complete),
             audio_seconds_per_wall_second=round(audio / wall, 3) if wall > 0 else None,
             cpu_seconds_per_audio_second={k: round(v["cpu_s"] / v["audio_s"], 4) for k, v in cost.items()})
    if spool is not None and commit:
        # Files what a worker finished after its job ran here instead.
        try:
            collected = remote.collect(catalog, root, spool)
            if collected.get("tasks"):
                note(step="spool collect", status=collected["status"])
        except (remote.RemoteError, bank.BankError, OSError) as exc:
            note(step="spool collect failed", error=str(exc)[-600:])

    if names:
        if commit:
            steps.propose("propose --all (dry run)", "--dry-run")
            steps.propose("propose --all (commit)")
            rerun = steps.propose("propose --all (rerun: must be unchanged)")
            if isinstance(rerun, dict) and rerun.get("status") not in ("unchanged", "nothing_proposed"):
                steps.failures += 1
                note(step="rerun was not quiet", status=rerun.get("status"))
        else:
            steps.propose("propose --all (dry run)", "--dry-run")
    if commit and args.world_store:
        steps.run("World refresh", [args.world_python, str(HERE.parent / "world" / "world.py"), "refresh",
                                    args.archive, "--store", args.world_store, "--archive-command",
                                    args.archive_command])
        steps.run("conformance", [args.conformance_python or args.world_python,
                                  str(HERE.parent / "world" / "conformance.py"), "--store", args.world_store])
    failures += steps.failures
    note(step="done", failures=failures, mode=args.mode, commit=commit, s=round(time.monotonic() - started, 1))
    return 1 if failures else 0
