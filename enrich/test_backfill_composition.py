"""Composition through backfill's existing placement and scheduling adapter."""
import argparse
import json
import gc
import io
import warnings
from contextlib import redirect_stderr, redirect_stdout
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import backfill
import bank
import composition
from test_backfill import CATALOG, recording, schedule, FakePlanner


class CompositionCaller(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.programs = {match.group(1): "configured" for e in CATALOG.extractors.values()
                         for word in e.manifest["command"] if (match := bank.PROGRAM.fullmatch(word))}

    def choose(self, methods, **fields):
        results = [{"contract": list(backfill.proposers.PROPOSERS[m].requirement.contracts[0]),
                    "methods": [m]} for m in methods]
        config = {"composition": {"backfill": {"features": [], "attempt": [], "produce": results, **fields}}}
        return backfill.selection_of(config, CATALOG, schedule(), None)

    def planning(self, chosen, mode="eager"):
        planner = FakePlanner()
        planner.root = self.root
        planner.reader = bank.Bank(self.root, CATALOG)
        planner.programs = self.programs
        sched = schedule(())
        resources = backfill.replace(sched, units=backfill.candidate_units(chosen, CATALOG))
        shape = backfill.layout(CATALOG, resources, {}, 8, 32)
        return backfill.Planning(sched, planner, mode, shape, False, chosen)

    def test_explicit_bass_recipe_uses_existing_source_dependency_and_refresh_is_idempotent(self):
        planning = self.planning(self.choose(["bass"]))
        rec = recording("bass")
        jobs = planning.recording(rec)
        by_unit = {j.unit: j for j in jobs}
        stems = by_unit[backfill.Unit("stems")]
        tonal = by_unit[backfill.Unit("tonal", "stems/bass")]
        self.assertIn(stems, tonal.after)
        self.assertEqual(planning.refresh(rec), [])
        self.assertEqual(len(planning.result.jobs), len(jobs))
        self.assertNotIn(backfill.Unit("notes-basic-pitch", "stems/bass"), by_unit)

    def test_requested_results_respect_on_demand_and_song_placement(self):
        rec = recording("regions", songs=True)
        demand = self.planning(self.choose(["bass"]), "on-demand")
        self.assertEqual(demand.recording(rec), [])
        self.assertTrue(demand.result.deferred)
        songs = self.planning(self.choose(["bass"]), "songs")
        jobs = songs.recording(rec)
        self.assertTrue(any(j.unit.extractor == "stems" and j.region is not None for j in jobs))
        self.assertTrue(all(not j.requested for j in jobs))
        bass = [j for j in jobs if j.unit == backfill.Unit("tonal", "stems/bass")]
        self.assertEqual([j.region for j in bass], rec.regions)
        self.assertEqual(bass[0].after, [j for j in jobs if j.unit == backfill.Unit("stems")])

    def test_attempts_do_not_expand_and_empty_cli_overrides_only_attempts(self):
        config = {"composition": {"backfill": {"features": [], "attempt": ["bass"]}}}
        chosen = backfill.selection_of(config, CATALOG, schedule(), None)
        planning = self.planning(chosen)
        self.assertEqual(planning.recording(recording("attempt")), [])
        self.assertEqual(planning.refresh(recording("attempt")), [])
        self.assertEqual(backfill.selection_of(config, CATALOG, schedule(), "").attempt, ())
        produced = self.choose(["beats"], attempt=["bass"])
        override = composition.selection({}, "backfill", produced, "")
        self.assertEqual(override.attempt, ())
        self.assertEqual(override.produce, produced.produce)

    def test_invalid_selection_fails_before_bank_creation_or_discovery(self):
        config = self.root / "bad.json"
        config.write_text(json.dumps({"composition": {"backfill": {"attempt": ["unknown"]}}}))
        parser = argparse.ArgumentParser()
        backfill.add_arguments(parser)
        args = parser.parse_args(["--config", str(config), "--bank", str(self.root / "absent")])
        with patch.object(backfill, "discover", side_effect=AssertionError("discovery ran")):
            with self.assertRaises(backfill.BackfillError):
                backfill.main(args)
        self.assertFalse((self.root / "absent").exists())

    def test_failed_selected_bass_line_path_does_not_schedule_alternative(self):
        planning = self.planning(self.choose(["bass-line"]))
        rec = recording("failed")
        planning.planner.states[(rec.object_id, backfill.Unit("f0-pesto", "stems/bass"))] = backfill.FAILED
        jobs = planning.recording(rec)
        self.assertFalse(any(j.unit.extractor == "notes-basic-pitch" for j in jobs))
        self.assertTrue(any(unit == backfill.Unit("f0-pesto", "stems/bass") and state == backfill.FAILED
                            for _, unit, state, _ in planning.result.held))
        self.assertEqual(planning.refresh(rec), [])

    def test_successful_prerequisite_reveals_target_through_existing_want(self):
        chosen = self.choose(["notes"], features=[["stems"]])
        planning = self.planning(chosen)
        rec = recording("preferred")
        first = composition.Expansion((composition.FeatureTarget("stems"), composition.FeatureTarget("melody")),
                                      ("notes",), ())
        later = composition.Expansion((composition.FeatureTarget("stems"),
                                       composition.FeatureTarget("f0-pesto", "stems/vocals")), ("notes",), ())
        with patch.object(composition, "expand", return_value=first):
            planning.recording(rec)
        stems = next(j for j in planning.result.jobs if j.unit.extractor == "stems")
        planning.planner.made(rec, stems.unit)
        stems.status = "done"
        with patch.object(composition, "expand", return_value=later):
            added = planning.refresh(rec)
            self.assertEqual([j.unit for j in added], [backfill.Unit("f0-pesto", "stems/vocals")])
            self.assertIn(stems, added[0].after)
            self.assertEqual(planning.refresh(rec), [])

    def test_explanations_keep_regions_evidence_routes_and_dispatch_causes(self):
        planning = self.planning(self.choose(["bass"]))
        planning.planner.config_identity = "frozen-synthetic"
        rec = recording("explanation")
        rec.evidence = {"snapshot": "synthetic", "artifact": {"id": rec.object_id, "revision_id": "rev"}}
        region = backfill.Region(10, 20, 10)
        with patch("subprocess.Popen", side_effect=AssertionError("dry explanation dispatched process")):
            planning.targets(rec)
            planning.targets(rec, region)
            planning.targets(rec, region)
        explanations = planning.explanations[rec.object_id]
        self.assertEqual([e["region"] for e in explanations], [None, region.as_json()])
        self.assertEqual(explanations[-1]["evidence"], rec.evidence)
        self.assertEqual(explanations[-1]["config_identity"], "frozen-synthetic")
        target = next(t for t in explanations[-1]["targets"] if t["source"] == "stems/bass")
        self.assertEqual(target["dispatch_observation"]["status"], backfill.WAITING)
        self.assertIsNone(target["key"])
        self.assertIsNotNone(target["parameters"])
        self.assertIn("source", target["route"]["value"])
        self.assertIn("source", target["cost"]["value"])
        self.assertTrue(any(d["region"] == region.as_json() for d in planning.diagnostics[rec.object_id]))

    def test_config_snapshot_is_nested_and_restored_even_on_failure(self):
        path = self.root / "config.json"
        path.write_text(json.dumps({"parameters": {}}))
        args = argparse.Namespace(config=path)
        with self.assertRaisesRegex(RuntimeError, "stop"):
            with backfill.extract.frozen_config(args, "config") as outer:
                snapshot = Path(args.config)
                path.write_text(json.dumps({"composition": {"backfill": {"attempt": []}}}))
                with backfill.extract.frozen_config(args, "config") as inner:
                    self.assertIs(inner, outer)
                    self.assertEqual(backfill.extract.load_config(args.config), {"parameters": {}})
                raise RuntimeError("stop")
        self.assertEqual(args.config, path)
        self.assertFalse(snapshot.exists())
        self.assertFalse(hasattr(args, "_enrichment_config_snapshot"))


class LogLifetime(unittest.TestCase):
    def test_backfill_closes_log_on_success_and_failure(self):
        with tempfile.TemporaryDirectory() as root:
            parser = argparse.ArgumentParser()
            backfill.add_arguments(parser)
            for cores in (1, -1):
                with self.subTest(cores=cores):
                    args = parser.parse_args([
                        "--bank", root + "/bank", "--log", root + "/events.jsonl",
                        "--cores", str(cores), "--proposers", "",
                    ])
                    handles = []
                    real_log = backfill.Log

                    def log(path):
                        result = real_log(path)
                        handles.append(result.handle)
                        return result

                    with patch.object(backfill, "Log", side_effect=log), \
                            patch.object(backfill, "discover", return_value=([], [])), \
                            redirect_stdout(io.StringIO()):
                        if cores < 0:
                            with self.assertRaises(backfill.BackfillError):
                                backfill.main(args)
                        else:
                            self.assertEqual(backfill.main(args), 0)
                    # A leaked file can warn during a later command's JSON stderr.
                    closed = [handle.closed for handle in handles]
                    errors = io.StringIO()
                    with warnings.catch_warnings(), redirect_stderr(errors):
                        warnings.simplefilter("always", ResourceWarning)
                        handles.clear()
                        gc.collect()
                        errors.write(json.dumps({"detail": "door admit failed"}))
                    self.assertEqual(json.loads(errors.getvalue()), {"detail": "door admit failed"})
                    self.assertEqual(closed, [True])
