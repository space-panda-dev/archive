"""The whole-recording window search (ADR 0072, amended 2026-10-18).

A tool, not an Archive client: it takes a recording's profiles, its
suggested spans, and a corpus of profiles, and returns plain data. `enrich
match-windows` and `calibrate_windows.py` both search through
`search_recording`.

1. **Candidate pass** (`candidates_of`): every corpus occurrence is compared
   along the recording at the quarter rate, in chunks 2 occurrence-lengths
   long, one starting every occurrence-length. Up to 2 disjoint alignments
   are traced per chunk; one within the hit distance is a hit.
2. **Regions** (`hits_of`, `regions_of`): an occurrence's overlapping hits
   merge into the closer, and hits of every occurrence gather into regions,
   each with its 5 closest Works.
3. **Snapping** (`snap`): a region becomes windows of whole `music` and
   `unknown` spans, an outer and an inner variant; a `talk` or `quiet` span
   between two of them does not split them.
4. **Claim rounds** (`claim_rounds`): each window is compared by the
   quarter-rate distance with its candidate Works' occurrences; the closest
   is kept and claims its spans, and the other regions snap again.
5. **Decision** (`decide`): each kept window is compared with every corpus
   occurrence by the quarter-rate distance. A near tie is decided again by
   the exact distance on freshly measured audio.

Every value the search uses is in `cover.SEARCH`, which the run records.
Nothing here depends on the number of processes.
"""

from __future__ import annotations

import math
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Sequence

import numpy

import cover

AR = cover.ANALYSIS["analysis_rate"]
HOP = cover.ANALYSIS["hop_size"]
STACK = cover.ANALYSIS["chroma_cross_similarity"]["frameStackSize"]

# Fresh profiles of a window [start, end) of the recording, or None where
# they cannot be measured (a calibration workset holds no audio).
WindowFeatures = Callable[[int, int], "cover.Features | None"]
# The exact distances of profiles to these corpus entries.
ExactDistances = Callable[[cover.Features, list[str]], dict[str, float]]


def overlap(a: tuple[int, int], b: tuple[int, int]) -> int:
    return max(0, min(a[1], b[1]) - max(a[0], b[0]))


class Recording:
    """A whole recording's profiles and how its frames map to its samples."""

    def __init__(self, profiles: numpy.ndarray, sample_rate: int):
        self.profiles = numpy.ascontiguousarray(profiles, dtype=numpy.float32)
        if self.profiles.ndim != 2 or self.profiles.shape[1] != 12:
            raise cover.CoverError(f"profiles of shape {self.profiles.shape} are not (12 bins) frames")
        self.rate = sample_rate
        self.frames = len(self.profiles)

    def sample_of(self, frame: float) -> int:
        """The source sample at the centre of analysis frame `frame`."""
        return round(frame * HOP * self.rate / AR)

    @property
    def covered(self) -> tuple[int, int]:
        return 0, self.sample_of(self.frames - 1)

    def window(self, start: int, end: int) -> numpy.ndarray:
        """The profiles of samples `[start, end)` as a slice: frame
        round(start at the analysis rate / hop) and the frame count a fresh
        measurement of the window would have. Not bit-identical to a fresh
        measurement unless `start` falls on the frame grid."""
        offset = start * AR / self.rate
        first = round(offset / HOP)
        length = round((end - start) * AR / self.rate)
        count = math.ceil(length / HOP) + 1
        return self.profiles[max(0, first):first + count]


# -- 1. the candidate pass ----------------------------------------------------

def stacks(x: numpy.ndarray) -> tuple[numpy.ndarray, numpy.ndarray]:
    """The candidate pass's stacks, n - 9 of them, and their squared norms."""
    count = max(0, len(x) - STACK)
    s = numpy.ascontiguousarray(numpy.concatenate([x[o:o + count] for o in range(STACK)], axis=1))
    return s, numpy.einsum("ij,ij->i", s, s)


def chunk_starts(frames: int, length: int, hop: int) -> list[int]:
    """Where each chunk starts: every `hop` frames, the last ending at the
    recording's end; one chunk when the recording is no longer than one."""
    if frames <= length:
        return [0]
    starts = list(range(0, frames - length, hop))
    starts.append(frames - length)
    return starts


def trace(scores: numpy.ndarray, end: tuple[int, int]) -> numpy.ndarray:
    """The path back from `end`, as cover.alignment traces it, start first."""
    i, j = end
    cells = [(i, j)]
    while True:
        here = float(scores[i, j])
        steps = ((i - 1, j - 1), (i - 2, j - 1), (i - 1, j - 2))
        before = [float(scores[c]) if c[0] >= 0 and c[1] >= 0 else 0.0 for c in steps]
        best = max(before)
        if best == 0 and here == 1:
            break
        i, j = steps[before.index(best)]
        cells.append((i, j))
    return numpy.array(cells[::-1], dtype=numpy.int32)


@dataclass(frozen=True)
class Alignment:
    """One traced alignment of a corpus occurrence: the recording frames it
    spans, `[start, end)` at the native rate, and its distance."""
    key: str
    frames: tuple[int, int]
    distance: float


def candidates_of(recording: numpy.ndarray, recording_stacks: tuple[numpy.ndarray, numpy.ndarray],
                  native_frames: int, key: str, reference: numpy.ndarray, search: dict[str, Any] | None = None
                  ) -> tuple[list[Alignment], int]:
    """One corpus occurrence along the quarter-rate recording: its
    alignments, and the matrix cells compared. `reference` is the
    occurrence's quarter-rate profiles."""
    search = cover.SEARCH if search is None else search
    group = search["quarter_rate_frames"]
    columns = len(reference) - STACK
    if columns <= 1 or len(recording) <= STACK + 1:
        return [], 0
    length = max(STACK + 2, round(float(search["chunk_length"]) * len(reference)))
    step = max(1, round(float(search["chunk_hop"]) * len(reference)))
    turned: dict[int, tuple[numpy.ndarray, numpy.ndarray]] = {}
    all_a, all_an = recording_stacks
    found: list[Alignment] = []
    cells = 0
    for start in chunk_starts(len(recording), length, step):
        piece = recording[start:start + length]
        if len(piece) <= STACK + 1:
            continue
        rows = len(piece) - STACK
        index = cover.transposition_index(piece, reference) % 12
        if index not in turned:
            turned[index] = stacks(numpy.roll(reference, index, axis=1))
        b, bn = turned[index]
        matrix = cover.binary_within(all_a[start:start + rows], all_an[start:start + rows], b, bn, shift=0)
        scores, _ = cover.serra09(matrix)
        cells += matrix.size
        best_rows = scores.max(axis=1)
        taken: list[tuple[int, int]] = []
        for _ in range(search["alignments_per_chunk"]):
            row = int(numpy.argmax(best_rows))
            score = float(best_rows[row])
            if score <= 0:
                break
            if not taken:
                first = cover.alignment(scores)
                assert first is not None
                end = first.end
            else:
                end = (row, int(numpy.argmax(scores[row])))
            path = trace(scores, end)
            r0, r1 = int(path[0, 0]), int(path[-1, 0])
            if any(r0 <= b_ and a_ <= r1 for a_, b_ in taken):
                break
            taken.append((r0, r1))
            best_rows[r0:r1 + 1] = 0
            found.append(Alignment(key, (group * (start + r0), min(native_frames, group * (start + r1 + STACK))),
                                   float(numpy.float32(math.sqrt(columns) / score))))
    return found, cells


_HELD: dict[str, Any] = {}


def _hold(recording: numpy.ndarray, native_frames: int, search: dict[str, Any]) -> None:
    _HELD.update(recording=recording, stacks=stacks(recording), frames=native_frames, search=search)


def _candidates(key: str, reference: numpy.ndarray) -> tuple[list[Alignment], int]:
    return candidates_of(_HELD["recording"], _HELD["stacks"], _HELD["frames"], key, reference, _HELD["search"])


def candidate_pass(recording: Recording, corpus: dict[str, numpy.ndarray], jobs: int = 1,
                   search: dict[str, Any] | None = None) -> tuple[dict[str, list[Alignment]], int]:
    """Every corpus occurrence's alignments along the recording, by
    occurrence, and the cells compared. `jobs` processes share the
    occurrences; the answer is the same for any number."""
    search = cover.SEARCH if search is None else search
    group = search["quarter_rate_frames"]
    reduced = cover.quarter_rate(recording.profiles, group)
    keys = sorted(corpus)
    references = {key: cover.quarter_rate(corpus[key], group) for key in keys}
    if jobs == 1 or len(keys) < 2:
        _hold(reduced, recording.frames, search)
        try:
            answers = {key: _candidates(key, references[key]) for key in keys}
        finally:
            _HELD.clear()
    else:
        with ProcessPoolExecutor(max_workers=jobs, initializer=_hold,
                                 initargs=(reduced, recording.frames, search)) as pool:
            futures = {key: pool.submit(_candidates, key, references[key]) for key in keys}
            answers = {key: futures[key].result() for key in keys}
    return {key: answers[key][0] for key in keys}, sum(cells for _, cells in answers.values())


# -- 2. hits and regions --------------------------------------------------------

def overlaps_half(a: tuple[int, int], b: tuple[int, int]) -> bool:
    """Whether two extents overlap by at least half the shorter."""
    shorter = min(a[1] - a[0], b[1] - b[0])
    return shorter > 0 and overlap(a, b) * 2 >= shorter


def overlaps_mutually(a: tuple[int, int], b: tuple[int, int]) -> bool:
    """Whether two extents overlap by at least half the longer."""
    longer = max(a[1] - a[0], b[1] - b[0])
    return longer > 0 and overlap(a, b) * 2 >= longer


def hits_of(alignments: list[Alignment], hit_distance: float) -> list[Alignment]:
    """One occurrence's hits: its alignments within the hit distance,
    closest first, each dropped when it overlaps a closer one by at least
    half the shorter."""
    found = sorted((a for a in alignments if a.distance <= hit_distance), key=lambda a: (a.distance, a.frames[0]))
    kept: list[Alignment] = []
    for hit in found:
        if not any(overlaps_half(hit.frames, k.frames) for k in kept):
            kept.append(hit)
    return kept


@dataclass
class Region:
    """Hits of several occurrences over one stretch of the recording: the
    frames of its first (closest) hit, and its candidate Works."""
    frames: tuple[int, int]
    hits: list[Alignment]
    works: list[str] = field(default_factory=list)


def regions_of(hits: list[Alignment], works_of: dict[str, Sequence[str]], keep: int) -> list[Region]:
    """Hits taken closest first; each joins the first region it overlaps by
    at least half the longer, or starts one. A region's Works are its hits'
    Works, ranked by the closest hit naming each, at most `keep`."""
    regions: list[Region] = []
    for hit in sorted(hits, key=lambda h: (h.distance, h.key)):
        for region in regions:
            if overlaps_mutually(hit.frames, region.frames):
                region.hits.append(hit)
                break
        else:
            regions.append(Region(hit.frames, [hit]))
    for region in regions:
        works: dict[str, float] = {}
        for hit in region.hits:
            for work in works_of[hit.key]:
                works.setdefault(work, hit.distance)
        region.works = [w for w, _ in sorted(works.items(), key=lambda kv: (kv[1], kv[0]))[:keep]]
    return regions


# -- 3. snapping ------------------------------------------------------------------

def snap(frames: tuple[int, int], recording: Recording, spans: Sequence[cover.Span], claimed: set[int],
         search: dict[str, Any] | None = None) -> dict[str, tuple[int, int]]:
    """A region's windows of whole spans: of the unclaimed spans of the
    search's classes that it overlaps, the contiguous group (split by a
    claimed span, not by another class) it overlaps most; its outer extent,
    and its inner one from the first to the last span the region covers at
    least half of. A variant under the shortest window is dropped."""
    search = cover.SEARCH if search is None else search
    lo, hi = recording.covered
    hit = (recording.sample_of(frames[0]), recording.sample_of(frames[1] - 1))
    touched = [i for i, s in enumerate(spans) if s.kind in search["span_classes"] and s.start_sample >= lo
               and s.end_sample <= hi + HOP * recording.rate // AR and i not in claimed
               and overlap(hit, (s.start_sample, s.end_sample)) > 0]
    groups: list[list[int]] = []
    for i in touched:
        if groups and not any(c in claimed for c in range(groups[-1][-1] + 1, i)):
            groups[-1].append(i)
        else:
            groups.append([i])
    if not groups:
        return {}

    def amount(group: list[int]) -> int:
        return sum(overlap(hit, (spans[i].start_sample, spans[i].end_sample)) for i in group)
    group = max(groups, key=lambda g: (amount(g), -g[0]))
    variants = {"outer": (spans[group[0]].start_sample, spans[group[-1]].end_sample)}
    inner = [i for i in group if overlap(hit, (spans[i].start_sample, spans[i].end_sample)) * 2
             >= spans[i].end_sample - spans[i].start_sample]
    variants["inner"] = (spans[inner[0]].start_sample, spans[inner[-1]].end_sample) if inner else variants["outer"]
    shortest = search["min_window_seconds"] * recording.rate
    return {name: w for name, w in variants.items() if w[1] - w[0] >= shortest}


# -- 4. claim rounds ----------------------------------------------------------------

@dataclass
class Window:
    """A snapped window: its candidate Works, and its quarter-rate distances
    to their occurrences, measured once when it was first snapped."""
    window: tuple[int, int]
    regions: set[int] = field(default_factory=set)
    works: set[str] = field(default_factory=set)
    distances: dict[str, float] | None = None
    closest: tuple[float, str] | None = None


def allowed_of(works: set[str], corpus: Sequence[str], works_of: dict[str, Sequence[str]]) -> list[str]:
    """The corpus occurrences of these Works, sorted."""
    return sorted(k for k in corpus if set(works_of[k]) & works)


def claim_rounds(regions: list[Region], recording: Recording, spans: Sequence[cover.Span],
                 hits: list[Alignment], works_of: dict[str, Sequence[str]], quarter: cover.QuarterRate,
                 search: dict[str, Any] | None = None) -> tuple[list[tuple[int, int]], dict[tuple[int, int], Window]]:
    """The kept windows in the order they were kept, and every window
    snapped on the way. Each round keeps the window closest to one of its
    candidate Works' occurrences, claims the spans inside it, and snaps the
    other regions again; a region left with no window is absorbed."""
    search = cover.SEARCH if search is None else search
    group = search["quarter_rate_frames"]
    hit_samples = [((recording.sample_of(h.frames[0]), recording.sample_of(h.frames[1] - 1)), h) for h in hits]
    windows: dict[tuple[int, int], Window] = {}

    def window_works(w: tuple[int, int]) -> set[str]:
        best: dict[str, float] = {}
        for extent, h in hit_samples:
            if overlap(extent, w) * 2 >= min(extent[1] - extent[0], w[1] - w[0]):
                for work in works_of[h.key]:
                    best[work] = min(best.get(work, math.inf), h.distance)
        return {k for k, _ in sorted(best.items(), key=lambda kv: (kv[1], kv[0]))[:search["works_per_window"]]}

    def register(r: int, variants: dict[str, tuple[int, int]]) -> None:
        for w in variants.values():
            entry = windows.setdefault(w, Window(w))
            entry.regions.add(r)
            entry.works |= set(regions[r].works) | window_works(w)

    def measure() -> None:
        for w, entry in windows.items():
            if entry.distances is not None:
                continue
            keys = allowed_of(entry.works, quarter.corpus, works_of)
            try:
                entry.distances = quarter.distances(cover.quarter_rate(recording.window(*w), group), keys)
            except cover.TooShort:
                entry.distances = {}
            finite = [(v, k) for k, v in entry.distances.items() if v != math.inf]
            entry.closest = min(finite) if finite else None

    variants_of = {}
    for r, region in enumerate(regions):
        variants_of[r] = snap(region.frames, recording, spans, set(), search)
        register(r, variants_of[r])
    measure()
    claimed: set[int] = set()
    kept: list[tuple[int, int]] = []
    pending = [r for r in range(len(regions)) if variants_of[r]]
    while pending:
        options = [(windows[w].closest[0], w, r) for r in pending for w in variants_of[r].values()
                   if windows[w].closest is not None]
        if not options:
            break
        _, w, r = min(options)
        kept.append(w)
        pending.remove(r)
        claimed |= {i for i, s in enumerate(spans) if s.start_sample >= w[0] and s.end_sample <= w[1]}
        for other in list(pending):
            variants = snap(regions[other].frames, recording, spans, claimed, search)
            if variants != variants_of[other]:
                variants_of[other] = variants
                if variants:
                    register(other, variants)
                else:
                    pending.remove(other)
        measure()
    return kept, windows


# -- 5. the decision ------------------------------------------------------------------

@dataclass(frozen=True)
class Decision:
    """How a kept window was decided. `path` is "quarter_rate", "exact"
    (a confirmed near tie), or "unmeasured" (a near tie whose fresh profiles
    were not available); `distances` are that path's, closest first."""
    window: tuple[int, int]
    path: str
    distances: list[cover.Distance]
    unaligned: list[str]
    too_short: list[str]
    work_margin: float | None
    suggested: bool
    work: str | None  # the closest Work, when suggested
    corpus: list[str]  # that Work's occurrences that aligned on the deciding path
    quarter_rate: list[cover.Distance]  # the quarter-rate distances, closest first
    compared_exactly: list[str]


def per_work(distances: list[cover.Distance], works_of: dict[str, Sequence[str]]) -> list[tuple[float, str]]:
    """Each Work's closest raw distance, closest first, ties by Work."""
    best: dict[str, float] = {}
    for d in distances:
        for work in works_of[d.corpus_id]:
            best.setdefault(work, d.raw)
    return sorted((v, w) for w, v in best.items())


def decide(window: tuple[int, int], candidates: set[str], recording: Recording, quarter: cover.QuarterRate,
           means: dict[str, tuple[float, ...]], works_of: dict[str, Sequence[str]], window_features: WindowFeatures,
           exact: ExactDistances, search: dict[str, Any] | None = None) -> Decision:
    """A kept window compared with every corpus occurrence by the
    quarter-rate distance, and suggested when its closest Work is within the
    suggestion distance. A suggestion whose closest Work beats the next by
    less than the near-tie margin is decided again by the exact distance on
    fresh profiles, against the candidate Works' occurrences and the half of
    the corpus the prefilter keeps; the exact distance runs for no other
    window."""
    search = cover.SEARCH if search is None else search
    keys = sorted(quarter.corpus)
    query = cover.quarter_rate(recording.window(*window), search["quarter_rate_frames"])
    try:
        raw = quarter.distances(query, keys)
        too_short = quarter.too_short(keys)
    except cover.TooShort:
        raw, too_short = {}, keys
    compared, apart = cover.ranked(raw)
    works = per_work(compared, works_of)
    margin = works[1][0] - works[0][0] if len(works) > 1 else None
    suggested = bool(compared) and compared[0].value <= Decimal(search["suggestion_distance"])
    common = {"window": window, "too_short": too_short, "quarter_rate": compared}
    if suggested and margin is not None and margin < float(search["near_tie_margin"]):
        fresh = window_features(*window)
        if fresh is None:
            return Decision(**common, path="unmeasured", distances=[], unaligned=[], work_margin=margin,
                            suggested=False, work=None, corpus=[], compared_exactly=[])
        wanted = set(allowed_of(candidates, keys, works_of))
        wanted |= set(cover.prefilter(fresh.mean, means, Decimal(search["prefilter_retention"])))
        exactly = sorted(wanted)
        compared, apart = cover.ranked(exact(fresh, exactly))
        works = per_work(compared, works_of)
        margin = works[1][0] - works[0][0] if len(works) > 1 else None
        suggested = bool(compared) and compared[0].value <= Decimal(search["exact_suggestion_distance"])
        path = "exact"
    else:
        exactly, path = [], "quarter_rate"
    work = works[0][1] if suggested else None
    corpus = sorted(d.corpus_id for d in compared if work in works_of[d.corpus_id]) if work else []
    return Decision(**common, path=path, distances=compared, unaligned=apart, work_margin=margin,
                    suggested=suggested, work=work, corpus=corpus, compared_exactly=exactly)


# -- the whole search --------------------------------------------------------------------

def search_recording(recording: Recording, spans: Sequence[cover.Span], corpus: dict[str, cover.Features],
                     works_of: dict[str, Sequence[str]], window_features: WindowFeatures, exact: ExactDistances,
                     jobs: int = 1, search: dict[str, Any] | None = None) -> dict[str, Any]:
    """Search one recording for the corpus's Works.

    `corpus` holds each allowed occurrence's features, and nothing
    else: an occurrence from the recording's session must already be left
    out. `window_features(start, end)` measures a window afresh for an exact
    confirmation; `exact(features, keys)` gives exact distances.

    Returns the kept windows' decisions in start-sample order; every
    region with its Works and every snapped window with its candidate Works;
    the numbers of alignments traced and of hits; the corpus entries too short to compare; and counts of
    the work done."""
    search = cover.SEARCH if search is None else search
    profiles = {key: features.profiles for key, features in corpus.items()}
    means = {key: features.mean for key, features in corpus.items()}
    alignments, cells = candidate_pass(recording, profiles, jobs, search)
    hit_distance = float(search["hit_distance"])
    hits = [h for key in sorted(alignments) for h in hits_of(alignments[key], hit_distance)]
    regions = regions_of(hits, works_of, search["works_per_region"])
    quarter = cover.QuarterRate(profiles)
    kept, windows = claim_rounds(regions, recording, spans, hits, works_of, quarter, search)
    claim_comparisons = quarter.comparisons
    decisions = [decide(w, windows[w].works, recording, quarter, means, works_of, window_features, exact, search)
                 for w in sorted(kept)]
    return {"decisions": decisions, "alignments": sum(len(found) for found in alignments.values()), "hits": len(hits),
            "regions": [{"frames": list(r.frames), "works": r.works} for r in regions],
            "windows": [{"window": list(w), "works": sorted(windows[w].works), "kept": w in kept}
                        for w in sorted(windows)],
            "candidate_cells": cells, "claim_comparisons": claim_comparisons,
            "decision_comparisons": quarter.comparisons - claim_comparisons,
            "too_short": quarter.too_short(sorted(corpus))}
