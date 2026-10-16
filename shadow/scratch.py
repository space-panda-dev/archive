#!/usr/bin/env python3
"""Scratch archives: writable copies of an archive for testing real commits (ADR 0061).

A scratch archive is a writable Btrfs snapshot of a source archive, with its
own `door serve`, World store, and relay in role `scratch` with commits on.
Taking the snapshot only reads the source; nothing here writes to it. Scratch
copies are disposable: `reset` drops the copy and takes a fresh snapshot, and
`delete` removes everything the tool made. None of it needs root: a subvolume
is recognised by its inode when `btrfs subvolume show` is refused, and a copy
is removed by emptying it and removing the directory when `btrfs subvolume
delete` is refused (Linux 4.18 and later let the owner remove an empty
subvolume that way).

    scratch.py create NAME --source ARCHIVE --port PORT --build BUILD
                      --python PYTHON --relay-config SHADOW_RELAY_JSON [--publish]
    scratch.py reset NAME
    scratch.py delete NAME
    scratch.py list

Everything lives in `--root` (default ~/archive-scratch), one directory per
scratch archive. Services run as transient systemd user units, which a reboot
stops; `reset` starts them again.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parent
NAME = re.compile(r"[a-z][a-z0-9-]{0,31}")
STATE = "scratch.json"


class ScratchError(Exception):
    pass


def run(arguments: list[str], **options: Any) -> str:
    completed = subprocess.run(arguments, capture_output=True, text=True, **options)
    if completed.returncode != 0:
        raise ScratchError(f"{' '.join(arguments[:3])} failed ({completed.returncode}): "
                           f"{completed.stderr.strip() or completed.stdout.strip()}")
    return completed.stdout


def private(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def units(name: str) -> tuple[str, str]:
    return f"archive-scratch-{name}-door", f"archive-scratch-{name}-relay"


def subvolume_from_stat(filesystem: str, inode: int) -> bool:
    """The root directory of every Btrfs subvolume is inode 256."""
    return filesystem == "btrfs" and inode == 256


def is_subvolume(path: Path) -> bool:
    shown = subprocess.run(["btrfs", "subvolume", "show", str(path)], capture_output=True, text=True)
    if shown.returncode == 0:
        return True
    if re.search(r"not a (btrfs )?subvolume|not a btrfs filesystem", shown.stderr, re.IGNORECASE):
        return False
    # Refused for some other reason, usually privileges: ask the file system.
    filesystem = run(["stat", "-f", "-c", "%T", str(path)]).strip()
    return subvolume_from_stat(filesystem, path.stat().st_ino)


def make_writable(path: Path) -> None:
    for directory, _, _ in os.walk(path):
        os.chmod(directory, 0o700)


def drop_subvolume(path: Path) -> None:
    deleted = subprocess.run(["btrfs", "subvolume", "delete", str(path)], capture_output=True, text=True)
    if deleted.returncode == 0:
        return
    # Without user_subvol_rm_allowed only root may delete a subvolume, but its
    # owner may rmdir it once it is empty.
    make_writable(path)
    for child in path.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    try:
        path.rmdir()
    except OSError as exc:
        raise ScratchError(f"could not remove {path}: btrfs said {deleted.stderr.strip()!r}, rmdir said {exc}") from exc


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        return probe.connect_ex(("127.0.0.1", port)) != 0


class Scratch:
    def __init__(self, root: Path, name: str):
        if not NAME.fullmatch(name):
            raise ScratchError("a scratch name is lowercase letters, digits, and hyphens, starting with a letter")
        self.name = name
        self.directory = root / name
        self.archive = self.directory / "archive"
        self.store = self.directory / "world-store"
        self.run_directory = self.directory / "run"
        self.staging = self.directory / "relay-staging"
        self.socket = self.run_directory / "door.sock"
        self.config = self.directory / "relay.json"

    # -- state -------------------------------------------------------------

    def state(self) -> dict[str, Any]:
        try:
            return json.loads((self.directory / STATE).read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ScratchError(f"no scratch archive named {self.name}") from exc

    def write_state(self, state: dict[str, Any]) -> None:
        temporary = self.directory / (STATE + ".tmp")
        temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, self.directory / STATE)

    # -- copies --------------------------------------------------------------

    def take_snapshot(self, source: Path) -> str:
        """One writable snapshot of the source. It is atomic, so the copy is one
        consistent state of the source, as if power had been lost then; Door
        recovers such a state (ADR 0031)."""
        if not (source / "archive.json").is_file():
            raise ScratchError(f"{source} is not an archive root")
        if not is_subvolume(source):
            raise ScratchError(f"{source} is not a Btrfs subvolume, so it cannot be snapshotted on its own; "
                               "making it one is a change to the source and needs the operator's approval")
        run(["btrfs", "subvolume", "snapshot", str(source), str(self.archive)])
        return run(["git", "-C", str(self.archive), "rev-parse", "HEAD"]).strip()

    def drop_copy(self) -> None:
        if self.archive.exists():
            drop_subvolume(self.archive)
        if self.store.exists():
            shutil.rmtree(self.store)

    # -- services ------------------------------------------------------------

    def environment(self, state: dict[str, Any]) -> dict[str, str]:
        return {"ARCHIVE_GIT_EXECUTABLE": state["git"], "ARCHIVE_GIT_ANNEX_EXECUTABLE": state["git_annex"]}

    def start(self, state: dict[str, Any]) -> None:
        door_unit, relay_unit = units(self.name)
        build = Path(state["build"])
        environment = self.environment(state)
        # World first, from the copy: a full rebuild on a new store.
        run([state["python"], str(REPOSITORY / "world/world.py"), "refresh", str(self.archive),
             "--store", str(self.store), "--archive-command", str(build / "bin/archive")],
            env={**os.environ, **environment})
        if self.socket.exists():
            self.socket.unlink()
        settings = [f"--setenv={key}={value}" for key, value in environment.items()]
        run(["systemd-run", "--user", f"--unit={door_unit}", f"--working-directory={self.archive}", *settings,
             str(build / "bin/door"), "serve", "--socket", str(self.socket)])
        for _ in range(200):
            if self.socket.exists():
                break
            time.sleep(0.05)
        else:
            raise ScratchError(f"{door_unit} did not create its socket")
        run(["systemd-run", "--user", f"--unit={relay_unit}", *settings,
             state["python"], str(REPOSITORY / "relay/relay.py"), "--config", str(self.config)])
        for _ in range(200):
            if not port_is_free(state["port"]):
                break
            time.sleep(0.05)
        else:
            raise ScratchError(f"{relay_unit} did not listen on port {state['port']}")
        if state["publish"]:
            run(["tailscale", "serve", "--bg", f"--https={state['port']}", str(state["port"])])

    def stop(self, state: dict[str, Any]) -> None:
        if state.get("publish"):
            subprocess.run(["tailscale", "serve", f"--https={state['port']}", "off"], capture_output=True)
        for unit in units(self.name):
            # A unit that is not running is already stopped; anything else fails.
            stopped = subprocess.run(["systemctl", "--user", "stop", unit], capture_output=True, text=True)
            if stopped.returncode not in (0, 5):
                raise ScratchError(f"could not stop {unit}: {stopped.stderr.strip()}")

    # -- commands ------------------------------------------------------------

    def create(self, source: Path, port: int, build: Path, python: str, relay_config: Path, publish: bool) -> dict[str, Any]:
        source = source.resolve()
        if self.directory.exists():
            raise ScratchError(f"{self.directory} exists; reset or delete it")
        if self.directory.resolve().is_relative_to(source):
            raise ScratchError("a scratch archive cannot live inside its source")
        for program in ("door", "archive"):
            if not os.access(build / "bin" / program, os.X_OK):
                raise ScratchError(f"{build}/bin/{program} is not executable")
        if not port_is_free(port):
            raise ScratchError(f"port {port} is in use")
        shadow = json.loads(relay_config.read_text(encoding="utf-8"))
        state = {
            "protocol": "archive-scratch/1", "name": self.name, "source": str(source), "port": port,
            "build": str(build.resolve()), "python": python, "publish": publish,
            "git": os.path.realpath(shutil.which("git") or ""), "git_annex": os.path.realpath(shutil.which("git-annex") or ""),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        if not (state["git"] and state["git_annex"]):
            raise ScratchError("git and git-annex must be on PATH")
        private(self.directory)
        private(self.run_directory)
        private(self.staging)
        self.config.write_text(json.dumps({
            "listen": {"host": "127.0.0.1", "port": port},
            "archive_root": str(self.archive), "door_socket": str(self.socket), "world_store": str(self.store),
            "archive_command": str(Path(state["build"]) / "bin/archive"), "staging": str(self.staging),
            "writes": "commit", "role": "scratch",
            "origins": shadow["origins"], "callers": shadow["callers"],
        }, indent=2) + "\n", encoding="utf-8")
        state["source_head"] = self.take_snapshot(source)
        self.write_state(state)
        self.start(state)
        return state

    def reset(self) -> dict[str, Any]:
        state = self.state()
        self.stop(state)
        self.drop_copy()
        state["source_head"] = self.take_snapshot(Path(state["source"]))
        state["reset_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.write_state(state)
        self.start(state)
        return state

    def delete(self) -> dict[str, Any]:
        state = self.state()
        self.stop(state)
        self.drop_copy()
        make_writable(self.directory)
        shutil.rmtree(self.directory)
        return {"deleted": self.name}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scratch", description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path.home() / "archive-scratch")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("name")
    create.add_argument("--source", type=Path, required=True)
    create.add_argument("--port", type=int, required=True)
    create.add_argument("--build", type=Path, required=True, help="a directory holding bin/door and bin/archive")
    create.add_argument("--python", required=True, help="a Python with World's requirements")
    create.add_argument("--relay-config", type=Path, required=True, help="the source's relay.json, for callers and origins")
    create.add_argument("--publish", action="store_true", help="publish the relay's port with tailscale serve")
    for name in ("reset", "delete"):
        commands.add_parser(name).add_argument("name")
    commands.add_parser("list")
    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            found = [json.loads(p.read_text()) for p in sorted(args.root.glob(f"*/{STATE}"))] if args.root.exists() else []
            answer: Any = {"scratch": found}
        else:
            scratch = Scratch(args.root, args.name)
            if args.command == "create":
                answer = scratch.create(args.source, args.port, args.build, args.python, args.relay_config, args.publish)
            elif args.command == "reset":
                answer = scratch.reset()
            else:
                answer = scratch.delete()
    except (ScratchError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({"status": "failed", "detail": str(exc)}), file=sys.stderr)
        return 1
    json.dump(answer, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
