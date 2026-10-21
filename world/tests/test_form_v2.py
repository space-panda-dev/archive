"""Form version 2, form spans version 2, and meter layers version 3 (ADR
0113's amendment of ADR 0083): open repeats, passes, departures, and the
played order of an open repeat.

Run from the repository root: python3 -m unittest world.tests.test_form_v2.
The module needs only the standard library. Every example is synthetic
(INV-23).
"""

import copy
import unittest

from world.form import (
    FORM_SPANS_V2,
    FORM_V2,
    METER_LAYERS_V3,
    bar_order,
    check_form,
    check_form_spans,
    check_form_spans_v2,
    check_form_v2,
    check_meter_layers_v2,
    check_meter_layers_v3,
    departures,
    enclosing,
    form_order,
    precision,
)
from world.harmony import HarmonyError
from world.tests.test_form import (
    FORM_REF,
    METER_REF,
    OTHER,
    form_claim,
    section,
    sequence_claim,
    sig,
    song,
    spans_claim,
    timed,
    untimed_meter,
)
from world.tests.test_harmony import RATE, SECOND, SEQUENCE, bar, fixture_stream, refs

# The ADR's chart: head, open solos on the form, head out with a last-time
# ending, and a coda.
JAZZ = [
    section("Intro", kind="intro"),               # 0
    section("A", kind="head"),                    # 1
    section("B", kind="head"),                    # 2
    section("Solos on the form", kind="solo"),    # 3  default ending
    section("Last time"),                         # 4  last-time ending
    section("Coda", kind="coda"),                 # 5
]
JAZZ_REPEATS = [{"first": 1, "last": 4, "instruction": "open for solos, head out",
                 "endings": [{"first": 3, "last": 3}, {"first": 4, "last": 4, "passes": ["last"]}]}]


def jazz(**payload) -> dict:
    return form_claim(copy.deepcopy(JAZZ), repeats=copy.deepcopy(JAZZ_REPEATS), **payload)


# An open repeat whose first pass takes a "1." ending and later passes the
# default, before the last.
FIRST = [section("Intro"), section("A"), section("1."), section("Solos"), section("Last"), section("Out")]
FIRST_REPEATS = [{"first": 1, "last": 4, "endings": [{"first": 2, "last": 2, "passes": [1]},
                                                     {"first": 3, "last": 3},
                                                     {"first": 4, "last": 4, "passes": ["last"]}]}]


def first_ending(**payload) -> dict:
    return form_claim(copy.deepcopy(FIRST), repeats=copy.deepcopy(FIRST_REPEATS), **payload)


def vamp() -> dict:
    return form_claim([section("Intro"), section("Vamp", kind="vamp"), section("Out")],
                      repeats=[{"first": 1, "last": 1, "instruction": "vamp until cue"}])


class FormV2Test(unittest.TestCase):
    def test_contract_names(self):
        self.assertEqual(FORM_V2, ("archive.claim/form", "2"))
        self.assertEqual(FORM_SPANS_V2, ("archive.claim/form_spans", "2"))
        self.assertEqual(METER_LAYERS_V3, ("archive.claim/meter_layers", "3"))

    def test_accepts_open_repeats(self):
        for claim in (jazz(), first_ending(), vamp()):
            check_form_v2(claim)

    def test_a_v1_form_is_a_v2_form(self):
        self.assertEqual(check_form_v2(song()), check_form(song()))

    def test_v1_refuses_what_only_v2_admits(self):
        with_instruction = song()
        with_instruction["payload"]["repeats"][0]["instruction"] = "2x"
        check_form_v2(with_instruction)
        for claim in (jazz(), vamp(), with_instruction):
            with self.assertRaises(HarmonyError):
                check_form(claim)

    def test_an_open_repeat_is_listed_as_open_pass_then_last(self):
        payload = jazz()["payload"]
        self.assertEqual(form_order(payload), [0, 1, 2, 3, 1, 2, 4, 5])
        self.assertEqual(form_order(payload, marked=True),
                         [(0, False), (1, True), (2, True), (3, True), (1, False), (2, False), (4, False), (5, False)])

    def test_numbered_passes_come_before_the_open_pass(self):
        self.assertEqual(form_order(first_ending()["payload"], marked=True),
                         [(0, False), (1, False), (2, False), (1, True), (3, True), (1, False), (4, False), (5, False)])

    def test_a_gap_in_the_numbers_takes_the_default(self):
        claim = first_ending()
        claim["payload"]["repeats"][0]["endings"][0]["passes"] = [2]
        check_form_v2(claim)
        # Pass 1 takes the default, pass 2 the "2." ending, then open and last.
        self.assertEqual(form_order(claim["payload"]), [0, 1, 3, 1, 2, 1, 3, 1, 4, 5])

    def test_an_open_vamp_is_one_open_pass(self):
        self.assertEqual(form_order(vamp()["payload"], marked=True), [(0, False), (1, True), (2, False)])

    def test_a_return_takes_an_open_repeat_once_with_its_last_time_ending(self):
        claim = first_ending(jumps=[{"after": 5, "kind": "da_capo", "then": "fine", "fine": 4}])
        check_form_v2(claim)
        self.assertEqual(form_order(claim["payload"]), [0, 1, 2, 1, 3, 1, 4, 5, 0, 1, 4])
        claim["payload"]["jumps"][0]["repeats_on_return"] = True
        self.assertEqual(form_order(claim["payload"], marked=True)[8:],
                         [(0, False), (1, False), (2, False), (1, True), (3, True), (1, False), (4, False)])

    def test_a_return_plays_an_open_vamp_once(self):
        claim = vamp()
        claim["payload"]["jumps"] = [{"after": 2, "kind": "da_capo", "then": "end"}]
        check_form_v2(claim)
        self.assertEqual(form_order(claim["payload"], marked=True),
                         [(0, False), (1, True), (2, False), (0, False), (1, False), (2, False)])

    def test_a_fixed_repeat_inside_an_open_pass_is_marked_open(self):
        claim = form_claim([section("A"), section("B"), section("C")],
                           repeats=[{"first": 0, "last": 2}, {"first": 1, "last": 1, "times": 2}])
        check_form_v2(claim)
        self.assertEqual(form_order(claim["payload"], marked=True), [(0, True), (1, True), (1, True), (2, True)])

    def test_bar_order_marks_bars_of_an_open_pass(self):
        layer = {"bars": [sig(section=s) for s in range(6)]}
        payload = jazz()["payload"]
        self.assertEqual(bar_order(layer, payload), form_order(payload))
        self.assertEqual(bar_order(layer, payload, marked=True), form_order(payload, marked=True))
        self.assertEqual(bar_order(layer, marked=True), [(i, False) for i in range(6)])

    def test_refuses_malformed_open_repeats(self):
        def with_endings(endings):
            claim = jazz()
            claim["payload"]["repeats"][0]["endings"] = endings
            return claim
        default, last_time = {"first": 3, "last": 3}, {"first": 4, "last": 4, "passes": ["last"]}
        cases = {
            "two defaults": with_endings([default, {"first": 4, "last": 4}]),
            "no last-time ending": with_endings([default, {"first": 4, "last": 4, "passes": [1]}]),
            "no default": with_endings([{"first": 3, "last": 3, "passes": [1]}, last_time]),
            "last with a number": with_endings([default, {"first": 4, "last": 4, "passes": [1, "last"]}]),
            "two last-time endings": with_endings([{"first": 3, "last": 3, "passes": ["last"]}, last_time]),
            "a pass named twice": form_claim([section(str(i)) for i in range(6)], repeats=[
                {"first": 0, "last": 4, "endings": [{"first": 1, "last": 1, "passes": [1]},
                                                    {"first": 2, "last": 2, "passes": [1, 2]},
                                                    {"first": 3, "last": 3},
                                                    {"first": 4, "last": 4, "passes": ["last"]}]}]),
            "an empty body": with_endings([{"first": 1, "last": 3}, last_time]),
            "endings not tiled": with_endings([{"first": 2, "last": 2}, last_time]),
            "empty instruction": jazz(),
            "long instruction": jazz(),
            "times of one": jazz(),
        }
        cases["empty instruction"]["payload"]["repeats"][0]["instruction"] = ""
        cases["long instruction"]["payload"]["repeats"][0]["instruction"] = "x" * 201
        cases["times of one"]["payload"]["repeats"][0]["times"] = 1
        for name, claim in cases.items():
            with self.subTest(name), self.assertRaises(HarmonyError):
                check_form_v2(claim)

    def test_a_fixed_repeat_keeps_version_1_rules(self):
        last_time, no_passes = song(), song()
        last_time["payload"]["repeats"][0]["endings"][1]["passes"] = ["last"]
        del no_passes["payload"]["repeats"][0]["endings"][1]["passes"]
        for claim in (last_time, no_passes):
            with self.assertRaises(HarmonyError):
                check_form_v2(claim)


def played(*entries: tuple) -> list[dict]:
    """Spans one second each from the start: (section, passes) or a label."""
    spans = []
    for at, entry in enumerate(entries):
        fields = {"label": entry} if isinstance(entry, str) else {"section": entry[0], **(
            {"passes": entry[1]} if entry[1] is not None else {})}
        spans.append(timed(at, at + 1, **fields))
    return spans


MANUAL = {"method": "manual"}


class FormSpansV2Test(unittest.TestCase):
    def three_choruses(self) -> list[dict]:
        return played((0, None), (1, [1]), (2, [1]), (3, [1]), (1, [2]), (2, [2]), (3, [2]),
                      "Extra chorus", (1, [3]), (2, [3]), (4, [3]), (5, None))

    def test_spans_name_the_passes_they_play(self):
        claim = spans_claim(self.three_choruses(), ("form", FORM_REF), alignment=MANUAL)
        self.assertEqual(len(check_form_spans_v2(claim, fixture_stream(), jazz())), 12)
        self.assertEqual(departures(claim), [7])

    def test_any_positive_pass_of_an_open_repeat(self):
        spans = played((1, [41]), (2, [41]), (3, [41]))
        check_form_spans_v2(spans_claim(spans, ("form", FORM_REF), alignment=MANUAL), fixture_stream(), jazz())

    def test_a_last_pass_may_be_the_first(self):
        spans = played((1, [1]), (4, [1]))
        check_form_spans_v2(spans_claim(spans, ("form", FORM_REF), alignment=MANUAL),
                            fixture_stream(), first_ending())

    def test_a_return_span(self):
        spans = played((0, None), (1, [1]))
        spans[1]["return"] = True
        check_form_spans_v2(spans_claim(spans, ("form", FORM_REF), alignment=MANUAL), fixture_stream(), jazz())

    def test_passes_shape_without_the_form(self):
        claim = spans_claim(played((1, [7, 2])), ("form", FORM_REF), alignment=MANUAL)
        check_form_spans_v2(claim, fixture_stream())

    def test_a_v1_form_and_v1_spans_are_accepted(self):
        claim = spans_claim(played((0, None), (1, [1]), (2, [1]), (3, [1])), ("form", FORM_REF), alignment=MANUAL)
        check_form_spans_v2(claim, fixture_stream(), song())
        check_form_spans_v2(spans_claim([timed(0, 8, label="A")], ("corrects", OTHER)), fixture_stream())

    def test_v1_refuses_what_only_v2_admits(self):
        stream = fixture_stream()
        cases = {
            "passes": (spans_claim(played((1, [1])), ("form", FORM_REF), alignment=MANUAL), song()),
            "precision": (spans_claim(played((0, None)), ("form", FORM_REF),
                                      alignment={"method": "manual", "precision": "exact"}), song()),
            "an open form": (spans_claim(played((0, None)), ("form", FORM_REF), alignment=MANUAL), jazz()),
        }
        for name, (claim, form) in cases.items():
            with self.subTest(name), self.assertRaises(HarmonyError):
                check_form_spans(claim, stream, form)

    def test_the_precision_as_read(self):
        def payload(**alignment):
            return {"alignment": alignment}
        self.assertEqual(precision(payload(method="manual")), "approximate")
        self.assertEqual(precision(payload(method="segmenter")), "rough")
        self.assertEqual(precision(payload(method="segmenter", precision="exact")), "exact")
        self.assertEqual(precision(payload(method="manual", precision="rough")), "rough")
        self.assertIsNone(precision({}))

    def test_departures_need_a_pinned_form(self):
        self.assertEqual(departures(spans_claim(played("A", "B"))), [])

    def test_enclosing_is_outer_first(self):
        repeats = [{"first": 0, "last": 3, "times": 2}, {"first": 1, "last": 2, "times": 2}]
        self.assertEqual(enclosing(repeats, 2), [0, 1])
        self.assertEqual(enclosing(repeats, 3), [0])

    def test_refuses_malformed_passes(self):
        stream = fixture_stream()

        def claim(*entries, **alignment):
            return spans_claim(played(*entries), ("form", FORM_REF), alignment=alignment or MANUAL)
        returned = claim((0, None))
        returned["payload"]["spans"][0]["return"] = "yes"
        labelled = claim((0, None))
        labelled["payload"]["spans"] = [timed(0, 1, label="Jam", passes=[1])]
        cases = {
            "no passes inside a repeat": (claim((1, None)), jazz()),
            "passes outside repeats": (claim((0, [1])), jazz()),
            "too many passes": (claim((1, [1, 1])), jazz()),
            "pass zero": (claim((1, [0])), jazz()),
            "past a fixed repeat's times": (claim((1, [3])), song()),
            "a fixed ending its pass does not take": (claim((4, [1])), song()),
            "the default on a numbered pass": (claim((3, [1])), first_ending()),
            "a numbered ending on another pass": (claim((2, [2])), first_ending()),
            "passes without a section": (labelled, jazz()),
            "return not a boolean": (returned, jazz()),
            "unknown precision": (claim((0, None), method="manual", precision="close"), jazz()),
            "a pass of zero without the form": (claim((1, [0])), None),
        }
        for name, (spans, form) in cases.items():
            with self.subTest(name), self.assertRaises(HarmonyError):
                check_form_spans_v2(spans, stream, form)


class MeterV3Test(unittest.TestCase):
    def jazz_meter(self) -> dict:
        bars = [sig(section=0), sig(section=1, chords=[{"entry": 0}]), sig(section=2, chords=[{"entry": 1}]),
                sig(section=3, chords=[{"entry": 2}]), sig(section=4, chords=[{"entry": 3}]), sig(section=5)]
        return untimed_meter(bars, ("form", FORM_REF), ("sequence", SEQUENCE))

    def test_an_untimed_meter_in_an_open_form(self):
        claim = self.jazz_meter()
        check_meter_layers_v3(claim, form_claim=jazz(), sequence_claim=sequence_claim(4))
        with self.assertRaises(HarmonyError):
            check_meter_layers_v2(claim, form_claim=jazz(), sequence_claim=sequence_claim(4))

    def test_a_version_2_claim_is_a_version_3_claim(self):
        claim = untimed_meter([sig(section=s) for s in range(7)], ("form", FORM_REF))
        check_meter_layers_v2(claim, form_claim=song())
        check_meter_layers_v3(claim, form_claim=song())

    def test_a_timed_placement_of_an_open_form(self):
        untimed = self.jazz_meter()
        layer = untimed["payload"]["layers"][0]
        bars = [bar(i * SECOND, (i + 1) * SECOND, 4, 4, placed={"layer": 0, "bar": b})
                for i, b in enumerate(bar_order(layer, jazz()["payload"]))]
        claim = {"payload": {"sample_rate": RATE, "layers": [{"bars": bars}], "alignment": MANUAL},
                 "refs": refs(("meter", METER_REF))}
        check_meter_layers_v3(claim, fixture_stream(), untimed_claim=untimed)

    def test_refuses_what_version_2_refuses(self):
        claim = self.jazz_meter()
        claim["payload"]["layers"][0]["bars"][2]["section"] = 0
        with self.assertRaises(HarmonyError):
            check_meter_layers_v3(claim, form_claim=jazz())
        with self.assertRaises(HarmonyError):
            check_meter_layers_v3(claim)  # bars going back need no form to refuse
        with self.assertRaises(HarmonyError):
            check_meter_layers_v3({**claim, "refs": refs()})


if __name__ == "__main__":
    unittest.main()
