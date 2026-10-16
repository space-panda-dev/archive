"""The matcher as a tool: features, alignment, exact scores, and decoding (ADR 0067)."""

from __future__ import annotations

import unittest
from fractions import Fraction

import numpy

import adapter
import match
import support
from support import needs_ffmpeg

RATE = match.SETTINGS["analysis_rate"]


def profiles(notes, transpose: int = 0, seed: int = 0):
    return match.profiles(support.tune_samples(notes, RATE, transpose=transpose, seed=seed))


class Scores(unittest.TestCase):
    def test_the_same_tune_transposed_scores_lower_than_another_tune(self) -> None:
        query = profiles(support.TUNE, transpose=3, seed=1)
        same = match.compare(query, profiles(support.TUNE, seed=2))
        other = match.compare(query, profiles(support.OTHER_TUNE, seed=3))
        # Anchor: the two are different, scored, and the transposition was found.
        self.assertIsNotNone(same.text)
        self.assertIsNotNone(other.text)
        self.assertNotEqual(same.text, other.text)
        self.assertEqual(match.transposition(query, profiles(support.TUNE, seed=2)), 3)
        self.assertLess(adapter_decimal(same.text), adapter_decimal(other.text))

    def test_a_score_does_not_depend_on_the_key(self) -> None:
        query = profiles(support.TUNE, transpose=3, seed=1)
        reference = profiles(support.TUNE, seed=2)
        expected = match.compare(query, reference)
        for shift in range(1, 12):
            rotated = numpy.roll(reference, shift, axis=1)
            self.assertEqual(match.transposition(query, rotated), (3 - shift) % 12)
            self.assertEqual(match.compare(query, rotated), expected)

    def test_scores_are_deterministic(self) -> None:
        samples = support.tune_samples(support.TUNE, RATE, transpose=3, seed=1)
        first, second = match.profiles(samples), match.profiles(samples.copy())
        self.assertTrue(numpy.array_equal(first, second))
        reference = profiles(support.OTHER_TUNE, seed=3)
        self.assertEqual(match.compare(first, reference), match.compare(second, reference))
        self.assertEqual(match.compare(first, reference).text, match.compare(first, reference.copy()).text)

    def test_qmax_follows_serra09_exactly(self) -> None:
        self.assertEqual(match.qmax(numpy.eye(4, dtype=bool)), Fraction(4))
        # A gap in the diagonal costs the onset penalty and the path resumes:
        # 1, 2, then 2 - 0.5, then 2.5, 3.5.
        gapped = numpy.eye(5, dtype=bool)
        gapped[2, 2] = False
        self.assertEqual(match.qmax(gapped), Fraction(7, 2))
        # A skipped row or column continues the path without a penalty.
        skipping = numpy.zeros((4, 3), dtype=bool)
        skipping[0, 0] = skipping[2, 1] = skipping[3, 2] = True
        self.assertEqual(match.qmax(skipping), Fraction(3))
        self.assertEqual(match.qmax(numpy.zeros((3, 3), dtype=bool)), Fraction(0))

    def test_no_aligned_path_has_no_score(self) -> None:
        self.assertIsNone(match.score_text(100, Fraction(0)))

    def test_score_text_is_exact_and_rounds_half_to_even(self) -> None:
        self.assertEqual(match.score_text(4, Fraction(2)), "1.000000")
        # 1/128 = 0.0078125: half way, so the even neighbour wins.
        self.assertEqual(match.score_text(1, Fraction(128)), "0.007812")
        self.assertEqual(match.score_text(1, Fraction(3, 2)), "0.666667")
        self.assertEqual(match.score_text(2, Fraction(1)), "1.414214")

    def test_a_frame_without_a_peak_is_all_zeros(self) -> None:
        samples = numpy.concatenate([numpy.zeros(RATE * 2), support.tune_samples(support.TUNE, RATE, repeats=1)])
        result = match.profiles(samples)
        silent = 2 * RATE // match.SETTINGS["hop_size"] - 2
        self.assertTrue(numpy.all(result[:silent] == 0))
        self.assertFalse(numpy.any(numpy.isnan(result)))
        self.assertEqual(result[-5:].max(axis=1).tolist(), [1.0] * 5)

    def test_too_little_audio_is_refused(self) -> None:
        with self.assertRaisesRegex(match.MatchError, "shorter than"):
            match.profiles(numpy.zeros(RATE))
        with self.assertRaisesRegex(match.MatchError, "cannot be stacked"):
            match.stack(numpy.zeros((match.SETTINGS["stack_frames"] - 1, 12)))


def adapter_decimal(text: str):
    from decimal import Decimal
    return Decimal(text)


class Parameters(unittest.TestCase):
    def test_parameters_are_canonical_json_with_the_limit(self) -> None:
        data = adapter.canonical(match.parameters("0.25"))
        self.assertIn(b'"proposal_limit":"0.25"', data)
        self.assertEqual(adapter.canonical(match.parameters("0.25")), data)
        self.assertNotEqual(adapter.canonical(match.parameters("0.3")), data)

    def test_a_limit_that_is_not_decimal_text_is_refused(self) -> None:
        for bad in ("-1", "1e3", ".5", "0.", "", "01", "nan", " 1"):
            with self.assertRaises(match.MatchError, msg=bad):
                match.parameters(bad)


@needs_ffmpeg
class Decoding(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = support.scratch(self, "match-")
        self.wav = support.write_wav(self.tmp / "tune.wav", support.tune_samples(support.TUNE, 44100), 44100)
        self.length = len(support.tune_samples(support.TUNE, 44100))

    def test_a_span_decodes_to_exactly_its_samples(self) -> None:
        samples = match.decode(match.Piece(self.wav, 1000, 5000, 44100), support.FFMPEG, support.FFPROBE)
        self.assertEqual(len(samples), 4000)
        whole = match.decode(match.Piece(self.wav, 0, self.length, 44100), support.FFMPEG, support.FFPROBE)
        self.assertTrue(numpy.array_equal(samples, whole[1000:5000]))

    def test_pieces_are_resampled_and_joined_in_order(self) -> None:
        half = self.length // 2
        joined = match.load([match.Piece(self.wav, 0, half, 44100), match.Piece(self.wav, half, self.length, 44100)],
                            support.FFMPEG, support.FFPROBE)
        whole = match.load([match.Piece(self.wav, 0, self.length, 44100)], support.FFMPEG, support.FFPROBE)
        self.assertEqual(len(joined), len(whole))
        self.assertEqual(len(whole), self.length // 2)
        with self.assertRaisesRegex(match.MatchError, "no audio"):
            match.load([], support.FFMPEG, support.FFPROBE)

    def test_a_span_past_the_audio_is_refused(self) -> None:
        with self.assertRaisesRegex(match.MatchError, "runs past its audio"):
            match.decode(match.Piece(self.wav, 0, self.length + 1, 44100), support.FFMPEG, support.FFPROBE)

    def test_a_span_in_another_rate_is_refused(self) -> None:
        with self.assertRaisesRegex(match.MatchError, "sampled at 44100 Hz"):
            match.decode(match.Piece(self.wav, 0, 100, 48000), support.FFMPEG, support.FFPROBE)

    def test_an_empty_span_is_refused(self) -> None:
        with self.assertRaisesRegex(match.MatchError, "is empty"):
            match.decode(match.Piece(self.wav, 10, 10, 44100), support.FFMPEG, support.FFPROBE)

    def test_a_file_without_audio_or_unreadable_is_refused(self) -> None:
        video = self.tmp / "picture.mkv"
        support.ffmpeg("-f", "lavfi", "-i", "testsrc=d=0.2:s=32x32", "-c:v", "ffv1", str(video))
        with self.assertRaisesRegex(match.MatchError, "has no audio stream"):
            match.stream_rate(video, support.FFPROBE)
        garbage = self.tmp / "garbage.wav"
        garbage.write_bytes(b"not audio at all")
        with self.assertRaisesRegex(match.MatchError, "could not read"):
            match.stream_rate(garbage, support.FFPROBE)

    def test_programs_that_cannot_run_or_fail_are_refused(self) -> None:
        missing = str(self.tmp / "no-such-program")
        with self.assertRaisesRegex(match.MatchError, "ffprobe could not run"):
            match.stream_rate(self.wav, missing)
        with self.assertRaisesRegex(match.MatchError, "ffmpeg could not run"):
            match.decode(match.Piece(self.wav, 0, 100, 44100), missing, support.FFPROBE)
        with self.assertRaisesRegex(match.MatchError, "could not decode"):
            match.decode(match.Piece(self.wav, 0, 100, 44100), support.FFPROBE, support.FFPROBE)


if __name__ == "__main__":
    unittest.main()
