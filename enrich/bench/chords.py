"""The chord bench (ADR 0116 section 6, proposed): machine chords, key,
meter, downbeats and sections scored against a chart placed on a recording.

A chord set lives under the bench root like a note set, as
`sets/<name>.json` with contract `archive-bench-chord-set/1`. Each item names
the recording (its sha256 is the item's id), the chart document it was
placed from, and the **reference**: a JSON file
(`archive-bench-chord-reference/1`) holding the chart's chords, key, meter,
downbeats and sections in seconds over the placed stretch, with the
transposition and how the placement was made:

- `person`: a person tapped each played section's downbeat; bars between
  two taps divide the time by their beats (`reference_from_taps`);
- `chord-blind`: a run that saw beats, downbeats and form spans only. No
  such run is built yet; a reference may say it, and its downbeats are then
  not scored.

A reference is never made from the chords it scores. Scores, by mir_eval,
over the placed stretch only (`score_item`): `mir_eval.chord` root, majmin,
sevenths, tetrads and mirex, duration weighted, with names from World's
computed Harte strings on both sides; `mir_eval.key.weighted_score` against
the machine key region covering most of the stretch (a note collection with
no tonic is read as its major key, and `key_collection` says whether the
collections agree); `meter`, 1 when the machine's beats per bar equal the
chart's numerator; `downbeat_f`, `mir_eval.beat.f_measure` of downbeats
(person-placed references only); and `mir_eval.segment` boundary detection
at 0.5 s and 3 s with pairwise label agreement. A metric the machine gave
nothing for is absent, never zero; a mean is over the items that have it.

The gate is the horn set's: a mean (over all items and per subset) dropping
by more than the tolerance (0.02) fails.
"""

from __future__ import annotations

import json
import platform
import tempfile
import time
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Protocol

from .sets import DIGEST, BenchError, FileRef, _file, sha256_file

SET_CONTRACT = "archive-bench-chord-set/1"
REFERENCE_CONTRACT = "archive-bench-chord-reference/1"
RESULT_CONTRACT = "archive-bench-chord-result/1"
PLACEMENTS = ("person", "chord-blind")
CHORD_METRICS = ("root", "majmin", "sevenths", "tetrads", "mirex")
METRICS = (*CHORD_METRICS, "key", "key_collection", "meter", "downbeat_f",
           "boundary_f_0.5", "boundary_f_3", "pairwise_f")
DEFAULT_TOLERANCE = 0.02
PITCH_NAMES = ("C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")
MAJOR = frozenset({0, 2, 4, 5, 7, 9, 11})
MINOR = frozenset({0, 2, 3, 5, 7, 8, 10})


def world(name: str):
    import importlib
    import sys
    root = str(Path(__file__).resolve().parents[2])
    if root not in sys.path:
        sys.path.append(root)
    return importlib.import_module(f"world.{name}")


# ---------------------------------------------------------------------------
# The set


class ChordItem:
    def __init__(self, id: str, label: str, subset: str, recording: FileRef, chart: FileRef, reference: FileRef):
        self.id, self.label, self.subset = id, label, subset
        self.recording, self.chart, self.reference = recording, chart, reference


class ChordSet:
    def __init__(self, name: str, digest: str, items: tuple[ChordItem, ...]):
        self.name, self.digest, self.items = name, digest, items


def is_chord_set(raw: Any) -> bool:
    return isinstance(raw, dict) and raw.get("contract") == SET_CONTRACT


def parse_set(name: str, raw: Any, digest: str = "") -> ChordSet:
    """Validate a chord set manifest; every problem is reported at once."""
    errors: list[str] = []
    if not isinstance(raw, dict):
        raise BenchError(f"set {name}: the manifest must be a JSON object")
    if raw.get("contract") != SET_CONTRACT:
        errors.append(f"contract: must be {SET_CONTRACT!r}, not {raw.get('contract')!r}")
    if raw.get("set") != name:
        errors.append(f"set: names {raw.get('set')!r}, but the file is {name!r}")
    if raw.get("task") != "chords":
        errors.append(f"task: a chord set's task is 'chords', not {raw.get('task')!r}")
    items_raw = raw.get("items")
    if not isinstance(items_raw, list) or not items_raw:
        errors.append("items: must be a non-empty list")
        items_raw = []
    items, seen = [], set()
    for n, it in enumerate(items_raw):
        if not isinstance(it, dict):
            errors.append(f"items[{n}]: must be an object")
            continue
        where = f"items[{n}] ({it.get('label') if isinstance(it.get('label'), str) else n})"
        for key in ("id", "label", "subset"):
            if not isinstance(it.get(key), str) or not it.get(key):
                errors.append(f"{where}.{key}: must be a non-empty string")
        recording = _file(f"{where}.recording", it.get("recording"), errors, audio=True)
        chart = _file(f"{where}.chart", it.get("chart"), errors, audio=False)
        reference = _file(f"{where}.reference", it.get("reference"), errors, audio=False)
        if recording is not None and it.get("id") != recording.sha256:
            errors.append(f"{where}.id: must be the sha256 of its recording")
        if it.get("id") in seen:
            errors.append(f"{where}.id: listed twice")
        seen.add(it.get("id"))
        if not errors:
            items.append(ChordItem(it["id"], it["label"], it["subset"], recording, chart, reference))
    if errors:
        raise BenchError(f"set {name} is not valid:\n  " + "\n  ".join(errors))
    return ChordSet(name, digest, tuple(items))


def verify(root: Path, chord_set: ChordSet) -> None:
    """Every file present and unchanged, before any variant runs."""
    problems = []
    for item in chord_set.items:
        for what, ref in (("recording", item.recording), ("chart", item.chart), ("reference", item.reference)):
            path = root / ref.path
            if not path.is_file():
                problems.append(f"{item.label}: the {what} {path} is missing")
            elif sha256_file(path) != ref.sha256:
                problems.append(f"{item.label}: the {what} {path} has changed (its digest is not {ref.sha256})")
    if problems:
        raise BenchError(f"set {chord_set.name} cannot run:\n  " + "\n  ".join(problems))


# ---------------------------------------------------------------------------
# The reference


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _spans(value: Any, where: str, low: float, high: float, errors: list[str], contiguous: bool) -> None:
    if not isinstance(value, list) or not value:
        errors.append(f"{where}: must be a non-empty list of [start, end, label]")
        return
    previous = low
    for n, span in enumerate(value):
        if not (isinstance(span, list) and len(span) == 3 and _number(span[0]) and _number(span[1])
                and isinstance(span[2], str) and span[2]):
            errors.append(f"{where}[{n}]: must be [start, end, label]")
            return
        if span[0] < previous - 1e-9 or span[1] <= span[0] or span[1] > high + 1e-9:
            errors.append(f"{where}[{n}]: must rise, not overlap, and stay inside the stretch")
            return
        if contiguous and abs(span[0] - previous) > 1e-6:
            errors.append(f"{where}[{n}]: leaves a gap; a reference's held chords meet")
            return
        previous = span[1]
    if contiguous and abs(previous - high) > 1e-6:
        errors.append(f"{where}: must end at the stretch's end")


def parse_reference(raw: Any, item: ChordItem | None = None) -> dict[str, Any]:
    """Validate a reference, against its item when given; returns it."""
    errors: list[str] = []
    if not isinstance(raw, dict):
        raise BenchError("a reference must be a JSON object")
    if raw.get("contract") != REFERENCE_CONTRACT:
        errors.append(f"contract: must be {REFERENCE_CONTRACT!r}")
    for key in ("recording_sha256", "chart_sha256"):
        if not isinstance(raw.get(key), str) or not DIGEST.match(raw[key]):
            errors.append(f"{key}: must be sha256:<64 hex>")
    if item is not None and (raw.get("recording_sha256"), raw.get("chart_sha256")) != (item.recording.sha256,
                                                                                         item.chart.sha256):
        errors.append("recording_sha256 and chart_sha256 must be the item's recording and chart")
    if raw.get("placement") not in PLACEMENTS:
        errors.append(f"placement: one of {PLACEMENTS}")
    t = raw.get("transposition")
    if not isinstance(t, int) or isinstance(t, bool) or not 0 <= t <= 11:
        errors.append("transposition: an integer 0 to 11")
    stretch = raw.get("stretch")
    if not (isinstance(stretch, list) and len(stretch) == 2 and all(map(_number, stretch))
            and 0 <= stretch[0] < stretch[1]):
        errors.append("stretch: [start, end] seconds, start before end")
        stretch = [0.0, 0.0]
    _spans(raw.get("chords"), "chords", stretch[0], stretch[1], errors, contiguous=True)
    _spans(raw.get("sections"), "sections", stretch[0], stretch[1], errors, contiguous=False)
    if raw.get("key") is not None and not isinstance(raw.get("key"), str):
        errors.append("key: a mir_eval key ('Eb minor') or null")
    if not isinstance(raw.get("numerator"), int) or isinstance(raw.get("numerator"), bool) or raw["numerator"] < 1:
        errors.append("numerator: the chart's beats per bar, a positive integer")
    downbeats = raw.get("downbeats")
    if not isinstance(downbeats, list) or not all(map(_number, downbeats)) or downbeats != sorted(downbeats):
        errors.append("downbeats: ascending seconds")
    if errors:
        raise BenchError("the reference is not valid:\n  " + "\n  ".join(errors))
    return raw


def _harte(reading: dict[str, Any] | None) -> str:
    """World's Harte name of a reading: N for no chord, X when it names no stack."""
    if not isinstance(reading, dict):
        return "X"
    if reading.get("no_chord") is True:
        return "N"
    if "bass" not in reading:
        return "X"
    return world("harmony").stack_names(reading["bass"], reading.get("tones", []), reading.get("root"))[0].harte


def _key_name(reading: dict[str, Any] | None, t: int = 0) -> str | None:
    """A chart key reading (tonic and mode) as mir_eval names it, moved up t."""
    if not isinstance(reading, dict) or "tonic" not in reading:
        return None
    collection = frozenset((pc - reading["tonic"]) % 12 for pc in reading.get("collection", []))
    mode = "major" if collection == MAJOR else "minor" if collection == MINOR else None
    if mode is None:
        mode = {"ionian": "major", "major": "major", "aeolian": "minor", "minor": "minor"}.get(reading.get("mode"))
    return None if mode is None else f"{PITCH_NAMES[(reading['tonic'] + t) % 12]} {mode}"


def reference_from_taps(chart_claims: dict[str, Any], taps: list[float], *, recording_sha256: str,
                        chart_sha256: str, transposition: int = 0, first_occurrence: int = 0) -> dict[str, Any]:
    """A person's reference: `taps` are the downbeats of consecutive played
    sections from `first_occurrence` (the played order restarts after its
    last section, one pass per chorus), then the end of the last. The
    chart's claims are payloads: `meter` (untimed), `form` (or null),
    `sequence`, and optionally `key` (a key_sequence)."""
    from enrich import align
    chart = align.chart_of(chart_claims["meter"], chart_claims.get("form"), chart_claims["sequence"])
    if len(taps) < 2 or taps != sorted(taps) or len(set(taps)) != len(taps):
        raise BenchError("taps: at least two, strictly rising")
    occurrences: list[list[align.PlayedBar]] = []
    for bar in chart.bars:
        if not occurrences or occurrences[-1][0].occurrence != bar.occurrence:
            occurrences.append([])
        occurrences[-1].append(bar)
    bars: list[tuple[float, float, align.PlayedBar]] = []
    for n in range(len(taps) - 1):
        occurrence = occurrences[(first_occurrence + n) % len(occurrences)]
        start, end = taps[n], taps[n + 1]
        total = sum(b.beats for b in occurrence)
        at = 0
        for bar in occurrence:
            bars.append((start + (end - start) * at / total, start + (end - start) * (at + bar.beats) / total, bar))
            at += bar.beats
    def name(entry: int | None) -> str:
        if entry is None:
            return "X"
        reading = chart.entries[entry]["readings"][0]
        return _harte(align.transpose_reading(reading, transposition) if transposition else reading)

    chords: list[list[Any]] = []
    for start, end, bar in bars:
        events = list(bar.changes)
        if not chords and not (events and events[0][0] == 0):
            events.insert(0, (Fraction(0), bar.held))
        for offset, entry in events:
            at = start + (end - start) * float(offset)
            if chords:
                chords[-1][1] = at
            chords.append([at, None, name(entry)])
    chords[-1][1] = taps[-1]
    chords = [c for c in chords if c[1] > c[0]]
    sections = []
    for n in range(len(taps) - 1):
        occurrence = occurrences[(first_occurrence + n) % len(occurrences)][0]
        label = "section" if occurrence.section is None else str(occurrence.section)
        if chart_claims.get("form") is not None and occurrence.section is not None:
            label = chart_claims["form"]["sections"][occurrence.section].get("label", label)
        sections.append([taps[n], taps[n + 1], label])
    key = None
    if chart_claims.get("key"):
        regions = chart_claims["key"].get("regions", [])
        if regions:
            key = _key_name(regions[0]["readings"][0], transposition)
    numerators = [chart_claims["meter"]["layers"][chart.layer]["bars"][b.bar]["numerator"] for _, _, b in bars]
    return {"contract": REFERENCE_CONTRACT, "recording_sha256": recording_sha256, "chart_sha256": chart_sha256,
            "placement": "person", "transposition": transposition, "stretch": [taps[0], taps[-1]],
            "chords": chords, "key": key, "numerator": max(set(numerators), key=numerators.count),
            "downbeats": [start for start, _, _ in bars], "sections": sections}


# ---------------------------------------------------------------------------
# Scoring


def _clip(spans: list[list[Any]], low: float, high: float, fill: str):
    import mir_eval
    import numpy as np
    inside = [s for s in spans if s[1] > low and s[0] < high]
    if not inside:
        return np.array([[low, high]]), [fill]
    intervals = np.array([[max(s[0], low), min(s[1], high)] for s in inside], dtype=float)
    labels = [s[2] for s in inside]
    return mir_eval.util.adjust_intervals(intervals, labels, low, high, fill, fill)


def _machine_key(regions: list[list[Any]], low: float, high: float) -> tuple[str | None, frozenset[int] | None]:
    """The machine key region covering most of the stretch: (mir_eval name,
    collection). A collection with no tonic is read as its major key."""
    best, best_overlap = None, 0.0
    for start, end, collection, tonic, mode in regions:
        overlap = min(end, high) - max(start, low)
        if overlap > best_overlap:
            best, best_overlap = (collection, tonic, mode), overlap
    if best is None:
        return None, None
    collection, tonic, mode = best
    classes = frozenset(collection)
    if tonic is None:
        tonic = next((t for t in range(12) if frozenset((pc - t) % 12 for pc in classes) == MAJOR), None)
        mode = "ionian"
    if tonic is None:
        return None, classes
    return _key_name({"tonic": tonic, "collection": sorted(classes), "mode": mode}), classes


def _collection(name: str | None) -> frozenset[int] | None:
    if name is None:
        return None
    tonic, mode = name.split(" ")
    pc = PITCH_NAMES.index(tonic)
    return frozenset((pc + i) % 12 for i in (MAJOR if mode == "major" else MINOR))


def score_item(reference: dict[str, Any], estimate: dict[str, Any]) -> dict[str, float | None]:
    """Every metric of one item over the reference's stretch (see the module)."""
    import mir_eval
    import numpy as np
    low, high = reference["stretch"]
    out: dict[str, float | None] = dict.fromkeys(METRICS)
    if not estimate.get("chords"):
        raise BenchError("the variant gave no chords: a chord item cannot be scored without them")
    ref_i, ref_l = _clip(reference["chords"], low, high, "N")
    est_i, est_l = _clip(estimate["chords"], low, high, "N")
    intervals, ref_labels, est_labels = mir_eval.util.merge_labeled_intervals(ref_i, ref_l, est_i, est_l)
    durations = mir_eval.util.intervals_to_durations(intervals)
    for metric in CHORD_METRICS:
        comparisons = getattr(mir_eval.chord, metric)(ref_labels, est_labels)
        out[metric] = float(mir_eval.chord.weighted_accuracy(comparisons, durations))
    if reference.get("key") and estimate.get("keys"):
        name, collection = _machine_key(estimate["keys"], low, high)
        if name is not None:
            out["key"] = float(mir_eval.key.weighted_score(reference["key"], name))
        if collection is not None:
            out["key_collection"] = 1.0 if collection == _collection(reference["key"]) else 0.0
    if estimate.get("numerator") is not None:
        out["meter"] = 1.0 if estimate["numerator"] == reference["numerator"] else 0.0
    if reference["placement"] == "person" and estimate.get("downbeats") is not None:
        inside = np.array([t for t in estimate["downbeats"] if low <= t <= high])
        out["downbeat_f"] = float(mir_eval.beat.f_measure(np.array(reference["downbeats"]), inside))
    if estimate.get("sections"):
        ri, rl = _clip(reference["sections"], low, high, "none")
        ei, el = _clip(estimate["sections"], low, high, "none")
        ri, ei = ri - low, ei - low   # mir_eval.segment counts from 0
        out["boundary_f_0.5"] = float(mir_eval.segment.detection(ri, ei, window=0.5)[2])
        out["boundary_f_3"] = float(mir_eval.segment.detection(ri, ei, window=3.0)[2])
        out["pairwise_f"] = float(mir_eval.segment.pairwise(ri, rl, ei, el)[2])
    return out


# ---------------------------------------------------------------------------
# Variants: what a machine says about one recording


class ChordVariant(Protocol):
    name: str
    version: str

    def parameters(self) -> dict[str, Any]: ...

    def provenance(self) -> dict[str, Any]: ...

    def estimate(self, item: ChordItem, audio: Path, work: Path) -> dict[str, Any]: ...


NOMINAL_RATE = 48000   # bank times become samples and back; any rate keeps them to a sample


class BankProposers:
    """What `enrich propose` would claim from a filled feature bank, in
    seconds: the named chord proposer's first readings as World's Harte
    names, the `beats` proposer's bars (downbeats and their most common
    numerator), the `key` proposer's regions and the `sections` proposer's
    spans. The bench never runs an extractor here: the bank is filled
    beforehand (`enrich/extract.py run`), and a feature it lacks is absent.

    Settings: `bank` (the bank's root), optional `chords` (a chord proposer,
    default chords-jiang) and `config` (the extractor runner's config)."""

    name = "bank-proposers"
    version = "1"

    def __init__(self, root: Path, settings: dict[str, Any]) -> None:
        bank = settings.get("bank")
        if not isinstance(bank, str) or not (root / bank).expanduser().is_dir():
            raise BenchError(f"variant {self.name}: config.json names no bank directory")
        self.bank = (root / bank).expanduser()
        self.chords = settings.get("chords", "chords-jiang")
        self.config = settings.get("config")

    def parameters(self) -> dict[str, Any]:
        return {"chords": self.chords, "meter": "beats", "key": "key", "sections": "sections",
                "readings": "each span's first reading, named by World's stack_names"}

    def provenance(self) -> dict[str, Any]:
        return {"bank": "named by config.json"}

    def estimate(self, item: ChordItem, audio: Path, work: Path) -> dict[str, Any]:
        import argparse
        import sys
        enrich = str(Path(__file__).resolve().parents[1])
        if enrich not in sys.path:
            sys.path.insert(0, enrich)
        import propose
        import proposers
        store = propose.bank_of(argparse.Namespace(bank=str(self.bank), config=self.config))
        count = int(item.recording.seconds * NOMINAL_RATE) + 1
        context = proposers.Context(store, item.id, NOMINAL_RATE, count)

        def payload(name: str) -> dict[str, Any] | None:
            found = proposers.PROPOSERS[name].propose(context)
            return None if isinstance(found, proposers.Lacking) else found.outputs[0].payload

        seconds = lambda sample: sample / NOMINAL_RATE
        chords = payload(self.chords)
        if chords is None:
            raise BenchError(f"{item.label}: the bank lacks what {self.chords} reads")
        out: dict[str, Any] = {"chords": [[seconds(s["start_sample"]), seconds(s["end_sample"]),
                                           _harte(s["readings"][0])] for s in chords["spans"]]}
        meter = payload("beats")
        bars = [] if meter is None else [bar for layer in meter["layers"] for bar in layer.get("bars", [])]
        if bars:
            numerators = [bar["numerator"] for bar in bars]
            out["numerator"] = max(set(numerators), key=numerators.count)
            out["downbeats"] = [seconds(bar["start_sample"]) for bar in bars]
        keys = payload("key")
        if keys is not None:
            out["keys"] = [[seconds(r["start_sample"]), seconds(r["end_sample"]), r["readings"][0]["collection"],
                            r["readings"][0].get("tonic"), r["readings"][0].get("mode")]
                           for r in keys["regions"] if "collection" in r["readings"][0]]
        sections = payload("sections")
        if sections is not None:
            out["sections"] = [[seconds(s["start_sample"]), seconds(s["end_sample"]), s.get("label", "?")]
                               for s in sections["spans"]]
        return out


VARIANTS: dict[str, Callable[[Path, dict[str, Any]], ChordVariant]] = {BankProposers.name: BankProposers}


def build(name: str, root: Path) -> ChordVariant:
    from .variants import load_config
    if name not in VARIANTS:
        raise BenchError(f"no chord variant {name!r}; known: {', '.join(sorted(VARIANTS))}")
    settings = load_config(root)["variants"].get(name)
    if not isinstance(settings, dict):
        raise BenchError(f"variant {name}: {root / 'config.json'} has no settings for it")
    return VARIANTS[name](root, settings)


# ---------------------------------------------------------------------------
# Running, recording, the gate


def _means(rows: list[dict[str, Any]]) -> dict[str, Any]:
    means: dict[str, Any] = {}
    for metric in METRICS:
        values = [r[metric] for r in rows if r[metric] is not None]
        means[metric] = round(sum(values) / len(values), 4) if values else None
    return {**means, "items": len(rows)}


def run_set(root: Path, chord_set: ChordSet, variant: ChordVariant, log=print) -> dict[str, Any]:
    verify(root, chord_set)
    rows = []
    for item in chord_set.items:
        reference = parse_reference(json.loads((root / item.reference.path).read_text(encoding="utf-8")), item)
        with tempfile.TemporaryDirectory(prefix="bench-chords-") as work:
            started = time.monotonic()
            estimate = variant.estimate(item, root / item.recording.path, Path(work))
            seconds = time.monotonic() - started
        row = {"id": item.id, "label": item.label, "subset": item.subset, "reference_sha256": item.reference.sha256,
               "placement": reference["placement"],
               **{k: (None if v is None else round(v, 4)) for k, v in score_item(reference, estimate).items()},
               "seconds": round(seconds, 2)}
        rows.append(row)
        log(f"  {item.label:<10} mirex {row['mirex']:.2f}  majmin {row['majmin']:.2f}  key {row['key']}")
    subsets = sorted({r["subset"] for r in rows})
    return {"contract": RESULT_CONTRACT, "set": chord_set.name, "set_sha256": chord_set.digest, "input": "recording",
            "variant": {"name": variant.name, "version": variant.version, "parameters": variant.parameters(),
                        "provenance": variant.provenance()},
            "scoring": {"metrics": list(METRICS), "scorer": _scorer()},
            "finished": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "machine": platform.machine(),
            "items": rows,
            "means": {"all": _means(rows), **{s: _means([r for r in rows if r["subset"] == s]) for s in subsets}}}


def _scorer() -> str:
    import mir_eval
    return f"mir_eval {mir_eval.__version__}"


def compare(result: dict[str, Any], baseline: dict[str, Any], tolerance: float = DEFAULT_TOLERANCE) -> list[str]:
    """What regressed beyond the tolerance; empty when the result passes.
    Raises when the two are not comparable."""
    if result["variant"]["name"] != baseline["variant"]["name"]:
        raise BenchError(f"cannot compare {result['variant']['name']} with a baseline of {baseline['variant']['name']}")
    ids = lambda doc: sorted((r["id"], r["reference_sha256"]) for r in doc["items"])
    if ids(result) != ids(baseline):
        raise BenchError("the result and the baseline cover different items or references")
    failures = []
    for group, base in baseline["means"].items():
        now = result["means"].get(group)
        if now is None:
            raise BenchError(f"the result has no mean for {group}")
        for metric in METRICS:
            if base.get(metric) is None:
                continue
            if now.get(metric) is None:
                raise BenchError(f"{group} {metric}: the baseline has it and the result does not")
            if base[metric] - now[metric] > tolerance:
                failures.append(f"{group} {metric}: {now[metric]:.4f}, down from {base[metric]:.4f} "
                                f"(tolerance {tolerance})")
    return failures


def summary(result: dict[str, Any]) -> str:
    lines = [f"{result['set']} · {result['variant']['name']} ({result['variant']['version']})"]
    for group, m in result["means"].items():
        shown = "  ".join(f"{k} {m[k]:.3f}" for k in METRICS if m[k] is not None)
        lines.append(f"  mean {group:<9} {shown}  ({m['items']} items)")
    return "\n".join(lines)
