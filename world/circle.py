"""Bandmates, a relay's circle, and co-written Works (ADR 0088): the relay's
circle configuration, a member's draft rules, the readable set, the work
origin and sharing consent contracts, the sharing gate, and the client's
commit rule.

The two contracts, `archive.claim/work_origin` version 1 and
`archive.claim/sharing_consent` version 1, are decided by ADR 0088 for
specification 0.0.10. This module holds their rules as World and the relay
will apply them, so a client can check a proposal before the relay and Door
do; Door remains the authority (INV-2). It uses only the standard library.

Everything computed here (the readable set, the writers of a Work, the Works
a disclosure touches, the gate) is a view: regenerable from the records,
never stored, never a judgment between claims (INV-7, INV-8).

A record, wherever this module reads one, is a stored envelope (D-3): a dict
with `revision_id`, `object_id`, `shape`, `contracts` (or a `contract`
(id, version) pair), `agent`, `payload`, `refs` (each `{role, id,
revision_id}`), and `supersedes` (each `{role, id, revision_id}`, or a bare
revision ID).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Iterable

from world import documents

WORK_ORIGIN = ("archive.claim/work_origin", "1")
SHARING_CONSENT = ("archive.claim/sharing_consent", "1")
PUBLICATION_APPROVAL = ("archive.transition/publication_approval", "1")
PUBLICATION_WITHDRAWAL = ("archive.transition/publication_withdrawal", "1")
# ADR 0085, amended 2026-10-19: an approval states its class; a withdrawal
# withdraws an approval of either version.
PUBLICATION_APPROVAL_2 = ("archive.transition/publication_approval", "2")
PUBLICATION_WITHDRAWAL_2 = ("archive.transition/publication_withdrawal", "2")
APPROVALS = frozenset({PUBLICATION_APPROVAL, PUBLICATION_APPROVAL_2})
WITHDRAWALS = frozenset({PUBLICATION_WITHDRAWAL, PUBLICATION_WITHDRAWAL_2})
BAND_MEMBERSHIP = ("archive.claim/band_membership", "1")
LEAD_SHEET = ("archive.claim/lead_sheet", "1")
PERFORMANCE_WORK = {("archive.claim/performance_work", v) for v in ("1", "2", "3")}
# Records that tie an Artifact, a selection, or an occurrence to a Work.
WORK_LINKS = PERFORMANCE_WORK | {("archive.claim/audio_selection", v) for v in ("1", "2")} \
    | {("archive.claim/song_occurrence", v) for v in ("1", "2")}

OBJECT_ID = re.compile(r"urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
REVISION_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
ORIGINS = ("own", "co_written", "others")
DECISIONS = ("approve", "refuse")
APPROVAL_DECISIONS = ("approve", "hold_back")
CLASSES = ("private", "circle", "service", "open", "distribution")
BEYOND_CIRCLE = ("service", "open", "distribution")
ROLES = ("scratch", "shadow", "live")
WRITES = ("dry-run", "commit")
MAX_NAME = 200
MAX_REASON = 2000
MAX_WRITERS = 32
MAX_COVERS = 64

# The contracts a circle member may cite (ADR 0088). Everything else,
# including every transition, sensitivity, rights, document sources, work
# origins, machine provenance, and correspondence, is the operator's.
MEMBER_CONTRACTS = frozenset({
    ("archive.assessment/judgment", "1"),
    ("archive.assessment/judgment", "2"),          # ADR 0081
    ("archive.claim/span_annotation", "1"),        # ADR 0081
    ("archive.claim/chord_stacks", "1"),           # ADR 0077
    ("archive.claim/chord_stacks", "2"),           # ADR 0079
    ("archive.claim/chord_sequence", "1"),         # ADR 0077
    ("archive.claim/chord_sequence", "2"),         # ADR 0079
    ("archive.claim/meter_layers", "1"),           # ADR 0077
    ("archive.claim/meter_layers", "2"),           # ADR 0083
    ("archive.claim/key_regions", "1"),            # ADR 0080
    ("archive.claim/key_sequence", "1"),           # ADR 0080
    ("archive.claim/tuning", "1"),                 # ADR 0080
    ("archive.claim/form", "1"),                   # ADR 0083
    ("archive.claim/form_spans", "1"),             # ADR 0083
    ("archive.claim/lead_sheet", "1"),             # ADR 0084
    ("archive.claim/timed_lyrics", "1"),           # ADR 0085
    ("archive.claim/notes", "1"),                  # ADR 0086
    ("archive.claim/melody", "1"),                 # ADR 0086
    ("archive.claim/melody_spans", "1"),           # ADR 0086
    ("archive.claim/contribution", "1"),           # ADR 0078
    ("archive.claim/band_membership", "1"),        # ADR 0078
    ("archive.claim/selection_kind", "1"),         # ADR 0048
    ("archive.claim/selection_title", "1"),        # ADR 0048
    ("archive.claim/performance_work", "3"),       # ADR 0048
    SHARING_CONSENT,                               # ADR 0088
    ("archive.claim/melody", "2"),                 # ADR 0113, "Who may save"
    ("archive.claim/underlay", "1"),               # ADR 0113
    ("archive.claim/form", "2"),                   # ADR 0113
    ("archive.claim/form_spans", "2"),             # ADR 0113
    ("archive.claim/meter_layers", "3"),           # ADR 0113
    ("archive.claim/lead_sheet", "2"),             # ADR 0113 (a person's; a band's stays the operator's)
})


class CircleError(ValueError):
    """A configuration, draft, or claim that breaks a rule of ADR 0088. `code`
    is the relay's refusal code for it (ADR 0060, ADR 0058)."""

    def __init__(self, message: str, code: str = "request_malformed") -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# Shared pieces


def _closed(value: Any, required: set[str], optional: set[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CircleError(f"{where} must be an object")
    missing = required - set(value)
    extra = set(value) - required - optional
    if missing or extra:
        raise CircleError(f"{where} has missing {sorted(missing)} or unknown {sorted(extra)} fields")
    return value


def _text(value: Any, where: str, maximum: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise CircleError(f"{where} must be text of 1 to {maximum} characters")
    return value


def _object_id(value: Any, where: str) -> str:
    if not isinstance(value, str) or not OBJECT_ID.fullmatch(value):
        raise CircleError(f"{where} must be an object identifier")
    return value


def _refs_by_role(refs: Any, counts: dict[str, tuple[int, int]], where: str) -> dict[str, list[dict[str, Any]]]:
    """References grouped by role, with each role's (minimum, maximum) count.
    No revision may be pinned twice."""
    if not isinstance(refs, list):
        raise CircleError(f"{where} refs must be a list")
    by_role: dict[str, list[dict[str, Any]]] = {role: [] for role in counts}
    seen = set()
    for index, ref in enumerate(refs):
        _closed(ref, {"role", "id", "revision_id"}, set(), f"{where} ref {index}")
        if ref["role"] not in counts:
            raise CircleError(f"{where} ref {index} has role {ref['role']!r}, which this claim cannot have")
        if not isinstance(ref["id"], str) or not OBJECT_ID.fullmatch(ref["id"]) \
                or not isinstance(ref["revision_id"], str) or not REVISION_ID.fullmatch(ref["revision_id"]):
            raise CircleError(f"{where} ref {index} must pin an object and an exact revision")
        if ref["revision_id"] in seen:
            raise CircleError(f"{where} pins revision {ref['revision_id']} twice")
        seen.add(ref["revision_id"])
        by_role[ref["role"]].append(ref)
    for role, (low, high) in counts.items():
        if not low <= len(by_role[role]) <= high:
            raise CircleError(f"{where} needs {low} to {high} '{role}' references")
    return by_role


def _target(targets: dict[str, dict[str, Any]], ref: dict[str, Any], where: str) -> dict[str, Any]:
    """What a reference pins, as Door would read it from the committed
    revision: `shape`, an Entity's `kind`, a Claim's `contract`, and for a
    pinned claim its `payload` and `refs`."""
    target = targets.get(ref["revision_id"])
    if target is None:
        raise CircleError(f"{where} pins {ref['revision_id']}, which is not a committed revision")
    if target.get("id", ref["id"]) != ref["id"]:
        raise CircleError(f"{where} pins a revision of another object")
    return target


def _entity(targets, ref, kind: str, where: str) -> None:
    target = _target(targets, ref, where)
    if target.get("shape") != "entity" or target.get("kind") != kind:
        raise CircleError(f"{where} must pin an Entity of kind {kind}")


def _contract(record: dict[str, Any]) -> tuple[str, str] | None:
    """The (id, version) a record cites: its `contract`, or, in a stored
    envelope (D-3), its one cited `contracts` entry."""
    contract = record.get("contract")
    if contract:
        return tuple(contract)
    cited = record.get("contracts")
    if isinstance(cited, list) and len(cited) == 1 and isinstance(cited[0], dict):
        return cited[0].get("id"), cited[0].get("version")
    return None


def _superseded_ids(record: dict[str, Any]) -> list[str]:
    out = []
    for item in record.get("supersedes") or []:
        out.append(item.get("revision_id") if isinstance(item, dict) else item)
    return out


def heads(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """The records no other record supersedes."""
    records = list(records)
    superseded = {rid for r in records for rid in _superseded_ids(r)}
    return [r for r in records if r["revision_id"] not in superseded]


def _ref_ids(record: dict[str, Any], role: str) -> list[str]:
    return [r["id"] for r in record.get("refs", []) if isinstance(r, dict) and r.get("role") == role]


# ---------------------------------------------------------------------------
# The relay's circle


@dataclass(frozen=True)
class Circle:
    """A relay's circle: a destination of class circle, its members' person
    objects, and optionally the band's organisation object."""

    destination: str
    members: frozenset[str]
    band: str | None = None


def check_circle_config(callers: Any, circle: Any, operator: str) -> Circle | None:
    """Check a relay's `callers` and `circle` configuration. Returns the
    circle, or None for a relay that serves only the operator."""
    if not isinstance(callers, dict) or not callers:
        raise CircleError("callers must map each tailnet login to an agent")
    agents = [_object_id(agent, f"caller {login!r}") for login, agent in callers.items()]
    others = {a for a in agents if a != operator}
    if circle is None:
        if others:
            raise CircleError("a relay with no circle serves only the operator", "forbidden_identity")
        return None
    body = _closed(circle, {"destination", "members"}, {"band"}, "circle")
    destination = _text(body["destination"], "circle destination", MAX_NAME)
    members = body["members"]
    if not isinstance(members, list) or not members:
        raise CircleError("circle members must be a non-empty list")
    for index, member in enumerate(members):
        _object_id(member, f"circle member {index}")
    if len(set(members)) != len(members):
        raise CircleError("circle members repeats a person")
    if operator in members:
        raise CircleError("the operator is never a circle member")
    if set(members) != others:
        raise CircleError("every caller but the operator is a circle member, and every member is a caller")
    band = _object_id(body["band"], "circle band") if "band" in body else None
    return Circle(destination, frozenset(members), band)


def capacity(agent: str, operator: str, circle: Circle | None) -> str:
    """`operator` or `member`; anyone else is refused."""
    if agent == operator:
        return "operator"
    if circle is not None and agent in circle.members:
        return "member"
    raise CircleError(f"{agent} is not the operator or a circle member", "forbidden_identity")


OPERATOR_ROUTES = frozenset({"/world/query", "/door/transition", "/door/transition-dry-run"})


def check_route(route: str, capacity_: str) -> None:
    """Raw queries and every transition are the operator's."""
    if capacity_ != "operator" and route in OPERATOR_ROUTES:
        raise CircleError(f"{route} answers the operator only", "operator_only")


# ---------------------------------------------------------------------------
# A member's drafts


def check_member_draft(draft: dict[str, Any], caller: str, circle: Circle,
                       readable: set[str], records: dict[str, dict[str, Any]]) -> None:
    """Check one record draft a member sends, before Door sees it. `readable`
    is the caller's readable set of revision IDs; `records` maps revision IDs
    to committed records (for supersessions and band memberships). Raises
    CircleError with the relay's code."""
    if not isinstance(draft, dict):
        raise CircleError("a draft must be an object")
    if draft.get("shape") not in ("claim", "assessment"):
        raise CircleError("a member sends only claims and assessments", "operator_only")
    contracts = draft.get("contracts")
    if not isinstance(contracts, list) or not contracts:
        raise CircleError("a draft cites at least one contract")
    cited = set()
    for c in contracts:
        if not isinstance(c, dict):
            raise CircleError("a contract citation must be an object")
        cited.add((c.get("id"), c.get("version")))
    refused = cited - MEMBER_CONTRACTS
    if refused:
        names = ", ".join(f"{i} version {v}" for i, v in sorted(refused, key=str))
        raise CircleError(f"{names} is the operator's", "operator_only")
    agent = draft.get("agent")
    if agent != caller:
        if circle.band is None or agent != circle.band or cited != {LEAD_SHEET}:
            raise CircleError(f"a draft must name the caller's agent {caller}", "agent_mismatch")
        adopted = [records.get(r.get("revision_id")) for r in draft.get("refs", [])
                   if isinstance(r, dict) and r.get("role") == "adopted_by"]
        if not any(m and _contract(m) == BAND_MEMBERSHIP and _ref_ids(m, "member") == [caller]
                   and _ref_ids(m, "band") == [circle.band] for m in adopted):
            raise CircleError("a band's lead sheet is sent by a member it is adopted by", "agent_mismatch")
    for ref in draft.get("refs", []) or []:
        if not isinstance(ref, dict) or ref.get("revision_id") not in readable:
            raise CircleError("a draft pins a revision the circle cannot read", "not_disclosed")
    for rid in _superseded_ids(draft):
        old = records.get(rid)
        if rid not in readable or old is None:
            raise CircleError("a draft supersedes a revision the circle cannot read", "not_disclosed")
        if old.get("agent") != agent:
            raise CircleError("a draft supersedes only its own agent's records", "agent_mismatch")


# ---------------------------------------------------------------------------
# archive.claim/work_origin version 1


def check_work_origin(claim: dict[str, Any], targets: dict[str, dict[str, Any]], operator: str) -> dict[str, Any]:
    """Check an `archive.claim/work_origin` version 1 claim. Returns the
    payload; raises CircleError."""
    where = "work origin"
    record = _closed(claim, {"payload", "refs", "agent"}, {"revision_id"}, where)
    payload = _closed(record["payload"], {"origin"}, set(), f"{where} payload")
    origin = payload["origin"]
    if origin not in ORIGINS:
        raise CircleError(f"{where} origin must be one of {', '.join(ORIGINS)}")
    _object_id(record["agent"], f"{where} agent")
    by_role = _refs_by_role(record["refs"], {"work": (1, 1), "writer": (0, MAX_WRITERS), "band": (0, 1)}, where)
    _entity(targets, by_role["work"][0], "musical_work", f"{where} work")
    writers = []
    for ref in by_role["writer"]:
        _entity(targets, ref, "person", f"{where} writer")
        writers.append(ref["id"])
    if len(set(writers)) != len(writers):
        raise CircleError(f"{where} names a writer twice")
    for ref in by_role["band"]:
        _entity(targets, ref, "organisation", f"{where} band")
    if origin == "co_written":
        if len(writers) < 2 or operator not in writers:
            raise CircleError(f"{where} co_written names at least two writers, the operator among them")
    elif origin == "own":
        if writers not in ([], [operator]) or by_role["band"]:
            raise CircleError(f"{where} own names no writer but the operator, and no band")
    else:
        if operator in writers or by_role["band"]:
            raise CircleError(f"{where} others never names the operator as writer, and names no band")
    return payload


# ---------------------------------------------------------------------------
# archive.claim/sharing_consent version 1


def check_sharing_consent(claim: dict[str, Any], targets: dict[str, dict[str, Any]], operator: str) -> dict[str, Any]:
    """Check an `archive.claim/sharing_consent` version 1 claim: a co-writer's
    answer for one destination, recorded with the Work's origin."""
    where = "sharing consent"
    record = _closed(claim, {"payload", "refs", "agent"}, {"revision_id"}, where)
    payload = _closed(record["payload"], {"destination", "decision"}, {"reason"}, f"{where} payload")
    _text(payload["destination"], f"{where} destination", MAX_NAME)
    if payload["decision"] not in DECISIONS:
        raise CircleError(f"{where} decision must be approve or refuse")
    if "reason" in payload:
        _text(payload["reason"], f"{where} reason", MAX_REASON)
    agent = _object_id(record["agent"], f"{where} agent")
    if agent == operator:
        raise CircleError(f"{where} is never the operator's: the operator's is the publication approval")
    by_role = _refs_by_role(record["refs"], {"origin": (1, 1), "covers": (1, MAX_COVERS)}, where)
    origin = _target(targets, by_role["origin"][0], f"{where} origin")
    if origin.get("shape") != "claim" or _contract(origin) != WORK_ORIGIN:
        raise CircleError(f"{where} origin must pin a Claim of {WORK_ORIGIN[0]} version {WORK_ORIGIN[1]}")
    if (origin.get("payload") or {}).get("origin") != "co_written":
        raise CircleError(f"{where} pins a co_written origin")
    if agent not in _ref_ids(origin, "writer"):
        raise CircleError(f"{where} is asserted by a writer its origin names")
    for ref in by_role["covers"]:
        if _target(targets, ref, f"{where} covers").get("shape") != "claim":
            raise CircleError(f"{where} covers pins Claim revisions")
    return payload


# ---------------------------------------------------------------------------
# Writers, Works touched, and the sharing gate


def governing_origins(records: Iterable[dict[str, Any]], work_id: str, operator: str) -> list[dict[str, Any]]:
    """Head origins of a Work that govern sharing: the operator's, and those
    whose asserter names themselves as a writer."""
    out = []
    for r in heads(records):
        if _contract(r) != WORK_ORIGIN or _ref_ids(r, "work") != [work_id]:
            continue
        if r.get("agent") == operator or r.get("agent") in _ref_ids(r, "writer"):
            out.append(r)
    return out


def writers(records: Iterable[dict[str, Any]], work_id: str, operator: str) -> tuple[bool, frozenset[str], frozenset[str]]:
    """(co-written, writers, bands) of a Work: the union over its governing
    origins. A co-writer's own origin keeps them a writer whatever the
    operator's says."""
    origins = governing_origins(records, work_id, operator)
    co = any((r.get("payload") or {}).get("origin") == "co_written" for r in origins)
    named = frozenset(w for r in origins for w in _ref_ids(r, "writer"))
    bands = frozenset(b for r in origins for b in _ref_ids(r, "band"))
    return co, named, bands


def works_touched(covered: Iterable[str], records: dict[str, dict[str, Any]]) -> frozenset[str]:
    """The musical Works a disclosure of `covered` revision IDs touches: every
    `musical_work` Entity in the transitive closure of their references, and
    every Work a performance_work claim ties to an occurrence, selection, or
    Artifact in that closure. Objects compare by ID at any revision, so the
    rule errs toward finding a Work."""
    by_object: dict[str, list[dict[str, Any]]] = {}
    for r in records.values():
        by_object.setdefault(r.get("object_id"), []).append(r)
    objects: set[str] = set()
    stack = [records[rid] for rid in covered if rid in records]
    seen_revisions: set[str] = set()
    while stack:
        r = stack.pop()
        if r["revision_id"] in seen_revisions:
            continue
        seen_revisions.add(r["revision_id"])
        objects.add(r.get("object_id"))
        for ref in r.get("refs", []):
            objects.add(ref["id"])
            if ref.get("revision_id") in records:
                stack.append(records[ref["revision_id"]])
    # Links up from the audio: selections of an Artifact, occurrences of a
    # selection, Works of an occurrence.
    changed = True
    while changed:
        changed = False
        for r in records.values():
            if _contract(r) in WORK_LINKS and r.get("object_id") not in objects \
                    and any(ref["id"] in objects for ref in r.get("refs", []) if ref.get("role") != "work"):
                objects.add(r.get("object_id"))
                changed = True
            if _contract(r) in PERFORMANCE_WORK and any(i in objects for i in _ref_ids(r, "performance")):
                for w in _ref_ids(r, "work"):
                    if w not in objects:
                        objects.add(w)
                        changed = True
    return frozenset(o for o in objects
                     if any(x.get("shape") == "entity" and (x.get("payload") or {}).get("kind") == "musical_work"
                            for x in by_object.get(o, [])))


def band_members(records: Iterable[dict[str, Any]], bands: frozenset[str], operator: str) -> frozenset[str]:
    """Members, in force, of the given bands: head band memberships the
    operator asserted, with no `to` date and not `ended`."""
    out = set()
    for r in heads(records):
        if _contract(r) != BAND_MEMBERSHIP or r.get("agent") != operator:
            continue
        payload = r.get("payload") or {}
        if "to" in payload or payload.get("ended"):
            continue
        if set(_ref_ids(r, "band")) & bands:
            out.update(_ref_ids(r, "member"))
    return frozenset(out)


@dataclass(frozen=True)
class Gate:
    """Whether covered revisions may go to a destination as far as co-writing
    goes. `missing` lists (writer, revision) pairs still needing a consent;
    `why` says, per Work, which rule applied."""

    allowed: bool
    missing: tuple[tuple[str, str], ...] = ()
    why: tuple[str, ...] = field(default=())


def sharing_gate(covered: Iterable[str], destination: str, exposure_class: str, audience: Iterable[str] | None,
                 records: dict[str, dict[str, Any]], operator: str) -> Gate:
    """ADR 0088's rule for co-written Works (D12.2). `audience` is the set of
    people a private or circle destination reaches, or None when unknown.
    The operator's own approval, sensitivity, and rights are checked
    elsewhere; this gate only adds what co-writers require."""
    if exposure_class not in CLASSES:
        raise CircleError(f"exposure class must be one of {', '.join(CLASSES)}")
    covered = list(covered)
    all_records = list(records.values())
    consents = [r for r in heads(all_records) if _contract(r) == SHARING_CONSENT]
    missing: list[tuple[str, str]] = []
    why: list[str] = []
    for work in sorted(works_touched(covered, records)):
        co, named, bands = writers(all_records, work, operator)
        if not co:
            why.append(f"{work}: not co-written, so ADR 0010 alone applies")
            continue
        origin_objects = {r.get("object_id") for r in governing_origins(all_records, work, operator)}
        if exposure_class in ("private", "circle") and audience is not None:
            inside = named | band_members(all_records, bands, operator)
            if set(audience) <= inside:
                why.append(f"{work}: inside the band; the operator's approval suffices")
                continue
            why.append(f"{work}: the destination reaches someone outside the band")
        else:
            why.append(f"{work}: beyond the band; every co-writer's consent is needed")
        for writer in sorted(named - {operator}):
            given = [c for c in consents
                     if c.get("agent") == writer
                     and (c.get("payload") or {}).get("decision") == "approve"
                     and (c.get("payload") or {}).get("destination") == destination
                     and any(i in origin_objects for i in _ref_ids(c, "origin"))]
            for rid in covered:
                if not any(rid in {ref["revision_id"] for ref in c.get("refs", []) if ref.get("role") == "covers"}
                           for c in given):
                    missing.append((writer, rid))
    return Gate(not missing, tuple(missing), tuple(why))


# ---------------------------------------------------------------------------
# The readable set


@dataclass(frozen=True)
class Readable:
    """What a circle may read: revision IDs, the ones withheld (by
    sensitivity, since rights never limit the private circle, or by a fact
    that cannot be established), and why each was added."""

    revisions: frozenset[str]
    withheld: frozenset[str]
    why: dict[str, str]


def _withdrawn(records: Iterable[dict[str, Any]]) -> dict[str, str]:
    """Each withdrawn approval revision, and the withdrawal (of either
    version) that withdrew it."""
    return {ref.get("revision_id"): r["revision_id"] for r in records if _contract(r) in WITHDRAWALS
            for ref in r.get("refs", []) if isinstance(ref, dict) and ref.get("role") == "withdraws"}


def _approvals_to(records: list[dict[str, Any]], destination: str, operator: str) -> list[dict[str, Any]]:
    """Live approvals to the circle: version 1, which reaches no further than
    the circle, and version 2 whose class is `circle`."""
    withdrawn = _withdrawn(records)
    return [r for r in records
            if (_contract(r) == PUBLICATION_APPROVAL
                or (_contract(r) == PUBLICATION_APPROVAL_2 and (r.get("payload") or {}).get("class") == "circle"))
            and r["revision_id"] not in withdrawn
            and (r.get("payload") or {}).get("destination") == destination
            and (r.get("payload") or {}).get("decision") == "approve"
            and _ref_ids(r, "authorizer") == [operator]]


def readable_set(records: dict[str, dict[str, Any]], operator: str, circle: Circle,
                 reach: Callable[[str], str]) -> Readable:
    """The circle's readable set (ADR 0088). `reach(object_id)` gives the
    furthest exposure class an object may reach under its sensitivity and
    rights (ADR 0085), in which rights never fall short of the circle; an
    object that may not reach `circle` is withheld."""
    all_records = list(records.values())
    floor = CLASSES.index("circle")

    def reachable(object_id: str) -> bool:
        cls = reach(object_id)
        if cls not in CLASSES:
            return False    # a fact that cannot be established never widens disclosure
        return CLASSES.index(cls) >= floor

    out: set[str] = set()
    withheld: set[str] = set()
    why: dict[str, str] = {}

    def add(rid: str, reason: str) -> None:
        record = records.get(rid)
        if record is None or rid in out or rid in withheld:
            return
        if not reachable(record.get("object_id")):
            withheld.add(rid)
            return
        out.add(rid)
        why[rid] = reason
        for ref in record.get("refs", []):
            add(ref.get("revision_id"), f"pinned by {rid}")

    for approval in _approvals_to(all_records, circle.destination, operator):
        covers = [ref["revision_id"] for ref in approval.get("refs", []) if ref.get("role") == "covers"]
        gate = sharing_gate(covers, circle.destination, "circle", circle.members, records, operator)
        blocked = {rid for _, rid in gate.missing}
        for rid in covers:
            if rid not in blocked:
                add(rid, f"covered by {approval['revision_id']}")
    authors = set(circle.members) | ({circle.band} if circle.band else set())
    changed = True
    while changed:
        changed = False
        for r in all_records:
            rid = r["revision_id"]
            if rid in out or rid in withheld or r.get("agent") not in authors \
                    or r.get("shape") not in ("claim", "assessment"):
                continue
            pins = [ref.get("revision_id") for ref in r.get("refs", [])]
            if all(p in out for p in pins):
                before = len(out)
                add(rid, f"asserted by member {r.get('agent')}")
                changed = changed or len(out) != before
    return Readable(frozenset(out), frozenset(withheld), why)


def readable_by(caller: str, operator: str, circle_set: Readable, records: dict[str, dict[str, Any]]) -> frozenset[str]:
    """What one caller reads: everything for the operator; for a member, the
    circle's set plus their own records."""
    if caller == operator:
        return frozenset(records)
    own = {rid for rid, r in records.items() if r.get("agent") == caller}
    return circle_set.revisions | own


# ---------------------------------------------------------------------------
# Publication beyond the circle (ADR 0085, amended 2026-10-19)


def check_publication_approval_v2(claim: dict[str, Any], targets: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Check an `archive.transition/publication_approval` version 2 record:
    version 1 with the `class` of its destination. Whether an approval
    beyond the circle was allowed is `beyond_circle`, which Door applies on
    the day of the transition and `may_publish` on the day asked."""
    where = "publication approval"
    record = _closed(claim, {"payload", "refs"}, {"revision_id", "agent"}, where)
    payload = _closed(record["payload"], {"destination", "decision", "class"}, set(), f"{where} payload")
    _text(payload["destination"], f"{where} destination", MAX_NAME)
    if payload["decision"] not in APPROVAL_DECISIONS:
        raise CircleError(f"{where} decision must be one of {', '.join(APPROVAL_DECISIONS)}")
    if payload["class"] not in CLASSES:
        raise CircleError(f"{where} class must be one of {', '.join(CLASSES)}")
    by_role = _refs_by_role(record["refs"], {"authorizer": (1, 1), "covers": (1, 1 << 30)}, where)
    _entity(targets, by_role["authorizer"][0], "person", f"{where} authorizer")
    for ref in by_role["covers"]:
        if _target(targets, ref, f"{where} covers").get("shape") != "claim":
            raise CircleError(f"{where} covers pins Claim revisions")
    return payload


def check_publication_withdrawal_v2(claim: dict[str, Any], targets: dict[str, dict[str, Any]],
                                    others: Iterable[dict[str, Any]] = ()) -> dict[str, Any]:
    """Check an `archive.transition/publication_withdrawal` version 2 record:
    version 1 whose `withdraws` pins an approval of version 1 or 2. With
    `others`, the archive's other records, also that no withdrawal of either
    version withdraws the same approval."""
    where = "publication withdrawal"
    record = _closed(claim, {"payload", "refs"}, {"revision_id", "agent"}, where)
    payload = _closed(record["payload"], set(), {"reason"}, f"{where} payload")
    if "reason" in payload:
        _text(payload["reason"], f"{where} reason", MAX_REASON)
    by_role = _refs_by_role(record["refs"], {"authorizer": (1, 1), "withdraws": (1, 1)}, where)
    _entity(targets, by_role["authorizer"][0], "person", f"{where} authorizer")
    withdrawn = by_role["withdraws"][0]
    target = _target(targets, withdrawn, f"{where} withdraws")
    if target.get("shape") != "claim" or _contract(target) not in APPROVALS:
        raise CircleError(f"{where} withdraws a publication approval of version 1 or 2")
    for other in others:
        if other.get("revision_id") != record.get("revision_id") and _contract(other) in WITHDRAWALS \
                and withdrawn["revision_id"] in _ref_revisions(other, "withdraws"):
            raise CircleError(f"{where}: {withdrawn['revision_id']} is already withdrawn by {other['revision_id']}")
    return payload


def _ref_revisions(record: dict[str, Any], role: str) -> list[str]:
    return [r.get("revision_id") for r in record.get("refs", []) if isinstance(r, dict) and r.get("role") == role]


def _as_world(record: dict[str, Any]) -> dict[str, Any]:
    """A record as `documents.reach` reads one: `contract`, an Entity's
    `kind`, `at`, and `supersedes` as references."""
    out = dict(record)
    contract = _contract(record)
    if contract:
        out["contract"] = contract
    if record.get("shape") == "entity" and "kind" not in out:
        out["kind"] = (record.get("payload") or {}).get("kind")
    out["supersedes"] = [{"revision_id": rid} for rid in _superseded_ids(record)]
    out.setdefault("at", "")
    return out


def reacher(records: dict[str, dict[str, Any]], operator: str, today: date) -> documents.Reacher:
    """Reach over these records, in either envelope form, read once."""
    return documents.Reacher([_as_world(r) for r in records.values()], operator, today)


def beyond_circle(payload: dict[str, Any], covers: list[str], records: dict[str, dict[str, Any]], operator: str,
                  today: date, reach: documents.Reacher | None = None) -> list[str]:
    """What blocks an approval beyond the circle on `today` (S-30, S-32):
    for an `approve` of class service, open, or distribution, each covered
    revision's object must reach the class by sensitivity and by rights (the
    strictest claim winning), and ADR 0088's sharing gate must pass for the
    destination, so every co-writer of a co-written Work the covered
    revisions touch has a head `approve` consent naming the destination
    exactly and covering every covered revision. Returns one line per
    blocker, naming the object, label, statement, writer, and revision;
    empty when nothing blocks it. Door refuses by the same rule on the UTC
    date of the transition; no override past a sensitivity limit is
    defined."""
    cls = payload.get("class")
    if payload.get("decision") != "approve" or cls not in BEYOND_CIRCLE:
        return []
    reach = reach or reacher(records, operator, today)
    level = CLASSES.index(cls)
    blockers: list[str] = []
    for rid in covers:
        record = records.get(rid)
        if record is None:
            blockers.append(f"{rid} is not a revision in the archive")
            continue
        found = reach.reach(record["object_id"])
        for kind, limit in (("sensitivity", found.labelled), ("rights", found.rights)):
            if level > CLASSES.index(limit):
                blockers.append(f"{rid} ({record['object_id']}): its {kind} lets it reach no further than {limit}")
        for line in found.why:
            limit = line.rsplit("no further than ", 1)[-1]
            if limit in CLASSES and level > CLASSES.index(limit):
                blockers.append(f"{rid}: {line}")
    gate = sharing_gate(covers, payload.get("destination"), cls, None, records, operator)
    for writer, rid in gate.missing:
        blockers.append(f"co-writer {writer} has no head approve consent to {payload.get('destination')!r} "
                        f"covering {rid}")
    return blockers


def may_publish(approval: str, records: dict[str, dict[str, Any]], operator: str, today: date) -> dict[str, Any]:
    """Whether a publication approval still permits publication on `today`,
    and if not, everything that blocks it (ADR 0085, amended 2026-10-19): it
    is not the head, is withdrawn, holds back, is version 1 (which states no
    class and reaches no further than the circle), or is to the circle or
    private; or `beyond_circle` blocks it. A publisher calls this when it
    publishes, because rights expire and consents change. A view: computed,
    never stored."""
    record = records.get(approval)
    if record is None or _contract(record) not in APPROVALS:
        raise CircleError(f"{approval} is not a publication approval", "view_subject_not_found")
    payload = record.get("payload") or {}
    blockers: list[str] = []
    superseding = sorted(r["revision_id"] for r in records.values() if approval in _superseded_ids(r))
    if superseding:
        blockers.append(f"the approval is not the head: {', '.join(superseding)} supersedes it")
    withdrawn = _withdrawn(records.values())
    if approval in withdrawn:
        blockers.append(f"the approval is withdrawn by {withdrawn[approval]}")
    if payload.get("decision") != "approve":
        blockers.append(f"the approval's decision is {payload.get('decision')}, not approve")
    if _contract(record) == PUBLICATION_APPROVAL:
        blockers.append("a version 1 approval states no class and reaches no further than the circle")
    elif payload.get("class") not in BEYOND_CIRCLE:
        blockers.append(f"the approval's class is {payload.get('class')}, which is not beyond the circle")
    else:
        blockers += beyond_circle(payload, _ref_revisions(record, "covers"), records, operator, today)
    return {"approval": approval, "destination": payload.get("destination"), "class": payload.get("class"),
            "on": today.isoformat(), "may_publish": not blockers, "blockers": blockers}


# ---------------------------------------------------------------------------
# The client's commit rule (ADR 0060 as amended)


def client_may_commit(archive: Any) -> bool:
    """A client sends a commit only to a scratch relay, or to a live relay
    whose writes are on. Never to the shadow. An answer without the fields
    is refused: a fact that cannot be established never permits a write."""
    if not isinstance(archive, dict):
        return False
    role, writes = archive.get("role"), archive.get("writes")
    if role not in ROLES or writes not in WRITES:
        return False
    return role == "scratch" or (role == "live" and writes == "commit")
