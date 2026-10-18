"""The whole-recording window search (ADR 0072, amendment of 2026-10-18).

The recording and the corpus are synthetic audio measured by the real
writers: `cover.measure_whole` for the recording, `cover.Analyser.features`
for each occurrence. Snapping and the merging of hits are pure rules and are
tested on stated extents."""

from __future__ import annotations

import json
import math
import random
import tempfile
import unittest
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import cover  # first: it pins BLAS to one thread before numpy loads it
import numpy
import support
import windows
from support import needs_ffmpeg
from test_cover import needs_essentia

RATE = 44100


def _bomb(*_, **__):
    raise AssertionError("must not be called")


class Synthetic:
    """A recording of progression P (transposed up two), talk, then
    progression Q, between room floors; occurrences of P in other keys and
    rates (Work X), of Q (Work Y), and of P once more for Work Z, which makes
    a near tie wherever Z is in the corpus."""

    cached: dict = {}

    @classmethod
    def build(cls, test: unittest.TestCase) -> "Synthetic":
        if "built" in cls.cached:
            return cls.cached["built"]
        self = cls()
        # Measured once for the module; the audio stays for fresh decodes.
        tmp = Path(tempfile.mkdtemp(prefix="windows-", dir="/tmp"))
        unittest.addModuleCleanup(support.remove_tree, tmp)
        second = RATE
        p_up = support.progression_samples(support.PROGRESSION, RATE, transpose=2, seed=1)
        q = support.progression_samples(support.OTHER_PROGRESSION, RATE, seed=2)
        floor = [0.0] * second
        talk = [0.05 * ((i * 7919) % 200 - 100) / 100 for i in range(4 * second)]
        samples = [*floor, *p_up, *talk, *q, *floor]
        self.path = support.write_wav(tmp / "recording.wav", samples, RATE)
        self.analyser = cover.Analyser()
        self.profiles = cover.measure_whole(self.path, RATE, len(samples), analyser=self.analyser)
        self.recording = windows.Recording(self.profiles, RATE)
        self.p = (second, second + len(p_up))
        self.q = (self.p[1] + len(talk), self.p[1] + len(talk) + len(q))
        half_p, half_q = sum(self.p) // 2, sum(self.q) // 2
        self.spans = [cover.Span(0, self.p[0], "quiet"), cover.Span(self.p[0], half_p, "music"),
                      cover.Span(half_p, self.p[1], "music"), cover.Span(self.p[1], self.q[0], "talk"),
                      cover.Span(self.q[0], half_q, "music"), cover.Span(half_q, self.q[1], "unknown"),
                      cover.Span(self.q[1], len(samples), "quiet")]

        def occurrence(name: str, chords, rate: int, transpose: int, seed: int) -> cover.Features:
            audio = support.progression_samples(chords, rate, transpose=transpose, seed=seed)
            path = support.write_wav(tmp / f"{name}.wav", audio, rate)
            return self.analyser.features(cover.load([cover.Piece(path, 0, len(audio), rate)]))
        self.everything = {"x1": occurrence("x1", support.PROGRESSION, 48000, 0, 3),
                           "x2": occurrence("x2", support.PROGRESSION, RATE, -2, 4),
                           "y1": occurrence("y1", support.OTHER_PROGRESSION, 48000, 0, 5),
                           "z1": occurrence("z1", support.PROGRESSION, RATE, 5, 6)}
        self.works_of = {"x1": ["work-x"], "x2": ["work-x"], "y1": ["work-y"], "z1": ["work-z"]}
        cls.cached["built"] = self
        return self

    def corpus(self, *keys: str) -> dict[str, cover.Features]:
        return {key: self.everything[key] for key in (keys or ("x1", "x2", "y1"))}

    def fresh(self, start: int, end: int) -> cover.Features:
        return self.analyser.features(cover.load([cover.Piece(self.path, start, end, RATE)]))

    def exact(self, corpus: dict[str, cover.Features]):
        def distances(features: cover.Features, keys: list[str]) -> dict[str, float]:
            return {key: self.analyser.distance(features.profiles, corpus[key].profiles) for key in keys}
        return distances

    def search(self, corpus: dict[str, cover.Features] | None = None, search: dict | None = None,
               window_features=None, exact=None, jobs: int = 1) -> dict:
        corpus = self.corpus() if corpus is None else corpus
        return windows.search_recording(self.recording, self.spans, corpus, self.works_of,
                                         window_features or _bomb, exact or _bomb, jobs, search)


def frames_of(extent: tuple[int, int]) -> tuple[int, int]:
    return round(extent[0] * 22050 / RATE / 2048), round(extent[1] * 22050 / RATE / 2048)


@needs_essentia
@needs_ffmpeg
class CandidatePass(unittest.TestCase):
    def setUp(self) -> None:
        self.s = Synthetic.build(self)

    def test_a_planted_song_is_found_where_it_is_and_not_elsewhere(self) -> None:
        profiles = {k: f.profiles for k, f in self.s.corpus().items()}
        alignments, cells = windows.candidate_pass(self.s.recording, profiles)
        self.assertGreater(cells, 0)
        limit = float(cover.SEARCH["hit_distance"])
        p, q = frames_of(self.s.p), frames_of(self.s.q)

        def inside(a: windows.Alignment, extent: tuple[int, int]) -> bool:
            return windows.overlap(a.frames, extent) * 2 >= a.frames[1] - a.frames[0]
        x_hits = [a for a in alignments["x1"] if a.distance <= limit]
        y_hits = [a for a in alignments["y1"] if a.distance <= limit]
        self.assertTrue(x_hits and all(inside(a, p) for a in x_hits), alignments["x1"])
        self.assertTrue(y_hits and all(inside(a, q) for a in y_hits), alignments["y1"])
        # Anchor: P's occurrence does align over Q too, just not as a hit.
        far = [a.distance for a in alignments["y1"] if inside(a, p)]
        self.assertTrue(far and min(far) > limit, alignments["y1"])

    def test_chunks_start_every_hop_and_the_last_ends_at_the_end(self) -> None:
        self.assertEqual(windows.chunk_starts(10, 20, 5), [0])
        self.assertEqual(windows.chunk_starts(20, 20, 5), [0])
        self.assertEqual(windows.chunk_starts(21, 20, 5), [0, 1])
        self.assertEqual(windows.chunk_starts(47, 20, 10), [0, 10, 20, 27])
        self.assertEqual(windows.chunk_starts(50, 20, 10), [0, 10, 20, 30])

    def candidates(self, profiles: numpy.ndarray, search: dict) -> list[windows.Alignment]:
        reduced = cover.quarter_rate(profiles)
        reference = cover.quarter_rate(self.s.everything["x1"].profiles)
        found, _ = windows.candidates_of(reduced, windows.stacks(reduced), len(profiles), "x1", reference, search)
        return found

    def test_a_chunk_traces_two_disjoint_alignments_and_no_more(self) -> None:
        # The tracing on a stated matrix: two diagonal runs of 31 matches. A
        # score decays by half a point a row after its last match, so the
        # second run outscores the first's tail; the first in row order is
        # traced first.
        rng = numpy.random.default_rng(8)
        recording = rng.random((4 * 145, 12), dtype=numpy.float32)
        reference = rng.random((4 * 60, 12), dtype=numpy.float32)

        def stated(a, an, b, bn, shift):
            matrix = numpy.zeros((len(a), len(b)), numpy.float32)
            for i in range(31):
                matrix[5 + i, 2 + i] = 1
            for i in range(31):
                matrix[100 + i, 10 + i] = 1
            return matrix
        reduced, ref = cover.quarter_rate(recording), cover.quarter_rate(reference)
        search = dict(cover.SEARCH, chunk_length="3")
        with patch.object(cover, "binary_within", stated):
            found, _ = windows.candidates_of(reduced, windows.stacks(reduced), len(recording), "k", ref, search)
            one, _ = windows.candidates_of(reduced, windows.stacks(reduced), len(recording), "k", ref,
                                           dict(search, alignments_per_chunk=1))
        # Stack row r covers quarter-rate frames r to r + 8: native 4r to 4(r + 9).
        self.assertEqual([a.frames for a in found], [(4 * 5, 4 * (35 + 9)), (4 * 100, 4 * (130 + 9))])
        columns = len(ref) - 9
        self.assertEqual([a.distance for a in found], [float(numpy.float32(math.sqrt(columns) / s))
                                                       for s in (31.0, 31.0)])
        self.assertEqual([a.frames for a in one], [found[0].frames])

    def test_a_second_alignment_that_runs_into_the_first_stops_the_tracing(self) -> None:
        # Sixteen seconds of talk are too few for the score to fall to zero
        # between the copies: the second path traces back into the first.
        p = self.s.recording.window(*self.s.p)
        talk = self.s.recording.window(self.s.p[1], self.s.q[0])
        found = self.candidates(numpy.concatenate([p, *[talk] * 4, p]), dict(cover.SEARCH, chunk_length="3"))
        self.assertEqual(len(found), 1)

    def test_any_number_of_processes_finds_the_same(self) -> None:
        profiles = {k: f.profiles for k, f in self.s.corpus().items()}
        one = windows.candidate_pass(self.s.recording, profiles, jobs=1)
        two = windows.candidate_pass(self.s.recording, profiles, jobs=2)
        self.assertEqual(one, two)

    def test_a_recording_shorter_than_the_stack_finds_nothing(self) -> None:
        short = windows.Recording(self.s.profiles[:43], RATE)  # ten quarter-rate frames
        self.assertEqual(windows.candidate_pass(short, {"x1": self.s.everything["x1"].profiles})[0], {"x1": []})
        found = windows.search_recording(short, self.s.spans, self.s.corpus(), self.s.works_of, _bomb, _bomb)
        self.assertEqual((found["hits"], found["decisions"]), (0, []))
        # Anchor: one quarter-rate frame more aligns somewhere.
        longer = windows.Recording(self.s.profiles[100:148], RATE)
        self.assertTrue(windows.candidate_pass(longer, {"x1": self.s.everything["x1"].profiles})[0]["x1"])


class Regions(unittest.TestCase):
    def test_an_occurrences_overlapping_hits_merge_into_the_closer(self) -> None:
        a = [windows.Alignment("k", (0, 100), 0.15), windows.Alignment("k", (40, 140), 0.10),
             windows.Alignment("k", (300, 400), 0.12), windows.Alignment("k", (500, 600), 0.25)]
        self.assertEqual([h.frames for h in windows.hits_of(a, 0.20)], [(40, 140), (300, 400)])
        # Less than half the shorter is no overlap.
        b = [windows.Alignment("k", (0, 100), 0.15), windows.Alignment("k", (51, 151), 0.10)]
        self.assertEqual(len(windows.hits_of(b, 0.20)), 2)

    def test_regions_gather_mutually_overlapping_hits_and_keep_the_closest_works(self) -> None:
        hits = [windows.Alignment(f"k{i}", (0 + i, 100 + i), 0.05 + i / 100) for i in range(7)]
        hits.append(windows.Alignment("far", (0, 300), 0.01))  # covers them, but not half of itself
        works_of = {f"k{i}": [f"w{6 - i}"] for i in range(7)} | {"far": ["w-far"]}
        regions = windows.regions_of(hits, works_of, keep=5)
        self.assertEqual([r.frames for r in regions], [(0, 300), (0, 100)])
        self.assertEqual(regions[0].works, ["w-far"])
        self.assertEqual(regions[1].works, ["w6", "w5", "w4", "w3", "w2"])
        self.assertEqual(len(regions[1].hits), 7)


class Snap(unittest.TestCase):
    """Snapping on stated spans. At 22,050 Hz a frame is 2,048 samples."""

    def setUp(self) -> None:
        self.recording = windows.Recording(numpy.zeros((2000, 12), numpy.float32), 22050)
        self.spans = [cover.Span(0, 100_000, "music"), cover.Span(100_000, 200_000, "talk"),
                      cover.Span(200_000, 400_000, "music"), cover.Span(400_000, 600_000, "unknown"),
                      cover.Span(600_000, 640_000, "quiet"), cover.Span(640_000, 900_000, "music")]

    def frames(self, start: int, end: int) -> tuple[int, int]:
        return start // 2048, end // 2048 + 1

    def test_outer_and_inner_variants_bridging_talk(self) -> None:
        got = windows.snap(self.frames(60_000, 450_000), self.recording, self.spans, set())
        # Talk between two music spans does not split them.
        self.assertEqual(got["outer"], (0, 600_000))
        # Inner: the first and last spans covered at least half.
        self.assertEqual(got["inner"], (200_000, 400_000))

    def test_a_claimed_span_splits_a_group_and_the_larger_overlap_wins(self) -> None:
        got = windows.snap(self.frames(60_000, 450_000), self.recording, self.spans, {2})
        self.assertEqual(got["outer"], (400_000, 600_000))
        got = windows.snap(self.frames(0, 250_000), self.recording, self.spans, {2})
        self.assertEqual(got["outer"], (0, 100_000))

    def test_a_window_too_short_for_the_quarter_rate_distance_is_dropped(self) -> None:
        # 86,016 samples are 42 hops: 43 frames, 10 quarter-rate frames. One
        # sample more makes 44 frames, 11 quarter-rate frames.
        quarter = cover.QuarterRate({"k": numpy.ones((400, 12), numpy.float32)})
        for end, kept in ((86_016, False), (86_017, True)):
            with self.subTest(end=end):
                spans = [cover.Span(0, end, "music"), cover.Span(end, 300_000, "talk")]
                got = windows.snap(self.frames(0, 80_000), self.recording, spans, set())
                self.assertEqual(got, {"outer": (0, end), "inner": (0, end)} if kept else {})
                self.assertEqual(windows.comparable(self.recording, (0, end), 4), kept)
                # Anchor: the floor is the quarter-rate distance's own.
                query = cover.quarter_rate(self.recording.window(0, end))
                if kept:
                    self.assertIn("k", quarter.distances(query, ["k"]))
                else:
                    with self.assertRaises(cover.TooShort):
                        quarter.distances(query, ["k"])
        # Fewer frames to a group raise the frame count it takes, not the floor.
        self.assertTrue(windows.comparable(self.recording, (0, 86_016), 3))

    def test_only_the_search_classes_are_snapped_to(self) -> None:
        self.assertEqual(windows.snap(self.frames(110_000, 190_000), self.recording, self.spans, set()), {})
        music_only = dict(cover.SEARCH, span_classes=["music"])
        got = windows.snap(self.frames(410_000, 590_000), self.recording, self.spans, set(), music_only)
        self.assertEqual(got, {})

    def test_every_edge_is_a_span_edge(self) -> None:
        rng = random.Random(7)
        for _ in range(300):
            edges = sorted(rng.sample(range(1, 4_000_000), 12))
            edges = [0, *edges, 4_000_000]
            spans = [cover.Span(a, b, rng.choice(["music", "unknown", "talk", "quiet"]))
                     for a, b in zip(edges, edges[1:])]
            start = rng.randrange(0, 1900)
            claimed = set(rng.sample(range(len(spans)), rng.randrange(0, 4)))
            got = windows.snap((start, start + rng.randrange(1, 100)), self.recording, spans, claimed)
            for start_sample, end_sample in got.values():
                self.assertIn(start_sample, edges)
                self.assertIn(end_sample, edges)
                inside = [i for i, s in enumerate(spans) if start_sample <= s.start_sample and s.end_sample <= end_sample]
                self.assertFalse(set(inside) & claimed)


@needs_essentia
@needs_ffmpeg
class Search(unittest.TestCase):
    def setUp(self) -> None:
        self.s = Synthetic.build(self)

    def test_each_song_is_kept_once_in_whole_spans_and_suggested_for_its_work(self) -> None:
        found = self.s.search()
        windows_ = [d.window for d in found["decisions"]]
        self.assertEqual(len(windows_), 2)
        edges = {e for s in self.s.spans for e in (s.start_sample, s.end_sample)}
        for start, end in windows_:
            self.assertIn(start, edges)
            self.assertIn(end, edges)
        (p_window, q_window) = windows_
        self.assertLessEqual(p_window[1], q_window[0])  # kept windows share no span
        self.assertGreater(windows.overlap(p_window, self.s.p) * 2, p_window[1] - p_window[0])
        self.assertGreater(windows.overlap(q_window, self.s.q) * 2, q_window[1] - q_window[0])
        p, q = found["decisions"]
        self.assertEqual((p.path, p.suggested, p.work), ("quarter_rate", True, "work-x"))
        self.assertEqual((q.path, q.suggested, q.work), ("quarter_rate", True, "work-y"))
        self.assertLessEqual(p.distances[0].value, Decimal(cover.SEARCH["suggestion_distance"]))
        self.assertEqual(p.corpus, ["x1", "x2"])
        self.assertEqual(q.corpus, ["y1"])

    def test_the_decision_compares_every_allowed_occurrence(self) -> None:
        corpus = self.s.corpus()
        compared = []
        real = cover.QuarterRate.distances

        def spy(quarter, query, keys):
            compared.append(list(keys))
            return real(quarter, query, keys)
        with patch.object(cover.QuarterRate, "distances", spy):
            found = self.s.search(corpus)
        decisions = compared[-len(found["decisions"]):]
        self.assertEqual(len(decisions), 2)
        for keys in decisions:
            self.assertEqual(set(keys), set(corpus))
        # Anchor: claim rounds compare only the candidate Works' occurrences.
        self.assertTrue(any(set(keys) < set(corpus) for keys in compared[:-2]), compared)

    def test_a_window_is_suggested_at_or_below_the_distance_and_never_above(self) -> None:
        found = self.s.search()
        closest = found["decisions"][0].distances[0].value
        at = self.s.search(search=dict(cover.SEARCH, suggestion_distance=str(closest)))
        below = self.s.search(search=dict(cover.SEARCH, suggestion_distance=str(closest - Decimal("0.0001"))))
        self.assertTrue(at["decisions"][0].suggested)
        self.assertFalse(below["decisions"][0].suggested)
        self.assertEqual(below["decisions"][0].corpus, [])

    def test_one_claim_for_the_closest_work_even_when_another_is_within_the_distance(self) -> None:
        # No near tie by margin, so the quarter-rate distance decides alone.
        corpus = self.s.corpus("x1", "y1", "z1")
        found = self.s.search(corpus, dict(cover.SEARCH, near_tie_margin="0", suggestion_distance="0.5"))
        p = found["decisions"][0]
        within = {w for v, w in windows.per_work(p.distances, self.s.works_of) if v <= 0.5}
        self.assertGreater(len(within), 1, within)  # anchor: several Works within the distance
        self.assertTrue(p.suggested)
        self.assertEqual(p.work, windows.per_work(p.distances, self.s.works_of)[0][1])
        self.assertTrue(all(self.s.works_of[k] == [p.work] for k in p.corpus))

    def test_the_exact_distance_never_runs_when_the_margin_is_clear(self) -> None:
        # Bombs: neither a fresh measurement nor an exact distance may run.
        with patch.object(cover.Analyser, "distance", _bomb), patch.object(cover, "load", _bomb):
            found = self.s.search(window_features=_bomb, exact=_bomb)
        self.assertTrue(all(d.path == "quarter_rate" and d.compared_exactly == [] for d in found["decisions"]))
        self.assertTrue(all(d.work_margin >= float(cover.SEARCH["near_tie_margin"]) for d in found["decisions"]))

    def test_an_unsuggested_window_is_never_compared_exactly(self) -> None:
        corpus = self.s.corpus("x1", "y1", "z1")
        found = self.s.search(corpus, dict(cover.SEARCH, suggestion_distance="0.05"))
        self.assertFalse(any(d.suggested for d in found["decisions"]))
        # Anchor: P's window is a near tie by margin.
        self.assertLess(found["decisions"][0].work_margin, float(cover.SEARCH["near_tie_margin"]))

    def test_a_planted_near_tie_is_decided_exactly_on_fresh_audio(self) -> None:
        corpus = self.s.corpus("x1", "x2", "y1", "z1")
        measured, asked = [], []

        def fresh(start: int, end: int) -> cover.Features:
            measured.append((start, end))
            return self.s.fresh(start, end)

        def exact(features: cover.Features, keys: list[str]) -> dict[str, float]:
            asked.append(keys)
            return self.s.exact(corpus)(features, keys)
        found = self.s.search(corpus, window_features=fresh, exact=exact)
        p, q = found["decisions"]
        # Work Z's occurrence is P again: Works X and Z tie.
        self.assertLess(p.quarter_rate[0].raw, float(cover.SEARCH["suggestion_distance"]))
        self.assertEqual(p.path, "exact")
        self.assertEqual(measured, [p.window])
        self.assertEqual(q.path, "quarter_rate")
        # Candidates (Works X and Z) and the prefilter's half.
        means = {k: f.mean for k, f in corpus.items()}
        prefiltered = set(cover.prefilter(self.s.fresh(*p.window).mean, means, Decimal("0.5")))
        self.assertEqual(asked, [sorted({"x1", "x2", "z1"} | prefiltered)])
        self.assertEqual(p.compared_exactly, asked[0])
        # Suggested on the exact scale's limit, for the exact closest Work.
        self.assertEqual(p.suggested, p.distances[0].value <= Decimal(cover.SEARCH["exact_suggestion_distance"]))
        self.assertTrue(p.suggested, p.distances)
        self.assertEqual(p.work, windows.per_work(p.distances, self.s.works_of)[0][1])
        # Anchor: the exact and quarter-rate scores differ.
        self.assertNotEqual(p.distances[0].raw, p.quarter_rate[0].raw)
        # Without fresh profiles it is unmeasured, never suggested.
        found = self.s.search(corpus, window_features=lambda s, e: None, exact=_bomb)
        self.assertEqual((found["decisions"][0].path, found["decisions"][0].suggested), ("unmeasured", False))

    def test_a_window_too_short_to_compare_is_refused_not_passed_over(self) -> None:
        # Snapping drops such windows, so meeting one later is a defect: the
        # claim rounds and the decision raise rather than skip it.
        quarter = cover.QuarterRate({k: f.profiles for k, f in self.s.corpus().items()})
        means = {k: f.mean for k, f in self.s.corpus().items()}
        short = (self.s.p[0], self.s.p[0] + 2 * RATE)
        self.assertFalse(windows.comparable(self.s.recording, short, 4))
        with self.assertRaises(cover.TooShort):
            windows.decide(short, {"work-x"}, self.s.recording, quarter, means, self.s.works_of, _bomb, _bomb)
        region = windows.Region(frames_of(short), [], ["work-x"])
        with patch.object(windows, "snap", lambda *_: {"outer": short}), self.assertRaises(cover.TooShort):
            windows.claim_rounds([region], self.s.recording, self.s.spans, [], self.s.works_of, quarter)

    def test_profiles_that_are_not_finite_are_refused(self) -> None:
        # Anchor: a sign-bit NaN gets the smallest order key, so a distance
        # that is not a number would count as the closest.
        keys = cover.order_keys(numpy.array([1, -numpy.nan, 0.5], numpy.float32))
        self.assertEqual(int(numpy.argmin(keys)), 1)
        for bad in (numpy.nan, -numpy.nan, numpy.inf, -1.0):
            with self.subTest(bad=bad):
                profiles = self.s.profiles.copy()
                profiles[200:240, 3] = bad
                with self.assertRaisesRegex(cover.CoverError, "finite and non-negative"):
                    windows.Recording(profiles, RATE)
                corpus = dict(self.s.corpus())
                corpus["x1"] = cover.Features(profiles, corpus["x1"].mean)
                with patch.object(windows, "candidates_of", _bomb), \
                        self.assertRaisesRegex(cover.CoverError, "x1 profiles must be finite"):
                    self.s.search(corpus)
                with self.assertRaisesRegex(cover.CoverError, "x1 profiles must be finite"):
                    cover.QuarterRate({"x1": profiles})

    def test_nothing_aligning_is_an_answer_not_a_suggestion(self) -> None:
        found = self.s.search()
        self.assertTrue(found["decisions"])
        recording, quarter = self.s.recording, cover.QuarterRate({k: f.profiles for k, f in self.s.corpus().items()})
        means = {k: f.mean for k, f in self.s.corpus().items()}
        window = found["decisions"][0].window
        with patch.object(cover, "binary_within", lambda a, an, b, bn, shift: numpy.zeros((len(a), len(b)),
                                                                                          numpy.float32)):
            d = windows.decide(window, {"work-x"}, recording, quarter, means, self.s.works_of, _bomb, _bomb)
        self.assertEqual((d.suggested, d.distances, d.unaligned), (False, [], ["x1", "x2", "y1"]))

    def test_an_empty_corpus_finds_nothing(self) -> None:
        found = self.s.search({})
        self.assertEqual((found["hits"], found["regions"], found["decisions"]), (0, [], []))

    def test_no_music_spans_finds_no_window(self) -> None:
        talk = [cover.Span(s.start_sample, s.end_sample, "talk") for s in self.s.spans]
        found = windows.search_recording(self.s.recording, talk, self.s.corpus(), self.s.works_of, _bomb, _bomb)
        self.assertGreater(found["hits"], 0)  # anchor: the songs are still heard
        self.assertEqual((found["windows"], found["decisions"]), ([], []))

    def test_a_window_crosses_a_talk_span_inside_a_song(self) -> None:
        a, b = self.s.p[0] + 10 * RATE, self.s.p[0] + 13 * RATE
        spans = [cover.Span(0, self.s.p[0], "quiet"), cover.Span(self.s.p[0], a, "music"), cover.Span(a, b, "talk"),
                 cover.Span(b, self.s.p[1], "music"), *self.s.spans[3:]]
        found = windows.search_recording(self.s.recording, spans, self.s.corpus(), self.s.works_of, _bomb, _bomb)
        p = found["decisions"][0]
        self.assertEqual(p.window, (self.s.p[0], self.s.p[1]))
        self.assertTrue(p.suggested)

    def test_the_same_inputs_give_the_same_bits_with_any_number_of_processes(self) -> None:
        def plain(found: dict) -> str:
            return json.dumps({**found, "decisions": [asdict(d) for d in found["decisions"]]}, default=str)
        one = plain(self.s.search(jobs=1))
        self.assertEqual(plain(self.s.search(jobs=1)), one)
        self.assertEqual(plain(self.s.search(jobs=2)), one)


@needs_essentia
@needs_ffmpeg
class ParametersAreLive(unittest.TestCase):
    """Every leaf of the search's parameters, altered, changes what the search
    answers. A new leaf fails here until it has a row."""

    ROWS = {
        "span_classes": (["music"], {}),
        "quarter_rate_frames": (3, {}),
        "chunk_length": ("3", {}),
        "chunk_hop": ("0.5", {}),
        "alignments_per_chunk": (1, {}),
        "hit_distance": ("0.05", {}),
        "works_per_region": (1, {}),
        "works_per_window": (0, {"works_per_region": 0}),
        "suggestion_distance": ("0.05", {}),
        "near_tie_margin": ("0", {}),
        "exact_suggestion_distance": ("0.01", {}),
        "prefilter_retention": ("1", {}),
    }

    def test_every_parameter_changes_the_answer(self) -> None:
        s = Synthetic.build(self)
        corpus = s.corpus("x1", "x2", "y1", "z1")
        self.assertEqual(sorted(self.ROWS), sorted(cover.SEARCH))
        self.assertEqual(cover.parameters()["search"], cover.SEARCH)
        # The measured recording three times over, so that chunks overlap and
        # repeat; its spans repeat with it.
        length = s.recording.sample_of(s.recording.frames)
        recording = windows.Recording(numpy.concatenate([s.profiles] * 3), RATE)
        spans = [cover.Span(x.start_sample + i * length, x.end_sample + i * length, x.kind)
                 for i in range(3) for x in s.spans]

        def fresh(start: int, end: int) -> cover.Features:
            profiles = recording.window(start, end)
            return cover.Features(profiles, tuple(float(v) for v in profiles.mean(axis=0)))

        def answer(search: dict) -> str:
            found = windows.search_recording(recording, spans, corpus, s.works_of, fresh, s.exact(corpus), 1, search)
            return json.dumps({**found, "decisions": [asdict(d) for d in found["decisions"]]}, default=str)
        # Anchor quarter_rate_frames: the corpus is reduced by the search's
        # group, as the recording is, not by the default.
        made = []
        real = cover.QuarterRate.__init__

        def spy(quarter, corpus, frames=None):
            made.append(quarter)
            real(quarter, corpus, frames)
        with patch.object(cover.QuarterRate, "__init__", spy):
            answer(dict(cover.SEARCH, quarter_rate_frames=3))
        [quarter] = made
        self.assertNotEqual(cover.SEARCH["quarter_rate_frames"], 3)
        self.assertEqual({k: len(quarter.reference(k)) for k in corpus},
                         {k: len(f.profiles) // 3 for k, f in corpus.items()})
        for leaf, (value, base) in self.ROWS.items():
            with self.subTest(leaf=leaf):
                before = dict(cover.SEARCH, **base)
                self.assertNotEqual(before[leaf], value)
                self.assertNotEqual(answer(dict(before, **{leaf: value})), answer(before), leaf)


if __name__ == "__main__":
    unittest.main()
