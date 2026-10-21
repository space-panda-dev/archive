#!/usr/bin/env python3
"""The melody extractor: a pitch contour, its voicing, and its intonation (ADR 0074).

Version 2 resolved pitch in 2-cent bins rather than 10, gated voicing by
level (below), and made each row's level an output. Version 3 measures only
the rows within reach of the level gate, takes its bins from `bin_cents` (5
on whatever it measures, 2 on the vocal stem it prefers), and says what share
of the rows it measured (`measured_fraction`).

It measures the predominant melody of the content's first audio stream with
Essentia's MELODIA (`PredominantPitchMelodia`), on mono audio at 44,100 Hz
after Essentia's `EqualLoudness` filter, as its documentation advises, with
frames of 2,048 samples every `hop_size` samples; row k of every series is
centred on sample k x `hop_size`, and n samples make ceil(n / hop_size) + 1
rows, as the algorithms frame them. Writes, on the `melody` grid:

- `pitch`: the melody's fundamental frequency in Hz, 0 where unvoiced;
- `confidence`: the algorithm's confidence in it, 0 where unvoiced;
- `voiced`: 1 where MELODIA decides a melody sounds and the level gate
  (below) keeps it, 0 where it does not;
- `level_db`: the RMS level in dBFS of the decoded audio (before the
  equal-loudness filter) over the 2,048 samples centred on the row, samples
  outside the recording counting as silence, floored at -120;
- `midi`: the pitch as a fractional MIDI note number against the tuning
  reference (69 is the reference itself), 0 where unvoiced;
- `cents`: the pitch's deviation from the nearest equal-tempered note
  against the reference, -50 to 50, 0 where unvoiced;

and `notes` (spans in seconds, each valued with its MIDI note, by Essentia's
`PitchContourSegmentation` from the gated contour, on a second decode),
`voiced_fraction`, `melodia_voiced_fraction` (the share MELODIA voiced
before the gate), `measured_fraction` (the share of rows it measured; below),
`loud_db` and `gate_db` (below), `reference_hz`, and `samples` (the decoded
length at 44,100 Hz).

Pitch is quantised to MELODIA's salience bins (`binResolution`), `bin_cents`
cents apart on a grid anchored at 55 Hz: with Essentia's default of 10, every
cents value is a multiple of 10, too coarse for intonation. Finer bins cost
time: on 60 s of a studio mix and of its vocal stem, 5-cent bins took 1.1
times the time of 10, and 2-cent bins 1.5 and 1.7 times (version 2 used 2
everywhere). The default is 5, and 2 where the manifest's preference for the
vocal stem applies, since the sung line is what intonation is read from; any
value from 1 to 10 is accepted.

The tuning reference is the `reference_hz` parameter (440 Hz). It is not
read from a tuning extractor: an extractor's needs are fixed (ADR 0074), and
a reader holding a measured tuning t adds 12 log2(`reference_hz` / t) to
`midi` and takes cents from that. `PitchContourSegmentation` takes a whole number of
hertz, so the notes are quantised against the reference rounded to one;
`midi` and `cents` use it exactly.

It measures whatever audio it is given: the mix, or a stem when a stem is
the content. On a mix MELODIA follows the most salient line, which in a
band is as often a keyboard, a bass, or a horn as the voice. So its manifest
prefers the vocal stem (`"prefers"`, docs/feature-bank.md): where the bank
already holds a recording's `stems/vocals`, `extract.py run melody` (or
`all`) measures that stem, as `--from stems/vocals` does (ADR 0075), with
`bin_cents` 2, and files the vocal line's melody with the recording, keyed
by the stem's bits; `Bank.default_entry(recording, "melody")` reads it back.
Where it holds none, it measures the mix; a preference never separates.
`--no-prefer` measures the mix regardless. The program itself never knows
which it measures. The gate below is what makes a stem's melody usable: a
separated vocal is mostly near-silence with the band's bleed in it, and
MELODIA voices that bleed as readily as the voice.

MELODIA's own voicing decision is relative to the contours present, not to
level: a noise floor is mostly voiced (white noise at -40 dBFS between tones
came out 86 % voiced, and noise alone about 80 %), and the confidence scales
with level, so it does not separate them either; and its melody runs on from
a note into the noise after it without a gap. So the voiced rows are cut
into contours, wherever the pitch moves from one row to the next by more
than MELODIA's `pitchContinuity` allows inside one contour (27.5625 cents a
millisecond, 80 cents a row at the default stride), and a contour is kept
only if at least half its rows reach the gate: `gate_db` is the higher of
`voicing_gate.floor_db` and `loud_db` less `voicing_gate.range_db`, where
`loud_db` is the 99th percentile of the recording's row levels.

MELODIA is run only on the rows within `voicing_gate.reach_seconds` (1 s)
of a row that reaches the gate, since a contour with none of its rows near
one cannot keep half of them above it; the levels are measured first, on a
decode of their own, so the gate is known before MELODIA runs. Every other
row is unvoiced without being measured. This is where a vocal stem saves
time: its bleed is often more than 35 dB under the voice. On a master
nearly every row is within reach. A contour longer than the reach on both
sides of its loud rows is measured only near them, and each stretch of
measured rows is analysed on its own, so MELODIA's voicing, which is
relative to the contours in the audio it is given, is relative to that
stretch (as it is to a chunk's, below).

A whole contour is kept or dropped, so a sung note's quiet tail stays with
it; a contour that never rises out of the floor goes, and so does noise that
runs into a note's first frame (its loudest row would reach the gate; most
of its rows do not). The gate is relative, so a quiet recording is gated as
a loud one is, and the defaults (-60 dBFS, 35 dB) drop a noise floor more
than 35 dB under the loud parts: tones at -16 dBFS over white noise at -55
keep the tones and lose the noise (the test). Noise closer than that to the
music is not dropped, and a recording of nothing but noise sets its own loud
level, so it stays mostly voiced unless it is below the floor. Rows the gate
drops are 0 in `pitch`, `confidence`, `midi`, and `cents`, as every unvoiced
row is; `level_db` is there to gate again differently.

`guessUnvoiced` is held false, since it reports guessed pitches in frames
MELODIA judged unvoiced, with negative confidence, which `voiced` and
`confidence` would not describe.

Nothing here reads the `frames` series. MELODIA computes its own spectra
(44,100 Hz, a Hann window zero-padded four times, a hop of 128 samples, 2.9
ms), which a grid of 93 ms at 22,050 Hz cannot give, and the bank holds no
decoded audio, so this decodes the content again.

The audio is measured a chunk at a time, so memory for the audio does not
grow with the recording (the rows, 128 times fewer than the samples, are
held until the gate is known, about 20 MB a recording-hour): each stretch of
measured rows is cut into chunks of `chunk_seconds`, each analysed with
`margin_seconds` of audio either side within its stretch, and only its own
rows are kept, so a contour that crosses a chunk's edge is tracked whole
within the margin. MELODIA's thresholds are relative to the audio it is
given, so a chunked contour can differ from one measured on the whole
recording near quiet or busy passages; `chunk_seconds` 0 measures each
stretch at once, and with a gate every row reaches (`floor_db` -120 and
`range_db` 120) the whole recording, exactly as the algorithm does, at
about 4 bytes a sample of memory, twice. The audio is decoded three times
(levels, contour, notes); decoding is about 1 % of the time.

`hop_size` is the stride: 256 or 512 measure 2 or 4 times fewer frames, in
less time, with a coarser contour; the algorithms' continuity and duration
settings are in milliseconds, so they keep their meaning. The levels are
measured over whole hops of the decoded stream, not over each chunk, so they
do not depend on the chunking or on which rows are measured.

A region run (ADR 0112) is laid on the whole run's grid. Where the
request's input carries the `region` it lies at in the recording, the audio
is placed at that offset (rounded to a sample at 44,100 Hz) after silence
back to the whole run's row at or before it, so every row falls where the
whole run's does; a row before the region is dropped, the grid's
`first_centre` says where the first row lies on the cut's timeline, and
notes starting before the region are dropped. What stays the region's own:
`loud_db` and the gate are the region's, and its chunks start where its
measured rows do, so MELODIA's voicing, which is relative to the contours it
is given, can differ from the whole run's near the region's edges and where
the whole run's chunks cut it elsewhere.

Invoked by the runner as `melody.py <request.json>`.
"""

from __future__ import annotations

import importlib.metadata
import json
import platform
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable, Iterator

import numpy

VERSION = "3"
RATE = 44100
FRAME = 2048
LEVEL_FLOOR_DB = -120.0
LOUD_PERCENTILE = 99
ESSENTIA_BUILD = "2.1b6.dev1438"
HOPS = (64, 128, 256, 512, 1024)  # divisors of PitchContourSegmentation's 1,024-sample RMS hop
SERIES = {"pitch": "<f4", "confidence": "<f4", "voiced": "u1", "midi": "<f4", "cents": "<f4", "level_db": "<f4"}
DECIMAL = re.compile(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?")

# Essentia's defaults for each algorithm, as decimal text where they are not
# integers; frameSize, hopSize and sampleRate are set from the fields above.
# Essentia types each option: an INTEGER one takes only an integer (Essentia
# refuses a fraction there), a REAL one an integer or decimal text, and a
# BOOL one a boolean. The test reads the types from Essentia itself.
PREDOMINANT = {"filterIterations": 3, "guessUnvoiced": False, "harmonicWeight": "0.8",
               "magnitudeCompression": 1, "magnitudeThreshold": 40, "maxFrequency": 20000, "minDuration": 100,
               "minFrequency": 80, "numberHarmonics": 20, "peakDistributionThreshold": "0.9",
               "peakFrameThreshold": "0.9", "pitchContinuity": "27.5625", "referenceFrequency": 55,
               "timeContinuity": 100, "voiceVibrato": False, "voicingTolerance": "0.2"}
SEGMENTATION = {"minDuration": "0.1", "pitchDistanceThreshold": 60, "rmsThreshold": -2}
INTEGERS = {"predominant": {"filterIterations", "magnitudeThreshold", "minDuration", "numberHarmonics",
                            "timeContinuity"},
            "segmentation": {"pitchDistanceThreshold", "rmsThreshold"}}
FIXED_OPTIONS = {"guessUnvoiced": False}  # see the module's description
BIN_CENTS = (1.0, 10.0)  # MELODIA's binResolution; see the module's description
GATE = {"floor_db": "-60", "range_db": "35", "reach_seconds": "1"}
REACH_SECONDS = 10.0  # the most reach_seconds may be


class MelodyError(Exception):
    pass


def decode_arguments(path: str) -> list[str]:
    """The ffmpeg arguments after the program name: the first audio stream,
    whole, mono, at 44,100 Hz, as 16-bit samples."""
    return ["-nostdin", "-hide_banner", "-nostats", "-loglevel", "error", "-i", path, "-map", "0:a:0", "-ac", "1",
            "-ar", str(RATE), "-c:a", "pcm_s16le", "-f", "s16le", "-"]


def parameters() -> dict[str, Any]:
    """The default settings, which the manifest must declare exactly."""
    return {"decoder": "ffmpeg", "decode_arguments": decode_arguments("{input}"), "sample_scale": 32768,
            "essentia": ESSENTIA_BUILD, "analysis_rate": RATE, "frame_size": FRAME, "hop_size": 128,
            "equal_loudness": True, "bin_cents": 5, "predominant": PREDOMINANT, "segmentation": SEGMENTATION,
            "reference_hz": "440", "voicing_gate": GATE, "chunk_seconds": 120, "margin_seconds": 5}


# -- settings ------------------------------------------------------------------

def _number(value: Any, where: str) -> float | int | bool:
    if isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, str) and DECIMAL.fullmatch(value):
        return float(value)
    raise MelodyError(f"{where} must be an integer, a boolean, or decimal text, not {value!r}")


def _options(given: Any, defaults: dict[str, Any], where: str) -> dict[str, Any]:
    """The options as Essentia takes them: a boolean, an integer, or a float,
    by Essentia's type for each (INTEGERS)."""
    if not isinstance(given, dict) or set(given) != set(defaults):
        raise MelodyError(f"{where} must set exactly {sorted(defaults)}")
    options: dict[str, Any] = {}
    for name, value in given.items():
        default = defaults[name]
        if isinstance(default, bool):
            if not isinstance(value, bool):
                raise MelodyError(f"{where}.{name} must be a boolean, not {value!r}")
            if name in FIXED_OPTIONS and value != FIXED_OPTIONS[name]:
                raise MelodyError(f"{where}.{name} is fixed at {FIXED_OPTIONS[name]!r} in melody {VERSION}")
            options[name] = value
        elif name in INTEGERS[where]:
            if isinstance(value, bool) or not isinstance(value, int):
                raise MelodyError(f"{where}.{name} must be an integer, not {value!r}")
            options[name] = value
        else:
            number = _number(value, f"{where}.{name}")
            if isinstance(number, bool):
                raise MelodyError(f"{where}.{name} must be a number, not {value!r}")
            options[name] = float(number)
    return options


def settings(given: dict[str, Any]) -> dict[str, Any]:
    """The request's parameters as the settings the algorithms take. What can
    vary is the stride, the chunking, the reference, and the algorithms'
    own options; the decoder, rate, frame, and filter are fixed, and other
    values are refused rather than measured with settings they do not name."""
    defaults = parameters()
    if set(given) != set(defaults):
        raise MelodyError(f"melody {VERSION} takes exactly the parameters {sorted(defaults)}")
    for fixed in ("decoder", "decode_arguments", "sample_scale", "essentia", "analysis_rate", "frame_size",
                  "equal_loudness"):
        if given[fixed] != defaults[fixed]:
            raise MelodyError(f"melody {VERSION} has a fixed {fixed}: {defaults[fixed]!r}; "
                              f"the request names {given[fixed]!r}")
    hop = given["hop_size"]
    if hop not in HOPS or isinstance(hop, bool):
        raise MelodyError(f"hop_size must be one of {HOPS}, not {hop!r}")
    chunk, margin = given["chunk_seconds"], given["margin_seconds"]
    if not (isinstance(chunk, int) and not isinstance(chunk, bool) and chunk >= 0):
        raise MelodyError(f"chunk_seconds must be a whole number of seconds, 0 for the whole recording, not {chunk!r}")
    if not (isinstance(margin, int) and not isinstance(margin, bool) and margin >= 0):
        raise MelodyError(f"margin_seconds must be a whole number of seconds, not {margin!r}")
    if chunk and chunk < 2 * margin:
        raise MelodyError(f"a chunk of {chunk} s is shorter than its two margins of {margin} s")
    gate = given["voicing_gate"]
    if not isinstance(gate, dict) or set(gate) != set(GATE):
        raise MelodyError(f"voicing_gate must set exactly {sorted(GATE)}")
    floor, span, reach = (_number(gate[name], f"voicing_gate.{name}") for name in ("floor_db", "range_db",
                                                                                     "reach_seconds"))
    if not (isinstance(gate["floor_db"], str) and LEVEL_FLOOR_DB <= floor <= 0):
        raise MelodyError(f"voicing_gate.floor_db must be decimal text from {LEVEL_FLOOR_DB:g} to 0 dBFS, "
                          f"not {gate['floor_db']!r}")
    if not (isinstance(gate["range_db"], str) and 0 <= span <= -LEVEL_FLOOR_DB):
        raise MelodyError(f"voicing_gate.range_db must be decimal text from 0 to {-LEVEL_FLOOR_DB:g} dB, "
                          f"not {gate['range_db']!r}")
    if not (isinstance(gate["reach_seconds"], str) and 0 <= reach <= REACH_SECONDS):
        raise MelodyError(f"voicing_gate.reach_seconds must be decimal text from 0 to {REACH_SECONDS:g} s, "
                          f"not {gate['reach_seconds']!r}")
    reference = _number(given["reference_hz"], "reference_hz")
    if isinstance(reference, bool) or not 0 < reference < 22000:
        raise MelodyError(f"reference_hz must be a frequency, not {given['reference_hz']!r}")
    predominant = _options(given["predominant"], PREDOMINANT, "predominant")
    bins = _number(given["bin_cents"], "bin_cents")
    low, high = BIN_CENTS
    if isinstance(bins, bool) or not low <= bins <= high:
        raise MelodyError(f"bin_cents must be from {low:g} to {high:g} cents, not {given['bin_cents']!r}")
    predominant["binResolution"] = float(bins)
    return {"hop": hop, "chunk": (chunk * RATE // hop) * hop, "margin": (margin * RATE // hop) * hop,
            "reference": float(reference), "gate_floor": float(floor), "gate_range": float(span),
            "reach": int(round(float(reach) * RATE / hop)), "predominant": predominant,
            "segmentation": _options(given["segmentation"], SEGMENTATION, "segmentation")}


def essentia_standard() -> Any:
    try:
        import essentia
        essentia.log.infoActive = False
        essentia.log.warningActive = False
        import essentia.standard as standard
        installed = importlib.metadata.version("essentia")
    except (ImportError, importlib.metadata.PackageNotFoundError) as exc:
        raise MelodyError(f"Essentia is not importable here: {exc}") from exc
    if installed != ESSENTIA_BUILD:
        raise MelodyError(f"Essentia {installed} is installed; melody {VERSION} is measured with {ESSENTIA_BUILD}")
    return standard


# -- decoding and chunking -----------------------------------------------------

def decoded_blocks(path: str, ffmpeg: str = "ffmpeg", block_bytes: int = 1 << 20) -> Iterator[numpy.ndarray]:
    """The first audio stream, mono at 44,100 Hz, as float32 blocks of
    samples divided by 32,768, in order."""
    with tempfile.TemporaryFile() as errors:
        try:
            process = subprocess.Popen([ffmpeg, *decode_arguments(path)], stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=errors)
        except OSError as exc:
            raise MelodyError(f"ffmpeg could not run: {exc}") from exc
        assert process.stdout is not None
        carry = b""
        try:
            while chunk := process.stdout.read(block_bytes):
                chunk = carry + chunk
                usable = len(chunk) - len(chunk) % 2
                carry = chunk[usable:]
                if usable:
                    yield numpy.frombuffer(chunk[:usable], dtype="<i2").astype(numpy.float32) / numpy.float32(32768)
        finally:
            process.stdout.close()
            returncode = process.wait()
        if returncode != 0 or carry:
            errors.seek(0)
            raise MelodyError(f"ffmpeg could not decode {path}: "
                              f"{errors.read().decode('utf-8', 'replace').strip()[:300]}")


def offset_at(region: dict[str, int] | None, rate: int) -> int | None:
    """Where a region's audio starts on the recording's timeline, in samples
    at `rate`, to the nearest (half to even); None for whole content."""
    if region is None:
        return None
    whole, part = divmod(region["start"] * rate, region["rate"])
    return whole + (1 if 2 * part > region["rate"] or (2 * part == region["rate"] and whole % 2) else 0)


def led(blocks: Iterable[numpy.ndarray], lead: int) -> Iterator[numpy.ndarray]:
    """The blocks after `lead` samples of silence."""
    if lead:
        yield numpy.zeros(lead, dtype=numpy.float32)
    yield from blocks


def plan(measured: numpy.ndarray, samples: int, chunk: int, margin: int, hop: int
         ) -> list[tuple[int, int, int, int]]:
    """The audio to analyse: (start, end, first kept row, kept rows), in
    order. `measured` says which rows of the whole recording's grid (row k
    centred on sample k x hop) are measured; each run of them is cut into
    chunks of `chunk` samples' rows (0: one piece), each analysed from
    `margin` before its rows to `margin` after, within its run and the
    recording, so every chunk's rows lie on the grid and every measured row is
    kept exactly once. Rows outside every run are not analysed."""
    assert chunk % hop == 0 and margin % hop == 0
    pieces = []
    for low, high in spans(measured):
        stride = chunk // hop or high - low
        run_start, run_end = low * hop, min(samples, high * hop)
        for first in range(low, high, stride):
            last = min(high, first + stride)
            start = max(run_start, first * hop - margin)
            end = min(run_end, last * hop + margin)
            # Rows past the recording's last sample are measured from the audio before them.
            start = min(start, (end - 1) // hop * hop)
            pieces.append((start, end, first, last - first))
    return pieces


def cut(blocks: Iterable[numpy.ndarray], pieces: list[tuple[int, int, int, int]], samples: int
        ) -> Iterator[numpy.ndarray]:
    """Each piece's samples, [start, end), from the decoded blocks in order,
    holding no more than a piece and a block at a time. The decode must give
    the `samples` the first decode did."""
    buffer = numpy.zeros(0, dtype=numpy.float32)
    offset = 0  # the sample index of buffer[0]
    blocks = iter(blocks)
    pending: list[numpy.ndarray] = []
    have = 0  # samples decoded so far
    for start, end, _first, _count in pieces:
        while have < end:
            block = next(blocks, None)
            if block is None:
                break
            if have + len(block) <= start and not pending:
                offset = have = have + len(block)  # wholly before the piece: not kept
                buffer = buffer[:0]
                continue
            pending.append(block)
            have += len(block)
        buffer = numpy.concatenate([buffer, *pending]) if pending else buffer
        pending = []
        if start < offset:
            raise MelodyError(f"a piece from sample {start} comes after one from {offset}")
        if start > offset:
            buffer, offset = buffer[start - offset:], start
        if have < end:
            raise MelodyError(f"the audio decoded to {have} samples, not the {samples} it did before")
        yield buffer[:end - offset]
    for block in blocks:  # the rest, so the decoder is waited for and its failure seen
        have += len(block)
    if have != samples:
        raise MelodyError(f"the audio decoded to {have} samples, not the {samples} it did before")


def spans(rows: numpy.ndarray) -> list[tuple[int, int]]:
    """The [start, end) ranges of true rows."""
    edges = numpy.flatnonzero(numpy.diff(numpy.concatenate([[0], rows.astype(numpy.int8), [0]])))
    return list(zip(edges[::2].tolist(), edges[1::2].tolist()))


def reach(rows: numpy.ndarray, distance: int) -> numpy.ndarray:
    """The rows within `distance` rows of a true row."""
    count = numpy.concatenate([[0], numpy.cumsum(rows, dtype=numpy.int64)])
    k = numpy.arange(len(rows))
    return count[numpy.clip(k + distance + 1, 0, len(rows))] - count[numpy.clip(k - distance, 0, len(rows))] > 0


# -- measuring -----------------------------------------------------------------

class Melody:
    """The algorithm instances, configured once."""

    def __init__(self, es: Any, chosen: dict[str, Any]):
        self.hop = chosen["hop"]
        self.melodia = es.PredominantPitchMelodia(sampleRate=RATE, frameSize=FRAME, hopSize=self.hop,
                                                  **chosen["predominant"])
        self.equal_loudness = es.EqualLoudness(sampleRate=RATE)
        segmentation = chosen["segmentation"]
        self.segmentation = es.PitchContourSegmentation(
            sampleRate=RATE, hopSize=self.hop, tuningFrequency=int(round(chosen["reference"])),
            **segmentation)

    def measure(self, audio: numpy.ndarray) -> tuple[numpy.ndarray, numpy.ndarray]:
        """Pitch and confidence for every row of this audio."""
        self.equal_loudness.reset()
        pitch, confidence = self.melodia(self.equal_loudness(audio))
        pitch = numpy.asarray(pitch, dtype=numpy.float32)
        confidence = numpy.asarray(confidence, dtype=numpy.float32)
        rows = -(-len(audio) // self.hop) + 1
        if len(pitch) != rows or len(confidence) != rows:
            raise MelodyError(f"{len(audio)} samples made {len(pitch)} rows, not the {rows} the grid says")
        return pitch, confidence

    def notes(self, pitch: numpy.ndarray, audio: numpy.ndarray) -> list[tuple[float, float, float]]:
        """The notes of this audio's (gated) contour as (onset, duration, MIDI
        note) in seconds from the audio's start."""
        if not numpy.any(pitch > 0):
            return []
        onsets, durations, numbers = self.segmentation(numpy.ascontiguousarray(pitch, dtype=numpy.float32), audio)
        # Essentia values a voiced run of one or two rows -inf or NaN (a run
        # the gate's contour cuts can leave): no note can be read from it.
        return [(float(o), float(d), float(m)) for o, d, m in zip(onsets, durations, numbers) if numpy.isfinite(m)]


def intonation(pitch: numpy.ndarray, reference: float) -> tuple[numpy.ndarray, numpy.ndarray, numpy.ndarray]:
    """Voicing, the fractional MIDI note number against the reference, and
    the cents from the nearest equal-tempered note; 0 where unvoiced."""
    voiced = pitch > 0
    midi = numpy.zeros(pitch.shape, dtype=numpy.float64)
    midi[voiced] = 69 + 12 * numpy.log2(pitch[voiced].astype(numpy.float64) / reference)
    cents = numpy.zeros(pitch.shape, dtype=numpy.float64)
    cents[voiced] = 100 * (midi[voiced] - numpy.round(midi[voiced]))
    return voiced.astype(numpy.uint8), midi.astype(numpy.float32), cents.astype(numpy.float32)


class Energy:
    """The sum of squared samples in each hop of the decoded stream, taken as
    the blocks pass on their way to the chunks, so each row's level is the
    same however the audio is chunked."""

    def __init__(self, hop: int):
        self.hop = hop
        self.sums: list[numpy.ndarray] = []
        self.carry = numpy.zeros(0, dtype=numpy.float64)

    def tap(self, blocks: Iterable[numpy.ndarray]) -> Iterator[numpy.ndarray]:
        for block in blocks:
            joined = numpy.concatenate([self.carry, block.astype(numpy.float64)])
            whole = len(joined) - len(joined) % self.hop
            if whole:
                self.sums.append(numpy.square(joined[:whole]).reshape(-1, self.hop).sum(axis=1))
            self.carry = joined[whole:]
            yield block

    def hops(self) -> numpy.ndarray:
        """Each hop's sum, the last (partial) hop included: ceil(n / hop) values."""
        tail = [numpy.array([numpy.square(self.carry).sum()])] if len(self.carry) else []
        return numpy.concatenate(self.sums + tail) if self.sums or tail else numpy.zeros(0)


def row_levels(hop_sums: numpy.ndarray, rows: int, hop: int, frame: int = FRAME) -> numpy.ndarray:
    """The RMS level in dBFS of the `frame` samples centred on each row (row k
    on sample k x hop), from the hops' sums of squares; samples outside the
    recording are silence, and the level is floored at LEVEL_FLOOR_DB."""
    half = frame // 2 // hop
    assert half * 2 * hop == frame, "a frame is a whole, even number of hops"
    cumulative = numpy.concatenate([[0.0], numpy.cumsum(hop_sums)])
    k = numpy.arange(rows)
    low = numpy.clip(k - half, 0, len(hop_sums))
    high = numpy.clip(k + half, 0, len(hop_sums))
    mean_square = numpy.maximum(cumulative[high] - cumulative[low], 0.0) / frame
    floor = 10 ** (LEVEL_FLOOR_DB / 10)
    return 10 * numpy.log10(numpy.maximum(mean_square, floor))


def runs(pitch: numpy.ndarray, step_cents: float) -> list[tuple[int, int]]:
    """The [start, end) row ranges of contours: voiced rows in a row whose
    pitch moves by at most `step_cents` from one row to the next."""
    voiced = pitch > 0
    cents = numpy.where(voiced, 1200 * numpy.log2(numpy.where(voiced, pitch, 1.0).astype(numpy.float64)), 0.0)
    breaks = numpy.zeros(len(pitch), dtype=bool)
    breaks[1:] = voiced[1:] & voiced[:-1] & (numpy.abs(numpy.diff(cents)) > step_cents)
    starts = numpy.flatnonzero(voiced & (breaks | ~numpy.concatenate([[False], voiced[:-1]])))
    ends = numpy.concatenate([starts[1:], [len(pitch)]]) if len(starts) else starts
    # A contour ends at its next break or its first unvoiced row.
    unvoiced = numpy.flatnonzero(~voiced)
    found = []
    for start, limit in zip(starts.tolist(), ends.tolist()):
        after = unvoiced[numpy.searchsorted(unvoiced, start):]
        found.append((start, min(limit, int(after[0])) if len(after) else limit))
    return found


def loudness(level_db: numpy.ndarray, floor_db: float, range_db: float) -> tuple[float, float]:
    """`loud_db`, the recording's 99th-percentile row level, and `gate_db`,
    the higher of the floor and the loud level less the range."""
    loud = float(numpy.percentile(level_db, LOUD_PERCENTILE)) if len(level_db) else LEVEL_FLOOR_DB
    return loud, max(floor_db, loud - range_db)


def gate(pitch: numpy.ndarray, level_db: numpy.ndarray, gate_db: float, step_cents: float) -> numpy.ndarray:
    """The voicing the gate keeps: each contour of rows MELODIA voiced is
    kept whole if at least half its rows reach `gate_db` (see the module's
    description)."""
    kept = numpy.zeros(pitch.shape, dtype=bool)
    for start, end in runs(pitch, step_cents):
        if float(numpy.median(level_db[start:end])) >= gate_db:
            kept[start:end] = True
    return kept


def run(request: dict[str, Any], es: Any) -> dict[str, Any]:
    """Measure the request's audio and write every output; return the result."""
    chosen = settings(request["parameters"])
    reference = chosen["reference"]
    path = request["input"]["path"]
    melody = Melody(es, chosen)
    hop = chosen["hop"]
    output = Path(request["output_dir"])
    # A region's audio after the silence that puts its rows on the whole
    # run's grid; the row before the region is dropped at the end.
    offset = offset_at(request["input"].get("region"), RATE)
    lead = 0 if offset is None else offset % hop
    drop = 1 if lead else 0

    def decoded() -> Iterator[numpy.ndarray]:
        return led(decoded_blocks(path), lead)

    # First the levels, and from them the gate and the rows within its reach.
    energy = Energy(hop)
    samples = sum(len(block) for block in energy.tap(decoded()))
    if samples == lead:
        raise MelodyError(f"{path} decodes to no samples")
    rows = -(-samples // hop) + 1
    level_db = row_levels(energy.hops(), rows, hop)
    loud_db, gate_db = loudness(level_db[drop:], chosen["gate_floor"], chosen["gate_range"])
    measured = reach(level_db >= gate_db, chosen["reach"])
    pieces = plan(measured, samples, chosen["chunk"], chosen["margin"], hop)
    # Then MELODIA on those rows only; every other row is unvoiced.
    pitch = numpy.zeros(rows, dtype=numpy.float32)
    confidence = numpy.zeros(rows, dtype=numpy.float32)
    # cut() first, so that it is drawn to its end and checks the decode.
    for audio, (start, _end, first, count) in zip(cut(decoded(), pieces, samples), pieces):
        measured_pitch, measured_confidence = melody.measure(audio)
        skip = first - start // hop
        if skip < 0 or skip + count > len(measured_pitch):
            raise MelodyError(f"rows {first} to {first + count} are not in the audio from sample {start}")
        pitch[first:first + count] = measured_pitch[skip:skip + count]
        confidence[first:first + count] = measured_confidence[skip:skip + count]
    melodia_voiced = pitch > 0
    # MELODIA's pitchContinuity is the most a contour moves in a millisecond.
    step = chosen["predominant"]["pitchContinuity"] * hop * 1000 / RATE
    voiced = gate(pitch, level_db, gate_db, step)
    pitch = numpy.where(voiced, pitch, 0).astype(numpy.float32)
    confidence = numpy.where(voiced, confidence, 0).astype(numpy.float32)
    flags, midi, cents = intonation(pitch, reference)
    values = {"pitch": pitch, "confidence": confidence, "voiced": flags, "midi": midi, "cents": cents,
              "level_db": level_db}
    values = {name: value[drop:] for name, value in values.items()}
    for name, dtype in SERIES.items():
        (output / f"{name}.{'u8' if dtype == 'u1' else 'f32'}").write_bytes(
            numpy.ascontiguousarray(values[name], dtype=dtype).tobytes())
    # The notes are segmented from the gated contour, so the audio is decoded
    # again: the gate is known only once the whole recording is.
    notes = []
    for audio, (start, _end, first, count) in zip(cut(decoded(), pieces, samples), pieces):
        here = pitch[start // hop:start // hop + -(-len(audio) // hop) + 1]
        # A note belongs to the piece whose kept rows hold its onset.
        low, high = first * hop / RATE, (first + count) * hop / RATE
        for onset, duration, number in melody.notes(here, audio):
            onset += start / RATE
            if low <= onset < high and onset >= lead / RATE:
                onset -= lead / RATE
                notes.append({"start": onset, "end": onset + duration, "value": number})
    (output / "notes.json").write_text(json.dumps(notes))
    outputs: dict[str, Any] = {name: {"shape": [rows - drop, 1]} for name in SERIES}
    outputs.update({"notes": {}, "voiced_fraction": {"value": float(voiced[drop:].mean())},
                    "melodia_voiced_fraction": {"value": float(melodia_voiced[drop:].mean())},
                    "measured_fraction": {"value": float(measured[drop:].mean())},
                    "loud_db": {"value": loud_db}, "gate_db": {"value": gate_db},
                    "reference_hz": {"value": reference}, "samples": {"value": samples - lead}})
    software = [{"name": "ffmpeg", "version": ffmpeg_version()},
                {"name": "essentia", "version": importlib.metadata.version("essentia")},
                {"name": "numpy", "version": numpy.__version__},
                {"name": "python", "version": platform.python_version()}]
    return {"contract": "archive-extractor-result/1", "software": software,
            "grids": {"melody": {"sample_rate": RATE, "hop": hop, "frame": FRAME, "first_centre": drop * hop - lead}},
            "outputs": outputs}


def ffmpeg_version(ffmpeg: str = "ffmpeg") -> str:
    """The decoder's version, which the entry records; a decoder that does
    not say is a failure, not an entry with an unknown decoder."""
    try:
        first = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True, stdin=subprocess.DEVNULL
                               ).stdout.split("\n", 1)[0].split()
    except OSError as exc:
        raise MelodyError(f"ffmpeg could not run: {exc}") from exc
    if len(first) > 2 and first[:2] == ["ffmpeg", "version"]:
        return first[2]
    raise MelodyError(f"{ffmpeg} -version does not say which ffmpeg it is")


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: melody.py <request.json>", file=sys.stderr)
        return 2
    try:
        request = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
        result = run(request, essentia_standard())
        (Path(request["output_dir"]) / "result.json").write_text(json.dumps(result))
    except (MelodyError, OSError, KeyError, ValueError, RuntimeError) as exc:
        print(f"melody: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
