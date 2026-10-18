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

# Stand-ins for a stem separator and a tuning estimator, which the catalog
# does not ship yet: real programs the real runner runs, so the melody
# extractor reads their outputs exactly as it would read the real ones'.
STEMS = r'''
import json, shutil, sys
request = json.load(open(sys.argv[1]))
shutil.copyfile(request["parameters"]["vocals"], request["output_dir"] + "/vocals.flac")
json.dump({"contract": "archive-extractor-result/1", "outputs": {"vocals": {}}},
          open(request["output_dir"] + "/result.json", "w"))
'''
TONAL = r'''
import json, sys
request = json.load(open(sys.argv[1]))
value = request["parameters"]["tuning"]
json.dump({"contract": "archive-extractor-result/1",
           "outputs": {"tuning_frequency": {"value": float(value) if value[0].isdigit() else value}}},
          open(request["output_dir"] + "/result.json", "w"))
'''


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
        self.assertEqual(extractor.needs, [])
        self.assertEqual(extractor.optional, [melody.STEM, melody.TUNING])
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

    def test_without_a_stem_or_tuning_extractor_it_reads_nothing(self) -> None:
        catalog = bank.Catalog.load()
        for absent in ("stems", "tonal"):
            self.assertNotIn(absent, catalog.extractors)  # anchor: this test is about their absence
        self.assertEqual(catalog.needs("melody"), [])


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
            "number option": self.given(monophonic={**melody.MONOPHONIC, "minFrequency": True}),
            "unknown": {**melody.parameters(), "colour": "blue"},
        }
        for name, given in refused.items():
            with self.subTest(name), self.assertRaises(melody.MelodyError):
                melody.settings(given)

    def test_a_tuning_reference_must_be_a_frequency(self) -> None:
        self.assertEqual(melody.reference_of({"needs": {}}, 440.0), (440.0, "parameter"))
        self.assertEqual(melody.reference_of({"needs": {melody.TUNING: {"value": 442.0}}}, 440.0),
                         (442.0, melody.TUNING))
        for value in (0, -440.0, "442", True, None, 30000.0):
            with self.subTest(value=value), self.assertRaises(melody.MelodyError):
                melody.reference_of({"needs": {melody.TUNING: {"value": value}}}, 440.0)

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
        self.assertEqual(entry.scalar("source"), "mix")
        self.assertEqual((entry.scalar("reference"), entry.scalar("reference_hz")), ("parameter", 440.0))

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


@needs_ffmpeg
@needs_essentia
class OptionalNeeds(unittest.TestCase):
    """With a stem separator and a tuning estimator in the catalog, the
    melody is measured on the vocal stem against the measured reference."""

    def catalog(self, directory: Path, tuning: str, vocals: Path) -> tuple[Path, Path]:
        extractors = directory / "more"
        for name, program, outputs, parameters in (
                ("stems", STEMS, {"vocals": {"kind": "file", "media_type": "audio/flac", "suffix": ".flac"}},
                 {"vocals": str(vocals)}),
                ("tonal", TONAL, {"tuning_frequency": {"kind": "scalar", "unit": "Hz"}}, {"tuning": tuning})):
            (extractors / name).mkdir(parents=True)
            (extractors / name / "program.py").write_text(program)
            (extractors / name / "extractor.json").write_text(json.dumps(
                {"contract": "archive-extractor/1", "id": name, "version": "1",
                 "command": ["${python}", "program.py"], "accepts": ["audio/*"], "parameters": parameters,
                 "outputs": outputs}))
        config = directory / "config.json"
        config.write_text(json.dumps({"extractors": [str(bank.EXTRACTORS), str(extractors)]}))
        return extractors, config

    def test_the_vocal_stem_and_the_measured_reference_are_read(self) -> None:
        directory = scratch(self)
        # The mix holds a loud C5 and a quieter E4; the "vocal stem" holds only the E4.
        mix = support.write_wav(directory / "mix.wav", tone(2.0, midi_hz(72), 0.4) + tone(2.0, midi_hz(64), 0.15),
                                RATE)
        support.ffmpeg("-i", str(support.write_wav(directory / "vocals.wav", tone(2.0, midi_hz(64)), RATE)),
                       "-c:a", "flac", str(directory / "vocals.flac"))
        extractors, config = self.catalog(directory, "442", directory / "vocals.flac")
        code, answer = run_melody(directory, mix, "--config", str(config))
        self.assertEqual(code, 0, answer)
        self.assertEqual(list(answer["extractors"]), ["stems", "tonal", "melody"])
        catalog = bank.Catalog.load([bank.EXTRACTORS, extractors])
        self.assertEqual(catalog.needs("melody"), [melody.STEM, melody.TUNING])
        entry = entry_of(directory, mix, catalog=catalog)
        self.assertEqual(set(entry.manifest["needs"]), {melody.STEM, melody.TUNING})
        self.assertEqual(entry.scalar("source"), melody.STEM)
        self.assertEqual((entry.scalar("reference"), entry.scalar("reference_hz")), (melody.TUNING, 442.0))
        voiced = entry.series("voiced")[:, 0].astype(bool)
        self.assertGreater(float(voiced.mean()), 0.6)
        # The stem's E4, not the mix's louder C5; and against 442 Hz, E4 tuned to 440 is about 7.85 cents flat.
        self.assertLess(abs(float(numpy.median(entry.series("pitch")[voiced])) - midi_hz(64)), 1.5)
        self.assertLess(abs(float(numpy.median(entry.series("cents")[voiced])) + 7.85), 2)
        # Anchor: without them, the same mix is measured as a mix, under another key.
        self.assertEqual(run_melody(directory, mix)[0], 0)
        plain = entry_of(directory, mix)
        self.assertNotEqual(plain.manifest["key"], entry.manifest["key"])
        self.assertEqual(plain.scalar("source"), "mix")
        plain_voiced = plain.series("voiced")[:, 0].astype(bool)
        self.assertLess(abs(float(numpy.median(plain.series("pitch")[plain_voiced])) - midi_hz(72)), 2)

    def test_a_reference_that_is_not_a_frequency_fails_the_melody(self) -> None:
        directory = scratch(self)
        mix = support.write_wav(directory / "mix.wav", tone(1.0, 440.0), RATE)
        support.ffmpeg("-i", str(mix), "-c:a", "flac", str(directory / "vocals.flac"))
        _, config = self.catalog(directory, "sharp", directory / "vocals.flac")
        code, answer = run_melody(directory, mix, "--config", str(config))
        self.assertEqual(code, 1)
        self.assertEqual(answer["extractors"]["tonal"]["status"], "ran")
        self.assertEqual(answer["extractors"]["melody"]["status"], "failed")
        self.assertIn("not a frequency", answer["extractors"]["melody"]["log"])

    def test_a_failed_stem_blocks_the_melody_rather_than_measuring_the_mix(self) -> None:
        directory = scratch(self)
        mix = support.write_wav(directory / "mix.wav", tone(1.0, 440.0), RATE)
        _, config = self.catalog(directory, "440", directory / "no-such-stem.flac")
        code, answer = run_melody(directory, mix, "--config", str(config))
        self.assertEqual(code, 1)
        self.assertEqual(answer["extractors"]["stems"]["status"], "failed")
        self.assertEqual(answer["extractors"]["melody"]["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
