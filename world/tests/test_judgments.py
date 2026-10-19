"""Scoped judgments and span annotations (ADR 0081): the payload and
reference rules, version 1 judgments unchanged, and the standing view.

Run from the repository root: python3 -m unittest world.tests.test_judgments.
The module needs only the standard library. Every example is synthetic
(INV-23): the judged claims, the stream, the sequence, and the version 1
judgment are the fixture archive's own records from fixture/plan.json, each
given an identity here as Door would give it one, so a rule that disagrees
with a real record's shape fails here first.
"""

import copy
import json
import unittest
from pathlib import Path

from world.judgments import (
    JUDGMENT_V1,
    JUDGMENT_V2,
    SPAN_ANNOTATION,
    AnnotationError,
    JudgmentError,
    Scope,
    check_judgment,
    check_span_annotation,
    standing,
)

ROOT = Path(__file__).resolve().parents[2]
PERSON = "urn:uuid:00000000-0000-4000-8000-0000000000f1"     # a person who judges and marks
BANDMATE = "urn:uuid:00000000-0000-4000-8000-0000000000f2"   # another person


def fixture_records() -> dict[str, dict]:
    """The fixture plan's records by key, each given an object id and a
    revision id, with every `pin` resolved to an exact reference."""
    plan = json.loads((ROOT / "fixture/plan.json").read_text())
    records = [r for step in plan["steps"] for r in step.get("records", [])]
    ids: dict[str, int] = {}

    def pinned(key: str) -> dict:
        # Records the plan pins but does not list (the operator) get one too.
        n = ids.setdefault(key, len(ids) + 1)
        return {"id": f"urn:uuid:00000000-0000-4000-8000-{n:012x}", "revision_id": "sha256:" + f"{n:064x}"}

    out = {}
    for r in records:
        identity = pinned(r["key"])
        record = {"payload": copy.deepcopy(r.get("payload", {})), "refs": [],
                  "object_id": identity["id"], "revision_id": identity["revision_id"]}
        for ref in r.get("refs", []):
            if "pin" in ref:
                record["refs"].append({"role": ref["role"], **pinned(ref["pin"])})
        if "contracts" in r:
            record["contracts"] = copy.deepcopy(r["contracts"])
        if "agent" in r:
            record["agent"] = r["agent"]
        out[r["key"]] = record
    return out


RECORDS = fixture_records()


def claim(key: str, agent: str | None = None) -> dict:
    """A fixture claim as a judged claim: payload, refs, revision, agent."""
    record = RECORDS[key]
    out = {"payload": record["payload"], "refs": record["refs"], "revision_id": record["revision_id"],
           "contracts": record["contracts"]}
    if agent or "agent" in record:
        out["agent"] = agent or record["agent"]
    return out


def ref_to(key: str, role: str) -> dict:
    record = RECORDS[key]
    return {"role": role, "id": record["object_id"], "revision_id": record["revision_id"]}


STREAM = claim("stream")
CHORDS = claim("chords")                        # a machine's: three spans, the second with two readings
CHART = claim("chords-chart", agent=PERSON)     # a chart's keys part, placed by a person
SEQUENCE = claim("sequence", agent=PERSON)      # three entries about a Work


def judgment(of: str, verdict: str = "affirmed", version: str = "2", agent: str = PERSON, n: int = 1,
             **payload) -> dict:
    return {"payload": {"verdict": verdict, **payload}, "refs": [ref_to(of, "assessment_of")],
            "contracts": [{"id": JUDGMENT_V1[0], "version": version}], "agent": agent,
            "revision_id": "sha256:" + f"{0xa0 + n:02x}" * 32}


def span(start: int, end: int) -> dict:
    return {"sample_rate": 8000, "span": {"start_sample": start, "end_sample": end}}


class VersionOneUnchanged(unittest.TestCase):
    def test_the_fixture_judgment_is_still_valid(self):
        record = copy.deepcopy(RECORDS["judgment"])
        v1 = {"payload": record["payload"], "refs": record["refs"], "contracts": record["contracts"]}
        self.assertEqual(v1["contracts"], [{"id": JUDGMENT_V1[0], "version": "1"}])
        self.assertEqual(check_judgment(v1), Scope("claim"))
        self.assertEqual(check_judgment(v1, claim("title")), Scope("claim"))

    def test_version_one_has_no_scope(self):
        for scope in (span(0, 1000), {"entries": {"first": 0, "last": 0}},
                      {"reading": {"span": 1, "reading": 0}}):
            with self.subTest(scope=scope), self.assertRaises(JudgmentError):
                check_judgment(judgment("chords", version="1", **scope), CHORDS)

    def test_version_one_withdrawal_by_another_agent_is_not_newly_refused(self):
        # ADR 0048 did not check who withdraws; version 1 keeps its meaning.
        self.assertEqual(check_judgment(judgment("chords", "withdrawn", version="1"), CHORDS), Scope("claim"))

    def test_unknown_version_and_missing_citation_are_refused(self):
        with self.assertRaises(JudgmentError):
            check_judgment(judgment("chords", version="3"))
        bare = judgment("chords")
        del bare["contracts"]
        with self.assertRaises(JudgmentError):
            check_judgment(bare)


class ScopedJudgments(unittest.TestCase):
    def test_unscoped_version_two_judges_the_whole_claim(self):
        self.assertEqual(check_judgment(judgment("chords", reason="checked by ear"), CHORDS), Scope("claim"))

    def test_span_of_a_timed_claim(self):
        scope = check_judgment(judgment("chords", "rejected", **span(1000, 2000)), CHORDS, STREAM)
        self.assertEqual(scope, Scope("span", "samples", 1000, 2000))
        # Every timed shape is judged the same way: span suggestions, a span
        # contribution, meter.
        for key in ("suggestions", "contribution-span", "chords-chart"):
            with self.subTest(key=key):
                check_judgment(judgment(key, **span(0, 4000)), claim(key), STREAM)

    def test_reading_of_a_span(self):
        scope = check_judgment(judgment("chords", reading={"span": 1, "reading": 1}), CHORDS)
        self.assertEqual(scope, Scope("reading", "samples", 1000, 3000, 1, 1))

    def test_entries_and_a_reading_of_an_entry(self):
        self.assertEqual(check_judgment(judgment("sequence", entries={"first": 1, "last": 2}), SEQUENCE),
                         Scope("entries", "entries", 1, 3))
        self.assertEqual(check_judgment(judgment("sequence", reading={"entry": 2, "reading": 0}), SEQUENCE),
                         Scope("reading", "entries", 2, 3, 2, 0))

    def test_malformed_scopes_are_refused(self):
        cases = {
            "two scopes": dict(**span(0, 10), reading={"span": 0, "reading": 0}),
            "span without its rate": {"span": {"start_sample": 0, "end_sample": 10}},
            "rate without a span": {"sample_rate": 8000},
            "empty span": span(10, 10),
            "backwards span": span(20, 10),
            "negative start": span(-1, 10),
            "float sample": {"sample_rate": 8000, "span": {"start_sample": 0.5, "end_sample": 10}},
            "boolean sample": {"sample_rate": 8000, "span": {"start_sample": False, "end_sample": 10}},
            "extra span field": {"sample_rate": 8000, "span": {"start_sample": 0, "end_sample": 10, "x": 1}},
            "reading with both": {"reading": {"span": 0, "entry": 0, "reading": 0}},
            "reading with neither": {"reading": {"reading": 0}},
            "entries backwards": {"entries": {"first": 2, "last": 1}},
            "unknown field": {"weight": "0.5"},
        }
        for name, payload in cases.items():
            with self.subTest(name), self.assertRaises(JudgmentError):
                check_judgment(judgment("chords", **payload))

    def test_scopes_are_checked_against_the_judged_revision(self):
        cases = {
            "span past the stream": ("chords", span(0, 4001)),
            "rate differs": ("chords", {"sample_rate": 44100, "span": {"start_sample": 0, "end_sample": 10}}),
            "span of an untimed claim": ("sequence", span(0, 10)),
            "span of a title": ("title", span(0, 10)),
            "entries of a timed claim": ("chords", {"entries": {"first": 0, "last": 0}}),
            "entry past the last": ("sequence", {"entries": {"first": 0, "last": 3}}),
            "span index past the last": ("chords", {"reading": {"span": 3, "reading": 0}}),
            "reading the span does not have": ("chords", {"reading": {"span": 0, "reading": 1}}),
            "reading of a claim without readings": ("suggestions", {"reading": {"span": 0, "reading": 0}}),
            "a span's reading of an untimed claim": ("sequence", {"reading": {"span": 0, "reading": 0}}),
        }
        for name, (key, payload) in cases.items():
            judged = SEQUENCE if key == "sequence" else claim(key)
            with self.subTest(name), self.assertRaises(JudgmentError):
                check_judgment(judgment(key, **payload), judged, STREAM)

    def test_a_judgment_given_another_claim_is_refused(self):
        with self.assertRaises(JudgmentError):
            check_judgment(judgment("chords"), CHART)

    def test_withdrawn_is_the_asserters_own(self):
        own = judgment("chords-chart", "withdrawn", **span(0, 2000))
        self.assertEqual(check_judgment(own, CHART).kind, "span")
        with self.assertRaises(JudgmentError):
            check_judgment(judgment("chords-chart", "withdrawn", agent=BANDMATE), CHART)
        with self.assertRaises(JudgmentError):
            check_judgment(judgment("chords", "withdrawn"), CHORDS)

    def test_reference_rules(self):
        two = judgment("chords")
        two["refs"].append(ref_to("chords-chart", "assessment_of"))
        loose = judgment("chords")
        del loose["refs"][0]["revision_id"]
        other_role = judgment("chords")
        other_role["refs"][0]["role"] = "about"
        for name, record in (("two judged", two), ("unpinned", loose), ("unknown role", other_role)):
            with self.subTest(name), self.assertRaises(JudgmentError):
                check_judgment(record)


class Standing(unittest.TestCase):
    def test_no_judgment_is_no_verdict(self):
        result = standing(CHORDS, [], PERSON, at=1500)
        self.assertEqual((result.verdict, result.conflict), (None, False))

    def test_prefer_one_claim_over_another_is_two_judgments(self):
        # "From 0.125 s to 0.25 s I hear the chart's C, not the machine's A":
        # affirm the chart's span, reject the machine's. Nothing ranks the
        # claims; the person said which they hold, where.
        prefer = judgment("chords-chart", **span(1000, 2000), n=1)
        over = judgment("chords", "rejected", **span(1000, 2000), n=2)
        both = [prefer, over]
        self.assertNotEqual(CHART["revision_id"], CHORDS["revision_id"])
        self.assertEqual(standing(CHART, both, PERSON, at=1500).verdict, "affirmed")
        self.assertEqual(standing(CHORDS, both, PERSON, at=1500).verdict, "rejected")
        # Outside the span, the person said nothing of either.
        self.assertIsNone(standing(CHORDS, both, PERSON, at=2500).verdict)
        self.assertIsNone(standing(CHART, both, PERSON, at=500).verdict)
        # And another person's view is untouched by them.
        self.assertIsNone(standing(CHORDS, both, BANDMATE, at=1500).verdict)

    def test_narrower_stands_over_wider(self):
        whole = judgment("chords", n=1)
        part = judgment("chords", "rejected", **span(1000, 3000), n=2)
        inner = judgment("chords", **span(1500, 2000), n=3)
        one = judgment("chords", "rejected", reading={"span": 1, "reading": 0}, n=4)
        js = [whole, part, inner, one]
        self.assertEqual(standing(CHORDS, js, PERSON).verdict, "affirmed")
        self.assertEqual(standing(CHORDS, js, PERSON, at=500).verdict, "affirmed")
        self.assertEqual(standing(CHORDS, js, PERSON, at=1200).verdict, "rejected")
        self.assertEqual(standing(CHORDS, js, PERSON, at=1700).verdict, "affirmed")
        result = standing(CHORDS, js, PERSON, at=1700, reading=0)
        self.assertEqual((result.verdict, result.judgments), ("rejected", (one["revision_id"],)))
        self.assertIn("one reading", result.why)
        # The span's other reading is judged by the span judgments alone.
        self.assertEqual(standing(CHORDS, js, PERSON, at=1700, reading=1).verdict, "affirmed")

    def test_equally_narrow_disagreement_is_a_conflict(self):
        a = judgment("chords", **span(1000, 2000), n=1)
        b = judgment("chords", "rejected", **span(1500, 2500), n=2)
        result = standing(CHORDS, [a, b], PERSON, at=1700)
        self.assertTrue(result.conflict)
        self.assertIsNone(result.verdict)
        self.assertEqual(result.judgments, tuple(sorted((a["revision_id"], b["revision_id"]))))
        # Where only one applies, it stands.
        self.assertEqual(standing(CHORDS, [a, b], PERSON, at=1200).verdict, "affirmed")
        # Agreement over a partial overlap is no conflict.
        c = judgment("chords", **span(1500, 2500), n=3)
        self.assertEqual(standing(CHORDS, [a, c], PERSON, at=1700).verdict, "affirmed")

    def test_entries_of_a_sequence(self):
        js = [judgment("sequence", entries={"first": 0, "last": 1}, n=1),
              judgment("sequence", "rejected", reading={"entry": 1, "reading": 0}, n=2)]
        self.assertEqual(standing(SEQUENCE, js, PERSON, at=0).verdict, "affirmed")
        self.assertEqual(standing(SEQUENCE, js, PERSON, at=1, reading=0).verdict, "rejected")
        self.assertIsNone(standing(SEQUENCE, js, PERSON, at=2).verdict)

    def test_judgments_of_another_revision_are_not_read(self):
        other = judgment("chords-chart", "rejected")
        self.assertIsNone(standing(CHORDS, [other], PERSON, at=1500).verdict)

    def test_a_malformed_judgment_fails_the_view(self):
        bad = judgment("chords", **span(0, 10))
        bad["payload"]["sample_rate"] = 44100
        with self.assertRaises(JudgmentError):
            standing(CHORDS, [bad], PERSON, at=5)


def mark(kind: str = "interesting", agent: str = PERSON, about: tuple[str, ...] = (), **payload) -> dict:
    refs = [ref_to("recording", "source"), ref_to("stream", "stream")] + [ref_to(k, "about") for k in about]
    body = {"kind": kind, "sample_rate": 8000, "span": {"start_sample": 1000, "end_sample": 2000}, **payload}
    return {"payload": body, "refs": refs, "agent": agent,
            "contracts": [{"id": SPAN_ANNOTATION[0], "version": SPAN_ANNOTATION[1]}]}


def entry_mark(kind: str = "unsure", work: bool = True, **payload) -> dict:
    refs = [ref_to("sequence", "sequence")] + ([ref_to("work-a", "work")] if work else [])
    return {"payload": {"kind": kind, "entries": {"first": 0, "last": 1}, **payload}, "refs": refs,
            "agent": PERSON}


class SpanAnnotations(unittest.TestCase):
    def test_a_difference_worth_zooming_into(self):
        # The mix's machine chord and the chart's keys part differ here; the
        # person marks it interesting, about both. It disputes neither.
        record = mark(about=("chords", "chords-chart"))
        found = check_span_annotation(record, STREAM, about={c["revision_id"]: c for c in (CHORDS, CHART)})
        self.assertEqual((found.kind, found.axis, found.start, found.end), ("interesting", "samples", 1000, 2000))
        self.assertEqual(found.about, (CHORDS["revision_id"], CHART["revision_id"]))

    def test_unsure_and_note(self):
        self.assertEqual(check_span_annotation(mark("unsure"), STREAM).kind, "unsure")
        note = mark("note", text="the bass drops out for two bars")
        self.assertEqual(check_span_annotation(note, STREAM, agent_kind="person").kind, "note")

    def test_on_a_sequence_about_a_work(self):
        found = check_span_annotation(entry_mark(), sequence=SEQUENCE)
        self.assertEqual((found.axis, found.start, found.end), ("entries", 0, 2))
        check_span_annotation(entry_mark(work=False), sequence=SEQUENCE)
        # A judgment can scope to the mark's own span: a mark is a timed claim.
        mark_claim = dict(mark(), revision_id="sha256:" + "ee" * 32)
        target = {"role": "assessment_of", "id": "urn:uuid:00000000-0000-4000-8000-0000000000ee",
                  "revision_id": mark_claim["revision_id"]}
        withdraw = {"payload": {"verdict": "withdrawn", **span(1000, 2000)}, "refs": [target],
                    "contracts": [{"id": JUDGMENT_V2[0], "version": "2"}], "agent": PERSON}
        self.assertEqual(check_judgment(withdraw, mark_claim, STREAM).kind, "span")

    def test_malformed_annotations_are_refused(self):
        def without(record: dict, key: str) -> dict:
            out = copy.deepcopy(record)
            del out["payload"][key]
            return out

        base = mark()
        machine = mark(agent="pkg:generic/archive-fixture-detector@1")
        with_run = mark()
        with_run["refs"].append(ref_to("run-harmony", "run"))
        with_document = mark()
        with_document["refs"].append(ref_to("chart", "document"))
        both_frames = mark()
        both_frames["refs"].append(ref_to("sequence", "sequence"))
        no_stream = mark()
        no_stream["refs"] = no_stream["refs"][:1]
        twice = mark(about=("chords", "chords"))
        on_frame = mark(about=("stream",))
        no_agent = mark()
        del no_agent["agent"]
        cases = {
            "unknown kind": mark("conflict"),
            "note without text": mark("note"),
            "text on unsure": mark("unsure", text="hmm"),
            "text too long": mark("note", text="x" * 2001),
            "missing span": without(base, "span"),
            "missing rate": without(base, "sample_rate"),
            "empty span": mark(span={"start_sample": 5, "end_sample": 5}),
            "a program's mark": machine,
            "no agent": no_agent,
            "a run": with_run,
            "a document": with_document,
            "both frames": both_frames,
            "a source without its stream": no_stream,
            "about twice": twice,
            "about its own stream": on_frame,
            "entries on a recording": mark(entries={"first": 0, "last": 0}),
            "too many about": mark(about=("chords",) * 9),
        }
        for name, record in cases.items():
            with self.subTest(name), self.assertRaises(AnnotationError):
                check_span_annotation(record, STREAM)

    def test_checked_against_what_it_pins(self):
        far = mark(span={"start_sample": 3000, "end_sample": 4001})
        rate = mark(sample_rate=44100)
        other_recording = copy.deepcopy(CHORDS)
        other_recording["refs"] = [r for r in other_recording["refs"] if r["role"] != "source"]
        about_other = mark(about=("chords",))
        for name, record, kwargs in (
                ("past the stream", far, {}),
                ("rate differs", rate, {}),
                ("about another recording", about_other, {"about": {CHORDS["revision_id"]: other_recording}}),
                ("not a person", mark(), {"agent_kind": "software"})):
            with self.subTest(name), self.assertRaises(AnnotationError):
                check_span_annotation(record, STREAM, **kwargs)
        cases = {
            "entry past the last": entry_mark(entries={"first": 2, "last": 3}),
            "a work the sequence does not describe": dict(entry_mark(), refs=[
                ref_to("sequence", "sequence"), ref_to("work-b", "work")]),
            "a work without its sequence": dict(entry_mark(), refs=[ref_to("work-a", "work")]),
            "samples on a sequence": entry_mark(sample_rate=8000),
        }
        for name, record in cases.items():
            with self.subTest(name), self.assertRaises(AnnotationError):
                check_span_annotation(record, sequence=SEQUENCE)
        # The pinned sequence must be a chord sequence, not another claim with entries.
        not_a_sequence = dict(copy.deepcopy(SEQUENCE), contracts=CHORDS["contracts"])
        with self.assertRaises(AnnotationError):
            check_span_annotation(entry_mark(), sequence=not_a_sequence)
        with self.assertRaises(AnnotationError):
            check_span_annotation(entry_mark(), sequence=CHORDS)
        # ADR 0079's chord sequence version 2 keeps the entries a mark ranges over; a version 3 is unknown.
        version_2 = copy.deepcopy(SEQUENCE)
        version_2["contracts"] = [{"id": "archive.claim/chord_sequence", "version": "2"}]
        check_span_annotation(entry_mark(), sequence=version_2)
        version_2["contracts"][0]["version"] = "3"
        with self.assertRaises(AnnotationError):
            check_span_annotation(entry_mark(), sequence=version_2)


if __name__ == "__main__":
    unittest.main()
