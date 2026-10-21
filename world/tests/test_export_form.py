"""ADR 0089's exporters as ADR 0113 amends them: a form's repeats, endings
and jumps; open repeats with their instruction; last-time endings; stacked
verses; and a sketch, which is never exported.

Run from the repository root: python3 -m unittest world.tests.test_export_form.
Every example is synthetic (INV-23).
"""

import unittest

from world.exports import (
    LAST_TIME,
    OPEN_COUNT_LOSS,
    ExportError,
    form_marks,
    lilypond_form,
    musicxml_form,
    musicxml_lyric,
    refuse_sketch,
    verse_numbers,
)
from world.form import check_form, check_form_v2

# ADR 0113's jazz chart: intro, head, open solos on the form, head out, a
# last-time ending, and a coda.
JAZZ = {"sections": [{"label": "Intro", "kind": "intro"}, {"label": "A", "kind": "head"},
                     {"label": "B", "kind": "head"}, {"label": "Solos on the form", "kind": "solo"},
                     {"label": "Last time"}, {"label": "Coda", "kind": "coda"}],
        "repeats": [{"first": 1, "last": 4, "instruction": "open for solos, head out",
                     "endings": [{"first": 3, "last": 3}, {"first": 4, "last": 4, "passes": ["last"]}]}]}
# A fixed repeat with first and second endings, then D.S. al Coda.
SONG = {"sections": [{"label": "Intro"}, {"label": "Verse"}, {"label": "1."}, {"label": "2."},
                     {"label": "Bridge"}, {"label": "Coda"}],
        "repeats": [{"first": 1, "last": 3, "times": 2,
                     "endings": [{"first": 2, "last": 2, "passes": [1]}, {"first": 3, "last": 3, "passes": [2]}]}],
        "jumps": [{"after": 4, "kind": "dal_segno", "to": 1, "then": "coda", "coda_from": 1, "coda": 5}]}
VAMP = {"sections": [{"label": "Vamp"}, {"label": "Out"}],
        "repeats": [{"first": 0, "last": 0, "instruction": "vamp until cue"}]}


def claim(payload):
    return {"payload": payload, "refs": [{"role": "work", "id": "urn:uuid:00000000-0000-4000-8000-0000000000b1",
                                          "revision_id": "sha256:" + "b1" * 32}]}


class Fixtures(unittest.TestCase):
    def test_the_forms_pass_their_own_rules(self):
        check_form_v2(claim(JAZZ))
        check_form(claim(SONG))
        check_form_v2(claim(VAMP))


class RepeatSigns(unittest.TestCase):
    def test_an_open_repeat_has_no_times_and_its_instruction_as_words(self):
        sections, losses = musicxml_form(JAZZ)
        self.assertEqual(losses, [OPEN_COUNT_LOSS])
        self.assertIn('<repeat direction="forward"/>', sections[1]["before"][0])
        self.assertIn("<words>open for solos, head out</words>", sections[1]["before"][1])
        self.assertIn('<repeat direction="backward"/>', sections[3]["after"][-1])
        self.assertNotIn("times=", sections[3]["after"][-1])

    def test_a_last_time_ending_prints_the_instruction_or_last_time(self):
        sections, _ = musicxml_form(JAZZ)
        self.assertIn('type="start">open for solos, head out</ending>', sections[4]["before"][0])
        self.assertIn('type="discontinue"', sections[4]["after"][0])
        plain = {**JAZZ, "repeats": [{k: v for k, v in JAZZ["repeats"][0].items() if k != "instruction"}]}
        sections, _ = musicxml_form(plain)
        self.assertIn(f'type="start">{LAST_TIME}</ending>', sections[4]["before"][0])

    def test_open_endings_are_numbered_as_the_played_order_lists_them(self):
        # The default is the open pass after the numbered ones; last time, the pass after that.
        marks, _ = form_marks(JAZZ)
        self.assertEqual([m["numbers"] for m in marks[3]["before"] if m["kind"] == "ending"], [[1]])
        self.assertEqual([m["numbers"] for m in marks[4]["before"] if m["kind"] == "ending"], [[2]])

    def test_a_fixed_repeat_with_endings_and_a_jump(self):
        sections, losses = musicxml_form(SONG)
        self.assertEqual(losses, [])
        self.assertIn('<repeat direction="backward" times="2"/>', sections[2]["after"][-1])
        self.assertIn('<ending number="2" type="discontinue"/>', sections[3]["after"][-1])
        self.assertNotIn("backward", sections[3]["after"][-1])
        self.assertIn("<segno/>", "".join(sections[1]["before"]))
        self.assertIn('<sound tocoda="coda"/>', "".join(sections[1]["after"]))
        self.assertIn("D.S. al Coda", "".join(sections[4]["after"]))
        self.assertIn("<coda/>", "".join(sections[5]["before"]))

    def test_lilypond(self):
        text, losses = lilypond_form(JAZZ, ["i", "a", "b", "s", "l", "c"])
        self.assertEqual(losses, [OPEN_COUNT_LOSS])
        self.assertEqual(text, 'i \\mark "open for solos, head out" \\repeat volta 2 { a b \\alternative '
                               '{ { \\volta 1 s } { \\volta 2 \\mark "open for solos, head out" l } } } c')
        text, _ = lilypond_form(SONG, ["i", "v", "x", "y", "b", "c"])
        self.assertEqual(text, 'i \\repeat volta 2 { \\segnoMark v \\textEndMark "To Coda" \\alternative '
                               '{ { \\volta 1 x } { \\volta 2 y } } } b \\textEndMark "D.S. al Coda" \\codaMark c')
        text, _ = lilypond_form(VAMP, ["v", "o"])
        self.assertEqual(text, '\\mark "vamp until cue" \\repeat volta 2 { v } o')
        with self.assertRaises(ExportError):
            lilypond_form(VAMP, ["v"])

    def test_the_view_is_deterministic(self):
        self.assertEqual(musicxml_form(JAZZ), musicxml_form(JAZZ))


class StackedVerses(unittest.TestCase):
    def test_numbers_follow_the_lowest_pass_and_the_lead_sheets_order(self):
        verse_two = {"label": "Verse 2", "passes": {"repeat": 0, "passes": [2]}, "lines": []}
        verse_one = {"passes": {"repeat": 0, "passes": [1, 3]}, "lines": []}
        spanish = {"label": "Spanish", "passes": {"repeat": 0, "passes": [1]}, "lines": []}
        last = {"passes": {"repeat": 0, "passes": ["last"]}, "lines": []}
        everywhere = {"lines": []}
        self.assertEqual(verse_numbers([verse_two, verse_one, spanish, last, everywhere]), [
            {"number": 2, "prefix": "Verse 2"},
            {"number": 1, "prefix": "1., 3."},
            {"number": 3, "prefix": "Spanish"},     # 1 is taken, 2 is taken: the next free number
            {"number": 4, "prefix": LAST_TIME},     # one more than pass 3, the highest numbered
            {"number": 5, "prefix": None},          # no passes: after them all
        ])

    def test_lyric_element(self):
        self.assertEqual(musicxml_lyric(2, "lan", "begin", prefix="2.", extend=True),
                         '<lyric number="2"><syllabic>begin</syllabic><text>2. lan</text><extend/></lyric>')
        with self.assertRaises(ExportError):
            musicxml_lyric(1, "x", "whole")


class Sketches(unittest.TestCase):
    def test_a_sketch_is_never_exported(self):
        with self.assertRaisesRegex(ExportError, "sketch"):
            refuse_sketch({"notes": [{"pitch": 64}, {"pitch": 67, "duration": {"count": 1, "unit": 4}}]})
        refuse_sketch({"notes": [{"pitch": 64, "duration": {"count": 1, "unit": 4}}]})


if __name__ == "__main__":
    unittest.main()
