"""Scratch archive tool (ADR 0061).

Btrfs, systemd, and tailscale are replaced by small scripts on PATH that do
the same thing with plain copies and background processes, so the tool's own
steps, Door, World, and the relay all run for real. The source is the
published fixture archive, built through Door.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DOOR = os.environ.get("ARCHIVE_TEST_DOOR")
ARCHIVE = os.environ.get("ARCHIVE_TEST_ARCHIVE")
if os.environ.get("ARCHIVE_REQUIRE_INTEGRATION") and not (DOOR and ARCHIVE):
    raise RuntimeError("ARCHIVE_REQUIRE_INTEGRATION is set but ARCHIVE_TEST_DOOR or ARCHIVE_TEST_ARCHIVE is not")
LOGIN = "operator@example.test"
ORIGIN = "https://review.example.test:8449"

FAKE_BTRFS = """#!/bin/sh
# subvolume show|snapshot|delete, with plain copies. FAKE_BTRFS_REFUSE lists
# the subcommands to refuse as an unprivileged user would see.
[ "$1" = subvolume ] || exit 2
case " $FAKE_BTRFS_REFUSE " in
  *" $2 "*) echo "ERROR: Could not $2 $3: Operation not permitted" >&2; exit 1 ;;
esac
case "$2" in
  show) grep -qxF "$3" "$FAKE_SUBVOLUMES" || { echo "ERROR: Not a Btrfs subvolume: Invalid argument" >&2; exit 1; } ;;
  snapshot) [ $# -eq 4 ] || exit 2; cp -a "$3" "$4" && echo "$4" >> "$FAKE_SUBVOLUMES" ;;
  delete) grep -qxF "$3" "$FAKE_SUBVOLUMES" || exit 1; chmod -R u+w "$3"; rm -rf "$3" ;;
  *) exit 2 ;;
esac
"""

FAKE_STAT = """#!/bin/sh
# stat -f -c %T PATH names the file system; FAKE_STAT_FAIL refuses.
[ "$1" = -f ] || exit 2
[ -z "$FAKE_STAT_FAIL" ] || { echo "stat: cannot read file system information" >&2; exit 1; }
echo btrfs
"""

FAKE_SYSTEMD_RUN = """#!/bin/sh
# systemd-run --user --unit=U [--working-directory=D] [--setenv=K=V]... COMMAND...
set -e
shift
unit=""; dir="$PWD"
while :; do
  case "$1" in
    --unit=*) unit="${1#--unit=}"; shift ;;
    --working-directory=*) dir="${1#--working-directory=}"; shift ;;
    --setenv=*) export "${1#--setenv=}"; shift ;;
    *) break ;;
  esac
done
cd "$dir"
"$@" > "$FAKE_UNITS/$unit.log" 2>&1 &
echo $! > "$FAKE_UNITS/$unit.pid"
"""

FAKE_SYSTEMCTL = """#!/bin/sh
# systemctl --user stop UNIT
[ "$2" = stop ] || exit 2
[ -f "$FAKE_UNITS/$3.pid" ] || exit 5
kill "$(cat "$FAKE_UNITS/$3.pid")" 2>/dev/null || true
rm -f "$FAKE_UNITS/$3.pid"
"""

FAKE_TAILSCALE = """#!/bin/sh
echo "$@" >> "$FAKE_UNITS/tailscale.log"
"""


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def remove_tree(path: Path) -> None:
    for directory, _, _ in os.walk(path):
        os.chmod(directory, 0o700)
    shutil.rmtree(path, ignore_errors=True)


@unittest.skipUnless(DOOR and ARCHIVE, "set ARCHIVE_TEST_DOOR and ARCHIVE_TEST_ARCHIVE to run Door")
class ScratchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="scratch-test-"))
        bin_directory = cls.tmp / "fake-bin"
        bin_directory.mkdir()
        for name, text in (("btrfs", FAKE_BTRFS), ("systemd-run", FAKE_SYSTEMD_RUN),
                           ("systemctl", FAKE_SYSTEMCTL), ("tailscale", FAKE_TAILSCALE),
                           ("stat", FAKE_STAT)):
            (bin_directory / name).write_text(text)
            (bin_directory / name).chmod(0o755)
        cls.units = cls.tmp / "units"
        cls.units.mkdir()
        cls.subvolumes = cls.tmp / "subvolumes"
        cls.environment = {**os.environ, "PATH": f"{bin_directory}:{os.environ['PATH']}",
                           "FAKE_SUBVOLUMES": str(cls.subvolumes), "FAKE_UNITS": str(cls.units),
                           "archive_datadir": os.environ.get("archive_datadir", str(ROOT))}
        cls.source = cls.tmp / "shadow" / "archive"
        cls.source.parent.mkdir()
        built = subprocess.run([sys.executable, str(ROOT / "fixture/build.py"), str(cls.source),
                                "--door", DOOR, "--archive", ARCHIVE], capture_output=True, text=True, env=cls.environment)
        if built.returncode != 0:
            raise AssertionError(built.stderr)
        cls.subvolumes.write_text(str(cls.source.resolve()) + "\n")
        cls.build = cls.tmp / "build"
        (cls.build / "bin").mkdir(parents=True)
        os.symlink(DOOR, cls.build / "bin/door")
        os.symlink(ARCHIVE, cls.build / "bin/archive")
        cls.relay_config = cls.tmp / "shadow" / "relay.json"
        manifest = json.loads(built.stdout)
        cls.operator = manifest["operator_id"]
        cls.archive_id = manifest["archive_id"]
        cls.relay_config.write_text(json.dumps({"origins": [ORIGIN], "callers": {LOGIN: cls.operator}}))
        cls.root = cls.tmp / "scratch-root"

    @classmethod
    def tearDownClass(cls) -> None:
        for pid_file in cls.units.glob("*.pid"):
            subprocess.run(["kill", pid_file.read_text().strip()], capture_output=True)
        remove_tree(cls.tmp)

    def scratch(self, *arguments: str, **environment: str) -> tuple[int, dict]:
        completed = subprocess.run([sys.executable, str(HERE / "scratch.py"), "--root", str(self.root), *arguments],
                                   capture_output=True, text=True, env={**self.environment, **environment})
        output = completed.stdout if completed.returncode == 0 else completed.stderr
        return completed.returncode, json.loads(output)

    def create(self, name: str, port: int, **environment: str) -> dict:
        code, answer = self.scratch("create", name, "--source", str(self.source), "--port", str(port),
                                    "--build", str(self.build), "--python", sys.executable,
                                    "--relay-config", str(self.relay_config), "--publish", **environment)
        self.assertEqual(code, 0, answer)
        return answer

    def call(self, port: int, method: str, path: str, body: object = None) -> tuple[int, dict]:
        request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                         data=None if body is None else json.dumps(body).encode(),
                                         headers={"Tailscale-User-Login": LOGIN, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.loads(error.read())

    def head(self, root: Path) -> str:
        return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()

    def proposal(self, name: str) -> dict:
        import uuid
        object_id = "urn:uuid:" + str(uuid.uuid5(uuid.UUID(self.archive_id[9:]), "archive.core/entity\0" + name))
        return {"manifest": {"protocol": "archive-admission/1", "records": ["records/entity.json"],
                             "identities": [{"object_id": object_id, "namespace": "archive.core/entity", "name": name}],
                             "contents": [], "expected_heads": [{"object_id": object_id, "revision_id": None}]},
                "records": {"records/entity.json": {
                    # Door refuses a spec whose context the archive does not hold.
                    "spec": json.loads((self.source / "archive.json").read_text())["spec"],
                    "object_id": object_id, "shape": "entity", "by": "pkg:generic/scratch-test@0",
                    "agent": self.operator, "payload": {"kind": "person", "label": name}, "refs": [], "supersedes": []}}}

    def test_create_commit_reset_and_delete(self) -> None:
        source_head = self.head(self.source)
        port = free_port()
        state = self.create("trial", port)
        self.assertEqual(state["source_head"], source_head)
        status, health = self.call(port, "GET", "/health")
        self.assertEqual((status, health["archive"]["role"], health["result"]["writes"]), (200, "scratch", "commit"))
        self.assertEqual(health["archive"]["archive_id"], self.archive_id)
        status, committed = self.call(port, "POST", "/door/admit", self.proposal("scratch-commit"))
        self.assertEqual(status, 200, committed)
        self.assertEqual(committed["result"]["world"]["status"], "refreshed", committed)
        scratch_archive = self.root / "trial" / "archive"
        self.assertNotEqual(self.head(scratch_archive), source_head)
        # The source never changes.
        self.assertEqual(self.head(self.source), source_head)
        self.assertEqual(subprocess.run(["git", "-C", str(self.source), "status", "--porcelain"],
                                        capture_output=True, text=True).stdout, "")
        code, _ = self.scratch("reset", "trial")
        self.assertEqual(code, 0)
        self.assertEqual(self.head(scratch_archive), source_head)
        status, again = self.call(port, "POST", "/door/admit", self.proposal("scratch-commit"))
        self.assertEqual((status, again["result"]["door"]["retry"]), (200, False))
        code, listed = self.scratch("list")
        self.assertEqual([item["name"] for item in listed["scratch"]], ["trial"])
        code, deleted = self.scratch("delete", "trial")
        self.assertEqual((code, deleted), (0, {"deleted": "trial"}))
        self.assertFalse((self.root / "trial").exists())
        self.assertEqual(list(self.units.glob("archive-scratch-trial-*.pid")), [])
        tailscale = (self.units / "tailscale.log").read_text().splitlines()
        self.assertIn(f"serve --bg --https={port} {port}", tailscale)
        self.assertIn(f"serve --https={port} off", tailscale)
        self.assertEqual(self.head(self.source), source_head)

    def test_unprivileged_copies_are_removed_by_emptying_them(self) -> None:
        # btrfs subvolume delete is refused, as for a user without
        # user_subvol_rm_allowed; reset and delete still remove the copy.
        self.create("owner", free_port(), FAKE_BTRFS_REFUSE="delete")
        copy = self.root / "owner" / "archive"
        self.assertTrue((copy / "archive.json").is_file())
        marker = copy / "left-by-the-old-copy"
        marker.write_text("x")
        code, answer = self.scratch("reset", "owner", FAKE_BTRFS_REFUSE="delete")
        self.assertEqual(code, 0, answer)
        self.assertTrue((copy / "archive.json").is_file())
        self.assertFalse(marker.exists())
        self.assertFalse((copy / "archive").exists())
        code, answer = self.scratch("delete", "owner", FAKE_BTRFS_REFUSE="delete")
        self.assertEqual((code, answer), (0, {"deleted": "owner"}))
        self.assertFalse((self.root / "owner").exists())

    def test_a_source_that_is_not_a_subvolume_is_refused(self) -> None:
        plain = self.tmp / "plain"
        shutil.copytree(self.source, plain, symlinks=True)
        self.addCleanup(remove_tree, plain)
        cases = {
            "btrfs says so": {},
            # show refused: the file system is Btrfs but the inode is not 256.
            "the inode says so": {"FAKE_BTRFS_REFUSE": "show"},
        }
        for label, environment in cases.items():
            with self.subTest(label):
                code, answer = self.scratch("create", "plain", "--source", str(plain), "--port", str(free_port()),
                                            "--build", str(self.build), "--python", sys.executable,
                                            "--relay-config", str(self.relay_config), **environment)
                self.assertEqual(code, 1)
                self.assertIn("not a Btrfs subvolume", answer["detail"])
                self.assertFalse((self.root / "plain" / "archive").exists())
                remove_tree(self.root / "plain")

    def test_an_unanswerable_subvolume_question_fails(self) -> None:
        code, answer = self.scratch("create", "unknown", "--source", str(self.source), "--port", str(free_port()),
                                    "--build", str(self.build), "--python", sys.executable,
                                    "--relay-config", str(self.relay_config),
                                    FAKE_BTRFS_REFUSE="show", FAKE_STAT_FAIL="1")
        self.assertEqual(code, 1)
        self.assertIn("stat -f -c failed", answer["detail"])
        self.assertFalse((self.root / "unknown" / "archive").exists())
        remove_tree(self.root / "unknown")

    def test_refusals_before_anything_is_made(self) -> None:
        busy = socket.socket()
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        self.addCleanup(busy.close)
        cases = {
            "an invalid name": ("Bad_Name", free_port()),
            "a port in use": ("busy", busy.getsockname()[1]),
        }
        for label, (name, port) in cases.items():
            with self.subTest(label):
                code, _ = self.scratch("create", name, "--source", str(self.source), "--port", str(port),
                                       "--build", str(self.build), "--python", sys.executable,
                                       "--relay-config", str(self.relay_config))
                self.assertEqual(code, 1)
                self.assertFalse((self.root / name).exists())
        code, answer = self.scratch("reset", "missing")
        self.assertEqual((code, "no scratch archive named missing" in answer["detail"]), (1, True))


class SubvolumeDecisionTests(unittest.TestCase):
    """The decisions the fake file system cannot reach: a real inode 256, and
    an emptied subvolume that still cannot be removed."""

    def setUp(self) -> None:
        sys.path.insert(0, str(HERE))
        self.addCleanup(sys.path.remove, str(HERE))
        import scratch
        self.scratch = scratch

    def test_only_inode_256_on_btrfs_is_a_subvolume_root(self) -> None:
        decide = self.scratch.subvolume_from_stat
        self.assertNotEqual(decide("btrfs", 256), decide("btrfs", 257))
        self.assertEqual([decide("btrfs", 256), decide("btrfs", 257), decide("ext2/ext3", 256)], [True, False, False])

    def test_a_refused_show_falls_back_to_the_inode(self) -> None:
        from types import SimpleNamespace
        from unittest import mock
        refused = subprocess.CompletedProcess([], 1, "", "ERROR: Could not show: Operation not permitted")
        root = SimpleNamespace(stat=lambda: SimpleNamespace(st_ino=256), __str__=lambda self: "/x")
        with mock.patch.object(self.scratch.subprocess, "run", return_value=refused), \
             mock.patch.object(self.scratch, "run", return_value="btrfs\n") as asked:
            self.assertTrue(self.scratch.is_subvolume(root))
        self.assertEqual(asked.call_args.args[0][:4], ["stat", "-f", "-c", "%T"])

    def test_a_copy_that_cannot_be_removed_fails(self) -> None:
        from unittest import mock
        copy = Path(tempfile.mkdtemp(prefix="scratch-drop-"))
        self.addCleanup(remove_tree, copy)
        (copy / "inside").mkdir()
        (copy / "inside" / "file").write_text("x")
        refused = subprocess.CompletedProcess([], 1, "", "Operation not permitted")
        with mock.patch.object(self.scratch.subprocess, "run", return_value=refused), \
             mock.patch.object(Path, "rmdir", side_effect=OSError("Directory not empty")):
            with self.assertRaisesRegex(self.scratch.ScratchError, "could not remove"):
                self.scratch.drop_subvolume(copy)


if __name__ == "__main__":
    unittest.main()
