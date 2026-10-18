"""The tonal extractor, run by the real runner on synthetic audio (ADR 0074).

The audio is written by Python's `wave` writer (through `support.write_wav`)
and decoded by ffmpeg, as a recording would be; requests are made by the
runner's own code. Tuning is the headline: a drift must be followed window
by window, across the wrap at half a semitone, through clipping and noise,
and silence, talk, and noise must leave gaps rather than values.
"""

from __future__ import annotations

import io
import json
import math
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch

import numpy

import bank
import extract
import support
from support import needs_ffmpeg
from test_cover import needs_essentia

sys.path.insert(0, str(Path(__file__).resolve().parent / "extractors" / "tonal"))
import tonal  # noqa: E402

RATE = 44100
CHORD_SECONDS = 2.0
# C major: C, F, G, A minor.
C_MAJOR = ((60, 64, 67), (65, 69, 72), (67, 71, 74), (57, 60, 64))
CHORD_NAMES = ("C", "F", "G", "Am")


def progression(chords: tuple[tuple[int, ...], ...], cents: Callable[[float], float], repeats: int,
                transpose: int = 0, noise: float = 0.003, seed: int = 0) -> numpy.ndarray:
    """Chords of six-harmonic tones, two seconds each, tuned `cents(t)` from
    440 Hz at each chord's start, with faint white noise, peaking at 0.3."""
    rng = numpy.random.default_rng(seed)
    count = int(CHORD_SECONDS * RATE)
    t = numpy.arange(count) / RATE
    envelope = numpy.minimum(1, numpy.minimum(t / 0.02, (CHORD_SECONDS - t) / 0.05))
    parts = []
    for index, chord in enumerate(chords * repeats):
        offset = cents(index * CHORD_SECONDS) / 1200
        parts.append(envelope * sum(
            numpy.sin(2 * numpy.pi * 440 * 2 ** ((note + transpose - 69) / 12 + offset) * h * t
                      + rng.uniform(0, 2 * numpy.pi)) / h ** 1.2
            for note in chord for h in range(1, 7)))
    samples = numpy.concatenate(parts)
    samples = samples / numpy.abs(samples).max() * 0.3
    return samples + noise * rng.standard_normal(len(samples))


def noise(seconds: float, amplitude: float = 0.05, seed: int = 9) -> numpy.ndarray:
    return amplitude * numpy.random.default_rng(seed).standard_normal(int(seconds * RATE))


def talk(seconds: float, seed: int = 4) -> numpy.ndarray:
    """White noise whose level rises and falls four times a second."""
    t = numpy.arange(int(seconds * RATE)) / RATE
    return 0.1 * numpy.random.default_rng(seed).standard_normal(len(t)) * (0.55 + 0.45 * numpy.sin(2 * numpy.pi
                                                                                                   * 4 * t))


def scratch(test: unittest.TestCase) -> Path:
    directory = Path(tempfile.mkdtemp(prefix="bank-tonal-"))
    test.addCleanup(shutil.rmtree, directory, True)
    return directory


def write(directory: Path, name: str, samples: numpy.ndarray, flac: bool = False) -> Path:
    path = support.write_wav(directory / f"{name}.wav", samples, RATE)
    if flac:
        support.ffmpeg("-i", str(path), "-ac", "2", "-c:a", "flac", str(path.with_suffix(".flac")))
        path.unlink()
        path = path.with_suffix(".flac")
    return path


def run(directory: Path, source: Path, *extra: str) -> tuple[int, dict]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = extract.main(["run", "tonal", "--content", str(source), "--bank", str(directory / "bank"), *extra])
    return code, json.loads(out.getvalue() or err.getvalue())


def entry(directory: Path, source: Path) -> bank.Entry:
    found = bank.Bank(directory / "bank").entry(bank.file_digest(source), "tonal")
    assert found is not None
    return found


def measured(test: unittest.TestCase, directory: Path, source: Path) -> bank.Entry:
    code, answer = run(directory, source)
    test.assertEqual(code, 0, answer)
    return entry(directory, source)


def a_request(directory: Path, source: Path) -> dict[str, Any]:
    """The request the runner would give tonal, made by the runner's code
    once `frames` is in the bank."""
    out = io.StringIO()
    with redirect_stdout(out):
        assert extract.main(["run", "frames", "--content", str(source), "--bank", str(directory / "bank")]) == 0
    runner = extract.Run(bank.Catalog.load(), (directory / "bank").resolve(), source, "audio/wav", None,
                         {}, {}, None)
    _, keys, _ = runner.graph(["tonal"])
    output = directory / "out"
    output.mkdir(exist_ok=True)
    return runner.request("tonal", keys, output)


class Manifest(unittest.TestCase):
    def test_the_manifest_declares_exactly_what_the_program_writes(self) -> None:
        extractor = bank.Catalog.load()["tonal"]
        self.assertEqual(extractor.manifest["parameters"], tonal.parameters())
        self.assertEqual(extractor.needs, ["frames/hpcp", "frames/rms_db"])
        declared = {name: out["kind"] for name, out in extractor.outputs.items()}
        written = {**{name: "scalar" for name in tonal.SCALARS}, **{name: "spans" for name in tonal.SPANS},
                   **{name: "series" for name in tonal.SERIES}}
        self.assertEqual(declared, written)
        for name, dtype in tonal.SERIES.items():
            self.assertEqual(extractor.outputs[name]["dtype"], dtype)
        self.assertEqual(extractor.outputs["tuning_windows"]["columns"], len(tonal.TUNING_COLUMNS))
        # It decodes and frames as frames does, so its rows are frames' rows.
        frames_parameters = bank.Catalog.load()["frames"].manifest["parameters"]
        for field in ("decode_arguments", "sample_scale", "analysis_rate", "frame_size", "hop_size", "framing",
                      "windowing", "essentia"):
            self.assertEqual(tonal.parameters()[field], frames_parameters[field], field)

    def test_windows_begin_on_block_edges(self) -> None:
        """A block's frames are decided before any window reads them, so every
        window starts and ends on a block's edge (or the recording's end)."""
        block = tonal.PITCHED["block_frames"]
        for settings in (tonal.TUNING, tonal.KEY):
            self.assertEqual(settings["window_frames"] % block, 0)
            self.assertEqual(settings["hop_frames"] % block, 0)


class Arithmetic(unittest.TestCase):
    def test_cents_fold_into_half_a_semitone_either_side(self) -> None:
        self.assertEqual(tonal.fold(0.0), 0.0)
        self.assertEqual(tonal.fold(65.0), -35.0)   # Essentia's top end is 65 cents
        self.assertEqual(tonal.fold(-35.0), -35.0)
        self.assertEqual(tonal.fold(50.0), -50.0)
        self.assertEqual(tonal.fold(49.0), 49.0)

    def test_a_window_is_written_near_the_recording(self) -> None:
        self.assertEqual(tonal.near(-49.0, 48.0), 51.0)
        self.assertEqual(tonal.near(52.0, -48.0), -48.0)
        self.assertEqual(tonal.near(10.0, 12.0), 10.0)
        self.assertEqual(tonal.near(60.0, -45.0), -40.0)

    def test_frequency_of_cents(self) -> None:
        self.assertEqual(tonal.frequency_of(0.0), 440.0)
        self.assertAlmostEqual(tonal.frequency_of(1200.0), 880.0)
        self.assertAlmostEqual(tonal.frequency_of(-30.0), 432.4, places=1)

    def test_a_peak_falls_in_the_bin_of_its_distance_from_the_grid(self) -> None:
        frequencies = numpy.array([0.0, 440 * 2 ** (17.4 / 1200), 220 * 2 ** ((300 + 99.6) / 1200)],
                                  dtype=numpy.float32)
        histogram = tonal.deviation_histogram(frequencies, numpy.array([5.0, 2.0, 3.0], dtype=numpy.float32))
        self.assertEqual(histogram[17], 2.0)
        self.assertEqual(histogram[99], 3.0)
        self.assertEqual(histogram.sum(), 5.0)  # the zero frequency is left out
        empty = tonal.deviation_histogram(numpy.zeros(0, dtype=numpy.float32), numpy.zeros(0, dtype=numpy.float32))
        self.assertEqual(empty.shape, (tonal.BINS,))
        self.assertFalse(empty.any())

    def test_concentration_goes_round_the_circle(self) -> None:
        histogram = numpy.zeros(tonal.BINS)
        histogram[[98, 2]] = 1.0
        histogram[50] = 2.0
        self.assertEqual(tonal.concentration(histogram, 0.0, 15), 0.5)
        self.assertEqual(tonal.concentration(histogram, -50.0, 15), 0.5)  # -50 is bin 50
        self.assertEqual(tonal.concentration(numpy.zeros(tonal.BINS), 0.0, 15), 0.0)
        self.assertEqual(tonal.best_concentration(histogram, 15), 0.5)
        self.assertEqual(tonal.best_concentration(numpy.zeros(tonal.BINS), 15), 0.0)
        # Noise: the window's share of the circle.
        self.assertAlmostEqual(tonal.best_concentration(numpy.ones(tonal.BINS), 15), 0.31)

    def test_windows_cover_the_rows_and_none_hides_inside_another(self) -> None:
        self.assertEqual(tonal.windows(0, 4, 2), [])
        self.assertEqual(tonal.windows(3, 4, 2), [(0, 3)])
        self.assertEqual(tonal.windows(4, 4, 2), [(0, 4)])
        self.assertEqual(tonal.windows(5, 4, 2), [(0, 4), (2, 5)])
        self.assertEqual(tonal.windows(6, 4, 2), [(0, 4), (2, 6)])
        self.assertEqual(tonal.windows(7, 4, 2), [(0, 4), (2, 6), (4, 7)])
        for rows in range(1, 40):
            spans = tonal.windows(rows, 6, 3)
            self.assertEqual(spans[-1][1], rows)
            for before, after in zip(spans, spans[1:]):
                self.assertGreater(after[1], before[1])

    def test_a_frame_stands_for_the_hop_centred_on_it(self) -> None:
        hop = tonal.HOP / tonal.RATE
        samples = 10 * tonal.HOP + 100  # 12 frames; the last centred 1,948 samples past the end
        self.assertEqual(tonal.extent(3, 5, samples), (2.5 * hop, 4.5 * hop))
        self.assertEqual(tonal.extent(0, 2, samples), (0.0, 1.5 * hop))  # nothing before sample 0
        self.assertEqual(tonal.extent(10, 12, samples), (9.5 * hop, samples / tonal.RATE))  # nor past the end
        self.assertEqual(tonal.extent(11, 12, samples), (samples / tonal.RATE, samples / tonal.RATE))

    def test_the_tuning_grid_centres_a_row_on_its_frames(self) -> None:
        grid = tonal.tuning_grid()
        for row in (0, 1, 7):
            first = row * tonal.TUNING["hop_frames"]
            last = first + tonal.TUNING["window_frames"] - 1
            centre = grid["first_centre"] + row * grid["hop"]
            self.assertEqual(centre, (first + last) * tonal.HOP / 2)
            if row:  # row 0's window is clipped to the audio's start
                begin, end = tonal.extent(first, last + 1, 10 ** 9)
                self.assertAlmostEqual(centre / tonal.RATE, (begin + end) / 2)
            self.assertEqual(grid["frame"], (last + 1 - first) * tonal.HOP)

    def test_runs_merge_one_label_and_stop_at_a_gap(self) -> None:
        labels = ["C", "C", "F", "F", "F", "G", "G"]
        mask = [True, True, True, False, True, True, True]
        samples = 6 * tonal.HOP
        spans = tonal.runs(labels, mask, [1.0, 0.5, 0.25, 9.0, 0.5, 1.0, 1.0], samples)
        at = lambda frame: tonal.extent(frame, frame + 1, 10 ** 9)[0]  # noqa: E731
        self.assertEqual([(s["label"], s["start"], s["end"], s["value"]) for s in spans],
                         [("C", 0.0, at(2), 0.75), ("F", at(2), at(3), 0.25), ("F", at(4), at(5), 0.5),
                          ("G", at(5), samples / tonal.RATE, 1.0)])
        self.assertEqual(tonal.runs([], [], [], samples), [])
        self.assertEqual(tonal.runs(["C"], [False], [1.0], samples), [])

    def test_detuned_needs_a_tuning_beyond_the_limit(self) -> None:
        limit = tonal.KEY["max_detune_cents"]
        self.assertFalse(tonal.detuned({"tuning_cents": limit, "tuning_confidence": 0.9}))
        self.assertTrue(tonal.detuned({"tuning_cents": limit + 1, "tuning_confidence": 0.9}))
        self.assertTrue(tonal.detuned({"tuning_cents": -limit - 1, "tuning_confidence": 0.9}))
        self.assertFalse(tonal.detuned({"tuning_cents": -limit - 1, "tuning_confidence": 0.0}))  # no tuning

    def test_spread_needs_two_windows(self) -> None:
        self.assertEqual(tonal.spread([]), 0.0)
        self.assertEqual(tonal.spread([12.0]), 0.0)
        self.assertAlmostEqual(tonal.spread([0.0, 10.0]), 8.0)

    def test_dissonance_is_given_the_loud_peaks_in_frequency_order(self) -> None:
        frequencies = numpy.arange(1, 301, dtype=numpy.float32) * 10
        magnitudes = numpy.full(300, 0.02, dtype=numpy.float32)  # within 40 dB of the loudest
        magnitudes[::5] = 1.0     # sixty loud peaks
        magnitudes[7] = 0.5
        magnitudes[200:] = 0.005  # more than 40 dB down
        kept_f, kept_m = tonal.dissonance_peaks(frequencies, magnitudes)
        self.assertEqual(len(kept_f), tonal.DISSONANCE["maxPeaks"])
        self.assertTrue(numpy.all(numpy.diff(kept_f) > 0))
        self.assertEqual(int((kept_m == 1.0).sum()), 40)  # the loud ones below 2,000 Hz
        self.assertIn(80.0, kept_f)  # the 0.5 peak
        self.assertTrue(numpy.all(kept_f <= 2000))  # nothing more than 40 dB down
        few_f, few_m = tonal.dissonance_peaks(frequencies[:20], magnitudes[:20])
        self.assertEqual(len(few_f), 20)  # fewer than the limit, all within 40 dB: all kept
        empty = numpy.zeros(0, dtype=numpy.float32)
        self.assertEqual(len(tonal.dissonance_peaks(empty, empty)[0]), 0)


@needs_ffmpeg
@needs_essentia
class Tuning(unittest.TestCase):
    def test_a_drift_is_followed_window_by_window_and_gaps_stay_empty(self) -> None:
        """Forty cents of drift over two minutes, then silence and noise and
        talk, then the same progression held 25 cents sharp."""
        directory = scratch(self)
        drift = lambda t: -20 + t / 3  # noqa: E731  (-20 to +20 over 120 s)
        first = progression(C_MAJOR, drift, repeats=15)
        gap = numpy.concatenate([numpy.zeros(10 * RATE), noise(15), talk(15)])
        second = progression(C_MAJOR, lambda t: 25, repeats=8, transpose=2, seed=1)
        source = write(directory, "session", numpy.concatenate([first, gap, second]), flac=True)
        found = measured(self, directory, source)
        first_end, gap_end = len(first) / RATE, (len(first) + len(gap)) / RATE

        spans = found.items("tuning_drift")
        before = [span for span in spans if span["end"] <= first_end]
        after = [span for span in spans if span["start"] >= gap_end]
        # Anchor: there is a drift to follow, and both parts were measured.
        self.assertGreaterEqual(len(before), 9)
        self.assertGreaterEqual(len(after), 4)
        for span in before:
            truth = drift((span["start"] + span["end"]) / 2)
            self.assertLess(abs(span["value"] - truth), 4.0, (span, truth))
        for span in after:
            self.assertLess(abs(span["value"] - 25), 2.0, span)
        self.assertGreater(before[-1]["value"] - before[0]["value"], 25)
        # No window lies mostly in the gap: silence, noise, and talk give nothing.
        for span in spans:
            inside = min(span["end"], gap_end) - max(span["start"], first_end)
            self.assertLess(inside, (span["end"] - span["start"]) / 2, span)

        pitched = found.series("pitched")[:, 0]
        rows = len(pitched)
        self.assertEqual(rows, len(found.series("dissonance")))
        row = lambda t: int(t / tonal.SECONDS_PER_ROW)  # noqa: E731
        self.assertTrue(pitched[row(5):row(first_end) - 30].all())
        self.assertFalse(pitched[row(first_end) + 30:row(gap_end) - 30].any())
        self.assertTrue(pitched[row(gap_end) + 30:rows - 30].all())

        windows = found.series("tuning_windows")
        self.assertEqual(windows.shape, (len(tonal.windows(rows, 216, 108)), 3))
        listed = {round(span["start"], 6) for span in spans}
        for index, (cents, confidence, share) in enumerate(windows):
            start = tonal.extent(index * tonal.TUNING["hop_frames"], rows, 10 ** 9)[0]
            self.assertEqual(round(start, 6) in listed, share >= 0.5, (index, share))
            self.assertTrue(0 <= confidence <= 1 and 0 <= share <= 1)
        grid = found.grid("tuning_windows")
        self.assertEqual(grid, tonal.tuning_grid())
        # The first window starts with the audio and the last ends with it,
        # not half a hop before or one past the frames' nominal end.
        self.assertEqual(spans[0]["start"], 0.0)
        self.assertAlmostEqual(spans[-1]["end"], (len(first) + len(gap) + len(second)) / RATE, places=4)

        # The whole recording: the longer, drifting part spreads its evidence
        # over 40 cents, so the steady 25 cents sharp part decides the mode.
        self.assertLess(abs(found.scalar("tuning_cents") - 25), 3)
        self.assertAlmostEqual(found.scalar("tuning_frequency"), tonal.frequency_of(found.scalar("tuning_cents")))
        self.assertGreater(found.scalar("drift_spread_cents"), 25)

    def test_steady_tunings_are_found_across_the_wrap_and_through_clipping_and_noise(self) -> None:
        directory = scratch(self)
        cases = (("flat48", -48, lambda x: x), ("sharp48", 48, lambda x: x),
                 ("clipped17", 17, lambda x: numpy.clip(x * 4, -0.1, 0.1)),
                 ("noisy-30", -30, lambda x: x + noise(len(x) / RATE, 0.05)))
        for name, cents, change in cases:
            with self.subTest(name=name):
                source = write(directory, name, change(progression(C_MAJOR, lambda t: cents, repeats=7)))
                found = measured(self, directory, source)
                self.assertLess(abs(found.scalar("tuning_cents") - cents), 3)
                self.assertGreater(found.scalar("tuning_confidence"), 0.5)
                drift = found.items("tuning_drift")
                self.assertGreaterEqual(len(drift), 4)
                # Near the wrap no window jumps a semitone to the other side.
                for span in drift:
                    self.assertLess(abs(span["value"] - cents), 3, span)
                self.assertLess(found.scalar("drift_spread_cents"), 3)

    def test_a_drift_across_the_wrap_stays_continuous(self) -> None:
        directory = scratch(self)
        source = write(directory, "wrap", progression(C_MAJOR, lambda t: 40 + t / 5, repeats=13))
        values = [span["value"] for span in measured(self, directory, source).items("tuning_drift")]
        self.assertGreaterEqual(len(values), 8)
        self.assertLess(values[0], 47)
        self.assertGreater(values[-1], 53)  # past +50, written as +5x, not -4x
        self.assertTrue(all(b >= a - 2 for a, b in zip(values, values[1:])), values)

    def test_silence_and_noise_measure_no_tuning(self) -> None:
        directory = scratch(self)
        for name, samples in (("silence", numpy.zeros(30 * RATE)), ("noise", noise(40, 0.1)),
                              ("talk", talk(40))):
            with self.subTest(name=name):
                found = measured(self, directory, write(directory, name, samples))
                self.assertFalse(found.series("pitched").any())
                self.assertEqual(found.scalar("tuning_frequency"), 440.0)
                self.assertEqual(found.scalar("tuning_cents"), 0.0)
                self.assertEqual(found.scalar("tuning_confidence"), 0.0)
                self.assertEqual(found.items("tuning_drift"), [])
                self.assertEqual(found.scalar("drift_spread_cents"), 0.0)
                self.assertEqual((found.scalar("key"), found.scalar("scale"), found.scalar("key_strength")),
                                 ("", "", 0.0))
                self.assertEqual(found.items("keys"), [])
                self.assertEqual(found.items("chords"), [])
                self.assertEqual(found.scalar("chords_changes_rate"), 0.0)
                self.assertFalse(found.series("tuning_windows")[:, 2].any())


@needs_ffmpeg
@needs_essentia
class Harmony(unittest.TestCase):
    def test_key_and_chords_of_a_progression_and_its_transposition(self) -> None:
        directory = scratch(self)
        for transpose, key, names in ((0, "C", CHORD_NAMES), (2, "D", ("D", "G", "A", "Bm"))):
            with self.subTest(transpose=transpose):
                source = write(directory, f"song{transpose}",
                               progression(C_MAJOR, lambda t: 0, repeats=10, transpose=transpose))
                found = measured(self, directory, source)
                self.assertEqual((found.scalar("key"), found.scalar("scale")), (key, "major"))
                self.assertGreater(found.scalar("key_strength"), 0.7)
                windows = found.items("keys")
                self.assertGreaterEqual(len(windows), 3)
                self.assertEqual({span["label"] for span in windows}, {f"{key} major"})
                spans = found.items("chords")
                total = sum(span["end"] - span["start"] for span in spans)
                right = sum(span["end"] - span["start"] for span in spans if span["label"] in names)
                self.assertGreater(total, 70)
                self.assertGreater(right / total, 0.9)
                # Chord changes fall near the two-second marks: on average
                # within a hop (half a hop late would be a frame's centre
                # taken for its start). ChordsDetection averages two seconds
                # of profiles, so a change between chords sharing two notes
                # (A minor to C) can come a few hops late.
                changes = [span["start"] for span in spans[1:] if span["end"] - span["start"] > 1]
                self.assertGreaterEqual(len(changes), 20)
                offsets = numpy.array([start - round(start / CHORD_SECONDS) * CHORD_SECONDS for start in changes])
                self.assertLess(abs(float(offsets.mean())), tonal.SECONDS_PER_ROW, offsets)
                self.assertLess(abs(float(numpy.median(offsets))), tonal.SECONDS_PER_ROW, offsets)
                self.assertLess(float(numpy.abs(offsets).max()), 0.45, offsets)
                self.assertFalse(found.scalar("detuned"))
                self.assertGreater(found.scalar("chords_changes_rate"), 0)
                self.assertGreater(found.scalar("chords_number_rate"), 0)

    def test_a_detuned_recording_leaves_key_and_chords_empty(self) -> None:
        """Near half a semitone from 440 Hz the profiles split each pitch
        class: C major 46 cents sharp gives no key rather than A minor."""
        directory = scratch(self)
        for cents, is_detuned in ((36, False), (46, True), (-46, True)):
            with self.subTest(cents=cents):
                found = measured(self, directory, write(directory, f"detuned{cents}",
                                                        progression(C_MAJOR, lambda t: cents, repeats=6)))
                self.assertLess(abs(found.scalar("tuning_cents") - cents), 3)  # anchor: the tuning is found
                self.assertIs(found.scalar("detuned"), is_detuned)
                if is_detuned:
                    self.assertEqual((found.scalar("key"), found.scalar("scale"), found.scalar("key_strength")),
                                     ("", "", 0.0))
                    self.assertEqual((found.items("keys"), found.items("chords")), ([], []))
                    self.assertEqual((found.scalar("chords_changes_rate"), found.scalar("chords_number_rate")),
                                     (0.0, 0.0))
                    self.assertTrue(found.series("pitched").any())  # the tuning outputs stay
                    self.assertGreaterEqual(len(found.items("tuning_drift")), 2)
                else:
                    self.assertEqual((found.scalar("key"), found.scalar("scale")), ("C", "major"))
                    self.assertTrue(found.items("chords"))

    def test_pitched_frames_with_empty_profiles_have_no_key(self) -> None:
        es = tonal.cover.essentia_module()
        hpcp = numpy.zeros((700, 12), dtype=numpy.float32)
        pitched = numpy.ones(700, dtype=bool)
        self.assertEqual(tonal.keys(es, hpcp, pitched, 700 * tonal.HOP),
                         {"key": "", "scale": "", "key_strength": 0.0, "keys": []})
        hpcp[:, [3, 7, 10]] = 1.0  # anchor: the same frames with a C major profile have a key in every window
        found = tonal.keys(es, hpcp, pitched, 700 * tonal.HOP)
        self.assertEqual((found["key"], len(found["keys"])), ("C", len(tonal.windows(700, 324, 162))))
        hpcp[400:] = 0.0  # a window whose pitched frames are all empty is left out
        found = tonal.keys(es, hpcp, pitched, 700 * tonal.HOP)
        self.assertEqual(len(found["keys"]), len([w for w in tonal.windows(700, 324, 162) if w[0] < 400]))

    def test_the_key_reads_the_banks_profiles(self) -> None:
        """Key and chords come from frames/hpcp, not from a second analysis:
        profiles rotated by two semitones in the bank give D for C."""
        directory = scratch(self)
        source = write(directory, "song", progression(C_MAJOR, lambda t: 0, repeats=6))
        request = a_request(directory, source)
        self.assertEqual(tonal.measure(request)["key"], "C")  # anchor
        hpcp = numpy.fromfile(request["needs"]["frames/hpcp"]["path"], dtype="<f4").reshape(-1, 12)
        rotated = directory / "rotated.f32"
        numpy.roll(hpcp, 2, axis=1).astype("<f4").tofile(rotated)  # A is bin 0; C up two is D
        request["needs"]["frames/hpcp"]["path"] = str(rotated)
        moved = tonal.measure(request)
        self.assertEqual(moved["key"], "D")
        self.assertIn("D", {span["label"] for span in moved["chords"]})


@needs_ffmpeg
@needs_essentia
class Dissonance(unittest.TestCase):
    def test_a_cluster_is_rougher_than_a_fifth_and_silence_is_zero(self) -> None:
        directory = scratch(self)
        t = numpy.arange(10 * RATE) / RATE

        def tones(frequencies: tuple[float, ...]) -> numpy.ndarray:
            return sum(0.1 * numpy.sin(2 * numpy.pi * f * t) for f in frequencies)
        source = write(directory, "intervals", numpy.concatenate([
            tones((220.0, 330.0)), numpy.zeros(5 * RATE), tones((220.0, 233.08, 246.94)), noise(10, 0.1)]))
        found = measured(self, directory, source)
        roughness = found.series("dissonance")[:, 0]
        row = lambda t: int(t / tonal.SECONDS_PER_ROW)  # noqa: E731
        fifth, quiet, cluster, hiss = (roughness[row(a) + 5:row(b) - 5] for a, b in ((0, 10), (10, 15), (15, 25),
                                                                                     (25, 35)))
        self.assertLess(float(fifth.max()), 0.05)
        self.assertTrue((quiet == 0).all())
        self.assertGreater(float(cluster.min()), 0.4)
        self.assertGreater(float(hiss.mean()), 0.4)
        self.assertEqual(found.grid("dissonance"), {"sample_rate": 22050, "hop": 2048, "frame": 4096,
                                                    "first_centre": 0})

    def test_a_quiet_floor_below_the_gate_reads_zero(self) -> None:
        """Room tone at about -70 dBFS is not silence, but it is below the
        level gate, so it adds nothing to an average of dissonance."""
        directory = scratch(self)
        source = write(directory, "room", noise(10, 0.0003))
        request = a_request(directory, source)
        rms_db = numpy.fromfile(request["needs"]["frames/rms_db"]["path"], dtype="<f4")
        inner = rms_db[5:-5]
        # Anchor: the floor is quiet but not silent, and ungated it is rough.
        self.assertTrue(((inner > -90) & (inner < tonal.GATE["rms_db_min"])).all(), inner)
        with patch.dict(tonal.GATE, {"rms_db_min": -100}):
            self.assertGreater(float(tonal.measure(request)["dissonance"][5:-5].mean()), 0.3)
        self.assertFalse(tonal.measure(request)["dissonance"].any())


@needs_ffmpeg
@needs_essentia
class Runner(unittest.TestCase):
    def test_a_second_run_is_a_hit_and_starts_nothing(self) -> None:
        directory = scratch(self)
        source = write(directory, "song", progression(C_MAJOR, lambda t: 0, repeats=3))

        def bomb(*_args, **_kwargs):
            raise AssertionError("a cache hit started a program")
        # Anchor: on a miss, the bomb goes off.
        with patch.object(extract.subprocess, "Popen", bomb), self.assertRaisesRegex(AssertionError, "cache hit"):
            run(directory, source)
        self.assertEqual(run(directory, source)[0], 0)
        with patch.object(extract.subprocess, "Popen", bomb):
            code, answer = run(directory, source)
        self.assertEqual(code, 0, answer)
        self.assertEqual({name: item["status"] for name, item in answer["extractors"].items()},
                         {"frames": "hit", "tonal": "hit"})

    def test_rows_line_up_with_frames(self) -> None:
        directory = scratch(self)
        for seconds in ("0.05", "1", "7.3"):
            with self.subTest(seconds=seconds):
                source = support.sine_wav(directory / f"tone{seconds}.wav", 48000, seconds)
                found = measured(self, directory, source)
                frames_entry = bank.Bank(directory / "bank").entry(bank.file_digest(source), "frames")
                rows = frames_entry.series("hpcp").shape[0]
                self.assertEqual(found.series("dissonance").shape, (rows, 1))
                self.assertEqual(found.series("pitched").shape, (rows, 1))
                self.assertEqual(found.series("pitched").dtype, numpy.dtype("u1"))

    def test_other_settings_are_refused_not_measured(self) -> None:
        directory = scratch(self)
        source = support.sine_wav(directory / "tone.wav", 22050, "1")
        config = directory / "config.json"
        config.write_text(json.dumps({"parameters": {"tonal": {"gate": {"rms_db_min": -40}}}}))
        code, answer = run(directory, source, "--config", str(config))
        self.assertEqual(code, 1)
        self.assertEqual(answer["extractors"]["frames"]["status"], "ran")
        self.assertIn("fixed settings", answer["extractors"]["tonal"]["log"])

    def test_audio_with_no_samples_blocks_it(self) -> None:
        directory = scratch(self)
        code, answer = run(directory, support.empty_wav(directory / "empty.wav"))
        self.assertEqual(code, 1)
        self.assertEqual(answer["extractors"]["tonal"]["status"], "blocked")


@needs_ffmpeg
@needs_essentia
class Refusals(unittest.TestCase):
    """Each way the extractor refuses, through its own entry points."""

    def setUp(self) -> None:
        self.directory = scratch(self)
        self.source = write(self.directory, "song", progression(C_MAJOR, lambda t: 0, repeats=2))
        self.request = a_request(self.directory, self.source)

    def refused(self, pattern: str, **arguments: Any) -> None:
        with self.assertRaisesRegex(tonal.TonalError, pattern):
            tonal.measure(self.request, **arguments)

    def test_a_decode_of_another_length_is_refused(self) -> None:
        audio = numpy.concatenate(list(tonal.frames.decoded_blocks(str(self.source))))
        self.assertEqual(len(tonal.measure(self.request, blocks=[audio])["dissonance"]),
                         self.request["needs"]["frames/hpcp"]["shape"][0])  # anchor: the same audio fits
        self.refused("made 6 frames", blocks=[audio[:5 * tonal.HOP]])
        self.refused("more frames than", blocks=[audio, audio])

    def test_a_missing_or_misshapen_need_is_refused(self) -> None:
        hpcp = self.request["needs"]["frames/hpcp"]
        del self.request["needs"]["frames/rms_db"]
        self.refused("does not carry frames/rms_db")
        self.request = a_request(self.directory, self.source)
        self.request["needs"]["frames/hpcp"] = {**hpcp, "grid": {**hpcp["grid"], "hop": 1024}}
        self.refused("not on the frames grid")
        self.request["needs"]["frames/hpcp"] = {**hpcp, "dtype": "f64"}
        self.refused("not an f32 series")
        self.request["needs"]["frames/hpcp"] = {**hpcp, "shape": [hpcp["shape"][0] + 1, 12]}
        self.refused("holds")
        rms = self.request["needs"]["frames/rms_db"]
        short = self.directory / "short.f32"
        numpy.zeros(rms["shape"][0] - 1, dtype="<f4").tofile(short)
        self.request["needs"]["frames/hpcp"] = hpcp
        self.request["needs"]["frames/rms_db"] = {**rms, "path": str(short), "shape": [rms["shape"][0] - 1, 1]}
        self.refused("rows and frames/rms_db")

    def test_chords_of_another_length_are_refused(self) -> None:
        class Short:
            def ChordsDetection(self, **_settings: Any) -> Callable[[numpy.ndarray], tuple[list, list]]:
                return lambda hpcp: (["C"] * (len(hpcp) - 1), [1.0] * (len(hpcp) - 1))
        hpcp = numpy.ones((50, 12), dtype=numpy.float32)
        with self.assertRaisesRegex(tonal.TonalError, "gave 49 chords for 50 frames"):
            tonal.chords(Short(), hpcp, numpy.ones(50, dtype=bool), "C", "major", 50 * tonal.HOP)

    def test_a_dissonance_that_is_not_finite_is_refused(self) -> None:
        real = tonal.cover.essentia_module()

        class NotFinite:
            def __getattr__(self, name: str) -> Any:
                if name == "Dissonance":
                    return lambda: (lambda frequencies, magnitudes: float("nan"))
                return getattr(real, name)
        self.assertTrue(numpy.isfinite(tonal.measure(self.request)["dissonance"]).all())  # anchor
        with patch.object(tonal.cover, "essentia_module", lambda: NotFinite()):
            self.refused("Dissonance gave a value that is not finite")

    def test_windows_left_open_are_refused(self) -> None:
        tuning = tonal.Tuning(tonal.cover.essentia_module(), 300)
        empty = numpy.zeros(0, dtype=numpy.float32)
        for row in range(250):
            tuning.add(row, False, None, empty, empty)
        with self.assertRaisesRegex(tonal.TonalError, "never closed"):
            tuning.result(300 * tonal.HOP)

    def test_main_reports_a_failure_and_writes_nothing(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(tonal.main([str(self.directory / "absent.json")]), 1)
            self.assertEqual(tonal.main([]), 2)
        self.assertIn("tonal:", err.getvalue())
        self.assertIn("usage", err.getvalue())
        request = self.directory / "request.json"
        request.write_bytes(bank.canonical({**self.request, "parameters": {**self.request["parameters"],
                                                                            "key": {}}}))
        with redirect_stderr(io.StringIO()) as err:
            self.assertEqual(tonal.main([str(request)]), 1)
        self.assertIn("fixed settings", err.getvalue())
        self.assertEqual(list(Path(self.request["output_dir"]).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
