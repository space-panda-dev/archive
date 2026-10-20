#!/usr/bin/env python3
"""Run an enrichment tool against an archive's records and propose its result.

    enrich probe --archive <dir> --artifact <object id> [--staging <annex>] [--export <file>] [--dry-run]
    enrich detect --archive <dir> --artifact <object id> [--stream <claim id>] [--preset jam|show|studio]
                  [--staging <annex>] [--dry-run]
    enrich match --archive <dir> (--selection | --occurrence) <object id> --limit <decimal>
                 [--staging <annex>] [--dry-run]
    enrich match-windows --archive <dir> --suggestions <object id> [--jobs <n>] [--profile-cache <dir>]
                 [--staging <annex>] [--dry-run | --regenerate]
    enrich propose --archive <dir> (--artifact <object id> [--stream <claim id>] | --all)
                   [--proposers <name,...>] [--bank <root>] [--config <file>] [--staging <annex>] [--dry-run]
    enrich backfill --archive <dir> --bank <root> [--config <file>] [--mode songs|eager|on-demand] [--cores <n>]
                    [--spool <command>] [--commit] [--retry-failed] [--world-store <dir>] [--log <file>]

`backfill` (backfill.py, ADR 0112) probes, fills the feature bank, and
proposes for every recording; it streams one JSON line per event and is a
dry run unless `--commit`.

The command reads the archive only through `archive export`, runs the tool on
locally present content, and proposes the run and its claims to `door admit`
through the adapter (ADR 0064). It prints one JSON object.
Exit status: 0 when the claims were committed, predicted, or already
recorded; 3 when the tool kept nothing to propose; 1 on any failure; and 2,
as argparse decides, when the arguments are not understood.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import adapter  # noqa: E402
import probe as probe_tool  # noqa: E402

STREAM_CONTRACT = ("archive.claim/audio_stream", "1")
SPANS_CONTRACT = ("archive.claim/span_suggestions", "1")
MATCH_CONTRACT = ("archive.claim/work_match", "2")
WINDOW_MATCH_CONTRACT = ("archive.claim/work_match", "3")
SESSION_CONTRACT = ("archive.claim/session_parts", "1")
SELECTION_CONTRACT = ("archive.claim/audio_selection", "2")
OCCURRENCE_CONTRACT = ("archive.claim/song_occurrence", "2")
LINK_CONTRACT = ("archive.claim/performance_work", "3")
# Not 2: argparse exits 2 on arguments it does not understand, and a caller
# must never read a mistyped command as "this file has no audio".
NOTHING_PROPOSED = 3


class CommandError(Exception):
    pass


def environment() -> dict[str, str]:
    """Door needs absolute git and git-annex executables; take them from PATH
    when unset, as the fixture builder does."""
    env = dict(os.environ)
    for variable, program in (("ARCHIVE_GIT_EXECUTABLE", "git"), ("ARCHIVE_GIT_ANNEX_EXECUTABLE", "git-annex")):
        if variable not in env:
            found = shutil.which(program)
            if found is None:
                raise CommandError(f"{program} is not on PATH and {variable} is unset")
            env[variable] = os.path.realpath(found)
    return env


def reader_of(args: argparse.Namespace, env: dict[str, str]) -> adapter.ArchiveReader:
    """The archive as `archive export` reports it, or as a caller already
    exported it (`--export`), so an importer running many commands need not
    export every record for each. A given export must name the archive's
    current commit: proposing against an older snapshot could repeat what
    was committed since."""
    if args.export is None:
        return adapter.ArchiveReader(Path(args.archive), args.archive_command, env)
    try:
        export = json.loads(Path(args.export).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CommandError(f"the export {args.export} could not be read: {exc}") from exc
    if not isinstance(export, dict) or export.get("status") != "ok":
        raise CommandError(f"the export {args.export} is not a complete archive export")
    head = subprocess.run([env["ARCHIVE_GIT_EXECUTABLE"], "-C", str(args.archive), "rev-parse", "HEAD"],
                          capture_output=True, text=True)
    if head.returncode != 0:
        raise CommandError(f"the archive's commit could not be read: {head.stderr.strip()}")
    if export.get("snapshot") != head.stdout.strip():
        raise CommandError(f"the export names commit {export.get('snapshot')}, but the archive is at "
                           f"{head.stdout.strip()}")
    return adapter.ArchiveReader.from_export(Path(args.archive), export)


def content_location(root: Path, artifact: dict[str, Any]) -> Path:
    """Where the Artifact's bytes are in the archive's content worktree
    (layout archive/2, D-1), refused unless a file of the size the record
    states is there. Its digest is not checked: `content_path` reads it."""
    content = artifact["payload"]["content"]
    hexdigest = content["digest"].removeprefix("sha256:")
    path = root / "content" / "sha256" / hexdigest[0:2] / hexdigest[2:4] / hexdigest
    if not path.is_file():
        raise CommandError(f"the content of {artifact['object_id']} is not locally present at {path}")
    if path.stat().st_size != content["size"]:
        raise CommandError(f"the local content of {artifact['object_id']} does not match its record")
    return path


def content_path(root: Path, artifact: dict[str, Any]) -> Path:
    """The Artifact's bytes in the archive's content worktree (layout
    archive/2, D-1), checked against the digest and size the record states.
    Reading them changes nothing."""
    content = artifact["payload"]["content"]
    path = content_location(root, artifact)
    hasher, size = hashlib.sha256(), 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            hasher.update(block)
            size += len(block)
    if "sha256:" + hasher.hexdigest() != content["digest"] or size != content["size"]:
        raise CommandError(f"the local content of {artifact['object_id']} does not match its record")
    return path


def run_probe(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    env = environment()
    reader = reader_of(args, env)
    # Refuse an archive that cannot hold the claims before decoding anything.
    reader.citation(adapter.RUN_CONTRACT)
    reader.citation(STREAM_CONTRACT)
    artifact = reader.head(args.artifact)
    if artifact["shape"] != "artifact":
        raise CommandError(f"{args.artifact} is a {artifact['shape']}, not an Artifact")
    pin = {"object_id": artifact["object_id"], "revision_id": artifact["revision_id"]}
    used = (adapter.Pin("used", artifact["object_id"], artifact["revision_id"]),)
    software = probe_tool.software(args.ffmpeg, args.ffprobe)
    started = adapter.now()
    # Rerunning on unchanged inputs writes nothing (INV-5), and skips the decode.
    candidate = adapter.RunResult(agent=probe_tool.AGENT, method_id=probe_tool.METHOD_ID,
                                  method_version=probe_tool.METHOD_VERSION, software=software,
                                  started_at=started, ended_at=started, used=used, outputs=(),
                                  parameters=probe_tool.parameters())
    previous = reader.equivalent_run(candidate)
    if previous is not None:
        return 0, {"status": "unchanged", "artifact": pin,
                   "run": {"object_id": previous["object_id"], "revision_id": previous["revision_id"]}}
    path = content_path(reader.root, artifact)
    result = probe_tool.probe(path, args.ffmpeg, args.ffprobe)
    ended = adapter.now()
    if not result.streams:
        return NOTHING_PROPOSED, {"status": "nothing_proposed", "artifact": pin, "probe": result.as_json()}
    outputs = tuple(
        adapter.Output(contract=STREAM_CONTRACT, name=f"stream-{stream.stream_index}",
                       payload=stream.payload(),
                       refs=(adapter.Pin("artifact", artifact["object_id"], artifact["revision_id"]),))
        for stream in result.streams)
    run = adapter.RunResult(agent=probe_tool.AGENT, method_id=probe_tool.METHOD_ID,
                            method_version=probe_tool.METHOD_VERSION, software=software,
                            started_at=started, ended_at=ended, used=used, outputs=outputs,
                            parameters=probe_tool.parameters())
    admission = adapter.propose(reader, run, door=args.door, staging=staging_of(args, env), dry_run=args.dry_run,
                                env=env)
    return 0, {"artifact": pin, "probe": result.as_json(), **admission.as_json()}


def stream_claims(reader: adapter.ArchiveReader, artifact: dict[str, Any]) -> list[dict[str, Any]]:
    """The current revisions of the audio_stream claims about exactly this
    Artifact revision, in export order."""
    found = []
    for record in reader.records:
        if record["shape"] != "claim" or not reader.cites(record, STREAM_CONTRACT) \
                or reader.heads.get(record["object_id"]) is not record:
            continue
        if any(ref.get("role") == "artifact" and ref.get("id") == artifact["object_id"]
               and ref.get("revision_id") == artifact["revision_id"] for ref in record.get("refs", [])):
            found.append(record)
    return found


def choose_stream(reader: adapter.ArchiveReader, artifact: dict[str, Any], wanted: str | None) -> dict[str, Any]:
    """The stream claim the spans are measured against: the one named, or
    the only one about this Artifact revision. Several are never chosen
    between silently."""
    claims = stream_claims(reader, artifact)
    if wanted is not None:
        for claim in claims:
            if claim["object_id"] == wanted:
                return claim
        raise CommandError(f"{wanted} is not an audio_stream claim about revision {artifact['revision_id']} "
                           f"of {artifact['object_id']}")
    if not claims:
        raise CommandError(f"no audio_stream claim states the facts of revision {artifact['revision_id']} of "
                           f"{artifact['object_id']}; run enrich probe first")
    if len(claims) > 1:
        listed = ", ".join(f"{c['object_id']} (stream {c['payload'].get('stream_index')})" for c in claims)
        raise CommandError(f"several audio_stream claims are about this Artifact revision; name one with "
                           f"--stream: {listed}")
    return claims[0]


def run_detect(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    import detector  # numpy is needed only here
    env = environment()
    reader = reader_of(args, env)
    # Refuse an archive that cannot hold the claims before decoding anything.
    reader.citation(adapter.RUN_CONTRACT)
    reader.citation(SPANS_CONTRACT)
    artifact = reader.head(args.artifact)
    if artifact["shape"] != "artifact":
        raise CommandError(f"{args.artifact} is a {artifact['shape']}, not an Artifact")
    stream_claim = choose_stream(reader, artifact, args.stream)
    stream = detector.Stream.from_payload(stream_claim["payload"])
    pins = {"artifact": {"object_id": artifact["object_id"], "revision_id": artifact["revision_id"]},
            "stream": {"object_id": stream_claim["object_id"], "revision_id": stream_claim["revision_id"]}}
    used = (adapter.Pin("used", artifact["object_id"], artifact["revision_id"]),
            adapter.Pin("used", stream_claim["object_id"], stream_claim["revision_id"]))
    parameters = detector.parameters(args.preset)
    software = detector.software(args.ffmpeg)
    started = adapter.now()
    # Rerunning on unchanged inputs writes nothing (INV-5), and skips the decode.
    candidate = adapter.RunResult(agent=detector.AGENT, method_id=detector.METHOD_ID,
                                  method_version=detector.METHOD_VERSION, software=software,
                                  started_at=started, ended_at=started, used=used, outputs=(),
                                  parameters=parameters)
    previous = reader.equivalent_run(candidate)
    if previous is not None:
        return 0, {"status": "unchanged", **pins,
                   "run": {"object_id": previous["object_id"], "revision_id": previous["revision_id"]}}
    path = content_path(reader.root, artifact)
    detection = detector.detect(path, stream, parameters["values"], args.ffmpeg)
    ended = adapter.now()
    output = adapter.Output(contract=SPANS_CONTRACT, name="spans", payload=detection.payload(),
                            refs=(adapter.Pin("source", artifact["object_id"], artifact["revision_id"]),
                                  adapter.Pin("stream", stream_claim["object_id"], stream_claim["revision_id"])))
    run = adapter.RunResult(agent=detector.AGENT, method_id=detector.METHOD_ID,
                            method_version=detector.METHOD_VERSION, software=software,
                            started_at=started, ended_at=ended, used=used, outputs=(output,),
                            parameters=parameters)
    admission = adapter.propose(reader, run, door=args.door, staging=staging_of(args, env), dry_run=args.dry_run,
                                env=env)
    return 0, {**pins, "detection": detection.as_json(), **admission.as_json()}


def staging_of(args: argparse.Namespace, env: dict[str, str]) -> adapter.Staging | None:
    if not args.staging:
        return None
    return adapter.Staging(Path(args.staging), env["ARCHIVE_GIT_EXECUTABLE"], env["ARCHIVE_GIT_ANNEX_EXECUTABLE"], env)


# -- match -------------------------------------------------------------------

def pin_of(record: dict[str, Any]) -> dict[str, str]:
    return {"object_id": record["object_id"], "revision_id": record["revision_id"]}


def pinned(reader: adapter.ArchiveReader, ref: dict[str, Any]) -> dict[str, Any]:
    record = reader.by_revision.get(ref.get("revision_id"))
    if record is None or record["object_id"] != ref.get("id"):
        raise CommandError(f"the export lacks revision {ref.get('revision_id')} of {ref.get('id')}")
    return record


def compared_selection(reader: adapter.ArchiveReader, occurrence: dict[str, Any]) -> dict[str, Any]:
    """The audio an occurrence is compared by: its first selection, in the
    canonical order the contract keeps its references in."""
    refs = [ref for ref in occurrence["refs"] if ref["role"] == "selection"]
    if not refs:
        raise CommandError(f"occurrence {occurrence['revision_id']} pins no selection")
    return pinned(reader, refs[0])


def selection_pieces(reader: adapter.ArchiveReader,
                     selection: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """The first track's spans, each with the Artifact revision it pins. The
    tracks of one selection capture the same moment, so one is compared."""
    if not reader.cites(selection, SELECTION_CONTRACT):
        raise CommandError(f"{selection['object_id']} does not cite {SELECTION_CONTRACT[0]} "
                           f"version {SELECTION_CONTRACT[1]}")
    first = selection["payload"]["tracks"][0]["pieces"]
    refs = [ref for ref in selection["refs"] if ref["role"] == "piece"][:len(first)]
    return [(pinned(reader, ref), piece) for ref, piece in zip(refs, first, strict=True)]


def match_corpus(reader: adapter.ArchiveReader, compared_object: str | None, compared_selections: set[str],
                 compared_artifacts: frozenset[str] = frozenset()
                 ) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Every Work that a head `performance_work` version 3 link pins, with the
    occurrences linked to it. A link to a superseded occurrence revision, an
    occurrence that holds the compared audio (or any audio from
    `compared_artifacts`), and a Work whose head is not a musical work are
    skipped with their reason. Nothing is chosen as current: every head link
    counts."""
    works: dict[str, dict[str, Any]] = {}
    skipped: list[dict[str, Any]] = []
    for link in reader.records:
        if link["shape"] != "claim" or not reader.cites(link, LINK_CONTRACT) \
                or reader.heads.get(link["object_id"]) is not link:
            continue
        [performance] = [ref for ref in link["refs"] if ref["role"] == "performance"]
        [work_ref] = [ref for ref in link["refs"] if ref["role"] == "work"]
        occurrence = pinned(reader, performance)
        work = reader.head(work_ref["id"])
        selections = {ref["id"] for ref in occurrence["refs"] if ref["role"] == "selection"}
        reason = None
        if reader.heads.get(occurrence["object_id"]) is not occurrence:
            reason = "the link pins a superseded occurrence revision"
        elif occurrence["object_id"] == compared_object or selections & compared_selections:
            reason = "the occurrence holds the compared audio"
        elif compared_artifacts and occurrence_artifacts(reader, occurrence) & compared_artifacts:
            reason = "the occurrence's audio comes from the compared session"
        elif work["shape"] != "entity" or work["payload"].get("kind") != "musical_work":
            reason = "the Work's head is not a musical_work Entity"
        if reason:
            skipped.append({"link": pin_of(link), "reason": reason})
            continue
        entry = works.setdefault(work["object_id"], {"work": work, "links": [], "occurrences": {}})
        entry["links"].append(link)
        entry["occurrences"][occurrence["object_id"]] = occurrence
    return works, skipped


def occurrence_artifacts(reader: adapter.ArchiveReader, occurrence: dict[str, Any]) -> set[str]:
    """The Artifacts every selection of an occurrence takes audio from."""
    found = set()
    for ref in occurrence["refs"]:
        if ref["role"] == "selection":
            found |= {piece["id"] for piece in pinned(reader, ref)["refs"] if piece["role"] == "piece"}
    return found


def session_artifacts(reader: adapter.ArchiveReader, artifact_id: str) -> frozenset[str]:
    """The Artifact and every part of every source of each session a head
    `session_parts` claim places it in: the same rehearsal, heard by another
    microphone or recorded before or after a rollover."""
    claims = [record for record in reader.records
              if record["shape"] == "claim" and reader.cites(record, SESSION_CONTRACT)
              and reader.heads.get(record["object_id"]) is record]

    def refs(record: dict[str, Any], role: str) -> set[str]:
        return {ref["id"] for ref in record["refs"] if ref["role"] == role}
    sessions = set().union(*(refs(c, "session") for c in claims if artifact_id in refs(c, "part")))
    together = {artifact_id}.union(*(refs(c, "part") for c in claims if refs(c, "session") & sessions))
    return frozenset(together)


def run_match(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    import match as match_tool  # numpy and scipy are needed by this command only

    try:
        limit = match_tool.check_decimal(args.limit, "the limit")
        settings = match_tool.parameters(args.limit)
    except match_tool.MatchError as exc:
        raise CommandError(str(exc)) from exc
    env = environment()
    reader = reader_of(args, env)
    # Refuse an archive that cannot hold the claims before reading any audio.
    reader.citation(adapter.RUN_CONTRACT)
    reader.citation(MATCH_CONTRACT)
    role = "selection" if args.selection else "occurrence"
    compared = reader.head(args.selection or args.occurrence)
    wanted = SELECTION_CONTRACT if role == "selection" else OCCURRENCE_CONTRACT
    if compared["shape"] != "claim" or not reader.cites(compared, wanted):
        raise CommandError(f"{compared['object_id']} does not cite {wanted[0]} version {wanted[1]}")
    if role == "selection":
        query_selection, compared_selections = compared, {compared["object_id"]}
    else:
        query_selection = compared_selection(reader, compared)
        compared_selections = {ref["id"] for ref in compared["refs"] if ref["role"] == "selection"}
    works, skipped = match_corpus(reader, compared["object_id"], compared_selections)
    if not works:
        raise CommandError("the corpus is empty: no head performance_work version 3 link pins a head song "
                           f"occurrence other than the compared audio ({len(skipped)} links skipped)")

    # Every revision the run reads, in a stable order.
    read: dict[str, dict[str, Any]] = {}
    for record in (compared, query_selection, *(a for a, _ in selection_pieces(reader, query_selection))):
        read[record["revision_id"]] = record
    audio_of: dict[str, dict[str, Any]] = {}
    for entry in works.values():
        read[entry["work"]["revision_id"]] = entry["work"]
        for link in entry["links"]:
            read[link["revision_id"]] = link
        for occurrence in entry["occurrences"].values():
            selection = compared_selection(reader, occurrence)
            audio_of[occurrence["revision_id"]] = selection
            for record in (occurrence, selection, *(a for a, _ in selection_pieces(reader, selection))):
                read[record["revision_id"]] = record
    used = tuple(adapter.Pin("used", r["object_id"], r["revision_id"])
                 for r in sorted(read.values(), key=lambda r: (r["object_id"], r["revision_id"])))
    software = probe_tool.software(args.ffmpeg, args.ffprobe) + match_tool.software()
    started = adapter.now()
    # Rerunning on unchanged inputs writes nothing (INV-5), and decodes nothing.
    candidate = adapter.RunResult(agent=match_tool.AGENT, method_id=match_tool.METHOD_ID,
                                  method_version=match_tool.METHOD_VERSION, software=software, started_at=started,
                                  ended_at=started, used=used, outputs=(), parameters=settings)
    previous = reader.equivalent_run(candidate)
    if previous is not None:
        return 0, {"status": "unchanged", role: pin_of(compared), "run": pin_of(previous)}

    features: dict[str, Any] = {}

    def profiles(selection: dict[str, Any]) -> Any:
        if selection["revision_id"] not in features:
            pieces = [match_tool.Piece(content_path(reader.root, artifact), piece["start_sample"],
                                       piece["end_sample"], piece["sample_rate"])
                      for artifact, piece in selection_pieces(reader, selection)]
            features[selection["revision_id"]] = match_tool.profiles(
                match_tool.load(pieces, args.ffmpeg, args.ffprobe))
        return features[selection["revision_id"]]

    report, outputs = [], []
    try:
        query = profiles(query_selection)
        for work_id in sorted(works):
            entry = works[work_id]
            scores = []
            for occurrence_id in sorted(entry["occurrences"]):
                occurrence = entry["occurrences"][occurrence_id]
                score = match_tool.compare(query, profiles(audio_of[occurrence["revision_id"]]))
                scores.append({"occurrence": pin_of(occurrence), **score.as_json()})
            scored = [s for s in scores if s["score"] is not None]
            best = min(scored, key=lambda s: (Decimal(s["score"]), s["occurrence"]["object_id"]), default=None)
            proposed = best is not None and Decimal(best["score"]) <= limit
            report.append({"work": pin_of(entry["work"]), "best": best, "proposed": proposed, "scores": scores})
            if not proposed:
                continue
            corpus = tuple(adapter.Pin("corpus", o["object_id"], o["revision_id"])
                           for _, o in sorted(entry["occurrences"].items()))
            outputs.append(adapter.Output(
                contract=MATCH_CONTRACT, name="work-" + work_id.removeprefix("urn:uuid:"),
                payload={"method": {"id": match_tool.METHOD_ID, "version": match_tool.METHOD_VERSION},
                         "score": best["score"], "scale": match_tool.SCALE, "direction": match_tool.DIRECTION},
                refs=(adapter.Pin(role, compared["object_id"], compared["revision_id"]),
                      adapter.Pin("work", entry["work"]["object_id"], entry["work"]["revision_id"]), *corpus),
                parameters_role="parameters"))
    except match_tool.MatchError as exc:
        raise CommandError(str(exc)) from exc
    ended = adapter.now()
    answer = {role: pin_of(compared), "limit": args.limit, "works": report, "skipped": skipped}
    if not outputs:
        return NOTHING_PROPOSED, {"status": "nothing_proposed", **answer}
    run = adapter.RunResult(agent=match_tool.AGENT, method_id=match_tool.METHOD_ID,
                            method_version=match_tool.METHOD_VERSION, software=software, started_at=started,
                            ended_at=ended, used=used, outputs=tuple(outputs), parameters=settings)
    admission = adapter.propose(reader, run, door=args.door, staging=staging_of(args, env), dry_run=args.dry_run,
                                env=env)
    return 0, {**answer, **admission.as_json()}


def run_match_windows(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    import cover  # Essentia and numpy are needed by this command only
    import profile_cache
    import windows

    env = environment()
    reader = reader_of(args, env)
    # Refuse an archive that cannot hold the claims before reading any audio.
    reader.citation(adapter.RUN_CONTRACT)
    reader.citation(WINDOW_MATCH_CONTRACT)
    suggestions = reader.head(args.suggestions)
    if suggestions["shape"] != "claim" or not reader.cites(suggestions, SPANS_CONTRACT):
        raise CommandError(f"{suggestions['object_id']} does not cite {SPANS_CONTRACT[0]} version {SPANS_CONTRACT[1]}")
    by_role = {ref["role"]: ref for ref in suggestions["refs"]}
    source, stream = pinned(reader, by_role["source"]), pinned(reader, by_role["stream"])
    rate = suggestions["payload"]["sample_rate"]
    spans = [cover.Span(span["start_sample"], span["end_sample"], span["class"])
             for span in suggestions["payload"]["spans"]]
    works, skipped = match_corpus(reader, None, set(), session_artifacts(reader, source["object_id"]))
    answer: dict[str, Any] = {"suggestions": pin_of(suggestions), "skipped": skipped}
    if not works:
        return NOTHING_PROPOSED, {"status": "nothing_proposed", **answer,
                                  "detail": "the corpus is empty: no head performance_work version 3 link pins a head "
                                            f"song occurrence from another session ({len(skipped)} links skipped)"}

    # Every revision the run reads, in a stable order.
    read = {record["revision_id"]: record for record in (suggestions, source, stream)}
    works_of: dict[str, set[str]] = {}
    corpus_pieces: dict[str, tuple[cover.Piece, ...]] = {}
    # Each occurrence's Artifacts, whose content digests key the profile cache
    # and whose bytes are checked only when they are about to be decoded.
    corpus_artifacts: dict[str, tuple[dict[str, Any], ...]] = {}
    for work_id, entry in works.items():
        read[entry["work"]["revision_id"]] = entry["work"]
        for link in entry["links"]:
            read[link["revision_id"]] = link
        for occurrence_id, occurrence in entry["occurrences"].items():
            works_of.setdefault(occurrence_id, set()).add(work_id)
            selection = compared_selection(reader, occurrence)
            pieces = selection_pieces(reader, selection)
            for record in (occurrence, selection, *(artifact for artifact, _ in pieces)):
                read[record["revision_id"]] = record
            corpus_artifacts[occurrence_id] = tuple(artifact for artifact, _ in pieces)
            corpus_pieces[occurrence_id] = tuple(
                cover.Piece(content_location(reader.root, artifact), piece["start_sample"], piece["end_sample"],
                            piece["sample_rate"]) for artifact, piece in pieces)
    used = tuple(adapter.Pin("used", r["object_id"], r["revision_id"])
                 for r in sorted(read.values(), key=lambda r: (r["object_id"], r["revision_id"])))
    try:
        software = probe_tool.software(args.ffmpeg, args.ffprobe) + cover.software()
        settings = cover.parameters()
    except cover.CoverError as exc:
        raise CommandError(str(exc)) from exc
    started = adapter.now()
    # Rerunning on unchanged inputs writes nothing (INV-5), and decodes nothing.
    candidate = adapter.RunResult(agent=cover.AGENT, method_id=cover.METHOD_ID, method_version=cover.METHOD_VERSION,
                                  software=software, started_at=started, ended_at=started, used=used, outputs=(),
                                  parameters=settings)
    previous = reader.equivalent_run(candidate)
    if previous is not None and not args.regenerate:
        return 0, {"status": "unchanged", **answer, "run": pin_of(previous)}

    source_path = content_path(reader.root, source)
    if stream["payload"].get("sample_rate") != rate:
        raise CommandError(f"the suggestions are in samples at {rate} Hz, but their stream claim states "
                           f"{stream['payload'].get('sample_rate')} Hz")
    sample_count = stream["payload"]["sample_count"]
    outputs = []
    checked: set[str] = set()

    def check_content(occurrences: list[str]) -> None:
        """Hash each file about to be decoded, once, however many
        occurrences take audio from it."""
        for occurrence_id in occurrences:
            for artifact in corpus_artifacts[occurrence_id]:
                if artifact["payload"]["content"]["digest"] not in checked:
                    content_path(reader.root, artifact)
                    checked.add(artifact["payload"]["content"]["digest"])
    try:
        cache = None if args.profile_cache is None else \
            profile_cache.ProfileCache(Path(args.profile_cache), software, args.ffmpeg)
        with cover.Comparer(args.jobs, args.ffmpeg, args.ffprobe) as comparer:
            corpus_digests = {key: tuple(a["payload"]["content"]["digest"] for a in artifacts)
                              for key, artifacts in corpus_artifacts.items()}
            measured_corpus = profile_cache.measure_corpus(comparer, corpus_pieces, corpus_digests, cache,
                                                           check_content)
            for occurrence_id, measured in measured_corpus.items():
                if isinstance(measured, cover.TooShort):
                    answer["skipped"].append({"occurrence": occurrence_id, "reason": str(measured)})
                elif isinstance(measured, cover.CoverError):
                    raise CommandError(f"occurrence {occurrence_id} could not be measured: {measured}")
            if not comparer.corpus:
                return NOTHING_PROPOSED, {"status": "nothing_proposed", **answer,
                                          "detail": "no corpus occurrence is long enough to compare"}
            recording = windows.Recording(cover.measure_whole(source_path, rate, sample_count, args.ffmpeg,
                                                              args.ffprobe, analyser=comparer.analyser), rate)

            def window_features(start: int, end: int) -> cover.Features:
                return comparer.features([cover.Piece(source_path, start, end, rate)])

            found = windows.search_recording(recording, spans, comparer.corpus,
                                             {key: sorted(value) for key, value in works_of.items()},
                                             window_features, comparer.distances, args.jobs)
    except cover.CoverError as exc:
        raise CommandError(str(exc)) from exc
    for occurrence_id in found["too_short"]:
        answer["skipped"].append({"occurrence": occurrence_id,
                                  "reason": "too short for the quarter-rate distance"})
    report = []
    works_by_occurrence = {key: sorted(value) for key, value in works_of.items()}
    quarter_limit = Decimal(cover.SEARCH["suggestion_distance"])

    def within(distances: list[cover.Distance], limit: Decimal) -> list[dict[str, str]]:
        return [{"work": work, "occurrence": d.corpus_id, "distance": str(d.value)}
                for work, d in windows.works_within(distances, works_by_occurrence, limit)]

    for number, decision in enumerate(found["decisions"]):
        window = {"start_sample": decision.window[0], "end_sample": decision.window[1]}
        scale = cover.EXACT_SCALE if decision.path == "exact" else cover.QUARTER_RATE_SCALE
        limit = Decimal(cover.SEARCH["exact_suggestion_distance"]) if decision.path == "exact" else quarter_limit
        # Every Work within the limit is kept here, in the run's answer; only
        # the closest is claimed (ADR 0072, amendment of 2026-10-18).
        entry: dict[str, Any] = {
            "window": window, "path": decision.path, "scale": scale,
            "closest": [{"occurrence": d.corpus_id, "distance": str(d.value)} for d in decision.distances[:5]],
            "within": within(decision.distances, limit), "work_margin": decision.work_margin,
            "unaligned": len(decision.unaligned), "proposed": None}
        if decision.path == "exact":
            # The near tie's comparisons: the quarter-rate view that sent it
            # to the exact distance, and what the exact distance compared.
            ranked = windows.per_work(decision.quarter_rate, works_by_occurrence)
            entry["quarter_rate_closest"] = [{"occurrence": d.corpus_id, "distance": str(d.value)}
                                             for d in decision.quarter_rate[:5]]
            entry["quarter_rate_within"] = within(decision.quarter_rate, quarter_limit)
            entry["quarter_rate_margin"] = ranked[1][0] - ranked[0][0] if len(ranked) > 1 else None
            entry["compared_exactly"] = decision.compared_exactly
        report.append(entry)
        if not decision.suggested:
            continue
        work = works[decision.work]["work"]
        occurrences = works[decision.work]["occurrences"]
        score = str(next(d.value for d in decision.distances if d.corpus_id in decision.corpus))
        entry["proposed"] = {"work": pin_of(work), "score": score}
        outputs.append(adapter.Output(
            contract=WINDOW_MATCH_CONTRACT, name=f"window-{number}-work-" + decision.work.removeprefix("urn:uuid:"),
            payload={"method": {"id": cover.METHOD_ID, "version": cover.METHOD_VERSION}, "score": score,
                     "scale": scale, "direction": cover.DIRECTION, "sample_rate": rate, "window": window},
            refs=(adapter.Pin("suggestions", suggestions["object_id"], suggestions["revision_id"]),
                  adapter.Pin("work", work["object_id"], work["revision_id"]),
                  *(adapter.Pin("corpus", occurrences[o]["object_id"], occurrences[o]["revision_id"])
                    for o in decision.corpus)),
            parameters_role="parameters"))
    ended = adapter.now()
    answer.update({"suggestion_distances": {"quarter_rate": cover.SEARCH["suggestion_distance"],
                                            "exact": cover.SEARCH["exact_suggestion_distance"]},
                   "hits": found["hits"], "regions": len(found["regions"]), "windows": report})
    if not outputs:
        return NOTHING_PROPOSED, {"status": "nothing_proposed", **answer}
    if args.regenerate:
        # The answer again, for a run that may already be recorded: proposed
        # to nobody, and the same as that run's when nothing it pins changed.
        return 0, {"status": "regenerated", **answer,
                   "run": None if previous is None else pin_of(previous)}
    run_result = adapter.RunResult(agent=cover.AGENT, method_id=cover.METHOD_ID, method_version=cover.METHOD_VERSION,
                                   software=software, started_at=started, ended_at=ended, used=used,
                                   outputs=tuple(outputs), parameters=settings)
    admission = adapter.propose(reader, run_result, door=args.door, staging=staging_of(args, env),
                                dry_run=args.dry_run, env=env)
    return 0, {**answer, **admission.as_json()}


def run_propose(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    import propose  # bank features as claims (ADR 0079); see propose.py
    if args.all and args.stream:
        raise CommandError("--stream names one Artifact's stream; it cannot go with --all")
    env = environment()
    return propose.run(args, env, reader_of(args, env), staging_of(args, env), stream_claims, choose_stream)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="enrich", description="Propose enrichment results to Door")
    commands = parser.add_subparsers(dest="command", required=True)

    def common(command: argparse.ArgumentParser) -> None:
        command.add_argument("--archive", required=True, help="the archive root")
        command.add_argument("--staging", help="a git-annex repository to stage new parameter content in")
        command.add_argument("--dry-run", action="store_true", help="ask Door to predict; stage and write nothing")
        command.add_argument("--door", default="door")
        command.add_argument("--archive-command", default="archive")
        command.add_argument("--ffmpeg", default="ffmpeg")
        command.add_argument("--ffprobe", default="ffprobe")
        command.add_argument("--export", help="an `archive export` of the archive's current commit, read "
                                              "instead of exporting again")

    probe = commands.add_parser("probe", help="count an Artifact's audio streams and propose audio_stream claims")
    probe.add_argument("--artifact", required=True, help="the Artifact's object identifier")
    common(probe)
    detect = commands.add_parser("detect", help="suggest spans of an Artifact's audio stream and propose them")
    detect.add_argument("--artifact", required=True, help="the Artifact's object identifier")
    detect.add_argument("--stream", help="the audio_stream claim to measure against, when there are several")
    detect.add_argument("--preset", default="jam", help="the detector preset: jam, show, or studio")
    common(detect)
    match = commands.add_parser("match", help="compare a selection or occurrence with the occurrences linked to "
                                              "each Work and propose work_match claims")
    compared = match.add_mutually_exclusive_group(required=True)
    compared.add_argument("--selection", help="an audio_selection version 2 object identifier")
    compared.add_argument("--occurrence", help="a song_occurrence version 2 object identifier")
    match.add_argument("--limit", required=True,
                       help="decimal text: a Work's best score is proposed only when at or below it")
    common(match)
    windows = commands.add_parser("match-windows", help="search a whole recording for the Works of the occurrences "
                                                        "linked to them, in windows of its suggested spans, and "
                                                        "propose work_match version 3 claims")
    windows.add_argument("--suggestions", required=True, help="a span_suggestions version 1 object identifier")
    windows.add_argument("--jobs", type=int, default=1, help="processes measuring and searching (default 1)")
    windows.add_argument("--profile-cache",
                         help="a directory that keeps the corpus occurrences' profiles between runs; regenerable, "
                              "outside any archive (profile_cache.py)")
    windows.add_argument("--regenerate", action="store_true",
                         help="search even when an equivalent run is recorded, and print the answer with every "
                              "Work within the limit; propose nothing")
    common(windows)
    proposing = commands.add_parser("propose", help="read an Artifact's bank features with the named proposers and "
                                                    "propose their claims, each proposer's with its own run")
    import propose
    propose.add_arguments(proposing)
    common(proposing)
    backfilling = commands.add_parser("backfill", help="fill the feature bank for every recording and propose "
                                                       "what it holds (ADR 0112); a dry run unless --commit")
    import backfill
    backfill.add_arguments(backfilling)
    common(backfilling)
    args = parser.parse_args(argv)
    if args.command == "backfill":
        # Streams JSON lines, one per event, rather than printing one answer.
        try:
            return backfill.main(args, environment())
        except (CommandError, backfill.BackfillError, backfill.bank.BankError, backfill.remote.RemoteError) as exc:
            print(json.dumps({"status": "failed", "detail": str(exc)}), file=sys.stderr)
            return 1
    if args.command == "match-windows" and args.jobs < 1:
        parser.error("--jobs must be at least 1")
    errors: tuple[type[Exception], ...] = (CommandError, adapter.AdapterError, probe_tool.ProbeError)
    try:
        if args.command == "probe":
            code, answer = run_probe(args)
        elif args.command == "detect":
            import detector  # numpy is needed only here
            errors += (detector.DetectorError,)
            code, answer = run_detect(args)
        elif args.command == "match":
            code, answer = run_match(args)
        elif args.command == "propose":
            errors += (propose.ProposeError,)
            code, answer = run_propose(args)
        else:
            code, answer = run_match_windows(args)
    except errors as exc:
        # What a command made before it failed is kept, and said (propose).
        print(json.dumps({**getattr(exc, "answer", {}), "status": "failed", "detail": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(answer, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
