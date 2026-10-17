#!/usr/bin/env python3
"""Check the window matcher's thresholds against accepted performances (ADR 0071).

    calibrate_windows.py --archive <dir> [--archive-command <archive>] [--jobs <n>]
                         [--source <artifact object id>]... [--out <file>]

The V2 system's thresholds carry over with its Essentia build; this checks
that they hold on an archive's own accepted performances before suggestions
are trusted. It reads the archive only through `archive export` and its
locally present content, proposes nothing, and writes only `--out`.

Every recording that holds an accepted song occurrence (a head
`performance_work` version 3 link) is searched exactly as `enrich
match-windows` searches it. Its spans are the newest head `span_suggestions`
claim about it when one exists. Otherwise the probe and the `jam` detector
run in memory, and nothing is proposed. Its corpus is every other accepted
occurrence outside its session. Each run's best window is then judged against
the occurrences accepted on that recording:

- **correct**: the closest Work is the Work of an accepted occurrence the
  window overlaps;
- **wrong**: it is not;
- **unaccepted**: the window overlaps no accepted occurrence.

Each judgment counts as suggested when the window's distance is within the
suggestion distance, and the report lists every distance by judgment. An
accepted occurrence is reached when a run's best window overlaps at least
half of it. The summary gives precision and reach at the suggestion distance.
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import adapter  # noqa: E402
import cover  # noqa: E402
import enrich  # noqa: E402


def overlap(a: tuple[int, int], b: tuple[int, int]) -> int:
    return max(0, min(a[1], b[1]) - max(a[0], b[0]))


def spans_of(reader: adapter.ArchiveReader, artifact: dict[str, Any], args: argparse.Namespace
             ) -> tuple[int, list[cover.Span], str]:
    """The recording's suggested spans, from the archive when it holds them,
    else from the probe and detector in memory."""
    claims = [record for record in reader.records
              if record["shape"] == "claim" and reader.cites(record, enrich.SPANS_CONTRACT)
              and reader.heads.get(record["object_id"]) is record
              and any(ref["role"] == "source" and ref["revision_id"] == artifact["revision_id"]
                      for ref in record["refs"])]
    if claims:
        payload = claims[-1]["payload"]
        origin = "archive"
    else:
        import detector
        import probe
        path = enrich.content_path(reader.root, artifact)
        probed = probe.probe(path, args.ffmpeg, args.ffprobe)
        if not probed.streams:
            raise enrich.CommandError(f"{artifact['object_id']} has no countable audio stream")
        stream = detector.Stream.from_payload(probed.streams[0].payload())
        payload = detector.detect(path, stream, detector.parameters("jam")["values"], args.ffmpeg).payload()
        origin = "detected in memory"
    spans = [cover.Span(s["start_sample"], s["end_sample"], s["class"]) for s in payload["spans"]]
    return payload["sample_rate"], spans, origin


def calibrate(args: argparse.Namespace) -> dict[str, Any]:
    env = enrich.environment()
    reader = adapter.ArchiveReader(Path(args.archive), args.archive_command, env)
    works, skipped = enrich.match_corpus(reader, None, set())
    if not works:
        raise enrich.CommandError("no head performance_work version 3 link pins a head song occurrence")
    works_of: dict[str, set[str]] = {}
    occurrences: dict[str, dict[str, Any]] = {}
    for work_id, entry in works.items():
        for occurrence_id, occurrence in entry["occurrences"].items():
            works_of.setdefault(occurrence_id, set()).add(work_id)
            occurrences[occurrence_id] = occurrence
    # Each accepted occurrence, by the recording its first piece is cut from.
    pieces_of: dict[str, tuple[cover.Piece, ...]] = {}
    accepted: dict[str, list[dict[str, Any]]] = {}
    artifacts: dict[str, dict[str, Any]] = {}
    for occurrence_id, occurrence in sorted(occurrences.items()):
        pieces = enrich.selection_pieces(reader, enrich.compared_selection(reader, occurrence))
        pieces_of[occurrence_id] = tuple(
            cover.Piece(enrich.content_path(reader.root, artifact), piece["start_sample"], piece["end_sample"],
                        piece["sample_rate"]) for artifact, piece in pieces)
        artifact, first = pieces[0]
        artifacts[artifact["object_id"]] = artifact
        accepted.setdefault(artifact["object_id"], []).append(
            {"occurrence": occurrence_id, "works": sorted(works_of[occurrence_id]), "sample_rate": first["sample_rate"],
             "extent": (first["start_sample"], pieces[-1][1]["end_sample"])})
    unknown = sorted(set(args.source or ()) - set(accepted))
    if unknown:
        raise enrich.CommandError(f"no accepted occurrence is cut from {', '.join(unknown)}")
    sources = sorted(args.source) if args.source else sorted(accepted)
    limit = Decimal(cover.SEARCH["suggestion_distance"])
    report: dict[str, Any] = {"suggestion_distance": cover.SEARCH["suggestion_distance"],
                              "corpus": len(occurrences), "skipped_links": len(skipped), "sources": []}
    with cover.Comparer(args.jobs, args.ffmpeg, args.ffprobe) as comparer:
        measured = comparer.measure_corpus(pieces_of)
        report["unmeasured"] = {key: str(value) for key, value in measured.items() if isinstance(value, cover.CoverError)}
        everything = dict(comparer.corpus)
        for source_id in sources:
            artifact = artifacts[source_id]
            rate, spans, origin = spans_of(reader, artifact, args)
            session = enrich.session_artifacts(reader, source_id)
            comparer.corpus = {key: features for key, features in everything.items()
                               if not enrich.occurrence_artifacts(reader, occurrences[key]) & session}
            path = enrich.content_path(reader.root, artifact)
            here = [a for a in accepted[source_id] if a["sample_rate"] == rate]
            judged = []
            for run in cover.runs_of(spans, cover.SEARCH["run_classes"]):
                result = cover.search_run(run, spans, rate,
                                          lambda s, e: comparer.features([cover.Piece(path, s, e, rate)]),
                                          comparer.corpus, comparer.distances)
                best = result["best"]
                if best is None:
                    continue
                window = (best["window"]["start_sample"], best["window"]["end_sample"])
                closest = best["distances"][0]
                closest_works = sorted(works_of[closest.corpus_id])
                overlapping = [a for a in here if overlap(window, a["extent"])]
                if not overlapping:
                    judgment = "unaccepted"
                elif any(set(closest_works) & set(a["works"]) for a in overlapping):
                    judgment = "correct"
                else:
                    judgment = "wrong"
                judged.append({"window": best["window"], "distance": str(closest.value), "margin": best["margin"],
                               "judgment": judgment, "suggested": closest.value <= limit, "unaligned": len(best["unaligned"]),
                               "closest_works": closest_works, "steps": len(result["steps"]),
                               "accepted_works": sorted({w for a in overlapping for w in a["works"]})})
            reached = []
            for a in here:
                length = a["extent"][1] - a["extent"][0]
                best_cover = max((overlap((j["window"]["start_sample"], j["window"]["end_sample"]), a["extent"])
                                  for j in judged), default=0)
                found = any(j["judgment"] == "correct" and j["suggested"] and set(j["closest_works"]) & set(a["works"])
                            and overlap((j["window"]["start_sample"], j["window"]["end_sample"]), a["extent"])
                            for j in judged)
                reached.append({"occurrence": a["occurrence"], "covered": round(best_cover / length, 3),
                                "reached": best_cover * 2 >= length, "suggested": found})
            report["sources"].append({"artifact": source_id, "spans": origin, "corpus": len(comparer.corpus),
                                      "windows": judged, "accepted": reached})
            print(json.dumps({"source": source_id, "windows": len(judged)}), file=sys.stderr, flush=True)
    windows = [j for s in report["sources"] for j in s["windows"]]
    accepted_all = [a for s in report["sources"] for a in s["accepted"]]
    suggested = [j for j in windows if j["suggested"]]
    report["summary"] = {
        "sources": len(report["sources"]),
        "windows": len(windows),
        "suggested": len(suggested),
        "suggested_correct": sum(j["judgment"] == "correct" for j in suggested),
        "suggested_wrong": sum(j["judgment"] == "wrong" for j in suggested),
        "suggested_unaccepted": sum(j["judgment"] == "unaccepted" for j in suggested),
        "accepted": len(accepted_all),
        "accepted_reached": sum(a["reached"] for a in accepted_all),
        "accepted_suggested": sum(a["suggested"] for a in accepted_all),
        "distances": {judgment: sorted(j["distance"] for j in windows if j["judgment"] == judgment)
                      for judgment in ("correct", "wrong", "unaccepted")},
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="calibrate-windows", description=__doc__.splitlines()[0])
    parser.add_argument("--archive", required=True)
    parser.add_argument("--archive-command", default="archive")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--source", action="append",
                        help="check only this recording (an Artifact object identifier); repeatable")
    parser.add_argument("--out", type=Path, help="write the full report here; the summary is always printed")
    args = parser.parse_args(argv)
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")
    try:
        report = calibrate(args)
    except (enrich.CommandError, adapter.AdapterError, cover.CoverError) as exc:
        print(json.dumps({"status": "failed", "detail": str(exc)}), file=sys.stderr)
        return 1
    if args.out:
        args.out.write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
    print(json.dumps(report["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
