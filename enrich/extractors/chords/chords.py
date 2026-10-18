#!/usr/bin/env python3
"""The chords extractor: a chord per stretch of the beat grid, from the
tuning-referenced pitch-class profiles of the treble and the bass (ADR 0074).

It reads `tonal/chroma`, `tonal/bass_chroma`, and `tonal/pitched` (on the
`frames` grid), `rhythm/beats`, and `frames/samples` from the bank, decodes
nothing, and writes:

- `chords`: spans that tile the recording, each one chord, labelled with
  its root (as Essentia names notes: A, Bb, B, C, C#, D, Eb, E, F, F#, G, Ab),
  its quality (below), and a slash and the bass note when the bass plays a
  note other than the root ("Abmaj7/Bb"), valued by the chord's mean score;
  "N" (no chord, no value) where the stretch is not pitched;
- `chord_candidates`: for every chord span, three spans with its start and
  end: the three chords that score highest over it, best (the chord named)
  first, each over the span's bass note and valued by its mean score, so a
  reader can offer the alternatives a dense voicing leaves open (A C E G is
  Am7 or C6/A as the bass and the voicing lean);
- `changes_per_minute`: chord changes per minute of chord time, and
  `beat_share`: the share of the chord time on the tracked beat grid rather
  than on the grid filled in where the tracker kept no beat.

Why on the beat. Version 2 of `tonal` named a triad every frame (93 ms)
from two seconds of profile, with no notion of how long a chord lasts: on an
album of eighteen dense masters half its spans were shorter than half a
second, a third of its changes returned to the chord before within 1.5 s,
and half went between triads sharing two notes, the triads inside one
seventh or extended chord. Here the chords are scored per half beat, and a
Viterbi path through the half beats, paying `switch` (0.4) for every change,
decides where the chord changes, so a change must earn its place over the
stretch it starts. The path places the boundaries; each span is then named
by the chord that scores best over all of it (`label`), so a seventh that
the path did not pay to switch to is still named where it holds a span.

The grid. The beats are `rhythm`'s. Where two beats are further apart than
one median beat interval and a half, the gap is cut into as many equal
beats as fit; before the first beat and after the last, the grid steps on
at the median interval. With fewer than two beats the grid is
`without_beats_seconds` (0.5 s). Each beat is cut into `per_beat` (2)
stretches, so a change pushed onto the off-beat, as this music often does,
lands where it is played. A stretch's profile is the mean of the frames
whose centres it holds (the nearest frame, for a stretch shorter than a
hop); it is pitched when at least half of those frames are.

The score. A stretch's treble and bass profiles are square-rooted (energy
to amplitude), each scaled to unit length, and summed with the bass at
`bass_weight` (0.25), then scaled to unit length again. Every chord of the
vocabulary is a twelve-bin template, 1 on its notes, scaled to unit length.
A chord's score is the cosine of the stretch's profile with its template,
plus `bass_root` (0.15) times the share of the stretch's bass that is the
chord's root, minus a penalty for the notes it needs (none for three, 0.04
for four, 0.12 for five or six, as a richer template fits any profile
better) and, for sus2 and sus4, 0.05 more: a bare fifth's third harmonic is
the ninth above its root, so without it a fifth reads as sus2 (on the album
above sus2 and sus4 took 30 % of the chord time, and 9 % with it, with no
reference agreeing less). The vocabulary is maj, m, 7, maj7, m7, m7b5, dim,
dim7, aug, sus2, sus4, 6, m6, add9, and the extended 9, m9, maj9, and m11,
which this music uses throughout; on transcriptions of four of the tracks
the extended chords raised the share of the transcribed notes the chord
holds from 0.77 to 0.81 with fewer wrong notes, not more.

The slash. The bass line is decoded on a path of its own, through the
twelve notes and "none", a stretch's score for a note being its share of the
stretch's bass and for "none" `min_bass_share` (0.4), at the same price a
change of `bass_switch` (1.5), so a note must hold more than a beat and a
half to count and a walking or passing bass does not cut the chords. A
chord span is cut where the bass line moves, and labelled with a slash where
its bass note (the one the line holds longest in it) is not its root, so C
over G and then Cmaj7 over C are two chords, not one. Neighbouring spans
named alike merge. The bass is weighed lightly on purpose: at twice
these weights the bass's root drowned the seventh of a clean Cmaj7
(read C) and pulled the root of C over G onto G (read Gsus4), and on the
album named roots after the bass a little more often (57 % of transcribed
beats against 54 %) while holding fewer of the transcribed notes.

Measured on that album against the references (ChordU's automatic
timelines, Ultimate Guitar charts, and Guitar Pro transcriptions aligned to
the recordings), the spans' median length went from 0.46 s to 1.3 s, about
the charts' one chord per one to one and a half seconds, and returns within
1.5 s from 30 % to 2 %; the root agreed with the transcriptions on 52 % of
beats where `tonal` 2's agreed on 38 %, and the chord held 79 % of the
transcribed notes where `tonal` 2's triads held 60 %. Chord recognition on a
dense mix stays approximate: the candidates are part of the answer.

Times are seconds from the first decoded sample of the recording, the time
base `rhythm` and `frames` share; spans end at the decoded audio's end.
Invoked by the runner as `chords.py <request.json>`; the settings are fixed,
and a request with other parameters is refused.
"""

from __future__ import annotations

import json
import platform
import re
import sys
from pathlib import Path
from typing import Any, Sequence

import numpy

RATE, HOP = 22050, 2048  # the frames grid
NOTES = ("A", "Bb", "B", "C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab")  # bin 0 is A, as in tonal's profiles
VOCABULARY = [["", [0, 4, 7]], ["m", [0, 3, 7]], ["7", [0, 4, 7, 10]], ["maj7", [0, 4, 7, 11]],
              ["m7", [0, 3, 7, 10]], ["m7b5", [0, 3, 6, 10]], ["dim", [0, 3, 6]], ["dim7", [0, 3, 6, 9]],
              ["aug", [0, 4, 8]], ["sus2", [0, 2, 7]], ["sus4", [0, 5, 7]], ["6", [0, 4, 7, 9]],
              ["m6", [0, 3, 7, 9]], ["add9", [0, 2, 4, 7]], ["9", [0, 2, 4, 7, 10]], ["m9", [0, 2, 3, 7, 10]],
              ["maj9", [0, 2, 4, 7, 11]], ["m11", [0, 2, 3, 5, 7, 10]]]
GRID = {"beats": "rhythm/beats", "per_beat": 2, "gap_beats": "1.5", "without_beats_seconds": "0.5",
        "min_pitched": "0.5"}
SCORE = {"compression": "square-root", "bass_weight": "0.25", "bass_root": "0.15",
         "notes_penalty": {"3": "0", "4": "0.04", "5": "0.12", "6": "0.12"},
         "quality_penalty": {"sus2": "0.05", "sus4": "0.05"}}
DECODE = {"method": "viterbi", "switch": "0.4", "bass_switch": "1.5", "label": "best-over-span"}
SLASH = {"min_bass_share": "0.4"}
CANDIDATES = 3
NEEDS = {"tonal/chroma": 12, "tonal/bass_chroma": 12, "tonal/pitched": 1}


class ChordsError(Exception):
    pass


def parameters() -> dict[str, Any]:
    """The settings, which the manifest must declare exactly."""
    return {"vocabulary": VOCABULARY, "grid": GRID, "score": SCORE, "decode": DECODE, "slash": SLASH,
            "candidates": CANDIDATES}


def templates() -> tuple[numpy.ndarray, list[tuple[int, str]], numpy.ndarray]:
    """Every chord's unit-length template, its (root bin, quality), and its penalty."""
    rows, chords, penalties = [], [], []
    for quality, intervals in VOCABULARY:
        for root in range(12):
            template = numpy.zeros(12)
            template[[(root + step) % 12 for step in intervals]] = 1.0
            rows.append(template / numpy.linalg.norm(template))
            chords.append((root, quality))
            penalties.append(float(SCORE["notes_penalty"][str(len(intervals))])
                             + float(SCORE["quality_penalty"].get(quality, "0")))
    return numpy.array(rows), chords, numpy.array(penalties)


TEMPLATES, CHORDS, PENALTIES = templates()


def name(root: int, quality: str, bass: int | None = None) -> str:
    return NOTES[root] + quality + ("" if bass is None or bass == root else "/" + NOTES[bass])


LABEL = re.compile(r"^(A|Bb|B|C|C#|D|Eb|E|F|F#|G|Ab)(" + "|".join(re.escape(q) for q, _ in VOCABULARY[1:])
                   + r")?(?:/(A|Bb|B|C|C#|D|Eb|E|F|F#|G|Ab))?$")


def notes_of(label: str) -> set[int] | None:
    """The pitch classes (bin 0 is A) a label names, its bass included; None
    for "N"; refused for anything else."""
    if label == "N":
        return None
    match = LABEL.match(label)
    if not match:
        raise ChordsError(f"{label!r} is not a chord this extractor names")
    root = NOTES.index(match.group(1))
    intervals = dict((q, i) for q, i in VOCABULARY)[match.group(2) or ""]
    notes = {(root + step) % 12 for step in intervals}
    if match.group(3):
        notes.add(NOTES.index(match.group(3)))
    return notes


# -- the grid -------------------------------------------------------------------

def grid(beats: Sequence[float], seconds: float) -> tuple[numpy.ndarray, numpy.ndarray]:
    """The stretches' edges in seconds, from 0 to `seconds`, and for each
    stretch whether it lies between two tracked beats one beat apart."""
    beats = numpy.asarray([b for b in beats if 0 <= b < seconds], dtype=numpy.float64)
    if len(beats) < 2:
        step = float(GRID["without_beats_seconds"])
        edges = numpy.append(numpy.arange(0.0, seconds, step), seconds)
        return subdivide(edges, numpy.zeros(len(edges) - 1, dtype=bool))
    interval = float(numpy.median(numpy.diff(beats)))
    # Each edge with the flag of the stretch that ends at it.
    marks = [(float(edge), False) for edge in numpy.arange(beats[0], 0.0, -interval)[::-1][:-1]]
    marks.append((float(beats[0]), False))
    for before, after in zip(beats[:-1], beats[1:]):
        gap = float(after - before)
        count = max(1, int(round(gap / interval)))
        tracked = count == 1 and gap <= float(GRID["gap_beats"]) * interval
        marks += [(float(before + gap * part / count), tracked) for part in range(1, count + 1)]
    marks += [(float(edge), False) for edge in numpy.arange(beats[-1] + interval, seconds, interval)]
    marks = [(0.0, False)] + [mark for mark in marks if 0.0 < mark[0] < seconds] + [(seconds, False)]
    edges = numpy.asarray([edge for edge, _ in marks], dtype=numpy.float64)
    flags = numpy.asarray([flag for _, flag in marks[1:]], dtype=bool)
    return subdivide(edges, flags)


def subdivide(edges: numpy.ndarray, on_beat: numpy.ndarray) -> tuple[numpy.ndarray, numpy.ndarray]:
    parts = GRID["per_beat"]
    fine = [edges[0]]
    flags = []
    for index, (start, end) in enumerate(zip(edges[:-1], edges[1:])):
        fine += list(start + (end - start) * numpy.arange(1, parts + 1) / parts)
        flags += [bool(on_beat[index])] * parts
    return numpy.asarray(fine, dtype=numpy.float64), numpy.asarray(flags, dtype=bool)


def frame_ranges(edges: numpy.ndarray, rows: int) -> list[tuple[int, int]]:
    """For each stretch, the frames whose centres it holds; the nearest frame
    when it holds none."""
    ranges = []
    for start, end in zip(edges[:-1], edges[1:]):
        first = min(int(numpy.ceil(start * RATE / HOP)), rows)
        stop = min(int(numpy.ceil(end * RATE / HOP)), rows)
        if stop <= first:
            first = min(max(int(round((start + end) / 2 * RATE / HOP)), 0), rows - 1)
            stop = first + 1
        ranges.append((first, stop))
    return ranges


# -- the score and the path ----------------------------------------------------

def unit(values: numpy.ndarray) -> numpy.ndarray:
    norms = numpy.linalg.norm(values, axis=1, keepdims=True)
    return values / numpy.where(norms > 0, norms, 1.0)


def scores(treble: numpy.ndarray, bass: numpy.ndarray) -> tuple[numpy.ndarray, numpy.ndarray]:
    """Every stretch's score for every chord, and its bass shares."""
    treble, bass = numpy.sqrt(numpy.maximum(treble, 0.0)), numpy.sqrt(numpy.maximum(bass, 0.0))
    totals = bass.sum(axis=1, keepdims=True)
    shares = bass / numpy.where(totals > 0, totals, 1.0)
    profile = unit(unit(treble) + float(SCORE["bass_weight"]) * unit(bass))
    roots = numpy.array([root for root, _ in CHORDS])
    return profile @ TEMPLATES.T - PENALTIES[None, :] + float(SCORE["bass_root"]) * shares[:, roots], shares


def viterbi(emission: numpy.ndarray, switch: float) -> numpy.ndarray:
    """The path through the rows that maximises the summed scores less
    `switch` for every change of state."""
    rows, states = emission.shape
    back = numpy.zeros((rows, states), dtype=numpy.int64)
    total = emission[0].copy()
    for row in range(1, rows):
        best = int(numpy.argmax(total))
        jump = total[best] - switch
        back[row] = numpy.where(jump > total, best, numpy.arange(states))
        total = numpy.maximum(total, jump) + emission[row]
    path = numpy.zeros(rows, dtype=numpy.int64)
    path[-1] = int(numpy.argmax(total))
    for row in range(rows - 1, 0, -1):
        path[row - 1] = back[row, path[row]]
    return path


def runs(values: Sequence[Any]) -> list[tuple[int, int]]:
    """(start, stop) of each run of equal consecutive values."""
    out = []
    start = 0
    for index in range(1, len(values) + 1):
        if index == len(values) or values[index] != values[start]:
            out.append((start, index))
            start = index
    return out


def decide(treble: numpy.ndarray, bass: numpy.ndarray, pitched: numpy.ndarray, edges: numpy.ndarray,
           on_beat: numpy.ndarray) -> dict[str, Any]:
    """The chords, their candidates, and the summary scalars for stretches
    with these mean profiles and pitched flags."""
    emission, shares = scores(treble, bass)
    # The bass line's states are its twelve notes and "none", which scores
    # what a note needs to be named.
    bass_emission = numpy.concatenate([shares, numpy.full((len(shares), 1), float(SLASH["min_bass_share"]))], axis=1)
    state = numpy.full(len(pitched), -1, dtype=numpy.int64)
    low = numpy.full(len(pitched), -1, dtype=numpy.int64)
    for start, stop in runs(list(pitched)):
        if pitched[start]:
            state[start:stop] = viterbi(emission[start:stop], float(DECODE["switch"]))
            path = viterbi(bass_emission[start:stop], float(DECODE["bass_switch"]))
            low[start:stop] = numpy.where(path == 12, -1, path)

    def named(start: int, stop: int) -> tuple[str, numpy.ndarray, int | None]:
        """A span's label, its chords' mean scores, and its bass note: the
        chord scoring best over all of it, over the note the bass line holds
        longest in it (none if it holds none)."""
        if state[start] < 0:
            return "N", numpy.zeros(0), None
        mean = emission[start:stop].mean(axis=0)
        notes = [int(note) for note in low[start:stop] if note >= 0]
        bass_note = max(sorted(set(notes)), key=notes.count) if notes else None
        return name(*CHORDS[int(numpy.argmax(mean))], bass_note), mean, bass_note

    spans = [[start, stop, named(start, stop)[0]]
             for start, stop in runs([(int(a), int(b)) for a, b in zip(state, low)])]
    merged = True
    while merged:  # neighbours that came out alike are one chord
        merged = False
        for index in range(len(spans) - 1):
            if spans[index][2] == spans[index + 1][2]:
                start, stop = spans[index][0], spans[index + 1][1]
                spans[index:index + 2] = [[start, stop, named(start, stop)[0]]]
                merged = True
                break
    chords, candidates = [], []
    for start, stop, _ in spans:
        label, mean, bass_note = named(start, stop)
        span = {"start": float(edges[start]), "end": float(edges[stop])}
        if label == "N":
            chords.append({**span, "label": "N"})
            continue
        ranked = [int(i) for i in numpy.argsort(-mean, kind="stable")[:CANDIDATES]]
        chords.append({**span, "label": label, "value": float(mean[ranked[0]])})
        candidates += [{**span, "label": name(*CHORDS[i], bass_note), "value": float(mean[i])} for i in ranked]
    lengths = numpy.diff(edges)
    chord_time = float(sum(c["end"] - c["start"] for c in chords if c["label"] != "N"))
    changes = sum(1 for before, after in zip(chords, chords[1:])
                  if before["label"] != "N" and after["label"] != "N")
    return {"chords": chords, "chord_candidates": candidates,
            "changes_per_minute": changes / (chord_time / 60.0) if chord_time > 0 else 0.0,
            "beat_share": float(lengths[on_beat & (state >= 0)].sum() / chord_time) if chord_time > 0 else 0.0}


def check(outputs: dict[str, Any], seconds: float) -> None:
    """What the outputs promise, checked before anything is written: the
    chords tile the recording, every label is one this extractor names, and
    each chord has its candidates, its own first and the rest in order."""
    chords, candidates = outputs["chords"], outputs["chord_candidates"]
    if not chords or chords[0]["start"] != 0.0 or abs(chords[-1]["end"] - seconds) > 1e-6:
        raise ChordsError("the chords do not cover the recording")
    named = 0
    for index, chord in enumerate(chords):
        if chord["end"] <= chord["start"] or (index and chord["start"] != chords[index - 1]["end"]):
            raise ChordsError(f"chord {index} does not follow the one before it")
        if notes_of(chord["label"]) is None:
            continue
        group = candidates[named * CANDIDATES:(named + 1) * CANDIDATES]
        named += 1
        if len(group) != CANDIDATES or group[0]["label"] != chord["label"] \
                or any((c["start"], c["end"]) != (chord["start"], chord["end"]) for c in group) \
                or any(after["value"] > before["value"] for before, after in zip(group, group[1:])):
            raise ChordsError(f"chord {index}'s candidates are not its own, ranked")
        for candidate in group:
            notes_of(candidate["label"])
    if len(candidates) != named * CANDIDATES:
        raise ChordsError("there are candidates for no chord")


# -- the request ------------------------------------------------------------------

def need(request: dict[str, Any], feature: str, columns: int, rows: int | None = None) -> numpy.ndarray:
    """A needed series on the frames grid."""
    described = request["needs"].get(feature)
    if described is None:
        raise ChordsError(f"the request does not carry {feature}")
    dtype = {"f32": "<f4", "u8": "u1"}.get(described.get("dtype", ""))
    if dtype is None or described.get("shape", [None, None])[1] != columns:
        raise ChordsError(f"{feature} is not a series of {columns} columns")
    grid_of = described.get("grid") or {}
    if (grid_of.get("sample_rate"), grid_of.get("hop"), grid_of.get("first_centre")) != (RATE, HOP, 0):
        raise ChordsError(f"{feature} is not on the frames grid: {grid_of}")
    values = numpy.fromfile(described["path"], dtype=dtype)
    count = described["shape"][0]
    if values.size != count * columns or (rows is not None and count != rows):
        raise ChordsError(f"{feature} holds {values.size} values, not {rows or count} x {columns}")
    return values.reshape(count, columns).astype(numpy.float64)


def measure(request: dict[str, Any]) -> dict[str, Any]:
    chroma = need(request, "tonal/chroma", 12)
    rows = len(chroma)
    bass = need(request, "tonal/bass_chroma", 12, rows)
    pitched = need(request, "tonal/pitched", 1, rows)[:, 0] > 0
    samples = request["needs"].get("frames/samples", {}).get("value")
    if not isinstance(samples, int) or samples <= 0:
        raise ChordsError("the request does not carry the decoded length, frames/samples")
    beats_need = request["needs"].get("rhythm/beats")
    if beats_need is None:
        raise ChordsError("the request does not carry rhythm/beats")
    beats = [float(event["time"]) for event in json.loads(Path(beats_need["path"]).read_text(encoding="utf-8"))]
    seconds = samples / RATE
    edges, on_beat = grid(beats, seconds)
    ranges = frame_ranges(edges, rows)
    treble = numpy.array([chroma[a:b].mean(axis=0) for a, b in ranges])
    low = numpy.array([bass[a:b].mean(axis=0) for a, b in ranges])
    share = numpy.array([pitched[a:b].mean() for a, b in ranges])
    outputs = decide(treble, low, share >= float(GRID["min_pitched"]), edges, on_beat)
    check(outputs, seconds)
    return outputs


SCALARS = ("changes_per_minute", "beat_share")
SPANS = ("chords", "chord_candidates")


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: chords.py <request.json>", file=sys.stderr)
        return 2
    try:
        request = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
        if request.get("parameters") != parameters():
            raise ChordsError("chords 1 has fixed settings; the request names others")
        output = Path(request["output_dir"])
        measured = measure(request)
        result_outputs: dict[str, Any] = {name: {"value": measured[name]} for name in SCALARS}
        for name in SPANS:
            (output / f"{name}.json").write_text(json.dumps(measured[name]), encoding="utf-8")
            result_outputs[name] = {}
        software = [{"name": "numpy", "version": numpy.__version__},
                    {"name": "python", "version": platform.python_version()}]
        result = {"contract": "archive-extractor-result/1", "software": software, "grids": {},
                  "outputs": result_outputs}
        (output / "result.json").write_text(json.dumps(result), encoding="utf-8")
    except (ChordsError, OSError, KeyError, ValueError, TypeError) as exc:
        print(f"chords: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
