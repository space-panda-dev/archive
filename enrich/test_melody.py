"""The melody extractor, run by the real runner on synthetic audio (ADR 0074).

Its contour must be the bits Essentia's PredominantPitchMelodia makes of the
whole recording when it is measured whole, and the chunks it measures by
default must cover exactly the whole recording's rows.
"""

from __future__ import annotations

import io
import json
import math
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

sys.path.insert(0, str(Path(__file__).resolve().parent / "extractors" / "melody"))
import melody  # noqa: E402

RATE = melody.RATE

# MELODIA keeps a contour as melody only when its salience is near the mean
# of every contour in the audio (`voicingTolerance`, 0.2 by default). Music
# has hundreds of contours; a test tune of a few pure tones has a handful,
# and one of two equal tones can fall below the line (with the defaults, of
# two 1.5 s tones a second apart, only the first is voiced). Tests about the
# extractor rather than that threshold use the most lenient setting.
LENIENT = {"predominant": {**melody.PREDOMINANT, "voicingTolerance": "1.4"}}


def scratch(test: unittest.TestCase) -> Path:
    directory = Path(tempfile.mkdtemp(prefix="bank-melody-"))
    test.addCleanup(shutil.rmtree, directory, True)
    return directory


def tone(seconds: float, frequency: float, amplitude: float = 0.4, rate: int = RATE) -> numpy.ndarray:
    """A tone with three harmonics, as a voice or an instrument has."""
    t = numpy.arange(int(seconds * rate)) / rate
    return amplitude * sum(numpy.sin(2 * numpy.pi * h * frequency * t) / h for h in (1, 2, 3)) / 1.84


def silence(seconds: float, rate: int = RATE) -> numpy.ndarray:
    return numpy.zeros(int(seconds * rate))


def midi_hz(note: float, reference: float = 440.0) -> float:
    return reference * 2 ** ((note - 69) / 12)


def run_melody(directory: Path, audio: Path, *extra: str, target: str = "melody") -> tuple[int, dict]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = extract.main(["run", target, "--content", str(audio), "--bank", str(directory / "bank"), *extra])
    return code, json.loads(out.getvalue() or err.getvalue())


def configured(directory: Path, fields: dict, name: str = "config.json", **more) -> list[str]:
    path = directory / name
    path.write_text(json.dumps({"parameters": {"melody": fields}, **more}))
    return ["--config", str(path)]


def entry_of(directory: Path, audio: Path, fields: dict | None = None,
             catalog: bank.Catalog | None = None) -> bank.Entry:
    found = bank.Bank(directory / "bank", catalog, {"melody": fields} if fields else None).entry(
        bank.file_digest(audio), "melody")
    assert found is not None
    return found


def decoded(path: Path) -> numpy.ndarray:
    """The audio as the extractor decodes it, by ffmpeg run here."""
    raw = subprocess.run(["ffmpeg", *melody.decode_arguments(str(path))], capture_output=True, check=True).stdout
    return numpy.frombuffer(raw, dtype="<i2").astype(numpy.float32) / numpy.float32(32768)


class Manifest(unittest.TestCase):
    def test_the_manifest_declares_exactly_what_the_program_implements(self) -> None:
        extractor = bank.Catalog.load()["melody"]
        self.assertEqual(extractor.manifest["parameters"], melody.parameters())
        # It reads no feature, whatever else the catalog holds (ADR 0074: a need is fixed).
        self.assertEqual(extractor.needs, [])
        self.assertNotIn("optional", extractor.manifest)
        series = {name: out for name, out in extractor.outputs.items() if out["kind"] == "series"}
        self.assertEqual(set(series), set(melody.SERIES))
        for name, out in series.items():
            self.assertEqual(out["grid"], "melody")
            self.assertEqual(numpy.dtype(melody.SERIES[name]).itemsize, bank.DTYPES[out["dtype"]], name)
        # Every default is one the program accepts.
        chosen = melody.settings(melody.parameters())
        self.assertEqual((chosen["hop"], chosen["reference"]), (128, 440.0))
        self.assertEqual(chosen["chunk"] % 128, 0)
        self.assertEqual(chosen["margin"] % 128, 0)

class Settings(unittest.TestCase):
    def given(self, **changes) -> dict:
        return {**melody.parameters(), **changes}

    def test_what_can_vary_is_accepted(self) -> None:
        chosen = melody.settings(self.given(hop_size=512, chunk_seconds=0, reference_hz="442.5"))
        self.assertEqual((chosen["hop"], chosen["chunk"], chosen["reference"]), (512, 0, 442.5))
        # A chunk is a whole number of hops, rounded down.
        self.assertEqual(melody.settings(self.given(chunk_seconds=7, margin_seconds=1))["chunk"],
                         7 * RATE // 128 * 128)

    def test_what_it_does_not_implement_is_refused(self) -> None:
        refused = {
            "fixed": self.given(analysis_rate=22050),
            "decode": self.given(decode_arguments=["-i", "{input}"]),
            "filter": self.given(equal_loudness=False),
            "stride": self.given(hop_size=100),
            "boolean stride": self.given(hop_size=True),
            "chunk": self.given(chunk_seconds=-1),
            "fraction": self.given(chunk_seconds="1.5"),
            "margins": self.given(chunk_seconds=8, margin_seconds=5),
            "reference": self.given(reference_hz="0"),
            "reference text": self.given(reference_hz="A4"),
            "option": self.given(predominant={**melody.PREDOMINANT, "frameSize": 4096}),
            "missing option": self.given(segmentation={"minDuration": "0.1"}),
            "boolean option": self.given(predominant={**melody.PREDOMINANT, "voiceVibrato": 1}),
            "number option": self.given(predominant={**melody.PREDOMINANT, "minFrequency": True}),
            "guessed pitches": self.given(predominant={**melody.PREDOMINANT, "guessUnvoiced": True}),
            "fraction for an integer": self.given(predominant={**melody.PREDOMINANT, "numberHarmonics": "20.5"}),
            "integer as text": self.given(predominant={**melody.PREDOMINANT, "numberHarmonics": "20"}),
            "fraction for a threshold": self.given(segmentation={**melody.SEGMENTATION,
                                                                 "pitchDistanceThreshold": "60.9"}),
            "float for an integer": self.given(segmentation={**melody.SEGMENTATION, "rmsThreshold": -2.5}),
            "unknown": {**melody.parameters(), "colour": "blue"},
        }
        for name, given in refused.items():
            with self.subTest(name), self.assertRaises(melody.MelodyError):
                melody.settings(given)

    @needs_essentia
    def test_every_option_it_accepts_reaches_essentia(self) -> None:
        """Each option changed to another value of its kind is taken by the
        algorithms, so what settings() accepts never fails inside Essentia."""
        es = melody.essentia_standard()
        algorithms = {"predominant": es.PredominantPitchMelodia(), "segmentation": es.PitchContourSegmentation()}
        for group, defaults in (("predominant", melody.PREDOMINANT), ("segmentation", melody.SEGMENTATION)):
            types = {name: str(algorithms[group].paramType(name)) for name in defaults}
            self.assertEqual(melody.INTEGERS[group], {n for n, kind in types.items() if kind == "INTEGER"}, group)
            self.assertEqual({n for n, d in defaults.items() if isinstance(d, bool)},
                             {n for n, kind in types.items() if kind == "BOOL"}, group)

        def changed(group: str, defaults: dict) -> dict:
            # Within every option's range: booleans flipped unless fixed, integers
            # moved by one toward zero, reals to nine tenths as decimal text
            # (a fraction, even where the default is a whole number).
            return {name: (default if name in melody.FIXED_OPTIONS else not default) if isinstance(default, bool)
                    else default - (1 if default > 0 else -1) if name in melody.INTEGERS[group]
                    else f"{float(default) * 0.9:.4f}" for name, default in defaults.items()}
        chosen = melody.settings(self.given(predominant=changed("predominant", melody.PREDOMINANT),
                                            segmentation=changed("segmentation", melody.SEGMENTATION)))
        self.assertTrue(chosen["predominant"]["voiceVibrato"])
        self.assertEqual(chosen["predominant"]["numberHarmonics"], 19)  # anchor: the values changed
        self.assertEqual(chosen["predominant"]["magnitudeCompression"], 0.9)
        self.assertIsInstance(chosen["predominant"]["harmonicWeight"], float)
        melody.Melody(es, chosen)

    def test_the_decoder_must_say_its_version(self) -> None:
        directory = scratch(self)
        for name, script, expected in (("good", "echo 'ffmpeg version 7.1.1 Copyright'", "7.1.1"),
                                       ("silent", "exit 0", None), ("other", "echo 'avconv 12'", None)):
            program = directory / name
            program.write_text(f"#!/bin/sh\n{script}\n")
            program.chmod(0o755)
            with self.subTest(name):
                if expected is None:
                    with self.assertRaisesRegex(melody.MelodyError, "does not say"):
                        melody.ffmpeg_version(str(program))
                else:
                    self.assertEqual(melody.ffmpeg_version(str(program)), expected)
        with self.assertRaisesRegex(melody.MelodyError, "could not run"):
            melody.ffmpeg_version(str(directory / "no-ffmpeg"))

    def test_essentia_other_than_the_pinned_build_is_refused(self) -> None:
        with patch.object(melody.importlib.metadata, "version", return_value="2.1b5"):
            with self.assertRaisesRegex(melody.MelodyError, "2.1b5"):
                melody.essentia_standard()


class Chunks(unittest.TestCase):
    def test_chunks_keep_exactly_the_whole_recordings_rows(self) -> None:
        """For lengths around the chunk and margin edges and blocks that split
        them, the kept rows are 0 to ceil(n / hop) with none twice, and each
        chunk's audio is the recording's samples from its start."""
        hop, chunk, margin = 4, 40, 8
        generator = numpy.random.default_rng(3)
        for length in (1, 3, 4, 5, 39, 40, 41, 47, 48, 49, 79, 80, 81, 88, 89, 200, 1001):
            audio = generator.uniform(-1, 1, length).astype(numpy.float32)
            rows = -(-length // hop) + 1
            for block in (1, 7, 40, 5000):
                with self.subTest(length=length, block=block):
                    kept = 0
                    pieces = list(melody.chunks([audio[i:i + block] for i in range(0, length, block)],
                                                chunk, margin, hop))
                    for number, (start, samples, first, count) in enumerate(pieces):
                        self.assertEqual(first, kept)
                        self.assertEqual(start % hop, 0)
                        self.assertEqual(start, max(0, first * hop - margin))
                        self.assertEqual(samples.tobytes(), audio[start:start + len(samples)].tobytes())
                        local = -(-len(samples) // hop) + 1
                        last = number == len(pieces) - 1
                        self.assertEqual(count is None, last)
                        taken = local - (first - start // hop) if last else count
                        self.assertLessEqual(first - start // hop + taken, local)
                        # The audio runs a margin past the kept rows, or to the recording's end.
                        end = length if last else min(length, (first + count) * hop + margin)
                        self.assertEqual(start + len(samples), end)
                        kept += taken
                    self.assertEqual(kept, rows)
        self.assertEqual(list(melody.chunks([], 40, 8, 4)), [])

    def test_no_chunking_is_one_piece(self) -> None:
        audio = numpy.arange(1000, dtype=numpy.float32)
        pieces = list(melody.chunks([audio[:300], audio[300:]], 0, 0, 128))
        self.assertEqual(len(pieces), 1)
        self.assertEqual(pieces[0][0:1] + pieces[0][2:], (0, 0, None))
        self.assertEqual(pieces[0][1].tobytes(), audio.tobytes())


class Intonation(unittest.TestCase):
    def test_cents_and_note_numbers(self) -> None:
        pitch = numpy.array([0, 440, 466.1638, 452.893, 427.474, 880, 441], dtype=numpy.float32)
        voiced, midi, cents = melody.intonation(pitch, 440.0)
        self.assertEqual(voiced.tolist(), [0, 1, 1, 1, 1, 1, 1])
        numpy.testing.assert_allclose(midi, [0, 69, 70, 69.5, 68.5, 81, 69.0393], atol=1e-3)
        self.assertEqual(cents[0], 0)
        numpy.testing.assert_allclose(numpy.abs(cents[[1, 2, 5]]), 0, atol=0.01)
        numpy.testing.assert_allclose(numpy.abs(cents[[3, 4]]), 50, atol=0.01)
        self.assertAlmostEqual(float(cents[6]), 3.93, places=2)
        # Against a reference of 442 Hz, 440 Hz is about 7.85 cents flat.
        self.assertAlmostEqual(float(melody.intonation(pitch, 442.0)[2][1]), -7.85, places=2)


@needs_ffmpeg
@needs_essentia
class Melody(unittest.TestCase):
    def test_whole_recording_is_the_bits_essentia_measures(self) -> None:
        directory = scratch(self)
        rate = 48000
        notes = (64, 67, 71, 72, 71, 67)
        samples = numpy.concatenate([tone(0.5, midi_hz(n), rate=rate) for n in notes] + [silence(0.3, rate)])
        source = support.write_wav(directory / "tune.wav", samples, rate)
        support.ffmpeg("-i", str(source), "-ac", "2", "-c:a", "flac", str(directory / "tune.flac"))
        source = directory / "tune.flac"
        fields = {"chunk_seconds": 0}
        code, answer = run_melody(directory, source, *configured(directory, fields))
        self.assertEqual(code, 0, answer)
        entry = entry_of(directory, source, fields)
        es = melody.essentia_standard()
        audio = decoded(source)
        pitch, confidence = es.PredominantPitchMelodia()(es.EqualLoudness()(audio))
        # Anchor: the contour says something, so equality is not two empty arrays.
        self.assertGreater(float(numpy.mean(numpy.asarray(pitch) > 0)), 0.6)
        self.assertEqual(entry.series("pitch")[:, 0].tobytes(), numpy.asarray(pitch, dtype="<f4").tobytes())
        self.assertEqual(entry.series("confidence")[:, 0].tobytes(),
                         numpy.asarray(confidence, dtype="<f4").tobytes())
        self.assertEqual(entry.scalar("samples"), len(audio))
        self.assertEqual(entry.series("pitch").shape, (math.ceil(len(audio) / 128) + 1, 1))
        self.assertEqual(entry.grid("pitch"), {"sample_rate": RATE, "hop": 128, "frame": 2048, "first_centre": 0})
        self.assertEqual(entry.scalar("reference_hz"), 440.0)

    def test_chunks_follow_the_whole_recording(self) -> None:
        """A tune measured in chunks of 4 s with 1 s margins agrees with the
        whole measure on nearly every row, and exactly in row count."""
        directory = scratch(self)
        rng = numpy.random.default_rng(5)
        notes = rng.choice([60, 62, 64, 65, 67, 69, 71, 72], 26)
        samples = numpy.concatenate([tone(0.45, midi_hz(n)) for n in notes]) + 0.02 * rng.standard_normal(
            int(0.45 * RATE) * len(notes))
        source = support.write_wav(directory / "tune.wav", samples, RATE)
        whole, chunked = {"chunk_seconds": 0, **LENIENT}, {"chunk_seconds": 4, "margin_seconds": 1, **LENIENT}
        self.assertEqual(run_melody(directory, source, *configured(directory, whole, "a.json"))[0], 0)
        self.assertEqual(run_melody(directory, source, *configured(directory, chunked, "b.json"))[0], 0)
        a, b = entry_of(directory, source, whole), entry_of(directory, source, chunked)
        self.assertNotEqual(a.manifest["key"], b.manifest["key"])  # anchor: two measurements, not one twice
        pa, pb = a.series("pitch")[:, 0], b.series("pitch")[:, 0]
        self.assertEqual(pa.shape, pb.shape)
        self.assertGreater(float(numpy.mean(pa > 0)), 0.8)
        self.assertGreater(float(numpy.mean(pa == pb)), 0.95)
        # No note is found twice where chunks meet. Note segmentation weighs
        # each piece's loudness against that piece's own, so a repeated note
        # can be split or joined differently in chunks than whole.
        onsets = [n["start"] for n in b.items("notes")]
        self.assertEqual(onsets, sorted(set(onsets)))
        self.assertLessEqual(abs(len(onsets) - len(a.items("notes"))), 2)
        self.assertGreater(len(onsets), 20)

    def test_a_tone_is_on_time_in_tune_and_measured_in_cents(self) -> None:
        directory = scratch(self)
        sharp = midi_hz(69.4)  # 40 cents above A4
        samples = numpy.concatenate([silence(1.0), tone(1.5, 440.0), silence(0.5), tone(1.5, sharp), silence(0.5)])
        source = support.write_wav(directory / "tones.wav", samples, RATE)
        code, answer = run_melody(directory, source, *configured(directory, LENIENT))
        self.assertEqual(code, 0, answer)
        entry = entry_of(directory, source, LENIENT)
        voiced = entry.series("voiced")[:, 0].astype(bool)
        seconds = numpy.arange(len(voiced)) * 128 / RATE
        first = float(seconds[voiced][0])
        self.assertLess(abs(first - 1.0), 0.06, first)
        in_a = voiced & (seconds > 1.2) & (seconds < 2.3)
        in_sharp = voiced & (seconds > 3.2) & (seconds < 4.3)
        self.assertGreater(int(in_a.sum()), 300)
        self.assertGreater(int(in_sharp.sum()), 300)
        self.assertLess(abs(float(numpy.median(entry.series("pitch")[in_a])) - 440.0), 1.0)
        self.assertLess(abs(float(numpy.median(entry.series("midi")[in_a])) - 69), 0.02)
        self.assertLess(abs(float(numpy.median(entry.series("cents")[in_a]))), 2)
        self.assertLess(abs(float(numpy.median(entry.series("cents")[in_sharp])) - 40), 2)
        # Where nothing sounds, every series is 0.
        for name in ("pitch", "confidence", "midi", "cents"):
            self.assertTrue(numpy.all(entry.series(name)[~voiced] == 0), name)
        self.assertAlmostEqual(entry.scalar("voiced_fraction"), float(voiced.mean()))
        self.assertEqual([round(n["value"]) for n in entry.items("notes")], [69, 69])

    def test_a_clipped_melody_over_a_chord_is_the_melody(self) -> None:
        directory = scratch(self)
        notes = (72, 76, 79, 76)
        melody_line = numpy.concatenate([tone(0.6, midi_hz(n), amplitude=1.6) for n in notes])
        t = numpy.arange(len(melody_line)) / RATE
        chord = sum(0.08 * numpy.sin(2 * numpy.pi * midi_hz(n) * t) for n in (48, 52, 55))
        samples = numpy.clip(melody_line + chord, -1, 1)
        self.assertGreater(float(numpy.mean(numpy.abs(samples) >= 1)), 0.1)  # anchor: it is clipped
        source = support.write_wav(directory / "clipped.wav", samples, RATE)
        self.assertEqual(run_melody(directory, source, *configured(directory, LENIENT))[0], 0)
        entry = entry_of(directory, source, LENIENT)
        self.assertEqual([round(n["value"]) for n in entry.items("notes")], list(notes))

    def test_silence_has_no_melody_and_noise_stays_finite(self) -> None:
        directory = scratch(self)
        quiet = support.write_wav(directory / "quiet.wav", silence(2.0), RATE)
        noise = support.noise_flac(directory / "noise.flac", RATE, "3")
        for source in (quiet, noise):
            with self.subTest(source=source.name):
                code, answer = run_melody(directory, source)
                self.assertEqual(code, 0, answer)
                entry = entry_of(directory, source)
                for name in melody.SERIES:
                    self.assertTrue(numpy.isfinite(entry.series(name)).all(), name)
        # Noise alone is mostly "voiced" (about 80 % of rows): MELODIA's
        # voicing is relative to the contours present, and in noise every
        # contour is noise; its confidence scales with level, so it does not
        # tell noise from a quiet tone either. Recorded, not hidden.
        self.assertGreater(entry_of(directory, noise).scalar("voiced_fraction"), 0.5)
        self.assertEqual(entry_of(directory, quiet).scalar("voiced_fraction"), 0)
        self.assertEqual(entry_of(directory, quiet).items("notes"), [])

    def test_what_else_is_installed_changes_nothing(self) -> None:
        """A stem separator in the catalog that cannot run here (its program
        is not configured) neither blocks the melody nor changes its key.
        The shipped `stems` is one; this adds another, so the test does not
        rest on what ships."""
        directory = scratch(self)
        source = support.write_wav(directory / "tone.wav", tone(1.0, 440.0), RATE)
        self.assertEqual(run_melody(directory, source)[0], 0)
        plain = entry_of(directory, source)
        more = directory / "more" / "separator"
        more.mkdir(parents=True)
        (more / "stems.py").write_text("raise SystemExit(1)\n")
        (more / "extractor.json").write_text(json.dumps(
            {"contract": "archive-extractor/1", "id": "separator", "version": "1",
             "command": ["${separator-python}", "stems.py"], "accepts": ["audio/*", "video/*"],
             "outputs": {"vocals": {"kind": "file", "media_type": "audio/flac", "suffix": ".flac"}}}))
        catalog = bank.Catalog.load([bank.EXTRACTORS, directory / "more"])
        self.assertIn("separator", catalog.extractors)  # anchor: it is installed
        config = directory / "config.json"
        config.write_text(json.dumps({"extractors": [str(bank.EXTRACTORS), str(directory / "more")]}))
        code, answer = run_melody(directory, source, "--config", str(config))
        self.assertEqual((code, list(answer["extractors"])), (0, ["melody"]), answer)
        self.assertEqual(answer["extractors"]["melody"]["status"], "hit")
        self.assertEqual(catalog.keys(plain.manifest["content"], ["melody"])["melody"], plain.manifest["key"])

    def test_a_second_run_runs_nothing(self) -> None:
        directory = scratch(self)
        source = support.write_wav(directory / "tone.wav", tone(1.0, 440.0), RATE)
        self.assertEqual(run_melody(directory, source)[0], 0)

        def bomb(*_args, **_kwargs):
            raise AssertionError("ran an extractor whose entry is in the bank")
        with patch.object(extract, "run_program", bomb):
            code, answer = run_melody(directory, source)
        self.assertEqual((code, answer["extractors"]["melody"]["status"]), (0, "hit"))

    def test_audio_with_no_samples_fails_and_files_nothing(self) -> None:
        directory = scratch(self)
        code, answer = run_melody(directory, support.empty_wav(directory / "empty.wav"))
        self.assertEqual(code, 1)
        self.assertIn("no samples", answer["extractors"]["melody"]["log"])
        self.assertEqual(list((directory / "bank").glob("sha256/*/*/*/bank/melody/*/entry.json")), [])

    def test_undecodable_audio_and_a_missing_decoder_fail(self) -> None:
        directory = scratch(self)
        broken = directory / "broken.wav"
        broken.write_text("not audio at all\n")
        code, answer = run_melody(directory, broken)
        self.assertEqual(code, 1)
        self.assertIn("could not decode", answer["extractors"]["melody"]["log"])
        with self.assertRaisesRegex(melody.MelodyError, "could not run"):
            list(melody.decoded_blocks(str(broken), ffmpeg=str(directory / "no-ffmpeg")))

    def test_settings_it_does_not_implement_are_refused_not_measured(self) -> None:
        directory = scratch(self)
        source = support.write_wav(directory / "tone.wav", tone(1.0, 440.0), RATE)
        code, answer = run_melody(directory, source, *configured(directory, {"hop_size": 100}))
        self.assertEqual(code, 1)
        self.assertIn("hop_size", answer["extractors"]["melody"]["log"])

    def test_the_stride_thins_the_rows(self) -> None:
        directory = scratch(self)
        source = support.write_wav(directory / "tone.wav", numpy.concatenate([silence(0.5), tone(2.0, 440.0)]), RATE)
        fields = {"hop_size": 512}
        self.assertEqual(run_melody(directory, source, *configured(directory, fields))[0], 0)
        entry = entry_of(directory, source, fields)
        self.assertEqual(entry.series("pitch").shape, (math.ceil(int(2.5 * RATE) / 512) + 1, 1))
        self.assertEqual(entry.grid("pitch")["hop"], 512)
        voiced = entry.series("voiced")[:, 0].astype(bool)
        self.assertLess(abs(float(numpy.median(entry.series("pitch")[voiced])) - 440.0), 1.0)


if __name__ == "__main__":
    unittest.main()
