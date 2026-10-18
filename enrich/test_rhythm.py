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
import rhythm  # noqa: E402

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
        for name in ("tempo", "onset_count", "onset_deviation", "offgrid", "chunk_bpm", "chunk_confidence"):
            self.assertTrue(numpy.isfinite(entry.series(name)).all(), name)
        with patch.object(extract.subprocess, "Popen", side_effect=AssertionError("ran again")):
            self.assertEqual(run(directory, audio, {**rhythm.parameters(), **SHORT_CHUNKS})[1]["extractors"]
                             ["rhythm"]["status"], "hit")

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
