#!/usr/bin/env python3
"""The `documents` command: importers of outside documents (ADR 0116, proposed).

    documents/documents.py ireal import FILE --archive DIR --staging DIR [--dry-run]
                                         [--retrieved-at DATE] [--proposals DIR]
    documents/documents.py ireal candidates FILE... --archive DIR --out DIR
    documents/documents.py ireal link DECISIONS --archive DIR [--dry-run] [--operator URN]

Each prints one JSON object. `import` exits 1 when a chart could not be read
(its document is still admitted) or a step failed; `link` and `candidates`
exit 1 when refused. 2 means the arguments were not understood. Nothing here
writes a record: Door admits every batch (INV-2).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "ireal"))
sys.path.insert(0, str(HERE.parent / "enrich"))

import adapter  # noqa: E402
import importer  # noqa: E402
import links  # noqa: E402
from batches import BatchError  # noqa: E402


def environment() -> dict[str, str]:
    """Door needs absolute git and git-annex executables; take them from PATH
    when unset, as capture does."""
    env = dict(os.environ)
    for variable, program in (("ARCHIVE_GIT_EXECUTABLE", "git"), ("ARCHIVE_GIT_ANNEX_EXECUTABLE", "git-annex")):
        if variable not in env:
            found = shutil.which(program)
            if found is None:
                raise adapter.AdapterError(f"{program} is not on PATH and {variable} is unset")
            env[variable] = os.path.realpath(found)
    return env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="documents", description="Importers of outside documents")
    formats = parser.add_subparsers(dest="format", required=True)
    ireal = formats.add_parser("ireal", help="iReal Pro playlists (ADR 0116)")
    commands = ireal.add_subparsers(dest="command", required=True)

    def common(command: argparse.ArgumentParser) -> None:
        command.add_argument("--archive", required=True, type=Path, help="the archive root")
        command.add_argument("--door", default="door")
        command.add_argument("--archive-command", default="archive")

    imp = commands.add_parser("import", help="admit one playlist file and read its charts into claims")
    imp.add_argument("file", type=Path)
    common(imp)
    imp.add_argument("--staging", required=True, type=Path, help="the git-annex repository new content is staged in")
    imp.add_argument("--retrieved-at", help="when the operator exported the file (RFC 3339 date or UTC instant)")
    imp.add_argument("--dry-run", action="store_true", help="ask Door to predict; stage and write nothing")
    imp.add_argument("--proposals", type=Path, help="keep each batch's proposal directory here")

    cand = commands.add_parser("candidates", help="list candidate Works and people for admitted charts")
    cand.add_argument("files", type=Path, nargs="+")
    common(cand)
    cand.add_argument("--out", required=True, type=Path,
                      help="a private directory outside any archive and this repository")

    link = commands.add_parser("link", help="propose the operator's confirmed links")
    link.add_argument("decisions", type=Path)
    common(link)
    link.add_argument("--operator", help="the operator's Entity URN (default: the archive's operator)")
    link.add_argument("--dry-run", action="store_true")
    link.add_argument("--proposals", type=Path, help="keep each batch's proposal directory here")

    args = parser.parse_args(argv)
    try:
        env = environment()
        archive = Path(os.path.realpath(args.archive))
        if args.command == "import":
            answer = importer.run_import(args.file, archive=archive, staging_path=Path(os.path.realpath(args.staging)),
                                         door=args.door, archive_command=args.archive_command, dry_run=args.dry_run,
                                         retrieved_at=args.retrieved_at, proposals=args.proposals, env=env)
            print(json.dumps(answer, sort_keys=True))
            return 1 if answer["failed"] else 0
        if args.command == "candidates":
            reader = adapter.ArchiveReader(archive, args.archive_command, env)
            counts = links.candidates(reader, args.files, args.out)
            print(json.dumps({"status": "written", "out": str(args.out), **counts}, sort_keys=True))
            return 0
        answer = links.run_link(args.decisions, archive=archive, door=args.door, archive_command=args.archive_command,
                                dry_run=args.dry_run, operator=args.operator, proposals=args.proposals, env=env)
        print(json.dumps(answer, sort_keys=True))
        return 0
    except (adapter.AdapterError, BatchError, importer.ImportError_, links.LinkError, OSError) as error:
        print(json.dumps({"status": "failed", "detail": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
