"""Charts read into claim payloads, and link candidates, on invented charts
and an invented export only. Every payload is also checked by World inside
`chart_claims`; these tests pin what the payloads say."""

from __future__ import annotations

import unittest

from chart import UnsupportedChart
from claims import UNMARKED, chart_claims
from links import Index, normalise, person_candidates, work_candidates

import adapter


def labels(claims) -> list[str]:
    return [section["label"] for section in claims.form["sections"]]


def bar_chords(claims) -> list[list[tuple[int, dict | None]]]:
    return [[(c["entry"], c.get("at")) for c in bar.get("chords", [])] for bar in claims.meter["layers"][0]["bars"]]


def texts(claims) -> list[str]:
    return [entry["readings"][0].get("text") for entry in claims.sequence["entries"]]


class Sections(unittest.TestCase):
    def test_marks_endings_and_the_unmarked_stretch(self) -> None:
        claims = chart_claims("[T44C XyQ|F XyQ][*AG7XyQ|C XyQ]{*BF  |N1G7  }XyQ|N2C  Z", "C")
        self.assertEqual(labels(claims), [UNMARKED, "A", "B", "1.", "2."])
        self.assertEqual(claims.form["repeats"], [{"first": 2, "last": 4, "times": 2, "endings": [
            {"first": 3, "last": 3, "passes": [1]}, {"first": 4, "last": 4, "passes": [2]}]}])
        bars = claims.meter["layers"][0]["bars"]
        self.assertEqual([bar["section"] for bar in bars], [0, 0, 1, 1, 2, 3, 4])
        self.assertEqual([bar["locator"] for bar in bars], [{"bar": n} for n in range(1, 8)])
        self.assertEqual(claims.form["sections"][1]["locator"], {"bar": 3})

    def test_a_section_after_a_repeat_repeats_the_mark_in_force_and_its_kind(self) -> None:
        claims = chart_claims("T44*i{C XyQ|F XyQ}G7XyQ|C XyQZ", "")
        self.assertEqual(labels(claims), ["i", "i"])
        self.assertEqual([s.get("kind") for s in claims.form["sections"]], ["intro", "intro"])
        self.assertEqual(claims.form["repeats"], [{"first": 0, "last": 0, "times": 2}])
        self.assertIsNone(claims.key)

    def test_dal_segno_al_coda(self) -> None:
        claims = chart_claims("[T44*ASC XyQ|F Q XyQ|G7XyQ|C <D.S. al Coda>XyQ]Q[*BF XyQ|C XyQZ", "F")
        form = claims.form
        self.assertEqual(labels(claims), ["A", "A", "B"])
        self.assertEqual(form["jumps"], [{"after": 1, "kind": "dal_segno", "then": "coda", "to": 0,
                                          "coda_from": 0, "coda": 2}])

    def test_da_capo_al_fine(self) -> None:
        claims = chart_claims("[T44*AC XyQ|F <Fine>XyQ][*BG7XyQ|C <D.C. al Fine>XyQZ", "C")
        self.assertEqual(claims.form["jumps"], [{"after": 1, "kind": "da_capo", "then": "fine", "fine": 0}])


class Entries(unittest.TestCase):
    def test_cells_place_chords_in_any_metre(self) -> None:
        claims = chart_claims("[T34C F G |T44A-7 D7 |G^7XyQZ", "G")
        self.assertEqual(bar_chords(claims), [
            [(0, None), (1, {"count": 1, "unit": 4}), (2, {"count": 1, "unit": 2})],
            [(3, None), (4, {"count": 1, "unit": 2})],
            [(5, None)]])
        self.assertEqual([(bar["numerator"], bar["denominator"]) for bar in claims.meter["layers"][0]["bars"]],
                         [(3, 4), (4, 4), (4, 4)])

    def test_a_repeated_bar_holds_one_downbeat_chord_and_copies_more(self) -> None:
        claims = chart_claims("[T44C XyQKcl LZD-7 G7 |x |F XyQ|G XyQ|r|XyQ|C XyQ|C XyQ|r|XyQZ", "C")
        self.assertEqual(texts(claims), ["C", "D-7", "G7", "D-7", "G7", "F", "G", "F", "G", "C", "C"])
        self.assertEqual(bar_chords(claims), [
            [(0, None)], [], [(1, None), (2, {"count": 1, "unit": 2})], [(3, None), (4, {"count": 1, "unit": 2})],
            [(5, None)], [(6, None)], [(7, None)], [(8, None)], [(9, None)], [(10, None)], [], []])

    def test_a_two_bar_repeat_brings_back_a_chord_that_is_not_sounding(self) -> None:
        # F then G, repeated: the repeat's first bar is F again, not G held.
        claims = chart_claims("[T44F XyQ|G XyQ|r|XyQZ", "C")
        self.assertEqual(texts(claims), ["F", "G", "F", "G"])
        self.assertEqual(bar_chords(claims), [[(0, None)], [(1, None)], [(2, None)], [(3, None)]])

    def test_alternates_invisible_roots_slashes_and_no_chord(self) -> None:
        claims = chart_claims("[T44C7(Db7) W/E pn |(Gb7)XyQZ", "C")
        entries = claims.sequence["entries"]
        self.assertEqual([[r.get("text") for r in e["readings"]] for e in entries],
                         [["C7", "Db7"], ["W/E"], ["n"], ["Gb7"]])
        self.assertEqual(entries[1]["readings"][0]["bass"], 4)
        self.assertEqual(entries[2]["readings"][0], {"no_chord": True, "text": "n"})

    def test_text_only_readings_are_counted_never_guessed(self) -> None:
        claims = chart_claims("[T44C2 XyQ|F**XyQZ", "")
        self.assertEqual(claims.sequence["entries"][0]["readings"], [{"text": "C2"}])
        self.assertEqual([symbol for symbol, _ in claims.text_only], ["C2", "F**"])

    def test_the_key_field(self) -> None:
        self.assertEqual(chart_claims("[T44C XyQZ", "Eb-").key, {"regions": [{"readings": [
            {"collection": [1, 3, 5, 6, 8, 10, 11], "tonic": 3, "mode": "minor", "text": "Eb-"}]}]})


class Failures(unittest.TestCase):
    def test_structure_that_cannot_be_read_fails_the_whole_chart(self) -> None:
        for markup, reason in (
                ("C7 |F |", "no time signature"),
                ("[T44(C7)x (F7)|C XyQZ", "a bar repeat beside other cells"),
                ("[T44x XyQ|C XyQZ", "too few bars"),
                ("T44{C XyQ|SF XyQ}G <D.S.>XyQZ", "World refuses the reading: form jump 0 lands inside a repeat"),
                ("[T44 XyQ|p p p p Z", "no chords")):
            with self.subTest(markup=markup), self.assertRaisesRegex(UnsupportedChart, reason):
                chart_claims(markup, "C")


def export(records: list[dict]) -> adapter.ArchiveReader:
    full = []
    for number, record in enumerate(records):
        full.append({"revision_id": f"sha256:{number:064x}", "supersedes": [], "refs": [], "contracts": [],
                     "agent": "urn:uuid:00000000-0000-4000-8000-0000000000ff", **record})
    return adapter.ArchiveReader.from_export(
        "/nonexistent", {"archive_id": "urn:uuid:00000000-0000-4000-8000-000000000000", "spec": "archive/0.0.10",
                         "snapshot": None, "records": full, "contracts": []})


class Candidates(unittest.TestCase):
    WORK, OTHER, PERSON = ("urn:uuid:00000000-0000-4000-8000-0000000000a%d" % n for n in (1, 2, 3))

    def setUp(self) -> None:
        def ref(role: str, target: str, n: int) -> dict:
            return {"role": role, "id": target, "revision_id": f"sha256:{n:064x}"}
        self.reader = export([
            {"object_id": self.WORK, "shape": "entity", "payload": {"kind": "musical_work", "label": "Invented Waltz"}},
            {"object_id": self.OTHER, "shape": "entity",
             "payload": {"kind": "musical_work", "label": "The Invented Waltz"}},
            {"object_id": self.PERSON, "shape": "entity", "payload": {"kind": "person", "label": "Jane Inventor"}},
            {"object_id": "urn:uuid:00000000-0000-4000-8000-0000000000b1", "shape": "claim",
             "contracts": [{"id": "archive.claim/contribution", "version": "1"}],
             "payload": {"scope": "work", "role": {"type": "composer"}},
             "refs": [ref("work", self.WORK, 0), ref("contributor", self.PERSON, 2)]},
            {"object_id": "urn:uuid:00000000-0000-4000-8000-0000000000b2", "shape": "claim",
             "contracts": [{"id": "archive.claim/performance_work", "version": "3"}], "payload": {},
             "refs": [ref("work", self.WORK, 0)]},
        ])

    def test_normalised_titles(self) -> None:
        self.assertEqual(normalise("Invented Waltz, The"), normalise("the  invented waltz"))
        self.assertEqual(normalise("Café & Bar (Live)"), "cafe and bar")

    def test_a_title_alone_scores_below_a_title_with_its_composer(self) -> None:
        index = Index.of(self.reader)
        found = work_candidates(index, "Invented Waltz, The", "Inventor Jane")
        self.assertEqual([(c["work"], c["score"]) for c in found], [(self.WORK, 3), (self.OTHER, 1)])
        self.assertEqual(found[0]["evidence"], {"credits": ["Jane Inventor"], "performances": 1,
                                                "musicbrainz_correspondences": 0})
        self.assertEqual(found[1]["reasons"], ["normalised title equal"])
        self.assertEqual([c["person"] for c in person_candidates(index, "Inventor Jane")], [self.PERSON])

    def test_a_different_title_by_the_same_composer_is_still_listed(self) -> None:
        found = work_candidates(Index.of(self.reader), "Another Invented Tune", "Inventor Jane")
        self.assertEqual([(c["work"], c["reasons"][0][:22]) for c in found], [(self.WORK, "composer credit agrees")])


if __name__ == "__main__":
    unittest.main()
