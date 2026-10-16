"""The adapter: canonical bytes, proposer obligations, staging, reuse, and Door (ADR 0064)."""

from __future__ import annotations

import json
import subprocess
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import adapter
import support
from support import needs_door
from test_probe import fake_program

REPOSITORY = Path(__file__).resolve().parent.parent
STREAM = ("archive.claim/audio_stream", "1")
PAYLOAD = {"stream_index": 0, "codec": "pcm_s16le", "sample_rate": 8000, "channel_count": 1, "sample_count": 4000}


class Canonical(unittest.TestCase):
    def test_every_specification_bundle_hashes_to_its_file_name(self) -> None:
        bundles = sorted((REPOSITORY / "spec" / "contracts").glob("sha256-*.json"))
        self.assertTrue(bundles)
        for path in bundles:
            bundle = json.loads(path.read_bytes())
            self.assertEqual(adapter.canonical(bundle), path.read_bytes(), path.name)
            self.assertEqual(adapter.digest_of(adapter.canonical(bundle)),
                             "sha256:" + path.stem.removeprefix("sha256-"))

    def test_members_are_ordered_by_utf16_code_units(self) -> None:
        # RFC 8785 section 3.2.3's example keys, in its stated order.
        keys = ["\r", "1", "\u0080", "ö", "€", "\U0001f600", "דּ"]
        encoded = adapter.canonical({key: 0 for key in reversed(keys)}).decode("utf-8")
        positions = [encoded.index(json.dumps(key, ensure_ascii=False)) for key in keys]
        self.assertEqual(positions, sorted(positions))

    def test_values_canonical_json_here_cannot_hold_are_refused(self) -> None:
        for value in (1.5, {1: "x"}, {"x": object()}):
            with self.assertRaises(adapter.AdapterError):
                adapter.canonical(value)
        self.assertEqual(adapter.canonical([True, None, -3, "é"]), '[true,null,-3,"é"]'.encode())


class ProposerObligations(unittest.TestCase):
    def test_package_urls_follow_d7(self) -> None:
        for good in ("pkg:generic/archive-enrich-probe@0.1.0", "pkg:pypi/some-tool@1.2.3", adapter.BY):
            self.assertEqual(adapter.check_package_url(good), good)
        for bad in ("generic/tool@1", "pkg:", "pkg:generic", "pkg:generic/tool@1?arch=x", "pkg:generic/tool@1#sub",
                    "pkg:generic//tool", "pkg:generic/example.com/tool", "pkg:generic/to ol", "pkg:generic/tool@1:2"):
            with self.assertRaises(adapter.AdapterError, msg=bad):
                adapter.check_package_url(bad)

    def test_run_names_are_fresh_uuidv4_tokens(self) -> None:
        first, second = adapter.new_run_name(), adapter.new_run_name()
        self.assertNotEqual(first, second)
        self.assertRegex(first, r"^run-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")

    def test_instants_are_utc_with_z(self) -> None:
        moment = datetime(2001, 2, 3, 4, 5, 6, 7, tzinfo=timezone(timedelta(hours=2)))
        self.assertEqual(adapter.utc_instant(moment), "2001-02-03T02:05:06.000007Z")
        with self.assertRaises(adapter.AdapterError):
            adapter.utc_instant(datetime(2001, 2, 3))


def result(used: tuple[adapter.Pin, ...] = (), parameters=None, **changes) -> adapter.RunResult:
    started = adapter.now()
    fields = dict(agent="pkg:generic/archive-enrich-test@1", method_id="test-method", method_version="1",
                  software=(("tool", "1.0"),), started_at=started, ended_at=started + timedelta(seconds=1),
                  used=used, outputs=(adapter.Output(STREAM, "stream-0", dict(PAYLOAD)),), parameters=parameters)
    fields.update(changes)
    return adapter.RunResult(**fields)


@needs_door
class Proposals(unittest.TestCase):
    """Against a scratch archive whose one Artifact the fixture builder admitted."""

    def setUp(self) -> None:
        self.tmp = support.scratch(self, "adapter-")
        wav = support.sine_wav(self.tmp / "a.wav", 8000, "0.5")
        self.root, manifest = support.build_archive(self, {"wav": wav})
        self.wav = manifest["objects"]["wav"]
        self.env = support.environment()
        self.reader = adapter.ArchiveReader(self.root, support.ARCHIVE, self.env)
        self.used = (adapter.Pin("used", self.wav["object_id"], self.wav["revision_id"]),)
        self.artifact_pin = adapter.Pin("artifact", self.wav["object_id"], self.wav["revision_id"])

    def staging(self, path: Path | None = None) -> adapter.Staging:
        return adapter.Staging(path or self.tmp / "staging", self.env["ARCHIVE_GIT_EXECUTABLE"],
                               self.env["ARCHIVE_GIT_ANNEX_EXECUTABLE"], self.env)

    def run_result(self, **changes) -> adapter.RunResult:
        changes.setdefault("outputs", (adapter.Output(STREAM, "stream-0", dict(PAYLOAD), (self.artifact_pin,)),))
        changes.setdefault("used", self.used)
        return result(**changes)

    def propose(self, run: adapter.RunResult, **options) -> adapter.Admission:
        options.setdefault("door", support.DOOR)
        options.setdefault("env", self.env)
        return adapter.propose(self.reader, run, **options)

    def show(self, selector: str) -> list[dict]:
        shown = subprocess.run([support.ARCHIVE, "show", selector], cwd=self.root, capture_output=True,
                               text=True, env=self.env)
        self.assertEqual(shown.returncode, 0, shown.stdout)
        return json.loads(shown.stdout)["records"]

    def test_a_run_and_its_outputs_commit_in_one_batch(self) -> None:
        before = support.head(self.root)
        admission = self.propose(self.run_result())
        self.assertEqual(admission.status, "committed")
        after = support.head(self.root)
        self.assertNotEqual(before, after)
        # One commit holds both: its parent is the commit before.
        parent = subprocess.run(["git", "rev-parse", "HEAD^"], cwd=self.root, capture_output=True,
                                text=True, check=True).stdout.strip()
        self.assertEqual(parent, before)
        [run] = self.show(admission.run["object_id"])
        [stream] = self.show(admission.outputs[0]["object_id"])
        self.assertEqual(run["agent"], "pkg:generic/archive-enrich-test@1")
        self.assertEqual(run["by"], adapter.BY)
        self.assertEqual(run["payload"]["method"], {"id": "test-method", "version": "1"})
        self.assertEqual(run["payload"]["software"], [{"name": "tool", "version": "1.0"}])
        self.assertEqual(run["refs"], [{"role": "used", "id": self.wav["object_id"],
                                        "revision_id": self.wav["revision_id"]}])
        self.assertIn({"role": "run", "id": run["object_id"], "revision_id": run["revision_id"]}, stream["refs"])
        self.assertEqual(stream["payload"], PAYLOAD)

    def test_new_parameters_are_admitted_as_content_and_then_reused(self) -> None:
        parameters = {"threshold": 3, "preset": "plain"}
        first = self.propose(self.run_result(parameters=parameters), staging=self.staging())
        self.assertFalse(first.parameters["reused"])
        [artifact] = self.show(first.parameters["object_id"])
        data = adapter.canonical(parameters)
        self.assertEqual(artifact["payload"], {"content": {"digest": adapter.digest_of(data), "size": len(data)}})
        hexdigest = adapter.digest_of(data).removeprefix("sha256:")
        stored = self.root / "content" / "sha256" / hexdigest[:2] / hexdigest[2:4] / hexdigest
        self.assertEqual(stored.read_bytes(), data)
        [run] = self.show(first.run["object_id"])
        self.assertIn({"role": "parameters", "id": artifact["object_id"], "revision_id": artifact["revision_id"]},
                      run["refs"])

        class Bomb(adapter.Staging):
            def stage(self, data: bytes, filename: str) -> str:
                raise AssertionError("reused parameters must not be staged")

            def remote_name(self, archive_root: Path, create: bool) -> str:
                raise AssertionError("reused parameters need no remote")

        self.reader = adapter.ArchiveReader(self.root, support.ARCHIVE, self.env)
        second = self.propose(self.run_result(parameters=parameters), staging=Bomb(self.tmp / "staging", "", ""))
        self.assertTrue(second.parameters["reused"])
        self.assertEqual(second.parameters["object_id"], first.parameters["object_id"])
        self.assertNotEqual(second.run["object_id"], first.run["object_id"])

    def test_new_parameters_without_staging_are_refused_before_door(self) -> None:
        before = support.head(self.root)
        with self.assertRaisesRegex(adapter.AdapterError, "staging is needed"):
            self.propose(self.run_result(parameters={"x": 1}), door="/nonexistent/door")
        self.assertEqual(support.head(self.root), before)

    def test_a_dry_run_stages_and_configures_nothing(self) -> None:
        before = support.head(self.root)
        staging = self.tmp / "staging"
        admission = self.propose(self.run_result(parameters={"x": 1}), staging=self.staging(staging), dry_run=True)
        self.assertEqual(admission.status, "predicted")
        self.assertEqual(len(admission.revision_ids), 3)
        self.assertEqual(support.head(self.root), before)
        self.assertFalse(staging.exists())
        remotes = subprocess.run(["git", "remote"], cwd=self.root, capture_output=True, text=True).stdout.split()
        self.assertNotIn(adapter.STAGING_REMOTE, remotes)

    def test_the_predicted_key_is_the_key_git_annex_gives(self) -> None:
        staging = self.staging()
        data = adapter.canonical({"x": 1})
        name = adapter.digest_of(data).removeprefix("sha256:") + ".json"
        self.assertEqual(staging.stage(data, name), staging.expected_key(data, name))
        # Staging the same bytes again writes nothing.
        commit = support.head(staging.path)
        staging.stage(data, name)
        self.assertEqual(support.head(staging.path), commit)

    def test_a_staging_repository_with_another_backend_is_refused(self) -> None:
        staging = self.staging()
        staging.stage(b"{}", "seed.json")
        subprocess.run(["git", "config", "annex.backend", "SHA256"], cwd=staging.path, check=True)
        data = adapter.canonical({"x": 2})
        with self.assertRaisesRegex(adapter.AdapterError, "not the key"):
            staging.stage(data, "other.json")

    def test_a_staging_path_that_is_not_a_repository_is_refused(self) -> None:
        path = self.tmp / "occupied"
        path.mkdir()
        (path / "file").write_text("x")
        with self.assertRaisesRegex(adapter.AdapterError, "neither empty nor a git repository"):
            self.staging(path).stage(b"{}", "x.json")

    def test_the_remote_is_found_by_path_and_never_redirected(self) -> None:
        staging = self.staging()
        staging.stage(b"{}", "seed.json")
        subprocess.run(["git", "remote", "add", "elsewhere-name", str(staging.path)], cwd=self.root, check=True)
        self.assertEqual(staging.remote_name(self.root, create=True), "elsewhere-name")
        other = self.staging(self.tmp / "other")
        subprocess.run(["git", "remote", "add", adapter.STAGING_REMOTE, str(self.tmp / "third")], cwd=self.root,
                       check=True)
        with self.assertRaisesRegex(adapter.AdapterError, "not the staging repository"):
            other.remote_name(self.root, create=True)

    def test_a_failing_remote_listing_is_refused(self) -> None:
        git = fake_program(self.tmp, "git", "exit 2\n")
        staging = adapter.Staging(self.tmp / "staging", git, "", self.env)
        with self.assertRaisesRegex(adapter.AdapterError, "reading the archive's remotes failed"):
            staging.remote_name(self.root, create=False)

    def test_door_refusing_the_batch_leaves_the_archive_unchanged(self) -> None:
        before = support.head(self.root)
        broken = {key: value for key, value in PAYLOAD.items() if key != "sample_count"}
        run = self.run_result(outputs=(adapter.Output(STREAM, "stream-0", broken, (self.artifact_pin,)),))
        with self.assertRaisesRegex(adapter.AdapterError, "door admit failed"):
            self.propose(run)
        self.assertEqual(support.head(self.root), before)

    def test_a_door_answer_other_than_the_plan_is_refused(self) -> None:
        door = fake_program(self.tmp, "door", "echo '{\"status\": \"committed\", \"revision_ids\": []}'\n")
        with self.assertRaisesRegex(adapter.AdapterError, "planned"):
            self.propose(self.run_result(), door=door)

    def test_proposer_obligations_are_checked_before_door(self) -> None:
        cases = {
            "keeps no output": self.run_result(outputs=()),
            "ended before": self.run_result(ended_at=datetime(2000, 1, 1, tzinfo=timezone.utc)),
            "ASCII token": self.run_result(outputs=(adapter.Output(STREAM, "stream 0", PAYLOAD),)),
            "share an identity": self.run_result(outputs=(adapter.Output(STREAM, "s", PAYLOAD),) * 2),
            "adapter's to add": self.run_result(outputs=(adapter.Output(
                STREAM, "s", PAYLOAD, (adapter.Pin("run", self.wav["object_id"], self.wav["revision_id"]),)),)),
            "role used": self.run_result(used=(adapter.Pin("input", self.wav["object_id"],
                                                           self.wav["revision_id"]),)),
            "not in the archive": self.run_result(used=(adapter.Pin("used", self.wav["object_id"],
                                                                    "sha256:" + "0" * 64),)),
            "Package URL": self.run_result(agent="tool"),
        }
        for message, run in cases.items():
            with self.assertRaisesRegex(adapter.AdapterError, message):
                self.propose(run, door="/nonexistent/door")

    def test_an_equivalent_run_is_found_only_when_every_input_matches(self) -> None:
        run = self.run_result(parameters={"x": 1})
        self.assertIsNone(self.reader.equivalent_run(run))
        admission = self.propose(run, staging=self.staging())
        reader = adapter.ArchiveReader(self.root, support.ARCHIVE, self.env)
        found = reader.equivalent_run(self.run_result(parameters={"x": 1}))
        self.assertEqual(found["revision_id"], admission.run["revision_id"])
        for changed in (self.run_result(parameters={"x": 2}), self.run_result(),
                        self.run_result(parameters={"x": 1}, software=(("tool", "1.1"),)),
                        self.run_result(parameters={"x": 1}, method_version="2"),
                        self.run_result(parameters={"x": 1}, agent="pkg:generic/another@1"),
                        result(parameters={"x": 1})):
            self.assertIsNone(reader.equivalent_run(changed))

    def test_reading_finds_heads_artifacts_and_citations(self) -> None:
        self.assertEqual(self.reader.head(self.wav["object_id"])["revision_id"], self.wav["revision_id"])
        with self.assertRaisesRegex(adapter.AdapterError, "holds no object"):
            self.reader.head("urn:uuid:00000000-0000-4000-8000-000000000000")
        digest = self.reader.head(self.wav["object_id"])["payload"]["content"]["digest"]
        self.assertEqual(self.reader.artifact_with_digest(digest)["object_id"], self.wav["object_id"])
        self.assertIsNone(self.reader.artifact_with_digest("sha256:" + "0" * 64))
        export = support.export(self.root)
        export["contracts"] = [b for b in export["contracts"] if b["id"] != "archive.claim/derivation_run"]
        lacking = adapter.ArchiveReader.from_export(self.root, export)
        with self.assertRaisesRegex(adapter.AdapterError, "does not install archive.claim/derivation_run"):
            lacking.citation(adapter.RUN_CONTRACT)
        self.assertEqual(self.reader.citation(adapter.RUN_CONTRACT)["digest"],
                         "sha256:3059f5b5865eceddb40d160a3e85d3655f2e6e77ba2be1b2c5d290fdd36fdfb8")


@needs_door
class Reading(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = support.scratch(self, "reader-")

    def test_a_directory_that_is_not_an_archive_is_refused(self) -> None:
        with self.assertRaisesRegex(adapter.AdapterError, "is not complete"):
            adapter.ArchiveReader(self.tmp, support.ARCHIVE, support.environment())

    def test_an_export_that_is_not_json_is_refused(self) -> None:
        echo = fake_program(self.tmp, "archive", "echo not json\n")
        with self.assertRaisesRegex(adapter.AdapterError, "is not complete"):
            adapter.ArchiveReader(self.tmp, echo)

    def test_a_missing_archive_command_is_refused(self) -> None:
        with self.assertRaisesRegex(adapter.AdapterError, "could not run"):
            adapter.ArchiveReader(self.tmp, str(self.tmp / "no-archive"))


if __name__ == "__main__":
    unittest.main()
