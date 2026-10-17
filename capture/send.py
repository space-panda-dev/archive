#!/usr/bin/env python3
"""Send a recorder card read on one machine to `capture import` on another.

    capture/send.py <card directory> --config <file> [--session-name <name>]
                    [--jobs <n>] [--defer-listening-copies] [--dry-run]

A card is read where it is inserted, but the archive lives on its own
machine (ADR 0070). This command hashes every file on the card and groups it
with the recorder grammars here, so a card the importer would refuse is
refused before anything crosses the network; streams the files over ssh into
an inbox directory on the archive machine, named by the card's contents so a
second send of the same card reuses it; and runs `capture import` there with
`--expect`, so the importer refuses any file that did not arrive as the bytes
hashed here. It never changes the card.

The configuration is the operator's, kept outside this repository:

    {"host": "<ssh destination>",
     "inbox": "<directory on the archive machine>",
     "import": ["<python>", "<.../capture/capture.py>", "import", "--archive", "<dir>",
                "--staging", "<annex>", "--derived-media", "<root>", ...]}

Put the inbox on the same file system as the staging repository: the
importer then stages each file by reflink, and the copy this command makes is
the only one written.

It prints the importer's JSON answer and exits with its status; a failure
before the import prints `{"status": "failed", "detail": ...}` and exits 1.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import capture  # noqa: E402
import grammar as grammars_module  # noqa: E402

PROTOCOL = "archive-capture-send/1"


class SendError(Exception):
    pass


def read_config(path: Path) -> dict[str, Any]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SendError(f"the configuration {path} could not be read: {exc}") from exc
    if not isinstance(config, dict) or not isinstance(config.get("host"), str) or not config["host"] \
            or not isinstance(config.get("inbox"), str) or not config["inbox"].startswith("/") \
            or not isinstance(config.get("import"), list) or not config["import"] \
            or not all(isinstance(part, str) and part for part in config["import"]):
        raise SendError(f"the configuration {path} needs a host, an absolute inbox path, and an import command "
                        "as a list of strings")
    return config


def manifest_of(files: list[capture.InputFile]) -> dict[str, Any]:
    return {"protocol": PROTOCOL,
            "files": [{"path": item.relative, "digest": item.digest, "size": item.size} for item in files]}


def batch_name(manifest: dict[str, Any]) -> str:
    """The card's contents name its inbox directory: the same card sent
    again lands in the same place."""
    listing = json.dumps(manifest["files"], sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "card-" + hashlib.sha256(listing).hexdigest()[:32]


def remote(ssh: str, host: str, command: str, stdin: Any = None, what: str = "") -> subprocess.CompletedProcess:
    try:
        return subprocess.run([ssh, host, command], stdin=stdin, capture_output=True,
                              input=None if stdin is not None else b"")
    except OSError as exc:
        raise SendError(f"ssh could not run for {what}: {exc}") from exc


def checked(completed: subprocess.CompletedProcess, what: str) -> None:
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip() or completed.stdout.decode("utf-8", "replace")
        raise SendError(f"{what} failed ({completed.returncode}): {detail.strip()[-500:]}")


def send(card: Path, config: dict[str, Any], args: argparse.Namespace,
         grammars: Sequence[Any]) -> tuple[int, str, str]:
    files, ignored = capture.read_directory(card)
    if not files:
        raise SendError(f"{card} holds no file to send" + (f" (passed over: {', '.join(ignored)})" if ignored else ""))
    # Refuse here what the importer would refuse there, before the transfer.
    capture.group(files, grammars, args.session_name)
    manifest = manifest_of(files)
    batch = batch_name(manifest)
    inbox = config["inbox"].rstrip("/")
    directory, manifest_path = f"{inbox}/{batch}", f"{inbox}/{batch}.manifest.json"
    host, ssh = config["host"], args.ssh

    checked(remote(ssh, host, f"mkdir -p -m 700 {shlex.quote(directory)}", what="making the inbox directory"),
            "making the inbox directory")
    with tempfile.TemporaryFile() as listing:
        listing.write(json.dumps(manifest, indent=2).encode("utf-8") + b"\n")
        listing.seek(0)
        checked(remote(ssh, host, f"cat > {shlex.quote(manifest_path)}", stdin=listing, what="the manifest"),
                "writing the manifest")
    # One tar stream of exactly the listed files, without macOS's extended
    # attribute companions; the card is only read.
    environment = {**os.environ, "COPYFILE_DISABLE": "1"}
    try:
        packing = subprocess.Popen([args.tar, "-c", "-f", "-", "-C", str(card), "--",
                                    *(item.relative for item in files)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment)
    except OSError as exc:
        raise SendError(f"tar could not run: {exc}") from exc
    try:
        unpacking = remote(ssh, host, f"tar -x -f - -C {shlex.quote(directory)}", stdin=packing.stdout,
                           what="the transfer")
    finally:
        # Whatever happened to the transfer, tar is finished with and its
        # pipes closed; a transfer that never started stops it.
        packing.stdout.close()
        if packing.poll() is None:
            packing.kill()
        packed_errors = packing.stderr.read().decode("utf-8", "replace")
        packing.stderr.close()
        packing.wait()
    if packing.returncode != 0:
        raise SendError(f"reading the card failed ({packing.returncode}): {packed_errors.strip()[-500:]}")
    checked(unpacking, "the transfer")

    command = [*config["import"], directory, "--expect", manifest_path]
    if args.session_name is not None:
        command += ["--session-name", args.session_name]
    if args.jobs is not None:
        command += ["--jobs", str(args.jobs)]
    if args.defer_listening_copies:
        command.append("--defer-listening-copies")
    if args.dry_run:
        command.append("--dry-run")
    imported = remote(ssh, host, shlex.join(command), what="capture import")
    return (imported.returncode, imported.stdout.decode("utf-8", "replace"),
            imported.stderr.decode("utf-8", "replace"))


def main(argv: list[str] | None = None, grammars: Sequence[Any] = grammars_module.GRAMMARS) -> int:
    parser = argparse.ArgumentParser(prog="capture-send", description=__doc__.splitlines()[0])
    parser.add_argument("card", type=Path, help="the card's directory; it is only read")
    parser.add_argument("--config", type=Path, required=True, help="the operator's send configuration (JSON)")
    parser.add_argument("--session-name", help="passed to capture import, for files no grammar recognises")
    parser.add_argument("--jobs", type=int, help="passed to capture import")
    parser.add_argument("--defer-listening-copies", action="store_true", help="passed to capture import")
    parser.add_argument("--dry-run", action="store_true",
                        help="send the files, then ask the importer to predict; it stages and commits nothing")
    parser.add_argument("--ssh", default="ssh")
    parser.add_argument("--tar", default="tar")
    args = parser.parse_args(argv)
    if args.session_name is not None and not args.session_name.strip():
        parser.error("--session-name must not be empty")
    if args.jobs is not None and args.jobs < 1:
        parser.error("--jobs must be at least 1")
    try:
        if not args.card.is_dir():
            raise SendError(f"{args.card} is not a directory")
        code, out, err = send(args.card, read_config(args.config), args, grammars)
    except (SendError, capture.CaptureError) as exc:
        print(json.dumps({"status": "failed", "detail": str(exc)}), file=sys.stderr)
        return 1
    sys.stdout.write(out)
    sys.stderr.write(err)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
