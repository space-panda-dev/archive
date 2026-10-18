"""The tonal extractor, run by the real runner on synthetic audio (ADR 0074).

The audio is written by Python's `wave` writer (through `support.write_wav`)
and decoded by ffmpeg, as a recording would be; requests are made by the
runner's own code. Tuning is the headline: a drift must be followed window
by window, across the wrap at half a semitone, through clipping and noise,
and silence, talk, and noise must leave gaps rather than values, and never
a tuning. The key must name a mode, not only major or minor, and must hold
on a recording tuned near half a semitone from 440 Hz.
"""

from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Callable, Sequence
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
# C major: C, F, G, A minor, with their roots in the bass.
C_MAJOR = ((60, 64, 67), (65, 69, 72), (67, 71, 74), (57, 60, 64))
C_BASS = (48, 41, 43, 45)
# F dorian's vamp: Fm7 and Bb7 (the D of Bb7 is dorian's sixth), with their roots in the bass.
F_DORIAN = ((65, 68, 72, 75), (62, 65, 68, 70))
F_BASS = (41, 46)


def progression(chords: tuple[tuple[int, ...], ...], cents: Callable[[float], float], repeats: int,
                transpose: int = 0, noise: float = 0.003, seed: int = 0,
                bass: Sequence[int] | None = None) -> numpy.ndarray:
    """Chords of six-harmonic tones, two seconds each, tuned `cents(t)` from
    440 Hz at each chord's start, with faint white noise, peaking at 0.3;
    `bass` adds one note under each chord."""
    rng = numpy.random.default_rng(seed)
    count = int(CHORD_SECONDS * RATE)
    t = numpy.arange(count) / RATE
    envelope = numpy.minimum(1, numpy.minimum(t / 0.02, (CHORD_SECONDS - t) / 0.05))
    parts = []
    for index, chord in enumerate(chords * repeats):
        offset = cents(index * CHORD_SECONDS) / 1200
        notes = chord + ((bass[index % len(bass)],) if bass else ())
        parts.append(envelope * sum(
            numpy.sin(2 * numpy.pi * 440 * 2 ** ((note + transpose - 69) / 12 + offset) * h * t
                      + rng.uniform(0, 2 * numpy.pi)) / h ** 1.2
            for note in notes for h in range(1, 7)))
    samples = numpy.concatenate(parts)
    samples = samples / numpy.abs(samples).max() * 0.3
    return samples + noise * rng.standard_normal(len(samples))


def noise(seconds: float, amplitude: float = 0.05, seed: int = 9) -> numpy.ndarray:
    return amplitude * numpy.random.default_rng(seed).standard_normal(int(seconds * RATE))


def pink(seconds: float, seed: int = 1) -> numpy.ndarray:
    """Noise whose power falls by 3 dB an octave, peaking at 0.3."""
    rng = numpy.random.default_rng(seed)
    count = int(seconds * RATE)
    spectrum = numpy.fft.rfft(rng.standard_normal(count))
    spectrum /= numpy.sqrt(numpy.maximum(numpy.arange(len(spectrum)), 1))
    samples = numpy.fft.irfft(spectrum, count)
    return samples / numpy.abs(samples).max() * 0.3


def talk(seconds: float, seed: int = 4) -> numpy.ndarray:
    """White noise whose level rises and falls four times a second."""
    t = numpy.arange(int(seconds * RATE)) / RATE
    return 0.1 * numpy.random.default_rng(seed).standard_normal(len(t)) * (0.55 + 0.45 * numpy.sin(2 * numpy.pi
                                                                                                   * 4 * t))


def hum(seconds: float, mains: int, amplitude: float = 0.003) -> numpy.ndarray:
    """Mains hum: the mains frequency and its next three harmonics, falling
    as 1/k."""
    t = numpy.arange(int(seconds * RATE)) / RATE
    return amplitude * sum(numpy.sin(2 * numpy.pi * mains * k * t) / k for k in (1, 2, 3, 4))


def gappy_talk(seconds: float, seed: int = 5) -> numpy.ndarray:
    """Quiet talk with silent gaps between its syllables: white noise at
    0.03 whose level falls to nothing four times a second."""
    t = numpy.arange(int(seconds * RATE)) / RATE
    return 0.03 * numpy.random.default_rng(seed).standard_normal(len(t)) * (0.5 + 0.5 * numpy.sin(2 * numpy.pi
                                                                                                   * 4 * t)) ** 2


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


def frames_peaks(samples: numpy.ndarray) -> list[tuple[numpy.ndarray, numpy.ndarray]]:
    """Every frame's spectral peaks as tonal finds them, at 22,050 Hz."""
    es = tonal.cover.essentia_module()
    windowing = es.Windowing(**tonal.cover._options(tonal.cover.ANALYSIS["windowing"]))
    spectrum, peaks = es.Spectrum(), es.SpectralPeaks(**tonal.cover._options(tonal.PEAKS))
    signal = samples[::2].astype(numpy.float32)  # 44,100 Hz to 22,050: noise has nothing to alias
    return [peaks(spectrum(windowing(signal[start:start + tonal.FRAME])))
            for start in range(0, len(signal) - tonal.FRAME, tonal.HOP)]


class Manifest(unittest.TestCase):
    def test_the_manifest_declares_exactly_what_the_program_writes(self) -> None:
        extractor = bank.Catalog.load()["tonal"]
        self.assertEqual(extractor.manifest["parameters"], tonal.parameters())
        self.assertEqual(extractor.needs, ["frames/rms_db"])
        declared = {name: out["kind"] for name, out in extractor.outputs.items()}
        written = {**{name: "scalar" for name in tonal.SCALARS}, **{name: "spans" for name in tonal.SPANS},
                   **{name: "series" for name in tonal.SERIES}}
        self.assertEqual(declared, written)
        for name, dtype in tonal.SERIES.items():
            self.assertEqual(extractor.outputs[name]["dtype"], dtype)
        self.assertEqual(extractor.outputs["tuning_windows"]["columns"], len(tonal.TUNING_COLUMNS))
        self.assertEqual(extractor.outputs["chroma"]["columns"], 12)
        self.assertEqual(extractor.outputs["bass_chroma"]["columns"], 12)
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
        self.assertEqual(tonal.fold(65.0), -35.0)
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

    def test_the_histogram_counts_the_loud_peaks_below_two_kilohertz(self) -> None:
        frequencies = numpy.array([100.0, 500.0, 1999.0, 2000.0, 3000.0, 700.0], dtype=numpy.float32)
        magnitudes = numpy.array([1.0, 0.5, 0.2, 1.0, 1.0, 0.009], dtype=numpy.float32)  # 0.009 is 41 dB down
        kept_f, kept_m = tonal.histogram_peaks(frequencies, magnitudes)
        self.assertEqual(kept_f.tolist(), [100.0, 500.0, 1999.0])
        self.assertEqual(kept_m.tolist(), [1.0, 0.5, numpy.float32(0.2)])
        empty = numpy.zeros(0, dtype=numpy.float32)
        self.assertEqual(len(tonal.histogram_peaks(empty, empty)[0]), 0)

    def test_mains_hum_is_found_where_two_harmonics_run_through_half_the_frames(self) -> None:
        f32 = lambda *values: numpy.array(values, dtype=numpy.float32)  # noqa: E731
        hum_frame = (f32(60.4, 119.2, 200.0, 440.0), f32(1.0, 1.0, 1.0, 1.0))
        talk_frame = (f32(300.0, 517.0), f32(1.0, 1.0))
        self.assertEqual(tonal.mains_lines([hum_frame, hum_frame, talk_frame]), [60])
        self.assertEqual(tonal.mains_lines([hum_frame, talk_frame, talk_frame]), [])  # in a third of them
        # One harmonic is a note, not hum; 50 Hz's harmonics are found the same way.
        one = (f32(60.0, 440.0), f32(1.0, 1.0))
        self.assertEqual(tonal.mains_lines([one, one]), [])
        # A G in music tuned 35 cents sharp sits on 50 Hz's fourth and eighth
        # harmonics (G4 400 Hz and its octave): not the lowest, so not hum.
        g_sharp = (f32(200.1, 400.2, 800.4, 600.3), f32(1.0, 1.0, 1.0, 1.0))
        self.assertEqual(tonal.mains_lines([g_sharp, g_sharp]), [])
        fifty = (f32(50.9, 99.0, 150.0), f32(1.0, 1.0, 1.0))
        self.assertEqual(tonal.mains_lines([fifty]), [50])
        # A line more than 40 dB below the frame's loudest peak is not counted.
        quiet = (f32(60.0, 120.0, 440.0), f32(0.001, 0.001, 1.0))
        self.assertEqual(tonal.mains_lines([quiet]), [])
        # No frames, or frames without peaks, find nothing.
        empty = (f32(), f32())
        self.assertEqual(tonal.mains_lines([]), [])
        self.assertEqual(tonal.mains_lines([empty, empty]), [])

    def test_hum_is_left_out_near_its_harmonics_only(self) -> None:
        frequencies = numpy.array([58.0, 61.4, 119.0, 123.47, 440.0, 1980.5, 2040.0], dtype=numpy.float32)
        magnitudes = numpy.arange(1, 8, dtype=numpy.float32)
        kept_f, kept_m = tonal.without_mains(frequencies, magnitudes, [60])
        # 61.4 and 119 are within 1.5 Hz of a harmonic; 58.0 is not; B2 (123.47) and A4 stay;
        # 1980.5 is a harmonic below 2 kHz; 2040 is above it and stays.
        self.assertEqual(kept_f.tolist(), [58.0, numpy.float32(123.47), 440.0, 2040.0])
        self.assertEqual(kept_m.tolist(), [1.0, 4.0, 5.0, 7.0])
        self.assertIs(tonal.without_mains(frequencies, magnitudes, [])[0], frequencies)
        empty = numpy.zeros(0, dtype=numpy.float32)
        self.assertEqual(len(tonal.without_mains(empty, empty, [50, 60])[0]), 0)
        self.assertEqual(tonal.mains_of(3), [50, 60])
        self.assertEqual(tonal.mains_of(0), [])

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

    def test_the_histogram_tuning_is_its_densest_stretch_refined(self) -> None:
        histogram = numpy.zeros(tonal.BINS)
        histogram[[16, 17, 18]] = [1.0, 2.0, 1.0]
        histogram[60] = 1.5  # outweighs any one bin of the cluster, not the cluster
        self.assertAlmostEqual(tonal.histogram_tuning(histogram, 15), 17.5, places=6)
        wrapped = numpy.zeros(tonal.BINS)
        wrapped[[98, 99, 0, 1]] = 1.0  # either side of the grid
        self.assertAlmostEqual(abs(tonal.histogram_tuning(wrapped, 15)), 0.0, places=6)
        sharp = numpy.zeros(tonal.BINS)
        sharp[[47, 48, 49, 50, 51]] = 1.0  # either side of half a semitone
        self.assertAlmostEqual(abs(tonal.histogram_tuning(sharp, 15)), 49.5, places=6)
        self.assertIsNone(tonal.histogram_tuning(numpy.zeros(tonal.BINS), 15))

    def test_the_recordings_tuning_is_the_weighted_median_round_the_circle(self) -> None:
        self.assertEqual(tonal.weighted_median([1.0, 2.0, 30.0], [1.0, 1.0, 1.0]), 2.0)
        self.assertEqual(tonal.weighted_median([1.0, 2.0, 30.0], [1.0, 1.0, 5.0]), 30.0)
        self.assertEqual(tonal.recording_tuning([10.0, 12.0, -30.0], [0.5, 0.5, 0.9]), 10.0)
        # Windows either side of half a semitone are one tuning, not two.
        self.assertAlmostEqual(tonal.recording_tuning([49.0, -49.0, 48.0], [0.6, 0.6, 0.6]), 49.0)
        self.assertAlmostEqual(tonal.recording_tuning([-49.0, 49.0, -48.0], [0.6, 0.6, 0.6]), -49.0)
        self.assertIsNone(tonal.recording_tuning([], []))

    def test_each_frame_takes_the_confident_windows_tuning(self) -> None:
        local = tonal.frame_tuning(10, [2.0, 6.0], [10.0, 20.0], 15.0)
        self.assertEqual(local.tolist(), [10.0, 10.0, 10.0, 12.5, 15.0, 17.5, 20.0, 20.0, 20.0, 20.0])
        self.assertEqual(tonal.frame_tuning(3, [], [], 7.0).tolist(), [7.0, 7.0, 7.0])

    def test_a_profile_folds_each_peak_on_its_frames_tuning(self) -> None:
        sharp = 30.0
        c4 = 440 * 2 ** ((-9 + sharp / 100) / 12)  # C, 30 cents sharp
        frequencies = numpy.array([c4, 6000.0], dtype=numpy.float32)
        magnitudes = numpy.array([2.0, 9.0], dtype=numpy.float32)
        folded = tonal.profile(frequencies, magnitudes, sharp, [100, 5000])
        self.assertAlmostEqual(float(folded[3]), 4.0, places=4)  # C is bin 3; energy, on the grid
        self.assertAlmostEqual(float(folded.sum()), 4.0, places=4)  # 6 kHz is outside the band
        # Half a semitone from the grid weighs nothing.
        between = tonal.profile(numpy.array([c4 * 2 ** (0.5 / 12)], dtype=numpy.float32),
                                numpy.array([1.0], dtype=numpy.float32), sharp, [100, 5000])
        self.assertAlmostEqual(float(between.sum()), 0.0, places=6)
        # Anchor: on 440 Hz's grid the same C is 30 cents off, and weighs less.
        self.assertLess(float(tonal.profile(frequencies, magnitudes, 0.0, [100, 5000])[3]), 0.8 * 4.0)
        self.assertFalse(tonal.profile(frequencies, magnitudes, sharp, [40, 250]).any())

    def test_profile_peaks_keep_the_bands_and_the_loud(self) -> None:
        frequencies = numpy.array([30.0, 41.0, 300.0, 4999.0, 5001.0, 800.0], dtype=numpy.float32)
        magnitudes = numpy.array([1.0, 1.0, 1.0, 1.0, 1.0, 0.0009], dtype=numpy.float32)  # 61 dB down
        kept_f, _ = tonal.profile_peaks(frequencies, magnitudes)
        self.assertEqual(kept_f.tolist(), [41.0, 300.0, 4999.0])
        empty = numpy.zeros(0, dtype=numpy.float32)
        self.assertEqual(len(tonal.profile_peaks(empty, empty)[0]), 0)

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


class Key(unittest.TestCase):
    """The key of profiles made by hand: bin 0 is A, so C is 3 and F is 8."""

    @staticmethod
    def profile(weights: dict[int, float]) -> numpy.ndarray:
        values = numpy.full(12, 0.01)
        for note, weight in weights.items():
            values[note] = weight
        return values

    def test_every_mode_of_three_collections_is_a_candidate(self) -> None:
        self.assertEqual(len(tonal.KEY_CANDIDATES), 12 * 7 + 12 + 12)
        self.assertEqual(len({(tonic, mode) for tonic, mode, _, _ in tonal.KEY_CANDIDATES}),
                         len(tonal.KEY_CANDIDATES))
        for (tonic, mode, collection, _), template in zip(tonal.KEY_CANDIDATES, tonal.KEY_TEMPLATES):
            self.assertEqual(collection[0], tonic)
            self.assertEqual(len(set(collection)), 7)
            self.assertEqual(int(numpy.argmax(template)), tonic)
            self.assertEqual(sorted(numpy.flatnonzero(template > 2.0)), sorted(collection))
        # The diatonic modes of one tonic are seven collections; of one collection, seven tonics.
        dorian = next((c, n) for t, m, c, n in tonal.KEY_CANDIDATES if (t, m) == (8, "dorian"))
        ionian = next((c, n) for t, m, c, n in tonal.KEY_CANDIDATES if (t, m) == (6, "ionian"))
        self.assertEqual(set(dorian[0]), set(ionian[0]))  # F dorian holds Eb major's notes
        self.assertEqual((dorian[1], ionian[1]), ("Eb major scale", "Eb major scale"))
        # Every collection is named once, by its notes: 12 diatonic, 12 of each minor.
        self.assertEqual(len(tonal.COLLECTION_NAMES), 36)
        for name in tonal.COLLECTION_NAMES:
            notes = {frozenset(c) for _, _, c, n in tonal.KEY_CANDIDATES if n == name}
            self.assertEqual(len(notes), 1, name)
        self.assertIn("A harmonic minor scale", tonal.COLLECTION_NAMES)

    def test_the_tonic_decides_between_modes_of_one_collection(self) -> None:
        # Eb major's notes: Eb F G Ab Bb C D (bins 6 8 10 11 1 3 5).
        notes = {6: 1.0, 8: 1.0, 10: 1.0, 11: 1.0, 1: 1.0, 3: 1.0, 5: 1.0}
        on_f = self.profile({**notes, 8: 3.0, 3: 2.5, 11: 2.2})  # F, its fifth C, its third Ab
        found = tonal.key_of(on_f)
        self.assertEqual((found["key"], found["mode"], found["scale"]), ("F", "dorian", "minor"))
        self.assertEqual(found["scale_notes"], "F G Ab Bb C D Eb")
        on_eb = self.profile({**notes, 6: 3.0, 1: 2.5, 10: 2.2})  # Eb, Bb, G
        self.assertEqual((tonal.key_of(on_eb)["key"], tonal.key_of(on_eb)["mode"]), ("Eb", "ionian"))
        self.assertGreater(found["key_margin"], 0)
        self.assertLessEqual(found["key_strength"], 1.0)
        self.assertEqual(found["collection"], "Eb major scale")
        self.assertTrue(found["tonic_named"])
        self.assertGreaterEqual(found["tonic_margin"], float(tonal.KEY["min_tonic_margin"]))
        self.assertGreater(found["collection_margin"], 0)

    def test_a_tonic_no_note_leans_towards_is_not_named(self) -> None:
        """Eb major's notes, none leaning: a best tonic and mode come out, but
        no tonic leads by the margin, so the label is the collection."""
        flat = tonal.key_of(self.profile({6: 1.0, 8: 1.0, 10: 1.0, 11: 1.0, 1: 1.0, 3: 1.0, 5: 1.0}))
        self.assertEqual(flat["collection"], "Eb major scale")
        self.assertLess(flat["tonic_margin"], float(tonal.KEY["min_tonic_margin"]))
        self.assertFalse(flat["tonic_named"])
        self.assertEqual(tonal.key_label(flat), "Eb major scale")
        # Anchor: leaning on F names it.
        leaning = tonal.key_of(self.profile({6: 1.0, 8: 3.0, 10: 1.0, 11: 2.2, 1: 1.0, 3: 2.5, 5: 1.0}))
        self.assertEqual(tonal.key_label(leaning), "F dorian")
        # Asked for a collection, the best tonic of its notes, even where another wins.
        within = tonal.key_of(self.profile({6: 1.0, 8: 3.0, 10: 1.0, 11: 2.2, 1: 1.0, 3: 2.5, 5: 1.0}),
                              "Ab major scale")
        self.assertEqual(within["collection"], "Ab major scale")
        self.assertIsNone(tonal.key_of(numpy.zeros(12), "Ab major scale"))

    def test_the_collection_path_pays_for_each_change(self) -> None:
        names = tonal.COLLECTION_NAMES
        c_major, g_major = names.index("C major scale"), names.index("G major scale")

        def rows(leads: list[tuple[int, float]]) -> numpy.ndarray:
            out = numpy.full((len(leads), len(tonal.KEY_CANDIDATES)), 0.4)
            for row, (collection, lead) in enumerate(leads):
                out[row, tonal.KEY_COLLECTIONS == names[collection]] = lead
            return out
        switch = float(tonal.KEY["region_switch"])
        # One window leaning to G by less than the switch stays in C ...
        brief = rows([(c_major, 0.5), (c_major, 0.5), (g_major, 0.5 + switch / 2), (c_major, 0.5)])
        self.assertEqual(tonal.collection_path(brief, switch), [c_major] * 4)
        # ... and three windows leaning by more change it.
        held = rows([(c_major, 0.7), (g_major, 0.6), (g_major, 0.6), (g_major, 0.6)])
        self.assertEqual(tonal.collection_path(held, switch), [c_major] + [g_major] * 3)
        # Anchor: with no price, the brief lean is followed.
        self.assertEqual(tonal.collection_path(brief, 0.0)[2], g_major)
        self.assertEqual(tonal.collection_path(numpy.zeros((0, len(tonal.KEY_CANDIDATES))), switch), [])

    def test_a_raised_seventh_reads_harmonic_minor(self) -> None:
        # A harmonic minor: A B C D E F G# (bins 0 2 3 5 7 8 11).
        found = tonal.key_of(self.profile({0: 3.0, 2: 1.0, 3: 2.2, 5: 1.0, 7: 2.5, 8: 1.0, 11: 1.0}))
        self.assertEqual((found["key"], found["mode"], found["scale"]), ("A", "harmonic minor", "minor"))
        self.assertEqual(found["scale_notes"], "A B C D E F Ab")

    def test_no_weight_and_no_contrast_have_no_key(self) -> None:
        self.assertIsNone(tonal.key_of(numpy.zeros(12)))
        self.assertIsNone(tonal.key_of(numpy.ones(12)))

    def test_the_bass_leans_the_tonic(self) -> None:
        """C major's notes with the bass on A read A aeolian; with it on C,
        C ionian."""
        treble = numpy.zeros((10, 12))
        treble[:, [3, 5, 7, 8, 10, 0, 2]] = 1.0
        bass = numpy.zeros((10, 12))
        bass[:, 0] = 1.0
        on_a = tonal.key_of(tonal.key_profile(treble, bass))
        bass[:, 0], bass[:, 3] = 0.0, 1.0
        on_c = tonal.key_of(tonal.key_profile(treble, bass))
        self.assertEqual(((on_a["key"], on_a["mode"]), (on_c["key"], on_c["mode"])),
                         (("A", "aeolian"), ("C", "ionian")))
        # A silent frame adds nothing; with no bass the treble decides alone.
        silent = numpy.vstack([treble, numpy.zeros((5, 12))])
        self.assertTrue(numpy.allclose(tonal.key_profile(silent, numpy.zeros((15, 12))),
                                       tonal.key_profile(treble, numpy.zeros((10, 12)))))

    def test_regions_lay_overlapping_windows_end_to_end(self) -> None:
        spans = [{"start": 0.0, "end": 30.0, "label": "F dorian", "value": 0.8},
                 {"start": 15.0, "end": 45.0, "label": "F dorian", "value": 0.6},
                 {"start": 30.0, "end": 60.0, "label": "Eb ionian", "value": 0.7},
                 {"start": 90.0, "end": 120.0, "label": "Eb ionian", "value": 0.9}]
        regions = tonal.regions(spans)
        self.assertEqual([(r["start"], r["end"], r["label"]) for r in regions],
                         [(0.0, 37.5, "F dorian"), (37.5, 60.0, "Eb ionian"), (90.0, 120.0, "Eb ionian")])
        self.assertAlmostEqual(regions[0]["value"], (0.8 * 22.5 + 0.6 * 15.0) / 37.5)
        self.assertEqual(tonal.regions([]), [])


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

        # The whole recording: the confidence-weighted median of the
        # confident windows, so the drifting part's middle, not either end.
        confident = [(c, w) for c, w, s in windows if s >= 0.5 and w >= 0.4]
        self.assertGreaterEqual(len(confident), 13)  # anchor: nearly every listed window is confident
        self.assertEqual(found.scalar("tuning_window_count"), len(confident))
        expected = tonal.weighted_median([c for c, _ in confident], [w for _, w in confident])
        self.assertAlmostEqual(found.scalar("tuning_cents"), expected, delta=0.01)
        self.assertTrue(-10 < found.scalar("tuning_cents") < 25)
        self.assertAlmostEqual(found.scalar("tuning_frequency"), tonal.frequency_of(found.scalar("tuning_cents")))
        self.assertGreater(found.scalar("drift_spread_cents"), 25)
        self.assertEqual(found.items("detuned_spans"), [])

    def test_steady_tunings_are_found_across_the_wrap_and_through_clipping_and_noise(self) -> None:
        directory = scratch(self)
        cases = (("flat48", -48, lambda x: x), ("sharp48", 48, lambda x: x),
                 ("clipped17", 17, lambda x: numpy.clip(x * 4, -0.1, 0.1)),
                 ("noisy-30", -30, lambda x: x + noise(len(x) / RATE, 0.05)),
                 ("pink-noisy12", 12, lambda x: x + 0.3 * pink(len(x) / RATE)))
        for name, cents, change in cases:
            with self.subTest(name=name):
                source = write(directory, name, change(progression(C_MAJOR, lambda t: cents, repeats=7)))
                found = measured(self, directory, source)
                self.assertLess(abs(found.scalar("tuning_cents") - cents), 3)
                self.assertGreater(found.scalar("tuning_confidence"), 0.4)
                drift = found.items("tuning_drift")
                self.assertGreaterEqual(len(drift), 4)
                # Near the wrap no window jumps a semitone to the other side.
                for span in drift:
                    self.assertLess(abs(span["value"] - cents), 3, span)
                self.assertLess(found.scalar("drift_spread_cents"), 3)

    def test_a_drift_across_the_wrap_stays_continuous(self) -> None:
        """From 40 to 61 cents sharp: past half a semitone the windows are
        written on whichever side of it the recording's tuning falls, but
        never jump a semitone between neighbours."""
        directory = scratch(self)
        found = measured(self, directory, write(directory, "wrap", progression(C_MAJOR, lambda t: 40 + t / 5,
                                                                              repeats=13)))
        values = [span["value"] for span in found.items("tuning_drift")]
        self.assertGreaterEqual(len(values), 8)
        self.assertGreater(values[-1] - values[0], 15)  # anchor: the drift was followed
        self.assertTrue(all(0 <= b - a < 5 for a, b in zip(values, values[1:])), values)
        whole = found.scalar("tuning_cents")
        self.assertTrue(all(abs(value - whole) < 15 for value in values), (whole, values))
        self.assertTrue(abs(whole) > 45, whole)  # a tuning near half a semitone, on either side

    def test_noise_never_gives_a_tuning_where_tuning_frequency_gave_minus_23_cents(self) -> None:
        """White and pink noise, every frame forced through as pitched: the
        windows never reach the confidence a tuning needs, so none is
        claimed. Essentia's TuningFrequency, which version 2 used, reads pink
        noise as 434.2 Hz, the -23 cents a dense master also fell to."""
        es = tonal.cover.essentia_module()
        for name, samples in (("white", noise(30, 0.1, seed=1)), ("pink", pink(30))):
            with self.subTest(name=name):
                peaks = frames_peaks(samples)
                tuning = tonal.Tuning(len(peaks))
                for row, (frequencies, magnitudes) in enumerate(peaks):
                    tuning.add(row, True, tonal.deviation_histogram(*tonal.histogram_peaks(frequencies, magnitudes)))
                outputs, rows, anchors = tuning.result(len(peaks) * tonal.HOP)
                self.assertTrue((rows[:, 2] == 1).all())  # anchor: every window was fully pitched
                self.assertTrue((rows[:, 1] < 0.4).all(), rows[:, 1])
                self.assertEqual((outputs["tuning_cents"], outputs["tuning_confidence"]), (0.0, 0.0))
                self.assertEqual(outputs["tuning_frequency"], 440.0)
                self.assertEqual(anchors, [])
                self.assertFalse(outputs["detuned"])
                if name == "pink":
                    old = es.TuningFrequency(resolution=1)
                    for frequencies, magnitudes in peaks:
                        estimate = old(frequencies, magnitudes)
                    self.assertEqual(round(float(estimate[1])), -23)

    def test_mains_hum_under_talk_neither_pitches_it_nor_moves_the_tuning(self) -> None:
        """A rehearsal: quiet talk with gaps over a room floor and mains hum,
        then a C major progression. In the gaps the hum's harmonics are the
        loudest peaks, all on one offset from the grid (60 Hz's 49 cents,
        50 Hz's 35), so a block of talk would agree on a tuning. Without the
        hum left out it did: the talk was pitched and the tuning windows over
        it read the hum's offset."""
        directory = scratch(self)
        rng = numpy.random.default_rng(3)
        music = progression(C_MAJOR, lambda t: 0, repeats=6, bass=C_BASS)
        for mains in (60, 50):
            with self.subTest(mains=mains):
                room = numpy.concatenate([gappy_talk(60), music])
                samples = room + hum(len(room) / RATE, mains) + 0.002 * rng.standard_normal(len(room))
                source = write(directory, f"hum{mains}", samples)
                found = measured(self, directory, source)
                pitched = found.series("pitched")[:, 0]
                row = lambda t: int(t / tonal.SECONDS_PER_ROW)  # noqa: E731
                self.assertFalse(pitched[:row(55)].any())
                self.assertTrue(pitched[row(65):len(pitched) - 30].all())
                self.assertLess(abs(found.scalar("tuning_cents")), 3)
                self.assertEqual((found.scalar("key"), found.scalar("mode")), ("C", "ionian"))
                self.assertEqual([span["start"] >= 50 for span in found.items("tuning_drift")],
                                 [True] * len(found.items("tuning_drift")))
                # Anchor: with the hum left in, the talk is pitched.
                request = a_request(directory, source)
                with patch.dict(tonal.HUM, {"min_lines": 99}):
                    unfiltered = tonal.measure(request)
                self.assertGreater(float(unfiltered["pitched"][:row(55), 0].mean()), 0.5)

    def test_silence_and_noise_measure_no_tuning(self) -> None:
        directory = scratch(self)
        for name, samples in (("silence", numpy.zeros(30 * RATE)), ("noise", noise(40, 0.1)),
                              ("pink", pink(40)), ("talk", talk(40))):
            with self.subTest(name=name):
                found = measured(self, directory, write(directory, name, samples))
                self.assertFalse(found.series("pitched").any())
                self.assertEqual(found.scalar("tuning_frequency"), 440.0)
                self.assertEqual(found.scalar("tuning_cents"), 0.0)
                self.assertEqual(found.scalar("tuning_confidence"), 0.0)
                self.assertEqual(found.scalar("tuning_window_count"), 0)
                self.assertEqual(found.items("tuning_drift"), [])
                self.assertEqual(found.scalar("drift_spread_cents"), 0.0)
                for scalar, empty in tonal.NO_KEY.items():
                    self.assertEqual(found.scalar(scalar), empty, scalar)
                self.assertEqual((found.items("keys"), found.items("key_regions")), ([], []))
                self.assertFalse(found.series("tuning_windows")[:, 2].any())
                self.assertEqual(found.series("chroma").shape[1], 12)
                self.assertEqual(found.series("chroma").any(), name != "silence")


@needs_ffmpeg
@needs_essentia
class Harmony(unittest.TestCase):
    def test_key_of_a_progression_and_its_transposition(self) -> None:
        directory = scratch(self)
        for transpose, key, notes in ((0, "C", "C D E F G A B"), (2, "D", "D E F# G A B C#")):
            with self.subTest(transpose=transpose):
                source = write(directory, f"song{transpose}", progression(C_MAJOR, lambda t: 0, repeats=10,
                                                                          transpose=transpose, bass=C_BASS))
                found = measured(self, directory, source)
                self.assertEqual((found.scalar("key"), found.scalar("mode"), found.scalar("scale")),
                                 (key, "ionian", "major"))
                self.assertEqual(found.scalar("scale_notes"), notes)
                self.assertGreater(found.scalar("key_strength"), 0.7)
                self.assertGreater(found.scalar("key_margin"), 0)
                self.assertEqual(found.scalar("collection"), f"{key} major scale")
                self.assertTrue(found.scalar("tonic_named"))
                windows = found.items("keys")
                self.assertGreaterEqual(len(windows), 3)
                # A window names the tonic, or only the notes when it cannot tell.
                self.assertIn(f"{key} ionian", {span["label"] for span in windows})
                self.assertLessEqual({span["label"] for span in windows}, {f"{key} ionian", f"{key} major scale"})
                regions = found.items("key_regions")
                self.assertEqual([(r["start"], r["label"]) for r in regions], [(0.0, f"{key} ionian")])
                self.assertEqual(regions[0]["end"], windows[-1]["end"])
                self.assertFalse(found.scalar("detuned"))

    def test_a_dorian_vamp_reads_dorian_not_the_major_key_of_its_notes(self) -> None:
        """Fm7 three bars in four and Bb7 the fourth: the notes are Eb
        major's, the tonic F, and the D of Bb7 makes it dorian, not aeolian."""
        directory = scratch(self)
        vamp = (F_DORIAN[0], F_DORIAN[0], F_DORIAN[1], F_DORIAN[0])
        bass = (F_BASS[0], F_BASS[0], F_BASS[1], F_BASS[0])
        found = measured(self, directory, write(directory, "vamp", progression(vamp, lambda t: 0, repeats=8,
                                                                               bass=bass)))
        self.assertEqual((found.scalar("key"), found.scalar("mode"), found.scalar("scale")), ("F", "dorian", "minor"))
        self.assertEqual(found.scalar("scale_notes"), "F G Ab Bb C D Eb")
        self.assertEqual((found.scalar("collection"), found.scalar("tonic_named")), ("Eb major scale", True))
        self.assertEqual({span["label"] for span in found.items("keys")}, {"F dorian"})
        self.assertEqual([span["label"] for span in found.items("key_regions")], ["F dorian"])

    def test_a_recording_near_half_a_semitone_keeps_its_profiles_and_key(self) -> None:
        """Near half a semitone from 440 Hz a profile referenced to 440 Hz
        blurs each pitch class into its neighbour: version 2 gave no key
        beyond 40 cents. The profiles here follow the tuning, so they hold
        the notes as an in-tune recording's do, the key stays, and the
        detuning is reported."""
        directory = scratch(self)
        scale = [3, 5, 7, 8, 10, 0, 2]  # C major's notes; bin 0 is A

        def shares(cents: float) -> tuple[bank.Entry, float, float]:
            source = write(directory, f"detuned{cents}", progression(C_MAJOR, lambda t: cents, repeats=8,
                                                                     bass=C_BASS))
            found = measured(self, directory, source)
            chroma = found.series("chroma")[found.series("pitched")[:, 0] > 0].sum(axis=0)
            hpcp = bank.Bank(directory / "bank").entry(bank.file_digest(source), "frames").series("hpcp").sum(axis=0)
            return found, float(chroma[scale].sum() / chroma.sum()), float(hpcp[scale].sum() / hpcp.sum())
        _, in_tune, hpcp_in_tune = shares(0)
        self.assertGreater(in_tune, 0.97)
        for cents, is_detuned in ((36, False), (46, True), (-46, True)):
            with self.subTest(cents=cents):
                found, share, hpcp_share = shares(cents)
                self.assertLess(abs(found.scalar("tuning_cents") - cents), 3)  # anchor: the tuning is found
                self.assertIs(found.scalar("detuned"), is_detuned)
                self.assertEqual(bool(found.items("detuned_spans")), is_detuned)
                self.assertEqual((found.scalar("key"), found.scalar("mode")), ("C", "ionian"))
                self.assertLess(abs(share - in_tune), 0.01)
                if cents == 46:  # anchor: there, the profile on 440 Hz loses its notes
                    self.assertLess(hpcp_share, hpcp_in_tune - 0.05)

    def test_regions_follow_the_notes_and_name_a_tonic_only_when_it_leads(self) -> None:
        """C major's notes throughout, the weight moving between C, A, and F
        window by window: the windows disagree on the tonic, the notes do not,
        so there is one region, labelled by its notes. Then G major's notes
        for long enough: a second region."""
        rows = 1620  # nine key windows
        chroma = numpy.zeros((rows, 12))
        chroma[:, [3, 5, 7, 8, 10, 0, 2]] = 1.0
        leaning = {3: (3, 10, 7), 0: (0, 7, 3), 8: (8, 3, 0)}  # C, A, F: tonic, fifth, third
        for index, note in enumerate((3, 0, 8, 3, 0)):  # a window's length each
            tonic, fifth, third = leaning[note]
            chroma[index * 324:(index + 1) * 324, [tonic, fifth, third]] = [3.0, 2.5, 2.2]
        bass = numpy.zeros((rows, 12))
        pitched = numpy.ones(rows, dtype=bool)
        found = tonal.keys(chroma, bass, pitched, rows * tonal.HOP)
        labels = {span["label"] for span in found["keys"]}
        self.assertGreaterEqual(len(labels), 2, labels)  # anchor: the windows do disagree
        self.assertEqual([region["label"] for region in found["key_regions"]], ["C major scale"])
        self.assertEqual(found["collection"], "C major scale")
        chroma[810:, 8] = 0.0  # F out ...
        chroma[810:, 9] = 1.0  # ... F# in: G major's notes for the second half
        found = tonal.keys(chroma, bass, pitched, rows * tonal.HOP)
        regions = found["key_regions"]
        self.assertEqual(len(regions), 2, regions)
        self.assertEqual(tonal.key_of(tonal.key_profile(chroma[1000:], bass[1000:]))["collection"], "G major scale")
        self.assertEqual([region["label"] for region in regions], ["C major scale", "G major scale"])
        self.assertLess(regions[0]["value"], float(tonal.KEY["min_tonic_margin"]))  # the value is the tonic margin
        self.assertEqual((regions[0]["start"], regions[-1]["end"]), (0.0, found["keys"][-1]["end"]))

    def test_pitched_frames_with_empty_profiles_have_no_key(self) -> None:
        chroma = numpy.zeros((700, 12))
        bass = numpy.zeros((700, 12))
        pitched = numpy.ones(700, dtype=bool)
        self.assertEqual(tonal.keys(chroma, bass, pitched, 700 * tonal.HOP),
                         {**tonal.NO_KEY, "keys": [], "key_regions": []})
        chroma[:, [3, 5, 7, 8, 10, 0, 2]] = 1.0  # anchor: C major's notes have a key in every window
        chroma[:, 3] = 2.0
        found = tonal.keys(chroma, bass, pitched, 700 * tonal.HOP)
        self.assertEqual((found["key"], len(found["keys"])), ("C", len(tonal.windows(700, 324, 162))))
        chroma[400:] = 0.0  # a window whose pitched frames are all empty is left out
        found = tonal.keys(chroma, bass, pitched, 700 * tonal.HOP)
        self.assertEqual(len(found["keys"]), len([w for w in tonal.windows(700, 324, 162) if w[0] < 400]))


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
        measured_quiet = tonal.measure(request)
        self.assertFalse(measured_quiet["dissonance"].any())
        self.assertFalse(measured_quiet["chroma"].any() or measured_quiet["bass_chroma"].any())


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
                for name in ("dissonance", "pitched"):
                    self.assertEqual(found.series(name).shape, (rows, 1))
                for name in ("chroma", "bass_chroma"):
                    self.assertEqual(found.series(name).shape, (rows, 12))
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
        self.assertEqual(len(tonal.measure(self.request, decode=lambda: [audio])["dissonance"]),
                         self.request["needs"]["frames/rms_db"]["shape"][0])  # anchor: the same audio fits
        self.refused("made 6 frames", decode=lambda: [audio[:5 * tonal.HOP]])
        self.refused("more frames than", decode=lambda: [audio, audio])

    def test_a_missing_or_misshapen_need_is_refused(self) -> None:
        rms = self.request["needs"]["frames/rms_db"]
        del self.request["needs"]["frames/rms_db"]
        self.refused("does not carry frames/rms_db")
        self.request["needs"]["frames/rms_db"] = {**rms, "grid": {**rms["grid"], "hop": 1024}}
        self.refused("not on the frames grid")
        self.request["needs"]["frames/rms_db"] = {**rms, "dtype": "f64"}
        self.refused("not an f32 series")
        self.request["needs"]["frames/rms_db"] = {**rms, "shape": [rms["shape"][0] + 1, 1]}
        self.refused("holds")

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

    def test_a_profile_that_is_not_finite_is_refused(self) -> None:
        real = tonal.profile
        self.assertTrue(numpy.isfinite(tonal.measure(self.request)["chroma"]).all())  # anchor
        with patch.object(tonal, "profile", lambda *a: real(*a) * numpy.nan):
            self.refused("not finite")

    def test_a_second_decode_that_differs_is_refused(self) -> None:
        audio = numpy.concatenate(list(tonal.frames.decoded_blocks(str(self.source))))

        calls = []

        def shrinking() -> list[numpy.ndarray]:
            """The audio, then the audio less a second."""
            calls.append(1)
            return [audio if len(calls) == 1 else audio[:-tonal.RATE]]
        self.refused("second decode made", decode=shrinking)
        self.assertEqual(len(calls), 2)
        # Anchor: the same audio twice is measured.
        self.assertEqual(len(tonal.measure(self.request, decode=lambda: [audio])["chroma"]),
                         self.request["needs"]["frames/rms_db"]["shape"][0])

    def test_windows_left_open_are_refused(self) -> None:
        tuning = tonal.Tuning(300)
        for row in range(250):
            tuning.add(row, False, None)
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
