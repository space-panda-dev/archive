"""Measure the checker and its former N-Quads transfer on synthetic data.

Run with the declared World test Python, from the repository root:
    python -m world.tests.benchmark_conformance --payload-items 100000 --repeat 3
No archive, network, external model or persistent cache is used.
"""
import argparse
import json
import platform
import statistics
import time
from unittest.mock import patch

import pyoxigraph
import pyshacl
import rdflib
from pyoxigraph import Literal, NamedNode, Quad

from world import conformance
from world.tests.test_conformance_transfer import ARCH, FIXTURE, serialized_graphs
from world.world import PROJECTION_VERSION, build_projection


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload-items", type=int, default=100000)
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()
    if args.payload_items < 0 or args.repeat < 1:
        parser.error("payload-items must be nonnegative and repeat positive")
    projection = build_projection(json.loads(FIXTURE.read_text()))
    records = NamedNode(projection.archive_id + "#world:records")
    payload = NamedNode(ARCH + "payload")
    quad = next(projection.store.quads_for_pattern(None, payload, None, records))
    text = json.dumps({"data": [{"a": "hello", "b": 3.14159}] * args.payload_items})
    projection.store.remove(quad)
    projection.store.add(Quad(quad.subject, payload,
        Literal(text, datatype=NamedNode(str(rdflib.RDF.JSON))), records))
    direct = conformance._graphs
    times = {"serialized": [], "direct": []}
    reference = None
    for _ in range(args.repeat):
        for name, transfer in (("serialized", serialized_graphs), ("direct", direct)):
            start = time.perf_counter()
            with patch.object(conformance, "_graphs", transfer):
                report = conformance.check_store(projection.store, projection.archive_id, PROJECTION_VERSION)
            times[name].append(time.perf_counter() - start)
            if reference is None:
                reference = report
            if report != reference or not report.conforms:
                raise AssertionError("the complete conformance reports differ or fail")
    result = {"python": platform.python_version(), "platform": platform.platform(),
              "rdflib": rdflib.__version__, "pyshacl": pyshacl.__version__,
              "pyoxigraph": pyoxigraph.__version__, "projection": PROJECTION_VERSION,
              "graphs": reference.graphs, "quads": len(projection.store),
              "json_payload_bytes": len(text.encode()), "seconds": times,
              "median_speedup": statistics.median(times["serialized"]) / statistics.median(times["direct"])}
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
