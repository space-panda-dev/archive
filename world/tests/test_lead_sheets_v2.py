"""Lead sheet version 2 (ADR 0113): version 1's rules with the inputs ADR
0113 adds, a melody that is never a sketch, and words only on the notes the
lead sheet shows.

Run from the repository root: python3 -m unittest world.tests.test_lead_sheets_v2.
Every example is synthetic (INV-23); the fixtures are test_lead_sheets.py's.
"""

import copy
import unittest

from world.lead_sheets import (
    FORM_SPANS_V2,
    FORM_V2,
    LEAD_SHEET_V2,
    MELODY_SPANS,
    MELODY_V2,
    METER_LAYERS_V3,
    TIMED_LYRICS,
    UNDERLAY,
    LeadSheetError,
    changes_since,
    check_lead_sheet,
    check_lead_sheet_revision,
    rendered_inputs,
)
from world.tests.test_lead_sheets import (
    AGENTS, ANN, ANN_SHEET, BO, LYRICS, MIX, MIX_STREAM, OTHER_LYRICS, TARGETS, WORK, WORK_MELODY, pin, ref,
    take_sheet, work_sheet,
)

NOTES_TAKE = pin(0x40)            # a notes claim of the mix's voice
OTHER_NOTES = pin(0x41)           # another notes claim, which no melody spans claim selects
MELODY_TWO = pin(0x42)            # a measured melody version 2
SKETCH = pin(0x43)                # a sketch: pitches, no rhythm
FORM_TWO = pin(0x44)              # a form with an open repeat
SPANS_TWO = pin(0x45)             # form spans version 2 of the mix
METER_THREE = pin(0x46)           # an untimed meter version 3
VERSE_ONE = pin(0x47)             # an underlay of LYRICS under MELODY_TWO
STRAY_UNDERLAY = pin(0x48)        # an underlay of another document
SKETCH_UNDERLAY = pin(0x49)       # an underlay under a melody the sheet does not pin
SPANS_SELECTING = pin(0x4A)       # melody spans selecting NOTES_TAKE
CARRIED_TIMING = pin(0x4B)        # timed lyrics whose syllables name NOTES_TAKE
ASTRAY_TIMING = pin(0x4C)         # timed lyrics whose syllables name OTHER_NOTES


def claim(at, contract, payload, refs):
    return {"shape": "claim", "id": at["id"], "contract": contract, "payload": payload, "refs": refs}


def timing(notes):
    return {"sample_rate": 8000, "lines": [{"line": 1, "start_sample": 0, "end_sample": 8000, "words": [
        {"word": 1, "start_sample": 0, "end_sample": 4000, "syllables": [
            {"syllable": 1, "start_char": 0, "end_char": 3, "start_sample": 0, "end_sample": 4000,
             "carried_by": {"notes": notes["revision_id"], "onsets": [0]}}]}]}]}


V2_TARGETS = {
    **TARGETS,
    MELODY_TWO["revision_id"]: claim(MELODY_TWO, MELODY_V2, {"notes": [
        {"pitch": 64, "duration": {"count": 1, "unit": 4}}]}, [ref("work", WORK)]),
    SKETCH["revision_id"]: claim(SKETCH, MELODY_V2, {"notes": [{"pitch": 64}, {"pitch": 67}]}, [ref("work", WORK)]),
    FORM_TWO["revision_id"]: claim(FORM_TWO, FORM_V2, {}, [ref("work", WORK)]),
    SPANS_TWO["revision_id"]: claim(SPANS_TWO, FORM_SPANS_V2, {},
                                    [ref("source", MIX), ref("stream", MIX_STREAM), ref("form", FORM_TWO)]),
    METER_THREE["revision_id"]: claim(METER_THREE, METER_LAYERS_V3, {}, [ref("work", WORK), ref("form", FORM_TWO)]),
    VERSE_ONE["revision_id"]: claim(VERSE_ONE, UNDERLAY, {}, [ref("melody", MELODY_TWO), ref("document", LYRICS)]),
    STRAY_UNDERLAY["revision_id"]: claim(STRAY_UNDERLAY, UNDERLAY, {},
                                         [ref("melody", MELODY_TWO), ref("document", OTHER_LYRICS)]),
    SKETCH_UNDERLAY["revision_id"]: claim(SKETCH_UNDERLAY, UNDERLAY, {},
                                          [ref("melody", SKETCH), ref("document", LYRICS)]),
    SPANS_SELECTING["revision_id"]: claim(SPANS_SELECTING, MELODY_SPANS, {"spans": [
        {"start_sample": 0, "end_sample": 8000, "notes": NOTES_TAKE["revision_id"]}]},
        [ref("source", MIX), ref("stream", MIX_STREAM), ref("notes", NOTES_TAKE)]),
    CARRIED_TIMING["revision_id"]: claim(CARRIED_TIMING, TIMED_LYRICS, timing(NOTES_TAKE),
                                         [ref("source", MIX), ref("stream", MIX_STREAM), ref("document", LYRICS),
                                          ref("notes", NOTES_TAKE)]),
    ASTRAY_TIMING["revision_id"]: claim(ASTRAY_TIMING, TIMED_LYRICS, timing(OTHER_NOTES),
                                        [ref("source", MIX), ref("stream", MIX_STREAM), ref("document", LYRICS),
                                         ref("notes", OTHER_NOTES)]),
}


def check(sheet, version="2"):
    return check_lead_sheet(sheet, V2_TARGETS, AGENTS, version=version)


def replacing(sheet, role, old, new):
    sheet = copy.deepcopy(sheet)
    sheet["refs"] = [ref(role, new) if r["role"] == role and r["revision_id"] == old["revision_id"] else r
                     for r in sheet["refs"]]
    return sheet


def adding(sheet, *refs):
    sheet = copy.deepcopy(sheet)
    sheet["refs"] += list(refs)
    return sheet


def song_sheet():
    """Ann's lead sheet of her song at version 2: a measured melody version
    2, a form with an open repeat, bars laid in it, and verse 1's words
    under her tune."""
    sheet = replacing(work_sheet(), "melody", WORK_MELODY, MELODY_TWO)
    sheet["refs"] = [r for r in sheet["refs"] if r["role"] not in ("form", "meter")]
    return adding(sheet, ref("form", FORM_TWO), ref("meter", METER_THREE), ref("underlay", VERSE_ONE))


class LeadSheetVersion2(unittest.TestCase):
    def test_contract_is_named(self):
        self.assertEqual(LEAD_SHEET_V2, ("archive.claim/lead_sheet", "2"))

    def test_version_1_sheets_hold_at_version_2(self):
        check(work_sheet())
        check(take_sheet())

    def test_the_new_inputs(self):
        check(song_sheet())
        check(adding(take_sheet(), ref("form", SPANS_TWO)))

    def test_version_1_refuses_the_new_inputs_and_the_underlay(self):
        with self.assertRaisesRegex(LeadSheetError, "underlay"):
            check(song_sheet(), version="1")
        with self.assertRaisesRegex(LeadSheetError, "melody 0 must pin"):
            check(replacing(work_sheet(), "melody", WORK_MELODY, MELODY_TWO), version="1")
        with self.assertRaises(LeadSheetError):
            check(adding(take_sheet(), ref("form", SPANS_TWO)), version="1")

    def test_a_sketch_is_never_pinned(self):
        sheet = replacing(work_sheet(), "melody", WORK_MELODY, SKETCH)
        with self.assertRaisesRegex(LeadSheetError, "sketch"):
            check(sheet)

    def test_an_underlay_needs_the_lyrics_it_places(self):
        sheet = song_sheet()
        sheet["refs"] = [r for r in sheet["refs"] if r["role"] != "lyrics"]
        with self.assertRaisesRegex(LeadSheetError, "without the lyric document"):
            check(sheet)
        with self.assertRaisesRegex(LeadSheetError, "exactly the lyric document"):
            check(replacing(song_sheet(), "underlay", VERSE_ONE, STRAY_UNDERLAY))

    def test_an_underlay_is_under_a_melody_the_sheet_pins(self):
        with self.assertRaisesRegex(LeadSheetError, "does not pin"):
            check(replacing(song_sheet(), "underlay", VERSE_ONE, SKETCH_UNDERLAY))

    def test_timed_lyrics_carried_by_notes_a_melody_spans_claim_selects(self):
        sheet = take_sheet()
        sheet = replacing(sheet, "lyric_timing", pin(0x25), CARRIED_TIMING)
        with self.assertRaisesRegex(LeadSheetError, "no pinned melody spans"):
            check(sheet)
        check(adding(sheet, ref("melody", SPANS_SELECTING)))
        with self.assertRaisesRegex(LeadSheetError, "no pinned melody spans"):
            check(adding(replacing(sheet, "lyric_timing", CARRIED_TIMING, ASTRAY_TIMING),
                         ref("melody", SPANS_SELECTING)))
        # Version 1 does not state the rule.
        check(sheet, version="1")

    def test_derived_from_a_version_1_or_2_lead_sheet(self):
        check(adding(song_sheet(), ref("derived_from", ANN_SHEET)))

    def test_a_version_1_sheets_next_revision_may_be_version_2(self):
        check_lead_sheet_revision(work_sheet(), song_sheet())
        with self.assertRaises(LeadSheetError):
            check_lead_sheet_revision(work_sheet(), song_sheet() | {"agent": BO["id"]})

    def test_underlays_are_rendered_and_followed(self):
        self.assertIn(VERSE_ONE["revision_id"], {r["revision_id"] for r in rendered_inputs(song_sheet())})
        later = {"revision_id": "sha256:" + "4f" * 32, "shape": "claim", "supersedes": [VERSE_ONE["revision_id"]],
                 "refs": []}
        self.assertEqual([c.kind for c in changes_since(song_sheet(), [later])], ["revised"])

    def test_an_unknown_version_is_refused(self):
        with self.assertRaisesRegex(LeadSheetError, "version"):
            check(work_sheet(), version="3")


if __name__ == "__main__":
    unittest.main()
