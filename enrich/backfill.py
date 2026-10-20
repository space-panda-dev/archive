"""`enrich backfill`: fill the feature bank for every recording, and propose
what it holds (ADR 0112; ADR 0090 for the Spool fan-out).

    enrich.py backfill --archive <dir> --bank <dir> [--config <file>]
        [--mode songs|eager|on-demand | --songs | --eager | --on-demand] [--cores n] [--memory-gb n]
        [--spool "<spool> --dir <dir>"] [--commit [--follow [--follow-seconds s]]] [--retry-failed]
        [--proposers a,b,...] [--world-store <dir> [--world-python <python>]
        [--conformance-python <python>]] [--log <file>]
        [--door door] [--archive-command archive] [--staging <dir>] [--ffmpeg ..] [--ffprobe ..]

A dry run unless `--commit`: it probes nothing, runs nothing, and prints the
plan, what each recording still needs, and two projected times from the
recorded costs: until every recording is usable, and until all is made.
With `--commit`, in order:

1. `enrich probe` every audio or video Artifact `enrich propose --all` lists
   as unprobed (one `audio_stream` claim each, through Door);
2. plan: for every Artifact with an audio stream claim, the units of the
   configuration (an extractor on the content, or on a file feature of it
   with `--from`) whose entries the bank lacks, read through `bank.Bank` as
   every reader reads them (an entry made on another device counts once a
   passing equivalence record lets it stand in, ADR 0090's amendment), and
   every bank request (`requests/`); the bank is the ledger;
3. the fast tier: every unit that measures the content itself, prefers no
   stem, and costs at most `fast_above` (by the recorded costs: rhythm,
   tonal, chords-jiang, chords-chordino, structure) over EVERY recording,
   then `enrich propose --all`: from then every recording is usable
   (chords, beats, key, tuning, sections; ADR 0112, "Usable"), the `usable`
   event; with `--world-store`, World is refreshed then too;
4. the background tier (stems and what measures them: f0-pesto, melody,
   tonal and notes on the bass stem): no background job starts before step
   3 is done. In priority order: song recordings, then the most recently
   admitted, then the rest; a bank request goes ahead of everything, in
   either tier, and is removed once its entry is made. Proposals in
   batches as background units land (at most every
   `propose_every_seconds`);
5. `enrich propose --all` (dry run, commit, and a rerun that must answer
   unchanged); with `--world-store`, World refresh and conformance; `done`
   carries `time_to_usable_s` and `time_to_done_s`.

The pool runs many recordings at once, each extractor in copies of one or
two threads (`layout`), each copy reserved the cores it really uses
(`cores`: chordino 1 though its threads parameter is 3, basic-pitch 3 though
held to 1 thread), as many as the cores and memory allow. A unit routed to
Spool (`"route": "spool"`, first `stems`) is put with `remote.py put`, at
most `spool_in_flight` at a time in priority order, and collected as results
arrive; with `overflow`, free cores here take routed work the worker is not
holding. A worker that files nothing within `local_when_away_seconds` (every
mode has one) plus its oldest task's expected time is absent, and its work
is made here. A task the spool failed for good, or could not put, runs here.

`--follow` keeps it running: new bank requests every `poll_seconds`, first
in the queue; new admissions every `rescan_seconds`; until stopped (SIGTERM,
SIGINT) or `--follow-seconds` pass. A request is planned only when the key
this configuration gives what it asks for is the request's key (the relay's
`bank_parameters` must be this configuration's `parameters`); otherwise it
is reported, kept, and not made.

Modes (ADR 0112; `regions.plan` decides). `eager`: every unit over every
recording, whole. `songs` (the default): a cheap unit runs whole; an
expensive unit (recorded cost above `expensive_above` CPU-seconds per
audio-second) runs over the recording's song regions (`regions.regions_for`)
as region entries (one region that is the whole recording: whole), and where
there are none it is left for on demand; a unit on a file feature follows
that feature's maker. `on-demand`: only the bank requests. A unit routed to
Spool runs whole only: a task carries no region yet.

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
import signal
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

# Per extractor: `threads` (what its numeric libraries are held to), `cores`
# (what one copy is reserved on the machine: what it really uses, which is
# not always its threads), memory per copy, and `cost`, the CPU-seconds per
# audio-second of one pass on the x86-64 laptop at those threads: the cost
# gate's baseline (`python -m enrich.bench cost`, 2026-10-20, 120 s clip; the
# costlier pass where a unit runs twice). Under a full backfill the laptop's
# CPU-seconds come out about `load_factor` (1.15) above it. `remote_cost` is
# the worker's end to end seconds per audio-second (the Mac, MPS stems, one
# task at a time with float WAV transfers: 0.277 measured 2026-10-19; two at
# once with WavPack transfers off the home network: 0.223, 2026-10-20).
# chordino's own `threads` parameter is 3 but it uses one core (CPU time =
# wall time); jiang's is 3 and it uses 1.7; basic-pitch is held to 1 thread
# and uses 3.4 (CPU 3.2x wall). The parameters are in the key, so the
# reservation, not the parameter, is fixed here.
SETTINGS: dict[str, dict[str, Any]] = {
    "stems": {"threads": 2, "cores": 2, "memory_gb": 3.0, "cost": 1.445, "remote_cost": 0.223,
              "transport": "wavpack"},
    "f0-pesto": {"cost": 0.186, "memory_gb": 1.5},
    # Jiang holds the whole recording's features: on the whole shadow its
    # copies peaked at 11.4 GB resident on a 93-minute recording (round 2,
    # 2026-10-20), and four 2 GB reservations sent the laptop into swap.
    "chords-jiang": {"cores": 2, "cost": 0.151, "memory_gb": 2.0, "memory_gb_per_hour": 6.0},
    "notes-basic-pitch": {"cores": 3, "cost": 0.107, "memory_gb": 1.5},
    "melody": {"cost": 0.076},
    "chords-chordino": {"cores": 1, "cost": 0.042},
    "rhythm": {"cost": 0.032},
    "tonal": {"cost": 0.028},
    "structure": {"cost": 0.003},
}
# `memory_gb_per_hour`: what a copy holds beyond `memory_gb` per hour of the
# audio it measures (the recording, or its region), reserved per job.
DEFAULT_SETTING = {"threads": 1, "cores": None, "memory_gb": 1.0, "memory_gb_per_hour": 0.0,
                   "cost": None, "remote_cost": None,
                   "route": None, "remote_parameters": None, "tier": None, "transport": None}
# `transport`: the lossless encoding a routed extractor's content travels in
# (`remote.TRANSPORTS`), for an extractor that reads only its first audio
# stream's samples; null sends the content's own bits.
# What one copy takes where the configured parameters run an extractor on
# another device: stems on `cpu-inductor` (the network compiled for the CPU)
# measured 1.04 CPU-seconds per audio-second at one thread on the laptop
# against 1.49 eager at two (720 s, warm compile cache, 2026-10-20), so one
# thread a copy and twice the copies. A configured setting still wins.
DEVICE_SETTINGS: dict[tuple[str, str], dict[str, Any]] = {
    ("stems", "cpu-inductor"): {"threads": 1, "cores": None, "cost": 1.04},  # reserved: its threads
}
TIERS = ("fast", "background")
SCHEDULE = {
    "expensive_above": regions.EXPENSIVE_THRESHOLD,
    # A unit is in the fast tier when it measures no file feature, prefers
    # none, needs only fast units, and costs at most this (ADR 0112, "usable").
    "fast_above": 0.2,
    "load_factor": 1.15, "poll_seconds": 30.0, "memory_reserve_gb": 2.0,
    # How long a routed task may wait with nothing filed from the worker
    # before its work is made here; every mode has one (no infinite wait).
    "local_when_away_seconds": {"songs": 1800, "eager": 900, "on-demand": 600},
    "spool_in_flight": 2,  # routed tasks put at once; the rest wait here, in priority order
    "overflow": True,  # free cores here take routed work the worker is not holding
    "propose_every_seconds": 900.0,  # background proposals, batched
    "rescan_seconds": 600.0,  # --follow: how often new admissions are looked for
}
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
    fast_above: float
    load_factor: float
    poll_seconds: float
    memory_reserve_gb: float
    local_when_away_seconds: dict[str, float]
    spool_in_flight: int = 2
    overflow: bool = True
    propose_every_seconds: float = 900.0
    rescan_seconds: float = 600.0

    def setting(self, extractor_id: str) -> dict[str, Any]:
        return {**DEFAULT_SETTING, **self.settings.get(extractor_id, {})}

    def costs(self) -> dict[str, float]:
        """The recorded cost of each extractor that has one."""
        return {name: float(value["cost"]) for name, value in self.settings.items() if value.get("cost") is not None}

    def expensive(self, extractor_id: str) -> bool:
        return regions.expensive(extractor_id, self.costs(), self.expensive_above)

    def away(self, mode: str) -> float:
        given = self.local_when_away_seconds.get(mode)
        return float(given if given is not None else SCHEDULE["local_when_away_seconds"][mode])

    def tier(self, unit: Unit, catalog: bank.Catalog) -> str:
        """`fast` or `background` (ADR 0112, "usable"): the configured tier,
        else fast when the unit measures the content itself, prefers no
        feature, its needs are fast, and its recorded cost is at most
        `fast_above`; an unmeasured unit is background."""
        given = self.setting(unit.extractor)["tier"]
        if given is not None:
            return given
        extractor = catalog[unit.extractor]
        cost = self.setting(unit.extractor)["cost"]
        if unit.source is not None or extractor.prefers or cost is None or float(cost) > self.fast_above:
            return "background"
        for needed in catalog.upstream(unit.extractor):
            if needed != unit.extractor and self.setting(needed)["cost"] is not None \
                    and self.tier(Unit(needed), catalog) != "fast":
                return "background"
        return "fast"


def schedule_of(config: dict[str, Any], catalog: bank.Catalog) -> Schedule:
    """The configuration's `backfill` object over the defaults: `units` (a
    list of [extractor] or [extractor, feature]), `extractors` (per extractor
    `threads`, `cores`, `memory_gb`, `cost`, `remote_cost`, `route`,
    `remote_parameters`, `tier`), and the schedule's numbers. Unknown names
    are refused, not ignored."""
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
    for (name, device), value in DEVICE_SETTINGS.items():
        if name in catalog.extractors and device_of(catalog.parameters(name, config.get("parameters"))) == device:
            settings[name] = {**settings.get(name, {}), **value}
    for name, value in given.get("extractors", {}).items():
        catalog[name]
        if not isinstance(value, dict) or set(value) - set(DEFAULT_SETTING):
            raise BackfillError(f"backfill extractor {name}: only {sorted(DEFAULT_SETTING)}")
        if value.get("route") not in (None, "spool"):
            raise BackfillError(f"backfill extractor {name}: route is \"spool\" or null")
        if value.get("tier") not in (None, *TIERS):
            raise BackfillError(f"backfill extractor {name}: tier is one of {', '.join(TIERS)}, or null")
        if value.get("remote_parameters") is not None and not isinstance(value["remote_parameters"], dict):
            raise BackfillError(f"backfill extractor {name}: remote_parameters is an object of its parameters")
        if value.get("transport") not in (None, *remote.TRANSPORTS):
            raise BackfillError(f"backfill extractor {name}: transport is one of {sorted(remote.TRANSPORTS)} or null")
        settings[name] = {**settings.get(name, {}), **value}
    numbers = {**SCHEDULE, **{k: v for k, v in given.items() if k in SCHEDULE}}
    away = numbers["local_when_away_seconds"]
    if not isinstance(away, dict):
        away = {mode: away for mode in MODES}
    away = {**SCHEDULE["local_when_away_seconds"], **away}
    if any(not isinstance(away[m], (int, float)) or isinstance(away[m], bool) or away[m] < 0 for m in MODES):
        raise BackfillError("local_when_away_seconds is a number of seconds for every mode: a backfill never "
                            "waits for an absent worker for ever")
    return Schedule(units, settings, float(numbers["expensive_above"]), float(numbers["fast_above"]),
                    float(numbers["load_factor"]), float(numbers["poll_seconds"]),
                    float(numbers["memory_reserve_gb"]), {mode: float(away[mode]) for mode in MODES},
                    int(numbers["spool_in_flight"]), bool(numbers["overflow"]),
                    float(numbers["propose_every_seconds"]), float(numbers["rescan_seconds"]))


def device_of(parameters: dict[str, Any]) -> str | None:
    """The device an extractor's parameters run its model on (`apply.device`)."""
    apply = parameters.get("apply")
    return apply.get("device") if isinstance(apply, dict) else None


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
    hold at `threads` (the cores each copy is reserved) each, bounded by
    memory, and at least one."""
    by_cores = cores // max(1, threads)
    by_memory = math.floor(memory_gb / memory_per_copy_gb) if memory_per_copy_gb > 0 else by_cores
    return max(1, min(by_cores, by_memory))


def layout(catalog: bank.Catalog, schedule: Schedule, parameters: dict[str, Any], cores: int,
           memory_gb: float) -> dict[str, dict[str, Any]]:
    """Per extractor of the units: threads per copy, the cores each copy is
    reserved (its configured `cores`, else its threads), and copies at once."""
    answer = {}
    for extractor_id in sorted({unit.extractor for unit in schedule.units}):
        threads = min(cores, width_of(catalog, extractor_id, schedule, parameters))
        reserved = schedule.setting(extractor_id)["cores"]
        reserved = min(cores, int(reserved)) if reserved is not None else threads
        memory = float(schedule.setting(extractor_id)["memory_gb"])
        answer[extractor_id] = {"threads": threads, "cores": reserved, "memory_gb": memory,
                                "copies": copies(cores, memory_gb, reserved, memory)}
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
    at: str | None = None  # when it was first admitted (its record's `at`)
    priority: int = 0  # its place in the background queue (`prioritise`)


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
    first: dict[str, str] = {}
    for record in records:
        at = record.get("at")
        if isinstance(at, str) and (record["object_id"] not in first or at < first[record["object_id"]]):
            first[record["object_id"]] = at
    found = []
    for record in current.values():
        if record["shape"] != "artifact" or not (record["object_id"] in seconds or record["object_id"] in unprobed):
            continue
        digest = record["payload"]["content"]["digest"]
        found.append(Recording(record["object_id"], digest, content_path(archive, digest),
                               seconds.get(record["object_id"]), False, at=first.get(record["object_id"])))
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

RANKS = {"requested": 0, "fast": 1, "background": 2}


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
    status: str = "pending"  # pending, putting, put, running, done, failed, blocked
    detail: str | None = None
    since: float = 0.0
    cpu_s: float = 0.0
    wall_s: float = 0.0
    region: Region | None = None  # made over this region of the recording, or whole
    requested: bool = False  # asked for by a bank request
    cores: int | None = None  # reserved on the machine; its threads when None
    tier: str = "background"
    requests: list[str] = field(default_factory=list)  # the request keys this job makes good

    @property
    def seconds(self) -> float | None:
        """The audio this job measures: its region, or the whole recording."""
        if self.region is not None:
            return (self.region.end - self.region.start) / self.region.rate
        return self.recording.seconds

    @property
    def reserved(self) -> int:
        return self.cores if self.cores is not None else self.threads

    @property
    def rank(self) -> int:
        """Requests first, then the fast tier, then the background."""
        return RANKS["requested"] if self.requested else RANKS[self.tier]

    def priority(self) -> tuple[Any, ...]:
        """Its place in the queue: rank, then the recording's priority (song
        recordings, then the most recent), then what others wait for first."""
        return self.rank, self.recording.priority, self.dependents == 0, self.order

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
    answered: list[str] = field(default_factory=list)  # request keys whose entry is already made
    planning: "Planning | None" = None


def request_key(planner: Planner, request: dict[str, Any]) -> str:
    """The key this backfill's configuration gives what a request asks for,
    computed as `bank.ask` computes it: when it is the request's key, the
    asker reads with the same parameters, and what is made here answers it."""
    catalog = planner.catalog.within(request["content"], request["region"])
    feature = request["feature"]
    parameters = planner.parameters if feature is None else catalog.overrides_for(feature, planner.parameters)
    extractor_id = request["extractor"]["id"]
    return catalog.keys(request["content"], [extractor_id], parameters)[extractor_id]


class Planning:
    """Builds a plan, and keeps building it: `--follow` adds the requests and
    recordings that arrive while it runs, each planned once."""

    def __init__(self, schedule: Schedule, planner: Planner, mode: str, shape: dict[str, dict[str, Any]],
                 spool: bool):
        if mode not in MODES:
            raise BackfillError(f"mode is one of {', '.join(MODES)}, not {mode!r}")
        self.schedule, self.planner, self.mode, self.shape, self.spool = schedule, planner, mode, shape, spool
        self.units = unit_order(schedule.units, planner.catalog)
        self.known = {u.extractor for u in self.units} | {u.producer for u in self.units if u.producer}
        self.result = Plan([], [], {}, [], planning=self)
        self.planned: dict[tuple[str, Unit, Region | None], Job] = {}
        self.seen: set[tuple[str, Unit, Region | None]] = set()
        self.recordings: set[str] = set()
        self.asked: set[str] = set()

    def promote(self, job: Job) -> None:
        """A request for what is already planned: it and what it waits for jump the queue."""
        job.requested = True
        for producer in job.after:
            if not producer.requested:
                self.promote(producer)

    def want(self, recording: Recording, unit: Unit, region: Region | None, requested: bool = False,
             state: tuple[str, str | None] | None = None) -> Job | None:
        mark = (recording.object_id, unit, region)
        if mark in self.planned:
            job = self.planned[mark]
            if requested and not job.requested:
                self.promote(job)
            return job
        if mark in self.seen:
            return None
        self.seen.add(mark)
        planner, plan_ = self.planner, self.result
        status, detail = state or planner.state(recording, unit, region)
        if status in (DONE, SKIPPED):
            plan_.settled[status] = plan_.settled.get(status, 0) + 1
            return None
        if status in (FAILED, UNCONFIGURED, DAMAGED):
            plan_.held.append((recording, unit, status, detail))
            return None
        catalog = planner.catalog
        after: list[Job] = []
        if unit.producer is not None:
            if status == WAITING and requested:
                # A request for a measure of a feature asks for the feature too.
                made = self.want(recording, Unit(unit.producer), region, True)
                after += [made] if made is not None else []
            else:
                after += [self.planned[k] for k in [(recording.object_id, Unit(unit.producer), region)]
                          if k in self.planned]
        else:
            producers = [p["input"].split("/", 1)[0] for p in catalog[unit.extractor].prefers] \
                + catalog.upstream(unit.extractor)
            after += [self.planned[k] for k in [(recording.object_id, Unit(p), region) for p in producers]
                      if k in self.planned]
            after += [self.planned[k] for k in [(recording.object_id, probe, region)
                                                for probe in probe_units(catalog, unit)] if k in self.planned]
        if status == WAITING and not after:
            plan_.held.append((recording, unit, "blocked", detail))
            return None
        setting = self.schedule.setting(unit.extractor)
        shape = self.shape[unit.extractor]
        job = Job(recording, unit, len(plan_.jobs), threads=shape["threads"], memory_gb=shape["memory_gb"],
                  after=after, region=region, requested=requested, cores=shape.get("cores"),
                  tier=self.schedule.tier(unit, catalog),
                  # a Spool task carries no region yet (ADR 0112): a region job runs here
                  routed=bool(self.spool and setting["route"] == "spool" and unit.source is None
                              and region is None and not catalog[unit.extractor].needs))
        if setting["memory_gb_per_hour"] and job.seconds:
            job.memory_gb = round(job.memory_gb + float(setting["memory_gb_per_hour"]) * job.seconds / 3600, 2)
        if job.tier == "fast" and any(a.tier == "background" for a in after):
            job.tier = "background"  # it waits for background work, so it cannot be part of the fast pass
        for producer in after:
            producer.dependents += 1
        self.planned[mark] = job
        plan_.jobs.append(job)
        return job

    def request(self, request: dict[str, Any], by_digest: dict[str, Recording]) -> list[Job]:
        """Plan one bank request, first in the queue; the jobs it added."""
        key, plan_ = request["key"], self.result
        if key in self.asked:
            return []
        self.asked.add(key)
        before = len(plan_.jobs)
        recording = by_digest.get(request["content"])
        installed = self.planner.catalog.extractors.get(request["extractor"]["id"])
        if recording is None:
            self.asked.discard(key)  # --follow asks again once new admissions are planned
            if key not in {k for k, _ in plan_.unplaced}:
                plan_.unplaced.append((key, "its content is not a recording here"))
            return []
        if installed is None or installed.version != request["extractor"]["version"]:
            plan_.unplaced.append((key, f"asks for {request['extractor']['id']} "
                                        f"{request['extractor']['version']}, which is not installed"))
            return []
        try:
            ours = request_key(self.planner, request)
        except (bank.BankError, KeyError) as exc:
            plan_.unplaced.append((key, f"its key cannot be computed here: {exc}"))
            return []
        if ours != key:
            plan_.unplaced.append((key, f"this configuration makes {ours} for it, so the asker reads with other "
                                        f"parameters: the relay's bank_parameters must be the backfill "
                                        f"configuration's parameters"))
            return []
        unit = Unit(installed.id, request["feature"])
        region = None if request["region"] is None else Region(**request["region"])
        state = self.planner.state(recording, unit, region)
        if state[0] == DONE:
            plan_.answered.append(key)
            return []
        job = self.want(recording, unit, region, True, state)
        if job is not None:
            job.requests.append(key)
        return plan_.jobs[before:]

    def recording(self, recording: Recording) -> list[Job]:
        """Plan what the mode asks for of one recording; the jobs it added."""
        if recording.object_id in self.recordings:
            return []
        self.recordings.add(recording.object_id)
        plan_, planner, schedule = self.result, self.planner, self.schedule
        before = len(plan_.jobs)
        choice = regions.plan(self.mode, self.known, recording.regions, schedule.costs(), schedule.expensive_above,
                              recording.length)
        for unit in self.units:
            whole = planner.state(recording, unit)
            if whole[0] in (DONE, SKIPPED, UNCONFIGURED, DAMAGED):
                self.want(recording, unit, None, state=whole)
                continue
            over = choice[unit.extractor]
            if unit.producer is not None and over is not None:
                # Where its feature is made: whole once made whole, else as its maker is made.
                over = "whole" if whole[0] != WAITING else choice[unit.producer]
            if over is None:
                why = ("on-demand mode: made when asked" if self.mode == "on-demand"
                       else "expensive, and the recording has no song regions: on demand"
                       if choice[unit.extractor] is None
                       else f"measures {unit.source}, which is left for on demand")
                plan_.deferred.append((recording, unit, why))
                continue
            if over == "whole":
                self.want(recording, unit, None, state=whole)
            else:
                for region in over:
                    self.want(recording, unit, region)
        return plan_.jobs[before:]


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
    preference probes, over the same region. Each job has its tier (fast or
    background, `Schedule.tier`); the runner orders by `Job.priority`."""
    planning = Planning(schedule, planner, mode, shape, spool)
    by_digest = {r.digest: r for r in recordings}
    for request in asked or []:
        planning.request(request, by_digest)
    ordered = sorted(recordings, key=lambda r: (not r.songs,)) if mode == "songs" else list(recordings)
    for recording in ordered:
        planning.recording(recording)
    return planning.result


def prioritise(recordings: list[Recording]) -> None:
    """Each recording's place in the background queue: song recordings
    first, then the most recent (its first admission, `at`), then the rest."""
    ordered = sorted(recordings, key=lambda r: r.at or "", reverse=True)
    ordered.sort(key=lambda r: (not r.songs, r.at is None))
    for index, recording in enumerate(ordered):
        recording.priority = index


def projection(jobs: list[Job], schedule: Schedule, cores: int | None = None) -> dict[str, Any]:
    """The plan's cost from the recorded costs: CPU-seconds here (times the
    load factor) and seconds on the worker, and two projected times: until
    every recording is usable (the fast tier over `cores`) and until all is
    made (the fast tier, then the background over `cores` and the worker
    together, the laptop taking what the worker is not holding when
    `overflow`), each at least its longest single job. Proposing is not
    counted."""
    cores = cores or os.cpu_count() or 1
    fast = local = remote_s = routed_local = 0.0
    longest_fast = longest = 0.0
    unmeasured: set[str] = set()
    recordings: dict[str, float | None] = {}
    for job in jobs:
        seconds = job.seconds
        recordings[job.recording.object_id] = job.recording.seconds
        setting = schedule.setting(job.unit.extractor)
        if seconds is None:
            continue
        if setting["cost"] is None and not (job.routed and setting["remote_cost"] is not None):
            unmeasured.add(job.unit.extractor)
            continue
        cpu = float(setting["cost"] or 0.0) * seconds * schedule.load_factor
        if job.tier == "fast" and not job.requested:
            fast += cpu
            longest_fast = max(longest_fast, cpu / max(1, job.reserved))
        elif job.routed and setting["remote_cost"] is not None:
            remote_s += float(setting["remote_cost"]) * seconds
            routed_local += cpu
            longest = max(longest, min(cpu / max(1, job.reserved), float(setting["remote_cost"]) * seconds))
        else:
            local += cpu
            longest = max(longest, cpu / max(1, job.reserved))
    audio = sum(s for s in recordings.values() if s is not None)
    unknown_length = sum(1 for s in recordings.values() if s is None)
    usable = max(fast / cores, longest_fast)
    if remote_s and schedule.overflow and routed_local:
        # Worker and laptop share the routed work: the worker at its rate, the laptop with what it has left.
        share = (local + routed_local) / (cores + routed_local / remote_s)
        background = max(share, local / cores)
        background = min(background, max(remote_s, local / cores))
    else:
        background = max(local / cores, remote_s)
    background = max(background, longest)  # one long recording's job is a floor: it runs on one copy
    done = usable + background
    return {"audio_hours": round(audio / 3600, 2), "compute_hours_here": round((fast + local) / 3600, 2),
            "compute_hours_on_workers": round(remote_s / 3600, 2),
            "time_to_usable_hours": round(usable / 3600, 3), "time_to_done_hours": round(done / 3600, 2),
            "projected_wall_hours": round(done / 3600, 2), "projected_days": round(done / 86400, 2),
            "usable_seconds_per_audio_second": round(usable / audio, 4) if audio else None,
            "seconds_per_audio_second": round(done / audio, 3) if audio else None,
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
    """A pool of copies over the plan. A job starts when what it waits for
    is done, its extractor has a free copy, and its reserved cores and memory
    fit; the ready jobs go in `Job.priority` order (requests, the fast tier,
    the background), and the earliest that does not fit holds the rest, so a
    wide one is never starved by narrow ones. No background job starts while
    a fast job is still to run or `hold_background` says the fast tier's
    proposal is outstanding (ADR 0112: every recording usable first).

    Routed jobs are put to the spool at most `spool_in_flight` at a time, in
    the same order, and collected every `poll_seconds`; with `overflow`, a
    routed job the worker is not holding runs here when cores are free. A
    worker that files nothing for `local_when_away_seconds` (plus the time
    its oldest task should take) is absent: its tasks are made here, and
    nothing more is put to it for that long again.

    With `intake` (`--follow`), it does not stop when the plan is done: it
    asks `intake` for new jobs every `poll_seconds` until `stop` says so.
    `tick` is called on every turn, outside the lock (the proposals);
    `settled` with each job that ends, inside it."""

    def __init__(self, jobs: list[Job], planner: Planner, schedule: Schedule, shape: dict[str, dict[str, Any]],
                 cores: int, memory_gb: float, mode: str, note: Callable[..., None],
                 run_unit: Callable[[Job], tuple[dict[str, Any], float]],
                 spool: remote.Spool | None = None, config: str | None = None, *,
                 intake: Callable[[], list[Job]] | None = None, stop: Callable[[], bool] | None = None,
                 tick: Callable[[], None] | None = None, hold_background: Callable[[], bool] | None = None,
                 settled: Callable[[Job], None] | None = None):
        self.jobs, self.planner, self.schedule, self.shape = jobs, planner, schedule, shape
        self.cores, self.memory_gb, self.mode, self.note = cores, memory_gb, mode, note
        self.run_unit, self.spool, self.config = run_unit, spool, config
        self.intake, self.stop, self.tick = intake, stop or (lambda: False), tick or (lambda: None)
        self.hold_background = hold_background or (lambda: False)
        self.settled = settled or (lambda job: None)
        self.changed = threading.Condition()
        self.free_cores, self.free_memory = cores, memory_gb
        self.copies: dict[str, int] = {}
        self.last_filed = time.monotonic()
        self.put_paused_until = 0.0
        self.first_background: float | None = None  # when the first background job started

    def end(self, job: Job, status: str, detail: str | None = None) -> None:
        """Inside the lock: the job is done, failed or blocked."""
        job.status = status
        if detail is not None:
            job.detail = detail
        self.settled(job)

    # -- Spool --

    def put_window(self) -> None:
        """Inside the lock: put the next routed jobs, in priority order, until
        `spool_in_flight` are with the spool."""
        if self.spool is None or time.monotonic() < self.put_paused_until:
            return
        room = self.schedule.spool_in_flight - sum(1 for j in self.jobs if j.status in ("put", "putting"))
        if room <= 0:
            return
        open_background = self.background_open()
        chosen = sorted((j for j in self.jobs if j.routed and j.status == "pending"
                         and all(a.status == "done" for a in j.after) and (j.rank < 2 or open_background)),
                        key=Job.priority)[:room]
        if not chosen:
            return
        for job in chosen:
            job.status = "putting"
        # Beside the pool: a transport takes about 0.01 s per audio-second to
        # encode and check, which must not hold back what runs here.
        threading.Thread(target=lambda: [self.put_one(job) for job in chosen], daemon=True).start()

    def put_one(self, job: Job) -> None:
        try:
            answer = remote.put(self.planner.catalog, self.planner.root, self.spool, job.unit.extractor,
                                job.recording.path, job.recording.media_type
                                or extract.media_type_of(job.recording.path), job.recording.digest,
                                remote_parameters(self.planner.parameters, job.unit.extractor, self.schedule),
                                transport=self.schedule.setting(job.unit.extractor)["transport"],
                                ffmpeg=self.planner.programs.get("ffmpeg"))
        except Exception as exc:  # noqa: BLE001  (the job runs here; the backfill goes on)
            with self.changed:
                job.routed, job.status = False, "pending"
                self.changed.notify_all()
            self.note(step="spool put failed; runs here", **job.describe(), error=str(exc)[-600:])
            return
        with self.changed:
            job.detail = answer["task_id"]
            if answer["status"] == "hit":
                self.end(job, "done")
                self.changed.notify_all()
                return
            if not any(j.status == "put" for j in self.jobs):
                self.last_filed = time.monotonic()  # the worker's clock starts with its first task
            job.status, job.since = "put", time.monotonic()
            self.changed.notify_all()
        self.note(step="spool put", **job.describe(), status=answer["status"], task=answer["task_id"],
                  transport=answer.get("transport"))

    def collect(self) -> None:
        """Outside the lock: file what the spool holds, and give back to this
        machine what the spool gave up or an absent worker holds."""
        try:
            answer = remote.collect(self.planner.catalog, self.planner.root, self.spool)
        except (remote.RemoteError, bank.BankError, OSError) as exc:
            self.note(step="spool collect failed", error=str(exc)[-600:])
            answer = {"tasks": {}, "failed": {}}
        if answer.get("tasks") or answer.get("failed"):
            self.note(step="spool collect", status=answer.get("status"), failed=answer.get("failed"),
                      tasks={k[:17]: v.get("status") for k, v in answer.get("tasks", {}).items()})
        with self.changed:
            waiting = [j for j in self.jobs if j.status == "put"]
        events = []
        for job in waiting:
            task = job.detail
            if self.planner.state(job.recording, job.unit)[0] == DONE:
                events.append((job, "done", "filed from the spool", {"waited_s": round(time.monotonic() - job.since,
                                                                                       1)}))
            elif self.made_elsewhere(job):
                events.append((job, "pending", "made on the worker, but it does not stand in here; runs here",
                               {"task": task, "detail": "no passing equivalence record names the software it "
                                                        "was made with"}))
            elif task in (answer.get("failed") or {}) \
                    or (answer.get("tasks") or {}).get(task, {}).get("status") == "refused":
                events.append((job, "pending", "the spool gave the task up; runs here", {"task": task}))
        with self.changed:
            now = time.monotonic()
            for job, status, step, extra in events:
                if status == "done":
                    job.wall_s = now - job.since
                    self.last_filed = now
                    self.end(job, "done")
                else:
                    job.status, job.routed = "pending", False
                self.note(step=step, **job.describe(), **extra)
            still = [j for j in self.jobs if j.status == "put"]
            if still:
                oldest = min(still, key=lambda j: j.since)
                cost = self.schedule.setting(oldest.unit.extractor)["remote_cost"]
                allowance = float(cost) * (oldest.seconds or 0.0) if cost is not None else 0.0
                limit = self.schedule.away(self.mode) + allowance
                if now - max(self.last_filed, oldest.since) > limit:
                    for job in still:
                        job.status, job.routed = "pending", False
                        self.note(step="no worker took the task in time; runs here too", **job.describe(),
                                  task=job.detail, limit_s=round(limit, 1))
                    self.put_paused_until = now + self.schedule.away(self.mode)
            self.changed.notify_all()

    def made_elsewhere(self, job: Job) -> bool:
        """Whether the worker's entry, under the parameters it was put with,
        is in the bank although it does not count here."""
        parameters = remote_parameters(self.planner.parameters, job.unit.extractor, self.schedule)
        if parameters is self.planner.parameters:
            return False
        exact = bank.Bank(self.planner.root, self.planner.catalog, parameters, exact=True)
        return exact.entry(job.recording.digest, job.unit.extractor) is not None

    # -- the pool --

    def background_open(self) -> bool:
        """Whether a background job may start: the fast tier is all made and
        its proposal is not outstanding."""
        if any(j.tier == "fast" and not j.requested and j.status in ("pending", "putting", "put", "running")
               for j in self.jobs):
            return False
        return not self.hold_background()

    def fits(self, job: Job) -> bool:
        copies = self.shape[job.unit.extractor]["copies"]
        return (self.copies.get(job.unit.extractor, 0) < copies and job.reserved <= self.free_cores
                and (job.memory_gb <= self.free_memory or self.free_cores == self.cores))

    def start(self, job: Job) -> None:
        job.status, job.since = "running", time.monotonic()
        if job.routed:
            job.routed = False  # taken here, from the worker's queue
            self.note(step="routed work taken here", **job.describe())
        if job.rank == RANKS["background"] and self.first_background is None:
            self.first_background = job.since
        self.free_cores -= job.reserved
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
            self.free_cores += job.reserved
            self.free_memory += job.memory_gb
            self.copies[job.unit.extractor] -= 1
            if state == DONE:
                self.end(job, "done")
            else:
                self.end(job, "failed", first_error(answer) if state is None
                         else f"ran, but the bank has no entry ({state})")
            self.note(step="ran" if job.status == "done" else "failed", **job.describe(), s=round(job.wall_s, 1),
                      cpu_s=round(cpu, 1), tier=job.tier, requested=job.requested or None,
                      error=job.detail if job.status == "failed" else None)
            self.changed.notify_all()

    def ready(self) -> list[Job]:
        """Inside the lock: the jobs that may start, in the order they are offered."""
        open_background = self.background_open()
        ready = [j for j in self.jobs if j.status == "pending" and all(a.status == "done" for a in j.after)
                 and (j.rank < RANKS["background"] or open_background)]
        here = sorted((j for j in ready if not j.routed), key=Job.priority)
        taken = sorted((j for j in ready if j.routed), key=Job.priority) \
            if self.spool is None or self.schedule.overflow else []
        return here + taken

    def run(self) -> None:
        last_collect = last_intake = time.monotonic()
        with self.changed:
            while True:
                for job in self.jobs:
                    if job.status == "pending" and any(a.status in ("failed", "blocked") for a in job.after):
                        self.end(job, "blocked", "what it measures was not made")
                        self.note(step="blocked", **job.describe())
                stopping = self.intake is not None and self.stop()
                held = self.hold_background()
                started = 0
                if not stopping:
                    self.put_window()
                    for job in self.ready():
                        if not self.fits(job):
                            if self.copies.get(job.unit.extractor, 0) < self.shape[job.unit.extractor]["copies"]:
                                break  # it waits for cores or memory: nothing behind it jumps ahead
                            continue  # its extractor's copies are all busy: the next may start
                        self.start(job)
                        started += 1
                active = any(j.status in ("pending", "putting", "put", "running") for j in self.jobs)
                running = any(j.status in ("running", "putting") for j in self.jobs)
                waiting = any(j.status == "put" for j in self.jobs)
                self.changed.release()
                try:
                    self.tick()
                    if waiting and time.monotonic() - last_collect >= self.schedule.poll_seconds:
                        self.collect()
                        last_collect = time.monotonic()
                    if self.intake is not None and not stopping \
                            and time.monotonic() - last_intake >= self.schedule.poll_seconds:
                        for job in self.intake():
                            if all(job is not j for j in self.jobs):
                                self.jobs.append(job)
                        last_intake = time.monotonic()
                finally:
                    self.changed.acquire()
                if stopping and not running:
                    return
                if not active and self.intake is None:
                    return
                if active and not (running or waiting or started or held or self.intake is not None
                                   or time.monotonic() < self.put_paused_until):
                    # With nothing running every ready job fits, so a pending one waits on itself.
                    raise BackfillError("nothing can start: the plan waits on itself")
                self.changed.wait(timeout=min(1.0, self.schedule.poll_seconds) if waiting or self.intake
                                  else 0.05 if held else 5.0)



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
    command.add_argument("--follow", action="store_true",
                         help="keep running: make bank requests as they arrive and new admissions (needs --commit)")
    command.add_argument("--follow-seconds", type=float,
                         help="with --follow, stop after this many seconds (default: until stopped)")


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

    def world(self) -> None:
        a = self.args
        self.run("World refresh", [a.world_python, str(HERE.parent / "world" / "world.py"), "refresh", a.archive,
                                   "--store", a.world_store, "--archive-command", a.archive_command])
        self.run("conformance", [a.conformance_python or a.world_python,
                                 str(HERE.parent / "world" / "conformance.py"), "--store", a.world_store])

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


def discover(args: argparse.Namespace, env: dict[str, str] | None, steps: "Steps", planner: Planner,
             note: Callable[..., None], commit: bool) -> tuple[list[Recording], list[Recording]]:
    """Probe what `propose --all` lists as unprobed, then every recording of
    the archive (all, and those whose content is here), with its song
    regions and its place in the queue."""
    names = args.proposers.strip()
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
    find_regions(present, export, Path(args.archive), planner.reader, note)
    prioritise(present)
    return recordings, present


class Proposals:
    """When to propose while the backfill runs (ADR 0112): once the fast
    tier is all made (every recording usable), then in batches as background
    units land, at most every `propose_every_seconds`; one at a time, each in
    a thread of its own so the pool goes on. With no proposers, a proposal
    is nothing, and usable is when the fast tier is made. A usable proposal
    that fails makes nothing usable: the background goes on, and the next
    proposal that succeeds (a batch, at most every `propose_every_seconds`,
    or the last) is the usable one."""

    def __init__(self, steps: "Steps", args: argparse.Namespace, note: Callable[..., None], started: float,
                 every: float, enabled: bool):
        self.steps, self.args, self.note, self.started, self.every = steps, args, note, started, every
        self.enabled = enabled
        self.runner: Runner | None = None
        self.thread: threading.Thread | None = None
        self.kind: str | None = None
        self.fast_made = True  # fast work (or none) not yet proposed: the first proposal is the usable one
        self.background_made = False
        self.last = time.monotonic()
        self.usable_s: float | None = None
        self.usable_pending = False  # the usable proposal failed: the next good one is usable
        self.lock = threading.Lock()

    def settled(self, job: Job) -> None:
        if job.status != "done":
            return
        if job.tier == "fast" and not job.requested:
            self.fast_made = True
        else:
            self.background_made = True

    def fast_left(self) -> bool:
        assert self.runner is not None
        with self.runner.changed:
            return any(j.tier == "fast" and not j.requested and j.status in ("pending", "putting", "put", "running")
                       for j in self.runner.jobs)

    def hold_background(self) -> bool:
        """Whether the fast tier's proposal is still to come or under way."""
        with self.lock:
            return self.fast_made or self.kind == "usable"

    def busy(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def tick(self) -> None:
        if self.busy():
            return
        if self.fast_made and not self.fast_left():
            self.begin("usable")
        elif (self.background_made or self.usable_pending) and time.monotonic() - self.last >= self.every:
            self.begin("background")

    def begin(self, kind: str) -> None:
        with self.lock:
            self.kind = kind
            if kind == "usable":
                self.fast_made = False
            else:
                self.background_made = False
        self.thread = threading.Thread(target=self.propose, args=(kind,), daemon=True)
        self.thread.start()

    def propose(self, kind: str) -> None:
        failures = self.steps.failures
        try:
            if self.enabled:
                self.steps.propose(f"propose --all ({'usable' if kind == 'usable' else 'background batch'})")
                if kind == "usable" and self.args.world_store:
                    self.steps.world()
        finally:
            with self.lock:
                self.last = time.monotonic()
                self.kind = None
                good = self.steps.failures == failures
                if (kind == "usable" or self.usable_pending) and not good:
                    self.usable_pending = True
                    self.note(step="usable proposal failed", s=round(time.monotonic() - self.started, 1))
                elif kind == "usable" or self.usable_pending:
                    self.usable_pending = False
                    first = self.usable_s is None
                    if first:
                        self.usable_s = round(time.monotonic() - self.started, 1)
                    self.note(step="usable", s=round(time.monotonic() - self.started, 1), first=first)
            if self.runner is not None:
                with self.runner.changed:
                    self.runner.changed.notify_all()

    def finish(self) -> None:
        if self.thread is not None:
            self.thread.join()


class Follow:
    """`--follow`: every `poll_seconds`, the bank's new requests, first in
    the queue; every `rescan_seconds`, new admissions (probed, then planned);
    until `--follow-seconds` pass or the unit is stopped (SIGTERM, SIGINT)."""

    def __init__(self, planning: Planning, args: argparse.Namespace, env: dict[str, str] | None, steps: "Steps",
                 note: Callable[..., None], root: Path, by_digest: dict[str, Recording], present: list[Recording]):
        self.planning, self.args, self.env, self.steps, self.note = planning, args, env, steps, note
        self.root, self.by_digest, self.present = root, by_digest, present
        self.until = time.monotonic() + args.follow_seconds if args.follow_seconds is not None else None
        self.stopped = False
        self.last_scan = time.monotonic()
        self.reported: set[str] = set()

    def stop(self) -> bool:
        return self.stopped or (self.until is not None and time.monotonic() >= self.until)

    def intake(self) -> list[Job]:
        added: list[Job] = []
        if time.monotonic() - self.last_scan >= self.planning.schedule.rescan_seconds:
            self.last_scan = time.monotonic()
            try:
                _, present = discover(self.args, self.env, self.steps, self.planning.planner, self.note, True)
            except (BackfillError, OSError) as exc:
                self.note(step="rescan failed", error=str(exc)[-600:])
                present = []
            known = {r.object_id for r in self.present}
            new = [r for r in present if r.object_id not in known]
            if new:
                self.present.extend(new)
                prioritise(self.present)
                for recording in new:
                    self.by_digest[recording.digest] = recording
                    added += self.planning.recording(recording)
                self.note(step="new recordings", count=len(new), jobs=len(added))
        try:
            asked = bank.requests(self.root)
        except (bank.BankError, OSError) as exc:
            self.note(step="requests unreadable", error=str(exc)[-600:])
            return added
        for request in asked:
            if request["key"] in self.planning.asked:
                continue
            jobs = self.planning.request(request, self.by_digest)
            added += jobs
            if jobs:
                self.note(step="request planned", key=request["key"], jobs=[j.unit.name for j in jobs])
        report(self.planning.result, self.root, self.note, self.reported, True)
        return added


def report(planned: Plan, root: Path, note: Callable[..., None], reported: set[str], commit: bool) -> None:
    """Say, once each, which requests are not planned; with `commit`, remove
    those whose entry is already made."""
    for key, why in planned.unplaced:
        if key not in reported:
            reported.add(key)
            note(step="request not planned", key=key, detail=why)
    for key in planned.answered:
        if key not in reported:
            reported.add(key)
            if commit:
                bank.request_path(root, key).unlink(missing_ok=True)
            note(step="request already made" + ("; removed" if commit else ""), key=key)


def main(args: argparse.Namespace, env: dict[str, str] | None = None,
         run_unit: Callable[[Job], tuple[dict[str, Any], float]] | None = None) -> int:
    """The command; `env` is Door's environment (`enrich.environment`), and
    `run_unit` replaces `extract.py run` (tests)."""
    if args.commit and args.dry_run:
        raise BackfillError("--commit and --dry-run cannot go together; a backfill is a dry run by default")
    if args.follow and not args.commit:
        raise BackfillError("--follow makes what arrives, so it needs --commit")
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
    note(step="start", mode=args.mode, commit=commit, follow=args.follow or None, cores=cores,
         memory_gb=round(memory_gb, 1), bank=str(root))

    names = args.proposers.strip()
    root.mkdir(parents=True, exist_ok=True)  # an empty bank is a bank; propose refuses a missing one
    planner = Planner(catalog, root, parameters, programs, args.retry_failed)
    recordings, present = discover(args, env, steps, planner, note, commit)
    shape = layout(catalog, schedule, parameters, cores, memory_gb)
    # Requests are read in every mode; a damaged one stops the backfill (bank.requests refuses it).
    asked = bank.requests(root)
    planned = plan(present, schedule, planner, args.mode, shape, spool is not None, asked)
    assert planned.planning is not None
    sources: dict[str, int] = {}
    for recording in present:
        sources[recording.span_source] = sources.get(recording.span_source, 0) + 1
    note(step="recordings", count=len(recordings), here=len(present),
         with_songs=sum(1 for r in present if r.songs), song_spans_from=sources, requests=len(asked))
    note(step="layout", extractors=shape)
    by_unit: dict[str, int] = {}
    by_tier: dict[str, int] = {}
    for job in planned.jobs:
        by_unit[job.unit.name] = by_unit.get(job.unit.name, 0) + 1
        by_tier[job.tier] = by_tier.get(job.tier, 0) + 1
    note(step="plan", jobs=len(planned.jobs), by_unit=by_unit, by_tier=by_tier,
         fast_units=sorted({u.name for u in schedule.units if schedule.tier(u, catalog) == "fast"}),
         routed=sum(1 for j in planned.jobs if j.routed),
         regions=sum(1 for j in planned.jobs if j.region is not None),
         requested=sum(1 for j in planned.jobs if j.requested), unplaced=len(planned.unplaced),
         already=planned.settled, deferred=len(planned.deferred), held=len(planned.held),
         projection=projection(planned.jobs, schedule, cores))
    for recording in present:
        units = [j.unit.name for j in planned.jobs if j.recording is recording]
        later = [u.name for r, u, _ in planned.deferred if r is recording]
        if units or later or not commit:
            note(step="recording", recording=recording.object_id, content=recording.digest[7:19],
                 seconds=None if recording.seconds is None else round(recording.seconds),
                 songs=recording.songs, span_source=recording.span_source, priority=recording.priority,
                 regions=[r.as_json() for r in recording.regions] or None, runs=units, on_demand=later or None)
    reported: set[str] = set()
    report(planned, root, note, reported, commit)
    for recording, unit, state, detail in planned.held:
        note(step="held", recording=recording.object_id, content=recording.digest[7:19], unit=unit.name,
             state=state, detail=detail)

    failures = sum(1 for _, _, state, _ in planned.held if state in (DAMAGED,))
    proposals = Proposals(steps, args, note, started, schedule.propose_every_seconds, bool(names))
    handlers: dict[int, Any] = {}
    if commit and (planned.jobs or args.follow):
        follow = Follow(planned.planning, args, env, steps, note, root, {r.digest: r for r in present}, present) \
            if args.follow else None
        if follow is not None:
            follow.reported = reported

            def stopped(*_: Any) -> None:
                follow.stopped = True

            for signum in (signal.SIGTERM, signal.SIGINT):
                try:
                    handlers[signum] = signal.signal(signum, stopped)
                except ValueError:  # not the main thread (tests)
                    pass

        def settled(job: Job) -> None:
            proposals.settled(job)
            if job.status == "done":
                for key in job.requests:
                    bank.request_path(root, key).unlink(missing_ok=True)
                    planned.planning.asked.discard(key)  # asked again later, it is planned again
                    note(step="request made; removed", key=key, **job.describe())

        runner = Runner(planned.jobs, planner, schedule, shape, cores, memory_gb, args.mode, note,
                        run_unit or (lambda job: run_extract(job, root, args.config, args.ffmpeg)), spool,
                        args.config, intake=follow.intake if follow else None, stop=follow.stop if follow else None,
                        tick=proposals.tick, hold_background=proposals.hold_background, settled=settled)
        proposals.runner = runner
        made_from = time.monotonic()
        try:
            runner.run()
        finally:
            for signum, handler in handlers.items():
                signal.signal(signum, handler)
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
             background_started_s=None if runner.first_background is None
             else round(runner.first_background - started, 1),
             cpu_seconds_per_audio_second={k: round(v["cpu_s"] / v["audio_s"], 4) for k, v in cost.items()})
    proposals.finish()
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
        steps.world()
    failures += steps.failures
    finished = round(time.monotonic() - started, 1)
    usable = proposals.usable_s if proposals.usable_s is not None or proposals.usable_pending \
        else finished if commit else None
    note(step="done", failures=failures, mode=args.mode, commit=commit, s=finished,
         time_to_usable_s=usable, time_to_done_s=finished if commit else None)
    return 1 if failures else 0
