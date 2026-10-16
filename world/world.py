#!/usr/bin/env python3
"""Build and query World's disposable RDF projection."""

from __future__ import annotations

import argparse
import gc
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

try:
    from pyoxigraph import (
        BlankNode,
        Literal,
        NamedNode,
        Quad,
        QueryBoolean,
        QuerySolutions,
        QueryTriples,
        RdfFormat,
        Store,
        serialize,
    )
except ImportError as exc:  # pragma: no cover - exercised by CLI installation
    raise SystemExit(
        "World needs pyoxigraph; install world/requirements.txt"
    ) from exc


PROV = "http://www.w3.org/ns/prov#"
OA = "http://www.w3.org/ns/oa#"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
DCTERMS = "http://purl.org/dc/terms/"
SKOS = "http://www.w3.org/2004/02/skos/core#"
FOAF = "http://xmlns.com/foaf/0.1/"
MUSICBRAINZ_WORK = re.compile(r"https://musicbrainz\.org/work/([0-9a-fA-F-]{36})\Z")
MBID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z")
PROJECTION_VERSION = 6
# Additions keep a projection's version (ADR 0059) but change what a full
# build writes, so each addition raises the version's revision. A generation
# built at an earlier revision is rebuilt in full rather than extended, or its
# older records would lack what the addition projects.
PROJECTION_REVISIONS = {6: 3}
XSD = "http://www.w3.org/2001/XMLSchema#"
UTC_INSTANT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?Z\Z")
DECIMAL = re.compile(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?\Z")
SPAN_CLASSES = {"music", "talk", "quiet", "unknown"}
# Core-context terms that name a record's own fields or shape classes. D-15
# makes `artifact` and `source` reference roles too, but a direct triple by
# such a role would give the record node a second, unrelated meaning for its
# predicate (arch:source is the imported-source JSON; arch:Artifact is a
# class). Those references keep their reified statement and get no direct
# triple (projection 6, revision 3).
ENVELOPE_TERMS = ("shape", "revision_id", "object_id", "by", "agent", "at", "payload", "refs", "source",
                  "contracts", "artifact", "entity", "claim", "assessment")
class ProjectionError(Exception):
    status = "incomplete"


class Unsupported(ProjectionError):
    status = "unsupported"


@dataclass(frozen=True)
class Projection:
    store: Store
    archive_id: str
    snapshot: str
    records_graph: NamedNode
    context_graph: NamedNode
    domain_graph: NamedNode
    version: int = 6


def nn(value: str) -> NamedNode:
    return NamedNode(value)


def json_literal(value: Any) -> Literal:
    return Literal(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        datatype=nn(RDF + "JSON"),
    )


def uri_component(value: str) -> str:
    return quote(value, safe="-._~")


def _required_text(value: dict[str, Any], key: str, where: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ProjectionError(f"{where}.{key} must be a non-empty string")
    return item


@dataclass(frozen=True)
class ExportData:
    """One `archive export` result, full or `--since` (ADR 0055).

    A full export has ``since`` None and no pinned records. An export since an
    earlier commit holds only the records added after it in ``records``, and
    the earlier records they pin in ``pinned``; pinned records are already
    projected and are read only to check and resolve the added ones.
    """

    snapshot: str
    archive_id: str
    spec: str
    context: dict[str, Any]
    records: list[dict[str, Any]]
    contracts: list[dict[str, Any]]
    pinned: list[dict[str, Any]]
    since: str | None


def _read_export(export: Any) -> ExportData:
    if not isinstance(export, dict):
        raise ProjectionError("export must be a JSON object")
    status = export.get("status")
    if status != "ok":
        if status == "unsupported":
            raise Unsupported("archive export reports unsupported input")
        raise ProjectionError(f"archive export is incomplete (status: {status!r})")
    snapshot = _required_text(export, "snapshot", "export")
    archive_id = _required_text(export, "archive_id", "export")
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", snapshot):
        raise ProjectionError("export.snapshot must be a full Git commit identifier")
    if not re.fullmatch(r"urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", archive_id):
        raise ProjectionError("export.archive_id must be Archive's UUID URN")
    spec = _required_text(export, "spec", "export")
    context = export.get("context")
    records = export.get("records")
    contracts = export.get("contracts")
    if not isinstance(context, dict):
        raise ProjectionError("export.context must be the committed core context object")
    if not isinstance(records, list) or not all(isinstance(x, dict) for x in records):
        raise ProjectionError("export.records must be an array of record envelopes")
    if not isinstance(contracts, list) or not all(isinstance(x, dict) for x in contracts):
        raise ProjectionError("export.contracts must be an array of contract bundles")
    since = export.get("since")
    pinned = export.get("pinned_records", [])
    if since is not None and (not isinstance(since, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", since)):
        raise ProjectionError("export.since must be a full Git commit identifier")
    if (since is None) != ("pinned_records" not in export):
        raise ProjectionError("export.since and export.pinned_records must appear together")
    if not isinstance(pinned, list) or not all(isinstance(x, dict) for x in pinned):
        raise ProjectionError("export.pinned_records must be an array of record envelopes")
    return ExportData(snapshot, archive_id, spec, context, records, contracts, pinned, since)


def _expand_iri(context: dict[str, Any], iri: str) -> str:
    raw = context.get("@context", context)
    if not isinstance(raw, dict):
        raise Unsupported("core context has no supported @context object")
    if iri.startswith(("http://", "https://", "urn:")):
        return iri
    if ":" in iri:
        prefix, suffix = iri.split(":", 1)
        base_entry = raw.get(prefix)
        base = base_entry.get("@id") if isinstance(base_entry, dict) else base_entry
        if isinstance(base, str) and base.endswith(("#", "/", ":")):
            return base + suffix
    raise Unsupported(f"cannot expand IRI term {iri!r} through the committed context")


def _predicate(context: dict[str, Any], term: str) -> str:
    """Expand an envelope term through its committed, offline JSON-LD context."""
    raw = context.get("@context", context)
    if not isinstance(raw, dict):
        raise Unsupported("core context has no supported @context object")
    entry = raw.get(term)
    iri = entry.get("@id") if isinstance(entry, dict) else entry
    if not isinstance(iri, str):
        raise Unsupported(f"core context does not define {term!r}")
    return _expand_iri(context, iri)


def _typed_time(context: dict[str, Any], timestamp: str) -> Literal:
    raw = context.get("@context", context)
    mapping = raw.get("at") if isinstance(raw, dict) else None
    datatype = mapping.get("@type") if isinstance(mapping, dict) else None
    if not isinstance(datatype, str):
        raise Unsupported("core context does not declare the datatype of at")
    return Literal(timestamp, datatype=nn(_expand_iri(context, datatype)))


def _add(store: Store, graph: NamedNode, subject: NamedNode, predicate: str, obj: Any) -> None:
    store.add(Quad(subject, nn(predicate), obj, graph))


def _record_uri(archive_id: str, revision: str) -> NamedNode:
    return nn(archive_id + "#revision=" + revision)


def _object_uri(object_id: str) -> NamedNode:
    return nn(object_id)


def _check_records(records: list[dict[str, Any]]) -> None:
    seen_revisions: set[str] = set()
    for i, record in enumerate(records):
        where = f"export.records[{i}]"
        revision = _required_text(record, "revision_id", where)
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", revision):
            raise ProjectionError(f"{where}.revision_id must be a SHA-256 revision identifier")
        _required_text(record, "object_id", where)
        shape = _required_text(record, "shape", where)
        _required_text(record, "by", where)
        _required_text(record, "agent", where)
        _required_text(record, "at", where)
        if shape not in {"artifact", "entity", "claim", "assessment"}:
            raise Unsupported(f"{where}.shape {shape!r} is not supported")
        if revision in seen_revisions:
            raise ProjectionError(f"duplicate record revision {revision}")
        seen_revisions.add(revision)
        if not isinstance(record.get("payload"), (dict, list, str, int, float, bool)) and record.get("payload") is not None:
            raise ProjectionError(f"{where}.payload is not JSON")
        for field in ("refs", "supersedes", "contracts"):
            if field in record and not isinstance(record[field], list):
                raise ProjectionError(f"{where}.{field} must be an array")


def graph_names(archive_id: str) -> tuple[NamedNode, NamedNode, NamedNode]:
    """The archive-wide records, context, and domain graphs (projection 6).

    They name no snapshot, so a later generation can extend an earlier one;
    each generation's metadata records the snapshot it projects (ADR 0055).
    """
    base = archive_id + "#world"
    return nn(base + ":records"), nn(base + ":context"), nn(base + ":domain")


def build_projection(export: Any, version: int = PROJECTION_VERSION) -> Projection:
    data = _read_export(export)
    if data.since is not None:
        raise ProjectionError("a full projection needs a full archive export, not one since an earlier commit")
    if version not in PROJECTORS:
        raise ProjectionNotServed(version, sorted(PROJECTORS))
    store = Store()
    PROJECTORS[version](store, data)
    records_graph, context_graph, domain_graph = graph_names(data.archive_id)
    return Projection(store, data.archive_id, data.snapshot, records_graph, context_graph, domain_graph, version)


def _project(store: Store, data: ExportData, known_object: Any = None) -> None:
    """Add the quads of ``data.records`` and of the context and contracts.

    A record's quads depend only on the record, the earlier records it pins
    (``data.pinned`` holds those not among ``data.records``), and the context
    and contracts it cites. Projecting the records added since an earlier
    commit into a copy of that commit's projection therefore gives the
    projection of the later commit (ADR 0055). Quads form a set, so adding the
    archive-wide context and contract quads again changes nothing.
    ``known_object`` answers whether an object outside the export already
    exists, for unpinned references to it.
    """
    archive_id, spec, context = data.archive_id, data.spec, data.context
    records, contracts = data.records, data.contracts
    known_records = records + data.pinned
    _check_records(known_records)
    core_terms = context.get("@context", context)
    contract_contexts: dict[tuple[str, str], dict[str, Any]] = {}
    available_contracts: set[tuple[str, str]] = set()
    for bundle in contracts:
        identifier = _required_text(bundle, "id", "contract")
        version = _required_text(bundle, "version", f"contract {identifier}")
        available_contracts.add((identifier, version))
        contract_context = bundle.get("context", {})
        if not isinstance(contract_context, dict):
            raise ProjectionError(f"contract {identifier} context must be an object")
        contract_contexts[(identifier, version)] = contract_context
    for record in known_records:
        for citation in record.get("contracts", []):
            if not isinstance(citation, dict):
                raise ProjectionError("record contract citation must be an object")
            key = (citation.get("id"), citation.get("version"))
            if key not in available_contracts:
                raise ProjectionError(f"unresolved contract citation {key!r}")
    base = archive_id + "#world"
    records_graph, context_graph, domain_graph = graph_names(archive_id)

    # The graph is an interpretation of evidence, and can be discarded and
    # rebuilt at any time.
    shape_predicate = _predicate(context, "shape")
    object_predicate = _predicate(context, "object_id")
    revision_predicate = _predicate(context, "revision_id")
    writer_predicate = _predicate(context, "by")
    agent_predicate = _predicate(context, "agent")
    at_predicate = _predicate(context, "at")
    payload_predicate = _predicate(context, "payload")
    refs_predicate = _predicate(context, "refs")
    supersedes_predicate = _predicate(context, "supersedes")
    source_predicate = _predicate(context, "source")
    contracts_predicate = _predicate(context, "contracts")
    envelope_predicates = {_predicate(context, term) for term in ENVELOPE_TERMS}

    objects = {_required_text(record, "object_id", "record") for record in known_records}
    records_by_revision = {_required_text(record, "revision_id", "record"): record for record in known_records}
    rev_ids = set(records_by_revision)
    for record in records:
        revision = _required_text(record, "revision_id", "record")
        object_id = _required_text(record, "object_id", "record")
        shape = record["shape"]
        subject = _record_uri(archive_id, revision)
        role_terms: dict[str, Any] = dict(core_terms)
        cited_role_terms: dict[str, Any] = {}
        for citation in record.get("contracts", []):
            key = (citation["id"], citation["version"])
            for term, definition in contract_contexts[key].items():
                previous = cited_role_terms.get(term)
                if previous is not None and previous != definition:
                    raise Unsupported(f"record {revision} contract contexts collide on term {term!r}")
                cited_role_terms[term] = definition
        role_terms.update(cited_role_terms)
        _add(store, records_graph, subject, RDF + "type", nn(PROV + "Entity"))
        _add(store, records_graph, subject, RDF + "type", nn(_predicate(context, shape)))
        _add(store, records_graph, subject, shape_predicate, nn(_predicate(context, shape)))
        _add(store, records_graph, subject, revision_predicate, Literal(revision))
        _add(store, records_graph, subject, object_predicate, _object_uri(object_id))
        _add(store, records_graph, subject, writer_predicate, nn(record["by"]))
        _add(store, records_graph, subject, agent_predicate, nn(record["agent"]))
        _add(store, records_graph, subject, at_predicate, _typed_time(context, record["at"]))
        _add(store, records_graph, subject, payload_predicate, json_literal(record.get("payload")))
        _add(store, records_graph, subject, DCTERMS + "type", Literal(shape))

        # Web Annotation gives claims and assessments an explicit body/target
        # while preserving each as a distinct immutable record revision.
        if shape in {"claim", "assessment"}:
            _add(store, records_graph, subject, RDF + "type", nn(OA + "Annotation"))
            _add(store, records_graph, subject, OA + "hasBody", json_literal(record.get("payload")))

        _add(store, records_graph, subject, PROV + "wasAttributedTo", nn(record["agent"]))
        _add(store, records_graph, subject, PROV + "generatedAtTime", _typed_time(context, record["at"]))
        _add(store, records_graph, nn(record["by"]), RDF + "type", nn(PROV + "SoftwareAgent"))

        for field, predicate in (("refs", refs_predicate), ("supersedes", supersedes_predicate)):
            for index, ref in enumerate(record.get(field, [])):
                if not isinstance(ref, dict):
                    raise ProjectionError(f"record {revision} {field}[{index}] must be an object")
                role = _required_text(ref, "role", f"record {revision} {field}[{index}]")
                target_id = _required_text(ref, "id", f"record {revision} {field}[{index}]")
                target_revision = ref.get("revision_id")
                if target_id not in objects and not (known_object is not None and known_object(target_id)):
                    raise ProjectionError(f"unresolved reference from {revision} to object {target_id}")
                target = _object_uri(target_id)
                if target_revision is not None:
                    if not isinstance(target_revision, str) or target_revision not in rev_ids:
                        raise ProjectionError(f"unresolved pinned reference from {revision} to revision {target_revision!r}")
                    pinned_record = records_by_revision[target_revision]
                    if pinned_record["object_id"] != target_id:
                        raise ProjectionError(
                            f"pinned reference from {revision} names object {target_id} but revision "
                            f"{target_revision} belongs to {pinned_record['object_id']}"
                        )
                    target = _record_uri(archive_id, target_revision)
                role_iri = _predicate({"@context": role_terms}, role)
                link = nn(f"{base}:reference:{uri_component(revision)}:{field}:{index}")
                _add(store, records_graph, subject, predicate, link)
                _add(store, records_graph, link, RDF + "type", nn(RDF + "Statement"))
                _add(store, records_graph, link, RDF + "subject", subject)
                _add(store, records_graph, link, RDF + "predicate", nn(role_iri))
                _add(store, records_graph, link, RDF + "object", target)
                _add(store, records_graph, link, _predicate(context, "role"), nn(role_iri))
                if role_iri not in envelope_predicates:
                    _add(store, records_graph, subject, role_iri, target)
                _add(store, records_graph, link, _predicate(context, "id"), Literal(target_id))
                if target_revision is not None:
                    _add(store, records_graph, link, _predicate(context, "revision_id"), Literal(target_revision))
                if shape in {"claim", "assessment"} and field == "refs":
                    _add(store, records_graph, subject, OA + "hasTarget", target)
                if role == "source" or role == "derived_from":
                    _add(store, records_graph, subject, PROV + "wasDerivedFrom", target)

        if "source" in record:
            _add(store, records_graph, subject, source_predicate, json_literal(record["source"]))
        if "contracts" in record:
            _add(store, records_graph, subject, contracts_predicate, json_literal(record["contracts"]))
            # The same citations as links to the cited bundles' nodes in the
            # context graph, so a query can select by contract without
            # matching text (projection 6, revision 2).
            for citation in record["contracts"]:
                _add(store, records_graph, subject, DCTERMS + "conformsTo",
                     contract_node(archive_id, citation["id"], citation["version"]))

    # Project the closed World claim bundles. Claims themselves
    # remain in records_graph; this graph adds navigable domain relations.
    local_scheme = nn(archive_id + "#catalogue")
    mb_scheme = nn(archive_id + "#catalogue:musicbrainz")
    _add(store, domain_graph, local_scheme, RDF + "type", nn(SKOS + "ConceptScheme"))
    _add(store, domain_graph, mb_scheme, RDF + "type", nn(SKOS + "ConceptScheme"))
    kind_scheme = nn(archive_id + "#catalogue:performance-kinds")
    for claim in records:
        if claim.get("shape") == "assessment":
            _project_review_assessment(store, archive_id, core_terms, contract_contexts, claim, records_by_revision)
            continue
        if claim.get("shape") != "claim":
            continue
        claim_ref = _record_uri(archive_id, claim["revision_id"])
        cited = {(c.get("id"), c.get("version")) for c in claim.get("contracts", []) if isinstance(c, dict)}
        _project_review_claim(store, archive_id, core_terms, contract_contexts, claim, cited,
                              records_by_revision, domain_graph, kind_scheme)
        _project_derivation_claim(store, archive_id, core_terms, contract_contexts, claim, cited,
                                  records_by_revision)
        if ("archive.claim/audio_selection", "1") in cited:
            payload = claim.get("payload")
            pieces = payload.get("pieces") if isinstance(payload, dict) and set(payload) == {"pieces"} else None
            refs = claim.get("refs")
            if not isinstance(pieces, list) or not pieces or not isinstance(refs, list) or len(refs) != len(pieces):
                raise ProjectionError(f"audio selection {claim['revision_id']} needs aligned, nonempty pieces and pins")
            for index, (piece, ref) in enumerate(zip(pieces, refs)):
                if not isinstance(piece, dict) or set(piece) != {"start_sample", "end_sample", "sample_rate"} \
                        or not all(type(piece[key]) is int for key in piece) \
                        or piece["start_sample"] < 0 or piece["end_sample"] <= piece["start_sample"] \
                        or piece["sample_rate"] <= 0 or not isinstance(ref, dict) \
                        or ref.get("role") != "piece" or not _valid_pinned_record(ref, records_by_revision, "artifact"):
                    raise ProjectionError(f"audio selection {claim['revision_id']} has an invalid piece or Artifact pin at {index}")
                piece_uri = nn(claim_ref.value + ":piece=" + str(index))
                source_uri = _record_uri(archive_id, ref["revision_id"])
                _add(store, claim_ref, claim_ref, _predicate({"@context": {**core_terms, **contract_contexts[("archive.claim/audio_selection", "1")]}}, "piece"), piece_uri)
                _add(store, claim_ref, piece_uri, RDF + "value", Literal(index))
                _add(store, claim_ref, piece_uri, PROV + "wasDerivedFrom", source_uri)
                for key in ("start_sample", "end_sample", "sample_rate"):
                    _add(store, claim_ref, piece_uri, _predicate({"@context": {**core_terms, **contract_contexts[("archive.claim/audio_selection", "1")]}}, key), Literal(piece[key]))
        # Occurrence v2 groups version 2 (multi-source) selections; v1 groups v1.
        for occurrence_version, selection_version in (("1", "1"), ("2", "2")):
            if ("archive.claim/song_occurrence", occurrence_version) not in cited:
                continue
            refs = claim.get("refs")
            if claim.get("payload") != {} or not isinstance(refs, list) or not refs:
                raise ProjectionError(f"song occurrence {claim['revision_id']} needs an empty payload and selections")
            pins = []
            for ref in refs:
                if not isinstance(ref, dict) or ref.get("role") != "selection" or not _valid_pinned_record(ref, records_by_revision, "claim"):
                    raise ProjectionError(f"song occurrence {claim['revision_id']} has an invalid selection pin")
                if not _cites(records_by_revision[ref["revision_id"]], "archive.claim/audio_selection", selection_version):
                    raise ProjectionError(f"song occurrence {claim['revision_id']} pins a Claim without the audio selection contract")
                pins.append((ref["id"], ref["revision_id"]))
            if pins != sorted(set(pins)):
                raise ProjectionError(f"song occurrence {claim['revision_id']} has noncanonical selection membership")
            selection_predicate = _predicate({"@context": {**core_terms, **contract_contexts[("archive.claim/song_occurrence", occurrence_version)]}}, "selection")
            for _, revision in pins:
                _add(store, claim_ref, claim_ref, selection_predicate, _record_uri(archive_id, revision))
        # performance_work v2 links an occurrence v1, and v3 an occurrence v2.
        for link_version, occurrence_version in (("2", "1"), ("3", "2")):
            if ("archive.claim/performance_work", link_version) not in cited:
                continue
            ref_by_role = _claim_refs_by_role(claim, {"performance", "work"})
            occurrence = ref_by_role["performance"]
            work = ref_by_role["work"]
            if claim.get("payload") != {} or not _valid_pinned_record(occurrence, records_by_revision, "claim") \
                    or not _valid_pinned_entity(work, records_by_revision, "musical_work"):
                raise ProjectionError(f"performance/work v{link_version} claim {claim['revision_id']} must pin a song occurrence and musical_work")
            if not _cites(records_by_revision[occurrence["revision_id"]], "archive.claim/song_occurrence", occurrence_version):
                raise ProjectionError(f"performance/work v{link_version} claim {claim['revision_id']} pins a Claim without the song occurrence contract")
            work_predicate = _predicate({"@context": {**core_terms, **contract_contexts[("archive.claim/performance_work", link_version)]}}, "work")
            _add(store, claim_ref, _record_uri(archive_id, occurrence["revision_id"]), work_predicate, _record_uri(archive_id, work["revision_id"]))
        if ("archive.claim/performance_work", "1") in cited:
            ref_by_role = _claim_refs_by_role(claim, {"performance", "work"})
            performance = ref_by_role.get("performance")
            work = ref_by_role.get("work")
            if not _valid_pinned_record(performance, records_by_revision, "claim") or not _valid_pinned_entity(work, records_by_revision, "musical_work"):
                raise ProjectionError(f"performance/work claim {claim['revision_id']} must pin a performance claim and musical_work")
            performance_uri = _object_uri(performance["id"])
            work_uri = _object_uri(work["id"])
            role_context = dict(core_terms)
            role_context.update(contract_contexts[("archive.claim/performance_work", "1")])
            work_predicate = _predicate({"@context": role_context}, "work")
            # Keep the relation inside the exact claim's named graph. This
            # exposes every assertion without creating an unlabeled aggregate.
            _add(store, claim_ref, performance_uri, work_predicate, work_uri)
        if ("archive.claim/work_musicbrainz_correspondence", "1") in cited:
            ref_by_role = _claim_refs_by_role(claim, {"work"})
            work = ref_by_role.get("work")
            payload = claim.get("payload")
            if not _valid_pinned_entity(work, records_by_revision, "musical_work"):
                raise ProjectionError(f"work correspondence {claim['revision_id']} must pin a musical_work")
            if not isinstance(payload, dict) or set(payload) != {"external_work", "mapping"}:
                raise ProjectionError(f"work correspondence {claim['revision_id']} has an invalid closed payload")
            external_work_value = payload.get("external_work")
            match = MUSICBRAINZ_WORK.fullmatch(external_work_value) if isinstance(external_work_value, str) else None
            mapping = payload.get("mapping")
            if (not match or not MBID.fullmatch(match.group(1)) or match.group(1) != match.group(1).lower()
                    or not isinstance(mapping, str) or mapping not in {"skos:exactMatch", "skos:closeMatch"}):
                raise ProjectionError(f"work correspondence {claim['revision_id']} must name a MusicBrainz Work URI and SKOS mapping")
            work_uri = _object_uri(work["id"])
            local_concept = nn(archive_id + "#concept=work:" + uri_component(work["id"]))
            external_work = nn(external_work_value)
            external_concept = nn(archive_id + "#concept=musicbrainz-work:" + match.group(1).lower())
            _add(store, domain_graph, local_concept, RDF + "type", nn(SKOS + "Concept"))
            _add(store, domain_graph, local_concept, SKOS + "inScheme", local_scheme)
            _add(store, domain_graph, local_concept, FOAF + "focus", work_uri)
            _add(store, domain_graph, external_concept, RDF + "type", nn(SKOS + "Concept"))
            _add(store, domain_graph, external_concept, SKOS + "inScheme", mb_scheme)
            _add(store, domain_graph, external_concept, FOAF + "focus", external_work)
            mapping_iri = mapping if mapping.startswith("http") else SKOS + mapping.split(":", 1)[1]
            _add(store, claim_ref, local_concept, mapping_iri, external_concept)

    # The projection says what it is, so a client can check the version it
    # was written for (ADR 0059). None of this depends on the snapshot, so an
    # incremental refresh adds the same quads again.
    projection_node = nn(base)
    arch_term = lambda name: _expand_iri(context, "arch:" + name)  # noqa: E731
    _add(store, context_graph, projection_node, RDF + "type", nn(arch_term("WorldProjection")))
    _add(store, context_graph, projection_node, arch_term("projection_version"), Literal(6))
    _add(store, context_graph, projection_node, DCTERMS + "conformsTo", nn(arch_term("world-projection-6")))
    _add(store, context_graph, projection_node, _predicate(context, "spec"), Literal(spec))

    # The context and contract bundles are committed definitions for this
    # projection, kept separately from projected record revisions.
    context_subject = nn(base + ":core-context")
    _add(store, context_graph, context_subject, RDF + "type", nn(PROV + "Entity"))
    _add(store, context_graph, context_subject, DCTERMS + "identifier", Literal(spec))
    _add(store, context_graph, context_subject, PROV + "value", json_literal(context))
    for bundle in contracts:
        identifier = _required_text(bundle, "id", "contract")
        version = _required_text(bundle, "version", f"contract {identifier}")
        subject = contract_node(archive_id, identifier, version)
        _add(store, context_graph, subject, RDF + "type", nn(PROV + "Entity"))
        _add(store, context_graph, subject, DCTERMS + "identifier", Literal(identifier))
        _add(store, context_graph, subject, _predicate(context, "version"), Literal(version))
        _add(store, context_graph, subject, PROV + "value", json_literal(bundle))


def contract_node(archive_id: str, identifier: str, version: str) -> NamedNode:
    """A contract bundle's node, <{archive}#world:contract:{id}:{version}>."""
    return nn(archive_id + "#world:contract:" + uri_component(identifier) + ":" + uri_component(version))


# The projection versions World builds, and the function that projects each
# (ADR 0059). During an overlap window the previous version's projector stays
# here beside the new one; PROJECTION_VERSION is the newest.
PROJECTORS: dict[int, Any] = {6: _project}


def _context_matches(store: Store, data: ExportData) -> bool:
    """Whether a projection was made under exactly this spec and core context.

    The context names the predicates of every record quad, so a generation
    made under another one cannot be extended (ADR 0055).
    """
    _, context_graph, _ = graph_names(data.archive_id)
    subject = nn(data.archive_id + "#world:core-context")
    values = {quad.object for quad in store.quads_for_pattern(subject, nn(PROV + "value"), None, context_graph)}
    specs = {quad.object for quad in store.quads_for_pattern(subject, nn(DCTERMS + "identifier"), None, context_graph)}
    return values == {json_literal(data.context)} and specs == {Literal(data.spec)}


PERFORMANCE_KINDS = {"original", "cover", "improvisation", "noodle", "talking", "transition", "incomplete", "unknown"}


def _cites(record: dict[str, Any], contract: str, version: str) -> bool:
    return any(isinstance(c, dict) and c.get("id") == contract and c.get("version") == version
               for c in record.get("contracts", []))


def _int_field(value: Any, key: str, minimum: int) -> int:
    item = value.get(key) if isinstance(value, dict) else None
    if type(item) is not int or item < minimum:
        raise ProjectionError(f"{key} must be an integer of at least {minimum}")
    return item


def _project_review_claim(store: Store, archive_id: str, core_terms: dict[str, Any],
                          contract_contexts: dict[tuple[str, str], dict[str, Any]], claim: dict[str, Any],
                          cited: set[tuple[Any, Any]], records_by_revision: dict[str, dict[str, Any]],
                          domain_graph: NamedNode, kind_scheme: NamedNode) -> None:
    """Project the specification 0.0.5 review claims inside each claim's graph.

    Every relation stays in the named graph of the exact Claim revision that
    asserted it, so competing titles, kinds, credits, and alignments remain
    separate and attributable. No winner is chosen.
    """
    revision = claim["revision_id"]
    claim_ref = _record_uri(archive_id, revision)
    payload = claim.get("payload")
    refs = claim.get("refs")
    if not isinstance(refs, list) or not all(isinstance(ref, dict) for ref in refs):
        raise ProjectionError(f"claim {revision} has malformed references")

    def term(contract: tuple[str, str], name: str) -> str:
        return _predicate({"@context": {**core_terms, **contract_contexts[contract]}}, name)

    def arch(name: str) -> str:
        return _expand_iri({"@context": core_terms}, "arch:" + name)

    def pin(ref: dict[str, Any], shape: str) -> NamedNode:
        if not _valid_pinned_record(ref, records_by_revision, shape):
            raise ProjectionError(f"claim {revision} has an invalid {ref.get('role')!r} pin")
        return _record_uri(archive_id, ref["revision_id"])

    def selection_pin() -> NamedNode:
        selections = [ref for ref in refs if ref.get("role") == "selection"]
        if len(selections) != 1:
            raise ProjectionError(f"claim {revision} must pin exactly one selection")
        target = records_by_revision.get(selections[0].get("revision_id"))
        if target is None or not _cites(target, "archive.claim/audio_selection", "2"):
            raise ProjectionError(f"claim {revision} must pin an audio selection version 2")
        return pin(selections[0], "claim")

    if ("archive.claim/audio_selection", "2") in cited:
        contract = ("archive.claim/audio_selection", "2")
        tracks = payload.get("tracks") if isinstance(payload, dict) and set(payload) == {"tracks"} else None
        pieces_refs = [ref for ref in refs if ref.get("role") == "piece"]
        if not isinstance(tracks, list) or not tracks:
            raise ProjectionError(f"audio selection {revision} needs a nonempty track list")
        position = 0
        for track_index, track in enumerate(tracks):
            pieces = track.get("pieces") if isinstance(track, dict) and set(track) == {"pieces"} else None
            if not isinstance(pieces, list) or not pieces:
                raise ProjectionError(f"audio selection {revision} track {track_index} needs spans")
            track_uri = nn(claim_ref.value + ":track=" + str(track_index))
            _add(store, claim_ref, claim_ref, term(contract, "tracks"), track_uri)
            _add(store, claim_ref, track_uri, RDF + "value", Literal(track_index))
            for piece_index, piece in enumerate(pieces):
                if position >= len(pieces_refs) or not isinstance(piece, dict) \
                        or set(piece) != {"start_sample", "end_sample", "sample_rate"}:
                    raise ProjectionError(f"audio selection {revision} has an invalid span at track {track_index}")
                start = _int_field(piece, "start_sample", 0)
                end = _int_field(piece, "end_sample", start + 1)
                rate = _int_field(piece, "sample_rate", 1)
                piece_uri = nn(track_uri.value + ":piece=" + str(piece_index))
                _add(store, claim_ref, track_uri, term(contract, "pieces"), piece_uri)
                _add(store, claim_ref, piece_uri, RDF + "value", Literal(piece_index))
                _add(store, claim_ref, piece_uri, PROV + "wasDerivedFrom", pin(pieces_refs[position], "artifact"))
                for key, value in (("start_sample", start), ("end_sample", end), ("sample_rate", rate)):
                    _add(store, claim_ref, piece_uri, term(contract, key), Literal(value))
                position += 1
        if position != len(pieces_refs):
            raise ProjectionError(f"audio selection {revision} needs one piece pin per span")
        for ref in refs:
            if ref.get("role") == "alignment":
                target = records_by_revision.get(ref.get("revision_id"))
                if target is None or not _cites(target, "archive.claim/capture_alignment", "1"):
                    raise ProjectionError(f"audio selection {revision} pins an alignment without its contract")
                _add(store, claim_ref, claim_ref, term(contract, "alignment"), pin(ref, "claim"))

    # Version 2 projects as version 1 does; only its run is typed (ADR 0067),
    # and the run stays in the records graph as prov:wasGeneratedBy.
    for alignment_version in ("1", "2"):
        if ("archive.claim/capture_alignment", alignment_version) not in cited:
            continue
        contract = ("archive.claim/capture_alignment", alignment_version)
        by_role = _claim_refs_by_role(claim, {"reference", "aligned"} | ({"run"} if any(r.get("role") == "run" for r in refs) else set()))
        if alignment_version == "2" and "run" in by_role:
            _pinned_claim_citing(by_role["run"], records_by_revision, "archive.claim/derivation_run", "1", revision)
        reference = pin(by_role["reference"], "artifact")
        aligned = pin(by_role["aligned"], "artifact")
        anchors = payload.get("anchors") if isinstance(payload, dict) else None
        if reference == aligned or not isinstance(anchors, list) or not anchors:
            raise ProjectionError(f"capture alignment {revision} needs two recordings and anchors")
        _add(store, claim_ref, claim_ref, term(contract, "reference"), reference)
        _add(store, claim_ref, claim_ref, term(contract, "aligned"), aligned)
        for key in ("reference_rate", "aligned_rate"):
            _add(store, claim_ref, claim_ref, term(contract, key), Literal(_int_field(payload, key, 1)))
        _add(store, claim_ref, claim_ref, term(contract, "max_error_samples"),
             Literal(_int_field(payload, "max_error_samples", 0)))
        previous: tuple[int, int] | None = None
        for index, anchor in enumerate(anchors):
            pair = (_int_field(anchor, "reference_sample", 0), _int_field(anchor, "aligned_sample", 0))
            if previous is not None and not (pair[0] > previous[0] and pair[1] > previous[1]):
                raise ProjectionError(f"capture alignment {revision} anchors must strictly increase")
            previous = pair
            anchor_uri = nn(claim_ref.value + ":anchor=" + str(index))
            _add(store, claim_ref, claim_ref, term(contract, "anchors"), anchor_uri)
            _add(store, claim_ref, anchor_uri, RDF + "value", Literal(index))
            _add(store, claim_ref, anchor_uri, term(contract, "reference_sample"), Literal(pair[0]))
            _add(store, claim_ref, anchor_uri, term(contract, "aligned_sample"), Literal(pair[1]))

    if ("archive.claim/selection_kind", "1") in cited:
        kind = payload.get("kind") if isinstance(payload, dict) and set(payload) == {"kind"} else None
        if kind not in PERFORMANCE_KINDS:
            raise ProjectionError(f"selection kind {revision} has an unknown kind")
        concept = nn(archive_id + "#concept=performance-kind:" + kind)
        _add(store, domain_graph, kind_scheme, RDF + "type", nn(SKOS + "ConceptScheme"))
        _add(store, domain_graph, concept, RDF + "type", nn(SKOS + "Concept"))
        _add(store, domain_graph, concept, SKOS + "inScheme", kind_scheme)
        _add(store, domain_graph, concept, SKOS + "notation", Literal(kind))
        _add(store, claim_ref, selection_pin(), arch("performance_kind"), concept)

    if ("archive.claim/selection_title", "1") in cited:
        title = payload.get("title") if isinstance(payload, dict) and set(payload) == {"title"} else None
        if not isinstance(title, str) or not title:
            raise ProjectionError(f"selection title {revision} needs a title")
        _add(store, claim_ref, selection_pin(), term(("archive.claim/selection_title", "1"), "title"), Literal(title))

    if ("archive.claim/artist_credit", "1") in cited:
        contract = ("archive.claim/artist_credit", "1")
        selection = selection_pin()
        artists = [ref for ref in refs if ref.get("role") == "artist"]
        if payload != {} or not artists or len({ref.get("id") for ref in artists}) != len(artists):
            raise ProjectionError(f"artist credit {revision} needs distinct artists and an empty payload")
        for index, ref in enumerate(artists):
            if not (_valid_pinned_entity(ref, records_by_revision, "person")
                    or _valid_pinned_entity(ref, records_by_revision, "organisation")):
                raise ProjectionError(f"artist credit {revision} must credit a person or organisation")
            credit = nn(claim_ref.value + ":credit=" + str(index))
            _add(store, claim_ref, selection, arch("credit"), credit)
            _add(store, claim_ref, credit, RDF + "value", Literal(index))
            _add(store, claim_ref, credit, term(contract, "artist"), _record_uri(archive_id, ref["revision_id"]))

    if ("archive.claim/session_parts", "1") in cited:
        contract = ("archive.claim/session_parts", "1")
        sessions = [ref for ref in refs if ref.get("role") == "session"]
        parts = [ref for ref in refs if ref.get("role") == "part"]
        if payload != {} or len(sessions) != 1 or not parts \
                or not _valid_pinned_entity(sessions[0], records_by_revision, "recording_session"):
            raise ProjectionError(f"session parts {revision} must pin one recording_session and its parts")
        session = _record_uri(archive_id, sessions[0]["revision_id"])
        for index, ref in enumerate(parts):
            part = nn(claim_ref.value + ":part=" + str(index))
            _add(store, claim_ref, session, term(contract, "part"), part)
            _add(store, claim_ref, part, RDF + "value", Literal(index))
            _add(store, claim_ref, part, PROV + "wasDerivedFrom", pin(ref, "artifact"))


def _project_review_assessment(store: Store, archive_id: str, core_terms: dict[str, Any],
                               contract_contexts: dict[tuple[str, str], dict[str, Any]], assessment: dict[str, Any],
                               records_by_revision: dict[str, dict[str, Any]]) -> None:
    """Project a judgment in its own graph; the judged claim is unchanged."""
    if not _cites(assessment, "archive.assessment/judgment", "1"):
        return
    revision = assessment["revision_id"]
    graph = _record_uri(archive_id, revision)
    contract = ("archive.assessment/judgment", "1")
    by_role = _claim_refs_by_role(assessment, {"assessment_of"})
    target = by_role["assessment_of"]
    payload = assessment.get("payload")
    if not _valid_pinned_record(target, records_by_revision, "claim") or not isinstance(payload, dict) \
            or payload.get("verdict") not in {"affirmed", "rejected", "withdrawn"} \
            or not set(payload) <= {"verdict", "reason"}:
        raise ProjectionError(f"judgment {revision} must judge a claim revision with a known verdict")
    context = {"@context": {**core_terms, **contract_contexts[contract]}}
    _add(store, graph, graph, _predicate(context, "assessment_of"), _record_uri(archive_id, target["revision_id"]))
    _add(store, graph, graph, _predicate(context, "verdict"), Literal(payload["verdict"]))
    if "reason" in payload:
        _add(store, graph, graph, _predicate(context, "reason"), Literal(payload["reason"]))


def _pinned_claim_citing(ref: Any, records_by_revision: dict[str, dict[str, Any]], contract: str, version: str,
                         revision: str) -> dict[str, Any]:
    """The Claim revision ``ref`` pins, which must cite ``contract`` ``version``."""
    if not _valid_pinned_record(ref, records_by_revision, "claim") \
            or not _cites(records_by_revision[ref["revision_id"]], contract, version):
        role = ref.get("role") if isinstance(ref, dict) else None
        raise ProjectionError(f"claim {revision} {role!r} must pin a Claim citing {contract} version {version}")
    return records_by_revision[ref["revision_id"]]


def _instant_order(value: str) -> tuple[str, Any]:
    """An RFC 3339 UTC instant as a comparable key; Door admits any number of
    fractional digits, which ``datetime`` would not."""
    from decimal import Decimal
    whole, _, fraction = value[:-1].partition(".")
    return whole, Decimal("0." + (fraction or "0"))


def _project_derivation_claim(store: Store, archive_id: str, core_terms: dict[str, Any],
                              contract_contexts: dict[tuple[str, str], dict[str, Any]], claim: dict[str, Any],
                              cited: set[tuple[Any, Any]], records_by_revision: dict[str, dict[str, Any]]) -> None:
    """Project specification 0.0.7's derivation runs and the claims runs make
    (ADRs 0064 to 0067) inside each claim's own graph.

    A run is a prov:Activity; a stream's facts hang from the Artifact revision
    they describe; each suggested span is a node; a match names what it
    compared, the Work, its corpus, score, and run. Disagreeing runs stay
    side by side, and nothing here is current.
    """
    revision = claim["revision_id"]
    claim_ref = _record_uri(archive_id, revision)
    payload = claim.get("payload")
    refs = claim["refs"]

    def context(contract: tuple[str, str]) -> dict[str, Any]:
        return {"@context": {**core_terms, **contract_contexts[contract]}}

    def term(contract: tuple[str, str], name: str) -> str:
        return _predicate(context(contract), name)

    def typed(contract: tuple[str, str], name: str, value: str) -> Literal:
        entry = context(contract)["@context"].get(name)
        datatype = entry.get("@type") if isinstance(entry, dict) else None
        if not isinstance(datatype, str):
            raise Unsupported(f"{contract[0]} version {contract[1]} does not declare the datatype of {name}")
        return Literal(value, datatype=nn(_expand_iri(context(contract), datatype)))

    def arch(name: str) -> str:
        return _expand_iri({"@context": core_terms}, "arch:" + name)

    def record(ref: dict[str, Any], shape: str) -> NamedNode:
        if not _valid_pinned_record(ref, records_by_revision, shape):
            raise ProjectionError(f"claim {revision} has an invalid {ref.get('role')!r} pin")
        return _record_uri(archive_id, ref["revision_id"])

    def run(ref: dict[str, Any]) -> NamedNode:
        _pinned_claim_citing(ref, records_by_revision, "archive.claim/derivation_run", "1", revision)
        return _record_uri(archive_id, ref["revision_id"])

    def method(contract: tuple[str, str]) -> None:
        value = payload.get("method") if isinstance(payload, dict) else None
        if not isinstance(value, dict) or set(value) != {"id", "version"} \
                or not all(isinstance(value[key], str) and value[key] for key in value):
            raise ProjectionError(f"claim {revision} needs a method with an id and version")
        node = nn(claim_ref.value + ":method")
        _add(store, claim_ref, claim_ref, term(contract, "method"), node)
        _add(store, claim_ref, node, DCTERMS + "identifier", Literal(value["id"]))
        _add(store, claim_ref, node, _predicate({"@context": core_terms}, "version"), Literal(value["version"]))

    if ("archive.claim/derivation_run", "1") in cited:
        contract = ("archive.claim/derivation_run", "1")
        if not isinstance(payload, dict) or set(payload) != {"method", "software", "started_at", "ended_at"}:
            raise ProjectionError(f"derivation run {revision} has an invalid closed payload")
        started, ended, software = payload["started_at"], payload["ended_at"], payload["software"]
        if not all(isinstance(value, str) and UTC_INSTANT.fullmatch(value) for value in (started, ended)) \
                or _instant_order(ended) < _instant_order(started):
            raise ProjectionError(f"derivation run {revision} needs ordered UTC instants")
        if not isinstance(software, list) or any(
                not isinstance(item, dict) or set(item) != {"name", "version"}
                or not all(isinstance(item[key], str) and item[key] for key in item) for item in software):
            raise ProjectionError(f"derivation run {revision} needs software as name and version pairs")
        parameters = [ref for ref in refs if ref.get("role") == "parameters"]
        if len(parameters) > 1 or any(ref.get("role") not in {"used", "parameters"} for ref in refs):
            raise ProjectionError(f"derivation run {revision} may pin only inputs and one parameters Artifact")
        _add(store, claim_ref, claim_ref, RDF + "type", nn(PROV + "Activity"))
        method(contract)
        _add(store, claim_ref, claim_ref, term(contract, "started_at"), typed(contract, "started_at", started))
        _add(store, claim_ref, claim_ref, term(contract, "ended_at"), typed(contract, "ended_at", ended))
        # `software` is a core term (D-15) naming prov:SoftwareAgent; each
        # program is an agent the run was associated with, in reported order.
        for index, item in enumerate(software):
            agent = nn(claim_ref.value + ":software=" + str(index))
            _add(store, claim_ref, claim_ref, PROV + "wasAssociatedWith", agent)
            _add(store, claim_ref, agent, RDF + "type", nn(_predicate({"@context": core_terms}, "software")))
            _add(store, claim_ref, agent, RDF + "value", Literal(index))
            _add(store, claim_ref, agent, FOAF + "name", Literal(item["name"]))
            _add(store, claim_ref, agent, _predicate({"@context": core_terms}, "version"), Literal(item["version"]))
        for ref in refs:
            if ref["role"] == "used":
                if not any(_valid_pinned_record(ref, records_by_revision, shape) for shape in ("artifact", "claim", "entity")):
                    raise ProjectionError(f"derivation run {revision} has an invalid 'used' pin")
                _add(store, claim_ref, claim_ref, term(contract, "used"), _record_uri(archive_id, ref["revision_id"]))
            else:
                _add(store, claim_ref, claim_ref, term(contract, "parameters"), record(ref, "artifact"))

    if ("archive.claim/audio_stream", "1") in cited:
        contract = ("archive.claim/audio_stream", "1")
        by_role = _claim_refs_by_role(claim, {"artifact", "run"})
        fields = {"stream_index", "codec", "sample_rate", "channel_count", "sample_count"}
        if not isinstance(payload, dict) or not fields <= set(payload) <= fields | {"bits_per_sample"} \
                or not isinstance(payload["codec"], str) or not payload["codec"]:
            raise ProjectionError(f"audio stream {revision} has an invalid closed payload")
        values = {"stream_index": _int_field(payload, "stream_index", 0), "codec": payload["codec"],
                  "sample_rate": _int_field(payload, "sample_rate", 1),
                  "channel_count": _int_field(payload, "channel_count", 1),
                  "sample_count": _int_field(payload, "sample_count", 1)}
        if "bits_per_sample" in payload:
            values["bits_per_sample"] = _int_field(payload, "bits_per_sample", 1)
        if values["channel_count"] > 64 or values.get("bits_per_sample", 1) > 64:
            raise ProjectionError(f"audio stream {revision} has a channel count or sample width above 64")
        artifact = record(by_role["artifact"], "artifact")
        # The facts hang from the Artifact revision they describe, so a
        # client reaches a recording's streams from the recording.
        stream = nn(claim_ref.value + ":stream")
        _add(store, claim_ref, artifact, arch("audio_stream"), stream)
        for key, value in values.items():
            _add(store, claim_ref, stream, term(contract, key), Literal(value))
        _add(store, claim_ref, stream, term(contract, "run"), run(by_role["run"]))

    if ("archive.claim/span_suggestions", "1") in cited:
        contract = ("archive.claim/span_suggestions", "1")
        by_role = _claim_refs_by_role(claim, {"source", "stream", "run"})
        source = record(by_role["source"], "artifact")
        stream_record = _pinned_claim_citing(by_role["stream"], records_by_revision, "archive.claim/audio_stream", "1", revision)
        measured = [ref for ref in stream_record.get("refs", []) if isinstance(ref, dict) and ref.get("role") == "artifact"]
        if len(measured) != 1 or (measured[0].get("id"), measured[0].get("revision_id")) != \
                (by_role["source"]["id"], by_role["source"]["revision_id"]):
            raise ProjectionError(f"span suggestions {revision} pin a stream of another Artifact revision")
        if not isinstance(payload, dict) or set(payload) != {"sample_rate", "spans"}:
            raise ProjectionError(f"span suggestions {revision} have an invalid closed payload")
        rate = _int_field(payload, "sample_rate", 1)
        if rate != _int_field(stream_record.get("payload"), "sample_rate", 1):
            raise ProjectionError(f"span suggestions {revision} differ from their stream's sample rate")
        count = _int_field(stream_record.get("payload"), "sample_count", 1)
        spans = payload["spans"]
        if not isinstance(spans, list) or not 1 <= len(spans) <= 10000:
            raise ProjectionError(f"span suggestions {revision} need 1 to 10000 spans")
        _add(store, claim_ref, claim_ref, PROV + "wasDerivedFrom", source)
        _add(store, claim_ref, claim_ref, term(contract, "stream"), _record_uri(archive_id, by_role["stream"]["revision_id"]))
        _add(store, claim_ref, claim_ref, term(contract, "run"), run(by_role["run"]))
        _add(store, claim_ref, claim_ref, term(contract, "sample_rate"), Literal(rate))
        previous_end = 0
        for index, span in enumerate(spans):
            if not isinstance(span, dict) or set(span) != {"start_sample", "end_sample", "class"} \
                    or span["class"] not in SPAN_CLASSES:
                raise ProjectionError(f"span suggestions {revision} have an invalid span at {index}")
            start = _int_field(span, "start_sample", previous_end)
            end = _int_field(span, "end_sample", start + 1)
            if end > count:
                raise ProjectionError(f"span suggestions {revision} span {index} ends past the stream's sample count")
            previous_end = end
            node = nn(claim_ref.value + ":span=" + str(index))
            _add(store, claim_ref, claim_ref, term(contract, "spans"), node)
            _add(store, claim_ref, node, RDF + "value", Literal(index))
            _add(store, claim_ref, node, term(contract, "start_sample"), Literal(start))
            _add(store, claim_ref, node, term(contract, "end_sample"), Literal(end))
            _add(store, claim_ref, node, term(contract, "class"), Literal(span["class"]))

    if ("archive.claim/work_match", "2") in cited:
        contract = ("archive.claim/work_match", "2")
        roles = [ref.get("role") for ref in refs]
        compared = [ref for ref in refs if ref.get("role") in {"occurrence", "selection"}]
        if len(compared) != 1 or roles.count("work") != 1 or roles.count("run") != 1 \
                or roles.count("parameters") > 1 or "corpus" not in roles \
                or not set(roles) <= {"occurrence", "selection", "work", "corpus", "parameters", "run"}:
            raise ProjectionError(f"work match {revision} must compare one occurrence or selection with a Work, corpus, and run")
        if not isinstance(payload, dict) or set(payload) != {"method", "score", "scale", "direction"} \
                or not isinstance(payload["score"], str) or not DECIMAL.fullmatch(payload["score"]) \
                or not isinstance(payload["scale"], str) or not payload["scale"] \
                or payload["direction"] not in {"higher_is_closer", "lower_is_closer"}:
            raise ProjectionError(f"work match {revision} has an invalid closed payload")
        [audio] = compared
        if audio["role"] == "occurrence":
            _pinned_claim_citing(audio, records_by_revision, "archive.claim/song_occurrence", "2", revision)
        else:
            _pinned_claim_citing(audio, records_by_revision, "archive.claim/audio_selection", "2", revision)
        _add(store, claim_ref, claim_ref, term(contract, audio["role"]), _record_uri(archive_id, audio["revision_id"]))
        for ref in refs:
            if ref["role"] == "work":
                if not _valid_pinned_entity(ref, records_by_revision, "musical_work"):
                    raise ProjectionError(f"work match {revision} must pin a musical_work")
                _add(store, claim_ref, claim_ref, term(contract, "work"), _record_uri(archive_id, ref["revision_id"]))
            elif ref["role"] == "corpus":
                _pinned_claim_citing(ref, records_by_revision, "archive.claim/song_occurrence", "2", revision)
                if ref["id"] == audio["id"]:
                    raise ProjectionError(f"work match {revision} compares an occurrence with itself")
                _add(store, claim_ref, claim_ref, term(contract, "corpus"), _record_uri(archive_id, ref["revision_id"]))
            elif ref["role"] == "parameters":
                _add(store, claim_ref, claim_ref, term(contract, "parameters"), record(ref, "artifact"))
            elif ref["role"] == "run":
                _add(store, claim_ref, claim_ref, term(contract, "run"), run(ref))
        method(contract)
        _add(store, claim_ref, claim_ref, term(contract, "score"), Literal(payload["score"], datatype=nn(XSD + "decimal")))
        _add(store, claim_ref, claim_ref, term(contract, "scale"), Literal(payload["scale"]))
        _add(store, claim_ref, claim_ref, term(contract, "direction"), Literal(payload["direction"]))


def _valid_pinned_entity(ref: Any, records_by_revision: dict[str, dict[str, Any]], kind: str) -> bool:
    if not isinstance(ref, dict) or not isinstance(ref.get("revision_id"), str):
        return False
    entity = records_by_revision.get(ref["revision_id"])
    return bool(entity and entity.get("object_id") == ref.get("id") and entity.get("shape") == "entity"
                and isinstance(entity.get("payload"), dict) and entity["payload"].get("kind") == kind)


def _valid_pinned_record(ref: Any, records_by_revision: dict[str, dict[str, Any]], shape: str) -> bool:
    if not isinstance(ref, dict) or not isinstance(ref.get("revision_id"), str):
        return False
    record = records_by_revision.get(ref["revision_id"])
    return bool(record and record.get("object_id") == ref.get("id") and record.get("shape") == shape)


def _claim_refs_by_role(claim: dict[str, Any], expected: set[str]) -> dict[str, dict[str, Any]]:
    refs = claim.get("refs", [])
    if not all(isinstance(ref, dict) for ref in refs):
        raise ProjectionError(f"claim {claim['revision_id']} has a malformed reference")
    by_role = {ref.get("role"): ref for ref in refs}
    if len(by_role) != len(refs) or set(by_role) != expected:
        raise ProjectionError(f"claim {claim['revision_id']} must have exactly these reference roles: {', '.join(sorted(expected))}")
    if any(not isinstance(ref.get("revision_id"), str) for ref in refs):
        raise ProjectionError(f"claim {claim['revision_id']} requires exact revision pins")
    return by_role


def nquads(projection: Projection) -> bytes:
    data = projection.store.dump(format=RdfFormat.N_QUADS)
    lines = sorted(line for line in data.decode("utf-8").splitlines() if line.strip())
    return ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")


def _run_export(source: Path, archive_command: str, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [archive_command, "export", *arguments],
            cwd=source,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise ProjectionError(f"could not run archive export: {exc}") from exc


def _load_export(source: Path, export_file: Path | None, archive_command: str) -> Any:
    if export_file is not None:
        try:
            return json.loads(export_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ProjectionError(f"archive export JSON is malformed: {exc}") from exc
    completed = _run_export(source, archive_command, [])
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"archive export exited with status {completed.returncode}"
        raise ProjectionError(detail)
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ProjectionError(f"archive export returned malformed JSON: {exc}") from exc


def _load_since_export(source: Path, since: str, archive_command: str) -> tuple[Any | None, str]:
    """Ask the archive what was added after ``since``.

    Returns the parsed export, or None with the reason when the archive cannot
    answer: ``since`` is not an ancestor of HEAD, or the command failed. The
    caller then rebuilds in full, which reports any real archive problem.
    """
    try:
        completed = _run_export(source, archive_command, ["--since", since])
    except ProjectionError as exc:
        return None, str(exc)
    if completed.returncode != 0:
        detail = (completed.stdout.strip() or completed.stderr.strip()
                  or f"archive export --since exited with status {completed.returncode}")
        return None, detail
    try:
        return json.loads(completed.stdout), ""
    except json.JSONDecodeError as exc:
        return None, f"archive export --since returned malformed JSON: {exc}"


def _pointer(store_root: Path, version: int) -> Path:
    """The pointer selecting the current generation of one projection version."""
    return store_root / f"CURRENT-p{version}"


def _read_generation(store_root: Path, selected: str) -> Projection:
    match = re.fullmatch(r"([0-9a-f]{40}|[0-9a-f]{64})-p([1-9][0-9]*)(?:r([2-9]|[1-9][0-9]+))?", selected)
    if not match:
        raise ProjectionError("World store pointer names a malformed generation")
    snapshot, generation_version = match.group(1), int(match.group(2))
    revision = int(match.group(3) or 1)
    generation = store_root / "versions" / selected
    try:
        metadata = json.loads((generation / "metadata.json").read_text(encoding="utf-8"))
        store = Store.read_only(str(generation / "dataset"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ProjectionError(f"World store generation {snapshot} is incomplete: {exc}") from exc
    if metadata.get("snapshot") != snapshot:
        raise ProjectionError("World store metadata does not match its pointer")
    if metadata.get("format") != 1:
        raise Unsupported("World store metadata format is unsupported")
    if metadata.get("projection_version") != generation_version:
        raise ProjectionError("World store projection version does not match its pointer")
    if metadata.get("projection_revision", 1) != revision:
        raise ProjectionError("World store projection revision does not match its pointer")
    return Projection(
        store,
        metadata["archive_id"],
        snapshot,
        nn(metadata["records_graph"]),
        nn(metadata["context_graph"]),
        nn(metadata["domain_graph"]),
        generation_version,
    )


def _read_current(store_root: Path, version: int | None = None) -> Projection:
    """Open the selected generation of ``version``, or of the newest version.

    ``CURRENT`` selects the newest projection version's generation, and
    ``CURRENT-p<N>`` selects version N's (ADR 0059). A store written before
    per-version pointers has only ``CURRENT``; it answers for the version its
    generation was built by and no other.
    """
    try:
        if version is not None and _pointer(store_root, version).exists():
            selected = _pointer(store_root, version).read_text(encoding="ascii").strip()
        else:
            selected = (store_root / "CURRENT").read_text(encoding="ascii").strip()
    except OSError as exc:
        raise ProjectionError(f"World store has no readable CURRENT generation: {exc}") from exc
    projection = _read_generation(store_root, selected)
    if version is not None and projection.version != version:
        raise ProjectionNotServed(version, served_projections(store_root))
    return projection


def selected_generation(store_root: Path, version: int) -> str | None:
    """The generation that answers for ``version`` now, by name, or None when
    the version is not served. Reads only the pointer: cheap enough to ask
    before every query, and a name is never reused for other contents."""
    pointer = _pointer(store_root, version)
    try:
        selected = (pointer if pointer.exists() else store_root / "CURRENT").read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return None
    match = re.fullmatch(r"[0-9a-f]{40,64}-p([1-9][0-9]*)(?:r[0-9]+)?", selected)
    return selected if match and int(match.group(1)) == version else None


def served_projections(store_root: Path) -> list[int]:
    """The projection versions this store can answer, from its pointers."""
    versions = set()
    for pointer in store_root.glob("CURRENT-p*"):
        match = re.fullmatch(r"CURRENT-p([1-9][0-9]*)", pointer.name)
        if match:
            versions.add(int(match.group(1)))
    try:
        selected = (store_root / "CURRENT").read_text(encoding="ascii").strip()
    except FileNotFoundError:
        selected = ""
    match = re.fullmatch(r"[0-9a-f]{40,64}-p([1-9][0-9]*)(?:r[0-9]+)?", selected)
    if match:
        versions.add(int(match.group(1)))
    return sorted(versions)


class ProjectionNotServed(ProjectionError):
    """A request for a projection version the store does not hold."""

    status = "refused"

    def __init__(self, version: int, served: list[int]):
        super().__init__(f"projection {version} is not served; served: {', '.join(map(str, served)) or 'none'}")
        self.version = version
        self.served = served


def _generation_id(snapshot: str, version: int, revision: int) -> str:
    """A generation's directory name; revision 1 keeps the original form."""
    return f"{snapshot}-p{version}" + (f"r{revision}" if revision > 1 else "")


def _generation_metadata(archive_id: str, snapshot: str, version: int = PROJECTION_VERSION) -> dict[str, Any]:
    records_graph, context_graph, domain_graph = graph_names(archive_id)
    revision = PROJECTION_REVISIONS.get(version, 1)
    return {
        "format": 1,
        "projection_version": version,
        **({"projection_revision": revision} if revision > 1 else {}),
        "archive_id": archive_id,
        "snapshot": snapshot,
        "records_graph": records_graph.value,
        "context_graph": context_graph.value,
        "domain_graph": domain_graph.value,
    }


def _prepare_store_root(source: Path, store_root: Path) -> Path:
    archive_root = source.resolve()
    resolved_store = store_root.resolve()
    if resolved_store == archive_root or archive_root in resolved_store.parents:
        raise Unsupported("World's regenerable store must live outside the archive")
    store_root.mkdir(parents=True, exist_ok=True)
    versions = store_root / "versions"
    versions.mkdir(exist_ok=True)
    return versions


def _replace_pointer(store_root: Path, pointer_path: Path, generation_id: str) -> None:
    """Point ``pointer_path`` at a generation. Replacing this small file is
    atomic; readers see either the previous complete generation or the new
    one."""
    try:
        selected = pointer_path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        selected = None
    if selected == generation_id:
        return
    pointer_fd, pointer_name = tempfile.mkstemp(prefix=".CURRENT-", dir=store_root)
    try:
        with os.fdopen(pointer_fd, "w", encoding="ascii") as pointer:
            pointer.write(generation_id + "\n")
            pointer.flush()
            os.fsync(pointer.fileno())
        os.replace(pointer_name, pointer_path)
    except Exception:
        try:
            os.unlink(pointer_name)
        except FileNotFoundError:
            pass
        raise


def _remove_staging(staging: Path) -> None:
    """Remove a failed build's staging directory. The engine may still be
    closing files in it, so try again briefly; a hidden staging directory
    that survives is never read as a generation."""
    for _ in range(20):
        gc.collect()
        shutil.rmtree(staging, ignore_errors=True)
        if not staging.exists():
            return
        time.sleep(0.05)


def _install_generation(store_root: Path, archive_id: str, snapshot: str, fill: Any,
                        version: int = PROJECTION_VERSION) -> None:
    """Make the immutable generation for ``snapshot`` exist, then select it.

    ``fill(path)`` creates the dataset at ``path`` in a staging directory; the
    generation appears under ``versions/`` only once complete. An existing
    generation is checked and reused, never rewritten. Any failure leaves the
    previous generation selected.
    """
    versions = store_root / "versions"
    generation_id = _generation_id(snapshot, version, PROJECTION_REVISIONS.get(version, 1))
    destination = versions / generation_id
    expected = _generation_metadata(archive_id, snapshot, version)
    if destination.exists():
        try:
            metadata = json.loads((destination / "metadata.json").read_text(encoding="utf-8"))
            existing = Store.read_only(str(destination / "dataset"))
            del existing
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ProjectionError(f"existing World generation is incomplete: {exc}") from exc
        if any(metadata.get(key) != value for key, value in expected.items()):
            raise ProjectionError("the selected World generation has incompatible metadata")
    else:
        staging = Path(tempfile.mkdtemp(prefix=".world-build-", dir=store_root))
        try:
            fill(staging / "dataset")
            gc.collect()
            (staging / "metadata.json").write_text(
                json.dumps(expected, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            os.replace(staging, destination)
        except BaseException:
            _remove_staging(staging)
            raise

    _replace_pointer(store_root, _pointer(store_root, version), generation_id)
    if version == PROJECTION_VERSION:
        _replace_pointer(store_root, store_root / "CURRENT", generation_id)


def rebuild_store(
    source: Path,
    store_root: Path,
    export_file: Path | None = None,
    archive_command: str = "archive",
    version: int = PROJECTION_VERSION,
) -> Projection:
    """Build a complete immutable generation of ``version``, then atomically
    select it."""
    projection = build_projection(_load_export(source, export_file, archive_command), version)
    _prepare_store_root(source, store_root)

    def fill(path: Path) -> None:
        dataset = Store(path)
        dataset.extend(projection.store)
        dataset.flush()
        del dataset

    _install_generation(store_root, projection.archive_id, projection.snapshot, fill, version)
    return _read_current(store_root, version)


def _selected_metadata(store_root: Path, version: int) -> dict[str, Any] | None:
    """The selected generation's metadata, or None when there is none to extend.

    Absence is the only quiet answer: a pointer or generation that exists but
    cannot be read is an error, never a reason to rebuild silently.
    """
    pointer = _pointer(store_root, version)
    if not pointer.exists():
        pointer = store_root / "CURRENT"
    try:
        selected = pointer.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return None
    try:
        metadata = json.loads((store_root / "versions" / selected / "metadata.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(metadata, dict) or selected != _generation_id(
            str(metadata.get("snapshot")), metadata.get("projection_version"), metadata.get("projection_revision", 1)):
        return None
    return metadata


@dataclass(frozen=True)
class Refresh:
    """What a refresh selected, how (``incremental``, ``full``, or
    ``unchanged``), and why a full rebuild was needed."""

    projection: Projection
    mode: str
    reason: str = ""


def refresh_store(
    source: Path,
    store_root: Path,
    archive_command: str = "archive",
    version: int = PROJECTION_VERSION,
) -> Refresh:
    """Bring one projection version up to the archive's HEAD, incrementally
    when possible.

    When the selected generation was built by this projection version for
    this archive from an ancestor of HEAD, under the same core context, copy
    it and project only the records added since (ADR 0055). In any other case
    rebuild in full. A failure leaves the previous generation selected.
    """
    _prepare_store_root(source, store_root)
    projector = PROJECTORS[version]

    def full(reason: str) -> Refresh:
        return Refresh(rebuild_store(source, store_root, archive_command=archive_command, version=version),
                       "full", reason)

    current = _selected_metadata(store_root, version)
    if current is None:
        return full("no selected generation")
    if current.get("format") != 1 or current.get("projection_version") != version:
        return full("the selected generation has another projection version")
    if current.get("projection_revision", 1) != PROJECTION_REVISIONS.get(version, 1):
        return full(f"the selected generation predates an addition to projection {version}")
    if current != _generation_metadata(str(current.get("archive_id")), str(current.get("snapshot")), version):
        return full("the selected generation has unexpected metadata")
    exported, refusal = _load_since_export(source, current["snapshot"], archive_command)
    if exported is None:
        return full("archive export --since refused: " + refusal)
    data = _read_export(exported)
    if data.since != current["snapshot"] or data.archive_id != current["archive_id"]:
        return full("archive export --since answered for another archive or commit")
    if data.snapshot == current["snapshot"]:
        if data.records:
            raise ProjectionError("archive export --since HEAD returned records")
        # Keep history quiet: nothing was added, so nothing is written.
        _install_generation(store_root, data.archive_id, data.snapshot, None, version)
        return Refresh(_read_current(store_root, version), "unchanged")
    previous = store_root / "versions" / _generation_id(current["snapshot"], version, PROJECTION_REVISIONS.get(version, 1)) / "dataset"
    try:
        source_store = Store.read_only(str(previous))
    except OSError as exc:
        return full(f"the selected generation is unreadable: {exc}")
    if not _context_matches(source_store, data):
        del source_store
        return full("the specification or core context changed")
    records_graph, _, _ = graph_names(data.archive_id)
    object_predicate = nn(_predicate(data.context, "object_id"))

    def fill(path: Path) -> None:
        # A backup of an on-disk store hard-links its immutable files, so the
        # copy costs little and leaves the previous generation untouched.
        source_store.backup(str(path))
        dataset = Store(path)

        def known_object(object_id: str) -> bool:
            # An unpinned reference may name an object only earlier records hold.
            return any(True for _ in dataset.quads_for_pattern(None, object_predicate, nn(object_id), records_graph))

        try:
            projector(dataset, data, known_object)
            dataset.flush()
        finally:
            # Close the copy before the staging directory is kept or removed.
            del dataset

    try:
        _install_generation(store_root, data.archive_id, data.snapshot, fill, version)
    finally:
        del source_store
    return Refresh(_read_current(store_root, version), "incremental")


def refresh_served(source: Path, store_root: Path, archive_command: str = "archive") -> dict[int, Refresh]:
    """Refresh every projection version World serves (ADR 0059), and stop
    serving any other: its pointer is removed, its generations stay until the
    store is deleted."""
    refreshed = {version: refresh_store(source, store_root, archive_command, version)
                 for version in sorted(PROJECTORS)}
    for pointer in store_root.glob("CURRENT-p*"):
        match = re.fullmatch(r"CURRENT-p([1-9][0-9]*)", pointer.name)
        if match and int(match.group(1)) not in PROJECTORS:
            pointer.unlink()
    return refreshed


SPARQL_RESULTS_JSON = "application/sparql-results+json"
N_TRIPLES = "application/n-triples"
TURTLE = "text/turtle"
JSON_LD = "application/ld+json"
# The formats a graph answer (CONSTRUCT, DESCRIBE) can take. Adding one keeps
# the projection version; N-Triples stays the default (ADR 0059).
GRAPH_FORMATS = {N_TRIPLES: RdfFormat.N_TRIPLES, TURTLE: RdfFormat.TURTLE, JSON_LD: RdfFormat.JSON_LD}
XSD_STRING = "http://www.w3.org/2001/XMLSchema#string"
RDF_LANG_STRING = RDF + "langString"


class QueryRefused(ProjectionError):
    """A query World will not answer, such as one that leaves the store."""

    status = "refused"


class TooManyRows(ProjectionError):
    """An answer larger than the caller's bound; refused, never truncated."""

    status = "refused"


@dataclass(frozen=True)
class QueryAnswer:
    """A standard answer: SPARQL 1.1 Query Results JSON for SELECT and ASK;
    for CONSTRUCT and DESCRIBE, N-Triples or Turtle text, or expanded JSON-LD
    as a JSON value (ADR 0059)."""

    media_type: str
    data: Any


# Strings, IRIs, and comments, which may contain the word SERVICE without
# being the keyword.
_SPARQL_OPAQUE = re.compile(
    r"'''(?:[^'\\]|\\.|'(?!''))*'''"
    r'|"""(?:[^"\\]|\\.|"(?!""))*"""'
    r"|'(?:[^'\\\n]|\\.)*'"
    r'|"(?:[^"\\\n]|\\.)*"'
    r'|<[^<>"{}|^`\\\s]*>'
    r"|#[^\n]*"
)
_SERVICE = re.compile(r"(?<![\w:?$])SERVICE(?![\w:])", re.IGNORECASE)


def refuse_federation(sparql: str) -> None:
    """Refuse SERVICE: a World answer comes only from World's own store.

    The engine would otherwise fetch any URL a query names, from the archive
    machine. The check errs towards refusing.
    """
    if _SERVICE.search(_SPARQL_OPAQUE.sub(" ", sparql)):
        raise QueryRefused("SERVICE is not allowed: World answers only from its own store")


def term_json(term: Any) -> dict[str, str]:
    """One RDF term in SPARQL 1.1 Query Results JSON form."""
    if isinstance(term, NamedNode):
        return {"type": "uri", "value": term.value}
    if isinstance(term, BlankNode):
        return {"type": "bnode", "value": term.value}
    if isinstance(term, Literal):
        answer = {"type": "literal", "value": term.value}
        if term.language:
            answer["xml:lang"] = term.language
        elif term.datatype.value not in (XSD_STRING, RDF_LANG_STRING):
            answer["datatype"] = term.datatype.value
        return answer
    raise Unsupported(f"World cannot express a {type(term).__name__} term in query results")


def query(projection: Projection, sparql: str, max_rows: int | None = None,
          rdf_format: str = N_TRIPLES) -> QueryAnswer:
    """Answer one read-only query in a standard result format.

    ``max_rows`` bounds solutions or triples. An answer that would exceed it
    is refused, never truncated, and reading stops at the bound.
    ``rdf_format`` is the media type of a graph answer, one of GRAPH_FORMATS;
    SELECT and ASK answers are always SPARQL Results JSON.
    """
    if rdf_format not in GRAPH_FORMATS:
        raise QueryRefused(f"World cannot write a graph as {rdf_format!r}; it writes {', '.join(GRAPH_FORMATS)}")
    refuse_federation(sparql)
    result = projection.store.query(sparql)
    if isinstance(result, QueryBoolean):
        return QueryAnswer(SPARQL_RESULTS_JSON, {"head": {}, "boolean": bool(result)})
    if isinstance(result, QuerySolutions):
        names = [variable.value for variable in result.variables]
        bindings: list[dict[str, dict[str, str]]] = []
        for row in result:
            if max_rows is not None and len(bindings) >= max_rows:
                raise TooManyRows(f"the query returned more than {max_rows} rows")
            bindings.append({name: term_json(row[name]) for name in names if row[name] is not None})
        return QueryAnswer(SPARQL_RESULTS_JSON, {"head": {"vars": names}, "results": {"bindings": bindings}})
    if isinstance(result, QueryTriples):
        triples = []
        for triple in result:
            if max_rows is not None and len(triples) >= max_rows:
                raise TooManyRows(f"the query returned more than {max_rows} triples")
            triples.append(triple)
        text = serialize(triples, format=GRAPH_FORMATS[rdf_format]).decode("utf-8")
        return QueryAnswer(rdf_format, json.loads(text) if rdf_format == JSON_LD else text)
    raise Unsupported("SPARQL query returned an unsupported result kind")


def musicbrainz_work_candidate(mbid: str, timeout: float = 10.0) -> dict[str, Any]:
    """Fetch one MusicBrainz Work as a disposable candidate; never admit it."""
    if not MBID.fullmatch(mbid):
        raise ProjectionError("MusicBrainz Work lookup requires a UUID MBID")
    work_uri = "https://musicbrainz.org/work/" + mbid.lower()
    request = urllib.request.Request(
        "https://musicbrainz.org/ws/2/work/" + mbid.lower() + "?fmt=json",
        headers={"User-Agent": "Archive-World/1.0 (targeted Work candidate lookup)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"status": "unavailable", "work": work_uri, "detail": str(exc)}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"status": "unavailable", "work": work_uri, "detail": f"MusicBrainz returned malformed JSON: {exc}"}
    if not isinstance(data, dict) or data.get("id", "").lower() != mbid.lower() or not isinstance(data.get("title"), str):
        return {"status": "unavailable", "work": work_uri, "detail": "MusicBrainz response did not contain the requested Work"}
    fields = {key: data[key] for key in ("id", "title", "disambiguation", "type", "first-release-date") if key in data}
    return {"status": "candidate", "work": work_uri, "source": "MusicBrainz", "retrieved_at": datetime.now(timezone.utc).isoformat(), "data": fields,
            "notice": "Disposable suggestion only; no Archive claim was created."}


def _main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="world", description="Build and query Archive's disposable RDF projection")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_parser = subparsers.add_parser("build", help="project one archive export to deterministic N-Quads")
    build_parser.add_argument("export", type=Path)
    build_parser.add_argument("--output", "-o", type=Path)
    rebuild_parser = subparsers.add_parser("rebuild", help="rebuild a disposable store from committed archive evidence")
    rebuild_parser.add_argument("archive_root", type=Path)
    rebuild_parser.add_argument("--store", required=True, type=Path)
    rebuild_parser.add_argument("--archive-command", default="archive")
    rebuild_parser.add_argument("--export-file", type=Path, help="consume a captured archive export instead of running archive export")
    refresh_parser = subparsers.add_parser(
        "refresh", help="extend the selected generation with the records added since it, or rebuild in full")
    refresh_parser.add_argument("archive_root", type=Path)
    refresh_parser.add_argument("--store", required=True, type=Path)
    refresh_parser.add_argument("--archive-command", default="archive")
    query_parser = subparsers.add_parser("query", help="run SPARQL against the selected World store generation")
    query_parser.add_argument("--store", required=True, type=Path)
    query_parser.add_argument("--query-file", "-q", required=True, type=Path)
    query_parser.add_argument("--projection", type=int, help="the projection version to answer from (default: the newest)")
    query_parser.add_argument("--rdf-format", choices=list(GRAPH_FORMATS), default=N_TRIPLES,
                              help="the media type of a CONSTRUCT or DESCRIBE answer")
    candidate_parser = subparsers.add_parser("musicbrainz-work", help="fetch one disposable MusicBrainz Work candidate by MBID")
    candidate_parser.add_argument("mbid")
    for unsupported_name, description in (
        ("current", "no accepted per-kind rule defines a winning current claim"),
        ("promote", "World does not submit claims to Door; claim admission is not implemented here"),
    ):
        unsupported_parser = subparsers.add_parser(unsupported_name, help=description)
        unsupported_parser.set_defaults(unsupported_detail=description)
    args = parser.parse_args(argv)
    try:
        if hasattr(args, "unsupported_detail"):
            raise Unsupported(args.unsupported_detail)
        if args.command == "build":
            projection = build_projection(json.loads(args.export.read_text(encoding="utf-8")))
            output = nquads(projection)
            if args.output:
                args.output.write_bytes(output)
            else:
                sys.stdout.buffer.write(output)
        elif args.command == "rebuild":
            for version in sorted(PROJECTORS):
                projection = rebuild_store(
                    args.archive_root,
                    args.store,
                    export_file=args.export_file,
                    archive_command=args.archive_command,
                    version=version,
                )
            json.dump({"status": "ok", "archive_id": projection.archive_id, "snapshot": projection.snapshot,
                       "projections": sorted(PROJECTORS)}, sys.stdout)
            sys.stdout.write("\n")
        elif args.command == "refresh":
            served = refresh_served(args.archive_root, args.store, archive_command=args.archive_command)
            refreshed = served[PROJECTION_VERSION]
            report = {"status": "ok", "archive_id": refreshed.projection.archive_id,
                      "snapshot": refreshed.projection.snapshot, "refresh": refreshed.mode,
                      "projections": {str(version): {"refresh": item.mode, **({"reason": item.reason} if item.reason else {})}
                                      for version, item in served.items()}}
            if refreshed.reason:
                report["reason"] = refreshed.reason
            json.dump(report, sys.stdout, sort_keys=True)
            sys.stdout.write("\n")
        elif args.command == "musicbrainz-work":
            json.dump(musicbrainz_work_candidate(args.mbid), sys.stdout, ensure_ascii=False, sort_keys=True, indent=2)
            sys.stdout.write("\n")
        else:
            projection = _read_current(args.store, args.projection)
            answer = query(projection, args.query_file.read_text(encoding="utf-8"), rdf_format=args.rdf_format)
            if isinstance(answer.data, str):
                sys.stdout.write(answer.data)
            else:
                json.dump(answer.data, sys.stdout, ensure_ascii=False, sort_keys=True, indent=2)
                sys.stdout.write("\n")
    except Unsupported as exc:
        print(json.dumps({"status": "unsupported", "detail": str(exc)}), file=sys.stderr)
        return 3
    except ProjectionError as exc:
        print(json.dumps({"status": exc.status, "detail": str(exc)}), file=sys.stderr)
        return 2
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"status": "incomplete", "detail": str(exc)}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
