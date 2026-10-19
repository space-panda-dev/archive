"""The records a named view reads, from World's selected generation, and the
answer the relay and `world view` give (ADR 0082).

A view reads records as `world/views.py` takes them. The records graph
holds every revision World projected: its object, shape, agent, time,
payload, contract citations, and each reference and supersession as a
statement whose IRI states its field and position and whose `arch:role` is
the role's predicate. The role's name is the term of the record's own
contexts (the core context and the contracts it cites, from the context
graph) that names that predicate, so the records are read back exactly as
they were committed. Reading them is regenerable work, never stored.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from world import views

ARCH = "urn:uuid:3e8b29f8-70c9-403e-bb4c-2280d59eca7f#"
DCTERMS = "http://purl.org/dc/terms/"
PROV = "http://www.w3.org/ns/prov#"


class RecordsUnreadable(ValueError):
    """The generation's records graph cannot be read back as records."""


def _expand(context: dict[str, Any], term: Any) -> str | None:
    iri = term.get("@id") if isinstance(term, dict) else term
    if not isinstance(iri, str):
        return None
    if iri.startswith(("http://", "https://", "urn:")):
        return iri
    prefix, _, suffix = iri.partition(":")
    base = context.get(prefix)
    base = base.get("@id") if isinstance(base, dict) else base
    return base + suffix if isinstance(base, str) and suffix else None


def _contexts(projection: Any) -> tuple[dict[str, Any], dict[tuple[str, str], dict[str, Any]]]:
    core: dict[str, Any] | None = None
    bundles: dict[tuple[str, str], dict[str, Any]] = {}
    graph = projection.context_graph.value
    for row in projection.store.query(
            f"SELECT ?s ?value WHERE {{ GRAPH <{graph}> {{ ?s <{PROV}value> ?value }} }}"):
        value = json.loads(row["value"].value)
        if row["s"].value.endswith("#world:core-context"):
            core = value.get("@context", value)
        elif isinstance(value, dict) and "id" in value and "version" in value:
            bundles[(value["id"], value["version"])] = value.get("context", {})
    if core is None:
        raise RecordsUnreadable("the context graph holds no core context")
    return core, bundles


def records(projection: Any) -> list[dict[str, Any]]:
    """Every record revision of the projection, as views read them: its
    `revision_id`, `id` (and `object_id`), `shape`, `contract` (the one it
    cites), `contracts`, `agent`, `at`, `payload`, `refs`, `supersedes`, an
    Entity's `kind`, and `head` (false when another revision supersedes it)."""
    core, bundles = _contexts(projection)
    graph = projection.records_graph.value
    found: dict[str, dict[str, Any]] = {}
    for row in projection.store.query(f"""
            SELECT ?r ?revision ?object ?shape ?agent ?at ?payload ?contracts WHERE {{ GRAPH <{graph}> {{
              ?r <{ARCH}revision_id> ?revision ; <{ARCH}object_id> ?object ; <{DCTERMS}type> ?shape ;
                 <{DCTERMS}creator> ?agent ; <{PROV}generatedAtTime> ?at ; <{ARCH}payload> ?payload .
              OPTIONAL {{ ?r <{ARCH}contracts> ?contracts }} }} }}"""):
        contracts = json.loads(row["contracts"].value) if row["contracts"] is not None else []
        payload = json.loads(row["payload"].value)
        cited = [(c["id"], c["version"]) for c in contracts]
        record = {"revision_id": row["revision"].value, "id": row["object"].value, "object_id": row["object"].value,
                  "shape": row["shape"].value, "agent": row["agent"].value, "at": row["at"].value,
                  "payload": payload, "contracts": contracts, "refs": [], "supersedes": [],
                  "contract": list(cited[0]) if len(cited) == 1 else None}
        if record["shape"] == "entity" and isinstance(payload, dict) and "kind" in payload:
            record["kind"] = payload["kind"]
        found[row["r"].value] = record
    links: dict[str, list[tuple[str, int, str, str, str | None]]] = {}
    for row in projection.store.query(f"""
            SELECT ?r ?link ?role ?id ?pinned WHERE {{ GRAPH <{graph}> {{
              VALUES ?field {{ <{ARCH}refs> <{ARCH}supersedes> }}
              ?r ?field ?link . ?link <{ARCH}role> ?role ; <{ARCH}id> ?id .
              OPTIONAL {{ ?link <{ARCH}revision_id> ?pinned }} }} }}"""):
        # <{archive}#world:reference:{revision}:{field}:{index}>
        _, field, index = row["link"].value.rsplit(":", 2)
        links.setdefault(row["r"].value, []).append(
            (field, int(index), row["role"].value, row["id"].value,
             row["pinned"].value if row["pinned"] is not None else None))
    for subject, items in links.items():
        record = found.get(subject)
        if record is None:
            continue
        terms = dict(core)
        for citation in record["contracts"]:
            terms.update(bundles.get((citation["id"], citation["version"]), {}))
        names = _role_names(terms)
        for field, index, role_iri, object_id, pinned in sorted(items):
            if field == "supersedes":
                role = "supersedes"
            else:
                candidates = names.get(role_iri, [])
                if len(candidates) != 1:
                    raise RecordsUnreadable(f"record {record['revision_id']} names role {role_iri} by "
                                            f"{len(candidates)} terms of its contexts")
                role = candidates[0]
            ref = {"role": role, "id": object_id, **({"revision_id": pinned} if pinned else {})}
            record[field].append(ref)
    superseded = {ref.get("revision_id") for r in found.values() for ref in r["supersedes"]}
    result = []
    for record in sorted(found.values(), key=lambda r: r["revision_id"]):
        record["head"] = record["revision_id"] not in superseded
        result.append(record)
    return result


def _role_names(terms: dict[str, Any]) -> dict[str, list[str]]:
    names: dict[str, list[str]] = {}
    for term, definition in terms.items():
        if term.startswith("@"):
            continue
        iri = _expand(terms, definition)
        if iri is not None:
            names.setdefault(iri, []).append(term)
    return names


def answer(projection: Any, request: Any, *, caller: str | None = None,
           readable: Iterable[str] | None = None) -> dict[str, Any]:
    """Answer one view request from the projection. `readable`, when given,
    is the set of revisions the caller may read (ADR 0088); every other
    record is withheld from the view, and the answer says how many."""
    held = records(projection)
    if readable is None:
        world = held
    else:
        allowed = set(readable)
        world = [r for r in held if r["revision_id"] in allowed]
    result = views.run_view(request, world, caller=caller, withheld=len(held) - len(world))
    return {"snapshot": projection.snapshot, "projection": projection.version, **result}
