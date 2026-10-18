"""Essentia cover similarity and the growing-window search (ADRs 0071, 0072).

The search is tested with the measure replaced by tables, so every decision
(growth, stall, degrade, rollback, short windows, the prefilter) is checked
exactly. The measure itself runs the pinned Essentia build on synthetic tunes.
"""

from __future__ import annotations

import importlib.metadata
import math
import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import numpy

import cover
import support
from support import needs_ffmpeg

try:
    cover.essentia_module()
    ESSENTIA = True
except cover.CoverError:
    ESSENTIA = False
if os.environ.get("ARCHIVE_REQUIRE_ESSENTIA") and not ESSENTIA:
    raise RuntimeError("ARCHIVE_REQUIRE_ESSENTIA is set but the pinned Essentia build is not importable")
needs_essentia = unittest.skipUnless(ESSENTIA, "the pinned Essentia build is needed (enrich/requirements-match.txt)")

def essentia_cross(a: numpy.ndarray, b: numpy.ndarray) -> numpy.ndarray:
    """Essentia's own ChromaCrossSimilarity, as the method configures it."""
    options = cover._options(cover.ANALYSIS["chroma_cross_similarity"])
    return cover.essentia_module().ChromaCrossSimilarity(**options)(a, b)


RATE = 1000  # samples per second in the search tests: two seconds is 2,000 samples


def spans(*kinds: str, length: int = 3000) -> list[cover.Span]:
    """Consecutive spans of `length` samples with these classes."""
    return [cover.Span(index * length, (index + 1) * length, kind) for index, kind in enumerate(kinds)]


class Table:
    """A stand-in for the measure: each window, by its end sample, has a
    distance to each corpus entry. Records every window measured and every
    comparison made."""

    def __init__(self, distances: dict[int, dict[str, float]], means: dict[str, tuple[float, ...]] | None = None):
        self.distances = distances
        self.windows: list[tuple[int, int]] = []
        self.compared: list[tuple[int, list[str]]] = []
        keys = sorted({key for row in distances.values() for key in row})
        self.corpus = {key: cover.Features(numpy.zeros((0, 12)), (means or {}).get(key, (1.0,) + (0.0,) * 11))
                       for key in keys}

    def window_features(self, start: int, end: int) -> cover.Features:
        self.windows.append((start, end))
        return cover.Features(numpy.array([end]), (1.0,) + (0.0,) * 11)

    def measure(self, features: cover.Features, keys: list[str]) -> dict[str, float]:
        end = int(features.profiles[0])
        self.compared.append((end, list(keys)))
        return {key: self.distances[end][key] for key in keys}

    def search(self, run: list[int], suggested: list[cover.Span]) -> dict:
        return cover.search_run(run, suggested, RATE, self.window_features, self.corpus, self.measure)


class Search(unittest.TestCase):
    def setUp(self) -> None:
        # Compare the whole corpus: the prefilter has its own tests.
        patcher = patch.dict(cover.SEARCH, {"prefilter_retention": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_growth_stops_after_two_steps_without_improvement_and_keeps_the_best(self) -> None:
        suggested = spans(*["music"] * 6)
        table = Table({3000: {"a": 0.30}, 6000: {"a": 0.10}, 9000: {"a": 0.12}, 12000: {"a": 0.11},
                       15000: {"a": 0.01}, 18000: {"a": 0.01}})
        result = table.search(list(range(6)), suggested)
        # Anchor: a later step would have been better; the stall stopped first.
        self.assertLess(table.distances[15000]["a"], table.distances[6000]["a"])
        self.assertEqual([end for _, end in table.windows], [3000, 6000, 9000, 12000])
        self.assertEqual(result["best"]["step"], 1)
        self.assertEqual(result["best"]["window"], {"start_sample": 0, "end_sample": 6000})
        self.assertEqual(result["best"]["distances"][0].value, Decimal("0.1000"))

    def test_one_step_worse_than_the_best_by_more_than_the_margin_stops_at_once(self) -> None:
        suggested = spans(*["music"] * 4)
        table = Table({3000: {"a": 0.05}, 6000: {"a": 0.2001}, 9000: {"a": 0.01}, 12000: {"a": 0.01}})
        result = table.search(list(range(4)), suggested)
        self.assertEqual([end for _, end in table.windows], [3000, 6000])
        self.assertEqual(result["best"]["step"], 0)
        # Exactly the margin is not more than it: growth continues.
        table = Table({3000: {"a": 0.05}, 6000: {"a": 0.20}, 9000: {"a": 0.21}, 12000: {"a": 0.01}})
        table.search(list(range(4)), suggested)
        self.assertEqual([end for _, end in table.windows], [3000, 6000, 9000])

    def test_distances_are_compared_rounded_to_four_places(self) -> None:
        # 0.10004 rounds to 0.1000, which is no improvement on 0.1000.
        suggested = spans(*["music"] * 4)
        table = Table({3000: {"a": 0.1}, 6000: {"a": 0.10004}, 9000: {"a": 0.09996}, 12000: {"a": 0.05}})
        result = table.search(list(range(4)), suggested)
        self.assertEqual(result["best"]["step"], 0)
        self.assertEqual([end for _, end in table.windows], [3000, 6000, 9000])

    def test_a_window_too_short_to_measure_is_skipped_and_is_not_a_step(self) -> None:
        suggested = [cover.Span(0, 500, "music"), cover.Span(500, 1500, "music"), cover.Span(1500, 4000, "music"),
                     cover.Span(4000, 7000, "music")]
        table = Table({4000: {"a": 0.2}, 7000: {"a": 0.1}})
        result = table.search(list(range(4)), suggested)
        self.assertEqual(table.windows, [(0, 4000), (0, 7000)])
        self.assertEqual([step["spans"] for step in result["steps"]], [[0, 2], [0, 3]])
        self.assertEqual(result["best"]["spans"], [0, 3])

    def test_a_window_grows_to_at_most_eight_spans(self) -> None:
        suggested = spans(*["music"] * 10)
        table = Table({end: {"a": 1.0 - end / 100000} for end in range(3000, 33000, 3000)})
        result = table.search(list(range(10)), suggested)
        self.assertEqual(len(table.windows), 8)
        self.assertEqual(result["best"]["window"], {"start_sample": 0, "end_sample": 24000})

    def test_an_empty_corpus_or_no_long_window_has_no_best(self) -> None:
        table = Table({})
        result = table.search([0, 1], spans("music", "music"))
        self.assertIsNone(result["best"])
        self.assertEqual([step["compared"] for step in result["steps"]], [0, 0])
        short = Table({100: {"a": 0.0}})
        self.assertIsNone(short.search([0], [cover.Span(0, 100, "music")])["best"])
        self.assertEqual(short.windows, [])

    def test_the_best_step_lists_every_distance_closest_first_with_its_margin(self) -> None:
        table = Table({3000: {"b": 0.07, "a": 0.07, "c": 0.12}})
        best = table.search([0], spans("music"))["best"]
        self.assertEqual([(d.corpus_id, str(d.value)) for d in best["distances"]],
                         [("a", "0.0700"), ("b", "0.0700"), ("c", "0.1200")])
        self.assertEqual(best["margin"], "0.0000")
        self.assertIsNone(Table({3000: {"a": 0.1}}).search([0], spans("music"))["best"]["margin"])

    def test_an_entry_that_does_not_align_is_counted_but_never_ranked(self) -> None:
        table = Table({3000: {"a": 0.07, "b": math.inf, "c": 0.12}})
        result = table.search([0], spans("music"))
        best = result["best"]
        self.assertEqual([d.corpus_id for d in best["distances"]], ["a", "c"])
        self.assertEqual(best["unaligned"], ["b"])
        self.assertEqual(best["margin"], "0.0500")
        self.assertEqual((result["steps"][0]["compared"], result["steps"][0]["unaligned"]), (3, 1))

    def test_a_step_where_nothing_aligns_before_any_best_keeps_growing(self) -> None:
        table = Table({3000: {"a": math.inf}, 6000: {"a": 0.05}})
        result = table.search([0, 1], spans("music", "music"))
        self.assertEqual([end for _, end in table.windows], [3000, 6000])
        self.assertEqual(result["best"]["step"], 1)
        # Nothing aligning anywhere is no best at all, not a failure.
        self.assertIsNone(Table({3000: {"a": math.inf}}).search([0], spans("music"))["best"])

    def test_a_step_where_nothing_aligns_after_a_best_stops_at_once(self) -> None:
        table = Table({3000: {"a": 0.05}, 6000: {"a": math.inf}, 9000: {"a": 0.01}})
        result = table.search([0, 1, 2], spans("music", "music", "music"))
        # Anchor: the step after would have been better.
        self.assertLess(table.distances[9000]["a"], table.distances[3000]["a"])
        self.assertEqual([end for _, end in table.windows], [3000, 6000])
        self.assertEqual(result["best"]["step"], 0)

    def test_any_other_answer_that_is_not_a_distance_still_fails(self) -> None:
        for bad in (float("nan"), -math.inf, -0.1):
            with self.assertRaises(cover.CoverError):
                Table({3000: {"a": bad}}).search([0], spans("music"))


class Runs(unittest.TestCase):
    def test_talk_and_quiet_end_a_run_and_unknown_joins_one(self) -> None:
        suggested = spans("talk", "music", "unknown", "music", "quiet", "music", "talk", "talk", "unknown")
        self.assertEqual(cover.runs_of(suggested, cover.SEARCH["run_classes"]), [[1, 2, 3], [5], [8]])
        self.assertEqual(cover.runs_of(spans("talk", "quiet"), cover.SEARCH["run_classes"]), [])


class Prefilter(unittest.TestCase):
    def test_half_the_corpus_is_kept_by_transposition_invariant_mean_chroma(self) -> None:
        query = (1.0, 0.5) + (0.0,) * 10
        rotated = tuple(numpy.roll(query, 5))
        corpus = {"same-key": query, "other-key": rotated, "far": (0.0,) * 6 + (1.0,) * 6,
                  "farther": (0.0,) * 11 + (1.0,)}
        self.assertAlmostEqual(cover.transposition_invariant_similarity(query, rotated), 1.0, places=6)
        self.assertEqual(cover.prefilter(query, corpus, Decimal("0.5")), ["other-key", "same-key"])
        # Rounded up, never empty, and everything at a retention of one.
        self.assertEqual(len(cover.prefilter(query, dict(list(corpus.items())[:3]), Decimal("0.5"))), 2)
        self.assertEqual(cover.prefilter(query, {"far": corpus["far"]}, Decimal("0.01")), ["far"])
        self.assertEqual(cover.prefilter(query, corpus, Decimal("1")), sorted(corpus))
        self.assertEqual(cover.prefilter(query, {}, Decimal("0.5")), [])

    def test_the_search_compares_only_what_the_prefilter_keeps(self) -> None:
        near = (1.0,) + (0.0,) * 11
        far = (0.0,) * 6 + (1.0,) * 6
        table = Table({3000: {"near": 0.2, "far": 0.01}}, means={"near": near, "far": far})
        best = table.search([0], spans("music"))["best"]
        # Anchor: the excluded entry would have been the closest.
        self.assertLess(table.distances[3000]["far"], table.distances[3000]["near"])
        self.assertEqual(table.compared, [(3000, ["near"])])
        self.assertEqual([d.corpus_id for d in best["distances"]], ["near"])


class Rounding(unittest.TestCase):
    def test_a_distance_is_rounded_half_to_even_from_its_exact_value(self) -> None:
        self.assertEqual(cover.rounded(0.08), Decimal("0.0800"))
        # Neither float is exactly half a unit: each rounds by its exact value.
        self.assertEqual(cover.rounded(0.00015), Decimal("0.0001"))  # 0.000149999...
        self.assertEqual(cover.rounded(0.00025), Decimal("0.0003"))  # 0.000250000...05
        for bad in (float("nan"), float("inf"), -0.1):
            with self.assertRaises(cover.CoverError):
                cover.rounded(bad)


class EssentiaBuild(unittest.TestCase):
    def test_another_essentia_build_is_refused(self) -> None:
        with patch.object(importlib.metadata, "version", return_value="2.1b6.dev1389"):
            with self.assertRaisesRegex(cover.CoverError, "2.1b6.dev1389 is installed, but the method is "
                                                          "calibrated with 2.1b6.dev1438"):
                cover.essentia_module()

    @needs_essentia
    def test_importing_essentia_prints_nothing(self) -> None:
        import subprocess
        import sys
        completed = subprocess.run([sys.executable, "-c", "import cover; cover.essentia_module()"],
                                   cwd=Path(cover.__file__).parent, capture_output=True, text=True)
        self.assertEqual((completed.returncode, completed.stdout, completed.stderr), (0, "", ""))

    def test_a_missing_essentia_is_refused(self) -> None:
        with patch.dict("sys.modules", {"essentia": None, "essentia.standard": None}):
            with self.assertRaisesRegex(cover.CoverError, "Essentia is not importable here"):
                cover.essentia_module()


@needs_essentia
@needs_ffmpeg
class Measure(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = support.scratch(self, "cover-")
        self.query = self.wav("query", support.TUNE, transpose=3, seed=1)
        self.same = self.wav("same", support.TUNE, seed=2)
        self.other = self.wav("other", support.OTHER_TUNE, seed=3)

    def wav(self, name: str, notes, rate: int = 48000, **options) -> cover.Piece:
        path = support.write_wav(self.tmp / f"{name}.wav", support.tune_samples(notes, rate, **options), rate)
        frames = len(support.tune_samples(notes, rate, **options))
        return cover.Piece(path, 0, frames, rate)

    def test_the_same_tune_in_another_key_is_closer_than_another_tune(self) -> None:
        analyser = cover.Analyser()
        query, same, other = (analyser.features(cover.load([p])) for p in (self.query, self.same, self.other))
        near = cover.rounded(analyser.distance(query.profiles, same.profiles))
        far = cover.rounded(analyser.distance(query.profiles, other.profiles))
        # Anchor: two different, finite scores. Synthetic tunes are not what
        # the thresholds were measured on, so no threshold is asserted.
        self.assertNotEqual(near, far)
        self.assertLess(near, far)

    def test_essentia_answers_infinity_when_nothing_aligns(self) -> None:
        # The search's unaligned test must match what Essentia actually
        # answers: one second of music against silence aligns nowhere.
        analyser = cover.Analyser()
        music = support.progression_samples(support.PROGRESSION, 22050).astype(numpy.float32)
        short = analyser.features(music[:22050]).profiles
        silence = analyser.features(numpy.zeros(4 * 22050, numpy.float32)).profiles
        distance = analyser.distance(short, silence)
        self.assertTrue(cover.unaligned(distance), distance)
        # Anchor: longer music does align with the same silence.
        self.assertFalse(cover.unaligned(analyser.distance(analyser.features(music).profiles, silence)))

    def test_the_distance_is_the_smaller_direction_and_deterministic(self) -> None:
        analyser = cover.Analyser()
        query, same = (analyser.features(cover.load([p])) for p in (self.query, self.same))
        self.assertEqual(analyser.distance(query.profiles, same.profiles),
                         analyser.distance(same.profiles, query.profiles))
        again = cover.Analyser().features(cover.load([self.query]))
        self.assertTrue(numpy.array_equal(query.profiles, again.profiles))
        # Both directions are aligned, and the smaller distance kept,
        # whichever direction gives it.
        aligned = []
        for answers, expected in (((0.3, 0.2), 0.2), ((0.1, 0.4), 0.1)):
            replies = iter(answers)

            def cover_(matrix):
                aligned.append(matrix)
                return matrix, next(replies)
            with patch.object(analyser, "cover", cover_):
                self.assertEqual(analyser.distance(query.profiles, same.profiles), expected)
        forward = essentia_cross(query.profiles, same.profiles)
        backward = essentia_cross(same.profiles, query.profiles)
        # Anchor: the two directions are different matrices.
        self.assertFalse(numpy.array_equal(forward, backward))
        for got, wanted in zip(aligned, [forward, backward] * 2, strict=True):
            self.assertTrue(numpy.array_equal(got, wanted))

    def test_parallel_comparison_answers_exactly_what_one_process_does(self) -> None:
        corpus = {"same": (self.same,), "other": (self.other,)}
        answers = []
        for jobs in (1, 2):
            with cover.Comparer(jobs) as comparer:
                measured = comparer.measure_corpus(corpus)
                self.assertEqual(sorted(measured), ["other", "same"])
                query = comparer.features([self.query])
                answers.append(comparer.distances(query, ["other", "same"]))
        self.assertEqual(answers[0], answers[1])
        self.assertEqual(len(answers[0]), 2)

    def test_decoding_reads_exactly_the_span_at_the_analysis_rate(self) -> None:
        samples = cover.decode(cover.Piece(self.same.path, 48000, 96000, 48000))
        self.assertEqual(len(samples), 22050)
        self.assertEqual(samples.dtype, numpy.float32)
        # 16-bit samples scaled by 32,768, as the V2 system's loader read them.
        self.assertTrue(numpy.array_equal(samples * 32768, numpy.round(samples * 32768)))

    def test_a_span_past_the_end_or_at_the_wrong_rate_or_empty_is_refused(self) -> None:
        end = self.same.end_sample
        with self.assertRaisesRegex(cover.CoverError, "runs past its audio"):
            cover.decode(cover.Piece(self.same.path, 0, end + 48000, 48000))
        with self.assertRaisesRegex(cover.CoverError, "is sampled at 48000 Hz, but its span is in samples at 44100"):
            cover.decode(cover.Piece(self.same.path, 0, 1000, 44100))
        with self.assertRaisesRegex(cover.CoverError, "is empty"):
            cover.decode(cover.Piece(self.same.path, 10, 10, 48000))
        with self.assertRaisesRegex(cover.CoverError, "ffmpeg could not run"):
            cover.decode(self.same, ffmpeg=str(self.tmp / "absent-ffmpeg"))
        with self.assertRaisesRegex(cover.CoverError, "has no audio stream|could not read"):
            cover.decode(cover.Piece(Path(__file__), 0, 10, 48000))

    def test_audio_too_short_to_align_is_its_own_refusal(self) -> None:
        with self.assertRaises(cover.TooShort):
            cover.Analyser().features(cover.decode(cover.Piece(self.same.path, 0, 4800, 48000)))
        with cover.Comparer(1) as comparer:
            measured = comparer.measure_corpus({"short": (cover.Piece(self.same.path, 0, 4800, 48000),),
                                                "same": (self.same,)})
        self.assertIsInstance(measured["short"], cover.TooShort)
        self.assertEqual(sorted(comparer.corpus), ["same"])


def _bomb(*_, **__):
    raise AssertionError("must not be called")


@needs_essentia
@needs_ffmpeg
class CrossSimilarity(unittest.TestCase):
    """The NumPy cross-similarity against Essentia's, on profiles the real
    Analyser measures from audio Python's own writer wrote."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.analyser = cover.Analyser()
        cls.profiles = {}
        with tempfile.TemporaryDirectory(prefix="cover-cross-") as directory:
            for name, notes, options in (("query", support.TUNE, {"transpose": 3, "seed": 1}),
                                         ("same", support.TUNE, {"seed": 2, "repeats": 4}),
                                         ("other", support.OTHER_TUNE, {"seed": 3, "repeats": 2})):
                samples = support.tune_samples(notes, 48000, **options)
                path = support.write_wav(Path(directory) / f"{name}.wav", samples, 48000)
                piece = cover.Piece(path, 0, len(samples), 48000)
                cls.profiles[name] = cls.analyser.features(cover.load([piece])).profiles

    def assert_essentias(self, a: numpy.ndarray, b: numpy.ndarray) -> None:
        wanted = essentia_cross(a, b), essentia_cross(b, a)
        got = cover.cross_similarity(a, b), *cover.cross_similarities(a, b)
        for matrix, expected in zip(got, (wanted[0], *wanted), strict=True):
            self.assertEqual((matrix.dtype, matrix.shape), (expected.dtype, expected.shape))
            self.assertTrue(numpy.array_equal(matrix, expected))

    def test_the_matrices_are_essentias_bit_for_bit(self) -> None:
        query, same, other = (self.profiles[name] for name in ("query", "same", "other"))
        # Anchor: three lengths, a transposed key, and matrices with both
        # values in them.
        self.assertEqual(len({len(query), len(same), len(other)}), 3)
        self.assertNotEqual(cover.transposition_index(query, same), 0)
        self.assertNotEqual(cover.transposition_index(query, other), cover.transposition_index(same, other))
        matrix = essentia_cross(query, same)
        self.assertTrue(0 < matrix.sum() < matrix.size)
        for a, b in ((query, same), (query, other), (same, other), (same, same)):
            with self.subTest(frames=(len(a), len(b))):
                self.assert_essentias(a, b)

    def test_the_reverse_direction_reuses_the_dot_products_and_essentia_is_never_called(self) -> None:
        query, same = self.profiles["query"], self.profiles["same"]
        forward, backward = essentia_cross(query, same), essentia_cross(same, query)
        expected = self.analyser.distance(query, same)
        measured = []
        real = cover._forward

        def forward_(a, b):
            measured.append((a.shape, b.shape))
            return real(a, b)
        with patch.object(cover.essentia_module(), "ChromaCrossSimilarity", _bomb), \
                patch.object(cover, "cross_similarity", _bomb), patch.object(cover, "_forward", forward_):
            ab, ba = cover.cross_similarities(query, same)
            self.assertEqual(self.analyser.distance(query, same), expected)
        self.assertEqual(measured, [(query.shape, same.shape)] * 2)
        self.assertTrue(numpy.array_equal(ab, forward))
        self.assertTrue(numpy.array_equal(ba, backward))
        self.assertTrue(ba.flags["C_CONTIGUOUS"])

    def test_nine_frames_are_refused_as_essentia_refuses_them_and_ten_are_left_to_it(self) -> None:
        same, other = self.profiles["same"], self.profiles["other"]
        with self.assertRaises(RuntimeError):
            essentia_cross(same[:9], other)
        for a, b in ((same[:9], other), (other, same[:9]), (same[:0], other)):
            with self.assertRaises(cover.TooShort):
                cover.cross_similarity(a, b)
            with self.assertRaises(cover.TooShort):
                cover.cross_similarities(a, b)
        # Ten frames are one stack, and a row or column of one distance,
        # whose percentile Essentia reads from past the end of the row.
        ten = self.analyser.features(numpy.full(18432, 0.1, numpy.float32)).profiles
        self.assertEqual(len(ten), 10)
        for a, b in ((same[:10], other), (other, same[:10]), (ten, same)):
            calls = []
            real = cover._essentia_cross

            def essentia(x, y):
                calls.append((len(x), len(y)))
                return real(x, y)
            with patch.object(cover, "_essentia_cross", essentia):
                self.assert_essentias(a, b)
            self.assertEqual(sorted(calls), sorted([(len(a), len(b)), (len(a), len(b)), (len(b), len(a))]))

    def test_a_distance_that_is_not_a_number_is_left_to_essentia(self) -> None:
        # A nearly identical stack can make x.x - 2 x.y + y.y negative.
        same = self.profiles["same"]
        nudged = same.copy()
        nudged[::3, 4] = numpy.nextafter(nudged[::3, 4], numpy.float32(2))
        a, b = cover._profiles(same), cover._profiles(nudged)
        stacks_a, stacks_b = cover._stacks(a), cover._stacks(numpy.roll(b, cover.transposition_index(a, b), axis=1))
        distances = cover._matrix(cover._ordered_dot(stacks_a, stacks_a),
                                  cover._ordered_dot(stacks_a[:, None], stacks_b[None, :]),
                                  cover._ordered_dot(stacks_b, stacks_b))
        self.assertTrue(numpy.isnan(distances).any())
        with patch.object(cover, "_binarise", _bomb):
            self.assert_essentias(same, nudged)

    def test_a_percentile_at_a_whole_index_is_zero_as_in_essentia(self) -> None:
        # 201 distances put the 9.5th percentile at index 19 exactly, where
        # Essentia's interpolation answers 0: no cell but an exact match.
        long = numpy.concatenate([self.profiles["same"]] * 2)[:210]
        matrix = cover.cross_similarity(long, self.profiles["other"])
        self.assertEqual(matrix.shape[0], 201)
        self.assertEqual(matrix.sum(), 0)
        # Anchor: one frame fewer and the column percentiles are not zero.
        self.assertGreater(cover.cross_similarity(long[:209], self.profiles["other"]).sum(), 0)
        self.assert_essentias(long, self.profiles["other"])
        self.assert_essentias(long[:209], self.profiles["other"])

    def test_a_dot_product_near_a_rounding_boundary_is_summed_in_essentias_order(self) -> None:
        # One stack whose squares are 1, then 2^-24 less a little, then 36
        # of 0.77 of a unit in the last place of 1: added in Essentia's
        # order, each of those rounds up and the float32 sum is 1 + 2^-23;
        # summed exactly it is just under the midpoint and rounds to 1. The
        # stack is compared with itself, at a percentile index that is whole,
        # so only a distance of exactly 0 is a 1.
        profiles = numpy.zeros((210, 12), numpy.float32)
        at = 100
        profiles[at, :2] = 1, 2.0 ** -13 * (2 - 2.0 ** -23)
        small = 1.75 * 2.0 ** -27
        profiles[at, 2:] = profiles[at + 1] = profiles[at + 2] = small
        profiles[at + 3, :2] = small
        stack = cover._stacks(profiles)[at]
        dots, unsure = cover._cross_dots(profiles, profiles)
        # Anchor: the per-frame sum and Essentia's order disagree here.
        self.assertTrue(unsure[at, at])
        self.assertNotEqual(numpy.float32(dots[at, at]), cover._ordered_dot(stack, stack))
        matrix = cover.cross_similarity(profiles, profiles)
        self.assertEqual(matrix[at, at], 1)
        self.assert_essentias(profiles, profiles)

    def test_profiles_essentia_would_not_measure_from_audio_are_refused(self) -> None:
        same = self.profiles["same"]
        for bad in (-same, numpy.where(same > 0.5, numpy.nan, same), same[:, :11]):
            with self.assertRaises(cover.CoverError):
                cover.cross_similarity(bad, same)


class ReverseDirection(unittest.TestCase):
    """The reverse direction is measured on its own wherever its dot
    products' bits could differ from the forward one's. Each such case is
    built so a sum in double rounds differently in the other direction's
    order: 1, four of 2^-54, and 2^-24 sum to 1 + 2^-24, which rounds to 1
    in float32, when 1 comes first, and to 1 + 2^-23 when it comes last."""

    @staticmethod
    def profiles(frame: list[float], at: int, frames: int = 30) -> numpy.ndarray:
        array = numpy.zeros((frames, 12), numpy.float32)
        array[at] = frame
        return array

    def assert_measured_on_its_own(self, a: numpy.ndarray, b: numpy.ndarray) -> None:
        calls = []
        real = cover.cross_similarity

        def cross(x, y):
            calls.append((x is b, y is a))
            return real(x, y)
        with patch.object(cover, "cross_similarity", cross):
            _, backward = cover.cross_similarities(a, b)
        self.assertEqual(calls, [(True, True)])
        if ESSENTIA:
            self.assertTrue(numpy.array_equal(cover.cross_similarity(a, b), essentia_cross(a, b)))
            self.assertTrue(numpy.array_equal(backward, essentia_cross(b, a)))

    def test_when_the_transposition_indices_do_not_undo_each_other(self) -> None:
        # a's mean chroma has bins 0 and 3, b's bin 1: a ties at shifts 2
        # and 11 and takes 2, b ties at 1 and 10 and takes 1, not 10.
        rng = numpy.random.default_rng(5)
        a = numpy.zeros((30, 12), numpy.float32)
        a[:, 0] = a[:, 3] = rng.integers(1, 8, 30) / 8
        b = numpy.zeros((30, 12), numpy.float32)
        b[:, 1] = rng.integers(1, 8, 30) / 8
        self.assertEqual((cover.transposition_index(a, b), cover.transposition_index(b, a)), (2, 1))
        self.assert_measured_on_its_own(a, b)

    def test_when_rotating_a_stack_changes_its_norm(self) -> None:
        small, tiny = 2.0 ** -12, 2.0 ** -27
        a = self.profiles([1.0] + [0.0] * 11, 12)
        b = self.profiles([tiny] * 4 + [0.0] * 6 + [small, 1.0], 15)
        self.assertEqual((cover.transposition_index(a, b), cover.transposition_index(b, a)), (1, 11))
        stack = cover._stacks(b)[10]
        turned = cover._stacks(numpy.roll(b, 1, axis=1))[10]
        self.assertNotEqual(cover._ordered_dot(stack, stack), cover._ordered_dot(turned, turned))
        self.assert_measured_on_its_own(a, b)

    def test_when_a_cross_dot_product_rounds_otherwise_in_the_other_order(self) -> None:
        a = self.profiles([1.0] * 6 + [0.0] * 6, 12)
        b = self.profiles([2.0 ** -54] * 4 + [2.0 ** -24] + [0.0] * 6 + [1.0], 15)
        self.assertEqual((cover.transposition_index(a, b), cover.transposition_index(b, a)), (1, 11))
        forward = cover._forward(cover._profiles(a), cover._profiles(b))
        assert forward is not None
        rows, columns = forward.unsure
        # Anchor: the norms are the same bits both ways; only the cross
        # dot products differ.
        stacks_b, turned_a = cover._stacks(b), cover._stacks(numpy.roll(a, 11, axis=1))
        self.assertTrue(numpy.array_equal(cover._ordered_dot(stacks_b, stacks_b), forward.reference_norms))
        self.assertTrue(numpy.array_equal(cover._ordered_dot(turned_a, turned_a), forward.query_norms))
        self.assertFalse(numpy.array_equal(cover._ordered_dot(stacks_b[columns], turned_a[rows]),
                                           forward.cross[rows, columns]))
        self.assert_measured_on_its_own(a, b)

    def test_the_reverse_distances_are_added_in_their_own_order_not_transposed(self) -> None:
        # b's first stack lies between a's two, a hair nearer one, so the
        # row of a's two distances to it is decided in the last bit; the
        # norms are added in the other order the other way, and that bit
        # moves.
        rng = numpy.random.default_rng(2)
        a = ((0.5 + rng.integers(0, 2 ** 22, size=(11, 12)) / 2 ** 23) * (1 + 3 * rng.random((11, 1))))
        a = a.astype(numpy.float32)
        b = numpy.concatenate([((a[:-1].astype(numpy.float64) + a[1:]) / 2).astype(numpy.float32), a[-1:]])
        forward = cover._forward(a, b)
        assert forward is not None
        distances = cover._matrix(forward.query_norms, forward.cross, forward.reference_norms)
        reverse = cover._matrix(forward.reference_norms, forward.cross.T, forward.query_norms)
        # Anchor: the reverse distances differ from the transpose, and enough
        # to make the transposed matrix wrong.
        self.assertFalse(numpy.array_equal(reverse, distances.T))
        self.assertFalse(numpy.array_equal(cover._binarise(reverse), forward.matrix.T))
        with patch.object(cover, "_essentia_cross", _bomb), patch.object(cover, "cross_similarity", _bomb):
            ab, ba = cover.cross_similarities(a, b)
        self.assertTrue(numpy.array_equal(ab, forward.matrix))
        self.assertTrue(numpy.array_equal(ba, cover._binarise(reverse)))
        if ESSENTIA:
            self.assertTrue(numpy.array_equal(ab, essentia_cross(a, b)))
            self.assertTrue(numpy.array_equal(ba, essentia_cross(b, a)))


if __name__ == "__main__":
    unittest.main()
