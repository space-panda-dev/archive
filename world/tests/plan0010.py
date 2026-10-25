"""Specification 0.0.10's records, drafted without Door.

Door admits every contract specification 0.0.10 adds only once it enforces
their profile rules (D-18), so the fixture archive cannot hold them yet.
Until it can, World's tests draft them as fixture/build.py drafts a plan's
records: fixture/plan.json and then world/tests/fixtures/plan-0010.json, on
the operator that a real `archive init` appointed (the Door chain export's),
with the identities, revisions, citations, and content digests the builder
would give them. Nothing here admits anything; the drafts are World's input
only, and World re-checks each by its own modules before it projects it.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from fixture import build

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
PLANS = (ROOT / "fixture/plan.json", FIXTURES / "plan-0010.json")
CONTENT = (ROOT / "fixture/content", FIXTURES / "content")
BASE = FIXTURES / "door-world-chain-export.json"
# The current release: the drafts cite its catalog, which holds every 0.0.10
# bundle with its exact bytes and the versions 0.0.11 adds (ADR 0113).
SPEC = "0.0.12"


def _content(name: str) -> dict[str, Any]:
    for directory in CONTENT:
        if (directory / name).is_file():
            data = (directory / name).read_bytes()
            return {"digest": "sha256:" + hashlib.sha256(data).hexdigest(), "size": len(data)}
    raise FileNotFoundError(name)


def _export_key(payload: dict[str, Any], refs: list[dict[str, Any]]) -> str:
    def pins(role: str) -> list[list[str]]:
        return [[ref["id"], ref["revision_id"]] for ref in refs if ref["role"] == role]
    material = {"format": payload["format"], "exporter": payload["exporter"], "options": payload.get("options", {}),
                "rendered": pins("rendered"), "joined": pins("joined")}
    return "sha256:" + hashlib.sha256(build.canonical(material)).hexdigest()


def drafted_export() -> dict[str, Any]:
    """An export of the fixture plan and World's 0.0.10 plan, drafted."""
    base = json.loads(BASE.read_text())
    archive_id = base["archive_id"]
    [initialization] = [r for r in base["records"] if any(
        c["id"] == "archive.transition/operator_initialization" for c in r.get("contracts", []))]
    [appointed] = [ref for ref in initialization["refs"] if ref["role"] == "appoints"]
    records = [r for r in base["records"] if r["revision_id"] in (appointed["revision_id"], initialization["revision_id"])]
    operator = appointed["id"]
    installed = {(c["id"], c["version"]): c["digest"]
                 for c in json.loads((ROOT / "spec" / SPEC / "contracts.json").read_text())["contracts"]}
    heads: dict[str, tuple[str, str]] = {"operator": (operator, appointed["revision_id"])}
    minute = 0
    for path in PLANS:
        for step in json.loads(path.read_text())["steps"]:
            for record in step["records"]:
                draft = _draft(record, heads, archive_id, operator, installed)
                revision = build.revision_of(draft)
                heads[record["key"]] = (draft["object_id"], revision)
                minute += 1
                records.append({**draft, "revision_id": revision,
                                "at": f"2026-01-02T{minute // 60:02d}:{minute % 60:02d}:00Z"})
    export = {key: base[key] for key in ("archive_id", "context", "snapshot", "spec", "status")}
    # The bundles the catalog installs now, not those of the export the base
    # was cut from: a bundle amended or added since (as 0.0.10's were before
    # release) is the one each draft cites by digest.
    export["contracts"] = [
        {key: bundle[key] for key in ("context", "id", "schema", "version")}
        for bundle in (json.loads((ROOT / "spec/contracts" / ("sha256-" + digest.removeprefix("sha256:") + ".json"))
                                  .read_text()) for digest in installed.values())]
    export["records"] = records
    return export


def _draft(record: dict[str, Any], heads: dict[str, tuple[str, str]], archive_id: str, operator: str,
           installed: dict[tuple[str, str], str]) -> dict[str, Any]:
    def resolve(value: Any) -> Any:
        if isinstance(value, str) and value.startswith("$content:"):
            return _content(value.removeprefix("$content:"))
        if isinstance(value, str) and value.startswith("$revision:"):
            return heads[value.removeprefix("$revision:")][1]
        if isinstance(value, dict):
            return {key: resolve(item) for key, item in value.items()}
        if isinstance(value, list):
            return [resolve(item) for item in value]
        return value

    supersedes = []
    if "revises" in record:
        target, previous = heads[record["revises"]]
        supersedes = [{"role": "supersedes", "id": target, "revision_id": previous}]
    else:
        target = build.object_id(archive_id, record["namespace"], record["name"])
    payload = resolve(copy.deepcopy(record.get("payload", {})))
    if record["shape"] == "artifact":
        payload = {"content": _content(record["content"])}
    refs = [{"role": ref["role"], "id": heads[ref["pin"]][0], "revision_id": heads[ref["pin"]][1]}
            for ref in record.get("refs", [])]
    agent = record.get("agent", operator)
    if agent.startswith("@"):
        agent = heads[agent.removeprefix("@")][0]
    if payload.get("key") == "$export-key":
        payload["key"] = _export_key(payload, refs)
    draft = {"spec": "archive/" + SPEC, "object_id": target, "shape": record["shape"], "by": build.BY,
             "agent": agent, "payload": payload, "refs": refs, "supersedes": supersedes}
    if "source" in record:
        draft["source"] = record["source"]
    if record.get("contracts"):
        draft["contracts"] = [{"id": c["id"], "version": c["version"], "digest": installed[(c["id"], c["version"])]}
                              for c in record["contracts"]]
    return draft
