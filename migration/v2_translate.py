"""Plan Door admission proposals for the V2 audio route (ADRs 0047-0049).

Planning is a pure function over exact source bytes and caller-supplied data:
verified bundle locators and record bytes, the admitted bundle and audio
Artifacts, a sample-rate probe report, the operator's sheet decisions, the
installed contract citations, and a state file of previously generated
identity keys. It never opens a path, runs a program, or writes an archive.
Door validates and admits every proposal; this module only proposes.

Every route-eligible source row (each ``audio_segment`` and
``song_performance`` record) is accounted for exactly once as translated,
not proposed, or unresolved, with its reasons (INV-24). A row no rule covers
fails closed rather than being guessed.

The command-line entry point adds the I/O: ``plan`` reads files and writes
proposal directories, ``probe`` runs a configured ffprobe, and ``verify``
compares Door's dry-run prediction with the planned revisions.

Choices the ADRs left open, approved by the operator on 2026-10-14:

- Identity names: ``archive.migration/<contract>`` namespaces with
  ``v2-performance-<hex>`` for translated claims, ``<part>-v2-performance-<hex>``
  for judgments, ``<rule-slug>-<hex>`` for translation records, and
  ``archive.core/entity`` with ``artist-<uuid4>`` for artists. Work and artist
  keys persist in a state file saved before any admission.
- A translation record's ``source.id`` is the V2 record's own ``id``.
- An exponent-spelled boundary is rewritten as plain decimal text of equal
  value; more than six decimals is unresolved.
- A segment chain is unresolved as a whole if any record in it is.
- A performance with several snapshots of one ID is refused as not yet
  implemented, and V2 ``record_revision`` history stays in the bundle.
- Merged duplicate artist credits keep first-credit order.
- Credits and Work links are separate batches after the performance batch.
- An incomplete sheet proposes only selections.
- A rendition's occurrence gets no V2 judgment; its parts' order is carried by
  each part translation's ``part_position``, because the occurrence's
  selection refs must be sorted. Cross-file order uses
  ``RECORDING_TIME_FIELD`` and fails closed when it is absent or tied.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from collections import defaultdict
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


ROUTE_SCHEMAS = ("audio_segment", "song_performance")
# Cross-file rendition order: a recorder sets a file's modification time when
# it closes the file, so the earlier part's file has the earlier time. V2
# observed it at import and joins it to the artifact by source_record_id.
RECORDING_TIME_FIELD = "source_observation.observed_mtime"
BOUNDARY_RULE = "round-half-even/1"
KINDS = frozenset({"original", "cover", "improvisation", "noodle", "talking",
                   "transition", "incomplete", "unknown"})
SONG_KINDS = frozenset({"original", "cover"})
MAX_TITLE = 200
MAX_ARTISTS = 12
SECONDS_TEXT = re.compile(r"(0|[1-9][0-9]*)(\.[0-9]{1,6})?")
_UUID_URN = re.compile(r"urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_REVISION = re.compile(r"sha256:[0-9a-f]{64}")
_NAMESPACE_SEGMENT = re.compile(r"[a-z][a-z0-9._-]*")
_NAME = re.compile(r"[A-Za-z0-9_-]+")

# Contract citations every plan needs, by (id, version).
REQUIRED_CONTRACTS = (
    ("archive.claim/audio_selection", "2"),
    ("archive.claim/song_occurrence", "2"),
    ("archive.claim/selection_kind", "1"),
    ("archive.claim/selection_title", "1"),
    ("archive.claim/artist_credit", "1"),
    ("archive.claim/performance_work", "3"),
    ("archive.assessment/judgment", "1"),
    ("archive.migration/translation", "1"),
    ("archive.migration/selection_translation", "1"),
)

# Identity namespaces. Selections and Works follow ADR 0040; the rest are
# this module's allocation, one migration namespace per translated contract.
NS_SELECTION = "archive.migration/audio_selection"
NS_SELECTION_TRANSLATION = "archive.migration/selection_translation"
NS_OCCURRENCE = "archive.migration/song_occurrence"
NS_KIND = "archive.migration/selection_kind"
NS_TITLE = "archive.migration/selection_title"
NS_CREDIT = "archive.migration/artist_credit"
NS_JUDGMENT = "archive.migration/judgment"
NS_TRANSLATION = "archive.migration/translation"
NS_WORK_LINK = "archive.migration/performance_work"
NS_SONG = "archive.audio/song"
NS_ARTIST = "archive.core/entity"


class TranslateError(ValueError):
    """Unsafe or inconsistent planning input; no partial plan is returned."""


class SheetError(TranslateError):
    """The operator's sheet decisions cannot be resolved."""


# --------------------------------------------------------------------------
# Canonical JSON, revisions, and identity allocation (mirrors Door)


def jcs(value: Any) -> bytes:
    """RFC 8785 encoding restricted to the values Archive drafts use.

    Only integers within the exactly representable binary64 range are
    accepted: Door normalizes numbers through binary64, so a fraction or a
    larger integer could hash differently there.
    """
    return _jcs(value).encode("utf-8")


def _jcs(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        if abs(value) > 2 ** 53:
            raise TranslateError("integer outside the exact binary64 range")
        return str(value)
    if isinstance(value, float):
        raise TranslateError("non-integer numbers are not used in Archive drafts")
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise TranslateError("string contains a lone surrogate") from exc
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_jcs(item) for item in value) + "]"
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TranslateError("object keys must be strings")
        keys = sorted(value, key=lambda key: key.encode("utf-16-be"))
        return "{" + ",".join(_jcs(key) + ":" + _jcs(value[key]) for key in keys) + "}"
    raise TranslateError(f"unsupported JSON value: {type(value).__name__}")


def revision_of(draft: Mapping[str, Any]) -> str:
    identity = {key: item for key, item in draft.items() if key not in ("revision_id", "at")}
    return "sha256:" + hashlib.sha256(jcs(identity)).hexdigest()


def allocate(archive_id: str, namespace: str, name: str) -> str:
    """ADR 0026 allocation: UUIDv5 over the archive UUID, namespace, NUL, name."""
    if not _UUID_URN.fullmatch(archive_id):
        raise TranslateError("archive_id must be a lowercase urn:uuid")
    if not all(_NAMESPACE_SEGMENT.fullmatch(segment) for segment in namespace.split("/")):
        raise TranslateError(f"invalid identity namespace: {namespace}")
    if not _NAME.fullmatch(name):
        raise TranslateError(f"invalid identity name: {name}")
    return "urn:uuid:" + str(uuid.uuid5(uuid.UUID(archive_id[9:]), namespace + "\0" + name))


def _hex(text: str) -> str:
    return text.encode("utf-8").hex()


# --------------------------------------------------------------------------
# Source parsing


class SourceNumber(str):
    """The exact source spelling of a JSON number."""


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in pairs:
        if key in result:
            raise TranslateError("duplicate JSON object key in source record")
        result[key] = item
    return result


def _reject_constant(_: str) -> Any:
    raise TranslateError("non-finite JSON number in source record")


def parse_source(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                           parse_float=SourceNumber, parse_int=SourceNumber,
                           parse_constant=_reject_constant)
    except TranslateError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise TranslateError("source record is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise TranslateError("source locator does not identify a JSON object")
    return value


def _locator(row: Mapping[str, Any], raw: bytes) -> dict[str, Any]:
    keys = ("member", "member_sha256", "offset", "length", "sha256")
    if any(key not in row for key in keys):
        raise TranslateError("source locator is incomplete")
    locator = {key: row[key] for key in keys}
    if (not isinstance(locator["offset"], int) or isinstance(locator["offset"], bool)
            or not isinstance(locator["length"], int) or isinstance(locator["length"], bool)
            or locator["length"] != len(raw)
            or locator["sha256"] != "sha256:" + hashlib.sha256(raw).hexdigest()):
        raise TranslateError("source bytes do not match their locator")
    return locator


def seconds_to_samples(text: str, rate: int) -> tuple[str, int]:
    """Apply ADR 0047 rule 4 to one exact source boundary.

    Returns the boundary as plain decimal text (the source spelling when it
    already is one) and its sample offset, rounded half-even.
    """
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise TranslateError("boundary_not_a_decimal") from exc
    if not value.is_finite() or value < 0:
        raise TranslateError("boundary_negative_or_not_finite")
    if value != value.quantize(Decimal("0.000001")):
        raise TranslateError("boundary_has_more_than_six_decimals")
    if SECONDS_TEXT.fullmatch(text):
        spelled = text
    else:
        spelled = format(value.quantize(Decimal("0.000001")), "f").rstrip("0").rstrip(".") or "0"
        if not SECONDS_TEXT.fullmatch(spelled):
            raise TranslateError("boundary_cannot_be_spelled_as_decimal_text")
    with localcontext() as context:
        context.prec = 60
        samples = int((value * rate).to_integral_value(rounding=ROUND_HALF_EVEN))
    return spelled, samples


# --------------------------------------------------------------------------
# Sheet resolution


def normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def _resolve_roots(ids: Iterable[str], decisions: Mapping[str, Any], kind: str) -> dict[str, str]:
    roots: dict[str, str] = {}
    ids = list(ids)
    known = set(ids)
    for start in ids:
        seen: list[str] = []
        current = start
        while True:
            if current in seen:
                raise SheetError(f"{kind} same_as cycle through {current}")
            seen.append(current)
            target = (decisions.get(current) or {}).get("same_as")
            if target is None:
                break
            if target not in known:
                raise SheetError(f"{kind} {current} is grouped with unknown {target}")
            current = target
        roots[start] = current
    return roots


def resolve_sheet(sheet: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve the sheet into Work groups, artists, and renditions (ADRs 0049, 0050).

    A title group's answer is one of: a song name (the group is a Work, or
    ``same_as`` another group), ``works`` (a segue linking to each listed
    group's Work), or ``no_work`` (a confirmed absence). ``renditions`` lists
    title groups whose performances are parts of one rendition, in recording
    order. Returns ``complete`` False, with the unconfirmed ids, when any
    answer is missing or unconfirmed; nothing that depends on the sheet can
    be proposed then.
    """
    titles = sheet.get("titles")
    artists = sheet.get("artists")
    decisions = sheet.get("decisions")
    renditions = sheet.get("renditions", {})
    if (not isinstance(titles, Mapping) or not isinstance(artists, Mapping)
            or not isinstance(decisions, Mapping) or not isinstance(renditions, Mapping)):
        raise SheetError("sheet must contain titles, artists, decisions, and renditions objects")
    unconfirmed = sorted(
        [key for key in list(titles) + list(artists) if (decisions.get(key) or {}).get("confirmed") is not True]
        + [key for key, value in renditions.items()
           if not isinstance(value, Mapping) or value.get("confirmed") is not True])
    keys: dict[str, str] = {}
    for tid, title in titles.items():
        key = title.get("key") if isinstance(title, Mapping) else None
        if not isinstance(key, str) or not key or key != normalize_title(key):
            raise SheetError(f"title {tid} has no normalized key")
        if key in keys:
            raise SheetError(f"titles {keys[key]} and {tid} share a key")
        keys[key] = tid
    names: dict[str, str] = {}
    for aid, artist in artists.items():
        name = artist.get("name") if isinstance(artist, Mapping) else None
        if not isinstance(name, str) or not name:
            raise SheetError(f"artist {aid} has no name")
        if name in names:
            raise SheetError(f"artists {names[name]} and {aid} share a name")
        names[name] = aid
    if unconfirmed:
        return {"complete": False, "unconfirmed": unconfirmed}

    title_roots = _resolve_roots(titles, decisions, "title")
    modes: dict[str, dict[str, Any]] = {}
    for tid in titles:
        decision = decisions[tid]
        has_works = decision.get("works") is not None
        no_work = decision.get("no_work") is True
        if decision.get("same_as") is not None and (has_works or no_work):
            raise SheetError(f"title {tid} is grouped with another and also answers its Works")
        if has_works and no_work:
            raise SheetError(f"title {tid} lists Works and also says it has none")
        if no_work:
            modes[tid] = {"mode": "no_work"}
        elif has_works:
            listed = decision["works"]
            if not isinstance(listed, list) or not listed or any(item not in titles for item in listed):
                raise SheetError(f"title {tid} lists unknown Works")
            modes[tid] = {"mode": "works", "listed": list(listed)}
        else:
            modes[tid] = {"mode": "song"}
    works: dict[str, dict[str, Any]] = {}

    def song_root(tid: str) -> str:
        root = title_roots[tid]
        if modes[root]["mode"] != "song":
            raise SheetError(f"title {tid} names {root} as a Work, but {root} is not a song")
        song = decisions[root].get("song")
        if not isinstance(song, str) or not song.strip():
            raise SheetError(f"title group {root} has no song name")
        works.setdefault(root, {"key": titles[root]["key"], "label": song.strip()})
        return root

    group_works: dict[str, list[str] | None] = {}
    for tid in titles:
        mode = modes[title_roots[tid]]
        if mode["mode"] == "no_work":
            group_works[tid] = []
        elif mode["mode"] == "works":
            resolved: list[str] = []
            for listed in mode["listed"]:
                root = song_root(listed)
                if root not in resolved:
                    resolved.append(root)
            group_works[tid] = resolved
        else:
            group_works[tid] = [song_root(tid)]

    seen_groups: dict[str, str] = {}
    rendition_list = []
    for rid in sorted(renditions):
        groups = renditions[rid].get("groups")
        if not isinstance(groups, list) or len(groups) < 2 or any(group not in titles for group in groups):
            raise SheetError(f"rendition {rid} must list two or more known title groups")
        for group in groups:
            if group in seen_groups:
                raise SheetError(f"title group {group} is in renditions {seen_groups[group]} and {rid}")
            seen_groups[group] = rid
        rendition_list.append({"id": rid, "groups": list(groups)})

    artist_roots = _resolve_roots(artists, decisions, "artist")
    entities: dict[str, dict[str, Any]] = {}
    for aid, root in sorted(artist_roots.items()):
        kind = decisions[root].get("kind")
        if kind not in ("person", "organisation"):
            raise SheetError(f"artist {root} has no kind")
        entities.setdefault(root, {"label": artists[root]["name"], "kind": kind, "members": []})
        entities[root]["members"].append(aid)
    return {
        "complete": True,
        "unconfirmed": [],
        "group_of_key": dict(keys),
        "group_key": {tid: titles[tid]["key"] for tid in titles},
        "group_works": group_works,
        "renditions": rendition_list,
        "artist_root": {artists[aid]["name"]: root for aid, root in artist_roots.items()},
        "works": works,
        "artists": entities,
    }


# --------------------------------------------------------------------------
# Draft construction


class _Context:
    def __init__(self, context: Mapping[str, Any], citations: Mapping[tuple[str, str], dict[str, str]]):
        for key in ("archive_id", "spec", "by", "operator", "operator_logins", "bundle"):
            if key not in context:
                raise TranslateError(f"context lacks {key}")
        self.archive_id = context["archive_id"]
        self.spec = context["spec"]
        self.by = context["by"]
        self.operator = _pin(context["operator"], "operator")
        self.bundle = _pin(context["bundle"], "bundle")
        logins = context["operator_logins"]
        if not isinstance(logins, list) or not logins or any(not isinstance(item, str) or not item for item in logins):
            raise TranslateError("operator_logins must list the V2 operator logins mapped to the operator")
        self.operator_logins = frozenset(logins)
        missing = [f"{cid} v{version}" for cid, version in REQUIRED_CONTRACTS if (cid, version) not in citations]
        if missing:
            raise TranslateError("contract citations missing: " + ", ".join(missing))
        self.citations = citations

    def cite(self, cid: str, version: str) -> list[dict[str, str]]:
        return [dict(self.citations[(cid, version)])]

    def draft(self, object_id: str, shape: str, agent: str, payload: Any, refs: list[dict[str, str]],
              contracts: list[dict[str, str]] | None = None, supersedes: list[dict[str, str]] | None = None,
              source: dict[str, str] | None = None) -> dict[str, Any]:
        draft: dict[str, Any] = {
            "spec": self.spec, "object_id": object_id, "shape": shape, "by": self.by,
            "agent": agent, "payload": payload, "refs": refs, "supersedes": supersedes or [],
        }
        if contracts:
            draft["contracts"] = contracts
        if source:
            draft["source"] = source
        return draft


def _pin(value: Any, label: str) -> dict[str, str]:
    if (not isinstance(value, Mapping) or not _UUID_URN.fullmatch(str(value.get("object_id", "")))
            or not _REVISION.fullmatch(str(value.get("revision_id", "")))):
        raise TranslateError(f"{label} must pin object_id and revision_id")
    return {"object_id": value["object_id"], "revision_id": value["revision_id"]}


def _ref(role: str, object_id: str, revision: str) -> dict[str, str]:
    return {"role": role, "id": object_id, "revision_id": revision}


class _Batch:
    def __init__(self, name: str):
        self.name = name
        self.records: list[tuple[str, dict[str, Any], str, str | None, tuple[str, str] | None]] = []

    def add(self, label: str, draft: dict[str, Any], expected: str | None = None,
            identity: tuple[str, str] | None = None) -> str:
        revision = revision_of(draft)
        self.records.append((label, draft, revision, expected, identity))
        return revision

    def render(self) -> dict[str, Any]:
        files = []
        identities = []
        heads = []
        for index, (label, draft, revision, expected, identity) in enumerate(self.records):
            path = f"records/{index:03d}-{label}.json"
            files.append({"path": path, "bytes": jcs(draft).decode("utf-8"), "revision_id": revision,
                          "object_id": draft["object_id"]})
            if identity is not None:
                identities.append({"object_id": draft["object_id"], "namespace": identity[0], "name": identity[1]})
            heads.append({"object_id": draft["object_id"], "revision_id": expected})
        manifest = {"protocol": "archive-admission/1", "records": [item["path"] for item in files],
                    "identities": identities, "contents": [], "expected_heads": heads}
        return {"name": self.name, "manifest": manifest, "records": files}


# --------------------------------------------------------------------------
# Planning


def plan(source_rows: Sequence[tuple[Mapping[str, Any], bytes]], context: Mapping[str, Any],
         citations: Mapping[tuple[str, str], dict[str, str]], artifacts: Mapping[str, Any],
         probes: Mapping[str, Any], sheet: Mapping[str, Any], state: Mapping[str, Any],
         new_key: Callable[[], str] = lambda: str(uuid.uuid4())) -> dict[str, Any]:
    """Plan ordered admission batches and an accounting report.

    ``source_rows`` pairs each verified manifest locator with its exact
    record bytes. ``state`` holds previously generated Work and artist keys,
    which are reused so a rerun never mints a second identity; the returned
    ``state`` must be saved before any proposal is admitted.
    """
    ctx = _Context(context, citations)
    keys = {"songs": dict(state.get("songs", {})), "artists": dict(state.get("artists", {}))}

    segments: list[dict[str, Any]] = []
    performances: list[dict[str, Any]] = []
    artifact_sources: dict[str, list[Any]] = defaultdict(list)
    observations: dict[Any, list[Any]] = defaultdict(list)
    for row, raw in source_rows:
        record = parse_source(raw)
        if record.get("schema") == "artifact":
            artifact_sources[record.get("artifact_id")].append(record.get("source_record_id"))
        elif record.get("schema") == "source_observation":
            observations[record.get("source_record_id")].append(record.get("observed_mtime"))
        if record.get("schema") not in ROUTE_SCHEMAS:
            continue
        item = {"record": record, "locator": _locator(row, raw), "source": _source_of(row, record)}
        (segments if record["schema"] == "audio_segment" else performances).append(item)

    accounting: list[dict[str, Any]] = []
    batches: list[_Batch] = []
    flags: list[str] = []

    sheet_view = resolve_sheet(sheet)
    entity_batch = _Batch("entities")
    works: dict[str, dict[str, str]] = {}
    artist_pins: dict[str, dict[str, str]] = {}
    if sheet_view["complete"]:
        for root, work in sorted(sheet_view["works"].items(), key=lambda kv: kv[1]["key"]):
            if work["key"] not in keys["songs"]:
                keys["songs"][work["key"]] = new_key()
            song_key = keys["songs"][work["key"]]
            name = "song-" + str(uuid.UUID(song_key))
            object_id = allocate(ctx.archive_id, NS_SONG, name)
            draft = ctx.draft(object_id, "entity", ctx.operator["object_id"],
                              {"kind": "musical_work", "label": work["label"]}, [])
            works[root] = {"object_id": object_id,
                           "revision_id": entity_batch.add("work", draft, identity=(NS_SONG, name))}
        for root, artist in sorted(sheet_view["artists"].items(), key=lambda kv: kv[1]["label"]):
            if artist["label"] not in keys["artists"]:
                keys["artists"][artist["label"]] = new_key()
            artist_key = keys["artists"][artist["label"]]
            name = "artist-" + str(uuid.UUID(artist_key))
            object_id = allocate(ctx.archive_id, NS_ARTIST, name)
            draft = ctx.draft(object_id, "entity", ctx.operator["object_id"],
                              {"kind": artist["kind"], "label": artist["label"]}, [])
            artist_pins[root] = {"object_id": object_id,
                                 "revision_id": entity_batch.add("artist", draft, identity=(NS_ARTIST, name))}
        if entity_batch.records:
            batches.append(entity_batch)
    else:
        flags.append("sheet_incomplete: only selections are proposed; performances, Works, artists, "
                     "credits, and Work links wait for the confirmed sheet (renditions change occurrences)")

    selection_revisions = _plan_selections(segments, ctx, artifacts, probes, batches, accounting)
    recorded = {artifact_id: _recording_time(sources, observations)
                for artifact_id, sources in artifact_sources.items()}
    performance_plans = _plan_performances(performances, ctx, selection_revisions, sheet_view,
                                           recorded, batches, accounting, flags)
    _plan_credits(performance_plans, ctx, sheet_view, artist_pins, batches)
    _plan_work_links(performance_plans, ctx, sheet_view, works, keys, batches)

    rendered = [batch.render() for batch in batches]
    for index, batch in enumerate(rendered, start=1):
        batch["name"] = f"{index:04d}-{batch['name']}"
    totals: dict[str, int] = defaultdict(int)
    for entry in accounting:
        totals[entry["disposition"]] += 1
    if sum(totals.values()) != len(segments) + len(performances):
        raise TranslateError("accounting does not conserve route rows")
    report = {
        "format": "archive.v2-translation-report/1",
        "route_rows": len(segments) + len(performances),
        "totals": dict(sorted(totals.items())),
        "sheet": {"complete": sheet_view["complete"], "unconfirmed": sheet_view["unconfirmed"],
                  "works": len(works), "artists": len(artist_pins)},
        "performances": [{"performance_id": part["performance_id"], "rendition": unit["rendition"],
                          "parts": part["parts"], "work": unit["work"]}
                         for unit in performance_plans for part in unit["parts"]],
        "rows": accounting,
        "batches": [{"name": batch["name"], "records": len(batch["records"])} for batch in rendered],
        "flags": flags,
    }
    return {"batches": rendered, "report": report, "state": keys}


def _source_of(row: Mapping[str, Any], record: Mapping[str, Any]) -> dict[str, str]:
    source = {"schema": record["schema"]}
    manifest_source = row.get("source")
    identifier = record.get("id")
    if isinstance(manifest_source, Mapping):
        if manifest_source.get("schema") != record["schema"] or manifest_source.get("id") != identifier:
            raise TranslateError("manifest source identity does not match source bytes")
    if isinstance(identifier, str) and identifier:
        source["id"] = identifier
    else:
        source["id"] = record.get("segment_id") if record["schema"] == "audio_segment" else record.get("performance_id")
        if not isinstance(source["id"], str) or not source["id"]:
            raise TranslateError("source record has no V2 identity")
    revision = manifest_source.get("revision_id") if isinstance(manifest_source, Mapping) else None
    if isinstance(revision, str) and revision:
        source["revision_id"] = revision
    return source


def _row(item: Mapping[str, Any], disposition: str, reasons: Iterable[str], objects: Iterable[str] = ()) -> dict[str, Any]:
    record = item["record"]
    identifier = record.get("segment_id") if record["schema"] == "audio_segment" else record.get("performance_id")
    return {"schema": record["schema"], "id": identifier, "locator": item["locator"],
            "disposition": disposition, "reasons": sorted(set(reasons)), "archive_objects": list(objects)}


def _plan_selections(segments: list[dict[str, Any]], ctx: _Context, artifacts: Mapping[str, Any],
                     probes: Mapping[str, Any], batches: list[_Batch],
                     accounting: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """Translate linear correction chains (ADR 0047 rule 1).

    Returns the selection object and revision produced from each segment id.
    """
    by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in segments:
        identifier = item["record"].get("segment_id")
        if not isinstance(identifier, str) or not identifier:
            accounting.append(_row(item, "unresolved", ["segment_id_missing"]))
            continue
        by_id[identifier].append(item)
    parent: dict[str, str] = {identifier: identifier for identifier in by_id}

    def find(identifier: str) -> str:
        while parent[identifier] != identifier:
            parent[identifier] = parent[parent[identifier]]
            identifier = parent[identifier]
        return identifier

    for identifier, items in by_id.items():
        previous = items[0]["record"].get("supersedes_segment_id")
        if isinstance(previous, str) and previous in by_id:
            parent[find(identifier)] = find(previous)
    components: dict[str, list[str]] = defaultdict(list)
    for identifier in by_id:
        components[find(identifier)].append(identifier)

    produced: dict[str, dict[str, str]] = {}
    for members in sorted(components.values(), key=lambda ids: min(ids)):
        reasons: dict[str, list[str]] = {identifier: [] for identifier in members}
        chain_reasons: list[str] = []
        successors: dict[str, list[str]] = defaultdict(list)
        roots = []
        for identifier in members:
            if len(by_id[identifier]) > 1:
                chain_reasons.append("duplicate_segment_id")
            previous = by_id[identifier][0]["record"].get("supersedes_segment_id")
            if previous is None:
                roots.append(identifier)
            elif not isinstance(previous, str) or not previous:
                chain_reasons.append("predecessor_malformed")
            elif previous not in by_id:
                chain_reasons.append("missing_predecessor")
                roots.append(identifier)
            else:
                successors[previous].append(identifier)
        if any(len(items) > 1 for items in successors.values()):
            chain_reasons.append("chain_fork")
        if not roots:
            chain_reasons.append("chain_cycle")
        order: list[str] = []
        if len(roots) == 1 and not chain_reasons:
            current: str | None = roots[0]
            while current is not None:
                order.append(current)
                nexts = successors.get(current, [])
                current = nexts[0] if nexts else None
            if len(order) != len(members):
                chain_reasons.append("chain_not_linear")
        elif len(roots) > 1:
            chain_reasons.append("chain_has_several_roots")

        artifact_ids = {by_id[identifier][0]["record"].get("source_artifact_id") for identifier in members}
        if len(artifact_ids) != 1:
            chain_reasons.append("chain_changes_artifact")
        artifact_id = next(iter(artifact_ids)) if len(artifact_ids) == 1 else None
        artifact = artifacts.get(artifact_id) if isinstance(artifact_id, str) else None
        probe = probes.get(artifact_id) if isinstance(artifact_id, str) else None
        if artifact is None:
            chain_reasons.append("archive_artifact_missing")
        else:
            artifact = _pin(artifact, f"artifact {artifact_id}")
        if not isinstance(probe, Mapping) or not isinstance(probe.get("sample_rate"), int) or probe["sample_rate"] < 1:
            chain_reasons.append("sample_rate_probe_missing")
            probe = None

        converted: dict[str, dict[str, Any]] = {}
        for identifier in members:
            record = by_id[identifier][0]["record"]
            if record.get("review_state") != "reviewed":
                reasons[identifier].append("segment_not_reviewed")
            if record.get("operator") not in ctx.operator_logins:
                reasons[identifier].append("source_agent_unmapped")
            if probe is None:
                continue
            bounds = {}
            for field in ("start_seconds", "end_seconds"):
                value = record.get(field)
                if not isinstance(value, SourceNumber):
                    reasons[identifier].append(f"{field}_not_a_number")
                    continue
                try:
                    bounds[field] = seconds_to_samples(value, probe["sample_rate"])
                except TranslateError as exc:
                    reasons[identifier].append(f"{field}_{exc}")
            if len(bounds) == 2:
                start, end = bounds["start_seconds"][1], bounds["end_seconds"][1]
                if start >= end:
                    reasons[identifier].append("boundary_empty_or_reversed_after_rounding")
                samples = probe.get("samples")
                if isinstance(samples, int) and end > samples:
                    reasons[identifier].append("boundary_past_artifact_end")
                converted[identifier] = bounds

        if chain_reasons or any(reasons.values()):
            for identifier in members:
                for item in by_id[identifier]:
                    accounting.append(_row(item, "unresolved", chain_reasons + reasons[identifier]))
            continue

        root = order[0]
        name = "v2-segment-" + _hex(root)
        selection_id = allocate(ctx.archive_id, NS_SELECTION, name)
        previous_revision: str | None = None
        for position, identifier in enumerate(order):
            item = by_id[identifier][0]
            bounds = converted[identifier]
            batch = _Batch(f"selection-{_hex(root)[:16]}-r{position}")
            payload = {"tracks": [{"pieces": [{
                "start_sample": bounds["start_seconds"][1], "end_sample": bounds["end_seconds"][1],
                "sample_rate": probe["sample_rate"]}]}]}
            draft = ctx.draft(selection_id, "claim", ctx.operator["object_id"], payload,
                              [_ref("piece", artifact["object_id"], artifact["revision_id"])],
                              ctx.cite("archive.claim/audio_selection", "2"),
                              supersedes=[_ref("supersedes", selection_id, previous_revision)] if previous_revision else None)
            revision = batch.add("selection", draft, expected=previous_revision,
                                 identity=None if previous_revision else (NS_SELECTION, name))
            translation_name = "v2-segment-" + _hex(identifier)
            translation_id = allocate(ctx.archive_id, NS_SELECTION_TRANSLATION, translation_name)
            translation = ctx.draft(translation_id, "claim", ctx.by, {
                "rule": "v2-audio-segment-chain/1", "locator": item["locator"], "source": item["source"],
                "chain_root": root, "chain_position": position,
                "source_seconds": {"start": bounds["start_seconds"][0], "end": bounds["end_seconds"][0]},
                "probe": {"tool": probe["tool"], "tool_version": probe["tool_version"],
                          "sample_rate": probe["sample_rate"]},
                "boundary_rule": BOUNDARY_RULE,
            }, [_ref("translated", selection_id, revision),
                _ref("migration_source", ctx.bundle["object_id"], ctx.bundle["revision_id"])],
                ctx.cite("archive.migration/selection_translation", "1"),
                source={key: item["source"][key] for key in ("schema", "id")})
            batch.add("selection-translation", translation,
                      identity=(NS_SELECTION_TRANSLATION, translation_name))
            batches.append(batch)
            produced[identifier] = {"object_id": selection_id, "revision_id": revision,
                                    "artifact_id": artifact_id}
            accounting.append(_row(item, "translated", [], [selection_id, translation_id]))
            previous_revision = revision
    return produced


def _base_reasons(item: Mapping[str, Any], ctx: _Context, counts: Mapping[str, int],
                  selections: Mapping[str, dict[str, str]]) -> tuple[str | None, list[str], dict[str, str] | None]:
    """Return (not-proposed reason, unresolved reasons, selection) for one performance row."""
    record = item["record"]
    identifier = record.get("performance_id")
    if not isinstance(identifier, str) or not identifier:
        return None, ["performance_id_missing"], None
    state = record.get("state")
    if state in ("suggested", "retracted"):
        return f"performance_{state}", [], None
    reasons: list[str] = []
    if state != "reviewed":
        reasons.append("performance_state_unknown")
    if counts[identifier] > 1:
        reasons.append("performance_has_several_snapshots_translation_not_implemented")
    if record.get("operator") not in ctx.operator_logins:
        reasons.append("source_agent_unmapped")
    segment_ids = record.get("segment_ids")
    selection = None
    if not isinstance(segment_ids, list) or not segment_ids:
        reasons.append("segment_selection_missing")
    elif len(segment_ids) > 1:
        reasons.append("multi_segment_performance")
    else:
        selection = selections.get(segment_ids[0])
        if selection is None:
            reasons.append("segment_not_translated")
        elif selection["artifact_id"] != record.get("source_artifact_id"):
            reasons.append("segment_artifact_disagrees_with_performance")
    return None, reasons, selection


def _recording_time(sources: list[Any], observations: Mapping[Any, list[Any]]) -> int | None:
    """One V2 artifact's file modification time, from its source observation.

    Returns None unless the artifact has one source record with exactly one
    observation whose time is a decimal nanosecond count.
    """
    if len(sources) != 1:
        return None
    times = observations.get(sources[0], [])
    if len(times) != 1 or not isinstance(times[0], str) or not times[0].isdigit():
        return None
    return int(times[0])


def _rendition_units(eligible: list[dict[str, Any]], sheet_view: Mapping[str, Any],
                     recorded: Mapping[Any, int | None], flags: list[str]) -> list[list[dict[str, Any]]]:
    """Group performances into units: one per performance, or one per confirmed rendition.

    A rendition's groups must each hold exactly one reviewed performance.
    Parts on one file are ordered by start time; parts on different files by
    each file's observed modification time from the bundle. Order that
    disagrees, or cannot be established, refuses the plan (ADR 0050).
    """
    by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for part in eligible:
        title = part["record"].get("title")
        if isinstance(title, str):
            by_key[normalize_title(title)].append(part)
    in_rendition: dict[int, str] = {}
    units: list[list[dict[str, Any]]] = []
    for rendition in sheet_view["renditions"]:
        parts = []
        for group in rendition["groups"]:
            members = by_key.get(sheet_view["group_key"][group], [])
            if len(members) != 1:
                raise SheetError(f"rendition {rendition['id']} group {group} holds {len(members)} "
                                 "reviewed performances; exactly one is required")
            parts.append(members[0])
        for earlier, later in zip(parts, parts[1:]):
            first, second = earlier["record"], later["record"]
            if first.get("source_artifact_id") == second.get("source_artifact_id"):
                if not (isinstance(first.get("start_seconds"), SourceNumber)
                        and isinstance(second.get("start_seconds"), SourceNumber)
                        and Decimal(first["start_seconds"]) < Decimal(second["start_seconds"])):
                    raise SheetError(f"rendition {rendition['id']} lists its parts out of recording order")
            else:
                times = [recorded.get(record.get("source_artifact_id")) for record in (first, second)]
                if any(time is None for time in times):
                    raise SheetError(f"rendition {rendition['id']} cannot be ordered: a part's file has no "
                                     f"single {RECORDING_TIME_FIELD}")
                if times[0] == times[1]:
                    raise SheetError(f"rendition {rendition['id']} cannot be ordered: two parts' files share "
                                     f"one {RECORDING_TIME_FIELD}")
                if not times[0] < times[1]:
                    raise SheetError(f"rendition {rendition['id']} lists its parts out of recording order")
        for part in parts:
            in_rendition[id(part)] = rendition["id"]
        units.append(parts)
    for part in eligible:
        if id(part) not in in_rendition:
            units.append([part])
    units.sort(key=lambda unit: unit[0]["index"])
    for unit in units:
        for part in unit:
            part["rendition"] = in_rendition.get(id(part))
    return units


def _plan_performances(performances: list[dict[str, Any]], ctx: _Context,
                       selections: Mapping[str, dict[str, str]], sheet_view: Mapping[str, Any],
                       recorded: Mapping[Any, int | None], batches: list[_Batch],
                       accounting: list[dict[str, Any]], flags: list[str]) -> list[dict[str, Any]]:
    counts: dict[str, int] = defaultdict(int)
    for item in performances:
        identifier = item["record"].get("performance_id")
        if isinstance(identifier, str):
            counts[identifier] += 1
    eligible: list[dict[str, Any]] = []
    for index, item in enumerate(performances):
        not_proposed, reasons, selection = _base_reasons(item, ctx, counts, selections)
        if not_proposed:
            accounting.append(_row(item, "not_proposed", [not_proposed]))
        elif reasons:
            accounting.append(_row(item, "unresolved", reasons))
        elif not sheet_view["complete"]:
            accounting.append(_row(item, "unresolved", ["sheet_incomplete"]))
        else:
            eligible.append({"index": index, "item": item, "record": item["record"],
                             "performance_id": item["record"]["performance_id"], "selection": selection})
    if not sheet_view["complete"]:
        return []
    units = _rendition_units(eligible, sheet_view, recorded, flags)

    operator = ctx.operator["object_id"]
    planned: list[dict[str, Any]] = []
    for unit in units:
        first_hex = _hex(unit[0]["performance_id"])
        batch = _Batch(f"performance-{first_hex[:16]}")
        occurrence_name = "v2-performance-" + first_hex
        occurrence_id = allocate(ctx.archive_id, NS_OCCURRENCE, occurrence_name)
        # The contract requires selection refs unique and sorted by object and
        # revision, so a rendition's recording order is not encoded here.
        selection_refs = sorted({(part["selection"]["object_id"], part["selection"]["revision_id"])
                                 for part in unit})
        occurrence_revision = batch.add("occurrence", ctx.draft(
            occurrence_id, "claim", operator, {},
            [_ref("selection", oid, rev) for oid, rev in selection_refs],
            ctx.cite("archive.claim/song_occurrence", "2")), identity=(NS_OCCURRENCE, occurrence_name))
        is_rendition = len(unit) > 1
        for position, part in enumerate(unit):
            item, record = part["item"], part["record"]
            hex_id = _hex(part["performance_id"])
            name = "v2-performance-" + hex_id
            selection_ref = _ref("selection", part["selection"]["object_id"], part["selection"]["revision_id"])
            objects = [occurrence_id] if position == 0 else []
            parts: dict[str, Any] = {
                "occurrence": "translated:operator_rendition" if is_rendition else "translated"}
            # V2 reviewed each part, not the operator's grouping of parts into
            # one rendition, so only a single-part occurrence gets a V2 judgment.
            claims = [] if is_rendition else [("occurrence", occurrence_id, occurrence_revision)]
            kind = record.get("kind")
            if kind is None:
                parts["kind"] = "not_proposed:kind_absent"
            elif kind not in KINDS:
                parts["kind"] = "unresolved:kind_not_in_vocabulary"
            else:
                kind_id = allocate(ctx.archive_id, NS_KIND, name)
                objects.append(kind_id)
                claims.append(("kind", kind_id, batch.add("kind", ctx.draft(
                    kind_id, "claim", operator, {"kind": kind}, [selection_ref],
                    ctx.cite("archive.claim/selection_kind", "1")), identity=(NS_KIND, name))))
                parts["kind"] = "translated"
            title = record.get("title")
            if not isinstance(title, str) or not title.strip():
                parts["title"] = "not_proposed:title_absent"
            elif len(title) > MAX_TITLE:
                parts["title"] = "unresolved:title_longer_than_200"
            else:
                title_id = allocate(ctx.archive_id, NS_TITLE, name)
                objects.append(title_id)
                claims.append(("title", title_id, batch.add("title", ctx.draft(
                    title_id, "claim", operator, {"title": title}, [selection_ref],
                    ctx.cite("archive.claim/selection_title", "1")), identity=(NS_TITLE, name))))
                parts["title"] = "translated"
            judgments = []
            for claim_part, claim_id, claim_revision in claims:
                judgment_name = f"{claim_part}-{name}"
                judgment_id = allocate(ctx.archive_id, NS_JUDGMENT, judgment_name)
                objects.append(judgment_id)
                judgments.append(_ref("translated_assessment", judgment_id, batch.add("judgment", ctx.draft(
                    judgment_id, "assessment", operator, {"verdict": "affirmed"},
                    [_ref("assessment_of", claim_id, claim_revision)],
                    ctx.cite("archive.assessment/judgment", "1")), identity=(NS_JUDGMENT, judgment_name))))
            objects.append(_translation_into(batch, ctx, part, "v2-song-performance/1",
                                             "v2-song-performance-1-" + hex_id,
                                             [_ref("translated", occurrence_id, occurrence_revision)],
                                             part_position=position + 1 if is_rendition else None))
            for claim_part, claim_id, claim_revision in claims:
                if claim_part == "occurrence":
                    continue
                rule = {"kind": "v2-performance-kind/1", "title": "v2-performance-title/1"}[claim_part]
                objects.append(_translation_into(batch, ctx, part, rule, rule.replace("/", "-") + "-" + hex_id,
                                                 [_ref("translated", claim_id, claim_revision)]))
            if judgments:
                objects.append(_translation_into(batch, ctx, part, "v2-performance-review/1",
                                                 "v2-performance-review-1-core-" + hex_id, judgments))
            accounting.append(_row(item, "translated", [], objects))
            part.update({"parts": parts, "objects": objects, "selection_ref": selection_ref})
        batches.append(batch)
        planned.append({"parts": unit, "rendition": unit[0].get("rendition"), "work": None,
                        "occurrence": {"object_id": occurrence_id, "revision_id": occurrence_revision},
                        "name": occurrence_name})
    return planned


def _translation_into(batch: _Batch, ctx: _Context, part: Mapping[str, Any], rule: str, name: str,
                      translated: list[dict[str, str]], part_position: int | None = None) -> str:
    object_id = allocate(ctx.archive_id, NS_TRANSLATION, name)
    source = part["item"]["source"]
    payload: dict[str, Any] = {"rule": rule, "locator": part["item"]["locator"], "source": source}
    if part_position is not None:
        payload["part_position"] = part_position
    batch.add("translation", ctx.draft(object_id, "claim", ctx.by, payload,
              translated + [_ref("migration_source", ctx.bundle["object_id"], ctx.bundle["revision_id"])],
              ctx.cite("archive.migration/translation", "1"),
              source={key: source[key] for key in ("schema", "id")}), identity=(NS_TRANSLATION, name))
    return object_id


def _plan_credits(planned: list[dict[str, Any]], ctx: _Context, sheet_view: Mapping[str, Any],
                  artist_pins: Mapping[str, dict[str, str]], batches: list[_Batch]) -> None:
    """Artist credits, one batch per performance, after the entities they pin."""
    operator = ctx.operator["object_id"]
    for unit in planned:
        for part in unit["parts"]:
            parts, names = part["parts"], part["record"].get("artists")
            hex_id = _hex(part["performance_id"])
            name = "v2-performance-" + hex_id
            if not isinstance(names, list) or any(not isinstance(value, str) or not value for value in names):
                parts["artists"] = "unresolved:artists_malformed"
                continue
            if not names:
                parts["artists"] = "not_proposed:artists_absent"
                continue
            roots = [sheet_view["artist_root"].get(value) for value in names]
            if any(root is None for root in roots):
                parts["artists"] = "unresolved:artist_not_on_sheet"
                continue
            ordered: list[str] = []
            for root in roots:
                if root not in ordered:
                    ordered.append(root)
            if len(ordered) > MAX_ARTISTS:
                parts["artists"] = "unresolved:more_than_12_artists"
                continue
            batch = _Batch(f"credit-{hex_id[:16]}")
            credit_id = allocate(ctx.archive_id, NS_CREDIT, name)
            refs = [part["selection_ref"]] + [
                _ref("artist", artist_pins[root]["object_id"], artist_pins[root]["revision_id"])
                for root in ordered]
            credit_revision = batch.add("credit", ctx.draft(
                credit_id, "claim", operator, {}, refs, ctx.cite("archive.claim/artist_credit", "1")),
                identity=(NS_CREDIT, name))
            judgment_name = "credit-" + name
            judgment_id = allocate(ctx.archive_id, NS_JUDGMENT, judgment_name)
            judgment_revision = batch.add("judgment", ctx.draft(
                judgment_id, "assessment", operator, {"verdict": "affirmed"},
                [_ref("assessment_of", credit_id, credit_revision)],
                ctx.cite("archive.assessment/judgment", "1")), identity=(NS_JUDGMENT, judgment_name))
            part["objects"] += [
                credit_id, judgment_id,
                _translation_into(batch, ctx, part, "v2-performance-artists/1",
                                  "v2-performance-artists-1-" + hex_id,
                                  [_ref("translated", credit_id, credit_revision)]),
                _translation_into(batch, ctx, part, "v2-performance-review/1",
                                  "v2-performance-review-1-credit-" + hex_id,
                                  [_ref("translated_assessment", judgment_id, judgment_revision)])]
            batches.append(batch)
            parts["artists"] = ("translated" if len(ordered) == len(roots)
                                else "translated:merged_duplicate_artists")


def _plan_work_links(planned: list[dict[str, Any]], ctx: _Context, sheet_view: Mapping[str, Any],
                     works: Mapping[str, dict[str, str]], keys: Mapping[str, Any],
                     batches: list[_Batch]) -> None:
    """The operator's Work identifications: one link per distinct Work (ADRs 0049, 0050)."""
    operator = ctx.operator["object_id"]
    for unit in planned:
        targets: list[tuple[str, dict[str, Any]]] = []
        problems: list[str] = []
        song_parts = 0
        for part in unit["parts"]:
            record = part["record"]
            if record.get("kind") not in SONG_KINDS:
                part["parts"]["work"] = "not_proposed:kind_is_not_a_song"
                continue
            song_parts += 1
            title = record.get("title")
            group = sheet_view["group_of_key"].get(normalize_title(title)) if isinstance(title, str) else None
            if group is None:
                part["parts"]["work"] = "unresolved:title_not_on_sheet"
                problems.append("title_not_on_sheet")
                continue
            listed = sheet_view["group_works"][group]
            part["parts"]["work"] = "confirmed_no_work" if not listed else "identified"
            for root in listed:
                if root not in [existing for existing, _ in targets]:
                    targets.append((root, part))
        if problems:
            unit["work"] = "unresolved:" + ",".join(sorted(set(problems)))
            continue
        if not song_parts:
            unit["work"] = "not_proposed:kind_is_not_a_song"
            continue
        if not targets:
            unit["work"] = "confirmed_no_work"
            continue
        batch = _Batch(f"work-link-{_hex(unit['parts'][0]['performance_id'])[:16]}")
        for root, part in targets:
            work = works[root]
            suffix = "song-" + str(uuid.UUID(keys["songs"][sheet_view["works"][root]["key"]]))
            link_name = unit["name"] + "-" + suffix
            link_id = allocate(ctx.archive_id, NS_WORK_LINK, link_name)
            link_revision = batch.add("work-link", ctx.draft(
                link_id, "claim", operator, {},
                [_ref("performance", unit["occurrence"]["object_id"], unit["occurrence"]["revision_id"]),
                 _ref("work", work["object_id"], work["revision_id"])],
                ctx.cite("archive.claim/performance_work", "3")), identity=(NS_WORK_LINK, link_name))
            part["objects"] += [link_id, _translation_into(
                batch, ctx, part, "v2-operator-identification/1",
                "v2-operator-identification-1-" + _hex(part["performance_id"]) + "-" + suffix,
                [_ref("translated", link_id, link_revision)])]
        batches.append(batch)
        unit["work"] = "translated" if len(targets) == 1 else f"translated:{len(targets)}_works"


# --------------------------------------------------------------------------
# Contract citations


def load_citations(files: Iterable[tuple[str, bytes]]) -> dict[tuple[str, str], dict[str, str]]:
    """Read contract bundles; the digest is SHA-256 over the exact file bytes."""
    citations: dict[tuple[str, str], dict[str, str]] = {}
    for name, raw in files:
        digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        if name.startswith("sha256-") and name != digest.replace(":", "-") + ".json":
            raise TranslateError(f"contract {name} does not match its digest-addressed name")
        bundle = json.loads(raw)
        key = (bundle.get("id"), bundle.get("version"))
        if not all(isinstance(value, str) for value in key):
            raise TranslateError(f"contract {name} lacks id or version")
        if key in citations and citations[key]["digest"] != digest:
            raise TranslateError(f"two different bundles for {key[0]} v{key[1]}")
        citations[key] = {"id": key[0], "version": key[1], "digest": digest}
    return citations


# --------------------------------------------------------------------------
# Command line


def _read_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_plan(result: Mapping[str, Any], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=False)
    for batch in result["batches"]:
        directory = out / batch["name"]
        (directory / "records").mkdir(parents=True)
        (directory / "manifest.json").write_bytes(jcs(batch["manifest"]))
        for record in batch["records"]:
            (directory / record["path"]).write_bytes(record["bytes"].encode("utf-8"))
        (directory / "planned-revisions.json").write_bytes(jcs([record["revision_id"] for record in batch["records"]]))
    (out / "report.json").write_text(json.dumps(result["report"], indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _cmd_plan(args: argparse.Namespace) -> int:
    from migration.v2_lookup import open_verified_bundle

    rows: list[tuple[dict[str, Any], bytes]] = []
    with open_verified_bundle(Path(args.bundle)) as bundle:
        for row in bundle.manifest["records"]:
            rows.append((row, bundle.lookup(row).data))
    contract_files = []
    for directory in args.contracts:
        for path in sorted(Path(directory).glob("*.json")):
            contract_files.append((path.name, path.read_bytes()))
    state_path = Path(args.state)
    state = _read_json(args.state) if state_path.exists() else {}
    result = plan(rows, _read_json(args.context), load_citations(contract_files), _read_json(args.artifacts),
                  _read_json(args.probe), _read_json(args.sheet), state)
    # The state is saved first: an admitted Work must never be re-minted.
    temporary = state_path.with_suffix(state_path.suffix + ".tmp")
    temporary.write_text(json.dumps(result["state"], indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, state_path)
    write_plan(result, Path(args.out))
    print(json.dumps({"batches": len(result["batches"]), "totals": result["report"]["totals"],
                      "sheet_complete": result["report"]["sheet"]["complete"]}, sort_keys=True))
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    door = Path(args.door)
    if not door.is_absolute():
        raise TranslateError("--door must be an absolute path")
    proposal = Path(args.proposal).resolve()
    planned = json.loads((proposal / "planned-revisions.json").read_text(encoding="utf-8"))
    completed = subprocess.run([str(door), "admit", "--proposal", str(proposal), "--dry-run"],
                               cwd=args.archive, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        print(json.dumps({"status": "refused", "door": completed.stderr.strip()}))
        return 1
    predicted = json.loads(completed.stdout)["revision_ids"]
    status = "matched" if predicted == planned else "mismatched"
    print(json.dumps({"status": status, "planned": planned, "predicted": predicted}, sort_keys=True))
    return 0 if status == "matched" else 1


def probe_report(entries: Mapping[str, Any], tool_version: str) -> dict[str, Any]:
    """Shape ffprobe stream output into the planner's probe report."""
    report: dict[str, Any] = {}
    for artifact_id, output in entries.items():
        streams = output.get("streams") if isinstance(output, Mapping) else None
        audio = [stream for stream in streams or [] if stream.get("codec_type") == "audio"]
        if len(audio) != 1:
            raise TranslateError(f"{artifact_id}: expected exactly one audio stream")
        stream = audio[0]
        rate = int(stream["sample_rate"])
        entry: dict[str, Any] = {"sample_rate": rate, "tool": "ffprobe", "tool_version": tool_version}
        if stream.get("time_base") == f"1/{rate}" and str(stream.get("duration_ts", "")).isdigit():
            entry["samples"] = int(stream["duration_ts"])
        report[artifact_id] = entry
    return report


def _cmd_probe(args: argparse.Namespace) -> int:
    ffprobe = Path(args.ffprobe)
    if not ffprobe.is_absolute():
        raise TranslateError("--ffprobe must be an absolute path")
    version = subprocess.run([str(ffprobe), "-version"], capture_output=True, text=True, check=True).stdout
    tool_version = version.splitlines()[0].split()[2] if version.split() else "unknown"
    outputs = {}
    for artifact_id, path in sorted(_read_json(args.files).items()):
        completed = subprocess.run([str(ffprobe), "-v", "error", "-show_entries",
                                    "stream=codec_type,sample_rate,time_base,duration_ts", "-of", "json", path],
                                   capture_output=True, text=True, check=True)
        outputs[artifact_id] = json.loads(completed.stdout)
    print(json.dumps(probe_report(outputs, tool_version), indent=2, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    plan_parser = commands.add_parser("plan")
    for name in ("bundle", "context", "artifacts", "probe", "sheet", "state", "out"):
        plan_parser.add_argument("--" + name, required=True)
    plan_parser.add_argument("--contracts", nargs="+", required=True)
    verify_parser = commands.add_parser("verify")
    for name in ("door", "archive", "proposal"):
        verify_parser.add_argument("--" + name, required=True)
    probe_parser = commands.add_parser("probe")
    probe_parser.add_argument("--ffprobe", required=True)
    probe_parser.add_argument("--files", required=True, help="JSON object mapping V2 artifact id to file path")
    args = parser.parse_args(argv)
    try:
        return {"plan": _cmd_plan, "verify": _cmd_verify, "probe": _cmd_probe}[args.command](args)
    except (TranslateError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        print(f"v2_translate: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
