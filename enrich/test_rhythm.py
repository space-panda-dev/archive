"""The rhythm extractor, run by the real runner on synthetic audio (ADR 0074).

The audio is written by the writers the other tests use (Python's `wave`
through `support.write_wav`, and ffmpeg's own generators) and decoded by the
extractor's ffmpeg, as a recording would be.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import numpy

import bank
import extract
import support
from support import needs_ffmpeg
from test_cover import needs_essentia

sys.path.insert(0, str(Path(__file__).resolve().parent / "extractors" / "rhythm"))
import meter  # noqa: E402
import rhythm  # noqa: E402
from fractions import Fraction  # noqa: E402

RATE = 44100
# Two chunks of 60 s overlapping by 10 s, so 75 s of audio crosses a seam at 55 s.
SHORT_CHUNKS = {"chunk_seconds": 60, "overlap_seconds": 10}


def scratch(test: unittest.TestCase) -> Path:
    directory = Path(tempfile.mkdtemp(prefix="bank-rhythm-"))
    test.addCleanup(shutil.rmtree, directory, True)
    return directory


def hits(seconds: float, times, bed: float = 0.0, seed: int = 0, amplitude: float = 0.6) -> numpy.ndarray:
    """Percussive hits (a 1.5 kHz tone decaying in about 50 ms) at `times`,
    over an optional chord bed and a faint noise floor."""
    count = int(round(seconds * RATE))
    t = numpy.arange(count) / RATE
    samples = numpy.random.default_rng(seed).normal(0, 0.002, count)
    if bed:
        samples += bed * sum(numpy.sin(2 * numpy.pi * f * t) for f in (220.0, 277.18, 329.63)) / 3
    length = int(0.08 * RATE)
    shape = amplitude * numpy.exp(-60 * numpy.arange(length) / RATE) * numpy.sin(
        2 * numpy.pi * 1500 * numpy.arange(length) / RATE)
    for time in times:
        at = int(round(time * RATE))
        end = min(count, at + length)
        if 0 <= at < count:
            samples[at:end] += shape[:end - at]
    return samples


def burst(seconds: float, decay: float, amplitude: float, seed: int, tone: float = 0.0) -> numpy.ndarray:
    """A decaying noise burst, with a decaying sine of `tone` Hz under it if
    given: a kick is a 55 Hz tone with a click, a snare a louder burst, a hat
    a short faint one."""
    t = numpy.arange(int(seconds * RATE)) / RATE
    sound = amplitude * numpy.exp(-decay * t) * numpy.random.default_rng(seed).normal(0, 1, len(t))
    if tone:
        sound += 0.7 * numpy.exp(-18 * t) * numpy.sin(2 * numpy.pi * tone * t)
    return sound


CHORDS = ((220.0, 277.18, 329.63), (246.94, 293.66, 369.99), (196.0, 246.94, 293.66), (174.61, 220.0, 261.63))


def band(sections: list[tuple[float, float, int, int]], seed: int = 0,
         marked: bool = True) -> tuple[numpy.ndarray, numpy.ndarray]:
    """A drum kit and a chord a bar, section by section: (seconds, beat
    length in seconds, beats per bar, hats per beat). A kick on each bar's
    first beat, a snare on its even beats (2 and 4 in 4/4), a hat on every
    division of every beat. Unless `marked`, nothing marks the bar line: a
    kick on every beat and one chord throughout, as in four-on-the-floor
    over a held harmony. Returns the samples and the bar lines."""
    total = sum(section[0] for section in sections) + 1
    samples = numpy.random.default_rng(seed).normal(0, 0.002, int(total * RATE))
    kick = burst(0.25, 200, 0.3, seed + 3, tone=55.0)
    snare, hat = burst(0.12, 35, 0.3, seed + 1), burst(0.03, 300, 0.03, seed + 2)

    def place(sound: numpy.ndarray, at: float) -> None:
        first = int(round(at * RATE))
        end = min(len(samples), first + len(sound))
        samples[first:end] += sound[:end - first]

    def chord(at: float, seconds: float, notes: tuple[float, ...]) -> None:
        length = int(seconds * RATE)
        t = numpy.arange(length) / RATE
        fade = numpy.minimum(1, (length - numpy.arange(length)) / 2000)
        first = int(round(at * RATE))
        samples[first:first + length] += 0.06 * fade * sum(
            numpy.sin(2 * numpy.pi * f * t) for f in notes)[:len(samples) - first]

    bars, start = [], 0.5
    for seconds, beat, per_bar, division in sections:
        at = start
        while at + beat * per_bar <= start + seconds:
            bars.append(at)
            for b in range(per_bar):
                if b == 0 or not marked:
                    place(kick, at + b * beat)
                if b % 2 == 1:
                    place(snare, at + b * beat)
                for d in range(division):
                    place(hat, at + (b + d / division) * beat)
            if marked:
                chord(at, beat * per_bar, CHORDS[len(bars) % len(CHORDS)])
            at += beat * per_bar
        if not marked:
            chord(start, at - start, CHORDS[0])
        start = at
    return samples, numpy.asarray(bars)


def spikes(seconds: float, times, heights=1.0) -> numpy.ndarray:
    """A flux curve on the spectral rows with a spike of `heights` at each of
    `times` and nothing else: the accents of an idealised performance."""
    flux = numpy.zeros(int(seconds * meter.ROWS_PER_SECOND), numpy.float32)
    rows = numpy.round(numpy.asarray(times) * meter.ROWS_PER_SECOND).astype(int)
    flux[rows[rows < len(flux)]] = numpy.broadcast_to(heights, rows.shape)[rows < len(flux)]
    return flux


def measured(samples: numpy.ndarray, config: dict | None = None) -> tuple[rhythm.Measured, list[float]]:
    """What the extractor's own steps find in samples, in process: the
    measurement and the gated beats."""
    config = config or rhythm.parameters()
    found = rhythm.measure(rhythm.chunks([samples.astype(numpy.float32)], config["chunk_seconds"] * RATE,
                                         config["overlap_seconds"] * RATE), config, rhythm.essentia_standard())
    return found, rhythm.gate(found.beats, found.onsets, config["beat_gate_seconds"], config["beat_align_ms"] / 1000)


def on_grid(bpm: float, seconds: float, start: float) -> numpy.ndarray:
    """Exactly placed hits on every beat and every eighth between, over a bed."""
    beat = 60 / bpm
    beats = numpy.arange(start, seconds - 1, beat)
    return numpy.sort(numpy.concatenate((beats, beats[:-1] + beat / 2)))


def run(directory: Path, audio: Path, parameters: dict | None = None) -> tuple[int, dict]:
    argv = ["run", "rhythm", "--content", str(audio), "--bank", str(directory / "bank")]
    if parameters is not None:
        config = directory / "config.json"
        config.write_text(json.dumps({"parameters": {"rhythm": parameters}}))
        argv += ["--config", str(config)]
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = extract.main(argv)
    return code, json.loads(out.getvalue() or err.getvalue())


def entry_of(directory: Path, audio: Path, parameters: dict | None = None) -> bank.Entry:
    overrides = None if parameters is None else {"rhythm": parameters}
    found = bank.Bank(directory / "bank", parameters=overrides).entry(bank.file_digest(audio), "rhythm")
    assert found is not None
    return found


def times(entry: bank.Entry, name: str) -> numpy.ndarray:
    return numpy.asarray([event["time"] for event in entry.items(name)])


class Manifest(unittest.TestCase):
    def test_the_manifest_declares_exactly_what_the_program_implements(self) -> None:
        extractor = bank.Catalog.load()["rhythm"]
        self.assertEqual(extractor.manifest["parameters"], rhythm.parameters())
        self.assertEqual(extractor.manifest["needs"], [])
        self.assertEqual(rhythm.settings(rhythm.parameters()), rhythm.parameters())

    def test_settings_outside_what_it_implements_are_refused(self) -> None:
        for change, message in (({"method": "degara"}, "implements method"),
                                ({"essentia": "2.1b5"}, "implements essentia"),
                                ({"subdivisions": 0}, "subdivisions must be"),
                                ({"subdivisions": True}, "subdivisions must be"),
                                ({"max_tempo": "208"}, "max_tempo must be"),
                                ({"min_tempo": 150, "max_tempo": 120}, "below max_tempo"),
                                ({"chunk_seconds": 60, "overlap_seconds": 30}, "twice its overlap")):
            with self.subTest(change=change):
                with self.assertRaisesRegex(rhythm.RhythmError, message):
                    rhythm.settings({**rhythm.parameters(), **change})
        with self.assertRaisesRegex(rhythm.RhythmError, "not rhythm's"):
            rhythm.settings({**rhythm.parameters(), "extra": 1})

    def test_another_essentia_build_is_refused(self) -> None:
        with patch.object(rhythm.importlib.metadata, "version", return_value="2.1b6.dev1"):
            with self.assertRaisesRegex(rhythm.RhythmError, "2.1b6.dev1 is installed"):
                rhythm.essentia_standard()


class Chunking(unittest.TestCase):
    def test_kept_ranges_tile_the_audio_and_chunks_hold_its_samples(self) -> None:
        length, overlap = 1000, 200
        step = length - overlap
        audio = numpy.arange(5000, dtype=numpy.float32)
        for n in (1, 999, 1000, 1001, 1200, 1201, 1800, 1801, 4321, 5000):
            for block in (7, 333, 1000, 6000):
                with self.subTest(n=n, block=block):
                    source = audio[:n]
                    pieces = list(rhythm.chunks((source[i:i + block] for i in range(0, n, block)), length, overlap))
                    expected = 1 if n <= length else -(-(n - length) // step) + 1
                    self.assertEqual(len(pieces), expected)
                    covered = 0
                    for index, piece in enumerate(pieces):
                        self.assertEqual(piece.index, index)
                        self.assertEqual(piece.start, index * step)
                        self.assertLessEqual(len(piece.audio), length)
                        self.assertTrue(numpy.array_equal(piece.audio, source[piece.start:piece.start + length]))
                        self.assertEqual(piece.keep_from, covered)
                        # Nothing is kept from a chunk's first or last half-overlap but at the recording's ends.
                        if index:
                            self.assertGreaterEqual(piece.keep_from - piece.start, overlap // 2)
                        if index < len(pieces) - 1:
                            self.assertGreaterEqual(piece.start + length - piece.keep_to, overlap // 2)
                            self.assertEqual(len(piece.audio), length)
                        covered = piece.keep_to
                    self.assertEqual(covered, n)
        self.assertEqual(list(rhythm.chunks(iter([]), length, overlap)), [])

    def test_a_seam_drops_a_beat_too_soon_after_the_last(self) -> None:
        self.assertEqual(rhythm.join_beats([1.0, 1.5], [1.6, 2.0, 2.5], 0.3), [1.0, 1.5, 2.0, 2.5])
        self.assertEqual(rhythm.join_beats([1.0, 1.5], [1.9, 2.4], 0.3), [1.0, 1.5, 1.9, 2.4])
        self.assertEqual(rhythm.join_beats([], [0.1, 0.2], 0.3), [0.1, 0.2])

    def test_a_beat_needs_an_onset_on_it_or_onsets_on_both_sides(self) -> None:
        onsets = [1.0, 2.0, 3.0, 4.5, 20.0]
        beats = [0.0, 0.99, 1.5, 2.6, 3.8, 5.0, 6.0, 12.0, 19.0, 20.02, 21.0]
        # 1.5, 2.6, 3.8: onsets within 2 s each side; 0.99, 20.02: an onset on them.
        # 0.0 is before the first onset, 5.0 and 6.0 after the playing stops, 12.0 and 19.0 in a gap, 21.0 after the last.
        self.assertEqual(rhythm.gate(beats, onsets, 2, 0.05), [0.99, 1.5, 2.6, 3.8, 20.02])
        self.assertEqual(rhythm.gate([1.0, 2.0], [], 2, 0.05), [])
        self.assertEqual(rhythm.gate([], [1.0], 2, 0.05), [])

    def test_ticks_are_aligned_by_their_mean_distance_to_the_nearest_onset(self) -> None:
        onsets = numpy.arange(1.0, 20.0, 0.25)
        ticks = numpy.arange(1.0, 20.0, 0.5) + 0.015
        self.assertAlmostEqual(rhythm.alignment(ticks, onsets, 0.05), -0.015)
        self.assertAlmostEqual(rhythm.alignment(onsets[::2] - 0.03, onsets, 0.05), 0.03)
        # A tick with no onset within reach does not count; too few that do give no alignment.
        self.assertAlmostEqual(rhythm.alignment(numpy.r_[ticks, 30.0], onsets, 0.05), -0.015)
        self.assertIsNone(rhythm.alignment(ticks[:rhythm.ALIGN_MINIMUM - 1], onsets, 0.05))
        self.assertIsNone(rhythm.alignment(ticks + 0.2, onsets[::4], 0.05))
        self.assertIsNone(rhythm.alignment(ticks, [], 0.05))
        self.assertIsNone(rhythm.alignment([], onsets, 0.05))
        self.assertEqual(list(rhythm.nearest(numpy.asarray([1.0]), numpy.asarray([0.0, 3.0]))), [1.0, -2.0])

    def test_a_chunk_that_keeps_no_onset_has_no_confidence_and_borrows_the_last_shift(self) -> None:
        # Two 60 s chunks overlapping by 10 s, so the second keeps from 55 s.
        config = {**rhythm.parameters(), **SHORT_CHUNKS}
        played = numpy.arange(1.0, 54.0, 0.5)

        class Tracker:
            """RhythmExtractor2013's shape: ticks 15 ms after each chunk's onsets."""
            def __init__(self, **_: object) -> None:
                self.start = 0.0

            def reset(self) -> None:
                pass

            def __call__(self, audio: numpy.ndarray):
                ticks = numpy.asarray([t - self.start + 0.015 for t in played if t >= self.start])
                self.start += 50.0
                return 120.0, ticks, 4.0, None, None

        class Onsets(Tracker):
            def __call__(self, audio: numpy.ndarray):
                onsets = numpy.asarray([0.01] + [t - self.start for t in played if t >= self.start])
                self.start += 50.0
                return onsets, None

        es = type("Essentia", (), {"RhythmExtractor2013": Tracker, "OnsetRate": Onsets})
        found = rhythm.measure(rhythm.chunks([numpy.zeros(75 * RATE, dtype=numpy.float32)], 60 * RATE, 10 * RATE),
                               config, es)
        # The first chunk's onset at its first hop is the start of the audio, not playing.
        self.assertEqual(found.onsets[0], 1.0)
        # Anchor: the second chunk's tracker saw onsets (50 to 54 s), but it keeps none.
        self.assertEqual(found.chunk_onsets, [len(played), 0])
        self.assertEqual(found.chunk_confidence, [4.0, 0.0])
        # The second chunk has too few ticks to align by and takes the first chunk's shift.
        self.assertEqual([round(s, 9) for s in found.chunk_shift], [-0.015, -0.015])
        self.assertTrue(numpy.allclose(found.beats, played))

    def test_deviation_is_to_the_nearest_subdivision_and_signed(self) -> None:
        pairs = rhythm.intervals([10.0, 11.0, 12.0, 20.0, 21.0], 1.5)
        self.assertEqual(pairs, [(10.0, 11.0), (11.0, 12.0), (20.0, 21.0)])  # 12 to 20 is a gap, not a beat
        found = rhythm.deviations([9.0, 10.26, 11.74, 15.0, 20.5, 30.0], pairs, 4)
        # Onsets before the first beat, in the gap, and after the last are not measured.
        self.assertEqual([round(t, 6) for t, _, _ in found], [10.26, 11.74, 20.5])
        signed = [round(s, 6) for _, s, _ in found]
        self.assertEqual(signed, [0.01, -0.01, 0.0])
        self.assertEqual([round(f, 6) for _, _, f in found], [0.04, 0.04, 0.0])

    def test_window_sums_are_those_of_each_window(self) -> None:
        rng = numpy.random.default_rng(5)
        config = rhythm.parameters()
        beats = list(numpy.cumsum(rng.uniform(0.3, 0.7, 400)))
        onsets = list(numpy.sort(rng.uniform(0, beats[-1] + 5, 900)))
        found = rhythm.Measured(beats=beats, onsets=onsets, samples=int((beats[-1] + 7) * RATE))
        got = rhythm.series(found, beats, config)
        # The windows by brute force, as the row-by-row masks the cumulative sums replace.
        rows, low, high = rhythm.windows(found.samples, config["window_seconds"])
        pairs = rhythm.intervals(beats, 60.0 / config["min_tempo"])
        middles = numpy.asarray([(a + b) / 2 for a, b in pairs])
        lengths = numpy.asarray([b - a for a, b in pairs])
        each = rhythm.deviations(onsets, pairs, config["subdivisions"])
        times = numpy.asarray([m[0] for m in each])
        signed = numpy.asarray([m[1] for m in each]) * 1000
        fraction = numpy.asarray([m[2] for m in each])
        self.assertGreater(len(each), 500)  # anchor: most onsets are measured
        for k in range(rows):
            mask = (middles >= low[k]) & (middles < high[k])
            chosen = (times >= low[k]) & (times < high[k])
            expected_tempo = 60 / numpy.median(lengths[mask]) if mask.any() else 0
            self.assertAlmostEqual(float(got["tempo"][k, 0]), expected_tempo, places=3)
            self.assertEqual(got["onset_count"][k, 0], chosen.sum())
            if chosen.any():
                self.assertAlmostEqual(float(got["onset_deviation"][k, 0]), numpy.abs(signed[chosen]).mean(), places=3)
                self.assertAlmostEqual(float(got["onset_deviation"][k, 1]), signed[chosen].mean(), places=3)
                self.assertAlmostEqual(float(got["offgrid"][k, 0]), fraction[chosen].mean(), places=5)
            else:
                self.assertEqual(float(got["offgrid"][k, 0]), 0.0)

    def test_series_is_linear_in_the_recording(self) -> None:
        # Nine hours at 4.2 onsets a second: the row-by-row masks took about 35 s here.
        seconds = 9 * 3600
        rng = numpy.random.default_rng(0)
        beats = list(numpy.arange(0.3, seconds, 0.476))
        onsets = list(numpy.sort(rng.uniform(0, seconds, int(4.2 * seconds))))
        found = rhythm.Measured(beats=beats, onsets=onsets, samples=seconds * RATE)
        real_mean = numpy.mean
        with patch.object(rhythm.numpy, "mean", side_effect=AssertionError("a mean per row")):
            got = rhythm.series(found, beats, rhythm.parameters())
        self.assertIs(rhythm.numpy.mean, real_mean)
        self.assertEqual(got["offgrid"].shape, (seconds, 1))
        self.assertTrue(numpy.all(got["onset_count"][10:-10] > 10))  # anchor: every window is measured


class Levels(unittest.TestCase):
    """The primary level: which relation of the tracker's beats the music
    supports, settled between octaves by the preference."""

    config = rhythm.parameters()

    def test_a_relation_places_its_pulses_across_groups_of_beats(self) -> None:
        beats = numpy.arange(7.0)
        self.assertTrue(numpy.allclose(meter.pulses(beats, Fraction(3, 2), 0, 1.5),
                                       [0, 2 / 3, 4 / 3, 2, 8 / 3, 10 / 3, 4, 14 / 3, 16 / 3]))
        self.assertEqual(list(meter.pulses(beats, Fraction(1, 2), 1, 1.5)), [1.0, 3.0])
        # Uneven beats: a pulse between two beats is in proportion between them.
        self.assertTrue(numpy.allclose(meter.pulses(numpy.asarray([0.0, 1.0, 3.0]), Fraction(2), 0, 2.5),
                                       [0, 0.5, 1, 2]))
        # A gap longer than `longest` is not a beat: no pulse is laid across it.
        self.assertEqual(list(meter.pulses(numpy.asarray([0.0, 1, 2, 10, 11, 12]), Fraction(1), 0, 1.5)),
                         [0.0, 1.0, 10.0, 11.0])
        self.assertEqual(len(meter.pulses(numpy.asarray([0.0, 1.0]), Fraction(1, 2), 0, 1.5)), 0)

    def test_support_is_the_accent_on_a_level_s_pulses_at_its_best_phase(self) -> None:
        beats = numpy.arange(0.5, 30, 0.5)
        accent = spikes(31, beats, 1.0) + spikes(31, beats + 0.25, 0.2)
        peak = meter.peaks(meter.accents(accent))
        reference = meter.reference_level(peak, 15, 10)
        on_beat, _ = meter.support(beats, peak, Fraction(1), 2.0, reference)
        double, _ = meter.support(beats, peak, Fraction(2), 2.0, reference)
        half, phase = meter.support(beats, peak, Fraction(1, 2), 2.0, reference)
        self.assertGreater(on_beat, 0.9)
        self.assertAlmostEqual(double, (on_beat + 0.2 * on_beat) / 2, delta=0.05)
        self.assertGreater(half, 0.9)
        self.assertIn(phase, (0, 1))
        # Nothing to measure against, or too few pulses: no support.
        self.assertEqual(meter.support(beats, peak, Fraction(1), 2.0, 0.0), (0.0, 0))
        self.assertEqual(meter.support(beats[:2], peak, Fraction(1, 2), 2.0, reference), (0.0, 0))

    def test_the_primary_level_is_the_one_played_not_the_tracker_s_cross_rhythm(self) -> None:
        # Played: a beat every 0.6 s (100 BPM). The tracker has locked to 4:3 of it, a beat every 0.45 s.
        seconds = 60
        played = numpy.arange(0.6, seconds - 1, 0.6)
        tracker = numpy.arange(0.6, seconds - 1, 0.45)
        tempo = numpy.full(seconds, 60 / 0.45)
        self.assertAlmostEqual(tempo[0] / 100, 4 / 3)  # anchor: the tracker's level is not the played one
        rows = meter.Rows(spikes(seconds, played), numpy.zeros(int(seconds * meter.ROWS_PER_SECOND), numpy.float32),
                          numpy.zeros((int(seconds * meter.ROWS_PER_SECOND), 12), numpy.float32))
        found = meter.analyse(tracker, tempo, rows, self.config)
        self.assertTrue(numpy.allclose(found.primary[5:-5], 100, atol=0.5), found.primary)
        self.assertTrue(numpy.allclose(numpy.diff(found.pulse), 0.6, atol=0.01))
        self.assertLess(numpy.max(numpy.abs(rhythm.nearest(played, found.pulse))), 0.01)
        # The tracker's own level is published as the 4:3 alternative, with the support it lacks.
        middle = found.alternatives[10:-10]
        self.assertGreater(middle[:, meter.RELATION_NAMES.index("1:1")].min(), 0.9)
        self.assertLess(middle[:, meter.RELATION_NAMES.index("4:3")].max(), 0.4)
        self.assertEqual(len(found.spans), 1)
        self.assertAlmostEqual(found.primary_bpm, 100, delta=0.5)

    def test_levels_the_music_supports_alike_are_settled_by_the_preference(self) -> None:
        # Every beat and every half beat accented alike: 60, 80, and 120 BPM are all fully supported.
        seconds = 40
        beats = numpy.arange(0.5, seconds - 1, 0.5)
        rows = meter.Rows(spikes(seconds, numpy.arange(0.5, seconds - 1, 0.25)),
                          numpy.zeros(int(seconds * meter.ROWS_PER_SECOND), numpy.float32),
                          numpy.zeros((int(seconds * meter.ROWS_PER_SECOND), 12), numpy.float32))
        tempo = numpy.full(seconds, 120.0)
        for prior, expected in ((120, 120.0), (60, 60.0)):
            with self.subTest(prior=prior):
                found = meter.analyse(beats, tempo, rows, {**self.config, "prior_bpm": prior})
                self.assertTrue(numpy.allclose(found.primary[5:-5], expected), found.primary)

    def test_the_level_changes_only_on_sustained_evidence(self) -> None:
        seconds = 30
        tempo = numpy.full(seconds, 100.0)
        table = numpy.full((seconds, len(meter.RELATIONS)), 0.01)
        one, half = meter.RELATION_NAMES.index("1:1"), meter.RELATION_NAMES.index("1:2")
        # Each second favours the other level of the two, by a little.
        table[:, one] = numpy.where(numpy.arange(seconds) % 2, 0.8, 0.6)
        table[:, half] = numpy.where(numpy.arange(seconds) % 2, 0.6, 0.8)
        config = {**self.config, "prior_bpm": 70}  # 50 and 100 BPM equally preferred, near enough
        _, steady = meter.primary_levels(table, tempo, config)
        _, flicker = meter.primary_levels(table, tempo, {**config, "level_switch_cost": 0})
        self.assertGreater(numpy.count_nonzero(numpy.diff(flicker)), 20)  # anchor: the evidence alternates
        self.assertEqual(numpy.count_nonzero(numpy.diff(steady)), 0)

    def test_a_take_s_level_does_not_depend_on_the_take_before(self) -> None:
        # Take A at 140 BPM, then take B at 72 whose 1:1 is best supported, its 3:2 (108) next, its 1:2 last.
        one, cross, half = (meter.RELATION_NAMES.index(name) for name in ("1:1", "3:2", "1:2"))

        def take(bpm: float, seconds: int, supports: dict[int, float]) -> tuple[numpy.ndarray, numpy.ndarray]:
            table = numpy.full((seconds, len(meter.RELATIONS)), 0.05)
            for column, value in supports.items():
                table[:, column] = value
            return numpy.full(seconds, bpm), table

        a_tempo, a_table = take(140.0, 60, {one: 0.9})
        b_tempo, b_table = take(72.0, 60, {one: 0.9, cross: 0.6, half: 0.5})
        alone, _ = meter.primary_levels(b_table, b_tempo, self.config)
        self.assertTrue(numpy.allclose(alone, 72))
        for gap in (meter.RESET_SECONDS, 30, 600):
            with self.subTest(gap=gap):
                tempo = numpy.concatenate((a_tempo, numpy.zeros(gap), b_tempo))
                table = numpy.vstack((a_table, numpy.zeros((gap, len(meter.RELATIONS))), b_table))
                primary, _ = meter.primary_levels(table, tempo, self.config)
                self.assertTrue(numpy.allclose(primary[:60], 140))
                self.assertTrue(numpy.allclose(primary[-60:], 72), primary[-60:])
        # Anchor: within a take (a break shorter than RESET_SECONDS) the cost of a change still holds the level.
        short = meter.RESET_SECONDS - 1
        tempo = numpy.concatenate((a_tempo, numpy.zeros(short), b_tempo))
        table = numpy.vstack((a_table, numpy.zeros((short, len(meter.RELATIONS))), b_table))
        held, _ = meter.primary_levels(table, tempo, self.config)
        self.assertTrue(numpy.allclose(held[-60:], 108), held[-60:])

    def test_the_tracker_s_octave_counts_where_the_support_hardly_differs(self) -> None:
        # A ballad at 66 BPM with eighths: the doubled level's pulses are half the beats and half the weaker
        # eighths, so its support is about 0.72 of the beat's (measured on the band below); halving it never
        # loses support.
        seconds = 30
        table = numpy.full((seconds, len(meter.RELATIONS)), 0.2)
        table[:, meter.RELATION_NAMES.index("1:1")] = 0.8
        table[:, meter.RELATION_NAMES.index("2:1")] = 0.72 * 0.8
        table[:, meter.RELATION_NAMES.index("1:2")] = 0.85
        tempo = numpy.full(seconds, 66.0)
        counted, _ = meter.primary_levels(table, tempo, self.config)
        doubled, _ = meter.primary_levels(table, tempo, {**self.config, "tracker_octaves": 10})
        # Anchor: the preference near prior_bpm alone doubles it.
        self.assertTrue(numpy.allclose(doubled, 132))
        self.assertTrue(numpy.allclose(counted, 66))

    def test_seconds_without_beats_have_no_primary_and_no_alternatives(self) -> None:
        seconds = 40
        beats = numpy.arange(20.5, 39, 0.5)
        tempo = numpy.where(numpy.arange(seconds) >= 22, 120.0, 0.0)
        rows = meter.Rows(spikes(seconds, beats), numpy.zeros(int(seconds * meter.ROWS_PER_SECOND), numpy.float32),
                          numpy.zeros((int(seconds * meter.ROWS_PER_SECOND), 12), numpy.float32))
        found = meter.analyse(beats, tempo, rows, self.config)
        self.assertTrue(numpy.all(found.primary[:22] == 0))
        self.assertTrue(numpy.all(found.alternatives[:22] == 0))
        self.assertTrue(numpy.allclose(found.primary[25:35], 120))  # anchor: the playing has a primary
        self.assertGreaterEqual(found.pulse.min(), 20.4)
        # No beats at all: nothing, rather than a guess.
        nothing = meter.analyse([], numpy.zeros(seconds), rows, self.config)
        self.assertEqual((len(nothing.pulse), nothing.spans, nothing.changes, nothing.primary_bpm, nothing.meter),
                         (0, [], [], 0.0, ""))
        self.assertTrue(numpy.all(nothing.primary == 0))


class Meter(unittest.TestCase):
    """Division, beats per bar, downbeats, spans, and changes."""

    def test_the_division_is_where_the_accents_between_beats_fall(self) -> None:
        pulse = numpy.arange(0.6, 30, 0.6)
        steps = {"thirds": (1 / 3, 2 / 3), "halves": (0.5,), "quarters": (0.25, 0.5, 0.75), "nothing": ()}
        expected = {"thirds": 3, "halves": 2, "quarters": 2, "nothing": 2}
        for name, places in steps.items():
            with self.subTest(between=name):
                times = numpy.concatenate([pulse] + [pulse[:-1] + p * 0.6 for p in places])
                found = meter.divisions(pulse, meter.accents(spikes(31, times)))
                self.assertEqual(len(found), len(pulse) - 1)
                self.assertTrue(numpy.all(found == expected[name]), found)
        # Swung sixteenths (the second at 0.29 of the beat) are not thirds.
        swung = numpy.concatenate([pulse, pulse[:-1] + 0.29 * 0.6, pulse[:-1] + 0.5 * 0.6, pulse[:-1] + 0.79 * 0.6])
        self.assertTrue(numpy.all(meter.divisions(pulse, meter.accents(spikes(31, swung))) == 2))
        self.assertEqual(len(meter.divisions(pulse[:1], numpy.zeros(10))), 0)

    def test_beats_per_bar_are_how_far_apart_the_beats_repeat(self) -> None:
        rng = numpy.random.default_rng(4)
        for period, expected in ((2, 4), (3, 3), (4, 4), (5, 5), (7, 7)):
            with self.subTest(period=period):
                pattern = rng.normal(0, 1, (period, 28))
                features = pattern[numpy.arange(200) % period] + rng.normal(0, 0.3, (200, 28))
                found = meter.meters(meter.meter_scores(meter.repetition(features)))
                self.assertGreater(numpy.mean(found == expected), 0.9, (period, numpy.unique(found, return_counts=True)))
        # Nothing repeats: no evidence for any meter, and a tie is 4.
        self.assertTrue(numpy.all(meter.meters(numpy.zeros((10, len(meter.METERS)))) == 4))

    def test_downbeats_follow_the_evidence_and_a_short_bar_moves_them(self) -> None:
        downbeats = [0, 4, 8, 12, 16, 18, 22, 26, 30, 34, 38]  # a bar of two beats at 16
        evidence = numpy.zeros(42)
        evidence[downbeats] = 3.0
        position = meter.bar_positions(evidence, numpy.full(42, 4))
        self.assertEqual(list(numpy.flatnonzero(position == 0)), downbeats)
        self.assertEqual(list(meter.bar_lengths(position)), [4, 4, 4, 4, 2, 4, 4, 4, 4, 4, 4])
        # Without the evidence of a short bar, the bars keep their length.
        steady = meter.bar_positions(numpy.zeros(12), numpy.full(12, 3))
        self.assertTrue(numpy.all(numpy.diff(numpy.flatnonzero(steady == 0)) == 3))
        self.assertEqual(len(meter.bar_positions(numpy.zeros(0), numpy.zeros(0, int))), 0)

    def test_a_bar_s_margin_is_how_much_better_its_downbeat_fits_than_another_phase(self) -> None:
        count = 40
        per_bar = numpy.full(count, 4)
        position = numpy.arange(count) % 4
        clear = numpy.zeros(count)
        clear[position == 0] = 3.0
        self.assertTrue(numpy.allclose(meter.bar_margins(clear, position, per_bar), 3.0))
        # The evidence on the third beat as on the first: half a bar off fits as well, so the margin is 0.
        halves = clear.copy()
        halves[position == 2] = 3.0
        self.assertTrue(numpy.allclose(meter.bar_margins(halves, position, per_bar), 0.0))
        # Evidence on another beat than the downbeat: below 0.
        self.assertTrue(numpy.all(meter.bar_margins(numpy.roll(clear, 1), position, per_bar) < 0))
        # Pulses before the first downbeat, and too few bars to compare: 0, not a margin.
        late = (numpy.arange(count) - 2) % 4
        self.assertTrue(numpy.all(meter.bar_margins(clear, late, per_bar)[:2] == 0))
        self.assertTrue(numpy.all(meter.bar_margins(clear[:6], position[:6], per_bar[:6]) == 0))
        self.assertEqual(len(meter.bar_margins(numpy.zeros(0), numpy.zeros(0, int), numpy.zeros(0, int))), 0)

    def test_a_value_each_second_is_the_last_event_s_while_it_is_near(self) -> None:
        times = numpy.asarray([0.5, 1.5, 6.0])
        found = rhythm.per_second(times, numpy.asarray([1.0, 2.0, 3.0]), 9, 1.5)
        self.assertEqual(list(found), [0.0, 1.0, 2.0, 2.0, 0.0, 0.0, 3.0, 3.0, 0.0])
        self.assertEqual(list(rhythm.per_second(numpy.zeros(0), numpy.zeros(0), 3, 1.5)), [0.0] * 3)

    def test_downbeat_evidence_is_where_the_low_accent_and_the_harmony_change(self) -> None:
        seconds = 30
        pulse = numpy.arange(0.5, 29, 0.5)
        bars = pulse[::4]
        count = int(seconds * meter.ROWS_PER_SECOND)
        chroma = numpy.zeros((count, 12), numpy.float32)
        for index, start in enumerate(bars):
            first = int(start * meter.ROWS_PER_SECOND)
            chroma[first:first + int(2 * meter.ROWS_PER_SECOND), (index * 5) % 12] = 1  # a chord a bar
        evidence = meter.downbeat_evidence(pulse, meter.accents(spikes(seconds, bars)), chroma)
        on_bars = numpy.isin(numpy.arange(len(pulse)), numpy.arange(0, len(pulse), 4))
        self.assertGreater(evidence[on_bars][1:-1].min(), evidence[~on_bars][2:-2].max())
        self.assertEqual(len(meter.downbeat_evidence(pulse[:1], numpy.zeros(count), chroma)), 1)

    def test_standardising_constant_values_gives_zeros(self) -> None:
        self.assertEqual(list(meter.robust_z(numpy.full(5, 2.0))), [0.0] * 5)
        # Mostly constant: the median deviation is 0, so the spread falls back to the standard deviation.
        self.assertTrue(numpy.all(numpy.isfinite(meter.robust_z(numpy.r_[numpy.zeros(9), 1.0]))))
        self.assertEqual(len(meter.robust_z(numpy.zeros(0))), 0)

    def test_a_change_on_a_persisting_grid_is_of_meter_and_otherwise_of_tempo(self) -> None:
        # 4/4 at 90 (sixteenths at 360), 12/8 at 120 (triplet eighths at 360), 4/4 at 140, a break, 4/4 at 140.
        parts = [(90, "4/4", 2, 30.0), (120, "12/8", 3, 30.0), (140, "4/4", 2, 30.0)]
        pulse, label, division = [0.0], [], []
        rng = numpy.random.default_rng(2)
        for bpm, name, divided, seconds in parts:
            count = int(seconds * bpm / 60)
            # Played, not quantised: each beat up to 6% long or short, so runs break and must be rejoined.
            pulse.extend(pulse[-1] + numpy.cumsum(60 / bpm * rng.uniform(0.94, 1.06, count)))
            label += [name] * count
            division += [divided] * count
        label[10:13] = ["3/4"] * 3  # a stray three beats: too short for a span of its own
        pulse.append(pulse[-1] + 3.0)  # the break
        label.append("4/4")
        division.append(2)
        tail = pulse[-1] + 60 / 140 * numpy.arange(1, 30)
        pulse.extend(tail)
        label += ["4/4"] * len(tail)
        division += [2] * len(tail)
        found = meter.spans(numpy.asarray(pulse), label, division)
        self.assertEqual([s.label for s in found], ["4/4", "12/8", "4/4", "4/4"])
        for span, bpm in zip(found, (90, 120, 140, 140)):
            self.assertAlmostEqual(span.bpm, bpm, delta=0.03 * bpm)
        self.assertAlmostEqual(found[2].end, found[3].start - 3.0, places=6)  # anchor: the break splits the spans
        self.assertEqual([c["label"] for c in meter.changes(found)], ["meter", "tempo"])
        self.assertAlmostEqual(meter.changes(found)[0]["value"], found[1].bpm / found[0].bpm, places=5)
        self.assertEqual(meter.spans(numpy.asarray([1.0]), [], []), [])

    def test_the_song_s_tempo_and_meter_are_those_covering_the_most_time(self) -> None:
        span = meter.Span
        self.assertEqual(meter.most_time([span(0, 10, 100, "4/4", 2), span(10, 40, 150, "12/8", 3),
                                          span(40, 50, 103, "4/4", 2)]), (150, "12/8"))
        # Levels within 8% count as one; its tempo is the time-weighted median of its spans'.
        self.assertEqual(meter.most_time([span(0, 25, 100, "4/4", 2), span(25, 40, 150, "12/8", 3),
                                          span(40, 60, 103, "4/4", 2)]), (100, "4/4"))
        self.assertEqual(meter.most_time([]), (0.0, ""))


class SpectralRows(unittest.TestCase):
    def test_rows_measured_chunk_by_chunk_are_the_rows_of_the_whole(self) -> None:
        samples = hits(75, numpy.arange(0.5, 75, 0.37), bed=0.2).astype(numpy.float32)
        length, overlap = 60 * RATE, 10 * RATE
        pieces = list(rhythm.chunks([samples], length, overlap))
        self.assertEqual(len(pieces), 2)  # anchor: a seam is crossed
        joined = meter.Rows.join([meter.spectral_rows(c.audio, c.start, -(-c.keep_from // meter.HOP),
                                                      -(-c.keep_to // meter.HOP)) for c in pieces])
        whole = meter.spectral_rows(samples, 0, 0, -(-len(samples) // meter.HOP))
        self.assertEqual(len(joined.flux), -(-len(samples) // meter.HOP))
        for name in ("flux", "low", "chroma"):
            self.assertTrue(numpy.allclose(getattr(joined, name), getattr(whole, name), rtol=1e-4, atol=1e-3), name)
        self.assertGreater(float(whole.flux.max()), 10)  # anchor: the hits are measured
        self.assertEqual(len(meter.Rows.join([]).flux), 0)

    def test_the_measure_keeps_one_row_a_hop(self) -> None:
        config = {**rhythm.parameters(), **SHORT_CHUNKS}
        found, _ = measured(hits(75, numpy.arange(0.5, 75, 0.5)), config)
        self.assertEqual(len(found.rows), 2)
        self.assertEqual(len(meter.Rows.join(found.rows).flux), -(-75 * RATE // meter.HOP))


class Provenance(unittest.TestCase):
    def test_an_ffmpeg_that_names_no_version_is_refused(self) -> None:
        for answer in (subprocess.CompletedProcess(["ffmpeg"], 1, stdout="", stderr="broken"),
                       subprocess.CompletedProcess(["ffmpeg"], 0, stdout="something else\n", stderr="")):
            with self.subTest(answer=answer):
                with patch.object(rhythm.subprocess, "run", return_value=answer):
                    with self.assertRaisesRegex(rhythm.RhythmError, "names no version"):
                        rhythm.ffmpeg_version()
        with patch.object(rhythm.subprocess, "run", return_value=subprocess.CompletedProcess(
                ["ffmpeg"], 0, stdout="ffmpeg version 7.1 Copyright\nmore\n", stderr="")):
            self.assertEqual(rhythm.ffmpeg_version(), "7.1")

    @needs_ffmpeg
    def test_the_installed_ffmpeg_names_its_version(self) -> None:
        self.assertRegex(rhythm.ffmpeg_version(), r"^[0-9n]")


@needs_ffmpeg
@needs_essentia
class Rhythm(unittest.TestCase):
    def test_a_steady_pulse_across_a_seam(self) -> None:
        directory = scratch(self)
        audio = support.write_wav(directory / "pulse.wav", hits(75, numpy.arange(0.5, 75, 0.5), bed=0.1), RATE)
        code, answer = run(directory, audio, {**rhythm.parameters(), **SHORT_CHUNKS})
        self.assertEqual(code, 0, answer)
        entry = entry_of(directory, audio, {**rhythm.parameters(), **SHORT_CHUNKS})
        self.assertEqual(entry.scalar("seconds"), 75.0)
        beats = times(entry, "beats")
        # Anchor: both chunks found beats, so the seam at 55 s is crossed.
        self.assertGreater((beats < 50).sum(), 80)
        self.assertGreater((beats > 60).sum(), 20)
        gaps = numpy.diff(beats)
        self.assertTrue(numpy.all(numpy.abs(gaps - 0.5) < 0.03), gaps[numpy.abs(gaps - 0.5) >= 0.03])
        self.assertAlmostEqual(entry.scalar("bpm"), 120, delta=1)
        self.assertAlmostEqual(entry.scalar("onset_rate"), 2.0, delta=0.1)
        self.assertGreater(entry.scalar("confidence"), 3.0)
        confidence = entry.series("chunk_confidence")
        self.assertEqual(confidence.shape, (2, 1))
        self.assertTrue(numpy.all(confidence > 3.0), confidence)  # the second chunk had onsets too
        self.assertTrue(numpy.allclose(entry.series("chunk_bpm"), 120, atol=1))
        tempo = entry.series("tempo")
        self.assertEqual(tempo.shape, (75, 1))
        self.assertTrue(numpy.allclose(tempo[5:70], 120, atol=1.5), tempo.ravel())
        self.assertEqual(entry.grid("tempo"), {"sample_rate": RATE, "hop": RATE, "frame": 10 * RATE,
                                               "first_centre": 0})
        self.assertEqual(entry.grid("chunk_bpm"), {"sample_rate": RATE, "hop": 50 * RATE, "frame": 60 * RATE,
                                                   "first_centre": 30 * RATE})
        self.assertTrue(numpy.all(entry.series("onset_count")[5:70] >= 18))
        for name in ("tempo", "onset_count", "onset_deviation", "offgrid", "chunk_bpm", "chunk_confidence",
                     "primary_tempo", "tempo_support", "downbeat_margin"):
            self.assertTrue(numpy.isfinite(entry.series(name)).all(), name)
        # One level across the seam: the pulse is the beats, every one supported.
        self.assertTrue(numpy.allclose(entry.series("primary_tempo")[5:70], 120, atol=1.5))
        self.assertAlmostEqual(entry.scalar("primary_bpm"), 120, delta=1)
        pulse = times(entry, "pulse")
        self.assertGreater(len(pulse), 140)
        self.assertLess(numpy.max(numpy.abs(rhythm.nearest(beats, pulse))), 0.001)
        self.assertEqual(entry.series("tempo_support").shape, (75, 7))
        support_of = entry.series("tempo_support")[5:70]
        self.assertTrue(numpy.all(support_of[:, meter.RELATION_NAMES.index("1:1")] > 0.8), support_of)
        # Every other beat is as well supported (an octave); the cross-rhythms are not.
        crosses = [meter.RELATION_NAMES.index(name) for name in ("2:3", "3:4", "4:3", "3:2")]
        self.assertTrue(numpy.all(support_of[:, crosses] < 0.6), support_of)
        # Identical clicks repeat at every lag alike: no meter but the default.
        self.assertEqual(entry.scalar("meter"), "4/4")
        self.assertEqual(len(entry.items("tempo_spans")), 1)
        with patch.object(extract.subprocess, "Popen", side_effect=AssertionError("ran again")):
            self.assertEqual(run(directory, audio, {**rhythm.parameters(), **SHORT_CHUNKS})[1]["extractors"]
                             ["rhythm"]["status"], "hit")

    def test_bars_of_four_three_and_five_are_counted_from_the_bar_lines(self) -> None:
        directory = scratch(self)
        for beat, per_bar in ((0.6, 4), (0.5, 3), (0.5, 5)):
            with self.subTest(per_bar=per_bar):
                samples, bars = band([(45, beat, per_bar, 2)])
                audio = support.write_wav(directory / f"bars-of-{per_bar}.wav", samples, RATE)
                code, answer = run(directory, audio)
                self.assertEqual(code, 0, answer)
                entry = entry_of(directory, audio)
                self.assertEqual(entry.scalar("meter"), f"{per_bar}/4")
                self.assertAlmostEqual(entry.scalar("primary_bpm"), 60 / beat, delta=2)
                spans = entry.items("tempo_spans")
                self.assertEqual([s["label"] for s in spans], [f"{per_bar}/4"])
                self.assertEqual(entry.items("tempo_changes"), [])
                downbeats = entry.items("downbeats")
                down = numpy.asarray([d["time"] for d in downbeats])
                self.assertGreater(len(down), 0.8 * len(bars))  # anchor: bars were found
                # Downbeats are on the bar lines (the onsets' time base: a few ms early), each a bar long.
                self.assertTrue(numpy.all(numpy.abs(rhythm.nearest(bars, down)) < 0.05), down)
                self.assertTrue(all(d["value"] == per_bar and d["label"] == f"{per_bar}/4" for d in downbeats[:-1]))
                pulse = entry.items("pulse")
                # The pulse counts through each bar from its downbeat.
                counts = [p["value"] for p in pulse]
                first = counts.index(1)
                self.assertEqual(counts[first:first + 2 * per_bar], list(range(1, per_bar + 1)) * 2)
                self.assertTrue(set(down) <= {p["time"] for p in pulse})
                # A kick and a chord change on every bar line: the bars are clearly placed.
                margin = entry.series("downbeat_margin")[10:35, 0]
                self.assertGreater(numpy.median(margin), rhythm.parameters()["downbeat_margin"])

    def test_twelve_eight_into_four_four_on_one_grid_is_a_change_of_meter(self) -> None:
        # Triplet eighths at a dotted quarter of 80 BPM, then sixteenths at a quarter of 60: 240 a minute throughout.
        directory = scratch(self)
        samples, _ = band([(40, 0.75, 4, 3), (40, 1.0, 4, 4)])
        audio = support.write_wav(directory / "twelve-eight-into-four.wav", samples, RATE)
        code, answer = run(directory, audio)
        self.assertEqual(code, 0, answer)
        entry = entry_of(directory, audio)
        tempo = entry.series("tempo")[:, 0]
        # Anchor: the tracker's own tempo moves by 3:2, which one tempo would report as a change of tempo.
        self.assertAlmostEqual(float(numpy.median(tempo[50:70]) / numpy.median(tempo[10:30])), 1.5, delta=0.05)
        spans = entry.items("tempo_spans")
        self.assertEqual([s["label"] for s in spans], ["12/8", "4/4"])
        self.assertAlmostEqual(spans[0]["value"], 80, delta=2)
        self.assertAlmostEqual(spans[1]["start"], 40.5, delta=1.5)
        changes = entry.items("tempo_changes")
        self.assertEqual([c["label"] for c in changes], ["meter"])
        self.assertAlmostEqual(changes[0]["time"], spans[1]["start"])

    def test_bar_lines_nothing_marks_are_not_published(self) -> None:
        # Four on the floor over one held chord: every beat alike, so no phase of the bar is better than another.
        directory = scratch(self)
        samples, bars = band([(45, 0.6, 4, 2)], marked=False)
        audio = support.write_wav(directory / "four-on-the-floor.wav", samples, RATE)
        code, answer = run(directory, audio)
        self.assertEqual(code, 0, answer)
        entry = entry_of(directory, audio)
        pulse = entry.items("pulse")
        self.assertGreater(len(pulse), 0.8 * 4 * len(bars))  # anchor: the beat is found
        self.assertAlmostEqual(entry.scalar("primary_bpm"), 100, delta=2)
        self.assertLess(entry.series("downbeat_margin").max(), rhythm.parameters()["downbeat_margin"])
        self.assertEqual(entry.items("downbeats"), [])
        self.assertEqual({p["value"] for p in pulse}, {0})

    def test_a_slow_ballad_is_counted_where_the_tracker_counts_it(self) -> None:
        # 66 BPM with eighths on the hats: the preference near 120 BPM alone would make it 132.
        directory = scratch(self)
        samples, _ = band([(45, 60 / 66, 4, 2)])
        audio = support.write_wav(directory / "ballad.wav", samples, RATE)
        code, answer = run(directory, audio)
        self.assertEqual(code, 0, answer)
        entry = entry_of(directory, audio)
        self.assertAlmostEqual(entry.scalar("bpm"), 66, delta=1.5)  # anchor: the tracker counts 66
        self.assertAlmostEqual(entry.scalar("primary_bpm"), 66, delta=1.5)
        self.assertEqual(entry.scalar("meter"), "4/4")

    def test_takes_between_room_noise_are_each_measured_on_their_own(self) -> None:
        # A rehearsal: takes at 100 in 4/4, 140 in 3/4 and 72 in 12/8, 30 s of room noise between, a floor
        # of pink noise 50 dB down throughout. The 12/8 take used to inherit the 3/4 take's level.
        directory = scratch(self)
        takes = [band([(40, 0.6, 4, 2)], seed=1)[0], band([(40, 60 / 140, 3, 2)], seed=2)[0],
                 band([(45, 60 / 72, 4, 3)], seed=3)[0]]
        gap = numpy.zeros(30 * RATE)
        samples = numpy.concatenate((takes[0], gap, takes[1], gap, takes[2]))
        white = numpy.fft.rfft(numpy.random.default_rng(9).normal(0, 1, len(samples)))
        white[1:] /= numpy.sqrt(numpy.arange(1, len(white)))
        pink = numpy.fft.irfft(white, len(samples))
        samples = samples + pink / numpy.sqrt(numpy.mean(pink ** 2)) * 10 ** (-50 / 20)
        audio = support.write_wav(directory / "rehearsal.wav", samples, RATE)
        code, answer = run(directory, audio)
        self.assertEqual(code, 0, answer)
        entry = entry_of(directory, audio)
        spans = entry.items("tempo_spans")
        self.assertEqual([s["label"] for s in spans], ["4/4", "3/4", "12/8"], spans)
        for span, bpm in zip(spans, (100, 140, 72)):
            self.assertAlmostEqual(span["value"], bpm, delta=2)
        # Nothing is counted in the room noise between takes.
        starts = numpy.cumsum([0.0] + [len(t) / RATE + 30 for t in takes])
        for end_of_take, next_take in zip(starts[:-1] + [len(t) / RATE for t in takes], starts[1:]):
            pulse = times(entry, "pulse")
            self.assertEqual(list(pulse[(pulse > end_of_take + 1) & (pulse < next_take)]), [])
        self.assertEqual(entry.items("tempo_changes"), [])  # takes 30 s apart are not compared

    def test_loose_playing_is_further_off_the_grid_than_tight(self) -> None:
        directory = scratch(self)
        grid = numpy.arange(0.5, 40, 0.25)  # eighths at 120 BPM
        jitter = numpy.random.default_rng(3).uniform(-0.045, 0.045, len(grid))
        jitter[::2] = 0  # the beats themselves stay put, so the tracker keeps its tempo
        tight = support.write_wav(directory / "tight.wav", hits(40, grid, bed=0.1), RATE)
        loose = support.write_wav(directory / "loose.wav", hits(40, grid + jitter, bed=0.1), RATE)
        measured = {}
        for name, audio in (("tight", tight), ("loose", loose)):
            code, answer = run(directory, audio)
            self.assertEqual(code, 0, answer)
            entry = entry_of(directory, audio)
            middle = slice(8, 32)
            self.assertTrue(numpy.all(entry.series("onset_count")[middle] > 20), name)
            measured[name] = (entry.series("offgrid")[middle].mean(), entry.series("onset_deviation")[middle, 0].mean())
        # Anchor: the two inputs differ, and only in where the off-beat hits fall.
        self.assertNotEqual(bank.file_digest(tight), bank.file_digest(loose))
        self.assertLess(measured["tight"][0], 0.1, measured)
        self.assertLess(measured["tight"][1], 8, measured)  # Essentia's onsets and beats move in 11.6 ms steps
        self.assertGreater(measured["loose"][0], measured["tight"][0] + 0.05, measured)
        self.assertGreater(measured["loose"][1], measured["tight"][1] + 5, measured)

    def test_on_grid_playing_reads_on_the_grid_at_any_tempo_length_and_phase(self) -> None:
        shifts = []
        for bpm, seconds, start in ((100, 40, 0.5), (120, 41, 0.5), (126, 40, 1.013), (140, 60, 0.4),
                                    (120, 60, 1.013), (100, 61, 1.0)):
            with self.subTest(bpm=bpm, seconds=seconds, start=start):
                found, beats = measured(hits(seconds, on_grid(bpm, seconds, start), bed=0.15))
                rows = rhythm.series(found, beats, rhythm.parameters())
                playing = rows["onset_count"][:, 0] > 10
                self.assertGreater(playing.sum(), seconds - 15)  # anchor: the playing is measured
                offgrid = float(numpy.median(rows["offgrid"][playing, 0]))
                signed = float(numpy.median(rows["onset_deviation"][playing, 1]))
                self.assertLess(offgrid, 0.05, (offgrid, signed))
                self.assertLess(abs(signed), 3, (offgrid, signed))
                shifts.append(found.chunk_shift[0])
                if abs(found.chunk_shift[0]) > 0.02:
                    # The tracker's own ticks, unaligned, are what read as off the grid.
                    unaligned = [b - found.chunk_shift[0] for b in beats]
                    before = rhythm.series(found, unaligned, rhythm.parameters())
                    self.assertGreater(float(numpy.median(before["offgrid"][playing, 0])), 0.15)
        # Anchor: the sweep includes inputs whose ticks were well off the onsets and some that were not:
        # the tracker's ticks fall about 15 ms either side of the attack, the onsets about 15 ms before it.
        self.assertGreater(max(shifts) - min(shifts), 0.02, shifts)
        self.assertGreater(max(abs(s) for s in shifts), 0.02, shifts)

    def test_events_are_on_one_time_base_just_before_the_attacks(self) -> None:
        played = numpy.arange(1.0, 40.0, 0.5)
        found, beats = measured(hits(41, played, bed=0.1))
        onsets, beats = numpy.asarray(found.onsets), numpy.asarray(beats)
        self.assertEqual(len(onsets), len(played))  # anchor: one onset a hit
        lead = -rhythm.nearest(played, onsets)
        # OnsetRate's lead on this sharp click: about 14.5 ms, spread over its 11.6 ms hop.
        self.assertTrue(numpy.all((lead > -0.022) & (lead < -0.007)), lead * 1000)
        self.assertAlmostEqual(float(numpy.median(lead)), -0.0145, delta=0.002)
        # The beats sit on the onsets, whichever way the tracker's ticks were off.
        self.assertGreater(len(beats), len(played) - 3)
        self.assertLess(abs(float(numpy.median(rhythm.nearest(onsets, beats)))), 0.003)
        self.assertTrue(numpy.all(numpy.abs(rhythm.nearest(onsets, beats)) < 0.012))

    def test_no_beat_is_carried_on_past_the_playing(self) -> None:
        # Two songs with 20 s of room noise between, in a recording that starts in room noise.
        first, second = numpy.arange(1.0, 20.0, 0.5), numpy.arange(40.0, 59.6, 0.5)
        found, beats = measured(hits(61, numpy.concatenate((first, second))))
        beats = numpy.asarray(beats)
        self.assertGreater(((beats > 0.9) & (beats < 19.6)).sum(), 34)  # anchor: both songs have beats
        self.assertGreater(((beats > 39.9) & (beats < 59.6)).sum(), 36)
        self.assertGreaterEqual(min(found.onsets), 0.95)  # no onset at the start of the audio
        self.assertEqual(list(beats[(beats < 0.95) | ((beats > 19.55) & (beats < 39.95)) | (beats > 59.55)]), [])

    def test_silence_has_no_beats_and_no_confidence(self) -> None:
        directory = scratch(self)
        audio = support.write_wav(directory / "silence.wav", numpy.zeros(20 * RATE), RATE)
        code, answer = run(directory, audio)
        self.assertEqual(code, 0, answer)
        entry = entry_of(directory, audio)
        # Essentia alone finds 738 BPM in digital silence, with confidence 5.0.
        self.assertEqual(entry.items("beats"), [])
        self.assertEqual(entry.items("onsets"), [])
        self.assertEqual((entry.scalar("bpm"), entry.scalar("confidence"), entry.scalar("onset_rate")), (0.0, 0.0, 0.0))
        self.assertTrue(numpy.all(entry.series("tempo") == 0))
        self.assertEqual(entry.series("tempo").shape, (20, 1))
        # No beat, so no level, meter, or bar: empty rather than guessed.
        for name in ("pulse", "downbeats", "tempo_spans", "tempo_changes"):
            self.assertEqual(entry.items(name), [], name)
        self.assertEqual((entry.scalar("primary_bpm"), entry.scalar("meter")), (0.0, ""))
        self.assertEqual(entry.series("tempo_support").shape, (20, 7))
        self.assertTrue(numpy.all(entry.series("primary_tempo") == 0))
        self.assertTrue(numpy.all(entry.series("tempo_support") == 0))
        self.assertEqual(entry.series("downbeat_margin").shape, (20, 1))
        self.assertTrue(numpy.all(entry.series("downbeat_margin") == 0))

    def test_beats_are_not_invented_in_the_silence_before_playing(self) -> None:
        directory = scratch(self)
        samples = numpy.concatenate((numpy.zeros(30 * RATE), hits(30, numpy.arange(0.5, 30, 0.5))))
        audio = support.write_wav(directory / "late-start.wav", samples, RATE)
        self.assertEqual(run(directory, audio)[0], 0)
        entry = entry_of(directory, audio)
        beats = times(entry, "beats")
        self.assertGreater(len(beats), 40)  # anchor: the playing has beats
        self.assertGreaterEqual(beats.min(), 30 - rhythm.parameters()["beat_gate_seconds"])
        self.assertTrue(numpy.all(entry.series("tempo")[:22] == 0))

    def test_clipped_and_noisy_audio_is_measured_finite(self) -> None:
        directory = scratch(self)
        clipped = support.write_wav(directory / "clipped.wav",
                                    numpy.clip(hits(20, numpy.arange(0.5, 20, 0.5), bed=0.5, amplitude=3), -1, 1),
                                    RATE)
        noise = support.noise_flac(directory / "noise.flac", RATE, "20")
        for audio in (clipped, noise):
            with self.subTest(audio=audio.name):
                code, answer = run(directory, audio)
                self.assertEqual(code, 0, answer)
                entry = entry_of(directory, audio)
                for name in ("tempo", "onset_count", "onset_deviation", "offgrid", "chunk_bpm", "chunk_confidence"):
                    self.assertTrue(numpy.isfinite(entry.series(name)).all(), name)
                offgrid = entry.series("offgrid")
                self.assertTrue(numpy.all((offgrid >= 0) & (offgrid <= 0.5)))
        self.assertAlmostEqual(entry_of(directory, clipped).scalar("bpm"), 120, delta=2)

    def test_a_video_s_audio_stream_is_measured(self) -> None:
        directory = scratch(self)
        pulse = support.write_wav(directory / "pulse.wav", hits(12, numpy.arange(0.5, 12, 0.5)), RATE)
        video = directory / "clip.mp4"
        support.ffmpeg("-f", "lavfi", "-i", "color=c=black:s=32x32:r=5:d=12", "-i", str(pulse), "-shortest",
                       "-c:v", "mpeg4", "-c:a", "aac", str(video))
        code, answer = run(directory, video)
        self.assertEqual(code, 0, answer)
        self.assertEqual(answer["media_type"], "video/mp4")
        self.assertAlmostEqual(entry_of(directory, video).scalar("onset_rate"), 2.0, delta=0.3)

    def test_audio_with_no_samples_fails_and_files_nothing(self) -> None:
        directory = scratch(self)
        code, answer = run(directory, support.empty_wav(directory / "empty.wav"))
        self.assertEqual(code, 1)
        self.assertIn("no samples", answer["extractors"]["rhythm"]["log"])
        self.assertEqual(list((directory / "bank").glob("sha256/*/*/*/bank/rhythm/*/entry.json")), [])

    def test_what_ffmpeg_cannot_decode_fails(self) -> None:
        directory = scratch(self)
        audio = directory / "not-audio.flac"
        audio.write_bytes(b"this is not a FLAC stream" * 100)
        code, answer = run(directory, audio)
        self.assertEqual(code, 1)
        self.assertIn("could not decode", answer["extractors"]["rhythm"]["log"])

    def test_settings_it_does_not_implement_are_refused_through_the_runner(self) -> None:
        directory = scratch(self)
        audio = support.sine_wav(directory / "tone.wav", RATE, "2")
        code, answer = run(directory, audio, {**rhythm.parameters(), "method": "degara"})
        self.assertEqual(code, 1)
        self.assertIn("implements method", answer["extractors"]["rhythm"]["log"])

    def test_another_subdivision_is_another_entry(self) -> None:
        directory = scratch(self)
        # Triplets: an accented hit a beat at 100 BPM and two softer ones between.
        between = numpy.concatenate((numpy.arange(0.8, 30, 0.6), numpy.arange(1.0, 30, 0.6)))
        audio = support.write_wav(directory / "triplets.wav", hits(30, numpy.arange(0.6, 30, 0.6), bed=0.1)
                                  + hits(30, between, amplitude=0.2, seed=1), RATE)
        triplets = {**rhythm.parameters(), "subdivisions": 3}
        code, first = run(directory, audio)
        self.assertEqual(code, 0, first)
        code, second = run(directory, audio, triplets)
        self.assertEqual(code, 0, second)
        self.assertNotEqual(first["extractors"]["rhythm"]["key"], second["extractors"]["rhythm"]["key"])
        fours = entry_of(directory, audio).series("offgrid")[8:22].mean()
        threes = entry_of(directory, audio, triplets).series("offgrid")[8:22].mean()
        # Anchor: some beat was found, so the comparison is about the grid.
        self.assertAlmostEqual(entry_of(directory, audio, triplets).scalar("bpm"), 100, delta=2)
        self.assertLess(threes, fours, (threes, fours))


if __name__ == "__main__":
    unittest.main()
