"""`enrich probe` end to end: a scratch archive, real Door, archive show, and World."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

import adapter
import probe
import support
from support import needs_door
from test_probe import fake_program

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

PROV = "http://www.w3.org/ns/prov#"
OA = "http://www.w3.org/ns/oa#"


@needs_door
class ProbeCommand(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = support.scratch(self, "enrich-cli-")
        self.env = support.environment()
        wav = support.sine_wav(self.tmp / "take.wav", 44100, "1.5", channels=2)
        flac = support.noise_flac(self.tmp / "take.flac", 48000, "1.25")
        video = self.tmp / "picture.mkv"
        support.ffmpeg("-f", "lavfi", "-i", "testsrc=d=0.2:s=32x32", "-c:v", "ffv1", str(video))
        self.root, manifest = support.build_archive(self, {"wav": wav, "flac": flac, "video": video})
        self.objects = manifest["objects"]
        self.staging = self.tmp / "staging"

    def enrich(self, key: str, *extra: str, ffmpeg: str | None = None) -> tuple[int, dict]:
        target = self.objects[key]["object_id"] if key in self.objects else key
        completed = subprocess.run(
            [sys.executable, str(HERE / "enrich.py"), "probe", "--archive", str(self.root), "--artifact", target,
             "--door", support.DOOR, "--archive-command", support.ARCHIVE, "--ffmpeg", ffmpeg or support.FFMPEG,
             "--ffprobe", support.FFPROBE, *extra], capture_output=True, text=True, env=self.env)
        return completed.returncode, json.loads(completed.stdout or completed.stderr)

    def show(self, selector: str) -> dict:
        shown = subprocess.run([support.ARCHIVE, "show", selector], cwd=self.root, capture_output=True, text=True,
                               env=self.env)
        self.assertEqual(shown.returncode, 0, shown.stdout)
        [record] = json.loads(shown.stdout)["records"]
        return record

    def test_wav_and_flac_facts_reach_the_archive_and_world(self) -> None:
        code, wav = self.enrich("wav", "--staging", str(self.staging))
        self.assertEqual((code, wav["status"]), (0, "committed"), wav)
        code, flac = self.enrich("flac", "--staging", str(self.staging))
        self.assertEqual((code, flac["status"]), (0, "committed"), flac)
        # The second run reuses the parameters Artifact the first admitted.
        self.assertFalse(wav["parameters"]["reused"])
        self.assertTrue(flac["parameters"]["reused"])
        self.assertEqual(flac["parameters"]["object_id"], wav["parameters"]["object_id"])
        self.assertNotEqual(flac["run"]["object_id"], wav["run"]["object_id"])

        stream = self.show(wav["outputs"][0]["object_id"])
        self.assertEqual(stream["payload"], {"stream_index": 0, "codec": "pcm_s16le", "sample_rate": 44100,
                                             "channel_count": 2, "sample_count": 66150, "bits_per_sample": 16})
        self.assertEqual(stream["agent"], probe.AGENT)
        self.assertEqual(sorted(ref["role"] for ref in stream["refs"]), ["artifact", "run"])
        artifact_ref = next(ref for ref in stream["refs"] if ref["role"] == "artifact")
        self.assertEqual((artifact_ref["id"], artifact_ref["revision_id"]),
                         (self.objects["wav"]["object_id"], self.objects["wav"]["revision_id"]))
        run = self.show(wav["run"]["object_id"])
        self.assertEqual(run["payload"]["method"], {"id": probe.METHOD_ID, "version": probe.METHOD_VERSION})
        self.assertEqual([s["name"] for s in run["payload"]["software"]][:2], ["ffprobe", "ffmpeg"])
        self.assertLessEqual(run["payload"]["started_at"], run["payload"]["ended_at"])
        parameters = self.show(wav["parameters"]["object_id"])
        data = adapter.canonical(probe.parameters())
        self.assertEqual(parameters["payload"]["content"], {"digest": adapter.digest_of(data), "size": len(data)})
        flac_stream = self.show(flac["outputs"][0]["object_id"])
        self.assertEqual((flac_stream["payload"]["codec"], flac_stream["payload"]["sample_count"]), ("flac", 60000))
        self.assertNotIn("bits_per_sample", flac_stream["payload"])

        from world.world import _record_uri, build_projection, query
        projection = build_projection(support.export(self.root))
        answer = query(projection, f"""
            PREFIX prov: <{PROV}> PREFIX oa: <{OA}>
            SELECT ?stream ?run ?used ?body WHERE {{ GRAPH ?g {{
              ?stream prov:wasGeneratedBy ?run ; oa:hasBody ?body . ?run prov:used ?used . }} }}""").data
        rows = {row["stream"]["value"]: row for row in answer["results"]["bindings"]}
        archive_id = projection.archive_id
        self.assertEqual(len(rows), 2)
        row = rows[_record_uri(archive_id, wav["outputs"][0]["revision_id"]).value]
        self.assertEqual(row["run"]["value"], _record_uri(archive_id, wav["run"]["revision_id"]).value)
        self.assertEqual(row["used"]["value"], _record_uri(archive_id, self.objects["wav"]["revision_id"]).value)
        self.assertEqual(json.loads(row["body"]["value"])["sample_count"], 66150)

    def test_a_rerun_on_unchanged_inputs_writes_nothing_and_decodes_nothing(self) -> None:
        code, first = self.enrich("wav", "--staging", str(self.staging))
        self.assertEqual(code, 0, first)
        before = support.head(self.root)
        # A decoder that reports its version but explodes on any decode.
        marker = self.tmp / "decoded"
        bomb = fake_program(self.tmp, "ffmpeg-bomb",
                            f'if [ "$1" = "-version" ]; then exec "{support.FFMPEG}" -version; fi\n'
                            f'touch "{marker}"; exit 99\n')
        code, again = self.enrich("wav", "--staging", str(self.staging), ffmpeg=bomb)
        self.assertEqual((code, again["status"]), (0, "unchanged"), again)
        self.assertEqual(again["run"], first["run"])
        self.assertFalse(marker.exists())
        self.assertEqual(support.head(self.root), before)

    def test_a_dry_run_predicts_and_writes_nothing(self) -> None:
        before = support.head(self.root)
        code, answer = self.enrich("wav", "--staging", str(self.staging), "--dry-run")
        self.assertEqual((code, answer["status"]), (0, "predicted"), answer)
        self.assertEqual(len(answer["revision_ids"]), 3)
        self.assertEqual(support.head(self.root), before)
        self.assertFalse(self.staging.exists())

    def test_new_parameters_without_staging_are_refused(self) -> None:
        before = support.head(self.root)
        code, answer = self.enrich("wav")
        self.assertEqual(code, 1)
        self.assertIn("staging is needed", answer["detail"])
        self.assertEqual(support.head(self.root), before)

    def test_an_artifact_without_audio_proposes_nothing(self) -> None:
        before = support.head(self.root)
        code, answer = self.enrich("video", "--staging", str(self.staging))
        self.assertEqual((code, answer["status"]), (2, "nothing_proposed"), answer)
        self.assertEqual(answer["probe"], {"streams": [], "refused": []})
        self.assertEqual(support.head(self.root), before)
        self.assertFalse(self.staging.exists())

    def test_an_object_that_is_not_an_artifact_is_refused(self) -> None:
        code, answer = self.enrich("operator")
        self.assertEqual(code, 1)
        self.assertIn("not an Artifact", answer["detail"])

    def test_content_that_is_absent_or_altered_is_refused(self) -> None:
        digest = self.show(self.objects["flac"]["object_id"])["payload"]["content"]["digest"]
        hexdigest = digest.removeprefix("sha256:")
        relative = Path("content") / "sha256" / hexdigest[:2] / hexdigest[2:4] / hexdigest
        local = self.root / relative
        self.assertTrue(local.is_file())
        # Altered bytes: bit rot in this scratch archive's own annex object.
        target = local.resolve()
        os.chmod(target.parent, 0o700)
        os.chmod(target, 0o600)
        target.write_bytes(target.read_bytes()[:-1] + b"\x00")
        code, answer = self.enrich("flac", "--staging", str(self.staging))
        self.assertEqual(code, 1)
        self.assertIn("does not match its record", answer["detail"])
        # Absent bytes: the annex object is gone from this scratch archive.
        target.unlink()
        code, answer = self.enrich("flac", "--staging", str(self.staging))
        self.assertEqual(code, 1)
        self.assertIn("not locally present", answer["detail"])

    def test_git_that_cannot_be_found_is_refused(self) -> None:
        env = {key: value for key, value in self.env.items() if not key.startswith("ARCHIVE_GIT")}
        env["PATH"] = str(self.tmp / "empty-path")
        completed = subprocess.run([sys.executable, str(HERE / "enrich.py"), "probe", "--archive", str(self.root),
                                    "--artifact", self.objects["wav"]["object_id"]],
                                   capture_output=True, text=True, env=env)
        self.assertEqual(completed.returncode, 1)
        self.assertIn("git is not on PATH", json.loads(completed.stderr)["detail"])

    def test_an_unknown_object_is_refused(self) -> None:
        code, answer = self.enrich("urn:uuid:00000000-0000-4000-8000-000000000000")
        self.assertEqual(code, 1)
        self.assertIn("holds no object", answer["detail"])


if __name__ == "__main__":
    unittest.main()
