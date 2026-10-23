"""The f0-pesto and f0-torchcrepe extractors, run by the real runner (ADR 0074).

This machine has no PyTorch, so most tests configure `f0-python` as a
stand-in program: it runs the real `f0_pesto.py` or `f0_crepe.py` (request
handling, decoding, the grid, and the result) with only the tracker
replaced by a plain spectral-peak tracker. The real trackers run when
ARCHIVE_TEST_F0_PYTHON names their interpreter: tones of known pitch must
come back at that pitch, on the grid the entry declares.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import wave
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import numpy

import bank
import extract
from support import needs_ffmpeg

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "extractors" / "f0-pesto"))
sys.path.insert(0, str(HERE / "extractors" / "f0-torchcrepe"))
import f0_crepe  # noqa: E402
import f0_pesto  # noqa: E402

F0_PYTHON = os.environ.get("ARCHIVE_TEST_F0_PYTHON")
TRACKERS = {"f0-pesto": f0_pesto, "f0-torchcrepe": f0_crepe}

# The stand-in for the trackers' interpreter. The runner starts it as
# `<it> <program> <request.json>`; it loads that program and replaces its
# tracker with a spectral peak of 1,024 samples centred on each row.
FAKE = r'''#!{python}
import importlib.util, os, sys
import numpy
spec = importlib.util.spec_from_file_location("f0_program", sys.argv[1])
program = importlib.util.module_from_spec(spec)
spec.loader.exec_module(program)
mode = os.environ.get("F0_FAKE", "peaks")
if mode == "chunked":
    program.CHUNK = 2 * program.RATE  # chunks of 2 s, so a short input has several
if mode == "exit":
    print("the tracker crashed on purpose")
    sys.exit(1)

class Peaks:
    software = [{"name": "fake-tracker", "version": "1"}]

    def track(self, signal, first=None):
        if mode != "chunked":
            return self.peaks(signal)
        # PESTO's shape: each chunk tracked alone, its edges heard against silence.
        pitches, confidences = [], []
        for start, end in program.chunks(len(signal), first):
            pitch, confidence = self.peaks(signal[start:end])
            keep = (end - start) // program.HOP if end < len(signal) else None
            pitches.append(pitch[:keep])
            confidences.append(confidence[:keep])
        return numpy.concatenate(pitches), numpy.concatenate(confidences)

    def peaks(self, signal):
        hop, size = program.HOP, 1024
        rows = len(signal) // hop + 1 + {"short": -7, "long": 5}.get(mode, 0)
        padded = numpy.pad(signal.astype(numpy.float64), (size // 2, size // 2 + hop * rows))
        window = numpy.hanning(size)
        pitch, confidence = numpy.zeros(rows), numpy.zeros(rows)
        for k in range(rows):
            frame = padded[k * hop:k * hop + size] * window
            if numpy.mean(frame ** 2) < 1e-8:
                continue
            spectrum = numpy.abs(numpy.fft.rfft(frame, 8 * size))
            i = int(numpy.argmax(spectrum[1:-1])) + 1
            a, b, c = numpy.log(spectrum[i - 1:i + 2] + 1e-12)
            pitch[k] = (i + 0.5 * (a - c) / (a - 2 * b + c)) * program.RATE / (8 * size)
            confidence[k] = 0.9
        if mode == "nan":
            pitch[3], confidence[4] = numpy.nan, numpy.inf
        return pitch.astype(numpy.float32), confidence.astype(numpy.float32)

program.load_tracker = lambda: Peaks()
sys.exit(program.main(sys.argv[2:]))
'''


def scratch(test: unittest.TestCase) -> Path:
    directory = Path(tempfile.mkdtemp(prefix="bank-f0-"))
    test.addCleanup(shutil.rmtree, directory, True)
    return directory


def fake_python(directory: Path) -> Path:
    path = directory / "fake-f0-python"
    path.write_text(FAKE.replace("{python}", sys.executable))
    path.chmod(0o755)
    return path


def run(directory: Path, audio: Path, target: str, *extra: str, program: Path | None,
        env: dict[str, str] | None = None) -> tuple[int, dict]:
    argv = ["run", target, "--content", str(audio), "--bank", str(directory / "bank"), *extra]
    if program is not None:
        argv += ["--program", f"f0-python={program}"]
    out, err = io.StringIO(), io.StringIO()
    with patch.dict(os.environ, env or {}), redirect_stdout(out), redirect_stderr(err):
        code = extract.main(argv)
    return code, json.loads(out.getvalue() or err.getvalue())


def tones(path: Path, rate: int = 44100) -> Path:
    """A voice-like line at a known pitch: 1 s of A3 (220 Hz), 1 s of E4
    (329.63 Hz), each with five harmonics, then 0.5 s of silence; 16-bit
    mono WAV."""
    t = numpy.arange(rate) / rate
    parts = []
    for f in (220.0, 329.63):
        tone = sum(numpy.sin(2 * numpy.pi * f * h * t) / h for h in range(1, 6))
        parts.append(0.3 * tone / numpy.abs(tone).max())
    parts.append(numpy.zeros(rate // 2))
    signal = numpy.concatenate(parts)
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(numpy.round(signal * 32767).astype("<i2").tobytes())
    return path


def cents(f: numpy.ndarray, reference: float) -> numpy.ndarray:
    return 1200 * numpy.log2(f / reference)


class Manifests(unittest.TestCase):
    def test_each_manifest_declares_exactly_what_its_program_implements(self) -> None:
        catalog = bank.Catalog.load()
        for extractor_id, program in TRACKERS.items():
            with self.subTest(extractor=extractor_id):
                extractor = catalog[extractor_id]
                self.assertEqual(extractor.manifest["parameters"], program.parameters())
                self.assertEqual(extractor.manifest["command"][0], "${f0-python}")
                self.assertEqual(set(extractor.outputs), {"f0", "rows_from_tracker"} | (
                    {"level_db", "loud_db"} if extractor_id == "f0-pesto" else set()))
                self.assertEqual(extractor.outputs["f0"]["columns"], 2)
                self.assertEqual(extractor.outputs["f0"]["grid"], "f0")

    def test_torchcrepe_is_the_tiny_model_only(self) -> None:
        self.assertEqual(f0_crepe.parameters()["model"], "tiny")
        self.assertIn("tiny", bank.Catalog.load()["f0-torchcrepe"].manifest["requires"][-1])

    def test_the_archive_side_never_imports_torch(self) -> None:
        self.assertNotIn("torch", sys.modules)
        for program in TRACKERS.values():
            top = Path(program.__file__).read_text().split("\nclass ", 2)
            self.assertNotIn("import torch", top[0] + top[1])


class Levels(unittest.TestCase):
    def test_a_row_level_is_the_rms_of_the_20_ms_centred_on_it_silence_outside(self) -> None:
        signal = numpy.zeros(1600, dtype=numpy.float32)
        signal[800:] = 0.5
        level = f0_pesto.row_levels(signal, 11)
        self.assertEqual(level.shape, (11, 1))
        self.assertEqual(level.dtype, numpy.dtype("<f4"))
        self.assertEqual(level[0, 0], -120.0)                       # rows 0 to 3 hear only silence
        self.assertAlmostEqual(float(level[5, 0]), 10 * numpy.log10(0.125), places=4)  # half of it sounds
        self.assertAlmostEqual(float(level[7, 0]), 20 * numpy.log10(0.5), places=4)
        self.assertAlmostEqual(float(level[10, 0]), 10 * numpy.log10(0.125), places=4)  # past the end: silence


class Grid(unittest.TestCase):
    def test_a_track_is_cut_or_padded_to_the_grid_and_cleaned(self) -> None:
        for program in TRACKERS.values():
            track = program.on_grid(numpy.array([100.0, numpy.nan, numpy.inf, 200.0]),
                                    numpy.array([0.5, 0.6, numpy.nan, 0.7]), 3 * program.HOP)
            self.assertEqual(track.shape, (4, 2))
            self.assertEqual(track.dtype, numpy.dtype("<f4"))
            self.assertEqual(track[:, 0].tolist(), [100.0, 0.0, 0.0, 200.0])
            numpy.testing.assert_allclose(track[:, 1], [0.5, 0.6, 0.0, 0.7])
            self.assertEqual(program.on_grid(numpy.ones(9), numpy.ones(9), 159).shape, (1, 2))
            padded = program.on_grid(numpy.ones(2), numpy.ones(2), 5 * program.HOP + 1)
            self.assertEqual(padded[:, 0].tolist(), [1, 1, 0, 0, 0, 0])
            with self.assertRaises(program.F0Error):
                program.on_grid(numpy.ones(2), numpy.ones(3), 160)


class CrepeDecoding(unittest.TestCase):
    """torchcrepe's decoders add random noise; f0-torchcrepe decodes its own way."""

    def test_the_pitch_is_the_weighted_mean_of_the_bins_around_the_path(self) -> None:
        def hz(bins: float) -> float:
            return 10 * 2 ** ((f0_crepe.LOWEST_CENTS + 20 * bins) / 1200)

        probabilities = numpy.zeros((360, 5))
        probabilities[100, 0] = 0.9                                 # one bin: its centre
        probabilities[[99, 100, 101], 1] = [0.3, 0.9, 0.3]          # symmetric: the centre
        probabilities[[100, 101], 2] = [0.6, 0.6]                   # between two bins
        probabilities[[100, 110], 3] = [0.5, 0.9]                   # a bin past the reach is not counted
        path = numpy.array([100, 100, 100, 100, 200])                # no probability near: the path's bin
        got = f0_crepe.local_average_hz(probabilities, path)
        numpy.testing.assert_allclose(got, [hz(100), hz(100), hz(100.5), hz(100), hz(200)], rtol=1e-12)
        edges = numpy.zeros((360, 2))
        edges[[0, 1], 0] = [1.0, 1.0]
        edges[[358, 359], 1] = [1.0, 1.0]
        numpy.testing.assert_allclose(f0_crepe.local_average_hz(edges, numpy.array([0, 359])),
                                      [hz(0.5), hz(358.5)], rtol=1e-12)


@needs_ffmpeg
class Tracking(unittest.TestCase):
    """The real programs through the real runner, with the stand-in tracker."""

    def test_writes_the_pitch_on_its_grid_and_is_cached(self) -> None:
        directory = scratch(self)
        audio = tones(directory / "line.wav")
        fake = fake_python(directory)
        for extractor_id in TRACKERS:
            with self.subTest(extractor=extractor_id):
                code, answer = run(directory, audio, extractor_id, program=fake)
                self.assertEqual(code, 0, answer)
                entry = bank.Bank(directory / "bank").entry(bank.file_digest(audio), extractor_id)
                track = entry.series("f0")
                # 2.5 s at 16 kHz: 40,000 samples, a row every 160 from sample 0.
                self.assertEqual(track.shape, (40000 // 160 + 1, 2))
                self.assertEqual(entry.scalar("rows_from_tracker"), 251)
                self.assertEqual(entry.grid("f0"), {"sample_rate": 16000, "hop": 160, "first_centre": 0})
                self.assertLess(numpy.abs(cents(numpy.median(track[10:90, 0]), 220.0)), 5)
                self.assertLess(numpy.abs(cents(numpy.median(track[110:190, 0]), 329.63)), 5)
                self.assertEqual(track[215:, 0].tolist(), [0.0] * (251 - 215))
                if extractor_id == "f0-pesto":
                    level = entry.series("level_db")
                    self.assertEqual(level.shape, (251, 1))
                    self.assertEqual(entry.grid("level_db"), entry.grid("f0"))
                    # Each tone's RMS is -15.8 dBFS; from row 202 on, whose 20 ms
                    # reach starts past 2.0 s and the resampler's ringing, there
                    # is silence: the floor.
                    self.assertTrue(numpy.all(numpy.abs(level[10:190, 0] + 15.8) < 1.5), level[10:190, 0])
                    self.assertLess(level[201, 0], -40)
                    self.assertEqual(level[202:, 0].tolist(), [-120.0] * (251 - 202))
                    self.assertLess(abs(entry.scalar("loud_db") - float(numpy.percentile(level, 99))), 1e-3)
                names = {s["name"] for s in entry.manifest["software"]}
                self.assertTrue({"fake-tracker", "ffmpeg", "numpy", "python"} <= names)
                with patch.object(extract.subprocess, "Popen", side_effect=AssertionError("ran")):
                    code, again = run(directory, audio, extractor_id, "--digest", entry.manifest["content"],
                                      program=fake)
                self.assertEqual((code, again["extractors"][extractor_id]["status"]), (0, "hit"))

    def test_a_tracker_that_gives_too_few_or_too_many_rows_or_not_numbers_is_put_on_the_grid(self) -> None:
        directory = scratch(self)
        audio = tones(directory / "line.wav")
        for mode, rows in (("short", 244), ("long", 256), ("nan", 251)):
            with self.subTest(mode=mode):
                where = directory / mode
                code, answer = run(where, audio, "f0-pesto", program=fake_python(directory), env={"F0_FAKE": mode})
                self.assertEqual(code, 0, answer)
                entry = bank.Bank(where / "bank").entry(bank.file_digest(audio), "f0-pesto")
                track = entry.series("f0")
                self.assertEqual(track.shape, (251, 2))
                self.assertEqual(entry.scalar("rows_from_tracker"), rows)
                if mode == "short":
                    self.assertEqual(track[244:].tolist(), [[0.0, 0.0]] * 7)
                if mode == "nan":
                    self.assertEqual((track[3, 0], track[4, 1]), (0.0, 0.0))

    def test_failures_are_recorded_and_other_settings_refused(self) -> None:
        directory = scratch(self)
        audio = tones(directory / "line.wav")
        fake = fake_python(directory)
        code, answer = run(directory, audio, "f0-torchcrepe", program=fake, env={"F0_FAKE": "exit"})
        self.assertEqual(code, 1)
        self.assertIn("crashed on purpose", answer["extractors"]["f0-torchcrepe"]["log"])
        not_audio = directory / "nothing.wav"
        not_audio.write_bytes(b"not audio")
        code, answer = run(directory, not_audio, "f0-pesto", program=fake)
        self.assertEqual(code, 1)
        self.assertIn("could not decode", answer["extractors"]["f0-pesto"]["log"])
        config = directory / "config.json"
        config.write_text(json.dumps({"parameters": {"f0-torchcrepe": {"model": "full"}}}))
        code, answer = run(directory, audio, "f0-torchcrepe", "--config", str(config), program=fake)
        self.assertEqual(code, 1)
        self.assertIn("f0-torchcrepe has fixed settings", answer["extractors"]["f0-torchcrepe"]["log"])

    def test_without_its_interpreter_all_skips_it(self) -> None:
        directory = scratch(self)
        audio = tones(directory / "line.wav")
        code, answer = run(directory, audio, "all", "--dry-run", program=None)
        self.assertEqual(code, 0, answer)
        for extractor_id in TRACKERS:
            self.assertEqual(answer["extractors"][extractor_id]["status"], "skipped")
            self.assertIn("f0-python", answer["extractors"][extractor_id]["reason"])


class Region(unittest.TestCase):
    """A region is tracked on the whole run's rows and minutes (ADR 0112)."""

    def test_chunks_and_placement(self) -> None:
        minute = f0_pesto.CHUNK
        self.assertEqual(f0_pesto.chunks(10, None), [(0, 10)])
        self.assertEqual(f0_pesto.chunks(2 * minute + 5), [(0, minute), (minute, 2 * minute),
                                                           (2 * minute, 2 * minute + 5)])
        self.assertEqual(f0_pesto.chunks(minute + 500, 320), [(0, 320), (320, minute + 320),
                                                              (minute + 320, minute + 500)])
        signal = numpy.ones(1000, dtype=numpy.float32)
        placed, lead, first = f0_pesto.placed(signal, minute + 1601)
        self.assertEqual((lead, first, len(placed), float(placed[:lead].sum())), (1, minute - 1600, 1001, 0.0))

    @needs_ffmpeg
    def test_a_region_is_the_whole_runs_rows_over_it(self) -> None:
        directory = scratch(self)
        rate = 48000
        t = numpy.arange(7 * rate) / rate
        # A glide, so every row has its own pitch: 200 Hz up to about 400.
        phase = 2 * numpy.pi * (200 * t + 100 * t ** 2 / 7)
        signal = 0.3 * sum(numpy.sin(h * phase) / h for h in range(1, 4))
        audio = directory / "glide.wav"
        with wave.open(str(audio), "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(rate)
            writer.writeframes(numpy.round(signal * 32767).astype("<i2").tobytes())
        fake, env = fake_python(directory), {"F0_FAKE": "chunked"}
        code, answer = run(directory, audio, "f0-pesto", program=fake, env=env)
        self.assertEqual(code, 0, answer)
        digest = bank.file_digest(audio)
        whole = bank.Bank(directory / "bank").entry(digest, "f0-pesto")
        # 2.500 s to 5.9 s: past one of the whole run's chunk edges (4 s), off its rows.
        start, end = 120003, 283200
        code, answer = run(directory / "region", audio, "f0-pesto", "--region", f"{start}:{end}:{rate}",
                           program=fake, env=env)
        self.assertEqual(code, 0, answer)
        region = bank.covering(bank.Bank(directory / "region" / "bank"), digest, "f0-pesto", start, end, rate)
        grid = region.grid("f0")
        offset = start * 16000 // rate
        self.assertEqual(grid["first_centre"], 159)  # the row after 40,001 is 40,160
        self.assertEqual((offset + grid["first_centre"]) % 160, 0)
        first = (offset + grid["first_centre"]) // 160
        mine, theirs = region.series("f0"), whole.series("f0")
        self.assertEqual(len(mine), (offset + (end - start) // 3) // 160 - first + 1)
        # Rows whose 1,024 samples lie in the region are the whole run's.
        inside = slice(4, len(mine) - 4)
        numpy.testing.assert_allclose(mine[inside], theirs[first:first + len(mine)][inside], rtol=0, atol=1e-3)
        self.assertGreater(float(mine[inside, 0].min()), 250)  # anchor: pitched rows, not two silences
        levels = region.series("level_db")[:, 0]
        numpy.testing.assert_allclose(levels[2:-2], whole.series("level_db")[first + 2:first + len(mine) - 2, 0],
                                      rtol=0, atol=1e-3)



@unittest.skipUnless(F0_PYTHON and Path(F0_PYTHON).is_file(),
                     "set ARCHIVE_TEST_F0_PYTHON to the trackers' interpreter to run PESTO and torchcrepe")
@needs_ffmpeg
class RealTrackers(unittest.TestCase):
    def test_each_tracker_finds_tones_of_known_pitch_where_they_are(self) -> None:
        directory = scratch(self)
        audio = tones(directory / "line.wav")
        for extractor_id in TRACKERS:
            with self.subTest(extractor=extractor_id):
                code, answer = run(directory, audio, extractor_id, program=Path(F0_PYTHON))
                self.assertEqual(code, 0, answer)
                entry = bank.Bank(directory / "bank").entry(bank.file_digest(audio), extractor_id)
                track = entry.series("f0")
                self.assertEqual(track.shape, (251, 2))
                low, high = track[10:90], track[110:190]
                print(f"\n{extractor_id}: A3 {cents(numpy.median(low[:, 0]), 220.0):+.1f} c, "
                      f"E4 {cents(numpy.median(high[:, 0]), 329.63):+.1f} c, confidence "
                      f"{numpy.median(low[:, 1]):.2f} playing, {numpy.median(track[215:, 1]):.2f} silent",
                      file=sys.stderr)
                self.assertLess(abs(cents(numpy.median(low[:, 0]), 220.0)), 15)
                self.assertLess(abs(cents(numpy.median(high[:, 0]), 329.63)), 15)
                self.assertGreater(numpy.median(low[:, 1]), 0.5)
                self.assertLess(numpy.median(track[215:, 1]), numpy.median(low[:, 1]))
                # The change from A3 to E4 at 1.0 s is at row 100 (sample 16,000), within 50 ms.
                above = numpy.flatnonzero(track[60:140, 0] > 275)
                self.assertTrue(len(above))
                self.assertLessEqual(abs(60 + int(above[0]) - 100), 5)
                # The same file and settings give the same bits.
                first = entry.output("f0")["sha256"]
                shutil.rmtree(entry.path)
                self.assertEqual(run(directory, audio, extractor_id, program=Path(F0_PYTHON))[0], 0)
                again = bank.Bank(directory / "bank").entry(bank.file_digest(audio), extractor_id)
                self.assertEqual(again.output("f0")["sha256"], first)


if __name__ == "__main__":
    unittest.main()
