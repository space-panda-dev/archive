"""The stems extractor, run by the real runner (ADRs 0074, 0075).

This machine has no PyTorch, so most tests configure `demucs-python` as a
stand-in program: it runs the real `stems.py` (its request handling,
decoding, chunking, joining, measuring, quantising, FLAC encoding, and
result) with only the model replaced by fixed gains that sum to the mix.
Everything after the model is the real writer, so the stems it files are
the files the real extractor files. `stems-6s` is the same program with
another model and six stems, and is tested here with the same stand-in.
Real Demucs runs when ARCHIVE_TEST_DEMUCS_PYTHON names its interpreter.
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
import time
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
from test_beats_chords import stand_in_programs

sys.path.insert(0, str(Path(__file__).resolve().parent / "extractors" / "stems"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "extractors" / "stems-6s"))
import stems  # noqa: E402
import stems6s  # noqa: E402

DEMUCS_PYTHON = os.environ.get("ARCHIVE_TEST_DEMUCS_PYTHON")

# The stand-in for Demucs's interpreter. The runner starts it as
# `<it> <stems.py> <request.json>` (or stems6s.py); it loads that program and
# replaces its model, and with STEMS_FAKE_CHUNK its chunk and crossfade, in
# seconds. stems6s.py runs stems.py's code, so the stand-in replaces them
# there too. A program of six stems gets a model of six sources.
FAKE = r'''#!{python}
import functools, importlib.util, json, os, sys
import numpy
spec = importlib.util.spec_from_file_location("stems_program", sys.argv[1])
program = importlib.util.module_from_spec(spec)
spec.loader.exec_module(program)
modules = [program] + ([sys.modules["stems"]] if "stems" in sys.modules else [])
mode = os.environ.get("STEMS_FAKE", "gains")
if mode == "exit":
    print("the separator crashed on purpose")
    sys.exit(1)
six = "piano" in program.STEMS

class Gains:
    # Demucs's order, not the manifest's, so the program must reorder.
    sources = ["drums", "bass", "other", "vocals"] + (["guitar", "piano"] if six else [])
    samplerate = 48000 if mode == "rate" else 44100
    channels = 2
    software = [{"name": "fake-separator", "version": "1"}]
    gains = [0.1, 0.2, 0.15, 0.25, 0.2, 0.1] if six else [0.1, 0.2, 0.3, 0.4]
    if mode == "loud":
        gains[3] = 4.0
    if mode == "nobass":
        sources[1] = "flute" if six else "guitar"

    def separate(self, mix):
        out = numpy.stack([mix * numpy.float32(g) for g in self.gains])
        if mode == "shape":
            return out[..., :-1]
        if mode == "nan":
            out[0, 0, 0] = numpy.nan
        if mode == "changed":
            out = out * numpy.float32(0.5)
        if mode == "instrumental":
            # A vocal stem of bleed: only its first tenth sounds.
            out[self.sources.index("vocals"), ..., out.shape[-1] // 10:] = 0
        return out

def load(settings, apply):
    if os.environ.get("STEMS_FAKE_APPLY"):
        open(os.environ["STEMS_FAKE_APPLY"], "w").write(json.dumps(apply))
    return Gains()

for module in modules:
    module.load_model = load
    if os.environ.get("STEMS_FAKE_CHUNK") and hasattr(module, "separate"):
        chunk, overlap = (int(v) * module.RATE for v in os.environ["STEMS_FAKE_CHUNK"].split(","))
        module.separate = functools.partial(module.separate, chunk=chunk, overlap=overlap)
sys.exit(program.main(sys.argv[2:]))
'''
# Manifest order: vocals, drums, bass, other.
GAINS = {"vocals": 0.4, "drums": 0.1, "bass": 0.2, "other": 0.3}
SIX_GAINS = {"vocals": 0.25, "drums": 0.1, "bass": 0.2, "guitar": 0.2, "piano": 0.1, "other": 0.15}


def scratch(test: unittest.TestCase) -> Path:
    directory = Path(tempfile.mkdtemp(prefix="bank-stems-"))
    test.addCleanup(shutil.rmtree, directory, True)
    return directory


def fake_python(directory: Path) -> Path:
    path = directory / "fake-demucs-python"
    path.write_text(FAKE.replace("{python}", sys.executable))
    path.chmod(0o755)
    return path


def run(directory: Path, audio: Path, target: str = "stems", *extra: str,
        program: Path | None = None, env: dict[str, str] | None = None) -> tuple[int, dict]:
    argv = ["run", target, "--content", str(audio), "--bank", str(directory / "bank"), *extra]
    if program is not None:
        argv += ["--program", f"demucs-python={program}", "--program", f"stems-6s-python={program}"]
    out, err = io.StringIO(), io.StringIO()
    with patch.dict(os.environ, env or {}), redirect_stdout(out), redirect_stderr(err):
        code = extract.main(argv)
    return code, json.loads(out.getvalue() or err.getvalue())


def decode(path: Path, *arguments: str) -> numpy.ndarray:
    """A file's first audio stream as interleaved float32, 44,100 Hz stereo."""
    completed = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-map", "0:a:0",
                                "-ac", "2", "-ar", "44100", *arguments, "-f", "f32le", "-"],
                               capture_output=True, check=True)
    return numpy.frombuffer(completed.stdout, dtype="<f4").reshape(-1, 2).T


def chord_with_clicks(path: Path, seconds: int, volume: str = "0.5") -> Path:
    """A real recorder's shape: 48 kHz stereo FLAC, a chord with clicks."""
    support.ffmpeg("-f", "lavfi", "-i", f"sine=frequency=220:sample_rate=48000:duration={seconds}",
                   "-f", "lavfi", "-i", f"sine=frequency=277.18:sample_rate=48000:duration={seconds}",
                   "-f", "lavfi", "-i", f"sine=frequency=1000:sample_rate=48000:duration={seconds}:beep_factor=4",
                   "-filter_complex", f"[0][1][2]amix=inputs=3:normalize=0,volume={volume},"
                   "aformat=channel_layouts=stereo",
                   "-c:a", "flac", str(path))
    return path


class Manifest(unittest.TestCase):
    def test_the_manifest_declares_exactly_what_the_program_implements(self) -> None:
        extractor = bank.Catalog.load()["stems"]
        self.assertEqual(extractor.manifest["parameters"], stems.parameters())
        self.assertEqual(extractor.manifest["command"], ["${demucs-python}", "stems.py"])
        files = {name for name, out in extractor.outputs.items() if out["kind"] == "file"}
        self.assertEqual(files, set(stems.STEMS))
        self.assertEqual(extractor.outputs["levels"]["columns"], len(stems.STEMS))
        scalars = {name for name, out in extractor.outputs.items() if out["kind"] == "scalar"}
        self.assertEqual(scalars, {f"{s}_{m}" for s in stems.STEMS for m in ("rms_db", "peak_db", "clamped")}
                         | {"mix_rms_db", "samples", "seconds"})

    def test_every_device_is_declared_interchangeable_with_what_it_brings(self) -> None:
        catalog = bank.Catalog.load()
        declared = catalog["stems"].interchangeable
        self.assertEqual(declared["values"], list(stems.DEVICES))
        self.assertEqual(set(stems.TORCH_DEVICE), set(stems.DEVICES))
        self.assertTrue(set(stems.COMPILED) <= set(stems.DEVICES))
        alternatives = dict(catalog.alternatives("stems"))
        self.assertEqual(set(alternatives), set(stems.DEVICES) - {"cpu"})  # anchor: every other device
        for device, other in alternatives.items():
            self.assertEqual(other, stems.parameters(stems.HTDEMUCS, device), device)
        # Configured for another device, the manifest's cpu is among its alternatives, as the program makes it.
        overrides = {"stems": stems.parameters(stems.HTDEMUCS, "cpu-inductor")}
        self.assertEqual(dict(catalog.alternatives("stems", overrides))["cpu"], stems.parameters())

    def test_the_archive_side_never_imports_torch(self) -> None:
        # The program and the runner load without PyTorch; only load_model imports it.
        self.assertNotIn("torch", sys.modules)
        source = (bank.EXTRACTORS / "stems" / "stems.py").read_text()
        top = source.split("\nclass Demucs", 1)[0]
        self.assertNotIn("import torch", top)
        self.assertNotIn("import demucs", top)


class Joining(unittest.TestCase):
    """Chunks and their crossfade, against a separator whose output changes
    from chunk to chunk, so a misplaced sample or weight shows."""

    def test_joined_chunks_are_the_signal_with_linear_crossfades(self) -> None:
        rng = numpy.random.default_rng(1)
        for n, chunk, overlap, block in ((1, 5, 2, 3), (7, 5, 2, 3), (8, 5, 2, 1), (23, 5, 2, 4), (100, 10, 0, 7),
                                         (101, 10, 3, 1000), (37, 4, 4, 5)):
            with self.subTest(n=n, chunk=chunk, overlap=overlap, block=block):
                signal = rng.standard_normal((2, n)).astype(numpy.float32)
                blocks = [signal[:, i:i + block] for i in range(0, n, block)]
                joiner = stems.Joiner(overlap)
                out, starts, calls = [], [], 0
                position = 0
                for window, last in stems.windows(blocks, chunk, overlap):
                    starts.append(position)
                    self.assertTrue(numpy.array_equal(window, signal[:, position:position + window.shape[1]]))
                    if calls:
                        self.assertGreater(window.shape[1], overlap)
                    # Source 0 is the window plus its index; source 1 the window.
                    separated = numpy.stack([window + numpy.float32(calls), window])
                    out.extend(joiner.add(separated, last))
                    calls += 1
                    position += chunk
                    self.assertEqual(last, window.shape[1] < chunk + overlap or position + overlap >= n)
                joined = numpy.concatenate(out, axis=-1)
                self.assertEqual(joined.shape, (2, 2, n))
                numpy.testing.assert_allclose(joined[1], signal, rtol=0, atol=1e-6)
                expected = numpy.zeros(n)
                for index, start in enumerate(starts):
                    expected[start:] = index
                ramp = (numpy.arange(overlap) + 0.5) / overlap
                for index, start in enumerate(starts[1:], 1):
                    expected[start:start + overlap] = (index - 1) * (1 - ramp) + index * ramp
                numpy.testing.assert_allclose(joined[0] - signal, numpy.broadcast_to(expected, (2, n)),
                                              rtol=0, atol=1e-5)

    def test_a_bad_chunk_is_refused(self) -> None:
        with self.assertRaises(stems.StemsError):
            list(stems.windows([numpy.zeros((2, 3), dtype=numpy.float32)], 0, 1))


class Measures(unittest.TestCase):
    def test_streamed_levels_are_the_whole_signals(self) -> None:
        rng = numpy.random.default_rng(2)
        hop = stems.LEVEL_HOP
        for n in (1, hop - 1, hop, hop + 1, 3 * hop + 17, 5 * hop):
            for block in (1000, hop, 3 * hop + 5):
                with self.subTest(n=n, block=block):
                    signal = (rng.standard_normal((4, 2, n)) * 0.1).astype(numpy.float32)
                    levels = stems.Levels(4)
                    for i in range(0, n, block):
                        levels.add(signal[..., i:i + block])
                    series = levels.series()
                    rows = -(-n // hop) + 1
                    self.assertEqual(series.shape, (rows, 4))
                    padded = numpy.zeros((4, 2, hop + n + 2 * hop))
                    padded[..., hop:hop + n] = signal
                    for k in (0, rows // 2, rows - 1):
                        frame = padded[..., k * hop:k * hop + 2 * hop]  # centred on sample k * hop
                        power = numpy.square(frame).sum(axis=(1, 2)) / (2 * hop * 2)
                        expected = numpy.maximum(10 * numpy.log10(numpy.maximum(power, 1e-30)), -120)
                        numpy.testing.assert_allclose(series[k], expected, rtol=0, atol=1e-3)
                    whole = numpy.square(signal.astype(numpy.float64)).sum(axis=(1, 2)) / (2 * n)
                    for s in range(4):
                        self.assertAlmostEqual(levels.rms_db(s), 10 * math.log10(whole[s]), places=6)
                        self.assertAlmostEqual(levels.peak_db(s), 20 * math.log10(numpy.abs(signal[s]).max()),
                                               places=5)

    def test_silence_is_the_floor_not_minus_infinity(self) -> None:
        levels = stems.Levels(1)
        levels.add(numpy.zeros((1, 2, 5000), dtype=numpy.float32))
        self.assertEqual(levels.series().tolist(), [[-120.0], [-120.0], [-120.0]])
        self.assertEqual(levels.rms_db(0), -120)
        self.assertEqual(levels.peak_db(0), -120)
        self.assertEqual(stems.Levels(1).rms_db(0), -120)

    def test_quantising_rounds_to_nearest_and_counts_what_it_clamps(self) -> None:
        samples = numpy.array([[0.0, 0.5 / 32768, 1.5 / 32768, -1.0, 1.0, 2.0, -3.0, 32767.4 / 32768]],
                              dtype=numpy.float32)
        data, clamped = stems.quantise(samples)
        self.assertEqual(numpy.frombuffer(data, dtype="<i2").tolist(), [0, 0, 2, -32768, 32767, 32767, -32768, 32767])
        self.assertEqual(clamped, 3)
        # Two channels are interleaved, left first.
        data, _ = stems.quantise(numpy.array([[0.25, 0.5], [-0.25, -0.5]], dtype=numpy.float32))
        self.assertEqual(numpy.frombuffer(data, dtype="<i2").tolist(), [8192, -8192, 16384, -16384])


class ModelFiles(unittest.TestCase):
    def test_pinned_files_must_be_the_pinned_bits(self) -> None:
        directory = scratch(self)
        (directory / "w").write_bytes(b"weights")
        good = {"w": bank.file_digest(directory / "w")}
        stems.check_files({"w": directory / "w"}, good)
        with self.assertRaisesRegex(stems.StemsError, "not the pinned"):
            stems.check_files({"w": directory / "w"}, {"w": "sha256:" + "0" * 64})
        with self.assertRaisesRegex(stems.StemsError, "was not found"):
            stems.check_files({}, good)

    def test_a_model_of_another_rate_or_without_a_stem_is_refused(self) -> None:
        directory = scratch(self)

        class Model:
            sources, samplerate, channels, software = list(stems.STEMS), 44100, 2, []

        other_rate = Model()
        other_rate.samplerate = 48000
        with self.assertRaisesRegex(stems.StemsError, "48000 Hz"):
            stems.separate("unused", directory, other_rate)
        no_bass = Model()
        no_bass.sources = ["vocals", "drums", "guitar", "other"]
        with self.assertRaisesRegex(stems.StemsError, "no bass"):
            stems.separate("unused", directory, no_bass)

    @needs_ffmpeg
    def test_an_encoder_that_stops_or_fails_is_an_error(self) -> None:
        directory = scratch(self)
        with self.assertRaisesRegex(stems.StemsError, "could not run"):
            stems.Encoder(directory / "a.flac", ffmpeg=str(directory / "no-such-ffmpeg"))
        stopped = stems.Encoder(directory / "b.flac", ffmpeg="false")
        stopped.process.wait()
        with self.assertRaisesRegex(stems.StemsError, "stopped encoding"):
            for _ in range(64):  # past the pipe's buffer
                stopped.write(numpy.zeros((2, 1 << 16), dtype=numpy.float32))
        stopped.kill()
        failed = stems.Encoder(directory / "c.flac", ffmpeg="false")
        failed.process.wait()
        with self.assertRaisesRegex(stems.StemsError, "could not encode"):
            failed.close()
        failed.kill()


@needs_ffmpeg
class Separation(unittest.TestCase):
    """The real program through the real runner, with the stand-in model."""

    def test_writes_four_flac_stems_and_their_levels_and_is_cached(self) -> None:
        directory = scratch(self)
        audio = chord_with_clicks(directory / "take.flac", 13)
        fake = fake_python(directory)
        # 4-second chunks with 1-second crossfades: four chunks and three joins.
        code, answer = run(directory, audio, program=fake, env={"STEMS_FAKE_CHUNK": "4,1"})
        self.assertEqual(code, 0, answer)
        self.assertEqual(answer["extractors"]["stems"]["status"], "ran")
        entry = bank.Bank(directory / "bank").entry(bank.file_digest(audio), "stems")
        mix = decode(audio)
        n = mix.shape[1]
        self.assertEqual(entry.scalar("samples"), n)
        self.assertAlmostEqual(entry.scalar("seconds"), n / 44100)
        for stem, gain in GAINS.items():
            with self.subTest(stem=stem):
                path = entry.file(stem)
                self.assertEqual(path.suffix, ".flac")
                self.assertEqual(entry.output(stem)["media_type"], "audio/flac")
                probe = json.loads(subprocess.run(
                    ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
                    capture_output=True, text=True, check=True).stdout)["streams"][0]
                self.assertEqual((probe["codec_name"], probe["sample_rate"], probe["channels"], probe["sample_fmt"]),
                                 ("flac", "44100", 2, "s16"))
                got = decode(path)
                expected = numpy.clip(numpy.rint(mix.astype(numpy.float64) * gain * 32768), -32768, 32767) / 32768
                self.assertEqual(got.shape, mix.shape)
                # The crossfade of two equal windows can move a rounding by one step, never more.
                self.assertLessEqual(numpy.abs(got - expected).max(), 1 / 32768 + 1e-9)
                self.assertGreater(numpy.mean(got == expected), 0.999)
                self.assertAlmostEqual(entry.scalar(f"{stem}_rms_db"),
                                       entry.scalar("mix_rms_db") + 20 * math.log10(gain), places=2)
                self.assertEqual(entry.scalar(f"{stem}_clamped"), 0)
        levels = entry.series("levels")
        self.assertEqual(levels.shape, (-(-n // 4096) + 1, 4))
        self.assertEqual(entry.grid("levels"), {"sample_rate": 44100, "hop": 4096, "frame": 8192,
                                                "first_centre": 0})
        # Each column is its gain below the mix's level, row for row.
        numpy.testing.assert_allclose(levels[:, 0] - levels[:, 1], 20 * math.log10(0.4 / 0.1), atol=0.01)
        self.assertEqual({s["name"] for s in entry.manifest["software"]} >= {"fake-separator", "ffmpeg", "numpy"},
                         True)
        # A second run is a hit: nothing starts and nothing is hashed.
        with patch.object(extract.subprocess, "Popen", side_effect=AssertionError("ran")), \
                patch.object(bank, "file_digest", side_effect=AssertionError("hashed")):
            code, answer = run(directory, audio, "stems", "--digest", entry.manifest["content"], program=fake)
        self.assertEqual((code, answer["extractors"]["stems"]["status"]), (0, "hit"))

    def test_the_stems_are_the_same_bits_however_the_input_is_chunked(self) -> None:
        directory = scratch(self)
        audio = chord_with_clicks(directory / "take.flac", 9)
        fake = fake_python(directory)
        decoded = []
        for chunk in ("600,10", "2,1", "3,2"):
            code, answer = run(directory / chunk.replace(",", "-"), audio, program=fake,
                               env={"STEMS_FAKE_CHUNK": chunk})
            self.assertEqual(code, 0, answer)
            entry = bank.Bank(directory / chunk.replace(",", "-") / "bank").entry(bank.file_digest(audio), "stems")
            decoded.append({stem: decode(entry.file(stem)) for stem in GAINS})
        # Equal windows join to themselves, up to one rounding step.
        for other in decoded[1:]:
            for stem in GAINS:
                self.assertLessEqual(numpy.abs(other[stem] - decoded[0][stem]).max(), 1 / 32768 + 1e-9)

    def test_a_loud_stem_is_clamped_and_counted_and_its_level_is_not(self) -> None:
        directory = scratch(self)
        audio = chord_with_clicks(directory / "take.flac", 3, volume="0.9")
        code, answer = run(directory, audio, program=fake_python(directory), env={"STEMS_FAKE": "loud"})
        self.assertEqual(code, 0, answer)
        entry = bank.Bank(directory / "bank").entry(bank.file_digest(audio), "stems")
        self.assertGreater(entry.scalar("vocals_clamped"), 0)
        self.assertGreater(entry.scalar("vocals_peak_db"), 0)
        self.assertAlmostEqual(entry.scalar("vocals_rms_db"), entry.scalar("mix_rms_db") + 20 * math.log10(4),
                               places=2)
        self.assertEqual(entry.scalar("drums_clamped"), 0)
        self.assertLessEqual(numpy.abs(decode(entry.file("vocals"))).max(), 1.0)

    def test_silence_separates_into_silent_stems(self) -> None:
        directory = scratch(self)
        audio = directory / "silence.wav"
        support.ffmpeg("-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "2", "-c:a", "pcm_s16le", str(audio))
        code, answer = run(directory, audio, program=fake_python(directory))
        self.assertEqual(code, 0, answer)
        entry = bank.Bank(directory / "bank").entry(bank.file_digest(audio), "stems")
        self.assertEqual({entry.scalar(f"{s}_rms_db") for s in GAINS}, {-120})
        self.assertEqual(set(entry.series("levels").ravel().tolist()), {-120.0})
        self.assertEqual(entry.scalar("mix_rms_db"), -120)

    def test_a_mono_recording_is_separated_as_stereo(self) -> None:
        directory = scratch(self)
        audio = support.sine_wav(directory / "mono.wav", 22050, "2")
        code, answer = run(directory, audio, program=fake_python(directory))
        self.assertEqual(code, 0, answer)
        entry = bank.Bank(directory / "bank").entry(bank.file_digest(audio), "stems")
        vocals = decode(entry.file("vocals"))
        self.assertTrue(numpy.array_equal(vocals[0], vocals[1]))
        self.assertEqual(entry.scalar("samples"), 88200)

    def test_each_failure_is_recorded_and_files_nothing(self) -> None:
        directory = scratch(self)
        audio = chord_with_clicks(directory / "take.flac", 2)
        fake = fake_python(directory)
        cases = {"shape": "returned shape", "nan": "not finite", "rate": "48000 Hz", "nobass": "no bass",
                 "exit": "crashed on purpose"}
        for mode, message in cases.items():
            with self.subTest(mode=mode):
                code, answer = run(directory, audio, program=fake, env={"STEMS_FAKE": mode})
                self.assertEqual(code, 1)
                result = answer["extractors"]["stems"]
                self.assertEqual(result["status"], "failed")
                self.assertIn(message, result["log"])
                self.assertEqual(list((directory / "bank").glob("sha256/*/*/*/bank/stems/*/entry.json")), [])
                failure = bank.failure_path(directory / "bank", bank.file_digest(audio), "stems", result["key"])
                self.assertIn(message, json.loads(failure.read_text())["log"])
                self.assertEqual(list((directory / "bank" / "tmp").iterdir()), [])

    def test_inputs_it_cannot_separate_fail(self) -> None:
        directory = scratch(self)
        fake = fake_python(directory)
        not_audio = directory / "notes.wav"
        not_audio.write_bytes(b"not audio at all")
        code, answer = run(directory, not_audio, program=fake)
        self.assertEqual(code, 1)
        self.assertIn("could not decode", answer["extractors"]["stems"]["log"])
        empty = directory / "empty.wav"
        support.ffmpeg("-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "0", "-c:a", "pcm_s16le", str(empty))
        code, answer = run(directory, empty, program=fake)
        self.assertEqual(code, 1)
        self.assertIn("decodes to no samples", answer["extractors"]["stems"]["log"])

    def test_other_settings_are_refused_not_separated(self) -> None:
        directory = scratch(self)
        audio = chord_with_clicks(directory / "take.flac", 1)
        config = directory / "config.json"
        config.write_text(json.dumps({"parameters": {"stems": {"chunk_seconds": 60}}}))
        code, answer = run(directory, audio, "stems", "--config", str(config), program=fake_python(directory))
        self.assertEqual(code, 1)
        self.assertIn("fixed settings", answer["extractors"]["stems"]["log"])

    def test_each_device_is_run_with_the_settings_it_brings(self) -> None:
        directory = scratch(self)
        audio = chord_with_clicks(directory / "take.flac", 1)
        for device in stems.DEVICES:
            config = directory / f"{device}.json"
            config.write_text(json.dumps({"parameters": {"stems": stems.parameters(stems.HTDEMUCS, device)}}))
            told = directory / f"{device}.apply.json"
            code, answer = run(directory, audio, "stems", "--config", str(config), program=fake_python(directory),
                               env={"STEMS_FAKE_APPLY": str(told)})
            self.assertEqual(code, 0, answer)
            self.assertEqual(json.loads(told.read_text()), stems.apply_on(device))
            entry = bank.Bank(directory / "bank", None, {"stems": stems.parameters(stems.HTDEMUCS, device)},
                              exact=True).entry(bank.file_digest(audio), "stems")
            self.assertEqual(entry.manifest["parameters"]["apply"]["device"], device)

    def test_an_interpreter_without_demucs_says_so(self) -> None:
        # This interpreter has no PyTorch: the real model loader is reached and refuses.
        directory = scratch(self)
        audio = chord_with_clicks(directory / "take.flac", 1)
        code, answer = run(directory, audio, program=Path(sys.executable))
        self.assertEqual(code, 1)
        self.assertIn("cannot load Demucs", answer["extractors"]["stems"]["log"])

    def test_without_demucs_python_all_runs_the_rest_and_says_why_not_stems(self) -> None:
        directory = scratch(self)
        audio = chord_with_clicks(directory / "take.flac", 2)
        code, answer = run(directory, audio, "loudness-r128")
        self.assertEqual(code, 0, answer)
        code, answer = run(directory, audio, "stems")
        self.assertEqual(code, 1)
        self.assertIn("--program demucs-python=", answer["extractors"]["stems"]["error"])
        code, answer = run(directory, audio, "all", "--dry-run")
        self.assertEqual(code, 0, answer)
        self.assertEqual(answer["extractors"]["stems"]["status"], "skipped")
        self.assertIn("demucs-python", answer["extractors"]["stems"]["reason"])


@needs_ffmpeg
class StemAsContent(unittest.TestCase):
    """A stem is content: every extractor measures it unchanged, by its own
    digest, and its entries live with the recording it came from."""

    def test_extractors_measure_a_stem_as_content_filed_with_the_recording(self) -> None:
        directory = scratch(self)
        audio = chord_with_clicks(directory / "take.flac", 4)
        fake = fake_python(directory)
        recording = bank.file_digest(audio)
        code, answer = run(directory, audio, "loudness-r128", "--from", "stems/vocals", program=fake)
        self.assertEqual(code, 0, answer)
        # The stem was made first, on the recording, then measured.
        self.assertEqual(answer["from"]["extractors"]["stems"]["status"], "ran")
        self.assertEqual(answer["extractors"]["loudness-r128"]["status"], "ran")
        reader = bank.Bank(directory / "bank")
        stem_path, stem_digest, stem_type = reader.input_of(recording, "stems/vocals")
        self.assertNotEqual(stem_digest, recording)
        self.assertEqual(stem_digest, bank.file_digest(stem_path))
        self.assertEqual((answer["content"], answer["filed_under"], stem_type),
                         (stem_digest, recording, "audio/flac"))
        measured = reader.entry(stem_digest, "loudness-r128", filed_under=recording)
        self.assertEqual(measured.manifest["content"], stem_digest)
        self.assertEqual(measured.manifest["media_type"], "audio/flac")
        self.assertEqual(measured.path.parents[2], bank.entry_directory(directory / "bank", recording, "stems",
                                                                        answer["from"]["entry"]).parents[2])
        # The recording's own loudness is another entry: 0.4 is about 8 dB louder than the stem.
        self.assertIsNone(reader.entry(recording, "loudness-r128"))
        self.assertEqual(run(directory, audio, "loudness-r128")[0], 0)
        difference = reader.entry(recording, "loudness-r128").scalar("integrated") - measured.scalar("integrated")
        self.assertAlmostEqual(difference, -20 * math.log10(0.4), delta=0.3)
        # Nothing is filed under the stem's own digest: deleting the
        # recording's directory deletes everything made from it.
        directories = {p.relative_to(directory / "bank" / "sha256") for p in
                       (directory / "bank" / "sha256").glob("*/*/*")}
        self.assertEqual(directories, {Path(recording[7:9], recording[9:11], recording[7:])})
        # Again: everything is a hit, and nothing runs.
        with patch.object(extract.subprocess, "Popen", side_effect=AssertionError("ran")):
            code, answer = run(directory, audio, "loudness-r128", "--from", "stems/vocals", program=fake)
        self.assertEqual(code, 0, answer)
        self.assertEqual(answer["extractors"]["loudness-r128"]["status"], "hit")

    def test_a_stem_with_the_same_bits_keeps_its_measures_and_other_bits_do_not(self) -> None:
        directory = scratch(self)
        audio = chord_with_clicks(directory / "take.flac", 3)
        fake = fake_python(directory)
        recording = bank.file_digest(audio)
        self.assertEqual(run(directory, audio, "loudness-r128", "--from", "stems/drums", program=fake)[0], 0)
        reader = bank.Bank(directory / "bank")
        stems_entry = reader.entry(recording, "stems")
        first = reader.input_of(recording, "stems/drums")[1]
        # The stems entry is regenerable: deleted and made again with the same
        # bits, the stem's digest is the same and its measure is a hit.
        shutil.rmtree(stems_entry.path)
        code, answer = run(directory, audio, "loudness-r128", "--from", "stems/drums", program=fake)
        self.assertEqual(code, 0, answer)
        self.assertEqual(answer["from"]["extractors"]["stems"]["status"], "ran")
        self.assertEqual(answer["extractors"]["loudness-r128"]["status"], "hit")
        self.assertEqual(reader.input_of(recording, "stems/drums")[1], first)
        # Made again with other bits, the stem is other content, measured anew.
        shutil.rmtree(reader.entry(recording, "stems").path)
        code, answer = run(directory, audio, "loudness-r128", "--from", "stems/drums", program=fake,
                           env={"STEMS_FAKE": "changed"})
        self.assertEqual(code, 0, answer)
        self.assertNotEqual(answer["content"], first)
        self.assertEqual(answer["extractors"]["loudness-r128"]["status"], "ran")

    def test_all_on_a_stem_runs_every_measure_but_the_separator(self) -> None:
        directory = scratch(self)
        audio = chord_with_clicks(directory / "take.flac", 2)
        code, answer = run(directory, audio, "all", "--from", "stems/other", "--dry-run",
                           program=fake_python(directory))
        # In a dry run nothing is separated, so nothing can be keyed on the stem yet.
        self.assertEqual(code, 0, answer)
        self.assertEqual(answer["from"]["extractors"]["stems"]["status"], "would_run")
        self.assertEqual(answer["extractors"], {})
        self.assertEqual(run(directory, audio, "stems", program=fake_python(directory))[0], 0)
        # The learned beat and chord extractors configured too; the rest are skipped.
        code, answer = run(directory, audio, "all", "--from", "stems/other", "--dry-run",
                           *stand_in_programs(directory), program=fake_python(directory))
        self.assertEqual(code, 0, answer)
        self.assertNotIn("stems", answer["extractors"])
        # Nor the six-stem separator, though `run` configures its interpreter: it would separate the stem again.
        self.assertNotIn("stems-6s", answer["extractors"])
        self.assertEqual(set(answer["extractors"]), set(bank.Catalog.load().extractors) - {"stems", "stems-6s"})
        # Those whose interpreter is not configured here are skipped; the rest would measure the stem.
        tasks = [task for task in answer["extractors"].values() if task["status"] != "skipped"]
        self.assertIn("loudness-r128", {e for e, t in answer["extractors"].items() if t["status"] == "would_run"})
        for task in tasks:
            self.assertEqual(task["task"]["input"]["digest"], answer["content"])
            self.assertEqual(task["task"]["input"]["filed_under"], bank.file_digest(audio))

    def test_all_on_a_stem_never_separates_it_again(self) -> None:
        """Every separator in the catalog is left out of `all --from`, with
        its interpreter configured; named on its own, it still runs."""
        catalog = bank.Catalog.load()
        separators = {e for e, extractor in catalog.extractors.items() if extract.makes_content(extractor)}
        self.assertEqual(separators, {"stems", "stems-6s"})
        directory = scratch(self)
        audio = chord_with_clicks(directory / "take.flac", 2)
        fake = fake_python(directory)
        self.assertEqual(run(directory, audio, "stems", program=fake)[0], 0)
        for source in ("stems/vocals", "stems/other"):
            with self.subTest(source=source):
                code, answer = run(directory, audio, "all", "--from", source, "--dry-run", program=fake)
                self.assertEqual(code, 0, answer)
                self.assertTrue(answer["extractors"])  # anchor: the stem is measured
                self.assertEqual(separators & set(answer["extractors"]), set())
                self.assertEqual(set(answer["from"]["extractors"]), {"stems"})
        code, answer = run(directory, audio, "stems-6s", "--from", "stems/vocals", "--dry-run", program=fake)
        self.assertEqual((code, answer["extractors"]["stems-6s"]["status"]), (0, "would_run"))

    def test_what_cannot_be_content_is_refused(self) -> None:
        directory = scratch(self)
        audio = chord_with_clicks(directory / "take.flac", 1)
        fake = fake_python(directory)
        for target, source, message in (("frames", "stems/levels", "only a file feature"),
                                        ("frames", "stems/nothing", "only a file feature"),
                                        ("stems", "stems/vocals", "cannot be its own input"),
                                        ("frames", "stems", "takes <extractor>/<output>")):
            with self.subTest(source=source):
                code, answer = run(directory, audio, target, "--from", source, program=fake)
                self.assertEqual(code, 1)
                self.assertIn(message, answer["detail"])
        self.assertFalse((directory / "bank").exists())

    def test_when_the_stem_cannot_be_made_nothing_is_measured(self) -> None:
        directory = scratch(self)
        audio = chord_with_clicks(directory / "take.flac", 1)
        code, answer = run(directory, audio, "loudness-r128", "--from", "stems/vocals",
                           program=fake_python(directory), env={"STEMS_FAKE": "exit"})
        self.assertEqual(code, 1)
        self.assertEqual(answer["from"]["extractors"]["stems"]["status"], "failed")
        self.assertEqual((answer["content"], answer["extractors"]), (None, {}))

    def test_a_stem_changed_in_the_bank_is_refused_before_anything_is_filed(self) -> None:
        directory = scratch(self)
        audio = chord_with_clicks(directory / "take.flac", 1)
        fake = fake_python(directory)
        self.assertEqual(run(directory, audio, "stems", program=fake)[0], 0)
        path, _, _ = bank.Bank(directory / "bank").input_of(bank.file_digest(audio), "stems/bass")
        with open(path, "ab") as handle:
            handle.write(b"\0")
        code, answer = run(directory, audio, "loudness-r128", "--from", "stems/bass", program=fake)
        self.assertEqual(code, 1)
        self.assertIn("has digest", answer["detail"])
        self.assertEqual(list((directory / "bank").glob("sha256/*/*/*/bank/loudness-r128/*/entry.json")), [])


@needs_ffmpeg
class StemFrames(unittest.TestCase):
    """The frames extractor on a stem: the sweep's profiles of a vocal line."""

    @needs_essentia
    def test_frames_of_a_stem_are_cover_profiles_of_that_stem(self) -> None:
        import cover
        directory = scratch(self)
        audio = chord_with_clicks(directory / "take.flac", 3)
        code, answer = run(directory, audio, "frames", "--from", "stems/vocals", program=fake_python(directory))
        self.assertEqual(code, 0, answer)
        reader = bank.Bank(directory / "bank")
        recording = bank.file_digest(audio)
        stem_path, stem_digest, _ = reader.input_of(recording, "stems/vocals")
        hpcp = reader.entry(stem_digest, "frames", filed_under=recording).series("hpcp")
        samples = reader.entry(recording, "stems").scalar("samples")
        fresh = cover.Analyser().features(cover.load([cover.Piece(stem_path, 0, samples, 44100)])).profiles
        # Anchor: the profiles say something, so equality is not two empty arrays.
        self.assertGreater(len(fresh), 30)
        self.assertGreater(float(fresh.max()), 0.5)
        self.assertTrue(numpy.array_equal(hpcp, fresh))


class SixStemsManifest(unittest.TestCase):
    def test_the_manifest_declares_exactly_what_the_program_implements(self) -> None:
        extractor = bank.Catalog.load()["stems-6s"]
        self.assertEqual(extractor.manifest["parameters"], stems6s.parameters())
        self.assertEqual(extractor.manifest["command"], ["${stems-6s-python}", "stems6s.py"])
        self.assertEqual(stems6s.STEMS, ("vocals", "drums", "bass", "guitar", "piano", "other"))
        files = [name for name, out in extractor.outputs.items() if out["kind"] == "file"]
        self.assertEqual(files, list(stems6s.STEMS))
        self.assertEqual(extractor.outputs["levels"]["columns"], 6)
        scalars = {name for name, out in extractor.outputs.items() if out["kind"] == "scalar"}
        self.assertEqual(scalars, {f"{s}_{m}" for s in stems6s.STEMS for m in ("rms_db", "peak_db", "clamped")}
                         | {"mix_rms_db", "samples", "seconds"})

    def test_it_differs_from_stems_only_in_its_model_and_its_stems(self) -> None:
        four, six = stems.parameters(), stems6s.parameters()
        # Anchor: the two are different methods, so their keys differ.
        self.assertNotEqual(four["model"], six["model"])
        self.assertEqual({k: v for k, v in four.items() if k not in ("model", "stems")},
                         {k: v for k, v in six.items() if k not in ("model", "stems")})
        self.assertEqual(six["model"]["name"], "htdemucs_6s")
        self.assertEqual(set(six["model"]["files"]), {"htdemucs_6s.yaml", "5c90dfd2.safetensors"})


@needs_ffmpeg
class SixStems(unittest.TestCase):
    """stems-6s, the real program through the real runner, with the stand-in model."""

    def test_writes_six_flac_stems_beside_the_four_and_each_is_content(self) -> None:
        directory = scratch(self)
        audio = chord_with_clicks(directory / "take.flac", 5)
        fake = fake_python(directory)
        code, answer = run(directory, audio, "stems-6s", program=fake, env={"STEMS_FAKE_CHUNK": "2,1"})
        self.assertEqual(code, 0, answer)
        reader = bank.Bank(directory / "bank")
        recording = bank.file_digest(audio)
        entry = reader.entry(recording, "stems-6s")
        mix = decode(audio)
        for stem, gain in SIX_GAINS.items():
            with self.subTest(stem=stem):
                got = decode(entry.file(stem))
                expected = numpy.clip(numpy.rint(mix.astype(numpy.float64) * gain * 32768), -32768, 32767) / 32768
                self.assertLessEqual(numpy.abs(got - expected).max(), 1 / 32768 + 1e-9)
                self.assertAlmostEqual(entry.scalar(f"{stem}_rms_db"),
                                       entry.scalar("mix_rms_db") + 20 * math.log10(gain), places=2)
        levels = entry.series("levels")
        self.assertEqual(levels.shape, (-(-mix.shape[1] // 4096) + 1, 6))
        # Columns in the manifest's order: vocals (0.25) over piano (0.1).
        numpy.testing.assert_allclose(levels[:, 0] - levels[:, 4], 20 * math.log10(0.25 / 0.1), atol=0.01)
        # A measure of a six-stem vocal is keyed by that stem and filed with the recording,
        # beside the same measure of the four-stem vocal, which is other content.
        code, six = run(directory, audio, "loudness-r128", "--from", "stems-6s/vocals", program=fake)
        self.assertEqual(code, 0, six)
        code, four = run(directory, audio, "loudness-r128", "--from", "stems/vocals", program=fake)
        self.assertEqual(code, 0, four)
        self.assertNotEqual(six["content"], four["content"])
        self.assertEqual(six["content"], entry.output("vocals")["sha256"])
        for answer in (six, four):
            self.assertEqual(answer["filed_under"], recording)
            reader.entry(answer["content"], "loudness-r128", filed_under=recording)

    def test_its_failures_name_it(self) -> None:
        directory = scratch(self)
        audio = chord_with_clicks(directory / "take.flac", 1)
        code, answer = run(directory, audio, "stems-6s", program=fake_python(directory), env={"STEMS_FAKE": "nobass"})
        self.assertEqual(code, 1)
        self.assertIn("stems-6s: the model has no bass source", answer["extractors"]["stems-6s"]["log"])
        config = directory / "config.json"
        config.write_text(json.dumps({"parameters": {"stems-6s": {"stems": ["vocals", "other"]}}}))
        code, answer = run(directory, audio, "stems-6s", "--config", str(config), program=fake_python(directory))
        self.assertEqual(code, 1)
        self.assertIn("stems-6s has fixed settings", answer["extractors"]["stems-6s"]["log"])

    def test_without_its_interpreter_all_skips_it(self) -> None:
        directory = scratch(self)
        audio = chord_with_clicks(directory / "take.flac", 1)
        argv = ["run", "all", "--content", str(audio), "--bank", str(directory / "bank"), "--dry-run",
                "--program", f"demucs-python={fake_python(directory)}"]
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = extract.main(argv)
        answer = json.loads(out.getvalue())
        self.assertEqual(code, 0, answer)
        self.assertEqual(answer["extractors"]["stems"]["status"], "would_run")
        self.assertEqual(answer["extractors"]["stems-6s"]["status"], "skipped")
        self.assertIn("stems-6s-python", answer["extractors"]["stems-6s"]["reason"])


@unittest.skipUnless(DEMUCS_PYTHON and Path(DEMUCS_PYTHON).is_file(),
                     "set ARCHIVE_TEST_DEMUCS_PYTHON to Demucs's interpreter to run real separation")
@needs_ffmpeg
class RealDemucs(unittest.TestCase):
    def test_htdemucs_separates_and_the_stems_sum_to_the_mix(self) -> None:
        directory = scratch(self)
        audio = chord_with_clicks(directory / "take.flac", 12)
        started = time.monotonic()
        code, answer = run(directory, audio, program=Path(DEMUCS_PYTHON))
        elapsed = time.monotonic() - started
        self.assertEqual(code, 0, answer)
        entry = bank.Bank(directory / "bank").entry(bank.file_digest(audio), "stems")
        software = {item["name"]: item["version"] for item in entry.manifest["software"]}
        self.assertEqual(software["demucs"], "4.1.0")
        self.assertIn("torch", software)
        mix = decode(audio)
        total = numpy.zeros_like(mix, dtype=numpy.float64)
        for stem in stems.STEMS:
            separated = decode(entry.file(stem))
            self.assertEqual(separated.shape, mix.shape)
            total += separated
        residual = numpy.square(total - mix).mean() / numpy.square(mix).mean()
        print(f"\nreal htdemucs: {12 / elapsed:.2f}x real time on 12 s; residual {10 * math.log10(residual):.1f} dB; "
              + ", ".join(f"{s} {entry.scalar(s + '_rms_db')} dBFS" for s in stems.STEMS), file=sys.stderr)
        self.assertLess(10 * math.log10(residual), -20)
        # The same build and settings give the same bits (shifts 0: no random offset).
        first = {stem: entry.output(stem)["sha256"] for stem in stems.STEMS}
        shutil.rmtree(entry.path)
        self.assertEqual(run(directory, audio, program=Path(DEMUCS_PYTHON))[0], 0)
        again = bank.Bank(directory / "bank").entry(bank.file_digest(audio), "stems")
        self.assertEqual({stem: again.output(stem)["sha256"] for stem in stems.STEMS}, first)


    def test_the_compiled_network_gives_the_eager_stems(self) -> None:
        # cpu-inductor runs htdemucs_core compiled: other bits, within a few 16-bit steps.
        directory = scratch(self)
        audio = chord_with_clicks(directory / "take.flac", 12)
        self.assertEqual(run(directory, audio, program=Path(DEMUCS_PYTHON))[0], 0)
        config = directory / "inductor.json"
        config.write_text(json.dumps({"parameters": {"stems": stems.parameters(stems.HTDEMUCS, "cpu-inductor")}}))
        code, answer = run(directory, audio, "stems", "--config", str(config), program=Path(DEMUCS_PYTHON))
        self.assertEqual(code, 0, answer)
        digest = bank.file_digest(audio)
        eager = bank.Bank(directory / "bank", exact=True).entry(digest, "stems")
        compiled = bank.Bank(directory / "bank", None, {"stems": stems.parameters(stems.HTDEMUCS, "cpu-inductor")},
                             exact=True).entry(digest, "stems")
        self.assertNotEqual(eager.manifest["key"], compiled.manifest["key"])
        self.assertIn("c++", {item["name"] for item in compiled.manifest["software"]})
        for stem in stems.STEMS:
            steps = numpy.abs(decode(eager.file(stem)) - decode(compiled.file(stem))).max() * stems.SCALE
            self.assertLessEqual(float(steps), 4, stem)

    def test_htdemucs_6s_separates_six_stems_that_sum_to_the_mix(self) -> None:
        directory = scratch(self)
        audio = chord_with_clicks(directory / "take.flac", 12)
        code, answer = run(directory, audio, "stems-6s", program=Path(DEMUCS_PYTHON))
        self.assertEqual(code, 0, answer)
        entry = bank.Bank(directory / "bank").entry(bank.file_digest(audio), "stems-6s")
        software = {item["name"]: item["version"] for item in entry.manifest["software"]}
        self.assertEqual(software["htdemucs_6s"], f"adefossez/HTDemucs-6s@{stems6s.MODEL['revision']}")
        mix = decode(audio)
        total = sum(decode(entry.file(stem)).astype(numpy.float64) for stem in stems6s.STEMS)
        residual = numpy.square(total - mix).mean() / numpy.square(mix).mean()
        self.assertLess(10 * math.log10(residual), -20)

if __name__ == "__main__":
    unittest.main()
