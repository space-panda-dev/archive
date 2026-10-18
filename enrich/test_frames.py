"""The frames extractor, run by the real runner on synthetic audio (ADR 0074).

Its pitch-class profiles must be the bits `cover.Analyser.features` makes of
the same whole recording, so a reader of the bank measures nothing again.
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
from unittest.mock import patch

import numpy

import bank
import cover
import extract
import support
from support import needs_ffmpeg
from test_cover import needs_essentia

sys.path.insert(0, str(Path(__file__).resolve().parent / "extractors" / "frames"))
import frames  # noqa: E402


def run_frames(directory: Path, audio: Path, *extra: str) -> tuple[int, dict]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = extract.main(["run", "frames", "--content", str(audio), "--bank", str(directory / "bank"), *extra])
    return code, json.loads(out.getvalue() or err.getvalue())


def scratch(test: unittest.TestCase) -> Path:
    directory = Path(tempfile.mkdtemp(prefix="bank-frames-"))
    test.addCleanup(shutil.rmtree, directory, True)
    return directory


class Manifest(unittest.TestCase):
    def test_the_manifest_declares_exactly_what_the_program_implements(self) -> None:
        extractor = bank.Catalog.load()["frames"]
        self.assertEqual(extractor.manifest["parameters"], frames.parameters())
        self.assertEqual(extractor.manifest["parameters"]["essentia"], cover.ESSENTIA_BUILD)
        series = {name for name, out in extractor.outputs.items() if out["kind"] == "series"}
        self.assertEqual(series, set(frames.SERIES))
        for name, columns in frames.SERIES.items():
            self.assertEqual(extractor.outputs[name]["columns"], columns)
            self.assertEqual(extractor.outputs[name]["grid"], "frames")
        # The HPCP settings are cover's, so changing cover's changes the key.
        for field in ("analysis_rate", "frame_size", "hop_size", "windowing", "spectral_peaks", "hpcp"):
            self.assertEqual(frames.parameters()[field], cover.ANALYSIS[field])


@needs_essentia
class Framing(unittest.TestCase):
    def test_framing_is_frame_generators(self) -> None:
        """The streamed frames are Essentia's FrameGenerator's, for lengths
        around the hop and the frame and for blocks that split frames."""
        es = cover.essentia_module()
        generator = numpy.random.default_rng(7)
        for length in (1, 2047, 2048, 2049, 4095, 4096, 4097, 6143, 6144, 6145, 50001):
            audio = generator.uniform(-1, 1, length).astype(numpy.float32)
            expected = [numpy.array(frame) for frame in es.FrameGenerator(audio, frameSize=frames.FRAME,
                                                                          hopSize=frames.HOP)]
            self.assertEqual(len(expected), math.ceil(length / frames.HOP) + 1)
            for block in (1000, 2048, 3000, 100000):
                with self.subTest(length=length, block=block):
                    blocks = [audio[i:i + block] for i in range(0, length, block)]
                    found = list(frames.framed(blocks))
                    self.assertEqual(len(found), len(expected))
                    for mine, theirs in zip(found, expected):
                        self.assertEqual(mine.dtype, numpy.float32)
                        self.assertEqual(mine.tobytes(), theirs.tobytes())
        self.assertEqual(list(frames.framed([])), [])


@needs_ffmpeg
@needs_essentia
class Frames(unittest.TestCase):
    def test_hpcp_is_the_bits_cover_measures_on_the_whole_recording(self) -> None:
        directory = scratch(self)
        for rate, suffix, channels in ((44100, ".wav", 1), (48000, ".flac", 2)):
            with self.subTest(rate=rate):
                source = support.write_wav(directory / f"song-{rate}.wav",
                                           support.progression_samples(support.PROGRESSION, rate, repeats=2), rate)
                if suffix != ".wav" or channels != 1:
                    support.ffmpeg("-i", str(source), "-ac", str(channels), "-c:a", "flac",
                                   str(source.with_suffix(suffix)))
                    source = source.with_suffix(suffix)
                code, answer = run_frames(directory, source)
                self.assertEqual(code, 0, answer)
                entry = bank.Bank(directory / "bank").entry(bank.file_digest(source), "frames")
                stored = entry.series("hpcp")
                total = 16 * rate  # eight one-second chords, twice
                fresh = cover.Analyser().features(cover.load([cover.Piece(source, 0, total, rate)])).profiles
                # Anchor: the profiles say something, so equality is not two empty arrays.
                self.assertGreater(len(fresh), 100)
                self.assertGreater(float(fresh.max()), 0.5)
                self.assertEqual(stored.dtype, numpy.dtype("<f4"))
                self.assertEqual(stored.shape, fresh.shape)
                self.assertTrue(numpy.array_equal(stored, fresh))
                self.assertEqual(stored.tobytes(), fresh.astype("<f4").tobytes())

    def test_every_track_shares_one_grid(self) -> None:
        directory = scratch(self)
        rate = 16000
        source = support.write_wav(directory / "tone.wav", 0.5 * numpy.sin(2 * numpy.pi * 440
                                                                              * numpy.arange(3 * rate) / rate), rate)
        code, answer = run_frames(directory, source)
        self.assertEqual(code, 0, answer)
        entry = bank.Bank(directory / "bank").entry(bank.file_digest(source), "frames")
        samples = entry.scalar("samples")
        self.assertEqual(samples, 3 * frames.RATE)
        self.assertAlmostEqual(entry.scalar("seconds"), 3.0)
        rows = math.ceil(samples / frames.HOP) + 1
        for name, columns in frames.SERIES.items():
            self.assertEqual(entry.series(name).shape, (rows, columns), name)
            self.assertEqual(entry.grid(name), {"sample_rate": 22050, "hop": 2048, "frame": 4096,
                                                "first_centre": 0})
        middle = slice(3, rows - 3)  # frames whose window lies inside the audio
        # A sine of amplitude 0.5 has an RMS of 0.5 / sqrt 2, about -9.03 dBFS.
        self.assertTrue(numpy.allclose(entry.series("rms_db")[middle], 20 * math.log10(0.5 / math.sqrt(2)),
                                       atol=0.05))
        centroid = entry.series("centroid")[middle]
        # Near the tone; quantisation noise across the spectrum pulls it up a little.
        self.assertTrue(numpy.all(numpy.abs(centroid - 440) < 100), centroid.ravel()[:5])
        hpcp = entry.series("hpcp")[middle]
        self.assertEqual(int(hpcp.mean(axis=0).argmax()), 0)  # A is HPCP's first bin
        self.assertEqual(entry.manifest["software"][1], {"name": "essentia", "version": cover.ESSENTIA_BUILD})

    def test_silence_is_measured_and_finite(self) -> None:
        directory = scratch(self)
        source = support.write_wav(directory / "quiet.wav", numpy.zeros(22050), 22050)
        code, answer = run_frames(directory, source)
        self.assertEqual(code, 0, answer)
        entry = bank.Bank(directory / "bank").entry(bank.file_digest(source), "frames")
        self.assertTrue(numpy.all(entry.series("rms_db") == frames.RMS_FLOOR_DB))
        for name in frames.SERIES:
            self.assertTrue(numpy.isfinite(entry.series(name)).all(), name)

    def test_audio_with_no_samples_fails(self) -> None:
        directory = scratch(self)
        code, answer = run_frames(directory, support.empty_wav(directory / "empty.wav"))
        self.assertEqual(code, 1)
        self.assertIn("no samples", answer["extractors"]["frames"]["log"])
        self.assertEqual(list((directory / "bank").glob("sha256/*/*/*/bank/frames/*/entry.json")), [])

    def test_other_settings_are_refused_not_measured(self) -> None:
        directory = scratch(self)
        source = support.sine_wav(directory / "tone.wav", 22050, "1")
        config = directory / "config.json"
        config.write_text(json.dumps({"parameters": {"frames": {"hop_size": 1024}}}))
        code, answer = run_frames(directory, source, "--config", str(config))
        self.assertEqual(code, 1)
        self.assertIn("fixed settings", answer["extractors"]["frames"]["log"])

    def test_cover_reads_whole_file_profiles_from_the_bank(self) -> None:
        directory = scratch(self)
        rate = 22050
        query = support.write_wav(directory / "query.wav",
                                  support.progression_samples(support.PROGRESSION, rate, repeats=2), rate)
        reference = support.write_wav(directory / "reference.wav",
                                      support.progression_samples(support.PROGRESSION, rate, transpose=2,
                                                                  repeats=2, seed=1), rate)
        measured = io.StringIO()
        with redirect_stdout(measured):
            self.assertEqual(cover.main([str(query), str(reference)]), 0)
        for source in (query, reference):
            self.assertEqual(run_frames(directory, source)[0], 0)

        def bomb(*_args, **_kwargs):
            raise AssertionError("measured again what the bank holds")
        banked = io.StringIO()
        with patch.object(cover.Analyser, "features", bomb), redirect_stdout(banked):
            self.assertEqual(cover.main(["--bank", str(directory / "bank"), str(query), str(reference)]), 0)
        scores = json.loads(banked.getvalue())["scores"]
        self.assertIsNotNone(scores[str(reference)])
        self.assertEqual(json.loads(measured.getvalue()), json.loads(banked.getvalue()))
        # A file the bank does not hold is measured, as before.
        empty_bank = directory / "empty-bank"
        empty_bank.mkdir()
        again = io.StringIO()
        with redirect_stdout(again):
            self.assertEqual(cover.main(["--bank", str(empty_bank), str(query), str(reference)]), 0)
        self.assertEqual(json.loads(again.getvalue()), json.loads(measured.getvalue()))

    def test_cover_refuses_a_damaged_bank_rather_than_guessing(self) -> None:
        directory = scratch(self)
        source = support.write_wav(directory / "query.wav",
                                   support.progression_samples(support.PROGRESSION, 22050, repeats=2), 22050)
        self.assertEqual(run_frames(directory, source)[0], 0)
        entry = bank.Bank(directory / "bank").entry(bank.file_digest(source), "frames")
        (entry.path / "entry.json").write_text("{")
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(cover.main(["--bank", str(directory / "bank"), str(source), str(source)]), 1)
        self.assertIn("feature bank", json.loads(err.getvalue())["detail"])


if __name__ == "__main__":
    unittest.main()
