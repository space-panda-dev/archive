"""World re-checks D-18's agent and target-form rules as Door does.

The tables in `world/envelope_rules.py` are compared with every bundle the
0.0.11 catalog installs, so a bundle that declares one of these rules cannot
go unchecked by World. The refusals mutate the drafted 0.0.10 records into
the cases Door's agreement test checks alone (`test_door_agreement_010.py`'s
DOOR_ONLY), and require World's projection to refuse them too.
"""

from __future__ import annotations

import json
import unittest

from world import envelope_rules
from world.tests.plan0010 import ROOT, drafted_export
from world.world import ProjectionError, build_projection


def declared(rule):
    """Each 0.0.11 contract whose bundle declares `rule` at its schema's
    root, with the declaration."""
    catalog = json.loads((ROOT / "spec" / "0.0.11" / "contracts.json").read_text())["contracts"]
    found = {}
    for entry in catalog:
        bundle = json.loads((ROOT / "spec" / "contracts" / (entry["digest"].replace(":", "-") + ".json")).read_text())
        text = json.dumps(bundle["schema"])
        if rule in bundle["schema"]:
            found[(entry["id"], entry["version"])] = bundle["schema"][rule]
        elif rule in text:
            raise AssertionError(f"{entry['id']} {entry['version']} declares {rule} below its root")
    return found


class Tables(unittest.TestCase):
    def test_the_agent_kinds_are_the_bundles(self):
        found = {contract: frozenset(rule["kinds"]) for contract, rule in declared("x-archive-agent").items()}
        self.assertTrue(found)
        self.assertEqual(envelope_rules.AGENT_KINDS, found)

    def test_the_target_forms_are_the_bundles(self):
        found = {contract: (rule["by"], rule["roles"]) for contract, rule in declared("x-archive-target-form").items()}
        self.assertTrue(found)
        self.assertEqual(envelope_rules.TARGET_FORMS, found)


class Refused(unittest.TestCase):
    def setUp(self):
        self.export = drafted_export()

    def record(self, contract, name):
        from fixture.build import object_id
        target = object_id(self.export["archive_id"], contract, name)
        [found] = [r for r in self.export["records"] if r["object_id"] == target]
        return found

    def entity(self, kind):
        return next(r["object_id"] for r in self.export["records"]
                    if r.get("shape") == "entity" and (r.get("payload") or {}).get("kind") == kind)

    def test_the_unmutated_drafts_project(self):
        build_projection(self.export)

    def test_an_origin_asserted_by_a_band(self):
        band = self.entity("organisation")
        self.record("archive.claim/work_origin", "world-fixture-origin")["agent"] = band
        with self.assertRaisesRegex(ProjectionError, "work_origin.*person Entity"):
            build_projection(self.export)

    def test_a_limit_by_a_tool(self):
        self.record("archive.claim/sensitivity", "world-fixture-sensitivity")["agent"] = "pkg:generic/some-tool@1"
        with self.assertRaisesRegex(ProjectionError, "sensitivity.*person Entity"):
            build_projection(self.export)

    def test_a_timed_meter_correcting_the_untimed_one(self):
        written = self.record("archive.claim/meter_layers", "world-fixture-meter-written")
        placed = self.record("archive.claim/meter_layers", "world-fixture-meter-placed")
        self.assertTrue(any(ref["role"] == "source" for ref in placed["refs"]))
        self.assertFalse(any(ref["role"] == "source" for ref in written["refs"]))
        placed["refs"].append({"role": "corrects", "id": written["object_id"], "revision_id": written["revision_id"]})
        with self.assertRaisesRegex(ProjectionError, "meter_layers.*corrects must pin a Claim"):
            build_projection(self.export)


if __name__ == "__main__":
    unittest.main()
