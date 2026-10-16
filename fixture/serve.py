#!/usr/bin/env python3
"""Build the fixture archive and serve it through Door and the relay (ADR 0062).

For a client's CI: one command gives a fresh archive with every contract in
use, `door serve`, a World store, and a relay in role `scratch` with commits
on, listening on the loopback interface. The caller sends the
`Tailscale-User-Login` header itself; no tailnet is involved.

    fixture/serve.py --work DIR [--port 8450] [--login ci@example.invalid] [--origin URL]...

It prints the fixture manifest (with the relay's URL and the login's agent)
once the relay answers, and serves until interrupted.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parent
sys.path.insert(0, str(REPOSITORY / "relay"))
sys.path.insert(0, str(REPOSITORY / "world"))

import relay  # noqa: E402
import world  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="archive-fixture-serve", description=__doc__.splitlines()[0])
    parser.add_argument("--work", type=Path, required=True, help="an empty or absent directory to build in")
    parser.add_argument("--port", type=int, default=8450)
    parser.add_argument("--login", default="ci@example.invalid", help="the Tailscale-User-Login clients send")
    parser.add_argument("--origin", action="append", default=[], help="an https origin browsers may call from")
    parser.add_argument("--door", default=shutil.which("door") or "door")
    parser.add_argument("--archive", default=shutil.which("archive") or "archive")
    args = parser.parse_args(argv)
    work = args.work.absolute()
    if work.exists() and any(work.iterdir()):
        print(json.dumps({"status": "failed", "detail": f"{work} is not empty"}), file=sys.stderr)
        return 1
    work.mkdir(parents=True, exist_ok=True)
    archive_root = work / "archive"
    built = subprocess.run([sys.executable, str(HERE / "build.py"), str(archive_root), "--door", args.door,
                            "--archive", args.archive], capture_output=True, text=True)
    if built.returncode != 0:
        sys.stderr.write(built.stderr)
        return 1
    manifest = json.loads(built.stdout)
    environment = dict(os.environ)
    for variable, program in (("ARCHIVE_GIT_EXECUTABLE", "git"), ("ARCHIVE_GIT_ANNEX_EXECUTABLE", "git-annex")):
        environment.setdefault(variable, os.path.realpath(shutil.which(program) or program))
    os.environ.update(environment)
    store = work / "world-store"
    world.refresh_served(archive_root, store, archive_command=args.archive)
    run_directory, staging = work / "run", work / "relay-staging"
    for directory in (run_directory, staging):
        directory.mkdir(mode=0o700)
    socket_path = run_directory / "door.sock"
    # A SIGTERM would otherwise end Python without the finally below, and
    # leave Door serving with nobody to stop it.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    door = subprocess.Popen([args.door, "serve", "--socket", str(socket_path)], cwd=archive_root, env=environment)
    try:
        for _ in range(400):
            if socket_path.exists():
                break
            time.sleep(0.05)
        else:
            print(json.dumps({"status": "failed", "detail": "door serve did not start"}), file=sys.stderr)
            return 1
        config = relay.Config(
            host="127.0.0.1", port=args.port, archive_root=archive_root, door_socket=socket_path,
            world_store=store, archive_command=args.archive, staging=staging, writes="commit", role="scratch",
            origins=frozenset(args.origin), callers={args.login: manifest["operator_id"]},
            query_timeout_seconds=10, max_rows=10000)
        manifest.update(relay=f"http://127.0.0.1:{args.port}", login=args.login, agent=manifest["operator_id"])
        (work / "fixture.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        sys.stdout.write(json.dumps(manifest, sort_keys=True) + "\n")
        sys.stdout.flush()
        relay.serve(config)
    finally:
        door.terminate()
        door.wait(timeout=30)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
