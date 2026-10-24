"""The underlay (ADR 0113): a lyric document's words under a Work-level
melody's items, by index, optionally under named passes of a repeat.

Run from the repository root: python3 -m unittest world.tests.test_underlay.
Only the standard library; every example, the words included, is synthetic
(INV-23).
"""

import copy
import unittest

from world.notes import (
    FORM_V2,
    UNTIMED_METER,
    UNTIMED_METER_V3,
    NotesError,
    check_underlay,
)


def pin(n: int) -> dict:
    return {"id": f"urn:uuid:00000000-0000-4000-8000-0000000000{n:02x}", "revision_id": "sha256:" + f"{n:02x}" * 32}


WORK, MELODY, DOCUMENT, FORM, METER, RUN, EARLIER, LYRICS = (pin(n) for n in range(0xb1, 0xb9))
TEXT = "Paper lanterns drifting\nharbor light again\n"


def ref(role: str, target: dict) -> dict:
    return {"role": role, **target}


def whole(pitch=60, count=1, unit=1) -> dict:
    return {"pitch": pitch, "duration": {"count": count, "unit": unit}}


# One item a bar of 4/4, but bar 2, a half note then a half rest: bar 0 is
# the intro (section 0), bars 1 and 2 the verse, bar 3 the chorus, bar 4 the
# outro.
ITEMS = [whole(60), whole(62), whole(64, 1, 2), {"rest": True, "duration": {"count": 1, "unit": 2}},
         whole(65), whole(67)]
SECTIONS = [0, 1, 1, 2, 3]


def melody_claim(items=ITEMS, meter=True, **payload) -> dict:
    refs = [ref("work", WORK)] + ([ref("meter", METER)] if meter else [])
    return {"revision_id": MELODY["revision_id"], "payload": {"notes": items, **payload}, "refs": refs}


def form_claim(repeats) -> dict:
    return {"revision_id": FORM["revision_id"], "refs": [ref("work", WORK)],
            "payload": {"sections": [{"label": s} for s in ("Intro", "Verse", "Chorus", "Outro")],
                        "repeats": repeats}}


def meter_claim(form=True) -> dict:
    return {"revision_id": METER["revision_id"],
            "refs": [ref("work", WORK)] + ([ref("form", FORM)] if form else []),
            "payload": {"layers": [{"role": "written", "bars": [
                {"numerator": 4, "denominator": 4, "section": s} for s in SECTIONS]}]}}


def underlay(lines, *extra, **payload) -> dict:
    return {"payload": {"lines": lines, **payload},
            "refs": [ref("melody", MELODY), ref("document", DOCUMENT), *extra]}


LINES = [
    {"line": 1, "words": [
        {"word": 1, "syllables": [{"syllable": 1, "start_char": 0, "end_char": 3, "items": [1]},
                                  {"syllable": 2, "start_char": 3, "end_char": 5, "items": [2]}]},
        {"word": 3, "items": [4]}]},
    {"line": 2, "words": [{"word": 1, "items": [5]}]},
]
FIXED = [{"first": 1, "last": 2, "times": 2}]
OPEN = [{"first": 1, "last": 2}]


class Shape(unittest.TestCase):
    def test_accepted_with_everything_it_reads(self):
        payload = check_underlay(underlay(LINES, label="Verse 1"), melody_claim(), TEXT.encode())
        self.assertEqual(payload["label"], "Verse 1")

    def test_text_document_given_as_str(self):
        check_underlay(underlay(LINES), melody_claim(), TEXT)

    def test_roles(self):
        with self.assertRaisesRegex(NotesError, "'melody'"):
            check_underlay({"payload": {"lines": LINES}, "refs": [ref("document", DOCUMENT)]})
        with self.assertRaisesRegex(NotesError, "unknown role 'work'"):
            check_underlay(underlay(LINES, ref("work", WORK)))
        with self.assertRaises(NotesError):
            check_underlay(underlay(LINES, ref("corrects", EARLIER), ref("derived_from", EARLIER)))
        check_underlay(underlay(LINES, ref("run", RUN), ref("corrects", EARLIER), ref("derived_from", LYRICS)))

    def test_payload_is_closed(self):
        with self.assertRaisesRegex(NotesError, "unknown"):
            check_underlay(underlay(LINES, verse=1))
        with self.assertRaisesRegex(NotesError, "label"):
            check_underlay(underlay(LINES, label=""))

    def test_lines_and_words_ascend(self):
        backwards = copy.deepcopy(LINES)
        backwards[1]["line"] = 1
        with self.assertRaisesRegex(NotesError, "later line"):
            check_underlay(underlay(backwards))
        words = [{"line": 1, "words": [{"word": 2, "items": [0]}, {"word": 2, "items": [1]}]}]
        with self.assertRaisesRegex(NotesError, "later word"):
            check_underlay(underlay(words))

    def test_a_word_holds_items_or_syllables(self):
        both = [{"line": 1, "words": [{"word": 1, "items": [0], "syllables": []}]}]
        with self.assertRaisesRegex(NotesError, "exactly one of items or syllables"):
            check_underlay(underlay(both))
        neither = [{"line": 1, "words": [{"word": 1}]}]
        with self.assertRaisesRegex(NotesError, "exactly one of items or syllables"):
            check_underlay(underlay(neither))

    def test_syllables_follow_the_lyric_rule(self):
        bad = copy.deepcopy(LINES)
        bad[0]["words"][0]["syllables"][1]["start_char"] = 2
        with self.assertRaisesRegex(NotesError, "at or after the syllable before it in characters"):
            check_underlay(underlay(bad))
        bad = copy.deepcopy(LINES)
        bad[0]["words"][0]["syllables"][1]["syllable"] = 1
        with self.assertRaisesRegex(NotesError, "later syllable"):
            check_underlay(underlay(bad))

    def test_words_never_share_or_cross_notes(self):
        shared = copy.deepcopy(LINES)
        shared[0]["words"][1]["items"] = [2]
        with self.assertRaisesRegex(NotesError, "never share or cross"):
            check_underlay(underlay(shared))
        crossed = copy.deepcopy(LINES)
        crossed[1]["words"][0]["items"] = [3]
        with self.assertRaisesRegex(NotesError, "never share or cross"):
            check_underlay(underlay(crossed))
        melisma = [{"line": 1, "words": [{"word": 1, "items": [0, 1, 1]}]}]
        with self.assertRaisesRegex(NotesError, "never share or cross"):
            check_underlay(underlay(melisma))

    def test_items_are_bounded(self):
        many = [{"line": 1, "words": [{"word": 1, "items": list(range(65))}]}]
        with self.assertRaisesRegex(NotesError, "1 to 64"):
            check_underlay(underlay(many))


class Content(unittest.TestCase):
    def test_every_index_is_a_pitch_item(self):
        on_rest = [{"line": 1, "words": [{"word": 1, "items": [3]}]}]
        with self.assertRaisesRegex(NotesError, "item 3, which is no pitch item"):
            check_underlay(underlay(on_rest), melody_claim())
        past = [{"line": 1, "words": [{"word": 1, "items": [6]}]}]
        with self.assertRaisesRegex(NotesError, "item 6, which is no pitch item"):
            check_underlay(underlay(past), melody_claim())

    def test_melody_given_must_be_the_one_pinned(self):
        other = melody_claim()
        other["revision_id"] = EARLIER["revision_id"]
        with self.assertRaisesRegex(NotesError, "other than the one it pins"):
            check_underlay(underlay(LINES), other)

    def test_melody_contract_when_stated(self):
        claim = melody_claim()
        claim["contract"] = ["archive.claim/notes", "1"]
        with self.assertRaisesRegex(NotesError, "melody version 1 or .* version 2"):
            check_underlay(underlay(LINES), claim)

    def test_a_sketch_may_be_pinned(self):
        sketch = melody_claim([{"pitch": 60}] * 6, meter=False)
        check_underlay(underlay(LINES), sketch)

    def test_locators_fall_within_the_document(self):
        far = copy.deepcopy(LINES)
        far[1]["line"] = 3
        with self.assertRaisesRegex(NotesError, "names line 3, but the document has 2"):
            check_underlay(underlay(far), document_text=TEXT)
        long = copy.deepcopy(LINES)
        long[0]["words"][0]["syllables"][1]["end_char"] = 6
        with self.assertRaisesRegex(NotesError, "ends at character 6, but the word has 5"):
            check_underlay(underlay(long), document_text=TEXT)
        word = copy.deepcopy(LINES)
        word[1]["words"][0]["word"] = 4
        with self.assertRaisesRegex(NotesError, "names word 4 of line 2, which has 3"):
            check_underlay(underlay(word), document_text=TEXT)


class Passes(unittest.TestCase):
    VERSE = [{"line": 1, "words": [{"word": 1, "items": [1]}, {"word": 2, "items": [2]}]}]

    def claim(self, passes, lines=None):
        return underlay(lines or self.VERSE, ref("form", FORM), passes=passes)

    def test_accepted_on_a_fixed_repeat(self):
        check_underlay(self.claim({"repeat": 0, "passes": [2]}), melody_claim(), TEXT,
                       form_claim(FIXED), meter_claim())

    def test_form_exactly_with_passes(self):
        with self.assertRaisesRegex(NotesError, "form exactly when it names passes"):
            check_underlay(underlay(self.VERSE, passes={"repeat": 0, "passes": [1]}))
        with self.assertRaisesRegex(NotesError, "form exactly when it names passes"):
            check_underlay(underlay(self.VERSE, ref("form", FORM)))

    def test_pass_numbers_ascend_from_one(self):
        for numbers in ([], [0], [2, 1], [1, 1], ["last", 1], [True], ["first"]):
            with self.subTest(numbers=numbers), self.assertRaises(NotesError):
                check_underlay(self.claim({"repeat": 0, "passes": numbers}))
        check_underlay(self.claim({"repeat": 0, "passes": [1, 3, "last"]}))
        check_underlay(self.claim({"repeat": 0, "passes": ["last"]}))

    def test_fixed_repeat_passes_within_times(self):
        with self.assertRaisesRegex(NotesError, "past its repeat's times"):
            check_underlay(self.claim({"repeat": 0, "passes": [3]}), form_claim=form_claim(FIXED))
        with self.assertRaisesRegex(NotesError, "fixed repeat"):
            check_underlay(self.claim({"repeat": 0, "passes": [1, "last"]}), form_claim=form_claim(FIXED))

    def test_open_repeat_may_name_last(self):
        for numbers in ([1, "last"], ["last"], [7]):
            check_underlay(self.claim({"repeat": 0, "passes": numbers}), melody_claim(), TEXT,
                           form_claim(OPEN), meter_claim())

    def test_repeat_must_exist(self):
        with self.assertRaisesRegex(NotesError, "repeat its form does not have"):
            check_underlay(self.claim({"repeat": 1, "passes": [1]}), form_claim=form_claim(FIXED))

    def test_a_sketch_names_no_passes(self):
        sketch = melody_claim([{"pitch": 60}] * 6, meter=False)
        with self.assertRaisesRegex(NotesError, "sketch"):
            check_underlay(self.claim({"repeat": 0, "passes": [1]}), sketch)

    def test_melody_must_pin_a_meter(self):
        with self.assertRaisesRegex(NotesError, "pins no meter"):
            check_underlay(self.claim({"repeat": 0, "passes": [1]}), melody_claim(meter=False))

    def test_meter_must_pin_the_same_form(self):
        with self.assertRaisesRegex(NotesError, "does not pin the same form"):
            check_underlay(self.claim({"repeat": 0, "passes": [1]}), melody_claim(), None,
                           form_claim(FIXED), meter_claim(form=False))

    def test_form_version_2_needs_meter_layers_version_3(self):
        form, meter = form_claim(OPEN), meter_claim()
        form["contract"], meter["contract"] = list(FORM_V2), list(UNTIMED_METER)
        with self.assertRaisesRegex(NotesError, "meter layers version 3"):
            check_underlay(self.claim({"repeat": 0, "passes": [1]}), melody_claim(), None, form, meter)
        meter["contract"] = list(UNTIMED_METER_V3)
        check_underlay(self.claim({"repeat": 0, "passes": [1]}), melody_claim(), None, form, meter)

    def test_meter_needs_the_melody(self):
        with self.assertRaisesRegex(NotesError, "needs the melody claim"):
            check_underlay(self.claim({"repeat": 0, "passes": [1]}), meter_claim=meter_claim())

    def test_no_passes_reads_no_form(self):
        with self.assertRaisesRegex(NotesError, "reads no form"):
            check_underlay(underlay(self.VERSE), form_claim=form_claim(FIXED))

    def test_every_item_lies_inside_the_repeat(self):
        outside = [{"line": 1, "words": [{"word": 1, "items": [1]}, {"word": 2, "items": [5]}]}]
        with self.assertRaisesRegex(NotesError, "outside repeat 0"):
            check_underlay(self.claim({"repeat": 0, "passes": [1]}, outside), melody_claim(), None,
                           form_claim(FIXED), meter_claim())

    def test_the_repeat_is_the_innermost(self):
        nested = [{"first": 1, "last": 3, "times": 2}, {"first": 1, "last": 1, "times": 2}]
        with self.assertRaisesRegex(NotesError, "repeat 1 is the innermost"):
            check_underlay(self.claim({"repeat": 0, "passes": [1]}), melody_claim(), None,
                           form_claim(nested), meter_claim())
        check_underlay(self.claim({"repeat": 1, "passes": [1]}), melody_claim(), None,
                       form_claim(nested), meter_claim())
        chorus = [{"line": 1, "words": [{"word": 1, "items": [4]}]}]
        check_underlay(self.claim({"repeat": 0, "passes": [2]}, chorus), melody_claim(), None,
                       form_claim(nested), meter_claim())

    def test_bar_without_section_is_refused(self):
        meter = meter_claim()
        del meter["payload"]["layers"][0]["bars"][1]["section"]
        with self.assertRaisesRegex(NotesError, "names no form section"):
            check_underlay(self.claim({"repeat": 0, "passes": [1]}), melody_claim(), None,
                           form_claim(FIXED), meter)

    def test_start_shifts_the_bars(self):
        # Starting in bar 1 puts item 0 in the verse and item 4 in the outro.
        early = [{"line": 1, "words": [{"word": 1, "items": [0]}, {"word": 2, "items": [1]}]}]
        check_underlay(self.claim({"repeat": 0, "passes": [1]}, early),
                       melody_claim(ITEMS[:5], start={"layer": 0, "bar": 1}), None, form_claim(FIXED), meter_claim())


if __name__ == "__main__":
    unittest.main()
