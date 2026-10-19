"""Documents staged through the relay and admitted through Door (ADR 0085).

A fresh archive, the real `door serve`, and a relay in commit mode on a
scratch role: the operator stages a lyric file, admits it with its
document source claim, and reads its bytes back. Without Door and Archive
the tests are skipped and say so, unless ARCHIVE_REQUIRE_INTEGRATION is set.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "relay"))

import relay  # noqa: E402
import staging  # noqa: E402
from test_circle import Client, remove_tree  # noqa: E402
from test_relay import ARCHIVE, DOOR, RelayHarness, private  # noqa: E402

needs_door = unittest.skipUnless(DOOR and ARCHIVE, "set ARCHIVE_TEST_DOOR and ARCHIVE_TEST_ARCHIVE")
LYRICS = "First line of a song\nSecond line of it\n".encode()


@needs_door
class StagingChecks(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="relay-documents-"))
        self.addCleanup(remove_tree, self.tmp)
        for variable, program in (("ARCHIVE_GIT_EXECUTABLE", "git"), ("ARCHIVE_GIT_ANNEX_EXECUTABLE", "git-annex")):
            os.environ.setdefault(variable, os.path.realpath(shutil.which(program)))
        os.environ.setdefault("archive_datadir", str(ROOT))
        self.root = self.tmp / "archive"
        initialized = subprocess.run([ARCHIVE, "init", "--operator", "alice", str(self.root)],
                                     capture_output=True, text=True)
        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        self.archive_id = json.loads(initialized.stdout)["archive_id"]
        self.spec = json.loads((self.root / "archive.json").read_text())["spec"]
        self.store = self.tmp / "world-store"
        relay.world.refresh_served(self.root, self.store, archive_command=ARCHIVE)
        self.operator = relay.access.load(self.store, relay.world.PROJECTION_VERSION, "-", None,
                                          date.today()).operator
        self.assertIsNotNone(self.operator)
        self.socket = private(self.tmp / "door") / "door.sock"
        door = subprocess.Popen([DOOR, "serve", "--socket", str(self.socket)], cwd=self.root,
                                stderr=subprocess.DEVNULL)
        self.addCleanup(door.wait, 30)
        self.addCleanup(door.terminate)
        for _ in range(200):
            if self.socket.exists():
                break
            time.sleep(0.05)
        self.harness = RelayHarness(self.tmp, archive_root=self.root, door_socket=self.socket,
                                    world_store=self.store, archive_command=ARCHIVE, writes="commit",
                                    role="scratch", callers={"operator@example.test": self.operator})
        self.addCleanup(self.harness.close)
        self.call = Client(self, self.harness)
        self.installed = {}
        for path in (self.root / "contracts").glob("sha256-*.json"):
            bundle = json.loads(path.read_bytes())
            self.installed[(bundle["id"], bundle["version"])] = "sha256:" + path.stem.removeprefix("sha256-")

    def request(self, method: str, path: str, body: object = None):
        return self.call(method, path, body, login="operator@example.test")

    def stage(self, data: bytes, media_type: str = "text/plain", **extra) -> tuple[int, dict]:
        status, _, answer = self.request("POST", "/documents/stage", {
            "media_type": media_type, "content": base64.b64encode(data).decode(), **extra})
        return status, answer

    def object_id(self, namespace: str, name: str) -> str:
        return "urn:uuid:" + str(uuid.uuid5(uuid.UUID(self.archive_id[9:]), namespace + "\0" + name))

    def admission(self, row: dict, media_type: str = "text/plain") -> dict:
        artifact = self.object_id("archive.core/artifact", "relay-lyrics")
        source = self.object_id("archive.claim/document_source", "relay-lyrics-source")
        common = {"spec": self.spec, "by": "pkg:generic/archive-relay-test@0", "agent": self.operator,
                  "supersedes": []}
        artifact_draft = {**common, "object_id": artifact, "shape": "artifact", "refs": [],
                          "payload": {"content": {"digest": row["digest"], "size": row["size"]}}}
        revision = relay.access.revision_of(artifact_draft)
        contract = ("archive.claim/document_source", "1")
        source_draft = {**common, "object_id": source, "shape": "claim",
                        "payload": {"media_type": media_type, "kinds": ["lyrics"], "filename": "lyrics.txt"},
                        "refs": [{"role": "document", "id": artifact, "revision_id": revision}],
                        "contracts": [{"id": contract[0], "version": contract[1], "digest": self.installed[contract]}]}
        return {"manifest": {"protocol": "archive-admission/1", "records": ["records/a.json", "records/b.json"],
                             "identities": [
                                 {"object_id": artifact, "namespace": "archive.core/artifact", "name": "relay-lyrics"},
                                 {"object_id": source, "namespace": "archive.claim/document_source",
                                  "name": "relay-lyrics-source"}],
                             "contents": [row],
                             "expected_heads": [{"object_id": artifact, "revision_id": None},
                                                {"object_id": source, "revision_id": None}]},
                "records": {"records/a.json": artifact_draft, "records/b.json": source_draft}}

    def test_a_staged_document_is_admitted_and_its_bytes_are_served(self) -> None:
        status, answer = self.stage(LYRICS, "text/plain; charset=utf-8", filename="lyrics.txt")
        self.assertEqual(status, 200, answer)
        row = {key: answer["result"][key] for key in ("digest", "size", "annex_key", "staging_remote")}
        digest = "sha256:" + hashlib.sha256(LYRICS).hexdigest()
        self.assertEqual((row["digest"], answer["result"]["media_type"], answer["result"]["artifacts"]),
                         (digest, "text/plain", []))
        # Described as another type, or not staged at all, it is not relayed.
        status, _, refused = self.request("POST", "/door/admit-dry-run", self.admission(row, "text/markdown"))
        self.assertEqual((status, refused["error"]["code"]), (422, "content_not_described"))
        other = {**row, "digest": "sha256:" + "5" * 64}
        status, _, refused = self.request("POST", "/door/admit-dry-run", self.admission(other))
        self.assertEqual((status, refused["error"]["code"]), (422, "content_not_relayed"))
        status, _, admitted = self.request("POST", "/door/admit", self.admission(row))
        self.assertEqual(status, 200, admitted)
        self.assertEqual((admitted["result"]["door"]["status"], admitted["result"]["world"]["status"]),
                         ("committed", "refreshed"))
        status, headers, body = self.request("GET", f"/documents/{digest}")
        self.assertEqual((status, headers["Content-Type"], body), (200, "text/plain", LYRICS))
        # Staged again, the answer names the Artifact now carrying the bytes.
        status, answer = self.stage(LYRICS)
        self.assertEqual(answer["result"]["artifacts"], [self.object_id("archive.core/artifact", "relay-lyrics")])

    def test_staging_refusals(self) -> None:
        wave = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 32
        for data, media_type, expected in (
                (wave, "text/plain", (422, "content_audio_or_video")),
                (b"lyrics", "audio/mpeg", (422, "content_audio_or_video")),
                (b"lyrics", "application/zip", (415, "media_type_unsupported")),
                (b"not\x00text", "text/plain", (422, "content_mismatch")),
                (b"%not a pdf", "application/pdf", (422, "content_mismatch"))):
            with self.subTest(expected[1], media_type=media_type):
                status, answer = self.stage(data, media_type)
                self.assertEqual((status, answer["error"]["code"]), expected)
        status, _, answer = self.request("POST", "/documents/stage", {"media_type": "text/plain", "content": "%%"})
        self.assertEqual((status, answer["error"]["code"]), (400, "request_malformed"))
        self.assertFalse((self.harness.config.staging / "documents").exists())

    def test_what_was_staged_over_seven_days_ago_is_removed(self) -> None:
        status, answer = self.stage(LYRICS)
        self.assertEqual(status, 200, answer)
        documents = self.harness.relay.documents
        digest = answer["result"]["digest"]
        self.assertIn(digest, documents.staged_for(self.operator))
        self.assertEqual(documents.prune(time.time() + 6 * 24 * 3600), [])
        self.assertEqual(documents.prune(time.time() + staging.KEEP_SECONDS + 60), [digest])
        self.assertEqual(list(documents.repository.glob(digest.removeprefix("sha256:") + "*")), [])
        self.assertEqual(list(documents.index.glob("*.json")), [])


if __name__ == "__main__":
    unittest.main()
