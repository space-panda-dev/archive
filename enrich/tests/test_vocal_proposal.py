"""The registered notes method carries an explicitly measured contour to claims."""
from __future__ import annotations

import copy
import sys
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import proposers


class Entry:
    def __init__(self, extractor, content, items=(), offset=0):
        self.manifest = {"extractor": {"id": extractor, "version": "1"}, "content": content,
                         "key": "sha256:" + "2" * 64}
        self._items, self._offset = list(items), offset

    def items(self, output):
        assert output == "notes"
        return self._items

    def output(self, output):
        return {"kind": "file", "sha256": "sha256:" + "3" * 64}

    def provenance(self, output):
        return {"feature": self.manifest["extractor"]["id"] + "/" + output,
                "extractor": self.manifest["extractor"], "entry": self.manifest["key"],
                "software": [{"name": "audio-only-test", "version": "1"}]}

    def shift(self, rate):
        return self._offset


class Bank:
    def __init__(self, contour):
        self.contour = contour
        self.stems = Entry("stems", "sha256:" + "1" * 64)

    def measured_over(self, content, feature, ids):
        assert feature == "stems/vocals" and ids == ["vocal-contour"]
        return [(self.stems, {"vocal-contour": self.contour})]

    def entry(self, content, extractor):
        assert extractor == "tonal"
        return None

    def default_entry(self, *args):
        raise AssertionError("A measured preferred contour must not call the legacy selector")


class VocalProposal(unittest.TestCase):
    def context(self, items, shift=0, region=None):
        contour = Entry("vocal-contour", "sha256:" + "3" * 64, items, shift)
        return proposers.Context(Bank(contour), "sha256:" + "1" * 64, 48000, 480000, region)

    def test_registered_notes_path_preserves_repeated_pitch_and_performed_times(self):
        context = self.context([{"start": .123, "end": .431, "value": 60.1},
                                {"start": .431, "end": .789, "value": 60.1},
                                {"start": .812, "end": 1.104, "value": 72.0}])
        result = proposers.PROPOSERS["notes"].propose(context)
        self.assertIsInstance(result, proposers.Proposal)
        self.assertEqual(result.outputs[0].payload["notes"], [
            {"onset_sample": 5904, "offset_sample": 20688, "pitch": 60},
            {"onset_sample": 20688, "offset_sample": 37872, "pitch": 60},
            {"onset_sample": 38976, "offset_sample": 52992, "pitch": 72}])
        self.assertEqual(result.outputs[0].payload["part"], "vocals")
        self.assertEqual({r["feature"] for r in result.features},
                         {"stems/vocals", "vocal-contour/notes", "vocal-contour/provenance"})
        self.assertEqual(result.outputs[0].payload["stem"]["sha256"], "sha256:" + "3" * 64)
        self.assertNotIn("builder", result.settings)

    def test_measured_pitch_is_not_corrected_by_a_tonal_estimate(self):
        # +30 cents would change 60.55 to 60.25 and round it down. The new
        # method must keep the measured A440 pitch and must not read tonal.
        context = self.context([{"start": .1, "end": .4, "value": 60.55}])
        original = copy.deepcopy(context.bank.contour._items)
        with patch.object(context.bank, "entry", side_effect=AssertionError("tonal must not be read")):
            result = proposers.PROPOSERS["notes"].propose(context)
        self.assertEqual(result.outputs[0].payload["notes"][0]["pitch"], 61)
        self.assertEqual(context.bank.contour._items, original)
        self.assertEqual(result.settings["tuning_policy"], {
            "grid": "12-TET, A4 = 440 Hz",
            "adjustment_cents": "0",
            "selection": "fixed uncorrected measured-pitch reading",
            "rounding": "nearest MIDI step, half to even",
            "cents": "not emitted; no tuning claim selected",
            "contour": "bank pitch and note features remain unchanged",
        })
        self.assertNotIn("cents", result.outputs[0].payload["notes"][0])
        self.assertFalse(any(item["feature"].startswith("tonal/") for item in result.features))

    def test_region_times_are_placed_and_clamped_to_the_stream_range(self):
        context = self.context([{"start": 0., "end": .5, "value": 62.}], shift=48000,
                               region={"start": 48000, "end": 96000, "rate": 48000})
        result = proposers.PROPOSERS["notes"].propose(context)
        self.assertEqual(result.outputs[0].payload["notes"],
                         [{"onset_sample": 48000, "offset_sample": 72000, "pitch": 62}])

    def test_empty_contour_is_an_explicit_lack_not_a_legacy_substitution(self):
        result = proposers.PROPOSERS["notes"].propose(self.context([]))
        self.assertIsInstance(result, proposers.Lacking)
        self.assertIn("no note", result.detail)

    def test_malformed_pitch_is_refused(self):
        with self.assertRaises(proposers.ProposerError):
            proposers.PROPOSERS["notes"].propose(self.context([
                {"start": .1, "end": .2, "value": float("nan")}]))


if __name__ == "__main__":
    unittest.main()
