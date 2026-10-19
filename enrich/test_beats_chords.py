"""The learned beat and chord extractors, run by the real runner (ADR 0074):
`beats-beatthis`, `chords-jiang`, and `chords-chordino`.

Each runs under an interpreter of its own with a model this machine need
not have, so most tests configure that interpreter as a stand-in program:
it loads the real extractor program and runs its `main` (the request check,
the outputs, the scalars, and `result.json`) with only the model replaced by
fixed answers. Everything after the model is the real writer. The checks
that refuse an unpinned model run for real, because they come before any
heavy import.

One opt-in class per extractor runs the real model on synthetic audio when
its environment is named:

- ARCHIVE_TEST_BEATTHIS_PYTHON and ARCHIVE_TEST_BEATTHIS_CHECKPOINT;
- ARCHIVE_TEST_JIANG_PYTHON and ARCHIVE_TEST_JIANG_REPO;
- ARCHIVE_TEST_CHORDINO_PYTHON.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import types
import unittest
import wave
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import numpy

import bank
import extract

EXTRACTORS = Path(__file__).resolve().parent / "extractors"


def load(extractor_id: str, program: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(f"{extractor_id.replace('-', '_')}_program",
                                                  EXTRACTORS / extractor_id / program)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


beatthis = load("beats-beatthis", "beatthis.py")
jiang = load("chords-jiang", "jiang.py")
chordino = load("chords-chordino", "chordino.py")

# The fixed answers the stand-ins give. FAKE_MODE changes them: "exit"
# crashes before writing, "empty" finds nothing.
BEATS = [0.5, 1.0, 1.5, 2.02, 2.5, 3.0]
DOWNBEATS = [0.5, 2.5]
CHORDINO_CHANGES = [(0.0, "N"), (0.37, "C"), (1.5, "Am7"), (2.9, "F/A")]
CHORDINO_SECONDS = 3.0
TEMPLATES = ["N", "C:maj", "A:min7", "F:maj/3", "C:maj7"]
# Frames of 0.5 s: two of C, two of A:min7, two where C:maj7 leads but the
# HMM keeps C:maj (so its readings disagree with its label), one of N. No
# two weights a span reads are equal, so the order of the top three is fixed.
JIANG_FRAME_SECONDS = 0.5
JIANG_LOG_PROBABILITIES = numpy.log(numpy.array([
    [0.04, 0.80, 0.06, 0.03, 0.07],
    [0.04, 0.70, 0.06, 0.03, 0.17],
    [0.02, 0.05, 0.80, 0.09, 0.04],
    [0.02, 0.05, 0.70, 0.19, 0.04],
    [0.03, 0.30, 0.06, 0.04, 0.57],
    [0.03, 0.24, 0.06, 0.04, 0.63],
    [0.90, 0.04, 0.03, 0.02, 0.01],
]))
JIANG_LABELS = [(0.0, 1.0, "C:maj"), (1.0, 2.0, "A:min7"), (2.0, 3.0, "C:maj"), (3.0, 3.5, "X:unknown")]

STAND_IN = r'''#!{python}
import importlib.util, os, sys
sys.path.insert(0, {enrich!r})
import test_beats_chords as fixtures
spec = importlib.util.spec_from_file_location("program", sys.argv[1])
program = importlib.util.module_from_spec(spec)
spec.loader.exec_module(program)
mode = os.environ.get("FAKE_MODE", "")
if mode == "exit":
    print("the model crashed on purpose")
    sys.exit(1)
empty = mode == "empty"
software = [{"name": "fake-model", "version": "1"}]

class Beats:
    software = software
    def track(self, path):
        return ([], []) if empty else (fixtures.BEATS, fixtures.DOWNBEATS)

class Chordino:
    software = software
    def label(self, path):
        return fixtures.CHORDINO_SECONDS, ([] if empty else fixtures.CHORDINO_CHANGES)

class Jiang:
    software = software
    def decode(self, path):
        labels = [] if empty else fixtures.JIANG_LABELS
        return program.Decoded(labels, fixtures.TEMPLATES, fixtures.JIANG_LOG_PROBABILITIES,
                               fixtures.JIANG_FRAME_SECONDS)

name = os.path.basename(sys.argv[1])
if name == "beatthis.py":
    program.load_model = lambda checkpoint: Beats()
elif name == "chordino.py":
    program.load_model = lambda: Chordino()
else:
    program.load_model = lambda repository: Jiang()
sys.exit(program.main(sys.argv[2:]))
'''


def stand_in_programs(directory: Path) -> list[str]:
    """`--program` arguments that run every learned extractor here with its
    model replaced: the interpreters are the stand-in, and the checkpoint
    and repository are placeholders the stand-in never opens."""
    program = directory / "fake-model-python"
    program.write_text(STAND_IN.replace("{python}", sys.executable)
                       .replace("{enrich!r}", repr(str(Path(__file__).resolve().parent))))
    program.chmod(0o755)
    placeholder = directory / "placeholder-model"
    placeholder.mkdir(exist_ok=True)
    arguments = []
    for name in ("beatthis-python", "jiang-python", "chordino-python"):
        arguments += ["--program", f"{name}={program}"]
    for name in ("beatthis-checkpoint", "jiang-repo"):
        arguments += ["--program", f"{name}={placeholder}"]
    return arguments


def scratch(test: unittest.TestCase) -> Path:
    directory = Path(tempfile.mkdtemp(prefix="bank-learned-"))
    test.addCleanup(shutil.rmtree, directory, True)
    return directory


def run(directory: Path, content: Path, target: str, *extra: str,
        env: dict[str, str] | None = None) -> tuple[int, dict]:
    argv = ["run", target, "--content", str(content), "--bank", str(directory / "bank"), *extra]
    out, err = io.StringIO(), io.StringIO()
    with patch.dict(os.environ, env or {}), redirect_stdout(out), redirect_stderr(err):
        code = extract.main(argv)
    return code, json.loads(out.getvalue() or err.getvalue())


def silence(path: Path, seconds: float = 3.0, rate: int = 22050) -> Path:
    """A WAV the stand-ins never decode; the runner only hashes it."""
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(b"\0\0" * int(seconds * rate))
    return path


PROGRAMS = {"beats-beatthis": (beatthis, "beatthis.py", ["${beatthis-python}", "beatthis.py", "${beatthis-checkpoint}"]),
            "chords-jiang": (jiang, "jiang.py", ["${jiang-python}", "jiang.py", "${jiang-repo}"]),
            "chords-chordino": (chordino, "chordino.py", ["${chordino-python}", "chordino.py"])}


class Manifests(unittest.TestCase):
    def test_each_manifest_declares_exactly_what_its_program_implements(self) -> None:
        catalog = bank.Catalog.load()
        for extractor_id, (module, _, command) in PROGRAMS.items():
            with self.subTest(extractor=extractor_id):
                extractor = catalog[extractor_id]
                self.assertEqual(extractor.manifest["parameters"], module.PARAMETERS)
                self.assertEqual(extractor.manifest["command"], command)
                self.assertEqual(extractor.needs, [])
                self.assertEqual(module.PARAMETERS["threads"], module.THREADS)

    def test_the_keys_are_the_ones_the_album_trial_filed(self) -> None:
        """The shipped versions keep the trial's id, version, and parameters,
        so a bank that holds the trial's entries holds these extractors'
        (their outputs were compared byte for byte on two recordings). A
        change to the parameters fails here: it is a new key, and the
        trial's entries would no longer be read."""
        content = "sha256:41e7a7256ec18b7b6e1c7495e0f0533da5b7132689c03b7acf34e03ed14ff621"
        trial = {"chords-jiang": "sha256:da77799bc2fd97c85a1d96f14814baff76b6417f722d12fc5501ca3a137ad9f2"}
        keys = bank.Catalog.load().keys(content, list(trial))
        self.assertEqual({e: keys[e] for e in trial}, trial)

    def test_the_archive_side_never_imports_the_models(self) -> None:
        """Loading the programs (as the tests do) imports none of the model
        libraries: they are imported only when a model is loaded."""
        heavy = ("torch", "beat_this", "librosa", "vamp", "chord_extractor", "soundfile")
        probe = ("import sys, test_beats_chords; "
                 f"print(sorted(set({heavy!r}) & set(sys.modules)))")
        completed = subprocess.run([sys.executable, "-c", probe], cwd=Path(__file__).resolve().parent,
                                   capture_output=True, text=True, check=True)
        self.assertEqual(completed.stdout.strip(), "[]")
        self.assertTrue(hasattr(jiang, "Jiang"))  # anchor: the probe loaded the programs that name them


class Pure(unittest.TestCase):
    def test_median_bpm_is_numpys_median_interval(self) -> None:
        rng = numpy.random.default_rng(4)
        for count in (2, 3, 10, 11, 400):
            beats = numpy.cumsum(rng.uniform(0.3, 0.7, count)).tolist()
            expected = round(60.0 / float(numpy.median(numpy.diff(beats))), 3)
            self.assertEqual(beatthis.median_bpm(beats), expected, count)
        self.assertEqual(beatthis.median_bpm([1.0]), 0)
        self.assertEqual(beatthis.median_bpm([]), 0)

    def test_chordino_spans_tile_to_the_decoded_length(self) -> None:
        spans = chordino.spans(CHORDINO_SECONDS, CHORDINO_CHANGES)
        self.assertEqual([(s["start"], s["end"], s["label"]) for s in spans],
                         [(0.0, 0.37, "N"), (0.37, 1.5, "C"), (1.5, 2.9, "Am7"), (2.9, 3.0, "F/A")])
        # A change the plugin reports past the decoded end is a span of nothing, not a reversed one.
        self.assertEqual(chordino.spans(2.0, [(0.0, "C"), (2.5, "N")])[-1], {"start": 2.5, "end": 2.5, "label": "N"})
        self.assertEqual(chordino.spans(2.0, []), [])
        bank.check_spans(spans, "chords")

    def test_chordino_passes_the_plugin_numbers(self) -> None:
        self.assertEqual(chordino.plugin_parameters(),
                         {"useNNLS": 1, "rollon": 1, "tuningmode": 0, "whitening": 1, "s": 0.7, "boostn": 0.1})

    def test_jiang_readings_weigh_each_span_by_its_frames_posteriors(self) -> None:
        decoded = jiang.Decoded(JIANG_LABELS, TEMPLATES, JIANG_LOG_PROBABILITIES, JIANG_FRAME_SECONDS)
        spans, document, share = jiang.readings(decoded)
        # Anchor: the third span's label and its leading reading differ.
        self.assertEqual(spans[2]["label"], "C:maj")
        self.assertEqual(document["spans"][2]["top"][0][0], "C:maj7")
        self.assertEqual([r["top"] for r in document["spans"]],
                         [[["C:maj", 0.75], ["C:maj7", 0.12], ["A:min7", 0.06]],
                          [["A:min7", 0.75], ["F:maj/3", 0.14], ["C:maj", 0.05]],
                          [["C:maj7", 0.6], ["C:maj", 0.27], ["A:min7", 0.06]],
                          [["N", 0.9], ["C:maj", 0.04], ["A:min7", 0.03]]])
        self.assertEqual([s["value"] for s in spans], [0.75, 0.75, 0.27, 0.0])
        # The fourth label is not a template: weight 0 and not among its readings.
        self.assertEqual(share, 0.75)
        self.assertEqual(document["templates"], len(TEMPLATES))
        self.assertEqual(document["frame_s"], JIANG_FRAME_SECONDS)
        for reading in document["spans"]:
            self.assertLessEqual(sum(weight for _, weight in reading["top"]), 1.0001)
        bank.check_spans(spans, "chords")

    def test_jiang_readings_of_a_span_shorter_than_a_frame_take_its_frame(self) -> None:
        decoded = jiang.Decoded([(0.1, 0.1, "C:maj"), (3.4, 9.0, "N")], TEMPLATES, JIANG_LOG_PROBABILITIES,
                                JIANG_FRAME_SECONDS)
        spans, document, _ = jiang.readings(decoded)
        self.assertEqual(document["spans"][0]["top"][0], ["C:maj", 0.8])
        self.assertEqual(document["spans"][1]["top"][0], ["N", 0.9])  # clamped to the last frame
        self.assertEqual(jiang.readings(jiang.Decoded([], TEMPLATES, JIANG_LOG_PROBABILITIES, 0.5))[2], 0)

    def test_posteriors_are_a_softmax_that_survives_very_small_log_probabilities(self) -> None:
        found = jiang.posteriors(numpy.array([[-1000.0, -1001.0], [0.0, 0.0]]))
        self.assertTrue(numpy.allclose(found.sum(axis=1), 1.0))
        self.assertAlmostEqual(float(found[0, 0]), 1 / (1 + numpy.exp(-1.0)))


class PinnedModels(unittest.TestCase):
    """The checks that refuse a model other than the pinned one run before
    any model library is imported, so they run for real here."""

    def test_beatthis_refuses_a_missing_or_other_checkpoint(self) -> None:
        directory = scratch(self)
        with self.assertRaisesRegex(beatthis.BeatThisError, "configure beatthis-checkpoint"):
            beatthis.load_model(directory / "absent.ckpt")
        other = directory / "other.ckpt"
        other.write_bytes(b"not the weights")
        with self.assertRaisesRegex(beatthis.BeatThisError, "not the pinned checkpoint"):
            beatthis.load_model(other)

    def test_jiang_refuses_a_checkout_without_the_pinned_files(self) -> None:
        directory = scratch(self)
        with self.assertRaisesRegex(jiang.JiangError, "configure jiang-repo"):
            jiang.load_model(directory)
        for name in jiang.FILES:
            (directory / name).parent.mkdir(parents=True, exist_ok=True)
            (directory / name).write_bytes(b"other bits")
        with self.assertRaisesRegex(jiang.JiangError, "not the pinned file"):
            jiang.load_model(directory)

    def test_jiang_refuses_a_checkout_whose_code_is_not_the_pinned_commit(self) -> None:
        """The model files can be the pinned bits while the code that runs
        them is not: an edited, added, or removed module is refused."""
        directory = scratch(self)
        files = {}
        for name in jiang.FILES:
            (directory / name).parent.mkdir(parents=True, exist_ok=True)
            (directory / name).write_bytes(name.encode())
            files[name] = bank.file_digest(directory / name)
        (directory / "mir" / "nn").mkdir(parents=True)
        (directory / "settings.py").write_text("DEFAULT_SR = 22050\n")
        (directory / "mir" / "nn" / "train.py").write_text("class NetworkInterface: pass\n")
        pinned = jiang.code_digest(directory)
        self.assertNotEqual(pinned, jiang.CODE)  # anchor: this checkout is not the shipped pin
        with patch.object(jiang, "FILES", files), patch.object(jiang, "CODE", pinned):
            jiang.check_files(directory)
            # Byte code and Git's own files are not the code.
            for ignored in ("__pycache__/settings.py", ".git/hooks/x.py"):
                (directory / ignored).parent.mkdir(parents=True, exist_ok=True)
                (directory / ignored).write_text("changed = True\n")
            jiang.check_files(directory)
            changes = {"edited": lambda: (directory / "settings.py").write_text("DEFAULT_SR = 44100\n"),
                       "added": lambda: (directory / "mir" / "extra.py").write_text(""),
                       "removed": lambda: (directory / "mir" / "nn" / "train.py").unlink(),
                       "renamed": lambda: (directory / "settings.py").rename(directory / "settings2.py")}
            for name, change in changes.items():
                with self.subTest(name):
                    before = {path: path.read_bytes() for path in directory.rglob("*.py")}
                    change()
                    with self.assertRaisesRegex(jiang.JiangError, "Python code is not that of"):
                        jiang.check_files(directory)
                    for path in directory.rglob("*.py"):
                        if path not in before:
                            path.unlink()
                    for path, data in before.items():
                        path.write_bytes(data)
                    jiang.check_files(directory)

    def test_chordino_refuses_another_plugin_build(self) -> None:
        directory = scratch(self)
        package = directory / "chord_extractor"
        (package / "_lib").mkdir(parents=True)
        (package / "__init__.py").write_text("")
        (package / "extractors.py").write_text("")
        (package / "_lib" / "nnls-chroma.so").write_bytes(b"another build")
        sys.path.insert(0, str(directory))
        self.addCleanup(sys.path.remove, str(directory))
        self.addCleanup(lambda: [sys.modules.pop(n, None) for n in ("chord_extractor", "chord_extractor.extractors")])
        with self.assertRaisesRegex(chordino.ChordinoError, "not the pinned plugin"):
            chordino.load_model()

    def test_without_the_libraries_each_says_which_interpreter_to_configure(self) -> None:
        """This interpreter has none of the model libraries: loading is
        refused with advice, not an ImportError the runner logs bare."""
        directory = scratch(self)
        checkpoint = directory / "final0.ckpt"
        checkpoint.write_bytes(b"x")
        with patch.object(beatthis, "CHECKPOINT", bank.file_digest(checkpoint)), \
                patch.dict(sys.modules, {"torch": None}):
            with self.assertRaisesRegex(beatthis.BeatThisError, "configure beatthis-python"):
                beatthis.load_model(checkpoint)
        with patch.object(jiang, "check_files", lambda repository: None), \
                patch.dict(sys.modules, {"librosa": None}), patch.object(os, "chdir", lambda path: None):
            with self.assertRaisesRegex(jiang.JiangError, "configure jiang-python"):
                jiang.load_model(directory)
        with patch.dict(sys.modules, {"chord_extractor": None}):
            with self.assertRaisesRegex(chordino.ChordinoError, "configure chordino-python"):
                chordino.load_model()


class ThroughTheRunner(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = scratch(self)
        self.content = silence(self.directory / "take.wav")
        self.programs = stand_in_programs(self.directory)

    def entry(self, extractor_id: str) -> bank.Entry:
        found = bank.Bank(self.directory / "bank").entry(bank.file_digest(self.content), extractor_id)
        self.assertIsNotNone(found)
        return found

    def test_beatthis_files_beats_downbeats_and_the_median_tempo(self) -> None:
        code, answer = run(self.directory, self.content, "beats-beatthis", *self.programs)
        self.assertEqual(code, 0, answer)
        entry = self.entry("beats-beatthis")
        self.assertEqual([e["time"] for e in entry.items("beats")], BEATS)
        self.assertEqual([e["time"] for e in entry.items("downbeats")], DOWNBEATS)
        self.assertEqual((entry.scalar("beat_count"), entry.scalar("downbeat_count")), (6, 2))
        self.assertEqual(entry.scalar("median_bpm"), 120.0)
        self.assertEqual(entry.manifest["software"], [{"name": "fake-model", "version": "1"}])
        # The trial's bytes: json.dumps' default separators.
        self.assertEqual((entry.path / entry.output("beats")["file"]).read_text(), json.dumps([{"time": t} for t in BEATS]))

    def test_chordino_files_spans_that_tile_the_recording(self) -> None:
        code, answer = run(self.directory, self.content, "chords-chordino", *self.programs)
        self.assertEqual(code, 0, answer)
        entry = self.entry("chords-chordino")
        self.assertEqual([s["label"] for s in entry.items("chords")], ["N", "C", "Am7", "F/A"])
        self.assertEqual((entry.scalar("chord_count"), entry.scalar("seconds")), (4, 3.0))

    def test_jiang_files_labels_and_their_top_three_readings(self) -> None:
        code, answer = run(self.directory, self.content, "chords-jiang", *self.programs)
        self.assertEqual(code, 0, answer)
        entry = self.entry("chords-jiang")
        self.assertEqual([(s["label"], s["value"]) for s in entry.items("chords")],
                         [("C:maj", 0.75), ("A:min7", 0.75), ("C:maj", 0.27), ("X:unknown", 0.0)])
        document = json.loads(entry.file("readings").read_text())
        self.assertEqual(document["spans"][2]["top"][0], ["C:maj7", 0.6])
        self.assertEqual(entry.output("readings")["media_type"], "application/json")
        self.assertEqual((entry.scalar("chord_count"), entry.scalar("decoded_in_top3")), (4, 0.75))

    def test_nothing_found_is_an_entry_not_a_failure(self) -> None:
        env = {"FAKE_MODE": "empty"}
        for extractor_id in PROGRAMS:
            with self.subTest(extractor=extractor_id):
                code, answer = run(self.directory, self.content, extractor_id, *self.programs, env=env)
                self.assertEqual(code, 0, answer)
                entry = self.entry(extractor_id)
                self.assertEqual(entry.items("chords" if extractor_id != "beats-beatthis" else "beats"), [])
        self.assertEqual(self.entry("beats-beatthis").scalar("median_bpm"), 0)
        self.assertEqual(self.entry("chords-jiang").scalar("decoded_in_top3"), 0)

    def test_a_second_run_is_a_hit_that_starts_nothing(self) -> None:
        targets = list(PROGRAMS)
        for extractor_id in targets:
            self.assertEqual(run(self.directory, self.content, extractor_id, *self.programs)[0], 0)

        def bomb(*_args, **_kwargs):
            raise AssertionError("a cache hit started a program")
        for extractor_id in targets:
            with patch.object(extract.subprocess, "Popen", bomb):
                code, answer = run(self.directory, self.content, extractor_id, *self.programs)
            self.assertEqual(code, 0, answer)
            self.assertEqual(answer["extractors"][extractor_id]["status"], "hit")
        # Anchor: with the entry gone, the bomb goes off.
        shutil.rmtree(self.entry("chords-jiang").path)
        with patch.object(extract.subprocess, "Popen", bomb), self.assertRaisesRegex(AssertionError, "cache hit"):
            run(self.directory, self.content, "chords-jiang", *self.programs)

    def test_a_crashed_model_is_a_failure_with_its_log(self) -> None:
        code, answer = run(self.directory, self.content, "chords-chordino", *self.programs, env={"FAKE_MODE": "exit"})
        self.assertNotEqual(code, 0)
        self.assertEqual(answer["extractors"]["chords-chordino"]["status"], "failed")
        self.assertIn("crashed on purpose", answer["extractors"]["chords-chordino"]["log"])

    def test_other_settings_are_refused_not_measured(self) -> None:
        config = self.directory / "config.json"
        for extractor_id in PROGRAMS:
            with self.subTest(extractor=extractor_id):
                config.write_text(json.dumps({"parameters": {extractor_id: {"threads": 8}}}))
                code, answer = run(self.directory, self.content, extractor_id, *self.programs, "--config", str(config))
                self.assertNotEqual(code, 0)
                self.assertIn("fixed settings", answer["extractors"][extractor_id]["log"])

    def test_run_all_skips_them_where_their_interpreters_are_not_configured(self) -> None:
        code, answer = run(self.directory, self.content, "all", "--dry-run")
        for extractor_id in PROGRAMS:
            self.assertEqual(answer["extractors"][extractor_id]["status"], "skipped", answer)


def tones(path: Path, rate: int = 44100, bpm: int = 120, bars: int = 12) -> Path:
    """A click on every beat, accented on the first of four, over the
    progression C, Am, F, G, one chord a bar: 24 s at 120 BPM."""
    beat = 60 / bpm
    length = int(bars * 4 * beat * rate)
    t = numpy.arange(length) / rate
    signal = numpy.zeros(length)
    for index, root in enumerate((60, 57, 65, 67) * (bars // 4)):
        third = 3 if root == 57 else 4
        start, end = int(index * 4 * beat * rate), int((index + 1) * 4 * beat * rate)
        for note in (root - 12, root, root + third, root + 7):
            frequency = 440 * 2 ** ((note - 69) / 12)
            for harmonic in (1, 2, 3):
                signal[start:end] += 0.06 / harmonic * numpy.sin(2 * numpy.pi * frequency * harmonic * t[start:end])
    click = numpy.exp(-numpy.arange(int(0.03 * rate)) / (0.004 * rate)) * numpy.sin(
        2 * numpy.pi * 2000 * numpy.arange(int(0.03 * rate)) / rate)
    for index in range(bars * 4):
        start = int(index * beat * rate)
        signal[start:start + len(click)] += click * (0.8 if index % 4 == 0 else 0.4)
    signal = signal / numpy.abs(signal).max() * 0.8
    pcm = numpy.round(signal * 32767).astype("<i2")
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(pcm.tobytes())
    return path


def configured(*names: str) -> bool:
    return all(os.environ.get(name) and Path(os.environ[name]).exists() for name in names)


class RealModel(unittest.TestCase):
    def run_real(self, extractor_id: str, programs: dict[str, str]) -> tuple[bank.Entry, float]:
        directory = scratch(self)
        content = tones(directory / "take.wav")
        arguments = [word for name, value in programs.items() for word in ("--program", f"{name}={value}")]
        started = time.monotonic()
        code, answer = run(directory, content, extractor_id, *arguments, "--timeout", "1200")
        elapsed = time.monotonic() - started
        self.assertEqual(code, 0, answer)
        entry = bank.Bank(directory / "bank").entry(bank.file_digest(content), extractor_id)
        print(f"\nreal {extractor_id}: {24 / elapsed:.1f}x real time on 24 s", file=sys.stderr)
        # The same build and settings give the same bits.
        first = {n: o.get("sha256", o.get("value")) for n, o in entry.manifest["outputs"].items()}
        shutil.rmtree(entry.path)
        self.assertEqual(run(directory, content, extractor_id, *arguments, "--timeout", "1200")[0], 0)
        again = bank.Bank(directory / "bank").entry(bank.file_digest(content), extractor_id)
        self.assertEqual({n: o.get("sha256", o.get("value")) for n, o in again.manifest["outputs"].items()}, first)
        return entry, elapsed


@unittest.skipUnless(configured("ARCHIVE_TEST_BEATTHIS_PYTHON", "ARCHIVE_TEST_BEATTHIS_CHECKPOINT"),
                     "set ARCHIVE_TEST_BEATTHIS_PYTHON and ARCHIVE_TEST_BEATTHIS_CHECKPOINT to run Beat This")
class RealBeatThis(RealModel):
    def test_beat_this_finds_the_clicks_and_the_bars(self) -> None:
        entry, _ = self.run_real("beats-beatthis", {
            "beatthis-python": os.environ["ARCHIVE_TEST_BEATTHIS_PYTHON"],
            "beatthis-checkpoint": os.environ["ARCHIVE_TEST_BEATTHIS_CHECKPOINT"]})
        self.assertAlmostEqual(entry.scalar("median_bpm"), 120, delta=2)
        beats = [e["time"] for e in entry.items("beats")]
        on_click = [b for b in beats if abs(b / 0.5 - round(b / 0.5)) * 0.5 < 0.05]
        self.assertGreater(len(on_click), 0.9 * len(beats))
        downbeats = [e["time"] for e in entry.items("downbeats")]
        self.assertGreater(len(downbeats), 6)
        self.assertGreater(statistics.median(numpy.diff(downbeats)), 1.5)


@unittest.skipUnless(configured("ARCHIVE_TEST_JIANG_PYTHON", "ARCHIVE_TEST_JIANG_REPO"),
                     "set ARCHIVE_TEST_JIANG_PYTHON and ARCHIVE_TEST_JIANG_REPO to run the Jiang recogniser")
class RealJiang(RealModel):
    def test_the_recogniser_names_the_progression(self) -> None:
        entry, _ = self.run_real("chords-jiang", {"jiang-python": os.environ["ARCHIVE_TEST_JIANG_PYTHON"],
                                                  "jiang-repo": os.environ["ARCHIVE_TEST_JIANG_REPO"]})
        named = {s["label"].split("/")[0] for s in entry.items("chords") if s["end"] - s["start"] > 1}
        self.assertLessEqual({"C:maj", "A:min", "F:maj", "G:maj"}, named)
        document = json.loads(entry.file("readings").read_text())
        self.assertEqual(len(document["spans"]), entry.scalar("chord_count"))
        self.assertGreater(entry.scalar("decoded_in_top3"), 0.5)


@unittest.skipUnless(configured("ARCHIVE_TEST_CHORDINO_PYTHON"),
                     "set ARCHIVE_TEST_CHORDINO_PYTHON to run Chordino")
class RealChordino(RealModel):
    def test_chordino_names_the_progression(self) -> None:
        entry, _ = self.run_real("chords-chordino", {"chordino-python": os.environ["ARCHIVE_TEST_CHORDINO_PYTHON"]})
        named = {s["label"] for s in entry.items("chords") if s["end"] - s["start"] > 1}
        self.assertLessEqual({"C", "Am", "F", "G"}, named)
        self.assertAlmostEqual(entry.scalar("seconds"), 24, delta=0.01)


if __name__ == "__main__":
    unittest.main()
