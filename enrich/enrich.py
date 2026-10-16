#!/usr/bin/env python3
"""Run an enrichment tool against an archive's Artifact and propose its result.

    enrich probe --archive <dir> --artifact <object id> [--staging <annex>] [--dry-run]

The command reads the archive only through `archive export`, runs the tool on
the Artifact's locally present content, and proposes the run and its claims
to `door admit` through the adapter (ADR 0064). It prints one JSON object.
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
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import adapter  # noqa: E402
import probe as probe_tool  # noqa: E402

STREAM_CONTRACT = ("archive.claim/audio_stream", "1")
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


def content_path(root: Path, artifact: dict[str, Any]) -> Path:
    """The Artifact's bytes in the archive's content worktree (layout
    archive/2, D-1), checked against the digest and size the record states.
    Reading them changes nothing."""
    content = artifact["payload"]["content"]
    hexdigest = content["digest"].removeprefix("sha256:")
    path = root / "content" / "sha256" / hexdigest[0:2] / hexdigest[2:4] / hexdigest
    if not path.is_file():
        raise CommandError(f"the content of {artifact['object_id']} is not locally present at {path}")
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
    reader = adapter.ArchiveReader(Path(args.archive), args.archive_command, env)
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
    staging = None
    if args.staging:
        staging = adapter.Staging(Path(args.staging), env["ARCHIVE_GIT_EXECUTABLE"],
                                  env["ARCHIVE_GIT_ANNEX_EXECUTABLE"], env)
    admission = adapter.propose(reader, run, door=args.door, staging=staging, dry_run=args.dry_run, env=env)
    return 0, {"artifact": pin, "probe": result.as_json(), **admission.as_json()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="enrich", description="Propose enrichment results to Door")
    commands = parser.add_subparsers(dest="command", required=True)
    probe = commands.add_parser("probe", help="count an Artifact's audio streams and propose audio_stream claims")
    probe.add_argument("--archive", required=True, help="the archive root")
    probe.add_argument("--artifact", required=True, help="the Artifact's object identifier")
    probe.add_argument("--staging", help="a git-annex repository to stage new parameter content in")
    probe.add_argument("--dry-run", action="store_true", help="ask Door to predict; stage and write nothing")
    probe.add_argument("--door", default="door")
    probe.add_argument("--archive-command", default="archive")
    probe.add_argument("--ffmpeg", default="ffmpeg")
    probe.add_argument("--ffprobe", default="ffprobe")
    args = parser.parse_args(argv)
    try:
        code, answer = run_probe(args)
    except (CommandError, adapter.AdapterError, probe_tool.ProbeError) as exc:
        print(json.dumps({"status": "failed", "detail": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(answer, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
