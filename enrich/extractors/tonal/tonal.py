#!/usr/bin/env python3
"""The tonal extractor: tuning and its drift, pitch-class profiles referenced
to that tuning, the key as a note collection with its tonic and mode, and
dissonance (ADR 0074).

It reads the `frames` extractor's levels from the bank, decodes the
recording again for each frame's spectral peaks, and measures:

- `pitched`: which frames carry pitched sound (below), on the `frames` grid,
  so that every other output, and any reader, can leave out silence, talk,
  and noise the same way;
- `tuning_windows`: the tuning in overlapping windows of about twenty
  seconds, every row with its confidence and its share of pitched frames,
  and `tuning_drift`: the windows that are at least half pitched, as spans
  whose value is the window's tuning in cents, written near `tuning_cents`
  (so a session tuned 49 cents sharp does not jump to -51 and back).
  Silence, talk, and noise leave gaps rather than invented values;
  `drift_spread_cents` is the spread of the listed windows, from their
  tenth to their ninetieth percentile;
- `tuning_frequency`, `tuning_cents`, `tuning_confidence`: the recording's
  reference tuning, the confidence-weighted median of the confident windows
  (below), as the frequency of A4 in Hz and its distance from 440 Hz in
  cents, folded into [-50, 50);
- `detuned`, `detuned_spans`: whether the recording, and which windows of
  it, sit more than `max_detune_cents` (40) from 440 Hz, where a profile
  referenced to 440 Hz, as `frames/hpcp` is, splits each pitch class over
  two bins;
- `chroma` and `bass_chroma`: twelve-bin pitch-class profiles on the
  `frames` grid, referenced to the tuning of the frame's own stretch of the
  recording rather than to 440 Hz, from the peaks between 100 and 5,000 Hz
  and between 40 and 250 Hz. Bin 0 is A, as in `frames/hpcp`. A bin holds
  the energy (squared magnitude) of the peaks within a semitone of its
  pitch class, weighted by a squared cosine of their distance, not
  normalised, so loud and quiet frames keep their difference; a frame below
  the level gate is 0. The `chords` extractor reads them;
- `key`, `scale`, `mode`, `scale_notes`, `key_strength`, `key_margin`: the
  key of the pitched frames (below), and `keys`: the same in windows of
  about thirty seconds that are at least half pitched, as spans labelled
  like "F dorian" whose value is the strength; `key_regions` lays those
  windows end to end (each keeps the stretch nearer its centre than its
  neighbours'), merging neighbours with one label, as a timeline;
- `dissonance`: `Dissonance` (sensory roughness) of every frame that
  passes the level gate, on the `frames` grid; a frame below it reads 0, as
  silence does, so a quiet room's noise is not averaged in. Talk and noise
  above the gate still have a value: mask with `pitched` to describe music.

What it reuses and what it cannot. The level gate reads `frames/rms_db`.
Everything else needs each frame's spectral peaks, frequency by frequency,
which the bank does not hold (a frame's peaks are a ragged list, which no
output kind declares, and `frames` keeps only a twelve-bin profile
referenced to 440 Hz). So this extractor decodes the recording again, with
the `frames` extractor's own decoder and framing, and checks that it made as
many frames as `frames` did, so its series line up row for row. It decodes
twice: the first pass finds the pitched frames, the tuning, and the
dissonance; the second folds each frame's peaks onto the tuning the first
found. Holding the peaks between the passes instead would cost about 200 MB
for a three-hour rehearsal (some 210 peaks a frame); decoding again costs
about 1.5 s for six minutes of music. That is three times `frames`'
spectral cost: the exception to "decode once" that ADR 0074 records, with
what would remove it. `frames/hpcp` is not read: the profiles here are
referenced to the measured tuning, which `frames` cannot know.

Tuning. Every peak within `relative_db` (40 dB) of its frame's loudest and
below `max_frequency` (2,000 Hz) adds its magnitude to a histogram of its
distance above the equal-tempered grid on 440 Hz, in one-cent bins round a
circle of 100 cents. Upper partials are left out because a real tone's sit
off the grid (the fifth harmonic 14 cents flat, the seventh 31), and the
quiet peaks because they are mostly noise. The concentration of a histogram
about a tuning is the share of its weight within `concentration_cents` (15)
of it, going round the circle: peaks that agree on one tuning give nearly
1, and noise, whose peaks fall anywhere, about the width's share of the
circle (31 %). A window's tuning is the centre of the 15-cent stretch that
concentrates its pitched frames' histogram most, refined to the circular
mean of the weight inside that stretch; its confidence is the concentration
of all its sounding frames' histogram about that tuning, so noise the pitch
gate let through still lowers it. The recording's tuning is the median of
the windows that are at least half pitched and at least `min_confidence`
(0.4) confident, each weighted by its confidence; with none, it is 440 Hz
with confidence 0. Version 2 used Essentia's `TuningFrequency`, whose
estimate over a long stretch of a dense mix, and over white or pink noise,
falls to 434.2 Hz (-23 cents) whatever the music: on an album of eighteen
masters eight read exactly that, where these windows read from -6 to +23
cents with confidence 0.4 to 0.8.

Pitched frames. The frames are taken in blocks of about five seconds; a
block is pitched when at least half of it sounds (passes the level gate)
and its sounding frames, each frame's histogram scaled to sum to one so
that every frame has one vote, concentrate about some tuning at least
`min_concentration` (0.4); a frame is pitched when it sounds and its block
is pitched. On synthetic sound music concentrates 0.66 to 1.0 even under
noise or drums, and noise, talk, drums alone, and a gliding tone 0.32 to
0.36. On the masters, restricting the histogram to the loud peaks below
2 kHz raised the pitched share of three dense tracks from 0.24-0.41 to
0.64-0.82; noise and talk stay flat either way. Without the equal votes a
few loud, in-tune frames of music carried a block of talk past the
threshold (six music frames among 54 read 0.41); with them it takes about
eight, so up to about four seconds of talk beside music can still be
marked pitched, as in version 2: the decision is a block's.

The profiles. Each frame's tuning is the confident windows' tunings,
interpolated between their centres (and held beyond the first and last),
or the recording's when no window is confident; a peak is folded to the
pitch class nearest to it on that tuning's grid. A recording tuned near half
a semitone from 440 Hz therefore keeps its pitch classes whole, where
`frames/hpcp` splits them; at exactly half a semitone the name depends on
which side the fold puts the tuning.

The key. The profile of the pitched frames is the sum of their `chroma`
and of half their `bass_chroma`, each frame's square-rooted bins divided by
their sum, so every pitched frame counts once and the bass, which mostly
plays roots, leans the tonic towards it. It is correlated with a template
for every tonic of every mode of the diatonic collection (ionian, dorian,
phrygian, lydian, mixolydian, aeolian, locrian) and of the harmonic and
melodic minor collections: the tonic weighs 5, the fifth 4.5, the third
4.25, the collection's other notes 3.75, and the notes outside it 2 (the
shape of Temperley's major profile, applied to any mode, so the seven
modes of one collection differ only in which note is the tonic). Harmonic
and melodic minor give up `collection_penalty` (0.02), so that a diatonic
reading wins a tie. `key` is the tonic, named as Essentia names notes (A,
Bb, B, C, C#, D, Eb, E, F, F#, G, Ab); `mode` the mode; `scale_notes` the
collection from the tonic up; `scale` is "major" for ionian, lydian, and
mixolydian and "minor" for the rest, so a reader of version 2 still finds
the key it expects; `key_strength` is the winner's correlation and
`key_margin` how far it leads the runner-up. Version 2 read "Eb major" for
music in F dorian, which has Eb major's notes and F's tonic. On the album
above, the new tonic agreed with 15 of 38 published key claims (Spotify's,
ChordU's, Ultimate Guitar's, and the keys of note-for-note transcriptions)
where version 2's agreed with 11; the claims agree with each other on the
tonic in 11 of 31 pairs. That is evidence about this album only; `keys` is a
window-by-window description, not a claim of modulation.

Times are seconds from the first decoded sample. Each frame stands for the
hop centred on its own centre, so a span of frames k0..k1 runs from half a
hop before frame k0's centre to half a hop after frame k1's, clipped to the
decoded audio, and consecutive spans touch. Invoked by the runner as
`tonal.py <request.json>`; the settings are fixed, and a request with other
parameters is refused.
"""

from __future__ import annotations

import importlib.metadata
import json
import platform
import sys
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

ENRICH = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ENRICH))
sys.path.insert(0, str(ENRICH / "extractors" / "frames"))

import numpy  # noqa: E402

import cover  # noqa: E402
import frames  # noqa: E402

RATE, FRAME, HOP = frames.RATE, frames.FRAME, frames.HOP
SECONDS_PER_ROW = HOP / RATE
BINS = 100  # one-cent bins of the deviation from the tempered grid

PEAKS = {"sampleRate": RATE, "orderBy": "frequency", "magnitudeThreshold": "0.00001", "minFrequency": 40,
         "maxFrequency": 5000, "maxPeaks": 10000}
GATE = {"rms_db_min": -60}
HISTOGRAM = {"relative_db": 40, "max_frequency": 2000}
PITCHED = {"block_frames": 54, "concentration_cents": 15, "min_sounding": "0.5", "min_concentration": "0.4",
           "votes": "one-per-sounding-frame"}
TUNING = {"estimator": "histogram-peak", "window_frames": 216, "hop_frames": 108, "min_pitched": "0.5",
          "min_confidence": "0.4", "recording": "confidence-weighted-median", "max_detune_cents": 40}
CHROMA = {"treble_hz": [100, 5000], "bass_hz": [40, 250], "relative_db": 60, "weighting": "cosine-squared",
          "width_semitones": 1, "reference": "local-tuning"}
KEY = {"weights": {"tonic": "5", "fifth": "4.5", "third": "4.25", "scale": "3.75", "outside": "2"},
       "bass_weight": "0.5", "collections": ["diatonic", "harmonic minor", "melodic minor"],
       "collection_penalty": "0.02", "window_frames": 324, "hop_frames": 162, "min_pitched": "0.5"}
DISSONANCE = {"maxPeaks": 100, "relative_threshold": "0.01"}
TUNING_COLUMNS = ("cents", "confidence", "pitched_share")

NOTES = ("A", "Bb", "B", "C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab")  # bin 0 is A, as Essentia's
DIATONIC = (0, 2, 4, 5, 7, 9, 11)
MODES = ("ionian", "dorian", "phrygian", "lydian", "mixolydian", "aeolian", "locrian")
COLLECTIONS = {"diatonic": DIATONIC, "harmonic minor": (0, 2, 3, 5, 7, 8, 11),
               "melodic minor": (0, 2, 3, 5, 7, 9, 11)}
MAJOR_MODES = ("ionian", "lydian", "mixolydian")


class TonalError(Exception):
    pass


def parameters() -> dict[str, Any]:
    """The settings, which the manifest must declare exactly."""
    return {"essentia": cover.ESSENTIA_BUILD, "decode_arguments": frames.decode_arguments("{input}"),
            "sample_scale": 32768, "analysis_rate": RATE, "frame_size": FRAME, "hop_size": HOP,
            "framing": "essentia-framegenerator-centred", "windowing": cover.ANALYSIS["windowing"],
            "spectral_peaks": PEAKS, "gate": GATE, "histogram": HISTOGRAM, "pitched": PITCHED, "tuning": TUNING,
            "chroma": CHROMA, "key": KEY, "dissonance": DISSONANCE}


def tuning_grid() -> dict[str, int]:
    """Row w of `tuning_windows` is the window of frames w x hop_frames to
    w x hop_frames + window_frames - 1, which stand for the audio from half a
    hop before the first one's centre to half a hop after the last one's, so
    it is centred midway between those two frames' centres. The grid cannot
    say that the last row may be cut short by the end of the recording: its
    window holds fewer frames (its pitched share is of those it holds), and
    `tuning_drift` gives its true extent."""
    return {"sample_rate": RATE, "hop": TUNING["hop_frames"] * HOP, "frame": TUNING["window_frames"] * HOP,
            "first_centre": (TUNING["window_frames"] - 1) * HOP // 2}


# -- pure arithmetic, tested on its own ---------------------------------------

def fold(cents: float) -> float:
    """Cents from 440 Hz folded into [-50, 50)."""
    return (cents + 50.0) % 100.0 - 50.0


def near(cents: float, reference: float) -> float:
    """The equivalent of `cents` (modulo a semitone) closest to `reference`."""
    return cents + 100.0 * round((reference - cents) / 100.0)


def frequency_of(cents: float) -> float:
    return 440.0 * 2.0 ** (cents / 1200.0)


def histogram_peaks(frequencies: numpy.ndarray, magnitudes: numpy.ndarray) -> tuple[numpy.ndarray, numpy.ndarray]:
    """The peaks the tuning histogram counts: within `relative_db` of the
    frame's loudest and below `max_frequency`."""
    if len(magnitudes) == 0:
        return frequencies, magnitudes
    keep = (magnitudes >= magnitudes.max() * 10.0 ** (-HISTOGRAM["relative_db"] / 20.0)) \
        & (frequencies < HISTOGRAM["max_frequency"])
    return frequencies[keep], magnitudes[keep]


def deviation_histogram(frequencies: numpy.ndarray, magnitudes: numpy.ndarray) -> numpy.ndarray:
    """Peak magnitude by distance above the tempered grid on 440 Hz, in
    one-cent bins from 0 to 99."""
    keep = frequencies > 0
    if not keep.any():
        return numpy.zeros(BINS)
    deviation = numpy.mod(1200.0 * numpy.log2(frequencies[keep].astype(numpy.float64) / 440.0), 100.0)
    return numpy.bincount(numpy.minimum(deviation.astype(numpy.int64), BINS - 1),
                          weights=magnitudes[keep].astype(numpy.float64), minlength=BINS)


def _within(width: float) -> numpy.ndarray:
    """For each centre bin, which bins lie within `width` cents of it."""
    centres = numpy.arange(BINS) + 0.5
    distance = numpy.abs(centres[:, None] - centres[None, :])
    return numpy.minimum(distance, BINS - distance) <= width


def concentration(histogram: numpy.ndarray, cents: float, width: float) -> float:
    """The share of the histogram's weight within `width` cents of `cents`,
    going round the circle; 0 for an empty histogram."""
    total = float(histogram.sum())
    if total <= 0:
        return 0.0
    centres = numpy.arange(BINS) + 0.5
    distance = numpy.abs(centres - (cents % 100.0))
    distance = numpy.minimum(distance, 100.0 - distance)
    return float(histogram[distance <= width].sum() / total)


def best_concentration(histogram: numpy.ndarray, width: float) -> float:
    """The histogram's concentration about whichever tuning concentrates it most."""
    total = float(histogram.sum())
    if total <= 0:
        return 0.0
    return float((_within(width) @ histogram).max() / total)


def histogram_tuning(histogram: numpy.ndarray, width: float) -> float | None:
    """The tuning, in cents folded into [-50, 50), that concentrates the
    histogram most: the centre of the best `width`-cent stretch, refined to
    the circular mean of the weight inside it. None for an empty histogram."""
    if float(histogram.sum()) <= 0:
        return None
    inside = _within(width)[int(numpy.argmax(_within(width) @ histogram))]
    angles = 2.0 * numpy.pi * (numpy.arange(BINS) + 0.5) / BINS
    mean = complex(numpy.sum(histogram[inside] * numpy.exp(1j * angles[inside])))
    return fold(float(numpy.angle(mean)) / (2.0 * numpy.pi) * BINS)


def weighted_median(values: Sequence[float], weights: Sequence[float]) -> float:
    """The smallest value at which the cumulative weight reaches half the total."""
    order = numpy.argsort(numpy.asarray(values, dtype=numpy.float64), kind="stable")
    sorted_values = numpy.asarray(values, dtype=numpy.float64)[order]
    cumulative = numpy.cumsum(numpy.asarray(weights, dtype=numpy.float64)[order])
    return float(sorted_values[int(numpy.searchsorted(cumulative, cumulative[-1] / 2.0))])


def recording_tuning(windows_cents: Sequence[float], confidences: Sequence[float]) -> float | None:
    """The confidence-weighted median of the windows' tunings, taken round the
    circle: each written near their weighted circular mean, so tunings either
    side of half a semitone are not split; folded into [-50, 50). None for no
    windows."""
    if not len(windows_cents):
        return None
    angles = 2.0 * numpy.pi * numpy.asarray(windows_cents, dtype=numpy.float64) / 100.0
    centre = float(numpy.angle(numpy.sum(numpy.asarray(confidences) * numpy.exp(1j * angles)))) / (2 * numpy.pi) * 100
    return fold(weighted_median([near(value, centre) for value in windows_cents], confidences))


def windows(rows: int, length: int, hop: int) -> list[tuple[int, int]]:
    """Windows of `length` rows every `hop` rows covering `rows` rows: the
    last may be cut short by the end, and none lies wholly inside the one
    before it."""
    if rows <= 0:
        return []
    count = 1 if rows <= length else -(-(rows - length) // hop) + 1
    return [(index * hop, min(index * hop + length, rows)) for index in range(count)]


def seconds(row: int) -> float:
    return row * SECONDS_PER_ROW


def extent(first: int, stop: int, samples: int) -> tuple[float, float]:
    """The seconds frames first..stop-1 stand for: each frame the hop centred
    on its own centre, clipped to the decoded audio's `samples`."""
    def clip(sample: float) -> float:
        return min(max(sample, 0.0), float(samples)) / RATE
    return clip((first - 0.5) * HOP), clip((stop - 0.5) * HOP)


def spread(values: Sequence[float]) -> float:
    """From the tenth to the ninetieth percentile; 0 for fewer than two."""
    if len(values) < 2:
        return 0.0
    low, high = numpy.percentile(numpy.asarray(values, dtype=numpy.float64), [10, 90])
    return float(high - low)


def dissonance_peaks(frequencies: numpy.ndarray, magnitudes: numpy.ndarray) -> tuple[numpy.ndarray, numpy.ndarray]:
    """The peaks `Dissonance` is given: those within the relative threshold
    of the frame's loudest, at most `maxPeaks` of the loudest, in frequency
    order. Without the threshold a lone sine's window side lobes, 62 dB
    down, read as a dissonant cluster (Essentia weights peaks by loudness,
    not by magnitude), and every frame measures about 0.5."""
    if len(magnitudes) == 0:
        return frequencies, magnitudes
    keep = magnitudes >= magnitudes.max() * float(DISSONANCE["relative_threshold"])
    frequencies, magnitudes = frequencies[keep], magnitudes[keep]
    if len(magnitudes) > DISSONANCE["maxPeaks"]:
        loudest = numpy.sort(numpy.argsort(-magnitudes, kind="stable")[:DISSONANCE["maxPeaks"]])
        frequencies, magnitudes = frequencies[loudest], magnitudes[loudest]
    return frequencies, magnitudes


def profile_peaks(frequencies: numpy.ndarray, magnitudes: numpy.ndarray) -> tuple[numpy.ndarray, numpy.ndarray]:
    """The peaks the profiles count: within `relative_db` of the frame's
    loudest and inside the treble or the bass band."""
    if len(magnitudes) == 0:
        return frequencies, magnitudes
    keep = magnitudes >= magnitudes.max() * 10.0 ** (-CHROMA["relative_db"] / 20.0)
    keep &= (frequencies >= min(CHROMA["bass_hz"][0], CHROMA["treble_hz"][0])) \
        & (frequencies <= max(CHROMA["bass_hz"][1], CHROMA["treble_hz"][1]))
    return frequencies[keep].astype(numpy.float32), magnitudes[keep].astype(numpy.float32)


def profile(frequencies: numpy.ndarray, magnitudes: numpy.ndarray, tuning: float,
            band: Sequence[int]) -> numpy.ndarray:
    """A frame's peaks in `band` (Hz, inclusive) folded onto twelve pitch
    classes (bin 0 is A) on the grid of the frame's `tuning` in cents: a
    peak's energy goes to its nearest pitch class, weighted by the squared
    cosine of its distance from it times pi (1 on the grid, 0 half a semitone
    away)."""
    out = numpy.zeros(12, dtype=numpy.float64)
    keep = (frequencies >= band[0]) & (frequencies <= band[1])
    if not keep.any():
        return out
    semitones = 12.0 * numpy.log2(frequencies[keep].astype(numpy.float64) / 440.0) - tuning / 100.0
    nearest = numpy.round(semitones)
    energy = magnitudes[keep].astype(numpy.float64) ** 2
    numpy.add.at(out, nearest.astype(numpy.int64) % 12, numpy.cos(numpy.pi * (semitones - nearest)) ** 2 * energy)
    return out


def frame_tuning(rows: int, centres: Sequence[float], cents: Sequence[float], recording: float) -> numpy.ndarray:
    """Each frame's tuning: the confident windows' tunings interpolated
    between their centre frames and held beyond the first and last; the
    recording's everywhere when no window is confident."""
    if not len(centres):
        return numpy.full(rows, recording, dtype=numpy.float64)
    return numpy.interp(numpy.arange(rows, dtype=numpy.float64), numpy.asarray(centres, dtype=numpy.float64),
                        numpy.asarray(cents, dtype=numpy.float64))


# -- the key ------------------------------------------------------------------

def _key_candidates() -> tuple[list[tuple[int, str, tuple[int, ...]]], numpy.ndarray, numpy.ndarray]:
    """Every (tonic bin, mode, collection bins from the tonic up), its
    template, and its penalty."""
    weights = {name: float(value) for name, value in KEY["weights"].items()}
    candidates, templates, penalties = [], [], []
    for name, intervals in COLLECTIONS.items():
        modes = list(enumerate(MODES)) if name == "diatonic" else [(0, name)]
        for degree, mode in modes:
            steps = tuple((intervals[(degree + k) % 7] - intervals[degree]) % 12 for k in range(7))
            for tonic in range(12):
                template = numpy.full(12, weights["outside"])
                for step in steps:
                    template[(tonic + step) % 12] = weights["scale"]
                template[tonic] = weights["tonic"]
                third = next(step for step in steps if step in (3, 4))
                template[(tonic + third) % 12] = weights["third"]
                fifth = 7 if 7 in steps else 6  # locrian's fifth is diminished
                template[(tonic + fifth) % 12] = weights["fifth"]
                candidates.append((tonic, mode, tuple((tonic + step) % 12 for step in steps)))
                templates.append(template)
                penalties.append(0.0 if name == "diatonic" else float(KEY["collection_penalty"]))
    return candidates, numpy.array(templates), numpy.array(penalties)


KEY_CANDIDATES, KEY_TEMPLATES, KEY_PENALTIES = _key_candidates()


def key_profile(chroma: numpy.ndarray, bass: numpy.ndarray) -> numpy.ndarray:
    """The frames' profile for the key: each frame's square-rooted treble and
    bass bins divided by their own sums (a silent frame adds nothing), summed,
    and the bass added at `bass_weight` of the treble's total."""
    def summed(values: numpy.ndarray) -> numpy.ndarray:
        roots = numpy.sqrt(numpy.maximum(values.astype(numpy.float64), 0.0))
        totals = roots.sum(axis=1, keepdims=True)
        return (roots / numpy.where(totals > 0, totals, 1.0)).sum(axis=0)
    treble, low = summed(chroma), summed(bass)
    profile = treble / treble.sum() if treble.sum() > 0 else numpy.zeros(12)
    if low.sum() > 0:
        profile = profile + float(KEY["bass_weight"]) * low / low.sum()
    return profile


def key_of(profile: numpy.ndarray) -> dict[str, Any] | None:
    """The best tonic and mode for a profile, or None for a profile with no
    weight or no contrast."""
    if profile.sum() <= 0 or numpy.ptp(profile) <= 0:
        return None
    centred = profile - profile.mean()
    templates = KEY_TEMPLATES - KEY_TEMPLATES.mean(axis=1, keepdims=True)
    scores = templates @ centred / (numpy.linalg.norm(templates, axis=1) * numpy.linalg.norm(centred)) - KEY_PENALTIES
    order = numpy.argsort(-scores, kind="stable")
    tonic, mode, collection = KEY_CANDIDATES[int(order[0])]
    return {"key": NOTES[tonic], "mode": mode, "scale": "major" if mode in MAJOR_MODES else "minor",
            "scale_notes": " ".join(NOTES[note] for note in collection), "key_strength": float(scores[order[0]]),
            "key_margin": float(scores[order[0]] - scores[order[1]])}


NO_KEY = {"key": "", "mode": "", "scale": "", "scale_notes": "", "key_strength": 0.0, "key_margin": 0.0}


def keys(chroma: numpy.ndarray, bass: numpy.ndarray, pitched: numpy.ndarray, samples: int) -> dict[str, Any]:
    """The recording's key and each window's, from the pitched frames."""
    found = key_of(key_profile(chroma[pitched], bass[pitched])) if pitched.any() else None
    outputs: dict[str, Any] = dict(found or NO_KEY)
    spans = []
    for start, end in windows(len(chroma), KEY["window_frames"], KEY["hop_frames"]):
        mask = pitched[start:end]
        if mask.sum() < float(KEY["min_pitched"]) * (end - start):
            continue
        window = key_of(key_profile(chroma[start:end][mask], bass[start:end][mask]))
        if window is None:
            continue
        begin, finish = extent(start, end, samples)
        spans.append({"start": begin, "end": finish, "label": f"{window['key']} {window['mode']}",
                      "value": window["key_strength"]})
    outputs["keys"] = spans
    outputs["key_regions"] = regions(spans)
    return outputs


def regions(spans: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Overlapping windows laid end to end: where two overlap, each keeps the
    side of the midpoint between their centres nearer its own; neighbours
    with one label merge, valued by their duration-weighted mean."""
    cores = []
    for index, span in enumerate(spans):
        start, end = span["start"], span["end"]
        centre = (start + end) / 2
        if index and spans[index - 1]["end"] > start:
            start = (centre + (spans[index - 1]["start"] + spans[index - 1]["end"]) / 2) / 2
        if index + 1 < len(spans) and spans[index + 1]["start"] < end:
            end = (centre + (spans[index + 1]["start"] + spans[index + 1]["end"]) / 2) / 2
        cores.append({"start": start, "end": end, "label": span["label"], "value": span["value"]})
    merged: list[dict[str, Any]] = []
    for core in cores:
        if merged and merged[-1]["label"] == core["label"] and merged[-1]["end"] == core["start"]:
            last = merged[-1]
            length, extra = last["end"] - last["start"], core["end"] - core["start"]
            last["value"] = (last["value"] * length + core["value"] * extra) / max(length + extra, 1e-12)
            last["end"] = core["end"]
        else:
            merged.append(dict(core))
    return merged


# -- measuring ------------------------------------------------------------------

class Tuning:
    """The tuning of every window, accumulated as the frames arrive: a
    window's pitched frames' histogram decides its tuning, and all its
    sounding frames' histogram its confidence, so noise the pitch gate let
    through still lowers it."""

    def __init__(self, rows: int):
        self.windows = windows(rows, TUNING["window_frames"], TUNING["hop_frames"])
        self.open: dict[int, dict[str, Any]] = {}
        self.sounding_histogram = numpy.zeros(BINS)
        self.finished: list[dict[str, Any]] = []
        self.next = 0

    def add(self, row: int, pitched: bool, histogram: numpy.ndarray | None) -> None:
        while self.next < len(self.windows) and self.windows[self.next][0] == row:
            self.open[self.next] = {"pitched_histogram": numpy.zeros(BINS), "histogram": numpy.zeros(BINS),
                                    "pitched": 0}
            self.next += 1
        if histogram is not None:
            self.sounding_histogram += histogram
            for window in self.open.values():
                window["histogram"] += histogram
                if pitched:
                    window["pitched_histogram"] += histogram
                    window["pitched"] += 1
        for index in [index for index in self.open if self.windows[index][1] == row + 1]:
            window = self.open.pop(index)
            start, end = self.windows[index]
            cents = histogram_tuning(window["pitched_histogram"], PITCHED["concentration_cents"]) \
                if window["pitched"] else None
            self.finished.append({"start": start, "end": end, "pitched": window["pitched"],
                                  "defined": cents is not None, "cents": cents or 0.0,
                                  "confidence": 0.0 if cents is None else
                                  concentration(window["histogram"], cents, PITCHED["concentration_cents"])})

    def result(self, samples: int) -> tuple[dict[str, Any], numpy.ndarray, list[tuple[float, float]]]:
        """The tuning outputs, the `tuning_windows` rows, and the confident
        windows as (centre frame, cents near the recording's tuning)."""
        if self.open or len(self.finished) != len(self.windows):
            raise TonalError(f"{len(self.windows) - len(self.finished)} tuning windows were never closed")
        listed = [window for window in self.finished if window["defined"]
                  and window["pitched"] / (window["end"] - window["start"]) >= float(TUNING["min_pitched"])]
        confident = [window for window in listed if window["confidence"] >= float(TUNING["min_confidence"])]
        found = recording_tuning([window["cents"] for window in confident],
                                 [window["confidence"] for window in confident])
        cents = 0.0 if found is None else found
        confidence = 0.0 if found is None else concentration(self.sounding_histogram, cents,
                                                             PITCHED["concentration_cents"])
        rows = numpy.zeros((len(self.finished), len(TUNING_COLUMNS)), dtype="<f4")
        drift = []
        for index, window in enumerate(self.finished):
            share = window["pitched"] / (window["end"] - window["start"])
            window_cents = near(window["cents"], cents) if window["defined"] else 0.0
            rows[index] = (window_cents, window["confidence"], share)
            if window in listed:
                begin, end = extent(window["start"], window["end"], samples)
                drift.append({"start": begin, "end": end, "value": window_cents})
        limit = TUNING["max_detune_cents"]
        detuned_spans = [dict(span) for span in drift if abs(span["value"]) > limit]
        anchors = [((window["start"] + window["end"] - 1) / 2, near(window["cents"], cents)) for window in confident]
        return ({"tuning_frequency": frequency_of(cents), "tuning_cents": cents, "tuning_confidence": confidence,
                 "tuning_drift": drift, "drift_spread_cents": spread([span["value"] for span in drift]),
                 "detuned": confidence > 0 and abs(cents) > limit, "detuned_spans": detuned_spans}, rows, anchors)


def need(request: dict[str, Any], feature: str, columns: int) -> numpy.ndarray:
    """A needed `frames` series, refused unless it is on the grid this
    extractor decodes to."""
    described = request["needs"].get(feature)
    if described is None:
        raise TonalError(f"the request does not carry {feature}")
    if described.get("dtype") != "f32" or described.get("shape", [None, None])[1] != columns:
        raise TonalError(f"{feature} is not an f32 series of {columns} columns")
    grid = described.get("grid") or {}
    if (grid.get("sample_rate"), grid.get("hop"), grid.get("frame"), grid.get("first_centre")) != (RATE, HOP,
                                                                                                    FRAME, 0):
        raise TonalError(f"{feature} is not on the frames grid this extractor decodes to: {grid}")
    values = numpy.fromfile(described["path"], dtype="<f4")
    rows = described["shape"][0]
    if values.size != rows * columns:
        raise TonalError(f"{described['path']} holds {values.size} values, not {rows} x {columns}")
    return values.reshape(rows, columns)


class Blocks:
    """Frames held a block at a time, until it is known whether the block is
    pitched, then handed to the tuning in order."""

    def __init__(self, tuning: Tuning, sounding: numpy.ndarray, pitched: numpy.ndarray):
        self.tuning, self.sounding, self.pitched = tuning, sounding, pitched
        self.held: list[tuple[int, numpy.ndarray | None]] = []

    def add(self, row: int, frequencies: numpy.ndarray, magnitudes: numpy.ndarray) -> None:
        histogram = deviation_histogram(*histogram_peaks(frequencies, magnitudes)) if self.sounding[row] else None
        self.held.append((row, histogram))
        if len(self.held) == PITCHED["block_frames"]:
            self.flush()

    def flush(self) -> None:
        if not self.held:
            return
        histograms = [histogram for _, histogram in self.held if histogram is not None]
        enough = len(histograms) >= float(PITCHED["min_sounding"]) * len(self.held)
        votes = [histogram / histogram.sum() for histogram in histograms if histogram.sum() > 0]
        is_pitched = enough and bool(votes) and best_concentration(numpy.sum(votes, axis=0),
                                                                   PITCHED["concentration_cents"]) \
            >= float(PITCHED["min_concentration"])
        for row, histogram in self.held:
            self.pitched[row] = is_pitched and histogram is not None
            self.tuning.add(row, bool(self.pitched[row]), histogram)
        self.held = []


def measure(request: dict[str, Any], decode: Callable[[], Iterable[numpy.ndarray]] | None = None) -> dict[str, Any]:
    """Every output: scalars and spans by name, and the series as arrays.
    Two passes over the decoded audio: the first finds the pitched frames,
    the tuning, and the dissonance; the second folds each frame's peaks on
    the tuning the first found, so nothing grows with the recording but the
    series themselves. `decode` gives the decoded audio afresh each time it
    is called (the input's, by default)."""
    es = cover.essentia_module()
    rms_db = need(request, "frames/rms_db", 1)[:, 0]
    rows = len(rms_db)
    sounding = rms_db >= GATE["rms_db_min"]

    def decoded() -> frames.Counted:
        return frames.Counted(frames.decoded_blocks(request["input"]["path"]) if decode is None else decode())

    windowing = es.Windowing(**cover._options(cover.ANALYSIS["windowing"]))
    spectrum = es.Spectrum()
    peaks = es.SpectralPeaks(**cover._options(PEAKS))
    dissonance = es.Dissonance()
    tuning = Tuning(rows)
    pitched = numpy.zeros(rows, dtype=bool)
    held = Blocks(tuning, sounding, pitched)
    roughness = numpy.zeros(rows, dtype="<f4")
    audio = decoded()
    row = 0
    for frame in frames.framed(audio):
        if row >= rows:
            raise TonalError(f"the decode made more frames than the {rows} frames/rms_db has")
        frequencies, magnitudes = peaks(spectrum(windowing(frame)))
        held.add(row, frequencies, magnitudes)
        if sounding[row]:
            roughness[row] = dissonance(*dissonance_peaks(frequencies, magnitudes))
        row += 1
    held.flush()
    if row != rows:
        raise TonalError(f"the decode made {row} frames, but frames/rms_db has {rows}")
    if not numpy.isfinite(roughness).all():
        raise TonalError("Dissonance gave a value that is not finite")
    outputs, tuning_rows, anchors = tuning.result(audio.samples)
    local = frame_tuning(rows, [centre for centre, _ in anchors], [cents for _, cents in anchors],
                         outputs["tuning_cents"])

    chroma = numpy.zeros((rows, 12), dtype="<f4")
    bass = numpy.zeros((rows, 12), dtype="<f4")
    again = decoded()
    row = 0
    for frame in frames.framed(again):
        if row < rows and sounding[row]:
            frequencies, magnitudes = profile_peaks(*peaks(spectrum(windowing(frame))))
            chroma[row] = profile(frequencies, magnitudes, float(local[row]), CHROMA["treble_hz"])
            bass[row] = profile(frequencies, magnitudes, float(local[row]), CHROMA["bass_hz"])
        row += 1
    if row != rows or again.samples != audio.samples:
        raise TonalError(f"the second decode made {row} frames of {again.samples} samples, "
                         f"not {rows} of {audio.samples}")
    if not (numpy.isfinite(chroma).all() and numpy.isfinite(bass).all()):
        raise TonalError("a pitch-class profile holds a value that is not finite")
    outputs.update(keys(chroma, bass, pitched, audio.samples))
    outputs["pitched"] = pitched.astype("u1").reshape(-1, 1)
    outputs["tuning_windows"] = tuning_rows
    outputs["dissonance"] = roughness.reshape(-1, 1)
    outputs["chroma"] = chroma
    outputs["bass_chroma"] = bass
    return outputs


SCALARS = ("tuning_frequency", "tuning_cents", "tuning_confidence", "drift_spread_cents", "detuned", "key", "scale",
           "mode", "scale_notes", "key_strength", "key_margin")
SPANS = ("tuning_drift", "detuned_spans", "keys", "key_regions")
SERIES = {"pitched": "u8", "tuning_windows": "f32", "dissonance": "f32", "chroma": "f32", "bass_chroma": "f32"}


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: tonal.py <request.json>", file=sys.stderr)
        return 2
    try:
        request = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
        if request.get("parameters") != parameters():
            raise TonalError("tonal 3 has fixed settings; the request names others")
        output = Path(request["output_dir"])
        measured = measure(request)
        result_outputs: dict[str, Any] = {name: {"value": measured[name]} for name in SCALARS}
        for name in SPANS:
            (output / f"{name}.json").write_text(json.dumps(measured[name]), encoding="utf-8")
            result_outputs[name] = {}
        for name, dtype in SERIES.items():
            measured[name].tofile(output / f"{name}.{dtype}")
            result_outputs[name] = {"shape": list(measured[name].shape)}
        software = [{"name": "ffmpeg", "version": frames.ffmpeg_version()},
                    {"name": "essentia", "version": importlib.metadata.version("essentia")},
                    {"name": "numpy", "version": numpy.__version__},
                    {"name": "python", "version": platform.python_version()}]
        result = {"contract": "archive-extractor-result/1", "software": software,
                  "grids": {"frames": {"sample_rate": RATE, "hop": HOP, "frame": FRAME, "first_centre": 0},
                            "tuning": tuning_grid()},
                  "outputs": result_outputs}
        (output / "result.json").write_text(json.dumps(result), encoding="utf-8")
    except (TonalError, frames.FramesError, cover.CoverError, OSError, KeyError, ValueError, TypeError,
            RuntimeError) as exc:
        print(f"tonal: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
