"""Operator-only session associations, through the real Door and World."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from world.tests.fixture_exports import fixture_export
from world.tests.test_conformance import ROOT, integration_binaries, remove_tree
from world.world import build_projection, query, ProjectionError

ARCH = "urn:uuid:3e8b29f8-70c9-403e-bb4c-2280d59eca7f#"
CONTRACT = "archive.claim/session_ensemble"


class SessionEnsemble(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.manifest, cls.export = fixture_export()

    @classmethod
    def tearDownClass(cls):
        remove_tree(cls.tmp)

    def association(self, export):
        [record] = [r for r in export["records"] if any(c["id"] == CONTRACT for c in r.get("contracts", []))]
        return record

    def test_operator_association_projects_exact_session_and_ensemble_pins(self):
        projection = build_projection(self.export)
        answer = query(projection, f"SELECT DISTINCT ?session ?ensemble WHERE {{ GRAPH ?g {{ ?s <{ARCH}session> ?session ; <{ARCH}session_ensemble> ?ensemble }} }}")
        [row] = answer.data["results"]["bindings"]
        for role in ("session", "ensemble"):
            revision = self.manifest["objects"][role]["revision_id"]
            self.assertEqual(row[role]["value"], self.export["archive_id"] + "#revision=" + revision)

    def test_unknown_is_not_inferred_from_a_relay_or_membership(self):
        export = copy.deepcopy(self.export)
        export["records"].remove(self.association(export))
        projection = build_projection(export)
        answer = query(projection, f"SELECT ?ensemble WHERE {{ GRAPH ?g {{ ?s <{ARCH}session_ensemble> ?ensemble }} }}")
        self.assertEqual(answer.data["results"]["bindings"], [])

    def test_world_refuses_another_person_as_operator(self):
        export = copy.deepcopy(self.export)
        self.association(export)["agent"] = self.manifest["objects"]["guest"]["object_id"]
        with self.assertRaises(ProjectionError):
            build_projection(export)

    def test_world_refuses_a_person_as_ensemble(self):
        export = copy.deepcopy(self.export)
        for ref in self.association(export)["refs"]:
            if ref["role"] == "ensemble":
                ref.update(id=self.manifest["objects"]["guest"]["object_id"], revision_id=self.manifest["objects"]["guest"]["revision_id"])
        with self.assertRaises(ProjectionError):
            build_projection(export)

    def test_door_refuses_guest_assertion_without_admitting_the_claim(self):
        plan = json.loads((ROOT / "fixture/plan.json").read_text())
        for step in plan["steps"]:
            for record in step["records"]:
                if record["key"] == "session-ensemble":
                    record["agent"] = "@guest"
        path = self.tmp / "guest-plan.json"
        path.write_text(json.dumps(plan))
        door, archive = integration_binaries()
        root = self.tmp / "guest-archive"
        result = subprocess.run([sys.executable, str(ROOT / "fixture/build.py"), str(root),
                                 "--plan", str(path), "--content", str(ROOT / "fixture/content"),
                                 "--door", door, "--archive", archive], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("active operator", result.stderr)
