"""`python -m enrich.bench cost`: the backfill's cost per second of audio, gated.

Runs every unit of the backfill's configuration (`enrich backfill`'s
`schedule_of`: by default `backfill.UNITS`, in that order) once over one
clip, each as the backfill runs it: `extract.py run` in a process of its
own, under the interpreter the root's `config.json` names, held to the
threads `backfill.layout` gives that extractor. It measures each unit's CPU
seconds (user and system, the process and its children) per second of
audio, the backfill's own `made` measure, and their sum. Every run starts
from an empty bank, so nothing is answered from an earlier run.

    "cost": {"python": "/path/to/enrich-python/bin/python3",
             "config": "cost/extract-config.json",
             "clip": "cost/clip.wav"}

`config` is extract.py's configuration (its programs and optional
`backfill` object), and `clip` a WAV file: a song with voice and bass, so
every unit has work. The result is filed under `results/cost/`, the
baseline is `baselines/cost/backfill.json`, and the gate fails when

- the sum regresses by more than `TOTAL_BOUND` (20%) of the baseline's, or
- one unit more than doubles: more than `UNIT_BOUND` (100%) of its baseline
  plus `UNIT_SLACK` (0.02 s per audio-second). Two runs on the same
  machine moved one cheap unit (rhythm, 0.032) by 2.2x and the sum by 10%,
  so the per-unit bound only catches what the sum would hide: a cheap unit
  that grows several times over; or
- time-to-usable regresses by more than `TOTAL_BOUND`: the fast tier's sum
  (ADR 0112, "usable": the units every recording waits for before it is
  usable), gated on its own so a fast unit that grows cannot hide behind
  a cheaper `stems`.

Each unit carries its backfill tier, and the result carries the two
headline numbers per audio-second as the backfill projects them on this
machine alone: `time_to_usable_per_audio_s` (the fast tier's CPU seconds
times the load factor, over the cores; `time_to_usable_clip_s` adds the
recorded seconds of one recording's usable proposal, `usable_proposal_s`,
from `backfill.SCHEDULE`, which this suite does not measure) and `time_to_done_per_audio_s` (every
unit's, likewise). A baseline recorded before tiers is compared by the
result's tiers.

CPU seconds, not wall seconds, are gated: the machine's other load moves the
wall far more than the CPU time. A result is compared only with a baseline
of the same clip, machine architecture and core count, and the same units.

Exit status: 0 within the bounds, 1 a regression, 2 the run could not happen.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import struct
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .sets import BenchError, sha256_file
from .variants import load_config

ENRICH = Path(__file__).resolve().parent.parent
EXTRACT = ENRICH / "extract.py"
FIELDS = {"python", "config", "clip"}
CONTRACT = "archive-bench-cost/1"
TOTAL_BOUND = 0.20
UNIT_BOUND = 1.00
UNIT_SLACK = 0.02


def _backfill():
    if str(ENRICH) not in sys.path:
        sys.path.insert(0, str(ENRICH))
    import backfill  # noqa: PLC0415  (standard library only)
    import extract  # noqa: PLC0415
    return backfill, extract


def settings(root: Path) -> dict[str, Path]:
    found = load_config(root).get("cost")
    if not isinstance(found, dict) or set(found) != FIELDS or not all(isinstance(v, str) for v in found.values()):
        raise BenchError(f"{root / 'config.json'}: cost must be {{{', '.join(sorted(FIELDS))}}}, each text")
    paths = {key: (root / value).expanduser() for key, value in found.items()}
    for key, path in paths.items():
        if not path.exists():
            raise BenchError(f"cost: {key} {path} does not exist")
    return paths


def wav_seconds(path: Path) -> float:
    """A WAV file's length from its header (PCM or float; `wave` reads only PCM)."""
    with path.open("rb") as handle:
        head = handle.read(12)
        if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
            raise BenchError(f"cost: {path} is not a WAV file")
        byte_rate = None
        while True:
            chunk = handle.read(8)
            if len(chunk) < 8:
                raise BenchError(f"cost: {path} has no data chunk")
            name, size = chunk[:4], struct.unpack("<I", chunk[4:])[0]
            if name == b"fmt ":
                fmt = handle.read(size + (size & 1))
                byte_rate = struct.unpack("<I", fmt[8:12])[0]
            elif name == b"data":
                if not byte_rate:
                    raise BenchError(f"cost: {path} has its data before its format")
                return size / byte_rate
            else:
                handle.seek(size + (size & 1), os.SEEK_CUR)


def plan(config_path: Path, cores: int) -> list[dict[str, Any]]:
    """The backfill's units in its order, each with the threads its layout gives."""
    backfill, extract = _backfill()
    try:
        config = extract.load_config(str(config_path))
        catalog = extract.catalog_of(argparse.Namespace(extractors=None), config)
        schedule = backfill.schedule_of(config, catalog)
        shape = backfill.layout(catalog, schedule, config.get("parameters", {}), cores, 1e6)
    except (extract.RunError, backfill.BackfillError) as exc:
        raise BenchError(f"cost: {exc}") from exc
    order = backfill.unit_order(list(schedule.units), catalog)
    return [{"unit": u.name, "extractor": u.extractor, "source": u.source,
             "threads": shape[u.extractor]["threads"], "tier": schedule.tier(u, catalog),
             "load_factor": schedule.load_factor,
             "propose_s": schedule.propose_seconds_per_recording} for u in order]


def fast_sum(rows: list[dict[str, Any]], tiers: dict[str, str]) -> float:
    """The fast tier's CPU seconds per audio-second, by `tiers` (unit to tier)."""
    return sum(r["cpu_per_audio_s"] for r in rows if tiers.get(r["unit"]) == "fast")


def run_unit(python: Path, config: Path, clip: Path, digest: str, bank: Path, unit: dict[str, Any]
             ) -> tuple[dict[str, Any], float, float]:
    """One `extract.py run`, as `backfill.run_extract` runs it: the answer,
    CPU seconds (it and its children) and wall seconds."""
    backfill, _ = _backfill()
    command = [str(python), str(EXTRACT), "run", unit["extractor"],
               *(["--from", unit["source"]] if unit["source"] else []),
               "--content", str(clip), "--digest", digest, "--bank", str(bank), "--jobs", "1",
               "--config", str(config)]
    environment = {**os.environ, **backfill.thread_environment(unit["threads"])}
    started = time.monotonic()
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        process = subprocess.Popen(command, stdout=out, stderr=err, stdin=subprocess.DEVNULL, env=environment)
        _, status, usage = os.wait4(process.pid, 0)
        wall = time.monotonic() - started
        out.seek(0)
        err.seek(0)
        text, errors = out.read().decode("utf-8", "replace"), err.read().decode("utf-8", "replace")
    try:
        answer = json.loads(text or errors)
    except ValueError:
        answer = {"status": "failed", "detail": (text + errors)[-1500:]}
    if os.waitstatus_to_exitcode(status) != 0:
        answer = {**answer, "status": "failed"} if isinstance(answer, dict) else {"status": "failed"}
    return answer, usage.ru_utime + usage.ru_stime, wall


Runner = Callable[[Path, Path, Path, str, Path, dict[str, Any]], tuple[dict[str, Any], float, float]]


def measure(root: Path, cores: int | None = None, runner: Runner = run_unit, log=print) -> dict[str, Any]:
    paths = settings(root)
    cores = cores or os.cpu_count() or 1
    seconds = wav_seconds(paths["clip"])
    found = sha256_file(paths["clip"])
    digest = found if found.startswith("sha256:") else "sha256:" + found
    units = plan(paths["config"], cores)
    rows = []
    with tempfile.TemporaryDirectory(prefix="bench-cost-", dir=root) as work:
        bank = Path(work) / "bank"
        for unit in units:
            answer, cpu, wall = runner(paths["python"], paths["config"], paths["clip"], digest, bank, unit)
            if not isinstance(answer, dict) or answer.get("status") != "ok":
                detail = answer.get("detail") if isinstance(answer, dict) else answer
                raise BenchError(f"cost: {unit['unit']} failed: {str(detail)[-600:]}")
            row = {**{k: v for k, v in unit.items() if k not in ("load_factor", "propose_s")}, "cpu_s": round(cpu, 2), "wall_s": round(wall, 2),
                   "cpu_per_audio_s": round(cpu / seconds, 4), "status": answer.get("status")}
            rows.append(row)
            log(f"  {unit['unit']:<38} {unit['threads']} thr  {cpu:7.1f} cpu s  {wall:7.1f} s  "
                f"{row['cpu_per_audio_s']:.4f} per audio s")
    total = sum(r["cpu_per_audio_s"] for r in rows)
    fast = fast_sum(rows, {r["unit"]: r["tier"] for r in rows})
    load = units[0]["load_factor"] if units else 1.0
    proposal = units[0]["propose_s"] if units else 0.0
    return {"contract": CONTRACT, "clip_sha256": digest[7:], "audio_seconds": round(seconds, 3),
            "machine": platform.machine(), "cores": cores,
            "finished": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "units": rows, "total_cpu_per_audio_s": round(total, 4),
            "fast_cpu_per_audio_s": round(fast, 4), "load_factor": load,
            "time_to_usable_per_audio_s": round(fast * load / cores, 4),
            # The usable proposal is not measured here (it needs an archive and
            # Door): the recorded seconds of one recording's, the tail after
            # the fast pass that proposals recording by recording leave.
            "usable_proposal_s": proposal,
            "time_to_usable_clip_s": round(seconds * fast * load / cores + proposal, 1),
            "time_to_done_per_audio_s": round(total * load / cores, 4),
            "bounds": {"total": TOTAL_BOUND, "unit": UNIT_BOUND, "unit_slack": UNIT_SLACK}}


def compare(result: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    """What regressed beyond the bounds; empty when the result passes.
    Raises when the two are not comparable."""
    for key in ("clip_sha256", "machine", "cores"):
        if result[key] != baseline[key]:
            raise BenchError(f"cost: not comparable: {key} {result[key]!r}, baseline {baseline[key]!r}")
    now = {r["unit"]: r for r in result["units"]}
    then = {r["unit"]: r for r in baseline["units"]}
    if sorted(now) != sorted(then):
        raise BenchError(f"cost: not comparable: units {sorted(now)}, baseline {sorted(then)}")
    failures = []
    total, base_total = result["total_cpu_per_audio_s"], baseline["total_cpu_per_audio_s"]
    if total > base_total * (1 + TOTAL_BOUND):
        failures.append(f"total {total:.4f} s per audio-second, up from {base_total:.4f} "
                        f"(bound +{TOTAL_BOUND:.0%})")
    tiers = {r["unit"]: r.get("tier") for r in result["units"]}
    fast, base_fast = fast_sum(result["units"], tiers), fast_sum(baseline["units"], tiers)
    if base_fast and fast > base_fast * (1 + TOTAL_BOUND):
        failures.append(f"time-to-usable: the fast tier {fast:.4f} s per audio-second, up from {base_fast:.4f} "
                        f"(bound +{TOTAL_BOUND:.0%})")
    for name, base in then.items():
        value, limit = now[name]["cpu_per_audio_s"], base["cpu_per_audio_s"] * (1 + UNIT_BOUND) + UNIT_SLACK
        if value > limit:
            failures.append(f"{name} {value:.4f}, up from {base['cpu_per_audio_s']:.4f} (limit {limit:.4f})")
    return failures


def baseline_path(root: Path) -> Path:
    return root / "baselines" / "cost" / "backfill.json"


def write(root: Path, result: dict[str, Any], baseline: bool = False) -> Path:
    if baseline:
        path = baseline_path(root)
    else:
        path = root / "results" / "cost" / f"{result['finished'].replace(':', '')}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=1) + "\n")
    return path


def check(root: Path, record_baseline: bool = False, runner: Runner = run_unit, log=print) -> int:
    result = measure(root, runner=runner, log=log)
    log(f"  total {result['total_cpu_per_audio_s']:.4f} CPU s per audio-second "
        f"({result['audio_seconds']:.0f} s clip, {result['cores']} cores)")
    log(f"  fast tier {result['fast_cpu_per_audio_s']:.4f} CPU s per audio-second: time-to-usable "
        f"{result['time_to_usable_per_audio_s']:.4f} s, time-to-done {result['time_to_done_per_audio_s']:.4f} s "
        f"per audio-second on this machine alone")
    log(f"  result {write(root, result)}")
    if record_baseline:
        log(f"  baseline {write(root, result, baseline=True)}")
        return 0
    path = baseline_path(root)
    if not path.is_file():
        raise BenchError(f"cost: no baseline at {path} (record one with --record-baseline)")
    baseline = json.loads(path.read_text())
    failures = compare(result, baseline)
    log(f"  baseline total {baseline['total_cpu_per_audio_s']:.4f} ({baseline['finished']})")
    if failures:
        log("REGRESSION:\n  " + "\n  ".join(failures))
        return 1
    log(f"ok: total within +{TOTAL_BOUND:.0%}, every unit within +{UNIT_BOUND:.0%} + {UNIT_SLACK}")
    return 0
