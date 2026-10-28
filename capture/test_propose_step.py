"""Capture's feature-only production and opportunistic proposal boundary."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "enrich"))

import bank  # noqa: E402
import capture  # noqa: E402
import propose_step  # noqa: E402


class CaptureBankStep(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="capture-bank-step-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.args = argparse.Namespace(archive=self.tmp)

    def test_bank_step_dispatches_mix_targets_before_opportunistic_proposals(self) -> None:
        root = self.tmp / "bank"
        root.mkdir()
        config = self.tmp / "extract.json"
        # Configure every program referenced by the actual catalog. No model runs:
        # the process boundary records what the capture dispatcher requests.
        catalog = bank.Catalog.load()
        programs = {match.group(1): "configured" for extractor in catalog.extractors.values()
                    for word in extractor.manifest["command"]
                    if (match := bank.PROGRAM.fullmatch(word))}
        config.write_text(json.dumps({"programs": programs}))
        args = capture.argparse.Namespace(**vars(self.args), bank=root, extract_config=config, jobs=2)
        calls = []

        def process(argv, **options):
            self.assertEqual(argv[2], "run")
            self.assertNotIn("--from", argv)
            target = argv[3]
            self.assertNotEqual(target, "stems", "capture must not schedule separation")
            calls.append(target)
            return subprocess.CompletedProcess(argv, 0, json.dumps({"extractors": {target: {"status": "ran"}}}), "")

        def proposal(command, artifact, passed_args, env, export, **options):
            self.assertEqual(calls, ["chords-jiang", "chords-chordino", "rhythm", "tonal"])
            self.assertEqual((command, artifact, options["bank"]), ("propose", "mix", root))
            self.assertIs(passed_args, args)
            calls.append("propose")
            return 3, {"status": "nothing_proposed"}

        entry = {}
        with patch.object(propose_step.subprocess, "run", side_effect=process):
            propose_step.propose_all([({"digest": "sha256:" + "0" * 64, "file": "a.wav",
                                       "object_id": "mix"}, entry)], args, {}, proposal,
                                     capture.content_path, lambda: self.tmp / "export.json")
        self.assertEqual(calls[-1], "propose")
        self.assertEqual(entry["propose"]["bank"], dict.fromkeys(
            ("chords-jiang", "chords-chordino", "rhythm", "tonal"), "ran"))
        self.assertEqual(entry["propose"]["status"], "nothing_proposed")


    def test_explicit_empty_selection_runs_no_features_and_dispatches_no_proposal(self):
        root = self.tmp / "empty-bank"
        root.mkdir()
        config = self.tmp / "empty.json"
        config.write_text(json.dumps({"composition": {"capture": {"features": [], "attempt": []}}}))
        args = argparse.Namespace(archive=self.tmp, bank=root, extract_config=config, jobs=1)
        def proposal(*words, **options):
            raise AssertionError("empty selection dispatched proposal")
        with patch.object(propose_step.subprocess, "run", side_effect=AssertionError("extraction ran")):
            propose_step.propose_all([({"digest": "sha256:" + "0" * 64, "file": "a.wav", "object_id": "a"}, {})],
                                    args, {}, proposal, capture.content_path, lambda: self.tmp / "export")

    def test_invalid_composition_is_refused_before_archive_reader_or_admission(self):
        config = self.tmp / "invalid.json"
        config.write_text(json.dumps({"composition": {"capture": {"attempt": ["unknown"]}}}))
        args = argparse.Namespace(archive=self.tmp, staging=self.tmp / "staging", derived_media=self.tmp / "media",
                                  directory=self.tmp / "input", bank=self.tmp / "bank", extract_config=config)
        args.derived_media.mkdir()
        args.directory.mkdir()
        with patch.object(capture, "environment", return_value={}), \
                patch.object(capture.adapter, "ArchiveReader", side_effect=AssertionError("archive opened")):
            with self.assertRaises(capture.CaptureError):
                capture.import_directory(args, [])

    def test_run_freezes_config_for_selection_and_every_child_then_restores_args(self):
        config = self.tmp / "mutable.json"
        original = {"composition": {"capture": {"features": [["rhythm"]], "attempt": ["beats"]}}}
        config.write_text(json.dumps(original))
        args = argparse.Namespace(archive=self.tmp, bank=self.tmp / "bank", extract_config=config, jobs=1)
        args.bank.mkdir()
        seen = []
        report = {}
        evidence = {"snapshot": "synthetic", "artifact": {"id": "a", "revision_id": "rev-a"}}
        def process(argv, **options):
            snapshot = Path(argv[argv.index("--config") + 1])
            self.assertNotEqual(snapshot, config)
            self.assertEqual(json.loads(snapshot.read_text()), original)
            config.write_text(json.dumps({"composition": {"capture": {"features": [["stems"]], "attempt": []}}}))
            seen.append(snapshot)
            return subprocess.CompletedProcess(argv, 0, json.dumps({"extractors": {"rhythm": {"status": "ran"}}}), "")
        def proposal(*words, **options):
            self.assertEqual(options["proposers"], "beats")
            self.assertEqual(json.loads(Path(args.extract_config).read_text()), original)
            self.assertEqual(args._enrichment_config_snapshot["identity"], bank.digest_of(bank.canonical(original)))
            return 3, {"status": "nothing_proposed"}
        with patch.object(propose_step.subprocess, "run", side_effect=process):
            propose_step.propose_all([({"digest": "sha256:" + "0" * 64, "file": "a.wav", "object_id": "a"}, report)],
                                    args, {}, proposal, capture.content_path, lambda: self.tmp / "export",
                                    lambda artifact: evidence)
        explained = report["propose"]["composition_explanation"]
        self.assertEqual(explained["evidence"], evidence)
        self.assertEqual(explained["config_identity"], bank.digest_of(bank.canonical(original)))
        self.assertEqual(explained["methods"][0]["method"], "beats")
        self.assertEqual(args.extract_config, config)
        self.assertFalse(hasattr(args, "_enrichment_config_snapshot"))
        self.assertTrue(seen)
        self.assertFalse(seen[0].exists())
