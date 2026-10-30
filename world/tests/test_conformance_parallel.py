"""Bounded worker differential and failure regressions."""
import copy
import json
import os
import unittest
import rdflib
from concurrent.futures import Future as ConcurrentFuture, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from unittest.mock import patch

from pyoxigraph import DefaultGraph, Literal, NamedNode, Quad
from world import conformance
from world.world import PROJECTORS, PROJECTION_VERSION, build_projection

FIXTURE = Path(__file__).parent / 'fixtures/door-world-chain-export.json'
ARCH = 'urn:uuid:3e8b29f8-70c9-403e-bb4c-2280d59eca7f#'


def crash_worker(*args):
    os._exit(7)


class ParallelConformanceTests(unittest.TestCase):
    def projection(self):
        return build_projection(json.loads(FIXTURE.read_text()))

    def test_every_served_projection_matches_serial(self):
        for version in PROJECTORS:
            with self.subTest(version=version):
                p = build_projection(json.loads(FIXTURE.read_text()), version)
                self.assertEqual(conformance.check_store(p.store, p.archive_id, version),
                                 conformance.check_store(p.store, p.archive_id, version, workers=2))

    def test_invalid_superseded_revision_and_malformed_graphs_still_fail(self):
        exported = json.loads(FIXTURE.read_text())
        previous = next(r for r in exported['records'] if r['shape'] == 'claim')
        successor = copy.deepcopy(previous)
        successor['revision_id'] = 'sha256:' + '0' * 64
        self.assertNotEqual(previous['revision_id'], successor['revision_id'])
        successor['supersedes'] = [{'role': 'supersedes', 'id': previous['object_id'],
                                    'revision_id': previous['revision_id']}]
        exported['records'].append(successor)
        p = build_projection(exported)
        self.assertTrue(conformance.check_store(p.store, p.archive_id, PROJECTION_VERSION).conforms)
        history = NamedNode(p.archive_id + '#revision=' + previous['revision_id'])
        subject = next(p.store.quads_for_pattern(None, None, None, history)).subject
        for graph in [history, DefaultGraph(), NamedNode('urn:unknown:graph')]:
            p.store.add(Quad(subject, NamedNode(ARCH + 'undeclared'), Literal('bad'), graph))
        # Share the very same shape BNode terms across serial and spawn runs.
        loaded = {e['kind']: conformance.shapes(PROJECTION_VERSION, e['kind'])
                  for e in conformance.description(PROJECTION_VERSION)['graphs']}
        with patch.object(conformance, 'shapes', side_effect=lambda v, kind: loaded[kind]):
            serial = conformance.check_store(p.store, p.archive_id, PROJECTION_VERSION)
            parallel = conformance.check_store(p.store, p.archive_id, PROJECTION_VERSION, workers=2)
        self.assertEqual(serial, parallel)
        self.assertTrue(any(str(history.value) in problem for problem in parallel.problems))
        self.assertTrue(any('default graph' in problem for problem in parallel.problems))
        self.assertTrue(any('matches 0' in problem for problem in parallel.problems))

    def test_bounded_queue_keeps_every_revision(self):
        p = self.projection()
        state = {'pending': 0, 'peak': 0, 'checked': 0}
        class Future(ConcurrentFuture):
            def __init__(self, fn, args):
                super().__init__()
                self.fn, self.args = fn, args
                self.set_result(None)
            def result(self):
                state['pending'] -= 1
                state['checked'] += 1
                return self.fn(*self.args)
        class Pool:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.assert_spawn = kwargs['mp_context'].get_start_method()
                kwargs['initializer'](*kwargs['initargs'])
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def submit(self, fn, *args):
                state['pending'] += 1
                state['peak'] = max(state['peak'], state['pending'])
                if self.assert_spawn != 'spawn': raise AssertionError('must not fork the store')
                return Future(fn, args)
        class NoWholeStore:
            def __iter__(self): raise AssertionError('must stream graphs')
            def dump(self, **kwargs): raise AssertionError('must not serialize the dataset')
            def named_graphs(self): return p.store.named_graphs()
            def quads_for_pattern(self, *args): return p.store.quads_for_pattern(*args)
        class NoDataset(rdflib.Dataset):
            def __init__(self, *args, **kwargs): raise AssertionError('no whole dataset')
        with patch.object(conformance, 'ProcessPoolExecutor', Pool), \
             patch.object(conformance.rdflib, 'Dataset', NoDataset):
            result = conformance.check_store(NoWholeStore(), p.archive_id, PROJECTION_VERSION, workers=2)
        self.assertTrue(result.conforms, result.problems)
        self.assertEqual(state['checked'], result.graphs['revision'])
        self.assertEqual(state['peak'], 4)
        self.assertEqual(state['pending'], 0)

    def test_refills_before_slow_first_result_and_preserves_problem_order(self):
        p = self.projection()
        data = [conformance._GraphData(rdflib.URIRef(p.archive_id + "#revision=sha256:" + f"{i:064x}"),
                [], set(), set()) for i in range(15)]
        submitted, completed, active = [], [], []

        class Future:
            def __init__(self, name):
                self.name = name
            def result(self):
                active.remove(self)
                completed.append(self.name)
                return [self.name]

        class Pool:
            def __init__(self, **kwargs): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def submit(self, fn, graph, kind):
                if len(submitted) == 4:
                    # A full window refilled before the first, slow graph ended.
                    if str(data[0].identifier) in completed:
                        raise AssertionError("head-of-line wait blocked refill")
                future = Future(str(graph.identifier))
                submitted.append(future.name)
                active.append(future)
                if len(active) > 4:
                    raise AssertionError("unbounded pending graph payloads")
                return future

        def complete_latest(pending, **kwargs):
            self.assertEqual(kwargs["return_when"], conformance.FIRST_COMPLETED)
            return {next(reversed(pending))}, set()

        with patch.object(conformance, "_graphs", side_effect=lambda store: iter(data)), \
             patch.object(conformance, "_check_graph", side_effect=lambda graph, *args: [str(graph.identifier)]):
            serial = conformance.check_store(p.store, p.archive_id, PROJECTION_VERSION)
            with patch.object(conformance, "ProcessPoolExecutor", Pool), \
                 patch.object(conformance, "wait", complete_latest):
                parallel = conformance.check_store(p.store, p.archive_id, PROJECTION_VERSION, workers=2)
        self.assertEqual(serial, parallel)
        self.assertEqual(len(submitted), 15)
        self.assertEqual(completed[-1], str(data[0].identifier))
        self.assertFalse(active)

    def test_worker_crash_fails_closed(self):
        p = self.projection()
        def pool(**kwargs):
            kwargs['initializer'] = crash_worker
            return ProcessPoolExecutor(**kwargs)
        with patch.object(conformance, 'ProcessPoolExecutor', pool):
            with self.assertRaises(BrokenProcessPool):
                conformance.check_store(p.store, p.archive_id, PROJECTION_VERSION, workers=2)

    def test_worker_exception_and_missing_result_fail_closed(self):
        p = self.projection()
        for result in [RuntimeError('worker failed'), None, {}, '', 0, [42]]:
            with self.subTest(result=result):
                class Future(ConcurrentFuture):
                    def __init__(self):
                        super().__init__()
                        self.set_result(None)
                    def result(self):
                        if isinstance(result, Exception): raise result
                        return result
                class Pool:
                    def __init__(self, **kwargs): pass
                    def __enter__(self): return self
                    def __exit__(self, *args): pass
                    def submit(self, *args): return Future()
                with patch.object(conformance, 'ProcessPoolExecutor', Pool):
                    with self.assertRaises((RuntimeError, TypeError)):
                        conformance.check_store(p.store, p.archive_id, PROJECTION_VERSION, workers=2)

    def test_invalid_worker_counts_are_refused(self):
        p = self.projection()
        for workers in [0, -1, True, 1.5]:
            with self.subTest(workers=workers), self.assertRaises(ValueError):
                conformance.check_store(p.store, p.archive_id, PROJECTION_VERSION, workers=workers)
