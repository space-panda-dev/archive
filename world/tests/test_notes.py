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
    """The operator's case: short sung notes with silences between them are
    dotted eighths and sixteenths marked staccato, not note, rest, note."""

    def test_short_notes_fill_to_the_next_onset_and_are_staccato(self):
        sung = [note(0, 0.15, 60), note(0.375, 0.42, 62), note(0.5, 0.7, 64), note(0.75, 1.0, 65)]
        answer = written_rhythm(sung, timed_meter())
        # Dotted eighth, sixteenth, eighth, then the last note's own length.
        self.assertEqual(kinds(answer), [
            ("chord", 3, 16, None, ("staccato",)), ("chord", 1, 16, None, ("staccato",)),
            ("chord", 1, 8, None, ()), ("chord", 1, 8, None, ())])
        self.assertNotIn("rest", [w["kind"] for w in answer["written"]])
        self.assertEqual(answer["rule"], {"version": "1", "divisions": 4, "staccato_below": "0.6",
                                          "rest_beats": "1"})
        self.assertEqual(answer["written"][1]["bar"], 0)
        self.assertEqual(answer["written"][1]["at"], {"count": 3, "unit": 16})
        self.assertEqual(answer["written"][0]["sounded_share"], "0.4")

    def test_a_long_silence_at_a_phrase_boundary_is_a_rest(self):
        sung = [note(0, 0.5, 60), note(2.0, 2.4, 62)]
        # Anchor: the silence, 1.5 s, is three beats of half a second.
        self.assertGreaterEqual(sung[1]["onset_sample"] - sung[0]["offset_sample"], 3 * SECOND // 2)
        line_end = [[{"line": 1, "start_sample": 0, "end_sample": int(0.5 * SECOND)},
                     {"line": 2, "start_sample": 2 * SECOND, "end_sample": 3 * SECOND}]]
        answer = written_rhythm(sung, timed_meter(), lyrics=line_end)
        self.assertEqual(kinds(answer)[:2], [("chord", 1, 4, None, ()), ("rest", 3, 4, "line_end", ())])
        # No lyrics cover it: a breath.
        self.assertEqual(kinds(written_rhythm(sung, timed_meter()))[1][3], "breath")
        between_words = [[{"line": 1, "start_sample": 0, "end_sample": 3 * SECOND, "words": [
            {"word": 1, "start_sample": 0, "end_sample": int(0.4 * SECOND)},
            {"word": 2, "start_sample": 2 * SECOND, "end_sample": int(2.4 * SECOND)}]}]]
        self.assertEqual(kinds(written_rhythm(sung, timed_meter(), lyrics=between_words))[1][3], "breath")

    def test_no_rest_inside_a_word_or_for_a_short_silence(self):
        sung = [note(0, 0.4, 60), note(2.0, 2.4, 62)]
        held_word = [[{"line": 1, "start_sample": 0, "end_sample": 3 * SECOND, "words": [
            {"word": 1, "start_sample": 0, "end_sample": int(2.4 * SECOND)}]}]]
        answer = written_rhythm(sung, timed_meter(), lyrics=held_word)
        self.assertEqual(kinds(answer)[0], ("chord", 1, 1, None, ("staccato",)))
        self.assertNotIn("rest", [w["kind"] for w in answer["written"]])
        unwordy_line = [[{"line": 1, "start_sample": 0, "end_sample": 3 * SECOND}]]
        self.assertNotIn("rest", [w["kind"] for w in written_rhythm(sung, timed_meter(),
                                                                    lyrics=unwordy_line)["written"]])
        short = [note(0, 0.2, 60), note(0.45, 0.9, 62)]       # a silence under one beat
        self.assertNotIn("rest", [w["kind"] for w in written_rhythm(short, timed_meter())["written"]])

    def test_notes_starting_together_are_a_chord_and_written_marks_stay(self):
        sung = [note(0, 0.5, 48), note(0, 0.5, 55, articulation=["accent"]), note(0.5, 1, 52)]
        answer = written_rhythm(sung, timed_meter())
        self.assertEqual(answer["written"][0]["pitches"], [48, 55])
        self.assertEqual(answer["written"][0]["notes"], [0, 1])
        self.assertEqual(answer["written"][0]["articulation"], ["accent"])

    def test_onsets_snap_to_the_grid_and_outside_notes_are_counted(self):
        sung = [note(0.01, 0.5, 60), note(0.49, 1, 62), note(30, 31, 64)]
        answer = written_rhythm(sung, timed_meter())
        self.assertEqual([w["position"] for w in answer["written"]], [length(0, 1), length(1, 4)])
        self.assertEqual(answer["left_out"], 1)                 # past the meter's last bar

    def test_compound_bars_and_a_beats_only_layer(self):
        compound = timed_meter(numerator=6, denominator=8, grouping=[3, 3])
        answer = written_rhythm([note(0, 0.9, 60), note(1.0, 2.0, 62)], compound)
        self.assertEqual(answer["written"][0]["duration"], length(3, 8))
        beats = {"layers": [{"beats": [int(i * 0.5 * SECOND) for i in range(16)]}]}
        answer = written_rhythm([note(0, 0.5, 60), note(0.5, 1, 62)], beats)
        self.assertEqual(answer["written"][0]["duration"], length(1, 4))
        self.assertIsNone(answer["written"][0]["bar"])

    def test_the_rule_is_named_by_version(self):
        self.assertEqual(set(RHYTHM_RULES), {"1"})
        with self.assertRaises(NotesError):
            written_rhythm([note(0, 1, 60)], timed_meter(), version="2")
        with self.assertRaises(NotesError):
            written_rhythm([note(0, 1, 60)], untimed_meter((4, 4))["payload"])


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
