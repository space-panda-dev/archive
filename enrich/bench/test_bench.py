"""The bench harness on tiny synthetic sets: scoring, manifests, the gate,
loud failures, and the variant registry. Needs numpy, mir_eval and
pretty_midi (the bench interpreter); no model and no private data.

Run from the repository root:
    python -m unittest discover -s enrich/bench -p "test_*.py"
"""

from __future__ import annotations

import copy
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pretty_midi

from enrich.bench import __main__ as cli
from enrich.bench import variants
from enrich.bench.run import compare, cost, run_set
from enrich.bench.score import read_midi, score
from enrich.bench.sets import BenchError, load_set, parse_set, sha256_file, verify

SCORING = {"onset_tolerance_s": 0.05, "pitch_tolerance_cents": 50.0, "offset_ratio": 0.2,
           "offset_min_tolerance_s": 0.05}
TUNE = [(0.5, 0.9, 60.0), (1.0, 1.4, 62.0), (1.5, 2.3, 64.0), (2.5, 2.7, 65.0), (3.0, 3.9, 67.0)]


def write_midi(path: Path, notes) -> None:
    midi = pretty_midi.PrettyMIDI()
    instrument = pretty_midi.Instrument(65)
    for on, off, pitch in notes:
        instrument.notes.append(pretty_midi.Note(90, int(pitch), on, off))
    midi.instruments.append(instrument)
    midi.write(str(path))


def write_audio(path: Path, seconds: float, salt: int) -> None:
    """Bytes standing in for audio: the harness only digests and hands them on."""
    path.write_bytes(b"RIFF" + salt.to_bytes(4, "little") + bytes(int(seconds * 10)))


class FakeVariant:
    """Returns each item's reference notes, changed by `edit`; counts calls."""

    name, version = "fake", "1"

    def __init__(self, notes_by_audio, edit=lambda notes: notes):
        self.notes_by_audio, self.edit, self.calls = notes_by_audio, edit, 0

    def parameters(self):
        return {"edit": "test"}

    def provenance(self):
        return {}

    def transcribe(self, audio, work):
        self.calls += 1
        assert Path(work).is_dir()
        return self.edit(self.notes_by_audio[audio.name])


class Bench(unittest.TestCase):
    """A two-subset set of three items under a temporary bench root."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "sets").mkdir()
        (self.root / "data").mkdir()
        self.notes = {}
        items = []
        for n, (label, subset) in enumerate((("a", "one"), ("b", "one"), ("c", "two"))):
            notes = [(on + n, off + n, p + n) for on, off, p in TUNE]
            audio, ref = self.root / "data" / f"{label}.wav", self.root / "data" / f"{label}.mid"
            write_audio(audio, 30.0, n)
            write_midi(ref, notes)
            self.notes[audio.name] = read_midi(ref)
            digest = sha256_file(audio)
            items.append({"id": digest, "label": label, "subset": subset, "primary": "stem",
                          "inputs": {"stem": {"path": f"data/{label}.wav", "sha256": digest, "seconds": 30.0}},
                          "reference": {"path": f"data/{label}.mid", "sha256": sha256_file(ref)}})
        self.manifest = {"contract": "archive-bench-set/1", "set": "tiny", "task": "notes", "scoring": SCORING,
                         "items": items}
        self.save()

    def tearDown(self):
        self.tmp.cleanup()

    def save(self, manifest=None):
        (self.root / "sets" / "tiny.json").write_text(json.dumps(manifest or self.manifest))

    def run_with(self, variant, input_name=None):
        return run_set(self.root, load_set(self.root, "tiny"), variant, input_name, log=lambda *_: None)


class ScoringTest(unittest.TestCase):
    def test_identical_notes_score_one(self):
        self.assertEqual(score(TUNE, TUNE, SCORING), {"precision": 1.0, "recall": 1.0, "f1": 1.0, "f1_offsets": 1.0})

    def test_onset_tolerance_is_fifty_milliseconds(self):
        inside = [(on + 0.04, off + 0.04, p) for on, off, p in TUNE]
        outside = [(on + 0.06, off + 0.06, p) for on, off, p in TUNE]
        self.assertEqual(score(TUNE, inside, SCORING)["f1"], 1.0)
        self.assertEqual(score(TUNE, outside, SCORING)["f1"], 0.0)

    def test_pitch_tolerance_is_fifty_cents(self):
        self.assertEqual(score(TUNE, [(on, off, p + 0.4) for on, off, p in TUNE], SCORING)["f1"], 1.0)
        self.assertEqual(score(TUNE, [(on, off, p + 0.6) for on, off, p in TUNE], SCORING)["f1"], 0.0)

    def test_offsets_count_only_in_the_second_f1(self):
        clipped = [(on, on + 0.05, p) for on, off, p in TUNE]
        result = score(TUNE, clipped, SCORING)
        self.assertEqual(result["f1"], 1.0)
        self.assertEqual(result["f1_offsets"], 0.0)

    def test_half_the_notes_found(self):
        result = score(TUNE, TUNE[:2] + [(9.0, 9.5, 40.0)], SCORING)
        self.assertAlmostEqual(result["precision"], 2 / 3)
        self.assertAlmostEqual(result["recall"], 2 / 5)
        self.assertAlmostEqual(result["f1"], 0.5)

    def test_an_empty_estimate_scores_zero_but_an_empty_reference_refuses(self):
        self.assertEqual(score(TUNE, [], SCORING)["f1"], 0.0)
        with self.assertRaises(ValueError):
            score([], TUNE, SCORING)

    def test_midi_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_midi(Path(tmp) / "t.mid", TUNE)
            got = read_midi(Path(tmp) / "t.mid")
        self.assertEqual([p for _, _, p in got], [p for _, _, p in TUNE])
        self.assertTrue(all(abs(a[0] - b[0]) < 0.01 for a, b in zip(got, TUNE)))

    def test_cost_is_seconds_per_minute_of_audio(self):
        self.assertEqual(cost(30.0, 120.0), 15.0)
        with self.assertRaises(ValueError):
            cost(1.0, 0.0)


class ManifestTest(Bench):
    def broken(self, change) -> str:
        manifest = copy.deepcopy(self.manifest)
        change(manifest)
        with self.assertRaises(BenchError) as caught:
            parse_set("tiny", manifest)
        return str(caught.exception)

    def test_the_tiny_set_loads(self):
        loaded = load_set(self.root, "tiny")
        self.assertEqual([i.label for i in loaded.items], ["a", "b", "c"])
        self.assertEqual(loaded.scoring, SCORING)
        self.assertTrue(loaded.digest.startswith("sha256:"))

    def test_every_problem_is_reported(self):
        def change(m):
            m["contract"] = "other/1"
            m["items"][1]["id"] = m["items"][0]["id"]
            m["items"][2]["inputs"]["stem"]["path"] = "../outside.wav"
            del m["items"][2]["inputs"]["stem"]["seconds"]
        message = self.broken(change)
        for words in ("contract", "sha256 of its primary input", "listed twice", "relative path", "seconds"):
            self.assertIn(words, message)

    def test_primary_must_be_an_input(self):
        self.assertIn("not one of its inputs", self.broken(lambda m: m["items"][0].update(primary="mix")))

    def test_scoring_must_be_complete(self):
        self.assertIn("scoring", self.broken(lambda m: m["scoring"].pop("offset_ratio")))

    def test_the_set_name_must_match_its_file(self):
        self.assertIn("names", self.broken(lambda m: m.update(set="other")))

    def test_a_missing_set_fails(self):
        with self.assertRaises(BenchError):
            load_set(self.root, "absent")


class LoudFailureTest(Bench):
    def test_a_missing_audio_file_stops_the_run_before_any_variant_runs(self):
        (self.root / "data" / "c.wav").unlink()
        variant = FakeVariant(self.notes)
        with self.assertRaises(BenchError) as caught:
            self.run_with(variant)
        self.assertIn("c.wav is missing", str(caught.exception))
        self.assertEqual(variant.calls, 0)

    def test_a_changed_reference_stops_the_run(self):
        write_midi(self.root / "data" / "b.mid", TUNE[:1])
        variant = FakeVariant(self.notes)
        with self.assertRaises(BenchError) as caught:
            self.run_with(variant)
        self.assertIn("b.mid has changed", str(caught.exception))
        self.assertEqual(variant.calls, 0)

    def test_an_input_an_item_lacks_stops_the_run(self):
        with self.assertRaises(BenchError) as caught:
            verify(self.root, load_set(self.root, "tiny"), "mix")
        self.assertIn("has no input 'mix'", str(caught.exception))

    def test_the_cli_exits_2_on_a_missing_file_not_0_or_1(self):
        (self.root / "data" / "a.wav").unlink()
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            status = cli.main(["--root", str(self.root), "validate", "tiny"])
        self.assertEqual(status, 2)
        self.assertIn("a.wav is missing", err.getvalue())

    def test_no_root_fails(self):
        saved = os.environ.pop("ARCHIVE_BENCH_ROOT", None)
        try:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(["validate", "tiny"]), 2)
        finally:
            if saved is not None:
                os.environ["ARCHIVE_BENCH_ROOT"] = saved


class RunAndGateTest(Bench):
    def test_a_perfect_variant_scores_one_with_cost_and_subsets(self):
        result = self.run_with(FakeVariant(self.notes))
        self.assertEqual(result["means"]["all"]["f1"], 1.0)
        self.assertEqual(set(result["means"]), {"all", "one", "two"})
        self.assertEqual(result["means"]["one"]["items"], 2)
        self.assertEqual(result["cost"]["audio_seconds"], 90.0)
        self.assertGreaterEqual(result["cost"]["seconds_per_minute"], 0.0)
        self.assertEqual({r["seconds_per_minute"] >= 0 for r in result["items"]}, {True})
        self.assertEqual(result["variant"], {"name": "fake", "version": "1", "parameters": {"edit": "test"},
                                             "provenance": {}})

    def test_the_gate(self):
        good = self.run_with(FakeVariant(self.notes))
        drop_one = self.run_with(FakeVariant(self.notes, lambda notes: notes[1:]))   # 4 of 5 found: F1 0.889
        self.assertNotEqual(good["means"]["all"]["f1"], drop_one["means"]["all"]["f1"])   # anchor
        self.assertEqual(compare(good, good), [])
        self.assertEqual(compare(good, drop_one), [])   # better than the baseline passes
        failures = compare(drop_one, good, tolerance=0.02)
        self.assertTrue(any(f.startswith("all f1:") for f in failures), failures)
        self.assertTrue(any(f.startswith("two f1:") for f in failures), failures)
        self.assertEqual(compare(drop_one, good, tolerance=0.2), [])

    def test_a_drop_within_the_tolerance_passes_and_one_beyond_fails(self):
        base = self.run_with(FakeVariant(self.notes))
        near, far = copy.deepcopy(base), copy.deepcopy(base)
        near["means"]["all"]["f1"] = 1.0 - 0.019
        far["means"]["all"]["f1"] = 1.0 - 0.021
        self.assertEqual(compare(near, base, 0.02), [])
        self.assertEqual(len(compare(far, base, 0.02)), 1)

    def test_incomparable_results_are_refused(self):
        base = self.run_with(FakeVariant(self.notes))
        for change in (lambda r: r["variant"].update(name="other"),
                       lambda r: r["scoring"].update(onset_tolerance_s=0.1),
                       lambda r: r["items"].pop()):
            other = copy.deepcopy(base)
            change(other)
            with self.assertRaises(BenchError):
                compare(other, base)

    def test_the_cli_records_a_baseline_then_fails_on_a_regression(self):
        perfect, worse = FakeVariant(self.notes), FakeVariant(self.notes, lambda notes: notes[2:])
        saved = dict(variants.VARIANTS)
        (self.root / "config.json").write_text(json.dumps({"contract": "archive-bench-config/1",
                                                           "variants": {"fake": {}}}))
        try:
            variants.VARIANTS["fake"] = lambda root, settings: perfect
            args = ["--root", str(self.root)]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(args + ["run", "tiny", "--variant", "fake", "--record-baseline"]), 0)
                self.assertEqual(cli.main(args + ["check", "tiny"]), 0)
                variants.VARIANTS["fake"] = lambda root, settings: worse
                self.assertEqual(cli.main(args + ["check", "tiny"]), 1)
        finally:
            variants.VARIANTS.clear()
            variants.VARIANTS.update(saved)
        self.assertEqual((perfect.calls, worse.calls), (6, 3))   # baseline + first check; second check
        self.assertTrue((self.root / "baselines" / "tiny" / "fake@primary.json").is_file())
        self.assertEqual(len(list((self.root / "results" / "tiny" / "fake").glob("*.json"))), 3)

    def test_check_without_baselines_fails(self):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["--root", str(self.root), "check", "tiny"]), 2)


class VariantTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.python = Path(sys.executable)

    def tearDown(self):
        self.tmp.cleanup()

    def config(self, settings):
        (self.root / "config.json").write_text(json.dumps({"contract": "archive-bench-config/1",
                                                           "variants": settings}))

    def script(self, name, body):
        path = self.root / name
        path.write_text(body)
        return name

    def test_every_registered_variant_is_built_by_the_cli_and_states_itself(self):
        settings = {"basic-pitch": {"python": str(self.python)},
                    "horn-d0": {"python": str(self.python), "f0_script": self.script("f0.py", ""),
                                "builder_script": self.script("b.py", "")}}
        self.assertEqual(set(settings), set(variants.VARIANTS))   # a new variant needs its case here
        self.config(settings)
        for name in variants.VARIANTS:
            built = variants.build(name, self.root)
            self.assertEqual(built.name, name)
            self.assertTrue(built.version)
            json.dumps(built.parameters())
            json.dumps(built.provenance())

    def test_the_bank_variant_sends_the_extractors_exact_parameters(self):
        sys.path.insert(0, str(variants.EXTRACTORS / "notes-basic-pitch"))
        try:
            import notes   # the extractor itself; it refuses any other parameters
        finally:
            sys.path.pop(0)
        self.config({"basic-pitch": {"python": str(self.python)}})
        self.assertEqual(variants.build("basic-pitch", self.root).parameters(), notes.parameters())

    def test_missing_settings_and_paths_fail(self):
        self.config({"horn-d0": {"python": str(self.python), "f0_script": "absent.py", "builder_script": "b.py"}})
        with self.assertRaisesRegex(BenchError, "f0_script"):
            variants.build("horn-d0", self.root)
        with self.assertRaisesRegex(BenchError, "no settings"):
            variants.build("basic-pitch", self.root)
        with self.assertRaisesRegex(BenchError, "no variant"):
            variants.build("nonesuch", self.root)

    def test_a_builder_that_writes_nothing_fails_rather_than_scoring_zero(self):
        self.config({"horn-d0": {"python": str(self.python), "f0_script": self.script("f0.py", ""),
                                 "builder_script": self.script("b.py", "")}})
        variant = variants.build("horn-d0", self.root)
        with tempfile.TemporaryDirectory() as work:
            with self.assertRaisesRegex(BenchError, "wrote no est-horn-d0.mid"):
                variant.transcribe(self.root / "x.wav", Path(work))

    def test_a_failing_program_fails_with_its_message(self):
        self.config({"horn-d0": {"python": str(self.python),
                                 "f0_script": self.script("f0.py", "import sys; sys.exit('no model here')"),
                                 "builder_script": self.script("b.py", "")}})
        with tempfile.TemporaryDirectory() as work:
            with self.assertRaisesRegex(BenchError, "no model here"):
                variants.build("horn-d0", self.root).transcribe(self.root / "x.wav", Path(work))

    def test_the_bank_variant_reads_the_extractors_notes(self):
        fake = self.script("fake_notes.py", (
            "import json, sys, pathlib\n"
            "req = json.loads(pathlib.Path(sys.argv[2]).read_text())\n"
            "out = pathlib.Path(req['output_dir'])\n"
            "(out / 'notes.json').write_text(json.dumps({'columns': ['onset_s', 'offset_s', 'midi', 'amplitude'],"
            " 'notes': [[0.5, 0.9, 60, 0.7], [1.0, 1.4, 62, 0.6]]}))\n"
            "(out / 'result.json').write_text(json.dumps({'software': [{'name': 'x', 'version': '1'}]}))\n"))
        self.config({"basic-pitch": {"python": str(self.python)}})
        variant = variants.build("basic-pitch", self.root)
        # An interpreter that runs the fake in place of notes.py, with the same arguments.
        launcher = self.root / "python"
        launcher.write_text(f"#!/bin/sh\nexec {sys.executable} {self.root / fake} \"$@\"\n")
        launcher.chmod(0o755)
        variant.python = launcher
        with tempfile.TemporaryDirectory() as work:
            got = variant.transcribe(self.root / "x.wav", Path(work))
        self.assertEqual(got, [(0.5, 0.9, 60.0), (1.0, 1.4, 62.0)])
        self.assertEqual(variant.provenance()["software"], [{"name": "x", "version": "1"}])


if __name__ == "__main__":
    unittest.main()
