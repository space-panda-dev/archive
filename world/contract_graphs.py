"""The claim graphs of specification 0.0.10's contracts (projection 6,
revision 6).

World re-checks each claim with the module that holds its contract's rules
(the table `RECHECKS`), and only then projects it, inside the graph of the
claim revision itself, as 0.0.9's claims are projected: nothing here is
current, ranked, or merged, and every value stays its asserter's.

A claim projects by its payload and its references, read through the
contract's own JSON-LD context fragment (D-18): each payload field is the
predicate its contract's context names; an object is a node,
`<{claim}:{field}>`; an array of objects is one node per item,
`<{claim}:{item}={n}>`, whose `rdf:value` is its position from 0; an array of
scalars is one value per item, except where order or repetition is the
meaning (a grouping, a spelling, open strings), which is one text. A
reference is the predicate its role names, to the revision it pins. `RULES`
lists where World departs from that reading, and why: a field the context
leaves to the core context, a term that would collide with an earlier shape,
an index that names a node, and a value that names a concept. The claim
revision of a contract new in 0.0.10 is typed `arch:Claim` in its own graph;
the versions 2 of chord stacks, chord sequence, meter layers, and judgment
keep the shapes of their versions 1.

The module needs pyoxigraph only to build terms; the rules it re-checks are
the standard-library modules beside it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from pyoxigraph import Literal, NamedNode, Quad, Store

from world import (catalogue, circle, documents, envelope_rules, exports, form, harmony, judgments, lead_sheets, notes,
                   tonality)

ARCH = "urn:uuid:3e8b29f8-70c9-403e-bb4c-2280d59eca7f#"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
PROV = "http://www.w3.org/ns/prov#"
SKOS = "http://www.w3.org/2004/02/skos/core#"
FOAF = "http://xmlns.com/foaf/0.1/"
XSD = "http://www.w3.org/2001/XMLSchema#"
RDF_JSON = RDF + "JSON"

CHORD_STACKS_2 = ("archive.claim/chord_stacks", "2")
CHORD_SEQUENCE_2 = ("archive.claim/chord_sequence", "2")
METER_LAYERS_2 = ("archive.claim/meter_layers", "2")
JUDGMENT_2 = ("archive.assessment/judgment", "2")
# Versions 2 of contracts 0.0.9 projects keep their versions 1's shapes.
SECOND_VERSIONS = frozenset({CHORD_STACKS_2, CHORD_SEQUENCE_2, METER_LAYERS_2, JUDGMENT_2})


class ContractGraphError(ValueError):
    """A 0.0.10 claim World's re-check refuses, or cannot project."""


@dataclass(frozen=True)
class Lookups:
    """What a re-check reads beyond the record: every record revision World
    holds by its identifier, the Entity kind of an agent's object, and the
    active operator's person object."""

    records: dict[str, dict[str, Any]]
    entity_kind: Callable[[str], str | None]
    operator: Callable[[], str | None]


def lookups(store: Store, records_graph: NamedNode, records_by_revision: dict[str, dict[str, Any]]) -> Lookups:
    """What a 0.0.10 re-check reads beyond the records it pins: an agent's
    Entity kind and the active operator, from the export, or, for a refresh
    whose export since an earlier commit lacks them, from the generation it
    extends (the records graph keeps every revision's object and payload)."""
    def entity_kind(object_id: str) -> str | None:
        kinds = {r["payload"].get("kind") for r in records_by_revision.values()
                 if r.get("object_id") == object_id and r.get("shape") == "entity" and isinstance(r.get("payload"), dict)}
        if not kinds:
            kinds = {json.loads(row["payload"].value).get("kind") for row in store.query(
                f"SELECT ?payload WHERE {{ GRAPH <{records_graph.value}> {{ ?r <{ARCH}object_id> <{object_id}> ; "
                f"<{ARCH}shape> <{ARCH}Entity> ; <{ARCH}payload> ?payload }} }}")} if object_id.startswith("urn:") else set()
        return next(iter(kinds)) if len(kinds) == 1 else None

    def operator() -> str | None:
        appointed = {ref["id"] for r in records_by_revision.values()
                     if any(c.get("id") == "archive.transition/operator_initialization" for c in r.get("contracts", []))
                     for ref in r.get("refs", []) if ref.get("role") == "appoints"}
        if not appointed:
            appointed = {row["operator"].value for row in store.query(
                f"SELECT ?operator WHERE {{ GRAPH <{records_graph.value}> {{ ?t <{ARCH}appoints> ?pin . "
                f"?pin <{ARCH}object_id> ?operator }} }}")}
        return next(iter(appointed)) if len(appointed) == 1 else None

    return Lookups(records_by_revision, entity_kind, operator)


# ---------------------------------------------------------------------------
# Reading records as the rule modules read them


def contract_of(record: dict[str, Any]) -> tuple[str, str] | None:
    cited = [(c.get("id"), c.get("version")) for c in record.get("contracts", []) if isinstance(c, dict)]
    return cited[0] if len(cited) == 1 else None


def record_view(record: dict[str, Any]) -> dict[str, Any]:
    """A record as the rule modules take a claim: payload, refs, revision, agent."""
    return {"payload": record.get("payload"), "refs": record.get("refs", []),
            "revision_id": record["revision_id"], "agent": record.get("agent")}


def targets(record: dict[str, Any], records: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """What each revision a record pins is, as the rule modules read targets:
    its object, shape, an Entity's kind, a Claim's contract, payload, refs,
    and agent. A revision World does not hold is left out, and the module
    refuses the pin."""
    found = {}
    for ref in record.get("refs", []):
        target = records.get(ref.get("revision_id")) if isinstance(ref, dict) else None
        if target is None:
            continue
        payload = target.get("payload")
        found[target["revision_id"]] = {
            "id": target["object_id"], "object_id": target["object_id"], "revision_id": target["revision_id"],
            "shape": target.get("shape"), "kind": payload.get("kind") if isinstance(payload, dict) else None,
            "contract": contract_of(target), "contracts": target.get("contracts", []),
            "payload": payload, "refs": target.get("refs", []), "agent": target.get("agent")}
    return found


def _pinned(record: dict[str, Any], role: str, lookups: Lookups) -> list[dict[str, Any]]:
    return [lookups.records[r["revision_id"]] for r in record.get("refs", [])
            if isinstance(r, dict) and r.get("role") == role and r.get("revision_id") in lookups.records]


def pinned_view(record: dict[str, Any]) -> dict[str, Any]:
    """A pinned claim as the rule modules take one: payload, refs, revision."""
    return {"payload": record.get("payload"), "refs": record.get("refs", []), "revision_id": record["revision_id"]}


def _one(record: dict[str, Any], role: str, lookups: Lookups) -> dict[str, Any] | None:
    found = _pinned(record, role, lookups)
    return pinned_view(found[0]) if len(found) == 1 else None


def _stream(record: dict[str, Any], lookups: Lookups, contract: tuple[str, str]) -> dict[str, Any]:
    stream = _one(record, "stream", lookups)
    if stream is None:
        raise ContractGraphError(f"{contract[0]} {record['revision_id']} must pin one stream World holds")
    return stream


def _agent_kind(record: dict[str, Any], lookups: Lookups) -> str | None:
    agent = record.get("agent")
    return lookups.entity_kind(agent) if isinstance(agent, str) else None


def _operator(lookups: Lookups, contract: tuple[str, str]) -> str:
    operator = lookups.operator()
    if operator is None:
        raise ContractGraphError(f"{contract[0]} is checked against the active operator, whom World cannot find")
    return operator


# ---------------------------------------------------------------------------
# Re-checks: each contract's own module, with what it reads


def _judgment(record: dict[str, Any], lookups: Lookups) -> None:
    view = {**record_view(record), "contracts": [{"id": c["id"], "version": c["version"]}
                                                 for c in record.get("contracts", [])]}
    judged = _pinned(record, "assessment_of", lookups)
    judged_view = stream = None
    if len(judged) == 1:
        judged_view = {**record_view(judged[0]), "contracts": [{"id": c["id"], "version": c["version"]}
                                                               for c in judged[0].get("contracts", [])]}
        stream = _one(judged[0], "stream", lookups)
    judgments.check_judgment(view, judged_view, stream)


def _key_sequence(record: dict[str, Any], lookups: Lookups) -> None:
    pinned_form = _one(record, "form", lookups)
    sections = None
    if pinned_form is not None and isinstance(pinned_form["payload"], dict):
        sections = len(pinned_form["payload"].get("sections", []))
    tonality.check_key_sequence(record_view(record), _one(record, "chord_sequence", lookups), sections)


def _span_annotation(record: dict[str, Any], lookups: Lookups) -> None:
    about = {r["revision_id"]: record_view(r) for r in _pinned(record, "about", lookups)}
    judgments.check_span_annotation(record_view(record), _one(record, "stream", lookups),
                                    _one(record, "sequence", lookups), about, _agent_kind(record, lookups))


def _lead_sheet(record: dict[str, Any], lookups: Lookups) -> None:
    agent = record.get("agent")
    kinds = {agent: lookups.entity_kind(agent)} if isinstance(agent, str) else {}
    lead_sheets.check_lead_sheet(record_view(record), targets(record, lookups.records), kinds)
    for earlier in record.get("supersedes", []):
        previous = lookups.records.get(earlier.get("revision_id")) if isinstance(earlier, dict) else None
        if previous is not None:
            lead_sheets.check_lead_sheet_revision(record_view(previous), record_view(record))


def _melody_spans(record: dict[str, Any], lookups: Lookups) -> None:
    pinned_notes = {r["revision_id"]: pinned_view(r) for r in _pinned(record, "notes", lookups)}
    notes.check_melody_spans(record_view(record), _stream(record, lookups, ("archive.claim/melody_spans", "1")),
                             pinned_notes)


def _with_targets(check: Callable[..., Any]) -> Callable[[dict[str, Any], Lookups], Any]:
    return lambda record, lookups: check(record_view(record), targets(record, lookups.records))


def _with_operator(check: Callable[..., Any], contract: tuple[str, str]) -> Callable[[dict[str, Any], Lookups], Any]:
    return lambda record, lookups: check(record_view(record), targets(record, lookups.records),
                                         _operator(lookups, contract))


# Every contract specification 0.0.10 adds, and how World re-checks it. The
# conformance tests enumerate the catalogs against this table, so a contract
# a release adds cannot be projected unchecked.
RECHECKS: dict[tuple[str, str], Callable[[dict[str, Any], Lookups], Any]] = {
    CHORD_STACKS_2: lambda r, l: harmony.check_chord_stacks_v2(
        record_view(r), _stream(r, l, CHORD_STACKS_2), _one(r, "sequence", l)),
    CHORD_SEQUENCE_2: lambda r, l: harmony.check_chord_sequence_v2(record_view(r)),
    METER_LAYERS_2: lambda r, l: form.check_meter_layers_v2(
        record_view(r), _one(r, "stream", l), untimed_claim=_one(r, "meter", l), form_claim=_one(r, "form", l),
        sequence_claim=_one(r, "sequence", l)),
    JUDGMENT_2: _judgment,
    ("archive.claim/form", "1"): lambda r, l: form.check_form(record_view(r)),
    ("archive.claim/form_spans", "1"): lambda r, l: form.check_form_spans(
        record_view(r), _stream(r, l, ("archive.claim/form_spans", "1")), _one(r, "form", l)),
    ("archive.claim/key_sequence", "1"): _key_sequence,
    ("archive.claim/key_regions", "1"): lambda r, l: tonality.check_key_regions(
        record_view(r), _stream(r, l, ("archive.claim/key_regions", "1")), _one(r, "sequence", l),
        _one(r, "tuning", l)),
    ("archive.claim/tuning", "1"): lambda r, l: tonality.check_tuning(
        record_view(r), _stream(r, l, ("archive.claim/tuning", "1"))),
    ("archive.claim/span_annotation", "1"): _span_annotation,
    ("archive.claim/lead_sheet", "1"): _lead_sheet,
    ("archive.claim/document_source", "1"): _with_targets(documents.check_document_source),
    ("archive.claim/timed_lyrics", "1"): _with_targets(documents.check_timed_lyrics),
    ("archive.claim/sensitivity", "1"): lambda r, l: documents.check_sensitivity(
        record_view(r), targets(r, l.records), _agent_kind(r, l)),
    ("archive.claim/rights", "1"): _with_targets(documents.check_rights),
    ("archive.claim/notes", "1"): lambda r, l: notes.check_notes(
        record_view(r), _stream(r, l, ("archive.claim/notes", "1")), _one(r, "tuning", l)),
    ("archive.claim/melody", "1"): lambda r, l: notes.check_melody(record_view(r), _one(r, "meter", l)),
    ("archive.claim/melody_spans", "1"): _melody_spans,
    ("archive.claim/tracklist", "1"): _with_targets(catalogue.check_tracklist),
    ("archive.claim/recording_audio", "1"): _with_targets(catalogue.check_recording_audio),
    ("archive.claim/catalogue_correspondence", "1"): _with_targets(catalogue.check_correspondence),
    ("archive.claim/work_origin", "1"): _with_operator(circle.check_work_origin, ("archive.claim/work_origin", "1")),
    ("archive.claim/sharing_consent", "1"): _with_operator(circle.check_sharing_consent,
                                                           ("archive.claim/sharing_consent", "1")),
    ("archive.claim/export", "1"): _with_targets(exports.check_export),
}


def recheck(record: dict[str, Any], contract: tuple[str, str], lookups: Lookups) -> None:
    """Re-check one record by its contract's module; raise ContractGraphError."""
    try:
        RECHECKS[contract](record, lookups)
        envelope_rules.check(record, contract, lookups.entity_kind, lookups.records)
    except ContractGraphError:
        raise
    except (ValueError, KeyError, TypeError) as exc:
        raise ContractGraphError(f"{contract[0]} version {contract[1]} {record['revision_id']}: {exc}") from exc


# ---------------------------------------------------------------------------
# Projection rules where the context alone does not say how a value projects

# Where World names a field or role itself. `work` names a match's Work on
# earlier claims' graphs (the Asserted shape), so a described Work is
# `arch:described_work`, as chord sequences have it since 0.0.9; the others
# are fields the context leaves to the core context (`kind`, `label`, `role`,
# `at`, `name`), or terms an earlier shape already targets (`repeats` of a
# chord sequence, `tracks` of a selection, `reason` of a judgment).
TERMS: dict[tuple[str, str], str] = {
    ("*", "@work"): ARCH + "described_work",
    ("*", "label"): RDFS + "label",
    ("*", "version"): ARCH + "version",
    ("archive.claim/form", "sections.*.kind"): ARCH + "section_kind",
    ("archive.claim/form", "jumps.*.kind"): ARCH + "jump_kind",
    ("archive.claim/form", "repeats"): ARCH + "form_repeats",
    ("archive.claim/form_spans", "spans.*.kind"): ARCH + "section_kind",
    ("archive.claim/meter_layers", "layers.*.role"): ARCH + "layer_role",
    ("archive.claim/meter_layers", "modulations.*.kind"): ARCH + "modulation_kind",
    ("archive.claim/meter_layers", "modulations.*.bar"): ARCH + "modulated_bar",
    ("archive.claim/meter_layers", "layers.*.bars.*.chords.*.at"): ARCH + "bar_offset",
    ("archive.claim/notes", "notes.*.locator.at"): ARCH + "bar_offset",
    ("archive.claim/melody", "start.at"): ARCH + "bar_offset",
    ("archive.claim/sensitivity", "label"): ARCH + "tlp_label",
    ("archive.claim/sensitivity", "reason"): ARCH + "limit_reason",
    ("archive.claim/sharing_consent", "reason"): ARCH + "consent_reason",
    ("archive.claim/span_annotation", "kind"): ARCH + "mark_kind",
    ("archive.claim/lead_sheet", "parts.*.name"): ARCH + "part_name",
    ("archive.claim/export", "software"): ARCH + "export_software",
    ("archive.claim/export", "software.*.name"): FOAF + "name",
    ("archive.claim/export", "exporter.id"): "http://purl.org/dc/terms/identifier",
    ("archive.claim/tracklist", "media.*.tracks"): ARCH + "listed_tracks",
}

# Arrays whose order or repetition is the meaning, projected as one text.
JOINED: dict[tuple[str, str], str] = {
    ("archive.claim/meter_layers", "layers.*.bars.*.grouping"): "+",
    ("archive.claim/key_regions", "regions.*.readings.*.spelling"): " ",
    ("archive.claim/key_sequence", "regions.*.readings.*.spelling"): " ",
    ("archive.claim/notes", "strings"): " ",
    ("archive.claim/lead_sheet", "parts.*.strings"): " ",
}

# Objects whose keys are the asserter's own names, not terms: one rdf:JSON value.
OPAQUE = {("archive.claim/export", "options")}

# Values the context types @vocab that are words, not IRIs.
WORDS = {("archive.claim/work_origin", "origin")}

ITEM_NAMES = {"entries": "entry", "media": "medium", "uncertain": "uncertain", "drift": "drift",
              "software": "software", "chords": "chord", "readings": "reading"}


def item_name(field: str) -> str:
    """The name of one item of an array field in its node's IRI: spans, span=."""
    return ITEM_NAMES.get(field, field[:-1] if field.endswith("s") else field)


RIGHTS_SCHEME = ARCH + "rights"


def rights_concept(statement: str) -> str:
    """D-18's concept for a rights statement, arch:rights-{statement}."""
    return ARCH + "rights-" + statement


# ---------------------------------------------------------------------------
# Projecting


class _Graph:
    """One claim revision's graph and the terms its contract's context names."""

    def __init__(self, store: Store, claim: NamedNode, contract: tuple[str, str], core_terms: dict[str, Any],
                 contract_terms: dict[str, Any], record_uri: Callable[[str], NamedNode],
                 object_uri: Callable[[str], NamedNode]):
        self.store, self.claim, self.contract = store, claim, contract
        self.terms = {**core_terms, **contract_terms}
        # Only the contract's own terms name its fields; a core term (kind,
        # label) names an envelope's, so World names those itself (TERMS).
        self.contract_terms = frozenset(contract_terms)
        self.record_uri, self.object_uri = record_uri, object_uri

    def add(self, subject: NamedNode, predicate: str, value: Any, graph: NamedNode | None = None) -> None:
        self.store.add(Quad(subject, NamedNode(predicate), value, graph or self.claim))

    def expand(self, iri: str) -> str:
        if iri.startswith(("http://", "https://", "urn:")):
            return iri
        prefix, _, suffix = iri.partition(":")
        base = self.terms.get(prefix)
        base = base.get("@id") if isinstance(base, dict) else base
        if not suffix or not isinstance(base, str) or not base.endswith(("#", "/", ":")):
            raise ContractGraphError(f"{self.contract[0]} cannot expand {iri!r} through its context")
        return base + suffix

    def term(self, path: str, key: str) -> tuple[str, dict[str, Any]]:
        """The predicate of a field at `path` (dotted, * for an item), and its
        context entry."""
        for rule in ((self.contract[0], path), ("*", key)):
            if rule in TERMS:
                return TERMS[rule], {}
        entry = self.terms.get(key) if key in self.contract_terms else None
        iri = entry.get("@id") if isinstance(entry, dict) else entry
        if not isinstance(iri, str):
            raise ContractGraphError(f"{self.contract[0]} version {self.contract[1]} has no term for {path}")
        return self.expand(iri), entry if isinstance(entry, dict) else {}


def project(store: Store, claim: NamedNode, record: dict[str, Any], contract: tuple[str, str],
            core_terms: dict[str, Any], contract_terms: dict[str, Any], lookups: Lookups,
            record_uri: Callable[[str], NamedNode], object_uri: Callable[[str], NamedNode],
            domain_graph: NamedNode) -> None:
    """Re-check `record` by its contract's rules, then add its graph."""
    recheck(record, contract, lookups)
    emit(store, claim, record, contract, core_terms, contract_terms, record_uri, object_uri, domain_graph)


def emit(store: Store, claim: NamedNode, record: dict[str, Any], contract: tuple[str, str],
         core_terms: dict[str, Any], contract_terms: dict[str, Any], record_uri: Callable[[str], NamedNode],
         object_uri: Callable[[str], NamedNode], domain_graph: NamedNode) -> None:
    """Add the graph of a checked record."""
    graph = _Graph(store, claim, contract, core_terms, contract_terms, record_uri, object_uri)
    if contract not in SECOND_VERSIONS:
        graph.add(claim, RDF + "type", NamedNode(ARCH + "Claim"))
    _references(graph, record)
    payload = record.get("payload")
    if not isinstance(payload, dict):
        raise ContractGraphError(f"{contract[0]} {record['revision_id']} has no payload object")
    _fields(graph, claim, payload, "", record)
    if contract == CHORD_STACKS_2:
        # An absent end is asserted; projecting it says so, as for version 1.
        end = graph.term("spans.*.end", "end")[0]
        for index, span in enumerate(payload["spans"]):
            if "end" not in span:
                graph.add(NamedNode(claim.value + ":span=" + str(index)), end, Literal("asserted"))
    if contract == ("archive.claim/rights", "1"):
        _rights_concept(graph, payload["statement"], domain_graph)


def _references(graph: _Graph, record: dict[str, Any]) -> None:
    """Each pinned role as its context's predicate. `source` names the Artifact
    whose samples are counted, as prov:wasDerivedFrom, as 0.0.9's claims have
    it; a role the context leaves to the core context (`recording`,
    `release`, `artifact`) is a class there, and stays only in the records
    graph's reference statement."""
    for ref in record.get("refs", []):
        role = ref["role"]
        if role == "source":
            predicate = PROV + "wasDerivedFrom"
        elif role == "work":
            predicate = TERMS[("*", "@work")]
        elif role in graph.contract_terms:
            predicate = graph.term(role, role)[0]
        else:
            continue
        target = graph.record_uri(ref["revision_id"]) if ref.get("revision_id") else graph.object_uri(ref["id"])
        graph.add(graph.claim, predicate, target)


def _fields(graph: _Graph, owner: NamedNode, value: dict[str, Any], path: str, record: dict[str, Any]) -> None:
    contract = graph.contract
    for key, item in value.items():
        here = f"{path}.{key}" if path else key
        special = _special(graph, owner, here, key, item, record)
        if special:
            continue
        predicate, entry = graph.term(here, key)
        if (contract[0], here) in OPAQUE:
            graph.add(owner, predicate, Literal(json.dumps(item, sort_keys=True, separators=(",", ":")),
                                                datatype=NamedNode(RDF_JSON)))
        elif (contract[0], here) in JOINED:
            graph.add(owner, predicate, Literal(JOINED[(contract[0], here)].join(str(v) for v in item)))
        elif isinstance(item, dict):
            node = NamedNode(owner.value + ":" + key)
            graph.add(owner, predicate, node)
            _fields(graph, node, item, here, record)
        elif isinstance(item, list):
            for index, element in enumerate(item):
                if isinstance(element, dict):
                    node = NamedNode(owner.value + ":" + item_name(key) + "=" + str(index))
                    graph.add(owner, predicate, node)
                    graph.add(node, RDF + "value", Literal(index))
                    _fields(graph, node, element, here + ".*", record)
                else:
                    graph.add(owner, predicate, _scalar(graph, here, entry, element))
        else:
            graph.add(owner, predicate, _scalar(graph, here, entry, item))


def _scalar(graph: _Graph, path: str, entry: dict[str, Any], value: Any) -> Any:
    kind = entry.get("@type")
    if kind in ("@id", "@vocab") and (graph.contract[0], path) not in WORDS:
        return NamedNode(graph.expand(value))
    if isinstance(kind, str) and not kind.startswith("@"):
        return Literal(str(value), datatype=NamedNode(graph.expand(kind)))
    if isinstance(value, (bool, int, str)):
        return Literal(value)
    raise ContractGraphError(f"{graph.contract[0]} {path} holds a value World cannot project: {value!r}")


# Indices that name a node, of this claim (role None) or of the claim a role
# pins: its revision's `<{revision}:{item}={n}>`, as 0.0.9 names a placed
# span's sequence entry. Where several roles may anchor the index (a key
# sequence's regions range over a chord sequence or a form), the pinned one
# names it; with none pinned the index stays a number.
INDEXES: dict[tuple[tuple[str, str], str], tuple[tuple[str | None, str], ...]] = {
    (CHORD_STACKS_2, "spans.*.entry"): (("sequence", "entry"),),
    (CHORD_SEQUENCE_2, "repeats.*.first"): ((None, "entry"),),
    (CHORD_SEQUENCE_2, "repeats.*.last"): ((None, "entry"),),
    (METER_LAYERS_2, "modulations.*.layer"): ((None, "layer"),),
    (METER_LAYERS_2, "layers.*.bars.*.chords.*.entry"): (("sequence", "entry"),),
    (METER_LAYERS_2, "layers.*.bars.*.section"): (("form", "section"),),
    (JUDGMENT_2, "entries.first"): (("assessment_of", "entry"),),
    (JUDGMENT_2, "entries.last"): (("assessment_of", "entry"),),
    (("archive.claim/span_annotation", "1"), "entries.first"): (("sequence", "entry"),),
    (("archive.claim/span_annotation", "1"), "entries.last"): (("sequence", "entry"),),
    (("archive.claim/form_spans", "1"), "spans.*.section"): (("form", "section"),),
    (("archive.claim/key_regions", "1"), "regions.*.region"): (("sequence", "region"),),
    (("archive.claim/key_sequence", "1"), "regions.*.first"): (("chord_sequence", "entry"), ("form", "section")),
    (("archive.claim/key_sequence", "1"), "regions.*.last"): (("chord_sequence", "entry"), ("form", "section")),
    **{(("archive.claim/form", "1"), path): ((None, "section"),) for path in (
        "repeats.*.first", "repeats.*.last", "repeats.*.endings.*.first", "repeats.*.endings.*.last",
        "jumps.*.after", "jumps.*.to", "jumps.*.coda", "jumps.*.coda_from", "jumps.*.fine")},
}


def _special(graph: _Graph, owner: NamedNode, path: str, key: str, value: Any, record: dict[str, Any]) -> bool:
    """The values that name a node, of this claim or of a claim it pins, so
    a reader follows them as it follows 0.0.9's placed entries, and the
    values that name a concept."""
    contract, claim = graph.contract, graph.claim
    pinned: dict[str, list[dict[str, Any]]] = {}
    for ref in record.get("refs", []):
        if ref.get("revision_id"):
            pinned.setdefault(ref["role"], []).append(ref)

    def node_of(role: str | None) -> str | None:
        if role is None:
            return claim.value
        found = pinned.get(role, [])
        return graph.record_uri(found[0]["revision_id"]).value if len(found) == 1 else None

    for role, item in INDEXES.get((contract, path), ()):
        base = node_of(role)
        if base is not None and isinstance(value, int) and not isinstance(value, bool):
            graph.add(owner, graph.term(path, key)[0], NamedNode(f"{base}:{item}={value}"))
            return True
    if contract == METER_LAYERS_2 and path == "modulations.*.bar":
        layer = _item(owner, record, "modulations")["layer"]
        graph.add(owner, TERMS[(contract[0], path)], NamedNode(f"{claim.value}:layer={layer}:bar={value}"))
        return True
    if contract == METER_LAYERS_2 and path == "layers.*.bars.*.placed" and node_of("meter") is not None:
        # The written bar this bar was placed from, in the untimed meter it pins.
        graph.add(owner, graph.term(path, key)[0],
                  NamedNode(f"{node_of('meter')}:layer={value['layer']}:bar={value['bar']}"))
        return True
    if contract == ("archive.claim/melody_spans", "1") and path == "spans.*.notes":
        graph.add(owner, graph.term(path, key)[0], graph.record_uri(value))
        return True
    if contract == ("archive.claim/lead_sheet", "1") and path == "parts.*.document":
        # The index of the document reference the part was read from.
        documents_pinned = [r for r in record.get("refs", []) if r.get("role") == "document"]
        graph.add(owner, graph.term(path, key)[0], graph.record_uri(documents_pinned[value]["revision_id"]))
        return True
    if contract == ("archive.claim/rights", "1") and path == "statement":
        graph.add(owner, graph.term(path, key)[0], NamedNode(rights_concept(value)))
        return True
    return False


def _item(owner: NamedNode, record: dict[str, Any], field: str) -> dict[str, Any]:
    index = int(owner.value.rsplit("=", 1)[1])
    return record["payload"][field][index]


def _rights_concept(graph: _Graph, statement: str, domain: NamedNode) -> None:
    """The statement's concept of D-18's rights scheme, in the domain graph."""
    concept = NamedNode(rights_concept(statement))
    graph.add(NamedNode(RIGHTS_SCHEME), RDF + "type", NamedNode(SKOS + "ConceptScheme"), domain)
    graph.add(concept, RDF + "type", NamedNode(SKOS + "Concept"), domain)
    graph.add(concept, SKOS + "inScheme", NamedNode(RIGHTS_SCHEME), domain)
    graph.add(concept, SKOS + "notation", Literal(statement), domain)
    if statement != documents.PERSONAL_USE:
        graph.add(concept, SKOS + "exactMatch", NamedNode(documents.statement_uri(statement)), domain)
