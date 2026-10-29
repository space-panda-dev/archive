"""Bounded input recipes: identity, cached alternatives and actual consumption."""
from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

import proposers as p


class Inputs(unittest.TestCase):
    def context(self):
        b = Mock()
        b.entry.return_value = None
        b.measured_over.return_value = []
        b.default_entry.return_value = (None, None)
        b.default_input.return_value = None
        return p.Context(b, "sha256:" + "a" * 64, 44100, 441000)

    def test_registry_all_declares_bounded_requirements(self):
        self.assertTrue(p.PROPOSERS)
        for name, method in p.PROPOSERS.items():
            with self.subTest(name=name):
                r = method.requirement
                self.assertIn(r.resolver, ("direct", "bass", "bass-line", "notes", "unsupported"))
                resolved = p.resolve_inputs(name, self.context())
                self.assertFalse(resolved.ready)
                self.assertEqual(bool(resolved.production), r.resolver != "unsupported")

    def test_actual_direct_dispatch_consumes_resolver_selected_entry(self):
        ctx = self.context()
        ctx.bank.entry.side_effect = AssertionError("execution independently selected bank input")
        entry = Mock()
        entry.items.return_value = []
        resolved = p.ResolvedInputs("direct", {"rhythm": entry})
        with patch.object(p, "resolve_inputs", return_value=resolved) as resolver, patch.object(p, "read"):
            answer = p.PROPOSERS["beats"].propose(ctx)
        resolver.assert_called_once_with("beats", ctx)
        self.assertIsInstance(answer, p.Lacking)  # valid empty; never choose another algorithm
        self.assertFalse(answer.missing)

    def test_cold_bass_recipes_are_ordered_and_complete(self):
        resolved = p.resolve_inputs("bass-line", self.context())
        self.assertEqual([x.id for x in resolved.production], ["bass-pesto", "bass-basic-pitch"])
        self.assertEqual(resolved.production[0].production,
                         (p.FeatureTarget("stems"), p.FeatureTarget("tonal", "stems/bass"),
                          p.FeatureTarget("f0-pesto", "stems/bass")))
        self.assertEqual(resolved.production[1].production[-1], p.FeatureTarget("notes-basic-pitch", "stems/bass"))

    def test_complete_cached_lower_bass_alternative_uses_one_stem_identity(self):
        ctx = self.context()
        first, equivalent, tonal, notes = object(), object(), object(), object()
        ctx.bank.measured_over.return_value = [
            (first, {"tonal": tonal, "f0-pesto": None, "notes-basic-pitch": None}),
            (equivalent, {"tonal": tonal, "f0-pesto": None, "notes-basic-pitch": notes})]
        found = p.resolve_inputs("bass-line", ctx)
        self.assertTrue(found.ready)
        self.assertIs(found.entries["stems"], equivalent)
        self.assertIs(found.entries["notes-basic-pitch"], notes)
        self.assertEqual(found.path, "bass-basic-pitch")
        self.assertFalse(found.production)
        ctx.bank.entry.assert_not_called()  # basic-pitch does not require recording tuning

    def test_cached_stem_needs_no_configured_stem_producer(self):
        ctx = self.context()
        ctx.bank.measured_over.return_value = [(object(), {"tonal": None, "f0-pesto": None,
                                                          "notes-basic-pitch": None})]
        found = p.resolve_inputs("bass-line", ctx)
        self.assertTrue(found.production)
        self.assertFalse(any(t.extractor == "stems" for path in found.production for t in path.production))

    def vocal(self, entry=None, pesto=None):
        ctx = self.context()
        ctx.bank.default_entry.return_value = (entry, "stems/vocals")
        ctx.bank.entry_from.return_value = pesto
        stem = Mock()
        stem.output.return_value = {"sha256": "vocal-content"}
        ctx.bank.candidates.return_value = [stem]
        return ctx, stem

    def test_cached_vocal_melody_before_producing_pesto(self):
        melody = SimpleNamespace(manifest={"content": "vocal-content"})
        ctx, stem = self.vocal(melody)
        result = p.resolve_inputs("notes", ctx)
        self.assertTrue(result.ready)
        self.assertEqual(result.path, "melody")
        self.assertIs(result.entries["stems"], stem)
        self.assertFalse(result.production)
        ctx.bank.entry.assert_not_called()

    def test_missing_vocal_measurement_never_selects_cached_mix_or_produces_stems(self):
        ctx, stem = self.vocal()
        ctx.bank.entry.side_effect = AssertionError("must not look at cached mix/optional inputs")
        result = p.resolve_inputs("notes", ctx)
        self.assertFalse(result.ready)
        self.assertIs(result.entries["stems"], stem)
        self.assertEqual([x.production for x in result.production],
                         [(p.FeatureTarget("f0-pesto", "stems/vocals"),),
                          (p.FeatureTarget("melody", "stems/vocals"),)])

    def test_mix_and_nonvocal_preference_never_produce_pesto_or_stems(self):
        for source in (None, "stems/other"):
            ctx = self.context()
            ctx.bank.default_entry.return_value = (None, source)
            ctx.bank.candidates.return_value = [Mock()]
            result = p.resolve_inputs("notes", ctx)
            self.assertEqual([path.production for path in result.production],
                             [(p.FeatureTarget("melody", source),)])

    def test_actual_mix_melody_never_reads_corrupt_unused_optional_cache(self):
        ctx = self.context()
        entry = Mock()
        entry.items.return_value = []
        ctx.bank.default_entry.return_value = (entry, None)
        ctx.bank.entry.side_effect = AssertionError("unused optional cache was read")
        with patch.object(p, "read"):
            answer = p.PROPOSERS["notes"].propose(ctx)
        self.assertIsInstance(answer, p.Lacking)
        self.assertFalse(answer.missing)  # valid empty path never becomes missing production

    def test_optional_entry_is_resolved_lazily_once_and_consumed_by_helper(self):
        ctx = self.context()
        resolved = p.resolve_inputs("bass-line", ctx)
        ctx.bank.entry.assert_not_called()
        tuning = Mock()
        tuning.scalar.side_effect = lambda name: {"tuning_confidence": 1, "tuning_cents": 7}[name]
        ctx.bank.entry.return_value = tuning
        bound = replace(ctx, inputs=resolved)
        with patch.object(p, "read"):
            self.assertEqual(p.tuning_of(p.Proposal([], {}), bound)[0], 7)
            self.assertEqual(p.tuning_of(p.Proposal([], {}), bound)[0], 7)
        ctx.bank.entry.assert_called_once_with(ctx.content, "tonal")

    def test_region_context_is_retained_by_shared_resolver(self):
        ctx = replace(self.context(), region={"start": 10, "end": 20, "rate": 1})
        resolved = p.resolve_inputs("notes", ctx)
        self.assertEqual(resolved.production[0].production, (p.FeatureTarget("melody"),))
        ctx.bank.default_entry.assert_called_once_with(ctx.content, "melody")


if __name__ == "__main__":
    unittest.main()
