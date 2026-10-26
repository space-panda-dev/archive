"""Operational explanations preserve Bank identity without starting work."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bank
import composition as c
import proposers as p
from test_bank import Fixture


class Diagnostics(unittest.TestCase):
    def test_missing_source_is_unknown_and_json_serializable(self):
        catalog = bank.Catalog.load()
        with tempfile.TemporaryDirectory() as root:
            context = p.Context(bank.Bank(Path(root), catalog), 'sha256:' + '1' * 64, 10, 100)
            chosen = c.Selection(features=(p.FeatureTarget('tonal', 'stems/bass'),))
            expanded = c.expand(chosen, context, catalog)
            with patch('subprocess.Popen', side_effect=AssertionError('process')):
                result = c.explain(expanded, chosen, context, catalog,
                                   evidence={'artifact_revision': 'synthetic'}, config_identity='frozen')
            self.assertIsNone(result['targets'][0]['key'])
            self.assertIn('deferred', result['targets'][0]['reason'])
            self.assertEqual(result['config_identity'], 'frozen')
            json.dumps(result)

    def test_real_bank_selected_region_identity_and_no_processes(self):
        f = Fixture(self)
        f.add('a')
        self.assertEqual(f.run('a')[0], 0)
        reader = f.reader()
        entry = reader.entry(f.digest, 'a')
        region = {'start': 10, 'end': 20, 'rate': 10}
        context = p.Context(bank.RegionView(reader, f.digest, region), f.digest, 10, 100, region)
        chosen = c.Selection(features=(p.FeatureTarget('a'),))
        expanded = c.expand(chosen, context, reader.catalog)
        with patch('subprocess.Popen', side_effect=AssertionError('process')):
            result = c.explain(expanded, chosen, context, reader.catalog)
        target = result['targets'][0]
        self.assertEqual(target['key'], entry.manifest['key'])
        self.assertEqual(target['parameters'], entry.manifest['parameters'])
        self.assertEqual(target['coverage'], {'requested': region, 'selected': None})
        self.assertEqual(target['equivalence'], 'configured-parameters')

    def test_missing_region_expected_key_uses_dispatch_catalog(self):
        f = Fixture(self)
        f.add('a')
        reader = f.reader()
        region = {'start': 10, 'end': 20, 'rate': 10}
        context = p.Context(bank.RegionView(reader, f.digest, region), f.digest, 10, 100, region)
        chosen = c.Selection(features=(p.FeatureTarget('a'),))
        actual = reader.catalog.within(f.digest, region).keys(f.digest, ['a'])['a']
        self.assertNotEqual(actual, reader.catalog.keys(f.digest, ['a'])['a'])
        explained = c.explain(c.expand(chosen, context, reader.catalog), chosen, context, reader.catalog)
        self.assertEqual(explained['targets'][0]['expected_key'], actual)

    def test_damaged_attempt_does_not_abort_unrelated_feature_targets(self):
        catalog = bank.Catalog.load()
        with tempfile.TemporaryDirectory() as root:
            reader = bank.Bank(Path(root), catalog)
            context = p.Context(reader, 'sha256:' + '1' * 64, 10, 100)
            chosen = c.Selection(attempt=('beats',), features=(p.FeatureTarget('rhythm'), p.FeatureTarget('tonal')))
            original = reader.entry
            def damaged(content, name, *args, **kwargs):
                if name == 'rhythm':
                    raise bank.BankError('synthetic damaged rhythm entry')
                return original(content, name, *args, **kwargs)
            with patch.object(reader, 'entry', side_effect=damaged):
                expanded = c.expand(chosen, context, catalog)
                explained = c.explain(expanded, chosen, context, catalog)
            self.assertEqual(expanded.targets, chosen.features)
            self.assertEqual(expanded.diagnostics[-1].code, 'damaged')
            self.assertEqual(explained['methods'][0]['observation']['status'], 'damaged')
            self.assertEqual(explained['targets'][0]['cache'], 'damaged')
            self.assertEqual(explained['targets'][1]['cache'], 'missing')

    def test_lost_selected_source_is_a_damaged_method_observation(self):
        catalog = bank.Catalog.load()
        with tempfile.TemporaryDirectory() as root:
            context = p.Context(bank.Bank(Path(root), catalog), 'sha256:' + '1' * 64, 10, 100)
            chosen = c.Selection(attempt=('notes',))
            with patch.object(p, 'resolve_inputs', side_effect=p.ProposerError('selected source entry is gone')):
                expanded = c.expand(chosen, context, catalog)
                explained = c.explain(expanded, chosen, context, catalog)
            self.assertEqual(expanded.diagnostics[-1].code, 'damaged')
            self.assertEqual(explained['methods'][0]['observation']['status'], 'damaged')

    def test_optional_declarations_never_force_lookup(self):
        catalog = bank.Catalog.load()
        requirement = p.Requirement((('test/claim', '1'),), ('whole',),
                                    (p.InputPath('direct', (), ()),),
                                    (p.FeatureRead('tonal', (next(iter(catalog['tonal'].outputs)),)),), 'direct')
        method = p.Proposer('synthetic', '1', lambda _: None, requirement)
        with tempfile.TemporaryDirectory() as root:
            reader = bank.Bank(Path(root), catalog)
            context = p.Context(reader, 'sha256:' + '1' * 64, 10, 100)
            chosen = c.Selection(attempt=('synthetic',))
            with patch.dict(p.PROPOSERS, {'synthetic': method}), patch.object(
                    reader, 'entry', side_effect=AssertionError('optional lookup')):
                expanded = c.expand(chosen, context, catalog)
                result = c.explain(expanded, chosen, context, catalog)
        self.assertIsNone(result['methods'][0]['optional'][0]['key'])
        self.assertEqual(result['methods'][0]['version'], '1')
