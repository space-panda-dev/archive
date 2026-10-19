"""Proposers: features of the bank read as claims (ADR 0079, D1.4).

A proposer is a named enrichment method that reads bank entries of one
recording and makes claims from them. It never runs an extractor and never
measures audio: what it reads is in the bank, found by the keys the
installed extractors give (ADR 0073), or it proposes nothing and says which
features it lacked. Its id is its run's method, so the first current rule of
ADR 0082 can rank machines by it: each chord extractor has its own proposer,
named as the extractor is.

Each proposer here returns a `Proposal`: the claims' contracts and payloads
(the command adds the `source`, `stream`, and `run` pins), the features it
read with their provenance, the software those entries recorded, and its
settings. `propose.py` turns that into one derivation run and its claims in
one Door batch, and checks every claim with World's own rules first.

Times: bank spans and events are in seconds from the start of the decoded
stream, the first audio stream, which the pinned stream claim describes. A
time `t` is the sample `round(t * sample_rate)` of that stream, half to even,
computed exactly from the decimal text the bank wrote, and clamped to 0 and
`sample_count`. A span that becomes empty is dropped; events that land on
one sample are one.

Only `bass`, `bass-line`, and the notes proposer's bleed filter need numpy,
to average the stem's chroma and levels; everything else uses the standard
library.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import bank  # noqa: E402
import chord_labels  # noqa: E402


def world_module(name: str) -> Any:
    """One of World's standard-library modules (its contract checks and
    tables), imported when first needed, so every other enrich command runs
    from a copy of `enrich/` alone."""
    if str(HERE.parent) not in sys.path:
        sys.path.append(str(HERE.parent))
    import importlib
    return importlib.import_module(f"world.{name}")

CHORD_STACKS = ("archive.claim/chord_stacks", "2")
METER_LAYERS = ("archive.claim/meter_layers", "2")
KEY_REGIONS = ("archive.claim/key_regions", "1")
TUNING = ("archive.claim/tuning", "1")
NOTES = ("archive.claim/notes", "1")
FORM_SPANS = ("archive.claim/form_spans", "1")

MAX_CHORD_SPANS = 100000   # chord stacks version 2; a longer run is split into claims of one run
MAX_BEATS = 100000
MAX_BARS = 10000
MAX_REGIONS = 1000
MAX_DRIFT = 10000
MAX_NOTES = 100000
MAX_SECTIONS = 10000
MAX_TEXT = 200

TIMES = ("a time t in seconds is sample round(t * sample_rate) of the pinned stream, half to even, "
         "from the decimal text the bank wrote, clamped to 0 and sample_count; an empty span is dropped "
         "and events on one sample are one")


class ProposerError(Exception):
    """A proposer found features it cannot read as a claim."""


@dataclass(frozen=True)
class Context:
    """One recording's bank and the stream its claims count samples of."""
    bank: bank.Bank
    content: str        # the mix Artifact's content digest, which every entry is filed under
    sample_rate: int
    sample_count: int


@dataclass(frozen=True)
class Planned:
    """One claim a proposer makes; the command adds its pins."""
    contract: tuple[str, str]
    name: str
    payload: dict[str, Any]


@dataclass
class Proposal:
    outputs: list[Planned]
    settings: dict[str, Any]
    features: list[dict[str, Any]] = field(default_factory=list)
    software: list[tuple[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class Lacking:
    """Nothing to propose: the features absent from the bank, or, when they
    are present, why they hold nothing to claim. `code` names a refusal a
    caller can act on, such as `not_yet` for a method declared but not built."""
    missing: tuple[str, ...] = ()
    detail: str | None = None
    code: str | None = None

    def as_json(self) -> dict[str, Any]:
        answer: dict[str, Any] = {}
        if self.code:
            answer["code"] = self.code
        if self.missing:
            answer["missing"] = list(self.missing)
        if self.detail:
            answer["detail"] = self.detail
        return answer


@dataclass(frozen=True)
class Proposer:
    id: str
    version: str
    propose: Callable[[Context], Proposal | Lacking]


# -- shared ----------------------------------------------------------------

def sample_of(seconds: Any, rate: int, count: int) -> int:
    """A bank time in seconds as a sample of the stream (see the module)."""
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        raise ProposerError(f"a bank time must be a number, not {seconds!r}")
    value = (Decimal(repr(seconds)) * rate).quantize(Decimal(1), rounding=ROUND_HALF_EVEN)
    return min(max(int(value), 0), count)


def decimal_text(value: Any, places: int | None = None) -> str:
    """Exact decimal text of a bank number, as written or rounded half to
    even to `places`."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProposerError(f"not a number: {value!r}")
    number = Decimal(repr(value))
    if not number.is_finite():
        raise ProposerError(f"not a finite number: {value!r}")
    if places is not None:
        number = number.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN)
    text = format(number, "f")
    if re.fullmatch(r"-0(\.0*)?", text):
        text = text[1:]
    return text


def read(proposal: Proposal, context: Context, entry: bank.Entry, output: str) -> None:
    """Record that the proposal read one output of an entry: the feature's
    name, its extractor, the entry's key, the content it measured and the
    content it is filed under, and a file's digest; and the entry's software."""
    provenance = entry.provenance(output)
    record = {"feature": provenance["feature"], "extractor": provenance["extractor"], "entry": provenance["entry"],
              "content": entry.manifest["content"], "filed_under": context.content}
    described = entry.output(output)
    if described["kind"] == "file":
        record["sha256"] = described["sha256"]
    proposal.features.append(record)
    for item in provenance["software"]:
        pair = (item["name"], item["version"])
        if pair not in proposal.software:
            proposal.software.append(pair)


def stem_citation(entry: bank.Entry, output: str) -> dict[str, str]:
    """A file output of the bank cited by bank key (ADR 0079, D1.1)."""
    described = entry.output(output)
    if described["kind"] != "file":
        raise ProposerError(f"{entry.manifest['extractor']['id']}/{output} is not a file to cite")
    return {"extractor": entry.manifest["extractor"]["id"], "key": entry.manifest["key"], "output": output,
            "sha256": described["sha256"]}


def split(name: str, contract: tuple[str, str], payload: dict[str, Any], spans: list[dict[str, Any]],
          cap: int) -> list[Planned]:
    """One claim, or consecutive claims of one run of at most `cap` spans
    each: a gap asserts nothing, so a split loses nothing."""
    if len(spans) <= cap:
        return [Planned(contract, name, {**payload, "spans": spans})]
    return [Planned(contract, f"{name}-{number}", {**payload, "spans": spans[start:start + cap]})
            for number, start in enumerate(range(0, len(spans), cap))]


def timed_spans(items: list[dict[str, Any]], context: Context, what: str) -> list[tuple[int, int, dict[str, Any]]]:
    """Bank spans as (start, end, span) samples, empty ones dropped, refused
    when two overlap: a claim's spans never do."""
    found, previous = [], 0
    for item in items:
        start = sample_of(item["start"], context.sample_rate, context.sample_count)
        end = sample_of(item["end"], context.sample_rate, context.sample_count)
        if end <= start:
            continue
        if start < previous:
            raise ProposerError(f"{what} has spans that overlap at {item['start']} s")
        found.append((start, end, item))
        previous = end
    return found


def weight_scale(text: str) -> str:
    if not 0 < len(text) <= MAX_TEXT:
        raise ProposerError(f"a weight scale must be 1 to {MAX_TEXT} characters: {text!r}")
    return text


# -- chords ----------------------------------------------------------------

def readings_of(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Readings in the extractor's order, a later one equal to an earlier dropped."""
    kept: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate not in kept:
            kept.append(candidate)
    return kept


def propose_jiang(context: Context) -> Proposal | Lacking:
    entry = context.bank.entry(context.content, "chords-jiang")
    if entry is None:
        return Lacking(("chords-jiang/chords", "chords-jiang/readings"))
    labelled = entry.items("chords")
    try:
        document = json.loads(entry.file("readings").read_text(encoding="utf-8"))
        candidates = document["spans"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ProposerError(f"chords-jiang/readings of {entry.path} cannot be read: {exc}") from exc
    if not isinstance(candidates, list) or len(candidates) != len(labelled) or any(
            not isinstance(c, dict) or (c.get("start"), c.get("end")) != (s["start"], s["end"])
            for c, s in zip(candidates, labelled)):
        raise ProposerError(f"chords-jiang/readings of {entry.path} does not describe its chords spans one by one")
    proposal = Proposal([], {"readings": "the extractor's weighted candidates in its order, each read from its "
                                         "Harte label; N is no chord; an unread label is text only",
                             "weights": "the extractor's own, as exact decimal text", "times": TIMES})
    read(proposal, context, entry, "chords")
    read(proposal, context, entry, "readings")
    order = {id(item): index for index, item in enumerate(labelled)}
    spans = []
    for start, end, item in timed_spans(labelled, context, "chords-jiang/chords"):
        found = []
        for pair in candidates[order[id(item)]].get("top", []):
            if not (isinstance(pair, list) and len(pair) == 2 and isinstance(pair[0], str)):
                raise ProposerError(f"chords-jiang/readings of {entry.path} has a candidate that is not [label, weight]")
            found.append({**chord_labels.harte_reading(pair[0]), "weight": decimal_text(pair[1])})
        readings = readings_of(found)
        if readings:
            spans.append({"start_sample": start, "end_sample": end, "readings": readings})
    if not spans:
        return Lacking(detail="chords-jiang found no chord span to claim")
    scale = weight_scale(f"chords-jiang {entry.manifest['extractor']['version']} readings: "
                         f"{entry.manifest['parameters']['readings']['weight']}")
    proposal.outputs = split("chords", CHORD_STACKS, {"sample_rate": context.sample_rate, "weight_scale": scale},
                             spans, MAX_CHORD_SPANS)
    return proposal


def propose_chordino(context: Context) -> Proposal | Lacking:
    entry = context.bank.entry(context.content, "chords-chordino")
    if entry is None:
        return Lacking(("chords-chordino/chords",))
    proposal = Proposal([], {"readings": "one a span, read from Chordino's label; N is no chord; an unread label "
                                         "is text only", "times": TIMES})
    read(proposal, context, entry, "chords")
    spans = [{"start_sample": start, "end_sample": end, "readings": [chord_labels.chordino_reading(item["label"])]}
             for start, end, item in timed_spans(entry.items("chords"), context, "chords-chordino/chords")
             if isinstance(item.get("label"), str)]
    if not spans:
        return Lacking(detail="chords-chordino found no chord span to claim")
    proposal.outputs = split("chords", CHORD_STACKS, {"sample_rate": context.sample_rate}, spans, MAX_CHORD_SPANS)
    return proposal


# -- beats -----------------------------------------------------------------

METER = re.compile(r"([1-9][0-9]?)/([1-9][0-9]?)\Z")
DENOMINATORS = (1, 2, 4, 8, 16, 32, 64)


def bars_of(pulse: list[dict[str, Any]], downbeats: list[dict[str, Any]], context: Context) -> list[dict[str, Any]]:
    """A bar from each published downbeat to the pulse its value of beats
    later, in the meter its label states, holding the pulses inside it as
    its beats when their count is the numerator or divides it evenly (as
    equal groups), and no beats otherwise. A downbeat that is not a pulse,
    whose bar would run past the last pulse, overlap the bar before, or whose
    label is not a meter makes no bar."""
    samples = [sample_of(event["time"], context.sample_rate, context.sample_count) for event in pulse]
    index_of: dict[float, int] = {}
    for index, event in enumerate(pulse):
        index_of.setdefault(event["time"], index)
    bars, previous_end = [], 0
    for downbeat in downbeats:
        first = index_of.get(downbeat["time"])
        meter = METER.fullmatch(downbeat.get("label", ""))
        value = downbeat.get("value")
        if first is None or meter is None or isinstance(value, bool) or not isinstance(value, (int, float)) \
                or value != int(value) or int(value) < 1:
            continue
        last = first + int(value)
        numerator, denominator = int(meter.group(1)), int(meter.group(2))
        if last >= len(pulse) or denominator not in DENOMINATORS or not 1 <= numerator <= 64:
            continue
        start, end = samples[first], samples[last]
        if end <= start or start < previous_end or end > context.sample_count:
            continue
        bar: dict[str, Any] = {"start_sample": start, "end_sample": end, "numerator": numerator,
                               "denominator": denominator}
        inside = sorted({s for s in samples[first:last] if start <= s < end})
        if len(inside) == numerator:
            bar["beats"] = inside
        elif numerator % len(inside) == 0:
            bar["grouping"] = [numerator // len(inside)] * len(inside)
            bar["beats"] = inside
        bars.append(bar)
        previous_end = end
    return bars


def propose_beats(context: Context) -> Proposal | Lacking:
    entry = context.bank.entry(context.content, "rhythm")
    if entry is None:
        return Lacking(("rhythm/pulse", "rhythm/downbeats"))
    pulse, downbeats = entry.items("pulse"), entry.items("downbeats")
    proposal = Proposal([], {"beats": "a beat-only layer of rhythm/pulse, with no downbeats and no role",
                             "bars": "from each rhythm/downbeats event to the pulse its value of beats later, in the "
                                     "meter its label states; the pulses inside as beats when their count is the "
                                     "numerator or divides it evenly, as equal groups; no role",
                             "times": TIMES})
    read(proposal, context, entry, "pulse")
    read(proposal, context, entry, "downbeats")
    beats = sorted({sample_of(event["time"], context.sample_rate, context.sample_count) for event in pulse}
                   - {context.sample_count})
    if not beats:
        return Lacking(detail="rhythm/pulse holds no pulse inside the stream")
    if len(beats) > MAX_BEATS:
        raise ProposerError(f"rhythm/pulse holds {len(beats)} beats; a layer holds at most {MAX_BEATS}")
    layers: list[dict[str, Any]] = [{"beats": beats}]
    bars = bars_of(pulse, downbeats, context)
    if len(bars) > MAX_BARS:
        raise ProposerError(f"rhythm/downbeats make {len(bars)} bars; a layer holds at most {MAX_BARS}")
    if bars:
        layers.append({"bars": bars})
    proposal.outputs = [Planned(METER_LAYERS, "meter", {"sample_rate": context.sample_rate, "layers": layers})]
    return proposal


# -- bass ------------------------------------------------------------------

BASS_SETTINGS = {
    "intervals": "between consecutive rhythm/pulse events of the mix; a frame of the stem's tonal entry belongs "
                 "to the interval holding its centre",
    "min_pitched_share": "0.5",
    "min_lead": "0.2",
    "lead": "(strongest class - next class) / strongest class of the interval's mean tonal/bass_chroma, "
            "rounded half to even to 4 places",
    "bin_0": "A",
    "merge": "neighbouring intervals with the same bass are one span, weighted by their least lead",
}
BASS_SCALE = "lead of the strongest bass class over the next, bank bass proposer version 1"


def bass_spans(chroma: Any, pitched: Any, grid: dict[str, int], pulse_times: list[float],
               context: Context) -> list[dict[str, Any]]:
    """The bass of each interval between two pulses (see BASS_SETTINGS)."""
    import numpy
    chroma = numpy.asarray(chroma, dtype=numpy.float64)
    pitched = numpy.asarray(pitched, dtype=numpy.float64).reshape(-1)
    if chroma.ndim != 2 or chroma.shape[1] != 12 or len(pitched) != chroma.shape[0]:
        raise ProposerError("tonal/bass_chroma and tonal/pitched of the bass stem are not one frame grid of 12 classes")
    centres = (grid["first_centre"] + numpy.arange(chroma.shape[0]) * grid["hop"]) / grid["sample_rate"]
    min_share, min_lead = Decimal(BASS_SETTINGS["min_pitched_share"]), Decimal(BASS_SETTINGS["min_lead"])
    times = sorted(set(pulse_times))
    found: list[tuple[int, int, int, Decimal]] = []
    for a, b in zip(times, times[1:]):
        start = sample_of(a, context.sample_rate, context.sample_count)
        end = sample_of(b, context.sample_rate, context.sample_count)
        frames = numpy.nonzero((centres >= a) & (centres < b))[0]
        if end <= start or len(frames) == 0:
            continue
        if Decimal(repr(float(pitched[frames].mean()))) < min_share:
            continue
        mean = chroma[frames].mean(axis=0)
        order = numpy.argsort(-mean, kind="stable")
        top, second = float(mean[order[0]]), float(mean[order[1]])
        if not top > 0:
            continue
        lead = Decimal(repr((top - second) / top)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)
        if lead < min_lead:
            continue
        found.append((start, end, (int(order[0]) + 9) % 12, lead))
    spans: list[dict[str, Any]] = []
    for start, end, bass, lead in found:
        last = spans[-1] if spans else None
        if last and last["end_sample"] == start and last["readings"][0]["bass"] == bass:
            last["end_sample"] = end
            last["readings"][0]["weight"] = format(min(Decimal(last["readings"][0]["weight"]), lead), "f")
        else:
            spans.append({"start_sample": start, "end_sample": end,
                          "readings": [{"bass": bass, "weight": format(lead, "f")}]})
    return spans


def propose_bass(context: Context) -> Proposal | Lacking:
    stems = context.bank.entry(context.content, "stems")
    measured = None if stems is None else context.bank.entry_from(context.content, "stems/bass", "tonal")
    rhythm = context.bank.entry(context.content, "rhythm")
    missing = []
    if stems is None:
        missing.append("stems/bass")
    if measured is None:
        missing += ["tonal/bass_chroma from stems/bass", "tonal/pitched from stems/bass"]
    if rhythm is None:
        missing.append("rhythm/pulse")
    if missing:
        return Lacking(tuple(missing))
    assert stems is not None and measured is not None and rhythm is not None
    proposal = Proposal([], {**BASS_SETTINGS, "times": TIMES})
    read(proposal, context, stems, "bass")
    read(proposal, context, measured, "bass_chroma")
    read(proposal, context, measured, "pitched")
    read(proposal, context, rhythm, "pulse")
    grid = measured.grid("bass_chroma")
    if grid is None or grid != measured.grid("pitched"):
        raise ProposerError("tonal/bass_chroma and tonal/pitched of the bass stem do not share a frame grid")
    spans = bass_spans(measured.series("bass_chroma"), measured.series("pitched"), grid,
                       [event["time"] for event in rhythm.items("pulse")], context)
    if not spans:
        return Lacking(detail="no interval between pulses has a pitched bass that leads by the margin")
    payload = {"sample_rate": context.sample_rate, "stem": stem_citation(stems, "bass"), "weight_scale": BASS_SCALE}
    proposal.outputs = split("bass", CHORD_STACKS, payload, spans, MAX_CHORD_SPANS)
    return proposal


# -- the bass line (ADR 0086, amended 2026-10-19) ---------------------------

BASS_LINE_SETTINGS = {
    "notes": "notes-basic-pitch/notes measured from stems/bass, each its midi rounded half to even",
    "min_pitched_share": BASS_SETTINGS["min_pitched_share"],
    "min_lead": BASS_SETTINGS["min_lead"],
    "keep": "a note is kept when the frames of tonal from stems/bass whose centres lie within it are on average "
            "at least min_pitched_share pitched and, where the strongest class of their mean bass_chroma leads "
            "the next by at least min_lead, that class is the note's; a note over no frame is dropped",
    "lead": BASS_SETTINGS["lead"],
    "bin_0": BASS_SETTINGS["bin_0"],
    "line": "one note at a time: of kept notes on one onset the lowest; a lower note cuts the one sounding at "
            "its onset; a higher note starting under a sounding one is dropped",
    "bass": "true: the bass line is a part of its own, never a melody",
}


def bass_line(rows: list[list[Any]], chroma: Any, pitched: Any, grid: dict[str, int],
              context: Context) -> list[dict[str, int]]:
    """The bass stem's notes as one line (see BASS_LINE_SETTINGS). Rows are
    notes-basic-pitch's: onset_s, offset_s, midi, ..."""
    import numpy
    chroma = numpy.asarray(chroma, dtype=numpy.float64)
    pitched = numpy.asarray(pitched, dtype=numpy.float64).reshape(-1)
    if chroma.ndim != 2 or chroma.shape[1] != 12 or len(pitched) != chroma.shape[0]:
        raise ProposerError("tonal/bass_chroma and tonal/pitched of the bass stem are not one frame grid of 12 classes")
    centres = (grid["first_centre"] + numpy.arange(chroma.shape[0]) * grid["hop"]) / grid["sample_rate"]
    min_share, min_lead = Decimal(BASS_SETTINGS["min_pitched_share"]), Decimal(BASS_SETTINGS["min_lead"])
    kept: list[dict[str, int]] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 3:
            raise ProposerError(f"notes-basic-pitch/notes has a row that is not onset, offset, midi: {row!r}")
        onset_s, offset_s, midi = row[0], row[1], row[2]
        if isinstance(midi, bool) or not isinstance(midi, (int, float)):
            continue
        pitch = int(Decimal(repr(midi)).quantize(Decimal(1), ROUND_HALF_EVEN))
        onset = sample_of(onset_s, context.sample_rate, context.sample_count)
        offset = sample_of(offset_s, context.sample_rate, context.sample_count)
        if not 0 <= pitch <= 127 or offset <= onset or onset >= context.sample_count:
            continue
        frames = numpy.nonzero((centres >= onset_s) & (centres < offset_s))[0]
        if len(frames) == 0 or Decimal(repr(float(pitched[frames].mean()))) < min_share:
            continue
        mean = chroma[frames].mean(axis=0)
        order = numpy.argsort(-mean, kind="stable")
        top, second = float(mean[order[0]]), float(mean[order[1]])
        if top > 0:
            lead = Decimal(repr((top - second) / top)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)
            if lead >= min_lead and (int(order[0]) + 9) % 12 != pitch % 12:
                continue
        kept.append({"onset_sample": onset, "offset_sample": offset, "pitch": pitch})
    line: list[dict[str, int]] = []
    for note in sorted(kept, key=lambda n: (n["onset_sample"], n["pitch"])):
        last = line[-1] if line else None
        if last is None or note["onset_sample"] >= last["offset_sample"]:
            line.append(dict(note))
        elif note["onset_sample"] == last["onset_sample"] or note["pitch"] >= last["pitch"]:
            continue  # the lowest on an onset came first; a higher note under a sounding one is dropped
        else:
            last["offset_sample"] = note["onset_sample"]
            line.append(dict(note))
    return line


def bass_line_entries(context: Context) -> tuple[bank.Entry | None, bank.Entry | None, bank.Entry | None, list[str]]:
    """The stems entry, notes-basic-pitch and tonal from stems/bass, and what is missing."""
    stems = context.bank.entry(context.content, "stems")
    notes = None if stems is None else context.bank.entry_from(context.content, "stems/bass", "notes-basic-pitch")
    measured = None if stems is None else context.bank.entry_from(context.content, "stems/bass", "tonal")
    missing = []
    if stems is None:
        missing.append("stems/bass")
    if notes is None:
        missing.append("notes-basic-pitch/notes from stems/bass")
    if measured is None:
        missing += ["tonal/bass_chroma from stems/bass", "tonal/pitched from stems/bass"]
    return stems, notes, measured, missing


def read_bass_line(proposal: Proposal, context: Context, notes: bank.Entry,
                   measured: bank.Entry) -> list[dict[str, int]]:
    read(proposal, context, notes, "notes")
    read(proposal, context, measured, "bass_chroma")
    read(proposal, context, measured, "pitched")
    grid = measured.grid("bass_chroma")
    if grid is None or grid != measured.grid("pitched"):
        raise ProposerError("tonal/bass_chroma and tonal/pitched of the bass stem do not share a frame grid")
    document = json.loads(notes.file("notes").read_text(encoding="utf-8"))
    return bass_line(document.get("notes", []), measured.series("bass_chroma"), measured.series("pitched"), grid,
                     context)


def propose_bass_line(context: Context) -> Proposal | Lacking:
    stems, notes, measured, missing = bass_line_entries(context)
    if missing:
        return Lacking(tuple(missing))
    assert stems is not None and notes is not None and measured is not None
    proposal = Proposal([], {**BASS_LINE_SETTINGS, "times": TIMES})
    read(proposal, context, stems, "bass")
    line = read_bass_line(proposal, context, notes, measured)
    if not line:
        return Lacking(detail="no note of the bass stem is pitched and agrees with its bass chroma")
    if len(line) > MAX_NOTES:
        raise ProposerError(f"the bass line holds {len(line)} notes; a claim holds at most {MAX_NOTES}")
    proposal.outputs = [Planned(NOTES, "bass-line", {"sample_rate": context.sample_rate, "part": "bass",
                                                     "stem": stem_citation(stems, "bass"), "bass": True,
                                                     "notes": line})]
    return proposal


# -- separation bleed in a vocal line (ADR 0086, amended 2026-10-19) --------

BLEED_SETTINGS = {
    "applies_to": "notes read from a vocal stem; notes of the mix are proposed as measured",
    "range_floor": "the duration-weighted 10th percentile of the run's note pitches, before any is dropped",
    "below_range_semitones": "7",
    "below_range": "a note more than below_range_semitones below range_floor is dropped",
    "quiet_db": "20",
    "unvoiced": "the voice counts as unvoiced over a note when the median of melody/level_db over the rows whose "
                "centres lie within it is more than quiet_db below melody/loud_db",
    "bass_match": "a note whose class is that of the bass line's notes for at least half its length, while the "
                  "voice is unvoiced, is dropped; the bass line is the bass-line proposer's",
}


def drop_bleed(notes: list[dict[str, int]], levels: Any, grid: dict[str, int] | None, loud_db: Any,
               bass: list[dict[str, int]] | None, rate: int) -> tuple[list[dict[str, int]], dict[str, int]]:
    """A vocal line without separation bleed (see BLEED_SETTINGS), and how
    many notes each rule dropped. `bass` None: no bass line to compare."""
    import numpy
    dropped = {"below_range": 0, "bass_match": 0}
    if not notes:
        return notes, dropped
    pitches = numpy.array([n["pitch"] for n in notes], dtype=numpy.float64)
    weights = numpy.array([n["offset_sample"] - n["onset_sample"] for n in notes], dtype=numpy.float64)
    order = numpy.argsort(pitches, kind="stable")
    cumulative = numpy.cumsum(weights[order])
    floor = pitches[order][int(numpy.searchsorted(cumulative, 0.1 * cumulative[-1]))]
    lowest = floor - int(BLEED_SETTINGS["below_range_semitones"])
    kept: list[dict[str, int]] = []
    levels = None if levels is None else numpy.asarray(levels, dtype=numpy.float64).reshape(-1)
    for note in notes:
        if note["pitch"] < lowest:
            dropped["below_range"] += 1
            continue
        if bass and levels is not None and grid is not None and loud_db is not None:
            onset, offset = note["onset_sample"], note["offset_sample"]
            shared = sum(max(0, min(offset, b["offset_sample"]) - max(onset, b["onset_sample"]))
                         for b in bass if b["pitch"] % 12 == note["pitch"] % 12)
            if 2 * shared >= offset - onset:
                first = grid["first_centre"] / grid["sample_rate"]
                step = grid["hop"] / grid["sample_rate"]
                low = max(0, int(numpy.ceil((onset / rate - first) / step)))
                high = min(len(levels), int(numpy.ceil((offset / rate - first) / step)))
                if high > low and float(numpy.median(levels[low:high])) < float(loud_db) - float(
                        BLEED_SETTINGS["quiet_db"]):
                    dropped["bass_match"] += 1
                    continue
        kept.append(note)
    return kept, dropped


# -- key, tuning, notes, sections (named in --proposers only) ---------------

KEY_LABEL = re.compile(r"(?P<tonic>[A-G][#b]?) (?P<mode>[a-z]+(?: minor)?)(?P<scale> scale)?\Z")
SCALE_MODES = {"major": "ionian", "harmonic minor": "harmonic minor", "melodic minor": "melodic minor"}
# Tonal versions whose key_regions value is the tonic's confidence; since
# version 6 it is the collection's margin, and every label is a collection.
TONIC_VALUED = ("4", "5")
TONIC_MODES = ("ionian", "dorian", "phrygian", "lydian", "mixolydian", "aeolian", "locrian",
               "harmonic minor", "melodic minor")


def key_reading(label: str) -> dict[str, Any]:
    """A key reading of a tonal key region's label: `F dorian` (a tonic and
    its mode) or `Eb major scale` (a note collection, no tonic). Anything
    else is text only."""
    found = KEY_LABEL.fullmatch(label)
    if found is None:
        return {"text": label}
    MODES = world_module("tonality").MODES
    tonic = chord_labels.note_class(found.group("tonic"))
    mode = found.group("mode")
    if found.group("scale"):
        if mode not in SCALE_MODES:
            return {"text": label}
        [intervals] = MODES[SCALE_MODES[mode]]
        return {"collection": sorted((tonic + i) % 12 for i in intervals), "text": label}
    if mode not in TONIC_MODES:
        return {"text": label}
    [intervals] = MODES[mode]
    return {"collection": sorted((tonic + i) % 12 for i in intervals), "tonic": tonic, "mode": mode, "text": label}


def propose_key(context: Context) -> Proposal | Lacking:
    entry = context.bank.entry(context.content, "tonal")
    if entry is None:
        return Lacking(("tonal/key_regions",))
    proposal = Proposal([], {"regions": "a region per tonal/key_regions span: its label's note collection (tonal 6 "
                                        "names no tonic), or a tonic and mode where an earlier tonal "
                                        "labelled one; weighted by the span's value",
                             "weights": "rounded half to even to 4 places", "times": TIMES})
    read(proposal, context, entry, "key_regions")
    regions = [{"start_sample": start, "end_sample": end,
                "readings": [{**key_reading(item.get("label", "")), "weight": decimal_text(item.get("value", 0), 4)}]}
               for start, end, item in timed_spans(entry.items("key_regions"), context, "tonal/key_regions")
               if item.get("label")]
    if not regions:
        return Lacking(detail="tonal/key_regions holds no region")
    if len(regions) > MAX_REGIONS:
        raise ProposerError(f"tonal/key_regions holds {len(regions)} regions; a claim holds at most {MAX_REGIONS}")
    version = entry.manifest["extractor"]["version"]
    scale = weight_scale(f"tonal {version} key_regions value: "
                         + ("the tonic's confidence given the collection" if version in TONIC_VALUED else
                            "how far the collection's best correlation leads the best reading on other notes"))
    proposal.outputs = [Planned(KEY_REGIONS, "keys", {"sample_rate": context.sample_rate, "weight_scale": scale,
                                                      "regions": regions})]
    return proposal


TUNING_SETTINGS = {"cents": "rounded half to even to 2 places and brought into [-50, 50)",
                   "min_drift_cents": "10",
                   "drift": "the tonal/tuning_drift windows further than min_drift_cents from the reference, "
                            "overlapping or touching ones merged; cents and confidence the mean of the merged "
                            "windows' tuning and concentration (tonal/tuning_windows)"}


def folded_cents(value: Decimal) -> Decimal:
    while value >= 50:
        value -= 100
    while value < -50:
        value += 100
    return value


def propose_tuning(context: Context) -> Proposal | Lacking:
    entry = context.bank.entry(context.content, "tonal")
    if entry is None:
        return Lacking(("tonal/tuning_cents", "tonal/tuning_confidence", "tonal/tuning_drift"))
    proposal = Proposal([], {**TUNING_SETTINGS, "times": TIMES})
    for name in ("tuning_cents", "tuning_confidence", "tuning_drift", "tuning_windows"):
        read(proposal, context, entry, name)
    confidence = entry.scalar("tuning_confidence")
    if not confidence:
        return Lacking(detail="tonal reports no tuning: too few confident windows")
    places = Decimal("0.01")
    reference = folded_cents(Decimal(decimal_text(entry.scalar("tuning_cents"))).quantize(places, ROUND_HALF_EVEN))
    windows = [row for row in entry.series("tuning_windows").tolist()]
    pitched_share = Decimal(entry.manifest["parameters"]["tuning"]["min_pitched"])
    concentrations = [row[1] for row in windows if Decimal(repr(float(row[2]))) >= pitched_share]
    drift_windows = entry.items("tuning_drift")
    if len(concentrations) != len(drift_windows):
        raise ProposerError(f"tonal/tuning_drift holds {len(drift_windows)} windows but tonal/tuning_windows "
                            f"{len(concentrations)} at least half pitched")
    minimum = Decimal(TUNING_SETTINGS["min_drift_cents"])
    groups: list[list[tuple[int, int, Decimal, Decimal]]] = []
    for item, concentration in zip(drift_windows, concentrations):
        cents = Decimal(decimal_text(item.get("value", 0)))
        if abs(cents - reference) <= minimum:
            continue
        start = sample_of(item["start"], context.sample_rate, context.sample_count)
        end = sample_of(item["end"], context.sample_rate, context.sample_count)
        if end <= start:
            continue
        window = (start, end, cents, Decimal(repr(float(concentration))))
        if groups and start <= groups[-1][-1][1]:
            groups[-1].append(window)
        else:
            groups.append([window])
    drift = []
    for group in groups:
        start, end = group[0][0], max(w[1] for w in group)
        if drift and start < drift[-1]["end_sample"]:
            start = drift[-1]["end_sample"]
        if end <= start:
            continue
        cents = folded_cents((sum(w[2] for w in group) / len(group)).quantize(places, ROUND_HALF_EVEN))
        share = (sum(w[3] for w in group) / len(group)).quantize(Decimal("0.0001"), ROUND_HALF_EVEN)
        drift.append({"start_sample": start, "end_sample": end, "cents": format(cents, "f"),
                      "confidence": format(share, "f")})
    if len(drift) > MAX_DRIFT:
        raise ProposerError(f"tonal/tuning_drift makes {len(drift)} drift spans; a claim holds at most {MAX_DRIFT}")
    payload: dict[str, Any] = {
        "sample_rate": context.sample_rate, "reference_cents": format(reference, "f"),
        "confidence_scale": weight_scale(f"tonal {entry.manifest['extractor']['version']} tuning: share of the "
                                         f"sounding frames' loud peak magnitude below 2 kHz within 15 cents of "
                                         f"the tuning"),
        "confidence": decimal_text(confidence, 4)}
    if drift:
        payload["drift"] = drift
    proposal.outputs = [Planned(TUNING, "tuning", payload)]
    return proposal


def propose_notes(context: Context) -> Proposal | Lacking:
    entry, feature = context.bank.default_entry(context.content, "melody")
    if entry is None:
        return Lacking((f"melody/notes from {feature}" if feature else "melody/notes",))
    proposal = Proposal([], {"part": "the stem's output, or mix", "notes": "melody/notes spans, each its value "
                             "rounded half to even to a MIDI note; notes on one onset and pitch are one",
                             "times": TIMES})
    payload: dict[str, Any] = {"sample_rate": context.sample_rate, "part": "mix"}
    if feature is not None:
        stems_id, output = feature.split("/", 1)
        stems = context.bank.entry(context.content, stems_id)
        if stems is None:
            raise ProposerError(f"{feature} was measured but its entry is gone")
        read(proposal, context, stems, output)
        payload.update(part=output, stem=stem_citation(stems, output))
    read(proposal, context, entry, "notes")
    notes: dict[tuple[int, int], dict[str, Any]] = {}
    for item in entry.items("notes"):
        value = item.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        pitch = int(Decimal(repr(value)).quantize(Decimal(1), ROUND_HALF_EVEN))
        onset = sample_of(item["start"], context.sample_rate, context.sample_count)
        offset = sample_of(item["end"], context.sample_rate, context.sample_count)
        if not 0 <= pitch <= 127 or offset <= onset or onset >= context.sample_count:
            continue
        notes.setdefault((onset, pitch), {"onset_sample": onset, "offset_sample": offset, "pitch": pitch})
    if not notes:
        return Lacking(detail="melody/notes holds no note inside the stream")
    found = [notes[k] for k in sorted(notes)]
    if payload["part"] == "bass":
        payload["bass"] = True
    if payload["part"] == "vocals":
        proposal.settings["bleed"] = BLEED_SETTINGS
        _, bass_notes, measured, missing = bass_line_entries(context)
        bass = None
        if missing:
            proposal.settings["bleed"] = {**BLEED_SETTINGS, "bass_line": "not in the bank, so bass_match was not "
                                          "applied: " + ", ".join(missing)}
        else:
            assert bass_notes is not None and measured is not None
            bass = read_bass_line(proposal, context, bass_notes, measured)
            read(proposal, context, entry, "level_db")
            read(proposal, context, entry, "loud_db")
        found, _ = drop_bleed(found, None if bass is None else entry.series("level_db"),
                              entry.grid("level_db"), None if bass is None else entry.scalar("loud_db"), bass,
                              context.sample_rate)
        if not found:
            return Lacking(detail="every note of melody/notes is separation bleed")
    if len(found) > MAX_NOTES:
        raise ProposerError(f"melody/notes holds {len(found)} notes; a claim holds at most {MAX_NOTES}")
    proposal.outputs = [Planned(NOTES, "notes", {**payload, "notes": found})]
    return proposal


def propose_sections(context: Context) -> Proposal | Lacking:
    entry = context.bank.entry(context.content, "structure")
    if entry is None:
        return Lacking(("structure/sections",))
    proposal = Proposal([], {"spans": "structure/sections with the extractor's labels", "times": TIMES})
    read(proposal, context, entry, "sections")
    spans = [{"start_sample": start, "end_sample": end, "label": item["label"]}
             for start, end, item in timed_spans(entry.items("sections"), context, "structure/sections")
             if isinstance(item.get("label"), str) and 0 < len(item["label"]) <= 100]
    if not spans:
        return Lacking(detail="structure/sections holds no section")
    if len(spans) > MAX_SECTIONS:
        raise ProposerError(f"structure/sections holds {len(spans)} sections; a claim holds at most {MAX_SECTIONS}")
    proposal.outputs = [Planned(FORM_SPANS, "sections", {"sample_rate": context.sample_rate, "spans": spans})]
    return proposal


# -- timed lyrics by forced alignment (ADR 0085, amended 2026-10-19) -------
#
# Declared, not built. A machine's timed lyrics claim comes from aligning the
# known lyric document to the vocal stem, never from ASR text. Its run uses
# the lyric document's revision and stems/vocals by bank key, and its
# parameters name the aligner, its version, the language, and the
# syllabification rule; its `alignment.method` is "forced alignment", it
# states no max_error_samples, and each syllable may name the vocal notes
# claim's onsets that carry it. enrich's environment holds numpy and scipy
# only: an aligner needs an acoustic model (a CTC phoneme or character model
# and its weights), so it belongs in an interpreter of its own, as f0-python
# holds PyTorch, run as an extractor over --from stems/vocals that is also
# given the document's text.

ALIGNMENT_REQUEST = {
    "document": "the lyric document's revision (a pinned Artifact), whose lines and words the claim locates",
    "stem": "stems/vocals of the recording, cited by bank key",
    "language": "a BCP 47 tag, stated by the person who asks",
    "syllabification": "the rule that splits a word into code point ranges",
    "notes": "optional: the vocal notes claim whose onsets may carry syllables",
}
ALIGNMENT_NEEDS = (
    "an aligner extractor (ADR 0074 manifest) run under its own interpreter with a pinned acoustic model",
    "a way to give an extractor the lyric document's text beside the stem",
    "a pinned syllabification rule",
    "enrich propose given the lyric document's revision to pin",
)


def propose_timed_lyrics(context: Context) -> Proposal | Lacking:
    """Refuses: forced alignment is declared (ALIGNMENT_REQUEST) but no
    aligner is installed (ALIGNMENT_NEEDS)."""
    return Lacking(missing=ALIGNMENT_NEEDS, code="not_yet",
                   detail="forced alignment of a lyric document to the vocal stem is not built yet")


PROPOSERS: dict[str, Proposer] = {proposer.id: proposer for proposer in (
    Proposer("chords-jiang", "1", propose_jiang),
    Proposer("chords-chordino", "1", propose_chordino),
    Proposer("beats", "1", propose_beats),
    Proposer("bass", "1", propose_bass),
    Proposer("key", "2", propose_key),
    Proposer("tuning", "1", propose_tuning),
    Proposer("notes", "2", propose_notes),
    Proposer("bass-line", "1", propose_bass_line),
    Proposer("sections", "1", propose_sections),
    Proposer("timed-lyrics", "0", propose_timed_lyrics),
)}
# The import proposes these; the others run only when named.
DEFAULT = ("chords-jiang", "chords-chordino", "beats", "bass")
