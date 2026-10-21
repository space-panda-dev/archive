#!/usr/bin/env python3
"""Transcripts of Door's and Archive's commands, for `wf prove` and its goldens.

    scripts/prove.py usage      what `archive` and `door` say without a command
    scripts/prove.py fixture    build the fixture archive, read it, and admit one record

Each step prints the command and its exit code, then its output with every
value that differs from run to run replaced: UUIDs by `<uuid-N>` and
digests by `<sha256-N>` in order of first appearance, and the scratch
directory by `<tmp>`. So a transcript is the same on every run and every
machine, and a change to what a command prints shows as a diff against
`fixture/golden/` (`wf prove --golden`). Executables come from
ARCHIVE_TEST_DOOR and ARCHIVE_TEST_ARCHIVE, else `cabal list-bin`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
DIGEST = re.compile(r"(?<=sha256[:-])[0-9a-f]{64}")


class Normal:
    """Replaces run-specific values by stable names, in order of appearance."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = str(tmp)
        self.names: dict[str, str] = {}

    def _name(self, kind: str, value: str) -> str:
        key = f"{kind}:{value}"
        if key not in self.names:
            self.names[key] = f"<{kind}-{sum(1 for k in self.names if k.startswith(kind + ':')) + 1}>"
        return self.names[key]

    def __call__(self, text: str) -> str:
        text = text.replace(os.path.realpath(self.tmp), "<tmp>").replace(self.tmp, "<tmp>")
        text = UUID.sub(lambda m: self._name("uuid", m.group()), text)
        return DIGEST.sub(lambda m: self._name("sha256", m.group()), text)


def executable(name: str) -> str:
    given = os.environ.get(f"ARCHIVE_TEST_{name.upper()}")
    if given:
        return given
    found = subprocess.run(["cabal", "list-bin", name], cwd=ROOT, capture_output=True, text=True)
    if found.returncode != 0:
        raise SystemExit(f"prove: no {name}: set ARCHIVE_TEST_{name.upper()} or build it with cabal")
    return found.stdout.strip()


def step(normal: Normal, label: str, argv: list[str], cwd: Path, show=None) -> subprocess.CompletedProcess:
    done = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
    print(f"$ {label}\n[exit {done.returncode}]")
    out = show(done.stdout) if show is not None and done.returncode == 0 else done.stdout
    for stream, text in (("stdout", out), ("stderr", done.stderr)):
        if text.strip():
            print(f"--- {stream}\n{normal(text.rstrip())}")
    return done


def pretty(text: str) -> str:
    return json.dumps(json.loads(text), indent=1, sort_keys=True)


def usage(tmp: Path) -> int:
    normal = Normal(tmp)
    step(normal, "archive", [executable("archive")], tmp)
    step(normal, "door", [executable("door")], tmp)
    return 0


def shapes(text: str) -> str:
    listed = json.loads(text)
    counted = Counter(record["shape"] for record in listed["records"])
    return json.dumps({"record_count": listed["record_count"], "shapes": dict(sorted(counted.items()))}, indent=1)


def fixture(tmp: Path) -> int:
    normal = Normal(tmp)
    door, archive = executable("door"), executable("archive")
    os.environ.setdefault("archive_datadir", str(ROOT))
    for variable, program in (("ARCHIVE_GIT_EXECUTABLE", "git"), ("ARCHIVE_GIT_ANNEX_EXECUTABLE", "git-annex")):
        os.environ.setdefault(variable, os.path.realpath(shutil.which(program) or program))
    root = tmp / "fixture" / "archive"
    root.parent.mkdir()
    built = subprocess.run([sys.executable, str(ROOT / "fixture" / "build.py"), str(root), "--door", door,
                            "--archive", archive], capture_output=True, text=True)
    print(f"$ fixture/build.py <tmp>/fixture/archive\n[exit {built.returncode}]")
    if built.returncode != 0:
        print(f"--- stderr\n{normal(built.stderr.rstrip())}")
        return 1
    manifest = json.loads(built.stdout)
    print(f"--- stdout (keys)\n{', '.join(sorted(manifest))}; {len(manifest['steps'])} steps")
    step(normal, "archive status", [archive, "status"], root, pretty)
    step(normal, "archive doctor", [archive, "doctor"], root, pretty)
    step(normal, "archive ls (shapes counted)", [archive, "ls"], root, shapes)
    step(normal, "door verify", [door, "verify"], root)
    name = "prove-client"
    object_id = "urn:uuid:" + str(uuid.uuid5(uuid.UUID(manifest["archive_id"][9:]), "archive.core/entity\0" + name))
    proposal = tmp / "proposal"
    (proposal / "records").mkdir(parents=True)
    (proposal / "manifest.json").write_text(json.dumps({
        "protocol": "archive-admission/1", "records": ["records/entity.json"],
        "identities": [{"object_id": object_id, "namespace": "archive.core/entity", "name": name}],
        "contents": [], "expected_heads": [{"object_id": object_id, "revision_id": None}]}))
    (proposal / "records" / "entity.json").write_text(json.dumps({
        "spec": manifest["spec"], "object_id": object_id, "shape": "entity", "by": "pkg:generic/prove@0",
        "agent": manifest["operator_id"], "payload": {"kind": "person", "label": "Prove client"},
        "refs": [], "supersedes": []}))
    step(normal, "door admit --proposal <tmp>/proposal --dry-run",
         [door, "admit", "--proposal", str(proposal), "--dry-run"], root, pretty)
    step(normal, "door admit --proposal <tmp>/proposal", [door, "admit", "--proposal", str(proposal)], root, pretty)
    step(normal, "archive status", [archive, "status"], root, pretty)
    return 0


def main(argv: list[str]) -> int:
    commands = {"usage": usage, "fixture": fixture}
    if len(argv) != 1 or argv[0] not in commands:
        print(f"usage: scripts/prove.py ({' | '.join(commands)})", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="archive-prove-") as tmp:
        return commands[argv[0]](Path(tmp))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
