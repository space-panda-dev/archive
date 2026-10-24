"""Specification 0.0.10's claims in World (projection 6, revision 6).

The records are world/tests/plan0010.py's drafts of the fixture plan and of
World's 0.0.10 plan, so their identities, pins, and citations are the ones
fixture/build.py would propose; World re-checks each by its own module before
it projects it. The catalogs, not a list here, say which contracts are new.
"""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from pyoxigraph import Literal, NamedNode

from world import contract_graphs, conformance, view_records
from world.tests.plan0010 import ROOT, drafted_export
from world.tests.test_world import fake_archive
from world.world import (PROJECTION_VERSION, ProjectionError, _read_current, build_projection, nquads,
                         rebuild_store, refresh_store)

ARCH = "urn:uuid:3e8b29f8-70c9-403e-bb4c-2280d59eca7f#"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
SKOS = "http://www.w3.org/2004/02/skos/core#"


def catalog(version):
    return {(c["id"], c["version"]) for c in
            json.loads((ROOT / "spec" / version / "contracts.json").read_text())["contracts"]}


NEW = catalog("0.0.11") - catalog("0.0.9")


def cites(record):
    return contract_graphs.contract_of(record)


class Projected(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.export = drafted_export()
        cls.projection = build_projection(cls.export)
        cls.store = cls.projection.store
        cls.new = [r for r in cls.export["records"] if cites(r) in NEW]

    def graph(self, record):
        return NamedNode(self.projection.archive_id + "#revision=" + record["revision_id"])

    def quads(self, record, subject=None, predicate=None):
        return list(self.store.quads_for_pattern(subject, None if predicate is None else NamedNode(predicate),
                                                 None, self.graph(record)))

    def named(self, name):
        from fixture.build import object_id
        found = [r for r in self.export["records"]
                 if r["object_id"] == object_id(self.export["archive_id"], name[0], name[1])]
        self.assertEqual(len(found), 1, name)
        return found[0]

    def test_every_new_contract_is_rechecked_and_cited(self):
        # Enumerate the catalogs: a contract a release adds is never
        # projected without its module's re-check.
        self.assertTrue(NEW)
        self.assertEqual(set(contract_graphs.RECHECKS), NEW)
        self.assertEqual({cites(r) for r in self.new}, NEW)

    def test_the_projection_conforms_and_every_declared_term_appears(self):
        report = conformance.check_store(self.store, self.projection.archive_id, PROJECTION_VERSION)
        self.assertEqual(report.problems, [])
        missing = set()
        for kind, (predicates, classes) in conformance.declared(PROJECTION_VERSION).items():
            missing |= {p for p in predicates - report.predicates.get(kind, set())}
        # Only the destroy roles the fixture cannot hold (test_conformance).
        self.assertEqual(missing, {ARCH + "dependent_destroy", ARCH + "dependent_source_gone",
                                   ARCH + "external_survivor"})

    def test_each_new_claim_projects_in_its_own_graph(self):
        for record in self.new:
            with self.subTest(contract=cites(record)):
                quads = self.quads(record)
                self.assertTrue(quads)
                typed = self.quads(record, self.graph(record), RDF + "type")
                if cites(record) in contract_graphs.SECOND_VERSIONS:
                    self.assertEqual(typed, [])
                else:
                    self.assertEqual([q.object for q in typed], [NamedNode(ARCH + "Claim")])

    def test_an_index_names_the_node_it_counts(self):
        form = self.named(("archive.claim/form", "world-w-form-segno"))
        repeat = NamedNode(self.graph(form).value + ":repeat=0")
        [first] = self.quads(form, repeat, ARCH + "first_section")
        self.assertEqual(first.object, NamedNode(self.graph(form).value + ":section=1"))
        self.assertEqual([q.object for q in self.quads(form, first.object, RDFS + "label")], [Literal("A")])
        placed = self.named(("archive.claim/meter_layers", "world-fixture-meter-placed"))
        written = self.named(("archive.claim/meter_layers", "world-fixture-meter-written"))
        bar = NamedNode(self.graph(placed).value + ":layer=0:bar=1")
        [source] = self.quads(placed, bar, ARCH + "placed_from")
        self.assertEqual(source.object, NamedNode(self.graph(written).value + ":layer=0:bar=1"))
        # The node it names is the written bar, in the written meter's graph.
        self.assertTrue(self.quads(written, source.object, ARCH + "numerator"))

    def test_a_rights_statement_is_a_concept_of_the_rights_scheme(self):
        rights = self.named(("archive.claim/rights", "world-fixture-rights"))
        [statement] = self.quads(rights, self.graph(rights), ARCH + "rights_statement")
        self.assertEqual(statement.object, NamedNode(ARCH + "rights-InC"))
        domain = self.projection.domain_graph
        match = list(self.store.quads_for_pattern(statement.object, NamedNode(SKOS + "exactMatch"), None, domain))
        self.assertEqual([q.object for q in match], [NamedNode("http://rightsstatements.org/vocab/InC/1.0/")])
        licence = NamedNode(ARCH + "rights-personal-use-licence")
        self.assertTrue(list(self.store.quads_for_pattern(licence, NamedNode(SKOS + "inScheme"), None, domain)))
        self.assertFalse(list(self.store.quads_for_pattern(licence, NamedNode(SKOS + "exactMatch"), None, domain)))

    def test_ordered_values_keep_their_order(self):
        key = self.named(("archive.claim/key_sequence", "world-fixture-key-sequence"))
        reading = NamedNode(self.graph(key).value + ":region=0:reading=0")
        self.assertEqual([q.object for q in self.quads(key, reading, ARCH + "spelling")], [Literal("C D E F G A B")])
        meter = self.named(("archive.claim/meter_layers", "world-fixture-meter-written"))
        bar = NamedNode(self.graph(meter).value + ":layer=0:bar=4")
        self.assertEqual([q.object for q in self.quads(meter, bar, ARCH + "grouping")], [Literal("3+3")])

    def test_records_read_back_as_committed(self):
        read = {r["revision_id"]: r for r in view_records.records(self.projection)}
        self.assertEqual(set(read), {r["revision_id"] for r in self.export["records"]})
        for record in self.export["records"]:
            got = read[record["revision_id"]]
            self.assertEqual((got["refs"], got["supersedes"], got["payload"], got["agent"], got["shape"]),
                             (record["refs"], record.get("supersedes", []), record["payload"], record["agent"],
                              record["shape"]), record["revision_id"])


class Refused(unittest.TestCase):
    """A claim World's module refuses fails the projection, naming why; it is
    never projected in part or skipped."""

    def setUp(self):
        self.export = drafted_export()

    def mutate(self, name, change):
        from fixture.build import object_id
        target = object_id(self.export["archive_id"], name[0], name[1])
        [record] = [r for r in self.export["records"] if r["object_id"] == target]
        change(record)
        return record

    def assertRefused(self, pattern):
        with self.assertRaisesRegex(ProjectionError, pattern):
            build_projection(self.export)

    def test_the_unmutated_drafts_project(self):
        build_projection(self.export)

    def test_a_region_that_ends_before_it_starts(self):
        def change(record):
            record["payload"]["regions"][0]["end_sample"] = 0
        self.mutate(("archive.claim/key_regions", "world-fixture-key-regions"), change)
        self.assertRefused("key_regions")

    def test_a_lead_sheet_asserted_by_software(self):
        self.mutate(("archive.claim/lead_sheet", "world-fixture-lead-sheet"),
                    lambda r: r.__setitem__("agent", "pkg:generic/some-tool@1"))
        self.assertRefused("lead_sheet.*person or a band")

    def test_a_consent_by_the_operator(self):
        operator = next(ref["id"] for r in self.export["records"] for ref in r["refs"] if ref["role"] == "appoints")
        self.mutate(("archive.claim/sharing_consent", "world-fixture-consent"), lambda r: r.__setitem__("agent", operator))
        self.assertRefused("sharing_consent")

    def test_an_export_whose_key_is_not_its_request(self):
        def change(record):
            record["payload"]["key"] = "sha256:" + "0" * 64
        self.mutate(("archive.claim/export", "world-fixture-export"), change)
        self.assertRefused("export.*key")

    def test_a_judgment_withdrawn_by_someone_else(self):
        from fixture.build import object_id
        guest = object_id(self.export["archive_id"], "archive.core/entity", "fixture-guest")

        def change(record):
            record["payload"]["verdict"] = "withdrawn"
            record["agent"] = guest
        self.mutate(("archive.assessment/judgment", "world-w-judgment-entries"), change)
        self.assertRefused("judgment")


class IncrementalRefresh(unittest.TestCase):
    """ADR 0055 with 0.0.10's claims: a refresh that projects only the records
    added since a cut equals a full rebuild, though the export since the cut
    holds neither the agents' Entities nor the operator's appointment, which
    World then reads from the generation it extends."""

    # Added since the cut: records whose agents' Entities and the operator's
    # appointment are pinned by nothing added, and a lead sheet, whose rule
    # needs its agent's kind; the export that renders it comes with it.
    ADDED = (("archive.claim/sharing_consent", "world-w-consent-refused"),
             ("archive.claim/sensitivity", "world-fixture-sensitivity"),
             ("archive.claim/span_annotation", "world-w-mark-entries"),
             ("archive.claim/lead_sheet", "world-w-lead-sheet-timed"),
             ("archive.claim/export", "world-w-export-pdf"))

    def test_incremental_equals_full(self):
        from fixture.build import object_id
        final = drafted_export()
        final["snapshot"] = "b" * 40
        objects = {object_id(final["archive_id"], *name) for name in self.ADDED}
        new = [r for r in final["records"] if r["object_id"] in objects]
        self.assertEqual(len(new), len(self.ADDED))
        added = {r["revision_id"] for r in new}
        cut = dict(final, snapshot="a" * 40, records=[r for r in final["records"] if r["revision_id"] not in added])
        self.assertFalse([r for r in cut["records"] for ref in r["refs"] if ref.get("revision_id") in added])
        by_revision = {r["revision_id"]: r for r in final["records"]}
        pinned = {ref["revision_id"] for r in new for ref in r["refs"] + r.get("supersedes", [])} - added
        since = dict(final, since=cut["snapshot"], records=new,
                     pinned_records=[by_revision[rid] for rid in sorted(pinned)])
        # Anchor the chain: what the re-checks read is outside the export.
        held = {r["object_id"] for r in since["pinned_records"]}
        self.assertFalse({r["agent"] for r in new} & held)
        self.assertFalse([r for r in since["pinned_records"]
                          if any(c["id"] == "archive.transition/operator_initialization" for c in r.get("contracts", []))])
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "archive").mkdir()
            for name, value in (("cut.json", cut), ("since.json", since), ("final.json", final)):
                (tmp / name).write_text(json.dumps(value))
            store = tmp / "world"
            rebuild_store(tmp / "archive", store, export_file=tmp / "cut.json")
            command, log = fake_archive(tmp / "bin", None, {cut["snapshot"]: tmp / "since.json"})
            refreshed = refresh_store(tmp / "archive", store, archive_command=command)
            self.assertEqual(refreshed.mode, "incremental")
            full = rebuild_store(tmp / "archive", tmp / "full", export_file=tmp / "final.json")
            self.assertEqual(nquads(_read_current(store)), nquads(full))


if __name__ == "__main__":
    unittest.main()
