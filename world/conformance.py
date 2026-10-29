#!/usr/bin/env python3
"""Check a World projection against its published description (ADR 0059).

For a projection version N, `world/projection/N/projection.json` names the
graphs World emits and a closed SHACL shapes file for each. A projection
conforms when:

- the default graph is empty and every named graph matches one pattern;
- each graph validates against its shapes with a SHACL engine;
- every subject in each graph is a focus node of some closed shape, so no
  predicate or class escapes the declaration.

Needs pyshacl (world/requirements-test.txt); World itself does not.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from contextlib import nullcontext
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pyoxigraph import BlankNode, DefaultGraph, Literal, NamedNode

import rdflib
from pyshacl import validate
from rdflib.namespace import RDF, SH

HERE = Path(__file__).resolve().parent
PROJECTIONS = HERE / "projection"


@dataclass
class Report:
    problems: list[str] = field(default_factory=list)
    graphs: dict[str, int] = field(default_factory=dict)
    predicates: dict[str, set[str]] = field(default_factory=dict)
    classes: dict[str, set[str]] = field(default_factory=dict)

    @property
    def conforms(self) -> bool:
        return not self.problems


def description(version: int) -> dict[str, Any]:
    path = PROJECTIONS / str(version) / "projection.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"projection {version} has no published description: {exc}") from exc
    if value.get("projection_version") != version or not value.get("graphs"):
        raise ValueError(f"{path} does not describe projection {version}")
    return value


def shapes(version: int, kind: str) -> rdflib.Graph:
    entry = next(item for item in description(version)["graphs"] if item["kind"] == kind)
    graph = rdflib.Graph()
    graph.parse(PROJECTIONS / str(version) / entry["shapes"], format="turtle")
    return graph


def declared(version: int) -> dict[str, tuple[set[str], set[str]]]:
    """Per graph kind: the predicates its shapes declare, and the classes
    allowed as rdf:type values."""
    answer = {}
    for entry in description(version)["graphs"]:
        graph = shapes(version, entry["kind"])
        predicates = {str(path) for path in graph.objects(None, SH.path)}
        classes = set()
        for prop in graph.subjects(SH.path, RDF.type):
            for listed in graph.objects(prop, SH["in"]):
                classes |= {str(item) for item in rdflib.collection.Collection(graph, listed)}
        answer[entry["kind"]] = (predicates, classes)
    return answer


def focus_nodes(shapes_graph: rdflib.Graph, data: rdflib.Graph) -> set[Any]:
    """Every node some shape targets, by the SHACL target rules."""
    nodes: set[Any] = set()
    for shape in shapes_graph.subjects(RDF.type, SH.NodeShape):
        for cls in shapes_graph.objects(shape, SH.targetClass):
            nodes |= set(data.subjects(RDF.type, cls))
        for predicate in shapes_graph.objects(shape, SH.targetSubjectsOf):
            nodes |= set(data.subjects(predicate, None))
        for predicate in shapes_graph.objects(shape, SH.targetObjectsOf):
            nodes |= set(data.objects(None, predicate))
        nodes |= set(shapes_graph.objects(shape, SH.targetNode))
    return nodes


def unclosed_shapes(version: int) -> list[str]:
    """Node shapes with a target that are not closed; a closed declaration
    needs every one of them closed."""
    problems = []
    for entry in description(version)["graphs"]:
        graph = shapes(version, entry["kind"])
        for shape in graph.subjects(RDF.type, SH.NodeShape):
            targeted = any(graph.objects(shape, p) for p in (SH.targetClass, SH.targetSubjectsOf,
                                                              SH.targetObjectsOf, SH.targetNode))
            if targeted and (shape, SH.closed, rdflib.Literal(True)) not in graph:
                problems.append(f"{entry['shapes']}: {shape} is not closed")
    return problems


def _rdflib_term(term: Any) -> Any:
    """Transfer RDF terms without serializing and reparsing their lexical forms.

    Oxigraph represents plain strings as xsd:string; its N-Quads serializer
    omits that datatype, as RDFLib's parser expects for SHACL value equality.
    Keep all other datatypes and language tags, including ill-typed literals.
    """
    if isinstance(term, NamedNode):
        return rdflib.URIRef(term.value)
    if isinstance(term, BlankNode):
        return rdflib.BNode(term.value)
    if isinstance(term, Literal):
        if term.direction is not None:
            raise ValueError("RDFLib conformance does not support directional literals")
        datatype = None if term.language or term.datatype.value == str(rdflib.XSD.string) else rdflib.URIRef(term.datatype.value)
        return rdflib.Literal(term.value, lang=term.language, datatype=datatype)
    raise ValueError(f"RDFLib conformance does not support RDF term {type(term).__name__}")


def _graphs(store: Any):
    """Transfer one isolated graph at a time, including the default graph.

    Nothing is retained across graph validation. This avoids both a whole
    RDFLib dataset and its second per-graph copy. Every quad is still visited;
    no validation results are cached and no historical graph is skipped.
    """
    from itertools import chain

    for name in chain((DefaultGraph(),), store.named_graphs()):
        identifier = rdflib.graph.DATASET_DEFAULT_GRAPH_ID if isinstance(name, DefaultGraph) else _rdflib_term(name)
        graph = rdflib.Graph(identifier=identifier)
        for quad in store.quads_for_pattern(None, None, None, name):
            graph.add(tuple(_rdflib_term(term) for term in (quad.subject, quad.predicate, quad.object)))
        if graph:
            yield graph


def _check_graph(graph: rdflib.Graph, kind: str, shape: rdflib.Graph) -> list[str]:
    name = str(graph.identifier)
    problems = []
    conforms, _, text = validate(graph, shacl_graph=shape, advanced=False,
                                 inference="none", allow_warnings=False)
    if not conforms:
        problems.append(f"graph {name} ({kind}) does not conform:\n{text}")
    outside = set(graph.subjects()) - focus_nodes(shape, graph)
    for subject in sorted(outside, key=str)[:5]:
        problems.append(f"graph {name} ({kind}): {subject} is not declared by any shape")
    return problems


_WORKER_SHAPES: dict[str, rdflib.Graph] = {}


def _worker_init(shape_data: dict[str, tuple[list[Any], list[Any]]]) -> None:
    # Copy terms directly, preserving blank node IDs in comparable SHACL reports.
    global _WORKER_SHAPES
    _WORKER_SHAPES = {}
    for kind, (triples, namespaces) in shape_data.items():
        graph = rdflib.Graph()
        for prefix, namespace in namespaces:
            graph.bind(prefix, namespace, replace=True)
        for triple in triples:
            graph.add(triple)
        _WORKER_SHAPES[kind] = graph


def _worker_check(identifier: Any, kind: str, triples: list[Any]) -> list[str]:
    graph = rdflib.Graph(identifier=identifier)
    for triple in triples:
        graph.add(triple)
    return _check_graph(graph, kind, _WORKER_SHAPES[kind])


def check_store(store: Any, archive_id: str, version: int, *, workers: int = 1) -> Report:
    """Check every quad of a pyoxigraph store holding one projection."""
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ValueError("conformance workers must be a positive integer")
    report = Report()
    entries = description(version)["graphs"]
    patterns = [(entry["kind"], re.compile(entry["pattern"].replace("{archive}", re.escape(archive_id))))
                for entry in entries]
    shapes_by_kind = {entry["kind"]: shapes(version, entry["kind"]) for entry in entries}
    pending: deque[Any] = deque()

    def collect() -> None:
        # Exceptions, including a dead worker, propagate; a partial report
        # cannot declare conformance when a revision was not checked.
        report.problems.extend(pending.popleft().result())

    executor = (ProcessPoolExecutor(max_workers=workers,
                    mp_context=multiprocessing.get_context("spawn"), initializer=_worker_init,
                    initargs=({kind: (list(graph), list(graph.namespaces()))
                               for kind, graph in shapes_by_kind.items()},))
                if workers > 1 else nullcontext(None))
    with executor as pool:
        for graph in _graphs(store):
            if not graph:
                continue
            name = str(graph.identifier)
            if graph.identifier == rdflib.graph.DATASET_DEFAULT_GRAPH_ID:
                while pending:
                    collect()
                report.problems.append(f"the default graph holds {len(graph)} triples; it must be empty")
                continue
            matched = [kind for kind, pattern in patterns if pattern.fullmatch(name)]
            if len(matched) != 1:
                while pending:
                    collect()
                report.problems.append(f"graph {name} matches {len(matched)} declared graph patterns")
                continue
            kind = matched[0]
            report.graphs[kind] = report.graphs.get(kind, 0) + 1
            report.predicates.setdefault(kind, set()).update(str(p) for p in graph.predicates())
            report.classes.setdefault(kind, set()).update(str(o) for o in graph.objects(None, RDF.type))
            if pool is not None and kind == "revision":
                pending.append(pool.submit(_worker_check, graph.identifier, kind, list(graph)))
                if len(pending) >= 2 * workers:
                    collect()
            else:
                while pending:
                    collect()
                report.problems.extend(_check_graph(graph, kind, shapes_by_kind[kind]))
        while pending:
            collect()
    if not report.graphs:
        report.problems.append("the projection holds no named graphs")
    return report


def main(argv: list[str] | None = None) -> int:
    sys.path.insert(0, str(HERE))
    import world  # noqa: PLC0415

    parser = argparse.ArgumentParser(prog="world-conformance", description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--store", type=Path, help="a World store; checks its selected generation")
    source.add_argument("--export", type=Path, help="an `archive export` JSON file to project and check")
    parser.add_argument("--projection", type=int, default=world.PROJECTION_VERSION)
    parser.add_argument("--workers", type=int, default=1,
                        help="revision SHACL processes (default: 1; queue bounded to twice this)")
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error("--workers must be positive")
    if args.store:
        projection = world._read_current(args.store, args.projection)
    else:
        projection = world.build_projection(json.loads(args.export.read_text(encoding="utf-8")), args.projection)
    report = check_store(projection.store, projection.archive_id, args.projection, workers=args.workers)
    report.problems[:0] = unclosed_shapes(args.projection)
    json.dump({"conforms": report.conforms, "projection": args.projection, "graphs": report.graphs,
               "problems": report.problems}, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0 if report.conforms else 1


if __name__ == "__main__":
    raise SystemExit(main())
