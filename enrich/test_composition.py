"""Composition selects work, while shared resolvers and the runner own input choice."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bank
import composition as c
import extract
import proposers as p


class Configuration(unittest.TestCase):
    def setUp(self):
        self.defaults = c.Selection(attempt=('key', 'beats'), features=(p.FeatureTarget('tonal'),))

    def test_absent_and_explicit_empty_are_distinct(self):
        self.assertEqual(c.selection({}, 'capture', self.defaults), self.defaults)
        empty = {'composition': {'capture': {'produce': [], 'attempt': [], 'features': []}}}
        self.assertEqual(c.selection(empty, 'capture', self.defaults), c.Selection())
        self.assertEqual(c.selection({}, 'capture', self.defaults, '').features, self.defaults.features)
        self.assertEqual(c.selection({}, 'capture', self.defaults, '').attempt, ())

    def test_legacy_and_new_features_conflict_even_when_empty(self):
        config = {'backfill': {'units': []}, 'composition': {'backfill': {'features': []}}}
        with self.assertRaisesRegex(c.CompositionError, 'conflicts'):
            c.selection(config, 'backfill', self.defaults)
        self.assertEqual(c.selection({'backfill': {'units': [['rhythm']]}}, 'backfill', self.defaults).features,
                         (p.FeatureTarget('rhythm'),))

    def test_unknown_and_malformed_configuration_is_refused(self):
        cases = [{'composition': {'other': {}}}, {'composition': {'capture': {'wat': []}}},
                 {'composition': {'capture': {'attempt': 'key'}}},
                 {'composition': {'capture': {'features': [['tonal', None]]}}},
                 {'composition': {'capture': {'produce': [{'contract': ['x', '1'], 'methods': []}]}}},
                 {'composition': {'capture': {'produce': [{'contract': ['x', '1'], 'methods': ['key', 'key']}]}}}]
        for config in cases:
            with self.subTest(config=config), self.assertRaises(c.CompositionError):
                c.selection(config, 'capture', self.defaults)

    def test_standalone_runner_accepts_only_outer_composition_shape(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text(json.dumps({'composition': {'unknown-to-runner': {}}}))
            self.assertIn('composition', extract.load_config(str(path)))
            path.write_text(json.dumps({'composition': []}))
            with self.assertRaisesRegex(extract.RunError, 'composition'):
                extract.load_config(str(path))


class Expansion(unittest.TestCase):
    def setUp(self):
        self.catalog = bank.Catalog.load()
        self.context = SimpleNamespace(region=None, content='digest',
                                       bank=Mock(entry=Mock(return_value=None), entry_from=Mock(return_value=None)))
        self.requirement = p.Requirement((('test/claim', '1'),), ('whole', 'region'),
                                        (p.InputPath('direct', (p.FeatureRead('rhythm', ('pulse',)),),
                                                     (p.FeatureTarget('rhythm'),)),), (), 'direct')
        self.registry = {'method': SimpleNamespace(requirement=self.requirement)}
        self.result = c.ResultTarget(('test/claim', '1'), ('method',))

    def expanded(self, selection, resolution, capabilities=None):
        with patch.dict(p.PROPOSERS, self.registry, clear=True), patch.object(p, 'resolve_inputs', return_value=resolution) as resolver:
            result = c.expand(selection, self.context, self.catalog, capabilities=capabilities)
            resolver.assert_called_once_with('method', self.context)
            return result

    def unresolved(self, paths=None):
        return SimpleNamespace(ready=False, path='direct', missing=('rhythm/pulse',),
                               production=paths if paths is not None else self.requirement.paths)

    def test_only_produce_expands_and_stable_dedup_executes_method_once(self):
        attempt = self.expanded(c.Selection(attempt=('method',)), self.unresolved(), {'rhythm': True})
        self.assertEqual(attempt.targets, ())
        produced = self.expanded(c.Selection((self.result,), ('method',), (p.FeatureTarget('rhythm'),)),
                                 self.unresolved(), {'rhythm': True})
        self.assertEqual(produced.targets, (p.FeatureTarget('rhythm'),))
        self.assertEqual(produced.proposers, ('method',))
        self.assertTrue(any(d.code == 'production-required' for d in produced.diagnostics))

    def test_cached_path_never_produces_speculative_alternative(self):
        ready = SimpleNamespace(ready=True, path='cached-alternative', missing=(), production=())
        result = self.expanded(c.Selection((self.result,)), ready, {'rhythm': True})
        self.assertEqual(result.targets, ())
        self.assertEqual(result.diagnostics[0].path, 'cached-alternative')

    def test_unknown_capability_blocks_and_reports_cause(self):
        result = self.expanded(c.Selection((self.result,)), self.unresolved())
        self.assertEqual(result.targets, ())
        cause = next(d for d in result.diagnostics if d.code == 'unconfigured-path')
        self.assertEqual(cause.prerequisites, ('rhythm',))
        self.assertIsNone(cause.as_json()['cost'])

    def test_first_configured_path_is_selected_without_running_or_fallback(self):
        paths = (p.InputPath('first', (), (p.FeatureTarget('tonal'),)),
                 p.InputPath('second', (), (p.FeatureTarget('rhythm'),)))
        with patch.object(extract, 'run_default', side_effect=AssertionError('must never execute')):
            result = self.expanded(c.Selection((self.result,)), self.unresolved(paths),
                                   {'tonal': False, 'rhythm': True})
        self.assertEqual(result.targets, (p.FeatureTarget('rhythm'),))
        self.assertTrue(any(d.path == 'first' and d.code == 'unconfigured-path' for d in result.diagnostics))

    def test_cached_stem_does_not_require_its_producer_capability(self):
        paths = (p.InputPath('bass-measurement', (), (p.FeatureTarget('tonal', 'stems/bass'),)),)
        self.context.bank.entry_from.side_effect = lambda content, source, name: object() if name == 'frames' else None
        result = self.expanded(c.Selection((self.result,)), self.unresolved(paths),
                               {'tonal': True, 'stems': False, 'frames': False})
        self.assertEqual(result.targets, (p.FeatureTarget('tonal', 'stems/bass'),))
        self.context.bank.entry_from.assert_called_with('digest', 'stems/bass', 'tonal')

    def test_exact_contract_unknown_method_scope_and_nonfile_sources_fail(self):
        with patch.dict(p.PROPOSERS, self.registry, clear=True):
            for selection in (c.Selection((c.ResultTarget(('test/claim', '2'), ('method',)),)),
                              c.Selection(attempt=('unknown',)),
                              c.Selection(features=(p.FeatureTarget('tonal', 'rhythm/pulse'),))):
                with self.subTest(selection=selection), self.assertRaises(c.CompositionError):
                    c.validate(selection, self.catalog)
            with self.assertRaises(c.CompositionError):
                c.validate(c.Selection(), self.catalog, 'unsupported')

    def test_all_registered_attempts_have_valid_declarations(self):
        # Anchors validation at the registry callers actually dispatch, including
        # the explicitly unsupported timed-lyrics attempt.
        for scope in ('whole', 'region'):
            c.validate(c.Selection(attempt=tuple(p.PROPOSERS)), self.catalog, scope)

    def test_produced_unsupported_scope_is_not_rescued_by_attempt(self):
        requirement = SimpleNamespace(contracts=(('test/claim', '1'),), scopes=(), paths=(),
                                      optional=(), resolver='unsupported')
        with patch.dict(p.PROPOSERS, {'method': SimpleNamespace(requirement=requirement)}, clear=True):
            c.validate(c.Selection(attempt=('method',)), self.catalog)
            with self.assertRaisesRegex(c.CompositionError, 'scope'):
                c.validate(c.Selection((self.result,), ('method',)), self.catalog)

    def test_registered_complex_recipes_reach_expansion_without_optional_work(self):
        b = Mock()
        b.entry.return_value = None
        b.entry_from.return_value = None
        b.measured_over.return_value = []
        b.default_entry.return_value = (None, None)
        b.default_input.return_value = None
        context = p.Context(b, 'sha256:' + 'a' * 64, 44100, 441000)
        capabilities = dict.fromkeys(self.catalog.extractors, True)
        cases = {
            'bass': (p.FeatureTarget('stems'), p.FeatureTarget('tonal', 'stems/bass'), p.FeatureTarget('rhythm')),
            'bass-line': (p.FeatureTarget('stems'), p.FeatureTarget('tonal', 'stems/bass'),
                          p.FeatureTarget('f0-pesto', 'stems/bass')),
            'notes': (p.FeatureTarget('melody'),),
            'vocal-notes': (p.FeatureTarget('stems'), p.FeatureTarget('vocal-contour', 'stems/vocals')),
        }
        for method, expected in cases.items():
            result = c.ResultTarget(p.PROPOSERS[method].requirement.contracts[0], (method,))
            with self.subTest(method=method):
                expanded = c.expand(c.Selection((result,)), context, self.catalog, capabilities=capabilities)
                self.assertEqual(expanded.targets, expected)
                self.assertEqual(expanded.proposers, (method,))


if __name__ == '__main__':
    unittest.main()
