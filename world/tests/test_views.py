"""Named views (ADR 0082): requests, chord names, the correction overlay,
differences, the first current rule for chords, and the lead sheet a viewer
reads.

Run from the repository root: python3 -m unittest world.tests.test_views.
Every example is synthetic (INV-23). Chord claims are checked by the real
`world.harmony` validator against the fixture archive's stream before any
view reads them, and runs, judgments, and memberships copy the fixture
archive's own records in fixture/plan.json, so a fixture cannot drift from
what Door admits.
"""

import copy
import json
import unittest
from pathlib import Path

from world import views
from world.contributions import check_band_membership
from world.harmony import check_chord_stacks, check_chord_stacks_v2, is_bass_only
from world.judgments import check_judgment
from world.tests.test_harmony import RATE, SOURCE, STREAM, fixture_stream, refs, span
from world.views import ViewError, check_request, run_view

ROOT = Path(__file__).resolve().parents[2]
C, Db, D, Eb, E, F, Gb, G, Ab, A, Bb, B = range(12)
SECOND = RATE


def uid(n: int) -> str:
    return f"urn:uuid:00000000-0000-4000-8000-{n:012x}"


def rev(n: int) -> str:
    return "sha256:" + f"{n:02x}" * 32


# A bass stem cited by bank key (ADR 0079), as the bank writes it.
BASS_STEM = {"extractor": "stems", "key": "sha256:" + "ab" * 32, "output": "bass", "sha256": "sha256:" + "cd" * 32}
PERSON = uid(0x501)
BANDMATE = uid(0x502)
BAND = uid(0x503)
WORK = uid(0x504)
RECORDING = uid(0x505)


def plan_record(namespace: str) -> dict:
    plan = json.loads((ROOT / "fixture/plan.json").read_text())
    return next(r for step in plan["steps"] for r in step.get("records", []) if r.get("namespace") == namespace)


class World:
    """Records as World reads them, built from checked payloads."""

    def __init__(self):
        self.records = [{"revision_id": SOURCE["revision_id"], "id": SOURCE["id"], "shape": "artifact",
                         "payload": {}, "refs": []}]
        self.n = 0x10
        self.runs = {}

    def _next(self) -> tuple[str, str]:
        self.n += 1
        return uid(self.n), rev(self.n)

    def run(self, method: str) -> dict:
        if method not in self.runs:
            payload = copy.deepcopy(plan_record("archive.derivation/run")["payload"])
            payload["method"]["id"] = method
            oid, rid = self._next()
            self.records.append({"revision_id": rid, "id": oid, "shape": "claim",
                                 "contract": ["archive.claim/derivation_run", "1"],
                                 "agent": f"pkg:generic/{method}@1", "payload": payload, "refs": []})
            self.runs[method] = {"id": oid, "revision_id": rid}
        return self.runs[method]

    def chords(self, spans: list, *, agent: str | None = None, method: str | None = None,
               extra: tuple = (), **payload) -> dict:
        extra_refs = list(extra)
        if method:
            extra_refs.append(("run", self.run(method)))
            agent = f"pkg:generic/{method}@1"
        oid, rid = self._next()
        claim = {"revision_id": rid, "payload": {"sample_rate": RATE, "spans": spans, **payload},
                 "refs": refs(*extra_refs), "agent": agent}
        # The real rules, before any view reads it. A bass-only reading and a
        # stem citation are ADR 0079's version 2, which version 1 refuses.
        version = "2" if "stem" in payload or any(is_bass_only(r) for s in spans for r in s["readings"]) else "1"
        (check_chord_stacks_v2 if version == "2" else check_chord_stacks)(claim, fixture_stream())
        record = {**claim, "id": oid, "shape": "claim", "contract": ["archive.claim/chord_stacks", version]}
        self.records.append(record)
        return record

    def judge(self, claim: dict, verdict: str, agent: str, version: str = "1", **scope) -> dict:
        payload = {"verdict": verdict, **scope}
        self.assertable(payload, version)
        oid, rid = self._next()
        record = {"revision_id": rid, "id": oid, "shape": "assessment",
                  "contract": ["archive.assessment/judgment", version], "agent": agent, "payload": payload,
                  "refs": [{"role": "assessment_of", "id": claim["id"], "revision_id": claim["revision_id"]}]}
        # ADR 0081's own check, against the claim it judges, so a scope is the shape 0081 defines.
        check_judgment({"payload": payload, "refs": record["refs"], "agent": agent,
                        "contracts": [{"id": "archive.assessment/judgment", "version": version}]},
                       {k: claim[k] for k in ("payload", "refs", "revision_id", "agent")}, fixture_stream())
        self.records.append(record)
        return record

    @staticmethod
    def assertable(payload: dict, version: str) -> None:
        template = plan_record("archive.assessment/judgment")["payload"]
        assert set(template) <= {"verdict", "reason"}
        if version == "1":
            assert set(payload) == {"verdict"}, "a version 1 judgment assesses a whole claim"

    def lead_sheet(self, agent: str, recording: str | None = None, source: str | None = None) -> dict:
        oid, rid = self._next()
        record_refs = [{"role": "work", "id": WORK, "revision_id": rev(0x99)}]
        if source:
            record_refs.append({"role": "source", "id": source, "revision_id": rev(0x95)})
        if recording:
            record_refs.append({"role": "recording", "id": recording, "revision_id": rev(0x98)})
        record = {"revision_id": rid, "id": oid, "shape": "claim", "contract": ["archive.claim/lead_sheet", "1"],
                  "agent": agent, "payload": {}, "refs": record_refs}
        self.records.append(record)
        return record

    def membership(self, member: str) -> dict:
        payload = copy.deepcopy(plan_record("archive.claim/band_membership")["payload"])
        oid, rid = self._next()
        band_ref = {"role": "band", "id": BAND, "revision_id": rev(0x97)}
        member_ref = {"role": "member", "id": member, "revision_id": rev(0x96)}
        claim = {"revision_id": rid, "payload": payload, "refs": [band_ref, member_ref]}
        check_band_membership(claim, {rev(0x97): {"id": BAND, "shape": "entity", "kind": "organisation"},
                                      rev(0x96): {"id": member, "shape": "entity", "kind": "person"}})
        record = {**claim, "id": oid, "shape": "claim", "contract": ["archive.claim/band_membership", "1"],
                  "agent": PERSON}
        self.records.append(record)
        return record


def triad(root: int, quality: str = "maj") -> dict:
    third = 3 if quality == "min" else 4
    return {"bass": root, "tones": sorted({(root + third) % 12, (root + 7) % 12}), "root": root}


def current(world: World, *, person: str = PERSON, **parameters) -> dict:
    request = {"view": "archive.view/chords_current", "version": "1", "subject": {"source": SOURCE},
               "parameters": {"person": person, **parameters}}
    return run_view(request, world.records)["answer"]


def at(answer: dict, second: float) -> dict:
    sample = int(second * SECOND)
    return next(s for s in answer["segments"] if s["start_sample"] <= sample < s["end_sample"])


class Requests(unittest.TestCase):
    def test_registry_and_dispatch_agree(self):
        # Every listed view dispatches to its own handler, and nothing is
        # dispatched that is not listed.
        self.assertEqual(set(views.VIEWS), set(views.HANDLERS))
        self.assertEqual({(v["view"], v["version"]) for v in views.catalogue()}, set(views.VIEWS))
        minimal = {
            "chord_names": {"readings": [triad(C)]},
            "chord_corrections": {"source": SOURCE},
            "chord_differences": {"source": SOURCE},
            "chords_current": {"source": SOURCE},
            "lead_sheet_current": {"work": WORK},
            "folded_chart": {"form_spans": SOURCE},
            # world/record_views.py's views of 0.0.10's other records.
            "standing": {"claim": SOURCE},
            "reach": {"object": WORK},
            "readable_set": {"circle": {"destination": "a circle", "members": [BANDMATE]}},
            "lead_sheet_changes": {"lead_sheet": SOURCE},
            "played_sections": {"form": SOURCE},
            "played_bars": {"meter": SOURCE},
            "tune": {"melody_spans": SOURCE},
            "track_credits": {"recording": RECORDING},
            "written_rhythm": {"notes": SOURCE},
            "bass_line": {"source": RECORDING},
            "may_publish": {"approval": SOURCE},
            "stale_lyric_links": {},
            "lyric_documents": {"work": WORK},
        }
        for key in views.VIEWS:
            reached = []
            original = views.HANDLERS[key]
            views.HANDLERS[key] = lambda world, subject, params, key=key: reached.append(key) or {}
            try:
                answer = run_view({"view": key[0], "version": key[1],
                                   "subject": minimal[key[0].removeprefix("archive.view/")]},
                                  World().records, caller=PERSON)
            finally:
                views.HANDLERS[key] = original
            self.assertEqual(reached, [key])
            self.assertEqual(answer["withheld"], 0)

    def test_unknown_and_unserved_views_are_refused(self):
        with self.assertRaises(ViewError) as caught:
            check_request({"view": "archive.view/chord_current", "version": "1", "subject": {}})
        self.assertEqual(caught.exception.code, "view_unknown")
        with self.assertRaises(ViewError) as caught:
            check_request({"view": "archive.view/chords_current", "version": "2", "subject": {"source": SOURCE}})
        self.assertEqual(caught.exception.code, "view_not_served")

    def test_malformed_requests_fail_closed(self):
        base = {"view": "archive.view/chords_current", "version": "1", "subject": {"source": SOURCE},
                "parameters": {"person": PERSON}}
        bad = [
            {**base, "extra": 1},
            {**base, "version": 1},
            {**base, "subject": {"source": {"id": SOURCE["id"]}}},
            {**base, "subject": {"source": SOURCE, "work": WORK}},
            {**base, "parameters": {"person": "operator"}},
            {**base, "parameters": {"person": PERSON, "machine_order": ["a", "a"]}},
            {**base, "parameters": {"person": PERSON, "machine_order": ["has space"]}},
            {**base, "parameters": {"person": PERSON, "machine_order": "chords-jiang"}},
            {**base, "parameters": {"person": PERSON, "start_sample": 10, "end_sample": 10}},
            {**base, "parameters": {"person": PERSON, "start_sample": True}},
            {**base, "parameters": {"person": PERSON, "weights": {}}},
            {**base, "parameters": []},
            {"view": "archive.view/chord_names", "version": "1", "subject": {"readings": []}},
            {"view": "archive.view/chord_names", "version": "1",
             "subject": {"readings": [{"bass": C, "tones": [G, E]}]}},
            {"view": "archive.view/chord_names", "version": "1",
             "subject": {"readings": [{"bass": 12}]}},
            {"view": "archive.view/chord_differences", "version": "1", "subject": {"source": SOURCE},
             "parameters": {"kinds": ["conflict"]}},
            {"view": "archive.view/lead_sheet_current", "version": "1", "subject": {"work": WORK},
             "parameters": {"by": {"person": PERSON, "band": BAND}}},
        ]
        for request in bad:
            with self.subTest(request=request), self.assertRaises(ViewError) as caught:
                check_request(request, caller=PERSON)
            self.assertEqual(caught.exception.code, "view_request_invalid")

    def test_person_defaults_to_the_caller_and_is_otherwise_required(self):
        request = {"view": "archive.view/chords_current", "version": "1", "subject": {"source": SOURCE}}
        checked = check_request(request, caller=BANDMATE)
        self.assertEqual(checked["parameters"]["person"], BANDMATE)
        self.assertEqual(checked["parameters"]["machine_order"], list(views.DEFAULT_MACHINE_ORDER))
        with self.assertRaises(ViewError):
            check_request(request)

    def test_an_unknown_subject_is_not_an_empty_answer(self):
        world = World()
        with self.assertRaises(ViewError) as caught:
            run_view(
                {"view": "archive.view/chords_current", "version": "1",
                 "subject": {"source": {"id": uid(0x777), "revision_id": rev(0x77)}},
                 "parameters": {"person": PERSON}}, world.records)
        self.assertEqual(caught.exception.code, "view_subject_not_found")
        # A source World knows, with no chord claims, is the complete empty answer.
        self.assertEqual(current(world)["segments"], [])


class Names(unittest.TestCase):
    def ask(self, *readings):
        return run_view({"view": "archive.view/chord_names", "version": "1",
                         "subject": {"readings": list(readings)}}, [])["answer"]["names"]

    def test_transposition_moves_what_is_named(self):
        """ADR 0089: a view parameter, repeated in the answer, never a claim."""
        answer = run_view({"view": "archive.view/chord_names", "version": "1",
                           "subject": {"readings": [{"bass": E, "tones": [C, G], "root": C}]},
                           "parameters": {"transpose": 2}}, [])
        self.assertEqual(answer["parameters"], {"transpose": 2, "key": None})
        symbols = [n["symbol"] for n in answer["answer"]["names"][0]["resolutions"][0]["names"]]
        self.assertEqual(symbols[0], "D/F#")
        self.assertEqual(run_view({"view": "archive.view/chord_names", "version": "1",
                                   "subject": {"readings": [triad(C)]}}, [])["parameters"], {"transpose": 0, "key": None})
        with self.assertRaises(ViewError):
            check_request({"view": "archive.view/chord_names", "version": "1",
                           "subject": {"readings": [triad(C)]}, "parameters": {"transpose": 12}})

    def test_one_stack_has_several_names(self):
        (entry,) = self.ask({"bass": G, "tones": [C, Eb, Bb]})
        symbols = [n["symbol"] for n in entry["resolutions"][0]["names"]]
        self.assertIn("Cm7/G", symbols)
        self.assertIn("Eb6/G", symbols)

    def test_uncertain_tones_give_one_set_of_names_per_resolution(self):
        (entry,) = self.ask({"bass": C, "tones": [E, G], "uncertain": [{"pitch_classes": [Bb, B],
                                                                        "present": "exactly_one"}]})
        self.assertEqual([r["tones"] for r in entry["resolutions"]], [[E, G, Bb], [E, G, B]])

    def test_forms_without_a_stack_have_no_names(self):
        forms = self.ask({"no_chord": True}, {"text": "?"}, {"bass": E, "root": C})
        self.assertEqual([(f["form"], f["resolutions"]) for f in forms],
                         [("no_chord", []), ("text", []), ("bass_only", [])])


class Corrections(unittest.TestCase):
    def overlay(self, world):
        return run_view({"view": "archive.view/chord_corrections", "version": "1",
                         "subject": {"source": SOURCE}}, world.records)["answer"]["segments"]

    def test_a_partial_correction_covers_only_what_it_overlaps(self):
        world = World()
        machine = world.chords([span(0, 4, triad(C)), span(4, 8, triad(F))], method="chords-jiang")
        person = world.chords([span(4, 6, {"bass": F, "tones": [C, E, A]})], agent=PERSON,
                              extra=(("corrects", {"id": machine["id"], "revision_id": machine["revision_id"]}),))
        (segment,) = self.overlay(world)
        self.assertEqual((segment["start_sample"], segment["end_sample"]), (4 * SECOND, 6 * SECOND))
        self.assertEqual(segment["shown"], [{"claim": person["revision_id"], "span": 0}])
        self.assertEqual(segment["corrected"], [{"claim": machine["revision_id"], "span": 1,
                                                 "by": [person["revision_id"]]}])
        self.assertFalse(segment["conflict"])

    def test_two_correctors_that_disagree_are_a_conflict(self):
        world = World()
        machine = world.chords([span(0, 4, triad(C))], method="chords-jiang")
        pin = ("corrects", {"id": machine["id"], "revision_id": machine["revision_id"]})
        world.chords([span(0, 4, triad(A, "min"))], agent=PERSON, extra=(pin,))
        world.chords([span(0, 4, triad(E, "min"))], agent=BANDMATE, extra=(pin,))
        (segment,) = self.overlay(world)
        self.assertTrue(segment["conflict"])
        self.assertEqual(len(segment["shown"]), 2)

    def test_claims_that_correct_each_other_are_a_conflict(self):
        world = World()
        first = world.chords([span(0, 4, triad(C))], agent=PERSON)
        second = world.chords([span(0, 4, triad(A, "min"))], agent=BANDMATE,
                              extra=(("corrects", {"id": first["id"], "revision_id": first["revision_id"]}),))
        first["refs"].append({"role": "corrects", "id": second["id"], "revision_id": second["revision_id"]})
        (segment,) = self.overlay(world)
        self.assertTrue(segment["conflict"])
        self.assertEqual(segment["shown"], [])

    def test_a_withdrawn_correction_corrects_nothing(self):
        world = World()
        machine = world.chords([span(0, 4, triad(C))], method="chords-jiang")
        person = world.chords([span(0, 4, triad(A, "min"))], agent=PERSON,
                              extra=(("corrects", {"id": machine["id"], "revision_id": machine["revision_id"]}),))
        self.assertEqual(len(self.overlay(world)), 1)
        world.judge(person, "withdrawn", PERSON)
        self.assertEqual(self.overlay(world), [])


class Differences(unittest.TestCase):
    def differences(self, world, **parameters):
        return run_view({"view": "archive.view/chord_differences", "version": "1",
                         "subject": {"source": SOURCE}, "parameters": parameters}, world.records)["answer"]

    def test_a_part_against_the_mix_is_a_difference_never_a_conflict(self):
        world = World()
        mix = world.chords([span(0, 4, {"bass": C, "tones": [E, G, B]})], method="chords-jiang")
        keys = world.chords([span(0, 4, {"bass": C, "tones": [D, E, G, A]})], agent=PERSON, part="keys")
        answer = self.differences(world)
        (entry,) = answer["differences"]
        self.assertEqual(entry["kind"], "part_vs_mix")
        self.assertEqual(entry["label"], "difference")
        self.assertEqual(entry["a"]["claim"], keys["revision_id"])
        self.assertEqual(entry["b"]["claim"], mix["revision_id"])
        self.assertEqual((entry["only_in_a"], entry["only_in_b"]), ([D, A], [B]))
        self.assertNotIn("conflict", json.dumps(answer))

    def test_two_parts_and_two_tools(self):
        world = World()
        world.chords([span(0, 2, triad(C))], method="chords-jiang")
        world.chords([span(1, 3, triad(A, "min"))], method="chords-chordino")
        world.chords([span(0, 2, {"bass": C, "tones": [E, G, B]})], agent=PERSON, part="guitar")
        world.chords([span(0, 2, {"bass": C, "tones": [D, E, G, A]})], agent=PERSON, part="keys")
        kinds = sorted(d["kind"] for d in self.differences(world, kinds=["part_vs_part", "tools"])["differences"])
        self.assertEqual(kinds, ["part_vs_part", "tools"])
        tools = next(d for d in self.differences(world, kinds=["tools"])["differences"])
        self.assertEqual((tools["start_sample"], tools["end_sample"]), (1 * SECOND, 2 * SECOND))

    def test_same_tool_agreement_and_text_are_not_differences(self):
        world = World()
        world.chords([span(0, 2, triad(C))], method="chords-jiang")
        world.chords([span(0, 2, triad(C))], method="chords-chordino")
        world.chords([span(0, 2, {"text": "C?"})], agent=PERSON, part="keys")
        answer = self.differences(world)
        self.assertEqual(answer["differences"], [])
        self.assertEqual(answer["not_compared"], 2)


class FirstCurrentRule(unittest.TestCase):
    def two_machines(self):
        world = World()
        jiang = world.chords([span(0, 4, triad(C))], method="chords-jiang")
        chordino = world.chords([span(0, 4, triad(A, "min"))], method="chords-chordino")
        # Anchor: the two machines disagree, or the order would prove nothing.
        self.assertNotEqual(jiang["payload"]["spans"][0]["readings"], chordino["payload"]["spans"][0]["readings"])
        return world, jiang, chordino

    def test_machines_in_the_stated_order(self):
        world, jiang, chordino = self.two_machines()
        chosen = at(current(world), 1)["chosen"]
        self.assertEqual((chosen["claim"], chosen["tier"], chosen["rank"]), (jiang["revision_id"], "machine", 0))
        reversed_order = at(current(world, machine_order=["chords-chordino", "chords-jiang"]), 1)
        self.assertEqual(reversed_order["chosen"]["claim"], chordino["revision_id"])
        self.assertIn({"claim": chordino["revision_id"], "span": 0, "reading": 0}, at(current(world), 1)["others"])

    def test_an_unordered_machine_is_never_chosen(self):
        world, _, _ = self.two_machines()
        segment = at(current(world, machine_order=["something-else"]), 1)
        self.assertNotIn("chosen", segment)
        self.assertNotIn("conflict", segment)
        self.assertEqual(len(segment["others"]), 2)

    def test_the_persons_own_claim_comes_first_and_only_where_it_speaks(self):
        world, jiang, _ = self.two_machines()
        mine = world.chords([span(1, 2, {"bass": C, "tones": [E, G, B]})], agent=PERSON)
        answer = current(world)
        self.assertEqual(at(answer, 1.5)["chosen"]["claim"], mine["revision_id"])
        self.assertEqual(at(answer, 1.5)["chosen"]["tier"], "person")
        self.assertEqual(at(answer, 1.5)["names"][0]["names"][0]["symbol"], "Cmaj7")
        self.assertEqual(at(answer, 0.5)["chosen"]["claim"], jiang["revision_id"])
        self.assertEqual(at(answer, 3)["chosen"]["claim"], jiang["revision_id"])

    def test_another_persons_claim_is_beside_not_chosen(self):
        world, jiang, _ = self.two_machines()
        theirs = world.chords([span(0, 4, triad(F))], agent=BANDMATE)
        segment = at(current(world), 1)
        self.assertEqual(segment["chosen"]["claim"], jiang["revision_id"])
        self.assertIn({"claim": theirs["revision_id"], "span": 0, "reading": 0}, segment["others"])
        # Chosen as the bandmate, their own claim comes first.
        self.assertEqual(at(current(world, person=BANDMATE), 1)["chosen"]["claim"], theirs["revision_id"])

    def test_another_persons_correction_is_not_applied(self):
        world, jiang, _ = self.two_machines()
        world.chords([span(0, 4, triad(F))], agent=BANDMATE,
                     extra=(("corrects", {"id": jiang["id"], "revision_id": jiang["revision_id"]}),))
        self.assertEqual(at(current(world), 1)["chosen"]["claim"], jiang["revision_id"])

    def test_the_persons_correction_replaces_what_it_corrects(self):
        world, jiang, _ = self.two_machines()
        fix = world.chords([span(0, 4, triad(A, "min"))], agent=PERSON,
                           extra=(("corrects", {"id": jiang["id"], "revision_id": jiang["revision_id"]}),))
        segment = at(current(world), 1)
        self.assertEqual(segment["chosen"]["claim"], fix["revision_id"])
        self.assertTrue(any(jiang["revision_id"] in line for line in segment["why"]))

    def test_an_affirmed_reading_is_the_persons_word(self):
        world = World()
        machine = world.chords([span(0, 4, triad(C), triad(A, "min"))], method="chords-chordino")
        world.judge(machine, "affirmed", PERSON, version="2", reading={"span": 0, "reading": 1})
        chosen = at(current(world), 1)["chosen"]
        self.assertEqual((chosen["tier"], chosen["reading"]), ("person", 1))

    def test_a_whole_claim_judgment_of_version_1_still_counts(self):
        world, jiang, chordino = self.two_machines()
        world.judge(chordino, "affirmed", PERSON)
        self.assertEqual(at(current(world), 1)["chosen"]["claim"], chordino["revision_id"])
        world.judge(chordino, "rejected", PERSON)
        self.assertEqual(at(current(world), 1)["chosen"]["claim"], jiang["revision_id"])

    def test_a_rejected_span_falls_to_the_next_machine(self):
        world, jiang, chordino = self.two_machines()
        world.judge(jiang, "rejected", PERSON, version="2", sample_rate=RATE,
                    span={"start_sample": 0, "end_sample": 4 * SECOND})
        self.assertEqual(at(current(world), 1)["chosen"]["claim"], chordino["revision_id"])
        # Another person's rejection does not change this person's view.
        world2, jiang2, _ = self.two_machines()
        world2.judge(jiang2, "rejected", BANDMATE)
        self.assertEqual(at(current(world2), 1)["chosen"]["claim"], jiang2["revision_id"])

    def test_the_narrowest_judgment_stands_and_cuts_the_stretch(self):
        """ADR 0081's standing: "I affirm this claim, except here" is two
        judgments; the rejected two seconds fall to the next machine and the
        rest stays the person's."""
        world, jiang, chordino = self.two_machines()
        world.judge(jiang, "affirmed", PERSON)
        world.judge(jiang, "rejected", PERSON, version="2", sample_rate=RATE,
                    span={"start_sample": 2 * SECOND, "end_sample": 4 * SECOND})
        answer = current(world)
        self.assertEqual((at(answer, 1)["chosen"]["claim"], at(answer, 1)["chosen"]["tier"]),
                         (jiang["revision_id"], "person"))
        self.assertEqual(at(answer, 3)["chosen"]["claim"], chordino["revision_id"])

    def test_withdrawn_only_by_its_own_asserter(self):
        world, jiang, chordino = self.two_machines()
        world.judge(jiang, "withdrawn", PERSON)  # not jiang's asserter: no effect
        self.assertEqual(at(current(world), 1)["chosen"]["claim"], jiang["revision_id"])
        world.judge(jiang, "withdrawn", jiang["agent"])
        self.assertEqual(at(current(world), 1)["chosen"]["claim"], chordino["revision_id"])

    def test_documents_before_machines_and_disagreeing_documents_conflict(self):
        world, _, _ = self.two_machines()
        document = {"id": uid(0x601), "revision_id": rev(0x61)}
        chart = world.chords([span(0, 4, triad(F))], agent=BANDMATE,
                             extra=(("document", document),), alignment={"method": "manual"})
        chosen = at(current(world), 1)["chosen"]
        self.assertEqual((chosen["claim"], chosen["tier"]), (chart["revision_id"], "document"))
        other = {"id": uid(0x602), "revision_id": rev(0x62)}
        world.chords([span(0, 4, triad(G))], agent=BANDMATE, extra=(("document", other),),
                     alignment={"method": "manual"})
        segment = at(current(world), 1)
        self.assertNotIn("chosen", segment)
        self.assertNotIn("stack", segment)
        self.assertEqual(len(segment["conflict"]), 2)

    def test_documents_that_agree_choose_one_and_name_the_other(self):
        world = World()
        for n in (0x603, 0x604):
            world.chords([span(0, 4, triad(F))], agent=BANDMATE,
                         extra=(("document", {"id": uid(n), "revision_id": rev(n % 256)}),),
                         alignment={"method": "manual"})
        segment = at(current(world), 1)
        self.assertIn("chosen", segment)
        self.assertTrue(any(line.startswith("agreeing") for line in segment["why"]))

    def test_a_held_continuation_yields_to_an_asserted_chord_of_its_tier(self):
        world = World()
        held = world.chords([{**span(0, 4, triad(C)), "end": "held"}], agent=BANDMATE,
                            extra=(("document", {"id": uid(0x605), "revision_id": rev(0x65)}),),
                            alignment={"method": "manual"}, hold_rule="until the next name")
        asserted = world.chords([span(2, 4, triad(F))], agent=BANDMATE,
                                extra=(("document", {"id": uid(0x606), "revision_id": rev(0x66)}),),
                                alignment={"method": "manual"})
        answer = current(world)
        self.assertEqual(at(answer, 1)["chosen"]["claim"], held["revision_id"])
        self.assertEqual(at(answer, 3)["chosen"]["claim"], asserted["revision_id"])

    def test_a_transposed_current_chord_keeps_its_reference(self):
        world, jiang, _ = self.two_machines()
        segment = at(current(world, transpose=-3), 1)
        self.assertEqual(segment["chosen"]["claim"], jiang["revision_id"])  # the claim's own reading is named
        self.assertEqual(segment["stack"], triad(A))
        self.assertEqual(segment["names"][0]["names"][0]["symbol"], "A")

    def test_the_bass_stem_is_the_slash_bass_of_a_machine_chord(self):
        world, _, _ = self.two_machines()
        world.chords([span(0, 2, {"bass": E})], method="bass-stem-pitch", stem=BASS_STEM)
        answer = current(world)
        segment = at(answer, 1)
        self.assertEqual(segment["stack"], {"bass": E, "tones": [C, G], "root": C})
        self.assertEqual(segment["names"][0]["names"][0]["symbol"], "C/E")
        self.assertIn("bass_from", segment)
        self.assertEqual(at(answer, 3)["stack"]["bass"], C)

    def test_a_bass_line_not_read_from_the_bass_stem_is_no_slash_bass(self):
        """D4.2's slash bass is the stem bass: a machine's bass-only claim
        from the mix, or from another stem, leaves the chord's bass alone."""
        world, _, _ = self.two_machines()
        world.chords([span(0, 2, {"bass": E})], method="bass-from-mix")
        world.chords([span(0, 2, {"bass": E})], method="bass-from-other", stem={**BASS_STEM, "output": "other"})
        segment = at(current(world), 1)
        self.assertEqual(segment["stack"]["bass"], C)
        self.assertNotIn("bass_from", segment)

    def test_disagreeing_bass_stems_keep_the_machines_own_bass(self):
        world, _, _ = self.two_machines()
        world.chords([span(0, 4, {"bass": E})], method="bass-stem-pitch", stem=BASS_STEM)
        world.chords([span(0, 4, {"bass": G})], method="bass-stem-other", stem=BASS_STEM)
        segment = at(current(world), 1)
        self.assertEqual(segment["stack"]["bass"], C)
        self.assertNotIn("bass_from", segment)

    def test_the_persons_bass_is_never_replaced(self):
        world, _, _ = self.two_machines()
        world.chords([span(0, 4, {"bass": E})], method="bass-stem-pitch")
        world.chords([span(0, 4, triad(C))], agent=PERSON)
        self.assertEqual(at(current(world), 1)["stack"]["bass"], C)

    def test_a_run_that_cannot_be_read_is_reported_not_ranked(self):
        world, jiang, chordino = self.two_machines()
        self.assertEqual(current(world)["unresolved_runs"], [])
        world.records = [r for r in world.records if r["revision_id"] != world.runs["chords-jiang"]["revision_id"]]
        answer = current(world)
        self.assertEqual(answer["unresolved_runs"], [jiang["revision_id"]])
        self.assertEqual(at(answer, 1)["chosen"]["claim"], chordino["revision_id"])

    def test_a_window_clips_the_answer(self):
        world, _, _ = self.two_machines()
        answer = current(world, start_sample=SECOND, end_sample=2 * SECOND)
        self.assertEqual([(s["start_sample"], s["end_sample"]) for s in answer["segments"]],
                         [(SECOND, 2 * SECOND)])

    def test_nothing_is_written(self):
        world, _, _ = self.two_machines()
        before = copy.deepcopy(world.records)
        current(world)
        self.assertEqual(world.records, before)


class LeadSheets(unittest.TestCase):
    def ask(self, world, caller=PERSON, **subject_and_parameters):
        parameters = subject_and_parameters.pop("parameters", {})
        return run_view({"view": "archive.view/lead_sheet_current", "version": "1",
                         "subject": {"work": WORK, **subject_and_parameters}, "parameters": parameters},
                        world.records, caller=caller)["answer"]

    def test_the_viewer_reads_their_own_by_default_with_the_others_beside(self):
        world = World()
        mine = world.lead_sheet(PERSON)
        theirs = world.lead_sheet(BANDMATE)
        answer = self.ask(world)
        self.assertEqual(answer["chosen"]["claim"], mine["revision_id"])
        self.assertEqual(answer["others"], [{"claim": theirs["revision_id"], "agent": BANDMATE}])
        self.assertEqual(self.ask(world, caller=BANDMATE)["chosen"]["claim"], theirs["revision_id"])

    def test_the_band_adopted_lead_sheet_names_its_members(self):
        world = World()
        world.lead_sheet(PERSON)
        adopted = world.lead_sheet(BAND)
        world.membership(PERSON)
        world.membership(BANDMATE)
        answer = self.ask(world, parameters={"by": {"band": BAND}})
        self.assertEqual(answer["chosen"]["claim"], adopted["revision_id"])
        self.assertEqual(sorted(m["member"] for m in answer["members"]), sorted([PERSON, BANDMATE]))
        self.assertEqual(len(answer["others"]), 1)

    def test_two_of_ones_own_are_a_conflict_and_a_recording_narrows(self):
        world = World()
        world.lead_sheet(PERSON)
        world.lead_sheet(PERSON)
        for_take = world.lead_sheet(PERSON, recording=RECORDING)
        self.assertEqual(len(self.ask(world)["conflict"]), 2)
        self.assertNotIn("chosen", self.ask(world))
        self.assertEqual(self.ask(world, recording=RECORDING)["chosen"]["claim"], for_take["revision_id"])

    def test_a_lead_sheet_without_a_recording_is_for_its_source(self):
        world = World()
        take, other = uid(0x506), uid(0x507)
        # Anchor: the two takes differ, and neither is the recording Entity.
        self.assertEqual(len({take, other, RECORDING}), 3)
        of_take = world.lead_sheet(PERSON, source=take)
        world.lead_sheet(PERSON, source=other)
        named = world.lead_sheet(PERSON, recording=RECORDING, source=take)
        self.assertEqual(self.ask(world, recording=take)["chosen"]["claim"], of_take["revision_id"])
        self.assertEqual(self.ask(world, recording=RECORDING)["chosen"]["claim"], named["revision_id"])
        # A sheet naming a recording is not also for its source.
        self.assertNotIn(named["revision_id"],
                         [c["claim"] for c in self.ask(world, recording=take).get("conflict", [])])
        self.assertIn("asserted no lead sheet", self.ask(world, recording=uid(0x508))["why"])
        # For the Work alone, the two source-only sheets are both read: a conflict.
        self.assertEqual(len(self.ask(world)["conflict"]), 2)

    def test_withdrawn_and_superseded_lead_sheets_are_not_read(self):
        world = World()
        old = world.lead_sheet(PERSON)
        old["head"] = False
        withdrawn = world.lead_sheet(PERSON)
        world.judge(withdrawn, "withdrawn", PERSON)
        answer = self.ask(world)
        self.assertNotIn("chosen", answer)
        self.assertEqual(answer["others"], [])


if __name__ == "__main__":
    unittest.main()
