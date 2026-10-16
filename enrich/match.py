"""Score how close two pieces of audio are as renditions of one song (ADR 0067).

A tool, not an Archive client: it takes audio and returns plain numbers. The
method follows Serrà, Serra, and Andrzejak (2009), "Cross recurrence
quantification for cover song identification", in numpy and scipy:

1. decode each piece to mono and resample it to the analysis rate;
2. a pitch-class profile per frame: spectral peaks between two frequencies,
   each peak's squared magnitude spread over the twelve pitch classes (bin 0
   is A at the reference frequency) with a squared-cosine window one
   semitone wide, normalised to a maximum of one;
3. each profile rounded to integer units (`profile_units` per unit), so
   everything after it is exact integer arithmetic: the marks below depend on
   no linear-algebra library, rounding mode, or thread count, none of which a
   run records;
4. the optimal transposition index: the circular shift of the reference's
   summed profile that best agrees with the query's, applied to every
   reference frame, so a performance in another key compares as the same song;
5. consecutive frames stacked into one vector, and a binary cross-recurrence
   matrix that marks a pair of vectors when each is among the other's
   nearest fraction of neighbours (by row and by column), by exact squared
   distance;
6. Qmax, the length of the longest locally aligned path through that matrix,
   with gap penalties, computed in exact integer units;
7. the score `sqrt(min(N_query, N_reference)) / Qmax`, where N counts stacked
   vectors, written as exact decimal text rounded half to even. Lower is
   closer; a pair with no aligned path (Qmax 0) has no score.

This is not the V2 system's Essentia matcher: no Essentia build is obtainable
reproducibly for both the flake and the macOS virtual environment, so the
method has its own identifier and its thresholds must be calibrated on real
recordings (see README.md). Every setting is in `parameters()`, which a run
records as its parameters Artifact.

`match.py <query> <reference>...` prints the score of each whole file.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
import sys
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path
from typing import Any, Sequence

import numpy
import scipy
import scipy.signal

AGENT = "pkg:generic/archive-enrich-match@0.1.0"
METHOD_ID = "archive-enrich/chroma-oti-qmax"
METHOD_VERSION = "1"
SCALE = "archive-enrich/chroma-oti-qmax/sqrt-min-length-over-qmax"
DIRECTION = "lower_is_closer"

# Settings, as decimal text where a value is not an integer: canonical JSON
# holds no floating-point numbers, and the text is what a run records.
SETTINGS: dict[str, Any] = {
    "analysis_rate": 22050,
    "frame_size": 4096,
    "hop_size": 2048,
    "window": "blackman-harris-4-term",
    "peak_min_hz": 40,
    "peak_max_hz": 5000,
    "peak_floor_db": 60,
    "reference_hz": 440,
    "pitch_classes": 12,
    "pitch_window_semitones": "1",
    "profile_units": 65536,
    "stack_frames": 9,
    "neighbour_fraction": "0.095",
    "gap_onset": "0.5",
    "gap_extension": "0.5",
    "score_places": 6,
    "minimum_seconds": 2,
}
DECIMAL = r"^(0|[1-9][0-9]*)(\.[0-9]+)?$"


class MatchError(Exception):
    """Audio could not be read or analysed, so nothing is scored."""


def decode_arguments(path: str, start: int | str, end: int | str) -> list[str]:
    """The ffmpeg arguments after the program name: the file's first audio
    stream, trimmed to `[start, end)` in samples at its own rate, downmixed
    to mono, as 32-bit floats on standard output. No resampling here: the
    trimmed length is checked against the span before anything else."""
    return ["-nostdin", "-hide_banner", "-nostats", "-loglevel", "error", "-i", path, "-map", "0:a:0",
            "-af", f"atrim=start_sample={start}:end_sample={end},asetpts=PTS-STARTPTS",
            "-ac", "1", "-c:a", "pcm_f32le", "-f", "f32le", "-"]


def parameters(limit: str) -> dict[str, Any]:
    """The run's configuration (ADR 0006): every setting of the method, the
    decoder invocation, and the limit a Work's best score must not exceed to
    be proposed."""
    check_decimal(limit, "the limit")
    return {**SETTINGS, "decoder": "ffmpeg", "decode_arguments": decode_arguments("{input}", "{start}", "{end}"),
            "resampler": "scipy.signal.resample_poly", "proposal_limit": limit}


def software() -> tuple[tuple[str, str], ...]:
    """The libraries the scores depend on, as installed now."""
    return (("numpy", numpy.__version__), ("scipy", scipy.__version__))


def check_decimal(text: str, what: str) -> Decimal:
    if not isinstance(text, str) or not re.match(DECIMAL, text):
        raise MatchError(f"{what} {text!r} is not unsigned decimal text")
    return Decimal(text)


# -- audio -------------------------------------------------------------------

@dataclass(frozen=True)
class Piece:
    """A span of one file's first audio stream, in samples at `sample_rate`."""
    path: Path
    start_sample: int
    end_sample: int
    sample_rate: int


def stream_rate(path: Path, ffprobe: str = "ffprobe") -> int:
    try:
        completed = subprocess.run([ffprobe, "-v", "error", "-select_streams", "a:0", "-show_entries",
                                    "stream=sample_rate", "-of", "json", str(path)],
                                   stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace")
    except OSError as exc:
        raise MatchError(f"ffprobe could not run: {exc}") from exc
    try:
        streams = json.loads(completed.stdout)["streams"]
        rate = int(streams[0]["sample_rate"]) if streams else None
    except (ValueError, KeyError, TypeError, IndexError):
        raise MatchError(f"ffprobe could not read {path}: {completed.stderr.strip()[:300]}") from None
    if completed.returncode != 0 or rate is None:
        raise MatchError(f"{path} has no audio stream")
    return rate


def decode(piece: Piece, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe") -> numpy.ndarray:
    """The piece's samples, mono, at its own rate. A piece whose rate is not
    its file's, or that runs past the file's end, is refused: the span would
    not denote the audio it names."""
    if not 0 <= piece.start_sample < piece.end_sample:
        raise MatchError(f"span {piece.start_sample}..{piece.end_sample} of {piece.path} is empty")
    rate = stream_rate(piece.path, ffprobe)
    if rate != piece.sample_rate:
        raise MatchError(f"{piece.path} is sampled at {rate} Hz, but its span is in samples at {piece.sample_rate} Hz")
    try:
        completed = subprocess.run([ffmpeg, *decode_arguments(str(piece.path), piece.start_sample, piece.end_sample)],
                                   stdin=subprocess.DEVNULL, capture_output=True)
    except OSError as exc:
        raise MatchError(f"ffmpeg could not run: {exc}") from exc
    if completed.returncode != 0:
        raise MatchError(f"ffmpeg could not decode {piece.path}: "
                         f"{completed.stderr.decode('utf-8', 'replace').strip()[:300]}")
    samples = numpy.frombuffer(completed.stdout, dtype="<f4")
    wanted = piece.end_sample - piece.start_sample
    if len(samples) != wanted:
        raise MatchError(f"span {piece.start_sample}..{piece.end_sample} of {piece.path} runs past its audio "
                         f"({len(samples)} of {wanted} samples decoded)")
    return samples


def resample(samples: numpy.ndarray, rate: int, target: int) -> numpy.ndarray:
    divisor = math.gcd(rate, target)
    if rate == target:
        return samples.astype(numpy.float64)
    return scipy.signal.resample_poly(samples.astype(numpy.float64), target // divisor, rate // divisor)


def load(pieces: Sequence[Piece], ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe") -> numpy.ndarray:
    """The pieces in order, each resampled to the analysis rate, joined."""
    if not pieces:
        raise MatchError("no audio to analyse")
    rate = SETTINGS["analysis_rate"]
    return numpy.concatenate([resample(decode(p, ffmpeg, ffprobe), p.sample_rate, rate) for p in pieces])


# -- features ----------------------------------------------------------------

def profiles(audio: numpy.ndarray) -> numpy.ndarray:
    """One pitch-class profile per frame, shape (frames, 12), each frame's
    maximum 1 (or all zeros for a frame with no peak)."""
    s = SETTINGS
    rate, size, hop, classes = s["analysis_rate"], s["frame_size"], s["hop_size"], s["pitch_classes"]
    if len(audio) < max(size, s["minimum_seconds"] * rate):
        raise MatchError(f"{len(audio) / rate:.3f} s of audio is shorter than the method's "
                         f"{s['minimum_seconds']} s minimum")
    count = 1 + (len(audio) - size) // hop
    window = scipy.signal.windows.blackmanharris(size, sym=False)
    frequencies = numpy.arange(size // 2 + 1) * rate / size
    band = numpy.flatnonzero((frequencies >= s["peak_min_hz"]) & (frequencies <= s["peak_max_hz"]))
    low, high = max(int(band[0]), 1), min(int(band[-1]), size // 2 - 1)
    floor = 10 ** (-s["peak_floor_db"] / 20)
    half_width = float(Fraction(s["pitch_window_semitones"]) / 2)
    result = numpy.zeros((count, classes))
    for first in range(0, count, 256):
        index = numpy.arange(first, min(first + 256, count))
        frames = audio[index[:, None] * hop + numpy.arange(size)[None, :]] * window
        magnitude = numpy.abs(numpy.fft.rfft(frames, axis=1))
        middle = magnitude[:, low:high + 1]
        left, right = magnitude[:, low - 1:high], magnitude[:, low + 1:high + 2]
        loudest = magnitude[:, low:high + 1].max(axis=1, keepdims=True)
        is_peak = (middle > left) & (middle >= right) & (middle > loudest * floor) & (loudest > 1e-9)
        rows, columns = numpy.nonzero(is_peak)
        if len(rows) == 0:
            continue
        # Parabolic interpolation of each peak on a decibel scale.
        a = 20 * numpy.log10(left[rows, columns] + 1e-12)
        b = 20 * numpy.log10(middle[rows, columns] + 1e-12)
        c = 20 * numpy.log10(right[rows, columns] + 1e-12)
        denominator = a - 2 * b + c
        offset = numpy.where(denominator != 0, 0.5 * (a - c) / numpy.where(denominator != 0, denominator, 1), 0.0)
        frequency = (columns + low + offset) * rate / size
        energy = (10 ** ((b - 0.25 * (a - c) * offset) / 20)) ** 2
        position = numpy.mod(classes * numpy.log2(frequency / s["reference_hz"]), classes)
        for neighbour in (numpy.floor(position), numpy.floor(position) + 1):
            distance = position - neighbour
            weight = numpy.where(numpy.abs(distance) <= half_width,
                                 numpy.cos(numpy.pi / 2 * distance / half_width) ** 2, 0.0)
            numpy.add.at(result, (index[rows], neighbour.astype(int) % classes), weight * energy)
    peak = result.max(axis=1, keepdims=True)
    return numpy.divide(result, peak, out=numpy.zeros_like(result), where=peak > 0)


def quantize(p: numpy.ndarray) -> numpy.ndarray:
    """Profiles in integer units, rounded half to even. What follows is exact."""
    return numpy.rint(numpy.asarray(p, dtype=numpy.float64) * SETTINGS["profile_units"]).astype(numpy.int64)


def transposition(query: numpy.ndarray, reference: numpy.ndarray) -> int:
    """The shift of the reference's pitch classes that best agrees with the
    query's, over the profiles summed in integer units; ties go to the
    smallest shift."""
    q, r = quantize(query).sum(axis=0), quantize(reference).sum(axis=0)
    agreement = [int(numpy.dot(q, numpy.roll(r, shift))) for shift in range(len(q))]
    return int(numpy.argmax(agreement))


def stack(p: numpy.ndarray) -> numpy.ndarray:
    m = SETTINGS["stack_frames"]
    if len(p) < m:
        raise MatchError(f"{len(p)} frames cannot be stacked {m} at a time")
    return numpy.stack([p[i:len(p) - m + 1 + i] for i in range(m)], axis=1).reshape(len(p) - m + 1, -1)


def cross_recurrence(query: numpy.ndarray, reference: numpy.ndarray) -> numpy.ndarray:
    """The binary matrix: query vectors by rows, transposed reference vectors
    by columns, marking mutual nearest neighbours. Distances are squared
    Euclidean distances of integer vectors, computed exactly in int64 (no
    BLAS: numpy multiplies integer matrices itself), so ties are exact."""
    shifted = numpy.roll(reference, transposition(query, reference), axis=1)
    a, b = stack(quantize(query)), stack(quantize(shifted))
    distance = (a * a).sum(axis=1)[:, None] + (b * b).sum(axis=1)[None, :] - 2 * (a @ b.T)
    fraction = Decimal(SETTINGS["neighbour_fraction"])
    k_row = max(1, math.ceil(fraction * distance.shape[1]))
    k_column = max(1, math.ceil(fraction * distance.shape[0]))
    by_row = numpy.partition(distance, k_row - 1, axis=1)[:, k_row - 1]
    by_column = numpy.partition(distance, k_column - 1, axis=0)[k_column - 1, :]
    return (distance <= by_row[:, None]) & (distance <= by_column[None, :])


def qmax(matrix: numpy.ndarray) -> Fraction:
    """The Serra09 Qmax of a binary matrix, exactly. A marked cell extends the
    best of its three predecessors (i-1,j-1), (i-2,j-1), (i-1,j-2) by one; an
    unmarked cell keeps the best of them less a gap penalty (onset after a
    marked predecessor, extension after an unmarked one), never below zero.
    The values are kept as integers over a common denominator."""
    onset, extension = Fraction(SETTINGS["gap_onset"]), Fraction(SETTINGS["gap_extension"])
    unit = math.lcm(onset.denominator, extension.denominator)
    step, onset_units, extension_units = unit, int(onset * unit), int(extension * unit)
    rows, columns = matrix.shape
    marked = numpy.zeros((rows + 2, columns + 2), dtype=bool)
    marked[2:, 2:] = matrix
    score = numpy.zeros((rows + 2, columns + 2), dtype=numpy.int64)
    penalty = numpy.where(marked, onset_units, extension_units)
    for i in range(2, rows + 2):
        diagonal, skip_row, skip_column = score[i - 1, 1:-1], score[i - 2, 1:-1], score[i - 1, :-2]
        extend = numpy.maximum(numpy.maximum(diagonal, skip_row), skip_column) + step
        gap = numpy.maximum.reduce([numpy.zeros(columns, dtype=numpy.int64),
                                    diagonal - penalty[i - 1, 1:-1],
                                    skip_row - penalty[i - 2, 1:-1],
                                    skip_column - penalty[i - 1, :-2]])
        score[i, 2:] = numpy.where(marked[i, 2:], extend, gap)
    return Fraction(int(score.max()), unit)


@dataclass(frozen=True)
class Score:
    qmax: Fraction
    query_length: int
    reference_length: int
    text: str | None  # None when no path aligns (Qmax 0)

    def as_json(self) -> dict[str, Any]:
        return {"score": self.text, "qmax": str(self.qmax), "query_length": self.query_length,
                "reference_length": self.reference_length}


def score_text(length: int, best: Fraction) -> str | None:
    """`sqrt(length) / best` as decimal text with the configured places,
    rounded half to even, computed in decimal arithmetic so the text depends
    only on the two exact inputs."""
    if best <= 0:
        return None
    context = Context(prec=60)
    value = context.divide(context.multiply(context.sqrt(Decimal(length)), Decimal(best.denominator)),
                           Decimal(best.numerator))
    quantum = Decimal(1).scaleb(-SETTINGS["score_places"])
    try:
        text = str(value.quantize(quantum, rounding=ROUND_HALF_EVEN, context=context))
    except InvalidOperation as exc:  # pragma: no cover - sixty digits hold any score here
        raise MatchError(f"the score {value} cannot be written to {SETTINGS['score_places']} places") from exc
    return text


def compare(query: numpy.ndarray, reference: numpy.ndarray) -> Score:
    """Score two profile sequences."""
    matrix = cross_recurrence(query, reference)
    best = qmax(matrix)
    length = min(matrix.shape)
    return Score(best, matrix.shape[0], matrix.shape[1], score_text(length, best))


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) < 2:
        print("usage: match.py <query> <reference>...", file=sys.stderr)
        return 2
    try:
        def whole(path: str) -> numpy.ndarray:
            rate = stream_rate(Path(path))
            completed = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", path, "-map", "0:a:0", "-ac", "1",
                                        "-c:a", "pcm_f32le", "-f", "f32le", "-"], capture_output=True)
            if completed.returncode != 0:
                raise MatchError(f"ffmpeg could not decode {path}")
            return profiles(resample(numpy.frombuffer(completed.stdout, dtype="<f4"), rate,
                                     SETTINGS["analysis_rate"]))
        query = whole(arguments[0])
        scores = {path: compare(query, whole(path)).as_json() for path in arguments[1:]}
    except MatchError as exc:
        print(json.dumps({"status": "failed", "detail": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps({"method": {"id": METHOD_ID, "version": METHOD_VERSION}, "scale": SCALE,
                      "direction": DIRECTION, "scores": scores}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
