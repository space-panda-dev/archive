"""Read-only inventory of V2 reviewed audio-to-song source relationships.

This module deliberately does not create Archive identities or Door proposals.
It identifies structurally eligible V2 source rows for the review app while
leaving the accepted claim, identity, boundary, and currentness contracts open.
Inputs are bytes and manifest data supplied by the caller; this module never
opens a path or accesses a production record tree.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from typing import Any, Mapping, Sequence


BUNDLE_FORMAT = "archive.v2-migration-bundle/1"
_ROUTE_SCHEMAS = frozenset({"audio_segment", "song_performance"})
_ARTIFACT_SCHEMAS = frozenset({"artifact", "derived_artifact"})


class AudioRouteError(ValueError):
    """Unsafe or inconsistent route input; no partial report is returned."""


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AudioRouteError("duplicate JSON object key in source record")
        result[key] = value
    return result


def _parse_record(raw: bytes) -> dict[str, Any]:
    def reject_constant(_: str) -> Any:
        raise AudioRouteError("non-finite JSON number")

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                           parse_constant=reject_constant)
    except AudioRouteError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise AudioRouteError("source record is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise AudioRouteError("source locator does not identify a JSON object")
    return value


def _locator(row: Mapping[str, Any], record: Mapping[str, Any]) -> dict[str, Any]:
    source = row.get("source")
    schema = record.get("schema")
    identifier = record.get("segment_id") if schema == "audio_segment" else record.get("performance_id")
    if schema in _ARTIFACT_SCHEMAS:
        identifier = record.get("artifact_id")
    if source is not None and not isinstance(source, Mapping):
        raise AudioRouteError("manifest source identity is malformed")
    if isinstance(source, Mapping) and (
        source.get("schema") != schema or source.get("id") != record.get("id")
    ):
        raise AudioRouteError("manifest source identity does not match source bytes")
    if not isinstance(identifier, str) or not identifier:
        raise AudioRouteError("source record lacks a V2 record id")
    result: dict[str, Any] = {
        "member": row.get("member"),
        "member_sha256": row.get("member_sha256"),
        "offset": row.get("offset"),
        "length": row.get("length"),
        "sha256": row.get("sha256"),
        "schema": schema,
        "id": identifier,
    }
    if isinstance(source, Mapping):
        result["source"] = dict(source)
    revision_id = source.get("revision_id") if isinstance(source, Mapping) else None
    if revision_id is not None:
        if (not isinstance(revision_id, str) or not revision_id
                or record.get("revision_id") != revision_id):
            raise AudioRouteError("invalid source revision id")
        result["revision_id"] = revision_id
    return result


def analyze_bundle(manifest: Mapping[str, Any], members: Mapping[str, bytes],
                   artifact_ids: Sequence[str] = ()) -> dict[str, Any]:
    """Analyze exact source records addressed by a verified bundle manifest.

    ``members`` maps the manifest's logical member paths to their exact bytes.
    Every supplied route locator is rechecked against those bytes before use.
    Artifact records in the bundle and explicitly supplied safe artifact ids
    establish only source presence, never an Archive artifact identity.
    """
    if not isinstance(manifest, Mapping) or manifest.get("format") != BUNDLE_FORMAT:
        raise AudioRouteError("unsupported or missing bundle format")
    files = manifest.get("files")
    records = manifest.get("records")
    if not isinstance(files, list) or not isinstance(records, list):
        raise AudioRouteError("bundle manifest lacks files or record locators")

    file_hashes: dict[str, str] = {}
    for item in files:
        if not isinstance(item, Mapping) or not isinstance(item.get("member"), str):
            raise AudioRouteError("invalid bundle file entry")
        member = item["member"]
        raw = members.get(member)
        if not isinstance(raw, bytes):
            raise AudioRouteError("bundle member is missing or is not bytes")
        if item.get("bytes") != len(raw) or item.get("sha256") != _sha256(raw):
            raise AudioRouteError("bundle member length or digest mismatch")
        if member in file_hashes:
            raise AudioRouteError("duplicate bundle member entry")
        file_hashes[member] = item["sha256"]

    direct_rows: list[dict[str, Any]] = []
    for row in records:
        if not isinstance(row, Mapping):
            raise AudioRouteError("invalid source record locator")
        member = row.get("member")
        offset, length = row.get("offset"), row.get("length")
        if (not isinstance(member, str) or member not in file_hashes
                or row.get("member_sha256") != file_hashes[member]
                or not isinstance(offset, int) or isinstance(offset, bool) or offset < 0
                or not isinstance(length, int) or isinstance(length, bool) or length < 1):
            raise AudioRouteError("invalid or unbound source locator")
        raw_member = members[member]
        raw_record = raw_member[offset:offset + length]
        if len(raw_record) != length or row.get("sha256") != _sha256(raw_record):
            raise AudioRouteError("source record length or digest mismatch")
        record = _parse_record(raw_record)
        if record.get("schema") not in _ROUTE_SCHEMAS | _ARTIFACT_SCHEMAS:
            continue
        locator = _locator(row, record)
        direct_rows.append({"record": record, "locator": locator})

    return analyze_records(direct_rows, artifact_ids=artifact_ids)


def _segment_boundary_reasons(record: Mapping[str, Any]) -> list[str]:
    """Report invalid source boundaries without coercing or rewriting them."""
    reasons: list[str] = []
    values: dict[str, Any] = {}
    valid: dict[str, bool] = {}
    for name in ("start_seconds", "end_seconds"):
        label = "start" if name == "start_seconds" else "end"
        if name not in record:
            reasons.append(f"segment_{label}_boundary_missing")
            valid[name] = False
            continue
        value = record[name]
        values[name] = value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            reasons.append(f"segment_{label}_boundary_not_numeric")
            valid[name] = False
            continue
        if isinstance(value, float) and not math.isfinite(value):
            reasons.append(f"segment_{label}_boundary_not_finite")
            valid[name] = False
            continue
        valid[name] = True
        if value < 0:
            reasons.append(f"segment_{label}_boundary_negative")
    if valid.get("start_seconds") and valid.get("end_seconds"):
        if values["end_seconds"] < values["start_seconds"]:
            reasons.append("segment_boundaries_reversed")
        elif values["end_seconds"] == values["start_seconds"]:
            reasons.append("segment_boundaries_empty")
    return reasons


def analyze_records(rows: Sequence[Mapping[str, Any]],
                    artifact_ids: Sequence[str] = ()) -> dict[str, Any]:
    """Analyze directly supplied safe record values and exact source locators.

    Each row has ``record`` and ``locator`` mappings. The function performs no
    filesystem reads and does not accept paths as substitutes for locators.
    This entry point is useful for synthetic fixtures and caller-verified
    source reads.
    """
    parsed: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("record"), Mapping):
            raise AudioRouteError("direct input row must contain a record object")
        record = dict(row["record"])
        schema = record.get("schema")
        if schema not in _ROUTE_SCHEMAS | _ARTIFACT_SCHEMAS:
            continue
        locator = row.get("locator")
        if not isinstance(locator, Mapping):
            raise AudioRouteError("direct input row must contain its source locator")
        required = ("member", "offset", "length", "sha256", "schema", "id")
        if any(key not in locator for key in required):
            raise AudioRouteError("direct source locator is incomplete")
        expected_id_field = {
            "audio_segment": "segment_id",
            "song_performance": "performance_id",
            "artifact": "artifact_id",
            "derived_artifact": "artifact_id",
        }[schema]
        if locator.get("schema") != schema or locator.get("id") != record.get(expected_id_field):
            raise AudioRouteError("direct source locator does not match its record")
        if not isinstance(locator.get("member"), str) or not isinstance(locator.get("offset"), int) or isinstance(locator.get("offset"), bool) or locator["offset"] < 0:
            raise AudioRouteError("invalid direct source locator offset or member")
        if not isinstance(locator.get("length"), int) or isinstance(locator.get("length"), bool) or locator["length"] < 1:
            raise AudioRouteError("invalid direct source locator length")
        if not isinstance(locator.get("sha256"), str) or not locator["sha256"].startswith("sha256:"):
            raise AudioRouteError("invalid direct source locator digest")
        parsed.append({"record": record, "locator": dict(locator)})

    by_schema: dict[str, list[dict[str, Any]]] = defaultdict(list)
    all_source_rows: list[dict[str, Any]] = []
    artifact_keys = set()
    if any(not isinstance(value, str) or not value for value in artifact_ids):
        raise AudioRouteError("artifact ids must be non-empty strings")
    artifact_keys.update(artifact_ids)

    for item in parsed:
        record, locator = item["record"], item["locator"]
        schema = record["schema"]
        all_source_rows.append({"schema": schema, "id": locator["id"], "locator": locator})
        by_schema[schema].append(item)
        if schema in _ARTIFACT_SCHEMAS and isinstance(record.get("artifact_id"), str) and record["artifact_id"]:
            artifact_keys.add(record["artifact_id"])

    segments_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in by_schema["audio_segment"]:
        identifier = item["record"].get("segment_id")
        if isinstance(identifier, str) and identifier:
            segments_by_id[identifier].append(item)

    segment_successors: dict[str, list[str]] = defaultdict(list)
    for item in by_schema["audio_segment"]:
        previous = item["record"].get("supersedes_segment_id")
        current = item["record"].get("segment_id")
        if isinstance(previous, str) and previous and isinstance(current, str) and current:
            segment_successors[previous].append(current)

    performance_successors: dict[str, list[str]] = defaultdict(list)
    performance_counts: dict[str, int] = defaultdict(int)
    for item in by_schema["song_performance"]:
        previous = item["record"].get("supersedes_performance_id")
        current = item["record"].get("performance_id")
        if isinstance(current, str) and current:
            performance_counts[current] += 1
        if isinstance(previous, str) and previous and isinstance(current, str) and current:
            performance_successors[previous].append(current)

    relationships: list[dict[str, Any]] = []
    eligible_count = 0
    for item in by_schema["song_performance"]:
        performance, perf_locator = item["record"], item["locator"]
        row: dict[str, Any] = {
            "performance": perf_locator,
            "source_artifact_id": performance.get("source_artifact_id"),
            "title_evidence": performance.get("title"),
            "state": performance.get("state"),
            "supersedes_performance_id": performance.get("supersedes_performance_id"),
            "superseded_by_performance_ids": sorted(performance_successors.get(perf_locator["id"], [])),
            "source_id_occurrences": performance_counts.get(perf_locator["id"], 0),
            "segment_ids": performance.get("segment_ids"),
            "segments": [],
            "eligibility": "unresolved",
            "eligibility_reasons": [],
            "archive_route": "unresolved",
            "archive_route_reason": "accepted_audio_claim_and_identity_contract_missing",
            "song_entity_id": None,
        }
        state = performance.get("state")
        if state == "suggested":
            row["eligibility_reasons"].append("performance_is_suggested")
        elif state == "retracted":
            row["eligibility_reasons"].append("performance_is_retracted")
        elif state != "reviewed":
            row["eligibility_reasons"].append("performance_state_missing_or_unknown")
        if row["superseded_by_performance_ids"]:
            row["eligibility_reasons"].append("performance_has_a_source_successor_currentness_unresolved")
        if row["source_id_occurrences"] > 1:
            row["eligibility_reasons"].append("performance_source_id_is_ambiguous")
        title = performance.get("title")
        if not isinstance(title, str) or not title.strip():
            row["eligibility_reasons"].append("performance_title_missing")

        artifact_id = performance.get("source_artifact_id")
        if not isinstance(artifact_id, str) or not artifact_id:
            row["eligibility_reasons"].append("performance_source_artifact_missing")
        elif artifact_id not in artifact_keys:
            row["eligibility_reasons"].append("source_artifact_missing")

        segment_ids = performance.get("segment_ids")
        if not isinstance(segment_ids, list) or not segment_ids or any(not isinstance(sid, str) or not sid for sid in segment_ids):
            row["eligibility_reasons"].append("segment_selection_missing_or_malformed")
        elif len(set(segment_ids)) != len(segment_ids):
            row["eligibility_reasons"].append("segment_selection_contains_duplicate_ids")
        else:
            for segment_id in segment_ids:
                matches = segments_by_id.get(segment_id, [])
                if not matches:
                    row["segments"].append({"id": segment_id, "status": "missing_segment"})
                    row["eligibility_reasons"].append("referenced_segment_missing")
                    continue
                if len(matches) != 1:
                    row["segments"].append({"id": segment_id, "status": "ambiguous_duplicate_segment"})
                    row["eligibility_reasons"].append("referenced_segment_ambiguous")
                    continue
                segment = matches[0]["record"]
                segment_row = {"id": segment_id, "locator": matches[0]["locator"],
                               "review_state": segment.get("review_state"),
                               "source_artifact_id": segment.get("source_artifact_id"),
                               "supersedes_segment_id": segment.get("supersedes_segment_id"),
                               "superseded_by_segment_ids": sorted(segment_successors.get(segment_id, [])),
                               "start_seconds": segment.get("start_seconds"),
                               "end_seconds": segment.get("end_seconds")}
                row["segments"].append(segment_row)
                row["eligibility_reasons"].extend(_segment_boundary_reasons(segment))
                if segment.get("review_state") != "reviewed":
                    row["eligibility_reasons"].append("referenced_segment_not_reviewed")
                if segment.get("source_artifact_id") != artifact_id:
                    row["eligibility_reasons"].append("segment_artifact_disagrees_with_performance")
                if segment_successors.get(segment_id):
                    row["eligibility_reasons"].append("referenced_segment_has_source_successor_currentness_unresolved")

        if not row["eligibility_reasons"]:
            row["eligibility"] = "eligible_reviewed_source_relationship"
            eligible_count += 1
        relationships.append(row)

    return {
        "mode": "read_only_source_analysis",
        "archive_claims_proposed": False,
        "archive_entity_ids_allocated": False,
        "eligible_relationship_count": eligible_count,
        "unresolved_relationship_count": len(relationships) - eligible_count,
        "source_row_count": len(all_source_rows),
        "source_rows": all_source_rows,
        "relationships": relationships,
    }
