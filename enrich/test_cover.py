"""Essentia cover similarity and the growing-window search (ADRs 0071, 0072).

The search is tested with the measure replaced by tables, so every decision
(growth, stall, degrade, rollback, short windows, the prefilter) is checked
exactly. The measure itself runs the pinned Essentia build on synthetic tunes.
"""

from __future__ import annotations

import importlib.metadata
import math
import os
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
        calls = []
        real = analyser.cross

        def cross(a, b):
            calls.append((a is query.profiles, b is query.profiles))
            return real(a, b)
        for answers, expected in (((0.3, 0.2), 0.2), ((0.1, 0.4), 0.1)):
            replies = iter(answers)
            with patch.object(analyser, "cross", cross), \
                    patch.object(analyser, "cover", lambda matrix: (matrix, next(replies))):
                self.assertEqual(analyser.distance(query.profiles, same.profiles), expected)
        self.assertEqual(calls, [(True, False), (False, True)] * 2)

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


if __name__ == "__main__":
    unittest.main()
