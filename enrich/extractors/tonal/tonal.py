#!/usr/bin/env python3
"""The tonal extractor: tuning and its drift, pitch-class profiles referenced
to that tuning, the key as a note collection with its tonic and mode, and
dissonance (ADR 0074).

It reads the `frames` extractor's levels from the bank, decodes the
recording again for each frame's spectral peaks, and measures:

- `pitched`: which frames carry pitched sound (below), on the `frames` grid,
  so that every other output, and any reader, can leave out silence, talk,
  noise, and mains hum the same way;
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
  cents, folded into [-50, 50), and `tuning_window_count`, how many windows
  it was taken from: `tuning_confidence` is measured over every sounding
  frame, so it does not show that a tuning rests on two windows of six.
  Since version 5 a tuning that does rest on too few (below) is not
  reported: 440 Hz with confidence 0, as when no window is confident;
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
  the level gate is 0;
- `collection`, `collection_margin`, `key`, `mode`, `scale`, `scale_notes`,
  `key_strength`, `tonic_margin`, `tonic_confidence`, `tonic_named`: the
  note collection of the pitched frames, its tonic and mode, and how sure
  the tonic is (below); `keys`: the same in windows of about thirty seconds
  that are at least half pitched; and `key_regions`, a timeline of the note
  collections. A span is labelled with the collection ("Eb major scale")
  and valued by its `collection_margin`; since version 6 no tonic is named
  (`tonic_named` is false everywhere), and readers read `collection` and
  `scale_notes`' notes, not the tonic;
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
for a three-hour rehearsal (some 210 peaks a frame). On three tracks of the
album below, timed one after the other on a shared machine, `tonal` took
1.5 to 1.65 times `frames`' processor time, each with its process start and
its decodes (2.2 to 2.7 s for `frames`, 3.4 to 4.0 s for `tonal`): the
exception to "decode once" that ADR 0074 records, with what would remove
it. `frames/hpcp` is not read: the profiles here are
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
with confidence 0. So it is with too few: fewer than `min_windows` (4)
confident windows, unless the recording is too short to have that many and
every window is confident, or fewer than `min_window_share` (half) of the
windows that are at least half pitched. A tuning from two windows of a
minute, or from six of fourteen pitched windows whose other eight agree
only weakly, speaks for a corner of the recording, not for the whole: on the
album below version 4 reported one track at +18.5 cents from two windows
of six, and another at +15.5 from six of fourteen. The windows themselves
keep their tunings (written near the confident windows' median, reported
or not), and the profiles still follow them. Version 2 used Essentia's `TuningFrequency`, whose
estimate over a long stretch of a dense mix, and over white or pink noise,
falls to 434.2 Hz (-23 cents) whatever the music: on an album of eighteen
masters eight read exactly that, where these windows read from -6 to +23
cents with confidence 0.4 to 0.8.

Pitched frames. The frames are taken in blocks of about five seconds; a
block is pitched when at least half of it sounds (passes the level gate) and
its sounding frames, each frame's histogram scaled to sum to one so that
every frame has one vote, concentrate about some tuning at least
`min_concentration` (0.4); a frame is pitched when it sounds and its block
is pitched. On synthetic sound music concentrates 0.66 to 1.0 even under
noise or drums, and noise, talk, drums alone, and a gliding tone 0.32 to
0.36. On the masters, restricting the histogram to the loud peaks below 2
kHz raised the pitched share of three dense tracks from 0.24-0.41 to
0.64-0.82; noise and talk stay flat either way. Without the equal votes a
few loud, in-tune frames of music carried a block of talk past the threshold
(six music frames among 54 read 0.41); with them it takes about eight, so up
to about four seconds of talk beside music can still be marked pitched, as
in version 2: the decision is a block's.

Mains hum is left out first. Its harmonics sit on one offset from the grid
(60 Hz's 49 cents, 50 Hz's 35), and in the gaps between syllables of quiet
talk they are a frame's loudest peaks, so a block of talk over hum agreed on
a tuning: in a synthetic rehearsal (talk at 0.03, hum at 0.003 and three
harmonics above it, a room floor) version 3 marked all of five minutes of
talk pitched over 60 Hz and 80 % over 50 Hz, read the windows over it at the
hum's offset, and gave the recording the key of B-flat or G major; with no
hum the talk was not pitched at all. So a block's mains (`mains_lines`) are
those of 50 and 60 Hz with at least two of their first three harmonics among
the loud peaks, within 1.5 Hz, of at least half its sounding frames, and
every peak within 1.5 Hz of any of their harmonics below 2 kHz is left out
of the block's histograms and of its frames' profiles. Only the first three
harmonics are asked for, because music tuned 35 cents sharp puts every G and
its partials on 50 Hz's harmonics (G4 is then 400 Hz), and music about
49 cents off puts every B on 60 Hz's. What still passes for hum is a low G
or B held through half a block at that tuning, or a held low note near 50 or
60 Hz: on the eighteen masters above, three blocks, each the short last
block of a track under a held bass note, and no other; the tuning there
moved by under 0.01 cent.

The profiles. Each frame's tuning is the confident windows' tunings,
interpolated between their centres (and held beyond the first and last),
or the recording's when no window is confident; a peak is folded to the
pitch class nearest to it on that tuning's grid. A recording tuned near half
a semitone from 440 Hz therefore keeps its pitch classes whole, where
`frames/hpcp` splits them; at exactly half a semitone the name depends on
which side the fold puts the tuning.

The key: the note collection. The profile of the pitched frames is the
sum of their `chroma` and of half their `bass_chroma`, each frame's
square-rooted bins divided by their sum, so every pitched frame counts once
(`key_shares` gives the two halves, `key_profile` their sum). It is
correlated with a template for every tonic of every mode of the diatonic
collection (ionian, dorian, phrygian, lydian, mixolydian, aeolian, locrian)
and of the harmonic and melodic minor collections: the tonic weighs 5, the
fifth 4.5, the third 4.25, the collection's other notes 3.75, and the notes
outside it 2 (the shape of Temperley's major profile, applied to any mode,
so the seven modes of one collection differ only in which note is the
tonic). Harmonic and melodic minor give up `collection_penalty` (0.02), so
that a diatonic reading wins a tie. The collection is the best reading's,
and `collection_margin` how far it leads the best reading on other notes.
Version 2 read "Eb major" for music in F dorian, which has Eb major's notes
and F's tonic.

The key: the tonic. The templates are good at the notes and poor at the
tonic, since the modes of one collection share their notes and differ only
in which note the music leans on: a progression of Cmaj7, Am7, Dm7, G7,
Fmaj7, and E7 read A aeolian, F lydian, or C ionian as its level and tuning
changed, each by a margin under 0.015. In version 4 the tonic was the best
template's and was named when it led the best reading with another tonic by
0.03; on the album below that margin did not predict being right (where the
tonic was named it agreed with 10 of 22 published key claims, where it was
not with 5 of 16), and a song in B-flat whose transcriptions both say B-flat
read G aeolian. So since version 5 the tonic is chosen among the
collection's own notes by `tonic_evidence`: each note's share of the bass
(which mostly plays roots), a quarter of its share of the treble, and
`favour` (0.08) more for the collection's major or minor tonic (its ionian
and aeolian tonics, or a harmonic or melodic minor collection's own), since
most music in a collection is in one of those two; another mode wins only
where the bass sits on its tonic clearly more. The song in B-flat reads B-flat
ionian; an F dorian vamp, its bass on F three bars in four, still reads F
dorian. `key` is the tonic, named as Essentia names notes (A, Bb, B, C, C#,
D, Eb, E, F, F#, G, Ab); `mode` the mode; `scale_notes` the collection from
the tonic up; `scale` is "major" for ionian, lydian, and mixolydian and
"minor" for the rest, so a reader of version 2 still finds the key it
expects; `key_strength` is the named mode's correlation; `tonic_margin` how
far the tonic's evidence leads the collection's next note. Version 4's
`key_margin`, how far the best template led the runner-up, is gone: the key
is no longer the best template.

`tonic_confidence` is the chance that the tonic is right, given the
collection: the softmax of the seven notes' evidence at `temperature`
(0.07), read at the tonic, so 1/7 where nothing tells the notes apart and
near 1 where one note holds the bass. The temperature is calibrated on the
album: there the mean confidence (0.43) matched the share of the published
key claims (Spotify's, ChordU's, Ultimate Guitar's, and the keys of
note-for-note transcriptions) whose tonic the key agreed with (0.45). It
did not tell the tracks it got right from those it got wrong: the claims
are noisy (they agree with each other on the tonic in 11 of 31 pairs), and
the most confident track disagrees with all three of its claims. So it is
calibrated in the mean on one album, not proven to rank. Version 5 named
the tonic (`tonic_named`) when its confidence was at least one half.
Version 6 names it nowhere: on the album the confidence did not rank
(of 5 claims at 0.7 or more, 1 agreed; its Brier score, 0.316, was worse
than a constant 0.45's, 0.247), named tonics agreed with 5 of 12 claims
and unnamed with 12 of 26, and neither the bass stem's share nor the chord
roots' raised the 17 of 38 agreements, the references themselves settling
at most 25 (most misses are the relative major or minor). So `tonic_named`
is false everywhere; `key`, `mode`, and `scale` remain the best guess and
`collection` and `scale_notes`' notes are what to read, until references
settle relative major and minor. A C major cycle of C, F, G, and A minor
with each root as long in the bass reads C ionian with a confidence of
about 0.4, its A as strong in the bass; C, F, C, G above one half. On the album
the tonic agreed with 17 of 38 claims where version 4's agreed with 15;
version 5 named it on 6 tracks of 18, 7 of its named claims wrong.

`keys` is a window-by-window description, not a claim of modulation.
`key_regions` is the timeline: the windows' collections are decoded on a
path that pays `region_switch` (0.1) for each change (`collection_path`),
so a change must lead by more than that over the windows it holds, the
windows are laid end to end (each keeps the stretch nearer its centre than
its neighbours'), runs of one collection merge, and each region's tonic is
found from its own pitched frames among that collection's notes (not
named, as above). On the album the regions went from 143 in version 3 (up to 16 a
track, through modes of one collection) to 50 (at most 6).

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
HUM = {"mains_hz": [50, 60], "lines": [1, 2, 3], "tolerance_hz": "1.5", "min_presence": "0.5", "min_lines": 2,
       "max_frequency": 2000}
PITCHED = {"block_frames": 54, "concentration_cents": 15, "min_sounding": "0.5", "min_concentration": "0.4",
           "votes": "one-per-sounding-frame"}
TUNING = {"estimator": "histogram-peak", "window_frames": 216, "hop_frames": 108, "min_pitched": "0.5",
          "min_confidence": "0.4", "recording": "confidence-weighted-median", "min_windows": 4,
          "min_window_share": "0.5", "max_detune_cents": 40}
CHROMA = {"treble_hz": [100, 5000], "bass_hz": [40, 250], "relative_db": 60, "weighting": "cosine-squared",
          "width_semitones": 1, "reference": "local-tuning"}
KEY = {"weights": {"tonic": "5", "fifth": "4.5", "third": "4.25", "scale": "3.75", "outside": "2"},
       "bass_weight": "0.5", "collections": ["diatonic", "harmonic minor", "melodic minor"],
       "collection_penalty": "0.02",
       "tonic": {"among": "the-collections-notes", "bass": "1", "treble": "0.25",
                 "favoured_modes": ["ionian", "aeolian", "harmonic minor", "melodic minor"], "favour": "0.08",
                 "temperature": "0.07", "named": "never"},
       "window_frames": 324, "hop_frames": 162, "min_pitched": "0.5", "regions": "viterbi-over-collections",
       "region_switch": "0.1"}
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
            "spectral_peaks": PEAKS, "gate": GATE, "histogram": HISTOGRAM, "hum": HUM, "pitched": PITCHED,
            "tuning": TUNING, "chroma": CHROMA, "key": KEY, "dissonance": DISSONANCE}


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


def mains_lines(peaks: Sequence[tuple[numpy.ndarray, numpy.ndarray]]) -> list[int]:
    """The mains frequencies (of `mains_hz`) whose hum runs through these
    frames: those with at least `min_lines` of their first harmonics
    (`lines`: the mains frequency itself, twice, and three times it) each
    found, within `tolerance_hz`, among the loud peaks below 2 kHz
    (`histogram_peaks`) of at least `min_presence` of the frames. None for no
    frames. Only the lowest harmonics are asked for because music tuned
    about 35 cents sharp puts every G and its partials on 50 Hz's harmonics
    (G2 at 100 Hz, G4 at 400), and about 49 cents from 440 Hz every B on
    60 Hz's: higher harmonics found in half the frames would take a song in
    G at that tuning for hum. Music holds 50 or 60 Hz and its octave
    through half a block only on a pedal low G or B at that tuning."""
    if not peaks:
        return []
    found = []
    tolerance = float(HUM["tolerance_hz"])
    loud = [histogram_peaks(frequencies, magnitudes)[0] for frequencies, magnitudes in peaks]
    for mains in HUM["mains_hz"]:
        lines = 0
        for harmonic in (mains * k for k in HUM["lines"]):
            present = sum(1 for frequencies in loud
                          if len(frequencies) and float(numpy.abs(frequencies - harmonic).min()) <= tolerance)
            lines += present >= float(HUM["min_presence"]) * len(peaks)
        if lines >= HUM["min_lines"]:
            found.append(int(mains))
    return found


def without_mains(frequencies: numpy.ndarray, magnitudes: numpy.ndarray,
                  mains: Sequence[int]) -> tuple[numpy.ndarray, numpy.ndarray]:
    """The peaks less those within `tolerance_hz` of a harmonic of any of
    `mains` below `max_frequency`."""
    if not len(mains) or not len(frequencies):
        return frequencies, magnitudes
    keep = numpy.ones(len(frequencies), dtype=bool)
    tolerance = float(HUM["tolerance_hz"])
    for line in mains:
        nearest = numpy.maximum(numpy.round(frequencies / line), 1.0) * line
        keep &= ~((numpy.abs(frequencies - nearest) <= tolerance) & (nearest < HUM["max_frequency"]))
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

def collection_name(kind: str, root: int) -> str:
    """A collection's name: a diatonic one by its ionian tonic ("Eb major
    scale"), the others by their own ("C harmonic minor scale")."""
    return f"{NOTES[root]} {'major' if kind == 'diatonic' else kind} scale"


def _key_candidates() -> tuple[list[tuple[int, str, tuple[int, ...], str]], numpy.ndarray, numpy.ndarray]:
    """Every (tonic bin, mode, collection bins from the tonic up, collection
    name), its template, and its penalty."""
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
                root = (tonic - intervals[degree]) % 12  # the collection's first note: ionian's tonic
                candidates.append((tonic, mode, tuple((tonic + step) % 12 for step in steps),
                                   collection_name(name, root)))
                templates.append(template)
                penalties.append(0.0 if name == "diatonic" else float(KEY["collection_penalty"]))
    return candidates, numpy.array(templates), numpy.array(penalties)


KEY_CANDIDATES, KEY_TEMPLATES, KEY_PENALTIES = _key_candidates()
KEY_TONICS = numpy.array([tonic for tonic, _, _, _ in KEY_CANDIDATES])
KEY_COLLECTIONS = numpy.array([collection for _, _, _, collection in KEY_CANDIDATES])
COLLECTION_NAMES = list(dict.fromkeys(KEY_COLLECTIONS.tolist()))


def key_shares(chroma: numpy.ndarray, bass: numpy.ndarray) -> tuple[numpy.ndarray, numpy.ndarray]:
    """The frames' treble and bass shares of each pitch class: each frame's
    square-rooted bins divided by their own sum (a silent frame adds
    nothing), summed over the frames, and each total scaled to sum to one (all
    zeros when it has no weight)."""
    def share(values: numpy.ndarray) -> numpy.ndarray:
        roots = numpy.sqrt(numpy.maximum(values.astype(numpy.float64), 0.0))
        totals = roots.sum(axis=1, keepdims=True)
        summed = (roots / numpy.where(totals > 0, totals, 1.0)).sum(axis=0)
        return summed / summed.sum() if summed.sum() > 0 else numpy.zeros(12)
    return share(chroma), share(bass)


def key_profile(treble: numpy.ndarray, low: numpy.ndarray) -> numpy.ndarray:
    """The profile the templates are correlated with: the treble shares and
    the bass shares at `bass_weight`."""
    return treble + float(KEY["bass_weight"]) * low


def key_scores(profile: numpy.ndarray) -> numpy.ndarray | None:
    """Every candidate's correlation with the profile, less its penalty; None
    for a profile with no weight or no contrast."""
    if profile.sum() <= 0 or numpy.ptp(profile) <= 0:
        return None
    centred = profile - profile.mean()
    templates = KEY_TEMPLATES - KEY_TEMPLATES.mean(axis=1, keepdims=True)
    return templates @ centred / (numpy.linalg.norm(templates, axis=1) * numpy.linalg.norm(centred)) - KEY_PENALTIES


def collection_path(scores: numpy.ndarray, switch: float) -> list[int]:
    """The collection (an index of COLLECTION_NAMES) of each row of
    candidate scores on the path that maximises each row's best score in its
    collection, less `switch` for every change: a change of collection must
    lead by more than `switch` summed over the windows it holds."""
    if not len(scores):
        return []
    emission = numpy.stack([scores[:, KEY_COLLECTIONS == name].max(axis=1) for name in COLLECTION_NAMES], axis=1)
    back = numpy.zeros(emission.shape, dtype=numpy.int64)
    total = emission[0].copy()
    for row in range(1, len(emission)):
        best = int(numpy.argmax(total))
        jump = total[best] - switch
        back[row] = numpy.where(jump > total, best, numpy.arange(emission.shape[1]))
        total = numpy.maximum(total, jump) + emission[row]
    path = [int(numpy.argmax(total))]
    for row in range(len(emission) - 1, 0, -1):
        path.append(int(back[row, path[-1]]))
    return path[::-1]


def tonic_evidence(notes: Sequence[int], treble: numpy.ndarray, low: numpy.ndarray,
                   favoured: Sequence[int]) -> numpy.ndarray:
    """Each of a collection's notes' claim to be its tonic: its bass share,
    `treble` times its treble share, and `favour` more for the collection's
    major or minor tonic (`favoured`)."""
    settings = KEY["tonic"]
    return numpy.array([float(settings["bass"]) * low[note] + float(settings["treble"]) * treble[note]
                        + (float(settings["favour"]) if note in favoured else 0.0) for note in notes])


def tonic_of(collection: str, treble: numpy.ndarray, low: numpy.ndarray) -> dict[str, Any]:
    """The tonic of a collection's notes, its mode, how far its evidence
    leads the next note's (`tonic_margin`), and its confidence: the softmax
    of the seven notes' evidence at `temperature`, read at the tonic. A
    harmonic or melodic minor collection has one mode named here, on its own
    tonic, which its other notes' evidence can only make less certain."""
    modes = [(tonic, mode, notes) for tonic, mode, notes, name in KEY_CANDIDATES if name == collection]
    notes = list(modes[0][2]) if len(modes) == 1 else sorted({tonic for tonic, _, _ in modes})
    favoured = [tonic for tonic, mode, _ in modes if mode in KEY["tonic"]["favoured_modes"]]
    evidence = tonic_evidence(notes, treble, low, favoured)
    named = {tonic: (mode, scale) for tonic, mode, scale in modes}
    best = max((index for index, note in enumerate(notes) if note in named), key=lambda index: evidence[index])
    weights = numpy.exp((evidence - evidence.max()) / float(KEY["tonic"]["temperature"]))
    tonic = notes[best]
    mode, scale = named[tonic]
    return {"tonic": tonic, "mode": mode, "notes": scale,
            "tonic_margin": float(evidence[best] - numpy.delete(evidence, best).max()),
            "tonic_confidence": float(weights[best] / weights.sum())}


def key_of(treble: numpy.ndarray, low: numpy.ndarray, collection: str | None = None) -> dict[str, Any] | None:
    """The note collection of treble and bass shares, its tonic and mode, and
    how sure each is. The collection is the one whose best mode correlates
    best with the profile (`key_profile`); `collection_margin` is how far it
    leads the best reading on other notes. The tonic is then chosen among
    that collection's notes by `tonic_of`, and `key_strength` is the
    correlation of the mode it names. With `collection`, that collection's
    tonic and mode. None for a profile with no weight or no contrast."""
    scores = key_scores(key_profile(treble, low))
    if scores is None:
        return None
    if collection is None:
        collection = str(KEY_COLLECTIONS[int(numpy.argmax(scores))])
    inside = KEY_COLLECTIONS == collection
    tonic = tonic_of(collection, treble, low)
    chosen = next(index for index, (t, m, _, name) in enumerate(KEY_CANDIDATES)
                  if (t, m, name) == (tonic["tonic"], tonic["mode"], collection))
    return {"key": NOTES[tonic["tonic"]], "mode": tonic["mode"],
            "scale": "major" if tonic["mode"] in MAJOR_MODES else "minor",
            "scale_notes": " ".join(NOTES[note] for note in tonic["notes"]), "key_strength": float(scores[chosen]),
            "collection": collection,
            "collection_margin": float(scores[inside].max() - scores[~inside].max()),
            "tonic_margin": tonic["tonic_margin"], "tonic_confidence": tonic["tonic_confidence"],
            "tonic_named": False}


NO_KEY = {"key": "", "mode": "", "scale": "", "scale_notes": "", "key_strength": 0.0, "collection": "",
          "collection_margin": 0.0, "tonic_margin": 0.0, "tonic_confidence": 0.0, "tonic_named": False}


def key_label(found: dict[str, Any]) -> str:
    """A span's label: the note collection. Since version 6 no tonic is
    named, so no label is a tonic and mode."""
    return found["collection"]


def keys(chroma: numpy.ndarray, bass: numpy.ndarray, pitched: numpy.ndarray, samples: int) -> dict[str, Any]:
    """The recording's key, each window's, and the regions: the windows'
    collections laid end to end, each region keyed again from its own frames."""
    found = key_of(*key_shares(chroma[pitched], bass[pitched])) if pitched.any() else None
    outputs: dict[str, Any] = dict(found or NO_KEY)
    spans, scored = [], []
    for start, end in windows(len(chroma), KEY["window_frames"], KEY["hop_frames"]):
        mask = pitched[start:end]
        if mask.sum() < float(KEY["min_pitched"]) * (end - start):
            continue
        treble, low = key_shares(chroma[start:end][mask], bass[start:end][mask])
        window = key_of(treble, low)
        if window is None:
            continue
        begin, finish = extent(start, end, samples)
        spans.append({"start": begin, "end": finish, "label": key_label(window), "value": window["collection_margin"]})
        scored.append(key_scores(key_profile(treble, low)))
    outputs["keys"] = spans
    path = collection_path(numpy.array(scored), float(KEY["region_switch"]))
    laid = regions([{**span, "label": COLLECTION_NAMES[index], "value": 0.0} for span, index in zip(spans, path)])
    found_regions = []
    for region in laid:
        first = max(int(numpy.floor(region["start"] / SECONDS_PER_ROW + 0.5)), 0)
        stop = min(int(numpy.ceil(region["end"] / SECONDS_PER_ROW + 0.5)), len(chroma))
        mask = pitched[first:stop]
        within = key_of(*key_shares(chroma[first:stop][mask], bass[first:stop][mask]), region["label"])
        found_regions.append({**region, "label": key_label(within), "value": within["collection_margin"]})
    outputs["key_regions"] = found_regions
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
        measured = recording_tuning([window["cents"] for window in confident],
                                    [window["confidence"] for window in confident])
        centre = 0.0 if measured is None else measured
        found = measured if supported(len(confident), len(listed), len(self.windows)) else None
        cents = 0.0 if found is None else found
        confidence = 0.0 if found is None else concentration(self.sounding_histogram, cents,
                                                             PITCHED["concentration_cents"])
        rows = numpy.zeros((len(self.finished), len(TUNING_COLUMNS)), dtype="<f4")
        drift = []
        for index, window in enumerate(self.finished):
            share = window["pitched"] / (window["end"] - window["start"])
            window_cents = near(window["cents"], centre) if window["defined"] else 0.0
            rows[index] = (window_cents, window["confidence"], share)
            if window in listed:
                begin, end = extent(window["start"], window["end"], samples)
                drift.append({"start": begin, "end": end, "value": window_cents})
        limit = TUNING["max_detune_cents"]
        detuned_spans = [dict(span) for span in drift if abs(span["value"]) > limit]
        anchors = [((window["start"] + window["end"] - 1) / 2, near(window["cents"], centre)) for window in confident]
        return ({"tuning_frequency": frequency_of(cents), "tuning_cents": cents, "tuning_confidence": confidence,
                 "tuning_window_count": len(confident),
                 "tuning_drift": drift, "drift_spread_cents": spread([span["value"] for span in drift]),
                 "detuned": confidence > 0 and abs(cents) > limit, "detuned_spans": detuned_spans}, rows, anchors)


def supported(confident: int, listed: int, windows: int) -> bool:
    """Whether `confident` windows are enough for a whole recording's tuning:
    at least `min_windows` of them (or every window, when the recording has
    fewer), and at least `min_window_share` of the `listed` windows, those
    at least half pitched. Fewer, and the tuning rests on a corner of the
    recording that its other pitched windows do not confirm."""
    return confident > 0 and confident >= min(TUNING["min_windows"], windows) \
        and confident >= float(TUNING["min_window_share"]) * listed


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
    """Frames held a block at a time, until it is known which mains hum runs
    through the block and whether it is pitched, then handed to the tuning
    in order. `hum` keeps, per frame, the mains found in its block (a bit
    for each of `mains_hz`), for the profiles to leave out too."""

    def __init__(self, tuning: Tuning, sounding: numpy.ndarray, pitched: numpy.ndarray, hum: numpy.ndarray):
        self.tuning, self.sounding, self.pitched, self.hum = tuning, sounding, pitched, hum
        self.held: list[tuple[int, tuple[numpy.ndarray, numpy.ndarray] | None]] = []

    def add(self, row: int, frequencies: numpy.ndarray, magnitudes: numpy.ndarray) -> None:
        self.held.append((row, (frequencies, magnitudes) if self.sounding[row] else None))
        if len(self.held) == PITCHED["block_frames"]:
            self.flush()

    def flush(self) -> None:
        if not self.held:
            return
        mains = mains_lines([peaks for _, peaks in self.held if peaks is not None])
        held = [(row, None if peaks is None else
                 deviation_histogram(*histogram_peaks(*without_mains(*peaks, mains)))) for row, peaks in self.held]
        histograms = [histogram for _, histogram in held if histogram is not None]
        enough = len(histograms) >= float(PITCHED["min_sounding"]) * len(held)
        votes = [histogram / histogram.sum() for histogram in histograms if histogram.sum() > 0]
        is_pitched = enough and bool(votes) and best_concentration(numpy.sum(votes, axis=0),
                                                                   PITCHED["concentration_cents"]) \
            >= float(PITCHED["min_concentration"])
        bits = sum(1 << HUM["mains_hz"].index(line) for line in mains)
        for row, histogram in held:
            self.pitched[row] = is_pitched and histogram is not None
            self.hum[row] = bits
            self.tuning.add(row, bool(self.pitched[row]), histogram)
        self.held = []


def mains_of(bits: int) -> list[int]:
    return [line for index, line in enumerate(HUM["mains_hz"]) if bits >> index & 1]


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
    hum = numpy.zeros(rows, dtype=numpy.uint8)
    held = Blocks(tuning, sounding, pitched, hum)
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
            frequencies, magnitudes = profile_peaks(*without_mains(*peaks(spectrum(windowing(frame))),
                                                                   mains_of(int(hum[row]))))
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


SCALARS = ("tuning_frequency", "tuning_cents", "tuning_confidence", "tuning_window_count", "drift_spread_cents",
           "detuned", "key", "scale", "mode", "scale_notes", "key_strength", "collection", "collection_margin",
           "tonic_margin", "tonic_confidence", "tonic_named")
SPANS = ("tuning_drift", "detuned_spans", "keys", "key_regions")
SERIES = {"pitched": "u8", "tuning_windows": "f32", "dissonance": "f32", "chroma": "f32", "bass_chroma": "f32"}


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: tonal.py <request.json>", file=sys.stderr)
        return 2
    try:
        request = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
        if request.get("parameters") != parameters():
            raise TonalError("tonal 5 has fixed settings; the request names others")
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
