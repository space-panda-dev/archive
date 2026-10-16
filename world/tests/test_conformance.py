"""World conforms to its published projection description (ADR 0059).

The main check projects the published fixture archive, which
fixture/build.py admits through the real Door. The other checks prove the
checker fails on each kind of drift, so a passing check means something.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pyoxigraph import Literal, NamedNode, Quad, Store

from world import conformance
from world.world import PROJECTION_VERSION, PROJECTORS, build_projection

ROOT = Path(__file__).resolve().parents[2]
DOOR_CHAIN_EXPORT = ROOT / "world/tests/fixtures/door-world-chain-export.json"
ARCH = "urn:uuid:3e8b29f8-70c9-403e-bb4c-2280d59eca7f#"

# Declared because specification 0.0.6's destroy contracts name these roles,
# but Door refuses every record that uses them until their evidence contracts
# exist ("no installed artifact contract defines dependent reachability",
# "external survivor evidence contracts are not installed"; door/test). The
# fixture therefore cannot hold them.
UNREACHABLE = {("records", ARCH + "dependent_destroy"), ("records", ARCH + "dependent_source_gone"),
               ("records", ARCH + "external_survivor")}


def integration_binaries():
    """Door and Archive, or a skip; CI sets ARCHIVE_REQUIRE_INTEGRATION so a
    missing binary fails instead of passing by skipping."""
    door, archive = os.environ.get("ARCHIVE_TEST_DOOR"), os.environ.get("ARCHIVE_TEST_ARCHIVE")
    if not (door and archive):
        if os.environ.get("ARCHIVE_REQUIRE_INTEGRATION"):
            raise AssertionError("ARCHIVE_TEST_DOOR and ARCHIVE_TEST_ARCHIVE are required")
        raise unittest.SkipTest("set ARCHIVE_TEST_DOOR and ARCHIVE_TEST_ARCHIVE to run Door")
    return door, archive


def remove_tree(path):
    """git-annex leaves read-only directories behind."""
    for directory, _, _ in os.walk(path):
        os.chmod(directory, 0o700)
    shutil.rmtree(path, ignore_errors=True)


def build_fixture_archive(parent):
    """Run the published builder against a fresh archive; return its export."""
    door, archive = integration_binaries()
    root = Path(parent) / "archive"
    completed = subprocess.run([sys.executable, str(ROOT / "fixture/build.py"), str(root),
                                "--door", door, "--archive", archive], capture_output=True, text=True)
    if completed.returncode != 0:
        raise AssertionError(f"the fixture builder failed: {completed.stderr}")
    manifest = json.loads(completed.stdout)
    environment = dict(os.environ)
    for variable, program in (("ARCHIVE_GIT_EXECUTABLE", "git"), ("ARCHIVE_GIT_ANNEX_EXECUTABLE", "git-annex")):
        environment.setdefault(variable, os.path.realpath(shutil.which(program) or program))
    exported = subprocess.run([archive, "export"], cwd=root, capture_output=True, text=True, env=environment)
    if exported.returncode != 0:
        raise AssertionError(f"archive export of the fixture failed: {exported.stderr}")
    return manifest, json.loads(exported.stdout)


class FixtureArchiveConformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.manifest, cls.export = build_fixture_archive(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        remove_tree(cls.tmp)

    def test_the_fixture_cites_every_contract_in_the_catalog(self):
        # Enumerate the catalog rather than naming today's contracts.
        catalog = json.loads((ROOT / "spec" / self.export["spec"].removeprefix("archive/") / "contracts.json").read_text())
        expected = {(c["id"], c["version"]) for c in catalog["contracts"]}
        cited = {(c["id"], c["version"]) for r in self.export["records"] for c in r.get("contracts", [])}
        self.assertEqual(cited, expected)
        self.assertEqual({r["agent"] for r in self.export["records"]}, {self.manifest["operator_id"]})

    def test_every_projection_version_served_conforms(self):
        for version in sorted(PROJECTORS):
            with self.subTest(projection=version):
                projection = build_projection(self.export, version)
                report = conformance.check_store(projection.store, projection.archive_id, version)
                self.assertEqual(report.problems, [])
                self.assertEqual(set(report.graphs), {"records", "context", "domain", "revision"})

    def test_every_declared_term_appears_in_the_fixture(self):
        # Anchor the declaration: a predicate or class the shapes declare but
        # World never emits for the fixture is stale, or the fixture is thin.
        projection = build_projection(self.export)
        report = conformance.check_store(projection.store, projection.archive_id, PROJECTION_VERSION)
        missing = set()
        for kind, (predicates, classes) in conformance.declared(PROJECTION_VERSION).items():
            missing |= {(kind, p) for p in predicates - report.predicates.get(kind, set())}
            missing |= {(kind, c) for c in classes - report.classes.get(kind, set())
                        if c.startswith(("http", "urn"))}
        self.assertEqual(missing, UNREACHABLE)

    def test_every_installed_contract_is_in_the_context_graph(self):
        projection = build_projection(self.export)
        answer = projection.store.query("""SELECT ?id ?version WHERE { GRAPH ?g {
            ?c <http://purl.org/dc/terms/identifier> ?id ; <urn:uuid:3e8b29f8-70c9-403e-bb4c-2280d59eca7f#version> ?version } }""")
        found = {(row["id"].value, row["version"].value) for row in answer}
        catalog = json.loads((ROOT / "spec/0.0.6/contracts.json").read_text())
        self.assertEqual(found, {(c["id"], c["version"]) for c in catalog["contracts"]})


class CheckerFailsOnDriftTests(unittest.TestCase):
    """Each mutation must fail; the unmutated projection is the anchor."""

    def setUp(self):
        self.export = json.loads(DOOR_CHAIN_EXPORT.read_text())
        self.projection = build_projection(self.export)
        self.archive_id = self.projection.archive_id
        self.records = NamedNode(self.archive_id + "#world:records")

    def problems(self):
        return conformance.check_store(self.projection.store, self.archive_id, PROJECTION_VERSION).problems

    def first_revision(self):
        return next(q.subject for q in self.projection.store.quads_for_pattern(
            None, NamedNode(ARCH + "revision_id"), None, self.records))

    def test_the_unmutated_projection_conforms(self):
        self.assertEqual(self.problems(), [])

    def test_an_undeclared_predicate_fails(self):
        self.projection.store.add(Quad(self.first_revision(), NamedNode(ARCH + "renamed_revision_id"),
                                       Literal("x"), self.records))
        self.assertTrue(any("does not conform" in p for p in self.problems()))

    def test_a_wrong_datatype_fails(self):
        context = NamedNode(self.archive_id + "#world:context")
        node = NamedNode(self.archive_id + "#world")
        for quad in list(self.projection.store.quads_for_pattern(node, NamedNode(ARCH + "projection_version"), None, context)):
            self.projection.store.remove(quad)
        self.projection.store.add(Quad(node, NamedNode(ARCH + "projection_version"), Literal("6"), context))
        self.assertTrue(any("does not conform" in p for p in self.problems()))

    def test_an_undeclared_subject_fails(self):
        self.projection.store.add(Quad(NamedNode("urn:example:loose"), NamedNode(ARCH + "revision_id"),
                                       Literal("sha256:" + "0" * 64), self.records))
        self.assertTrue(any("not declared by any shape" in p for p in self.problems()))

    def test_an_undeclared_class_fails(self):
        self.projection.store.add(Quad(self.first_revision(), NamedNode("http://www.w3.org/1999/02/22-rdf-syntax-ns#type"),
                                       NamedNode(ARCH + "Unknown"), self.records))
        self.assertTrue(any("does not conform" in p for p in self.problems()))

    def test_an_undeclared_graph_fails(self):
        self.projection.store.add(Quad(self.first_revision(), NamedNode(ARCH + "revision_id"),
                                       Literal("sha256:" + "0" * 64), NamedNode(self.archive_id + "#world:extra")))
        self.assertTrue(any("matches 0 declared graph patterns" in p for p in self.problems()))

    def test_a_default_graph_triple_fails(self):
        from pyoxigraph import DefaultGraph
        self.projection.store.add(Quad(self.first_revision(), NamedNode(ARCH + "revision_id"),
                                       Literal("sha256:" + "0" * 64), DefaultGraph()))
        self.assertTrue(any("default graph" in p for p in self.problems()))

    def test_an_empty_projection_is_not_conformant(self):
        self.assertTrue(conformance.check_store(Store(), self.archive_id, PROJECTION_VERSION).problems)

    def test_every_targeted_shape_is_closed(self):
        self.assertEqual(conformance.unclosed_shapes(PROJECTION_VERSION), [])

    def test_every_served_version_is_described(self):
        for version in PROJECTORS:
            self.assertEqual(conformance.description(version)["projection_version"], version)


if __name__ == "__main__":
    unittest.main()


class VocabularyTests(unittest.TestCase):
    def test_every_archive_term_the_shapes_use_is_declared(self):
        import rdflib
        from rdflib.namespace import RDF, RDFS, OWL, SH
        description = conformance.description(PROJECTION_VERSION)
        vocabulary = rdflib.Graph().parse(conformance.PROJECTIONS / str(PROJECTION_VERSION) / description["vocabulary"])
        declared = {str(s) for s in vocabulary.subjects(RDF.type, None)
                    if (s, RDF.type, RDF.Property) in vocabulary or (s, RDF.type, RDFS.Class) in vocabulary
                    or (s, RDF.type, OWL.Ontology) in vocabulary}
        used = set()
        for entry in description["graphs"]:
            graph = conformance.shapes(PROJECTION_VERSION, entry["kind"])
            used |= {str(t) for triple in graph for t in triple
                     if str(t).startswith(ARCH) and "/shapes/" not in str(t)}
        self.assertTrue(used)
        self.assertEqual(used - declared, set())
        self.assertEqual(declared - used, set())
        self.assertIn(description["iri"], declared)
