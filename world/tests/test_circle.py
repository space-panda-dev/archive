"""Bandmates, the relay's circle, and co-written Works (ADR 0088): the
circle configuration, a member's drafts, the work origin and sharing consent
contracts, the sharing gate, the readable set, and the client's commit rule.

Run from the repository root: python3 -m unittest world.tests.test_circle.
The module needs only the standard library. Every example is synthetic
(INV-23): the people, band, and Works are invented. Publication approvals and
withdrawals are copied from the fixture archive's own records in
fixture/plan.json, and one test reads the real Door export in
world/tests/fixtures.
"""

import copy
import json
import unittest
from pathlib import Path

from world.circle import (
    BAND_MEMBERSHIP,
    LEAD_SHEET,
    MEMBER_CONTRACTS,
    PUBLICATION_APPROVAL,
    PUBLICATION_WITHDRAWAL,
    SHARING_CONSENT,
    WORK_ORIGIN,
    Circle,
    CircleError,
    check_circle_config,
    check_member_draft,
    check_route,
    check_sharing_consent,
    check_work_origin,
    client_may_commit,
    readable_by,
    readable_set,
    sharing_gate,
    capacity,
    works_touched,
    writers,
)

ROOT = Path(__file__).resolve().parents[2]


def oid(n: int) -> str:
    return f"urn:uuid:00000000-0000-4000-8000-{n:012x}"


def rid(n: int) -> str:
    return "sha256:" + f"{n:04x}" * 16


# People and things. Each object's first revision is rid(n).
OPERATOR, MATE, MATE2, FRIEND, STRANGER = (oid(n) for n in (0x1, 0x2, 0x3, 0x4, 0x5))
BAND = oid(0x10)
WORK, OWN_WORK = oid(0x20), oid(0x21)
MIX, LYRICS = oid(0x30), oid(0x31)
DESTINATION = "band relay"
PUBLIC = "public site"


def record(n: int, shape: str, *, contract=None, agent=OPERATOR, payload=None, refs=(), obj=None,
           supersedes=()) -> dict:
    r = {"revision_id": rid(n), "object_id": obj or oid(n), "shape": shape, "agent": agent,
         "payload": payload or {}, "refs": list(refs), "supersedes": list(supersedes)}
    if contract:
        r["contracts"] = [{"id": contract[0], "version": contract[1]}]
    return r


def ref(role: str, target: dict) -> dict:
    return {"role": role, "id": target["object_id"], "revision_id": target["revision_id"]}


def entity(n: int, kind: str) -> dict:
    return record(n, "entity", payload={"kind": kind}, obj=oid(n))


PEOPLE = {p: entity(n, "person") for p, n in ((OPERATOR, 0x1), (MATE, 0x2), (MATE2, 0x3), (FRIEND, 0x4),
                                              (STRANGER, 0x5))}
BAND_E = entity(0x10, "organisation")
WORK_E = entity(0x20, "musical_work")
OWN_WORK_E = entity(0x21, "musical_work")
MIX_A = record(0x30, "artifact", payload={"content": {}})
LYRICS_A = record(0x31, "artifact", payload={"content": {}})

CHORDS = ("archive.claim/chord_stacks", "1")
JUDGMENT = ("archive.assessment/judgment", "1")
SELECTION = ("archive.claim/audio_selection", "2")
OCCURRENCE = ("archive.claim/song_occurrence", "2")
PERF_WORK = ("archive.claim/performance_work", "3")


def plan_record(namespace: str) -> dict:
    """A record from the fixture archive's plan, as Door admitted it."""
    plan = json.loads((ROOT / "fixture/plan.json").read_text())
    return next(r for step in plan["steps"] for r in step.get("records", [])
                if r.get("namespace") == namespace)


def approval(n: int, covers: list[dict], destination: str = DESTINATION, decision: str = "approve",
             authorizer: str = OPERATOR) -> dict:
    """A publication approval shaped exactly as the fixture archive's."""
    real = plan_record("archive.transition/publication_approval")
    assert set(real["payload"]) == {"destination", "decision"}
    assert {r["role"] for r in real["refs"]} == {"authorizer", "covers"}
    payload = dict(real["payload"], destination=destination, decision=decision)
    return record(n, "claim", contract=PUBLICATION_APPROVAL, payload=payload,
                  refs=[ref("authorizer", PEOPLE[authorizer])] + [ref("covers", c) for c in covers])


def withdrawal(n: int, of: dict) -> dict:
    real = plan_record("archive.transition/publication_withdrawal")
    assert {r["role"] for r in real["refs"]} == {"authorizer", "withdraws"}
    return record(n, "claim", contract=PUBLICATION_WITHDRAWAL, payload=dict(real["payload"]),
                  refs=[ref("authorizer", PEOPLE[OPERATOR]), ref("withdraws", of)])


def origin(n: int, kind: str, writer_ids=(), band: bool = False, agent: str = OPERATOR, work=WORK_E,
           supersedes=()) -> dict:
    refs = [ref("work", work)] + [ref("writer", PEOPLE[w]) for w in writer_ids]
    if band:
        refs.append(ref("band", BAND_E))
    return record(n, "claim", contract=WORK_ORIGIN, agent=agent, payload={"origin": kind}, refs=refs,
                  supersedes=supersedes)


def consent(n: int, of_origin: dict, covers: list[dict], agent: str = MATE, destination: str = PUBLIC,
            decision: str = "approve", supersedes=(), obj=None) -> dict:
    return record(n, "claim", contract=SHARING_CONSENT, agent=agent,
                  payload={"destination": destination, "decision": decision},
                  refs=[ref("origin", of_origin)] + [ref("covers", c) for c in covers],
                  supersedes=supersedes, obj=obj)


def membership(n: int, member: str, agent: str = OPERATOR, **payload) -> dict:
    return record(n, "claim", contract=BAND_MEMBERSHIP, agent=agent, payload=payload,
                  refs=[ref("band", BAND_E), ref("member", PEOPLE[member])])


def lead_sheet(n: int, work=WORK_E, agent=OPERATOR, extra=()) -> dict:
    return record(n, "claim", contract=LEAD_SHEET, agent=agent,
                  refs=[ref("work", work), ref("chords", CHORD_CLAIM), ref("lyrics", LYRICS_A), *extra])


def as_targets(records: list[dict]) -> dict:
    """What Door would find at each revision, for the claim checks."""
    out = {}
    for r in records:
        target = {"id": r["object_id"], "shape": r["shape"], "payload": r["payload"], "refs": r["refs"]}
        if r["shape"] == "entity":
            target["kind"] = r["payload"]["kind"]
        if "contracts" in r:
            target["contract"] = (r["contracts"][0]["id"], r["contracts"][0]["version"])
        out[r["revision_id"]] = target
    return out


CHORD_CLAIM = record(0x40, "claim", contract=CHORDS, refs=[ref("source", MIX_A)])
CORRECTION = record(0x41, "claim", contract=CHORDS, refs=[ref("source", MIX_A), ref("corrects", CHORD_CLAIM)])
BASE = [*PEOPLE.values(), BAND_E, WORK_E, OWN_WORK_E, MIX_A, LYRICS_A, CHORD_CLAIM, CORRECTION]
CIRCLE = Circle(DESTINATION, frozenset({MATE}), BAND)


def index(records: list[dict]) -> dict:
    return {r["revision_id"]: r for r in records}


def everywhere(_object_id: str) -> str:
    return "distribution"


# ---------------------------------------------------------------------------


class CircleConfigTest(unittest.TestCase):
    def test_a_circle_of_members(self):
        circle = check_circle_config({"op": OPERATOR, "mate": MATE},
                                     {"destination": DESTINATION, "members": [MATE], "band": BAND}, OPERATOR)
        self.assertEqual(circle, CIRCLE)
        self.assertEqual(capacity(OPERATOR, OPERATOR, circle), "operator")
        self.assertEqual(capacity(MATE, OPERATOR, circle), "member")

    def test_no_circle_serves_only_the_operator(self):
        self.assertIsNone(check_circle_config({"op": OPERATOR}, None, OPERATOR))
        with self.assertRaises(CircleError) as caught:
            check_circle_config({"op": OPERATOR, "mate": MATE}, None, OPERATOR)
        self.assertEqual(caught.exception.code, "forbidden_identity")

    def test_malformed_circles_are_refused(self):
        callers = {"op": OPERATOR, "mate": MATE}
        for circle in (
            {"destination": DESTINATION, "members": [MATE, MATE2]},           # a member with no login
            {"destination": DESTINATION, "members": [MATE, MATE]},            # repeated
            {"destination": DESTINATION, "members": [MATE, OPERATOR]},        # the operator
            {"destination": DESTINATION, "members": []},
            {"destination": "", "members": [MATE]},
            {"destination": "x" * 201, "members": [MATE]},
            {"destination": DESTINATION, "members": [MATE], "role": "x"},     # unknown field
            {"destination": DESTINATION, "members": ["mate"]},                # not an object id
            {"destination": DESTINATION, "members": [MATE], "band": "band"},
        ):
            with self.subTest(circle=circle), self.assertRaises(CircleError):
                check_circle_config(callers, circle, OPERATOR)
        with self.assertRaises(CircleError):   # a login with no membership
            check_circle_config({**callers, "two": MATE2}, {"destination": DESTINATION, "members": [MATE]}, OPERATOR)

    def test_strangers_and_operator_routes(self):
        with self.assertRaises(CircleError) as caught:
            capacity(STRANGER, OPERATOR, CIRCLE)
        self.assertEqual(caught.exception.code, "forbidden_identity")
        for route in ("/world/query", "/door/transition", "/door/transition-dry-run"):
            check_route(route, "operator")
            with self.subTest(route=route), self.assertRaises(CircleError) as caught:
                check_route(route, "member")
            self.assertEqual(caught.exception.code, "operator_only")
        check_route("/door/admit", "member")
        check_route("/door/admit-dry-run", "member")


class WorkOriginTest(unittest.TestCase):
    targets = as_targets(BASE)

    def check(self, claim):
        return check_work_origin({k: claim[k] for k in ("payload", "refs", "agent")}, self.targets, OPERATOR)

    def test_accepted(self):
        self.check(origin(0x100, "co_written", [OPERATOR, MATE], band=True))
        self.check(origin(0x101, "co_written", [MATE, OPERATOR], agent=MATE))
        self.check(origin(0x102, "own", [], work=OWN_WORK_E))
        self.check(origin(0x103, "own", [OPERATOR], work=OWN_WORK_E))
        self.check(origin(0x104, "others", [STRANGER]))
        self.check(origin(0x105, "others", []))

    def test_refused(self):
        bad = [
            origin(0x110, "co_written", [MATE, MATE2]),             # the operator is not a writer
            origin(0x111, "co_written", [OPERATOR]),                # one writer
            origin(0x112, "own", [OPERATOR], band=True),
            origin(0x113, "own", [MATE]),
            origin(0x114, "others", [OPERATOR]),
            origin(0x115, "others", [], band=True),
            origin(0x116, "shared", [OPERATOR, MATE]),
            origin(0x117, "co_written", [OPERATOR, MATE, MATE]),    # repeated writer
            origin(0x118, "co_written", [OPERATOR, MATE], work=BAND_E),  # not a Work
        ]
        organisation_writer = origin(0x119, "co_written", [OPERATOR, MATE])
        organisation_writer["refs"].append(ref("writer", BAND_E))
        bad.append(organisation_writer)
        two_works = origin(0x11A, "own")
        two_works["refs"].append(ref("work", OWN_WORK_E))
        bad.append(two_works)
        extra = origin(0x11B, "own")
        extra["payload"]["note"] = "x"
        bad.append(extra)
        no_agent = origin(0x11C, "own")
        no_agent["agent"] = "operator"
        bad.append(no_agent)
        unpinned = origin(0x11D, "own")
        unpinned["refs"][0] = {"role": "work", "id": WORK}
        bad.append(unpinned)
        for claim in bad:
            with self.subTest(claim=claim["payload"], refs=[r["role"] for r in claim["refs"]]):
                with self.assertRaises(CircleError):
                    self.check(claim)


class SharingConsentTest(unittest.TestCase):
    co = origin(0x100, "co_written", [OPERATOR, MATE], band=True)
    own = origin(0x101, "own", work=OWN_WORK_E)
    sheet = lead_sheet(0x200)
    targets = as_targets(BASE + [co, own, sheet])

    def check(self, claim):
        return check_sharing_consent({k: claim[k] for k in ("payload", "refs", "agent")}, self.targets, OPERATOR)

    def test_accepted(self):
        self.check(consent(0x300, self.co, [self.sheet]))
        refused = consent(0x301, self.co, [self.sheet], decision="refuse")
        refused["payload"]["reason"] = "Not before the record is out."
        self.check(refused)

    def test_refused(self):
        bad = [
            consent(0x310, self.co, [self.sheet], agent=OPERATOR),   # the operator's is the approval
            consent(0x311, self.co, [self.sheet], agent=MATE2),      # not a writer
            consent(0x312, self.own, [self.sheet], agent=MATE),      # not co-written
            consent(0x313, self.sheet, [self.sheet]),                # not an origin
            consent(0x314, self.co, [self.sheet], decision="maybe"),
            consent(0x315, self.co, []),
            consent(0x316, self.co, [WORK_E]),                       # covers an Entity
            consent(0x317, self.co, [self.sheet], destination=""),
            consent(0x318, self.co, [self.sheet, self.sheet]),       # the same revision twice
        ]
        long_reason = consent(0x319, self.co, [self.sheet])
        long_reason["payload"]["reason"] = "x" * 2001
        bad.append(long_reason)
        many = consent(0x31A, self.co, [self.sheet])
        many["refs"] += [{"role": "covers", "id": oid(0x900 + i), "revision_id": rid(0x900 + i)} for i in range(64)]
        bad.append(many)
        for claim in bad:
            with self.subTest(payload=claim["payload"], agent=claim["agent"]):
                with self.assertRaises(CircleError):
                    self.check(claim)


class SharingGateTest(unittest.TestCase):
    def setUp(self):
        self.co = origin(0x100, "co_written", [OPERATOR, MATE], band=True)
        self.sheet = lead_sheet(0x200)
        self.records = BASE + [self.co, self.sheet]

    def gate(self, records, destination=PUBLIC, cls="open", audience=None, covers=None):
        covers = covers or [self.sheet["revision_id"]]
        return sharing_gate(covers, destination, cls, audience, index(records), OPERATOR)

    def test_anchor_the_writers(self):
        co, named, bands = writers(self.records, WORK, OPERATOR)
        self.assertTrue(co)
        self.assertEqual(named, {OPERATOR, MATE})
        self.assertEqual(bands, {BAND})
        self.assertNotIn(MATE2, named)
        self.assertEqual(works_touched([self.sheet["revision_id"]], index(self.records)), {WORK})

    def test_inside_the_band_the_operator_suffices(self):
        gate = self.gate(self.records, DESTINATION, "circle", {MATE})
        self.assertTrue(gate.allowed, gate)
        self.assertIn("inside the band", gate.why[0])

    def test_a_member_of_the_band_who_is_not_a_writer(self):
        self.assertFalse(self.gate(self.records, DESTINATION, "circle", {MATE, MATE2}).allowed)
        joined = self.records + [membership(0x400, MATE2)]
        self.assertTrue(self.gate(joined, DESTINATION, "circle", {MATE, MATE2}).allowed)
        # Membership the member asserted, or one that ended, does not count.
        self_asserted = self.records + [membership(0x401, MATE2, agent=MATE2)]
        self.assertFalse(self.gate(self_asserted, DESTINATION, "circle", {MATE, MATE2}).allowed)
        ended = self.records + [membership(0x402, MATE2, to="2025")]
        self.assertFalse(self.gate(ended, DESTINATION, "circle", {MATE, MATE2}).allowed)

    def test_beyond_the_band_needs_every_co_writer(self):
        gate = self.gate(self.records)
        self.assertFalse(gate.allowed)
        self.assertEqual(gate.missing, ((MATE, self.sheet["revision_id"]),))
        given = consent(0x300, self.co, [self.sheet], obj=oid(0x300))
        self.assertTrue(self.gate(self.records + [given]).allowed)
        # A circle reaching an outsider is beyond the band too.
        self.assertFalse(self.gate(self.records, DESTINATION, "circle", {MATE, FRIEND}).allowed)
        self.assertTrue(self.gate(self.records + [consent(0x301, self.co, [self.sheet], destination=DESTINATION)],
                                  DESTINATION, "circle", {MATE, FRIEND}).allowed)

    def test_a_consent_counts_only_for_its_destination_revision_and_answer(self):
        other_place = consent(0x310, self.co, [self.sheet], destination="another site")
        self.assertFalse(self.gate(self.records + [other_place]).allowed)
        other_revision = consent(0x311, self.co, [CHORD_CLAIM])
        self.assertFalse(self.gate(self.records + [other_revision]).allowed)
        given = consent(0x312, self.co, [self.sheet], obj=oid(0x312))
        changed_mind = consent(0x313, self.co, [self.sheet], decision="refuse", obj=oid(0x312),
                               supersedes=[ref("supersedes", given)])
        self.assertTrue(self.gate(self.records + [given]).allowed)
        self.assertFalse(self.gate(self.records + [given, changed_mind]).allowed)

    def test_writers_are_the_union_of_governing_origins(self):
        own = origin(0x100, "own")
        mates_own = origin(0x101, "co_written", [OPERATOR, MATE], agent=MATE)
        records = BASE + [own, self.sheet]
        self.assertTrue(self.gate(records).allowed)                   # the operator alone says own
        self.assertFalse(self.gate(records + [mates_own]).allowed)    # the co-writer says otherwise
        strangers = origin(0x102, "co_written", [OPERATOR, FRIEND], agent=STRANGER)
        self.assertTrue(self.gate(records + [strangers]).allowed)     # not a writer: governs nothing

    def test_an_own_work_is_not_gated(self):
        own_sheet = lead_sheet(0x201, work=OWN_WORK_E)
        records = self.records + [origin(0x101, "own", work=OWN_WORK_E), own_sheet]
        self.assertTrue(self.gate(records, covers=[own_sheet["revision_id"]]).allowed)

    def test_a_recording_reaches_its_work_through_the_take(self):
        selection = record(0x500, "claim", contract=SELECTION, refs=[ref("piece", MIX_A)])
        occurrence = record(0x501, "claim", contract=OCCURRENCE, refs=[ref("selection", selection)])
        link = record(0x502, "claim", contract=PERF_WORK, refs=[ref("performance", occurrence), ref("work", WORK_E)])
        records = self.records + [selection, occurrence, link]
        self.assertEqual(works_touched([CHORD_CLAIM["revision_id"]], index(self.records)), frozenset())
        self.assertEqual(works_touched([CHORD_CLAIM["revision_id"]], index(records)), {WORK})
        self.assertFalse(self.gate(records, covers=[CHORD_CLAIM["revision_id"]]).allowed)

    def test_an_unknown_class_is_refused(self):
        with self.assertRaises(CircleError):
            self.gate(self.records, cls="friends")


class ReadableSetTest(unittest.TestCase):
    def setUp(self):
        self.own = origin(0x100, "own", work=OWN_WORK_E)
        self.sheet = lead_sheet(0x200, work=OWN_WORK_E)
        self.disclosed = approval(0x210, [self.sheet])
        self.records = BASE + [self.own, self.sheet, self.disclosed]

    def readable(self, records, circle=CIRCLE, reach=everywhere):
        return readable_set(index(records), OPERATOR, circle, reach)

    def test_nothing_is_readable_without_an_approval(self):
        self.assertEqual(self.readable(BASE + [self.own, self.sheet]).revisions, frozenset())

    def test_an_approval_discloses_what_it_covers_and_pins(self):
        got = self.readable(self.records).revisions
        for r in (self.sheet, CHORD_CLAIM, MIX_A, LYRICS_A, OWN_WORK_E):
            self.assertIn(r["revision_id"], got)
        self.assertNotIn(CORRECTION["revision_id"], got)   # the operator's correction is not inherited
        self.assertNotIn(self.own["revision_id"], got)

    def test_members_claims_follow_what_they_pin(self):
        mate_judges = record(0x220, "assessment", contract=JUDGMENT, agent=MATE,
                             payload={"verdict": "rejected"}, refs=[ref("assessment_of", CHORD_CLAIM)])
        mate2_on_correction = record(0x221, "assessment", contract=JUDGMENT, agent=MATE,
                                     payload={"verdict": "affirmed"}, refs=[ref("assessment_of", CORRECTION)])
        operator_judges = record(0x222, "assessment", contract=JUDGMENT, agent=OPERATOR,
                                 payload={"verdict": "affirmed"}, refs=[ref("assessment_of", CHORD_CLAIM)])
        on_judgment = record(0x223, "assessment", contract=JUDGMENT, agent=MATE,
                             payload={"verdict": "withdrawn"}, refs=[ref("assessment_of", mate_judges)])
        records = self.records + [mate_judges, mate2_on_correction, operator_judges, on_judgment]
        circle_set = self.readable(records)
        self.assertIn(mate_judges["revision_id"], circle_set.revisions)
        self.assertIn(on_judgment["revision_id"], circle_set.revisions)   # found by repeating
        self.assertNotIn(mate2_on_correction["revision_id"], circle_set.revisions)
        self.assertNotIn(operator_judges["revision_id"], circle_set.revisions)
        mine = readable_by(MATE, OPERATOR, circle_set, index(records))
        self.assertIn(mate2_on_correction["revision_id"], mine)            # a member reads their own
        self.assertEqual(readable_by(OPERATOR, OPERATOR, circle_set, index(records)), set(index(records)))

    def test_only_a_live_operator_approval_to_this_circle_discloses(self):
        withdrawn = self.records + [withdrawal(0x230, self.disclosed)]
        self.assertEqual(self.readable(withdrawn).revisions, frozenset())
        for other in (approval(0x231, [self.sheet], decision="hold_back"),
                      approval(0x232, [self.sheet], destination="another circle"),
                      approval(0x233, [self.sheet], authorizer=MATE)):
            records = BASE + [self.own, self.sheet, other]
            with self.subTest(payload=other["payload"]):
                self.assertEqual(self.readable(records).revisions, frozenset())

    def test_sensitivity_withholds(self):
        def no_lyrics(object_id):
            return "private" if object_id == LYRICS else "distribution"
        got = self.readable(self.records, reach=no_lyrics)
        self.assertIn(LYRICS_A["revision_id"], got.withheld)
        self.assertNotIn(LYRICS_A["revision_id"], got.revisions)
        self.assertIn(self.sheet["revision_id"], got.revisions)
        unknown = self.readable(self.records, reach=lambda _o: "unknown")
        self.assertEqual(unknown.revisions, frozenset())                   # fails closed

    def test_a_co_written_work_reaches_an_outsider_only_with_consent(self):
        co = origin(0x101, "co_written", [OPERATOR, MATE], band=True)
        sheet = lead_sheet(0x201)
        disclosed = approval(0x211, [sheet])
        records = BASE + [co, sheet, disclosed]
        self.assertIn(sheet["revision_id"], self.readable(records).revisions)
        wider = Circle(DESTINATION, frozenset({MATE, FRIEND}), BAND)
        self.assertNotEqual(wider.members, CIRCLE.members)
        self.assertNotIn(sheet["revision_id"], self.readable(records, wider).revisions)
        agreed = records + [consent(0x300, co, [sheet], destination=DESTINATION)]
        self.assertIn(sheet["revision_id"], self.readable(agreed, wider).revisions)

    def test_a_real_door_export(self):
        """A dry run against Door's own export: its envelopes parse, and with no
        approval to the circle nothing is readable; an approval covering one of
        its claims discloses that claim and what it pins."""
        export = json.loads((ROOT / "world/tests/fixtures/door-world-chain-export.json").read_text())
        records = index(export["records"])
        self.assertTrue(records)
        operator = next(r["agent"] for r in export["records"]
                        if any(c["id"] == "archive.transition/operator_initialization" for c in r.get("contracts", [])))
        circle = Circle(DESTINATION, frozenset({MATE}), None)
        self.assertEqual(readable_set(records, operator, circle, everywhere).revisions, frozenset())
        claim = next(r for r in export["records"] if r["shape"] == "claim"
                     and not r["contracts"][0]["id"].startswith("archive.transition/"))
        person = next(r for r in export["records"] if r["object_id"] == operator)
        grant = {"revision_id": rid(0xFFF), "object_id": oid(0xFFF), "shape": "claim", "agent": operator,
                 "contracts": [{"id": PUBLICATION_APPROVAL[0], "version": "1"}],
                 "payload": {"destination": DESTINATION, "decision": "approve"},
                 "refs": [ref("authorizer", person), ref("covers", claim)], "supersedes": []}
        got = readable_set({**records, grant["revision_id"]: grant}, operator, circle, everywhere).revisions
        self.assertIn(claim["revision_id"], got)
        for pinned in claim["refs"]:
            if pinned.get("revision_id") in records:
                self.assertIn(pinned["revision_id"], got)


class MemberDraftTest(unittest.TestCase):
    def setUp(self):
        self.sheet = lead_sheet(0x200, work=OWN_WORK_E)
        self.records = BASE + [origin(0x100, "own", work=OWN_WORK_E), self.sheet, approval(0x210, [self.sheet])]
        self.readable = readable_set(index(self.records), OPERATOR, CIRCLE, everywhere).revisions

    def draft(self, contract=JUDGMENT, agent=MATE, shape="assessment", refs=None, supersedes=()):
        return {"shape": shape, "agent": agent, "contracts": [{"id": contract[0], "version": contract[1]}],
                "payload": {}, "refs": refs if refs is not None else [ref("assessment_of", CHORD_CLAIM)],
                "supersedes": list(supersedes)}

    def check(self, draft, records=None, readable=None):
        records = index(records or self.records)
        return check_member_draft(draft, MATE, CIRCLE, readable if readable is not None else self.readable, records)

    def refused(self, draft, code, **kw):
        with self.assertRaises(CircleError) as caught:
            self.check(draft, **kw)
        self.assertEqual(caught.exception.code, code, str(caught.exception))

    def test_a_judgment_of_a_readable_claim(self):
        self.check(self.draft())

    def test_every_member_contract_is_admitted_and_nothing_else(self):
        for contract in sorted(MEMBER_CONTRACTS):
            with self.subTest(contract=contract):
                self.check(self.draft(contract=contract, shape="claim"))
        installed = json.loads((ROOT / "spec/0.0.9/contracts.json").read_text())["contracts"]
        installed = {(c["id"], c["version"]) for c in installed}
        # The member list names installed contracts correctly where 0.0.9 has them.
        for name in ("archive.assessment/judgment", "archive.claim/chord_stacks", "archive.claim/band_membership",
                     "archive.claim/contribution", "archive.claim/selection_title"):
            self.assertIn((name, "1"), installed & MEMBER_CONTRACTS)
        operators = installed - MEMBER_CONTRACTS
        self.assertTrue(any(i.startswith("archive.transition/") for i, _ in operators))
        for contract in sorted(operators) + [("archive.claim/sensitivity", "1"), ("archive.claim/rights", "1"),
                                             WORK_ORIGIN, ("archive.claim/document_source", "1")]:
            with self.subTest(contract=contract):
                self.refused(self.draft(contract=contract, shape="claim"), "operator_only")

    def test_shapes_and_agents(self):
        self.refused(self.draft(shape="entity"), "operator_only")
        self.refused(self.draft(shape="artifact"), "operator_only")
        self.refused(self.draft(agent=MATE2), "agent_mismatch")
        self.refused(self.draft(agent=OPERATOR), "agent_mismatch")
        with self.assertRaises(CircleError):
            self.check(self.draft() | {"contracts": []})

    def test_pins_and_supersessions(self):
        self.refused(self.draft(refs=[ref("assessment_of", CORRECTION)]), "not_disclosed")
        self.refused(self.draft(refs=[{"role": "assessment_of", "id": oid(0x999), "revision_id": rid(0x999)}]),
                     "not_disclosed")
        mine = record(0x240, "assessment", contract=JUDGMENT, agent=MATE, refs=[ref("assessment_of", CHORD_CLAIM)])
        records = self.records + [mine]
        readable = readable_set(index(records), OPERATOR, CIRCLE, everywhere).revisions
        self.check(self.draft(supersedes=[ref("supersedes", mine)]), records=records, readable=readable)
        self.refused(self.draft(supersedes=[ref("supersedes", self.sheet)]), "agent_mismatch")
        self.refused(self.draft(supersedes=[ref("supersedes", CORRECTION)]), "not_disclosed")

    def test_a_bands_lead_sheet_is_sent_by_an_adopting_member(self):
        mine = membership(0x410, MATE)
        theirs = membership(0x411, MATE2)
        records = self.records + [mine, theirs]
        readable = set(self.readable) | {mine["revision_id"], theirs["revision_id"], BAND_E["revision_id"]}

        def band_sheet(member, contract=LEAD_SHEET):
            return self.draft(contract=contract, agent=BAND, shape="claim",
                              refs=[ref("work", OWN_WORK_E), ref("chords", CHORD_CLAIM), ref("band", BAND_E),
                                    ref("adopted_by", member)])

        self.check(band_sheet(mine), records=records, readable=readable)
        with self.assertRaises(CircleError) as caught:
            self.check(band_sheet(theirs), records=records, readable=readable)
        self.assertEqual(caught.exception.code, "agent_mismatch")
        with self.assertRaises(CircleError) as caught:
            self.check(band_sheet(mine, contract=CHORDS), records=records, readable=readable)
        self.assertEqual(caught.exception.code, "agent_mismatch")
        without_band = Circle(DESTINATION, frozenset({MATE}), None)
        with self.assertRaises(CircleError):
            check_member_draft(band_sheet(mine), MATE, without_band, readable, index(records))


class ClientCommitTest(unittest.TestCase):
    def test_scratch_or_live_in_commit(self):
        self.assertTrue(client_may_commit({"role": "scratch", "writes": "commit"}))
        self.assertTrue(client_may_commit({"role": "scratch", "writes": "dry-run"}))   # the relay refuses it
        self.assertTrue(client_may_commit({"role": "live", "writes": "commit"}))
        for archive in ({"role": "live", "writes": "dry-run"}, {"role": "shadow", "writes": "commit"},
                        {"role": "shadow", "writes": "dry-run"}, {"role": "live"}, {"role": "scratch"},
                        {"role": "real", "writes": "commit"}, None, []):
            with self.subTest(archive=archive):
                self.assertFalse(client_may_commit(archive))


if __name__ == "__main__":
    unittest.main()
