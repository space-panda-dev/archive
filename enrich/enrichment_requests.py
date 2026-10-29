"""Scoped operational requests through canonical composition and Bank identity.

No model, worker, archive writer or independent scheduler lives here. The
backfill's existing priority requests and proposal rounds perform the work.
"""
from __future__ import annotations

import json
import sys
from typing import Any

import adapter
import bank
import composition
import propose
import proposers


def validate_scope(artifact: dict[str, Any], stream: dict[str, Any],
                   region: dict[str, int] | None) -> dict[str, int] | None:
    """Validate exact source/stream linkage and normalize requested sample bounds.

    Authorization and existence/currentness of the pinned records are the
    caller's responsibility. Scope validity never depends on model policy.
    """
    source_pin = propose.pin(artifact)
    rate, count = stream["payload"]["sample_rate"], stream["payload"]["sample_count"]
    if not any(r.get("role") == "artifact" and r.get("id") == source_pin["id"]
               and r.get("revision_id") == source_pin["revision_id"] for r in stream.get("refs", [])):
        raise ValueError("stream does not describe the exact source revision")
    if region is not None:
        try:
            region = bank.check_region(region)
        except bank.BankError as exc:
            raise ValueError(str(exc)) from exc
        if region["rate"] != rate or region["end"] > count:
            raise ValueError("request region must lie inside the selected stream at its sample rate")
    return region


def request(store: bank.Bank, config: dict[str, Any], artifact: dict[str, Any],
            stream: dict[str, Any], region: dict[str, int] | None,
            records: list[dict[str, Any]], *, scheduler: bool,
            asked_by: str = "relay") -> dict[str, Any]:
    """Request configured note production and report an exact admitted reading.

    Caller validates authorization and exact source/stream revision existence.
    Records must be one complete World generation. This function neither
    changes policy nor interprets a scheduler's running state from absent data.
    """
    source_pin, stream_pin = propose.pin(artifact), propose.pin(stream)
    payload = stream["payload"]
    rate, count = payload["sample_rate"], payload["sample_count"]
    region = validate_scope(artifact, stream, region)
    parameters = config.get("parameters", {})
    if dict(store.parameters or {}) != parameters:
        raise ValueError("request bank and enrichment configuration parameters differ")
    answer = dict(status="not_configured", source=source_pin, stream=stream_pin,
                  region=region, inference_region=region, requests=[], diagnostics=[])
    chosen = composition.selection(config, "backfill", composition.Selection())
    # The notes contract also represents bass and other parts. This route
    # requests the explicitly configured vocal producer, never whichever part
    # happens to finish first and never an implicit ranking of all methods.
    produced = tuple(composition.ResultTarget(r.contract, ("vocal-notes",))
                     for r in chosen.produce
                     if r.contract == proposers.NOTES and "vocal-notes" in r.methods)
    if not produced:
        return answer
    chosen = composition.Selection(produce=produced)
    content = artifact["payload"]["content"]["digest"]
    # A whole cached provider wins over cutting/reseparating it. Do not change
    # the extractor's region semantics or pretend a regional run is equivalent.
    if any(store.entry(content, name) is not None for name in ("stems", "stems-6s")):
        inference_region = None
    else:
        inference_region = region
    answer["inference_region"] = inference_region
    reader = bank.Bank(store.root, store.catalog.within(content, inference_region),
                       parameters, exact=store.exact)
    context = proposers.Context(reader, content, rate, count, inference_region)
    programs = {"python": sys.executable, "sh": "/bin/sh", **config.get("programs", {})}
    expanded = composition.expand(chosen, context, reader.catalog, parameters,
                                  composition.configured(reader.catalog, programs))
    answer["diagnostics"] = [d.as_json() for d in expanded.diagnostics]
    current = {r["revision_id"] for r in records if r.get("head", True)}
    # Export rows do not carry `head`; supersession is still authoritative.
    current -= {ref["revision_id"] for r in records for ref in r.get("supersedes", [])}
    ready, available, lacked, equivalent = [], False, [], False
    for method in expanded.proposers:
        result = propose.run_result(proposers.PROPOSERS[method], context, artifact, stream)
        if isinstance(result, proposers.Lacking):
            lacked.append(result)
            continue
        available = True
        runs = {r["revision_id"] for r in adapter.equivalent_runs(records, result)}
        equivalent = equivalent or bool(runs)
        for record in records:
            if record["payload"].get("part") != "vocals":
                continue
            if record["revision_id"] not in current or not any(
                    c.get("id") == proposers.NOTES[0] and c.get("version") == proposers.NOTES[1]
                    for c in record.get("contracts", [])):
                continue
            refs = record.get("refs", [])
            if not any(r.get("role") == "run" and r.get("revision_id") in runs for r in refs):
                continue
            if not all(any(ref.get("role") == role and ref.get("id") == pin["id"]
                           and ref.get("revision_id") == pin["revision_id"] for ref in refs)
                       for role, pin in (("source", source_pin), ("stream", stream_pin))):
                continue
            if any(record["payload"] == output.payload for output in result.outputs
                   if output.contract == proposers.NOTES):
                ready.append(propose.pin(record))
    unique = {p["revision_id"]: p for p in ready}
    if unique:
        answer["status"] = "ready" if len(unique) == 1 else "ambiguous"
        if len(unique) == 1:
            answer["notes"] = next(iter(unique.values()))
        return answer
    if equivalent:
        # Normal admission would answer unchanged for this exact derivation.
        # If its output was superseded, queueing cannot recreate a current one.
        answer["status"] = "unavailable"
        answer["diagnostics"].append(dict(code="reading-not-current",
                                           reason="Exact derivation exists without a current matching notes reading"))
        return answer
    if any(d.code in ("damaged", "blocked", "unsupported-production") for d in expanded.diagnostics):
        answer["status"] = "blocked"
        return answer
    # Valid empty readings are not an invitation to run another algorithm.
    if not available and lacked and all(not item.missing for item in lacked):
        answer["status"] = "unavailable"
        answer["diagnostics"].extend(dict(code="empty-reading", **item.as_json()) for item in lacked)
        return answer
    if not scheduler:
        answer["status"] = "unavailable"
        return answer
    targets = list(expanded.targets)
    if available and not targets:
        # An existing feature can still need its archival proposal. File the
        # same normal request so Follow can wake the recording's proposal round.
        for method in expanded.proposers:
            resolved = proposers.resolve_inputs(method, context)
            declared = next(p for p in proposers.PROPOSERS[method].requirement.paths
                            if p.id == resolved.path)
            for read in declared.required:
                if read.source is not None:
                    target = proposers.FeatureTarget(read.extractor, resolved.source or read.source)
                    if target not in targets:
                        targets.append(target)
    for target in targets:
        path, key, _ = bank.ask(reader, content, target.extractor, asked_by,
                             region=inference_region, feature=target.source)
        filed = bank.check_request(json.loads(path.read_text(encoding="utf-8")))
        if filed["feature"] != target.source:
            # Existing request keys can coincide when two file-feature inputs
            # use identical parameters. Never silently dispatch another source.
            answer["status"] = "blocked"
            answer["diagnostics"].append(dict(code="request-source-conflict", key=key,
                                               requested_source=target.source,
                                               filed_source=filed["feature"]))
            return answer
        answer["requests"].append(key)
    # Features may already exist while the normal proposal round/index update
    # has not happened. Stay queued; never call Door from this read/request path.
    answer["status"] = "queued" if targets or available else "blocked"
    return answer
