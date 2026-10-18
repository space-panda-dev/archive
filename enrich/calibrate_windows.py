#!/usr/bin/env python3
"""Check the window matcher's thresholds against accepted occurrences (ADRs 0071, 0072).

    calibrate_windows.py [all] --archive <dir> [--archive-command <archive>] [--jobs <n>]
                         [--source <artifact object id>]... [--out <file>]
    calibrate_windows.py measure --archive <dir> ... --workset <new dir>
    calibrate_windows.py search --workset <dir> [--digest <sha256:...>] --source-index <i>
                         [--windows <file.npz>] [--jobs <n>]
    calibrate_windows.py measure-windows --archive <dir> ... --workset <dir> --results <jsonl> --out <file.npz>
    calibrate_windows.py judge --workset <dir> --results <jsonl> [--out <file>]

`all` (the default) measures, searches, and judges in one process. The other
subcommands split that across machines, one task per recording:

- `measure` runs where the audio is and writes a workset: the plan, the
  corpus's profiles, and each recording's whole-recording profiles, with no
  audio. It prints the source indices to hand out.
- `search --source-index i` searches one recording from the workset alone and
  prints its result as one line. A near tie needs its window measured afresh,
  which a workset cannot do: the result then lists it under `needs_exact`.
- `measure-windows` runs where the audio is and measures every window the
  results list, into one file bound to the workset; `search --windows` with
  it decides them, and refuses a file measured for another workset.
- `judge` reads one complete result per recording and reports as `all` does.
  It refuses a result that still needs a window, and a workset in the
  earlier, per-window format.

`search` refuses a workset measured with other versions of Essentia or numpy.
That check cannot tell platforms apart: profiles differ in their low bits
between x86_64 Linux and arm64 macOS, and quarter-rate distances depend on the
BLAS library and its kernel (`cover.software`). Each result names its
software, and `judge` refuses results whose software differs, since a run with
other software is another run. Split a workset only across machines whose
software agrees.

Memory: searching one recording holds its profiles (about 1.9 MB per hour)
and the corpus's. A near tie's exact comparisons are the expensive part; on
the calibration workset a 1.55-hour recording with 3 near ties peaked at about
2.3 GB resident, against about 0.8 GB without them. `--jobs` processes share
the candidate pass and the exact comparisons.

The thresholds were fitted on one archive's accepted occurrences; this checks
them on another before its suggestions are trusted. It reads the archive only
through `archive export` and its locally present content, proposes nothing,
and writes only `--out` and `--workset`.

Every recording that holds an accepted song occurrence (a head
`performance_work` version 3 link) is searched exactly as `enrich
match-windows` searches it. Its spans are the newest head `span_suggestions`
claim about it when one exists. Otherwise the probe and the `jam` detector
run in memory, and nothing is proposed. Its corpus is every other accepted
occurrence outside its session. Each kept window is judged against the
occurrences accepted on that recording:

- **correct**: the closest Work is the Work of an accepted occurrence the
  window overlaps;
- **wrong**: it is not;
- **unaccepted**: the window overlaps no accepted occurrence.

Under the `strict` rule only accepted occurrences covering more than half of
the window count, and an accepted occurrence counts as suggested only through
a correct suggested window that it covers more than half of; under `lenient`
any overlap counts. An accepted occurrence is reached when a kept window
covers at least half of it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cover  # noqa: E402  first: it pins BLAS to one thread before numpy loads it
import numpy  # noqa: E402

import adapter  # noqa: E402
import enrich  # noqa: E402
import windows  # noqa: E402
from windows import overlap  # noqa: E402

FORMAT = "whole-recording/1"
RULES = ("lenient", "strict")


def spans_of(reader: adapter.ArchiveReader, artifact: dict[str, Any], args: argparse.Namespace
             ) -> tuple[int, int, list[cover.Span], str]:
    """The recording's sample rate, sample count, and suggested spans, from
    the archive when it holds them, else from the probe and detector in
    memory."""
    claims = [record for record in reader.records
              if record["shape"] == "claim" and reader.cites(record, enrich.SPANS_CONTRACT)
              and reader.heads.get(record["object_id"]) is record
              and any(ref["role"] == "source" and ref["revision_id"] == artifact["revision_id"]
                      for ref in record["refs"])]
    if claims:
        payload = claims[-1]["payload"]
        [stream_ref] = [ref for ref in claims[-1]["refs"] if ref["role"] == "stream"]
        sample_count = enrich.pinned(reader, stream_ref)["payload"]["sample_count"]
        origin = "archive"
    else:
        import detector
        import probe
        path = enrich.content_path(reader.root, artifact)
        probed = probe.probe(path, args.ffmpeg, args.ffprobe)
        if not probed.streams:
            raise enrich.CommandError(f"{artifact['object_id']} has no countable audio stream")
        stream = detector.Stream.from_payload(probed.streams[0].payload())
        sample_count = probed.streams[0].sample_count
        payload = detector.detect(path, stream, detector.parameters("jam")["values"], args.ffmpeg).payload()
        origin = "detected in memory"
    spans = [cover.Span(s["start_sample"], s["end_sample"], s["class"]) for s in payload["spans"]]
    return payload["sample_rate"], sample_count, spans, origin


def digest_of(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            sha.update(block)
    return "sha256:" + sha.hexdigest()


class Workset:
    """What the search needs, measured once where the audio is: the corpus's
    features and each recording's whole-recording profiles, with what judging
    needs. Written as a directory of files named by digest in its manifest,
    so that recordings can be searched on another machine without the audio
    or the archive."""

    def __init__(self, manifest: dict[str, Any], corpus: dict[str, cover.Features],
                 recordings: dict[int, numpy.ndarray]):
        self.manifest, self.corpus, self.recordings = manifest, corpus, recordings

    def write(self, directory: Path) -> str:
        directory.mkdir(parents=True, exist_ok=False)
        keys = sorted(self.corpus)
        numpy.savez(directory / "corpus.npz", **{f"profiles-{i}": self.corpus[k].profiles for i, k in enumerate(keys)},
                    **{f"mean-{i}": numpy.array(self.corpus[k].mean) for i, k in enumerate(keys)})
        for index, profiles in self.recordings.items():
            numpy.savez(directory / f"recording-{index}.npz", profiles=profiles)
        files = {path.name: digest_of(path) for path in sorted(directory.glob("*.npz"))}
        manifest = {**self.manifest, "corpus_keys": keys, "files": files}
        data = json.dumps(manifest, indent=1, sort_keys=True).encode("utf-8")
        (directory / "manifest.json").write_bytes(data)
        return "sha256:" + hashlib.sha256(data).hexdigest()

    @staticmethod
    def digest(directory: Path) -> str:
        """The digest of the workset's manifest, which names every other file."""
        return "sha256:" + hashlib.sha256((directory / "manifest.json").read_bytes()).hexdigest()

    @staticmethod
    def manifest_of(directory: Path, digest: str | None = None) -> dict[str, Any]:
        """The manifest, refused unless it has `digest` (when given) and is a
        whole-recording workset."""
        data = (directory / "manifest.json").read_bytes()
        if digest is not None and "sha256:" + hashlib.sha256(data).hexdigest() != digest:
            raise enrich.CommandError(f"{directory} is not workset {digest}")
        manifest = json.loads(data)
        if manifest.get("format") != FORMAT:
            raise enrich.CommandError(f"{directory} is not a {FORMAT} workset (format "
                                      f"{manifest.get('format')!r}); measure it again")
        return manifest

    @classmethod
    def read(cls, directory: Path, digest: str | None = None, source: int | None = None) -> "Workset":
        """The workset, refusing any file that is not what its manifest
        names. With `source`, only that recording's profiles are read."""
        manifest = cls.manifest_of(directory, digest)
        indices = [i for i in range(len(manifest["sources"])) if source is None or i == source]
        if source is not None and not indices:
            raise enrich.CommandError(f"the workset has no source {source}")
        for name in ["corpus.npz", *(f"recording-{i}.npz" for i in indices)]:
            if digest_of(directory / name) != manifest["files"].get(name):
                raise enrich.CommandError(f"{directory / name} is not the file its manifest names")
        with numpy.load(directory / "corpus.npz") as arrays:
            corpus = {key: cover.Features(arrays[f"profiles-{i}"], tuple(float(v) for v in arrays[f"mean-{i}"]))
                      for i, key in enumerate(manifest["corpus_keys"])}
        recordings = {}
        for i in indices:
            with numpy.load(directory / f"recording-{i}.npz") as arrays:
                recordings[i] = arrays["profiles"]
        return cls(manifest, corpus, recordings)


def plan(args: argparse.Namespace) -> tuple[adapter.ArchiveReader, dict[str, Any], dict[str, tuple[cover.Piece, ...]],
                                            dict[str, Path]]:
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
        rate, sample_count, spans, origin = spans_of(reader, artifact, args)
        session = enrich.session_artifacts(reader, source_id)
        sources.append({
            "artifact": source_id, "sample_rate": rate, "sample_count": sample_count, "spans_origin": origin,
            "spans": [[s.start_sample, s.end_sample, s.kind] for s in spans],
            "excluded": sorted(key for key in occurrences
                               if enrich.occurrence_artifacts(reader, occurrences[key]) & session),
            "accepted": [a for a in accepted[source_id] if a["sample_rate"] == rate]})
    manifest = {"format": FORMAT, "software": cover.profile_software(), "parameters": cover.parameters(),
                "corpus": len(occurrences), "skipped_links": len(skipped),
                "works_of": {key: sorted(value) for key, value in sorted(works_of.items())}, "sources": sources}
    paths = {s["artifact"]: enrich.content_path(reader.root, artifacts[s["artifact"]]) for s in sources}
    return reader, json.loads(json.dumps(manifest)), pieces_of, paths


def spans_from(source: dict[str, Any]) -> list[cover.Span]:
    return [cover.Span(start, end, kind) for start, end, kind in source["spans"]]


def corpus_for(source: dict[str, Any], corpus: dict[str, cover.Features]) -> dict[str, cover.Features]:
    excluded = set(source["excluded"])
    return {key: features for key, features in corpus.items() if key not in excluded}


def distance_json(d: cover.Distance) -> dict[str, Any]:
    return {"corpus": d.corpus_id, "distance": str(d.value), "raw": d.raw}


def decision_json(d: windows.Decision) -> dict[str, Any]:
    """A decision as plain data, every distance included."""
    return {"window": list(d.window), "path": d.path, "suggested": d.suggested, "work": d.work, "corpus": d.corpus,
            "work_margin": d.work_margin, "distances": [distance_json(x) for x in d.distances],
            "unaligned": d.unaligned, "too_short": d.too_short, "compared_exactly": d.compared_exactly,
            "quarter_rate": [distance_json(x) for x in d.quarter_rate]}


def search_source(manifest: dict[str, Any], index: int, everything: dict[str, cover.Features],
                  profiles: numpy.ndarray, window_features: windows.WindowFeatures, exact: windows.ExactDistances,
                  jobs: int) -> dict[str, Any]:
    """One recording, searched as `enrich match-windows` searches it, as
    plain data: the same wherever it ran with the same BLAS library."""
    source = manifest["sources"][index]
    corpus = corpus_for(source, everything)
    found = windows.search_recording(windows.Recording(profiles, source["sample_rate"]), spans_from(source), corpus,
                                     manifest["works_of"], window_features, exact, jobs)
    decisions = found.pop("decisions")
    return {"format": FORMAT, "artifact": source["artifact"], "source": index,
            "software": [list(s) for s in cover.software()],
            "needs_exact": [list(d.window) for d in decisions if d.path == "unmeasured"],
            "kept": [decision_json(d) for d in decisions], "hits": found["hits"], "regions": len(found["regions"]),
            "windows": len(found["windows"]), "too_short": found["too_short"],
            "comparisons": {"candidate_cells": found["candidate_cells"], "claims": found["claim_comparisons"],
                            "decisions": found["decision_comparisons"]}}


def judge_windows(manifest: dict[str, Any], windows_of: dict[str, list[dict[str, Any]]], rule: str
                  ) -> dict[str, Any]:
    """Each source's windows ({window, corpus, suggested}: the closest
    occurrence on the deciding path, and whether the window was suggested)
    judged against its accepted occurrences under `rule`."""
    if rule not in RULES:
        raise ValueError(rule)
    works_of = manifest["works_of"]
    sources = []
    for source in manifest["sources"]:
        if source["artifact"] not in windows_of:
            continue
        here = source["accepted"]
        judged = []
        for w in windows_of[source["artifact"]]:
            window = (w["window"]["start_sample"], w["window"]["end_sample"])
            closest_works = works_of[w["corpus"]]
            overlapping = [a for a in here if overlap(window, tuple(a["extent"]))]
            if rule == "strict":
                overlapping = [a for a in overlapping if overlap(window, tuple(a["extent"])) * 2 > window[1] - window[0]]
            if not overlapping:
                judgment = "unaccepted"
            elif any(set(closest_works) & set(a["works"]) for a in overlapping):
                judgment = "correct"
            else:
                judgment = "wrong"
            judged.append({**w, "judgment": judgment, "closest_works": closest_works,
                           "accepted_works": sorted({x for a in overlapping for x in a["works"]})})
        reached = []
        for a in here:
            extent = tuple(a["extent"])
            length = extent[1] - extent[0]
            best_cover = max((overlap((j["window"]["start_sample"], j["window"]["end_sample"]), extent)
                              for j in judged), default=0)

            def finds(j: dict[str, Any]) -> bool:
                window = (j["window"]["start_sample"], j["window"]["end_sample"])
                if not (j["judgment"] == "correct" and j["suggested"] and set(j["closest_works"]) & set(a["works"])):
                    return False
                # Strict: only the occurrence the window was judged on.
                if rule == "strict":
                    return overlap(window, extent) * 2 > window[1] - window[0]
                return overlap(window, extent) > 0
            reached.append({"occurrence": a["occurrence"], "covered": round(best_cover / length, 3),
                            "reached": best_cover * 2 >= length, "suggested": any(finds(j) for j in judged)})
        sources.append({"artifact": source["artifact"], "windows": judged, "accepted": reached})
    judged_all = [j for s in sources for j in s["windows"]]
    accepted_all = [a for s in sources for a in s["accepted"]]
    suggested = [j for j in judged_all if j["suggested"]]
    summary = {
        "sources": len(sources), "windows": len(judged_all), "suggested": len(suggested),
        "suggested_correct": sum(j["judgment"] == "correct" for j in suggested),
        "suggested_wrong": sum(j["judgment"] == "wrong" for j in suggested),
        "suggested_unaccepted": sum(j["judgment"] == "unaccepted" for j in suggested),
        "accepted": len(accepted_all), "accepted_reached": sum(a["reached"] for a in accepted_all),
        "accepted_suggested": sum(a["suggested"] for a in accepted_all),
        "accepted_untouched": sum(a["covered"] == 0 for a in accepted_all)}
    return {"summary": summary, "sources": sources}


def judge(manifest: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
    """Every recording's kept windows against what was accepted on it, under
    both rules. Refuses a missing, repeated, or incomplete result."""
    if manifest.get("format") != FORMAT:
        raise enrich.CommandError(f"the workset is not a {FORMAT} workset; measure it again")
    by_source: dict[int, dict[str, Any]] = {}
    for result in results:
        if result.get("format") != FORMAT:
            raise enrich.CommandError(f"a result is not a {FORMAT} result; search again")
        if result["source"] in by_source:
            raise enrich.CommandError(f"source {result['source']} has more than one result")
        by_source[result["source"]] = result
    sources = manifest["sources"]
    missing = [i for i in range(len(sources)) if i not in by_source]
    if missing:
        raise enrich.CommandError(f"{len(missing)} recordings have no result, the first source {missing[0]}")
    for i, result in by_source.items():
        if i >= len(sources) or result["artifact"] != sources[i]["artifact"]:
            raise enrich.CommandError(f"the result for source {i} is about another recording")
        if result["needs_exact"]:
            raise enrich.CommandError(f"source {i} still needs {len(result['needs_exact'])} windows measured "
                                      "afresh; run measure-windows and search it again with --windows")
    windows_of = {}
    for i, source in enumerate(sources):
        windows_of[source["artifact"]] = [
            {"window": {"start_sample": d["window"][0], "end_sample": d["window"][1]}, "path": d["path"],
             "corpus": d["distances"][0]["corpus"], "distance": d["distances"][0]["distance"],
             "suggested": d["suggested"]}
            for d in by_source[i]["kept"] if d["distances"]]
    report: dict[str, Any] = {key: manifest[key] for key in ("corpus", "skipped_links")}
    report["unmeasured"] = manifest.get("unmeasured", {})
    software = sorted({json.dumps(r["software"]) for r in by_source.values()})
    if len(software) > 1:
        raise enrich.CommandError(f"the results were searched with {len(software)} different software sets "
                                  f"({'; '.join(software)}); a run with other software is another run")
    report["software"] = [json.loads(s) for s in software]
    report["corpus_sizes"] = {s["artifact"]: s["corpus_size"] for s in sources}
    for rule in RULES:
        report[rule] = judge_windows(manifest, windows_of, rule)
    report["summary"] = {rule: report[rule]["summary"] for rule in RULES}
    return report


def measure(args: argparse.Namespace, comparer: cover.Comparer) -> tuple[Workset, dict[str, Path]]:
    """The plan, the corpus's features, and every recording's
    whole-recording profiles."""
    _, manifest, pieces_of, paths = plan(args)
    measured = comparer.measure_corpus(pieces_of)
    manifest["unmeasured"] = {key: str(value) for key, value in measured.items() if isinstance(value, cover.CoverError)}
    recordings = {}
    for index, source in enumerate(manifest["sources"]):
        source["corpus_size"] = len(corpus_for(source, comparer.corpus))
        recordings[index] = cover.measure_whole(paths[source["artifact"]], source["sample_rate"],
                                                source["sample_count"], args.ffmpeg, args.ffprobe,
                                                analyser=comparer.analyser)
        print(json.dumps({"measured": source["artifact"], "frames": len(recordings[index])}), file=sys.stderr,
              flush=True)
    return Workset(manifest, dict(comparer.corpus), recordings), paths


def calibrate(args: argparse.Namespace) -> dict[str, Any]:
    """Measure, search, and judge in this process, near ties on freshly
    decoded windows."""
    with cover.Comparer(args.jobs, args.ffmpeg, args.ffprobe) as comparer:
        workset, paths = measure(args, comparer)
        results = []
        for index, source in enumerate(workset.manifest["sources"]):
            path, rate = paths[source["artifact"]], source["sample_rate"]
            results.append(search_source(workset.manifest, index, workset.corpus, workset.recordings[index],
                                         lambda s, e: comparer.features([cover.Piece(path, s, e, rate)]),
                                         comparer.distances, args.jobs))
            print(json.dumps({"searched": source["artifact"], "kept": len(results[-1]["kept"])}), file=sys.stderr,
                  flush=True)
    return judge(workset.manifest, results)


# The measure-windows file's entry naming the workset it was measured for.
WORKSET_KEY = "workset"


def window_key(index: int, window: tuple[int, int]) -> str:
    return f"source-{index}-window-{window[0]}-{window[1]}"


def search(args: argparse.Namespace) -> dict[str, Any]:
    """One recording, from a workset, with any freshly measured windows."""
    workset = Workset.read(args.workset, args.digest, args.source_index)
    software = workset.manifest["software"]
    if json.loads(json.dumps(cover.profile_software())) != software:
        raise enrich.CommandError(f"this machine has {cover.profile_software()}, the workset was measured with "
                                  f"{software}")
    fresh: dict[str, numpy.ndarray] = {}
    if args.windows is not None:
        with numpy.load(args.windows) as arrays:
            fresh = {key: arrays[key] for key in arrays.files}
        bound = str(fresh.pop(WORKSET_KEY)) if WORKSET_KEY in fresh else None
        if bound != Workset.digest(args.workset):
            raise enrich.CommandError(f"{args.windows} was measured for workset {bound}, not "
                                      f"{Workset.digest(args.workset)}")

    def window_features(start: int, end: int) -> cover.Features | None:
        profiles = fresh.get(window_key(args.source_index, (start, end)))
        if profiles is None:
            return None
        return cover.Features(profiles, tuple(float(v) for v in profiles.mean(axis=0)))

    with cover.Comparer(args.jobs) as comparer:
        comparer.use_corpus(workset.corpus)
        result = search_source(workset.manifest, args.source_index, workset.corpus,
                               workset.recordings[args.source_index], window_features, comparer.distances, args.jobs)
    result["windows_file"] = None if args.windows is None else digest_of(args.windows)
    return result


def measure_windows(args: argparse.Namespace) -> dict[str, Any]:
    """Every window the results still need, measured afresh where the audio
    is, into one file."""
    manifest = Workset.manifest_of(args.workset)
    results = [json.loads(line) for line in args.results.read_text(encoding="utf-8").splitlines() if line]
    wanted = {r["source"]: [tuple(w) for w in r["needs_exact"]] for r in results if r["needs_exact"]}
    env = enrich.environment()
    reader = adapter.ArchiveReader(Path(args.archive), args.archive_command, env)
    measured: dict[str, numpy.ndarray] = {}
    with cover.Comparer(args.jobs, args.ffmpeg, args.ffprobe) as comparer:
        for index, needed in sorted(wanted.items()):
            source = manifest["sources"][index]
            path = enrich.content_path(reader.root, reader.head(source["artifact"]))
            for window, features in comparer.measure_windows(path, source["sample_rate"], needed).items():
                measured[window_key(index, window)] = features.profiles
    numpy.savez(args.out, **measured, **{WORKSET_KEY: numpy.array(Workset.digest(args.workset))})
    return {"windows": str(args.out), "digest": digest_of(args.out), "measured": len(measured),
            "sources": sorted(wanted)}


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(prog="calibrate-windows", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command")
    for name in ("all", "measure", "measure-windows"):
        command = commands.add_parser(name)
        command.add_argument("--archive", required=True)
        command.add_argument("--archive-command", default="archive")
        command.add_argument("--ffmpeg", default="ffmpeg")
        command.add_argument("--ffprobe", default="ffprobe")
        command.add_argument("--jobs", type=int, default=1)
    for name in ("all", "measure"):
        commands.choices[name].add_argument(
            "--source", action="append", help="check only this recording (an Artifact object identifier); repeatable")
    commands.choices["all"].add_argument("--out", type=Path,
                                         help="write the full report here; the summary is always printed")
    commands.choices["measure"].add_argument("--workset", type=Path, required=True,
                                             help="a new directory to write the workset into")
    command = commands.choices["measure-windows"]
    command.add_argument("--workset", type=Path, required=True)
    command.add_argument("--results", type=Path, required=True, help="search results, one JSON object per line")
    command.add_argument("--out", type=Path, required=True, help="the .npz file to write the windows' profiles to")
    command = commands.add_parser("search")
    command.add_argument("--workset", type=Path, required=True)
    command.add_argument("--digest", help="refuse the workset unless its manifest has this digest")
    command.add_argument("--source-index", type=int, required=True)
    command.add_argument("--windows", type=Path, help="freshly measured windows, from measure-windows")
    command.add_argument("--jobs", type=int, default=1)
    command = commands.add_parser("judge")
    command.add_argument("--workset", type=Path, required=True)
    command.add_argument("--results", type=Path, required=True, help="search results, one JSON object per line")
    command.add_argument("--out", type=Path)
    # Without a command, everything runs here.
    if not arguments or arguments[0] not in commands.choices:
        arguments = ["all", *arguments]
    args = parser.parse_args(arguments)
    if getattr(args, "jobs", 1) < 1:
        parser.error("--jobs must be at least 1")
    try:
        if args.command == "measure":
            with cover.Comparer(args.jobs, args.ffmpeg, args.ffprobe) as comparer:
                workset, _ = measure(args, comparer)
            digest = workset.write(args.workset)
            print(json.dumps({"workset": str(args.workset), "digest": digest,
                              "sources": list(range(len(workset.manifest["sources"])))}))
            return 0
        if args.command == "search":
            print(json.dumps(search(args), sort_keys=True))
            return 0
        if args.command == "measure-windows":
            print(json.dumps(measure_windows(args), sort_keys=True))
            return 0
        if args.command == "judge":
            manifest = Workset.manifest_of(args.workset)
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
