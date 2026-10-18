#!/usr/bin/env python3
"""The rhythm extractor: beats, tempo, onsets, and how tightly onsets sit on
the beat grid (ADR 0074).

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

Beat trackers find beats in silence: on digital silence RhythmExtractor2013
reports 738 BPM with confidence 5.0, its maximum. So a beat is kept only if
an onset lies within `beat_gate_seconds` of it, and a chunk with no onset
has confidence 0.

Why it does not read the `frames` series: both Essentia algorithms require
audio at 44,100 Hz and compute their own detection functions with hops of
512 and 1,024 samples (12 and 23 ms), while `frames` is 22,050 Hz audio cut
every 2,048 samples (93 ms), coarser than the deviations `onset_deviation`
measures. Decoding is under a tenth of the cost; the trackers are the rest.

Outputs:

- `beats`, `onsets`: events, in seconds from the first decoded sample;
- on the `seconds` grid (a row a second, each summarising the
  `window_seconds` centred on it):
  `tempo` (BPM, 60 over the median beat interval whose middle is in the
  window; 0 where there is none), `onset_count` (onsets in the window that
  lie between two kept beats), `onset_deviation` (milliseconds: the mean
  absolute and the mean signed distance from each such onset to the
  nearest point of the beat grid divided into `subdivisions`; positive is
  late), and `offgrid` (the mean absolute distance as a fraction of the
  subdivision's length, 0 on the grid, 0.25 for onsets placed at random,
  0.5 at most). Means, not medians: when half the onsets are the beats
  themselves, a median hides how far the other half stray, and each
  distance is bounded by half a subdivision, so no outlier dominates.
  Essentia's onsets and beats both fall on 512-sample (11.6 ms) steps, so
  a single distance is resolved to about that;
- on the `chunks` grid (a row a chunk): `chunk_bpm` and `chunk_confidence`,
  RhythmExtractor2013's own, confidence from 0 to 5.32 (above 3.5 is
  excellent by Essentia's guide);
- scalars: `bpm` (60 over the median of every kept beat interval),
  `confidence` (the chunks' confidences weighted by the seconds each keeps),
  `onset_rate` (onsets a second), and `seconds`.

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

RATE = 44100
ESSENTIA_BUILD = "2.1b6.dev1438"
SECOND = RATE
TUNABLE = {"min_tempo": (40, 180), "max_tempo": (60, 250), "subdivisions": (1, 12), "window_seconds": (2, 120),
           "chunk_seconds": (60, 3600), "overlap_seconds": (10, 600), "beat_gate_seconds": (1, 30)}


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
            "beat_gate_seconds": 2, "window_seconds": 10, "subdivisions": 4}


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
        chunk_onsets = kept(onsets, chunk)
        found.onsets.extend(chunk_onsets)
        found.beats = join_beats(found.beats, kept(ticks, chunk), shortest)
        found.chunk_bpm.append(float(bpm))
        # A tracker scores digital silence 5.0; a chunk with nothing in it has no beat to be sure of.
        found.chunk_confidence.append(float(confidence) if len(onsets) else 0.0)
        found.chunk_kept_seconds.append((chunk.keep_to - chunk.keep_from) / RATE)
        found.chunk_onsets.append(len(chunk_onsets))
    return found


def gate(beats: list[float], onsets: list[float], reach: float) -> list[float]:
    """The beats with an onset within `reach` seconds of them."""
    if not onsets:
        return []
    times = numpy.asarray(onsets)
    kept_beats = []
    for beat in beats:
        index = int(numpy.searchsorted(times, beat))
        near = [times[i] for i in (index - 1, index) if 0 <= i < len(times)]
        if any(abs(t - beat) <= reach for t in near):
            kept_beats.append(beat)
    return kept_beats


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


def series(found: Measured, beats: list[float], config: dict[str, Any]) -> dict[str, numpy.ndarray]:
    rows, low, high = windows(found.samples, config["window_seconds"])
    pairs = intervals(beats, 60.0 / config["min_tempo"])
    middles = numpy.asarray([(a + b) / 2 for a, b in pairs])
    lengths = numpy.asarray([b - a for a, b in pairs])
    measured = deviations(found.onsets, pairs, config["subdivisions"])
    onset_times = numpy.asarray([m[0] for m in measured])
    signed = numpy.asarray([m[1] for m in measured]) * 1000
    fraction = numpy.asarray([m[2] for m in measured])
    tempo = numpy.zeros((rows, 1), dtype="<f4")
    count = numpy.zeros((rows, 1), dtype="<f4")
    deviation = numpy.zeros((rows, 2), dtype="<f4")
    offgrid = numpy.zeros((rows, 1), dtype="<f4")
    for k in range(rows):
        in_window = (middles >= low[k]) & (middles < high[k]) if len(middles) else numpy.zeros(0, bool)
        if in_window.any():
            tempo[k, 0] = 60.0 / float(numpy.median(lengths[in_window]))
        if len(onset_times):
            chosen = (onset_times >= low[k]) & (onset_times < high[k])
            if chosen.any():
                count[k, 0] = int(chosen.sum())
                deviation[k, 0] = float(numpy.mean(numpy.abs(signed[chosen])))
                deviation[k, 1] = float(numpy.mean(signed[chosen]))
                offgrid[k, 0] = float(numpy.mean(fraction[chosen]))
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


def ffmpeg_version(ffmpeg: str = "ffmpeg") -> str:
    first = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True).stdout.split("\n", 1)[0].split()
    return first[2] if len(first) > 2 and first[:2] == ["ffmpeg", "version"] else "unknown"


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
        beats = gate(found.beats, found.onsets, config["beat_gate_seconds"])
        outputs: dict[str, Any] = {}
        for name, values in series(found, beats, config).items():
            (output / f"{name}.f32").write_bytes(values.tobytes())
            outputs[name] = {"shape": list(values.shape)}
        for name, values in (("beats", beats), ("onsets", found.onsets)):
            (output / f"{name}.json").write_text(json.dumps(events(values)))
            outputs[name] = {}
        for name, value in scalars(found, beats, config).items():
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
