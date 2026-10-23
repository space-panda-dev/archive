"""Placing a chart on a recording (ADR 0077's designed alignment, built by
ADR 0116 section 6): `enrich align`.

A chart is three untimed claims about one document: a `meter_layers`
version 2 claim (its bars, each with its chords at offsets), the `form` it
pins (sections, repeats, endings, jumps) and the `chord_sequence` it pins
(each chord's readings). A recording is two machine claims about one stream:
a timed `meter_layers` claim with a beat-only layer (its downbeats from the
layer's `downbeats` or from a bars layer of the same claim) and a
`chord_stacks` claim.

The method, `chart-placement` version 1:

- The chart's **played bars** are World's order (`world.form.form_order`):
  every repeat unrolled, each jump taken once. A bar lasts a whole number of
  the machine's beats (`beats_of`); a bar the rule cannot count is refused.
- **Beat-synchronous dynamic programming** over (played bar, beat): bar p
  starts on beat j and lasts its beats, or one beat fewer or more at a
  penalty (the beat tracker dropped or added one). After the last played bar
  the order starts again: a performance plays the form once per chorus. A
  placement starts on the first played bar, or on the second when the first
  is shorter (a pickup), at any beat, and ends after any bar; beats before
  and after it are unplaced at a fixed cost each, so an intro or a tag the
  chart lacks stays unplaced.
- A placed beat costs the distance between the chart chord sounding at its
  middle and the machine chord at its middle (`distance`); a bar costs the
  sum over its beats, plus a penalty when its first beat is not a machine
  downbeat.
- Each of the twelve **transpositions** of the chart is placed. The best is
  kept only when its mean cost per placed beat is under `limit`, it places at
  least `min_pass` of one pass of the played bars, and the second-best
  transposition costs at least `margin` per placed beat more. Otherwise
  nothing is placed (`Refused`), and the chart stays a sequence.

A placement becomes three claims (`claims`): a timed `meter_layers`
version 2 pinning the untimed claim in `meter`, each bar `placed` from its
untimed bar; a `chord_stacks` version 2 pinning the `sequence`, each span the
chart chord's `entry`, every end `held` until the next chart chord, its
readings transposed (and without `text`) under a transposition; and, when the
chart has a form, a `form_spans` version 1 pinning it, a span per played
section. World's checks run on each before Door sees it.

Pure: the standard library, numpy, and World's modules; no archive access.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np

HERE = Path(__file__).resolve().parent


def world_module(name: str) -> Any:
    """One of World's standard-library modules, imported when first needed."""
    if str(HERE.parent) not in sys.path:
        sys.path.append(str(HERE.parent))
    import importlib
    return importlib.import_module(f"world.{name}")


METHOD_ID = "chart-placement"
METHOD_VERSION = "1"
AGENT = "pkg:generic/archive-enrich-align@0.1.0"
SOFTWARE = ("archive-enrich-align", "0.1.0")
HOLD_RULE = "until the next chart chord"
ALIGNMENT_METHOD = ("chart-placement 1: the chart's played bars on the machine's beats by beat-synchronous "
                    "dynamic programming, best of twelve transpositions (enrich align)")

# Every choice the method makes; the run's parameters Artifact holds them.
PARAMETERS: dict[str, Any] = {
    "method": {"id": METHOD_ID, "version": METHOD_VERSION},
    "played_order": "world.form.form_order: every repeat unrolled, each jump taken once; the order restarts "
                    "after its last bar (one pass per chorus)",
    "beats_per_bar": "a grouping's length; else the numerator over 4, twice it over 2, a third of it over 8 "
                     "when divisible by 3; any other bar is refused",
    "start": "the first played bar, or the second when the first is shorter (a pickup), on any beat",
    "beat_distance": "0.75 times one minus the Jaccard index of the chart's and the machine's pitch classes "
                     "(bass, root and tones; uncertain tones left out) plus 0.25 when the basses differ; "
                     "no chord against no chord 0, against a chord 1; a text-only reading, a missing reading "
                     "or a beat no machine span covers 0.5; each the first reading of its span or entry, "
                     "at the beat's middle",
    "unplaced_beat_cost": "0.55",
    "stretch_cost": "1.0 per beat a bar is placed shorter or longer than its count (at most one)",
    "downbeat_cost": "0.5 when a bar's first beat is not a machine downbeat; 0 when the machine names none",
    "limit": "0.35 mean cost per placed beat, at most",
    "margin": "0.05 per placed beat, at least, between the best transposition and the second",
    "min_pass": "0.5 of one pass of the played bars, at least",
    "hold_rule": HOLD_RULE,
}
UNPLACED = 0.55
STRETCH = 1.0
DOWNBEAT = 0.5
LIMIT = 0.35
MARGIN = 0.05
MIN_PASS = 0.5
NEUTRAL = 0.5


class AlignError(ValueError):
    """The inputs cannot be placed by this method (a bar it cannot count, a
    claim of the wrong shape): a refusal, never a guess."""


@dataclass(frozen=True)
class Refused:
    """Nothing placed, and why, with the numbers that decided it."""
    detail: str
    numbers: dict[str, Any]


# ---------------------------------------------------------------------------
# Chords as pitch-class sets

STACK, NO_CHORD, UNKNOWN = "stack", "no_chord", "unknown"


@dataclass(frozen=True)
class Tones:
    kind: str
    mask: int = 0          # 12-bit pitch-class set
    bass: int | None = None


def tones_of(reading: dict[str, Any] | None) -> Tones:
    """The pitch classes a reading certainly sounds; its uncertain tones are
    left out. A bass-only reading sounds its bass."""
    if not isinstance(reading, dict):
        return Tones(UNKNOWN)
    if reading.get("no_chord") is True:
        return Tones(NO_CHORD)
    if "bass" not in reading:
        return Tones(UNKNOWN)
    classes = {reading["bass"], *reading.get("tones", [])}
    if "root" in reading:
        classes.add(reading["root"])
    return Tones(STACK, sum(1 << pc for pc in classes), reading["bass"])


def rotate(mask: int, semitones: int) -> int:
    semitones %= 12
    return ((mask << semitones) | (mask >> (12 - semitones))) & 0xFFF


POPCOUNT = np.array([bin(n).count("1") for n in range(4096)], dtype=np.int64)


def distance(chart: Tones, machine_kind: np.ndarray, machine_mask: np.ndarray, machine_bass: np.ndarray,
             transposition: int) -> np.ndarray:
    """The cost of `chart` (transposed) against every machine beat."""
    out = np.full(machine_kind.shape, NEUTRAL)
    if chart.kind == UNKNOWN:
        return out
    stack, none = machine_kind == 1, machine_kind == 2
    if chart.kind == NO_CHORD:
        out[stack], out[none] = 1.0, 0.0
        return out
    mask = rotate(chart.mask, transposition)
    bass = (chart.bass + transposition) % 12
    inter = POPCOUNT[machine_mask & mask]
    union = POPCOUNT[machine_mask | mask]
    jaccard = np.where(union > 0, inter / np.maximum(union, 1), 0.0)
    out[stack] = (0.75 * (1.0 - jaccard) + 0.25 * (machine_bass != bass))[stack]
    out[none] = 1.0
    return out


# ---------------------------------------------------------------------------
# The chart


@dataclass(frozen=True)
class PlayedBar:
    bar: int                 # the untimed bar's index in its layer
    occurrence: int          # which played section this bar is in (0 without a form)
    section: int | None
    beats: int               # its length on the machine's beat grid
    length: Fraction         # in whole notes
    changes: tuple[tuple[Fraction, int], ...]   # (offset as a fraction of the bar, entry)
    held: int | None         # the entry sounding at its downbeat when none changes there


@dataclass(frozen=True)
class Chart:
    layer: int
    bars: tuple[PlayedBar, ...]
    entries: tuple[dict[str, Any], ...]
    weight_scale: str | None
    has_form: bool

    def entry_at(self, bar: PlayedBar, fraction: float) -> int | None:
        current = bar.held
        for offset, entry in bar.changes:
            if offset <= fraction:
                current = entry
        return current


def beats_of(bar: dict[str, Any]) -> int:
    """How many of the machine's beats a bar lasts (see PARAMETERS)."""
    numerator, denominator = bar["numerator"], bar["denominator"]
    if "grouping" in bar:
        return len(bar["grouping"])
    if denominator == 4:
        return numerator
    if denominator == 2:
        return numerator * 2
    if denominator == 8 and numerator % 3 == 0:
        return numerator // 3
    raise AlignError(f"a bar of {numerator}/{denominator} with no grouping has no beat count under this method")


def chart_of(meter: dict[str, Any], form: dict[str, Any] | None, sequence: dict[str, Any]) -> Chart:
    """The played bars of an untimed meter claim's first written layer (or
    its first layer), with the form and the sequence it pins (payloads)."""
    form_tools = world_module("form")
    layers = meter["layers"]
    layer = next((i for i, l in enumerate(layers) if l.get("role") == "written"), 0)
    bars = layers[layer]["bars"]
    if form is not None:
        if any("section" not in bar for bar in bars):
            raise AlignError("every bar needs a section for the form to order it")
        by_section: dict[int, list[int]] = {}
        for index, bar in enumerate(bars):
            by_section.setdefault(bar["section"], []).append(index)
        order = [(occurrence, section, index)
                 for occurrence, section in enumerate(form_tools.form_order(form))
                 for index in by_section.get(section, [])]
    else:
        order = [(0, None, index) for index in range(len(bars))]
    if not order:
        raise AlignError("the chart plays no bar")
    played, current = [], None
    for occurrence, section, index in order:
        bar = bars[index]
        length = Fraction(bar["numerator"], bar["denominator"])
        changes = []
        for chord in bar.get("chords", []):
            at = form_tools.note_length(chord["at"], "at", zero=True) if "at" in chord else Fraction(0)
            changes.append((at / length, chord["entry"]))
        held = current
        if changes:
            current = changes[-1][1]
        played.append(PlayedBar(index, occurrence, section, beats_of(bar), length, tuple(changes), held))
    entries = tuple(sequence["entries"])
    if any(entry >= len(entries) for bar in played for _, entry in bar.changes):
        raise AlignError("a bar names an entry the sequence lacks")
    return Chart(layer, tuple(played), entries, sequence.get("weight_scale"), form is not None)


# ---------------------------------------------------------------------------
# The recording


@dataclass(frozen=True)
class Machine:
    sample_rate: int
    sample_count: int
    grid: tuple[int, ...]            # the beats' samples, then the end of the last beat
    downbeats: frozenset[int]        # beat indices
    kind: np.ndarray                 # per beat: 0 unknown, 1 stack, 2 no chord
    mask: np.ndarray
    bass: np.ndarray

    @property
    def beats(self) -> int:
        return len(self.grid) - 1


def machine_of(meter: dict[str, Any], chords: dict[str, Any], sample_count: int) -> Machine:
    """The machine's beats and chords from a timed meter_layers payload (a
    beat-only layer; downbeats from it or from a bars layer) and a
    chord_stacks payload, both about one stream of `sample_count` samples."""
    rate = meter["sample_rate"]
    if chords["sample_rate"] != rate:
        raise AlignError("the machine's meter and chords count samples at different rates")
    beat_layers = [layer for layer in meter["layers"] if "beats" in layer]
    if not beat_layers:
        raise AlignError("the machine's meter claim has no beat-only layer")
    beats = list(beat_layers[0]["beats"])
    if len(beats) < 2:
        raise AlignError("the machine's meter claim has fewer than two beats")
    downbeat_samples = set(beat_layers[0].get("downbeats", []))
    for layer in meter["layers"]:
        if "bars" in layer:
            downbeat_samples |= {bar["start_sample"] for bar in layer["bars"]}
    step = int(np.median(np.diff(beats)))
    grid = tuple(beats) + (min(sample_count, beats[-1] + step),)
    if grid[-1] <= grid[-2]:
        raise AlignError("the last beat is at the end of the stream")
    index = {sample: n for n, sample in enumerate(beats)}
    downbeats = frozenset(index[s] for s in downbeat_samples if s in index)
    spans = chords["spans"]
    starts = [span["start_sample"] for span in spans]
    kind, mask, bass = (np.zeros(len(beats), dtype=np.int64) for _ in range(3))
    import bisect
    for n in range(len(beats)):
        middle = (grid[n] + grid[n + 1]) // 2
        at = bisect.bisect_right(starts, middle) - 1
        if at < 0 or spans[at]["end_sample"] <= middle:
            continue
        found = tones_of(spans[at]["readings"][0])
        kind[n] = {UNKNOWN: 0, STACK: 1, NO_CHORD: 2}[found.kind]
        mask[n] = found.mask
        bass[n] = -1 if found.bass is None else found.bass
    return Machine(rate, sample_count, grid, downbeats, kind, mask, bass)


# ---------------------------------------------------------------------------
# The placement


@dataclass(frozen=True)
class Placed:
    played: int    # index into Chart.bars
    beat: int      # first beat
    beats: int     # beats it lasts


@dataclass(frozen=True)
class Placement:
    transposition: int
    bars: tuple[Placed, ...]
    cost: float
    mean_cost: float
    second_cost: float
    margin: float
    passes: int

    def numbers(self) -> dict[str, Any]:
        return {"transposition": self.transposition, "placed_bars": len(self.bars),
                "placed_beats": sum(b.beats for b in self.bars), "passes": self.passes,
                "mean_cost": round(self.mean_cost, 4), "margin": round(self.margin, 4)}


def _bar_costs(chart: Chart, machine: Machine, transposition: int):
    """cost[(p, delta)]: an array over start beats of bar p placed over its
    beats plus delta, downbeat penalty included; inf where it would not fit."""
    B = machine.beats
    tones = [tones_of(entry["readings"][0]) for entry in chart.entries]
    cache: dict[int | None, np.ndarray] = {}

    def row(entry: int | None) -> np.ndarray:
        if entry not in cache:
            cache[entry] = (np.full(B, NEUTRAL) if entry is None else
                            distance(tones[entry], machine.kind, machine.mask, machine.bass, transposition))
        return cache[entry]

    downbeat = np.full(B + 1, DOWNBEAT if machine.downbeats else 0.0)
    downbeat[list(machine.downbeats)] = 0.0
    costs = {}
    for p, bar in enumerate(chart.bars):
        for delta in (-1, 0, 1):
            m = bar.beats + delta
            if m < 1:
                continue
            total = np.full(B + 1, np.inf)
            fits = B - m + 1
            if fits > 0:
                acc = downbeat[:fits].copy() + STRETCH * abs(delta)
                for k in range(m):
                    acc += row(chart.entry_at(bar, (k + 0.5) / m))[k:k + fits]
                total[:fits] = acc
            costs[(p, delta)] = total
    return costs


def _place(chart: Chart, machine: Machine, transposition: int) -> tuple[float, list[Placed]] | None:
    B, P = machine.beats, len(chart.bars)
    costs = _bar_costs(chart, machine, transposition)
    starts = [0]
    if P > 1 and chart.bars[0].length < chart.bars[1].length:
        starts.append(1)
    inf = np.inf
    cost = np.full((B + 1, P), inf)
    back_delta = np.full((B + 1, P), 9, dtype=np.int8)        # 9: entered from a start state
    back_from_start = np.zeros((B + 1, P), dtype=bool)
    nxt = np.array([(p + 1) % P for p in range(P)])
    groups: dict[tuple[int, int], np.ndarray] = {}
    for delta in (-1, 0, 1):
        for p, bar in enumerate(chart.bars):
            if bar.beats + delta >= 1:
                groups.setdefault((delta, bar.beats + delta), []).append(p)
    groups = {key: np.array(value) for key, value in groups.items()}
    start_row = np.full(P, inf)
    best_end, best_state = inf, None
    for j in range(B + 1):
        here = cost[j]
        if j < B:
            entered = start_row.copy()
            entered[starts] = UNPLACED * j
        else:
            entered = np.full(P, inf)
        # End after the bar that reached this state.
        finite = np.isfinite(here)
        if finite.any():
            ends = here + UNPLACED * (B - j)
            p = int(np.argmin(ends))
            if ends[p] < best_end:
                best_end, best_state = float(ends[p]), (j, p)
        if j == B:
            break
        use_start = entered < here
        base = np.where(use_start, entered, here)
        for (delta, m), ps in groups.items():
            target = j + m
            if target > B:
                continue
            candidate = base[ps] + np.array([costs[(p, delta)][j] for p in ps])
            to = nxt[ps]
            better = candidate < cost[target, to]
            if better.any():
                cost[target, to[better]] = candidate[better]
                back_delta[target, to[better]] = delta
                back_from_start[target, to[better]] = use_start[ps][better]
    if best_state is None:
        return None
    placed: list[Placed] = []
    j, p = best_state
    while True:
        prev = (p - 1) % P
        delta = int(back_delta[j, p])
        m = chart.bars[prev].beats + delta
        placed.append(Placed(prev, j - m, m))
        if back_from_start[j, p]:
            break
        j, p = j - m, prev
    placed.reverse()
    return best_end, placed


def place(chart: Chart, machine: Machine) -> Placement | Refused:
    """The best transposition's placement, or why there is none."""
    results = []
    for t in range(12):
        found = _place(chart, machine, t)
        if found is not None:
            results.append((found[0], t, found[1]))
    if not results:
        return Refused("no bar of the chart fits the recording's beats", {"beats": machine.beats})
    results.sort(key=lambda r: (r[0], r[1]))
    best, t, bars = results[0]
    second = results[1][0] if len(results) > 1 else math.inf
    placed_beats = sum(b.beats for b in bars)
    mean = (best - UNPLACED * (machine.beats - placed_beats)) / placed_beats
    margin = (second - best) / placed_beats
    passes = sum(1 for b in bars if b.played == 0) or 1
    placement = Placement(t, tuple(bars), best, mean, second, margin, passes)
    numbers = placement.numbers()
    if len(bars) < MIN_PASS * len(chart.bars):
        return Refused(f"only {len(bars)} bars placed, under {MIN_PASS} of the {len(chart.bars)} played bars",
                       numbers)
    if mean > LIMIT:
        return Refused(f"the mean cost per placed beat {mean:.3f} is over the limit {LIMIT}", numbers)
    if margin < MARGIN:
        return Refused(f"the best transposition leads the second by {margin:.3f} per placed beat, "
                       f"under the margin {MARGIN}", numbers)
    return placement


# ---------------------------------------------------------------------------
# Claims


def transpose_reading(reading: dict[str, Any], t: int) -> dict[str, Any] | None:
    """A reading moved up t semitones; it loses its text (the untransposed
    chart's) and its voicing. A text-only reading cannot move: None."""
    if t == 0:
        return dict(reading)
    if reading.get("no_chord") is True:
        return {key: value for key, value in reading.items() if key != "text"}
    if "bass" not in reading:
        return None
    moved: dict[str, Any] = {"bass": (reading["bass"] + t) % 12}
    if "root" in reading:
        moved["root"] = (reading["root"] + t) % 12
    if "tones" in reading:
        moved["tones"] = sorted((pc + t) % 12 for pc in reading["tones"])
    if "uncertain" in reading:
        groups = [{"pitch_classes": sorted((pc + t) % 12 for pc in g["pitch_classes"]), "present": g["present"]}
                  for g in reading["uncertain"]]
        moved["uncertain"] = sorted(groups, key=lambda g: g["pitch_classes"])
    if "weight" in reading:
        moved["weight"] = reading["weight"]
    return moved


def _sample(machine: Machine, beat: float) -> int:
    whole = min(int(math.floor(beat)), machine.beats - 1)
    fraction = beat - whole
    a, b = machine.grid[whole], machine.grid[whole + 1]
    return a + int(round(fraction * (b - a)))


def payloads(chart: Chart, meter: dict[str, Any], machine: Machine,
             placement: Placement) -> dict[str, dict[str, Any]]:
    """The three payloads of a placement: `meter`, `chords` and (with a form)
    `form`. Their references are the caller's."""
    untimed_bars = meter["layers"][chart.layer]["bars"]
    alignment = {"method": ALIGNMENT_METHOD}
    bars, spans, sections = [], [], []
    current: tuple[int, int] | None = None   # (entry, start sample) of the span open now
    # (pass, occurrence, start, section): a new span starts where the played
    # section changes or the played order restarts (a new chorus), so a form
    # of one section still gives a span per chorus.
    open_section: tuple[int, int, int, int | None] | None = None
    passes, previous = 0, None

    def close(end: int) -> None:
        nonlocal current
        if current is None:
            return
        entry, start = current
        readings = []
        for reading in chart.entries[entry]["readings"]:
            moved = transpose_reading(reading, placement.transposition)
            if moved is not None and moved not in readings:
                readings.append(moved)
        if readings and end > start:
            spans.append({"start_sample": start, "end_sample": end, "readings": readings, "entry": entry,
                          "end": "held"})
        current = None

    for placed in placement.bars:
        bar = chart.bars[placed.played]
        start, end = machine.grid[placed.beat], machine.grid[placed.beat + placed.beats]
        source = untimed_bars[bar.bar]
        timed: dict[str, Any] = {"start_sample": start, "end_sample": end, "numerator": source["numerator"],
                                 "denominator": source["denominator"]}
        if "grouping" in source:
            timed["grouping"] = source["grouping"]
        expected = len(source["grouping"]) if "grouping" in source else source["numerator"]
        if placed.beats == expected:
            timed["beats"] = list(machine.grid[placed.beat:placed.beat + placed.beats])
        timed["placed"] = {"layer": chart.layer, "bar": bar.bar}
        bars.append(timed)
        if current is None and bar.held is not None and not (bar.changes and bar.changes[0][0] == 0):
            current = (bar.held, start)
        for offset, entry in bar.changes:
            at = _sample(machine, placed.beat + float(offset) * placed.beats)
            close(at)
            current = (entry, at)
        if previous is not None and placed.played <= previous:
            passes += 1
        previous = placed.played
        if chart.has_form:
            if open_section is None or open_section[:2] != (passes, bar.occurrence):
                if open_section is not None:
                    sections.append({"start_sample": open_section[2], "end_sample": start,
                                     "section": open_section[3]})
                open_section = (passes, bar.occurrence, start, bar.section)
    last_end = machine.grid[placement.bars[-1].beat + placement.bars[-1].beats]
    close(last_end)
    if open_section is not None:
        sections.append({"start_sample": open_section[2], "end_sample": last_end, "section": open_section[3]})
    out = {"meter": {"sample_rate": machine.sample_rate, "alignment": alignment,
                     "layers": [{"role": "written", "bars": bars}]},
           "chords": {"sample_rate": machine.sample_rate, "alignment": alignment, "hold_rule": HOLD_RULE,
                      "spans": spans}}
    if chart.weight_scale is not None:
        out["chords"]["weight_scale"] = chart.weight_scale
    if chart.has_form:
        out["form"] = {"sample_rate": machine.sample_rate, "alignment": alignment,
                       "spans": [s for s in sections if s["end_sample"] > s["start_sample"]]}
    return out
