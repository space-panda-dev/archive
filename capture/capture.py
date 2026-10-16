#!/usr/bin/env python3
"""Import a directory of recorder files into an archive (ADR 0070).

    capture import --archive <dir> --staging <annex> --derived-media <root> <directory>
                   [--session-name <name>] [--dry-run]

The importer hashes every file, skips content that is already an Artifact's,
groups the rest into sessions and sources with a recorder grammar, and
proposes each session to `door admit` in one content-bearing batch: an
Artifact per file, the `recording_session` Entity, and one
`archive.claim/session_parts` claim per source. Then it runs the enrichment
chain on each new Artifact: `enrich probe`, `enrich detect` on the session's
primary source, and `enrich/media.py`. It reads the archive only through
`archive export`, proposes only through Door, and never changes its input.

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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

HERE = Path(__file__).resolve().parent
ENRICH = HERE.parent / "enrich"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ENRICH))

import adapter  # noqa: E402
import grammar as grammars_module  # noqa: E402

BY = AGENT = "pkg:generic/archive-capture@0.1.0"
ARTIFACT_NAMESPACE = adapter.ARTIFACT_NAMESPACE
SESSION_NAMESPACE = "archive.audio/session"
PARTS_CONTRACT = ("archive.claim/session_parts", "1")
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
    what a name points to."""
    files: list[InputFile] = []
    ignored: list[str] = []
    for current, directories, names in os.walk(directory):
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
            digest, size = content_digest(path)
            files.append(InputFile(relative, path, size, digest))
    files.sort(key=lambda item: item.relative.encode("utf-8", "surrogateescape"))
    return files, sorted(ignored)


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
            sessions.append(Session(f"{g.label} {started:%Y-%m-%d %H:%M:%S}", g.name, sources))

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


def classify(sessions: list[Session], reader: adapter.ArchiveReader) -> None:
    """Mark each session already imported or new, and refuse what cannot be
    proposed: a session only partly in the archive (its parts would need a
    revision this importer does not make), the same content in two files,
    and a session object that already exists."""
    known = known_contents(reader)
    new_digests: Counter[str] = Counter()
    for session in sessions:
        present = [item for item in session.files if item.digest in known]
        if len(present) == len(session.files):
            session.status = "already_imported"
            continue
        if present:
            listed = ", ".join(item.relative for item in present)
            raise CaptureError(f"session {session.label!r} is partly imported already ({listed}); "
                               "its parts would need a new revision, which this importer does not propose")
        new_digests.update(item.digest for item in session.files)
        session_id = adapter.object_id(reader.archive_id, SESSION_NAMESPACE, session.name)
        if session_id in reader.heads:
            raise CaptureError(f"session {session.name} already exists, but none of its files' contents do")
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
    for item in session.files:
        pin = add(ARTIFACT_NAMESPACE, "capture-" + item.hex, "artifact",
                  {"content": {"digest": item.digest, "size": item.size}}, [])
        pins[item.digest] = pin
        artifacts.append({"file": item.relative, "digest": item.digest, **pin})
    entity = add(SESSION_NAMESPACE, session.name, "entity", {"kind": "recording_session", "label": session.label}, [])
    citation = reader.citation(PARTS_CONTRACT)
    parts = []
    for source in session.sources:
        refs = [{"role": "session", "id": entity["object_id"], "revision_id": entity["revision_id"]}]
        refs += [{"role": "part", "id": pins[item.digest]["object_id"], "revision_id": pins[item.digest]["revision_id"]}
                 for item in source.files]
        parts.append({"source": source.token,
                      **add(PARTS_CONTRACT[0], f"{session.name}-{source.token}", "claim", {}, refs, [citation])})
    session.planned = {"session": entity, "parts": parts, "artifacts": artifacts}
    return drafts, identities, artifacts


def admit(session: Session, reader: adapter.ArchiveReader, staging: adapter.Staging, door: str, dry_run: bool,
          env: dict[str, str]) -> list[str]:
    drafts, identities, _ = plan(session, reader)
    contents = []
    remote = None
    for item in session.files:
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


def enrich(command: str, artifact: str, args: argparse.Namespace, env: dict[str, str]) -> tuple[int, dict]:
    """Run one `enrich` command; 0 and 3 are answers, anything else fails."""
    arguments = [sys.executable, str(ENRICH / "enrich.py"), command, "--archive", str(args.archive),
                 "--artifact", artifact, "--staging", str(args.staging), "--door", args.door,
                 "--archive-command", args.archive_command, "--ffmpeg", args.ffmpeg]
    if command == "probe":
        arguments += ["--ffprobe", args.ffprobe]
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


def chain(session: Session, args: argparse.Namespace, env: dict[str, str], report: list[dict]) -> None:
    """Probe every new Artifact, detect spans on each part of the primary
    source, and make derived media; each run its own batch. A file with no
    countable audio stream is probed and reported, and nothing else runs on
    it. The first failure stops the chain."""
    primary = {item.digest for item in session.primary.files}
    for artifact in session.planned["artifacts"]:
        entry: dict[str, Any] = {"file": artifact["file"], "object_id": artifact["object_id"]}
        report.append(entry)
        code, answer = enrich("probe", artifact["object_id"], args, env)
        entry["probe"] = answer["status"]
        if code == 3:
            entry["detect"] = entry["media"] = "skipped: no countable audio stream"
            continue
        if artifact["digest"] in primary:
            _, answer = enrich("detect", artifact["object_id"], args, env)
            entry["detect"] = answer["status"]
        output = run([sys.executable, str(ENRICH / "media.py"), "--root", str(args.derived_media),
                      "--digest", artifact["digest"], "--ffmpeg", args.ffmpeg, "--ffprobe", args.ffprobe,
                      str(content_path(args.archive, artifact["digest"]))],
                     args.archive, f"derived media for {artifact['file']}", env)
        try:
            entry["media"] = {kind: made["status"] for kind, made in sorted(json.loads(output)["outputs"].items())}
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise CaptureError(f"derived media for {artifact['file']} answered {output[:300]!r}") from exc


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


def check_places(args: argparse.Namespace) -> None:
    """The input must be a directory apart from everything the importer
    writes, so an import can never read its own output or change its input."""
    if not args.directory.is_dir():
        raise CaptureError(f"{args.directory} is not a directory")
    if not args.derived_media.is_dir():
        raise CaptureError(f"the derived-media root {args.derived_media} is not a directory")
    for what, place in (("archive", args.archive), ("staging repository", args.staging),
                        ("derived-media root", args.derived_media)):
        if place.is_relative_to(args.directory) or args.directory.is_relative_to(place):
            raise CaptureError(f"the input {args.directory} and the {what} {place} overlap")


def describe(session: Session) -> dict[str, Any]:
    return {"name": session.name, "label": session.label, "grammar": session.grammar, "status": session.status,
            "sources": [{"source": source.token, "files": [item.relative for item in source.files]}
                        for source in session.sources],
            **session.planned}


def import_directory(args: argparse.Namespace, grammars: Sequence[Any]) -> dict[str, Any]:
    for name in ("archive", "staging", "derived_media", "directory"):
        setattr(args, name, Path(os.path.realpath(getattr(args, name))))
    check_places(args)
    env = environment()
    reader = adapter.ArchiveReader(args.archive, args.archive_command, env)
    reader.citation(PARTS_CONTRACT)
    files, ignored = read_directory(args.directory)
    if not files:
        raise CaptureError(f"{args.directory} holds no file to import"
                           + (f" (passed over: {', '.join(ignored)})" if ignored else ""))
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
            for session in new:
                chain(session, args, env, answer["chain"])
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
    args = parser.parse_args(argv)
    if args.session_name is not None and not args.session_name.strip():
        parser.error("--session-name must not be empty")
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
