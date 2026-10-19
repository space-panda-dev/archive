#!/usr/bin/env python3
"""The rhythm extractor: beats, tempo, onsets, how tightly onsets sit on the
beat grid, and the tempo level, meter, and downbeats a musician would count
(ADR 0074).

It decodes the content's first audio stream as mono 32-bit floats at
44,100 Hz and measures it in chunks of `chunk_seconds`, each overlapping the
next by `overlap_seconds`:

- Essentia's RhythmExtractor2013 (`multifeature`, BeatTrackerMultiFeature)
  gives each chunk's beats, tempo, and confidence;
- Essentia's OnsetRate gives each chunk's onsets.

Each chunk keeps what falls between the middles of its overlaps with its
neighbours, so no beat or onset comes from the last or first seconds a
tracker saw; where two chunks meet, a beat closer to the one before it than
the fastest tempo allows is dropped. Chunks keep memory bounded (measured:
about 680 MB at the peak for 10 and for 30 minutes, where one piece takes
610 MB for 10 minutes and 760 MB for 20 and keeps growing, since Essentia's
trackers hold several detection functions of the whole signal) and give a
long recording of many songs a confidence per chunk rather than one number
for all of it.

The two algorithms keep different time bases. OnsetRate reports an onset
before the attack it marks: measured on synthetic hits at random positions,
the median is 14.6 ms early for a 1.5 kHz click, 13.7 for a noise burst,
11.7 for a kick, 9.0 for a tone rising in 5 ms and 3.6 for one rising in
20 ms, each spread over about 11 ms (its 512-sample hop). RhythmExtractor2013
places a whole call's ticks about 15 ms early or 15 ms late, the sign
depending on the input's tempo and length, not on the playing. Left alone,
perfectly quantised eighths read as up to 30 ms off the grid (offgrid 0.19
to 0.27 at 100 to 140 BPM, where 0.25 is random). So each
chunk's ticks are moved by the mean distance from a tick to the nearest
onset within `beat_align_ms` (with at least `ALIGN_MINIMUM` such
pairs; otherwise by the previous chunk's shift, or not at all): the beats
are then on the onsets' time base, early by the same attack-dependent lead,
and an onset on a beat sits on its beat. Neither is corrected to the true
attack, since how early depends on the sound.

OnsetRate also reports an onset at its first hop wherever the decoded audio
does not start in digital silence (the step from its zero padding into the
signal), so onsets in the first `edge_onset_ms` are dropped.

Beat trackers find beats where nothing is played: on digital silence
RhythmExtractor2013 reports 738 BPM with confidence 5.0, its maximum, and
around playing it carries the pulse on before the first onset and after
the last. So a beat is kept only if an onset lies within
`beat_align_ms` of it, or onsets lie within `beat_gate_seconds` on both
sides of it; a chunk that keeps no onset has confidence 0.

Why it does not read the `frames` series: both Essentia algorithms require
audio at 44,100 Hz and compute their own detection functions with hops of
512 and 1,024 samples (12 and 23 ms), while `frames` is 22,050 Hz audio cut
every 2,048 samples (93 ms), coarser than the deviations `onset_deviation`
measures. Decoding is under a tenth of the cost; the trackers are the rest.

Outputs:

- `beats`, `onsets`: events, in seconds from the first decoded sample, on
  OnsetRate's time base (an onset is 4 to 15 ms before its attack, see
  above; the beats are aligned to the onsets);
- on the `seconds` grid (a row a second, each summarising the
  `window_seconds` centred on it):
  `tempo` (BPM, 60 over the median beat interval whose middle is in the
  window; 0 where there is none), `onset_count` (onsets in the window that
  lie between two kept beats), `onset_deviation` (milliseconds: the mean
  absolute and the mean signed distance from each such onset to the
  nearest point of the beat grid divided into `subdivisions`; positive is
  late; since the beats are aligned to the onsets on them, the signed
  mean says whether the onsets between beats come later or earlier than
  even division of the played beats, as in swing), and `offgrid` (the mean
  absolute distance as a fraction of the subdivision's length: about 0.03
  for onsets placed exactly on the grid, where both algorithms' 512-sample
  steps remain, 0.25 for onsets placed at random, 0.5 at most). Means,
  not medians: when half the onsets are the beats themselves, a median
  hides how far the other half stray, and each distance is bounded by
  half a subdivision, so no outlier dominates.
  Essentia's onsets and beats both fall on 512-sample (11.6 ms) steps, so
  a single distance is resolved to about that;
- on the `chunks` grid (a row a chunk): `chunk_bpm` and `chunk_confidence`,
  RhythmExtractor2013's own, confidence from 0 to 5.32 (above 3.5 is
  excellent by Essentia's guide); the last row's chunk may be shorter than
  the grid's `frame`, down to just over `overlap_seconds`;
- scalars: `bpm` (60 over the median of every kept beat interval),
  `confidence` (the chunks' confidences weighted by the seconds each keeps),
  `onset_rate` (onsets a second), and `seconds`.

A tracker follows one pulse and chooses it window by window: on dense
playing it locks to the eighths (2:1 of the counted beat), to a
cross-rhythm (3:2, 4:3), or to the beat, and changes its mind between
sections, so `bpm` can be a level never played and a change of feel at a
constant pulse (4/4 to 12/8) reads as a change of tempo. Measured on an
album of dense, odd-metered music against Spotify's tempi and human Guitar
Pro transcriptions, `bpm` was 3:2 or 4:3 of the reference on 4 of 17
tracks. So `meter.py` (see there) also measures, from each chunk's spectral
rows (spectral flux, its part below 200 Hz, and a chroma, from 2,048-sample
frames every 512 samples, kept for the chunk's kept range only, about 17 MB
a recording-hour) and the gated beats:

- `primary_tempo` (seconds grid, BPM): the level each second among 1:2, 2:3,
  3:4, 1:1, 4:3, 3:2 and 2:1 of the tracker's tempo with the most onset
  support (the mean accent on the level's pulses at their best phase)
  times a log-normal preference for tempi near `prior_bpm` (`prior_octaves`
  wide) and one centred on the tracker's own tempo (`tracker_octaves`
  wide), through a path that charges `level_switch_cost` per octave of
  change and starts afresh after a break without beats between takes;
  `tempo_support` (seconds grid, 7 columns): the support of 1:2, 2:3, 3:4,
  1:1, 4:3, 3:2 and 2:1 of that primary, the alternatives a reader may
  prefer;
- `pulse` (events): the primary level's beats, each with its place in its
  bar (1 the downbeat), or 0 where the bar's place is a guess;
  `downbeats` (events): the bar lines, each with the bar's length in beats
  and its meter, only where they are clearly placed; `downbeat_margin`
  (seconds grid): how clearly;
- `tempo_spans` (spans): stretches of one tempo level (within 8%) and one
  meter (`4/4`, `3/4`, `5/4`, `7/4`, or with a ternary division of the beat
  `12/8`, `9/8`, `15/8`, `21/8`); `tempo_changes` (events): where one span
  follows another, labelled `meter` when the beat or one of its
  subdivisions persists across it (only the grouping changed) and `tempo`
  otherwise;
- scalars `primary_bpm` and `meter`, the level and the meter covering the
  most time.

Octaves are a preference, not a measurement. Halving a level never loses
onset support (every other pulse of a supported level is as supported), and
doubling it loses only what the off-beats lack, so the support says which
cross-rhythm is played but hardly which octave: on the album, the doubled
level kept 57 to 93% of the counted level's support, and the counted level
75 to 92% of its half's, ranges that overlap. The preference near `prior_bpm`
decides, and the tracker's own octave counts as evidence beside it (a level
an octave from the tracker needs about 13% more), which keeps a ballad at
66 BPM with eighths at 66 where the preference alone made it 132. On the
synthetic band this holds down to about 64 BPM; slower ballads with eighths
are still read doubled, so a collection of slow material wants a lower
`prior_bpm`. A change of level costs `level_switch_cost` per octave, but
only within a take: after RESET_SECONDS without a tracker tempo (a break of
at least `window_seconds` plus that), the path starts afresh, so a take's
level does not depend on the take before it.

The meter is how far apart the beat-by-beat pattern (where the harmony
moves, the accents and the low accents) repeats; in a song whose bars are
not all of one length, a bar of 5 is preferred to one of 4 where the two
repeat about as well. On the album, beats per bar agreed with Spotify's
time signature on 16 of 17 tracks (15 before that preference; always
answering 4 agrees on 14), and with a transcription's bars on 99 to 100%
of three 4/4 songs' bars but 62% of one whose 4/4 turns to 5/4 (38%
before). Same-tempo changes between 4/4 and 12/8 on three tracks are
unverified.

The downbeats are where the bar-position model puts the evidence of a bar
line, at every beat: the low accent, less half the whole accent (the
backbeat's snare is loud but not low), and half each of how far the
beat-synchronous chroma moves at the beat and across a bar (see
`meter.downbeat_evidence`); a bar may end early (a bar of 2/4 in 4/4) at a
cost. Dense playing puts low accents and moves of the harmony on every
beat, so each bar also gets a margin: over the four bars either side, the
mean evidence on the bars' first beats less the largest on any other beat
(robust z units). On the synthetic band, whose bar lines carry a kick and
a chord change, it is 2 to 4; over four-on-the-floor and a held chord it
stays below 1. Only bars with a margin of at least `downbeat_margin` are
published as downbeats or give their pulses a place. Measured on the
album against a neural tracker's downbeats (Beat This), where our
downbeat falls on one of its beats: at the default of 1, 452 downbeats on
17 of 18 tracks, 81% on its downbeats (version 3: 145 on 8 tracks at a
margin of 2, 94%); at 2, 165 on 9 tracks, 96%; every bar, 63% (version 3:
56%). The rest are about as often one beat off as half a bar off (beat 3
for beat 1), so a reader wanting fewer and surer bar lines sets
`downbeat_margin` to 2.

Invoked by the runner as `rhythm.py <request.json>`. The settings in
`TUNABLE` may be configured; any other differing setting is refused.
"""

from __future__ import annotations

import importlib.metadata
import json
import platform
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

import numpy

import meter

RATE = 44100
ESSENTIA_BUILD = "2.1b6.dev1438"
SECOND = RATE
# Fewest tick-to-onset pairs a chunk's alignment shift is measured from.
ALIGN_MINIMUM = 8
TUNABLE = {"min_tempo": (40, 180), "max_tempo": (60, 250), "subdivisions": (1, 12), "window_seconds": (2, 120),
           "chunk_seconds": (60, 3600), "overlap_seconds": (10, 600), "beat_gate_seconds": (1, 30),
           "prior_bpm": (60, 180), "tracker_octaves": (1, 10), "level_switch_cost": (0, 100),
           "downbeat_margin": (0, 10)}


class RhythmError(Exception):
    pass


def decode_arguments(path: str) -> list[str]:
    """The ffmpeg arguments after the program name: the first audio stream,
    whole, mono, 32-bit float at 44,100 Hz."""
    return ["-nostdin", "-hide_banner", "-nostats", "-loglevel", "error", "-i", path, "-map", "0:a:0", "-ac", "1",
            "-ar", str(RATE), "-c:a", "pcm_f32le", "-f", "f32le", "-"]


def parameters() -> dict[str, Any]:
    """The default settings, which the manifest must declare exactly."""
    return {"decoder": "ffmpeg", "decode_arguments": decode_arguments("{input}"), "essentia": ESSENTIA_BUILD,
            "analysis_rate": RATE, "beats": "RhythmExtractor2013", "method": "multifeature", "min_tempo": 40,
            "max_tempo": 208, "onsets": "OnsetRate", "chunk_seconds": 300, "overlap_seconds": 30,
            "beat_gate_seconds": 2, "beat_align_ms": 50, "edge_onset_ms": 25,
            "window_seconds": 10, "subdivisions": 4, "accents": "spectral flux", "accent_frame": meter.FRAME,
            "accent_hop": meter.HOP, "prior_bpm": 120, "prior_octaves": 1, "tracker_octaves": 2,
            "level_switch_cost": 20, "downbeat_margin": 1,
            "meters": list(meter.METERS)}


def settings(requested: Any) -> dict[str, Any]:
    """The request's parameters, if this program implements them: the
    defaults with only `TUNABLE` fields changed, each an integer in range,
    and a chunk long enough to keep something after its overlaps."""
    defaults = parameters()
    if not isinstance(requested, dict) or set(requested) != set(defaults):
        raise RhythmError("the request's parameters are not rhythm's")
    for name, value in requested.items():
        if name in TUNABLE:
            low, high = TUNABLE[name]
            if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
                raise RhythmError(f"{name} must be an integer from {low} to {high}, not {value!r}")
        elif value != defaults[name]:
            raise RhythmError(f"rhythm implements {name} {defaults[name]!r} only, not {value!r}")
    if requested["min_tempo"] >= requested["max_tempo"]:
        raise RhythmError("min_tempo must be below max_tempo")
    if 2 * requested["overlap_seconds"] >= requested["chunk_seconds"]:
        raise RhythmError("a chunk must be more than twice its overlap")
    return requested


def essentia_standard() -> Any:
    """`essentia.standard`, refused unless it is the build the settings name."""
    try:
        import essentia
        essentia.log.infoActive = False
        import essentia.standard as standard
    except ImportError as exc:
        raise RhythmError(f"Essentia is not installed: {exc}") from exc
    installed = importlib.metadata.version("essentia")
    if installed != ESSENTIA_BUILD:
        raise RhythmError(f"Essentia {installed} is installed; rhythm names {ESSENTIA_BUILD}")
    return standard


# -- decoding and chunking ---------------------------------------------------

def decoded_blocks(path: str, ffmpeg: str = "ffmpeg", block_bytes: int = 1 << 22) -> Iterator[numpy.ndarray]:
    """The first audio stream as float32 blocks of samples, in order."""
    with tempfile.TemporaryFile() as errors:
        try:
            process = subprocess.Popen([ffmpeg, *decode_arguments(path)], stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=errors)
        except OSError as exc:
            raise RhythmError(f"ffmpeg could not run: {exc}") from exc
        assert process.stdout is not None
        carry = b""
        try:
            while chunk := process.stdout.read(block_bytes):
                chunk = carry + chunk
                usable = len(chunk) - len(chunk) % 4
                carry = chunk[usable:]
                if usable:
                    yield numpy.frombuffer(chunk[:usable], dtype="<f4")
        finally:
            process.stdout.close()
            returncode = process.wait()
        if returncode != 0 or carry:
            errors.seek(0)
            raise RhythmError(f"ffmpeg could not decode {path}: "
                              f"{errors.read().decode('utf-8', 'replace').strip()[:300]}")


@dataclass(frozen=True)
class Chunk:
    index: int
    start: int  # the sample its audio starts at
    keep_from: int  # the samples whose beats and onsets it keeps: [keep_from, keep_to)
    keep_to: int
    audio: numpy.ndarray = field(repr=False)


def chunks(blocks: Iterable[numpy.ndarray], length: int, overlap: int) -> Iterator[Chunk]:
    """The concatenated blocks as chunks of `length` samples starting every
    `length - overlap`, the last running to the end, in one buffer of a
    chunk's length filled in place, so memory does not grow with the
    recording (or with the number of blocks, as concatenating would). A
    chunk keeps from the middle of its overlap with the previous chunk (the
    start, for the first) to the middle of its overlap with the next (the
    end, for the last), so the kept ranges tile the audio. n samples make
    one chunk if n <= length, otherwise ceil((n - length) / (length -
    overlap)) + 1. Each chunk's audio is its own copy."""
    step = length - overlap
    half = overlap // 2
    buffer = numpy.empty(length, dtype=numpy.float32)
    filled = 0
    pending = numpy.zeros(0, dtype=numpy.float32)
    source = iter(blocks)
    start = index = 0

    def more() -> bool:
        """Whether any sample is left, moving the next non-empty block into `pending`."""
        nonlocal pending
        while not len(pending):
            block = next(source, None)
            if block is None:
                return False
            pending = numpy.asarray(block, dtype=numpy.float32)
        return True

    while True:
        while filled < length and more():
            taken = min(length - filled, len(pending))
            buffer[filled:filled + taken] = pending[:taken]
            pending = pending[taken:]
            filled += taken
        if filled < length or not more():
            if filled and (index == 0 or filled > overlap):
                yield Chunk(index, start, start + half if index else 0, start + filled, buffer[:filled].copy())
            return
        yield Chunk(index, start, start + half if index else 0, start + step + half, buffer.copy())
        buffer[:overlap] = buffer[step:]
        filled = overlap
        start += step
        index += 1


# -- measuring ---------------------------------------------------------------

@dataclass
class Measured:
    """What the chunks found, in seconds from the first sample."""
    beats: list[float] = field(default_factory=list)
    onsets: list[float] = field(default_factory=list)
    chunk_bpm: list[float] = field(default_factory=list)
    chunk_confidence: list[float] = field(default_factory=list)
    chunk_kept_seconds: list[float] = field(default_factory=list)
    chunk_onsets: list[int] = field(default_factory=list)
    chunk_shift: list[float] = field(default_factory=list)  # seconds each chunk's ticks were moved
    rows: list[meter.Rows] = field(default_factory=list)  # each chunk's spectral rows in its kept range
    shift: float = 0.0  # the latest measured shift, for a chunk too sparse to measure its own
    samples: int = 0


def kept(times: Any, chunk: Chunk) -> list[float]:
    """The chunk's times (seconds from its own start) that fall in its kept
    range, as seconds from the recording's start."""
    begin, end = chunk.keep_from / RATE, chunk.keep_to / RATE
    offset = chunk.start / RATE
    return [offset + float(t) for t in times if begin <= offset + float(t) < end]


def join_beats(before: list[float], after: list[float], shortest: float) -> list[float]:
    """Beats of the next chunk appended to those before it, dropping the
    first beats of the next chunk that come sooner than `shortest` seconds
    after the last beat kept: two trackers meeting at a seam may each put a
    beat near it."""
    index = 0
    while before and index < len(after) and after[index] - before[-1] < shortest:
        index += 1
    return before + after[index:]


def nearest(times: numpy.ndarray, at: numpy.ndarray) -> numpy.ndarray:
    """For each of `at`, the signed distance to the nearest of the sorted,
    non-empty `times`."""
    index = numpy.searchsorted(times, at)
    before = times[numpy.clip(index - 1, 0, len(times) - 1)] - at
    after = times[numpy.clip(index, 0, len(times) - 1)] - at
    return numpy.where(numpy.abs(before) <= numpy.abs(after), before, after)


def alignment(ticks: Any, onsets: Any, reach: float) -> float | None:
    """The mean distance from a tick to the nearest onset, over the ticks
    with one within `reach` seconds; None with fewer than `ALIGN_MINIMUM`."""
    ticks, onsets = numpy.asarray(ticks, dtype=numpy.float64), numpy.asarray(onsets, dtype=numpy.float64)
    if not len(ticks) or not len(onsets):
        return None
    distance = nearest(onsets, ticks)
    close = distance[numpy.abs(distance) <= reach]
    return float(numpy.mean(close)) if len(close) >= ALIGN_MINIMUM else None


def measure(pieces: Iterable[Chunk], config: dict[str, Any], es: Any) -> Measured:
    extractor = es.RhythmExtractor2013(method=config["method"], minTempo=config["min_tempo"],
                                       maxTempo=config["max_tempo"])
    onset_rate = es.OnsetRate()
    shortest = 60.0 / config["max_tempo"]
    found = Measured()
    for chunk in pieces:
        found.samples = chunk.start + len(chunk.audio)
        # OnsetRate keeps state between calls: unreset, a second chunk has no onsets.
        extractor.reset()
        onset_rate.reset()
        bpm, ticks, confidence, _, _ = extractor(chunk.audio)
        onsets, _ = onset_rate(chunk.audio)
        onsets = numpy.asarray(onsets, dtype=numpy.float64)
        if chunk.start == 0:
            onsets = onsets[onsets >= config["edge_onset_ms"] / 1000]
        shift = alignment(ticks, onsets, config["beat_align_ms"] / 1000)
        if shift is not None:
            found.shift = shift
        found.chunk_shift.append(found.shift)
        chunk_onsets = kept(onsets, chunk)
        found.onsets.extend(chunk_onsets)
        found.beats = join_beats(found.beats, kept(numpy.asarray(ticks, dtype=numpy.float64) + found.shift, chunk),
                                 shortest)
        found.chunk_bpm.append(float(bpm))
        # A tracker scores digital silence 5.0; a chunk that keeps no onset has no beat to be sure of.
        found.chunk_confidence.append(float(confidence) if chunk_onsets else 0.0)
        found.chunk_kept_seconds.append((chunk.keep_to - chunk.keep_from) / RATE)
        found.chunk_onsets.append(len(chunk_onsets))
        # The spectral rows centred in the kept range; the overlap gives each its whole frame.
        found.rows.append(meter.spectral_rows(chunk.audio, chunk.start, -(-chunk.keep_from // meter.HOP),
                                              -(-chunk.keep_to // meter.HOP)))
    return found


def gate(beats: list[float], onsets: list[float], reach: float, on: float) -> list[float]:
    """The beats with an onset within `on` seconds of them, or with onsets
    within `reach` seconds both before and after them: not the pulse a
    tracker carries on into silence before the first onset or after the
    last."""
    if not onsets or not beats:
        return []
    times = numpy.asarray(onsets, dtype=numpy.float64)
    at = numpy.asarray(beats, dtype=numpy.float64)
    index = numpy.searchsorted(times, at)
    previous = numpy.where(index > 0, times[numpy.maximum(index - 1, 0)], -numpy.inf)
    following = numpy.where(index < len(times), times[numpy.minimum(index, len(times) - 1)], numpy.inf)
    near = numpy.minimum(at - previous, following - at) <= on
    between = (at - previous <= reach) & (following - at <= reach)
    return [float(b) for b in at[near | between]]


def intervals(beats: list[float], longest: float) -> list[tuple[float, float]]:
    """Consecutive beat pairs no further apart than `longest` seconds (the
    slowest tempo): a longer gap is where the gate removed beats, not a beat."""
    return [(a, b) for a, b in zip(beats, beats[1:]) if 0 < b - a <= longest]


def deviations(onsets: list[float], pairs: list[tuple[float, float]], subdivisions: int) -> list[tuple[float, float, float]]:
    """For each onset between the two beats of a pair: its time, its signed
    distance in seconds to the nearest of the `subdivisions` + 1 points that
    divide the pair evenly (positive when the onset is late), and that
    distance as a fraction of one division."""
    found = []
    starts = numpy.asarray([a for a, _ in pairs])
    for onset in onsets:
        index = int(numpy.searchsorted(starts, onset, side="right")) - 1
        if index < 0:
            continue
        a, b = pairs[index]
        if not a <= onset < b:
            continue
        division = (b - a) / subdivisions
        position = (onset - a) / division
        signed = (position - round(position)) * division
        found.append((onset, signed, abs(signed) / division))
    return found


def windows(samples: int, window: int) -> tuple[int, numpy.ndarray, numpy.ndarray]:
    """The `seconds` grid: a row a second, centred on second k, and each
    row's window [k - window / 2, k + window / 2) in seconds."""
    rows = -(-samples // SECOND)
    centres = numpy.arange(rows, dtype=numpy.float64)
    return rows, centres - window / 2, centres + window / 2


def window_sums(times: numpy.ndarray, values: numpy.ndarray, low: numpy.ndarray,
                high: numpy.ndarray) -> tuple[numpy.ndarray, numpy.ndarray]:
    """For each window [low, high): how many of the sorted `times` fall in
    it, and the sums of each column of `values` over them, by cumulative
    sums, so the cost is linear in rows and times."""
    first = numpy.searchsorted(times, low, side="left")
    last = numpy.searchsorted(times, high, side="left")
    totals = numpy.vstack((numpy.zeros((1, values.shape[1])), numpy.cumsum(values, axis=0)))
    return last - first, totals[last] - totals[first]


def series(found: Measured, beats: list[float], config: dict[str, Any]) -> dict[str, numpy.ndarray]:
    rows, low, high = windows(found.samples, config["window_seconds"])
    pairs = intervals(beats, 60.0 / config["min_tempo"])
    middles = numpy.asarray([(a + b) / 2 for a, b in pairs], dtype=numpy.float64)
    lengths = numpy.asarray([b - a for a, b in pairs], dtype=numpy.float64)
    measured = deviations(found.onsets, pairs, config["subdivisions"])
    onset_times = numpy.asarray([m[0] for m in measured], dtype=numpy.float64)
    signed = numpy.asarray([m[1] for m in measured], dtype=numpy.float64) * 1000
    fraction = numpy.asarray([m[2] for m in measured], dtype=numpy.float64)
    tempo = numpy.zeros((rows, 1), dtype="<f4")
    first = numpy.searchsorted(middles, low, side="left")
    last = numpy.searchsorted(middles, high, side="left")
    for k in numpy.flatnonzero(last > first):
        tempo[k, 0] = 60.0 / float(numpy.median(lengths[first[k]:last[k]]))
    columns = numpy.column_stack((numpy.abs(signed), signed, fraction)) if len(measured) else numpy.zeros((0, 3))
    counts, sums = window_sums(onset_times, columns, low, high)
    means = sums / numpy.maximum(counts, 1)[:, None]
    count = counts.astype("<f4").reshape(-1, 1)
    deviation = means[:, :2].astype("<f4")
    offgrid = means[:, 2:].astype("<f4")
    return {"tempo": tempo, "onset_count": count, "onset_deviation": deviation, "offgrid": offgrid,
            "chunk_bpm": numpy.asarray(found.chunk_bpm, dtype="<f4").reshape(-1, 1),
            "chunk_confidence": numpy.asarray(found.chunk_confidence, dtype="<f4").reshape(-1, 1)}


def scalars(found: Measured, beats: list[float], config: dict[str, Any]) -> dict[str, float]:
    pairs = intervals(beats, 60.0 / config["min_tempo"])
    seconds = found.samples / RATE
    weight = sum(found.chunk_kept_seconds)
    return {"bpm": 60.0 / float(numpy.median([b - a for a, b in pairs])) if pairs else 0.0,
            "confidence": sum(c * w for c, w in zip(found.chunk_confidence, found.chunk_kept_seconds)) / weight,
            "onset_rate": len(found.onsets) / seconds, "seconds": seconds}


def events(times: list[float]) -> list[dict[str, float]]:
    return [{"time": round(t, 6)} for t in times]


def per_second(times: numpy.ndarray, values: numpy.ndarray, rows: int, longest: float) -> numpy.ndarray:
    """For each second k, the value of the last of the sorted `times` at or
    before k, if it is no more than `longest` seconds before; 0 otherwise."""
    found = numpy.zeros(rows)
    if not len(times):
        return found
    seconds = numpy.arange(rows, dtype=numpy.float64)
    index = numpy.searchsorted(times, seconds, side="right") - 1
    near = (index >= 0) & (seconds - times[numpy.maximum(index, 0)] <= longest)
    found[near] = numpy.asarray(values, numpy.float64)[index[near]]
    return found


def metrical(found: Measured, beats: list[float], tempo: numpy.ndarray,
             config: dict[str, Any]) -> tuple[dict[str, numpy.ndarray], dict[str, list[dict[str, Any]]],
                                              dict[str, Any]]:
    """The tempo levels, meter and downbeats (see `meter`), as the series,
    the events and spans, and the scalars rhythm writes."""
    found_levels = meter.analyse(beats, tempo, meter.Rows.join(found.rows), config)
    starts = numpy.flatnonzero(found_levels.position == 0)
    lengths = meter.bar_lengths(found_levels.position)
    # A bar's place is published only where its downbeat is clearly placed: elsewhere another phase of the bar
    # fits the evidence about as well, and a bar line one or two beats off misleads more than none.
    placed = found_levels.margin >= config["downbeat_margin"]
    items = {
        "pulse": [{"time": round(float(t), 6), "value": int(p) + 1 if sure else 0}
                  for t, p, sure in zip(found_levels.pulse, found_levels.position, placed)],
        "downbeats": [{"time": round(float(found_levels.pulse[i]), 6), "value": int(n),
                       "label": found_levels.labels[i]} for i, n in zip(starts, lengths) if placed[i]],
        "tempo_spans": [{"start": round(span.start, 6), "end": round(span.end, 6), "value": round(span.bpm, 3),
                         "label": span.label} for span in found_levels.spans],
        "tempo_changes": found_levels.changes}
    rows_out = {"primary_tempo": found_levels.primary.astype("<f4").reshape(-1, 1),
                "tempo_support": found_levels.alternatives.astype("<f4"),
                "downbeat_margin": per_second(found_levels.pulse, found_levels.margin, len(tempo),
                                              60.0 / config["min_tempo"]).astype("<f4").reshape(-1, 1)}
    return rows_out, items, {"primary_bpm": found_levels.primary_bpm, "meter": found_levels.meter}


def ffmpeg_version(ffmpeg: str = "ffmpeg") -> str:
    """The version `ffmpeg -version` names; refused rather than recorded as
    unknown when it names none, since the provenance would then be false."""
    answer = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True)
    first = answer.stdout.split("\n", 1)[0].split()
    if answer.returncode != 0 or len(first) < 3 or first[:2] != ["ffmpeg", "version"]:
        raise RhythmError(f"`{ffmpeg} -version` names no version (exit {answer.returncode})")
    return first[2]


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: rhythm.py <request.json>", file=sys.stderr)
        return 2
    try:
        request = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
        config = settings(request.get("parameters"))
        es = essentia_standard()
        output = Path(request["output_dir"])
        found = measure(chunks(decoded_blocks(request["input"]["path"]), config["chunk_seconds"] * RATE,
                               config["overlap_seconds"] * RATE), config, es)
        if found.samples == 0:
            raise RhythmError(f"{request['input']['path']} decodes to no samples")
        beats = gate(found.beats, found.onsets, config["beat_gate_seconds"], config["beat_align_ms"] / 1000)
        outputs: dict[str, Any] = {}
        rows = series(found, beats, config)
        level_rows, level_items, level_scalars = metrical(found, beats, rows["tempo"], config)
        for name, values in {**rows, **level_rows}.items():
            (output / f"{name}.f32").write_bytes(values.tobytes())
            outputs[name] = {"shape": list(values.shape)}
        for name, items in {"beats": events(beats), "onsets": events(found.onsets), **level_items}.items():
            (output / f"{name}.json").write_text(json.dumps(items))
            outputs[name] = {}
        for name, value in {**scalars(found, beats, config), **level_scalars}.items():
            outputs[name] = {"value": value}
        chunk_samples = config["chunk_seconds"] * RATE
        step = chunk_samples - config["overlap_seconds"] * RATE
        grids = {"seconds": {"sample_rate": RATE, "hop": SECOND, "frame": config["window_seconds"] * SECOND,
                             "first_centre": 0},
                 "chunks": {"sample_rate": RATE, "hop": step, "frame": chunk_samples,
                            "first_centre": chunk_samples // 2}}
        software = [{"name": "ffmpeg", "version": ffmpeg_version()},
                    {"name": "essentia", "version": importlib.metadata.version("essentia")},
                    {"name": "numpy", "version": numpy.__version__},
                    {"name": "python", "version": platform.python_version()}]
        result = {"contract": "archive-extractor-result/1", "software": software, "grids": grids,
                  "outputs": outputs}
        (output / "result.json").write_text(json.dumps(result))
    except (RhythmError, OSError, KeyError, ValueError, RuntimeError) as exc:
        print(f"rhythm: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
