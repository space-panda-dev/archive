"""Running a variant over a set, recording the result, and the regression gate.

A result is a JSON document under `<root>/results/<set>/<variant>/`; a
baseline is a result copied to `<root>/baselines/<set>/<variant>@<input>.json`.
The gate compares means (over all items and per subset) of F1 with and
without offsets, and fails when any drops by more than the tolerance. Cost
(seconds of wall time per minute of audio, model loading included) is
recorded and printed beside it, not gated: it depends on the machine's load.
"""

from __future__ import annotations

import json
import platform
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .score import read_midi, score, scorer_version
from .sets import BenchError, BenchSet, resolve, verify
from .variants import Variant

RESULT_CONTRACT = "archive-bench-result/1"
DEFAULT_TOLERANCE = 0.02
GATED = ("f1", "f1_offsets")


def _mean(rows: list[dict[str, Any]]) -> dict[str, Any]:
    keys = ("precision", "recall", "f1", "f1_offsets")
    return {**{k: round(sum(r[k] for r in rows) / len(rows), 4) for k in keys}, "items": len(rows)}


def cost(seconds: float, audio_seconds: float) -> float:
    """Seconds of work per minute of audio."""
    if audio_seconds <= 0:
        raise ValueError("audio length must be positive")
    return round(seconds / (audio_seconds / 60.0), 2)


def run_set(root: Path, bench_set: BenchSet, variant: Variant, input_name: str | None = None,
            log=print) -> dict[str, Any]:
    names = verify(root, bench_set, input_name)   # every file present and unchanged, before any work
    rows = []
    for item, name in zip(bench_set.items, names):
        audio = item.inputs[name]
        reference = read_midi(resolve(root, item.reference))
        with tempfile.TemporaryDirectory(prefix="bench-") as work:
            started = time.monotonic()
            estimate = variant.transcribe(resolve(root, audio), Path(work))
            seconds = time.monotonic() - started
        row = {"id": item.id, "label": item.label, "subset": item.subset, "input": name,
               "audio_sha256": audio.sha256, "reference_sha256": item.reference.sha256,
               "reference_notes": len(reference), "estimated_notes": len(estimate),
               **{k: round(v, 4) for k, v in score(reference, estimate, bench_set.scoring).items()},
               "seconds": round(seconds, 2), "audio_seconds": audio.seconds,
               "seconds_per_minute": cost(seconds, audio.seconds)}
        rows.append(row)
        log(f"  {item.label:<10} {name:<7} F1 {row['f1']:.2f} / {row['f1_offsets']:.2f}  "
            f"{row['seconds_per_minute']:.1f} s/min")
    subsets = sorted({r["subset"] for r in rows})
    total_s, total_audio = sum(r["seconds"] for r in rows), sum(r["audio_seconds"] for r in rows)
    return {
        "contract": RESULT_CONTRACT, "set": bench_set.name, "set_sha256": bench_set.digest,
        "input": input_name or "primary",
        "variant": {"name": variant.name, "version": variant.version, "parameters": variant.parameters(),
                    "provenance": variant.provenance()},
        "scoring": {**bench_set.scoring, "scorer": scorer_version()},
        "finished": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "machine": platform.machine(),
        "items": rows,
        "means": {"all": _mean(rows), **{s: _mean([r for r in rows if r["subset"] == s]) for s in subsets}},
        "cost": {"seconds": round(total_s, 1), "audio_seconds": round(total_audio, 1),
                 "seconds_per_minute": cost(total_s, total_audio)},
    }


def write_result(root: Path, result: dict[str, Any]) -> Path:
    folder = root / "results" / result["set"] / result["variant"]["name"]
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{result['finished'].replace(':', '')}-{result['input']}"
    path, n = folder / f"{stem}.json", 1
    while path.exists():   # two runs in one second
        n += 1
        path = folder / f"{stem}-{n}.json"
    path.write_text(json.dumps(result, indent=1) + "\n")
    return path


def baseline_path(root: Path, set_name: str, variant: str, input_name: str) -> Path:
    return root / "baselines" / set_name / f"{variant}@{input_name}.json"


def record_baseline(root: Path, result: dict[str, Any]) -> Path:
    path = baseline_path(root, result["set"], result["variant"]["name"], result["input"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=1) + "\n")
    return path


def compare(result: dict[str, Any], baseline: dict[str, Any], tolerance: float = DEFAULT_TOLERANCE) -> list[str]:
    """What regressed beyond the tolerance; empty when the result passes.
    Raises when the two are not comparable (other items, scoring or variant)."""
    def scoring(doc):
        return {k: v for k, v in doc["scoring"].items() if k != "scorer"}
    if result["variant"]["name"] != baseline["variant"]["name"]:
        raise BenchError(f"cannot compare {result['variant']['name']} with a baseline of {baseline['variant']['name']}")
    if scoring(result) != scoring(baseline):
        raise BenchError("the result and the baseline were scored with different tolerances")
    ids = lambda doc: sorted((r["id"], r["input"], r["reference_sha256"]) for r in doc["items"])
    if ids(result) != ids(baseline):
        raise BenchError("the result and the baseline cover different items, inputs or references")
    failures = []
    for group, base in baseline["means"].items():
        now = result["means"].get(group)
        if now is None:
            raise BenchError(f"the result has no mean for {group}")
        for key in GATED:
            if base[key] - now[key] > tolerance:
                failures.append(f"{group} {key}: {now[key]:.4f}, down from {base[key]:.4f} (tolerance {tolerance})")
    return failures


def summary(result: dict[str, Any]) -> str:
    lines = [f"{result['set']} · {result['variant']['name']} ({result['variant']['version']}) · input {result['input']}"]
    for group, m in result["means"].items():
        lines.append(f"  mean {group:<9} F1 {m['f1']:.3f}  F1+offsets {m['f1_offsets']:.3f}  "
                     f"P {m['precision']:.3f}  R {m['recall']:.3f}  ({m['items']} items)")
    lines.append(f"  cost {result['cost']['seconds_per_minute']:.1f} s per minute of audio "
                 f"({result['cost']['seconds']:.0f} s for {result['cost']['audio_seconds'] / 60:.1f} min)")
    return "\n".join(lines)
