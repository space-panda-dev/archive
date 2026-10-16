"""The fixture builder and the CI server (ADR 0059, ADR 0062).

world/tests/test_conformance.py checks what the built archive holds; these
check the builder's refusals and that the CI server answers a client like a
scratch relay.
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
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOOR = os.environ.get("ARCHIVE_TEST_DOOR")
ARCHIVE = os.environ.get("ARCHIVE_TEST_ARCHIVE")
if os.environ.get("ARCHIVE_REQUIRE_INTEGRATION") and not (DOOR and ARCHIVE):
    raise RuntimeError("ARCHIVE_REQUIRE_INTEGRATION is set but ARCHIVE_TEST_DOOR or ARCHIVE_TEST_ARCHIVE is not")
needs_door = unittest.skipUnless(DOOR and ARCHIVE, "set ARCHIVE_TEST_DOOR and ARCHIVE_TEST_ARCHIVE to run Door")


def remove_tree(path: Path) -> None:
    for directory, _, _ in os.walk(path):
        os.chmod(directory, 0o700)
    shutil.rmtree(path, ignore_errors=True)


class BuilderRefusals(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="fixture-test-"))
        self.addCleanup(remove_tree, self.tmp)

    def build(self, root: Path, *extra: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(HERE / "build.py"), str(root), "--door", DOOR or "door",
                               "--archive", ARCHIVE or "archive", *extra], capture_output=True, text=True)

    def test_an_existing_root_is_never_reused(self) -> None:
        root = self.tmp / "archive"
        root.mkdir()
        built = self.build(root)
        self.assertEqual(built.returncode, 1)
        self.assertIn("already exists", json.loads(built.stderr)["detail"])
        self.assertEqual(list(root.iterdir()), [])

    def test_a_plan_of_another_protocol_is_refused(self) -> None:
        plan = self.tmp / "plan.json"
        plan.write_text(json.dumps({"protocol": "something-else", "steps": [{}]}))
        built = self.build(self.tmp / "archive", "--plan", str(plan))
        self.assertEqual(built.returncode, 1)
        self.assertFalse((self.tmp / "archive").exists())

    @needs_door
    def test_a_pin_to_a_key_not_yet_made_stops_the_build(self) -> None:
        plan = json.loads((HERE / "plan.json").read_text())
        plan["steps"] = [{"name": "early", "command": "admit", "records": [
            {"key": "x", "namespace": "archive.core/entity", "name": "x", "shape": "entity",
             "payload": {"kind": "person"}, "refs": [{"role": "work", "pin": "later"}]}]}]
        path = self.tmp / "plan.json"
        path.write_text(json.dumps(plan))
        built = self.build(self.tmp / "archive", "--plan", str(path))
        self.assertEqual(built.returncode, 1)
        self.assertIn("before it exists", json.loads(built.stderr)["detail"])


@needs_door
class ServeChecks(unittest.TestCase):
    def test_a_client_can_commit_and_read_back(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="fixture-serve-"))
        self.addCleanup(remove_tree, tmp)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        server = subprocess.Popen([sys.executable, str(HERE / "serve.py"), "--work", str(tmp / "work"),
                                   "--port", str(port), "--door", DOOR, "--archive", ARCHIVE],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (server.terminate(), server.wait(timeout=30)))
        manifest = json.loads(server.stdout.readline())
        self.assertEqual(manifest["relay"], f"http://127.0.0.1:{port}")

        def call(method: str, path: str, body: object = None) -> tuple[int, dict]:
            request = urllib.request.Request(manifest["relay"] + path, method=method,
                                             data=None if body is None else json.dumps(body).encode(),
                                             headers={"Tailscale-User-Login": manifest["login"],
                                                      "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    return response.status, json.loads(response.read())
            except urllib.error.HTTPError as error:
                with error:
                    return error.code, json.loads(error.read())
            except urllib.error.URLError:
                return 0, {}

        for _ in range(200):
            status, health = call("GET", "/health")
            if status:
                break
            time.sleep(0.1)
        self.assertEqual((status, health["archive"]["role"], health["result"]["writes"]), (200, "scratch", "commit"))
        self.assertEqual(health["archive"]["archive_id"], manifest["archive_id"])
        name = "ci-client"
        object_id = "urn:uuid:" + str(uuid.uuid5(uuid.UUID(manifest["archive_id"][9:]), "archive.core/entity\0" + name))
        proposal = {"manifest": {"protocol": "archive-admission/1", "records": ["records/entity.json"],
                                 "identities": [{"object_id": object_id, "namespace": "archive.core/entity", "name": name}],
                                 "contents": [], "expected_heads": [{"object_id": object_id, "revision_id": None}]},
                    "records": {"records/entity.json": {
                        "spec": manifest["spec"], "object_id": object_id, "shape": "entity", "by": "pkg:generic/ci-client@0",
                        "agent": manifest["agent"], "payload": {"kind": "person", "label": "CI client"},
                        "refs": [], "supersedes": []}}}
        status, committed = call("POST", "/door/admit", proposal)
        self.assertEqual(status, 200, committed)
        [revision] = committed["result"]["door"]["revision_ids"]
        status, read = call("POST", "/world/query", {
            "query": f"ASK {{ GRAPH ?g {{ <{manifest['archive_id']}#revision={revision}> ?p ?o }} }}",
            "projection": health["archive"]["projections"][-1]})
        self.assertEqual((status, read["result"]["data"]["boolean"]), (200, True))
        # The fixture's own objects are there to build on.
        work = manifest["objects"]["work-a"]
        status, found = call("POST", "/world/query", {
            "query": f"ASK {{ GRAPH ?g {{ ?r <urn:uuid:3e8b29f8-70c9-403e-bb4c-2280d59eca7f#object_id> <{work['object_id']}> }} }}",
            "projection": health["archive"]["projections"][-1]})
        self.assertEqual((status, found["result"]["data"]["boolean"]), (200, True))
        # Stopping the server stops its Door service, which removes its socket.
        door_socket = tmp / "work" / "run" / "door.sock"
        self.assertTrue(door_socket.exists())
        server.terminate()
        self.assertEqual(server.wait(timeout=30), 0)
        self.assertFalse(door_socket.exists())


if __name__ == "__main__":
    unittest.main()
