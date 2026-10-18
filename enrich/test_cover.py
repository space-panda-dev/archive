"""The measure: Essentia's profiles, the bit-exact port of its distance, and
the quarter-rate distance (ADR 0071, amendment of 2026-10-18). The measure runs
the pinned Essentia build on synthetic tunes; the search has its own tests
(test_windows.py)."""

from __future__ import annotations

import importlib.metadata
import math
import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import cover  # first: it pins BLAS to one thread before numpy loads it
import numpy
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


ANALYSIS_COVER = {"distanceType": "asymmetric", "alignmentType": "serra09"}
class Ranked(unittest.TestCase):
    def test_every_distance_closest_first_ties_by_raw_then_identifier(self) -> None:
        compared, apart = cover.ranked({"b": 0.07, "a": 0.07, "c": 0.12, "d": 0.07004})
        self.assertEqual([(d.corpus_id, str(d.value)) for d in compared],
                         [("a", "0.0700"), ("b", "0.0700"), ("d", "0.0700"), ("c", "0.1200")])
        self.assertEqual(apart, [])

    def test_an_entry_that_does_not_align_is_listed_but_never_ranked(self) -> None:
        compared, apart = cover.ranked({"a": 0.07, "b": math.inf, "c": 0.12})
        self.assertEqual([d.corpus_id for d in compared], ["a", "c"])
        self.assertEqual(apart, ["b"])

    def test_any_other_answer_that_is_not_a_distance_still_fails(self) -> None:
        for bad in (float("nan"), -math.inf, -0.1):
            with self.assertRaises(cover.CoverError):
                cover.ranked({"a": bad})


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
        # Which nudge does it depends on the profiles' low bits, which differ
        # between platforms, so the first of several that does is used.
        same = self.profiles["same"]

        def has_nan(nudged: numpy.ndarray) -> bool:
            a, b = cover._profiles(same), cover._profiles(nudged)
            stacks_a = cover._stacks(a)
            stacks_b = cover._stacks(numpy.roll(b, cover.transposition_index(a, b), axis=1))
            distances = cover._matrix(cover._ordered_dot(stacks_a, stacks_a),
                                      cover._ordered_dot(stacks_a[:, None], stacks_b[None, :]),
                                      cover._ordered_dot(stacks_b, stacks_b))
            return bool(numpy.isnan(distances).any())
        candidates = []
        for step in (3, 2, 1):
            for bin_ in (4, *range(12)):
                nudged = same.copy()
                nudged[::step, bin_] = numpy.nextafter(nudged[::step, bin_], numpy.float32(2))
                candidates.append(nudged)
        nudged = next((n for n in candidates if has_nan(n)), None)
        self.assertIsNotNone(nudged, "no nudge made a distance that is not a number")
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


@needs_essentia
@needs_ffmpeg
class Alignment(unittest.TestCase):
    """The NumPy serra09 against Essentia's own CoverSongSimilarity, on
    matrices the real Analyser makes from written audio."""

    def setUp(self) -> None:
        tmp = support.scratch(self, "align-")
        rate = 48000
        # A short query, and a longer recording holding the same tune in
        # another key between two other tunes: unequal lengths both ways.
        tunes = {"query": support.tune_samples(support.TUNE, rate, transpose=3, repeats=2, seed=1),
                 "window": numpy.concatenate([support.tune_samples(support.OTHER_TUNE, rate, repeats=2, seed=4),
                                              support.tune_samples(support.TUNE, rate, repeats=2, seed=5),
                                              support.tune_samples(support.OTHER_TUNE, rate, repeats=2, seed=6)]),
                 "other": support.tune_samples(support.OTHER_TUNE, rate, seed=3)}
        self.analyser = cover.Analyser()
        self.profiles = {name: self.analyser.features(cover.load([cover.Piece(
            support.write_wav(tmp / f"{name}.wav", samples, rate), 0, len(samples), rate)])).profiles
            for name, samples in tunes.items()}
        self.essentia = self.analyser.es.CoverSongSimilarity(**cover._options(ANALYSIS_COVER))

    def matrices(self) -> list[numpy.ndarray]:
        return [self.analyser.cross(self.profiles[a], self.profiles[b])
                for a, b in (("query", "window"), ("window", "query"), ("query", "other"), ("other", "window"))]

    def assertEssentias(self, matrix: numpy.ndarray) -> float:
        expected_scores, expected = self.essentia(matrix)
        scores, distance = cover.serra09(matrix)
        self.assertEqual(scores.dtype, numpy.float32)
        self.assertTrue(numpy.array_equal(scores, expected_scores), matrix.shape)
        # The same float32, to the bit, or the same infinity.
        self.assertEqual(numpy.float32(distance).tobytes(), numpy.float32(expected).tobytes(), matrix.shape)
        return distance

    def test_the_method_keeps_the_settings_the_port_implements(self) -> None:
        self.assertEqual(cover.ANALYSIS["cover_song_similarity"], ANALYSIS_COVER)
        for penalty in ("disOnset", "disExtension"):
            self.assertEqual(self.essentia.paramValue(penalty), float(cover._DISRUPTION))

    def test_scores_and_distance_are_essentias_on_real_matrices(self) -> None:
        matrices = self.matrices()
        distances = [self.assertEssentias(matrix) for matrix in matrices]
        # Anchor: unequal lengths, both ways round, both through the
        # transposed path and not, and finite distances that differ.
        self.assertEqual({cover._turned(*matrix.shape) for matrix in matrices}, {True, False})
        self.assertTrue(all(matrix.shape[0] != matrix.shape[1] for matrix in matrices))
        self.assertTrue(all(math.isfinite(distance) for distance in distances))
        self.assertEqual(len(set(distances)), len(distances))

    def test_matrices_smaller_than_the_recurrences_reach_are_essentias(self) -> None:
        matrix = self.matrices()[0]
        # Cut from where a match lies past the first two rows and columns.
        rows, columns = numpy.argwhere(matrix[2:, 2:] == 1)[0]
        corner = matrix[rows:rows + 5, columns:columns + 5]
        answers = {(height, width): self.assertEssentias(numpy.ascontiguousarray(corner[:height, :width]))
                   for height in range(1, 6) for width in range(1, 6)}
        # Fewer than three rows or columns leave nothing to score.
        for (height, width), distance in answers.items():
            self.assertEqual(cover.unaligned(distance), min(height, width) < 3 or not corner[2:height, 2:width].any())
        # Anchor: some of them align.
        self.assertTrue(any(math.isfinite(distance) for distance in answers.values()))

    def test_nothing_matching_is_essentias_infinity(self) -> None:
        empty = numpy.zeros_like(self.matrices()[0])
        self.assertEqual(self.assertEssentias(empty), math.inf)
        self.assertIsNone(cover.alignment(cover.serra09(empty)[0]))

    def test_essentias_cover_song_similarity_is_never_called(self) -> None:
        def bomb(*_, **__):
            raise AssertionError("Essentia's CoverSongSimilarity was used")
        with patch.object(self.analyser.es, "CoverSongSimilarity", bomb):
            # Anchor: the bomb is live where the Analyser would find it.
            with self.assertRaises(AssertionError):
                cover.essentia_module().CoverSongSimilarity()
            analyser = cover.Analyser()
            distance = analyser.distance(self.profiles["query"], self.profiles["window"])
        self.assertTrue(math.isfinite(distance))
        _, expected_ab = self.essentia(self.analyser.cross(self.profiles["query"], self.profiles["window"]))
        _, expected_ba = self.essentia(self.analyser.cross(self.profiles["window"], self.profiles["query"]))
        self.assertEqual(distance, min(float(expected_ab), float(expected_ba)))

    def test_a_matrix_that_is_empty_or_not_binary_is_refused(self) -> None:
        matrix = self.matrices()[0]
        with self.assertRaisesRegex(cover.CoverError, "empty"):
            cover.serra09(matrix[:0])
        with self.assertRaisesRegex(cover.CoverError, "empty"):
            cover.serra09(matrix[0])
        for value in (0.5, 2, -1, math.nan):
            spoiled = matrix.copy()
            spoiled[3, 4] = value
            with self.assertRaisesRegex(cover.CoverError, "not binary"):
                cover.serra09(spoiled)
        # Anchor: the unspoiled matrix is accepted.
        cover.serra09(matrix)

    def test_the_alignment_ends_where_essentias_best_score_is_and_starts_at_a_match(self) -> None:
        for matrix in self.matrices():
            scores, _ = cover.serra09(matrix)
            found = cover.alignment(scores)
            expected_scores, _ = self.essentia(matrix)
            end = numpy.unravel_index(int(numpy.argmax(expected_scores)), expected_scores.shape)
            self.assertEqual(found.end, (int(end[0]), int(end[1])))
            self.assertEqual(found.score, float(numpy.max(expected_scores)))
            # It starts at a match that follows nothing, before its end.
            self.assertEqual(matrix[found.start], 1)
            self.assertEqual(scores[found.start], 1)
            self.assertLess(found.start, found.end)
            self.assertLessEqual(found.start[1], found.end[1])
            # The rectangle it spans holds its whole score on its own: two
            # empty rows and columns before it, as the recurrence needs.
            rows, columns = found.start[0] - 2, found.start[1] - 2
            alone = numpy.zeros((found.end[0] - rows + 1, found.end[1] - columns + 1), numpy.float32)
            alone[2:, 2:] = matrix[found.start[0]:found.end[0] + 1, found.start[1]:found.end[1] + 1]
            self.assertEqual(float(cover.serra09(alone)[0].max()), found.score)
            # Anchor: a real alignment, longer than one match.
            self.assertGreater(found.score, 1)


def calibration_cheap(a: numpy.ndarray, b: numpy.ndarray) -> float:
    """The quarter-rate distance as the calibration prototype first computed
    it, copied here as written there: partitioned order statistics, both
    directions' serra09, the smaller distance. Its inputs are already at the
    quarter rate."""
    k, kappa = 9, 0.095

    def stack(x):
        return numpy.ascontiguousarray(numpy.concatenate([x[i:len(x) - k + 1 + i] for i in range(k)], axis=1))

    def oti(x, y):
        mx, my = x.mean(0), y.mean(0)
        return int(numpy.argmax([numpy.dot(mx, numpy.roll(my, s)) for s in range(12)]))

    def binary(d):
        kr = max(0, int(kappa * d.shape[1]) - 1)
        kc = max(0, int(kappa * d.shape[0]) - 1)
        rows = numpy.partition(d, kr, axis=1)[:, kr]
        cols = numpy.partition(d, kc, axis=0)[kc]
        return ((d <= rows[:, None]) & (d <= cols[None, :])).astype(numpy.float32)

    if len(a) <= k + 1 or len(b) <= k + 1:
        return math.inf
    b = numpy.roll(b, oti(a, b), axis=1)
    sa, sb = stack(a), stack(b)
    d = (sa * sa).sum(1)[:, None] + (sb * sb).sum(1)[None, :] - 2 * (sa @ sb.T)
    m = binary(d)
    _, ab = cover.serra09(m)
    _, ba = cover.serra09(numpy.ascontiguousarray(m.T))
    return min(float(ab), float(ba))


class QuarterRate(unittest.TestCase):
    def test_frames_are_averaged_in_fours_renormalised_and_a_trailing_group_dropped(self) -> None:
        profiles = numpy.zeros((10, 12), numpy.float32)
        profiles[0:4, 0] = [1.0, 0.5, 0.5, 0.0]
        profiles[0:4, 3] = 0.25
        profiles[8:10] = 1.0  # a trailing group of two
        reduced = cover.quarter_rate(profiles)
        self.assertEqual(reduced.shape, (2, 12))
        self.assertEqual(reduced.dtype, numpy.float32)
        self.assertEqual(float(reduced[0, 0]), 1.0)
        self.assertEqual(float(reduced[0, 3]), 0.5)
        # A group of zero frames stays zero.
        self.assertTrue(numpy.array_equal(reduced[1], numpy.zeros(12, numpy.float32)))

    def test_order_statistics_by_sorted_keys_are_numpys_partition_with_ties_and_negative_zero(self) -> None:
        rng = numpy.random.default_rng(1)
        for case in range(60):
            rows, columns = int(rng.integers(12, 300)), int(rng.integers(12, 200))
            a = rng.random((rows, 108), dtype=numpy.float32)
            b = rng.random((columns, 108), dtype=numpy.float32)
            if case % 3 == 0:
                b[:5] = a[:5]  # exact ties, and distances that round to -0 or below
            an, bn = (a * a).sum(1), (b * b).sum(1)
            for shift in (0, 1):
                d = an[:, None] + bn[None, :]
                d -= numpy.float32(2) * (a @ b.T)
                kr = max(0, int(0.095 * columns) - shift)
                kc = max(0, int(0.095 * rows) - shift)
                wanted = ((d <= numpy.partition(d, kr, axis=1)[:, kr][:, None])
                          & (d <= numpy.partition(d, kc, axis=0)[kc][None, :])).astype(numpy.float32)
                self.assertTrue(numpy.array_equal(cover.binary_within(a, an, b, bn, shift), wanted))
        keys = cover.order_keys(numpy.array([-0.0, 0.0, -1.5, 2.0, -3.0], numpy.float32))
        self.assertEqual(keys[0], keys[1])
        self.assertEqual(list(numpy.argsort(keys, kind="stable")), [4, 2, 0, 1, 3])

    @needs_essentia
    def test_the_distance_is_the_calibrated_one_bit_for_bit(self) -> None:
        analyser = cover.Analyser()
        corpus = {}
        for name, chords, transpose, seed in (("same", support.PROGRESSION, -2, 4),
                                              ("other", support.OTHER_PROGRESSION, 0, 5)):
            samples = support.progression_samples(chords, 22050, transpose=transpose, seed=seed)
            corpus[name] = analyser.features(samples.astype(numpy.float32)).profiles
        query = analyser.features(support.progression_samples(support.PROGRESSION, 22050, seed=1)
                                  .astype(numpy.float32)).profiles
        rng = numpy.random.default_rng(3)
        corpus["random"] = rng.random((400, 12), dtype=numpy.float32)
        quarter = cover.QuarterRate(corpus)
        got = quarter.distances(cover.quarter_rate(query), sorted(corpus))
        for key in corpus:
            self.assertEqual(got[key], calibration_cheap(cover.quarter_rate(query), cover.quarter_rate(corpus[key])),
                             key)
        # Anchor: the same progression in another key is closer than another one.
        self.assertLess(got["same"], got["other"])
        self.assertEqual(quarter.comparisons, 3)

    def test_too_short_is_not_unaligned(self) -> None:
        rng = numpy.random.default_rng(4)
        quarter = cover.QuarterRate({"long": rng.random((200, 12), dtype=numpy.float32),
                                     "short": rng.random((43, 12), dtype=numpy.float32)})
        with self.assertRaises(cover.TooShort):
            quarter.distances(cover.quarter_rate(rng.random((40, 12), dtype=numpy.float32)), ["long"])
        query = cover.quarter_rate(rng.random((200, 12), dtype=numpy.float32))
        # A reference of ten quarter-rate frames is left out and named, never infinite.
        self.assertEqual(sorted(quarter.distances(query, ["long", "short"])), ["long"])
        self.assertEqual(quarter.too_short(["long", "short"]), ["short"])
        self.assertTrue(math.isfinite(quarter.distances(query, ["long"])["long"]))

    def test_nothing_matching_is_infinity(self) -> None:
        rng = numpy.random.default_rng(5)
        quarter = cover.QuarterRate({"a": rng.random((200, 12), dtype=numpy.float32)})
        query = cover.quarter_rate(rng.random((200, 12), dtype=numpy.float32))
        # Anchor: these align.
        self.assertTrue(math.isfinite(quarter.distances(query, ["a"])["a"]))
        with patch.object(cover, "binary_within", lambda a, an, b, bn, shift: numpy.zeros((len(a), len(b)),
                                                                                          numpy.float32)):
            self.assertTrue(cover.unaligned(quarter.distances(query, ["a"])["a"]))


@needs_essentia
@needs_ffmpeg
class MeasureWhole(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = support.scratch(self, "whole-")
        samples = support.progression_samples(support.PROGRESSION, 44100, seed=1, repeats=1)
        self.path = support.write_wav(self.tmp / "song.wav", samples, 44100)
        self.length = len(samples)

    def test_the_whole_recording_in_blocks_is_its_features_bit_for_bit(self) -> None:
        analyser = cover.Analyser()
        # Lengths whose analysis-rate sample counts fall on, just before, and
        # just after the hop, and blocks that end mid-frame.
        hop_in_source = 2048 * 2
        for count in (self.length, 44100 * 5, 44100 * 5 + 1, 44100 * 5 - 1, hop_in_source * 40,
                      hop_in_source * 40 + 3):
            wanted = analyser.features(cover.load([cover.Piece(self.path, 0, count, 44100)])).profiles
            for block in (1000, 2048, 3001, 1 << 20):
                got = cover.measure_whole(self.path, 44100, count, block_samples=block, analyser=analyser)
                self.assertEqual(got.shape, wanted.shape, (count, block))
                self.assertTrue(numpy.array_equal(got, wanted), (count, block))
        # Less than a hop: 500 samples at the analysis rate make two frames.
        tiny = cover.measure_whole(self.path, 44100, 1000, analyser=analyser)
        self.assertEqual(tiny.shape, (2, 12))

    def test_the_audio_is_never_loaded_whole(self) -> None:
        with patch.object(cover, "load", _bomb), patch.object(cover, "decode", _bomb):
            profiles = cover.measure_whole(self.path, 44100, self.length, block_samples=4096)
        self.assertEqual(len(profiles), math.ceil(self.length * 22050 / 44100 / 2048) + 1)

    def test_a_short_decode_a_wrong_rate_and_a_failing_decoder_are_refused(self) -> None:
        with self.assertRaisesRegex(cover.CoverError, "runs past its audio"):
            cover.measure_whole(self.path, 44100, self.length + 44100)
        with self.assertRaisesRegex(cover.CoverError, "is sampled at 44100 Hz"):
            cover.measure_whole(self.path, 48000, 1000)
        with self.assertRaisesRegex(cover.CoverError, "is empty"):
            cover.measure_whole(self.path, 44100, 0)
        with self.assertRaisesRegex(cover.CoverError, "ffmpeg could not run"):
            cover.measure_whole(self.path, 44100, 1000, ffmpeg=str(self.tmp / "absent"))
        from test_probe import fake_program
        broken = fake_program(self.tmp, "ffmpeg-broken", 'echo "cannot decode" >&2; exit 1\n')
        with self.assertRaisesRegex(cover.CoverError, "could not decode .*cannot decode"):
            cover.measure_whole(self.path, 44100, 1000, ffmpeg=broken)


class Software(unittest.TestCase):
    def test_the_blas_library_is_named_with_a_version(self) -> None:
        name, version = cover.blas()
        self.assertNotEqual(name, "unknown")
        self.assertNotEqual(version, "unknown")
        with patch.object(numpy, "show_config", return_value={"Build Dependencies": {"blas": {
                "name": "openblas", "version": "0.3.29"}}}):
            self.assertEqual(cover.blas(), ("openblas", "0.3.29"))
        with patch.object(numpy, "show_config", return_value={"Build Dependencies": {"blas": {
                "name": "accelerate", "version": "unknown"}}}), \
                patch.object(cover.platform, "mac_ver", return_value=("15.5", ("", "", ""), "arm64")):
            self.assertEqual(cover.blas(), ("accelerate", "macOS 15.5"))

    @needs_essentia
    def test_the_software_is_the_profiles_the_blas_library_and_how_it_runs(self) -> None:
        self.assertEqual(cover.software(), (*cover.profile_software(), cover.blas(),
                                            ("blas-runtime", cover.blas_runtime())))

    def test_blas_runs_one_thread_in_this_process_and_its_workers(self) -> None:
        self.assertTrue(cover.blas_runtime().endswith(", 1 thread"))
        self.assertNotIn("unknown", cover.blas_runtime())
        for variable in cover.BLAS_THREAD_VARIABLES:
            self.assertEqual(os.environ[variable], "1", variable)

    def test_numpy_loaded_before_the_pin_is_refused(self) -> None:
        # In a fresh interpreter: numpy first, with no thread variables set.
        script = ("import numpy, cover\ntry:\n    cover.blas_runtime()\nexcept cover.CoverError as exc:\n"
                  "    print('refused:', exc)\n")
        env = {k: v for k, v in os.environ.items() if k not in cover.BLAS_THREAD_VARIABLES}
        import subprocess
        import sys
        completed = subprocess.run([sys.executable, "-c", script], cwd=Path(cover.__file__).parent, env=env,
                                   capture_output=True, text=True, check=True)
        self.assertIn("refused: numpy was imported before cover.py pinned BLAS", completed.stdout)
        # Anchor: cover first is accepted in the same interpreter.
        completed = subprocess.run([sys.executable, "-c", script.replace("import numpy, cover", "import cover, numpy")
                                    + "else:\n    print('pinned')\n"], cwd=Path(cover.__file__).parent, env=env,
                                   capture_output=True, text=True, check=True)
        self.assertEqual(completed.stdout.strip(), "pinned")

    def test_a_kernel_that_cannot_be_asked_is_recorded_as_unknown(self) -> None:
        openblas = {"Build Dependencies": {"blas": {"name": "scipy-openblas", "version": "0.3.29"}}}
        accelerate = {"Build Dependencies": {"blas": {"name": "accelerate", "version": "unknown"}}}
        with patch.object(numpy, "show_config", return_value=openblas), \
                patch.object(cover, "_openblas_runtime", return_value=None):
            self.assertTrue(cover.blas_runtime().startswith("unknown kernel on "))
        with patch.object(numpy, "show_config", return_value=openblas), \
                patch.object(cover, "_openblas_runtime", return_value=("Haswell", 1)):
            self.assertEqual(cover.blas_runtime(), "Haswell, 1 thread")
        with patch.object(numpy, "show_config", return_value=openblas), \
                patch.object(cover, "_openblas_runtime", return_value=("Haswell", 8)), \
                self.assertRaisesRegex(cover.CoverError, "OpenBLAS runs 8 threads"):
            cover.blas_runtime()
        with patch.object(numpy, "show_config", return_value=accelerate), \
                patch.object(cover.subprocess, "run", side_effect=OSError("no sysctl")):
            self.assertTrue(cover.blas_runtime().startswith("unknown kernel on "))
        with patch.object(cover, "_BLAS_PINNED_AT_LOAD", False), \
                self.assertRaisesRegex(cover.CoverError, "imported before cover.py pinned BLAS"):
            cover.blas_runtime()

    def test_openblas_is_asked_through_the_library_numpy_loaded(self) -> None:
        # No OpenBLAS beside this numpy (Accelerate here) answers None; the
        # lookup itself runs where numpy bundles OpenBLAS.
        if "openblas" in cover.blas()[0]:
            kernel, threads = cover._openblas_runtime()
            self.assertEqual(threads, 1)
            self.assertTrue(kernel)
        else:
            self.assertIsNone(cover._openblas_runtime())


if __name__ == "__main__":
    unittest.main()
