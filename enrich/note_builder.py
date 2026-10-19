"""Notes from a pitch curve: one note per held pitch (ADR 0086).

A sung line or a bass line read from a monophonic pitch track (`f0-pesto`)
and the level of the same audio on the same rows, the way a person writes
it down: the pitches the line lands on and holds, not the path between them.
Slides, scoops and vibrato stay inside the note they lead into or belong to;
a gap of a consonant between two notes of one pitch is bridged; a held pitch
is split again only where the level dips (a re-articulation).

The rule is in five steps; each threshold is a setting (`VOICE`, `BASS`),
written as decimal text so a run's parameters state it exactly:

1. Voiced rows. Pitch is 69 + 12 log2(f / 440) less the tuning. A row is
   voiced with hysteresis: it starts at `confidence_on` and `level_on_db`
   (relative to the level reference) and stays while the confidence is at
   least `confidence_keep` and the level at least `level_keep_db`. A row
   `spike_semitones` or more from the median of the voiced rows within
   `spike_window_s` is moved by an octave when that lands within
   `spike_fold_within_semitones` of it, else unvoiced. Voiced runs shorter
   than `min_run_s` are passed over.
2. Notes. A row is stable when the rows within `stable_window_s` around it
   span at most `stable_range_semitones`; a plateau is a run of stable rows,
   split where its pitch moves `change_semitones` from the plateau's median
   so far for at least `change_persist_s`. A plateau shorter than
   max(`min_plateau_s`, `min_plateau_eighths` of an eighth note) is no note
   of its own. Each plateau is a note; rows between two plateaus go half to
   each; a voiced run with no plateau is one note. A note is split at a row
   lowest within 30 ms whose level lies `dip_db` under the louder of either
   side's loudest within `dip_window_s` (both sides), at least `dip_side_s`
   from the note's ends and from the previous split.
3. Tidy, repeated until nothing changes: a gap of at most `bridge_s` between
   two notes of one pitch (less than `same_pitch_semitones` apart or the
   same rounded pitch) is bridged, unless step 2 split them at a dip; a note shorter
   than `glide_s` that starts within `glide_gap_s` of the previous one and
   lies within `glide_semitones` of it joins it; a note shorter than
   `scoop_s` that leads, within `glide_gap_s`, into one at least twice as
   long and `scoop_min_semitones` to `scoop_max_semitones` away joins that
   one (its onset kept), unless it follows a note shorter than `scoop_run_s`
   within `glide_gap_s` (a run of short notes); then a note shorter than
   `min_note_s` is dropped.
4. Pitch: the mean of the note's rows within `centre_semitones` of their
   median, after up to `attack_s` of the attack and `release_s` of the
   release are left out (at most a quarter and a sixth of the note).
5. Register: a note `octave_from_semitones` or more from the median of the
   other notes with onsets within `octave_window_s` (at least three) moves
   by an octave when that brings it `octave_gain_semitones` closer;
   `octave_passes` passes.

Times are rows of the pitch track's grid; the caller turns them into
samples. Only numpy is needed.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from typing import Any, Mapping

VOICE: dict[str, Any] = {
    "confidence_on": "0.6",
    "confidence_keep": "0.35",
    "level_on_db": "-38",
    "level_keep_db": "-44",
    "level_reference": "the 90th percentile of the level over rows of confidence at least 0.8 when more than "
                       "50 rows are, else the 95th percentile of every row's level",
    "spike_semitones": "7",
    "spike_window_s": "0.3",
    "spike_fold_within_semitones": "2.5",
    "min_run_s": "0.04",
    "stable_window_s": "0.07",
    "stable_range_semitones": "1.2",
    "change_semitones": "0.8",
    "change_persist_s": "0.05",
    "min_plateau_s": "0.07",
    "min_plateau_eighths": "0.4",
    "dip_db": "14",
    "dip_window_s": "0.15",
    "dip_side_s": "0.06",
    "bridge_s": "0.1",
    "same_pitch_semitones": "0.6",
    "glide_s": "0.07",
    "glide_gap_s": "0.03",
    "glide_semitones": "2.5",
    "scoop_s": "0.11",
    "scoop_min_semitones": "0.6",
    "scoop_max_semitones": "3",
    "scoop_run_s": "0.2",
    "min_note_s": "0.06",
    "attack_s": "0.04",
    "release_s": "0.02",
    "centre_semitones": "1",
    "octave_window_s": "2",
    "octave_from_semitones": "9",
    "octave_gain_semitones": "6",
    "octave_passes": 2,
}

# The bass, and any plucked or blown single line: a note is re-attacked,
# never sung through a consonant, so no gap is bridged, a 3 dB level dip
# splits a held pitch, and notes and plateaus may be as short as a fast
# sixteenth, with no minimum following the tempo. Measured on real separated
# jazz solos against hand references ("horn-d0": mean note F1 0.76, Basic
# Pitch 0.65); there the onsets sat 7 to 14 ms early, so none is shifted.
BASS: dict[str, Any] = {
    **VOICE,
    "min_run_s": "0.03",
    "stable_range_semitones": "1",
    "min_plateau_s": "0.03",
    "min_plateau_eighths": "0",
    "dip_db": "3",
    "dip_side_s": "0.04",
    "bridge_s": "0",
    "glide_s": "0.03",
    "min_note_s": "0.03",
}


def _f(settings: Mapping[str, Any], name: str) -> float:
    return float(settings[name])


def level_reference(confidence: Any, level: Any) -> float:
    import numpy
    sure = confidence >= 0.8
    if int(sure.sum()) > 50:
        return float(numpy.percentile(level[sure], 90))
    return float(numpy.percentile(level, 95)) if len(level) else 0.0


def voiced_rows(hz: Any, confidence: Any, level: Any, settings: Mapping[str, Any], hop_s: float,
                tuning_cents: float) -> tuple[Any, Any, Any]:
    """Step 1: each row's pitch (NaN where the tracker has none), whether it
    is voiced, and its level relative to the reference."""
    import numpy
    from numpy.lib.stride_tricks import sliding_window_view
    midi = numpy.full(len(hz), numpy.nan)
    sounding = hz > 0
    midi[sounding] = 69 + 12 * numpy.log2(hz[sounding] / 440.0) - tuning_cents / 100.0
    loud = level - level_reference(confidence, level)
    on_conf, keep_conf = _f(settings, "confidence_on"), _f(settings, "confidence_keep")
    on_db, keep_db = _f(settings, "level_on_db"), _f(settings, "level_keep_db")
    voiced = numpy.zeros(len(hz), dtype=bool)
    on = False
    for i in range(len(hz)):
        if not sounding[i] or loud[i] < keep_db:
            on = False
        elif on:
            on = bool(confidence[i] >= keep_conf)
        else:
            on = bool(confidence[i] >= on_conf and loud[i] >= on_db)
        voiced[i] = on
    rows = numpy.flatnonzero(voiced)
    if len(rows):
        k = int(round(_f(settings, "spike_window_s") / hop_s / 2))
        pitches = midi[rows]
        median = numpy.median(sliding_window_view(numpy.pad(pitches, k, mode="edge"), 2 * k + 1), axis=1)
        deviation = pitches - median
        within = _f(settings, "spike_fold_within_semitones")
        for j in numpy.flatnonzero(numpy.abs(deviation) >= _f(settings, "spike_semitones")):
            moved = pitches[j] - 12 * numpy.sign(deviation[j])
            if abs(moved - median[j]) <= within:
                midi[rows[j]] = moved
            else:
                voiced[rows[j]] = False
    return midi, voiced, loud


def runs(voiced: Any, min_rows: int) -> list[tuple[int, int]]:
    import numpy
    edges = numpy.diff(numpy.concatenate([[0], voiced.astype(numpy.int8), [0]]))
    starts, ends = numpy.flatnonzero(edges == 1), numpy.flatnonzero(edges == -1)
    return [(int(a), int(b)) for a, b in zip(starts, ends) if b - a >= min_rows]


def plateaus(pitch: Any, min_rows: int, settings: Mapping[str, Any], hop_s: float) -> list[tuple[int, int]]:
    """Step 2's stable stretches of one voiced run, as [start, end) rows."""
    import numpy
    from numpy.lib.stride_tricks import sliding_window_view
    n = len(pitch)
    half = int(round(_f(settings, "stable_window_s") / hop_s)) // 2
    padded = numpy.pad(pitch, half, mode="constant", constant_values=numpy.nan)
    windows = sliding_window_view(padded, 2 * half + 1)
    stable = (numpy.nanmax(windows, axis=1) - numpy.nanmin(windows, axis=1)) <= _f(settings, "stable_range_semitones")
    change = _f(settings, "change_semitones")
    persist = int(round(_f(settings, "change_persist_s") / hop_s))
    found: list[tuple[int, int]] = []
    i = 0
    while i < n:
        if not stable[i]:
            i += 1
            continue
        j = i
        while j < n and stable[j]:
            j += 1
        start = i
        k = i + 1
        while k < j:
            reference = numpy.median(pitch[start:k])
            if abs(pitch[k] - reference) >= change:
                q = k
                while q < j and abs(pitch[q] - reference) >= change:
                    q += 1
                if q - k >= persist:
                    found.append((start, k))
                    start = k
            k += 1
        found.append((start, j))
        i = j
    return [(a, b) for a, b in found if b - a >= min_rows]


def segment(midi: Any, voiced: Any, loud: Any, settings: Mapping[str, Any], hop_s: float,
            min_plateau_s: float) -> list[dict[str, Any]]:
    """Step 2: notes of [first_row, end_row) and their plateau pitch."""
    import numpy
    notes = []
    least = max(3, int(round(min_plateau_s / hop_s)))
    side = int(round(_f(settings, "dip_side_s") / hop_s))
    reach = int(round(_f(settings, "dip_window_s") / hop_s))
    dip = _f(settings, "dip_db")
    for a, b in runs(voiced, int(numpy.ceil(_f(settings, "min_run_s") / hop_s - 1e-9))):
        pitch, level = midi[a:b], loud[a:b]
        found = plateaus(pitch, least, settings, hop_s)
        if not found:
            notes.append((a, b, float(numpy.median(pitch)), False))
            continue
        bounds = [0, *[(x1 + y0) // 2 for (_, x1), (y0, _) in zip(found, found[1:])], len(pitch)]
        for (x0, x1), s0, s1 in zip(found, bounds, bounds[1:]):
            value = float(numpy.median(pitch[x0:x1]))
            cuts = [s0]
            for c in range(max(s0 + side, x0), min(s1 - side, x1)):
                if level[c] != level[max(0, c - 3):c + 4].min():
                    continue
                depth = min(level[max(0, c - reach):c].max(), level[c + 1:c + reach + 1].max()) - level[c]
                if depth >= dip and c - cuts[-1] >= side:
                    cuts.append(c)
            cuts.append(s1)
            for c0, c1 in zip(cuts, cuts[1:]):
                notes.append((a + c0, a + c1, value, c0 != s0))
    return [{"first_row": a, "end_row": b, "pitch": p, "voiced": b - a, "dip": dip}
            for a, b, p, dip in notes if b - a >= 2]


def tidy(notes: list[dict[str, Any]], loud: Any, settings: Mapping[str, Any],
         hop_s: float) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Step 3."""
    counts = {"bridged": 0, "glides": 0, "scoops": 0, "dropped_short": 0}
    rows = lambda seconds: round(seconds / hop_s)  # noqa: E731  (a duration in whole rows)
    bridge, same = rows(_f(settings, "bridge_s")), _f(settings, "same_pitch_semitones")
    glide, gap_most, glide_st = rows(_f(settings, "glide_s")), rows(_f(settings, "glide_gap_s")), _f(
        settings, "glide_semitones")
    scoop, scoop_low, scoop_high = rows(_f(settings, "scoop_s")), _f(settings, "scoop_min_semitones"), _f(
        settings, "scoop_max_semitones")
    run = rows(_f(settings, "scoop_run_s"))
    length = lambda n: n["end_row"] - n["first_row"]  # noqa: E731
    changed = True
    while changed:
        changed = False
        out: list[dict[str, Any]] = []
        for note in notes:
            if out:
                last = out[-1]
                gap = note["first_row"] - last["end_row"]
                apart = abs(note["pitch"] - last["pitch"])
                if gap <= bridge and not note["dip"] and (apart < same or round(note["pitch"]) == round(last["pitch"])):
                    w1, w2 = last["voiced"], note["voiced"]
                    last["pitch"] = (last["pitch"] * w1 + note["pitch"] * w2) / (w1 + w2)
                    last["end_row"], last["voiced"] = note["end_row"], w1 + w2
                    counts["bridged"] += 1
                    changed = True
                    continue
                if gap <= gap_most and length(note) < glide and apart <= glide_st:
                    last["end_row"] = note["end_row"]
                    counts["glides"] += 1
                    changed = True
                    continue
                if gap <= gap_most and length(last) < scoop and length(note) >= 2 * length(last) and \
                        scoop_low <= apart <= scoop_high and not (
                            len(out) > 1 and out[-2]["end_row"] >= last["first_row"] - gap_most
                            and length(out[-2]) < run):
                    note["first_row"] = last["first_row"]
                    note["voiced"] += last["voiced"]
                    out[-1] = note
                    counts["scoops"] += 1
                    changed = True
                    continue
            out.append(note)
        notes = out
    shortest = rows(_f(settings, "min_note_s"))
    kept = [n for n in notes if length(n) >= shortest]
    counts["dropped_short"] = len(notes) - len(kept)
    return kept, counts


def centre_pitch(midi: Any, first: int, end: int, settings: Mapping[str, Any], hop_s: float) -> float:
    """Step 4."""
    import numpy
    n = end - first
    attack = min(int(round(_f(settings, "attack_s") / hop_s)), n // 4)
    release = min(int(round(_f(settings, "release_s") / hop_s)), n // 6)
    rows = midi[first + attack:end - release] if n - attack - release >= 3 else midi[first:end]
    rows = rows[~numpy.isnan(rows)]
    if not len(rows):
        return float("nan")
    median = numpy.median(rows)
    near = rows[numpy.abs(rows - median) <= _f(settings, "centre_semitones")]
    return float(near.mean()) if len(near) else float(median)


def fold_octaves(notes: list[dict[str, Any]], settings: Mapping[str, Any], hop_s: float) -> int:
    """Step 5, in place; how many notes moved."""
    import numpy
    reach = _f(settings, "octave_window_s") / hop_s
    far, gain = _f(settings, "octave_from_semitones"), _f(settings, "octave_gain_semitones")
    moved = 0
    for _ in range(int(settings["octave_passes"])):
        onsets = [n["first_row"] for n in notes]
        for i, note in enumerate(notes):
            low, high = bisect_left(onsets, note["first_row"] - reach), bisect_right(onsets, note["first_row"] + reach)
            around = [notes[j]["pitch"] for j in range(low, high) if j != i]
            if len(around) < 3:
                continue
            distance = note["pitch"] - float(numpy.median(around))
            if abs(distance) >= far and abs(distance - 12 * numpy.sign(distance)) <= abs(distance) - gain:
                note["pitch"] -= 12 * float(numpy.sign(distance))
                moved += 1
    return moved


def build(hz: Any, confidence: Any, level: Any, hop_s: float, settings: Mapping[str, Any],
          tuning_cents: float = 0.0, eighth_s: float | None = None) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """The notes of one line: [{first_row, end_row, pitch}] by first row,
    pitch a fractional MIDI note against the tuning, and how many notes each
    step made or changed. `hz`, `confidence`, and `level` (dBFS) are one row
    each on a grid of `hop_s`; `eighth_s` is an eighth note's length, if
    known."""
    import numpy
    hz = numpy.nan_to_num(numpy.asarray(hz, dtype=numpy.float64).reshape(-1), nan=0.0, posinf=0.0, neginf=0.0)
    confidence = numpy.nan_to_num(numpy.asarray(confidence, dtype=numpy.float64).reshape(-1))
    level = numpy.nan_to_num(numpy.asarray(level, dtype=numpy.float64).reshape(-1), nan=-120.0, neginf=-120.0)
    if not len(hz) == len(confidence) == len(level):
        raise ValueError(f"the pitch, confidence, and level have {len(hz)}, {len(confidence)}, {len(level)} rows")
    midi, voiced, loud = voiced_rows(hz, confidence, level, settings, hop_s, tuning_cents)
    shortest = _f(settings, "min_plateau_s")
    if eighth_s:
        shortest = max(shortest, _f(settings, "min_plateau_eighths") * eighth_s)
    notes = segment(midi, voiced, loud, settings, hop_s, shortest)
    counts = {"segments": len(notes)}
    notes, tidied = tidy(notes, loud, settings, hop_s)
    counts.update(tidied)
    for note in notes:
        centre = centre_pitch(midi, note["first_row"], note["end_row"], settings, hop_s)
        if centre == centre:
            note["pitch"] = centre
    counts["octaves_folded"] = fold_octaves(notes, settings, hop_s)
    counts["notes"] = len(notes)
    return [{"first_row": n["first_row"], "end_row": n["end_row"], "pitch": n["pitch"]} for n in notes], counts
