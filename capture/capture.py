#!/usr/bin/env python3
"""Import a directory of recorder files into an archive (ADR 0070).

    capture import --archive <dir> --staging <annex> --derived-media <root> <directory>
                   [--session-name <name>] [--expect <manifest>] [--jobs <n>]
                   [--profile-cache <dir>] [--detector-preset jam|show|studio]
                   [--defer-listening-copies] [--bank <root>] [--extract-config <file>] [--dry-run]

The importer hashes every file, groups the files into sessions and sources
with a recorder grammar, keeps one file of each take where the grammar's
sources are twins, and proposes each session to `door admit` in one batch:
an Artifact per new file, the `recording_session` Entity, and one
`archive.claim/session_parts` claim per source. A file whose content is
already an Artifact of no session is cited rather than admitted again; a
session already in the archive is left as it is. Then it runs the enrichment
chain on the new Artifacts, quickest first: `enrich probe`, `enrich detect`
on each session's primary source, waveform peaks, the listening copies, and
`enrich match-windows` on what was detected, and, when a feature bank is
configured, the bank entries and `enrich propose` (see `chain`). It reads the
archive only through `archive export`, proposes only through Door, and never
changes its input.

It prints one JSON object. Exit status: 0 when every new session was
committed and enriched, predicted by a dry run, or there was nothing new; 1
on any failure, with the failure and everything already done on stderr; and
2, as argparse decides, when the arguments are not understood.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

HERE = Path(__file__).resolve().parent
ENRICH = HERE.parent / "enrich"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ENRICH))

import adapter  # noqa: E402
import grammar as grammars_module  # noqa: E402
import propose_step  # noqa: E402

BY = AGENT = "pkg:generic/archive-capture@0.2.0"
ARTIFACT_NAMESPACE = adapter.ARTIFACT_NAMESPACE
SESSION_NAMESPACE = "archive.audio/session"
PARTS_CONTRACT = ("archive.claim/session_parts", "1")
START_CONTRACT = ("archive.claim/capture_start", "1")
SPANS_CONTRACT = ("archive.claim/span_suggestions", "1")
WINDOW_MATCH_CONTRACT = ("archive.claim/work_match", "3")
# The span detector's presets (enrich/detector.py), named here so a wrong one
# is refused before anything is staged.
DETECTOR_PRESETS = ("jam", "show", "studio")
STAGING_REMOTE = "capture-staging"
# The one source of a session the operator named: files no grammar knows.
NAMED_SOURCE = "files"
_EXTENSION = re.compile(r"^\.[A-Za-z0-9]{1,4}$")


class CaptureError(Exception):
    """The importer refused, or a step it ran failed."""


@dataclass
class InputFile:
    relative: str
    path: Path
    size: int
    digest: str
    recognised: grammars_module.Recognised | None = None

    @property
    def hex(self) -> str:
        return self.digest.removeprefix("sha256:")

    def staged_name(self) -> str:
        """The name the bytes get in the staging repository: the digest and,
        when git-annex keeps it in the key, the file's extension."""
        suffix = self.path.suffix
        return self.hex + (suffix if _EXTENSION.match(suffix) else "")


@dataclass
class Source:
    token: str
    files: list[InputFile]


@dataclass
class Session:
    label: str
    grammar: str | None
    sources: list[Source]
    status: str = "new"
    planned: dict[str, Any] = field(default_factory=dict)
    # Whether the grammar's sources are twins: one file of each take is kept.
    twins: bool = False
    # Files left out of the session, each with the reason (twins).
    passed_over: list[dict[str, str]] = field(default_factory=list)
    # The current Artifact record of each file already in the archive, by
    # digest: those files are cited, not admitted again.
    adopted: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def files(self) -> list[InputFile]:
        return [part for source in self.sources for part in source.files]

    @property
    def primary(self) -> Source:
        return self.sources[0]

    @property
    def name(self) -> str:
        """ADR 0048: `session-` and the SHA-256 of the first part's content,
        the first part being the primary source's (ADR 0070)."""
        return "session-" + self.primary.files[0].hex


# -- reading the input -------------------------------------------------------

def content_digest(path: Path) -> tuple[str, int]:
    hasher, size = hashlib.sha256(), 0
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            hasher.update(block)
            size += len(block)
    return "sha256:" + hasher.hexdigest(), size


def read_directory(directory: Path) -> tuple[list[InputFile], list[str]]:
    """Every regular file under the directory, hashed, in path order, and
    the hidden names that were passed over (a card's `.Trashes`, a copy's
    `._` files). A symbolic link or anything else that is not a regular file
    or directory is refused: the importer reads what is on the card, not
    what a name points to. A directory that cannot be listed refuses the run
    too, so no file under it is silently left out."""
    files: list[InputFile] = []
    ignored: list[str] = []

    def unreadable(exc: OSError) -> None:
        raise CaptureError(f"{exc.filename} could not be read: {exc.strerror or exc}") from exc

    for current, directories, names in os.walk(directory, onerror=unreadable):
        base = Path(current)
        for name in sorted(directories):
            relative = (base / name).relative_to(directory).as_posix()
            if (base / name).is_symlink():
                raise CaptureError(f"{relative} is a symbolic link; the importer reads only regular files")
            if name.startswith("."):
                ignored.append(relative + "/")
        directories[:] = sorted(d for d in directories if not d.startswith("."))
        for name in sorted(names):
            path = base / name
            relative = path.relative_to(directory).as_posix()
            if name.startswith("."):
                ignored.append(relative)
                continue
            if path.is_symlink() or not path.is_file():
                raise CaptureError(f"{relative} is not a regular file")
            try:
                digest, size = content_digest(path)
            except OSError as exc:
                raise CaptureError(f"{relative} could not be read: {exc.strerror or exc}") from exc
            files.append(InputFile(relative, path, size, digest))
    files.sort(key=lambda item: item.relative.encode("utf-8", "surrogateescape"))
    return files, sorted(ignored)


def check_expected(files: list[InputFile], manifest: Path) -> None:
    """The directory must hold exactly the files a manifest lists, with the
    contents it states: a copy made elsewhere (`capture send` hashes the
    card before copying it) is then known to be the card's bytes, and a
    transfer that dropped, added, or changed a file is refused before
    anything is staged."""
    try:
        listed = json.loads(manifest.read_text(encoding="utf-8"))
        expected = {entry["path"]: (entry["digest"], entry["size"]) for entry in listed["files"]}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CaptureError(f"the manifest {manifest} could not be read: {exc}") from exc
    found = {item.relative: (item.digest, item.size) for item in files}
    missing = sorted(set(expected) - set(found))
    extra = sorted(set(found) - set(expected))
    changed = sorted(path for path in set(expected) & set(found) if expected[path] != found[path])
    if missing or extra or changed:
        raise CaptureError("the directory is not what the manifest lists: "
                           + "; ".join(f"{what} {', '.join(paths)}" for what, paths in
                                       (("missing", missing), ("not listed", extra), ("changed", changed)) if paths))


# -- grouping ----------------------------------------------------------------

def group(files: list[InputFile], grammars: Sequence[Any], session_name: str | None) -> list[Session]:
    """Sessions in the grammar's terms, then the operator's named session.

    A file no grammar recognises is refused unless the operator names its
    session; the named session holds every such file as one source, in path
    order. Every refusal is decided before anything is written."""
    unrecognised: list[InputFile] = []
    takes: dict[tuple[str, str], dict[str, InputFile]] = {}
    for item in files:
        found = None
        for candidate in grammars:
            found = candidate.recognise(Path(item.relative).name)
            if found is not None:
                break
        if found is None:
            unrecognised.append(item)
            continue
        item.recognised = found
        take = takes.setdefault((found.grammar, found.take), {})
        if found.source in take:
            raise CaptureError(f"{take[found.source].relative} and {item.relative} are the same take and source")
        take[found.source] = item

    sessions: list[Session] = []
    for g in grammars:
        ordered = sorted((key for key in takes if key[0] == g.name),
                         key=lambda key: (next(iter(takes[key].values())).recognised.started, key[1]))
        chains: list[list[dict[str, InputFile]]] = []
        for key in ordered:
            take = takes[key]
            if chains and _continues(g, chains[-1][-1], take):
                chains[-1].append(take)
            else:
                chains.append([take])
        for chain in chains:
            tokens = [token for token in g.sources if token in chain[0]]
            sources = [Source(token, [take[token] for take in chain]) for token in tokens]
            started = chain[0][tokens[0]].recognised.started
            sessions.append(Session(f"{g.label} {started:%Y-%m-%d %H:%M:%S}", g.name, sources,
                                    twins=bool(getattr(g, "twins", False))))

    if unrecognised:
        if session_name is None:
            listed = ", ".join(item.relative for item in unrecognised)
            raise CaptureError(f"no recorder grammar recognises {listed}; name their session with --session-name")
        sessions.append(Session(session_name, None, [Source(NAMED_SOURCE, unrecognised)]))
    return sessions


def _continues(g: Any, previous: dict[str, InputFile], following: dict[str, InputFile]) -> bool:
    """Whether a take continues the previous take of its chain. Every
    source must agree: a rollover is the recorder's, and it splits every
    track at once, so a disagreement is refused rather than resolved."""
    shared = [token for token in g.sources if token in previous and token in following]
    try:
        verdicts = {token: g.continues(previous[token].path, previous[token].recognised.started,
                                       following[token].recognised.started) for token in shared}
    except grammars_module.GrammarError as exc:
        raise CaptureError(f"cannot decide whether {following[shared[0]].relative} continues a take: {exc}") from exc
    if not any(verdicts.values()):
        return False
    if set(previous) != set(following) or not all(verdicts.values()):
        names = ", ".join(sorted(f.relative for f in [*previous.values(), *following.values()]))
        raise CaptureError(f"the sources of {names} disagree about a rollover")
    return True


# -- the proposal ------------------------------------------------------------

def known_contents(reader: adapter.ArchiveReader) -> dict[str, dict[str, Any]]:
    """The current revision of an Artifact for each content digest the
    archive holds, first in export order."""
    known: dict[str, dict[str, Any]] = {}
    for record in reader.records:
        if record["shape"] == "artifact" and reader.heads.get(record["object_id"]) is record:
            known.setdefault(record["payload"]["content"]["digest"], record)
    return known


def session_parts(reader: adapter.ArchiveReader) -> set[str]:
    """Every Artifact object that is a part in a current session parts claim."""
    parts: set[str] = set()
    for record in reader.records:
        if (record["shape"] == "claim" and reader.heads.get(record["object_id"]) is record
                and reader.cites(record, PARTS_CONTRACT)):
            parts.update(ref["id"] for ref in record.get("refs", []) if ref.get("role") == "part")
    return parts


def keep_one_of_each_take(session: Session, known: dict[str, dict[str, Any]]) -> None:
    """For a grammar whose sources are twins, one file of each take, the same
    one every time (ADR 0070, amendment on twins): a file already in the
    archive is the take, the mix when both are; otherwise the mix, the
    grammar's first source. A twin already in the archive beside the kept
    file stays as a later source, so the archive says what it is; every other
    twin is passed over and reported."""
    if not session.twins or len(session.sources) < 2:
        return
    if len({len(source.files) for source in session.sources}) != 1:
        raise CaptureError(f"the sources of session {session.label!r} do not have a file for every take")
    first = session.sources[0]
    kept: list[InputFile] = []
    later: dict[str, list[InputFile]] = {source.token: [] for source in session.sources[1:]}
    for take in zip(*(source.files for source in session.sources)):
        present = [item for item in take if item.digest in known]
        keep = take[0] if take[0].digest in known or not present else present[0]
        kept.append(keep)
        for source, item in zip(session.sources, take):
            if item is keep:
                continue
            if item.digest in known:
                later[source.token].append(item)
            else:
                reason = f"twin of {keep.relative}" + (", already in the archive" if keep.digest in known else "")
                session.passed_over.append({"file": item.relative, "reason": reason})
    session.sources = [Source(first.token, kept)] + [Source(token, files) for token, files in later.items() if files]


def classify(sessions: list[Session], reader: adapter.ArchiveReader) -> None:
    """Mark each session already imported or new, and refuse what cannot be
    proposed: a session only partly in an existing session (its parts would
    need a revision this importer does not make), the same content in two
    files, and a session object that already exists. A file whose content is
    already an Artifact of no session is adopted: the session cites it
    (ADR 0070, amendment on migrated parts)."""
    known = known_contents(reader)
    belonging = session_parts(reader)
    new_digests: Counter[str] = Counter()
    for session in sessions:
        keep_one_of_each_take(session, known)
        present = [item for item in session.files if item.digest in known]
        if any(known[item.digest]["object_id"] in belonging for item in present):
            if len(present) == len(session.files):
                session.status = "already_imported"
                continue
            listed = ", ".join(item.relative for item in present)
            raise CaptureError(f"session {session.label!r} is partly imported already ({listed}); "
                               "its parts would need a new revision, which this importer does not propose")
        session.adopted = {item.digest: known[item.digest] for item in present}
        new_digests.update(item.digest for item in session.files if item.digest not in known)
        session_id = adapter.object_id(reader.archive_id, SESSION_NAMESPACE, session.name)
        if session_id in reader.heads:
            raise CaptureError(f"session {session.name} already exists, but none of its files is one of its parts")
    repeated = [digest for digest, count in new_digests.items() if count > 1]
    if repeated:
        names = ", ".join(item.relative for session in sessions for item in session.files if item.digest in repeated)
        raise CaptureError(f"the same content is in more than one file: {names}")


def plan(session: Session, reader: adapter.ArchiveReader) -> tuple[list[dict], list[dict], list[dict]]:
    """The batch's drafts and identity rows, and its artifacts in order."""
    drafts, identities, artifacts = [], [], []

    def add(namespace: str, name: str, shape: str, payload: dict, refs: list, contracts: list | None = None) -> dict:
        object_id = adapter.object_id(reader.archive_id, namespace, name)
        draft = {"spec": reader.spec, "object_id": object_id, "shape": shape, "by": BY, "agent": AGENT,
                 "payload": payload, "refs": refs, "supersedes": []}
        if contracts:
            draft["contracts"] = contracts
        drafts.append(draft)
        identities.append({"object_id": object_id, "namespace": namespace, "name": name})
        return {"object_id": object_id, "revision_id": adapter.revision_of(draft)}

    pins: dict[str, dict[str, str]] = {}
    adopted = []
    for item in session.files:
        if item.digest in session.adopted:
            record = session.adopted[item.digest]
            pins[item.digest] = {"object_id": record["object_id"], "revision_id": record["revision_id"]}
            adopted.append({"file": item.relative, "digest": item.digest, **pins[item.digest]})
        else:
            pin = add(ARTIFACT_NAMESPACE, "capture-" + item.hex, "artifact",
                      {"content": {"digest": item.digest, "size": item.size}}, [])
            pins[item.digest] = pin
            artifacts.append({"file": item.relative, "digest": item.digest, **pin})
        pin = pins[item.digest]
        if item.recognised is not None:
            instant = item.recognised.started
            start = {"basis": "grammar", "started": instant.isoformat(),
                     "clock": "zoned" if instant.utcoffset() is not None else "local"}
            add(START_CONTRACT[0], "capture-start-" + item.hex, "claim", start,
                [{"role": "subject", "id": pin["object_id"], "revision_id": pin["revision_id"]}],
                [reader.citation(START_CONTRACT)])
    entity = add(SESSION_NAMESPACE, session.name, "entity", {"kind": "recording_session", "label": session.label}, [])
    citation = reader.citation(PARTS_CONTRACT)
    parts = []
    for source in session.sources:
        refs = [{"role": "session", "id": entity["object_id"], "revision_id": entity["revision_id"]}]
        refs += [{"role": "part", "id": pins[item.digest]["object_id"], "revision_id": pins[item.digest]["revision_id"]}
                 for item in source.files]
        parts.append({"source": source.token,
                      **add(PARTS_CONTRACT[0], f"{session.name}-{source.token}", "claim", {}, refs, [citation])})
    session.planned = {"session": entity, "parts": parts, "artifacts": artifacts, "adopted": adopted}
    return drafts, identities, artifacts


def admit(session: Session, reader: adapter.ArchiveReader, staging: adapter.Staging, door: str, dry_run: bool,
          env: dict[str, str]) -> list[str]:
    drafts, identities, _ = plan(session, reader)
    contents = []
    remote = None
    for item in session.files:
        if item.digest in session.adopted:
            continue  # already in the archive: cited, not staged
        name = item.staged_name()
        key = (staging.key_for(item.digest, item.size, name) if dry_run
               else staging.stage_file(item.path, item.digest, item.size, name))
        if remote is None:
            remote = staging.remote_name(reader.root, create=not dry_run)
        contents.append({"digest": item.digest, "size": item.size, "annex_key": key, "staging_remote": remote})
    planned = [adapter.revision_of(draft) for draft in drafts]
    manifest = {"protocol": "archive-admission/1",
                "records": [f"records/{index:03d}.json" for index in range(len(drafts))],
                "identities": identities, "contents": contents,
                "expected_heads": [{"object_id": draft["object_id"], "revision_id": None} for draft in drafts]}
    proposal = Path(tempfile.mkdtemp(prefix="capture-proposal-"))
    try:
        (proposal / "records").mkdir()
        (proposal / "manifest.json").write_bytes(adapter.canonical(manifest))
        for path, draft in zip(manifest["records"], drafts):
            (proposal / path).write_bytes(adapter.canonical(draft))
        command = [door, "admit", "--proposal", str(proposal)] + (["--dry-run"] if dry_run else [])
        output = run(command, reader.root, f"door admit for session {session.name}", env)
    finally:
        shutil.rmtree(proposal, ignore_errors=True)
    try:
        answer = json.loads(output)
    except ValueError:
        answer = None
    expected = "predicted" if dry_run else "committed"
    if not isinstance(answer, dict) or answer.get("status") != expected or answer.get("revision_ids") != planned:
        raise CaptureError(f"Door answered {answer!r} for session {session.name}, but the importer planned {planned}")
    session.status = expected
    return planned


# -- the enrichment chain ----------------------------------------------------

def run(arguments: list[str], cwd: Path, what: str, env: dict[str, str]) -> str:
    try:
        completed = subprocess.run(arguments, cwd=cwd, env=env, capture_output=True, text=True)
    except OSError as exc:
        raise CaptureError(f"{what} could not run: {exc}") from exc
    if completed.returncode != 0:
        raise CaptureError(f"{what} failed ({completed.returncode}): "
                           f"{completed.stderr.strip() or completed.stdout.strip()}")
    return completed.stdout


def enrich(command: str, artifact: str, args: argparse.Namespace, env: dict[str, str],
           export: Path | None = None, bank: Path | None = None, proposers: str | None = None) -> tuple[int, dict]:
    """Run one `enrich` command on an Artifact (on a span suggestions claim
    for `match-windows`); 0 and 3 are answers, anything else fails. With
    ``export``, the command reads that export of the archive's current
    commit instead of exporting the archive again; ``bank`` is the feature
    bank `propose` reads."""
    target = "--suggestions" if command == "match-windows" else "--artifact"
    arguments = [sys.executable, str(ENRICH / "enrich.py"), command, "--archive", str(args.archive),
                 target, artifact, "--staging", str(args.staging), "--door", args.door,
                 "--archive-command", args.archive_command, "--ffmpeg", args.ffmpeg]
    if command in ("probe", "match-windows"):
        arguments += ["--ffprobe", args.ffprobe]
    if command == "detect":
        arguments += ["--preset", args.detector_preset]
    if command == "match-windows":
        arguments += ["--jobs", str(args.jobs)]
        if args.profile_cache is not None:
            arguments += ["--profile-cache", str(args.profile_cache)]
    if command == "propose":
        arguments += ["--bank", str(bank)]
        if proposers is not None:
            arguments += ["--proposers", proposers]
        if getattr(args, "extract_config", None):
            arguments += ["--config", str(args.extract_config)]
    if export is not None:
        arguments += ["--export", str(export)]
    try:
        completed = subprocess.run(arguments, env=env, capture_output=True, text=True)
    except OSError as exc:
        raise CaptureError(f"enrich {command} could not run: {exc}") from exc
    try:
        answer = json.loads(completed.stdout) if completed.returncode in (0, 3) else None
    except ValueError:
        answer = None
    if not isinstance(answer, dict) or not isinstance(answer.get("status"), str):
        raise CaptureError(f"enrich {command} of {artifact} failed ({completed.returncode}): "
                           f"{completed.stderr.strip() or completed.stdout.strip()}")
    return completed.returncode, answer


def content_path(root: Path, digest: str) -> Path:
    """Where layout archive/2 puts an Artifact's bytes (D-1)."""
    hexdigest = digest.removeprefix("sha256:")
    return root / "content" / "sha256" / hexdigest[0:2] / hexdigest[2:4] / hexdigest


def chain(sessions: list[Session], args: argparse.Namespace, env: dict[str, str], report: list[dict]) -> None:
    """Enrich every new Artifact of the committed sessions, quickest first,
    so a session can be reviewed before its slowest output exists:

    1. `enrich probe` on every Artifact, then `enrich detect` on each part of
       each session's primary source. Each run is its own Door batch, one at
       a time, against one export of the archive that is brought up to date
       with `archive export --since` after each commit.
    2. Waveform peaks for every Artifact with audio, `--jobs` at a time.
    3. Listening copies, `--jobs` at a time, unless deferred to a later
       `enrich/media.py` run.
    4. `enrich match-windows` on each detection, one at a time, each using
       `--jobs` processes: the slowest of these (ADR 0072).
    5. With a feature bank, the chord, rhythm, and tonal entries of each
       Artifact of each session's primary source, and `enrich propose` on it
       with the default proposers (ADR 0079, `propose_step.py`); without
       one, this step is reported as skipped.

    A file with no countable audio stream is probed and reported, and
    nothing else runs on it. A failure stops the chain once the step it
    happened in has finished: what that step already made is kept and
    reported, and no later step runs."""
    entries: list[tuple[Session, dict[str, Any], dict[str, Any]]] = []
    for session in sessions:
        for artifact in session.planned["artifacts"]:
            entry: dict[str, Any] = {"file": artifact["file"], "object_id": artifact["object_id"]}
            report.append(entry)
            entries.append((session, artifact, entry))
    if not entries:
        return
    reader = adapter.ArchiveReader(args.archive, args.archive_command, env)
    with tempfile.TemporaryDirectory(prefix="capture-export-") as scratch:
        export_file = Path(scratch) / "export.json"

        def current_export() -> Path:
            export_file.write_text(json.dumps(reader.export), encoding="utf-8")
            return export_file

        audio = []
        for session, artifact, entry in entries:
            code, answer = enrich("probe", artifact["object_id"], args, env, current_export())
            entry["probe"] = answer["status"]
            if code == 3:
                entry["detect"] = entry["media"] = "skipped: no countable audio stream"
                continue
            reader.refresh(args.archive_command, env)
            audio.append((session, artifact, entry))
        detected, primary = [], []
        for session, artifact, entry in audio:
            if artifact["digest"] in {item.digest for item in session.primary.files}:
                primary.append((artifact, entry))
                _, answer = enrich("detect", artifact["object_id"], args, env, current_export())
                entry["detect"] = answer["status"]
                reader.refresh(args.archive_command, env)
                detected.append((entry, suggestions_made_by(reader, answer)))

        for _, _, entry in audio:
            entry["media"] = {}
        derive_all("peaks", audio, args, env)
        if args.defer_listening_copies:
            for _, _, entry in audio:
                entry["media"]["listen"] = "deferred"
        else:
            derive_all("listen", audio, args, env)

        for entry, suggestions in detected:
            _, answer = enrich("match-windows", suggestions, args, env, current_export())
            entry["match"] = match_report(answer)
            if answer["status"] == "committed":
                reader.refresh(args.archive_command, env)

        def refreshed_export() -> Path:
            reader.refresh(args.archive_command, env)
            return current_export()

        def propose(*arguments: Any, **options: Any) -> tuple[int, dict]:
            try:
                return enrich(*arguments, **options)
            except CaptureError as exc:
                raise propose_step.StepError(str(exc)) from exc
        def selection_evidence(artifact: dict[str, Any]) -> dict[str, Any]:
            # Explain the same refreshed ArchiveReader the proposal stage uses.
            reader.refresh(args.archive_command, env)
            selected = reader.heads.get(artifact["object_id"])
            pin = lambda record: {"id": record["object_id"], "revision_id": record["revision_id"]}
            streams = [record for record in reader.heads.values()
                       if any(c.get("id") == "archive.claim/audio_stream" for c in record.get("contracts", []))
                       and selected is not None and any(
                           r.get("role") == "artifact" and r.get("id") == selected["object_id"]
                           and r.get("revision_id") == selected["revision_id"] for r in record.get("refs", []))]
            return {"snapshot": reader.snapshot, "artifact": pin(selected) if selected else None,
                    "streams": [{**pin(record), "sample_rate": record["payload"].get("sample_rate"),
                                 "sample_count": record["payload"].get("sample_count"),
                                 "stream_index": record["payload"].get("stream_index")} for record in streams]}

        try:
            propose_step.propose_all(primary, args, env, propose, content_path, refreshed_export,
                                     selection_evidence)
        except propose_step.StepError as exc:
            raise CaptureError(str(exc)) from exc


def match_report(answer: dict[str, Any]) -> dict[str, Any]:
    """What the import reports of one `enrich match-windows` answer: its
    status, how many window claims it proposed, and every kept window with
    each Work within the limit. Only a window's closest Work is claimed; the
    others are kept here, in the run's answer, and never claimed (ADR 0072,
    amendment of 2026-10-18)."""
    windows = answer.get("windows", [])
    report: dict[str, Any] = {"status": answer["status"],
                              "proposed": sum(1 for window in windows if window.get("proposed"))}
    if windows:
        report["windows"] = windows
    return report


def suggestions_made_by(reader: adapter.ArchiveReader, answer: dict[str, Any]) -> str:
    """The span suggestions claim the detector's run made, whether it was
    committed now or found unchanged from an earlier import."""
    run = answer.get("run", {}).get("revision_id")
    made = [record["object_id"] for record in reader.records
            if record["shape"] == "claim" and reader.cites(record, SPANS_CONTRACT)
            and any(ref.get("role") == "run" and ref.get("revision_id") == run for ref in record.get("refs", []))]
    if run is None or len(made) != 1:
        raise CaptureError(f"enrich detect answered run {run}, which made {len(made)} span suggestions claims, not one")
    return made[0]


def derive_all(kind: str, audio: list[tuple[Session, dict[str, Any], dict[str, Any]]],
               args: argparse.Namespace, env: dict[str, str]) -> None:
    """Make one kind of derived media for every Artifact, `--jobs` at a time.
    Each is its own `enrich/media.py` process, which writes only into the
    derived-media root, so they cannot interfere. Every one is waited for and
    reported; the first failure, in import order, is then raised."""
    def derive(artifact: dict[str, Any]) -> dict[str, Any]:
        output = run([sys.executable, str(ENRICH / "media.py"), "--root", str(args.derived_media),
                      "--digest", artifact["digest"], "--kind", kind, "--ffmpeg", args.ffmpeg,
                      "--ffprobe", args.ffprobe, str(content_path(args.archive, artifact["digest"]))],
                     args.archive, f"derived media ({kind}) for {artifact['file']}", env)
        try:
            return json.loads(output)["outputs"][kind]["status"]
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise CaptureError(f"derived media ({kind}) for {artifact['file']} answered {output[:300]!r}") from exc

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(derive, artifact) for _, artifact, _ in audio]
    failure = None
    for (_, _, entry), future in zip(audio, futures):
        try:
            entry["media"][kind] = future.result()
        except CaptureError as exc:
            entry["media"][kind] = "failed"
            failure = failure or exc
    if failure is not None:
        raise failure


# -- the command -------------------------------------------------------------

def environment() -> dict[str, str]:
    """Door needs absolute git and git-annex executables; take them from PATH
    when unset, as the enrichment command does."""
    env = dict(os.environ)
    for variable, program in (("ARCHIVE_GIT_EXECUTABLE", "git"), ("ARCHIVE_GIT_ANNEX_EXECUTABLE", "git-annex")):
        if variable not in env:
            found = shutil.which(program)
            if found is None:
                raise CaptureError(f"{program} is not on PATH and {variable} is unset")
            env[variable] = os.path.realpath(found)
    return env


def default_jobs() -> int:
    """Each listening copy is one single-threaded encoder; leave two
    processors for Door, the relay, and everything else."""
    return max(1, (os.cpu_count() or 1) - 2)


def check_places(args: argparse.Namespace) -> None:
    """The input must be a directory apart from everything the importer
    writes, so an import can never read its own output or change its input."""
    if not args.directory.is_dir():
        raise CaptureError(f"{args.directory} is not a directory")
    if not args.derived_media.is_dir():
        raise CaptureError(f"the derived-media root {args.derived_media} is not a directory")
    places = [("archive", args.archive), ("staging repository", args.staging),
              ("derived-media root", args.derived_media)]
    if getattr(args, "bank", None) is not None:
        places.append(("feature bank", args.bank))
    for what, place in places:
        if place.is_relative_to(args.directory) or args.directory.is_relative_to(place):
            raise CaptureError(f"the input {args.directory} and the {what} {place} overlap")


def describe(session: Session) -> dict[str, Any]:
    return {"name": session.name, "label": session.label, "grammar": session.grammar, "status": session.status,
            "sources": [{"source": source.token, "files": [item.relative for item in source.files]}
                        for source in session.sources],
            "passed_over": session.passed_over,
            **session.planned}


def import_directory(args: argparse.Namespace, grammars: Sequence[Any]) -> dict[str, Any]:
    try:
        with propose_step.extract.frozen_config(args, "extract_config"):
            return _import_directory(args, grammars)
    except propose_step.extract.RunError as exc:
        raise CaptureError(str(exc)) from exc


def _import_directory(args: argparse.Namespace, grammars: Sequence[Any]) -> dict[str, Any]:
    for name in ("archive", "staging", "derived_media", "directory", "bank", "extract_config"):
        if getattr(args, name, None) is not None:
            setattr(args, name, Path(os.path.realpath(getattr(args, name))))
    check_places(args)
    env = environment()
    selected_contracts = ()
    if propose_step.bank_root(args, env) is not None:
        try:
            _, _, chosen = propose_step.configured_selection(args)
            selected_contracts = propose_step.contracts(chosen)
        except propose_step.StepError as exc:
            raise CaptureError(str(exc)) from exc
    reader = adapter.ArchiveReader(args.archive, args.archive_command, env)
    # Refuse an archive the chain cannot finish in before anything is staged:
    # an import whose sessions are committed is not rerun.
    proposing = selected_contracts
    for contract in (PARTS_CONTRACT, START_CONTRACT, SPANS_CONTRACT, WINDOW_MATCH_CONTRACT, *proposing):
        reader.citation(contract)
    files, ignored = read_directory(args.directory)
    if not files:
        raise CaptureError(f"{args.directory} holds no file to import"
                           + (f" (passed over: {', '.join(ignored)})" if ignored else ""))
    if args.expect is not None:
        check_expected(files, Path(args.expect))
    sessions = group(files, grammars, args.session_name)
    classify(sessions, reader)
    answer: dict[str, Any] = {"ignored": ignored, "sessions": [], "chain": [],
                              "session_name_used": any(s.grammar is None and s.status == "new" for s in sessions)}
    staging = adapter.Staging(args.staging, env["ARCHIVE_GIT_EXECUTABLE"], env["ARCHIVE_GIT_ANNEX_EXECUTABLE"], env,
                              remote=STAGING_REMOTE)
    new = [session for session in sessions if session.status == "new"]
    try:
        for session in new:
            admit(session, reader, staging, args.door, args.dry_run, env)
        if not args.dry_run:
            chain(new, args, env, answer["chain"])
    except (CaptureError, adapter.AdapterError) as exc:
        answer["sessions"] = [describe(session) for session in sessions]
        raise Failed(str(exc), answer) from exc
    answer["sessions"] = [describe(session) for session in sessions]
    answer["status"] = "unchanged" if not new else ("predicted" if args.dry_run else "committed")
    return answer


class Failed(Exception):
    def __init__(self, detail: str, answer: dict[str, Any]):
        super().__init__(detail)
        self.answer = answer


def main(argv: list[str] | None = None, grammars: Sequence[Any] = grammars_module.GRAMMARS) -> int:
    parser = argparse.ArgumentParser(prog="capture", description="Import recorder files into an archive")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("import", help="import a directory of recorder files")
    command.add_argument("directory", help="the directory of recorder files; it is only read")
    command.add_argument("--archive", required=True, help="the archive root")
    command.add_argument("--staging", required=True, help="the git-annex repository to stage new content in")
    command.add_argument("--derived-media", required=True, help="the derived-media root (ADR 0069)")
    command.add_argument("--session-name", help="the name of the session of files no grammar recognises")
    command.add_argument("--dry-run", action="store_true",
                         help="ask Door to predict; stage, write, and enrich nothing")
    command.add_argument("--door", default="door")
    command.add_argument("--archive-command", default="archive")
    command.add_argument("--ffmpeg", default="ffmpeg")
    command.add_argument("--ffprobe", default="ffprobe")
    command.add_argument("--expect", help="a manifest of the files the directory must hold, with their digests "
                                          "and sizes, as `capture send` writes it")
    command.add_argument("--jobs", type=int, default=default_jobs(),
                         help="derived media made at once, and processes matching windows (default: the "
                              "processors less two, at least one)")
    command.add_argument("--detector-preset", choices=DETECTOR_PRESETS, default="jam",
                         help="the span detector's preset (ADR 0091): jam (the default) for rehearsals and "
                              "shows, studio for masters and studio recordings, which never says talk")
    command.add_argument("--profile-cache",
                         help="the directory `enrich match-windows` keeps the corpus occurrences' profiles in "
                              "between imports; regenerable, outside any archive")
    command.add_argument("--defer-listening-copies", action="store_true",
                         help="make peaks only; listening copies are left for a later enrich/media.py run")
    propose_step.add_arguments(command)
    args = parser.parse_args(argv)
    if args.session_name is not None and not args.session_name.strip():
        parser.error("--session-name must not be empty")
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")
    try:
        answer = import_directory(args, grammars)
    except Failed as exc:
        print(json.dumps({"status": "failed", "detail": str(exc), **exc.answer}, sort_keys=True), file=sys.stderr)
        return 1
    except (CaptureError, adapter.AdapterError) as exc:
        print(json.dumps({"status": "failed", "detail": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(answer, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
