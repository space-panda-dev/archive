"""Canonical scoped request/readiness behavior, without models or personal data."""
from __future__ import annotations
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import adapter
import bank
import composition
import enrichment_requests
import propose
import proposers
class Entry:
    """Minimal consumed Bank-entry interface, with explicit feature citations."""
    def __init__(self, extractor, content, items=()):
        self.manifest = {"extractor": {"id": extractor, "version": "1"},
                         "content": content, "key": "sha256:" + "2" * 64}
        self._items = list(items)
    def items(self, output):
        assert output == "notes"
        return self._items
    def output(self, output):
        return {"kind": "file", "sha256": "sha256:" + "3" * 64}
    def provenance(self, output):
        return {"feature": self.manifest["extractor"]["id"] + "/" + output,
                "extractor": self.manifest["extractor"], "entry": self.manifest["key"],
                "software": [{"name": "synthetic", "version": "1"}]}
    def shift(self, rate):
        return 0


class Store:
    def __init__(self, root):
        self.root, self.catalog, self.parameters, self.exact = Path(root), bank.Catalog.load(), {}, False
        self.source = "stems/vocals"
        self.stems = Entry("stems", "sha256:" + "1" * 64)
        self.contour = Entry("vocal-contour", "sha256:" + "3" * 64,
                             [{"start": .1, "end": .4, "value": 60.55}])
    def entry(self, content, extractor):
        return self.stems if extractor == self.source.split('/')[0] else None
    def measured_over(self, content, feature, ids):
        return [(self.stems, {"vocal-contour": self.contour})] if feature == self.source else []
    def entry_from(self, content, feature, extractor):
        return self.contour if extractor == "vocal-contour" and feature == self.source else None
    def default_input(self, *args):
        return self.source
    def default_entry(self, *args):
        raise AssertionError("strict vocal method must not inspect legacy notes")


class EnrichmentRequests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)
        self.config = {"composition": {"backfill": {"produce": [
            {"contract": list(proposers.NOTES), "methods": ["vocal-notes"]}], "attempt": []}},
            "programs": {"f0-python": "/configured/python"}}
        self.artifact = {"object_id": "urn:uuid:00000000-0000-4000-8000-000000000001",
                         "revision_id": "sha256:" + "1" * 64,
                         "payload": {"content": {"digest": "sha256:" + "1" * 64}}}
        self.stream = {"object_id": "urn:uuid:00000000-0000-4000-8000-000000000002",
                       "revision_id": "sha256:" + "2" * 64,
                       "refs": [dict(role="artifact", **propose.pin(self.artifact))],
                       "payload": {"sample_rate": 48000, "sample_count": 480000, "stream_index": 0}}
        self.region = dict(start=4800, end=19200, rate=48000)
    def request(self, records=(), scheduler=True):
        with patch.object(bank, "Bank", return_value=self.store):
            return enrichment_requests.request(self.store, self.config, self.artifact, self.stream,
                                               self.region, list(records), scheduler=scheduler)
    def records(self):
        context = proposers.Context(self.store, self.artifact['payload']['content']['digest'], 48000, 480000)
        run = propose.run_result(proposers.PROPOSERS['vocal-notes'], context, self.artifact, self.stream)
        run_id, parameter_id = 'sha256:'+'4'*64, 'sha256:'+'5'*64
        rr = dict(shape='claim', object_id='run', revision_id=run_id, agent=run.agent,
                  contracts=[dict(id=adapter.RUN_CONTRACT[0],version=adapter.RUN_CONTRACT[1])],
                  payload=dict(method=dict(id=run.method_id,version=run.method_version),
                               software=[dict(name=n,version=v) for n,v in run.software]),
                  refs=[p.ref() for p in run.used]+[dict(role='parameters',id='parameters',revision_id=parameter_id)])
        parameter = dict(shape='artifact',object_id='parameters',revision_id=parameter_id,
                         payload=dict(content=dict(digest=adapter.digest_of(run.parameter_bytes()))))
        notes = dict(shape='claim',object_id='notes',revision_id='sha256:'+'6'*64,
                     contracts=[dict(id=proposers.NOTES[0],version='1')],payload=run.outputs[0].payload,
                     refs=[p.ref() for p in run.outputs[0].refs]+[dict(role='run',id='run',revision_id=run_id)])
        return [parameter, rr, notes]
    def test_exact_policy_run_and_current_output_are_required_for_ready(self):
        records = self.records()
        answer = self.request(records)
        self.assertEqual(answer['status'], 'ready')
        self.assertEqual(answer['notes'], propose.pin(records[-1]))
        self.assertIsNone(answer['inference_region'])
        self.assertEqual(answer['region'], self.region)
        records[-1]['head'] = False
        self.assertEqual(self.request(records)['status'], 'unavailable')
        self.assertEqual(self.request(records)['diagnostics'][-1]['code'], 'reading-not-current')
        records[-1]['head'] = True
        records[0]['payload']['content']['digest'] = 'sha256:'+'f'*64
        self.assertEqual(self.request(records)['status'], 'queued')
    def test_duplicate_current_claims_are_ambiguous_not_newest(self):
        rows = self.records()
        rows.append({**rows[-1], 'object_id': 'alternative', 'revision_id': 'sha256:'+'7'*64})
        self.assertEqual(self.request(rows)['status'], 'ambiguous')
        self.assertNotIn('notes', self.request(rows))
    def test_cached_measurement_requests_proposal_once_without_separation(self):
        first, second = self.request(), self.request()
        self.assertEqual(first['status'], 'queued')
        self.assertEqual(first['requests'], second['requests'])
        requests = bank.requests(self.store.root)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]['extractor']['id'], 'vocal-contour')
        self.assertEqual(requests[0]['feature'], 'stems/vocals')
        self.assertIsNone(requests[0]['region'])
    def test_missing_contour_production_reuses_existing_six_stems_without_legacy(self):
        self.store.contour = None
        self.store.source = 'stems-6s/vocals'
        self.store.stems.manifest['extractor']['id'] = 'stems-6s'
        context = proposers.Context(self.store, 'sha256:'+'1'*64, 48000, 480000)
        chosen = composition.selection(self.config, 'backfill', composition.Selection())
        expanded = composition.expand(chosen, context, self.store.catalog, {},
                                      {name: True for name in self.store.catalog.extractors})
        self.assertEqual(expanded.targets, (proposers.FeatureTarget('vocal-contour','stems-6s/vocals'),))
    def test_empty_reading_and_disabled_scheduler_do_not_make_another_algorithm(self):
        self.assertEqual(self.request(scheduler=False)['status'], 'unavailable')
        self.store.contour._items = []
        self.assertEqual(self.request()['status'], 'unavailable')
        self.assertEqual(bank.requests(self.store.root), [])
    def test_existing_request_for_another_source_is_refused(self):
        bank.ask(self.store, self.artifact['payload']['content']['digest'], 'vocal-contour', 'other')
        answer = self.request()
        self.assertEqual(answer['status'], 'blocked')
        self.assertEqual(answer['diagnostics'][-1]['code'], 'request-source-conflict')

    def test_changed_source_revision_cannot_reuse_a_previous_ready_pin(self):
        records = self.records()
        self.artifact['revision_id'] = 'sha256:'+'8'*64
        with self.assertRaisesRegex(ValueError, 'exact source revision'):
            self.request(records)
        self.stream['revision_id'] = 'sha256:'+'9'*64
        self.stream['refs'] = [dict(role='artifact', **propose.pin(self.artifact))]
        self.assertEqual(self.request(records)['status'], 'queued')
        self.assertNotIn('notes', self.request(records))

    def test_scope_validation_is_independent_of_producer_configuration(self):
        self.config = {}
        self.region['rate'] = 44100
        with self.assertRaisesRegex(ValueError, 'sample rate'):
            self.request()
        self.region = dict(start=0, end=480001, rate=48000)
        with self.assertRaisesRegex(ValueError, 'inside'):
            enrichment_requests.validate_scope(self.artifact, self.stream, self.region)

    def test_ready_six_stem_reading_keeps_its_actual_provider(self):
        self.store.source = 'stems-6s/vocals'
        self.store.stems.manifest['extractor']['id'] = 'stems-6s'
        rows = self.records()
        self.assertEqual(rows[-1]['payload']['stem']['extractor'], 'stems-6s')
        self.assertEqual(self.request(rows)['status'], 'ready')

    def test_bass_notes_production_is_not_a_vocal_request_policy(self):
        self.config['composition']['backfill']['produce'][0]['methods'] = ['bass-line']
        self.assertEqual(self.request()['status'], 'not_configured')
        self.assertEqual(bank.requests(self.store.root), [])

    def test_unconfigured_policy_does_not_select_new_method(self):
        self.config = {}
        self.assertEqual(self.request()['status'], 'not_configured')
        self.assertEqual(bank.requests(self.store.root), [])

if __name__ == '__main__':
    unittest.main()
