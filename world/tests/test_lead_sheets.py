"""Lead sheets (ADR 0084): the claim that pins the parts it chose, a band's
adopted lead sheet, revisions, what changed since, and the render key.

Run from the repository root: python3 -m unittest world.tests.test_lead_sheets.
The module needs only the standard library. Every example is synthetic
(INV-23): the people, band, Work, and documents are invented. The inputs a
lead sheet pins are made the way their writers make them and checked by
their own modules (world/harmony.py, world/contributions.py) before a lead
sheet pins them, so a fixture cannot encode a shape their rules refuse. The
contracts of ADRs 0079, 0080, 0083, 0085, and 0086 are not yet written; for
those the fixtures hold only the references this record relies on (a
`source` for a timed input, a `work`, a lyric timing's `document`).
"""

import copy
import json
import unittest
from pathlib import Path

from world.contributions import check_band_membership, check_contribution
from world.harmony import check_chord_sequence, check_chord_stacks, check_meter_layers
from world.lead_sheets import (
    BAND_MEMBERSHIP,
    CHORD_SEQUENCE,
    CHORD_STACKS,
    CONTRIBUTION,
    FORM,
    KEY,
    LEAD_SHEET,
    LYRIC_TIMING,
    MELODY,
    METER_LAYERS,
    METER_LAYERS_V2,
    JUDGMENT,
    Change,
    LeadSheetError,
    changes_since,
    check_lead_sheet,
    check_lead_sheet_revision,
    render_key,
    rendered_inputs,
)

ROOT = Path(__file__).resolve().parents[2]
RATE = 8000
SECOND = RATE
GUITAR = "https://musicbrainz.org/instrument/63021302-86cd-4aee-80df-2270d54f4978"
C, Db, D, Eb, E, F, Gb, G, Ab, A, Bb, B = range(12)


def pin(n: int) -> dict:
    return {"id": f"urn:uuid:00000000-0000-4000-8000-{n:012x}", "revision_id": "sha256:" + f"{n:02x}" * 32}


def ref(role: str, value: dict) -> dict:
    return {"role": role, "id": value["id"], "revision_id": value["revision_id"]}


ANN = pin(0xA1)          # a person: the songwriter, who also plays guitar
BO = pin(0xA2)           # a person: a bandmate
BAND = pin(0xA4)         # an organisation: their band
OTHER_BAND = pin(0xA5)
WORK = pin(0xB1)         # a musical_work
WORK_RENAMED = {"id": WORK["id"], "revision_id": "sha256:" + "b9" * 32}  # a later revision of the same Work
OTHER_WORK = pin(0xB2)
RECORDING = pin(0xB3)    # a recording Entity
MIX = pin(0xC1)          # the recording's mix
MIX_STREAM = pin(0xC2)
OTHER_MIX = pin(0xC3)
OTHER_STREAM = pin(0xC4)
LYRICS = pin(0xD1)       # the songwriter's lyric document
OTHER_LYRICS = pin(0xD2)
TAB = pin(0xD3)          # a transcription file giving the guitar's tuning and capo
RUN = pin(0xE1)


def fixture_stream(artifact: dict) -> dict:
    """The fixture archive's own audio stream claim, about `artifact`."""
    plan = json.loads((ROOT / "fixture/plan.json").read_text())
    records = [r for step in plan["steps"] for r in step.get("records", [])]
    record = next(r for r in records if r.get("namespace") == "archive.claim/audio_stream")
    payload = dict(record["payload"])
    assert payload["sample_rate"] == RATE, "the fixture stream's rate changed; update RATE"
    payload["sample_count"] = 240 * SECOND
    return {"payload": payload, "refs": [ref("artifact", artifact), ref("run", RUN)]}


def entity(kind: str) -> dict:
    return {"shape": "entity", "kind": kind}


def admitted(at: dict, contract: tuple, claim: dict) -> dict:
    """A claim as Door would read it back at the revision `at` pins."""
    return {"shape": "claim", "id": at["id"], "contract": contract,
            "payload": claim["payload"], "refs": claim["refs"]}


def stack(bass: int, tones: list[int], root: int | None = None, text: str | None = None) -> dict:
    reading = {"bass": bass, "tones": tones}
    if root is not None:
        reading["root"] = root
    if text:
        reading["text"] = text
    return reading


Cmaj7 = stack(C, [E, G, B], C, "Cmaj7")
C69 = stack(C, [D, E, G, A], C, "C6/9")
Am7 = stack(A, [C, E, G], A, "Am7")


def span(start: float, end: float, *readings: dict) -> dict:
    return {"start_sample": int(start * SECOND), "end_sample": int(end * SECOND), "readings": list(readings)}


# The inputs, each made by its own writer's shape and checked by its own rules.
MACHINE_CHORDS = pin(0x10)
MACHINE_CHORDS_CLAIM = {"payload": {"sample_rate": RATE, "spans": [span(0, 2, Cmaj7), span(2, 4, Am7)]},
                        "refs": [ref("source", MIX), ref("stream", MIX_STREAM), ref("run", RUN)]}
CORRECTION = pin(0x11)
CORRECTION_CLAIM = {"payload": {"sample_rate": RATE, "spans": [span(0, 2, C69)]},
                    "refs": [ref("source", MIX), ref("stream", MIX_STREAM), ref("corrects", MACHINE_CHORDS)]}
OTHER_RECORDING_CHORDS = pin(0x12)
OTHER_RECORDING_CHORDS_CLAIM = {"payload": {"sample_rate": RATE, "spans": [span(0, 2, Cmaj7)]},
                                "refs": [ref("source", OTHER_MIX), ref("stream", OTHER_STREAM), ref("run", RUN)]}
SEQUENCE = pin(0x13)
SEQUENCE_CLAIM = {"payload": {"entries": [
    {"section": "Verse", "locator": {"line": 1, "word": 1}, "readings": [Cmaj7]},
    {"section": "Verse", "locator": {"line": 1, "word": 4}, "readings": [Am7]}]},
    "refs": [ref("work", WORK), ref("document", LYRICS)]}
OTHER_WORK_SEQUENCE = pin(0x14)
OTHER_WORK_SEQUENCE_CLAIM = {"payload": {"entries": [{"readings": [Am7]}]}, "refs": [ref("work", OTHER_WORK)]}
METER = pin(0x15)
METER_CLAIM = {"payload": {"sample_rate": RATE, "layers": [{"role": "written", "bars": [
    {"start_sample": 0, "end_sample": 2 * SECOND, "numerator": 4, "denominator": 4},
    {"start_sample": 2 * SECOND, "end_sample": 4 * SECOND, "numerator": 4, "denominator": 4}]}]},
    "refs": [ref("source", MIX), ref("stream", MIX_STREAM), ref("run", RUN)]}
CREDIT = pin(0x16)
CREDIT_CLAIM = {"payload": {"scope": "work", "role": {"type": "composer"}},
                "refs": [ref("contributor", ANN), ref("work", WORK)]}
ANN_IN_BAND = pin(0x17)
ANN_IN_BAND_CLAIM = {"payload": {"from": "2019"}, "refs": [ref("band", BAND), ref("member", ANN)]}
BO_IN_BAND = pin(0x18)
BO_IN_BAND_CLAIM = {"payload": {"from": "2021-03"}, "refs": [ref("band", BAND), ref("member", BO)]}
BO_IN_OTHER_BAND = pin(0x19)
BO_IN_OTHER_BAND_CLAIM = {"payload": {}, "refs": [ref("band", OTHER_BAND), ref("member", BO)]}
# Contracts of the parallel records: only the references this record reads.
WORK_METER = pin(0x20)       # ADR 0083: untimed bars on the Work
WORK_FORM = pin(0x21)        # ADR 0083: sections, repeats, endings on the Work
WORK_MELODY = pin(0x22)      # ADR 0086: the composition's tune
TAKE_MELODY = pin(0x23)      # ADR 0086: which part carries the tune, per span of the mix
KEY_CLAIM = pin(0x24)        # ADR 0080: a key over the Work's sections
LYRIC_TIMING_CLAIM = pin(0x25)  # ADR 0085: lines of LYRICS placed on the mix
STRAY_TIMING = pin(0x26)     # ADR 0085: lines of another document placed on the mix
ANN_SHEET = pin(0x30)        # Ann's own lead sheet, which the band adopts


class Fixtures(unittest.TestCase):
    """The inputs pass their own contracts' rules, so a lead sheet test never
    rests on an input its own module would refuse."""

    def test_inputs_pass_their_own_rules(self):
        check_chord_stacks(MACHINE_CHORDS_CLAIM, fixture_stream(MIX))
        check_chord_stacks(CORRECTION_CLAIM, fixture_stream(MIX))
        check_chord_stacks(OTHER_RECORDING_CHORDS_CLAIM, fixture_stream(OTHER_MIX))
        check_chord_sequence(SEQUENCE_CLAIM)
        check_chord_sequence(OTHER_WORK_SEQUENCE_CLAIM)
        check_meter_layers(METER_CLAIM, fixture_stream(MIX))
        people = {ANN["revision_id"]: entity("person"), BO["revision_id"]: entity("person"),
                  BAND["revision_id"]: entity("organisation"), OTHER_BAND["revision_id"]: entity("organisation"),
                  WORK["revision_id"]: entity("musical_work")}
        check_contribution(CREDIT_CLAIM, people)
        for membership in (ANN_IN_BAND_CLAIM, BO_IN_BAND_CLAIM, BO_IN_OTHER_BAND_CLAIM):
            check_band_membership(membership, people)


TARGETS = {
    ANN["revision_id"]: entity("person"),
    BO["revision_id"]: entity("person"),
    BAND["revision_id"]: entity("organisation"),
    OTHER_BAND["revision_id"]: entity("organisation"),
    WORK["revision_id"]: entity("musical_work"),
    WORK_RENAMED["revision_id"]: {**entity("musical_work"), "id": WORK["id"]},
    OTHER_WORK["revision_id"]: entity("musical_work"),
    RECORDING["revision_id"]: entity("recording"),
    MIX["revision_id"]: {"shape": "artifact"},
    OTHER_MIX["revision_id"]: {"shape": "artifact"},
    LYRICS["revision_id"]: {"shape": "artifact"},
    OTHER_LYRICS["revision_id"]: {"shape": "artifact"},
    TAB["revision_id"]: {"shape": "artifact"},
    RUN["revision_id"]: {"shape": "claim", "contract": ("archive.claim/derivation_run", "1")},
    MACHINE_CHORDS["revision_id"]: admitted(MACHINE_CHORDS, CHORD_STACKS, MACHINE_CHORDS_CLAIM),
    CORRECTION["revision_id"]: admitted(CORRECTION, CHORD_STACKS, CORRECTION_CLAIM),
    OTHER_RECORDING_CHORDS["revision_id"]: admitted(OTHER_RECORDING_CHORDS, CHORD_STACKS,
                                                    OTHER_RECORDING_CHORDS_CLAIM),
    SEQUENCE["revision_id"]: admitted(SEQUENCE, CHORD_SEQUENCE, SEQUENCE_CLAIM),
    OTHER_WORK_SEQUENCE["revision_id"]: admitted(OTHER_WORK_SEQUENCE, CHORD_SEQUENCE, OTHER_WORK_SEQUENCE_CLAIM),
    METER["revision_id"]: admitted(METER, METER_LAYERS, METER_CLAIM),
    CREDIT["revision_id"]: admitted(CREDIT, CONTRIBUTION, CREDIT_CLAIM),
    ANN_IN_BAND["revision_id"]: admitted(ANN_IN_BAND, BAND_MEMBERSHIP, ANN_IN_BAND_CLAIM),
    BO_IN_BAND["revision_id"]: admitted(BO_IN_BAND, BAND_MEMBERSHIP, BO_IN_BAND_CLAIM),
    BO_IN_OTHER_BAND["revision_id"]: admitted(BO_IN_OTHER_BAND, BAND_MEMBERSHIP, BO_IN_OTHER_BAND_CLAIM),
    WORK_METER["revision_id"]: {"shape": "claim", "contract": METER_LAYERS_V2, "refs": [ref("work", WORK)]},
    WORK_FORM["revision_id"]: {"shape": "claim", "contract": FORM, "refs": [ref("work", WORK)]},
    WORK_MELODY["revision_id"]: {"shape": "claim", "contract": MELODY, "refs": [ref("work", WORK)]},
    TAKE_MELODY["revision_id"]: {"shape": "claim", "contract": MELODY,
                                 "refs": [ref("source", MIX), ref("stream", MIX_STREAM)]},
    KEY_CLAIM["revision_id"]: {"shape": "claim", "contract": KEY, "refs": [ref("work", WORK)]},
    LYRIC_TIMING_CLAIM["revision_id"]: {"shape": "claim", "contract": LYRIC_TIMING,
                                        "refs": [ref("source", MIX), ref("stream", MIX_STREAM),
                                                 ref("document", LYRICS)]},
    STRAY_TIMING["revision_id"]: {"shape": "claim", "contract": LYRIC_TIMING,
                                  "refs": [ref("source", MIX), ref("stream", MIX_STREAM),
                                           ref("document", OTHER_LYRICS)]},
}
AGENTS = {ANN["id"]: "person", BO["id"]: "person", BAND["id"]: "organisation",
          "urn:uuid:00000000-0000-4000-8000-0000000000f1": "software"}


def work_sheet(agent: dict = ANN, n: int = 0x30) -> dict:
    """Ann's lead sheet of her own song, before any recording: her chords over
    her lyrics, her bars, her form, her tune, and her guitar part."""
    return {"revision_id": pin(n)["revision_id"], "agent": agent["id"],
            "payload": {"label": "Full chart", "parts": [
                {"name": "Guitar", "instrument": GUITAR, "tuning": [38, 45, 50, 55, 59, 64], "capo": 2,
                 "document": 0},
                {"name": "Voice"}]},
            "refs": [ref("work", WORK), ref("chords", SEQUENCE), ref("meter", WORK_METER), ref("form", WORK_FORM),
                     ref("key", KEY_CLAIM), ref("lyrics", LYRICS), ref("melody", WORK_MELODY),
                     ref("credits", CREDIT), ref("document", TAB)]}


def take_sheet() -> dict:
    """Bo's lead sheet for one recording: Bo's correction first, the
    machine's chords beside it, the machine's bars, the lyrics as sung."""
    return {"revision_id": pin(0x31)["revision_id"], "agent": BO["id"], "payload": {},
            "refs": [ref("work", WORK), ref("source", MIX), ref("recording", RECORDING),
                     ref("chords", CORRECTION), ref("chords", MACHINE_CHORDS), ref("chords", SEQUENCE),
                     ref("meter", METER), ref("form", WORK_FORM), ref("lyrics", LYRICS),
                     ref("lyric_timing", LYRIC_TIMING_CLAIM), ref("melody", TAKE_MELODY)]}


def band_sheet() -> dict:
    """The band's adopted lead sheet: Ann and Bo adopt Ann's, as it stands."""
    sheet = work_sheet(BAND, 0x32)
    sheet["refs"] += [ref("band", BAND), ref("adopted_by", ANN_IN_BAND), ref("adopted_by", BO_IN_BAND),
                      ref("derived_from", ANN_SHEET)]
    return sheet


TARGETS[ANN_SHEET["revision_id"]] = {"shape": "claim", "id": ANN_SHEET["id"], "contract": LEAD_SHEET,
                                     "payload": work_sheet()["payload"], "refs": work_sheet()["refs"]}


def without(claim: dict, role: str, value: dict | None = None) -> dict:
    claim = copy.deepcopy(claim)
    claim["refs"] = [r for r in claim["refs"]
                     if not (r["role"] == role and (value is None or r["revision_id"] == value["revision_id"]))]
    return claim


def plus(claim: dict, role: str, value: dict) -> dict:
    claim = copy.deepcopy(claim)
    claim["refs"].append(ref(role, value))
    return claim


def with_payload(claim: dict, **fields) -> dict:
    claim = copy.deepcopy(claim)
    claim["payload"].update(fields)
    return claim


def with_part(claim: dict, **fields) -> dict:
    claim = copy.deepcopy(claim)
    claim["payload"]["parts"][0] = {"name": "Guitar", **fields}
    return claim


class LeadSheets(unittest.TestCase):
    def check(self, claim: dict) -> dict:
        return check_lead_sheet(claim, TARGETS, AGENTS)

    def refused(self, claim: dict, message: str) -> None:
        with self.assertRaisesRegex(LeadSheetError, message):
            self.check(claim)

    def test_contract_is_named(self):
        self.assertEqual(LEAD_SHEET, ("archive.claim/lead_sheet", "1"))

    def test_a_person_lead_sheet_of_a_work_alone(self):
        payload = self.check(work_sheet())
        self.assertEqual(payload["parts"][0]["capo"], 2)
        # It pins every input exactly and copies none of them.
        self.assertNotIn("entries", json.dumps(payload))

    def test_a_person_lead_sheet_for_one_recording(self):
        sheet = take_sheet()
        self.check(sheet)
        # The asserter's precedence is the order of the chord references:
        # the correction, then the machine claim it corrects, then the Work's
        # sequence.
        chords = [r["revision_id"] for r in sheet["refs"] if r["role"] == "chords"]
        self.assertEqual(chords, [CORRECTION["revision_id"], MACHINE_CHORDS["revision_id"], SEQUENCE["revision_id"]])

    def test_a_melody_alone_is_a_lead_sheet(self):
        sheet = without(work_sheet(), "chords")
        self.check(sheet)
        self.refused(without(sheet, "melody"), "at least one chord or melody")

    def test_inputs_may_cite_an_earlier_revision_of_the_work(self):
        # The Work was renamed after the sequence was asserted: a new
        # revision of the same object, so the sequence is still about it.
        sheet = without(work_sheet(), "work")
        sheet["refs"].insert(0, ref("work", WORK_RENAMED))
        self.assertNotEqual(WORK_RENAMED["revision_id"], WORK["revision_id"])
        self.check(sheet)

    def test_a_band_adopted_lead_sheet(self):
        sheet = band_sheet()
        # Anchor: the band, not a person, is the asserter.
        self.assertNotEqual(sheet["agent"], work_sheet()["agent"])
        self.assertEqual(AGENTS[sheet["agent"]], "organisation")
        self.check(sheet)
        # Ann's own lead sheet stays beside it, unchanged and valid.
        self.check(work_sheet())

    def test_band_adoption_rules(self):
        sheet = band_sheet()
        self.refused(without(sheet, "adopted_by"), "pins the band and the memberships")
        self.refused(without(sheet, "band"), "pins the band and the memberships")
        self.refused(plus(without(sheet, "adopted_by", BO_IN_BAND), "adopted_by", BO_IN_OTHER_BAND),
                     "membership of another band")
        twice = copy.deepcopy(sheet)
        twice["refs"].append({"role": "adopted_by", "id": ANN_IN_BAND["id"], "revision_id": "sha256:" + "17" * 31 + "aa"})
        TARGETS_TWICE = dict(TARGETS)
        TARGETS_TWICE[twice["refs"][-1]["revision_id"]] = TARGETS[ANN_IN_BAND["revision_id"]]
        with self.assertRaisesRegex(LeadSheetError, "one member's adoption twice"):
            check_lead_sheet(twice, TARGETS_TWICE, AGENTS)
        other_agent = copy.deepcopy(sheet)
        other_agent["refs"] = [r if r["role"] != "band" else ref("band", OTHER_BAND) for r in other_agent["refs"]]
        self.refused(other_agent, "names that band as its agent")
        self.refused(plus(work_sheet(), "band", BAND), "by a person names no band")
        self.refused(plus(work_sheet(), "adopted_by", ANN_IN_BAND), "by a person names no band")
        self.refused(plus(without(sheet, "band"), "band", ANN), "pins the band and the memberships|kind organisation")

    def test_only_a_person_or_a_band_asserts_one(self):
        for agent in ("urn:uuid:00000000-0000-4000-8000-0000000000f1",   # software
                      "urn:uuid:00000000-0000-4000-8000-0000000000f2",   # unknown
                      "pkg:generic/lead-sheet-maker@1", None):
            sheet = work_sheet()
            sheet["agent"] = agent
            self.refused(sheet, "person or a band")
        sheet = work_sheet()
        del sheet["agent"]
        self.refused(sheet, "missing")
        self.refused(plus(work_sheet(), "run", RUN), "role 'run'")

    def test_timed_inputs_count_the_lead_sheets_own_source(self):
        self.refused(plus(work_sheet(), "chords", MACHINE_CHORDS), "a lead sheet of the Work alone pins only untimed")
        self.refused(plus(take_sheet(), "chords", OTHER_RECORDING_CHORDS), "another source")
        self.refused(plus(work_sheet(), "melody", TAKE_MELODY), "Work alone")
        self.refused(plus(work_sheet(), "recording", RECORDING), "recording without the source")

    def test_inputs_are_about_the_same_work(self):
        self.refused(plus(work_sheet(), "chords", OTHER_WORK_SEQUENCE), "another Work")
        moved = without(work_sheet(), "work")
        moved["refs"].insert(0, ref("work", OTHER_WORK))
        self.refused(moved, "another Work")

    def test_each_role_pins_its_own_kind(self):
        self.refused(plus(without(work_sheet(), "meter"), "chords", WORK_METER), "chords 1 must pin a Claim of")
        self.refused(plus(without(take_sheet(), "chords", CORRECTION), "meter", CORRECTION), "meter 1 must pin a Claim of")
        self.refused(plus(without(work_sheet(), "key"), "form", KEY_CLAIM), "form 1 must pin a Claim of")
        self.refused(plus(work_sheet(), "credits", ANN_IN_BAND), "credits 1 must pin a Claim of")
        self.refused(plus(work_sheet(), "derived_from", METER), "derived_from 0 must pin a Claim of")
        self.refused(plus(without(work_sheet(), "lyrics"), "lyrics", OTHER_WORK_SEQUENCE), "lyrics must pin an Artifact")
        self.refused(plus(take_sheet(), "recording", WORK), "recording")
        sheet = without(work_sheet(), "work")
        sheet["refs"].insert(0, ref("work", RECORDING))
        self.refused(sheet, "kind musical_work")
        self.refused(plus(work_sheet(), "lyric_sheet", LYRICS), "role 'lyric_sheet'")

    def test_lyric_timing_places_the_pinned_lyrics(self):
        self.refused(without(take_sheet(), "lyrics"), "lyric timing without the lyric document")
        self.refused(plus(take_sheet(), "lyric_timing", STRAY_TIMING), "exactly the lyric document")
        # A timing that names no document is refused, never taken as agreeing.
        unplaced = pin(0x43)
        targets = dict(TARGETS)
        targets[unplaced["revision_id"]] = {"shape": "claim", "contract": LYRIC_TIMING,
                                            "refs": [ref("source", MIX), ref("stream", MIX_STREAM)]}
        with self.assertRaisesRegex(LeadSheetError, "exactly the lyric document"):
            check_lead_sheet(plus(take_sheet(), "lyric_timing", unplaced), targets, AGENTS)

    def test_credits_are_the_works_own(self):
        recording_credit = pin(0x40)
        work_credit_elsewhere = pin(0x41)
        targets = dict(TARGETS)
        targets[recording_credit["revision_id"]] = {
            "shape": "claim", "contract": CONTRIBUTION, "payload": {"scope": "recording"},
            "refs": [ref("contributor", BO), ref("recording", RECORDING)]}
        targets[work_credit_elsewhere["revision_id"]] = {
            "shape": "claim", "contract": CONTRIBUTION, "payload": {"scope": "work"},
            "refs": [ref("contributor", BO), ref("work", OTHER_WORK)]}
        for credit in (recording_credit, work_credit_elsewhere):
            with self.assertRaisesRegex(LeadSheetError, "work-scope contribution about the same Work"):
                check_lead_sheet(plus(work_sheet(), "credits", credit), targets, AGENTS)

    def test_derived_from_a_lead_sheet_of_the_same_work(self):
        self.check(plus(take_sheet(), "derived_from", ANN_SHEET))
        other = pin(0x42)
        targets = dict(TARGETS)
        targets[other["revision_id"]] = {"shape": "claim", "contract": LEAD_SHEET,
                                         "refs": [ref("work", OTHER_WORK)]}
        with self.assertRaisesRegex(LeadSheetError, "lead sheet of another Work"):
            check_lead_sheet(plus(take_sheet(), "derived_from", other), targets, AGENTS)

    def test_references_pin_exact_committed_revisions(self):
        unpinned = copy.deepcopy(work_sheet())
        del unpinned["refs"][1]["revision_id"]
        self.refused(unpinned, "missing")
        uncommitted = plus(work_sheet(), "melody", pin(0x77))
        self.refused(uncommitted, "not a committed revision")
        twice = plus(work_sheet(), "form", SEQUENCE)
        self.refused(twice, "one revision twice")
        wrong_object = copy.deepcopy(work_sheet())
        wrong_object["refs"][1]["id"] = OTHER_WORK["id"]
        self.refused(wrong_object, "another object")
        self.refused(plus(plus(plus(plus(plus(work_sheet(), "meter", pin(0x50)), "meter", pin(0x51)),
                                    "meter", pin(0x52)), "meter", pin(0x53)), "meter", pin(0x54)),
                     "0 to 4 'meter'")

    def test_payload_is_closed(self):
        self.refused(with_payload(work_sheet(), lyrics="the words themselves"), "unknown \\['lyrics'\\]")
        self.refused(with_payload(work_sheet(), chords=[Cmaj7]), "unknown \\['chords'\\]")
        self.refused(with_payload(work_sheet(), label=""), "label must be text")
        self.refused(with_payload(work_sheet(), label="x" * 201), "label must be text")
        self.refused(with_payload(work_sheet(), parts=[]), "parts must be a list of 1 to 16")

    def test_part_rules(self):
        sheet = work_sheet()
        self.check(with_part(sheet, tuning=[40, 45, 50, 55, 59, 64]))
        # A re-entrant tuning is not ascending, and is allowed.
        self.check(with_part(sheet, tuning=[67, 60, 64, 69], capo=5))
        self.refused(with_part(sheet, capo=2), "capo without the tuning")
        self.refused(with_part(sheet, tuning=[40], capo=0), "capo must be an integer in 1..24")
        self.refused(with_part(sheet, tuning=[40], capo=25), "capo must be an integer in 1..24")
        self.refused(with_part(sheet, tuning=[40, 128]), r"tuning\[1\] must be an integer in 0..127")
        self.refused(with_part(sheet, tuning=[40, True]), r"tuning\[1\]")
        self.refused(with_part(sheet, tuning=[]), "tuning must be a list of 1 to 12")
        self.refused(with_part(sheet, tuning=[40], document=1), "document must be an integer in 0..0")
        self.refused(with_part(sheet, document=0), "document for a tuning it does not give")
        self.refused(with_part(without(sheet, "document"), tuning=[40], document=0), "pins none")
        self.refused(with_part(sheet, instrument="https://example.org/guitar"), "instrument must be")
        self.refused(with_part(sheet, string_count=6), "unknown \\['string_count'\\]")
        dup = copy.deepcopy(sheet)
        dup["payload"]["parts"][1]["name"] = "Guitar"
        self.refused(dup, "one part twice")
        self.refused(with_part(sheet, name="x" * 101), "name must be text")


class Revisions(unittest.TestCase):
    def test_an_update_is_a_new_revision_by_the_same_asserter(self):
        before = work_sheet()
        after = plus(without(before, "chords"), "chords", SEQUENCE)
        after["revision_id"] = "sha256:" + "31" * 32
        check_lead_sheet(after, TARGETS, AGENTS)
        check_lead_sheet_revision(before, after)

    def test_another_asserter_or_subject_is_a_new_lead_sheet(self):
        before = work_sheet()
        by_bo = copy.deepcopy(before)
        by_bo["agent"] = BO["id"]
        with self.assertRaisesRegex(LeadSheetError, "keeps its agent"):
            check_lead_sheet_revision(before, by_bo)
        on_a_recording = plus(before, "source", MIX)
        with self.assertRaisesRegex(LeadSheetError, "keeps its Work and its recording"):
            check_lead_sheet_revision(before, on_a_recording)
        renamed = without(before, "work")
        renamed["refs"].insert(0, ref("work", WORK_RENAMED))
        check_lead_sheet_revision(before, renamed)


class ChangesSince(unittest.TestCase):
    def test_nothing_changed(self):
        self.assertEqual(changes_since(take_sheet(), [take_sheet()]), [])

    def test_a_later_correction_revision_and_judgment_are_reported_and_pins_stay(self):
        sheet = take_sheet()
        pinned_before = copy.deepcopy(sheet["refs"])
        later_correction = {"revision_id": "sha256:" + "60" * 32, "shape": "claim", "supersedes": [],
                            "refs": [ref("corrects", MACHINE_CHORDS)]}
        sequence_v2 = {"revision_id": "sha256:" + "61" * 32, "shape": "claim",
                       "supersedes": [SEQUENCE["revision_id"]], "refs": []}
        sequence_v3 = {"revision_id": "sha256:" + "62" * 32, "shape": "claim",
                       "supersedes": [sequence_v2["revision_id"]], "refs": []}
        rejected = {"revision_id": "sha256:" + "63" * 32, "shape": "assessment", "supersedes": [],
                    "payload": {"verdict": "rejected"}, "refs": [ref("assessment_of", METER)]}
        lineage = {"revision_id": "sha256:" + "64" * 32, "shape": "claim", "supersedes": [ANN_SHEET["revision_id"]],
                   "refs": []}
        changes = changes_since(plus(sheet, "derived_from", ANN_SHEET),
                                [later_correction, sequence_v3, sequence_v2, rejected, lineage])
        self.assertIn(Change("chords", MACHINE_CHORDS["revision_id"], "corrected", later_correction["revision_id"]),
                      changes)
        self.assertIn(Change("chords", SEQUENCE["revision_id"], "revised", sequence_v2["revision_id"]), changes)
        self.assertIn(Change("chords", SEQUENCE["revision_id"], "revised", sequence_v3["revision_id"]), changes)
        self.assertIn(Change("meter", METER["revision_id"], "judged", rejected["revision_id"], "rejected"), changes)
        # Lineage is not an input: a revised lead sheet it started from is not
        # a change to this one.
        self.assertFalse([c for c in changes if c.role == "derived_from"])
        self.assertEqual(len(changes), 4)
        # The view moves nothing: the lead sheet still pins what it chose.
        self.assertEqual(sheet["refs"], pinned_before)
        check_lead_sheet(sheet, TARGETS, AGENTS)

    def test_judgment_contract_is_the_assessment_one(self):
        self.assertEqual(JUDGMENT, ("archive.assessment/judgment", "1"))


class Renders(unittest.TestCase):
    def test_rendered_inputs_are_what_a_render_shows(self):
        roles = {r["role"] for r in rendered_inputs(band_sheet())}
        self.assertIn("lyrics", roles)
        self.assertIn("credits", roles)
        self.assertIn("work", roles)
        for hidden in ("derived_from", "band", "adopted_by", "document"):
            self.assertNotIn(hidden, roles)

    def test_render_key_is_fixed_by_the_revision_format_renderer_and_view(self):
        revision = work_sheet()["revision_id"]
        key = render_key(revision, "pdf", "leadsheet-pdf@1.0.0", {"transpose": -2, "paper": "a4"})
        self.assertEqual(key, render_key(revision, "pdf", "leadsheet-pdf@1.0.0", {"paper": "a4", "transpose": -2}))
        self.assertTrue(key.startswith("sha256/30/30/" + "30" * 32 + "/lead-sheet.pdf.leadsheet-pdf@1.0.0."))
        others = {
            render_key(revision, "pdf", "leadsheet-pdf@1.0.0"),
            render_key(revision, "musicxml", "leadsheet-pdf@1.0.0", {"transpose": -2, "paper": "a4"}),
            render_key(revision, "pdf", "leadsheet-pdf@1.0.1", {"transpose": -2, "paper": "a4"}),
            render_key(band_sheet()["revision_id"], "pdf", "leadsheet-pdf@1.0.0", {"transpose": -2, "paper": "a4"}),
        }
        self.assertEqual(len(others), 4)
        self.assertNotIn(key, others)

    def test_render_key_refuses_what_names_no_exact_render(self):
        revision = work_sheet()["revision_id"]
        with self.assertRaisesRegex(LeadSheetError, "exact lead sheet revision"):
            render_key("latest", "pdf", "leadsheet-pdf@1")
        with self.assertRaisesRegex(LeadSheetError, "format"):
            render_key(revision, "docx", "leadsheet-pdf@1")
        with self.assertRaisesRegex(LeadSheetError, "name@version"):
            render_key(revision, "pdf", "leadsheet-pdf")


if __name__ == "__main__":
    unittest.main()
