"""`enrich propose` end to end: a bank the real runner filled, a scratch
archive, the real Door, `archive show`, and World's views (ADR 0079)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import adapter
import bank
import propose
import support
from support import needs_door
from test_cover import needs_essentia
from test_propose import fill_bank, music

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

CHORDS = "archive.claim/chord_stacks"
METER = "archive.claim/meter_layers"
PERSON = "urn:uuid:00000000-0000-4000-8000-0000000000aa"


@needs_essentia
@needs_door
class ProposeCommand(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.directory = Path(tempfile.mkdtemp(prefix="propose-cli-bank-"))
        cls.wav = music(cls.directory / "take.wav")
        cls.quiet = support.sine_wav(cls.directory / "quiet.wav", 44100, "2")
        with patch.dict(os.environ):
            cls.bank = fill_bank(cls.directory, cls.wav)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.directory, True)

    def setUp(self) -> None:
        self.tmp = support.scratch(self, "propose-")
        self.env = {key: value for key, value in support.environment().items() if key != "ARCHIVE_FEATURE_BANK"}
        self.staging = self.tmp / "staging"
        self.root, manifest = support.build_archive(self, {"mix": self.wav, "quiet": self.quiet})
        self.objects = manifest["objects"]
        for key in ("mix", "quiet"):
            code, answer = self.enrich("probe", "--artifact", self.objects[key]["object_id"],
                                       "--ffprobe", support.FFPROBE)
            self.assertEqual(code, 0, answer)

    def enrich(self, command: str, *extra: str) -> tuple[int, dict]:
        completed = subprocess.run(
            [sys.executable, str(HERE / "enrich.py"), command, "--archive", str(self.root), "--door", support.DOOR,
             "--archive-command", support.ARCHIVE, "--ffmpeg", support.FFMPEG, "--staging", str(self.staging),
             *extra], capture_output=True, text=True, env=self.env)
        return completed.returncode, json.loads(completed.stdout or completed.stderr)

    def propose(self, *extra: str, key: str = "mix", bank_root: Path | None = None) -> tuple[int, dict]:
        target = ["--all"] if "--all" in extra else ["--artifact", self.objects[key]["object_id"]]
        return self.enrich("propose", *target, "--bank", str(bank_root or self.bank),
                           *[e for e in extra if e != "--all"])

    def show(self, selector: str) -> dict:
        shown = subprocess.run([support.ARCHIVE, "show", selector], cwd=self.root, capture_output=True, text=True,
                               env=self.env)
        self.assertEqual(shown.returncode, 0, shown.stdout)
        [record] = json.loads(shown.stdout)["records"]
        return record

    def made_by(self, run: dict) -> list[dict]:
        return [r for r in support.export(self.root)["records"]
                if any(ref["role"] == "run" and ref["revision_id"] == run["revision_id"] for ref in r["refs"])]

    def test_the_default_proposers_commit_world_ranks_them_and_a_rerun_is_unchanged(self) -> None:
        code, answer = self.propose()
        self.assertEqual((code, answer["status"]), (0, "committed"), answer)
        statuses = {p["name"]: p["status"] for p in answer["proposers"]}
        self.assertEqual(statuses, dict.fromkeys(("chords-jiang", "chords-chordino", "beats", "bass"), "committed"))
        mix = self.objects["mix"]
        self.assertEqual(answer["artifact"], {"id": mix["object_id"], "revision_id": mix["revision_id"]})
        runs = {p["name"]: p["run"] for p in answer["proposers"]}

        for name, run_pin in runs.items():
            run = self.show(run_pin["id"])
            self.assertEqual(run["payload"]["method"], {"id": name, "version": "1"})
            self.assertEqual(run["agent"], propose.AGENT)
            self.assertEqual(run["payload"]["software"][0], {"name": "archive-enrich-propose", "version": "0.1.0"})
            used = sorted((r["id"], r["revision_id"]) for r in run["refs"] if r["role"] == "used")
            self.assertEqual(used, sorted([(mix["object_id"], mix["revision_id"]), (answer["stream"]["id"],
                                                                                    answer["stream"]["revision_id"])]))
            [parameters] = [r for r in run["refs"] if r["role"] == "parameters"]
            content = self.show(parameters["id"])["payload"]["content"]
            stored = (self.root / "content" / "sha256" / content["digest"][7:9] / content["digest"][9:11]
                      / content["digest"][7:]).read_bytes()
            self.assertEqual(json.loads(stored)["proposer"], {"id": name, "version": "1"})
            [claim] = self.made_by(run_pin)
            self.assertEqual(claim["agent"], propose.AGENT)
            self.assertEqual(sorted(r["role"] for r in claim["refs"]), ["run", "source", "stream"])
            contract = METER if name == "beats" else CHORDS
            self.assertEqual([(c["id"], c["version"]) for c in claim["contracts"]], [(contract, "2")])

        [bass] = self.made_by(runs["bass"])
        stems = bank.Bank(self.bank).entry(adapter.digest_of(self.wav.read_bytes()), "stems")
        self.assertEqual(bass["payload"]["stem"]["key"], stems.manifest["key"])

        # World's first current rule ranks the machine chords by their run's
        # method, and takes the bass from the bass stem's claim.
        from world import view_records
        from world.world import build_projection
        projection = build_projection(support.export(self.root))
        request = {"view": "archive.view/chords_current", "version": "1",
                   "subject": {"source": {"id": mix["object_id"], "revision_id": mix["revision_id"]}},
                   "parameters": {"person": PERSON}}
        segments = view_records.answer(projection, request)["answer"]["segments"]
        [jiang] = self.made_by(runs["chords-jiang"])
        chosen = [s for s in segments if s.get("chosen", {}).get("claim") == jiang["revision_id"]]
        self.assertTrue(chosen, segments)
        self.assertIn("machine chords-jiang, 1 in the stated order", chosen[0]["why"][0])
        self.assertTrue(any(s.get("bass_from", {}).get("claim") == bass["revision_id"] for s in segments),
                        segments)

        head = support.head(self.root)
        code, again = self.propose()
        self.assertEqual((code, again["status"]), (0, "unchanged"), again)
        self.assertEqual({p["name"]: p["run"] for p in again["proposers"]}, runs)
        self.assertEqual(support.head(self.root), head)

    def test_named_proposers_and_what_a_bare_recording_lacks(self) -> None:
        code, answer = self.propose("--proposers", "key,tuning,sections")
        self.assertEqual((code, answer["status"]), (0, "committed"), answer)
        self.assertEqual([p["status"] for p in answer["proposers"]], ["committed"] * 3)
        code, answer = self.propose(key="quiet")
        self.assertEqual((code, answer["status"]), (3, "nothing_proposed"), answer)
        self.assertEqual(answer["proposers"][3], {"name": "bass", "status": "nothing_proposed", "missing": [
            "stems/bass", "tonal/bass_chroma from stems/bass", "tonal/pitched from stems/bass", "rhythm/pulse"]})

    def test_forced_alignment_is_refused_as_not_yet(self) -> None:
        head = support.head(self.root)
        code, answer = self.propose("--proposers", "timed-lyrics")
        self.assertEqual((code, answer["status"]), (3, "nothing_proposed"), answer)
        [only] = answer["proposers"]
        self.assertEqual((only["name"], only["code"]), ("timed-lyrics", "not_yet"))
        self.assertEqual(support.head(self.root), head)

    def test_a_dry_run_predicts_and_writes_nothing(self) -> None:
        head = support.head(self.root)
        code, answer = self.propose("--dry-run", "--proposers", "chords-chordino")
        self.assertEqual((code, answer["status"]), (0, "predicted"), answer)
        self.assertEqual(support.head(self.root), head)

    def test_all_proposes_for_every_artifact_with_a_stream(self) -> None:
        code, answer = self.propose("--all", "--proposers", "chords-chordino,beats")
        self.assertEqual((code, answer["status"]), (0, "committed"), answer)
        by_artifact = {a["artifact"]["id"]: a["status"] for a in answer["artifacts"]}
        self.assertEqual(by_artifact, {self.objects["mix"]["object_id"]: "committed",
                                       self.objects["quiet"]["object_id"]: "nothing_proposed"})
        code, answer = self.propose("--all", "--proposers", "chords-chordino,beats")
        self.assertEqual((code, answer["status"]), (0, "unchanged"), answer)
        # The runs' parameters Artifacts are not recordings, so nothing is unprobed.
        self.assertNotIn("unprobed", answer)

    def test_all_names_the_recordings_never_probed(self) -> None:
        root, manifest = support.build_archive(self, {"mix": self.wav, "quiet": self.quiet})
        self.root, self.objects = root, manifest["objects"]
        code, answer = self.enrich("probe", "--artifact", self.objects["mix"]["object_id"],
                                   "--ffprobe", support.FFPROBE)
        self.assertEqual(code, 0, answer)
        code, answer = self.propose("--all", "--proposers", "beats")
        self.assertEqual((code, answer["status"]), (0, "committed"), answer)
        quiet = self.objects["quiet"]
        self.assertEqual(answer["unprobed"], [{"artifact": {"id": quiet["object_id"],
                                                            "revision_id": quiet["revision_id"]},
                                               "media_type": "audio/wav"}])
        self.assertIn("1 audio or video Artifact has no audio_stream claim", answer["detail"])
        self.assertIn("enrich probe --artifact", answer["detail"])

    def test_a_failed_proposer_keeps_what_the_ones_before_it_made(self) -> None:
        damaged = self.tmp / "bank"
        shutil.copytree(self.bank, damaged)
        entry = bank.Bank(damaged).entry(adapter.digest_of(self.wav.read_bytes()), "chords-jiang")
        readings = entry.file("readings")
        document = json.loads(readings.read_text())
        document["spans"] = document["spans"][1:]
        readings.write_text(json.dumps(document))
        code, answer = self.propose("--proposers", "chords-chordino,chords-jiang,beats", bank_root=damaged)
        self.assertEqual((code, answer["status"]), (1, "failed"), answer)
        self.assertIn("does not describe its chords spans one by one", answer["detail"])
        self.assertEqual([(p["name"], p["status"]) for p in answer["proposers"]],
                         [("chords-chordino", "committed"), ("chords-jiang", "failed")])
        self.assertEqual(len(self.made_by(answer["proposers"][0]["run"])), 1)

    def test_refusals(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(HERE / "enrich.py"), "propose", "--archive", str(self.root), "--artifact",
             self.objects["mix"]["object_id"], "--door", support.DOOR, "--archive-command", support.ARCHIVE],
            capture_output=True, text=True, env=self.env)
        self.assertEqual(completed.returncode, 1)
        self.assertIn("no feature bank", json.loads(completed.stderr)["detail"])
        code, answer = self.propose("--proposers", "chords,beats")
        self.assertEqual((code, answer["status"]), (1, "failed"))
        self.assertIn("--proposers takes distinct names", answer["detail"])
        code, answer = self.propose("--all", "--stream", self.objects["mix"]["object_id"])
        self.assertEqual(code, 1)
        self.assertIn("cannot go with --all", answer["detail"])


if __name__ == "__main__":
    unittest.main()
