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

1. `enrich probe` every audio or video Artifact the archive's export shows
   unprobed (`unprobed_of`: what `enrich propose --all` would list; one
   `audio_stream` claim each, through Door);
2. plan: for every Artifact with an audio stream claim, the units of the
   configuration (an extractor on the content, or on a file feature of it
   with `--from`) whose entries the bank lacks, read through `bank.Bank` as
   every reader reads them (an entry made on another device counts once a
   passing equivalence record lets it stand in, ADR 0090's amendment), and
   every bank request (`requests/`); the bank is the ledger;
3. the fast tier: every unit that measures the content itself, prefers no
   stem, and costs at most `fast_above` (by the recorded costs: rhythm,
   tonal, chords-jiang, chords-chordino, structure; a configuration may set
   a `tier`) over EVERY recording. Each recording is proposed as soon as its
   own fast units have settled (`backfill_proposals.Proposals`: one
   `--artifact` each, one `--all` when more than two wait), while the pool
   goes on with the others; once the fast tier has settled everywhere and
   every recording's proposal has run, every recording is usable (chords,
   beats, key, tuning, sections; ADR 0112, "Usable"), the `usable` event;
   with `--world-store`, World is refreshed then too;
4. the background tier (stems and what measures them: f0-pesto, melody,
   tonal and notes on the bass stem): no background job starts before step
   3 is done. In priority order: song recordings, then the most recently
   admitted, then the rest; a bank request goes ahead of everything, in
   either tier, and is removed once its entry is made. A job waiting for
   cores or memory does not hold the jobs behind it: a later one that ends
   before it would fit, or takes only what it will leave over, starts now
   (`backfill_queue`, backfilling; it is reserved for outright after
   `reserve_after_seconds`). Proposals in batches as background units land
   (at most every `propose_every_seconds`);
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
holding whenever it would end here sooner than the worker could end it. A
worker that files nothing within `local_when_away_seconds` (every mode has
one) plus its oldest task's expected time is absent, and its work
is made here; when the spool's directory can be read here, a task the
worker holds a lease on is never given up, and the clock runs from when
the worker could have started (`backfill_queue`). A task the spool failed
for good, or could not put, runs here.

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
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import adapter  # noqa: E402
import bank  # noqa: E402
import extract  # noqa: E402
import composition  # noqa: E402
import proposers  # noqa: E402
import regions  # noqa: E402
import remote  # noqa: E402
from backfill_proposals import Follow, Proposals, Steps  # noqa: E402,F401
from backfill_queue import DONE, RANKS, BackfillError, Job, Runner, first_error, remote_parameters  # noqa: E402,F401
from regions import Region  # noqa: E402

STREAM = "archive.claim/audio_stream"
PARTS = "archive.claim/session_parts"
SESSION_NAMESPACE = "archive.audio/session"  # capture/capture.py's, ADR 0048
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
    # Version 1 held the whole recording's features: its copies peaked at
    # 11.4 GB resident on a 93-minute recording (round 2, 2026-10-20). Version
    # 2 holds a ten-minute sample's (2 GB at most, measured 1.8 GB on a 9-minute
    # recording) and per hour only its spectrogram, features and outputs
    # (about 0.6 GB, counted from their sizes; 0.8 reserved until measured on
    # a long recording).
    "chords-jiang": {"cores": 2, "cost": 0.151, "memory_gb": 2.0, "memory_gb_per_hour": 0.8},
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
    # A worker's lease renewed within this long is held: its task is never
    # also made here, and the absent clock stops (Spool's reclaim age).
    "lease_fresh_seconds": 300.0,
    "overflow": True,  # free cores here take routed work the worker is not holding
    "propose_every_seconds": 900.0,  # background proposals, batched
    "rescan_seconds": 600.0,  # --follow: how often new admissions are looked for
    # A job that cannot start for cores or memory lets later jobs start around
    # it (by the recorded costs); once it has waited this long, nothing new
    # starts until it does (ADR 0112, round 3).
    "reserve_after_seconds": 1200.0,
    # One recording's usable proposal (`enrich propose`, five proposers' runs
    # through Door): 298 s for 25 recordings on the shadow (round 2), about
    # 2.4 s a run. Proposed recording by recording behind the fast pass, it
    # adds about one of these to time-to-usable (`projection`).
    "propose_seconds_per_recording": 12.0,
}
THREAD_VARIABLES = ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS",
                    "VECLIB_MAXIMUM_THREADS", "NUMBA_NUM_THREADS")


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
    reserve_after_seconds: float = 1200.0
    propose_seconds_per_recording: float = 12.0
    lease_fresh_seconds: float = 300.0

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
                    float(numbers["propose_every_seconds"]), float(numbers["rescan_seconds"]),
                    float(numbers["reserve_after_seconds"]), float(numbers["propose_seconds_per_recording"]),
                    float(numbers["lease_fresh_seconds"]))


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
    sample_rate: int = 1
    sample_count: int = 0
    evidence: dict[str, Any] = field(default_factory=dict)
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
    streams: dict[str, tuple[int, int]] = {}
    for record in current.values():
        if record["shape"] == "claim" and cites(record, STREAM):
            payload = record.get("payload", {})
            for ref in record.get("refs", []):
                if ref.get("role") == "artifact" and ref["id"] not in seconds:
                    rate, count = payload.get("sample_rate"), payload.get("sample_count")
                    if isinstance(rate, int) and rate > 0 and isinstance(count, int):
                        streams[ref["id"]] = (rate, count)
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
                               seconds.get(record["object_id"]), False, at=first.get(record["object_id"]),
                               sample_rate=streams.get(record["object_id"], (1, 0))[0],
                               sample_count=streams.get(record["object_id"], (1, 0))[1]))
    return found


def later_sources(export: dict[str, Any]) -> set[str]:
    """The Artifacts that are parts of a session's later source: in a
    current parts claim whose first part does not name the session
    (`session-` and its content digest, ADR 0048). The background analyses a
    take once, from the session's first source (ADR 0070, amendment on
    twins); a twin kept beside it is one of these."""
    current = heads(export["records"])
    digests = {record["object_id"]: record["payload"]["content"]["digest"]
               for record in current.values() if record["shape"] == "artifact"}
    later: set[str] = set()
    for record in current.values():
        if record["shape"] != "claim" or not cites(record, PARTS):
            continue
        sessions = [ref["id"] for ref in record.get("refs", []) if ref.get("role") == "session"]
        parts = [ref["id"] for ref in record.get("refs", []) if ref.get("role") == "part"]
        if len(sessions) != 1 or not parts or parts[0] not in digests:
            continue
        named = adapter.object_id(export["archive_id"], SESSION_NAMESPACE,
                                  "session-" + digests[parts[0]].removeprefix("sha256:"))
        if named != sessions[0]:
            later.update(parts)
    return later


def find_regions(recordings: list[Recording], export: dict[str, Any], archive: Path, reader: bank.Bank,
                 note: Callable[..., None]) -> None:
    """Each recording's song regions by `regions.regions_for`'s precedence,
    with `structure/playing` from the bank as the last source. A recording
    the export cannot place (no audio stream claim yet: a dry run before
    its probe) has none, and says why."""
    records = adapter.ArchiveReader.from_export(archive, export)
    for recording in recordings:
        artifact = records.heads.get(recording.object_id)
        pin = lambda record: {"id": record["object_id"], "revision_id": record["revision_id"]}
        recording.evidence = {"snapshot": records.snapshot, "artifact": pin(artifact) if artifact else None,
                              "streams": [pin(r) for r in records.heads.values() if cites(r, STREAM)
                                          and any(ref.get("role") == "artifact" and ref.get("id") == recording.object_id
                                                  for ref in r.get("refs", []))],
                              "selection_candidates": [{**pin(r), "refs": r.get("refs", [])} for r in records.heads.values()
                                  if any(cites(r, contract) for contract in
                                         (regions.OCCURRENCE, regions.SELECTION, regions.SUGGESTIONS[0]))]}
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
        recording.evidence["region_source"] = found["source"]
        if structure is not None:
            recording.evidence["structure_key"] = structure.manifest["key"]


# -- what a unit still needs ---------------------------------------------------------

TODO, WAITING, FAILED, SKIPPED, UNCONFIGURED, DAMAGED = (
    "todo", "waiting", "failed_before", "skipped", "unconfigured", "damaged")  # and DONE (backfill_queue)


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
                stem = reader.feature(recording.digest, feature)
                catalog = reader.catalog if stem is None or stem[0].timeline is None else \
                    reader.catalog.placed_by(found[1], feature, stem[0].manifest["key"])  # made over a region
                keys = catalog.keys(found[1], order, self.catalog.overrides_for(feature, self.parameters))
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
                 spool: bool, chosen: composition.Selection | None = None):
        if mode not in MODES:
            raise BackfillError(f"mode is one of {', '.join(MODES)}, not {mode!r}")
        self.schedule, self.planner, self.mode, self.shape, self.spool = schedule, planner, mode, shape, spool
        self.chosen = chosen
        self.diagnostics: dict[str, list[dict[str, Any]]] = {}
        self.explanations: dict[str, list[dict[str, Any]]] = {}
        self.units = unit_order(schedule.units, planner.catalog)
        candidates = candidate_units(chosen, planner.catalog) if chosen is not None else self.units
        self.known = {u.extractor for u in candidates} | {u.producer for u in candidates if u.producer}
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

    def targets(self, recording: Recording, region: Region | None = None) -> list[Unit]:
        if self.chosen is None:
            return self.units
        reader = self.planner.reader
        over = region.as_json() if region else None
        if over is not None:
            reader = bank.RegionView(reader, recording.digest, over)
        context = proposers.Context(reader, recording.digest, recording.sample_rate, recording.sample_count, over)
        expanded = composition.expand(self.chosen, context, self.planner.catalog, self.planner.parameters,
                                      composition.configured(self.planner.catalog, self.planner.programs))
        previous = self.diagnostics.get(recording.object_id, [])
        self.diagnostics[recording.object_id] = ([d for d in previous if d.get("region") != over]
            + [{**d.as_json(), "region": over} for d in expanded.diagnostics])
        explanation = composition.explain(
            expanded, self.chosen, context, self.planner.catalog, self.planner.parameters,
            evidence=recording.evidence, config_identity=getattr(self.planner, "config_identity", None),
            routes={name: {"configured": self.schedule.setting(name)["route"] or "local",
                           "transport_available": self.spool, "source": "backfill schedule"}
                    for name in self.planner.catalog.extractors},
            costs={name: {"value": self.schedule.setting(name)["cost"],
                          "remote_value": self.schedule.setting(name)["remote_cost"],
                          "units": "cpu-seconds/audio-second", "source": "backfill schedule recorded cost or override"}
                   for name in self.planner.catalog.extractors})
        for target, explained in zip(expanded.targets, explanation["targets"]):
            status, detail = self.planner.state(recording, Unit(target.extractor, target.source), region)
            explained["dispatch_observation"] = {"status": status, "reason": detail}
        previous_explanations = self.explanations.get(recording.object_id, [])
        self.explanations[recording.object_id] = ([e for e in previous_explanations if e["region"] != over]
                                                + [explanation])
        return unit_order([Unit(t.extractor, t.source) for t in expanded.targets], self.planner.catalog)

    def refresh(self, recording: Recording) -> list[Job]:
        """Reconcile newly selected inputs through the existing planner only."""
        if self.chosen is None or not self.chosen.produce:
            return []
        return self.recording(recording, refresh=True)

    def recording(self, recording: Recording, refresh: bool = False) -> list[Job]:
        """Plan what the mode asks for of one recording; the jobs it added."""
        if recording.object_id in self.recordings and not refresh:
            return []
        self.recordings.add(recording.object_id)
        plan_, planner, schedule = self.result, self.planner, self.schedule
        before = len(plan_.jobs)
        units = self.targets(recording)
        known = self.known | {u.extractor for u in units} | {u.producer for u in units if u.producer}
        choice = regions.plan(self.mode, known, recording.regions, schedule.costs(), schedule.expensive_above,
                              recording.length)
        for unit in units:
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
                    # A cached whole path and a region path can select different
                    # sources. Let the same resolver bind each actual scope.
                    scoped = self.targets(recording, region)
                    for candidate in scoped:
                        placement = choice[candidate.extractor]
                        candidate_whole = planner.state(recording, candidate)
                        if candidate.producer is not None and placement is not None:
                            # A cheap measurement follows the selected file feature's
                            # placement, just as in the whole-scope pass above.
                            placement = "whole" if candidate_whole[0] != WAITING else choice[candidate.producer]
                        if placement is None:
                            continue
                        self.want(recording, candidate, None if placement == "whole" else region,
                                  state=candidate_whole if placement == "whole" else None)
        return plan_.jobs[before:]


def plan(recordings: list[Recording], schedule: Schedule, planner: Planner, mode: str,
         shape: dict[str, dict[str, Any]], spool: bool,
         asked: list[dict[str, Any]] | None = None,
         chosen: composition.Selection | None = None) -> Plan:
    """The jobs the mode asks for that the bank lacks, and the bank requests
    (`asked`) in every mode, first; then song recordings first in `songs`
    mode. What each extractor is made over is `regions.plan`'s: whole, a
    recording's song regions, or nothing until asked for. A unit on a file
    feature goes where that feature's maker goes (whole where it is already
    made whole). Each job waits for the job in this plan that makes the
    feature it measures, the feature it would prefer, or the measure its
    preference probes, over the same region. Each job has its tier (fast or
    background, `Schedule.tier`); the runner orders by `Job.priority`."""
    planning = Planning(schedule, planner, mode, shape, spool, chosen)
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
    `overflow`), each at least its longest single job. Usable counts the
    proposals (`propose_seconds_per_recording`, recording by recording
    behind the fast pass); the background's batches are not counted."""
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
    # Each recording is proposed once its fast tier is made, one at a time
    # beside the pool: the last one's proposal comes after the fast pass, and
    # proposals slower than the fast pass bound it.
    proposal = schedule.propose_seconds_per_recording
    usable = max(fast / cores, longest_fast, proposal * len(recordings)) + proposal if recordings else 0.0
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


# -- the command ---------------------------------------------------------------------

class Log:
    def __init__(self, path: str | None):
        self.path = path
        if path:
            # Validate the destination and create an empty log even before an event.
            with open(path, "a", encoding="utf-8"):
                pass
        self.lock = threading.Lock()

    def __call__(self, **entry: Any) -> None:
        entry = {"at": time.strftime("%H:%M:%S"), **{k: v for k, v in entry.items() if v is not None}}
        line = json.dumps(entry, sort_keys=False)
        with self.lock:
            if self.path:
                # Callbacks may outlive the scheduler; no file lives with them.
                with open(self.path, "a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
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
    command.add_argument("--proposers", default=None, help="for propose --all; empty proposes nothing")
    command.add_argument("--world-store", help="refresh this World store and check its conformance at the end")
    command.add_argument("--world-python", default=sys.executable)
    command.add_argument("--conformance-python")
    command.add_argument("--log", help="append every event, one JSON line each, to this file")
    command.add_argument("--follow", action="store_true",
                         help="keep running: make bank requests as they arrive and new admissions (needs --commit)")
    command.add_argument("--follow-seconds", type=float,
                         help="with --follow, stop after this many seconds (default: until stopped)")


def export_of(args: argparse.Namespace, env: dict[str, str] | None) -> dict[str, Any]:
    try:
        done = subprocess.run([args.archive_command, "export"], cwd=args.archive, capture_output=True, text=True,
                              env=env)
    except OSError as exc:
        raise BackfillError(f"archive export failed: {exc}") from exc
    try:
        answer = json.loads(done.stdout)
    except ValueError:
        answer = None
    if done.returncode != 0 or not isinstance(answer, dict) or answer.get("status") != "ok":
        raise BackfillError(f"archive export failed: {done.stdout[-500:]} {done.stderr[-500:]}")
    return answer


def unprobed_of(export: dict[str, Any], archive: Path) -> list[str]:
    """The Artifacts `enrich propose --all` lists as unprobed: current
    Artifacts that no current audio_stream claim is about (their current
    revision), whose content is here and sniffs as audio or video."""
    current = heads(export["records"])
    about: set[tuple[str, str]] = set()
    for record in current.values():
        if record["shape"] == "claim" and any(c.get("id") == STREAM and c.get("version") == "1"
                                              for c in record.get("contracts", [])):
            about.update((ref.get("id"), ref.get("revision_id")) for ref in record.get("refs", [])
                         if ref.get("role") == "artifact")
    found = []
    for record in export["records"]:
        if record["shape"] != "artifact" or current.get(record["object_id"]) is not record \
                or (record["object_id"], record["revision_id"]) in about:
            continue
        path = content_path(archive, record["payload"]["content"]["digest"])
        try:
            media_type = extract.sniff_media_type(path) if path.is_file() else None
        except OSError:
            media_type = None
        if media_type and media_type.startswith(("audio/", "video/")):
            found.append(record["object_id"])
    return found


def discover(args: argparse.Namespace, env: dict[str, str] | None, steps: "Steps", planner: Planner,
             note: Callable[..., None], commit: bool) -> tuple[list[Recording], list[Recording]]:
    """Probe what `propose --all` would list as unprobed, then every recording of
    the archive (all, and those whose content is here), with its song
    regions and its place in the queue."""
    # Read from the export, as `propose --all` finds them (an export that
    # fails stops the backfill rather than planning without them); proposing
    # nothing to learn this cost the whole archive's proposals as a dry run.
    export = export_of(args, env)
    unprobed = unprobed_of(export, Path(args.archive))
    note(step="unprobed audio or video Artifacts", count=len(unprobed))
    for artifact in unprobed:
        steps.enrich(f"probe {artifact}", "probe", "--artifact", artifact, *([] if commit else ["--dry-run"]),
                     ok=(0, 3))
    if unprobed and commit:
        export = export_of(args, env)
    later = later_sources(export)
    recordings = [recording for recording in recordings_of(export["records"], Path(args.archive), set(unprobed))
                  if recording.object_id not in later]
    if later:
        note(step="later sources of their sessions; left out", count=len(later))
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


def selection_of(config: dict[str, Any], catalog: bank.Catalog, schedule: Schedule,
                 override: str | None) -> composition.Selection:
    try:
        chosen = composition.selection(config, "backfill", composition.Selection(
            attempt=tuple(PROPOSERS.split(",")),
            features=tuple(composition.FeatureTarget(u.extractor, u.source) for u in schedule.units)), override)
        composition.validate(chosen, catalog)
        return chosen
    except composition.CompositionError as exc:
        raise BackfillError(str(exc)) from exc


def candidate_units(chosen: composition.Selection, catalog: bank.Catalog) -> list[Unit]:
    """Reserve resources for finite recipes without scheduling their alternatives."""
    targets = list(chosen.features)
    for result in chosen.produce:
        for method in result.methods:
            for path in proposers.PROPOSERS[method].requirement.paths:
                targets.extend(path.production or ())
    units = [Unit(t.extractor, t.source) for t in targets]
    for target in targets:
        for name in catalog.upstream(target.extractor):
            units.append(Unit(name, target.source))
        if target.source:
            units.append(Unit(target.source.split("/", 1)[0]))
        for preferred in catalog[target.extractor].prefers:
            units.append(Unit(target.extractor, preferred["input"]))
            units.extend(probe_units(catalog, Unit(target.extractor)))
    return list(dict.fromkeys(units))


def main(args: argparse.Namespace, env: dict[str, str] | None = None,
         run_unit: Callable[[Job], tuple[dict[str, Any], float]] | None = None) -> int:
    original_proposers = args.proposers
    try:
        with extract.frozen_config(args, "config"):
            return _main(args, env, run_unit)
    except extract.RunError as exc:
        raise BackfillError(str(exc)) from exc
    finally:
        args.proposers = original_proposers


def _main(args: argparse.Namespace, env: dict[str, str] | None = None,
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
    for name in catalog.extractors:
        catalog.parameters(name, parameters)
    schedule = schedule_of(config, catalog)
    chosen = selection_of(config, catalog, schedule, args.proposers)
    schedule.units = [Unit(t.extractor, t.source) for t in chosen.features]
    resources = replace(schedule, units=candidate_units(chosen, catalog))
    args.proposers = ",".join(dict.fromkeys(
        [m for result in chosen.produce for m in result.methods] + list(chosen.attempt)))
    root = Path(args.bank).resolve()
    if args.spool:
        check_remote(catalog, root, parameters, resources)
    programs = {"python": sys.executable, "sh": "/bin/sh", **config.get("programs", {})}
    cores = args.cores or os.cpu_count() or 1
    memory_gb = args.memory_gb or max(1.0, machine_memory_gb() - schedule.memory_reserve_gb)
    if cores < 1 or memory_gb <= 0:
        raise BackfillError("--cores and --memory-gb must be positive")
    spool = remote.Spool(args.spool) if args.spool else None
    commit = args.commit
    steps = Steps(args, note, dict(os.environ if env is None else env))
    note(step="start", mode=args.mode, commit=commit, follow=args.follow or None, cores=cores,
         memory_gb=round(memory_gb, 1), bank=str(root))

    names = args.proposers.strip()
    root.mkdir(parents=True, exist_ok=True)  # an empty bank is a bank; propose refuses a missing one
    planner = Planner(catalog, root, parameters, programs, args.retry_failed)
    planner.config_identity = getattr(args, "_enrichment_config_snapshot", {}).get("identity")
    recordings, present = discover(args, env, steps, planner, note, commit)
    shape = layout(catalog, resources, parameters, cores, memory_gb)
    # Requests are read in every mode; a damaged one stops the backfill (bank.requests refuses it).
    asked = bank.requests(root)
    planned = plan(present, schedule, planner, args.mode, shape, spool is not None, asked, chosen)
    assert planned.planning is not None
    sources: dict[str, int] = {}
    for recording in present:
        sources[recording.span_source] = sources.get(recording.span_source, 0) + 1
    note(step="recordings", count=len(recordings), here=len(present),
         with_songs=sum(1 for r in present if r.songs), song_spans_from=sources, requests=len(asked))
    note(step="composition", recordings=planned.planning.diagnostics,
         explanations=planned.planning.explanations)
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
        follow = Follow(planned.planning, args, env, steps, note, root, {r.digest: r for r in present}, present,
                        discover, prioritise, report, request_ready=proposals.request_ready) if args.follow else None
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
            if job.status == "done":
                # Planning.result.jobs is the runner's list; append through want,
                # retaining tiers, priorities and predecessor failure semantics.
                planned.planning.refresh(job.recording)
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
        proposals.start(present, planned.jobs)
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
            committed = steps.proposed("propose --all (commit)", None)
            if proposals.usable_pending and committed:
                # The usable proposal failed and no batch succeeded since: the last proposal is the usable one.
                proposals.usable_pending = False
                proposals.usable_s = round(time.monotonic() - started, 1)
                note(step="usable", s=proposals.usable_s, first=True)
            rerun = steps.propose("propose --all (rerun: must be unchanged)")
            if isinstance(rerun, dict) and rerun.get("status") not in ("unchanged", "nothing_proposed"):
                steps.failed()
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
