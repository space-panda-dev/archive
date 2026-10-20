"""The notes-basic-pitch extractor, run by the real runner (ADR 0074).

This machine has no Basic Pitch, so most tests configure
`basic-pitch-python` as a stand-in program: it runs the real `notes.py`
(request handling, decoding, reading the contour, writing the notes, and
the result) with only the model and Basic Pitch's note maker replaced by a
transcription made here. The stand-in's contour puts a note where the
pinned model does, one bin above Basic Pitch's own mapping; that the model
does is what the real test shows. When ARCHIVE_TEST_BASIC_PITCH_PYTHON
names Basic Pitch's interpreter, tones of known pitch go through the real
model, and each note's `cents` must be within 10 cents of the truth.
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

sys.path.insert(0, str(Path(__file__).resolve().parent / "extractors" / "notes-basic-pitch"))
import notes  # noqa: E402

BASIC_PITCH_PYTHON = os.environ.get("ARCHIVE_TEST_BASIC_PITCH_PYTHON")

# The stand-in for Basic Pitch's interpreter. The runner starts it as
# `<it> notes.py <request.json>`; it loads notes.py and replaces its model
# with one that reads the decoded WAV's length and returns two notes: A4
# 10 cents sharp from frame 5 to 40, and C4 20 cents flat from frame 20 to
# 60, each a Gaussian peak in a 264-bin contour.
FAKE = r'''#!{python}
import importlib.util, os, sys
import numpy
spec = importlib.util.spec_from_file_location("notes_program", sys.argv[1])
program = importlib.util.module_from_spec(spec)
spec.loader.exec_module(program)
mode = os.environ.get("NOTES_FAKE", "two")
if mode == "exit":
    print("the model crashed on purpose")
    sys.exit(1)

class Fake:
    software = [{"name": "fake-basic-pitch", "version": "1"}]

    def transcribe(self, path, offset=None):
        if os.environ.get("NOTES_FAKE_OFFSET"):
            open(os.environ["NOTES_FAKE_OFFSET"], "w").write(repr(offset))
        # The program decodes to float WAV, which the wave module cannot read.
        data = open(path, "rb").read()
        fmt, chunk = data.index(b"fmt "), data.index(b"data")
        channels = int.from_bytes(data[fmt + 10:fmt + 12], "little")
        rate = int.from_bytes(data[fmt + 12:fmt + 16], "little")
        seconds = int.from_bytes(data[chunk + 4:chunk + 8], "little") / (4 * channels * rate)
        frames = int(seconds * 22050 / 256)
        times = numpy.arange(frames) * 256 / 22050
        contour = numpy.full((frames, 264), 1e-3)
        bins = numpy.arange(264)
        found = []
        for midi, off, start, end, amplitude in ((69, 10, 5, 40, 0.8), (60, -20, 20, 60, 0.5)):
            place = program.note_bin(midi) + off * 3 / 100
            contour[start:end] += 0.9 * numpy.exp(-0.5 * ((bins - place) / 0.7) ** 2)
            found.append((start, end, midi, amplitude))
        if mode == "outside":
            found.append((frames - 3, frames + 2, 64, 0.4))
        return program.Transcription(found[::-1], contour, times)

program.load_model = lambda: Fake()
sys.exit(program.main(sys.argv[2:]))
'''


def scratch(test: unittest.TestCase) -> Path:
    directory = Path(tempfile.mkdtemp(prefix="bank-notes-"))
    test.addCleanup(shutil.rmtree, directory, True)
    return directory


def fake_python(directory: Path) -> Path:
    path = directory / "fake-basic-pitch-python"
    path.write_text(FAKE.replace("{python}", sys.executable))
    path.chmod(0o755)
    return path


def run(directory: Path, audio: Path, *extra: str, program: Path | None,
        env: dict[str, str] | None = None) -> tuple[int, dict]:
    argv = ["run", "notes-basic-pitch", "--content", str(audio), "--bank", str(directory / "bank"), *extra]
    if program is not None:
        argv += ["--program", f"basic-pitch-python={program}"]
    out, err = io.StringIO(), io.StringIO()
    with patch.dict(os.environ, env or {}), redirect_stdout(out), redirect_stderr(err):
        code = extract.main(argv)
    return code, json.loads(out.getvalue() or err.getvalue())


TONES = ((69, 0), (69, 20), (57, -25), (52, 40), (64, -10), (76, 15))


def tones(path: Path, rate: int = 44100) -> Path:
    """Six harmonic tones of known pitch (MIDI note, cents from it), each
    1.2 s with 0.4 s of silence after, as 16-bit stereo WAV."""
    t = numpy.arange(int(1.2 * rate)) / rate
    ramp = numpy.minimum(1, numpy.minimum(t / 0.02, (t[-1] - t) / 0.02))
    parts = []
    for midi, off in TONES:
        f = 440 * 2 ** ((midi + off / 100 - 69) / 12)
        tone = sum(numpy.sin(2 * numpy.pi * f * h * t) / h ** 2 for h in range(1, 5))
        parts += [0.4 * tone / numpy.abs(tone).max() * ramp, numpy.zeros(int(0.4 * rate))]
    signal = numpy.concatenate(parts)
    pcm = numpy.round(numpy.stack([signal, signal], axis=1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(2)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(pcm.tobytes())
    return path


class Manifest(unittest.TestCase):
    def test_the_manifest_declares_exactly_what_the_program_implements(self) -> None:
        extractor = bank.Catalog.load()["notes-basic-pitch"]
        self.assertEqual(extractor.manifest["parameters"], notes.parameters())
        self.assertEqual(extractor.manifest["command"], ["${basic-pitch-python}", "notes.py"])
        self.assertEqual(extractor.manifest["parameters"]["model"]["runtime"], "onnxruntime")
        self.assertEqual(set(extractor.outputs), {"notes", "note_count", "seconds"})

    def test_the_archive_side_never_imports_the_model(self) -> None:
        self.assertNotIn("basic_pitch", sys.modules)
        top = (bank.EXTRACTORS / "notes-basic-pitch" / "notes.py").read_text().split("\nclass BasicPitch", 1)[0]
        for library in ("basic_pitch", "onnxruntime", "librosa", "tensorflow"):
            self.assertNotIn(f"import {library}", top)


class Contour(unittest.TestCase):
    def test_a_note_is_one_bin_above_basic_pitchs_own_mapping(self) -> None:
        # Basic Pitch's midi_pitch_to_contour_bin: 36 log2(hz / 27.5), so A4 is bin 144.
        self.assertEqual(notes.note_bin(69), 145)
        self.assertEqual(notes.note_bin(21), 1)

    def test_a_gaussian_peak_is_placed_exactly(self) -> None:
        bins = numpy.arange(9, dtype=float)
        for place in (3.0, 3.2, 3.49, 4.3, 4.5):
            row = numpy.exp(-0.5 * ((bins - place) / 0.8) ** 2)
            k = int(numpy.argmax(row))
            self.assertAlmostEqual(k + notes.peak_offset(*row[k - 1:k + 2]), place, places=6)
        self.assertEqual(notes.peak_offset(1.0, 1.0, 1.0), 0.0)
        self.assertEqual(notes.peak_offset(0.0, 0.5, 0.0), 0.0)

    def test_cents_and_bends_are_read_from_the_bin_the_model_uses(self) -> None:
        bins = numpy.arange(264)
        for off in (-40, -15, 0, 12.5, 33):
            place = notes.note_bin(62) + off * 3 / 100
            contour = numpy.tile(numpy.exp(-0.5 * ((bins - place) / 0.7) ** 2) + 1e-3, (6, 1))
            self.assertAlmostEqual(notes.note_cents(contour, 62), off, delta=0.5)
            self.assertEqual(set(notes.note_bends(contour, 62)), {round(off * 3 / 100)})
        # A bend that leaves the note is followed within 25 bins.
        place = numpy.r_[[notes.note_bin(62)] * 3, [notes.note_bin(62) + 6] * 3]
        contour = numpy.exp(-0.5 * ((bins[None, :] - place[:, None]) / 0.7) ** 2)
        self.assertEqual(notes.note_bends(contour, 62), [0, 0, 0, 6, 6, 6])

    def test_notes_at_the_edges_of_the_range_are_read(self) -> None:
        contour = numpy.full((4, 264), 1e-3)
        contour[:, 262] = 1.0
        self.assertEqual(len(notes.note_bends(contour, 108)), 4)
        self.assertTrue(numpy.isfinite(notes.note_cents(contour, 108)))
        contour = numpy.full((4, 264), 1e-3)
        contour[:, 1] = 1.0
        self.assertEqual(notes.note_bends(contour, 21), [0] * 4)


@needs_ffmpeg
class Transcribing(unittest.TestCase):
    """The real program through the real runner, with the stand-in model."""

    def test_writes_the_notes_with_their_cents_and_is_cached(self) -> None:
        directory = scratch(self)
        audio = tones(directory / "tones.wav")
        fake = fake_python(directory)
        code, answer = run(directory, audio, program=fake)
        self.assertEqual(code, 0, answer)
        entry = bank.Bank(directory / "bank").entry(bank.file_digest(audio), "notes-basic-pitch")
        document = json.loads(entry.file("notes").read_text())
        self.assertEqual(document["columns"], ["onset_s", "offset_s", "midi", "amplitude", "cents", "bends"])
        rows = document["notes"]
        self.assertEqual(entry.scalar("note_count"), 2)
        # By onset: A4 first, though the model gave it second.
        self.assertEqual([r[2] for r in rows], [69, 60])
        self.assertEqual(rows[0][:4], [round(5 * 256 / 22050, 4), round(40 * 256 / 22050, 4), 69, 0.8])
        self.assertAlmostEqual(rows[0][4], 10, delta=0.5)
        self.assertAlmostEqual(rows[1][4], -20, delta=0.5)
        self.assertEqual(len(rows[0][5]), 35)
        self.assertEqual(set(rows[0][5]), {0})
        self.assertEqual(set(rows[1][5]), {-1})  # 20 cents flat is nearer the bin below
        names = {s["name"] for s in entry.manifest["software"]}
        self.assertTrue({"fake-basic-pitch", "ffmpeg", "numpy", "python"} <= names)
        with patch.object(extract.subprocess, "Popen", side_effect=AssertionError("ran")):
            code, again = run(directory, audio, "--digest", entry.manifest["content"], program=fake)
        self.assertEqual((code, again["extractors"]["notes-basic-pitch"]["status"]), (0, "hit"))

    def test_a_note_past_the_contour_or_a_crash_fails_and_files_nothing(self) -> None:
        directory = scratch(self)
        audio = tones(directory / "tones.wav")
        for mode, message in (("outside", "a note spans frames"), ("exit", "crashed on purpose")):
            with self.subTest(mode=mode):
                code, answer = run(directory, audio, program=fake_python(directory), env={"NOTES_FAKE": mode})
                self.assertEqual(code, 1)
                self.assertIn(message, answer["extractors"]["notes-basic-pitch"]["log"])
                self.assertEqual(list((directory / "bank").glob("sha256/*/*/*/bank/notes-basic-pitch/*/entry.json")),
                                 [])

    def test_other_settings_and_undecodable_input_are_refused(self) -> None:
        directory = scratch(self)
        audio = tones(directory / "tones.wav")
        config = directory / "config.json"
        config.write_text(json.dumps({"parameters": {"notes-basic-pitch": {"contour_offset_bins": 0}}}))
        code, answer = run(directory, audio, "--config", str(config), program=fake_python(directory))
        self.assertEqual(code, 1)
        self.assertIn("fixed settings", answer["extractors"]["notes-basic-pitch"]["log"])
        not_audio = directory / "nothing.wav"
        not_audio.write_bytes(b"not audio")
        code, answer = run(directory, not_audio, program=fake_python(directory))
        self.assertEqual(code, 1)
        self.assertIn("could not decode", answer["extractors"]["notes-basic-pitch"]["log"])


class RegionGrid(unittest.TestCase):
    """A region is windowed as the whole run windows it (ADR 0112)."""

    @staticmethod
    def predict(window: numpy.ndarray) -> dict[str, numpy.ndarray]:
        # A stand-in model whose frame f reads the 256 samples from f x 256
        # of its window and knows where in the window it is, so a frame
        # agrees with the whole run's only on the same window grid.
        samples = window[0, :, 0].astype(numpy.float64)
        frames = numpy.array([samples[f * 256:(f + 1) * 256].sum() + f * 1e-3
                              for f in range(notes.WINDOW_FRAMES)])
        return {"note": frames[None, :, None], "contour": numpy.stack([frames, -frames], axis=1)[None]}

    def test_a_region_gives_the_whole_runs_frames_after_its_first_window(self) -> None:
        rng = numpy.random.default_rng(3)
        audio = rng.standard_normal(9 * notes.WINDOW_HOP + 1234).astype(numpy.float32)
        whole_layout = notes.layout(0, len(audio))
        self.assertEqual(whole_layout[:2], (notes.OVERLAP_SAMPLES // 2, 0))  # run_inference's own padding
        whole = notes.windowed(audio, whole_layout.lead, whole_layout.frames, self.predict)
        for offset, samples in ((2 * notes.WINDOW_HOP + 5000, 3 * notes.WINDOW_HOP + 77),
                                (notes.WINDOW_HOP - 100, notes.WINDOW_HOP), (40000, len(audio) - 40000)):
            with self.subTest(offset=offset):
                lead, base, frames = notes.layout(offset, samples)
                self.assertEqual(base, offset // notes.WINDOW_HOP * notes.KEPT_FRAMES)
                region = notes.windowed(audio[offset:offset + samples], lead, frames, self.predict)
                self.assertEqual(region["contour"].shape, (frames, 2))
                # Past the first window, every frame is the whole run's, but
                # the last, whose samples run past the region's end.
                after = notes.KEPT_FRAMES
                numpy.testing.assert_allclose(region["note"][after:-1], whole["note"][base + after:base + frames - 1],
                                              rtol=0, atol=1e-6)
                # In the first window, those reading only the region's audio are too.
                first = -(-(lead - notes.OVERLAP_SAMPLES // 2) // 256)
                numpy.testing.assert_allclose(region["note"][first:after], whole["note"][base + first:base + after],
                                              rtol=0, atol=1e-6)
                # The last frame reads the region's last samples; no frame starts past them.
                last = base + frames - 1
                position = last // notes.KEPT_FRAMES * notes.WINDOW_HOP + last % notes.KEPT_FRAMES * 256
                self.assertTrue(offset + samples - 256 <= position < offset + samples, (position, offset + samples))
        # Anchor: the region windowed from its own start reads other frames.
        offset = 2 * notes.WINDOW_HOP + 5000
        alone = notes.windowed(audio[offset:offset + 3 * notes.WINDOW_HOP], notes.OVERLAP_SAMPLES // 2,
                               3 * notes.KEPT_FRAMES, self.predict)
        base = offset // notes.WINDOW_HOP * notes.KEPT_FRAMES
        self.assertGreater(numpy.abs(alone["note"][200:300] - whole["note"][base + 200:base + 300]).max(), 1)

    def test_an_offset_is_the_nearest_sample_at_the_models_rate(self) -> None:
        self.assertIsNone(notes.offset_at(None, 22050))
        self.assertEqual(notes.offset_at({"start": 720000, "end": 2160000, "rate": 48000}, 22050), 330750)
        self.assertEqual(notes.offset_at({"start": 1, "end": 2, "rate": 44100}, 22050), 0)  # half, to even
        self.assertEqual(notes.offset_at({"start": 3, "end": 4, "rate": 44100}, 22050), 2)
        self.assertEqual(notes.offset_at({"start": 7, "end": 8, "rate": 48000}, 22050), 3)

    @needs_ffmpeg
    def test_a_region_run_tells_the_program_where_the_region_lies(self) -> None:
        directory = scratch(self)
        audio = tones(directory / "tones.wav")
        told = directory / "offset"
        code, answer = run(directory, audio, "--region", "44100:176400:44100", program=fake_python(directory),
                           env={"NOTES_FAKE_OFFSET": str(told)})
        self.assertEqual(code, 0, answer)
        self.assertEqual(told.read_text(), "22050")
        code, answer = run(directory / "whole", audio, program=fake_python(directory),
                           env={"NOTES_FAKE_OFFSET": str(told)})
        self.assertEqual(code, 0, answer)
        self.assertEqual(told.read_text(), "None")


@unittest.skipUnless(BASIC_PITCH_PYTHON and Path(BASIC_PITCH_PYTHON).is_file(),
                     "set ARCHIVE_TEST_BASIC_PITCH_PYTHON to Basic Pitch's interpreter to run the model")
@needs_ffmpeg
class RealModel(unittest.TestCase):
    def test_tones_of_known_pitch_read_within_ten_cents(self) -> None:
        directory = scratch(self)
        audio = tones(directory / "tones.wav")
        code, answer = run(directory, audio, program=Path(BASIC_PITCH_PYTHON))
        self.assertEqual(code, 0, answer)
        entry = bank.Bank(directory / "bank").entry(bank.file_digest(audio), "notes-basic-pitch")
        software = {s["name"]: s["version"] for s in entry.manifest["software"]}
        self.assertEqual(software["basic-pitch"], "0.4.0")
        self.assertIn("onnxruntime", software)
        rows = json.loads(entry.file("notes").read_text())["notes"]
        report = []
        for index, (midi, off) in enumerate(TONES):
            start, end = 1.6 * index, 1.6 * index + 1.2
            # The tone's own note: the loudest at its pitch while it sounds (harmonics make others).
            mine = [r for r in rows if r[2] == midi and r[0] < end and r[1] > start]
            self.assertTrue(mine, (midi, off, [r[:5] for r in rows if r[0] < end and r[1] > start]))
            note = max(mine, key=lambda r: (r[1] - r[0]) * r[3])
            report.append(f"{midi}{off:+d}: {note[4]:+.1f}")
            self.assertLessEqual(abs(note[4] - off), 10, (midi, off, note[:5]))
            # The bends say where the note is, too: within half a bin of the truth.
            self.assertLessEqual(abs(numpy.median(note[5]) - off * 3 / 100), 0.5 + 1e-9, (midi, off))
        print("\nbasic pitch cents, true: read: " + ", ".join(report), file=sys.stderr)

    def test_a_region_is_the_whole_runs_notes_over_it(self) -> None:
        directory = scratch(self)
        audio = tones(directory / "tones.wav")
        code, answer = run(directory, audio, program=Path(BASIC_PITCH_PYTHON))
        self.assertEqual(code, 0, answer)
        whole = bank.Bank(directory / "bank").entry(bank.file_digest(audio), "notes-basic-pitch")
        start, end = 113000, 352800  # 2.56 s to 8 s, off every grid
        code, answer = run(directory / "region", audio, "--region", f"{start}:{end}:44100",
                           program=Path(BASIC_PITCH_PYTHON))
        self.assertEqual(code, 0, answer)
        region = bank.covering(bank.Bank(directory / "region" / "bank"), bank.file_digest(audio),
                               "notes-basic-pitch", start, end, 44100)
        offset = start / 44100
        within = [r for r in json.loads(whole.file("notes").read_text())["notes"] if offset <= r[0] < end / 44100]
        found = [[r[0] + offset, r[1] + offset, *r[2:]] for r in json.loads(region.file("notes").read_text())["notes"]]
        self.assertGreater(len(within), 3)
        # The tones begin after silence, so nothing before the region reaches them.
        self.assertEqual([r[2] for r in found], [r[2] for r in within])
        for mine, theirs in zip(found, within):
            self.assertAlmostEqual(mine[0], theirs[0], delta=1e-3)
            self.assertAlmostEqual(mine[4], theirs[4], delta=0.2)


if __name__ == "__main__":
    unittest.main()
