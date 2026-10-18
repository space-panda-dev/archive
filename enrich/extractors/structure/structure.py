#!/usr/bin/env python3
"""The structure extractor: where the playing is, and its shape (ADR 0074).

It decodes nothing. It reads the `frames` series (level, pitch-class
profiles, MFCCs, on the 93 ms `frames` grid) and the `rhythm` events (onsets
and beats, to about 12 ms), and finds:

- `playing`: stretches of sustained sound that is not tuning up. A frame is
  *active* when its level is above the recording's `threshold_db` scalar
  (the louder of `range_db` below the `reference_db` scalar, the 95th
  percentile level, and `floor_margin_db` above the noise floor, if the
  recording has one), and never below `silence_db`. The noise floor is the
  level a recording returns to when nobody plays. A frame above
  `silence_db` is *steady quiet* when it is at least `floor_depth_db`
  below the reference, moves no more than `floor_range_db` over the
  `sustain_ms` centred on it, and has no onset in that time; it is
  *unpitched quiet* when it is at least `noise_depth_db` below the
  reference, moves no more than `noise_range_db` over that time, and its
  pitch-class profile is flat (the geometric over the arithmetic mean of
  the profile, its median over `sustain_ms`, at least
  `noise_flatness_percent`). The floor is the median of the lowest
  `floor_band_db`-wide band of levels holding `floor_min_ms` of frames that
  are either. The `floor` scalar says whether there is one, and `floor_db`
  is its level (else the 5th percentile level). A rehearsal's room noise
  between songs is a floor. A mastered recording has none: its quietest
  frames are music (a quiet intro, a held drone, a breakdown) a dozen or
  twenty dB under the rest, which a floor taken from the 5th percentile put
  below the threshold, cutting the song into pieces with false starts and
  count-ins between them. Without a floor the recording is measured from
  the reference alone, so all of it above `range_db` below the reference
  is playing, and it has no tuning, count-ins, or false starts: those are
  a rehearsal's, sound between stretches of room noise. The two kinds of
  quiet cover two kinds of room: one under a hum (pitched, but deep and
  steady), and one too shallow (a quiet acoustic take, a phone's automatic
  gain lifting the room) or too often struck (chairs, a pedal, a cough) to
  be steady quiet, but unpitched as noise is. The defaults were
  measured on synthetic rooms of white and pink noise, 17 to 39 dB under
  the playing, steady or wobbling 3 to 5 dB and struck up to twice a
  second, and on a mastered album of eighteen tracks with no gaps between
  them. Room noise's flatness measured a median of 0.89 to 0.91; the
  album's quiet frames (within `noise_range_db`, at least `noise_depth_db`
  under) 0.51 at most, a track's median, and no track holds more than
  0.7 s of unpitched quiet. Steady quiet alone makes a floor on the album
  from 19.5 dB below the reference (a held intro, 19.0 dB for a held
  outro), so `floor_depth_db` leaves 6.5 dB, and no track holds more than
  0.7 s of steady quiet at it. No real room has been measured: a room
  under a hum less than `floor_depth_db` under the playing has no floor,
  and a master's steady unpitched bed (wind, tape hiss) at least
  `noise_depth_db` under the rest would be taken for one. A frame is
  *sustained* when at least `sustain_percent` of the `sustain_ms` centred
  on it is active, which a run of clicks or plucks is not. Sustained runs
  grow to the active frames touching them, merge across gaps up to
  `merge_gap_ms`, and are kept from `min_playing_ms`.
- `tuning`: tuning up. A frame is *single-pitch* when the squared
  pitch-class profile averaged over `pitch_smooth_frames` has at least
  `single_pitch_percent` of its energy in one class (a held string with its
  harmonics measures 50 to 95, a triad about 35). Single-pitch active frames
  outside the playing, grouped across gaps up to `tuning_gap_ms`, are a
  tuning stretch when they hold at least `tuning_min_ms` of sound in notes
  held at least `held_ms`. A sustained run whose active frames are at
  least `tuning_share_percent` single-pitch and whose median level is
  `tuning_quiet_db` below the reference is tuning, not playing. Its label
  is the held pitch classes in order (`E A D G B E`); confidence rises with
  the share of held time on `open_strings` classes, with quietness, and
  with playing on both sides. The pitch is resolved to a semitone class
  only: `frames/hpcp` has 12 bins, so how far a string is from its pitch,
  in cents, is not measured.
- `count_ins`: from the first of three or more regular onsets (intervals
  within `count_in_tolerance_percent` of their mean, from
  `count_in_min_interval_ms` to `count_in_max_interval_ms`) in the
  `count_in_window_ms` before a playing stretch, outside any other playing
  or tuning, to the stretch's start. Confidence rises with the clicks'
  regularity and number, with the music starting about one interval after
  the last click, and with the music's beat (from `rhythm/beats`) matching
  the interval, its half, or its double. Label: `clicks=<n> interval=<s>`.
- `false_starts`: a playing stretch no longer than `false_start_max_ms`
  followed by another within `restart_max_ms`. Confidence is a quarter for
  that (a short stretch may as well be talk, a riff, or a sound check) and three
  quarters how much more alike the two openings are than chance: the mean
  cosine similarity of their pitch-class profiles, frame by frame (the
  first `opening_ms` of each, at the best offset within `opening_shift_ms`),
  measured from the similarity of the first opening to the second stretch
  as a whole (0 at that baseline or below, 1 when identical). Profiles are
  never negative, so their raw similarity has a high floor: talk or noise
  before a song scored about 0.6 raw against its opening, a different
  song about 0.25; against the baseline both score below 0.2 and the same
  song started again about 0.97. Label:
  `restart=<that relative similarity>`.
- `endings`: one for each playing stretch, from where its level last stood
  within `decline_db` of the stretch's median to where it fell below the
  threshold. `abrupt` when that takes no more than `abrupt_ms` (an edit or
  a cut; a recording that ends while playing is abrupt with confidence 1),
  `fade` when it takes at least `fade_min_ms` with onsets continuing at
  `fade_onsets_per_minute` or more (playing that gets quieter), and `stop`
  otherwise (the band stops and the room or the last chord rings out).
  Essentia's FadeDetection is not used: it sets its thresholds from the
  mean level of everything it is given, so a quiet song after a loud one,
  or a recording of many songs, moves them.
- `sections`: each playing stretch cut where the novelty of its
  self-similarity peaks: blocks of `block_frames` frames (about a second)
  described by their mean pitch-class profile and mean MFCCs 1 to 12
  (standardised over the playing), Foote's checkerboard kernel of
  `kernel_blocks` blocks a side along the diagonal (the span detector's
  `novelty_curve`, ADR 0066), divided by the kernel's positive weight so
  that two halves that are each uniform score one minus their similarity.
  A boundary is a peak of at least `section_min_novelty_percent` that is
  the largest within `section_min_ms` either side. Sections are labelled
  `A`, `B`, ... within each stretch, a section taking the label of the
  first earlier one whose mean features have a cosine similarity of at
  least `section_same_percent`. Value: the novelty at its start (1 for a
  stretch's first section).
- `novelty`: that curve, clipped to [0, 1] (standardised MFCCs can point apart,
  a negative cosine similarity, so the raw value can pass 1), a row a block, 0
  outside playing and where the kernel does not fit. On the `blocks` grid,
  row `k` stands at the start of block `k` (frame `k * block_frames`, whose
  centre is sample `k * block_frames * 2048`), the boundary it measures, and
  its `frame` is the `2 * kernel_blocks` blocks the kernel covers. A
  boundary between two sections is a row's time exactly.
- `loops`: stretches whose pitch-class profiles repeat with one period at
  least `loop_min_repeats` times in a row. For every frame and every lag from
  `loop_min_period_ms` to `loop_max_period_ms`, in frames, the mean cosine
  similarity of the period starting there with the next; a frame starts a
  repetition at the shortest lag whose similarity is within
  `loop_tolerance_percent` of the best, at least `loop_similarity_percent`,
  and at least `loop_contrast_percent` above the median over all lags (so a
  held chord, similar at every lag, is no loop). Consecutive frames on one
  lag make one loop. Label: `period=<s> repeats=<n>`, and `beats=<n>` when
  the median beat in it divides the period evenly. Confidence rises with
  the contrast. The period is resolved to a frame (93 ms). Only the
  pitch-class profiles are compared: with the MFCCs, a held chord over a
  steady beat read as a loop of whichever multiple of the beat lies
  closest to a whole number of frames. So a groove on one chord, or drums
  alone, is not a loop here.

Every time is in seconds from the first decoded sample, on the `frames`
grid (a frame is 186 ms wide and every 93 ms), so an edge is good to about
a frame, and none is later than the decoded audio's end (the `frames`
`seconds` scalar: the grid's last rows reach past it); count-in clicks are
`rhythm/onsets`, which are 4 to 15 ms early (see the rhythm extractor). Nothing here is a claim: a span suggestion is
the span detector's (ADR 0066), and a reader that proposes anything from
these features does so through a derivation run.

Invoked by the runner as `structure.py <request.json>`. The settings in
`TUNABLE` may be configured; any other differing setting is refused.
"""

from __future__ import annotations

import json
import math
import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ENRICH = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ENRICH))

import numpy  # noqa: E402

import detector  # noqa: E402

RATE = 22050
HOP = 2048
SECONDS = HOP / RATE  # one frame step
LOOP_CHUNK = 4096  # frames of loop similarities held at once: 4096 x 324 lags of float32, 5 MB
NEEDS = ("frames/hpcp", "frames/rms_db", "frames/mfcc", "frames/seconds", "rhythm/onsets", "rhythm/beats")
CLASSES = ("A", "A#", "B", "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#")  # frames/hpcp's bins, from A
TUNABLE = {
    "silence_db": (-120, -20), "range_db": (10, 80), "floor_margin_db": (3, 40),
    "floor_depth_db": (6, 60), "floor_range_db": (1, 20), "floor_band_db": (1, 20), "floor_min_ms": (500, 60000),
    "noise_flatness_percent": (50, 100), "noise_depth_db": (3, 60), "noise_range_db": (1, 30),
    "sustain_ms": (500, 10000), "sustain_percent": (50, 100), "merge_gap_ms": (0, 30000),
    "min_playing_ms": (500, 60000),
    "pitch_smooth_frames": (1, 21), "single_pitch_percent": (30, 100), "tuning_gap_ms": (0, 10000),
    "tuning_min_ms": (500, 60000), "held_ms": (200, 5000), "tuning_quiet_db": (0, 40), "tuning_share_percent": (50, 100),
    "count_in_window_ms": (1000, 20000), "count_in_min_interval_ms": (100, 2000),
    "count_in_max_interval_ms": (200, 3000), "count_in_tolerance_percent": (1, 50),
    "false_start_max_ms": (2000, 120000), "restart_max_ms": (1000, 600000), "opening_ms": (1000, 30000),
    "opening_shift_ms": (0, 10000),
    "decline_db": (1, 20), "abrupt_ms": (50, 2000), "fade_min_ms": (500, 30000),
    "fade_onsets_per_minute": (1, 600),
    "block_frames": (2, 64), "kernel_blocks": (2, 64), "section_min_novelty_percent": (1, 200),
    "section_min_ms": (1000, 120000), "section_same_percent": (50, 100),
    "loop_min_period_ms": (500, 60000), "loop_max_period_ms": (1000, 120000),
    "loop_min_repeats": (2, 64), "loop_similarity_percent": (50, 100), "loop_contrast_percent": (1, 100),
    "loop_tolerance_percent": (0, 20),
}


class StructureError(Exception):
    pass


def parameters() -> dict[str, Any]:
    """The default settings, which the manifest must declare exactly."""
    return {"frames_grid": {"sample_rate": RATE, "hop": HOP, "frame": 4096, "first_centre": 0},
            "level_percentiles": [5, 95],
            "silence_db": -80, "range_db": 35, "floor_margin_db": 10,
            "floor_depth_db": 26, "floor_range_db": 4, "floor_band_db": 6, "floor_min_ms": 2000,
            "noise_flatness_percent": 80, "noise_depth_db": 12, "noise_range_db": 8,
            "sustain_ms": 1500, "sustain_percent": 85, "merge_gap_ms": 2500, "min_playing_ms": 2000,
            "pitch_smooth_frames": 5, "single_pitch_percent": 55, "tuning_gap_ms": 3000, "tuning_min_ms": 2000,
            "held_ms": 700, "tuning_quiet_db": 6, "tuning_share_percent": 75, "open_strings": ["C", "D", "E", "G", "A", "B"],
            "count_in_window_ms": 8000, "count_in_min_interval_ms": 250, "count_in_max_interval_ms": 1500,
            "count_in_tolerance_percent": 12,
            "false_start_max_ms": 30000, "restart_max_ms": 60000, "opening_ms": 8000, "opening_shift_ms": 2000,
            "decline_db": 3, "abrupt_ms": 300, "fade_min_ms": 2500, "fade_onsets_per_minute": 60,
            "block_frames": 11, "kernel_blocks": 8, "novelty": "detector.novelty_curve",
            "section_features": ["hpcp", "mfcc[1:13] standardised"], "section_min_novelty_percent": 15,
            "section_min_ms": 8000, "section_same_percent": 90,
            "loop_min_period_ms": 2000, "loop_max_period_ms": 32000,
            "loop_features": ["hpcp"], "loop_min_repeats": 3, "loop_similarity_percent": 85, "loop_contrast_percent": 10,
            "loop_tolerance_percent": 2}


def settings(requested: Any) -> dict[str, Any]:
    """The request's parameters, if this program implements them: the
    defaults with only `TUNABLE` fields changed, each an integer in range."""
    defaults = parameters()
    if not isinstance(requested, dict) or set(requested) != set(defaults):
        raise StructureError("the request's parameters are not structure's")
    for name, value in requested.items():
        if name in TUNABLE:
            low, high = TUNABLE[name]
            if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
                raise StructureError(f"{name} must be an integer from {low} to {high}, not {value!r}")
        elif value != defaults[name]:
            raise StructureError(f"structure implements {name} {defaults[name]!r} only, not {value!r}")
    if requested["count_in_min_interval_ms"] >= requested["count_in_max_interval_ms"]:
        raise StructureError("count_in_min_interval_ms must be below count_in_max_interval_ms")
    if requested["loop_min_period_ms"] >= requested["loop_max_period_ms"]:
        raise StructureError("loop_min_period_ms must be below loop_max_period_ms")
    return requested


# -- reading the bank's features ---------------------------------------------

@dataclass(frozen=True)
class Features:
    level: numpy.ndarray  # dBFS a frame
    hpcp: numpy.ndarray  # [frames, 12]
    mfcc: numpy.ndarray  # [frames, 13]
    onsets: numpy.ndarray  # seconds
    beats: numpy.ndarray  # seconds
    seconds: float  # the decoded audio's length

    @property
    def frames(self) -> int:
        return len(self.level)


def read_needs(needs: dict[str, Any], grid: dict[str, int]) -> Features:
    """The needed features, by the paths the request gives, refused unless
    every series is on the `frames` grid the settings name."""
    missing = [name for name in NEEDS if name not in needs]
    if missing:
        raise StructureError(f"the request does not give {', '.join(missing)}")
    series = {}
    for name in ("frames/hpcp", "frames/rms_db", "frames/mfcc"):
        need = needs[name]
        found = {k: v for k, v in (need.get("grid") or {}).items() if k != "name"}
        if found != grid:
            raise StructureError(f"{name} is on grid {found}, not {grid}")
        if need.get("kind") != "series" or need.get("dtype") != "f32":
            raise StructureError(f"{name} is not an f32 series")
        rows, columns = need["shape"]
        values = numpy.fromfile(need["path"], dtype="<f4")
        if values.size != rows * columns:
            raise StructureError(f"{need['path']} does not hold its shape {need['shape']}")
        series[name] = values.reshape(rows, columns).astype(numpy.float64)
    rows = {len(values) for values in series.values()}
    if len(rows) != 1:
        raise StructureError(f"the frames series disagree on their length: {sorted(rows)}")
    seconds = needs["frames/seconds"].get("value")
    if needs["frames/seconds"].get("kind") != "scalar" or not isinstance(seconds, (int, float)) \
            or isinstance(seconds, bool) or not math.isfinite(seconds) or seconds < 0:
        raise StructureError(f"frames/seconds is not a length in seconds: {seconds!r}")
    events = {}
    for name in ("rhythm/onsets", "rhythm/beats"):
        items = json.loads(Path(needs[name]["path"]).read_text(encoding="utf-8"))
        events[name] = numpy.asarray([item["time"] for item in items], dtype=numpy.float64)
    return Features(series["frames/rms_db"][:, 0], series["frames/hpcp"], series["frames/mfcc"],
                    events["rhythm/onsets"], events["rhythm/beats"], float(seconds))


# -- small pieces ------------------------------------------------------------

def frames_of(ms: int) -> int:
    """A duration in whole frames, at least one."""
    return max(1, int(round(ms / 1000 / SECONDS)))


def runs(mask: numpy.ndarray) -> list[tuple[int, int]]:
    """Maximal runs `[start, end)` of true frames."""
    return detector.runs(numpy.asarray(mask, dtype=bool), 1)


def merge(spans: list[tuple[int, int]], gap: int) -> list[tuple[int, int]]:
    """Spans in order, joined where no more than `gap` frames separate them."""
    merged: list[list[int]] = []
    for start, end in spans:
        if merged and start - merged[-1][1] <= gap:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(a, b) for a, b in merged]


def ramp(value: float, low: float, high: float) -> float:
    """0 at `low`, 1 at `high`, linear between (either order)."""
    if high == low:
        return 1.0 if value >= high else 0.0
    return float(min(1.0, max(0.0, (value - low) / (high - low))))


def centred_fraction(mask: numpy.ndarray, width: int) -> numpy.ndarray:
    """For each frame, the fraction of true frames in the `width` frames
    centred on it (fewer at the ends: only those that exist)."""
    values = numpy.concatenate(([0], numpy.cumsum(mask, dtype=numpy.int64)))
    index = numpy.arange(len(mask))
    low = numpy.clip(index - width // 2, 0, len(mask))
    high = numpy.clip(index - width // 2 + width, 0, len(mask))
    return (values[high] - values[low]) / numpy.maximum(high - low, 1)


def t(frame: float) -> float:
    """A frame index as seconds (its centre), to the microsecond."""
    return round(float(frame) * SECONDS, 6)


def unit_rows(values: numpy.ndarray) -> numpy.ndarray:
    norms = numpy.sqrt(numpy.sum(values * values, axis=1, keepdims=True))
    return values / numpy.maximum(norms, 1e-12)


# -- level, activity, pitch -----------------------------------------------------

@dataclass(frozen=True)
class Levels:
    floor_db: float
    reference_db: float
    threshold_db: float
    active: numpy.ndarray
    floor: bool  # whether floor_db is a noise floor, not the 5th percentile


def moving_range(values: numpy.ndarray, width: int) -> numpy.ndarray:
    """For each frame, the largest minus the smallest value in the `width`
    frames centred on it (fewer at the ends: only those that exist)."""
    padded = numpy.pad(values, (width // 2, width - width // 2 - 1), mode="edge")
    windows = numpy.lib.stride_tricks.sliding_window_view(padded, width)
    return windows.max(axis=1) - windows.min(axis=1)


def steady_quiet(level: numpy.ndarray, onsets: numpy.ndarray, reference: float,
                 p: dict[str, Any]) -> numpy.ndarray:
    """Frames that sound like nobody playing: above `silence_db`, at least
    `floor_depth_db` below the reference, within `floor_range_db` over the
    `sustain_ms` centred on them, and with no onset in that time."""
    width = frames_of(p["sustain_ms"])
    times = numpy.arange(len(level)) * SECONDS
    half = p["sustain_ms"] / 2000
    struck = numpy.searchsorted(onsets, times + half, side="right") - numpy.searchsorted(onsets, times - half)
    return ((level > p["silence_db"]) & (level <= reference - p["floor_depth_db"])
            & (moving_range(level, width) <= p["floor_range_db"]) & (struck == 0))


def flatness(hpcp: numpy.ndarray) -> numpy.ndarray:
    """For each frame, how evenly its pitch-class profile spreads over the
    twelve classes: the geometric over the arithmetic mean of the profile
    scaled to sum to one (plus 1e-6 a class), 1 when flat, near 0 when a
    few classes hold it. Room noise measures about 0.9; music, which is
    pitched, a median of 0.1 to 0.5."""
    profile = numpy.maximum(hpcp, 0)
    profile = profile / numpy.maximum(profile.sum(axis=1, keepdims=True), 1e-12) + 1e-6
    return numpy.exp(numpy.mean(numpy.log(profile), axis=1)) / numpy.mean(profile, axis=1)


def moving_median(values: numpy.ndarray, width: int) -> numpy.ndarray:
    """For each frame, the median of the `width` frames centred on it (the
    ends repeated)."""
    padded = numpy.pad(values, (width // 2, width - width // 2 - 1), mode="edge")
    return numpy.median(numpy.lib.stride_tricks.sliding_window_view(padded, width), axis=1)


def unpitched_quiet(level: numpy.ndarray, hpcp: numpy.ndarray, reference: float,
                    p: dict[str, Any]) -> numpy.ndarray:
    """Frames that sound like room noise however shallow or struck: above
    `silence_db`, at least `noise_depth_db` below the reference, within
    `noise_range_db` over the `sustain_ms` centred on them, and unpitched,
    their pitch-class profile's flatness (its median over `sustain_ms`) at
    least `noise_flatness_percent`."""
    width = frames_of(p["sustain_ms"])
    flat = moving_median(flatness(hpcp), width) * 100 >= p["noise_flatness_percent"]
    return ((level > p["silence_db"]) & (level <= reference - p["noise_depth_db"])
            & (moving_range(level, width) <= p["noise_range_db"]) & flat)


def lowest_band(values: numpy.ndarray, band: float, count: int) -> float | None:
    """The median of the lowest `band`-dB-wide set of `values` holding at
    least `count` of them, or None when no set does."""
    ordered = numpy.sort(values)
    within = numpy.searchsorted(ordered, ordered + band, side="right") - numpy.arange(len(ordered))
    enough = numpy.flatnonzero(within >= count)
    if not len(enough):
        return None
    first = int(enough[0])
    return float(numpy.median(ordered[first:first + int(within[first])]))


def levels(level: numpy.ndarray, onsets: numpy.ndarray, hpcp: numpy.ndarray, p: dict[str, Any]) -> Levels:
    """The reference, the noise floor if the recording has one, and the
    threshold they set. A floor is the level the recording returns to when
    nobody plays: the lowest `floor_band_db` band holding `floor_min_ms` of
    frames that are steady quiet (onset-free, at least `floor_depth_db`
    below the reference) or unpitched quiet (flat pitch-class profiles at
    least `noise_depth_db` below it, struck or not). A mastered recording
    has none (its quietest frames are music, pitched, moving and struck, a
    dozen or twenty dB under the rest), and is then measured from the
    reference alone."""
    low, high = (float(v) for v in numpy.percentile(level, p["level_percentiles"]))
    quiet = level[steady_quiet(level, onsets, high, p) | unpitched_quiet(level, hpcp, high, p)]
    floor = lowest_band(quiet, p["floor_band_db"], frames_of(p["floor_min_ms"]))
    threshold = high - p["range_db"]
    if floor is not None:
        threshold = max(threshold, floor + p["floor_margin_db"])
    threshold = max(threshold, p["silence_db"])
    return Levels(low if floor is None else floor, high, threshold, level > threshold, floor is not None)


def single_pitch(hpcp: numpy.ndarray, p: dict[str, Any]) -> tuple[numpy.ndarray, numpy.ndarray]:
    """For each frame, whether one pitch class holds `single_pitch_percent`
    of the squared profile averaged over `pitch_smooth_frames`, and which."""
    width = p["pitch_smooth_frames"]
    totals = numpy.vstack((numpy.zeros((1, 12)), numpy.cumsum(hpcp, axis=0)))
    index = numpy.arange(len(hpcp))
    low = numpy.clip(index - width // 2, 0, len(hpcp))
    high = numpy.clip(index - width // 2 + width, 0, len(hpcp))
    smooth = (totals[high] - totals[low]) / numpy.maximum(high - low, 1)[:, None]
    energy = smooth * smooth
    share = energy.max(axis=1) / numpy.maximum(energy.sum(axis=1), 1e-12)
    return share * 100 >= p["single_pitch_percent"], energy.argmax(axis=1)


# -- playing and tuning --------------------------------------------------------

def held_notes(single: numpy.ndarray, pitch: numpy.ndarray, active: numpy.ndarray,
               start: int, end: int, minimum: int) -> list[tuple[int, int, int]]:
    """Notes held in [start, end): runs of active single-pitch frames on one
    class at least `minimum` frames long, as (start, end, class)."""
    notes = []
    good = single[start:end] & active[start:end]
    for a, b in runs(good):
        classes = pitch[start + a:start + b]
        cuts = numpy.flatnonzero(numpy.diff(classes)) + 1
        for c, d in zip(numpy.r_[0, cuts], numpy.r_[cuts, b - a]):
            if d - c >= minimum:
                notes.append((start + a + int(c), start + a + int(d), int(classes[c])))
    return notes


@dataclass(frozen=True)
class Stretch:
    start: int  # frames, [start, end)
    end: int
    confidence: float = 1.0
    label: str | None = None


def playing_and_tuning(f: Features, lv: Levels, p: dict[str, Any]) -> tuple[list[Stretch], list[Stretch]]:
    single, pitch = single_pitch(f.hpcp, p)
    active = lv.active
    sustained = active & (centred_fraction(active, frames_of(p["sustain_ms"])) * 100 >= p["sustain_percent"])
    held = frames_of(p["held_ms"])
    candidates = []
    tuning_like = []
    for a, b in runs(sustained):
        # Grow to the active frames touching the run: the window's edges start and stop it late.
        while a > 0 and active[a - 1]:
            a -= 1
        while b < len(active) and active[b]:
            b += 1
        if candidates and a < candidates[-1][1]:
            candidates[-1] = (candidates[-1][0], max(b, candidates[-1][1]))
            continue
        candidates.append((a, b))
    playing = []
    for a, b in merge(candidates, frames_of(p["merge_gap_ms"])):
        on = active[a:b]
        share = float(numpy.mean(single[a:b][on])) if on.any() else 0.0
        quiet = float(numpy.median(f.level[a:b][on])) if on.any() else lv.floor_db
        if lv.floor and share * 100 >= p["tuning_share_percent"] and quiet <= lv.reference_db - p["tuning_quiet_db"]:
            tuning_like.append((a, b))
        elif b - a >= frames_of(p["min_playing_ms"]):
            playing.append((a, b))
    if not lv.floor:
        # No floor, no rehearsal: a quiet single-note line in a master is playing, not tuning up.
        return [Stretch(a, b) for a, b in playing], []
    inside = numpy.zeros(f.frames, dtype=bool)
    for a, b in playing:
        inside[a:b] = True
    candidates_t = [(a, b) for a, b in runs(single & active & ~inside)]
    candidates_t = merge(sorted(candidates_t + tuning_like), frames_of(p["tuning_gap_ms"]))
    tuning = []
    open_classes = {CLASSES.index(name) for name in p["open_strings"]}
    for a, b in candidates_t:
        # Never into the playing: a stretch merged across a gap is cut back at the playing's edges.
        cut = numpy.flatnonzero(inside[a:b])
        if len(cut):
            pieces = [(a + int(s), a + int(e)) for s, e in runs(~inside[a:b])]
        else:
            pieces = [(a, b)]
        for c, d in pieces:
            notes = held_notes(single, pitch, active, c, d, held)
            held_frames = sum(e - s for s, e, _ in notes)
            if held_frames < frames_of(p["tuning_min_ms"]):
                continue
            on_open = sum(e - s for s, e, k in notes if k in open_classes) / held_frames
            quiet = float(numpy.median(numpy.concatenate([f.level[s:e] for s, e, _ in notes])))
            quietness = ramp(lv.reference_db - quiet, 0, 2 * p["tuning_quiet_db"])
            before = any(e <= c for _, e in playing)
            after = any(s >= d for s, _ in playing)
            between = 1.0 if before and after else 0.8 if before or after else 0.6
            sequence = []
            for _, _, k in notes:
                if not sequence or sequence[-1] != CLASSES[k]:
                    sequence.append(CLASSES[k])
            confidence = (0.5 + 0.5 * on_open) * (0.5 + 0.5 * quietness) * between
            start, end = notes[0][0], notes[-1][1]
            tuning.append(Stretch(start, end, round(confidence, 3), " ".join(sequence)))
    return [Stretch(a, b) for a, b in playing], tuning


# -- count-ins, false starts, endings --------------------------------------------

def count_ins(f: Features, playing: list[Stretch], tuning: list[Stretch], p: dict[str, Any]) -> list[dict[str, Any]]:
    found = []
    shortest, longest = p["count_in_min_interval_ms"] / 1000, p["count_in_max_interval_ms"] / 1000
    tolerance = p["count_in_tolerance_percent"] / 100
    for index, stretch in enumerate(playing):
        start = stretch.start * SECONDS
        earlier = [s.end * SECONDS for s in playing[:index]] + [s.end * SECONDS for s in tuning
                                                                 if s.end <= stretch.start]
        begin = max([start - p["count_in_window_ms"] / 1000, *earlier])
        clicks = f.onsets[(f.onsets > begin) & (f.onsets < start - shortest / 2)]
        if len(clicks) < 3:
            continue
        # The longest regular run ending at the last click before the music.
        chosen = [clicks[-1]]
        for click in clicks[-2::-1]:
            gaps = numpy.diff(numpy.r_[click, chosen])
            mean = float(numpy.mean(gaps))
            if not shortest <= mean <= longest or numpy.max(numpy.abs(gaps - mean)) > tolerance * mean:
                break
            chosen.insert(0, click)
        if len(chosen) < 3:
            continue
        gaps = numpy.diff(chosen)
        interval = float(numpy.mean(gaps))
        lead = start - chosen[-1]
        regular = 1 - float(numpy.max(numpy.abs(gaps - interval))) / (tolerance * interval)
        number = min(len(chosen), 4) / 4
        # The music starts about one interval after the last click (the frames grid is 93 ms).
        on_time = ramp(abs(lead - interval), interval * 0.5, SECONDS)
        after = f.beats[(f.beats >= start) & (f.beats < start + 10)]
        tempo = 0.5
        if len(after) >= 3:
            beat = float(numpy.median(numpy.diff(after)))
            tempo = max(ramp(abs(math.log2(interval / beat) - k), 0.15, 0.03) for k in (-1, 0, 1))
        confidence = (0.4 + 0.6 * regular) * (0.5 + 0.5 * number) * (0.5 + 0.5 * on_time) * (0.6 + 0.4 * tempo)
        found.append({"start": round(float(chosen[0]), 6), "end": t(stretch.start),
                      "value": round(confidence, 3), "label": f"clicks={len(chosen)} interval={interval:.3f}"})
    return found


def opening_similarity(hpcp: numpy.ndarray, first: Stretch, second: Stretch, p: dict[str, Any]) -> float:
    """How much more alike the two stretches' openings are than chance: the
    mean cosine similarity of their pitch-class profiles, frame by frame, at
    the best offset within `opening_shift_ms`, as a fraction of the way from
    the baseline (the first opening's mean similarity to every frame of the
    second stretch) to 1; 0 at or below the baseline."""
    length = min(frames_of(p["opening_ms"]), first.end - first.start, second.end - second.start)
    a = unit_rows(hpcp[first.start:first.start + length])
    best = 0.0
    shift = frames_of(p["opening_shift_ms"]) if p["opening_shift_ms"] else 0
    for offset in range(-shift, shift + 1):
        begin = second.start + offset
        if begin < 0 or begin + length > len(hpcp):
            continue
        b = unit_rows(hpcp[begin:begin + length])
        best = max(best, float(numpy.mean(numpy.sum(a * b, axis=1))))
    baseline = float(numpy.dot(a.mean(axis=0), unit_rows(hpcp[second.start:second.end]).mean(axis=0)))
    if baseline >= 1 - 1e-9:
        return 0.0
    return max(0.0, (best - baseline) / (1 - baseline))


def false_starts(f: Features, playing: list[Stretch], p: dict[str, Any]) -> list[dict[str, Any]]:
    found = []
    for first, second in zip(playing, playing[1:]):
        if (first.end - first.start) * SECONDS > p["false_start_max_ms"] / 1000:
            continue
        if (second.start - first.end) * SECONDS > p["restart_max_ms"] / 1000:
            continue
        similarity = opening_similarity(f.hpcp, first, second, p)
        found.append({"start": t(first.start), "end": t(first.end), "value": round(0.25 + 0.75 * similarity, 3),
                      "label": f"restart={similarity:.3f}"})
    return found


def endings(f: Features, lv: Levels, playing: list[Stretch], p: dict[str, Any]) -> list[dict[str, Any]]:
    found = []
    for stretch in playing:
        if stretch.end >= f.frames - 1:
            found.append({"start": t(stretch.end - 1), "end": round(f.seconds, 6), "value": 1.0, "label": "abrupt"})
            continue
        level = f.level[stretch.start:stretch.end]
        body = float(numpy.median(level))
        loud = numpy.flatnonzero(level >= body - p["decline_db"])
        decline = stretch.start + (int(loud[-1]) if len(loud) else 0)
        below = numpy.flatnonzero(f.level[decline:] <= lv.threshold_db)
        reach = decline + (int(below[0]) if len(below) else f.frames - decline)
        seconds = (reach - decline) * SECONDS
        onsets = int(numpy.sum((f.onsets > decline * SECONDS + 0.2) & (f.onsets < reach * SECONDS)))
        rate = onsets / max(seconds - 0.2, 1e-9) * 60
        abrupt_s, fade_s = p["abrupt_ms"] / 1000, p["fade_min_ms"] / 1000
        if seconds <= abrupt_s:
            label, confidence = "abrupt", ramp(seconds, 2 * abrupt_s, SECONDS)
            confidence = max(confidence, 0.5)
        elif seconds >= fade_s and rate >= p["fade_onsets_per_minute"]:
            label = "fade"
            confidence = 0.5 + 0.5 * min(ramp(seconds, fade_s, 2 * fade_s),
                                         ramp(rate, p["fade_onsets_per_minute"], 2 * p["fade_onsets_per_minute"]))
        else:
            label = "stop"
            confidence = 0.5 + 0.5 * min(ramp(seconds, abrupt_s, 2 * abrupt_s),
                                         max(ramp(seconds, fade_s, abrupt_s * 2),
                                             ramp(rate, p["fade_onsets_per_minute"], 0)))
        found.append({"start": t(decline), "end": t(reach), "value": round(confidence, 3), "label": label})
    return found


# -- sections and loops -----------------------------------------------------------

def described(f: Features, playing: list[Stretch]) -> numpy.ndarray:
    """Each frame's description for self-similarity: its pitch-class profile
    (unit length) beside MFCCs 1 to 12 standardised over the playing (unit
    length), equally weighted."""
    inside = numpy.zeros(f.frames, dtype=bool)
    for s in playing:
        inside[s.start:s.end] = True
    mfcc = f.mfcc[:, 1:]
    chosen = mfcc[inside] if inside.any() else mfcc
    mfcc = (mfcc - chosen.mean(axis=0)) / numpy.maximum(chosen.std(axis=0), 1e-9)
    return numpy.hstack((unit_rows(f.hpcp), unit_rows(mfcc))) / math.sqrt(2)


def blocks_of(values: numpy.ndarray, start: int, end: int, size: int) -> numpy.ndarray:
    """Means of whole blocks of `size` frames from `start`, up to `end`."""
    count = (end - start) // size
    return values[start:start + count * size].reshape(count, size, -1).mean(axis=1)


def sections(features: numpy.ndarray, playing: list[Stretch], frames: int,
             p: dict[str, Any]) -> tuple[list[dict[str, Any]], numpy.ndarray]:
    size, half = p["block_frames"], p["kernel_blocks"]
    weight = float(numpy.sum(numpy.clip(detector.checkerboard_kernel(half), 0, None)))
    novelty = numpy.zeros(frames // size + (1 if frames % size else 0), dtype=numpy.float64)
    spacing = max(1, int(round(p["section_min_ms"] / 1000 / (size * SECONDS))))
    found = []
    for stretch in playing:
        # Blocks aligned to the recording's grid of blocks, wholly inside the stretch.
        first = -(-stretch.start // size)
        last = stretch.end // size
        boundaries: list[tuple[int, float]] = []
        if last - first > 2 * half:
            blocks = blocks_of(features, first * size, last * size, size)
            curve = detector.novelty_curve(blocks, half) / weight
            curve[:half] = curve[last - first - half:] = 0
            novelty[first:last] = numpy.clip(curve, 0, 1)
            for index in range(half, last - first - half):
                lo, hi = max(half, index - spacing), min(last - first - half, index + spacing + 1)
                if curve[index] * 100 >= p["section_min_novelty_percent"] and curve[index] == curve[lo:hi].max():
                    if not boundaries or index - boundaries[-1][0] > spacing:
                        boundaries.append((index, float(curve[index])))
        edges = [stretch.start] + [(first + index) * size for index, _ in boundaries] + [stretch.end]
        values = [1.0] + [min(1.0, value) for _, value in boundaries]
        means = [unit_rows(features[a:b].mean(axis=0, keepdims=True))[0] for a, b in zip(edges, edges[1:])]
        labels: list[str] = []
        for index, mean in enumerate(means):
            label = None
            for earlier in range(index):
                if float(numpy.dot(mean, means[earlier])) * 100 >= p["section_same_percent"]:
                    label = labels[earlier]
                    break
            if label is None:
                label = section_name(len(set(labels)))
            labels.append(label)
        for (a, b), value, label in zip(zip(edges, edges[1:]), values, labels):
            found.append({"start": t(a), "end": t(b), "value": round(value, 3), "label": label})
    return found, novelty.astype("<f4").reshape(-1, 1)


def section_name(index: int) -> str:
    """A, B, ..., Z, AA, AB, ..."""
    name = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def lag_similarity(unit: numpy.ndarray, lags: range) -> numpy.ndarray:
    """[frames, lags], float32: for frame i and lag l, the mean cosine
    similarity of the unit rows i..i+l-1 with rows i+l..i+2l-1; NaN where
    that runs past the end."""
    count = len(unit)
    out = numpy.full((count, len(lags)), numpy.nan, dtype=numpy.float32)
    for column, lag in enumerate(lags):
        if 2 * lag > count:
            break
        diagonal = numpy.einsum("ij,ij->i", unit[:-lag], unit[lag:])
        sums = numpy.concatenate(([0.0], numpy.cumsum(diagonal)))
        starts = numpy.arange(count - 2 * lag + 1)
        out[starts, column] = (sums[starts + lag] - sums[starts]) / lag
    return out


def loop_choices(similarity: numpy.ndarray, p: dict[str, Any]) -> tuple[numpy.ndarray, numpy.ndarray]:
    """For each frame, the index of the shortest lag that is within
    `loop_tolerance_percent` of the best, at least `loop_similarity_percent`,
    and `loop_contrast_percent` above the median over all lags (-1 where
    none is), and that lag's contrast."""
    valid = ~numpy.isnan(similarity)
    usable = valid.sum(axis=1) >= 2
    filled = numpy.where(valid, similarity, -numpy.inf)
    best = filled.max(axis=1)
    median = numpy.full(len(similarity), numpy.inf, dtype=numpy.float32)
    if usable.any():
        median[usable] = numpy.nanmedian(similarity[usable], axis=1)
    good = (valid & (filled >= best[:, None] - p["loop_tolerance_percent"] / 100)
            & (filled * 100 >= p["loop_similarity_percent"])
            & ((filled - median[:, None]) * 100 >= p["loop_contrast_percent"]) & usable[:, None])
    chosen = numpy.where(good.any(axis=1), good.argmax(axis=1), -1)
    rows = numpy.arange(len(similarity))
    contrast = numpy.where(chosen >= 0, filled[rows, numpy.maximum(chosen, 0)] - median, 0.0)
    return chosen, contrast


def loops(features: numpy.ndarray, f: Features, playing: list[Stretch], p: dict[str, Any]) -> list[dict[str, Any]]:
    """Loops, frame by frame, a stretch at a time and `LOOP_CHUNK` frames
    of it at a time, so memory does not grow with the playing's length."""
    lags = range(frames_of(p["loop_min_period_ms"]), frames_of(p["loop_max_period_ms"]) + 1)
    found = []
    for stretch in playing:
        unit = unit_rows(features[stretch.start:stretch.end]).astype(numpy.float32)
        if len(unit) < lags.start * p["loop_min_repeats"]:
            continue
        # A chunk of rows at a time, each with the two longest periods after it that its rows compare.
        pieces = [loop_choices(lag_similarity(unit[c:c + LOOP_CHUNK + 2 * lags[-1]], lags)[:LOOP_CHUNK], p)
                  for c in range(0, len(unit), LOOP_CHUNK)]
        chosen = numpy.concatenate([piece[0] for piece in pieces])
        contrast = numpy.concatenate([piece[1] for piece in pieces])
        index = 0
        while index < len(unit):
            if chosen[index] < 0:
                index += 1
                continue
            end = index
            # One loop while the chosen lag stays within a frame of where it started.
            while end + 1 < len(unit) and chosen[end + 1] >= 0 and abs(chosen[end + 1] - chosen[index]) <= 1:
                end += 1
            lag = lags[int(numpy.median(chosen[index:end + 1]))]
            a = stretch.start + index
            b = min(stretch.start + end + 2 * lag, stretch.end)
            repeats = (b - a) / lag
            if repeats >= p["loop_min_repeats"]:
                confidence = 0.5 + 0.5 * ramp(float(numpy.mean(contrast[index:end + 1])),
                                              p["loop_contrast_percent"] / 100, 0.4)
                label = f"period={lag * SECONDS:.3f} repeats={int(round(repeats))}"
                beats = f.beats[(f.beats >= a * SECONDS) & (f.beats < b * SECONDS)]
                if len(beats) >= 4:
                    count = lag * SECONDS / float(numpy.median(numpy.diff(beats)))
                    if round(count) >= 1 and abs(count - round(count)) <= 0.03 * round(count) + 0.05:
                        label += f" beats={int(round(count))}"
                found.append({"start": t(a), "end": t(b), "value": round(confidence, 3), "label": label})
                # The next loop starts where this one ends.
                index = max(end + 1, b - stretch.start)
            else:
                index = end + 1
    return found


# -- the program -------------------------------------------------------------

def within(items: list[dict[str, Any]], seconds: float) -> list[dict[str, Any]]:
    """Spans no later than the audio's end: the `frames` grid makes
    `ceil(n / 2048) + 1` rows, so a frame index can stand up to two hops
    past the last decoded sample."""
    end = round(seconds, 6)
    return [{**item, "start": min(item["start"], end), "end": min(item["end"], end)} for item in items]


def analyse(f: Features, p: dict[str, Any]) -> dict[str, Any]:
    if f.frames == 0:
        raise StructureError("the frames series are empty")
    if not (numpy.isfinite(f.level).all() and numpy.isfinite(f.hpcp).all() and numpy.isfinite(f.mfcc).all()):
        raise StructureError("the frames series hold values that are not finite")
    lv = levels(f.level, f.onsets, f.hpcp, p)
    playing, tuning = playing_and_tuning(f, lv, p)
    # Count-ins and false starts are a rehearsal's: sound between stretches over a floor of room noise.
    rehearsal = lv.floor
    features = described(f, playing)
    section_spans, novelty = sections(features, playing, f.frames, p)
    spans = {
        "playing": [{"start": t(s.start), "end": t(s.end)} for s in playing],
        "tuning": [{"start": t(s.start), "end": t(s.end), "value": s.confidence, "label": s.label} for s in tuning],
        "count_ins": count_ins(f, playing, tuning, p) if rehearsal else [],
        "false_starts": false_starts(f, playing, p) if rehearsal else [],
        "endings": endings(f, lv, playing, p),
        "sections": section_spans,
        "loops": loops(unit_rows(f.hpcp), f, playing, p),
    }
    return {
        **{name: within(items, f.seconds) for name, items in spans.items()},
        "novelty": novelty,
        "floor_db": round(lv.floor_db, 3), "reference_db": round(lv.reference_db, 3),
        "threshold_db": round(lv.threshold_db, 3), "floor": lv.floor,
    }


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: structure.py <request.json>", file=sys.stderr)
        return 2
    try:
        request = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
        config = settings(request.get("parameters"))
        found = analyse(read_needs(request["needs"], config["frames_grid"]), config)
        output = Path(request["output_dir"])
        outputs: dict[str, Any] = {}
        for name, value in found.items():
            if isinstance(value, numpy.ndarray):
                (output / f"{name}.f32").write_bytes(value.tobytes())
                outputs[name] = {"shape": list(value.shape)}
            elif isinstance(value, list):
                (output / f"{name}.json").write_text(json.dumps(value))
                outputs[name] = {}
            else:
                outputs[name] = {"value": value}
        size = config["block_frames"]
        # Row k measures the boundary at the start of block k (detector.novelty_curve compares
        # blocks k-half..k-1 with k..k+half-1), and depends on all 2 * half of them.
        grids = {"blocks": {"sample_rate": RATE, "hop": size * HOP,
                            "frame": 2 * config["kernel_blocks"] * size * HOP, "first_centre": 0}}
        software = [{"name": "numpy", "version": numpy.__version__},
                    {"name": "python", "version": platform.python_version()}]
        result = {"contract": "archive-extractor-result/1", "software": software, "grids": grids,
                  "outputs": outputs}
        (output / "result.json").write_text(json.dumps(result))
    except (StructureError, OSError, KeyError, ValueError, TypeError) as exc:
        print(f"structure: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
