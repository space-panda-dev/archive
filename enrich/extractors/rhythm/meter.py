"""Tempo levels, meter, and downbeats for the rhythm extractor (ADR 0074).

A beat tracker follows one pulse, and which one it follows is a choice it
makes window by window: on dense playing it may lock to the eighths (2:1 of
the beat a musician counts), to a cross-rhythm (3:2 or 4:3), or to the beat,
and change its mind between sections. A single tempo then reports a level
that may never be played (a median across levels), and a change of feel at
a constant pulse (4/4 to 12/8) reads as a change of tempo. This module turns
the tracker's beats into:

- a *primary level* each second, chosen by onset support: every level
  related to the tracker's beat by 1:2, 2:3, 3:4, 1:1, 4:3, 3:2 or 2:1 is
  laid on the music as a pulse grid (at the phase that fits best), and its
  support is the mean accent found on its pulses. Support alone cannot tell
  octaves apart (every other beat of a well-supported level is as well
  supported), so a broad preference for tempi near `prior_bpm` (a
  log-normal, `prior_octaves` wide) weighs it, and a cost for changing level
  (`level_switch_cost` per octave of change) keeps the choice from flickering
  between levels the tracker switches between;
- `pulse`: the beats of the primary level, derived from the tracker's beats
  by the chosen relation;
- the beat's *division*, binary or ternary, from whether the accents
  between pulses fall at halves and quarters or at thirds;
- *beats per bar* from how the beat-by-beat pattern (where the harmony
  moves, the accents, the low accents) repeats: a bar of m beats makes beats
  m and 2m apart alike;
- *downbeats*, by a bar-position model over the pulses whose evidence is a
  low-frequency accent and a change of chroma at the bar line;
- *spans* of steady tempo and meter, and the *changes* between them: a
  change across which the beat or one of its subdivisions persists is a
  change of meter, not of tempo.

Everything here is a pure function of numpy arrays, so it is tested on
synthetic input without audio. The spectral rows are measured by `rhythm.py`
from its own decode.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Sequence

import numpy

RATE = 44100
HOP = 512  # the spectral rows: one every HOP samples, row r centred on sample r * HOP
FRAME = 2048
ROWS_PER_SECOND = RATE / HOP
LOW_HZ = 200.0  # the low band's upper edge: kick drum and bass
CHROMA_HZ = (100.0, 5000.0)
ACCENT_MEAN_SECONDS = 0.4  # the moving mean an accent must rise above
PEAK_SECONDS = 0.035  # how far from a pulse its accent is looked for
RELATIONS = (Fraction(1, 2), Fraction(2, 3), Fraction(3, 4), Fraction(1), Fraction(4, 3), Fraction(3, 2),
             Fraction(2))
RELATION_NAMES = tuple(f"{r.numerator}:{r.denominator}" for r in RELATIONS)
METERS = (4, 3, 5, 7)  # beats per bar, 4 first so a tie (no evidence) is 4; 2 and 6 read as 4 and 3
LAGS = 14  # the longest beat lag the repetition is measured at: two bars of 7
METER_MARGIN = 0.02  # how much better than 4 another meter's repetition must be
DIVISION_SECONDS = 0.012  # how far from a half, quarter or third its accent is looked for
SPAN_TOLERANCE = 0.08  # tempi within 8% are one level; 4:3 is 33% apart
MINIMUM_SPAN_SECONDS = 8.0
CHANGE_GAP_SECONDS = 4.0  # spans further apart are not compared


# -- spectral rows ------------------------------------------------------------

def chroma_matrix() -> tuple[numpy.ndarray, numpy.ndarray]:
    """The FFT bins between CHROMA_HZ, and a bins-by-12 matrix summing each
    bin's energy into its pitch class (0 is A)."""
    freqs = numpy.fft.rfftfreq(FRAME, 1 / RATE)
    bins = numpy.flatnonzero((freqs >= CHROMA_HZ[0]) & (freqs <= CHROMA_HZ[1]))
    classes = numpy.round(12 * numpy.log2(freqs[bins] / 440.0)).astype(int) % 12
    matrix = numpy.zeros((len(bins), 12), dtype=numpy.float32)
    matrix[numpy.arange(len(bins)), classes] = 1
    return bins, matrix


@dataclass
class Rows:
    """Spectral rows, one every HOP samples: `flux` (the summed rise of
    log-compressed magnitude from the row before), `low` (the same below
    LOW_HZ), and `chroma` (12 columns of energy by pitch class)."""
    flux: numpy.ndarray
    low: numpy.ndarray
    chroma: numpy.ndarray

    @staticmethod
    def join(parts: Sequence["Rows"]) -> "Rows":
        """The parts' rows in order, copied once."""
        if not parts:
            return Rows(numpy.zeros(0, numpy.float32), numpy.zeros(0, numpy.float32),
                        numpy.zeros((0, 12), numpy.float32))
        return Rows(numpy.concatenate([p.flux for p in parts]), numpy.concatenate([p.low for p in parts]),
                    numpy.vstack([p.chroma for p in parts]))


def spectral_rows(audio: numpy.ndarray, start: int, first: int, last: int, block: int = 2048) -> Rows:
    """Rows `first` to `last - 1` of a recording, measured from `audio`, its
    samples from sample `start` on. Row r's frame is FRAME samples centred on
    sample r * HOP, Hann-windowed; samples outside `audio` are zeros (the
    caller passes audio reaching past the rows it asks for, except at the
    recording's ends). Rows are measured in blocks, so memory does not grow
    with the chunk."""
    freqs = numpy.fft.rfftfreq(FRAME, 1 / RATE)
    low_bins = (freqs > 0) & (freqs < LOW_HZ)
    chroma_bins, matrix = chroma_matrix()
    window = numpy.hanning(FRAME).astype(numpy.float32)
    padded = numpy.concatenate((numpy.zeros(FRAME, numpy.float32), numpy.asarray(audio, numpy.float32),
                                numpy.zeros(FRAME, numpy.float32)))
    count = max(0, last - first)
    flux = numpy.zeros(count, numpy.float32)
    low = numpy.zeros(count, numpy.float32)
    chroma = numpy.zeros((count, 12), numpy.float32)
    offsets = numpy.arange(FRAME)
    for at in range(0, count, block):
        rows = numpy.arange(first + at - 1, first + min(count, at + block))  # one row before, for the rise
        begins = rows * HOP - FRAME // 2 - start + FRAME
        inside = (begins >= 0) & (begins + FRAME <= len(padded))
        frames = numpy.zeros((len(rows), FRAME), numpy.float32)
        frames[inside] = padded[begins[inside, None] + offsets]
        magnitude = numpy.abs(numpy.fft.rfft(frames * window, axis=1))
        level = numpy.log1p(100 * magnitude)
        rise = numpy.maximum(numpy.diff(level, axis=0), 0)
        if rows[0] < 0:  # the recording's first row has no row before it
            rise[0] = 0
        flux[at:at + len(rows) - 1] = rise.sum(axis=1)
        low[at:at + len(rows) - 1] = rise[:, low_bins].sum(axis=1)
        chroma[at:at + len(rows) - 1] = (magnitude[1:, chroma_bins] ** 2) @ matrix
    return Rows(flux, low, chroma)


# -- accents ------------------------------------------------------------------

def moving_mean(values: numpy.ndarray, width: int) -> numpy.ndarray:
    """The mean of the `width` values centred on each, fewer at the ends."""
    totals = numpy.concatenate(([0.0], numpy.cumsum(values, dtype=numpy.float64)))
    index = numpy.arange(len(values))
    low = numpy.maximum(index - width // 2, 0)
    high = numpy.minimum(index + width - width // 2, len(values))
    return (totals[high] - totals[low]) / numpy.maximum(high - low, 1)


def moving_max(values: numpy.ndarray, reach: int) -> numpy.ndarray:
    """The largest of the values within `reach` rows of each."""
    if not len(values) or reach <= 0:
        return numpy.asarray(values, numpy.float64).copy()
    padded = numpy.concatenate((numpy.full(reach, -numpy.inf), values, numpy.full(reach, -numpy.inf)))
    return numpy.lib.stride_tricks.sliding_window_view(padded, 2 * reach + 1).max(axis=1)


def accents(flux: numpy.ndarray) -> numpy.ndarray:
    """How far each row's flux rises above its moving mean, never below 0."""
    flux = numpy.asarray(flux, numpy.float64)
    return numpy.maximum(flux - moving_mean(flux, int(round(ACCENT_MEAN_SECONDS * ROWS_PER_SECOND))), 0)


def peaks(accent: numpy.ndarray) -> numpy.ndarray:
    """The largest accent within PEAK_SECONDS of each row: what a pulse
    placed at that row finds."""
    return moving_max(accent, int(round(PEAK_SECONDS * ROWS_PER_SECOND)))


def at(curve: numpy.ndarray, times: numpy.ndarray) -> numpy.ndarray:
    """The curve's values at the rows nearest `times` (seconds)."""
    if not len(curve):
        return numpy.zeros(len(times))
    return curve[numpy.clip(numpy.round(numpy.asarray(times) * ROWS_PER_SECOND).astype(int), 0, len(curve) - 1)]


# -- levels -------------------------------------------------------------------

def pulses(beats: numpy.ndarray, relation: Fraction, phase: int, longest: float) -> numpy.ndarray:
    """A pulse at `relation` times the beats' rate: for relation a/b, a pulses
    evenly spaced across every b beats, the groups of b beats starting at
    beat `phase`. Beats are not evenly spaced, so a pulse between two beats
    is placed in proportion between them. A group with a gap longer than
    `longest` seconds is skipped: the gap is not a beat."""
    beats = numpy.asarray(beats, numpy.float64)
    a, b = relation.numerator, relation.denominator
    starts = numpy.arange(phase, len(beats) - b, b)
    if not len(starts):
        return numpy.zeros(0)
    groups = beats[starts[:, None] + numpy.arange(b + 1)]
    steps = numpy.diff(groups, axis=1)
    groups = groups[numpy.all((steps > 0) & (steps <= longest), axis=1)]
    position = numpy.arange(a) * b / a  # in beats from the group's first
    whole = numpy.floor(position).astype(int)
    fraction = position - whole
    return (groups[:, whole] + fraction * (groups[:, whole + 1] - groups[:, whole])).ravel()


def support(beats: numpy.ndarray, peak: numpy.ndarray, relation: Fraction, longest: float,
            reference: float) -> tuple[float, int]:
    """The level's onset support and the phase it was found at: the mean
    accent on its pulses, each as a fraction of `reference` (at most 1), at
    the best of its phases; 0 with fewer than two pulses."""
    best, where = 0.0, 0
    if reference <= 0:
        return best, where
    for phase in range(relation.denominator):
        placed = pulses(beats, relation, phase, longest)
        if len(placed) < 2:
            continue
        score = float(numpy.mean(numpy.minimum(at(peak, placed) / reference, 1.0)))
        if score > best:
            best, where = score, phase
    return best, where


def window_beats(beats: numpy.ndarray, centre: float, window: float, extra: int) -> numpy.ndarray:
    """The beats in the window centred on `centre`, and `extra` after it, so
    the last group of a slower level is complete."""
    first = numpy.searchsorted(beats, centre - window / 2)
    last = numpy.searchsorted(beats, centre + window / 2)
    return beats[first:min(len(beats), last + extra)]


def reference_level(peak: numpy.ndarray, centre: float, window: float) -> float:
    """The accent a pulse counts as fully supported by: the 95th percentile of
    the peaks in the window."""
    low = max(0, int((centre - window / 2) * ROWS_PER_SECOND))
    high = min(len(peak), int((centre + window / 2) * ROWS_PER_SECOND))
    return float(numpy.percentile(peak[low:high], 95)) if high > low else 0.0


def support_table(beats: Sequence[float], peak: numpy.ndarray, rows: int, window: float, longest: float,
                  relations: Sequence[Sequence[Fraction]]) -> numpy.ndarray:
    """For each second k (the window centred on it) and each of
    `relations[k]`, the support of that level of the tracker's beats."""
    beats = numpy.asarray(beats, numpy.float64)
    table = numpy.zeros((rows, len(relations[0]) if rows else 0))
    for k in range(rows):
        extra = max(r.denominator for r in relations[k])
        chosen = window_beats(beats, k, window, extra)
        if len(chosen) < 4:
            continue
        reference = reference_level(peak, k, window)
        for j, relation in enumerate(relations[k]):
            table[k, j] = support(chosen, peak, relation, longest, reference)[0]
    return table


def preference(bpm: numpy.ndarray, centre: float, octaves: float) -> numpy.ndarray:
    """A log-normal preference for tempi near `centre`, `octaves` wide."""
    return numpy.exp(-0.5 * (numpy.log2(numpy.asarray(bpm, numpy.float64) / centre) / octaves) ** 2)


def primary_levels(table: numpy.ndarray, tempo: numpy.ndarray, config: dict[str, Any]) -> tuple[numpy.ndarray,
                                                                                                numpy.ndarray]:
    """The primary level each second, by Viterbi over the RELATIONS of the
    tracker's tempo that lie in [min_tempo, max_tempo]: each second scores
    log(support x preference), and a change of level from one second to the
    next costs `level_switch_cost` per octave changed. Returns the primary
    tempo (0 where the tracker has none) and the index of its relation."""
    rows = len(tempo)
    ratios = numpy.asarray([float(r) for r in RELATIONS])
    if not rows:
        return numpy.zeros(0), numpy.zeros(0, int)
    tempo = numpy.asarray(tempo, numpy.float64)
    candidates = tempo[:, None] * ratios[None, :]
    allowed = (tempo[:, None] > 0) & (candidates >= config["min_tempo"]) & (candidates <= config["max_tempo"])
    safe = numpy.where(allowed, candidates, 1.0)
    score = numpy.where(allowed, numpy.log(table * preference(safe, config["prior_bpm"], config["prior_octaves"])
                                           + 1e-3), -1e9)
    score[tempo <= 0] = 0.0
    cost = config["level_switch_cost"]
    total = score[0].copy()
    back = numpy.zeros((rows, len(RELATIONS)), int)
    back[0] = numpy.arange(len(RELATIONS))
    previous = safe[0] if tempo[0] > 0 else None
    for k in range(1, rows):
        if tempo[k] <= 0:
            back[k] = numpy.arange(len(RELATIONS))
            continue
        if previous is None:  # the first second with beats: nothing to change from
            previous = safe[k]
        change = -cost * numpy.abs(numpy.log2(safe[k][None, :] / previous[:, None]))
        options = total[:, None] + change
        back[k] = numpy.argmax(options, axis=0)
        total = options[back[k], numpy.arange(len(RELATIONS))] + score[k]
        previous = safe[k]
    path = numpy.zeros(rows, int)
    path[-1] = int(numpy.argmax(total))
    for k in range(rows - 1, 0, -1):
        path[k - 1] = back[k, path[k]]
    primary = numpy.where(tempo > 0, candidates[numpy.arange(rows), path], 0.0)
    return primary, path


def primary_pulse(beats: Sequence[float], peak: numpy.ndarray, tempo: numpy.ndarray, path: numpy.ndarray,
                  longest: float) -> numpy.ndarray:
    """The primary level's pulses: over each run of seconds with one
    relation to the tracker, that relation's pulses of the run's beats at
    their best-supported phase. Where two runs meet, a pulse closer than
    half a period to the one before is dropped."""
    beats = numpy.asarray(beats, numpy.float64)
    found: list[float] = []
    k, rows = 0, len(tempo)
    while k < rows:
        if tempo[k] <= 0:
            k += 1
            continue
        end = k
        while end + 1 < rows and tempo[end + 1] > 0 and path[end + 1] == path[k]:
            end += 1
        relation = RELATIONS[path[k]]
        first = numpy.searchsorted(beats, k - 0.5)
        last = numpy.searchsorted(beats, end + 0.5)
        run = beats[first:min(len(beats), last + relation.denominator)]
        phase = support(run, peak, relation, longest, float(numpy.max(peak, initial=0.0)) or 1.0)[1]
        placed = pulses(run, relation, phase, longest)
        placed = placed[(placed >= k - 0.5) & (placed < end + 0.5)]
        period = 60.0 / (tempo[k] * float(relation))
        for time in placed:
            if not found or time - found[-1] > period / 2:
                found.append(float(time))
        k = end + 1
    return numpy.asarray(found)


# -- division, meter and downbeats ----------------------------------------------

def smooth_choice(scores: numpy.ndarray, switch: float) -> numpy.ndarray:
    """The best path through per-step scores (steps x states) where changing
    state costs `switch`."""
    steps, states = scores.shape
    if not steps:
        return numpy.zeros(0, int)
    total = scores[0].copy()
    back = numpy.zeros((steps, states), int)
    penalty = switch * (1 - numpy.eye(states))
    for i in range(1, steps):
        options = total[:, None] - penalty
        back[i] = numpy.argmax(options, axis=0)
        total = options[back[i], numpy.arange(states)] + scores[i]
    path = numpy.zeros(steps, int)
    path[-1] = int(numpy.argmax(total))
    for i in range(steps - 1, 0, -1):
        path[i - 1] = back[i, path[i]]
    return path


def divisions(pulse: numpy.ndarray, accent: numpy.ndarray, switch: float = 6.0) -> numpy.ndarray:
    """For each pulse but the last, 2 or 3: whether the accents between it and
    the next fall where a binary division puts them (the half, or the
    quarters) or where a ternary one does (the thirds), through a path that
    changes only on sustained evidence. The binary evidence is the larger of
    the half's accent and the quarters' mean, so sixteenths that are absent
    or weak do not count against eighths. Accents are looked for within
    DIVISION_SECONDS of each place, less than PEAK_SECONDS, as a quarter and a
    third of a fast beat are closer than that (31 ms at 160 BPM). 2 where
    nothing is played between pulses."""
    if len(pulse) < 2:
        return numpy.zeros(0, int)
    near = moving_max(numpy.asarray(accent, numpy.float64), int(round(DIVISION_SECONDS * ROWS_PER_SECOND)))
    start, step = pulse[:-1], numpy.diff(pulse)
    half = at(near, start + step / 2)
    quarters = (at(near, start + step / 4) + at(near, start + 3 * step / 4)) / 2
    thirds = (at(near, start + step / 3) + at(near, start + 2 * step / 3)) / 2
    binary = numpy.maximum(half, quarters)
    scale = float(numpy.mean(binary + thirds)) or 1.0
    path = smooth_choice(numpy.column_stack((binary, thirds)) / scale, switch)
    return numpy.where(path == 1, 3, 2)


def beat_patterns(pulse: numpy.ndarray, peak: numpy.ndarray, low: numpy.ndarray, chroma: numpy.ndarray,
                  points: int = 8, block: int = 64) -> numpy.ndarray:
    """One row a beat (between consecutive pulses): how far its mean chroma
    moved from the beat before (a bar repeats where its harmony moves, not
    which chord it holds, which changes bar to bar), and the accents and low
    accents at `points` evenly spaced points in it. Each column is
    standardised within blocks of `block` beats, so a bar's repetition is
    measured against the music around it rather than its overall sound, and
    the harmony column weighs as much as each group of accents."""
    count = len(pulse) - 1
    if count < 1:
        return numpy.zeros((0, 1 + 2 * points))
    start, step = pulse[:-1], numpy.diff(pulse)
    places = start[:, None] + step[:, None] * numpy.arange(points)[None, :] / points
    rows = numpy.clip(numpy.round(pulse * ROWS_PER_SECOND).astype(int), 0, max(len(chroma) - 1, 0))
    totals = numpy.vstack((numpy.zeros((1, 12)), numpy.cumsum(chroma, axis=0, dtype=numpy.float64)))
    span = numpy.maximum(rows[1:] - rows[:-1], 1)
    mean_chroma = (totals[numpy.minimum(rows[:-1] + span, len(chroma))] - totals[rows[:-1]]) / span[:, None]
    unit = mean_chroma / (numpy.linalg.norm(mean_chroma, axis=1, keepdims=True) + 1e-12)
    moved = numpy.concatenate(([0.0], 1 - numpy.sum(unit[1:] * unit[:-1], axis=1)))
    features = numpy.hstack((moved[:, None], at(peak, places.ravel()).reshape(count, points),
                             at(low, places.ravel()).reshape(count, points)))
    for first in range(0, count, block):
        part = features[first:first + block]
        # A column that hardly varies (identical clicks, a held chord) carries no pattern: its spread is
        # floored at a tenth of its size, so rounding to the rows does not become one.
        spread = numpy.maximum(part.std(axis=0), 0.1 * numpy.abs(part).mean(axis=0)) + 1e-9
        features[first:first + block] = (part - part.mean(axis=0)) / spread
    features[:, 0] *= numpy.sqrt(points)
    return features


def repetition(features: numpy.ndarray) -> numpy.ndarray:
    """beats x (LAGS + 1): the cosine similarity of each beat's pattern to the
    one `lag` beats later (NaN past the end)."""
    count = len(features)
    unit = features / (numpy.linalg.norm(features, axis=1, keepdims=True) + 1e-9)
    similar = numpy.full((count, LAGS + 1), numpy.nan)
    for lag in range(1, LAGS + 1):
        if lag < count:
            similar[:count - lag, lag] = numpy.sum(unit[:count - lag] * unit[lag:], axis=1)
    return similar


def meter_scores(similar: numpy.ndarray, half: int = 24) -> numpy.ndarray:
    """beats x METERS: how much better than the average lag the beats m and
    2m apart repeat, over the `half` beats either side of each. Duple meter
    is the larger of the 2- and 4-beat scores and is called 4 (2/4 and 4/4
    differ in notation, not in what repeats); 6 is the 3-beat score's twin
    and is called 3."""
    count = len(similar)
    if not count:
        return numpy.zeros((0, len(METERS)))
    filled = numpy.nan_to_num(similar)
    present = (~numpy.isnan(similar)).astype(float)
    sums = numpy.vstack((numpy.zeros((1, LAGS + 1)), numpy.cumsum(filled, axis=0)))
    counts = numpy.vstack((numpy.zeros((1, LAGS + 1)), numpy.cumsum(present, axis=0)))
    index = numpy.arange(count)
    low, high = numpy.maximum(index - half, 0), numpy.minimum(index + half, count)
    lag_mean = (sums[high] - sums[low]) / numpy.maximum(counts[high] - counts[low], 1)
    baseline = lag_mean[:, 1:].mean(axis=1)

    def pair(m: int) -> numpy.ndarray:
        return (lag_mean[:, m] + lag_mean[:, 2 * m]) / 2 if 2 * m <= LAGS else lag_mean[:, m]

    columns = {3: pair(3), 4: numpy.maximum(pair(2), pair(4)), 5: pair(5), 7: pair(7)}
    return numpy.column_stack([columns[m] - baseline for m in METERS])


def meters(scores: numpy.ndarray, weight: float = 10.0, switch: float = 3.0,
           margin: float = METER_MARGIN) -> numpy.ndarray:
    """Beats per bar for each beat: METERS through a path weighing each beat's
    scores by `weight` and charging `switch` for a change, so the meter
    changes only where another one is better for a stretch of beats. A meter
    other than 4 must score `margin` better than 4 to count: where nothing
    repeats (a metronome), the noise in the scores is not a meter."""
    if not len(scores):
        return numpy.zeros(0, int)
    handicap = numpy.where(numpy.asarray(METERS) == 4, 0.0, margin)
    return numpy.asarray(METERS)[smooth_choice(weight * (scores - handicap), switch)]


def robust_z(values: numpy.ndarray) -> numpy.ndarray:
    """Values standardised by median and median absolute deviation, clipped
    to +-3."""
    values = numpy.asarray(values, numpy.float64)
    if not len(values):
        return values
    middle = numpy.median(values)
    spread = 1.4826 * numpy.median(numpy.abs(values - middle))
    if spread <= 0:
        spread = numpy.std(values) or 1.0
    return numpy.clip((values - middle) / spread, -3, 3)


def downbeat_evidence(pulse: numpy.ndarray, low: numpy.ndarray, chroma: numpy.ndarray) -> numpy.ndarray:
    """For each pulse: its low accent plus the change of chroma from the two
    beats before it to the two after, each robustly standardised. A bar line
    is where a bass note or kick lands and where the harmony moves."""
    count = len(pulse)
    if count < 2:
        return numpy.zeros(count)
    rows = numpy.clip(numpy.round(pulse * ROWS_PER_SECOND).astype(int), 0, max(len(chroma) - 1, 0))
    totals = numpy.vstack((numpy.zeros((1, 12)), numpy.cumsum(chroma, axis=0, dtype=numpy.float64)))
    before = numpy.concatenate(([rows[0]] * 2, rows[:-2]))
    after = numpy.concatenate((rows[2:], [rows[-1]] * 2))
    first = (totals[rows] - totals[before]) / numpy.maximum(rows - before, 1)[:, None]
    second = (totals[after] - totals[rows]) / numpy.maximum(after - rows, 1)[:, None]
    norm = numpy.linalg.norm(first, axis=1) * numpy.linalg.norm(second, axis=1)
    change = 1 - numpy.sum(first * second, axis=1) / numpy.where(norm > 0, norm, 1)
    change[norm <= 0] = 0
    low_peak = at(peaks(numpy.asarray(low, numpy.float64)), pulse)
    return robust_z(low_peak) + robust_z(change)


def bar_positions(evidence: numpy.ndarray, per_bar: numpy.ndarray, reset_cost: float = 6.0) -> numpy.ndarray:
    """Each pulse's position in its bar (0 is the downbeat), by Viterbi over
    positions: position j moves to j + 1, and to 0 after the bar's last beat
    (`per_bar[i]` beats); a bar may end early at `reset_cost` (a bar of 2/4
    inside 4/4). A downbeat scores its evidence; other positions score 0."""
    count = len(evidence)
    if not count:
        return numpy.zeros(0, int)
    states = int(max(per_bar))
    impossible = -1e18
    total = numpy.full(states, impossible)
    total[:per_bar[0]] = 0.0
    total[0] += evidence[0]
    back = numpy.zeros((count, states), int)
    for i in range(1, count):
        m = int(per_bar[i])
        new = numpy.full(states, impossible)
        choice = numpy.zeros(states, int)
        for j in range(1, m):  # the next beat of the bar
            new[j], choice[j] = total[j - 1], j - 1
        # A downbeat follows the last beat of a bar, or any other beat at a cost.
        ending = total - reset_cost
        last = int(per_bar[i - 1]) - 1
        ending[last] = total[last]
        choice[0] = int(numpy.argmax(ending))
        new[0] = ending[choice[0]] + evidence[i]
        total, back[i] = new, choice
    position = numpy.zeros(count, int)
    position[-1] = int(numpy.argmax(total))
    for i in range(count - 1, 0, -1):
        position[i - 1] = back[i, position[i]]
    return position


# -- spans and changes -------------------------------------------------------

def meter_label(per_bar: int, division: int) -> str:
    """4 beats divided in two is 4/4; divided in three, 12/8."""
    return f"{per_bar}/4" if division == 2 else f"{3 * per_bar}/8"


@dataclass(frozen=True)
class Span:
    start: float
    end: float
    bpm: float
    label: str
    division: int


def spans(pulse: numpy.ndarray, label: Sequence[str], division: Sequence[int]) -> list[Span]:
    """Runs of beats (between consecutive pulses) with one meter label and a
    length within SPAN_TOLERANCE of the run's median; a beat more than twice
    the run's median long is a gap, which no span crosses. A run shorter than
    MINIMUM_SPAN_SECONDS joins the longer of the runs it touches, or is
    dropped if it touches none. Each span's tempo is 60 over its median
    beat, its meter its most common label."""
    beats = numpy.diff(numpy.asarray(pulse, numpy.float64))
    runs: list[list[int]] = []  # beat indices, consecutive
    for i, length in enumerate(beats):
        if runs and runs[-1][-1] == i - 1:
            run = runs[-1]
            usual = float(numpy.median(beats[run]))
            if length > 2 * usual:
                continue  # a gap: the next beat starts a new run
            if label[i] == label[run[-1]] and abs(numpy.log(length / usual)) <= numpy.log(1 + SPAN_TOLERANCE):
                run.append(i)
                continue
        runs.append([i])

    def seconds(run: list[int]) -> float:
        return float(pulse[run[-1] + 1] - pulse[run[0]])

    while runs:
        shortest = min(range(len(runs)), key=lambda r: seconds(runs[r]))
        if seconds(runs[shortest]) >= MINIMUM_SPAN_SECONDS:
            break
        touching = [r for r in (shortest - 1, shortest + 1) if 0 <= r < len(runs)
                    and (runs[r][0] == runs[shortest][-1] + 1 or runs[r][-1] + 1 == runs[shortest][0])]
        if touching:
            into = max(touching, key=lambda r: seconds(runs[r]))
            runs[into] = sorted(runs[into] + runs[shortest])
        runs.pop(shortest)
    found: list[Span] = []
    for run in runs:
        names, counts = numpy.unique([label[i] for i in run], return_counts=True)
        span = Span(float(pulse[run[0]]), float(pulse[run[-1] + 1]), float(60 / numpy.median(beats[run])),
                    str(names[int(numpy.argmax(counts))]), int(numpy.median([division[i] for i in run])))
        # Runs split by a stray beat and rejoined may leave two touching spans of one level and meter: one span.
        if (found and found[-1].label == span.label and found[-1].end == span.start
                and abs(numpy.log(span.bpm / found[-1].bpm)) <= numpy.log(1 + SPAN_TOLERANCE)):
            first = found[-1]
            weight = (first.end - first.start, span.end - span.start)
            bpm = float(numpy.exp(numpy.average(numpy.log([first.bpm, span.bpm]), weights=weight)))
            span = Span(first.start, span.end, bpm, span.label, span.division)
            found.pop()
        found.append(span)
    return found


def subdivisions(span: Span) -> tuple[float, float]:
    """The span's pulse rates one and two levels below the beat: eighths and
    sixteenths of a binary beat, triplet eighths and sixteenths of a ternary
    one."""
    return span.bpm * span.division, span.bpm * span.division * 2


def changes(found: list[Span]) -> list[dict[str, Any]]:
    """Where one span is followed by the next within CHANGE_GAP_SECONDS (a
    break, or a stretch where the tracker kept no beat): `meter` if the beat,
    or one of its subdivisions (`subdivisions`), stays within SPAN_TOLERANCE
    across the change, so the grid persists and only its grouping changed
    (sixteenths at 112 BPM in 4/4 are triplet eighths at 150 in 12/8);
    `tempo` otherwise. The value is the new tempo over the old. The same
    meter at the same level on both sides of a break is no change."""
    tolerance = numpy.log(1 + SPAN_TOLERANCE)
    found_changes = []
    for before, after in zip(found, found[1:]):
        if after.start - before.end > CHANGE_GAP_SECONDS:
            continue
        ratio = after.bpm / before.bpm
        if after.label == before.label and abs(numpy.log(ratio)) <= tolerance:
            continue  # the same level and meter on both sides of a break
        grid = [abs(numpy.log(a / b)) <= tolerance
                for a in (after.bpm, *subdivisions(after)) for b in (before.bpm, *subdivisions(before))]
        found_changes.append({"time": round(after.start, 6), "value": round(float(ratio), 6),
                              "label": "meter" if any(grid) else "tempo"})
    return found_changes


def most_time(found: list[Span]) -> tuple[float, str]:
    """The tempo level and the meter covering the most time: tempi within
    SPAN_TOLERANCE of one another count as one level, whose tempo is the
    time-weighted median of its spans'."""
    if not found:
        return 0.0, ""
    durations = numpy.asarray([s.end - s.start for s in found])
    tempi = numpy.asarray([s.bpm for s in found])
    close = numpy.abs(numpy.log(tempi[:, None] / tempi[None, :])) <= numpy.log(1 + SPAN_TOLERANCE)
    level = int(numpy.argmax(close.astype(float) @ durations))
    members = numpy.flatnonzero(close[level])
    order = members[numpy.argsort(tempi[members])]
    weights = numpy.cumsum(durations[order])
    bpm = float(tempi[order][numpy.searchsorted(weights, weights[-1] / 2)])
    labels: dict[str, float] = {}
    for s, d in zip(found, durations):
        labels[s.label] = labels.get(s.label, 0.0) + float(d)
    return bpm, max(sorted(labels), key=lambda name: labels[name])


# -- everything ----------------------------------------------------------------

@dataclass
class Analysis:
    primary: numpy.ndarray  # BPM each second, 0 where none
    alternatives: numpy.ndarray  # seconds x RELATIONS: support of each relation of the primary
    pulse: numpy.ndarray
    position: numpy.ndarray  # each pulse's place in its bar, 0 the downbeat
    per_bar: numpy.ndarray  # each pulse's bar length in beats
    labels: list[str]
    spans: list[Span]
    changes: list[dict[str, Any]]
    primary_bpm: float
    meter: str


def analyse(beats: Sequence[float], tempo: numpy.ndarray, rows: Rows, config: dict[str, Any]) -> Analysis:
    """The primary level, its pulse, meter, downbeats, spans and changes, from
    the gated beats, the tracker's tempo each second, and the spectral rows."""
    tempo = numpy.asarray(tempo, numpy.float64).reshape(-1)
    seconds = len(tempo)
    window = config["window_seconds"]
    longest = 60.0 / config["min_tempo"]
    accent = accents(rows.flux)
    peak = peaks(accent)
    low = accents(rows.low)
    table = support_table(beats, peak, seconds, window, longest, [RELATIONS] * seconds)
    primary, path = primary_levels(table, tempo, config)
    alternatives = support_table(beats, peak, seconds, window, longest,
                                 [[RELATIONS[path[k]] * r for r in RELATIONS] for k in range(seconds)])
    alternatives[primary <= 0] = 0
    pulse = primary_pulse(beats, peak, tempo, path, longest)
    if len(pulse) < 2:
        empty = numpy.zeros(0, int)
        return Analysis(primary, alternatives, pulse, empty, empty, [], [], [], 0.0, "")
    division = divisions(pulse, accent)
    per_beat = meters(meter_scores(repetition(beat_patterns(pulse, peak, low, rows.chroma))))
    # Each pulse takes the meter and division of the beat it starts; the last, its predecessor's.
    per_bar = numpy.concatenate((per_beat, per_beat[-1:]))
    division_each = numpy.concatenate((division, division[-1:]))
    position = bar_positions(downbeat_evidence(pulse, low, rows.chroma), per_bar)
    labels = [meter_label(int(m), int(d)) for m, d in zip(per_bar, division_each)]
    found = spans(pulse, labels[:-1], division)
    bpm, meter = most_time(found)
    return Analysis(primary, alternatives, pulse, position, per_bar, labels, found, changes(found), bpm, meter)


def bar_lengths(position: numpy.ndarray) -> numpy.ndarray:
    """For each pulse that starts a bar, how many pulses the bar holds (the
    last bar: up to the last pulse)."""
    starts = numpy.flatnonzero(position == 0)
    return numpy.diff(numpy.concatenate((starts, [len(position)])))
