"""Differential and cost regressions for World's RDF representation transfer."""
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import rdflib
from rdflib.compare import isomorphic
from pyoxigraph import BlankNode, DefaultGraph, Literal, NamedNode, Quad, RdfFormat, Store

from world import conformance
from world.world import PROJECTORS, PROJECTION_VERSION, build_projection

FIXTURE = Path(__file__).parent / "fixtures/door-world-chain-export.json"
ARCH = "urn:uuid:3e8b29f8-70c9-403e-bb4c-2280d59eca7f#"


def serialized_dataset(store):
    """Previous checker path, retained only as a differential oracle."""
    dataset = rdflib.Dataset()
    dataset.parse(data=store.dump(format=RdfFormat.N_QUADS), format="nquads")
    return dataset


class DirectTermTransferTests(unittest.TestCase):
    def test_terms_match_the_previous_parser(self):
        store = Store()
        subject, predicate = NamedNode("urn:example:subject"), NamedNode("urn:example:predicate")
        terms = [NamedNode("urn:example:object"), BlankNode("shared"), Literal("plain"),
                 Literal('quotes " slash \\ newline\n unicode ☃'), Literal("bonjour", language="fr-CA"),
                 Literal("001", datatype=NamedNode(str(rdflib.XSD.integer))),
                 Literal("invalid", datatype=NamedNode(str(rdflib.XSD.integer))),
                 Literal("2026-10-29T01:02:03Z", datatype=NamedNode(str(rdflib.XSD.dateTime))),
                 Literal('{"a":[1,2]}', datatype=NamedNode(str(rdflib.RDF.JSON))),
                 Literal("opaque", datatype=NamedNode("urn:example:custom"))]
        for graph in (NamedNode("urn:example:graph"), DefaultGraph(), BlankNode("graph")):
            for term in terms:
                store.add(Quad(subject, predicate, term, graph))
            store.add(Quad(BlankNode("shared"), predicate, subject, graph))
        actual, reference = conformance._dataset(store), serialized_dataset(store)
        default = rdflib.graph.DATASET_DEFAULT_GRAPH_ID
        self.assertTrue(isomorphic(actual.graph(default), reference.graph(default)))
        self.assertTrue(isomorphic(actual.graph(rdflib.URIRef("urn:example:graph")),
                                   reference.graph(rdflib.URIRef("urn:example:graph"))))
        actual_blank = next(g for g in actual.graphs() if isinstance(g.identifier, rdflib.BNode))
        reference_blank = next(g for g in reference.graphs() if isinstance(g.identifier, rdflib.BNode))
        self.assertTrue(isomorphic(actual_blank, reference_blank))
        self.assertEqual(sum(len(g) for g in actual.graphs()), sum(len(g) for g in reference.graphs()))

    def test_large_payload_never_serializes_the_store(self):
        projection = build_projection(json.loads(FIXTURE.read_text()))
        records = NamedNode(projection.archive_id + "#world:records")
        payload = NamedNode(ARCH + "payload")
        quad = next(projection.store.quads_for_pattern(None, payload, None, records))
        projection.store.remove(quad)
        projection.store.add(Quad(quad.subject, payload,
            Literal(json.dumps({"data": [{"a": "hello", "b": 3.14159}] * 100000}),
                    datatype=NamedNode(str(rdflib.RDF.JSON))), records))

        class NoSerialization:
            def __iter__(self):
                return iter(projection.store)

            def dump(self, **kwargs):
                raise AssertionError("large payload must not cross the quadratic N-Quads parser")

        report = conformance.check_store(NoSerialization(), projection.archive_id, PROJECTION_VERSION)
        self.assertTrue(report.conforms, report.problems)
        self.assertEqual(report.graphs["revision"], 15)

    def test_all_served_projections_match_the_previous_checker(self):
        exported = json.loads(FIXTURE.read_text())
        for version in PROJECTORS:
            with self.subTest(version=version):
                projection = build_projection(exported, version)
                actual = conformance.check_store(projection.store, projection.archive_id, version)
                with patch.object(conformance, "_dataset", serialized_dataset):
                    reference = conformance.check_store(projection.store, projection.archive_id, version)
                self.assertEqual(actual, reference)

    def test_invalid_history_is_still_rejected(self):
        exported = json.loads(FIXTURE.read_text())
        previous = next(record for record in exported["records"] if record["shape"] == "claim")
        successor = copy.deepcopy(previous)
        successor["revision_id"] = "sha256:" + "0" * 64
        self.assertNotEqual(successor["revision_id"], previous["revision_id"])
        successor["supersedes"] = [{"role": "supersedes", "id": previous["object_id"],
                                    "revision_id": previous["revision_id"]}]
        exported["records"].append(successor)
        projection = build_projection(exported)
        # The invalid graph belongs to an explicitly superseded revision;
        # checking only the successor would miss this undeclared predicate.
        graph = NamedNode(projection.archive_id + "#revision=" + previous["revision_id"])
        self.assertTrue(conformance.check_store(projection.store, projection.archive_id,
                                                PROJECTION_VERSION).conforms)
        quad = next(projection.store.quads_for_pattern(None, None, None, graph))
        projection.store.add(Quad(quad.subject, NamedNode(ARCH + "undeclared"), Literal("bad"), graph))
        actual = conformance.check_store(projection.store, projection.archive_id, PROJECTION_VERSION)
        with patch.object(conformance, "_dataset", serialized_dataset):
            reference = conformance.check_store(projection.store, projection.archive_id, PROJECTION_VERSION)
        self.assertFalse(actual.conforms)
        self.assertFalse(reference.conforms)
        self.assertEqual(actual.graphs, reference.graphs)
        self.assertEqual(len(actual.problems), len(reference.problems))
        self.assertTrue(any("does not conform" in p for p in actual.problems))
