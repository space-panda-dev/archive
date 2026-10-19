"""Note events per part, and melody at two levels (ADR 0086): the payload
rules of `archive.claim/notes`, `archive.claim/melody`, and
`archive.claim/melody_spans`, and the views that read them.

Run from the repository root: python3 -m unittest world.tests.test_notes.
The module needs only the standard library. Every example is synthetic
(INV-23): the stream claim is the fixture archive's own stream record from
fixture/plan.json, lengthened to a minute.
"""

import copy
import json
import unittest
from fractions import Fraction
from pathlib import Path

from world.notes import (
    MELODY,
    MELODY_SPANS,
    NOTES,
    NotesError,
    check_melody,
    check_melody_spans,
    check_notes,
    check_stem_citation,
    handoffs,
    melody_in_bars,
    notes_range,
    tune_notes,
)

ROOT = Path(__file__).resolve().parents[2]


def pin(n: int) -> dict:
    return {"id": f"urn:uuid:00000000-0000-4000-8000-0000000000{n:02x}", "revision_id": "sha256:" + f"{n:02x}" * 32}


SOURCE, STREAM, RUN, DOCUMENT, WORK, TUNING, METER = (pin(n) for n in (0xc1, 0xc2, 0xc3, 0xc4, 0xc5, 0xc6, 0xc7))
OTHER_SOURCE, VOICE, GUITAR, GUITAR_LATER = (pin(n) for n in (0xd1, 0xd2, 0xd3, 0xd4))
RATE = 8000
SECOND = RATE
STEM = {"extractor": "stems", "key": "ab" * 32, "output": "vocals", "sha256": "cd" * 32}


def fixture_stream() -> dict:
    """The fixture archive's audio stream claim, about SOURCE, one minute long."""
    plan = json.loads((ROOT / "fixture/plan.json").read_text())
    records = [r for step in plan["steps"] for r in step.get("records", [])]
    record = next(r for r in records if r.get("namespace") == "archive.claim/audio_stream")
    payload = dict(record["payload"])
    assert payload["sample_rate"] == RATE, "the fixture stream's rate changed; update RATE"
    payload["sample_count"] = 60 * SECOND
    return {"revision_id": STREAM["revision_id"], "payload": payload,
            "refs": [{"role": "artifact", **SOURCE}, {"role": "run", **RUN}]}


def ref(role: str, target: dict) -> dict:
    return {"role": role, **target}


def note(onset: float, offset: float, pitch: int, **fields) -> dict:
    return {"onset_sample": int(onset * SECOND), "offset_sample": int(offset * SECOND), "pitch": pitch, **fields}


def notes_claim(notes: list, *extra: dict, revision: dict | None = None, **payload) -> dict:
    claim = {"payload": {"sample_rate": RATE, "part": "voice", "notes": notes, **payload},
             "refs": [ref("source", SOURCE), ref("stream", STREAM), *extra]}
    if revision is not None:
        claim["revision_id"] = revision["revision_id"]
    return claim


def tuning_claim(source: dict = SOURCE) -> dict:
    return {"revision_id": TUNING["revision_id"],
            "payload": {"sample_rate": RATE, "reference_cents": "-12.5"},
            "refs": [ref("source", source), ref("stream", STREAM)]}


def length(count: int, unit: int) -> dict:
    return {"count": count, "unit": unit}


def melody_claim(items: list, *extra: dict, **payload) -> dict:
    return {"payload": {"notes": items, **payload}, "refs": [ref("work", WORK), *extra]}


def untimed_meter(*bars: tuple[int, int]) -> dict:
    return {"revision_id": METER["revision_id"],
            "payload": {"layers": [{"role": "written", "bars": [
                {"numerator": n, "denominator": d} for n, d in bars]}]},
            "refs": [ref("work", WORK)]}


def spans_claim(spans: list, *pinned: dict) -> dict:
    return {"payload": {"sample_rate": RATE, "spans": spans},
            "refs": [ref("source", SOURCE), ref("stream", STREAM), *(ref("notes", p) for p in pinned)]}


def span(start: float, end: float, notes: dict, **fields) -> dict:
    return {"start_sample": int(start * SECOND), "end_sample": int(end * SECOND),
            "notes": notes["revision_id"], **fields}


class ContractNames(unittest.TestCase):
    def test_three_distinct_version_one_contracts(self):
        self.assertEqual(len({NOTES, MELODY, MELODY_SPANS}), 3)
        self.assertEqual({version for _, version in (NOTES, MELODY, MELODY_SPANS)}, {"1"})


class NotesAccepted(unittest.TestCase):
    def test_machine_notes_of_a_stem_by_bank_key(self):
        claim = notes_claim([note(0, 0.5, 60, velocity=90), note(0.5, 1.25, 62), note(0.5, 0.75, 67)],
                            ref("run", RUN), stem=STEM)
        self.assertEqual(len(check_notes(claim, fixture_stream())), 3)

    def test_cents_relative_to_a_pinned_tuning(self):
        claim = notes_claim([note(1, 2, 64, cents="-14.5"), note(2, 3, 65, cents="0")], ref("tuning", TUNING))
        check_notes(claim, fixture_stream(), tuning_claim())

    def test_document_track_with_bar_locators_and_instrument(self):
        claim = notes_claim(
            [note(0, 0.5, 52, locator={"bar": 1}),
             note(0.5, 0.75, 55, locator={"bar": 1, "at": length(1, 4), "duration": length(1, 8)}),
             note(0.75, 1, 59, locator={"bar": 1, "at": length(3, 8), "duration": length(1, 12)})],
            ref("document", DOCUMENT), part="guitar 2",
            alignment={"method": "manual"}, document_url="https://example.org/tab.gp",
            instrument={"strings": [64, 59, 55, 50, 45, 40], "capo": 2})
        check_notes(claim, fixture_stream())

    def test_split_by_range_and_confidence_on_its_own_scale(self):
        claim = notes_claim([note(30, 31, 60, confidence="0.9")], ref("run", RUN),
                            range={"start_sample": 30 * SECOND, "end_sample": 45 * SECOND},
                            confidence_scale="voicing confidence of the melody extractor, version 2")
        check_notes(claim, fixture_stream())
        self.assertEqual(notes_range(claim, 60 * SECOND), (30 * SECOND, 45 * SECOND))
        self.assertEqual(notes_range(notes_claim([note(0, 1, 60)]), 60 * SECOND), (0, 60 * SECOND))

    def test_a_note_starting_in_range_may_ring_past_it(self):
        claim = notes_claim([note(44, 50, 60)], range={"start_sample": 30 * SECOND, "end_sample": 45 * SECOND})
        check_notes(claim, fixture_stream())

    def test_unpitched_part_and_correction(self):
        claim = notes_claim([note(0, 0.1, 36), note(0, 0.1, 42)], ref("corrects", VOICE),
                            ref("derived_from", GUITAR), ref("contribution", RUN), unpitched=True, part="drums")
        check_notes(claim, fixture_stream())

    def test_a_person_chosen_stem_artifact_instead_of_a_bank_key(self):
        check_notes(notes_claim([note(0, 1, 60)], ref("analysed", DOCUMENT)), fixture_stream())


class NotesRefused(unittest.TestCase):
    def refused(self, claim: dict, tuning: dict | None = None) -> None:
        with self.assertRaises(NotesError):
            check_notes(claim, fixture_stream(), tuning)

    def test_part_is_required(self):
        claim = notes_claim([note(0, 1, 60)])
        del claim["payload"]["part"]
        self.refused(claim)

    def test_note_shapes(self):
        for bad in (note(1, 1, 60), note(1, 0.5, 60), note(0, 61, 60), note(0, 1, 128), note(0, 1, -1),
                    note(0, 1, True), note(0, 1, 60, velocity=0), note(0, 1, 60, velocity=128),
                    note(0, 1, 60, duration=1), {"onset_sample": 0, "pitch": 60}):
            with self.subTest(bad=bad):
                self.refused(notes_claim([bad]))

    def test_order_and_duplicates(self):
        self.refused(notes_claim([note(1, 2, 60), note(0, 2, 62)]))
        self.refused(notes_claim([note(0, 2, 64), note(0, 2, 60)]))
        self.refused(notes_claim([note(0, 2, 60), note(0, 1, 60)]))

    def test_cents_need_a_tuning_and_a_range(self):
        self.refused(notes_claim([note(0, 1, 60, cents="5")]))
        for bad in ("50", "-50.5", "5c", 5):
            with self.subTest(cents=bad):
                self.refused(notes_claim([note(0, 1, 60, cents=bad)], ref("tuning", TUNING)))

    def test_tuning_of_another_recording(self):
        self.refused(notes_claim([note(0, 1, 60)], ref("tuning", TUNING)), tuning_claim(OTHER_SOURCE))

    def test_unpitched_takes_no_cents_or_tuning(self):
        self.refused(notes_claim([note(0, 1, 36)], ref("tuning", TUNING), unpitched=True))
        self.refused(notes_claim([note(0, 1, 36)], unpitched=False))

    def test_locator_alignment_instrument_need_a_document(self):
        self.refused(notes_claim([note(0, 1, 60, locator={"bar": 1})]))
        self.refused(notes_claim([note(0, 1, 60)], alignment={"method": "manual"}))
        self.refused(notes_claim([note(0, 1, 60)], instrument={"capo": 2}))
        self.refused(notes_claim([note(0, 1, 60)], ref("document", DOCUMENT)))  # no alignment
        with_document = (ref("document", DOCUMENT),)
        for bad in ({"bar": 0}, {"bar": 1, "at": length(-1, 4)}, {"bar": 1, "duration": length(0, 4)},
                    {"bar": 1, "duration": length(1, 0)}, {"bar": 1, "beat": 2}):
            with self.subTest(locator=bad):
                self.refused(notes_claim([note(0, 1, 60, locator=bad)], *with_document,
                                         alignment={"method": "manual"}))
        self.refused(notes_claim([note(0, 1, 60)], *with_document, alignment={"method": "manual"}, instrument={}))

    def test_range_bounds_and_onsets(self):
        self.refused(notes_claim([note(0, 1, 60)], range={"start_sample": SECOND, "end_sample": 2 * SECOND}))
        self.refused(notes_claim([note(2, 3, 60)], range={"start_sample": SECOND, "end_sample": 2 * SECOND}))
        self.refused(notes_claim([note(0, 1, 60)], range={"start_sample": SECOND, "end_sample": SECOND}))
        self.refused(notes_claim([note(0, 1, 60)], range={"start_sample": 0, "end_sample": 61 * SECOND}))

    def test_confidence_exactly_with_a_scale(self):
        self.refused(notes_claim([note(0, 1, 60, confidence="0.5")]))
        self.refused(notes_claim([note(0, 1, 60)], confidence_scale="x"))

    def test_stem_citation(self):
        self.refused(notes_claim([note(0, 1, 60)], ref("analysed", DOCUMENT), stem=STEM))
        for field, bad in (("key", "AB" * 32), ("sha256", "cd" * 31), ("extractor", "Stems"), ("output", "")):
            with self.subTest(field=field):
                self.refused(notes_claim([note(0, 1, 60)], stem={**STEM, field: bad}))
        with self.assertRaises(NotesError):
            check_stem_citation({**STEM, "path": "x"})

    def test_references(self):
        self.refused(notes_claim([note(0, 1, 60)], ref("sequence", RUN)))
        self.refused(notes_claim([note(0, 1, 60)], ref("run", RUN), ref("run", DOCUMENT)))
        self.refused(notes_claim([note(0, 1, 60)], ref("corrects", VOICE), ref("corrects", VOICE)))
        wrong_rate = notes_claim([note(0, 1, 60)])
        wrong_rate["payload"]["sample_rate"] = RATE * 2
        self.refused(wrong_rate)
        other_stream = fixture_stream()
        other_stream["refs"][0] = ref("artifact", OTHER_SOURCE)
        with self.assertRaises(NotesError):
            check_notes(notes_claim([note(0, 1, 60)]), other_stream)


class WorkMelody(unittest.TestCase):
    # A pickup quarter, then a bar of 4/4 whose last note, a dotted half, is
    # tied into the next bar, which ends on a triplet.
    ITEMS = [{"pitch": 67, "duration": length(1, 4)},
             {"pitch": 72, "duration": length(1, 4)}, {"rest": True, "duration": length(1, 8)},
             {"pitch": 71, "duration": length(1, 8)}, {"pitch": 69, "duration": length(3, 4)},
             {"pitch": 67, "duration": length(1, 12)}, {"pitch": 65, "duration": length(1, 12)},
             {"pitch": 64, "duration": length(1, 12)}]

    def test_accepted_without_bars(self):
        self.assertEqual(len(check_melody(melody_claim(self.ITEMS))), 8)

    def test_accepted_on_untimed_bars_with_a_pickup(self):
        claim = melody_claim(self.ITEMS, ref("meter", METER), start={"layer": 0, "bar": 0, "at": length(3, 4)})
        check_melody(claim, untimed_meter((4, 4), (4, 4), (4, 4)))

    def test_laid_on_bars_ties_over_the_bar_line(self):
        lengths = [Fraction(1, 4), Fraction(1), Fraction(1)]  # the pickup's room, then two full bars
        pieces = melody_in_bars({"notes": self.ITEMS}, lengths)
        held = [p for p in pieces if p["item"] == 4]
        self.assertEqual([(p["bar"], p["at"], p["length"], p["tied"]) for p in held],
                         [(1, Fraction(1, 2), Fraction(1, 2), True), (2, Fraction(0), Fraction(1, 4), False)])
        self.assertEqual(sum((p["length"] for p in pieces), Fraction(0)), Fraction(7, 4))

    def test_document_subject_and_locators(self):
        claim = {"payload": {"notes": [{"pitch": 60, "duration": length(1, 2), "locator": {"bar": 3}}],
                             "document_url": "https://example.org/chart"},
                 "refs": [ref("document", DOCUMENT), ref("derived_from", VOICE)]}
        check_melody(claim)

    def test_refused(self):
        bad_claims = [
            {"payload": {"notes": self.ITEMS}, "refs": [ref("run", RUN)]},              # no subject
            melody_claim([{"rest": True, "duration": length(1, 4)}]),                    # only rests
            melody_claim([{"pitch": 60, "rest": True, "duration": length(1, 4)}]),       # both
            melody_claim([{"pitch": 60}]),                                                # no duration
            melody_claim([{"pitch": 60, "duration": length(0, 4)}]),                      # empty
            melody_claim([{"pitch": 60, "duration": length(1, 4096)}]),                   # too fine
            melody_claim([{"rest": False, "duration": length(1, 4)}]),
            melody_claim([{"pitch": 60, "duration": length(1, 4), "locator": {"bar": 1}}]),  # no document
            melody_claim([{"pitch": 60, "duration": length(1, 4), "onset_sample": 0}]),  # a sample in a Work
            melody_claim(self.ITEMS, start={"layer": 0, "bar": 0}),                      # start, no meter
            melody_claim(self.ITEMS, document_url="https://example.org/x"),              # url, no document
            melody_claim([], ref("meter", METER)),
        ]
        for index, claim in enumerate(bad_claims):
            with self.subTest(index=index), self.assertRaises(NotesError):
                check_melody(claim)

    def test_refused_against_its_bars(self):
        claim = melody_claim(self.ITEMS, ref("meter", METER), start={"layer": 0, "bar": 0, "at": length(3, 4)})
        for meter in (untimed_meter((4, 4), (4, 4)),                  # too short
                      untimed_meter((3, 4), (4, 4), (4, 4))):         # start offset at the bar's end
            with self.subTest(meter=meter), self.assertRaises(NotesError):
                check_melody(claim, meter)
        timed = untimed_meter((4, 4), (4, 4), (4, 4))
        timed["refs"].append(ref("source", SOURCE))
        with self.assertRaises(NotesError):
            check_melody(claim, timed)
        other = untimed_meter((4, 4), (4, 4), (4, 4))
        other["revision_id"] = RUN["revision_id"]
        with self.assertRaises(NotesError):
            check_melody(claim, other)
        past = melody_claim(self.ITEMS, ref("meter", METER), start={"layer": 0, "bar": 5})
        with self.assertRaises(NotesError):
            check_melody(past, untimed_meter((4, 4)))


class RecordingMelody(unittest.TestCase):
    def setUp(self):
        self.voice = notes_claim([note(0, 1, 67), note(1, 2, 69), note(8, 9, 71)], revision=VOICE)
        self.guitar = notes_claim([note(4, 5, 55), note(4, 5, 59), note(4, 5, 64), note(5, 6, 62)],
                                  revision=GUITAR, part="guitar",
                                  range={"start_sample": 0, "end_sample": 30 * SECOND})
        self.later = notes_claim([note(31, 32, 60)], revision=GUITAR_LATER, part="guitar",
                                 range={"start_sample": 30 * SECOND, "end_sample": 60 * SECOND})
        self.claims = {VOICE["revision_id"]: self.voice, GUITAR["revision_id"]: self.guitar,
                       GUITAR_LATER["revision_id"]: self.later}
        self.spans = [span(0, 4, VOICE), span(4, 8, GUITAR, select="highest"), span(8, 12, VOICE),
                      span(31, 40, GUITAR_LATER)]

    def test_hand_offs_and_the_tune(self):
        # Anchor: the two parts differ, so a hand-off is visible.
        self.assertNotEqual(VOICE["revision_id"], GUITAR["revision_id"])
        claim = spans_claim(self.spans, VOICE, GUITAR, GUITAR_LATER)
        check_melody_spans(claim, fixture_stream(), self.claims)
        self.assertEqual(handoffs(claim["payload"]),
                         [{"sample": 4 * SECOND, "from": VOICE["revision_id"], "to": GUITAR["revision_id"]},
                          {"sample": 8 * SECOND, "from": GUITAR["revision_id"], "to": VOICE["revision_id"]}])
        tune = tune_notes(claim["payload"], self.claims)
        self.assertEqual([n["pitch"] for n in tune], [67, 69, 64, 62, 71, 60])
        self.assertEqual(tune[2]["from"], GUITAR["revision_id"])

    def test_lowest_and_all(self):
        claim = spans_claim([span(4, 8, GUITAR, select="lowest")], GUITAR)
        self.assertEqual([n["pitch"] for n in tune_notes(claim["payload"], self.claims)], [55, 62])
        claim = spans_claim([span(4, 8, GUITAR)], GUITAR)
        self.assertEqual([n["pitch"] for n in tune_notes(claim["payload"], self.claims)], [55, 59, 64, 62])

    def test_refused(self):
        stream = fixture_stream()
        cases = [
            spans_claim(self.spans, VOICE, GUITAR),                               # selects an unpinned claim
            spans_claim([span(0, 4, VOICE)], VOICE, GUITAR),                      # pins an unused claim
            spans_claim([span(0, 4, VOICE), span(3, 6, GUITAR)], VOICE, GUITAR),  # overlap
            spans_claim([span(4, 4, VOICE)], VOICE),                              # empty
            spans_claim([span(0, 61, VOICE)], VOICE),                             # past the stream
            spans_claim([span(0, 4, VOICE, select="top")], VOICE),
            spans_claim([], VOICE),
            spans_claim([span(0, 4, VOICE)]),                                     # no notes pinned
        ]
        for index, claim in enumerate(cases):
            with self.subTest(index=index), self.assertRaises(NotesError):
                check_melody_spans(claim, stream, self.claims)

    def test_refused_against_the_notes_claims(self):
        stream = fixture_stream()
        # A span reaching outside the selected claim's range.
        with self.assertRaises(NotesError):
            check_melody_spans(spans_claim([span(25, 35, GUITAR)], GUITAR), stream, self.claims)
        # A notes claim about another recording.
        moved = copy.deepcopy(self.claims)
        moved[VOICE["revision_id"]]["refs"][0] = ref("source", OTHER_SOURCE)
        with self.assertRaises(NotesError):
            check_melody_spans(spans_claim([span(0, 4, VOICE)], VOICE), stream, moved)
        # A claim not given, and one given under the wrong revision.
        with self.assertRaises(NotesError):
            check_melody_spans(spans_claim([span(0, 4, VOICE)], VOICE), stream, {})
        swapped = {VOICE["revision_id"]: self.guitar}
        with self.assertRaises(NotesError):
            check_melody_spans(spans_claim([span(0, 4, VOICE)], VOICE), stream, swapped)
        # Without the notes claims, only the claim's own rules apply.
        check_melody_spans(spans_claim([span(25, 35, GUITAR)], GUITAR), stream)


class EarlierContractsUnchanged(unittest.TestCase):
    """This record adds contracts and amends none: ADR 0077's version 1
    chord stacks still accepts the same claim, and knows no notes role."""

    def test_chord_stacks_v1_still_valid_and_closed(self):
        from world.harmony import HarmonyError, check_chord_stacks
        stream = fixture_stream()
        claim = {"payload": {"sample_rate": RATE, "spans": [
                    {"start_sample": 0, "end_sample": SECOND, "readings": [{"bass": 0, "tones": [4, 7]}]}]},
                 "refs": [ref("source", SOURCE), ref("stream", STREAM), ref("run", RUN)]}
        check_chord_stacks(claim, stream)
        claim["refs"].append(ref("notes", VOICE))
        with self.assertRaises(HarmonyError):
            check_chord_stacks(claim, stream)


if __name__ == "__main__":
    unittest.main()
