#!/usr/bin/env python3
"""The melody extractor: a pitch contour, its voicing, and its intonation (ADR 0074).

Version 2: pitch is resolved in 2-cent bins rather than 10 (`binResolution`),
voicing is gated by level (below), and each row's level is an output.

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
before the gate), `loud_db` and `gate_db` (below), `reference_hz`, and
`samples` (the decoded length at 44,100 Hz).

Pitch is quantised to MELODIA's salience bins, `binResolution` cents apart
on a grid anchored at 55 Hz: with Essentia's default of 10, every cents value
is a multiple of 10, too coarse for intonation. Version 2 sets 2 (any value
from 1 to 10 is accepted): on 40 s of a studio mix and of its vocal stem, 2
took 1.5 and 2.1 times the time of 10, and 1 took 2.3 and 3.4 times.

The tuning reference is the `reference_hz` parameter (440 Hz). It is not
read from a tuning extractor: an extractor's needs are fixed (ADR 0074), and
a reader holding a measured tuning t adds 12 log2(`reference_hz` / t) to
`midi` and takes cents from that. `PitchContourSegmentation` takes a whole number of
hertz, so the notes are quantised against the reference rounded to one;
`midi` and `cents` use it exactly.

It measures whatever audio it is given: the mix, or a stem when a stem is
the content. On a mix MELODIA follows the most salient line, which in a
band is as often a keyboard, a bass, or a horn as the voice. For the sung
line, measure the vocal stem: `extract.py run melody --content <recording>
--from stems/vocals` (ADR 0075) makes the stems if they are missing and
files the vocal line's melody with the recording, keyed by the stem's bits.
The gate below is what makes a stem's melody usable: a separated vocal is
mostly near-silence with the band's bleed in it, and MELODIA voices that
bleed as readily as the voice.

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
held until the gate is known, about 20 MB a recording-hour): each chunk of
`chunk_seconds` is analysed with `margin_seconds` of audio either side and
only its own rows are kept, so a contour that crosses a chunk's edge is
tracked whole within the margin. MELODIA's
thresholds are relative to the audio it is given, so a chunked contour can
differ from one measured on the whole recording near quiet or busy
passages; `chunk_seconds` 0 measures the whole recording at once, exactly as
the algorithm does, at about 4 bytes a sample of memory, twice.

`hop_size` is the stride: 256 or 512 measure 2 or 4 times fewer frames, in
less time, with a coarser contour; the algorithms' continuity and duration
settings are in milliseconds, so they keep their meaning. The levels are
measured over whole hops of the decoded stream, not over each chunk, so they
do not depend on the chunking.

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

VERSION = "2"
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
PREDOMINANT = {"binResolution": 2, "filterIterations": 3, "guessUnvoiced": False, "harmonicWeight": "0.8",
               "magnitudeCompression": 1, "magnitudeThreshold": 40, "maxFrequency": 20000, "minDuration": 100,
               "minFrequency": 80, "numberHarmonics": 20, "peakDistributionThreshold": "0.9",
               "peakFrameThreshold": "0.9", "pitchContinuity": "27.5625", "referenceFrequency": 55,
               "timeContinuity": 100, "voiceVibrato": False, "voicingTolerance": "0.2"}
SEGMENTATION = {"minDuration": "0.1", "pitchDistanceThreshold": 60, "rmsThreshold": -2}
INTEGERS = {"predominant": {"filterIterations", "magnitudeThreshold", "minDuration", "numberHarmonics",
                            "timeContinuity"},
            "segmentation": {"pitchDistanceThreshold", "rmsThreshold"}}
FIXED_OPTIONS = {"guessUnvoiced": False}  # see the module's description
BIN_RESOLUTION = (1.0, 10.0)  # cents; see the module's description
GATE = {"floor_db": "-60", "range_db": "35"}


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
            "equal_loudness": True, "predominant": PREDOMINANT, "segmentation": SEGMENTATION,
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
    floor, span = (_number(gate[name], f"voicing_gate.{name}") for name in ("floor_db", "range_db"))
    if not (isinstance(gate["floor_db"], str) and LEVEL_FLOOR_DB <= floor <= 0):
        raise MelodyError(f"voicing_gate.floor_db must be decimal text from {LEVEL_FLOOR_DB:g} to 0 dBFS, "
                          f"not {gate['floor_db']!r}")
    if not (isinstance(gate["range_db"], str) and 0 <= span <= -LEVEL_FLOOR_DB):
        raise MelodyError(f"voicing_gate.range_db must be decimal text from 0 to {-LEVEL_FLOOR_DB:g} dB, "
                          f"not {gate['range_db']!r}")
    reference = _number(given["reference_hz"], "reference_hz")
    if isinstance(reference, bool) or not 0 < reference < 22000:
        raise MelodyError(f"reference_hz must be a frequency, not {given['reference_hz']!r}")
    predominant = _options(given["predominant"], PREDOMINANT, "predominant")
    low, high = BIN_RESOLUTION
    if not low <= predominant["binResolution"] <= high:
        raise MelodyError(f"predominant.binResolution must be from {low:g} to {high:g} cents, "
                          f"not {given['predominant']['binResolution']!r}")
    return {"hop": hop, "chunk": (chunk * RATE // hop) * hop, "margin": (margin * RATE // hop) * hop,
            "reference": float(reference), "gate_floor": float(floor), "gate_range": float(span),
            "predominant": predominant,
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


def chunks(blocks: Iterable[numpy.ndarray], chunk: int, margin: int, hop: int
           ) -> Iterator[tuple[int, numpy.ndarray, int, int | None]]:
    """The audio cut for analysis: (start, samples, first kept row, kept rows
    or None for every row to the end). Chunk c keeps rows centred on
    [c x chunk, (c + 1) x chunk) and is analysed from `margin` before to
    `margin` after, clipped to the audio; the last chunk keeps every row to
    the end, so the rows kept are exactly the whole recording's
    ceil(n / hop) + 1. `chunk` 0 is the whole recording in one piece.
    `chunk` and `margin` are multiples of `hop`, so every chunk's rows lie
    on the whole recording's grid."""
    assert chunk % hop == 0 and margin % hop == 0
    buffer = numpy.zeros(0, dtype=numpy.float32)
    offset = 0  # the sample index of buffer[0]
    index = 0
    pending: list[numpy.ndarray] = []  # blocks not yet joined to the buffer, joined once per chunk
    waiting = 0
    for block in blocks:
        pending.append(block)
        waiting += len(block)
        # Chunk `index` is not the last while audio goes on past its end; it
        # can be cut once its right margin, and one sample more, has arrived.
        if not chunk or offset + len(buffer) + waiting <= (index + 1) * chunk + margin:
            continue
        buffer = numpy.concatenate([buffer, *pending])
        pending, waiting = [], 0
        while offset + len(buffer) > (index + 1) * chunk + margin:
            start = max(0, index * chunk - margin)
            end = (index + 1) * chunk + margin
            yield start, buffer[start - offset:end - offset], index * chunk // hop, chunk // hop
            index += 1
            drop = max(0, index * chunk - margin) - offset
            buffer, offset = buffer[drop:], offset + drop
    buffer = numpy.concatenate([buffer, *pending])
    total = offset + len(buffer)
    if total == 0:
        return
    if not chunk:
        yield 0, buffer, 0, None
        return
    while True:
        start = max(0, index * chunk - margin)
        if (index + 1) * chunk >= total:
            yield start, buffer[start - offset:], index * chunk // hop, None
            return
        end = min(total, (index + 1) * chunk + margin)
        yield start, buffer[start - offset:end - offset], index * chunk // hop, chunk // hop
        index += 1


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


def gate(pitch: numpy.ndarray, level_db: numpy.ndarray, floor_db: float, range_db: float, step_cents: float
         ) -> tuple[numpy.ndarray, float, float]:
    """The voicing the gate keeps, `loud_db`, and `gate_db`: each contour of
    rows MELODIA voiced is kept whole if at least half its rows reach the gate,
    the higher of the floor and the recording's 99th-percentile row level
    less the range (see the module's description)."""
    loud = float(numpy.percentile(level_db, LOUD_PERCENTILE)) if len(level_db) else LEVEL_FLOOR_DB
    threshold = max(floor_db, loud - range_db)
    kept = numpy.zeros(pitch.shape, dtype=bool)
    for start, end in runs(pitch, step_cents):
        if float(numpy.median(level_db[start:end])) >= threshold:
            kept[start:end] = True
    return kept, loud, threshold


def run(request: dict[str, Any], es: Any) -> dict[str, Any]:
    """Measure the request's audio and write every output; return the result."""
    chosen = settings(request["parameters"])
    reference = chosen["reference"]
    path = request["input"]["path"]
    melody = Melody(es, chosen)
    hop = chosen["hop"]
    output = Path(request["output_dir"])
    energy = Energy(hop)
    pitches: list[numpy.ndarray] = []
    confidences: list[numpy.ndarray] = []
    kept_rows = 0
    samples = 0
    for start, audio, first_row, kept in chunks(energy.tap(decoded_blocks(path)), chunk=chosen["chunk"],
                                                margin=chosen["margin"], hop=hop):
        if first_row != kept_rows:
            raise MelodyError(f"chunk rows begin at {first_row}, after {kept_rows} rows")
        pitch, confidence = melody.measure(audio)
        skip = first_row - start // hop
        end = None if kept is None else skip + kept
        pitches.append(pitch[skip:end])
        confidences.append(confidence[skip:end])
        kept_rows += len(pitches[-1])
        samples = start + len(audio)
    if samples == 0:
        raise MelodyError(f"{path} decodes to no samples")
    rows = -(-samples // hop) + 1
    if kept_rows != rows:
        raise MelodyError(f"{samples} samples made {kept_rows} rows, not the {rows} the grid says")
    pitch = numpy.concatenate(pitches)
    confidence = numpy.concatenate(confidences)
    level_db = row_levels(energy.hops(), rows, hop)
    melodia_voiced = pitch > 0
    # MELODIA's pitchContinuity is the most a contour moves in a millisecond.
    step = chosen["predominant"]["pitchContinuity"] * hop * 1000 / RATE
    voiced, loud_db, gate_db = gate(pitch, level_db, chosen["gate_floor"], chosen["gate_range"], step)
    pitch = numpy.where(voiced, pitch, 0).astype(numpy.float32)
    confidence = numpy.where(voiced, confidence, 0).astype(numpy.float32)
    flags, midi, cents = intonation(pitch, reference)
    values = {"pitch": pitch, "confidence": confidence, "voiced": flags, "midi": midi, "cents": cents,
              "level_db": level_db}
    for name, dtype in SERIES.items():
        (output / f"{name}.{'u8' if dtype == 'u1' else 'f32'}").write_bytes(
            numpy.ascontiguousarray(values[name], dtype=dtype).tobytes())
    # The notes are segmented from the gated contour, so the audio is decoded
    # a second time: the gate is known only once the whole recording is.
    notes = []
    for start, audio, first_row, kept in chunks(decoded_blocks(path), chunk=chosen["chunk"],
                                                margin=chosen["margin"], hop=hop):
        here = pitch[start // hop:start // hop + -(-len(audio) // hop) + 1]
        # A note belongs to the chunk whose kept rows hold its onset.
        low, high = first_row * hop / RATE, (None if kept is None else (first_row + kept) * hop / RATE)
        for onset, duration, number in melody.notes(here, audio):
            onset += start / RATE
            if onset >= low and (high is None or onset < high):
                notes.append({"start": onset, "end": onset + duration, "value": number})
    (output / "notes.json").write_text(json.dumps(notes))
    outputs: dict[str, Any] = {name: {"shape": [rows, 1]} for name in SERIES}
    outputs.update({"notes": {}, "voiced_fraction": {"value": float(voiced.mean())},
                    "melodia_voiced_fraction": {"value": float(melodia_voiced.mean())},
                    "loud_db": {"value": loud_db}, "gate_db": {"value": gate_db},
                    "reference_hz": {"value": reference}, "samples": {"value": samples}})
    software = [{"name": "ffmpeg", "version": ffmpeg_version()},
                {"name": "essentia", "version": importlib.metadata.version("essentia")},
                {"name": "numpy", "version": numpy.__version__},
                {"name": "python", "version": platform.python_version()}]
    return {"contract": "archive-extractor-result/1", "software": software,
            "grids": {"melody": {"sample_rate": RATE, "hop": hop, "frame": FRAME, "first_centre": 0}},
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
