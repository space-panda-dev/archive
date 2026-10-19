"""The melody extractor, run by the real runner on synthetic audio (ADR 0074).

Its contour must be the bits Essentia's PredominantPitchMelodia makes of the
whole recording when it is measured whole, and the chunks it measures by
default must cover exactly the whole recording's rows.
"""

from __future__ import annotations

import io
import json
import math
import os
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
        # It prefers a vocal stem, in finer bins, where the bank holds one (never making it):
        # the 4-stem separator's, then the 6-stem separator's, then the mix.
        self.assertEqual(extractor.prefers, [{"input": feature, "parameters": {"bin_cents": 2},
                                              "at_least": {"voiced_fraction": "0.2"}}
                                             for feature in ("stems/vocals", "stems-6s/vocals")])
        self.assertEqual(melody.settings({**melody.parameters(), **extractor.prefers[0]["parameters"]})
                         ["predominant"]["binResolution"], 2.0)
        self.assertEqual(melody.settings(melody.parameters())["predominant"]["binResolution"], 5.0)
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
        chosen = melody.settings(self.given(hop_size=512, chunk_seconds=0, reference_hz="442.5",
                                            voicing_gate={"floor_db": "-70.5", "range_db": "0",
                                                          "reach_seconds": "0.5"}))
        self.assertEqual((chosen["hop"], chosen["chunk"], chosen["reference"]), (512, 0, 442.5))
        self.assertEqual((chosen["gate_floor"], chosen["gate_range"]), (-70.5, 0.0))
        self.assertEqual(chosen["reach"], round(0.5 * RATE / 512))
        self.assertEqual(melody.settings(self.given())["reach"], round(RATE / 128))
        for resolution in (1, "2.5", 10):
            self.assertEqual(melody.settings(self.given(bin_cents=resolution))["predominant"]["binResolution"],
                             float(resolution))
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
            "bins too fine": self.given(bin_cents="0.5"),
            "bins too coarse": self.given(bin_cents=11),
            "boolean bins": self.given(bin_cents=True),
            "bins among MELODIA's options": self.given(predominant={**melody.PREDOMINANT, "binResolution": 2}),
            "gate fields": self.given(voicing_gate={"floor_db": "-60", "range_db": "35"}),
            "gate number": self.given(voicing_gate={**melody.GATE, "floor_db": -60}),
            "gate above full scale": self.given(voicing_gate={**melody.GATE, "floor_db": "6"}),
            "gate below the level floor": self.given(voicing_gate={**melody.GATE, "floor_db": "-130"}),
            "negative range": self.given(voicing_gate={**melody.GATE, "range_db": "-5"}),
            "gate text": self.given(voicing_gate={**melody.GATE, "floor_db": "loud"}),
            "reach number": self.given(voicing_gate={**melody.GATE, "reach_seconds": 1}),
            "negative reach": self.given(voicing_gate={**melody.GATE, "reach_seconds": "-1"}),
            "reach too far": self.given(voicing_gate={**melody.GATE, "reach_seconds": "11"}),
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


class Pieces(unittest.TestCase):
    def check(self, measured: numpy.ndarray, length: int, chunk: int, margin: int, hop: int,
              audio: numpy.ndarray, block: int) -> list:
        """Every measured row is kept once and no other; each piece's audio is
        the recording's samples from its start, within its run of rows, and
        holds the rows it keeps."""
        pieces = melody.plan(measured, length, chunk, margin, hop)
        cut = list(melody.cut([audio[i:i + block] for i in range(0, length, block)], pieces, length))
        self.assertEqual(len(cut), len(pieces))
        kept = numpy.zeros(len(measured), dtype=int)
        runs = melody.spans(measured)
        for (start, end, first, count), samples in zip(pieces, cut):
            self.assertEqual(start % hop, 0)
            self.assertGreater(count, 0)
            self.assertEqual(samples.tobytes(), audio[start:end].tobytes())
            self.assertGreater(end, start)
            low, high = next((a, b) for a, b in runs if a <= first < b)
            self.assertLessEqual(first + count, high)
            self.assertGreaterEqual(start, min(low * hop, (end - 1) // hop * hop))
            self.assertLessEqual(end, min(length, high * hop))
            local = -(-len(samples) // hop) + 1
            self.assertLessEqual(first - start // hop + count, local)
            self.assertGreaterEqual(first - start // hop, 0)
            kept[first:first + count] += 1
        self.assertEqual(kept.tolist(), measured.astype(int).tolist())
        return pieces

    def test_every_measured_row_is_kept_once_from_its_own_audio(self) -> None:
        """For lengths around the chunk and margin edges, runs of measured
        rows anywhere (the last rows, past the last sample, included), and
        blocks that split them."""
        hop, chunk, margin = 4, 40, 8
        generator = numpy.random.default_rng(3)
        for length in (1, 3, 4, 5, 39, 40, 41, 47, 48, 49, 79, 80, 81, 88, 89, 200, 1001):
            audio = generator.uniform(-1, 1, length).astype(numpy.float32)
            rows = -(-length // hop) + 1
            masks = {"all": numpy.ones(rows, bool), "last": numpy.arange(rows) == rows - 1,
                     "random": generator.uniform(size=rows) < 0.4}
            for name, measured in masks.items():
                for block in (1, 7, 40, 5000):
                    with self.subTest(length=length, rows=name, block=block):
                        self.check(measured, length, chunk, margin, hop, audio, block)
                        self.check(measured, length, 0, 0, hop, audio, block)

    def test_every_row_measured_is_the_whole_recording_in_chunks(self) -> None:
        hop, chunk, margin = 4, 40, 8
        audio = numpy.arange(1001, dtype=numpy.float32)
        rows = -(-1001 // hop) + 1
        pieces = melody.plan(numpy.ones(rows, bool), 1001, chunk, margin, hop)
        self.assertEqual([p[0] for p in pieces], [max(0, i * chunk - margin) for i in range(len(pieces))])
        self.assertEqual(pieces[-1][1], 1001)
        whole = melody.plan(numpy.ones(rows, bool), 1001, 0, 0, hop)
        self.assertEqual(whole, [(0, 1001, 0, rows)])
        self.assertEqual(melody.plan(numpy.zeros(rows, bool), 1001, chunk, margin, hop), [])
        self.assertEqual(list(melody.cut([audio], [], 1001)), [])

    def test_a_decode_that_differs_from_the_first_is_refused(self) -> None:
        audio = numpy.zeros(1000, dtype=numpy.float32)
        pieces = melody.plan(numpy.ones(-(-1000 // 4) + 1, bool), 1000, 40, 8, 4)
        with self.assertRaisesRegex(melody.MelodyError, "not the 1000"):
            list(melody.cut([audio[:900]], pieces, 1000))
        with self.assertRaisesRegex(melody.MelodyError, "not the 1000"):
            list(melody.cut([audio, audio[:5]], pieces, 1000))

    def test_reach_and_spans(self) -> None:
        rows = numpy.zeros(20, bool)
        rows[[3, 10, 11, 19]] = True
        self.assertEqual(melody.spans(rows), [(3, 4), (10, 12), (19, 20)])
        self.assertEqual(melody.spans(melody.reach(rows, 2)), [(1, 6), (8, 14), (17, 20)])
        self.assertEqual(melody.spans(melody.reach(rows, 3)), [(0, 15), (16, 20)])  # runs that meet join
        self.assertEqual(melody.reach(rows, 0).tolist(), rows.tolist())
        self.assertEqual(melody.spans(numpy.zeros(4, bool)), [])


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


class Levels(unittest.TestCase):
    def test_a_rows_level_is_its_centred_frame_however_the_blocks_fall(self) -> None:
        generator = numpy.random.default_rng(7)
        audio = (generator.standard_normal(10_000) * 0.1).astype(numpy.float32)
        audio[4000:7000] = 0  # a silent stretch, floored rather than -inf
        hop = 128
        rows = -(-len(audio) // hop) + 1
        padded = numpy.concatenate([numpy.zeros(1024), audio.astype(numpy.float64), numpy.zeros(1024 + hop)])
        expected = numpy.array([10 * numpy.log10(max(numpy.mean(padded[k * hop:k * hop + 2048] ** 2), 1e-12))
                                for k in range(rows)])
        for block in (1, 77, 128, 4096, 20_000):
            with self.subTest(block=block):
                energy = melody.Energy(hop)
                passed = list(energy.tap(audio[i:i + block] for i in range(0, len(audio), block)))
                self.assertEqual(numpy.concatenate(passed).tobytes(), audio.tobytes())  # the blocks pass unchanged
                levels = melody.row_levels(energy.hops(), rows, hop)
                self.assertEqual(len(energy.hops()), -(-len(audio) // hop))
                numpy.testing.assert_allclose(levels, numpy.maximum(expected, melody.LEVEL_FLOOR_DB), atol=1e-6)
        self.assertEqual(float(levels[5504 // hop]), melody.LEVEL_FLOOR_DB)  # anchor: the silence is floored
        self.assertEqual(melody.Energy(hop).hops().size, 0)


class Gate(unittest.TestCase):
    def test_a_contour_is_kept_whole_when_half_its_rows_reach_the_gate(self) -> None:
        level = numpy.full(100, -80.0)
        level[10:20] = [-10, -12, -14, -16, -20, -30, -50, -60, -70, -78]  # a note and its tail
        level[30:40] = [-80] * 9 + [-10]  # noise running into a note's frame
        level[40:50] = -50.0  # bleed: a contour that never rises
        level[70:72] = -5.0
        pitch = numpy.zeros(100)
        pitch[10:20] = 440 * 2 ** (numpy.arange(10) * 0.5 / 1200)  # a slow glide stays one contour
        pitch[40:50] = 300.0
        pitch[60:65] = 200.0
        pitch[30:40] = 250.0
        # Noise run on from the note without a gap: a jump of a semitone is another contour.
        pitch[20:30] = 440 * 2 ** (1 / 12)
        voiced = pitch > 0
        loud, threshold = melody.loudness(level, floor_db=-60.0, range_db=35.0)
        kept = melody.gate(pitch, level, threshold, step_cents=80)
        self.assertEqual(loud, float(numpy.percentile(level, 99)))
        self.assertAlmostEqual(threshold, loud - 35)
        self.assertGreater(threshold, -50)  # anchor: the relative gate is the higher here
        self.assertTrue(kept[10:20].all())  # the tail, below the gate, stays with its note
        self.assertFalse(kept[20:30].any())  # what ran on after it does not
        self.assertFalse(kept[30:40].any())  # one loud row does not keep a contour
        self.assertFalse(kept[40:50].any())
        self.assertFalse(kept[60:65].any())  # voiced in silence
        self.assertFalse(kept[70:72].any())  # loud, but MELODIA did not voice it
        # Where the recording is quiet throughout, the floor is the gate.
        loud, threshold = melody.loudness(level - 30, floor_db=-60.0, range_db=35.0)
        kept = melody.gate(pitch, level - 30, threshold, step_cents=80)
        self.assertEqual(threshold, -60.0)
        self.assertTrue(kept[10:20].all())
        self.assertFalse(kept[40:50].any())
        self.assertTrue(numpy.array_equal(kept, kept & voiced))
        self.assertEqual(melody.runs(numpy.array([100, 100, 0, 100, 200, 201]), 80), [(0, 2), (3, 4), (4, 6)])
        self.assertEqual(melody.runs(numpy.zeros(3), 80), [])


@needs_essentia
class Notes(unittest.TestCase):
    def test_a_run_too_short_to_value_is_no_note(self) -> None:
        es = melody.essentia_standard()
        measure = melody.Melody(es, melody.settings(melody.parameters()))
        audio = (0.1 * numpy.random.default_rng(0).standard_normal(2 * RATE)).astype(numpy.float32)
        rows = -(-len(audio) // 128) + 1
        for length, expected in ((1, []), (2, []), (40, [69])):
            pitch = numpy.zeros(rows, dtype=numpy.float32)
            pitch[100:100 + length] = 440
            with self.subTest(rows=length):
                # Anchor: Essentia itself returns a note for the run, valued -inf or NaN when short.
                values = es.PitchContourSegmentation(hopSize=128, tuningFrequency=440)(pitch, audio)[2]
                self.assertEqual(len(values), 1)
                self.assertEqual(bool(numpy.isfinite(values[0])), bool(expected))
                self.assertEqual([round(n[2]) for n in measure.notes(pitch, audio)], expected)
        self.assertEqual(measure.notes(numpy.zeros(rows, dtype=numpy.float32), audio), [])


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
        pitch, confidence = es.PredominantPitchMelodia(binResolution=melody.parameters()["bin_cents"])(
            es.EqualLoudness()(audio))
        # Anchor: the contour says something, so equality is not two empty arrays.
        self.assertGreater(float(numpy.mean(numpy.asarray(pitch) > 0)), 0.6)
        # A loud tune with nothing under it: the gate keeps every voiced run,
        # so the bits are MELODIA's.
        self.assertEqual(entry.scalar("voiced_fraction"), entry.scalar("melodia_voiced_fraction"))
        self.assertEqual(entry.scalar("measured_fraction"), 1.0)  # its 0.3 s of silence is within reach
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

    def test_cents_are_resolved_finer_than_ten(self) -> None:
        """A tone 13 cents sharp reads 13 within 1.5 cents in the 2-cent bins
        the vocal stem is measured in; in the default 5-cent bins it reads 15,
        and at Essentia's default of 10, 10 or 20."""
        directory = scratch(self)
        source = support.write_wav(directory / "sharp.wav", tone(2.0, midi_hz(69.13)), RATE)
        for bins, close in ((2, True), (5, False), (10, False)):
            fields = {**LENIENT, "bin_cents": bins}
            with self.subTest(bins=bins):
                self.assertEqual(run_melody(directory, source, *configured(directory, fields))[0], 0)
                entry = entry_of(directory, source, fields)
                voiced = entry.series("voiced")[:, 0].astype(bool)
                self.assertGreater(int(voiced.sum()), 400)
                error = abs(float(numpy.median(entry.series("cents")[voiced])) - 13)
                self.assertEqual(error < 1.5, close, error)

    def test_the_gate_drops_a_noise_floor_melodia_voices_and_its_notes(self) -> None:
        """Tones at about -16 dBFS with white noise at about -55 dBFS between
        and under them: MELODIA voices most of the noise; the gate keeps the
        tones, drops the noise, and the notes are the tones'."""
        directory = scratch(self)
        rng = numpy.random.default_rng(11)
        parts = [silence(1.0), tone(1.0, 440.0, 0.35), silence(1.5), tone(1.0, midi_hz(72), 0.35), silence(1.5)]
        samples = numpy.concatenate(parts)
        samples = samples + 0.0018 * rng.standard_normal(len(samples))
        source = support.write_wav(directory / "room.wav", samples, RATE)
        code, answer = run_melody(directory, source, *configured(directory, LENIENT))
        self.assertEqual(code, 0, answer)
        entry = entry_of(directory, source, LENIENT)
        seconds = numpy.arange(entry.series("voiced").shape[0]) * 128 / RATE
        noise = ((seconds > 2.2) & (seconds < 3.3)) | (seconds > 4.7)
        tones = ((seconds > 1.1) & (seconds < 1.9)) | ((seconds > 3.6) & (seconds < 4.4))
        voiced = entry.series("voiced")[:, 0].astype(bool)
        es = melody.essentia_standard()
        raw, _ = es.PredominantPitchMelodia(**melody.settings({**melody.parameters(), **LENIENT})["predominant"],
                                            hopSize=128)(es.EqualLoudness()(decoded(source)))
        raw = numpy.asarray(raw)[:len(seconds)] > 0
        self.assertGreater(float(raw[noise].mean()), 0.5)  # anchor: MELODIA voices the noise
        self.assertGreater(entry.scalar("melodia_voiced_fraction"), entry.scalar("voiced_fraction"))
        self.assertLess(float(voiced[noise].mean()), 0.05)
        self.assertGreater(float(voiced[tones].mean()), 0.95)
        level = entry.series("level_db")[:, 0]
        self.assertLess(abs(float(numpy.median(level[noise])) + 55), 2)
        self.assertLess(abs(entry.scalar("loud_db") + 16), 3)
        self.assertEqual(entry.scalar("gate_db"), max(-60.0, entry.scalar("loud_db") - 35))
        self.assertEqual([round(n["value"]) for n in entry.items("notes")], [69, 72])
        for name in ("pitch", "confidence", "midi", "cents"):
            self.assertTrue(numpy.all(entry.series(name)[~voiced] == 0), name)

    def test_levels_do_not_depend_on_the_chunks(self) -> None:
        directory = scratch(self)
        rng = numpy.random.default_rng(2)
        samples = numpy.concatenate([tone(2.0, 330.0), 0.01 * rng.standard_normal(int(2.3 * RATE)), tone(1.7, 440.0)])
        source = support.write_wav(directory / "take.wav", samples, RATE)
        whole, chunked = {"chunk_seconds": 0}, {"chunk_seconds": 2, "margin_seconds": 0}
        self.assertEqual(run_melody(directory, source, *configured(directory, whole, "a.json"))[0], 0)
        self.assertEqual(run_melody(directory, source, *configured(directory, chunked, "b.json"))[0], 0)
        a, b = entry_of(directory, source, whole), entry_of(directory, source, chunked)
        self.assertNotEqual(a.manifest["key"], b.manifest["key"])  # anchor: two measurements
        self.assertEqual(a.series("level_db").tobytes(), b.series("level_db").tobytes())
        audio = decoded(source).astype(numpy.float64)
        row = 100
        frame = numpy.concatenate([numpy.zeros(1024), audio])[row * 128:row * 128 + 2048]
        self.assertAlmostEqual(float(a.series("level_db")[row, 0]), 10 * math.log10(numpy.mean(frame ** 2)), places=4)

    def test_rows_out_of_the_gates_reach_are_not_measured(self) -> None:
        """Two tones with 6 s of a quiet floor (about -75 dBFS, under the gate)
        between them: MELODIA is given only the tones and a second either
        side, and the tones come out as they do when every row is measured."""
        directory = scratch(self)
        rng = numpy.random.default_rng(4)
        samples = numpy.concatenate([tone(1.5, 440.0), silence(6.0), tone(1.5, midi_hz(72))])
        samples = samples + 0.0002 * rng.standard_normal(len(samples))
        source = support.write_wav(directory / "two.wav", samples, RATE)
        es = melody.essentia_standard()
        given: list[int] = []
        measure = melody.Melody.measure

        def counted(self, audio):
            given.append(len(audio))
            return measure(self, audio)

        results = {}
        everything = {**melody.GATE, "floor_db": "-120", "range_db": "120"}
        for name, fields in (("gated", {}), ("everything", {"voicing_gate": everything})):
            out = directory / name
            out.mkdir()
            request = {"parameters": {**melody.parameters(), **LENIENT, **fields},
                       "input": {"path": str(source)}, "output_dir": str(out)}
            given.clear()
            with patch.object(melody.Melody, "measure", counted):
                result = melody.run(request, es)
            results[name] = (result, numpy.fromfile(out / "pitch.f32", dtype="<f4"), sum(given))
        result, pitch, analysed = results["gated"]
        _, everywhere, all_analysed = results["everything"]
        n = len(decoded(source))
        self.assertEqual(all_analysed, n)  # anchor: with every row in reach, all of it is measured
        self.assertLess(analysed, n - 3.9 * RATE)  # the floor between, less a second of reach either side
        self.assertGreater(analysed, 4.9 * RATE)  # the tones and their reach
        rows = len(pitch)
        self.assertAlmostEqual(result["outputs"]["measured_fraction"]["value"], (n - 4 * RATE) / 128 / rows, delta=0.03)
        seconds = numpy.arange(rows) * 128 / RATE
        middle = (seconds > 2.6) & (seconds < 6.4)
        self.assertTrue(numpy.all(pitch[middle] == 0))
        tones = ((seconds > 0.2) & (seconds < 1.3)) | ((seconds > 7.7) & (seconds < 8.8))
        self.assertGreater(float(numpy.mean(pitch[tones] > 0)), 0.95)
        self.assertGreater(float(numpy.mean(pitch[tones] == everywhere[tones])), 0.95)

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
class VocalStem(unittest.TestCase):
    """The sung line is measured on the vocal stem as content (ADR 0075):
    `--from stems/vocals`, with the separator's model replaced by the fixed
    gains of `test_stems` (vocals are 0.4 of the mix) and everything after
    the model the real stems writer."""

    def test_the_melody_of_the_vocal_stem_is_filed_with_the_recording(self) -> None:
        import test_stems
        directory = scratch(self)
        notes = (60, 64, 67, 64)
        samples = numpy.concatenate([silence(0.3)] + [tone(0.5, midi_hz(n), 0.8) for n in notes] + [silence(0.3)])
        wav = support.write_wav(directory / "take.wav", samples, RATE)
        support.ffmpeg("-i", str(wav), "-ac", "2", "-c:a", "flac", str(directory / "take.flac"))
        source = directory / "take.flac"
        fake = ["--program", f"demucs-python={test_stems.fake_python(directory)}"]
        argv = configured(directory, LENIENT) + fake
        code, answer = run_melody(directory, source, "--from", "stems/vocals", *argv)
        self.assertEqual(code, 0, answer)
        self.assertEqual(answer["from"]["extractors"]["stems"]["status"], "ran")
        recording = bank.file_digest(source)
        reader = bank.Bank(directory / "bank", None, {"melody": LENIENT})
        stem_path, stem_digest, _ = reader.input_of(recording, "stems/vocals")
        self.assertNotEqual(stem_digest, recording)  # anchor: the stem is other content
        sung = reader.entry_from(recording, "stems/vocals", "melody")
        self.assertEqual(sung.manifest["content"], stem_digest)
        # On the stem it prefers, it measures in 2-cent bins; asked without the feature, the reader refuses.
        self.assertEqual(sung.manifest["parameters"]["bin_cents"], 2)
        with self.assertRaisesRegex(bank.BankError, "which feature"):
            reader.entry(stem_digest, "melody", filed_under=recording)
        # Now that the bank holds the stem, the stem's melody is the recording's by default:
        # run plainly, nothing runs, and the reader finds the same entry.
        with patch.object(extract, "run_program", side_effect=AssertionError("ran")):
            code, again = run_melody(directory, source, *argv)
        self.assertEqual(code, 0, again)
        self.assertEqual(again["extractors"]["melody"]["status"], "hit")
        self.assertEqual(again["extractors"]["melody"]["input"], "stems/vocals")
        self.assertEqual(again["extractors"]["melody"]["key"], sung.manifest["key"])
        self.assertEqual(reader.default_entry(recording, "melody")[0].manifest["key"], sung.manifest["key"])
        self.assertEqual(reader.default_entry(recording, "melody")[1], "stems/vocals")
        self.assertEqual(sung.path.parents[2].name, recording.split(":")[1])
        sung_notes = [round(n["value"]) for n in sung.items("notes")]
        # MELODIA's relative voicing can pass over one of a few pure tones
        # (see LENIENT); the notes it finds are the tune's.
        self.assertGreaterEqual(len(sung_notes), 3)
        self.assertLessEqual(set(sung_notes), set(notes))
        # The mix, measured too (asked for), is another entry; the stem is 0.4 of it, about 8 dB down.
        self.assertEqual(run_melody(directory, source, "--no-prefer", *argv)[0], 0)
        mix = entry_of(directory, source, LENIENT)
        self.assertNotEqual(mix.manifest["key"], sung.manifest["key"])
        self.assertEqual(mix.manifest["parameters"]["bin_cents"], 5)
        self.assertEqual([round(n["value"]) for n in mix.items("notes")], sung_notes)
        voiced = sung.series("voiced")[:, 0].astype(bool) & mix.series("voiced")[:, 0].astype(bool)
        self.assertGreater(int(voiced.sum()), 500)
        difference = sung.series("level_db")[voiced, 0] - mix.series("level_db")[voiced, 0]
        self.assertLess(abs(float(numpy.median(difference)) - 20 * math.log10(0.4)), 0.2)
        self.assertLess(abs(sung.scalar("loud_db") - mix.scalar("loud_db") - 20 * math.log10(0.4)), 0.2)

    def test_a_mostly_unvoiced_vocal_stem_falls_back_to_the_mix(self) -> None:
        """An instrumental's vocal stem is bleed: below a voiced fifth, the
        mix's melody is the recording's (ADR 0076, amended)."""
        import test_stems
        directory = scratch(self)
        notes = (60, 64, 67, 64, 62, 65, 69, 65)
        samples = numpy.concatenate([silence(0.3)] + [tone(0.5, midi_hz(n), 0.8) for n in notes] + [silence(0.3)])
        wav = support.write_wav(directory / "take.wav", samples, RATE)
        support.ffmpeg("-i", str(wav), "-ac", "2", "-c:a", "flac", str(directory / "take.flac"))
        source = directory / "take.flac"
        fake = ["--program", f"demucs-python={test_stems.fake_python(directory)}"]
        argv = configured(directory, LENIENT) + fake
        out = io.StringIO()
        with patch.dict(os.environ, {"STEMS_FAKE": "instrumental"}), redirect_stdout(out), \
                redirect_stderr(io.StringIO()):
            self.assertEqual(extract.main(["run", "stems", "--content", str(source), "--bank",
                                           str(directory / "bank"), *fake]), 0, out.getvalue())
        code, answer = run_melody(directory, source, *argv)
        self.assertEqual(code, 0, answer)
        recording = bank.file_digest(source)
        reader = bank.Bank(directory / "bank", None, {"melody": LENIENT})
        sung = reader.entry_from(recording, "stems/vocals", "melody")
        mix = entry_of(directory, source, LENIENT)
        self.assertLess(sung.scalar("voiced_fraction"), 0.2)
        self.assertGreater(mix.scalar("voiced_fraction"), 0.2)  # anchor: the mix is not near empty
        said = answer["extractors"]["melody"]
        self.assertNotIn("input", said)
        self.assertEqual(said["passed_over"], {"stems/vocals": sung.manifest["key"]})
        self.assertEqual(said["key"], mix.manifest["key"])
        entry, feature = reader.default_entry(recording, "melody")
        self.assertIsNone(feature)
        self.assertEqual((entry.manifest["key"], entry.manifest["parameters"]["bin_cents"]),
                         (mix.manifest["key"], 5))

    def separated(self, directory: Path, four: str | None, six: str | None) -> tuple[Path, list[str]]:
        """A sung tune, and the bank holding its stems from each separator
        given a STEMS_FAKE mode (None: that separator is not run); the
        source and the melody's arguments."""
        import test_stems
        notes = (60, 64, 67, 64, 62, 65, 69, 65)
        samples = numpy.concatenate([silence(0.3)] + [tone(0.5, midi_hz(n), 0.8) for n in notes] + [silence(0.3)])
        wav = support.write_wav(directory / "take.wav", samples, RATE)
        support.ffmpeg("-i", str(wav), "-ac", "2", "-c:a", "flac", str(directory / "take.flac"))
        source = directory / "take.flac"
        program = test_stems.fake_python(directory)
        fake = ["--program", f"demucs-python={program}", "--program", f"stems-6s-python={program}"]
        for separator, mode in (("stems", four), ("stems-6s", six)):
            if mode is None:
                continue
            out = io.StringIO()
            with patch.dict(os.environ, {"STEMS_FAKE": mode}), redirect_stdout(out), \
                    redirect_stderr(io.StringIO()):
                self.assertEqual(extract.main(["run", separator, "--content", str(source), "--bank",
                                               str(directory / "bank"), *fake]), 0, out.getvalue())
        return source, configured(directory, LENIENT) + fake

    def test_a_mostly_unvoiced_vocal_stem_falls_back_to_the_six_stem_vocals(self) -> None:
        """Where the 4-stem vocal stem is bleed but the 6-stem separator's
        voices the tune, the 6-stem vocal stem's melody is the recording's,
        in 2-cent bins, and the 4-stem one is passed over."""
        directory = scratch(self)
        source, argv = self.separated(directory, "instrumental", "gains")
        code, answer = run_melody(directory, source, *argv)
        self.assertEqual(code, 0, answer)
        recording = bank.file_digest(source)
        reader = bank.Bank(directory / "bank", None, {"melody": LENIENT})
        self.assertNotEqual(reader.input_of(recording, "stems/vocals")[1],
                            reader.input_of(recording, "stems-6s/vocals")[1])  # anchor: two stems
        four = reader.entry_from(recording, "stems/vocals", "melody")
        six = reader.entry_from(recording, "stems-6s/vocals", "melody")
        self.assertLess(four.scalar("voiced_fraction"), 0.2)
        self.assertGreaterEqual(six.scalar("voiced_fraction"), 0.2)
        said = answer["extractors"]["melody"]
        self.assertEqual(said["input"], "stems-6s/vocals")
        self.assertEqual(said["passed_over"], {"stems/vocals": four.manifest["key"]})
        self.assertEqual(said["key"], six.manifest["key"])
        self.assertEqual(six.manifest["parameters"]["bin_cents"], 2)
        entry, feature = reader.default_entry(recording, "melody")
        self.assertEqual((entry.manifest["key"], feature), (six.manifest["key"], "stems-6s/vocals"))
        # The mix was not measured: the 6-stem vocal stem was enough.
        self.assertIsNone(reader.entry(recording, "melody"))

    def test_the_four_stem_vocals_come_first_and_every_short_stem_is_passed_over(self) -> None:
        """Both separators' vocal stems voiced: the 4-stem one is the
        default and the 6-stem one is not measured. Both bleed: each is
        passed over in order, and the mix is the recording's melody."""
        directory = scratch(self)
        source, argv = self.separated(directory, "gains", "gains")
        code, answer = run_melody(directory, source, *argv)
        self.assertEqual(code, 0, answer)
        recording = bank.file_digest(source)
        reader = bank.Bank(directory / "bank", None, {"melody": LENIENT})
        self.assertIsNotNone(reader.input_of(recording, "stems-6s/vocals"))  # anchor: the bank holds both
        said = answer["extractors"]["melody"]
        self.assertEqual(said["input"], "stems/vocals")
        self.assertNotIn("passed_over", said)
        self.assertIsNone(reader.entry_from(recording, "stems-6s/vocals", "melody"))
        self.assertEqual(reader.default_entry(recording, "melody")[1], "stems/vocals")

        directory = scratch(self)
        source, argv = self.separated(directory, "instrumental", "instrumental")
        code, answer = run_melody(directory, source, *argv)
        self.assertEqual(code, 0, answer)
        recording = bank.file_digest(source)
        reader = bank.Bank(directory / "bank", None, {"melody": LENIENT})
        four = reader.entry_from(recording, "stems/vocals", "melody")
        six = reader.entry_from(recording, "stems-6s/vocals", "melody")
        mix = entry_of(directory, source, LENIENT)
        self.assertLess(four.scalar("voiced_fraction"), 0.2)
        self.assertLess(six.scalar("voiced_fraction"), 0.2)
        said = answer["extractors"]["melody"]
        self.assertNotIn("input", said)
        self.assertEqual(said["passed_over"], {"stems/vocals": four.manifest["key"],
                                               "stems-6s/vocals": six.manifest["key"]})
        self.assertEqual(list(said["passed_over"]), ["stems/vocals", "stems-6s/vocals"])
        self.assertEqual(said["key"], mix.manifest["key"])
        self.assertIsNone(reader.default_entry(recording, "melody")[1])

    def test_the_melody_never_prefers_the_bass(self) -> None:
        """Bass is its own category: no stem a melody prefers is a bass
        part, and its preference falls back below a voiced fifth."""
        catalog = bank.Catalog.load()
        prefers = catalog["melody"].prefers
        self.assertTrue(prefers)  # anchor: it prefers something
        self.assertEqual([p["input"] for p in prefers], ["stems/vocals", "stems-6s/vocals"])
        for preference in prefers:
            source, output = preference["input"].split("/")
            self.assertNotIn("bass", output)
            self.assertEqual(catalog[source].outputs[output]["kind"], "file")
            self.assertEqual(preference["at_least"], {"voiced_fraction": "0.2"})


if __name__ == "__main__":
    unittest.main()
