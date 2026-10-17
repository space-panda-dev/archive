#!/usr/bin/env python3
"""Check the window matcher's thresholds against accepted performances (ADR 0071).

    calibrate_windows.py [all] --archive <dir> [--archive-command <archive>] [--jobs <n>]
                         [--source <artifact object id>]... [--out <file>]
    calibrate_windows.py measure --archive <dir> ... --workset <new dir>
    calibrate_windows.py search --workset <dir> [--digest <sha256:...>]
                         --source-index <i> --run-index <j> [--jobs <n>]
    calibrate_windows.py judge --workset <dir> --results <jsonl> [--out <file>]

`all` (the default) measures, searches, and judges in one process. The other
three split that across machines: `measure` runs where the audio is and
writes a workset (pitch profiles of the corpus and of every window the search
may measure, with the plan; no audio); `search` searches one run from a
workset alone and prints its result as one line; `judge` reads every run's
result and reports as `all` does. Only alignment is split, never measuring:
profiles differ in their low bits between x86_64 Linux and arm64 macOS, but
the same profiles aligned to bit-identical distances on both (66 synthetic
pairs, 2026-10-17). `search` refuses a workset measured with other software.

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
import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy

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


class Workset:
    """What the search needs, measured once where the audio is: the corpus's
    features and every candidate window's, with what judging needs. Written
    as a directory of files named by digest in its manifest, so that runs
    can be searched on another machine without the audio or the archive."""

    def __init__(self, manifest: dict[str, Any], corpus: dict[str, cover.Features],
                 windows: dict[int, dict[tuple[int, int], cover.Features]]):
        self.manifest, self.corpus, self.windows = manifest, corpus, windows

    def write(self, directory: Path) -> str:
        directory.mkdir(parents=True, exist_ok=False)
        keys = sorted(self.corpus)
        numpy.savez(directory / "corpus.npz", **{f"profiles-{i}": self.corpus[k].profiles for i, k in enumerate(keys)},
                    **{f"mean-{i}": numpy.array(self.corpus[k].mean) for i, k in enumerate(keys)})
        for index, windows in self.windows.items():
            numpy.savez(directory / f"source-{index}.npz",
                        **{f"window-{start}-{end}": features.profiles for (start, end), features in windows.items()})
        files = {path.name: digest_of(path) for path in sorted(directory.glob("*.npz"))}
        manifest = {**self.manifest, "corpus_keys": keys, "files": files}
        data = json.dumps(manifest, indent=1, sort_keys=True).encode("utf-8")
        (directory / "manifest.json").write_bytes(data)
        return "sha256:" + hashlib.sha256(data).hexdigest()

    @classmethod
    def read(cls, directory: Path, digest: str | None = None, source: int | None = None) -> "Workset":
        """The workset, refusing any file that is not what its manifest
        names. With `source`, only that recording's windows are read."""
        data = (directory / "manifest.json").read_bytes()
        if digest is not None and "sha256:" + hashlib.sha256(data).hexdigest() != digest:
            raise enrich.CommandError(f"{directory} is not workset {digest}")
        manifest = json.loads(data)
        wanted = ["corpus.npz", *(f"source-{i}.npz" for i in range(len(manifest["sources"]))
                                  if source is None or i == source)]
        for name in wanted:
            if digest_of(directory / name) != manifest["files"].get(name):
                raise enrich.CommandError(f"{directory / name} is not the file its manifest names")
        with numpy.load(directory / "corpus.npz") as arrays:
            corpus = {key: cover.Features(arrays[f"profiles-{i}"], tuple(float(v) for v in arrays[f"mean-{i}"]))
                      for i, key in enumerate(manifest["corpus_keys"])}
        windows = {}
        for name in wanted[1:]:
            index = int(name.removeprefix("source-").removesuffix(".npz"))
            with numpy.load(directory / name) as arrays:
                windows[index] = {tuple(int(v) for v in key.removeprefix("window-").split("-")):
                                  cover.Features(arrays[key], tuple(float(v) for v in arrays[key].mean(axis=0)))
                                  for key in arrays.files}
        return cls(manifest, corpus, windows)


def digest_of(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            sha.update(block)
    return "sha256:" + sha.hexdigest()


def plan(args: argparse.Namespace) -> tuple[adapter.ArchiveReader, dict[str, Any], dict[str, tuple[cover.Piece, ...]],
                                            dict[str, dict[str, Any]]]:
    """Which recordings are checked, against which occurrences, and what was
    accepted on each: everything but the measurements."""
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
             "extent": [first["start_sample"], pieces[-1][1]["end_sample"]]})
    unknown = sorted(set(args.source or ()) - set(accepted))
    if unknown:
        raise enrich.CommandError(f"no accepted occurrence is cut from {', '.join(unknown)}")
    sources = []
    for source_id in (sorted(args.source) if args.source else sorted(accepted)):
        artifact = artifacts[source_id]
        rate, spans, origin = spans_of(reader, artifact, args)
        session = enrich.session_artifacts(reader, source_id)
        sources.append({
            "artifact": source_id, "sample_rate": rate, "spans_origin": origin,
            "spans": [[s.start_sample, s.end_sample, s.kind] for s in spans],
            "runs": cover.runs_of(spans, cover.SEARCH["run_classes"]),
            "excluded": sorted(key for key in occurrences
                               if enrich.occurrence_artifacts(reader, occurrences[key]) & session),
            "accepted": [a for a in accepted[source_id] if a["sample_rate"] == rate]})
    manifest = {"suggestion_distance": cover.SEARCH["suggestion_distance"], "software": cover.software(),
                "parameters": cover.parameters(), "corpus": len(occurrences), "skipped_links": len(skipped),
                "works_of": {key: sorted(value) for key, value in sorted(works_of.items())}, "sources": sources}
    paths = {s["artifact"]: enrich.content_path(reader.root, artifacts[s["artifact"]]) for s in sources}
    return reader, json.loads(json.dumps(manifest)), pieces_of, paths


def spans_from(source: dict[str, Any]) -> list[cover.Span]:
    return [cover.Span(start, end, kind) for start, end, kind in source["spans"]]


def corpus_for(source: dict[str, Any], corpus: dict[str, cover.Features]) -> dict[str, cover.Features]:
    excluded = set(source["excluded"])
    return {key: features for key, features in corpus.items() if key not in excluded}


def search_one(source: dict[str, Any], run_index: int, comparer: cover.Comparer, everything: dict[str, cover.Features],
               window_features: cover.WindowFeatures) -> dict[str, Any]:
    """One run of one recording, searched as `enrich match-windows` searches
    it, as plain data: the same wherever it ran."""
    comparer.corpus = corpus_for(source, everything)
    run = source["runs"][run_index]
    result = cover.search_run(run, spans_from(source), source["sample_rate"], window_features,
                              comparer.corpus, comparer.distances)
    best = result["best"]
    if best is not None:
        result["best"] = {"step": best["step"], "window": best["window"], "margin": best["margin"],
                          "unaligned": len(best["unaligned"]),
                          "closest": [{"corpus": d.corpus_id, "distance": str(d.value)} for d in best["distances"][:5]]}
    return {"artifact": source["artifact"], "run": run_index, "result": result}


def judge(manifest: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
    """Each run's best window against what was accepted on its recording."""
    limit = Decimal(manifest["suggestion_distance"])
    works_of = manifest["works_of"]
    by_run = {(r["artifact"], r["run"]): r["result"] for r in results}
    report: dict[str, Any] = {key: manifest[key] for key in ("suggestion_distance", "corpus", "skipped_links")}
    report.update(unmeasured=manifest.get("unmeasured", {}), sources=[])
    missing = [(s["artifact"], i) for s in manifest["sources"] for i in range(len(s["runs"]))
               if (s["artifact"], i) not in by_run]
    if missing:
        raise enrich.CommandError(f"{len(missing)} runs have no result, the first {missing[0][0]} run {missing[0][1]}")
    for source in manifest["sources"]:
        here = source["accepted"]
        judged = []
        for index in range(len(source["runs"])):
            best = by_run[(source["artifact"], index)]["best"]
            if best is None:
                continue
            window = (best["window"]["start_sample"], best["window"]["end_sample"])
            closest = best["closest"][0]
            distance = Decimal(closest["distance"])
            closest_works = works_of[closest["corpus"]]
            overlapping = [a for a in here if overlap(window, tuple(a["extent"]))]
            if not overlapping:
                judgment = "unaccepted"
            elif any(set(closest_works) & set(a["works"]) for a in overlapping):
                judgment = "correct"
            else:
                judgment = "wrong"
            judged.append({"window": best["window"], "distance": closest["distance"], "margin": best["margin"],
                           "judgment": judgment, "suggested": distance <= limit, "unaligned": best["unaligned"],
                           "closest_works": closest_works, "steps": len(by_run[(source["artifact"], index)]["steps"]),
                           "accepted_works": sorted({w for a in overlapping for w in a["works"]})})
        reached = []
        for a in here:
            extent = tuple(a["extent"])
            length = extent[1] - extent[0]
            best_cover = max((overlap((j["window"]["start_sample"], j["window"]["end_sample"]), extent)
                              for j in judged), default=0)
            found = any(j["judgment"] == "correct" and j["suggested"] and set(j["closest_works"]) & set(a["works"])
                        and overlap((j["window"]["start_sample"], j["window"]["end_sample"]), extent)
                        for j in judged)
            reached.append({"occurrence": a["occurrence"], "covered": round(best_cover / length, 3),
                            "reached": best_cover * 2 >= length, "suggested": found})
        report["sources"].append({"artifact": source["artifact"], "spans": source["spans_origin"],
                                  "corpus": source["corpus_size"],
                                  "windows": judged, "accepted": reached})
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
        "unaligned_windows": sum(j["unaligned"] > 0 for j in windows),
        "distances": {judgment: sorted(j["distance"] for j in windows if j["judgment"] == judgment)
                      for judgment in ("correct", "wrong", "unaccepted")},
    }
    return report


def measure(args: argparse.Namespace, comparer: cover.Comparer, every_window: bool
            ) -> tuple[Workset, dict[str, Path]]:
    """The plan and the corpus's features; with `every_window`, every
    candidate window's features too."""
    _, manifest, pieces_of, paths = plan(args)
    measured = comparer.measure_corpus(pieces_of)
    manifest["unmeasured"] = {key: str(value) for key, value in measured.items() if isinstance(value, cover.CoverError)}
    for source in manifest["sources"]:
        source["corpus_size"] = len(corpus_for(source, comparer.corpus))
    windows: dict[int, dict[tuple[int, int], cover.Features]] = {}
    if every_window:
        for index, source in enumerate(manifest["sources"]):
            rate, spans = source["sample_rate"], spans_from(source)
            wanted = sorted({(w["start_sample"], w["end_sample"]) for run in source["runs"]
                             for _, w in cover.candidate_windows(run, spans, rate)})
            windows[index] = comparer.measure_windows(paths[source["artifact"]], rate, wanted)
            print(json.dumps({"measured": source["artifact"], "windows": len(wanted)}), file=sys.stderr, flush=True)
    return Workset(manifest, dict(comparer.corpus), windows), paths


def calibrate(args: argparse.Namespace) -> dict[str, Any]:
    """Measure, search, and judge in this process."""
    with cover.Comparer(args.jobs, args.ffmpeg, args.ffprobe) as comparer:
        workset, paths = measure(args, comparer, every_window=False)
        everything = dict(comparer.corpus)
        results = []
        for source in workset.manifest["sources"]:
            path, rate = paths[source["artifact"]], source["sample_rate"]
            for index in range(len(source["runs"])):
                results.append(search_one(source, index, comparer, everything,
                                          lambda s, e: comparer.features([cover.Piece(path, s, e, rate)])))
            print(json.dumps({"source": source["artifact"], "runs": len(source["runs"])}), file=sys.stderr, flush=True)
    return judge(workset.manifest, results)


def search(args: argparse.Namespace) -> dict[str, Any]:
    """One run, from a workset."""
    workset = Workset.read(args.workset, args.digest, args.source_index)
    source = workset.manifest["sources"][args.source_index]
    windows = workset.windows[args.source_index]

    def window_features(start: int, end: int) -> cover.Features:
        if (start, end) not in windows:
            raise enrich.CommandError(f"the workset has no window {start}-{end}")
        return windows[(start, end)]

    software = workset.manifest["software"]
    if json.loads(json.dumps(cover.software())) != software:
        raise enrich.CommandError(f"this machine has {cover.software()}, the workset was measured with {software}")
    with cover.Comparer(args.jobs) as comparer:
        comparer.use_corpus(workset.corpus)
        return search_one(source, args.run_index, comparer, workset.corpus, window_features)


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(prog="calibrate-windows", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command")
    for name in ("all", "measure"):
        command = commands.add_parser(name)
        command.add_argument("--archive", required=True)
        command.add_argument("--archive-command", default="archive")
        command.add_argument("--ffmpeg", default="ffmpeg")
        command.add_argument("--ffprobe", default="ffprobe")
        command.add_argument("--jobs", type=int, default=1)
        command.add_argument("--source", action="append",
                             help="check only this recording (an Artifact object identifier); repeatable")
    commands.choices["all"].add_argument("--out", type=Path,
                                         help="write the full report here; the summary is always printed")
    commands.choices["measure"].add_argument("--workset", type=Path, required=True,
                                             help="a new directory to write the workset into")
    command = commands.add_parser("search")
    command.add_argument("--workset", type=Path, required=True)
    command.add_argument("--digest", help="refuse the workset unless its manifest has this digest")
    command.add_argument("--source-index", type=int, required=True)
    command.add_argument("--run-index", type=int, required=True)
    command.add_argument("--jobs", type=int, default=1)
    command = commands.add_parser("judge")
    command.add_argument("--workset", type=Path, required=True)
    command.add_argument("--results", type=Path, required=True, help="search results, one JSON object per line")
    command.add_argument("--out", type=Path)
    # Without a command, everything runs here, as before the split.
    if not arguments or arguments[0] not in commands.choices:
        arguments = ["all", *arguments]
    args = parser.parse_args(arguments)
    if getattr(args, "jobs", 1) < 1:
        parser.error("--jobs must be at least 1")
    try:
        if args.command == "measure":
            with cover.Comparer(args.jobs, args.ffmpeg, args.ffprobe) as comparer:
                workset, _ = measure(args, comparer, every_window=True)
            digest = workset.write(args.workset)
            print(json.dumps({"workset": str(args.workset), "digest": digest,
                              "runs": [[i, r] for i, s in enumerate(workset.manifest["sources"])
                                       for r in range(len(s["runs"]))]}))
            return 0
        if args.command == "search":
            print(json.dumps(search(args), sort_keys=True))
            return 0
        if args.command == "judge":
            manifest = json.loads((args.workset / "manifest.json").read_text(encoding="utf-8"))
            results = [json.loads(line) for line in args.results.read_text(encoding="utf-8").splitlines() if line]
            report = judge(manifest, results)
        else:
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
