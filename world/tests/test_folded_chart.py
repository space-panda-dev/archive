"""The folded chart (ADR 0113, `archive.view/folded_chart` version 1): a
recording's chart folds into repeat signs only where its form spans map
consecutive passes at approximate or exact precision; everything else is
written out as played.

Run from the repository root: python3 -m unittest world.tests.test_folded_chart.
The module needs only the standard library. Every example is synthetic
(INV-23). Chords are given per span, as the view takes them, so the tests
need no projection.
"""

import unittest

from world.form import FOLDED_CHART, check_form_spans_v2, check_form_v2, folded_chart
from world.tests.test_form import FORM_REF, form_claim, section, spans_claim
from world.tests.test_form_v2 import jazz
from world.tests.test_harmony import SECOND, fixture_stream

MANUAL = {"method": "manual"}


def placed(entries: list[tuple], **alignment) -> dict:
    """A form spans claim of one-second spans from the start: each entry a
    (section, passes) pair or a label, and the chords of its bars."""
    spans = []
    for at, (what, chords) in enumerate(entries):
        span = {"start_sample": at * SECOND, "end_sample": (at + 1) * SECOND}
        if isinstance(what, str):
            span["label"] = what
        else:
            span["section"] = what[0]
            if what[1] is not None:
                span["passes"] = what[1]
        spans.append(span)
    return spans_claim(spans, ("form", FORM_REF), alignment=alignment or MANUAL)


def chords_of(entries: list[tuple], gaps: dict[int, list] | None = None):
    """The chord reading as the view takes it: a function of a stretch."""
    table = {at * SECOND: chords for at, (_, chords) in enumerate(entries)}
    table.update(gaps or {})
    return lambda start, end: table.get(start, [])


# Head, two solo choruses on the form, and the head out: the ADR's chart.
CHORUSES = [
    ((0, None), ["Cmaj7"]),
    ((1, [1]), ["Dm7", "G7"]), ((2, [1]), ["Cmaj7", "C7"]), ((3, [1]), ["Em7", "A7"]),
    ((1, [2]), ["Dm7", "G7"]), ((2, [2]), ["Cmaj7", "F7"]), ((3, [2]), ["Em7", "A7"]),
    ((1, [3]), ["Dm7", "G7"]), ((2, [3]), ["Cmaj7", "C7"]), ((4, [3]), ["Fmaj7", "Fm6"]),
    ((5, None), ["Cmaj7"]),
]


def chart(entries, form=None, sample_count=None, gaps=None, **alignment):
    form = form or jazz()
    claim = placed(entries, **alignment)
    check_form_v2(form)
    check_form_spans_v2(claim, fixture_stream(), form)
    return folded_chart(form["payload"], claim, chords_of(entries, gaps), sample_count)


class FoldedChartTest(unittest.TestCase):
    def test_a_mapped_open_repeat_folds_with_its_count(self):
        view = chart(CHORUSES)
        self.assertEqual(view["view"], list(FOLDED_CHART))
        self.assertEqual(view["precision"], "approximate")
        intro, folded, coda = view["chart"]
        self.assertEqual((intro["kind"], intro["written_out"]), ("played", "played"))
        self.assertEqual((coda["kind"], coda["section"]), ("played", 5))
        self.assertEqual(folded["kind"], "repeat")
        self.assertEqual((folded["repeat"], folded["passes"], folded["count"], folded["open"]), (0, [1, 2, 3], 3, True))
        self.assertEqual(folded["spans"], [1, 9])
        self.assertNotIn("difference", folded)

    def test_a_bar_that_differs_outside_an_ending_is_a_variant(self):
        folded = chart(CHORUSES)["chart"][1]
        self.assertEqual(folded["body"], [
            {"section": 1, "bars": [{"chord": "Dm7"}, {"chord": "G7"}]},
            {"section": 2, "bars": [{"chord": "Cmaj7"}, {"chord": "C7", "variants": [{"passes": [2], "chord": "F7"}]}]},
        ])

    def test_passes_that_take_other_endings_show_as_endings(self):
        folded = chart(CHORUSES)["chart"][1]
        self.assertEqual(folded["endings"], [
            {"passes": [1, 2], "sections": [{"section": 3, "bars": ["Em7", "A7"]}]},
            {"passes": [3], "sections": [{"section": 4, "bars": ["Fmaj7", "Fm6"]}]},
        ])

    def test_the_same_ending_with_other_chords_is_its_own_ending(self):
        entries = list(CHORUSES)
        entries[6] = ((3, [2]), ["Em7", "Eb7"])
        endings = chart(entries)["chart"][1]["endings"]
        self.assertEqual([e["passes"] for e in endings], [[1], [2], [3]])

    def test_a_machine_placement_without_precision_never_folds(self):
        view = chart(CHORUSES, method="segmenter")
        self.assertEqual(view["precision"], "rough")
        self.assertEqual([item["kind"] for item in view["chart"]], ["played"] * len(CHORUSES))
        self.assertEqual({item["written_out"] for item in view["chart"]}, {"rough"})

    def test_a_rough_person_never_folds_and_an_approximate_machine_does(self):
        self.assertNotIn("repeat", {i["kind"] for i in chart(CHORUSES, method="manual", precision="rough")["chart"]})
        view = chart(CHORUSES, method="segmenter", precision="approximate")
        self.assertEqual(view["chart"][1]["passes"], [1, 2, 3])

    def test_a_departure_is_written_out_and_breaks_the_fold(self):
        entries = CHORUSES[:4] + [("Extra chorus", ["Dm7"])] + CHORUSES[4:]
        items = chart(entries)["chart"]
        self.assertEqual([i["kind"] for i in items], ["played"] * 5 + ["repeat", "played"])
        self.assertEqual([i.get("written_out") for i in items[1:5]], ["played", "played", "played", "departure"])
        self.assertEqual(items[4]["label"], "Extra chorus")
        self.assertEqual(items[5]["passes"], [2, 3])

    def test_a_pass_that_skips_part_of_the_body_is_written_out(self):
        entries = CHORUSES[:4] + [CHORUSES[5], CHORUSES[6]] + CHORUSES[7:]
        items = chart(entries)["chart"]
        # Pass 2 plays no A, so passes 1 and 2 do not fold; 2 then 3 do not
        # match either, and pass 3 alone folds with nothing.
        self.assertEqual({i["kind"] for i in items}, {"played"})

    def test_stretches_no_span_covers_are_written_out(self):
        entries = CHORUSES[:2]
        claim = placed(entries)
        claim["payload"]["spans"][1] = {**claim["payload"]["spans"][1], "start_sample": 2 * SECOND,
                                        "end_sample": 3 * SECOND}
        check_form_spans_v2(claim, fixture_stream(), jazz())
        table = {0: ["Cmaj7"], SECOND: ["N.C."], 2 * SECOND: ["Dm7", "G7"], 3 * SECOND: ["Cmaj7"]}
        view = folded_chart(jazz()["payload"], claim, lambda start, end: table.get(start, []), 4 * SECOND)
        self.assertEqual([(i["kind"], i["start_sample"], i["end_sample"], i["bars"]) for i in view["chart"]], [
            ("played", 0, SECOND, ["Cmaj7"]), ("uncovered", SECOND, 2 * SECOND, ["N.C."]),
            ("played", 2 * SECOND, 3 * SECOND, ["Dm7", "G7"]), ("uncovered", 3 * SECOND, 4 * SECOND, ["Cmaj7"])])

    def test_a_fixed_repeat_played_fewer_times_reports_the_difference(self):
        form = form_claim([section("Intro"), section("A"), section("Out")],
                          repeats=[{"first": 1, "last": 1, "times": 3}])
        entries = [((0, None), ["C"]), ((1, [1]), ["F", "G"]), ((1, [2]), ["F", "G"]), ((2, None), ["C"])]
        folded = chart(entries, form)["chart"][1]
        self.assertEqual((folded["count"], folded["times"], folded["difference"]), (2, 3, -1))
        self.assertNotIn("endings", folded)

    def test_an_inner_repeat_folds_inside_one_outer_pass(self):
        form = form_claim([section("Head"), section("Vamp"), section("Out")],
                          repeats=[{"first": 0, "last": 1, "times": 2}, {"first": 1, "last": 1}])
        entries = [((0, [1]), ["C"]), ((1, [1, 1]), ["F"]), ((1, [1, 2]), ["F"]), ((1, [1, 3]), ["Bb"]),
                   ((0, [2]), ["C"]), ((1, [2, 1]), ["F"]), ((2, None), ["C"])]
        items = chart(entries, form)["chart"]
        self.assertEqual([i["kind"] for i in items], ["played", "repeat", "played", "played", "played"])
        inner = items[1]
        self.assertEqual((inner["repeat"], inner["outer_passes"], inner["passes"]), (1, [1], [1, 2, 3]))
        self.assertEqual(inner["body"], [{"section": 1, "bars": [{"chord": "F", "variants": [
            {"passes": [3], "chord": "Bb"}]}]}])

    def test_an_outer_repeat_folds_over_its_inner_passes(self):
        form = form_claim([section("Head"), section("Riff"), section("Out")],
                          repeats=[{"first": 0, "last": 1}, {"first": 1, "last": 1, "times": 2}])
        entries = [((0, [1]), ["C"]), ((1, [1, 1]), ["F"]), ((1, [1, 2]), ["G"]),
                   ((0, [2]), ["C"]), ((1, [2, 1]), ["F"]), ((1, [2, 2]), ["G"]), ((2, None), ["C"])]
        folded, out = chart(entries, form)["chart"]
        self.assertEqual((folded["repeat"], folded["passes"], out["section"]), (0, [1, 2], 2))
        self.assertEqual([(b["section"], b.get("passes")) for b in folded["body"]], [(0, None), (1, [1]), (1, [2])])

    def test_a_tie_shows_the_earliest_pass(self):
        form = form_claim([section("A")], repeats=[{"first": 0, "last": 0}])
        entries = [((0, [1]), ["F"]), ((0, [2]), ["Bb"])]
        bar = chart(entries, form)["chart"][0]["body"][0]["bars"][0]
        self.assertEqual(bar, {"chord": "F", "variants": [{"passes": [2], "chord": "Bb"}]})

    def test_return_spans_fold_apart_from_the_first_time(self):
        form = form_claim([section("A"), section("B")], repeats=[{"first": 0, "last": 0, "times": 2}],
                          jumps=[{"after": 1, "kind": "da_capo", "then": "end", "repeats_on_return": True}])
        entries = [((0, [1]), ["C"]), ((0, [2]), ["C"]), ((1, None), ["G"]), ((0, [1]), ["C"]), ((0, [2]), ["C"]),
                   ((1, None), ["G"])]
        claim = placed(entries)
        for span in claim["payload"]["spans"][3:]:
            span["return"] = True
        check_form_spans_v2(claim, fixture_stream(), form)
        items = folded_chart(form["payload"], claim, chords_of(entries))["chart"]
        self.assertEqual([i["kind"] for i in items], ["repeat", "played", "repeat", "played"])
        self.assertNotIn("return", items[0])
        self.assertTrue(items[2]["return"])

    def test_spans_without_passes_or_form_are_unmapped(self):
        unpinned = spans_claim([{"start_sample": 0, "end_sample": SECOND, "label": "A"}])
        view = folded_chart(None, unpinned, lambda start, end: ["C"])
        self.assertEqual((view["precision"], view["chart"][0]["written_out"]), (None, "unmapped"))
        version_1 = placed([((1, None), ["Dm7"])])
        self.assertEqual(folded_chart(jazz()["payload"], version_1, lambda s, e: [])["chart"][0]["written_out"],
                         "unmapped")

    def test_the_view_is_deterministic(self):
        self.assertEqual(chart(CHORUSES), chart(CHORUSES))


if __name__ == "__main__":
    unittest.main()
