"""Door and World agree on the check beyond the circle (specification 0.0.10).

Door refuses, at `door transition`, a publication approval version 2 beyond
the circle that the archive's own sensitivity, rights, or co-writers'
consent forbid on the UTC date of the transition (S-30); World states the
same rule as `circle.beyond_circle`, which its `may_publish` view applies
again when a publisher publishes. Each case proposes an approval against the
published fixture archive (fixture/build.py) by `door transition --dry-run`,
which writes nothing, and asks World the same question of the fixture's
records read back from `archive export`. Every case states the expected
verdict, and the list has accepted and refused ones, so the two cannot agree
by both being wrong or by both refusing everything.

The fixture's facts the cases rest on: the selection title's closure holds
take-a, which an AMBER limit keeps to the circle; the rights claim pins a
Work whose rights (InC, held by the band, not the operator) reach service;
the co-written Work's origin reaches service by rights and sensitivity, so
only its co-writer's consent decides it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from world import circle
from world.tests.test_conformance import build_fixture_archive, integration_binaries, remove_tree
from world.tests.test_door_agreement import BY, _canonical

# The fixture's transition records of the release (fixture/plan.json); the
# 0.0.10 agreement test counts their contracts as covered here.
TRANSITION_KEYS = ("publication-v2", "withdrawal-v2")

APPROVAL = "archive.transition/publication_approval"
WITHDRAWAL = "archive.transition/publication_withdrawal"

# (label, covered fixture key, class, decision, destination, approved)
Case = tuple[str, str, str, str, str, bool]

BEFORE_CONSENT: list[Case] = [
    ("an approval to the circle is not checked beyond it", "title", "circle", "approve", "fixture-circle", True),
    ("an AMBER limit in the covered closure keeps it from a service", "title", "service", "approve", "fixture-circle", False),
    ("a hold-back is never checked", "title", "distribution", "hold_back", "elsewhere", True),
    ("rights held by the band reach a service", "rights", "service", "approve", "elsewhere", True),
    ("rights held by the band do not reach open", "rights", "open", "approve", "elsewhere", False),
    ("rights held by the band do not reach distribution", "rights", "distribution", "approve", "elsewhere", False),
    ("a co-written Work without its co-writer's consent", "origin", "service", "approve", "fixture-circle", False),
]

AFTER_CONSENT: list[Case] = [
    ("the co-writer's consent to the destination, covering the revision", "origin", "service", "approve",
     "fixture-circle", True),
    ("the same consent does not reach another destination", "origin", "service", "approve", "elsewhere", False),
    ("consent does not lift rights that keep a Work to service", "origin", "open", "approve", "fixture-circle", False),
]


class BeyondCircleAgreement(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.door_binary, cls.archive = integration_binaries()
        cls.tmp = Path(tempfile.mkdtemp())
        cls.manifest, cls.export = build_fixture_archive(cls.tmp)
        cls.root = cls.tmp / "archive"
        cls.environment = dict(os.environ)
        for variable, program in (("ARCHIVE_GIT_EXECUTABLE", "git"), ("ARCHIVE_GIT_ANNEX_EXECUTABLE", "git-annex")):
            cls.environment.setdefault(variable, os.path.realpath(shutil.which(program) or program))
        cls.count = 0

    @classmethod
    def tearDownClass(cls):
        remove_tree(cls.tmp)

    # -- the fixture --------------------------------------------------------

    def pinned(self, key: str) -> dict[str, str]:
        found = self.manifest["objects"][key]
        return {"id": found["object_id"], "revision_id": found["revision_id"]}

    def record(self, key: str) -> dict[str, Any]:
        revision = self.manifest["objects"][key]["revision_id"]
        return next(r for r in self.export["records"] if r["revision_id"] == revision)

    def proposal(self, command: str, draft: dict[str, Any], namespace: str, heads: list[str]) -> tuple[Path, str]:
        """A one-record proposal of `draft` as a new object, expecting the
        current heads of the objects `heads` names."""
        from fixture.build import object_id
        type(self).count += 1
        name = f"beyond-circle-{self.count}"
        draft = dict(draft, object_id=object_id(self.export["archive_id"], namespace, name), supersedes=[], by=BY)
        superseded = {s["revision_id"] for r in self.export["records"] for s in r.get("supersedes", [])}
        current = {r["object_id"]: r["revision_id"] for r in self.export["records"] if r["revision_id"] not in superseded}
        expected = [{"object_id": draft["object_id"], "revision_id": None}] + [
            {"object_id": o, "revision_id": current[o]} for o in sorted(set(heads))]
        proposal = self.tmp / "proposals" / name
        (proposal / "records").mkdir(parents=True)
        (proposal / "records/record.json").write_bytes(_canonical(draft))
        manifest: dict[str, Any] = {
            "protocol": f"archive-{'admission' if command == 'admit' else 'transition'}/1",
            "records": ["records/record.json"],
            "identities": [{"object_id": draft["object_id"], "namespace": namespace, "name": name}],
            "expected_heads": expected}
        if command == "admit":
            manifest["contents"] = []
        (proposal / "manifest.json").write_bytes(_canonical(manifest))
        return proposal, draft["object_id"]

    def run_door(self, command: str, proposal: Path, *extra: str) -> tuple[bool, str]:
        completed = subprocess.run([self.door_binary, command, "--proposal", str(proposal), *extra],
                                   cwd=self.root, env=self.environment, capture_output=True, text=True)
        if completed.returncode == 0:
            return True, completed.stdout.strip()
        return False, (completed.stderr or completed.stdout).strip()

    def approval(self, covered: str, exposure: str, decision: str, destination: str) -> dict[str, Any]:
        template = self.record("publication-v2")
        return {"spec": template["spec"], "shape": "claim", "agent": template["agent"],
                "contracts": template["contracts"],
                "payload": {"class": exposure, "decision": decision, "destination": destination},
                "refs": [{"role": "authorizer", **self.pinned("operator")}, {"role": "covers", **self.pinned(covered)}]}

    # -- the two verdicts ---------------------------------------------------

    def door_approves(self, draft: dict[str, Any]) -> tuple[bool, str]:
        heads = [ref["id"] for ref in draft["refs"]]
        return self.run_door("transition", self.proposal("transition", draft, APPROVAL, heads)[0], "--dry-run")

    def world_approves(self, draft: dict[str, Any]) -> tuple[bool, str]:
        records = {}
        for r in self.export["records"]:
            if "at" not in r:
                raise AssertionError("the export carries no `at`, which sensitivity order needs")
            cited = [(c["id"], c["version"]) for c in r.get("contracts", [])]
            view = {**r, "id": r["object_id"], "contract": list(cited[0]) if len(cited) == 1 else None}
            if r["shape"] == "entity" and "kind" in r["payload"]:
                view["kind"] = r["payload"]["kind"]
            records[r["revision_id"]] = view
        covers = [ref["revision_id"] for ref in draft["refs"] if ref["role"] == "covers"]
        blockers = circle.beyond_circle(draft["payload"], covers, records, self.manifest["operator_id"],
                                        datetime.now(timezone.utc).date())
        return not blockers, "; ".join(blockers)

    def agree(self, cases: list[Case]) -> None:
        for label, covered, exposure, decision, destination, approved in cases:
            with self.subTest(case=label):
                draft = self.approval(covered, exposure, decision, destination)
                door, door_reason = self.door_approves(draft)
                self.assertEqual(door, approved, f"Door: {door_reason}")
                world, world_reason = self.world_approves(draft)
                self.assertEqual(world, approved, f"World: {world_reason}")
                if not approved:
                    self.assertIn("beyond the circle", door_reason)

    def admit(self, draft: dict[str, Any], namespace: str) -> dict[str, str]:
        """Commit `draft` as a new object; its pin."""
        proposal, objectid = self.proposal("admit", draft, namespace, [])
        admitted, reason = self.run_door("admit", proposal)
        self.assertTrue(admitted, f"{namespace} is admitted: {reason}")
        return {"id": objectid, "revision_id": json.loads(reason)["revision_ids"][0]}

    def consent_to_origin(self) -> None:
        """Commit the co-writer's consent to the fixture circle's
        destination, covering the origin's revision, and read the archive
        back. A consent never covers the origin it pins, so it pins a second
        origin the operator asserts, which governs the Work as the first
        does."""
        template = self.record("origin")
        second = self.admit({key: template[key] for key in ("spec", "shape", "agent", "contracts", "payload", "refs")},
                            "archive.claim/work_origin")
        template = self.record("consent")
        draft = {"spec": template["spec"], "shape": "claim", "agent": template["agent"],
                 "contracts": template["contracts"], "payload": {"decision": "approve", "destination": "fixture-circle"},
                 "refs": [{"role": "origin", **second}, {"role": "covers", **self.pinned("origin")}]}
        self.admit(draft, "archive.claim/sharing_consent")
        exported = subprocess.run([self.archive, "export"], cwd=self.root, env=self.environment,
                                  capture_output=True, text=True)
        self.assertEqual(exported.returncode, 0, exported.stderr)
        type(self).export = json.loads(exported.stdout)

    # -- the cases ----------------------------------------------------------

    def test_the_check_beyond_the_circle(self):
        # Anchor: the fixture's consent covers the title, not the origin, so
        # the origin's cases turn on the consent this test adds.
        consent = self.record("consent")
        self.assertNotIn(self.pinned("origin")["revision_id"],
                         [ref["revision_id"] for ref in consent["refs"] if ref["role"] == "covers"])
        self.agree(BEFORE_CONSENT)
        self.consent_to_origin()
        self.agree(AFTER_CONSENT)

    def test_no_approval_is_withdrawn_twice_across_versions(self):
        # Door alone: World reads withdrawals, it does not admit them.
        for label, contract_key, target in (
                ("a version 2 withdrawal of an approval a version 1 withdrawal withdrew", "withdrawal-v2", "publication"),
                ("a version 2 withdrawal of an approval a version 2 withdrawal withdrew", "withdrawal-v2", "publication-v2")):
            with self.subTest(case=label):
                template = self.record(contract_key)
                draft = {"spec": template["spec"], "shape": "claim", "agent": template["agent"],
                         "contracts": template["contracts"], "payload": {"reason": "Again"},
                         "refs": [{"role": "authorizer", **self.pinned("operator")},
                                  {"role": "withdraws", **self.pinned(target)}]}
                heads = [ref["id"] for ref in draft["refs"]]
                accepted, reason = self.run_door("transition", self.proposal("transition", draft, WITHDRAWAL, heads)[0],
                                                 "--dry-run")
                self.assertFalse(accepted, label)
                self.assertIn("already withdrawn", reason)

    def test_every_case_list_has_both_verdicts(self):
        self.assertEqual({case[5] for case in BEFORE_CONSENT}, {True, False})
        self.assertEqual({case[5] for case in AFTER_CONSENT}, {True, False})


if __name__ == "__main__":
    unittest.main()
