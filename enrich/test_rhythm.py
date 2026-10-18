"""The rhythm extractor, run by the real runner on synthetic audio (ADR 0074).

The audio is written by the writers the other tests use (Python's `wave`
through `support.write_wav`, and ffmpeg's own generators) and decoded by the
extractor's ffmpeg, as a recording would be.
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

    def test_a_beat_needs_an_onset_near_it(self) -> None:
        self.assertEqual(rhythm.gate([1.0, 5.0, 9.0], [1.2, 8.5], 2), [1.0, 9.0])
        self.assertEqual(rhythm.gate([1.0, 2.0], [], 2), [])

    def test_deviation_is_to_the_nearest_subdivision_and_signed(self) -> None:
        pairs = rhythm.intervals([10.0, 11.0, 12.0, 20.0, 21.0], 1.5)
        self.assertEqual(pairs, [(10.0, 11.0), (11.0, 12.0), (20.0, 21.0)])  # 12 to 20 is a gap, not a beat
        found = rhythm.deviations([9.0, 10.26, 11.74, 15.0, 20.5, 30.0], pairs, 4)
        # Onsets before the first beat, in the gap, and after the last are not measured.
        self.assertEqual([round(t, 6) for t, _, _ in found], [10.26, 11.74, 20.5])
        signed = [round(s, 6) for _, s, _ in found]
        self.assertEqual(signed, [0.01, -0.01, 0.0])
        self.assertEqual([round(f, 6) for _, _, f in found], [0.04, 0.04, 0.0])


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
