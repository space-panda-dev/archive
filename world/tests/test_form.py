"""Form, form spans, and meter layers version 2 (ADR 0083): the payload
rules and the played-order views.

Run from the repository root: python3 -m unittest world.tests.test_form.
The module needs only the standard library. Every example is synthetic
(INV-23); the stream claim is the fixture archive's own, as in
test_harmony.
"""

import copy
import unittest

from world.form import (
    FORM,
    FORM_SPANS,
    METER_LAYERS_V2,
    FormError,
    SECTION_KINDS,
    bar_order,
    check_form,
    check_form_spans,
    check_meter_layers_v2,
    form_order,
    is_untimed_meter,
)
from world.harmony import HarmonyError, check_chord_sequence, check_meter_layers
from world.tests.test_harmony import (
    DOCUMENT,
    RATE,
    RUN,
    SECOND,
    SEQUENCE,
    WORK,
    bar,
    fixture_stream,
    meter_claim,
    refs,
)

FORM_REF = {"id": "urn:uuid:00000000-0000-4000-8000-0000000000c1", "revision_id": "sha256:" + "c1" * 32}
METER_REF = {"id": "urn:uuid:00000000-0000-4000-8000-0000000000c2", "revision_id": "sha256:" + "c2" * 32}
OTHER = {"id": "urn:uuid:00000000-0000-4000-8000-0000000000c3", "revision_id": "sha256:" + "c3" * 32}


def pins(*pairs: tuple[str, dict]) -> list[dict]:
    return [{"role": role, **value} for role, value in pairs]


def section(label: str, **fields) -> dict:
    return {"label": label, **fields}


def form_claim(sections: list[dict], *extra: tuple[str, dict], **payload) -> dict:
    return {"revision_id": FORM_REF["revision_id"], "payload": {"sections": sections, **payload},
            "refs": pins(("work", WORK), *extra)}


# A generic song: intro, verse, chorus, repeated with two endings, a bridge,
# then D.S. al Coda back to the verse, and a coda.
SONG = [
    section("Intro", kind="intro"),      # 0
    section("A", kind="verse"),          # 1  segno
    section("B", kind="chorus"),         # 2
    section("1.", kind="tag"),           # 3  first ending
    section("2."),                       # 4  second ending; To Coda after it
    section("Bridge", kind="bridge"),    # 5  D.S. al Coda after it
    section("Coda", kind="coda"),        # 6
]
SONG_REPEATS = [{"first": 1, "last": 4, "times": 2,
                 "endings": [{"first": 3, "last": 3, "passes": [1]}, {"first": 4, "last": 4, "passes": [2]}]}]
SONG_JUMPS = [{"after": 5, "kind": "dal_segno", "to": 1, "then": "coda", "coda_from": 4, "coda": 6}]


def song() -> dict:
    return form_claim(copy.deepcopy(SONG), repeats=copy.deepcopy(SONG_REPEATS), jumps=copy.deepcopy(SONG_JUMPS))


def sequence_claim(count: int) -> dict:
    entries = [{"readings": [{"bass": i % 12, "tones": sorted({(i + 4) % 12, (i + 7) % 12})}]} for i in range(count)]
    return {"revision_id": SEQUENCE["revision_id"], "payload": {"entries": entries}, "refs": pins(("work", WORK))}


def untimed_meter(bars: list[dict], *extra: tuple[str, dict], **payload) -> dict:
    return {"revision_id": METER_REF["revision_id"], "payload": {"layers": [{"bars": bars}], **payload},
            "refs": pins(("work", WORK), *extra)}


def sig(numerator: int = 4, denominator: int = 4, **fields) -> dict:
    return {"numerator": numerator, "denominator": denominator, **fields}


class FormTest(unittest.TestCase):
    def test_contract_names(self):
        self.assertEqual(FORM, ("archive.claim/form", "1"))
        self.assertEqual(FORM_SPANS, ("archive.claim/form_spans", "1"))
        self.assertEqual(METER_LAYERS_V2, ("archive.claim/meter_layers", "2"))
        self.assertTrue(issubclass(FormError, HarmonyError))

    def test_accepts_a_form_with_endings_and_a_coda(self):
        self.assertEqual(len(check_form(song())), 7)

    def test_played_order_takes_each_ending_then_the_jump_and_the_coda(self):
        payload = song()["payload"]
        check_form(song())
        # First time: intro, A B 1., A B 2., bridge; D.S. to A: A B 2. (the
        # repeat once, with its last ending), To Coda after 2., coda.
        self.assertEqual(form_order(payload), [0, 1, 2, 3, 1, 2, 4, 5, 1, 2, 4, 6])

    def test_repeats_on_return_are_taken_when_the_jump_says_so(self):
        claim = song()
        claim["payload"]["jumps"][0]["repeats_on_return"] = True
        check_form(claim)
        self.assertEqual(form_order(claim["payload"]), [0, 1, 2, 3, 1, 2, 4, 5, 1, 2, 3, 1, 2, 4, 6])

    def test_da_capo_al_fine(self):
        claim = form_claim([section("A"), section("B"), section("C")],
                           jumps=[{"after": 2, "kind": "da_capo", "then": "fine", "fine": 0}])
        check_form(claim)
        self.assertEqual(form_order(claim["payload"]), [0, 1, 2, 0])

    def test_da_capo_to_the_end_takes_the_jump_once(self):
        claim = form_claim([section("A"), section("B")], jumps=[{"after": 1, "kind": "da_capo", "then": "end"}])
        check_form(claim)
        self.assertEqual(form_order(claim["payload"]), [0, 1, 0, 1])

    def test_a_jump_inside_a_repeat_waits_for_its_last_pass(self):
        claim = form_claim([section("A"), section("B"), section("C")],
                           repeats=[{"first": 0, "last": 1, "times": 3}],
                           jumps=[{"after": 1, "kind": "da_capo", "then": "fine", "fine": 1}])
        check_form(claim)
        self.assertEqual(form_order(claim["payload"]), [0, 1, 0, 1, 0, 1, 0, 1])

    def test_nested_repeats_unroll_like_a_chord_sequence(self):
        claim = form_claim([section("A"), section("B"), section("C")],
                           repeats=[{"first": 0, "last": 2, "times": 2}, {"first": 1, "last": 1, "times": 3}])
        check_form(claim)
        self.assertEqual(form_order(claim["payload"]), [0, 1, 1, 1, 2] * 2)

    def test_an_ending_may_serve_several_passes(self):
        claim = form_claim([section("A"), section("1.-3."), section("4.")],
                           repeats=[{"first": 0, "last": 2, "times": 4, "endings": [
                               {"first": 1, "last": 1, "passes": [1, 2, 3]}, {"first": 2, "last": 2, "passes": [4]}]}])
        check_form(claim)
        self.assertEqual(form_order(claim["payload"]), [0, 1, 0, 1, 0, 1, 0, 2])

    def test_a_document_subject_with_located_sections(self):
        claim = {"payload": {"sections": [section("Verse", locator={"line": 1}), section("Chorus", locator={"line": 9})],
                             "document_url": "https://example.org/chart"},
                 "refs": pins(("document", DOCUMENT), ("run", RUN))}
        check_form(claim)

    def test_every_vocabulary_kind_is_accepted(self):
        check_form(form_claim([section(kind, kind=kind) for kind in SECTION_KINDS]))

    def test_a_correction_cites_a_form(self):
        check_form(form_claim([section("A")], ("corrects", OTHER)))

    def test_refuses_malformed_forms(self):
        def changed(edit):
            claim = song()
            edit(claim["payload"], claim)
            return claim

        cases = {
            "no subject": changed(lambda p, c: c.update(refs=[])),
            "unknown role": changed(lambda p, c: c["refs"].append({"role": "source", **OTHER})),
            "unknown field": changed(lambda p, c: p.update(tempo=120)),
            "an alignment": changed(lambda p, c: p.update(alignment={"method": "manual"})),
            "no sections": changed(lambda p, c: p.update(sections=[])),
            "no label": changed(lambda p, c: p["sections"][0].pop("label")),
            "long label": changed(lambda p, c: p["sections"][0].update(label="x" * 101)),
            "unknown kind": changed(lambda p, c: p["sections"][0].update(kind="chorus 2")),
            "locator without document": changed(lambda p, c: p["sections"][0].update(locator={"line": 1})),
            "url without document": changed(lambda p, c: p.update(document_url="https://example.org/")),
            "repeat of one": changed(lambda p, c: p["repeats"][0].update(times=1)),
            "repeat past end": changed(lambda p, c: p["repeats"][0].update(last=7)),
            "repeats crossing": changed(lambda p, c: p.update(repeats=[{"first": 0, "last": 2, "times": 2},
                                                                   {"first": 1, "last": 3, "times": 2}], jumps=[])),
            "repeats out of order": changed(lambda p, c: p.update(repeats=[{"first": 2, "last": 2, "times": 2},
                                                                       {"first": 0, "last": 0, "times": 2}], jumps=[])),
            "one ending": changed(lambda p, c: p["repeats"][0].update(endings=p["repeats"][0]["endings"][:1])),
            "ending gap": changed(lambda p, c: p["repeats"][0]["endings"][0].update(first=2, last=2)),
            "empty body": changed(lambda p, c: p["repeats"][0]["endings"][0].update(first=1)),
            "ending short of last": changed(lambda p, c: p["repeats"][0].update(last=5) or p.update(jumps=[])),
            "pass missing": changed(lambda p, c: p["repeats"][0]["endings"][1].update(passes=[3])),
            "pass twice": changed(lambda p, c: p["repeats"][0]["endings"][1].update(passes=[1, 2])),
            "repeat across body and ending": changed(lambda p, c: p["repeats"].append(
                {"first": 2, "last": 3, "times": 2})),
            "unknown jump kind": changed(lambda p, c: p["jumps"][0].update(kind="fine")),
            "segno missing": changed(lambda p, c: p["jumps"][0].pop("to")),
            "segno after the sign": changed(lambda p, c: p["jumps"][0].update(to=6)),
            "coda before the sign": changed(lambda p, c: p["jumps"][0].update(coda=5)),
            "coda field on fine": changed(lambda p, c: p["jumps"][0].update(then="fine", fine=2)),
            "lands inside a repeat": changed(lambda p, c: p["jumps"][0].update(to=2)),
            "jumps repeat a sign": changed(lambda p, c: p["jumps"].append(dict(p["jumps"][0]))),
            "flag not boolean": changed(lambda p, c: p["jumps"][0].update(repeats_on_return="yes")),
            "two refs of a role": changed(lambda p, c: c["refs"].append({"role": "work", **OTHER})),
        }
        for name, claim in cases.items():
            with self.subTest(name), self.assertRaises(HarmonyError):
                check_form(claim)

    def test_located_sections_never_go_back(self):
        claim = {"payload": {"sections": [section("A", locator={"line": 9}), section("B", locator={"line": 1})]},
                 "refs": pins(("document", DOCUMENT))}
        with self.assertRaises(FormError):
            check_form(claim)
        claim["payload"]["sections"][1]["locator"] = {"bar": 12}
        with self.assertRaises(FormError):
            check_form(claim)


def spans_claim(spans: list[dict], *extra: tuple[str, dict], **payload) -> dict:
    return {"payload": {"sample_rate": RATE, "spans": spans, **payload}, "refs": refs(*extra)}


def timed(start: float, end: float, **fields) -> dict:
    return {"start_sample": int(start * SECOND), "end_sample": int(end * SECOND), **fields}


class FormSpansTest(unittest.TestCase):
    def test_a_machine_segmentation_with_gaps(self):
        claim = spans_claim([timed(0, 8, label="A"), timed(10, 20, label="B", kind="chorus")], ("run", RUN))
        self.assertEqual(len(check_form_spans(claim, fixture_stream())), 2)

    def test_a_placed_form_cites_sections_and_states_its_alignment(self):
        played = form_order(song()["payload"])
        spans = [timed(i * 4, i * 4 + 4, section=s) for i, s in enumerate(played)]
        claim = spans_claim(spans, ("form", FORM_REF), alignment={"method": "manual"})
        check_form_spans(claim, fixture_stream(), song())

    def test_a_correction_cites_form_spans(self):
        check_form_spans(spans_claim([timed(0, 1, label="Intro")], ("corrects", OTHER)), fixture_stream())

    def test_refuses_malformed_form_spans(self):
        stream = fixture_stream()
        placed = [("form", FORM_REF)]
        cases = {
            "no label or section": (spans_claim([timed(0, 1)]), None),
            "section without form": (spans_claim([timed(0, 1, section=0)]), None),
            "placed without alignment": (spans_claim([timed(0, 1, section=0)], *placed), None),
            "alignment without placing": (spans_claim([timed(0, 1, label="A")], alignment={"method": "manual"}), None),
            "section out of the form": (spans_claim([timed(0, 1, section=7)], *placed,
                                                    alignment={"method": "manual"}), song()),
            "another form given": (spans_claim([timed(0, 1, section=0)], *placed, alignment={"method": "manual"}),
                                   {**song(), "revision_id": OTHER["revision_id"]}),
            "overlap": (spans_claim([timed(0, 2, label="A"), timed(1, 3, label="B")]), None),
            "past the stream": (spans_claim([timed(59, 61, label="A")]), None),
            "rate differs": (spans_claim([timed(0, 1, label="A")], sample_rate=RATE * 2), None),
            "unknown kind": (spans_claim([timed(0, 1, label="A", kind="drop")]), None),
            "locator without document": (spans_claim([timed(0, 1, label="A", locator={"bar": 1})]), None),
            "two locator keys": (spans_claim([timed(0, 1, label="A", locator={"bar": 1, "line": 2})],
                                             ("document", DOCUMENT), alignment={"method": "manual"}), None),
            "no stream": ({"payload": {"sample_rate": RATE, "spans": [timed(0, 1, label="A")]},
                           "refs": pins(("source", OTHER))}, None),
        }
        for name, (claim, form) in cases.items():
            with self.subTest(name), self.assertRaises(HarmonyError):
                check_form_spans(claim, stream, form)


class MeterV2Test(unittest.TestCase):
    def lead_sheet_meter(self) -> dict:
        """Bars of the generic song: one per section here, chords in them."""
        bars = [sig(section=0, chords=[{"entry": 0}]),
                sig(section=1, chords=[{"entry": 1}, {"entry": 2, "at": {"count": 3, "unit": 8}}]),
                sig(section=2, chords=[{"entry": 3}]),
                sig(section=3), sig(section=4, chords=[{"entry": 4}]),
                sig(7, 8, grouping=[2, 2, 3], section=5), sig(section=6, chords=[{"entry": 5}])]
        return untimed_meter(bars, ("form", FORM_REF), ("sequence", SEQUENCE))

    def test_an_untimed_meter_on_a_work_ties_bars_to_sections_and_entries(self):
        claim = self.lead_sheet_meter()
        self.assertTrue(is_untimed_meter(claim))
        check_meter_layers_v2(claim, form_claim=song(), sequence_claim=sequence_claim(6))

    def test_bar_order_follows_the_form(self):
        claim = self.lead_sheet_meter()
        layer = check_meter_layers_v2(claim)[0]
        self.assertEqual(bar_order(layer, song()["payload"]), form_order(song()["payload"]))
        self.assertEqual(bar_order(layer), list(range(7)))

    def test_bar_order_refuses_a_bar_with_no_section(self):
        layer = {"bars": [sig(section=0), sig()]}
        with self.assertRaises(FormError):
            bar_order(layer, song()["payload"])

    def test_untimed_modulation_between_adjacent_bars(self):
        claim = untimed_meter([sig(), sig(12, 8, grouping=[3, 3, 3, 3])], modulations=[
            {"layer": 0, "bar": 1, "kind": "regrouping",
             "pulse_before": {"denominator": 4}, "pulse_after": {"denominator": 4, "dots": 1}}])
        check_meter_layers_v2(claim)

    def test_untimed_felt_layer_beside_written(self):
        claim = untimed_meter([sig()])
        claim["payload"]["layers"] = [{"bars": [sig()], "role": "written"},
                                      {"bars": [sig(12, 8, grouping=[3, 3, 3, 3])], "role": "felt"}]
        check_meter_layers_v2(claim)

    def test_a_timed_placement_cites_the_untimed_claim(self):
        untimed = self.lead_sheet_meter()
        bars = [bar(i * SECOND, (i + 1) * SECOND, 4, 4, placed={"layer": 0, "bar": b})
                for i, b in enumerate(bar_order(untimed["payload"]["layers"][0], song()["payload"]))
                if b != 5]
        claim = {"payload": {"sample_rate": RATE, "layers": [{"bars": bars}], "alignment": {"method": "manual"}},
                 "refs": refs(("meter", METER_REF))}
        self.assertFalse(is_untimed_meter(claim))
        check_meter_layers_v2(claim, fixture_stream(), untimed_claim=untimed)

    def test_a_v1_timed_payload_is_a_v2_timed_payload(self):
        claim = meter_claim([{"bars": [bar(0, 2 * SECOND, 4, 4), bar(2 * SECOND, 4 * SECOND, 3, 4)]}])
        check_meter_layers(claim, fixture_stream())
        check_meter_layers_v2(claim, fixture_stream())

    def test_v1_still_refuses_what_only_v2_admits(self):
        untimed = self.lead_sheet_meter()
        with self.assertRaises(HarmonyError):
            check_meter_layers(untimed, fixture_stream())
        placed = {"payload": {"sample_rate": RATE, "alignment": {"method": "manual"},
                              "layers": [{"bars": [bar(0, SECOND, 4, 4, placed={"layer": 0, "bar": 0})]}]},
                  "refs": refs(("meter", METER_REF))}
        check_meter_layers_v2(placed, fixture_stream())
        with self.assertRaises(HarmonyError):
            check_meter_layers(placed, fixture_stream())

    def test_v1_sequences_and_meters_still_check(self):
        check_chord_sequence(sequence_claim(3))
        check_meter_layers(meter_claim([{"beats": [0, SECOND, 2 * SECOND]}]), fixture_stream())

    def test_a_chord_on_a_triplet_is_writable(self):
        """The offset is ADR 0086's written length, which allows a unit of 12."""
        tied = (("form", FORM_REF), ("sequence", SEQUENCE))
        claim = untimed_meter([sig(chords=[{"entry": 0}, {"entry": 1, "at": {"count": 2, "unit": 12}}])], *tied)
        check_meter_layers_v2(claim, form_claim=song(), sequence_claim=sequence_claim(6))

    def test_refuses_malformed_untimed_meter(self):
        form, seq = song(), sequence_claim(6)
        tied = (("form", FORM_REF), ("sequence", SEQUENCE))
        cases = {
            "no subject": {"payload": {"layers": [{"bars": [sig()]}]}, "refs": []},
            "samples on an untimed bar": untimed_meter([{**sig(), "start_sample": 0, "end_sample": 1}]),
            "a beat layer": {"payload": {"layers": [{"beats": [0, 1]}]}, "refs": pins(("work", WORK))},
            "a sample rate": untimed_meter([sig()], sample_rate=RATE),
            "an alignment": untimed_meter([sig()], alignment={"method": "manual"}),
            "bad denominator": untimed_meter([sig(4, 3)]),
            "grouping sum": untimed_meter([sig(7, 8, grouping=[2, 2])]),
            "section without form": untimed_meter([sig(section=0)]),
            "chords without sequence": untimed_meter([sig(chords=[{"entry": 0}])]),
            "section out of the form": untimed_meter([sig(section=9)], *tied),
            "sections go back": untimed_meter([sig(section=2), sig(section=1)], *tied),
            "entry out of the sequence": untimed_meter([sig(chords=[{"entry": 6}])], *tied),
            "entries go back": untimed_meter([sig(chords=[{"entry": 2}]), sig(chords=[{"entry": 1}])], *tied),
            "chord past the bar": untimed_meter([sig(3, 4, chords=[{"entry": 0, "at": {"count": 3, "unit": 4}}])],
                                                *tied),
            "chords not ascending": untimed_meter([sig(chords=[{"entry": 0, "at": {"count": 1, "unit": 4}},
                                                              {"entry": 1, "at": {"count": 1, "unit": 4}}])], *tied),
            "bad unit": untimed_meter([sig(chords=[{"entry": 0, "at": {"count": 1, "unit": 0}}])], *tied),
            "unit past 1024": untimed_meter([sig(chords=[{"entry": 0, "at": {"count": 1, "unit": 2048}}])], *tied),
            "locator without document": untimed_meter([sig(locator={"bar": 1})]),
            "felt without written": {"payload": {"layers": [{"bars": [sig()], "role": "felt"}]},
                                     "refs": pins(("work", WORK))},
            "modulation past the bars": untimed_meter([sig()], modulations=[
                {"layer": 0, "bar": 1, "kind": "equation", "before": {"denominator": 4}, "after": {"denominator": 4}}]),
            "a stem": untimed_meter([sig()], ("analysed", OTHER)),
        }
        for name, claim in cases.items():
            with self.subTest(name), self.assertRaises(HarmonyError):
                check_meter_layers_v2(claim, form_claim=form, sequence_claim=seq)

    def test_refuses_malformed_placements(self):
        untimed = self.lead_sheet_meter()

        def placement(layers, *extra, **payload):
            return {"payload": {"sample_rate": RATE, "layers": layers, **payload}, "refs": refs(*extra)}

        one = [{"bars": [bar(0, SECOND, 4, 4, placed={"layer": 0, "bar": 0})]}]
        cases = {
            "placed without meter": placement(one),
            "meter without alignment": placement(one, ("meter", METER_REF)),
            "another signature": placement([{"bars": [bar(0, SECOND, 3, 4, placed={"layer": 0, "bar": 0})]}],
                                           ("meter", METER_REF), alignment={"method": "manual"}),
            "no such bar": placement([{"bars": [bar(0, SECOND, 4, 4, placed={"layer": 0, "bar": 9})]}],
                                     ("meter", METER_REF), alignment={"method": "manual"}),
            "no such layer": placement([{"bars": [bar(0, SECOND, 4, 4, placed={"layer": 1, "bar": 0})]}],
                                       ("meter", METER_REF), alignment={"method": "manual"}),
            "section on a timed bar": placement([{"bars": [bar(0, SECOND, 4, 4, section=0)]}]),
        }
        for name, claim in cases.items():
            with self.subTest(name), self.assertRaises(HarmonyError):
                check_meter_layers_v2(claim, fixture_stream(), untimed_claim=untimed)
        with self.assertRaises(FormError):
            check_meter_layers_v2(placement(one, ("meter", METER_REF), alignment={"method": "manual"}),
                                  fixture_stream(), untimed_claim={**untimed, "revision_id": OTHER["revision_id"]})
        with self.assertRaises(FormError):
            check_meter_layers_v2(placement(one))


if __name__ == "__main__":
    unittest.main()
