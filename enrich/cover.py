"""Cover similarity by Essentia, and the growing-window search (ADRs 0071, 0072).

A tool, not an Archive client: it takes audio and suggested spans and returns
plain numbers. `enrich match-windows` reads the archive and proposes what it
finds.

The measure is the V2 system's, with its calibrated thresholds:

1. ffmpeg decodes a span to mono 16-bit PCM at 22,050 Hz, which is divided by
   32,768. This is exactly what the V2 system's loader read from the WAV
   file ffmpeg wrote for it.
2. Frames of 4,096 samples with a hop of 2,048, as Essentia's frame
   generator cuts them, each through a Blackman-Harris 62 dB window, the
   magnitude spectrum, spectral peaks, and a pitch-class profile (HPCP).
3. `ChromaCrossSimilarity` with the optimal transposition index, then
   `CoverSongSimilarity` with the serra09 alignment and the asymmetric
   distance, in both directions. The distance is the smaller one. The
   alignment is Essentia's recurrence ported to NumPy (`serra09`), which
   answers Essentia's scores and distances to the bit at a fraction of the
   cost; `alignment` says where in the matrix the best alignment lies.
4. The score is that distance rounded half to even to four places. Lower is
   closer.

The search grows a window over a recording's suggested spans. A run is a
maximal sequence of `music` or `unknown` spans; `talk` and `quiet` end it.
From each run's first span the window grows one span at a time, up to eight.
At each step it is compared with the half of the corpus whose
transposition-invariant mean chroma is closest to it. Growth stops after two
steps without improvement, or after one step worse than the best by more than
0.15, and the result is the best step, not the last. A window shorter than
two seconds is skipped without counting as a step.

Only Essentia 2.1b6.dev1438 is accepted: the thresholds were measured with
that build, and another is another method version (ADR 0071).

`cover.py <query> <reference>...` prints the distance of each whole file.
"""

from __future__ import annotations

import importlib.metadata
import json
import math
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy

AGENT = "pkg:generic/archive-enrich-cover@0.1.0"
METHOD_ID = "archive-enrich/essentia-serra09"
METHOD_VERSION = "1"
SCALE = "archive-enrich/essentia-serra09/smaller-asymmetric-distance"
DIRECTION = "lower_is_closer"
ESSENTIA_BUILD = "2.1b6.dev1438"

# Settings, as decimal text where a value is not an integer: canonical JSON
# holds no floating-point numbers, and the text is what a run records.
# Every value is the V2 system's; none may change without a new method
# version and a new calibration.
ANALYSIS: dict[str, Any] = {
    "analysis_rate": 22050,
    "frame_size": 4096,
    "hop_size": 2048,
    "windowing": {"type": "blackmanharris62"},
    "spectral_peaks": {"orderBy": "magnitude", "magnitudeThreshold": "0.00001", "minFrequency": 40,
                       "maxFrequency": 5000, "maxPeaks": 10000},
    "hpcp": {},
    "chroma_cross_similarity": {"oti": True, "otiBinary": False, "binarizePercentile": "0.095",
                                "frameStackSize": 9},
    "cover_song_similarity": {"distanceType": "asymmetric", "alignmentType": "serra09"},
    "score_places": 4,
}
SEARCH: dict[str, Any] = {
    "run_classes": ["music", "unknown"],
    "max_window_spans": 8,
    "min_window_seconds": 2,
    "stall_steps": 2,
    "degrade_margin": "0.15",
    "prefilter_retention": "0.5",
    # One false positive in 2,779 controlled pairs of the operator's own
    # recordings, measured with this build and this search (ADR 0071).
    "suggestion_distance": "0.08",
}
_FLOAT_OPTIONS = {"magnitudeThreshold", "binarizePercentile"}


class CoverError(Exception):
    """Audio could not be read or analysed, or Essentia is not the pinned
    build, so nothing is measured."""


class TooShort(CoverError):
    """The audio is too short for the alignment to find anything."""


def decode_arguments(path: str, start: int | str, end: int | str) -> list[str]:
    """The ffmpeg arguments after the program name: the file's first audio
    stream, trimmed to `[start, end)` in samples at its own rate, downmixed
    to mono and resampled to the analysis rate, as 16-bit samples on standard
    output."""
    return ["-nostdin", "-hide_banner", "-nostats", "-loglevel", "error", "-i", path, "-map", "0:a:0",
            "-af", f"atrim=start_sample={start}:end_sample={end},asetpts=PTS-STARTPTS",
            "-ac", "1", "-ar", str(ANALYSIS["analysis_rate"]), "-c:a", "pcm_s16le", "-f", "s16le", "-"]


def parameters() -> dict[str, Any]:
    """The run's configuration (ADR 0006): the measure, the decoder
    invocation, and the search."""
    return {**ANALYSIS, "decoder": "ffmpeg", "decode_arguments": decode_arguments("{input}", "{start}", "{end}"),
            "sample_scale": 32768, "search": SEARCH}


def essentia_module() -> Any:
    """`essentia.standard`, refused unless it is the pinned build."""
    try:
        import essentia
        # Its info log would otherwise print a line on stderr at import (on
        # Linux), ahead of the one JSON answer a command writes there.
        essentia.log.infoActive = False
        import essentia.standard as standard
        installed = importlib.metadata.version("essentia")
    except (ImportError, importlib.metadata.PackageNotFoundError) as exc:
        raise CoverError(f"Essentia is not importable here ({exc}); install enrich/requirements-match.txt "
                         "under CPython 3.14 (ADR 0071)") from exc
    if installed != ESSENTIA_BUILD:
        raise CoverError(f"Essentia {installed} is installed, but the method is calibrated with "
                         f"{ESSENTIA_BUILD} (ADR 0071)")
    return standard


def software() -> tuple[tuple[str, str], ...]:
    """The libraries the distances depend on, as installed now."""
    essentia_module()
    return (("essentia", importlib.metadata.version("essentia")), ("numpy", numpy.__version__))


def rounded(distance: float) -> Decimal:
    """A distance as the score text's value: the float's exact value, rounded
    half to even to the score's places."""
    if not math.isfinite(distance) or distance < 0:
        raise CoverError(f"Essentia answered a distance of {distance}")
    return Decimal(distance).quantize(Decimal(1).scaleb(-ANALYSIS["score_places"]), rounding=ROUND_HALF_EVEN)


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
        raise CoverError(f"ffprobe could not run: {exc}") from exc
    try:
        streams = json.loads(completed.stdout)["streams"]
        rate = int(streams[0]["sample_rate"]) if streams else None
    except (ValueError, KeyError, TypeError, IndexError):
        raise CoverError(f"ffprobe could not read {path}: {completed.stderr.strip()[:300]}") from None
    if completed.returncode != 0 or rate is None:
        raise CoverError(f"{path} has no audio stream")
    return rate


def decode(piece: Piece, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe") -> numpy.ndarray:
    """The piece's samples, mono, at the analysis rate. A piece whose rate is
    not its file's, or that runs past the file's end, is refused: the span
    would not denote the audio it names."""
    if not 0 <= piece.start_sample < piece.end_sample:
        raise CoverError(f"span {piece.start_sample}..{piece.end_sample} of {piece.path} is empty")
    rate = stream_rate(piece.path, ffprobe)
    if rate != piece.sample_rate:
        raise CoverError(f"{piece.path} is sampled at {rate} Hz, but its span is in samples at {piece.sample_rate} Hz")
    try:
        completed = subprocess.run([ffmpeg, *decode_arguments(str(piece.path), piece.start_sample, piece.end_sample)],
                                   stdin=subprocess.DEVNULL, capture_output=True)
    except OSError as exc:
        raise CoverError(f"ffmpeg could not run: {exc}") from exc
    if completed.returncode != 0:
        raise CoverError(f"ffmpeg could not decode {piece.path}: "
                         f"{completed.stderr.decode('utf-8', 'replace').strip()[:300]}")
    samples = numpy.frombuffer(completed.stdout, dtype="<i2").astype(numpy.float32) / numpy.float32(32768)
    # The resampler may round the length by a sample; a span past the end
    # of the audio is short by far more.
    wanted = (piece.end_sample - piece.start_sample) * ANALYSIS["analysis_rate"] / piece.sample_rate
    if abs(len(samples) - wanted) > 1:
        raise CoverError(f"span {piece.start_sample}..{piece.end_sample} of {piece.path} runs past its audio "
                         f"({len(samples)} of about {round(wanted)} samples decoded)")
    return samples


def load(pieces: Sequence[Piece], ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe") -> numpy.ndarray:
    if not pieces:
        raise CoverError("no audio to analyse")
    return numpy.concatenate([decode(piece, ffmpeg, ffprobe) for piece in pieces])


# -- the measure -------------------------------------------------------------

@dataclass(frozen=True)
class Features:
    """One span's pitch-class profiles, a (frames, 12) array, and their mean,
    which the prefilter compares."""
    profiles: numpy.ndarray
    mean: tuple[float, ...]


def _options(options: dict[str, Any]) -> dict[str, Any]:
    return {key: float(value) if key in _FLOAT_OPTIONS else value for key, value in options.items()}


# Essentia's `CoverSongSimilarity` with the serra09 alignment and the
# asymmetric distance (coversongsimilarity.cpp), in NumPy, checked against
# the pinned build by test_cover.Alignment.
# A cell of the score matrix outside the first two rows and columns is the
# best of the cells at (i-1, j-1), (i-2, j-1) and (i-1, j-2), plus one where
# the binary matrix matches, or less the disruption penalty, floored at zero,
# where it does not. Essentia's onset and extension penalties are both 0.5,
# its defaults, which the method keeps, so the penalty does not depend on the
# matrix. Every score is a multiple of 0.5, which float32 holds exactly, so
# the scores are Essentia's to the bit in any order of evaluation.
_DISRUPTION = numpy.float32(0.5)


def _turned(rows: int, columns: int) -> bool:
    """Whether the recurrence is cheaper over the transposed matrix. No cell
    depends on its own row, so each row is one vector step, and the set of
    cells a cell depends on is its own transpose, so the transposed matrix's
    scores are the transposed scores. A step costs about as much as copying
    a thousand transposed cells (measured on Apple silicon), so a matrix much
    taller than it is wide runs turned."""
    return (rows - columns) * 1000 > rows * columns


def serra09(matrix: numpy.ndarray) -> tuple[numpy.ndarray, float]:
    """Essentia's answer for a binary cross-similarity matrix whose rows are
    the query and columns the reference: the score matrix, and the square
    root of the number of columns over its highest score, as float32. With
    no match anywhere the highest score is zero, and the distance is positive
    infinity, as Essentia's is. A matrix that is empty or not binary is
    refused, as Essentia refuses it."""
    matrix = numpy.asarray(matrix, dtype=numpy.float32)
    if matrix.ndim != 2 or matrix.size == 0:
        raise CoverError(f"the cross-similarity matrix is empty or not a matrix ({matrix.shape})")
    # Every non-zero cell is a one: no NaN, no other value.
    if numpy.count_nonzero(matrix) != numpy.count_nonzero(matrix == 1):
        raise CoverError("the cross-similarity matrix is not binary")
    rows, columns = matrix.shape
    turned = _turned(rows, columns)
    # One where it matches, minus the penalty where it does not.
    gain = numpy.multiply(matrix.T if turned else matrix, 1 + _DISRUPTION, order="C")
    gain -= _DISRUPTION
    scores = numpy.zeros(gain.shape, numpy.float32)
    zero = numpy.float32(0)
    for i in range(2, len(scores)):
        cell = scores[i, 2:]
        numpy.maximum(scores[i - 1, 1:-1], scores[i - 2, 1:-1], out=cell)
        numpy.maximum(cell, scores[i - 1, :-2], out=cell)
        cell += gain[i, 2:]
        # A match is at least one, so only a disruption is floored.
        numpy.maximum(cell, zero, out=cell)
    if turned:
        scores = scores.T
    best = float(scores.max())
    # Essentia divides the double square root of the column count by the
    # float32 best score in double, and stores the quotient as float32.
    distance = float(numpy.float32(math.sqrt(columns) / best)) if best > 0 else math.inf
    return scores, distance


@dataclass(frozen=True)
class Alignment:
    """The best local alignment in a score matrix, as (row, column) cells:
    the match it starts at and the cell it ends at, and its score. Row r of a
    cross-similarity matrix is the query's stacked profiles r to r + 8, and
    column c the reference's."""
    start: tuple[int, int]
    end: tuple[int, int]
    score: float


def alignment(scores: numpy.ndarray) -> Alignment | None:
    """Where the alignment that Essentia's distance divides by lies in
    `serra09`'s score matrix, or None when nothing matched. It ends at the
    first cell, in row order, holding the highest score, the one Essentia
    finds; it starts where tracing back from there reaches a match that
    follows no score. Tracing back steps to a predecessor holding the score
    the cell was made from, preferring (i-1, j-1), then (i-2, j-1), then
    (i-1, j-2). Essentia keeps no path, so where two predecessors tie, the
    start is this order's choice among equally good ones."""
    end = numpy.unravel_index(int(numpy.argmax(scores)), scores.shape)
    score = float(scores[end])
    if score <= 0:
        return None
    i, j = int(end[0]), int(end[1])
    while True:
        here = float(scores[i, j])
        steps = ((i - 1, j - 1), (i - 2, j - 1), (i - 1, j - 2))
        before = [float(scores[cell]) for cell in steps]
        best = max(before)
        if best == 0 and here == 1:
            break
        i, j = steps[before.index(best)]
    return Alignment((i, j), (int(end[0]), int(end[1])), score)


class Analyser:
    """Essentia's algorithms, configured once with the method's settings,
    and the alignment's port."""

    def __init__(self) -> None:
        es = essentia_module()
        self.es = es
        self.windowing = es.Windowing(**_options(ANALYSIS["windowing"]))
        self.spectrum = es.Spectrum()
        self.peaks = es.SpectralPeaks(**_options(ANALYSIS["spectral_peaks"]))
        self.hpcp = es.HPCP(**_options(ANALYSIS["hpcp"]))
        self.cross = es.ChromaCrossSimilarity(**_options(ANALYSIS["chroma_cross_similarity"]))

    def features(self, audio: numpy.ndarray) -> Features:
        frames = [self.hpcp(*self.peaks(self.spectrum(self.windowing(frame))))
                  for frame in self.es.FrameGenerator(audio, frameSize=ANALYSIS["frame_size"],
                                                      hopSize=ANALYSIS["hop_size"])]
        stack = ANALYSIS["chroma_cross_similarity"]["frameStackSize"]
        if len(frames) <= stack:
            raise TooShort(f"{len(audio)} samples make {len(frames)} frames; the alignment needs more than {stack}")
        profiles = numpy.array(frames)
        return Features(profiles, tuple(float(value) for value in profiles.mean(axis=0)))

    def distance(self, a: numpy.ndarray, b: numpy.ndarray) -> float:
        _, ab = self.cover(self.cross(a, b))
        _, ba = self.cover(self.cross(b, a))
        return min(float(ab), float(ba))

    def cover(self, matrix: numpy.ndarray) -> tuple[numpy.ndarray, float]:
        """Essentia's `CoverSongSimilarity` answer, its score matrix and
        distance, from `serra09`."""
        return serra09(matrix)


def transposition_invariant_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """The cosine of two mean-chroma vectors at the best of twelve circular
    rotations, the same invariance the alignment's transposition index gives.
    It screens; it cannot rank (the V2 system measured no separation)."""
    norm_a = sum(value * value for value in a) ** 0.5 + 1e-9
    norm_b = sum(value * value for value in b) ** 0.5 + 1e-9
    unit_a = [value / norm_a for value in a]
    unit_b = [value / norm_b for value in b]
    best = None
    for shift in range(len(unit_b)):
        rotated = unit_b[-shift:] + unit_b[:-shift] if shift else unit_b
        score = sum(x * y for x, y in zip(unit_a, rotated, strict=True))
        best = score if best is None or score > best else best
    return float(best) if best is not None else 0.0


def prefilter(query: Sequence[float], corpus: dict[str, Sequence[float]], retention: Decimal) -> list[str]:
    """The corpus entries worth an alignment for this query: the closest
    `retention` share by mean chroma, rounded up, never none of a non-empty
    corpus. Ties go to the smaller identifier. Returned in identifier order."""
    ids = sorted(corpus)
    if not ids or retention >= 1:
        return ids
    scored = sorted(ids, key=lambda key: (-transposition_invariant_similarity(query, corpus[key]), key))
    keep = max(1, math.ceil(len(scored) * retention))
    kept = set(scored[:keep])
    return [key for key in ids if key in kept]


# -- the search --------------------------------------------------------------

@dataclass(frozen=True)
class Span:
    start_sample: int
    end_sample: int
    kind: str  # the suggested class


def runs_of(spans: Sequence[Span], classes: Sequence[str]) -> list[list[int]]:
    """Maximal sequences of consecutive spans whose class is one of
    `classes`, as span indices. Any other span ends a run."""
    runs: list[list[int]] = []
    current: list[int] = []
    for index, span in enumerate(spans):
        if span.kind in classes:
            current.append(index)
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    return runs


@dataclass(frozen=True)
class Distance:
    corpus_id: str
    raw: float
    value: Decimal  # rounded to the score's places


def unaligned(distance: float) -> bool:
    """Whether Essentia found no alignment at all. Its serra09 distance
    divides by the best alignment score, so a cross-similarity with no match
    in either direction answers positive infinity. That is an answer, the
    farthest there is, not a failure; anything else non-finite still is."""
    return distance == math.inf


def ranked(raw: dict[str, float]) -> tuple[list[Distance], list[str]]:
    """The aligned entries closest first, ties by the raw float, then by
    identifier; and the entries that did not align, sorted."""
    aligned = {key: value for key, value in raw.items() if not unaligned(value)}
    order = sorted(aligned.items(), key=lambda kv: (kv[1], kv[0]))
    return ([Distance(key, value, rounded(value)) for key, value in order],
            sorted(key for key in raw if key not in aligned))


def margin(distances: list[Distance]) -> Decimal | None:
    """How far the closest entry beat the next, over every aligned entry;
    None without a second. Reported to rank by, never a gate."""
    return distances[1].value - distances[0].value if len(distances) > 1 else None


def candidate_windows(run: list[int], spans: Sequence[Span], sample_rate: int) -> list[tuple[int, dict[str, int]]]:
    """Every window the search may measure for a run, by step: from the
    run's first span to each later span's end, up to the limit, less those
    too short to measure (which are not steps)."""
    shortest = SEARCH["min_window_seconds"] * sample_rate
    windows = []
    for step in range(min(len(run), SEARCH["max_window_spans"])):
        window = {"start_sample": spans[run[0]].start_sample, "end_sample": spans[run[step]].end_sample}
        if window["end_sample"] - window["start_sample"] >= shortest:
            windows.append((step, window))
    return windows


WindowFeatures = Callable[[int, int], Features]
Distances = Callable[[Features, list[str]], dict[str, float]]


def search_run(run: list[int], spans: Sequence[Span], sample_rate: int, window_features: WindowFeatures,
               corpus: dict[str, Features], distances: Distances) -> dict[str, Any]:
    """Grow a window from the run's first span and keep its best step.

    `window_features(start, end)` measures the source's samples `[start,
    end)`; `distances(features, ids)` compares them with those corpus entries.
    Returns every evaluated step, and the best step's window with every
    aligned distance there and the entries that did not align, or `best:
    None` when no window was long enough, the corpus was empty, or nothing
    aligned."""
    retention = Decimal(SEARCH["prefilter_retention"])
    degrade = Decimal(SEARCH["degrade_margin"])
    means = {key: features.mean for key, features in corpus.items()}
    best: tuple[int, dict[str, int], list[Distance], list[str]] | None = None
    stalled = 0
    steps: list[dict[str, Any]] = []
    for step, window in candidate_windows(run, spans, sample_rate):
        features = window_features(window["start_sample"], window["end_sample"])
        kept = prefilter(features.mean, means, retention)
        compared, apart = ranked(distances(features, kept))
        steps.append({"spans": [run[0], run[step]], "window": window, "compared": len(compared) + len(apart),
                      "unaligned": len(apart),
                      "closest": ({"corpus": compared[0].corpus_id, "distance": str(compared[0].value)}
                                  if compared else None)})
        if not compared:
            # Nothing compared, or nothing aligned. Nothing aligning is
            # worse than any best by more than the margin.
            if best is not None and apart:
                break
            continue
        if best is None or compared[0].value < best[2][0].value:
            best, stalled = (step, window, compared, apart), 0
            continue
        stalled += 1
        if compared[0].value > best[2][0].value + degrade or stalled >= SEARCH["stall_steps"]:
            break
    result: dict[str, Any] = {"spans": [run[0], run[-1]], "steps": steps, "best": None}
    if best is not None:
        step, window, compared, apart = best
        result["best"] = {"step": step, "spans": [run[0], run[step]], "window": window,
                          "margin": None if margin(compared) is None else str(margin(compared)),
                          "distances": compared, "unaligned": apart}
    return result


# -- comparing in parallel ---------------------------------------------------

_ANALYSER: Analyser | None = None
_CORPUS: dict[str, numpy.ndarray] = {}


def _start_worker(corpus: dict[str, numpy.ndarray]) -> None:
    global _ANALYSER, _CORPUS
    _ANALYSER = Analyser()
    _CORPUS = corpus


def _compare(profiles: numpy.ndarray, key: str) -> float:
    assert _ANALYSER is not None
    return _ANALYSER.distance(profiles, _CORPUS[key])


def _features_of(pieces: tuple[Piece, ...], ffmpeg: str, ffprobe: str) -> Features:
    global _ANALYSER
    if _ANALYSER is None:
        _ANALYSER = Analyser()
    return _ANALYSER.features(load(pieces, ffmpeg, ffprobe))


class Comparer:
    """Measures audio and compares profiles, `jobs` processes at a time, or
    in this process when `jobs` is 1. The results do not depend on `jobs`."""

    def __init__(self, jobs: int, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe"):
        if jobs < 1:
            raise CoverError("jobs must be at least 1")
        self.jobs, self.ffmpeg, self.ffprobe = jobs, ffmpeg, ffprobe
        self.analyser = Analyser()
        self.pool: ProcessPoolExecutor | None = None
        self.corpus: dict[str, Features] = {}

    def __enter__(self) -> "Comparer":
        return self

    def __exit__(self, *_: Any) -> None:
        if self.pool is not None:
            self.pool.shutdown(cancel_futures=True)

    def features(self, pieces: Sequence[Piece]) -> Features:
        return self.analyser.features(load(pieces, self.ffmpeg, self.ffprobe))

    def measure_corpus(self, corpus: dict[str, tuple[Piece, ...]]) -> dict[str, Features | CoverError]:
        """Every corpus entry's features, or the error that kept it from
        being measured, and the workers that compare against them."""
        measured: dict[str, Features | CoverError] = {}
        keys = sorted(corpus)
        if self.jobs == 1:
            for key in keys:
                try:
                    measured[key] = self.features(corpus[key])
                except CoverError as exc:
                    measured[key] = exc
        else:
            with ProcessPoolExecutor(max_workers=self.jobs) as pool:
                futures = {key: pool.submit(_features_of, corpus[key], self.ffmpeg, self.ffprobe) for key in keys}
                for key in keys:
                    try:
                        measured[key] = futures[key].result()
                    except CoverError as exc:
                        measured[key] = exc
        self.use_corpus({key: value for key, value in measured.items() if isinstance(value, Features)})
        return measured

    def measure_windows(self, path: Path, sample_rate: int, windows: list[tuple[int, int]]
                        ) -> dict[tuple[int, int], Features]:
        """The features of each window `[start, end)` of one file."""
        pieces = {window: (Piece(path, window[0], window[1], sample_rate),) for window in windows}
        if self.jobs == 1:
            return {window: self.features(piece) for window, piece in pieces.items()}
        with ProcessPoolExecutor(max_workers=self.jobs) as pool:
            futures = {window: pool.submit(_features_of, piece, self.ffmpeg, self.ffprobe)
                       for window, piece in pieces.items()}
            return {window: future.result() for window, future in futures.items()}

    def use_corpus(self, corpus: dict[str, Features]) -> None:
        """Compare against these features, measured here or elsewhere."""
        if self.pool is not None:
            self.pool.shutdown(cancel_futures=True)
            self.pool = None
        self.corpus = corpus
        if self.jobs > 1:
            self.pool = ProcessPoolExecutor(max_workers=self.jobs, initializer=_start_worker,
                                            initargs=({key: f.profiles for key, f in corpus.items()},))

    def distances(self, features: Features, keys: list[str]) -> dict[str, float]:
        if self.pool is None:
            return {key: self.analyser.distance(features.profiles, self.corpus[key].profiles) for key in keys}
        futures = {key: self.pool.submit(_compare, features.profiles, key) for key in keys}
        return {key: future.result() for key, future in futures.items()}


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) < 2:
        print("usage: cover.py <query> <reference>...", file=sys.stderr)
        return 2
    try:
        analyser = Analyser()

        def whole(path: str) -> Features:
            completed = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", path, "-map", "0:a:0", "-ac", "1",
                                        "-ar", str(ANALYSIS["analysis_rate"]), "-c:a", "pcm_s16le", "-f", "s16le",
                                        "-"], capture_output=True)
            if completed.returncode != 0:
                raise CoverError(f"ffmpeg could not decode {path}")
            samples = numpy.frombuffer(completed.stdout, dtype="<i2").astype(numpy.float32) / numpy.float32(32768)
            return analyser.features(samples)
        query = whole(arguments[0])
        distances = {path: analyser.distance(query.profiles, whole(path).profiles) for path in arguments[1:]}
        # null: no alignment at all.
        scores = {path: None if unaligned(value) else str(rounded(value)) for path, value in distances.items()}
    except CoverError as exc:
        print(json.dumps({"status": "failed", "detail": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps({"method": {"id": METHOD_ID, "version": METHOD_VERSION}, "scale": SCALE,
                      "direction": DIRECTION, "scores": scores}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
