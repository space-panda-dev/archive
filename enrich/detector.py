"""Suggest spans of one audio stream from one fixed signal set (ADR 0066).

A tool, not an Archive client: it takes a file, the stream's counted facts,
and a preset, and returns spans in integer samples at the stream's own rate,
each classed `music`, `talk`, `quiet`, or `unknown`.

The stream is decoded once by `ffmpeg` to 32-bit float samples at its own
rate, averaged to one channel, and cut into consecutive analysis hops of a
whole number of samples. Every signal is computed from those hops, and every
edge falls on a hop boundary or at the end of the stream:

- **silence runs**: hops quieter than a fixed level, merged into breaks;
- **loudness transitions**: changes between adjacent fixed windows;
- **chroma novelty**: a checkerboard kernel slid along the diagonal of a
  chroma self-similarity matrix (Foote's method);
- **quiet runs**: long runs close to the recording's own loudness floor.

A signal that cannot be computed (too little audio for its windows, values
that are not finite) refuses the whole detection; no signal is ever dropped.
The rules are written out in README.md. Requires numpy; `ffmpeg` must be
installed.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np

import probe

AGENT = "pkg:generic/archive-enrich-detect@0.2.0"
METHOD_ID = "archive-enrich/detect-spans"
METHOD_VERSION = "2"
CLASSES = ("music", "talk", "quiet", "unknown")
MAX_SPANS = 10_000

# Every duration is in milliseconds and a whole number of hops; every level
# is in decibels relative to full scale; nothing is a float, so a preset is
# canonical JSON (ADR 0064). Ported from the V2 detector's `jam` and `show`
# presets; the hop, chroma, quiet, and classification values are new or
# translated from that detector's fixed constants. Only the talk level gate
# (`talk_reference_percentile`, `talk_margin_db`) was fitted on real
# recordings (ADR 0091); the rest is uncalibrated (README.md).
_COMMON = {
    "hop_ms": 100,
    "silence_threshold_db": -35,
    "max_silence_breaks": 20,
    "chroma_block_ms": 1000,
    "chroma_kernel_blocks": 8,
    "chroma_peak_percentile": 90,
    "chroma_min_spacing_ms": 20000,
    "chroma_min_hz": 55,
    "chroma_max_hz": 5000,
    "quiet_floor_percentile": 5,
    "quiet_margin_db": 3,
    "quiet_min_ms": 60000,
    "classify_min_ms": 2000,
    "rank_threshold_percent": 35,
    "rank_band_percent": 5,
    "assign_talk": 1,
    "talk_reference_percentile": 80,
    "talk_margin_db": 10,
}
PRESETS: dict[str, dict[str, int]] = {
    "jam": {**_COMMON,
            "silence_min_ms": 2000, "min_break_ms": 5000, "merge_gap_ms": 15000, "min_segment_ms": 20000,
            "window_ms": 10000, "min_transition_delta_db": 6, "min_transition_spacing_ms": 120000,
            "max_loudness_transitions": 20, "max_review_points": 9999, "review_cluster_gap_ms": 10000},
    "show": {**_COMMON,
             "silence_min_ms": 1500, "min_break_ms": 4000, "merge_gap_ms": 20000, "min_segment_ms": 30000,
             "window_ms": 5000, "min_transition_delta_db": 5, "min_transition_spacing_ms": 90000,
             "max_loudness_transitions": 30, "max_review_points": 70, "review_cluster_gap_ms": 45000},
}
# Masters and studio imports: `jam`'s edges, and never `talk` (ADR 0091).
PRESETS["studio"] = {**PRESETS["jam"], "assign_talk": 0}
_DURATIONS = ("silence_min_ms", "min_break_ms", "merge_gap_ms", "min_segment_ms", "window_ms",
              "min_transition_spacing_ms", "chroma_block_ms", "chroma_min_spacing_ms", "review_cluster_gap_ms",
              "quiet_min_ms", "classify_min_ms")
_POSITIVE = ("max_silence_breaks", "max_loudness_transitions", "max_review_points", "chroma_kernel_blocks",
             "min_transition_delta_db", "chroma_min_hz", "chroma_max_hz")
_PERCENTILES = ("chroma_peak_percentile", "quiet_floor_percentile", "talk_reference_percentile")

# Krumhansl-Schmuckler key profiles, as the V2 detector's key confidence used.
MAJOR_PROFILE = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
MINOR_PROFILE = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)
# A hop with no energy reads as this level instead of minus infinity.
FLOOR_DB = -120.0
# The V2 detector's scale for novelty scores, so they compete with silence
# durations (seconds) and loudness changes (decibels) for review points.
NOVELTY_SCORE_SCALE = 60.0
SIGNALS = ("silence", "loudness", "chroma_novelty")  # also the edge priority within a region
FRAMES_PER_READ = 256


class DetectorError(Exception):
    """The detection could not be made; nothing is suggested."""


class SignalError(DetectorError):
    """One signal could not be computed, so the detection is refused."""

    def __init__(self, signal: str, reason: str):
        super().__init__(f"the {signal} signal cannot be computed: {reason}")
        self.signal = signal


def decode_arguments(path: str, stream_index: int | str) -> list[str]:
    """The ffmpeg arguments after the program name: one stream, decoded at
    its own rate and channel count to raw 32-bit float samples."""
    return ["-nostdin", "-hide_banner", "-nostats", "-loglevel", "error", "-i", path,
            "-map", f"0:{stream_index}", "-f", "f32le", "-acodec", "pcm_f32le", "-"]


def check_preset(values: dict[str, Any]) -> dict[str, int]:
    """Refuse a preset whose values the rules cannot use."""
    expected = set(PRESETS["jam"])
    if set(values) != expected:
        raise DetectorError(f"a preset has exactly the keys {sorted(expected)}; "
                            f"missing {sorted(expected - set(values))}, unknown {sorted(set(values) - expected)}")
    for key, value in values.items():
        if not isinstance(value, int) or isinstance(value, bool):
            raise DetectorError(f"preset value {key} must be an integer")
    hop = values["hop_ms"]
    if hop < 1:
        raise DetectorError("hop_ms must be positive")
    for key in _DURATIONS:
        if values[key] < hop or values[key] % hop:
            raise DetectorError(f"{key} must be a positive whole number of hops ({hop} ms)")
    for key in _POSITIVE:
        if values[key] < 1:
            raise DetectorError(f"{key} must be positive")
    for key in _PERCENTILES:
        if not 0 <= values[key] <= 100:
            raise DetectorError(f"{key} must be from 0 to 100")
    if values["chroma_min_hz"] >= values["chroma_max_hz"]:
        raise DetectorError("chroma_min_hz must be below chroma_max_hz")
    if values["quiet_margin_db"] < 0 or not 0 <= values["rank_band_percent"] <= values["rank_threshold_percent"] \
            or values["rank_threshold_percent"] + values["rank_band_percent"] > 100:
        raise DetectorError("quiet_margin_db and the rank threshold and band must leave a usable range")
    if values["assign_talk"] not in (0, 1):
        raise DetectorError("assign_talk must be 0 (never talk) or 1")
    if values["talk_margin_db"] < 0:
        raise DetectorError("talk_margin_db must not be negative")
    return dict(values)


def parameters(preset: str) -> dict[str, Any]:
    """The run's configuration (ADR 0006): the named preset's values and how
    the decoder was driven. The bytes change only with the preset or the
    method, so each preset's Artifact is admitted once and reused."""
    if preset not in PRESETS:
        raise DetectorError(f"unknown preset {preset!r}; known presets are {sorted(PRESETS)}")
    return {"preset": preset, "values": check_preset(PRESETS[preset]), "decoder": "ffmpeg",
            "decode_arguments": decode_arguments("{input}", "{stream_index}")}


def software(ffmpeg: str = "ffmpeg") -> tuple[tuple[str, str], ...]:
    """The decoder, its libraries, and the analysis library, as installed now."""
    try:
        decoder = probe.program_versions(ffmpeg, libraries=True)
    except probe.ProbeError as exc:
        raise DetectorError(str(exc)) from exc
    return (*decoder, ("numpy", np.__version__))


# -- the stream, cut into hops -------------------------------------------------

@dataclass(frozen=True)
class Stream:
    """The facts of the pinned audio_stream claim the detection measures against."""
    stream_index: int
    sample_rate: int
    channel_count: int
    sample_count: int

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "Stream":
        return cls(payload["stream_index"], payload["sample_rate"], payload["channel_count"], payload["sample_count"])


@dataclass
class Hops:
    """Per-hop measurements: hop `k` covers samples `[k*size, min((k+1)*size, count))`."""
    size: int
    count: int
    energy: np.ndarray      # sum of squared mono samples
    lengths: np.ndarray     # samples in the hop (the last may be short)
    chroma: np.ndarray      # (hops, 12) power per pitch class
    flatness: np.ndarray    # spectral flatness of the hop

    @property
    def level_db(self) -> np.ndarray:
        mean_square = self.energy / self.lengths
        with np.errstate(divide="ignore"):
            return np.maximum(10.0 * np.log10(mean_square), FLOOR_DB)


def hop_samples(sample_rate: int, hop_ms: int) -> int:
    size = sample_rate * hop_ms // 1000
    if size < 1:
        raise DetectorError(f"a {hop_ms} ms hop is shorter than one sample at {sample_rate} Hz")
    return size


def _chroma_bins(size: int, sample_rate: int, low_hz: int, high_hz: int) -> tuple[int, list[np.ndarray]]:
    """The FFT length for a hop and, for each pitch class from C, the FFT
    bins between the preset's frequency limits that round to it."""
    n_fft = 1 << max(0, (size - 1).bit_length())
    frequencies = np.arange(n_fft // 2 + 1) * (sample_rate / n_fft)
    usable = (frequencies >= low_hz) & (frequencies <= min(high_hz, sample_rate / 2))
    pitch = np.full(frequencies.shape, -1)
    pitch[usable] = np.mod(np.rint(12.0 * np.log2(frequencies[usable] / 440.0) + 69.0).astype(int), 12)
    bins = [np.flatnonzero(pitch == c) for c in range(12)]
    # A pitch class without a bin would read as silent in every hop, and a
    # chroma without any would leave a flat novelty curve that looks like
    # music without changes; neither is a measurement.
    missing = [c for c in range(12) if len(bins[c]) == 0]
    if missing:
        raise SignalError("chroma_novelty", f"at {sample_rate} Hz a {size}-sample hop has no FFT bin between "
                                            f"{low_hz} and {high_hz} Hz for pitch classes {missing} (0 is C)")
    return n_fft, bins


def _measure(block: np.ndarray, window: np.ndarray, n_fft: int,
             bins: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    energy = np.sum(block * block, axis=1)
    power = np.abs(np.fft.rfft(block * window, n=n_fft, axis=1)) ** 2
    chroma = np.stack([power[:, b].sum(axis=1) for b in bins], axis=1)
    clipped = np.maximum(power, 1e-10)
    flatness = np.exp(np.mean(np.log(clipped), axis=1)) / np.mean(clipped, axis=1)
    return energy, chroma, flatness


def _read_exactly(stream: Any, size: int) -> bytes:
    """`size` bytes, or fewer only at the end of the stream, so a short hop
    can only be the last one."""
    parts, have = [], 0
    while have < size:
        part = stream.read(size - have)
        if not part:
            break
        parts.append(part)
        have += len(part)
    return b"".join(parts)


def measure(path: Path, stream: Stream, hop_ms: int, chroma_min_hz: int, chroma_max_hz: int,
            ffmpeg: str = "ffmpeg") -> Hops:
    """Decode the stream once and measure every hop. The decoded sample
    count must equal the stream claim's, or nothing the detection says
    would be about that claim's samples."""
    size = hop_samples(stream.sample_rate, hop_ms)
    n_fft, bins = _chroma_bins(size, stream.sample_rate, chroma_min_hz, chroma_max_hz)
    window = np.hanning(size)
    frame_bytes = 4 * stream.channel_count
    energies, chromas, flatnesses, lengths = [], [], [], []
    total = 0
    with tempfile.TemporaryFile() as errors:
        try:
            process = subprocess.Popen([ffmpeg, *decode_arguments(str(Path(path).absolute()), stream.stream_index)],
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errors)
        except OSError as exc:
            raise DetectorError(f"ffmpeg could not run: {exc}") from exc
        with process:
            assert process.stdout is not None
            want = size * frame_bytes * FRAMES_PER_READ
            while True:
                data = _read_exactly(process.stdout, want)
                if not data:
                    break
                if len(data) % frame_bytes:
                    # Only the final read may be short; a part frame means a channel count mismatch.
                    process.kill()
                    raise DetectorError(f"the decoded stream is not whole {stream.channel_count}-channel frames")
                samples = np.frombuffer(data, dtype="<f4").astype(np.float64)
                mono = samples.reshape(-1, stream.channel_count).mean(axis=1)
                count = mono.shape[0]
                whole = count // size
                block = mono[: whole * size].reshape(whole, size)
                hop_lengths = np.full(whole, size, dtype=np.float64)
                if count % size:
                    tail = np.zeros((1, size))
                    tail[0, : count % size] = mono[whole * size:]
                    block = np.vstack([block, tail])
                    hop_lengths = np.append(hop_lengths, count % size)
                energy, chroma, flatness = _measure(block, window, n_fft, bins)
                energies.append(energy)
                chromas.append(chroma)
                flatnesses.append(flatness)
                lengths.append(hop_lengths)
                total += count
        errors.seek(0)
        detail = errors.read().decode("utf-8", errors="replace").strip()
    if process.returncode != 0:
        raise DetectorError(f"ffmpeg exited {process.returncode} decoding stream {stream.stream_index}: "
                            f"{detail[:300]}")
    if total != stream.sample_count:
        raise DetectorError(f"decoding gave {total} sample frames but the stream claim counts "
                            f"{stream.sample_count}; the detection would not be about that claim's samples")
    if total == 0:
        raise DetectorError("the stream decoded to no samples")
    hops = Hops(size=size, count=total, energy=np.concatenate(energies), lengths=np.concatenate(lengths),
                chroma=np.concatenate(chromas), flatness=np.concatenate(flatnesses))
    return hops


# -- the signals ---------------------------------------------------------------

@dataclass(frozen=True)
class Point:
    """A candidate edge at a hop boundary, from one signal."""
    hop: int
    signal: str
    score: float


def runs(mask: np.ndarray, minimum: int) -> list[tuple[int, int]]:
    """Maximal runs `[start, end)` of true hops at least `minimum` long."""
    padded = np.concatenate([[False], mask, [False]]).astype(np.int8)
    changes = np.flatnonzero(np.diff(padded))
    pairs = zip(changes[0::2].tolist(), changes[1::2].tolist())
    return [(start, end) for start, end in pairs if end - start >= minimum]


def _finite(signal: str, values: np.ndarray) -> None:
    if not np.all(np.isfinite(values)):
        raise SignalError(signal, "the decoded audio gives values that are not finite")


def _strongest(points: list[Point], limit: int) -> list[Point]:
    """At most `limit` points, the highest scores first (the earlier on a
    tie), returned in time order."""
    chosen = sorted(points, key=lambda p: (-p.score, p.hop, SIGNALS.index(p.signal)))[:limit]
    return sorted(chosen, key=lambda p: (p.hop, SIGNALS.index(p.signal)))


def silence_breaks(level_db: np.ndarray, p: dict[str, int]) -> list[Point]:
    """The V2 detector's silence breaks, in hops: silent runs at least
    `min_break_ms` long, merged across gaps of at most `merge_gap_ms`, each
    breaking at its middle hop when that leaves `min_segment_ms` after the
    previous break and before the end. Scored by run length in seconds."""
    _finite("silence", level_db)
    hop = p["hop_ms"]
    silent = runs(level_db < p["silence_threshold_db"], p["silence_min_ms"] // hop)
    merged: list[list[int]] = []
    for start, end in (r for r in silent if r[1] - r[0] >= p["min_break_ms"] // hop):
        if merged and start - merged[-1][1] <= p["merge_gap_ms"] // hop:
            merged[-1][1] = max(merged[-1][1], end)
            continue
        merged.append([start, end])
    total, minimum = len(level_db), p["min_segment_ms"] // hop
    points, previous = [], 0
    for start, end in merged:
        middle = (start + end) // 2
        if middle - previous < minimum or total - middle < minimum:
            continue
        points.append(Point(middle, "silence", (end - start) * hop / 1000))
        previous = middle
    return _strongest(points, p["max_silence_breaks"])


def loudness_transitions(hops: Hops, p: dict[str, int]) -> list[Point]:
    """The V2 detector's loudness transitions: the level of consecutive
    `window_ms` windows, a point at the start of each window whose level
    differs from the previous one's by at least `min_transition_delta_db`,
    at least `min_transition_spacing_ms` after the previous point. Scored
    by the change in decibels."""
    per = p["window_ms"] // p["hop_ms"]
    count = len(hops.energy)
    if count <= per:
        raise SignalError("loudness", f"it compares {p['window_ms']} ms windows and the stream fills only one")
    starts = np.arange(0, count, per)
    energy = np.add.reduceat(hops.energy, starts)
    lengths = np.add.reduceat(hops.lengths, starts)
    with np.errstate(divide="ignore"):
        level = np.maximum(10.0 * np.log10(energy / lengths), FLOOR_DB)
    _finite("loudness", level)
    spacing = p["min_transition_spacing_ms"] // p["hop_ms"]
    points, last = [], -spacing
    for index in range(1, len(level)):
        delta = float(level[index] - level[index - 1])
        at = int(starts[index])
        if abs(delta) < p["min_transition_delta_db"] or at - last < spacing:
            continue
        points.append(Point(at, "loudness", abs(delta)))
        last = at
    return _strongest(points, p["max_loudness_transitions"])


def checkerboard_kernel(half: int) -> np.ndarray:
    """The V2 detector's Gaussian-tapered checkerboard kernel, `2*half` square."""
    side = np.arange(2 * half)
    sign = np.where((side[:, None] < half) == (side[None, :] < half), 1.0, -1.0)
    distance = (side[:, None] - half + 0.5) ** 2 + (side[None, :] - half + 0.5) ** 2
    return sign * np.exp(-distance / (2 * (half * 0.7) ** 2))


def novelty_curve(chroma: np.ndarray, half: int) -> np.ndarray:
    """Foote novelty of chroma blocks: the kernel centred at block `i`
    compares blocks `i-half .. i-1` with `i .. i+half-1`. Only the band of
    the self-similarity matrix the kernel covers is computed, so a long
    recording never needs the whole matrix. Zero where the kernel does not fit."""
    blocks = chroma.shape[0]
    norm = chroma / (np.sqrt(np.sum(chroma * chroma, axis=1, keepdims=True)) + 1e-9)
    kernel = checkerboard_kernel(half)
    novelty = np.zeros(blocks)
    centres = np.arange(half, blocks - half)
    for a in range(2 * half):
        rows = norm[centres - half + a]
        for b in range(2 * half):
            novelty[centres] += kernel[a, b] * np.sum(rows * norm[centres - half + b], axis=1)
    return novelty


def chroma_novelty(hops: Hops, p: dict[str, int]) -> list[Point]:
    """The V2 detector's chroma novelty candidates: chroma summed over
    `chroma_block_ms` blocks, Foote novelty with a `chroma_kernel_blocks`
    half-kernel scaled to [0, 1], and a point at each block that reaches the
    `chroma_peak_percentile` of the curve and is the largest value within
    `chroma_min_spacing_ms` either side. Scored by novelty times 60."""
    _finite("chroma_novelty", hops.chroma)
    per = p["chroma_block_ms"] // p["hop_ms"]
    half = p["chroma_kernel_blocks"]
    starts = np.arange(0, len(hops.energy), per)
    chroma = np.add.reduceat(hops.chroma, starts, axis=0)
    blocks = len(starts)
    if blocks <= 2 * half:
        raise SignalError("chroma_novelty", f"its kernel spans {2 * half} blocks of {p['chroma_block_ms']} ms "
                                            f"and the stream fills only {blocks}")
    novelty = novelty_curve(chroma, half)
    _finite("chroma_novelty", novelty)
    if float(novelty[half:blocks - half].max()) <= float(novelty[half:blocks - half].min()):
        return []  # a flat curve has no peaks
    low, high = float(novelty.min()), float(novelty.max())
    novelty = (novelty - low) / (high - low)
    inner = novelty[half:blocks - half]
    threshold = float(np.percentile(inner, p["chroma_peak_percentile"]))
    spacing = max(1, p["chroma_min_spacing_ms"] // p["chroma_block_ms"])
    points, index = [], half
    while index < blocks - half:
        lo, hi = max(half, index - spacing), min(blocks - half, index + spacing)
        if novelty[index] >= threshold and novelty[index] == novelty[lo:hi].max():
            points.append(Point(int(starts[index]), "chroma_novelty", float(novelty[index]) * NOVELTY_SCORE_SCALE))
            index += spacing
        else:
            index += 1
    return points


def quiet_runs(level_db: np.ndarray, p: dict[str, int]) -> tuple[float, list[tuple[int, int]]]:
    """Runs of at least `quiet_min_ms` whose hops are within `quiet_margin_db`
    of the recording's floor (its `quiet_floor_percentile` hop level) and
    below `silence_threshold_db`. Returns the floor and the runs in hops."""
    _finite("quiet", level_db)
    floor = float(np.percentile(level_db, p["quiet_floor_percentile"]))
    quiet = (level_db <= floor + p["quiet_margin_db"]) & (level_db < p["silence_threshold_db"])
    return floor, runs(quiet, p["quiet_min_ms"] // p["hop_ms"])


# -- from points to spans --------------------------------------------------------

def review_regions(points: list[Point], gap: int) -> list[list[Point]]:
    """Points in time order, clustered while each is at most `gap` hops after the previous one."""
    regions: list[list[Point]] = []
    for point in sorted(points, key=lambda p: (p.hop, SIGNALS.index(p.signal))):
        if regions and point.hop - regions[-1][-1].hop <= gap:
            regions[-1].append(point)
        else:
            regions.append([point])
    return regions


def region_edge(region: list[Point]) -> int:
    """A region's edge: its strongest silence break, else its strongest
    loudness transition, else its strongest novelty peak (the earliest on a tie)."""
    for signal in SIGNALS:
        candidates = [p for p in region if p.signal == signal]
        if candidates:
            return min(candidates, key=lambda p: (-p.score, p.hop)).hop
    raise DetectorError("internal: an empty review region")


def boundaries(regions: list[list[Point]], minimum: int, total: int) -> list[int]:
    """The V2 detector's candidate segments: region edges in time order,
    each kept only when it leaves `minimum` hops after the previous kept
    edge and before the end."""
    kept, start = [], 0
    for edge in sorted(region_edge(region) for region in regions):
        if edge - start < minimum or total - edge < minimum:
            continue
        kept.append(edge)
        start = edge
    return kept


def _pearson(x: np.ndarray, y: np.ndarray) -> float | None:
    dx, dy = x - x.mean(), y - y.mean()
    denominator = float(np.sqrt(np.sum(dx * dx) * np.sum(dy * dy)))
    return None if denominator == 0.0 else float(np.sum(dx * dy)) / denominator


def key_confidence(chroma: np.ndarray) -> float | None:
    """The best Pearson correlation of a span's mean chroma with the 24
    rotated major and minor key profiles; None for a span without pitch energy."""
    total = chroma.sum(axis=0)
    if float(total.sum()) <= 0.0:
        return None
    mean = total / total.sum()
    best = None
    for profile in (np.array(MAJOR_PROFILE), np.array(MINOR_PROFILE)):
        for shift in range(12):
            correlation = _pearson(mean, np.roll(profile, shift))
            if correlation is not None and (best is None or correlation > best):
                best = correlation
    return best


def percentile_ranks(values: list[float | None]) -> list[Fraction | None]:
    """Each value's rank among the present values, from 0 to 1 exactly;
    equal values rank in span order. A lone value has no rank."""
    present = sorted((value, index) for index, value in enumerate(values) if value is not None)
    ranks: list[Fraction | None] = [None] * len(values)
    if len(present) > 1:
        for position, (_, index) in enumerate(present):
            ranks[index] = Fraction(position, len(present) - 1)
    return ranks


def is_talk_level(level_db: float | None, reference_db: float, p: dict[str, int]) -> bool:
    """The absolute evidence talk needs (ADR 0091): the span's median hop
    level more than `talk_margin_db` below the recording's reference level,
    and a preset that assigns talk at all."""
    return p["assign_talk"] == 1 and level_db is not None and level_db < reference_db - p["talk_margin_db"]


def classify(features: list[tuple[float | None, float | None, float | None]], reference_db: float,
             p: dict[str, int]) -> list[str]:
    """The V2 detector's session-relative rule, gated by level: each span's
    key confidence and spectral flatness are ranked among the recording's
    other unquiet spans (flatness inverted), and the mean rank is `music` at
    or above the threshold plus the band. At or below the threshold minus
    the band the rank only nominates `talk`: the span is `talk` when its
    median level (the third feature) also passes `is_talk_level`, and
    `unknown` when it does not. Between the two, or without a rank, it is
    `unknown`."""
    key_ranks = percentile_ranks([f[0] for f in features])
    flat_ranks = [None if r is None else 1 - r for r in percentile_ranks([f[1] for f in features])]
    music = Fraction(p["rank_threshold_percent"] + p["rank_band_percent"], 100)
    talk = Fraction(p["rank_threshold_percent"] - p["rank_band_percent"], 100)
    classes = []
    for key, flat, (_, _, level) in zip(key_ranks, flat_ranks, features):
        ranks = [r for r in (key, flat) if r is not None]
        if not ranks:
            classes.append("unknown")
            continue
        score = sum(ranks, Fraction(0)) / len(ranks)
        if score >= music:
            classes.append("music")
        elif score <= talk and is_talk_level(level, reference_db, p):
            classes.append("talk")
        else:
            classes.append("unknown")
    return classes


@dataclass
class Detection:
    sample_rate: int
    sample_count: int
    hop_samples: int
    spans: list[tuple[int, int, str]]
    report: dict[str, Any] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        return {"sample_rate": self.sample_rate,
                "spans": [{"start_sample": s, "end_sample": e, "class": c} for s, e, c in self.spans]}

    def as_json(self) -> dict[str, Any]:
        return {**self.payload(), "sample_count": self.sample_count, "hop_samples": self.hop_samples,
                "report": self.report}


def detect_hops(hops: Hops, stream: Stream, values: dict[str, int]) -> Detection:
    """Every signal, then the spans and their classes, from measured hops."""
    p = check_preset(values)
    hop = p["hop_ms"]
    level = hops.level_db
    signals = {"silence": silence_breaks(level, p), "loudness": loudness_transitions(hops, p),
               "chroma_novelty": chroma_novelty(hops, p)}
    floor, quiet = quiet_runs(level, p)
    points = _strongest([point for found in signals.values() for point in found], p["max_review_points"])
    regions = review_regions(points, p["review_cluster_gap_ms"] // hop)
    total_hops = len(level)
    minimum = p["min_segment_ms"] // hop
    edges = boundaries(regions, minimum, total_hops)

    size, count = hops.size, hops.count
    cuts = {0, count}
    quiet_starts = set()
    for start, end in quiet:
        cuts.update((start * size, min(end * size, count)))
        quiet_starts.add(start * size)
    for edge in edges:
        # A quiet run's own edges replace any boundary inside it or within the minimum segment of it.
        if all(not (start - minimum < edge < end + minimum) for start, end in quiet):
            cuts.add(edge * size)
    ordered = sorted(cuts)
    bounds = list(zip(ordered, ordered[1:]))
    if len(bounds) > MAX_SPANS:
        raise DetectorError(f"{len(bounds)} spans exceed the {MAX_SPANS} a suggestions claim holds")

    unquiet = [(s, e) for s, e in bounds if s not in quiet_starts]
    features = []
    for s, e in unquiet:
        if (e - s) < p["classify_min_ms"] // hop * size:
            features.append((None, None, None))
            continue
        first, last = s // size, -(-e // size)
        features.append((key_confidence(hops.chroma[first:last]), float(np.mean(hops.flatness[first:last])),
                         float(np.median(level[first:last]))))
    reference = float(np.percentile(level, p["talk_reference_percentile"]))
    classes = dict(zip(unquiet, classify(features, reference, p)))
    spans = [(s, e, "quiet" if s in quiet_starts else classes[(s, e)]) for s, e in bounds]
    report = {"points": {name: len(found) for name, found in signals.items()}, "review_points": len(points),
              "regions": len(regions), "boundaries": len(edges), "quiet_runs": len(quiet),
              "floor_db": round(floor, 3), "talk_reference_db": round(reference, 3)}
    return Detection(stream.sample_rate, count, size, spans, report)


def detect(path: Path, stream: Stream, values: dict[str, int], ffmpeg: str = "ffmpeg") -> Detection:
    """Decode the stream once and suggest its spans."""
    p = check_preset(values)
    path = Path(path).absolute()
    if not path.is_file():
        raise DetectorError(f"{path} is not a file")
    hops = measure(path, stream, p["hop_ms"], p["chroma_min_hz"], p["chroma_max_hz"], ffmpeg)
    return detect_hops(hops, stream, p)


def main(argv: list[str] | None = None) -> int:
    """Detect spans in a file whose stream facts are given, and print the
    plain result; no archive is involved. The facts are counted by the probe."""
    import argparse
    parser = argparse.ArgumentParser(prog="enrich-detect-file", description=__doc__.splitlines()[0])
    parser.add_argument("file", type=Path)
    parser.add_argument("--preset", choices=sorted(PRESETS), default="jam")
    parser.add_argument("--stream-index", type=int, default=0)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    args = parser.parse_args(argv)
    try:
        counted = {s.stream_index: s for s in probe.probe(args.file, args.ffmpeg, args.ffprobe).streams}
        if args.stream_index not in counted:
            raise DetectorError(f"stream {args.stream_index} is not an exactly counted audio stream")
        facts = counted[args.stream_index]
        stream = Stream(facts.stream_index, facts.sample_rate, facts.channel_count, facts.sample_count)
        result = detect(args.file, stream, PRESETS[args.preset], args.ffmpeg)
    except (DetectorError, probe.ProbeError) as exc:
        print(json.dumps({"status": "failed", "detail": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(result.as_json(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
