"""World across a specification upgrade (ADR 0068).

ADR 0068 says World reads an archive's new version after the upgrade commit
"like any other change". Door's tests prove the commit; these prove the
reading: an archive at the previous release, holding a record admitted under
it, is upgraded by the real `door transition`, and World's next refresh
rebuilds in full, equals a fresh projection, and conforms to its published
description, with the upgrade record and the earlier records both in it.

The current `archive init` makes an archive only at the current release, so
the earlier archive is made the way Door's own upgrade tests make it: a fresh
init whose root commit is rewritten to the previous release, its two
bootstrap records refinalized under it. Door then loads and verifies it like
any archive, which the first test checks before anything relies on it.
"""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

from world import conformance
from world.world import PROJECTION_VERSION, build_projection, nquads, refresh_store, _read_current

from test_conformance import integration_binaries, remove_tree

ROOT = Path(__file__).resolve().parents[2]
PREVIOUS, CURRENT = "0.0.8", "0.0.9"
UPGRADE = "archive.transition/specification_upgrade"
BY = "pkg:generic/archive-world-upgrade-test@0"


def canonical(value):
    """RFC 8785 for the values these records hold: strings, objects, arrays."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def revision_of(record):
    identity = {key: value for key, value in record.items() if key not in ("revision_id", "at")}
    return "sha256:" + hashlib.sha256(canonical(identity)).hexdigest()


def record_path(revision):
    digest = revision.removeprefix("sha256:")
    return Path("records") / digest[:2] / digest[2:4] / f"sha256-{digest}.json"


def allocated(archive_id, namespace, name):
    return "urn:uuid:" + str(uuid.uuid5(uuid.UUID(archive_id.removeprefix("urn:uuid:")), namespace + "\0" + name))


def catalog(version):
    return json.loads((ROOT / "spec" / version / "contracts.json").read_text())["contracts"]


class SpecificationUpgradeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.door, cls.archive = integration_binaries()
        cls.tmp = Path(tempfile.mkdtemp(prefix="world-upgrade-"))
        cls.root = cls.tmp / "archive"
        cls.env = dict(os.environ)
        for variable, program in (("ARCHIVE_GIT_EXECUTABLE", "git"), ("ARCHIVE_GIT_ANNEX_EXECUTABLE", "git-annex")):
            cls.env.setdefault(variable, os.path.realpath(shutil.which(program) or program))
        cls.env.setdefault("archive_datadir", str(ROOT))
        cls.git = cls.env["ARCHIVE_GIT_EXECUTABLE"]
        try:
            cls.make_earlier_archive()
        except BaseException:
            remove_tree(cls.tmp)
            raise

    @classmethod
    def tearDownClass(cls):
        remove_tree(cls.tmp)

    # -- helpers -----------------------------------------------------------

    @classmethod
    def run_checked(cls, command, what, cwd=None):
        completed = subprocess.run(command, cwd=cwd or cls.root, env=cls.env, capture_output=True, text=True)
        if completed.returncode != 0:
            raise AssertionError(f"{what} failed ({completed.returncode}): {completed.stderr}{completed.stdout}")
        return completed.stdout

    @classmethod
    def git_run(cls, *arguments):
        return cls.run_checked([cls.git, "-c", "user.name=archive-test", "-c", "user.email=archive-test@example.invalid",
                                *arguments], "git " + arguments[0])

    @classmethod
    def make_earlier_archive(cls):
        init = json.loads(cls.run_checked([cls.archive, "init", "--operator", "Operator", str(cls.root)],
                                          "archive init", cwd=cls.tmp))
        cls.archive_id, cls.operator_id = init["archive_id"], init["operator_id"]
        manifest = (cls.root / "archive.json").read_text()
        current, previous = f'"spec":"archive/{CURRENT}"', f'"spec":"archive/{PREVIOUS}"'
        if manifest.count(current) != 1:
            raise AssertionError(f"archive init did not write one {current}: {manifest}")
        (cls.root / "archive.json").write_text(manifest.replace(current, previous))
        cls.git_run("rm", "-r", "-q", f"spec/{CURRENT}")
        (cls.root / "spec" / PREVIOUS).mkdir(parents=True)
        for name in ("VERSION", "archive-spec.md", "context.jsonld"):
            shutil.copyfile(ROOT / "spec" / PREVIOUS / name, cls.root / "spec" / PREVIOUS / name)
        keep = {entry["digest"].removeprefix("sha256:") for entry in catalog(PREVIOUS)}
        for bundle in sorted((cls.root / "contracts").iterdir()):
            if bundle.name.removeprefix("sha256-").removesuffix(".json") not in keep:
                cls.git_run("rm", "-q", str(bundle.relative_to(cls.root)))
        stored = {}
        for path in cls.git_run("ls-files", "records").split():
            stored[path] = json.loads((cls.root / path).read_text())
        entities = [p for p, r in stored.items() if r["shape"] == "entity"]
        claims = [p for p, r in stored.items() if r["shape"] == "claim"]
        if len(entities) != 1 or len(claims) != 1:
            raise AssertionError(f"archive init wrote other than one Entity and one Claim: {sorted(stored)}")

        def refinalize(path, adjust=lambda record: record):
            record = adjust({key: value for key, value in stored[path].items() if key not in ("revision_id", "at")})
            record["spec"] = f"archive/{PREVIOUS}"
            revision = revision_of(record)
            cls.git_run("rm", "-q", path)
            target = cls.root / record_path(revision)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(canonical({**record, "revision_id": revision, "at": stored[path]["at"]}))
            return stored[path]["revision_id"], revision

        old, new = refinalize(entities[0])

        def repin(record):
            record["refs"] = [{**ref, "revision_id": new} if ref.get("revision_id") == old else ref
                              for ref in record["refs"]]
            return record

        refinalize(claims[0], repin)
        cls.operator_revision = new
        cls.git_run("add", "archive.json", "spec", "records")
        # Early Door builds committed the bootstrap files as 100755, and real
        # archives keep that first commit; Door must upgrade them as they are.
        bootstrap = ["archive.json", *cls.git_run("ls-files", "spec", "contracts").split()]
        cls.git_run("update-index", "--chmod=+x", *bootstrap)
        for path in bootstrap:
            (cls.root / path).chmod(0o755)
        cls.git_run("commit", "-q", "--amend", "--no-edit")

    def head(self):
        return self.git_run("rev-parse", "HEAD").strip()

    def propose(self, command, name, namespace, record, expected_heads=(), dry_run=False):
        proposal = self.tmp / "proposals" / name
        (proposal / "records").mkdir(parents=True)
        object_id = record["object_id"]
        manifest = {"protocol": f"archive-{'admission' if command == 'admit' else 'transition'}/1",
                    "records": ["records/record.json"],
                    "identities": [{"object_id": object_id, "namespace": namespace, "name": name}],
                    "expected_heads": [{"object_id": object_id, "revision_id": None}, *expected_heads]}
        if command == "admit":
            manifest["contents"] = []
        (proposal / "manifest.json").write_bytes(canonical(manifest))
        (proposal / "records" / "record.json").write_bytes(canonical(record))
        return subprocess.run([self.door, command, "--proposal", str(proposal), *(["--dry-run"] if dry_run else [])],
                              cwd=self.root, env=self.env, capture_output=True, text=True)

    def entity(self, name, spec):
        return {"spec": f"archive/{spec}", "object_id": allocated(self.archive_id, "archive.core/entity", name),
                "shape": "entity", "by": BY, "agent": self.operator_id,
                "payload": {"kind": "person", "label": name}, "refs": [], "supersedes": []}

    def export(self):
        return json.loads(self.run_checked([self.archive, "export"], "archive export"))

    def assert_conforms(self, projection):
        report = conformance.check_store(projection.store, projection.archive_id, PROJECTION_VERSION)
        self.assertEqual(report.problems, [])

    # -- the test ----------------------------------------------------------

    def test_world_reads_an_upgraded_archive(self):
        self.assertEqual((ROOT / "spec" / "VERSION").read_text().strip(), CURRENT,
                         "a new release needs this test's versions revisited")
        upgrade = [entry for entry in catalog(CURRENT) if entry["id"] == UPGRADE]
        self.assertEqual(len(upgrade), 1)
        # Anchor: the current release adds bundles to the previous one, so
        # the upgrade installs something World has not seen at this archive.
        self.assertLess({entry["digest"] for entry in catalog(PREVIOUS)}, {entry["digest"] for entry in catalog(CURRENT)})

        # Anchor: Door loads the rewritten archive as the previous release,
        # whose manifest is committed as an early Door committed it.
        self.assertEqual(json.loads((self.root / "archive.json").read_text())["spec"], f"archive/{PREVIOUS}")
        self.assertTrue(self.git_run("ls-files", "-s", "archive.json").startswith("100755 "))
        self.run_checked([self.door, "verify"], "door verify of the earlier archive")
        before = self.export()
        self.assertEqual(before["status"], "ok")
        self.assertEqual(before["spec"], f"archive/{PREVIOUS}")

        # A record of the previous release, and Door refusing one of the
        # current release until the upgrade (D-1, D-6).
        admitted = self.propose("admit", "earlier-guest", "archive.core/entity", self.entity("earlier-guest", PREVIOUS))
        self.assertEqual(admitted.returncode, 0, admitted.stderr)
        head = self.head()
        early = self.propose("admit", "early-guest", "archive.core/entity", self.entity("early-guest", CURRENT),
                             dry_run=True)
        self.assertNotEqual(early.returncode, 0, early.stdout)
        self.assertIn("selects a core context this archive does not hold", early.stdout + early.stderr)
        self.assertEqual(self.head(), head)

        store = self.tmp / "world"
        first = refresh_store(self.root, store, archive_command=self.archive)
        self.assertEqual(first.mode, "full")
        self.assert_conforms(_read_current(store))

        # The upgrade, by the real Door.
        upgrade_id = allocated(self.archive_id, UPGRADE, "upgrade")
        record = {"spec": f"archive/{CURRENT}", "object_id": upgrade_id, "shape": "claim", "by": BY,
                  "agent": self.operator_id, "contracts": upgrade,
                  "payload": {"from": f"archive/{PREVIOUS}", "to": f"archive/{CURRENT}"},
                  "refs": [{"role": "authorizer", "id": self.operator_id, "revision_id": self.operator_revision}],
                  "supersedes": []}
        upgraded = self.propose("transition", "upgrade", UPGRADE, record,
                                [{"object_id": self.operator_id, "revision_id": self.operator_revision}])
        self.assertEqual(upgraded.returncode, 0, upgraded.stderr)
        self.assertEqual(json.loads(upgraded.stdout)["status"], "committed")
        later = self.propose("admit", "later-guest", "archive.core/entity", self.entity("later-guest", CURRENT))
        self.assertEqual(later.returncode, 0, later.stderr)

        self.assertTrue(self.git_run("ls-tree", "HEAD", "archive.json").startswith("100644 blob "))
        self.assertEqual(self.git_run("status", "--porcelain"), "")

        after = self.export()
        self.assertEqual(after["spec"], f"archive/{CURRENT}")
        self.assertNotEqual(after["snapshot"], before["snapshot"])

        refreshed = refresh_store(self.root, store, archive_command=self.archive)
        self.assertEqual(refreshed.mode, "full")
        self.assertIn("specification or core context changed", refreshed.reason)
        current = _read_current(store)
        self.assertEqual(current.snapshot, after["snapshot"])
        self.assertEqual(nquads(current), nquads(build_projection(after)))
        self.assert_conforms(current)

        # Every record, of either release, is in the records graph.
        projected = nquads(current).decode("utf-8")
        revisions = {r["revision_id"] for r in after["records"]}
        self.assertEqual(len(revisions), 5)
        for revision in revisions:
            self.assertIn(revision.removeprefix("sha256:"), projected)


if __name__ == "__main__":
    unittest.main()
