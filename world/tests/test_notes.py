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
from bisect import bisect_right
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
    written_rhythm,
    RHYTHM_RULES,
    ARTICULATIONS,
)

ROOT = Path(__file__).resolve().parents[2]


def pin(n: int) -> dict:
    return {"id": f"urn:uuid:00000000-0000-4000-8000-0000000000{n:02x}", "revision_id": "sha256:" + f"{n:02x}" * 32}


SOURCE, STREAM, RUN, DOCUMENT, WORK, TUNING, METER = (pin(n) for n in (0xc1, 0xc2, 0xc3, 0xc4, 0xc5, 0xc6, 0xc7))
OTHER_SOURCE, VOICE, GUITAR, GUITAR_LATER = (pin(n) for n in (0xd1, 0xd2, 0xd3, 0xd4))
RATE = 8000
SECOND = RATE
STEM = {"extractor": "stems", "key": "sha256:" + "ab" * 32, "output": "vocals", "sha256": "sha256:" + "cd" * 32}


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
            strings=[40, 45, 50, 55, 59, 64], capo=2)
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
        self.refused(notes_claim([note(0, 1, 60)], strings=[40], capo=2))
        self.refused(notes_claim([note(0, 1, 60)], ref("document", DOCUMENT)))  # no alignment
        with_document = (ref("document", DOCUMENT),)
        for bad in ({"bar": 0}, {"bar": 1, "at": length(-1, 4)}, {"bar": 1, "duration": length(0, 4)},
                    {"bar": 1, "duration": length(1, 0)}, {"bar": 1, "beat": 2}):
            with self.subTest(locator=bad):
                self.refused(notes_claim([note(0, 1, 60, locator=bad)], *with_document,
                                         alignment={"method": "manual"}))
        self.refused(notes_claim([note(0, 1, 60)], *with_document, alignment={"method": "manual"}, capo=2))
        self.refused(notes_claim([note(0, 1, 60)], *with_document, alignment={"method": "manual"}, strings=[]))
        self.refused(notes_claim([note(0, 1, 60)], *with_document, alignment={"method": "manual"},
                                 instrument={"strings": [40]}))  # the old nested shape is gone

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
        # Bare hex is refused: the bank, ADR 0079, and ADR 0089 write `sha256:<hex>`.
        for field, bad in (("key", "AB" * 32), ("key", "ab" * 32), ("sha256", "cd" * 31), ("extractor", "Stems"),
                           ("output", "")):
            with self.subTest(field=field):
                self.refused(notes_claim([note(0, 1, 60)], stem={**STEM, field: bad}))
        with self.assertRaises(NotesError):
            check_stem_citation({**STEM, "path": "x"})

    def test_references(self):
        self.refused(notes_claim([note(0, 1, 60)], ref("sequence", RUN)))
        self.refused(notes_claim([note(0, 1, 60)], ref("run", RUN), ref("run", DOCUMENT)))
        self.refused(notes_claim([note(0, 1, 60)], ref("corrects", VOICE), ref("corrects", VOICE)))
        self.refused(notes_claim([note(0, 1, 60)], ref("corrects", VOICE), ref("derived_from", VOICE)))
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


# ---------------------------------------------------------------------------
# ADR 0086, amended 2026-10-19: articulation, the bass line, written rhythm


BASS = pin(0xd5)
BASS_STEM = dict(STEM, output="bass")


class Articulation(unittest.TestCase):
    def test_a_document_or_a_person_writes_articulation(self):
        claim = notes_claim([note(0, 0.2, 60, articulation=["staccato", "accent"]), note(0.5, 1, 62)],
                            ref("document", DOCUMENT), alignment={"method": "manual"})
        check_notes(claim, fixture_stream())
        check_notes(notes_claim([note(0, 1, 60, articulation=["fermata"])]), fixture_stream())   # a person's
        check_melody(melody_claim([{"pitch": 60, "duration": length(1, 4), "articulation": ["marcato"]},
                                   {"rest": True, "duration": length(1, 4)}]))

    def test_a_machine_writes_none(self):
        # Anchor: the same note without the mark is admissible from the run.
        check_notes(notes_claim([note(0, 0.2, 60)], ref("run", RUN), stem=STEM), fixture_stream())
        with self.assertRaises(NotesError):
            check_notes(notes_claim([note(0, 0.2, 60, articulation=["staccato"])], ref("run", RUN), stem=STEM),
                        fixture_stream())
        with self.assertRaises(NotesError):
            check_melody(melody_claim([{"pitch": 60, "duration": length(1, 4), "articulation": ["tenuto"]}],
                                      ref("run", RUN)))

    def test_names_are_distinct_and_known(self):
        self.assertEqual(len(ARTICULATIONS), 6)
        for marks in ([], ["staccato", "staccato"], ["strong-accent"], [1], "staccato", list(ARTICULATIONS) * 2):
            with self.subTest(marks=marks), self.assertRaises(NotesError):
                check_notes(notes_claim([note(0, 1, 60, articulation=marks)]), fixture_stream())
        with self.assertRaises(NotesError):
            check_melody(melody_claim([{"pitch": 60, "duration": length(1, 4)},
                                       {"rest": True, "duration": length(1, 4), "articulation": ["fermata"]}]))


class BassLine(unittest.TestCase):
    def test_the_bass_stem_says_bass(self):
        check_notes(notes_claim([note(0, 1, 40)], ref("run", RUN), stem=BASS_STEM, bass=True), fixture_stream())
        check_notes(notes_claim([note(0, 1, 40)], bass=True), fixture_stream())          # a person's bass part
        with self.assertRaises(NotesError):
            check_notes(notes_claim([note(0, 1, 40)], ref("run", RUN), stem=BASS_STEM), fixture_stream())
        with self.assertRaises(NotesError):
            check_notes(notes_claim([note(0, 1, 40)], bass=False), fixture_stream())

    def setUp(self):
        self.voice = notes_claim([note(0, 1, 67), note(1, 2, 69)], revision=VOICE)
        self.bass = notes_claim([note(4, 5, 40), note(5, 6, 43)], revision=BASS, part="bass", bass=True)
        self.claims = {VOICE["revision_id"]: self.voice, BASS["revision_id"]: self.bass}
        self.payload = spans_claim([span(0, 4, VOICE), span(4, 8, BASS)], VOICE, BASS)["payload"]

    def test_a_machine_span_on_the_bass_is_left_out_and_counted(self):
        left: list = []
        tune = tune_notes(self.payload, self.claims, by_person=False, left_out=left)
        self.assertEqual([n["pitch"] for n in tune], [67, 69])
        self.assertEqual([(x["notes"], x["start_sample"]) for x in left], [(BASS["revision_id"], 4 * SECOND)])

    def test_a_persons_bass_solo_is_the_tune_as_asserted(self):
        left: list = []
        tune = tune_notes(self.payload, self.claims, by_person=True, left_out=left)
        self.assertEqual([n["pitch"] for n in tune], [67, 69, 40, 43])
        self.assertEqual(left, [])


def timed_meter(bars: int = 8, bar_seconds: float = 2.0, numerator: int = 4, denominator: int = 4,
                **bar_fields) -> dict:
    """A timed meter of even 4/4 bars, two seconds each: a beat is half a second."""
    return {"layers": [{"bars": [
        {"start_sample": int(i * bar_seconds * SECOND), "end_sample": int((i + 1) * bar_seconds * SECOND),
         "numerator": numerator, "denominator": denominator, **bar_fields} for i in range(bars)]}]}


def kinds(answer: dict) -> list:
    return [(w["kind"], w["duration"]["count"], w["duration"]["unit"], w.get("boundary"),
             tuple(w.get("articulation", ()))) for w in answer["written"]]


class WrittenRhythm(unittest.TestCase):
    """Written rhythm version 2: sounded notes written as a musician writes
    them, eighths the norm, rests only at phrase boundaries, staccato only
    for a clearly detached note. A beat is half a second here."""

    def write(self, sung, meter=None, **fields):
        return written_rhythm(sung, meter or timed_meter(), sample_rate=RATE, **fields)

    def test_a_simpler_rhythm_wins_over_a_closer_one(self):
        # Version 1 wrote this dotted eighth, sixteenth, eighth, eighth with
        # two notes staccato; the sounded onset 0.375 s is three quarters of
        # a beat, but an eighth costs less than a sixteenth and a dot.
        sung = [note(0, 0.15, 60), note(0.375, 0.42, 62), note(0.5, 0.7, 64), note(0.75, 1.0, 65)]
        answer = self.write(sung)
        self.assertEqual([w["position"] for w in answer["written"]],
                         [length(0, 1), length(1, 8), length(1, 4), length(3, 8)])
        self.assertEqual(kinds(answer), [("chord", 1, 8, None, ())] * 4)
        self.assertEqual(answer["written"][0]["sounded_share"], "0.4")
        self.assertEqual((answer["feel"], answer["onset_offset_samples"]), ("straight", 0))

    def test_sixteenths_are_written_when_nothing_simpler_fits(self):
        run = [note(i * 0.125, i * 0.125 + 0.1, 60 + i) for i in range(5)]
        answer = self.write(run)
        self.assertEqual([w["position"] for w in answer["written"]],
                         [length(0, 1), length(1, 16), length(1, 8), length(3, 16), length(1, 4)])

    def test_a_constant_onset_offset_is_removed(self):
        late = [note(i * 0.25 + 0.03, i * 0.25 + 0.2, 60) for i in range(12)]
        answer = self.write(late)
        self.assertEqual(answer["onset_offset_samples"], int(0.03 * SECOND))
        self.assertEqual([w["position"] for w in answer["written"]], [length(*Fraction(i, 8).as_integer_ratio()) for i in range(12)])
        # Anchor: under ten onsets the offset is not measured.
        self.assertEqual(self.write(late[:9])["onset_offset_samples"], 0)
        # Never by more than 80 ms: 100 ms late, at a beat of a second.
        later = [note(i * 0.5 + 0.1, i * 0.5 + 0.3, 60) for i in range(12)]
        self.assertEqual(self.write(later, timed_meter(bar_seconds=4.0))["onset_offset_samples"],
                         int(0.08 * SECOND))

    def test_swung_eighths_are_found_and_written_straight(self):
        swung = [note(beat * 0.5 + at, beat * 0.5 + at + 0.1, 60) for beat in range(24) for at in (0, 1 / 3)]
        answer = self.write(swung, timed_meter(bars=7))
        self.assertEqual((answer["feel"], answer["offbeat_position"][:4]), ("swing", "0.66"))
        self.assertEqual(answer["written"][1]["position"], length(1, 8))
        self.assertTrue(all(w["duration"] == length(1, 8) for w in answer["written"][:-1]))
        # Anchor: offbeats at 0.6 of the beat are a straight lilt, not swing.
        lilt = [note(beat * 0.5 + at, beat * 0.5 + at + 0.1, 60) for beat in range(24) for at in (0, 0.3)]
        answer = self.write(lilt, timed_meter(bars=7))
        self.assertEqual(answer["feel"], "straight")
        self.assertLess(Fraction(answer["offbeat_position"]), Fraction("0.62"))

    def test_a_long_silence_at_a_phrase_boundary_is_a_rest(self):
        sung = [note(0, 0.5, 60), note(2.0, 2.4, 62)]
        # Anchor: the silence, 1.5 s, is three beats of half a second.
        self.assertGreaterEqual(sung[1]["onset_sample"] - sung[0]["offset_sample"], 3 * SECOND // 2)
        line_end = [[{"line": 1, "start_sample": 0, "end_sample": int(0.5 * SECOND)},
                     {"line": 2, "start_sample": 2 * SECOND, "end_sample": 3 * SECOND}]]
        answer = self.write(sung, lyrics=line_end)
        self.assertEqual(kinds(answer)[:2], [("chord", 1, 4, None, ()), ("rest", 3, 4, "line_end", ())])
        # No lyrics cover it: a breath, which is how a bass line's phrases end.
        self.assertEqual(kinds(self.write(sung))[1][3], "breath")
        between_words = [[{"line": 1, "start_sample": 0, "end_sample": 3 * SECOND, "words": [
            {"word": 1, "start_sample": 0, "end_sample": int(0.4 * SECOND)},
            {"word": 2, "start_sample": 2 * SECOND, "end_sample": int(2.4 * SECOND)}]}]]
        self.assertEqual(kinds(self.write(sung, lyrics=between_words))[1][3], "breath")
        # A breath of half a beat and 0.2 s is a rest too; a shorter one is not.
        breath = [note(0, 0.7, 60), note(1.0, 1.4, 62)]
        self.assertEqual(kinds(self.write(breath))[:2], [("chord", 3, 8, None, ()), ("rest", 1, 8, "breath", ())])
        short = [note(0, 0.35, 60), note(0.5, 0.9, 62)]       # 0.15 s of silence
        self.assertNotIn("rest", [w["kind"] for w in self.write(short)["written"]])

    def test_no_rest_inside_a_word(self):
        sung = [note(0, 0.4, 60), note(2.0, 2.4, 62)]
        held_word = [[{"line": 1, "start_sample": 0, "end_sample": 3 * SECOND, "words": [
            {"word": 1, "start_sample": 0, "end_sample": int(2.4 * SECOND)}]}]]
        answer = self.write(sung, lyrics=held_word)
        self.assertEqual(kinds(answer)[0], ("chord", 1, 1, None, ()))
        self.assertNotIn("rest", [w["kind"] for w in answer["written"]])
        unwordy_line = [[{"line": 1, "start_sample": 0, "end_sample": 3 * SECOND}]]
        self.assertNotIn("rest", [w["kind"] for w in self.write(sung, lyrics=unwordy_line)["written"]])

    def test_staccato_only_for_a_clearly_detached_note(self):
        held_word = [[{"line": 1, "start_sample": 0, "end_sample": 3 * SECOND, "words": [
            {"word": 1, "start_sample": 0, "end_sample": int(1.5 * SECOND)}]}]]
        detached = [note(0, 0.1, 60), note(0.5, 1.0, 62)]
        self.assertEqual(kinds(self.write(detached, lyrics=held_word))[0], ("chord", 1, 4, None, ("staccato",)))
        # Anchor: sounding 0.2 s of the beat, a sung note is not staccato.
        sung = [note(0, 0.2, 60), note(0.5, 1.0, 62)]
        self.assertEqual(kinds(self.write(sung, lyrics=held_word))[0], ("chord", 1, 4, None, ()))

    def test_a_held_phrase_end_is_tenuto(self):
        held = [note(0, 1.4, 60), note(3.0, 3.4, 62)]
        self.assertEqual(kinds(self.write(held))[:2], [("chord", 3, 4, None, ("tenuto",)), ("rest", 3, 4, "breath", ())])

    def test_every_syllable_is_sung_on_a_note(self):
        sung = [note(0, 0.9, 60), note(2.0, 2.4, 64)]
        syllables = [{"syllable": 1, "start_sample": 0, "end_sample": int(0.4 * SECOND)},
                     {"syllable": 2, "start_sample": int(0.5 * SECOND), "end_sample": int(0.9 * SECOND)}]
        lyrics = [[{"line": 1, "start_sample": 0, "end_sample": int(2.4 * SECOND), "words": [
            {"word": 1, "start_sample": 0, "end_sample": int(0.9 * SECOND), "syllables": syllables},
            {"word": 2, "start_sample": int(1.25 * SECOND), "end_sample": int(1.5 * SECOND)},
            {"word": 3, "start_sample": 2 * SECOND, "end_sample": int(2.4 * SECOND)}]}]]
        answer = self.write(sung, lyrics=lyrics)
        chords = [w for w in answer["written"] if w["kind"] == "chord"]
        self.assertEqual([c["lyric"] for c in chords], [
            {"line": 1, "word": 1, "syllable": 1}, {"line": 1, "word": 1, "syllable": 2},
            {"line": 1, "word": 2}, {"line": 1, "word": 3}])
        # The held note is split for its second syllable; the word sung where
        # no note sounds gets one at the pitch before it.
        self.assertEqual([(c["notes"], c["pitches"], c.get("from")) for c in chords], [
            ([0], [60], None), ([0], [60], "syllable split"), ([], [60], "syllable inferred"), ([1], [64], None)])
        self.assertEqual(answer["counts"]["syllables"], 4)
        self.assertEqual(answer["counts"]["syllables_on_notes"], 4)
        self.assertEqual((answer["counts"]["syllables_split"], answer["counts"]["syllables_inferred"]), (1, 1))
        # No note inside the meter to sing them on: each is counted unplaced.
        nothing = self.write([note(30, 31, 60)], lyrics=lyrics)
        self.assertEqual((nothing["written"], nothing["counts"]["syllables_unplaced"]), ([], 4))

    def test_a_note_continuing_a_syllable_is_its_melisma(self):
        sung = [note(0, 0.45, 60), note(0.5, 0.95, 62), note(1.0, 1.4, 64)]
        lyrics = [[{"line": 1, "start_sample": 0, "end_sample": int(1.4 * SECOND), "words": [
            {"word": 1, "start_sample": 0, "end_sample": int(0.95 * SECOND)},
            {"word": 2, "start_sample": SECOND, "end_sample": int(1.4 * SECOND)}]}]]
        chords = self.write(sung, lyrics=lyrics)["written"]
        self.assertEqual(chords[0]["lyric"], {"line": 1, "word": 1, "extend": True})
        self.assertTrue(chords[1]["melisma"])
        self.assertNotIn("melisma", chords[2])

    def test_an_octave_error_is_folded_and_the_clef_suggested(self):
        pitches = [60, 62, 64, 62, 76, 64, 62, 60]
        line = [note(i * 0.5, i * 0.5 + 0.45, p) for i, p in enumerate(pitches)]
        answer = self.write(line)
        chord = answer["written"][4]
        self.assertEqual((chord["pitches"], chord["folded"]), ([64], -12))
        self.assertEqual(answer["counts"]["octave_folded"], 1)
        self.assertEqual(answer["clef"], "treble-8vb")
        self.assertEqual(answer["clef_basis"], {"below_treble_staff": "0.625", "above_treble_8vb_staff": "0"})
        high = self.write([note(i * 0.5, i * 0.5 + 0.45, p + 12) for i, p in enumerate(pitches)])
        self.assertEqual(high["clef"], "treble")

    OCTAVE_ERROR = [60, 62, 64, 62, 76, 64, 62, 60]

    def octave_error(self):
        return [note(i * 0.5, i * 0.5 + 0.45, p) for i, p in enumerate(self.OCTAVE_ERROR)]

    def unsung_word(self):
        """A note, then a word sung where no note sounds, then a note."""
        sung = [note(0, 0.9, 60), note(2.0, 2.4, 64)]
        lyrics = [[{"line": 1, "start_sample": 0, "end_sample": int(2.4 * SECOND), "words": [
            {"word": 1, "start_sample": 0, "end_sample": int(0.9 * SECOND)},
            {"word": 2, "start_sample": int(1.25 * SECOND), "end_sample": int(1.5 * SECOND)},
            {"word": 3, "start_sample": 2 * SECOND, "end_sample": int(2.4 * SECOND)}]}]]
        return sung, lyrics

    def test_a_persons_notes_are_never_folded_or_filled(self):
        line = self.octave_error()
        machine = self.write(line, claims=["m"] * len(line), machine_claims={"m"})
        self.assertEqual(machine["counts"]["octave_folded"], 1)
        self.assertEqual(machine["rule"]["switches"], {"fill_syllables": True, "fold_octaves": True})
        person = self.write(line, claims=["p"] * len(line), machine_claims=set())
        self.assertEqual(person["counts"]["octave_folded"], 0)
        self.assertEqual(person["written"][4]["pitches"], [76])
        self.assertNotIn("folded", person["written"][4])
        sung, lyrics = self.unsung_word()
        filled = self.write(sung, lyrics=lyrics, claims=["m", "m"], machine_claims={"m"})
        self.assertEqual(filled["counts"]["syllables_inferred"], 1)
        kept = self.write(sung, lyrics=lyrics, claims=["p", "p"], machine_claims=set())
        self.assertEqual((kept["counts"]["syllables_inferred"], kept["counts"]["syllables_split"],
                          kept["counts"]["syllables_unplaced"]), (0, 0, 1))
        self.assertEqual([w["notes"] for w in kept["written"] if w["kind"] == "chord"], [[0], [1]])

    def test_folding_and_filling_are_switched_by_the_reader(self):
        off = self.write(self.octave_error(), fold_octaves=False)
        self.assertEqual((off["counts"]["octave_folded"], off["written"][4]["pitches"]), (0, [76]))
        self.assertFalse(off["rule"]["switches"]["fold_octaves"])
        sung, lyrics = self.unsung_word()
        unfilled = self.write(sung, lyrics=lyrics, fill_syllables=False)
        self.assertEqual((unfilled["counts"]["syllables_inferred"], unfilled["counts"]["syllables_unplaced"]), (0, 1))
        self.assertTrue(all(w.get("from") is None for w in unfilled["written"]))

    def test_a_bass_line_is_neither_folded_nor_filled_by_default(self):
        bass = self.write(self.octave_error(), line="bass")
        self.assertEqual(bass["rule"]["switches"], {"fill_syllables": False, "fold_octaves": False})
        self.assertEqual(bass["counts"]["octave_folded"], 0)
        sung, lyrics = self.unsung_word()
        self.assertEqual(self.write(sung, lyrics=lyrics, line="bass")["counts"]["syllables_inferred"], 0)
        self.assertEqual(self.write(self.octave_error(), line="bass", fold_octaves=True)["counts"]["octave_folded"], 1)
        with self.assertRaises(NotesError):
            self.write(self.octave_error(), line="drums")

    def test_a_syllable_stating_its_notes_wins_over_the_alignment(self):
        sung = [note(0, 0.45, 60), note(0.5, 0.95, 62), note(1.0, 1.45, 64), note(1.5, 1.95, 65)]
        syllables = [{"syllable": 1, "start_sample": 0, "end_sample": int(0.45 * SECOND)},
                     {"syllable": 2, "start_sample": int(0.5 * SECOND), "end_sample": int(1.95 * SECOND)}]
        lyrics = [[{"line": 1, "start_sample": 0, "end_sample": 2 * SECOND, "words": [
            {"word": 1, "start_sample": 0, "end_sample": int(1.95 * SECOND), "syllables": syllables}]}]]
        claims = ["v"] * len(sung)

        def lyric_pitches(answer):
            return [(w.get("lyric", {}).get("syllable"), w.get("melisma", False), w["pitches"])
                    for w in answer["written"] if w["kind"] == "chord"]

        # Anchor: by timing alone the second syllable lands on the second note.
        aligned = self.write(sung, lyrics=lyrics, claims=claims)
        self.assertEqual(lyric_pitches(aligned)[1], (2, False, [62]))
        # Stated: the first syllable is carried by the first two notes, the
        # second by the last two.
        syllables[0]["carried_by"] = {"notes": "v", "onsets": [0, int(0.5 * SECOND)]}
        syllables[1]["carried_by"] = {"notes": "v", "onsets": [SECOND, int(1.5 * SECOND)]}
        stated = self.write(sung, lyrics=lyrics, claims=claims)
        self.assertEqual(lyric_pitches(stated), [(1, False, [60]), (None, True, [62]), (2, False, [64]),
                                                 (None, True, [65])])
        self.assertTrue(stated["written"][0]["lyric"]["extend"])
        # A statement about another notes claim leaves the alignment to place it.
        for syllable in syllables:
            syllable["carried_by"]["notes"] = "another"
        self.assertEqual(lyric_pitches(self.write(sung, lyrics=lyrics, claims=claims))[1], (2, False, [62]))

    def test_notes_starting_together_are_a_chord_and_written_marks_stay(self):
        sung = [note(0, 0.5, 48), note(0.01, 0.5, 55, articulation=["accent"]), note(0.5, 1, 52)]
        answer = self.write(sung)
        self.assertEqual(answer["written"][0]["pitches"], [48, 55])
        self.assertEqual(answer["written"][0]["notes"], [0, 1])
        self.assertEqual(answer["written"][0]["articulation"], ["accent"])

    def test_outside_notes_are_counted(self):
        sung = [note(0.01, 0.5, 60), note(0.49, 1, 62), note(30, 31, 64)]
        answer = self.write(sung)
        self.assertEqual([w["position"] for w in answer["written"]], [length(0, 1), length(1, 4)])
        self.assertEqual(answer["left_out"], 1)                 # past the meter's last bar

    def test_compound_bars_and_a_beats_only_layer(self):
        compound = timed_meter(numerator=6, denominator=8, grouping=[3, 3])
        answer = self.write([note(0, 0.9, 60), note(1.0, 2.0, 62)], compound)
        self.assertEqual(answer["written"][0]["duration"], length(3, 8))
        beats = {"layers": [{"beats": [int(i * 0.5 * SECOND) for i in range(16)]}]}
        answer = self.write([note(0, 0.5, 60), note(0.5, 1, 62)], beats)
        self.assertEqual(answer["written"][0]["duration"], length(1, 4))
        self.assertIsNone(answer["written"][0]["bar"])

    def test_the_rule_is_named_by_version(self):
        self.assertEqual(set(RHYTHM_RULES), {"2"})
        rule = self.write([note(0, 1, 60)])["rule"]
        self.assertEqual(rule["version"], "2")
        self.assertEqual((rule["parameters"]["sigma_beats"], rule["parameters"]["cost_sixteenth"]), ("0.2", "3"))
        with self.assertRaises(NotesError):
            self.write([note(0, 1, 60)], version="1")
        with self.assertRaises(NotesError):
            self.write([note(0, 1, 60)], untimed_meter((4, 4))["payload"])


def needs_fine_values(chord: dict) -> bool:
    """A written note needing a sixteenth, a dot, or a triplet."""
    return chord["position"]["unit"] > 8 or chord["duration"]["unit"] > 8 or chord["duration"]["count"] == 3


class WrittenRhythmOfARecording(unittest.TestCase):
    """An excerpt of one album track as 0.0.10 measured it
    (`fixtures/written-rhythm-excerpt.json`): the vocal and bass notes
    claims' payloads, the beat tracker's bars, and forced-aligned syllables."""

    @classmethod
    def setUpClass(cls):
        cls.excerpt = json.loads((Path(__file__).parent / "fixtures/written-rhythm-excerpt.json").read_text())
        cls.rate = cls.excerpt["vocals"]["sample_rate"]

    def test_the_excerpt_is_a_notes_claims_payload(self):
        for part in ("vocals", "bass"):
            payload = self.excerpt[part]
            self.assertLessEqual({"sample_rate", "part", "notes"}, set(payload))
            for item in payload["notes"]:
                self.assertEqual(set(item), {"onset_sample", "offset_sample", "pitch"})
        self.assertIs(self.excerpt["bass"]["bass"], True)

    def test_the_sung_line_reads_as_written(self):
        payload = self.excerpt["vocals"]
        answer = written_rhythm(payload["notes"], self.excerpt["meter"], sample_rate=self.rate,
                                lyrics=[self.excerpt["lyrics"]])
        chords = [w for w in answer["written"] if w["kind"] == "chord"]
        rests = [w for w in answer["written"] if w["kind"] == "rest"]
        counts = answer["counts"]
        # Every syllable is on a note.
        self.assertGreater(counts["syllables"], 40)
        self.assertEqual((counts["syllables_on_notes"], counts["syllables_unplaced"]), (counts["syllables"], 0))
        self.assertEqual(sum(1 for c in chords if "lyric" in c), counts["syllables"])
        # Anchor: snapped to the nearest sixteenth, as version 1 did, many
        # more onsets fall off the eighth grid than the written line has
        # fine values.
        beats = [b for bar in self.excerpt["meter"]["layers"][0]["bars"] for b in bar["beats"]]
        snapped_off = 0
        for item in payload["notes"]:
            i = max(0, bisect_right(beats, item["onset_sample"]) - 1)
            if i + 1 < len(beats):
                fraction = (item["onset_sample"] - beats[i]) / (beats[i + 1] - beats[i])
                snapped_off += round(fraction * 4) % 2 == 1
        fine = sum(map(needs_fine_values, chords))
        self.assertGreater(snapped_off, 2 * fine)
        self.assertLessEqual(fine, len(chords) // 3)
        # Staccato is rare, and every rest is at a phrase boundary.
        self.assertLessEqual(sum("staccato" in c["articulation"] for c in chords), len(chords) // 10)
        self.assertTrue(rests and all(r["boundary"] in ("line_end", "breath") for r in rests))
        # Written in order, nothing overlapping.
        ends = [Fraction(w["position"]["count"], w["position"]["unit"]) for w in answer["written"]]
        self.assertEqual(ends, sorted(set(ends)))
        for item, following in zip(answer["written"], answer["written"][1:]):
            stop = Fraction(item["position"]["count"], item["position"]["unit"]) + \
                Fraction(item["duration"]["count"], item["duration"]["unit"])
            self.assertLessEqual(stop, Fraction(following["position"]["count"], following["position"]["unit"]))
        self.assertLessEqual(abs(answer["onset_offset_samples"]), 0.08 * self.rate)
        self.assertEqual((answer["clef"], answer["left_out"]), ("treble-8vb", 0))

    def test_the_bass_line_is_written_by_the_same_rule(self):
        payload = self.excerpt["bass"]
        answer = written_rhythm(payload["notes"], self.excerpt["meter"], sample_rate=self.rate)
        chords = [w for w in answer["written"] if w["kind"] == "chord"]
        self.assertEqual(sum(len(c["notes"]) for c in chords), len(payload["notes"]))
        self.assertEqual(answer["counts"]["syllables"], 0)
        self.assertLessEqual(sum(map(needs_fine_values, chords)), len(chords) // 3)


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
