"""What each caller of the relay may read and send, for one World generation
(ADRs 0085, 0088, 0089).

A relay serves its operator and, when it is configured with a circle, the
circle's members. The rules are World's (`world/circle.py`,
`world/documents.py`); this module reads the records of the selected World
generation once, computes from them what the circle may read, and answers
the relay's questions about one caller: may they read this revision, pin it
in a draft, hear this recording, or be shown this document. Everything here
is regenerable from World and held in memory only (docs/state-model.md).

A fact that cannot be established never widens what a member is shown
(S-32): an object whose reach cannot be read is withheld, and a generation
whose operator or members do not match the configuration serves no member.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Iterator

import world
from refusals import Refused
from world import circle as circles
from world import documents, view_records

OPERATOR_INITIALIZATION = ("archive.transition/operator_initialization", "1")
DOCUMENT_SOURCE = ("archive.claim/document_source", "1")
OBJECT_ID = circles.OBJECT_ID
REVISION_ID = circles.REVISION_ID
# Views whose answers are read from sensitivity, rights, and publication
# approvals: what may reach whom is the operator's to read (ADR 0088), and a
# member's filtered records could not answer them truly anyway.
OPERATOR_VIEWS = frozenset({"archive.view/readable_set", "archive.view/reach"})
# Raw queries and every transition are the operator's (ADR 0088).
OPERATOR_ROUTES = circles.OPERATOR_ROUTES


@dataclass(frozen=True)
class Caller:
    """Who is calling: the person their login is bound to, and whether they
    act as the `operator` or as a `member` of the circle."""

    agent: str
    capacity: str


def check_config(callers: Any, circle: Any) -> tuple[str, circles.Circle | None]:
    """The configured operator and circle. The operator is the one caller who
    is not a member; a relay with no circle has exactly one agent among its
    callers, so a login is never half-configured (ADR 0088)."""
    if not isinstance(callers, dict) or not callers or not all(isinstance(a, str) for a in callers.values()):
        raise ValueError("callers must map each tailnet login to an agent urn:uuid")
    members = set(circle.get("members") or []) if isinstance(circle, dict) else set()
    operators = {agent for agent in callers.values() if agent not in members}
    if len(operators) != 1:
        raise ValueError("every caller but one, the operator, must be a circle member; a relay with no circle "
                         "serves only the operator")
    operator = next(iter(operators))
    try:
        return operator, circles.check_circle_config(callers, circle, operator)
    except circles.CircleError as exc:
        raise ValueError(f"circle: {exc}") from None


def _contract(record: dict[str, Any]) -> tuple[str, str] | None:
    contract = record.get("contract")
    return (contract[0], contract[1]) if contract else None


def content_digest(record: dict[str, Any]) -> str | None:
    """The content digest an Artifact revision's payload names."""
    if record.get("shape") != "artifact" or not isinstance(record.get("payload"), dict):
        return None
    content = record["payload"].get("content")
    digest = content.get("digest") if isinstance(content, dict) else None
    return digest if isinstance(digest, str) else None


def revision_of(draft: dict[str, Any]) -> str:
    """The revision Door will give a draft: the digest of its canonical JSON
    without `revision_id` and `at` (D-3), so a later draft of the same
    proposal may pin an earlier one."""
    identity = {key: value for key, value in draft.items() if key not in ("revision_id", "at")}
    data = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _named(value: Any, objects: bool) -> Iterator[tuple[str, str]]:
    """Every pin (`{id, revision_id}`) and, when `objects`, every bare object
    identifier a view request names."""
    if isinstance(value, dict):
        if set(value) == {"id", "revision_id"} and all(isinstance(v, str) for v in value.values()):
            yield "pin", value["revision_id"]
            return
        for item in value.values():
            yield from _named(item, objects)
    elif isinstance(value, list):
        for item in value:
            yield from _named(item, objects)
    elif objects and isinstance(value, str) and OBJECT_ID.fullmatch(value):
        yield "object", value


class Access:
    """One World generation's answers about who may read what."""

    def __init__(self, generation: str, records: list[dict[str, Any]], operator: str,
                 circle: circles.Circle | None, today: date):
        self.generation, self.today, self.circle = generation, today, circle
        self.records = records
        self.by_revision = {r["revision_id"]: r for r in records}
        appointed = {ref["id"] for r in records if _contract(r) == OPERATOR_INITIALIZATION
                     for ref in r.get("refs", []) if ref.get("role") == "appoints"}
        self.operator = next(iter(appointed)) if len(appointed) == 1 else None
        self.contents: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            digest = content_digest(record)
            if digest is not None:
                self.contents.setdefault(digest, []).append(record)
        self.document_types: dict[str, set[str]] = {}
        for record in circles.heads(records):
            if _contract(record) == DOCUMENT_SOURCE and isinstance(record.get("payload"), dict):
                for ref in record.get("refs", []):
                    if ref.get("role") == "document":
                        self.document_types.setdefault(ref["id"], set()).add(record["payload"].get("media_type"))
        self.problem: str | None = None
        self.circle_set: circles.Readable | None = None
        if circle is not None:
            self.problem = self._roster(operator, circle)
            if self.problem is None:
                self.circle_set = circles.readable_set(self.by_revision, self.operator, circle, self._reach())

    def _roster(self, operator: str, circle: circles.Circle) -> str | None:
        """Why this generation cannot serve the circle, or None: the
        configured operator must be World's active operator, and each member
        a person Entity in the archive (ADR 0088)."""
        if self.operator is None:
            return "World names no single active operator"
        if self.operator != operator:
            return "the configured operator is not the active operator World names"
        people = {r["object_id"] for r in self.records
                  if r.get("shape") == "entity" and r.get("head", True) and r.get("kind") == "person"}
        missing = sorted(set(circle.members) - people)
        if missing:
            return f"circle members {', '.join(missing)} are not person Entities in the archive"
        return None

    def _reach(self):
        known: dict[str, str] = {}

        def reach(object_id: str) -> str:
            if object_id not in known:
                try:
                    known[object_id] = documents.reach(object_id, self.records, self.operator, self.today).furthest
                except (ValueError, KeyError, TypeError, IndexError):
                    known[object_id] = "unknown"    # never widens: the readable set withholds it
            return known[object_id]
        return reach

    # -- one caller -------------------------------------------------------

    def readable(self, caller: Caller) -> frozenset[str] | None:
        """The revisions a caller reads: None for the operator, who reads
        everything; for a member, the circle's set and their own records."""
        if caller.capacity == "operator":
            return None
        if self.circle_set is None:
            raise Refused("forbidden_identity", f"the circle is not served from this World generation: {self.problem}")
        return circles.readable_by(caller.agent, self.operator, self.circle_set, self.by_revision)

    def check_drafts(self, drafts: Iterable[tuple[str, dict[str, Any]]], caller: Caller) -> None:
        """A member's drafts, before Door sees them (ADR 0088): member
        contracts only, the caller's agent (or the band's, for a lead sheet
        they adopted), and pins only what they may read, which includes the
        drafts before it in the same proposal."""
        readable = set(self.readable(caller) or ())
        for name, draft in drafts:
            try:
                circles.check_member_draft(draft, caller.agent, self.circle, readable, self.by_revision)
            except circles.CircleError as exc:
                raise Refused(exc.code, f"{name}: {exc}") from None
            readable.add(revision_of(draft))

    def check_disclosed(self, request: dict[str, Any], caller: Caller) -> None:
        """A member's view request names only what they may read: every pin
        in it, and every object its subject names. Otherwise `not_disclosed`,
        whether or not it exists, so the refusal reveals nothing."""
        if caller.capacity == "operator":
            return
        if request["view"] in OPERATOR_VIEWS:
            raise Refused("operator_only", f"{request['view']} answers the operator only")
        readable = self.readable(caller)
        objects = {self.by_revision[rid]["object_id"] for rid in readable}
        named = list(_named(request["subject"], True)) + list(_named(request["parameters"], False))
        for kind, value in named:
            if (kind == "pin" and value not in readable) or (kind == "object" and value not in objects):
                raise Refused("not_disclosed", "the request names something your circle cannot read")

    def check_inputs(self, rendered: list[dict[str, str]], joined: list[dict[str, str]], caller: Caller) -> None:
        """An export's inputs: every rendered revision a Claim and every
        joined one an Artifact the caller may read (ADR 0089)."""
        readable = self.readable(caller)
        for pins, shape in ((rendered, "claim"), (joined, "artifact")):
            for pin in pins:
                if readable is not None and pin["revision_id"] not in readable:
                    raise Refused("not_disclosed", "the export names something your circle cannot read")
                record = self.by_revision.get(pin["revision_id"])
                if record is None or record["object_id"] != pin["id"]:
                    raise Refused("export_input_unknown", f"World holds no revision {pin['revision_id']} of {pin['id']}")
                if record.get("shape") != shape:
                    raise Refused("request_malformed", f"{pin['revision_id']} is not a{'n' if shape[0] == 'a' else ''} "
                                                       f"{shape.capitalize()} revision")

    def closure(self, revisions: Iterable[str], caller: Caller) -> list[dict[str, Any]]:
        """The records these revisions pin, transitively, as World reads
        them, limited to what the caller may read: what an exporter is given."""
        readable = self.readable(caller)
        seen: dict[str, dict[str, Any]] = {}
        queue = list(revisions)
        while queue:
            rid = queue.pop(0)
            record = self.by_revision.get(rid)
            if rid in seen or record is None or (readable is not None and rid not in readable):
                continue
            seen[rid] = record
            queue.extend(ref.get("revision_id") for ref in record.get("refs", []) if ref.get("revision_id"))
        return [seen[rid] for rid in sorted(seen)]

    def hears(self, digest: str, caller: Caller) -> bool:
        """Whether a member may be served this content or anything made from
        it (its derived media, stems, renders, a document's bytes): every
        Artifact that carries it must be readable, the most restrictive
        answer among them (S-32, ADR 0089)."""
        if caller.capacity == "operator":
            return caller.agent == self.operator
        found = self.contents.get(digest, [])
        if not found:
            return False
        readable = self.readable(caller)
        objects = {r["object_id"] for r in found}
        return all(any(r["revision_id"] in readable for r in found if r["object_id"] == obj) for obj in objects)

    def document_type(self, digest: str) -> str | None:
        """The media type a document source claim gives the document Artifacts
        carrying this content, or None when no claim describes one."""
        types = set()
        for record in self.contents.get(digest, []):
            types |= self.document_types.get(record["object_id"], set())
        if not types:
            return None
        if len(types) != 1 or not isinstance(next(iter(types)), str):
            raise Refused("internal_error", "document source claims give this content more than one media type")
        return next(iter(types))

    def artifacts(self, digest: str) -> list[str]:
        """The Artifacts already carrying this content."""
        return sorted({r["object_id"] for r in self.contents.get(digest, [])})


def load(store: Path, version: int, operator: str, circle: circles.Circle | None, today: date) -> Access:
    """Read the selected generation of `version` and compute its access."""
    for _ in range(3):
        generation = world.selected_generation(store, version)
        if generation is None:
            raise Refused("world_unavailable", f"World serves no projection {version}")
        try:
            projection = world._read_current(store, version)
            records = view_records.records(projection)
        except (world.ProjectionError, OSError, view_records.RecordsUnreadable) as exc:
            raise Refused("world_unavailable", f"World's records cannot be read: {exc}") from exc
        if world.selected_generation(store, version) == generation:
            return Access(generation, records, operator, circle, today)
    raise Refused("world_unavailable", "World's generation kept changing while it was read")
