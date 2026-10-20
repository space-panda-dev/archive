"""Relay tests (ADR 0056, ADR 0060).

Every answer the relay gives in these tests is validated against
relay/openapi.json, so the relay cannot drift from its published description
without a test failing. The Door tests run the real `door serve` and
`archive` executables named by ARCHIVE_TEST_DOOR and ARCHIVE_TEST_ARCHIVE (for
example from `cabal list-bin`); without them those tests are skipped and say
so, unless ARCHIVE_REQUIRE_INTEGRATION is set, when they fail.
"""

from __future__ import annotations

import http.client
import io
import gzip
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

import jsonschema
from pyoxigraph import RdfFormat, parse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "relay"))

import relay  # noqa: E402
import world  # noqa: E402

BOOTSTRAP_EXPORT = ROOT / "world/tests/fixtures/bootstrap-export.json"
# One archive at two snapshots, captured from Door, for reads across a refresh.
CHAIN_CUT_EXPORT = ROOT / "world/tests/fixtures/door-world-chain-cut-export.json"
CHAIN_EXPORT = ROOT / "world/tests/fixtures/door-world-chain-export.json"
OPENAPI = json.loads((ROOT / "relay/openapi.json").read_text())
SERVICE_SCHEMA = json.loads((ROOT / "door/service.schema.json").read_text())
LOGIN = "operator@example.test"
AGENT = "urn:uuid:00000000-0000-4000-8000-000000000001"
OTHER = "urn:uuid:00000000-0000-4000-8000-000000000002"
THIRD = "urn:uuid:00000000-0000-4000-8000-000000000003"
ARCHIVE_ID = "urn:uuid:00000000-0000-4000-8000-0000000000aa"
ORIGIN = "https://review.example.test:8449"
DOOR = os.environ.get("ARCHIVE_TEST_DOOR")
ARCHIVE = os.environ.get("ARCHIVE_TEST_ARCHIVE")
if os.environ.get("ARCHIVE_REQUIRE_INTEGRATION") and not (DOOR and ARCHIVE):
    raise RuntimeError("ARCHIVE_REQUIRE_INTEGRATION is set but ARCHIVE_TEST_DOOR or ARCHIVE_TEST_ARCHIVE is not")
needs_door = unittest.skipUnless(DOOR and ARCHIVE, "set ARCHIVE_TEST_DOOR and ARCHIVE_TEST_ARCHIVE to run Door through the relay")


def private(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def fake_archive(path: Path) -> Path:
    """An archive root holding only its manifest, for tests that never reach Door."""
    private(path)
    (path / "archive.json").write_text(json.dumps({"archive_id": ARCHIVE_ID, "layout": "archive/2", "spec": "archive/0.0.6"}))
    return path


def described(path: str, method: str, status: int) -> dict:
    """The schema relay/openapi.json gives this answer, resolvable on its own."""
    operation = OPENAPI["paths"].get(path, {}).get(method.lower())
    if operation is None:
        # An unknown route or method is still answered with a described failure.
        operation = next(op for item in OPENAPI["paths"].values() for op in item.values())
    response = operation["responses"].get(str(status))
    if response is None:
        raise AssertionError(f"relay/openapi.json describes no {status} answer for {method} {path}")
    return {**response["content"]["application/json"]["schema"], "components": OPENAPI["components"]}


def validate_service(value: dict) -> None:
    jsonschema.Draft202012Validator(SERVICE_SCHEMA).validate(value)


class RelayHarness:
    """A relay on an ephemeral loopback port, configured like the deployment."""

    def __init__(self, tmp: Path, archive_root: Path, door_socket: Path, world_store: Path, writes: str = "dry-run",
                 role: str = "shadow", archive_command: str = "archive", query_timeout_seconds: float = 30,
                 max_rows: int = 10000, query_workers: int = 3, cache_bytes: int = 64 * 1024 * 1024,
                 callers: dict[str, str] | None = None, media_root: Path | None = None, **more):
        self.config = relay.Config(
            host="127.0.0.1", port=0, archive_root=archive_root, door_socket=door_socket,
            world_store=world_store, archive_command=archive_command, staging=private(tmp / "staging"),
            writes=writes, role=role, origins=frozenset([ORIGIN]), callers=callers or {LOGIN: AGENT},
            query_timeout_seconds=query_timeout_seconds, max_rows=max_rows, query_workers=query_workers,
            cache_bytes=cache_bytes, media_root=media_root, **more)
        self.relay = relay.Relay(self.config)
        # The server the relay itself runs, so tests see its threading.
        self.server = relay.make_server(self.relay, "127.0.0.1", 0)
        self.server.RequestHandlerClass.log_message = lambda *args: None
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.relay.stop()

    def call(self, method: str, path: str, body: object = None, login: str | None = LOGIN,
             origin: str | None = None, content_type: str = "application/json",
             raw: bytes | None = None, accept_encoding: str | None = None) -> tuple[int, dict, object]:
        headers = {}
        if accept_encoding is not None:
            headers["Accept-Encoding"] = accept_encoding
        if login is not None:
            headers["Tailscale-User-Login"] = login
        if origin is not None:
            headers["Origin"] = origin
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        if data is not None:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                status, headers, payload = response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            with error:
                status, headers, payload = error.code, dict(error.headers), error.read()
        if headers.get("Content-Encoding") == "gzip":
            payload = gzip.decompress(payload)
        answer = json.loads(payload) if payload else None
        if method != "OPTIONS":
            jsonschema.Draft202012Validator(described(path, method, status)).validate(answer)
            if answer.get("ok") and path.startswith("/door/"):
                validate_service({"ok": True, "result": answer["result"]["door"]})
        return status, headers, answer


def allocated(archive_id: str, namespace: str, name: str) -> str:
    return "urn:uuid:" + str(uuid.uuid5(uuid.UUID(archive_id[9:]), namespace + "\0" + name))


def entity_proposal(archive_id: str, name: str, agent: str = AGENT, spec: str = "archive/0.0.6") -> dict:
    object_id = allocated(archive_id, "archive.core/entity", name)
    return {
        "manifest": {
            "protocol": "archive-admission/1",
            "records": ["records/entity.json"],
            "identities": [{"object_id": object_id, "namespace": "archive.core/entity", "name": name}],
            "contents": [],
            "expected_heads": [{"object_id": object_id, "revision_id": None}],
        },
        "records": {"records/entity.json": {
            "spec": spec, "object_id": object_id, "shape": "entity",
            "by": "pkg:generic/archive-relay-test@0", "agent": agent,
            "payload": {"kind": "person", "label": name}, "refs": [], "supersedes": [],
        }},
    }


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="relay-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def harness(self, **options) -> RelayHarness:
        options.setdefault("archive_root", fake_archive(self.tmp / "no-archive"))
        options.setdefault("door_socket", self.tmp / "no-door" / "door.sock")
        options.setdefault("world_store", self.tmp / "no-world")
        harness = RelayHarness(self.tmp, **options)
        self.addCleanup(harness.close)
        return harness


class DescriptionChecks(unittest.TestCase):
    """relay/openapi.json describes exactly what the relay serves."""

    def test_routes_and_methods_are_the_described_ones(self) -> None:
        described_routes = {path: [method.upper() for method in item] for path, item in OPENAPI["paths"].items()}
        self.assertEqual(described_routes, {path: [method] for path, method in relay.ROUTES.items()})

    def test_codes_are_the_relays_and_doors(self) -> None:
        documented = [option["const"] for option in OPENAPI["components"]["schemas"]["RefusalCode"]["oneOf"]]
        self.assertEqual(sorted(documented), sorted(set(relay.RELAY_CODES) | set(relay.DOOR_STATUSES)))
        door_codes = {option["const"] for option in SERVICE_SCHEMA["$defs"]["refusal_code"]["oneOf"]}
        self.assertEqual(set(relay.DOOR_STATUSES), door_codes)

    def test_every_status_a_code_answers_is_described(self) -> None:
        statuses = {str(status.value) for status in [*relay.RELAY_CODES.values(), *relay.DOOR_STATUSES.values()]}
        described_statuses = {status for item in OPENAPI["paths"].values() for op in item.values()
                              for status in op["responses"]}
        self.assertEqual(statuses - described_statuses, set())

    def test_each_described_code_names_its_status(self) -> None:
        for option in OPENAPI["components"]["schemas"]["RefusalCode"]["oneOf"]:
            code = option["const"]
            with self.subTest(code=code):
                if code in relay.RELAY_CODES:
                    self.assertIn(f"Relay ({relay.RELAY_CODES[code].value})", option["description"])
                if code in relay.DOOR_STATUSES:
                    self.assertIn(f"Door ({relay.DOOR_STATUSES[code].value})", option["description"])


class CallerChecks(Fixture):
    """Identity, origin, and request-shape refusals happen before Door or World."""

    def test_health_names_the_archive_role_and_what_does_not_answer(self) -> None:
        status, _, answer = self.harness().call("GET", "/health")
        self.assertEqual(status, 503)
        self.assertEqual(answer["archive"], {"archive_id": ARCHIVE_ID, "spec": "archive/0.0.6", "role": "shadow",
                                             "projections": [], "writes": "dry-run",
                                             "caller": {"agent": AGENT, "capacity": "operator"}})
        self.assertEqual((answer["ok"], answer["result"]["healthy"], answer["result"]["door"],
                          answer["result"]["world"]["status"]), (False, False, "unreachable", "unavailable"))

    def test_answers_report_the_specification_the_manifest_names_now(self) -> None:
        # A specification upgrade replaces the manifest while the relay runs.
        harness = self.harness()
        _, _, before = harness.call("GET", "/health")
        manifest = self.tmp / "no-archive" / "archive.json"
        manifest.write_text(json.dumps({"archive_id": ARCHIVE_ID, "layout": "archive/2", "spec": "archive/0.0.7"}))
        _, _, after = harness.call("GET", "/health")
        self.assertEqual((before["archive"]["spec"], after["archive"]["spec"]), ("archive/0.0.6", "archive/0.0.7"))

    def test_a_manifest_naming_another_archive_is_an_internal_error(self) -> None:
        # An upgrade never changes the identifier, so another one means the
        # root now holds a different archive; the relay names neither.
        harness = self.harness()
        other = "urn:uuid:" + str(uuid.uuid4())
        self.assertNotEqual(other, ARCHIVE_ID)
        manifest = self.tmp / "no-archive" / "archive.json"
        manifest.write_text(json.dumps({"archive_id": other, "layout": "archive/2", "spec": "archive/0.0.7"}))
        logged = io.StringIO()
        with redirect_stderr(logged):
            status, _, answer = harness.call("GET", "/health")
        self.assertEqual((status, answer["error"]["code"]), (500, "internal_error"))
        self.assertNotIn(other, json.dumps(answer))
        self.assertIn("names another archive", logged.getvalue())

    def test_a_request_without_an_allowed_login_is_refused_without_archive_details(self) -> None:
        harness = self.harness()
        for login in (None, "someone@example.test", ""):
            with self.subTest(login=login):
                status, _, answer = harness.call("GET", "/health", login=login)
                self.assertEqual((status, answer["error"]["code"]), (403, "forbidden_identity"))
                self.assertNotIn("archive", answer)

    def test_origins_must_be_listed(self) -> None:
        harness = self.harness()
        status, headers, answer = harness.call("GET", "/health", origin="https://elsewhere.example.test")
        self.assertEqual((status, answer["error"]["code"]), (403, "forbidden_origin"))
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        status, headers, _ = harness.call("GET", "/health", origin=ORIGIN)
        self.assertEqual(headers["Access-Control-Allow-Origin"], ORIGIN)

    def test_preflight_is_answered_only_for_listed_origins_and_callers(self) -> None:
        harness = self.harness()
        status, headers, _ = harness.call("OPTIONS", "/door/admit-dry-run", origin=ORIGIN)
        self.assertEqual(status, 204)
        self.assertEqual(headers["Access-Control-Allow-Origin"], ORIGIN)
        self.assertIn("Content-Type", headers["Access-Control-Allow-Headers"])
        self.assertEqual(harness.call("OPTIONS", "/door/admit", origin="https://elsewhere.example.test")[0], 403)
        self.assertEqual(harness.call("OPTIONS", "/door/admit", origin=ORIGIN, login="someone@example.test")[0], 403)

    def test_unknown_routes_and_wrong_methods(self) -> None:
        harness = self.harness()
        status, _, answer = harness.call("GET", "/nowhere")
        self.assertEqual((status, answer["error"]["code"]), (404, "not_found"))
        status, headers, answer = harness.call("GET", "/world/query")
        self.assertEqual((status, answer["error"]["code"], headers["Allow"]), (405, "method_not_allowed", "POST"))
        status, headers, answer = harness.call("DELETE", "/health")
        self.assertEqual((status, headers["Allow"]), (405, "GET"))

    def test_writes_and_queries_must_be_json(self) -> None:
        harness = self.harness()
        status, _, answer = harness.call("POST", "/world/query", {"query": "ASK {}", "projection": 6}, content_type="text/plain")
        self.assertEqual((status, answer["error"]["code"]), (415, "unsupported_media_type"))
        status, _, answer = harness.call("POST", "/world/query", raw=b"{not json")
        self.assertEqual((status, answer["error"]["code"]), (400, "request_malformed"))
        # The relay answers from the declared length, before reading any body.
        for length, expected in ((str(relay.MAX_BODY + 1), 413), ("-1", 400), ("many", 400)):
            with self.subTest(length=length):
                connection = http.client.HTTPConnection("127.0.0.1", harness.server.server_address[1], timeout=30)
                connection.putrequest("POST", "/world/query")
                for header, value in (("Tailscale-User-Login", LOGIN), ("Content-Type", "application/json"),
                                      ("Content-Length", length)):
                    connection.putheader(header, value)
                connection.endheaders()
                response = connection.getresponse()
                self.assertEqual(response.status, expected)
                json.loads(response.read())
                connection.close()

    def test_a_malformed_request_line_is_answered(self) -> None:
        harness = self.harness()
        with socket.create_connection(("127.0.0.1", harness.server.server_address[1]), timeout=30) as connection:
            connection.sendall(b"GARBAGE\r\n\r\n")
            reply = connection.recv(65536)
        # A line with no HTTP version is answered as HTTP/0.9: the body alone.
        body = reply.split(b"\r\n\r\n", 1)[1] if reply.startswith(b"HTTP/") else reply
        self.assertEqual(json.loads(body)["error"]["code"], "request_malformed")

    def test_logs_name_the_agent_never_the_login(self) -> None:
        handler = relay.make_handler(self.harness().relay)
        instance = handler.__new__(handler)
        instance.headers = {"Tailscale-User-Login": LOGIN}
        logged = io.StringIO()
        with redirect_stderr(logged):
            instance.log_message("%s", "GET /health 200")
            del instance.headers
            instance.log_message("%s", "malformed")
        self.assertIn(AGENT, logged.getvalue())
        self.assertNotIn(LOGIN, logged.getvalue())

    def test_an_unexpected_failure_is_answered_as_internal_error(self) -> None:
        harness = self.harness()
        with patch.object(harness.relay, "health", side_effect=KeyError("boom")), redirect_stderr(io.StringIO()):
            status, _, answer = harness.call("GET", "/health")
        self.assertEqual((status, answer["error"]["code"]), (500, "internal_error"))

    def test_a_draft_naming_another_agent_never_reaches_door(self) -> None:
        # The configured socket does not exist: reaching Door would answer 503.
        harness = self.harness()
        proposal = entity_proposal("urn:uuid:" + str(uuid.uuid4()), "someone-else",
                                   agent="urn:uuid:00000000-0000-4000-8000-0000000000ff")
        status, _, answer = harness.call("POST", "/door/admit-dry-run", proposal)
        self.assertEqual((status, answer["error"]["code"]), (403, "agent_mismatch"))
        self.assertEqual(list(harness.config.staging.iterdir()), [])

    def test_the_unreachable_socket_is_reported(self) -> None:
        # Anchors the test above: a well-formed proposal does reach the socket.
        harness = self.harness()
        status, _, answer = harness.call("POST", "/door/admit-dry-run",
                                         entity_proposal("urn:uuid:" + str(uuid.uuid4()), "anyone"))
        self.assertEqual((status, answer["error"]["code"]), (503, "door_unreachable"))
        self.assertEqual(list(harness.config.staging.iterdir()), [])

    def test_content_bearing_admissions_are_refused(self) -> None:
        proposal = entity_proposal("urn:uuid:" + str(uuid.uuid4()), "with-content")
        proposal["manifest"]["contents"] = [{"digest": "sha256:" + "0" * 64, "size": 0,
                                             "annex_key": "SHA256E-s0--" + "0" * 64, "staging_remote": "laptop"}]
        status, _, answer = self.harness().call("POST", "/door/admit-dry-run", proposal)
        self.assertEqual((status, answer["error"]["code"]), (422, "content_not_relayed"))

    def test_proposal_shape_is_checked(self) -> None:
        harness = self.harness()
        good = entity_proposal("urn:uuid:" + str(uuid.uuid4()), "shape")
        cases = {
            "extra field": {**good, "extra": 1},
            "unnamed draft": {**good, "records": {**good["records"], "records/other.json": {}}},
            "escaping path": {"manifest": {**good["manifest"], "records": ["../entity.json"]},
                              "records": {"../entity.json": good["records"]["records/entity.json"]}},
            "unhashable path": {"manifest": {**good["manifest"], "records": [["records/entity.json"]]},
                                "records": good["records"]},
            "wrong protocol": {"manifest": {**good["manifest"], "protocol": "archive-transition/1"},
                               "records": good["records"]},
        }
        for name, body in cases.items():
            with self.subTest(name):
                status, _, answer = harness.call("POST", "/door/admit-dry-run", body)
                self.assertEqual((status, answer["error"]["code"]), (400, "request_malformed"))

    def test_commits_are_refused_in_dry_run_mode(self) -> None:
        harness = self.harness()
        for path in ("/door/admit", "/door/transition"):
            with self.subTest(path):
                status, _, answer = harness.call("POST", path, {"manifest": {}, "records": {}})
                self.assertEqual((status, answer["error"]["code"]), (403, "commits_disabled"))


class FakeDoor:
    """A Door socket that accepts a request and then misbehaves."""

    def __init__(self, path: Path, reply: bytes):
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(str(path))
        self.listener.listen(4)
        self.reply = reply
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self) -> None:
        while True:
            try:
                connection, _ = self.listener.accept()
            except OSError:
                return
            with connection:
                connection.recv(65536)
                connection.sendall(self.reply)

    def close(self) -> None:
        self.listener.close()


class DoorAnswerChecks(Fixture):
    """What the relay says when Door's service fails to answer."""

    def door(self, reply: bytes) -> Path:
        path = private(self.tmp / "fake-door") / "door.sock"
        fake = FakeDoor(path, reply)
        self.addCleanup(fake.close)
        return path

    def test_no_answer_to_a_commit_is_an_uncertain_outcome_and_to_a_dry_run_a_failure(self) -> None:
        socket_path = self.door(b"")
        harness = self.harness(door_socket=socket_path, writes="commit", role="scratch")
        proposal = entity_proposal(ARCHIVE_ID, "silent")
        status, _, answer = harness.call("POST", "/door/admit", proposal)
        self.assertEqual((status, answer["error"]["code"]), (503, "outcome_uncertain"))
        status, _, answer = harness.call("POST", "/door/admit-dry-run", proposal)
        self.assertEqual((status, answer["error"]["code"]), (502, "door_failed"))

    def test_an_uncoded_door_refusal_is_not_passed_off_as_a_code(self) -> None:
        socket_path = self.door(b'{"ok":false,"error":"plain text"}\n')
        status, _, answer = self.harness(door_socket=socket_path).call(
            "POST", "/door/admit-dry-run", entity_proposal(ARCHIVE_ID, "old-door"))
        self.assertEqual((status, answer["error"]["code"]), (502, "door_failed"))

    def test_a_coded_door_refusal_keeps_its_code_and_fields(self) -> None:
        socket_path = self.door(json.dumps({"ok": False, "error": {
            "code": "io_failure", "message": "disk", "unadmitted_keys": ["K"]}}).encode() + b"\n")
        status, _, answer = self.harness(door_socket=socket_path).call(
            "POST", "/door/admit-dry-run", entity_proposal(ARCHIVE_ID, "coded"))
        self.assertEqual((status, answer["error"]), (500, {"code": "io_failure", "message": "disk", "unadmitted_keys": ["K"]}))


class ConfigChecks(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="relay-config-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.base = {
            "listen": {"host": "127.0.0.1", "port": 8450}, "archive_root": "/a", "door_socket": "/s/door.sock",
            "world_store": "/w", "staging": "/st", "writes": "dry-run", "role": "shadow",
            "origins": [ORIGIN], "callers": {LOGIN: AGENT},
        }

    def load(self, **changes) -> relay.Config:
        path = self.tmp / "relay.json"
        path.write_text(json.dumps({**self.base, **changes}))
        return relay.load_config(path)

    def test_a_valid_configuration_loads(self) -> None:
        self.assertEqual((self.load().callers, self.load().role), ({LOGIN: AGENT}, "shadow"))
        self.assertEqual(self.load(role="scratch", writes="commit").writes, "commit")
        self.assertFalse(self.load().bank_scheduler)
        self.assertTrue(self.load(bank_root="/b", bank_scheduler=True).bank_scheduler)

    def test_refusals(self) -> None:
        for name, changes in {
            "tailnet address": {"listen": {"host": "192.0.2.10", "port": 8450}},
            "all interfaces": {"listen": {"host": "0.0.0.0", "port": 8450}},
            "unknown write mode": {"writes": "on"},
            "no role": {"role": None},
            "unknown role": {"role": "staging"},
            "a committing shadow": {"writes": "commit"},
            "plain http origin": {"origins": ["http://review.example.test"]},
            "no callers": {"callers": {}},
            "caller without an agent": {"callers": {LOGIN: "not-an-agent"}},
            "two agents and no circle": {"callers": {LOGIN: AGENT, "other@example.test": OTHER}},
            "a member who is no caller": {"callers": {LOGIN: AGENT, "other@example.test": OTHER},
                                         "circle": {"destination": "band", "members": [OTHER, THIRD]}},
            "a circle with no members": {"circle": {"destination": "band", "members": []}},
            "an unknown practice feature": {"bank_root": "/b", "practice_features": ["stems/levels"]},
            "practice features without a bank": {"practice_features": ["stems/bass"]},
            "a scheduler without a bank": {"bank_scheduler": True},
            "a scheduler that is not a flag": {"bank_root": "/b", "bank_scheduler": "yes"},
            "a renderer named by a relative path": {"renderers": {"practice": {"version": "1",
                                                                              "command": ["render"]}}},
            "an exporter without a version": {"exporters": {"lead-sheet": {"command": ["/bin/true"]}}},
            "a zero timeout": {"render_timeout_seconds": 0},
            "unknown field": {"funnel": True},
        }.items():
            with self.subTest(name), self.assertRaises(ValueError):
                self.load(**changes)

    def test_a_circle_names_the_operator_by_who_is_not_a_member(self) -> None:
        config = self.load(callers={LOGIN: AGENT, "other@example.test": OTHER},
                           circle={"destination": "band", "members": [OTHER]})
        self.assertEqual(relay.access.check_config(config.callers, config.circle)[0], AGENT)
        # Anchor: without the circle the same callers are refused.
        with self.assertRaises(ValueError):
            self.load(callers={LOGIN: AGENT, "other@example.test": OTHER})

    def test_practice_features_installed_renderers_and_exporters_load(self) -> None:
        config = self.load(bank_root="/b", practice_features=["stems/bass", "stems/drums"],
                           renderers={"practice-test": {"version": "1", "command": ["/usr/bin/env", "true"]}},
                           exporters={"lead-sheet": {"version": "1.2", "command": ["/usr/bin/env", "true"]}})
        self.assertEqual((config.practice_features, set(config.renderers), set(config.exporters)),
                         (("stems/bass", "stems/drums"), {"practice-test"}, {"lead-sheet"}))

    def test_the_derived_media_root_is_optional(self) -> None:
        # The deployed relay's configuration predates media and must still load.
        self.assertIsNone(self.load().media_root)
        self.assertEqual(self.load(media_root="/m").media_root, Path("/m"))
        for value in (None, 7, ["/m"]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.load(media_root=value)

    def test_staging_must_be_private(self) -> None:
        staging = self.tmp / "staging"
        staging.mkdir(mode=0o755)
        staging.chmod(0o755)
        with self.assertRaises(ValueError):
            relay.private_directory(staging)
        staging.chmod(0o700)
        relay.private_directory(staging)

    def test_the_archive_manifest_must_name_the_archive(self) -> None:
        root = fake_archive(self.tmp / "archive")
        self.assertEqual(relay.read_archive_manifest(root), (ARCHIVE_ID, "archive/0.0.6"))
        (root / "archive.json").write_text("{}")
        with self.assertRaises(ValueError):
            relay.read_archive_manifest(root)


class QueryChecks(Fixture):
    def setUp(self) -> None:
        super().setUp()
        self.archive_root = fake_archive(self.tmp / "archive")
        self.store = self.tmp / "world-store"
        world.rebuild_store(self.archive_root, self.store, export_file=BOOTSTRAP_EXPORT)
        self.version = world.PROJECTION_VERSION

    def ask(self, harness: RelayHarness, query: str, **extra) -> tuple[int, dict]:
        status, _, answer = harness.call("POST", "/world/query", {"query": query, "projection": self.version, **extra})
        return status, answer

    def test_select_answers_standard_results_from_the_selected_generation(self) -> None:
        harness = self.harness(archive_root=self.archive_root, world_store=self.store)
        status, answer = self.ask(harness, "SELECT (COUNT(*) AS ?n) WHERE { GRAPH ?g { ?s ?p ?o } }")
        self.assertEqual(status, 200)
        expected = world._read_current(self.store)
        result = answer["result"]
        self.assertEqual((result["snapshot"], result["projection"], result["media_type"]),
                         (expected.snapshot, self.version, "application/sparql-results+json"))
        [row] = result["data"]["results"]["bindings"]
        self.assertEqual(row["n"]["datatype"], "http://www.w3.org/2001/XMLSchema#integer")
        self.assertGreater(int(row["n"]["value"]), 0)
        self.assertEqual(answer["archive"]["projections"], [self.version])

    def test_construct_answers_n_triples(self) -> None:
        harness = self.harness(archive_root=self.archive_root, world_store=self.store)
        status, answer = self.ask(harness, "CONSTRUCT { ?s ?p ?o } WHERE { GRAPH ?g { ?s ?p ?o } } LIMIT 3")
        self.assertEqual((status, answer["result"]["media_type"]), (200, "application/n-triples"))
        self.assertEqual(len(answer["result"]["data"].strip().splitlines()), 3)

    def test_construct_answers_the_format_asked_for(self) -> None:
        harness = self.harness(archive_root=self.archive_root, world_store=self.store)
        text = "CONSTRUCT { ?s ?p ?o } WHERE { GRAPH ?g { ?s ?p ?o } } ORDER BY ?s ?p ?o LIMIT 3"
        _, baseline = self.ask(harness, text)
        expected = set(parse(baseline["result"]["data"].encode(), format=RdfFormat.N_TRIPLES))
        self.assertEqual(len(expected), 3)
        for media_type, rdf_format in (("text/turtle", RdfFormat.TURTLE), ("application/ld+json", RdfFormat.JSON_LD)):
            with self.subTest(media_type):
                status, answer = self.ask(harness, text, rdf_format=media_type)
                self.assertEqual((status, answer["result"]["media_type"]), (200, media_type))
                data = answer["result"]["data"]
                data = data if isinstance(data, str) else json.dumps(data)
                self.assertEqual(set(parse(data.encode(), format=rdf_format)), expected)
        status, answer = self.ask(harness, text, rdf_format="application/rdf+xml")
        self.assertEqual((status, answer["error"]["code"]), (400, "request_malformed"))

    def test_the_projection_is_required_and_must_be_served(self) -> None:
        harness = self.harness(archive_root=self.archive_root, world_store=self.store)
        status, _, answer = harness.call("POST", "/world/query", {"query": "ASK {}"})
        self.assertEqual((status, answer["error"]["code"]), (400, "request_malformed"))
        status, _, answer = harness.call("POST", "/world/query", {"query": "ASK {}", "projection": "6"})
        self.assertEqual((status, answer["error"]["code"]), (400, "request_malformed"))
        status, answer = self.ask(harness, "ASK {}", projection=self.version - 1)
        self.assertEqual((status, answer["error"]["code"]), (404, "projection_not_served"))

    def test_updates_and_federation_cannot_run(self) -> None:
        harness = self.harness(archive_root=self.archive_root, world_store=self.store)
        status, answer = self.ask(harness, "INSERT DATA { <urn:x> <urn:y> <urn:z> }")
        self.assertEqual((status, answer["error"]["code"]), (400, "query_invalid"))
        status, answer = self.ask(harness, "SELECT * WHERE { SERVICE <http://192.0.2.1/> { ?s ?p ?o } }")
        self.assertEqual((status, answer["error"]["code"]), (400, "query_refused"))
        status, answer = self.ask(harness, "ASK { <urn:x> <urn:y> <urn:z> }")
        self.assertEqual((status, answer["result"]["data"]), (200, {"head": {}, "boolean": False}))

    def test_an_overrunning_query_is_stopped_and_the_next_one_runs(self) -> None:
        harness = self.harness(archive_root=self.archive_root, world_store=self.store, query_timeout_seconds=1e-6,
                               query_workers=1)
        status, answer = self.ask(harness, "ASK {}")
        self.assertEqual((status, answer["error"]["code"]), (504, "query_timeout"))
        [worker] = harness.relay.queries.workers
        self.assertIsNone(worker.process)
        worker.timeout = 30
        self.assertEqual(self.ask(harness, "ASK {}")[0], 200)

    def test_row_limit(self) -> None:
        harness = self.harness(archive_root=self.archive_root, world_store=self.store, max_rows=1)
        status, answer = self.ask(harness, "SELECT ?s WHERE { GRAPH ?g { ?s ?p ?o } }")
        self.assertEqual((status, answer["error"]["code"]), (422, "query_too_many_rows"))

    def test_a_missing_store_is_unavailable_not_empty(self) -> None:
        harness = self.harness(archive_root=self.archive_root, world_store=self.tmp / "absent")
        status, answer = self.ask(harness, "ASK {}")
        self.assertEqual((status, answer["error"]["code"]), (503, "world_unavailable"))


@needs_door
class DoorChecks(Fixture):
    """The relay's staged proposals are accepted by the real Door service."""

    def setUp(self) -> None:
        super().setUp()
        self.root = self.tmp / "archive"
        for variable, program in (("ARCHIVE_GIT_EXECUTABLE", "git"), ("ARCHIVE_GIT_ANNEX_EXECUTABLE", "git-annex")):
            found = shutil.which(program)
            self.assertIsNotNone(found, f"Door tests need {program} on PATH")
            os.environ.setdefault(variable, os.path.realpath(found))
        # An uninstalled build reads its specification from this source tree.
        os.environ.setdefault("archive_datadir", str(ROOT))
        initialized = subprocess.run([ARCHIVE, "init", "--operator", "alice", str(self.root)],
                                     capture_output=True, text=True)
        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        self.archive_id = json.loads(initialized.stdout)["archive_id"]
        # Door refuses a record whose spec selects a context the archive
        # does not hold, so drafts follow the archive's own specification.
        self.spec = json.loads((self.root / "archive.json").read_text())["spec"]
        self.socket = private(self.tmp / "door") / "door.sock"
        self.door = subprocess.Popen([DOOR, "serve", "--socket", str(self.socket)], cwd=self.root,
                                     stderr=subprocess.DEVNULL)
        self.addCleanup(self.stop_door)
        for _ in range(200):
            if self.socket.exists():
                break
            time.sleep(0.05)
        self.assertTrue(self.socket.exists(), "door serve did not start")
        self.store = self.tmp / "world-store"

    def stop_door(self) -> None:
        self.door.terminate()
        self.door.wait(timeout=30)

    def head(self) -> str:
        return subprocess.run(["git", "-C", str(self.root), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()

    def harness(self, **options) -> RelayHarness:
        return super().harness(archive_root=self.root, door_socket=self.socket, world_store=self.store,
                               archive_command=ARCHIVE, **options)

    def test_health_is_healthy_when_door_and_world_answer(self) -> None:
        world.refresh_served(self.root, self.store, archive_command=ARCHIVE)
        status, _, answer = self.harness().call("GET", "/health")
        self.assertEqual((status, answer["ok"], answer["result"]["healthy"]), (200, True, True))
        self.assertEqual((answer["archive"]["archive_id"], answer["archive"]["role"]), (self.archive_id, "shadow"))

    def test_a_dry_run_is_predicted_and_writes_nothing(self) -> None:
        harness = self.harness()
        before = self.head()
        status, _, answer = harness.call("POST", "/door/admit-dry-run", entity_proposal(self.archive_id, "dry", spec=self.spec))
        self.assertEqual(status, 200, answer)
        self.assertEqual(answer["result"]["door"]["status"], "predicted")
        self.assertEqual(len(answer["result"]["door"]["revision_ids"]), 1)
        self.assertEqual(self.head(), before)
        self.assertEqual(list(harness.config.staging.iterdir()), [])
        status, _, _ = harness.call("POST", "/door/admit", entity_proposal(self.archive_id, "dry", spec=self.spec))
        self.assertEqual(status, 403)
        self.assertEqual(self.head(), before)

    def test_doors_refusal_codes_are_passed_on(self) -> None:
        harness = self.harness()
        proposal = entity_proposal(self.archive_id, "refused", spec=self.spec)
        proposal["manifest"]["identities"][0]["name"] = "not-the-allocated-name"
        status, _, answer = harness.call("POST", "/door/admit-dry-run", proposal)
        self.assertEqual((status, answer["error"]["code"]), (422, "proposal_refused"))
        validate_service({"ok": False, "error": answer["error"]})

    def test_a_stale_expected_head_is_a_conflict(self) -> None:
        harness = self.harness(writes="commit", role="scratch")
        first = entity_proposal(self.archive_id, "renamed", spec=self.spec)
        status, _, committed = harness.call("POST", "/door/admit", first)
        self.assertEqual(status, 200, committed)
        [revision] = committed["result"]["door"]["revision_ids"]
        draft = dict(first["records"]["records/entity.json"])
        draft.update(payload={"kind": "person", "label": "renamed again"},
                     supersedes=[{"role": "supersedes", "id": draft["object_id"], "revision_id": revision}])
        def revise(expected: str) -> dict:
            return {"manifest": {"protocol": "archive-admission/1", "records": ["records/entity.json"],
                                 "identities": [], "contents": [],
                                 "expected_heads": [{"object_id": draft["object_id"], "revision_id": expected}]},
                    "records": {"records/entity.json": draft}}
        status, _, answer = harness.call("POST", "/door/admit-dry-run", revise("sha256:" + "0" * 64))
        self.assertEqual((status, answer["error"]["code"]), (409, "expected_head_stale"))
        # Anchor: the same proposal with the current head is accepted.
        self.assertEqual(harness.call("POST", "/door/admit-dry-run", revise(revision))[0], 200)

    def test_a_commit_is_read_back_through_world(self) -> None:
        harness = self.harness(writes="commit", role="scratch")
        before = self.head()
        proposal = entity_proposal(self.archive_id, "committed", spec=self.spec)
        predicted = harness.call("POST", "/door/admit-dry-run", proposal)[2]["result"]["door"]["revision_ids"]
        status, _, answer = harness.call("POST", "/door/admit", proposal)
        self.assertEqual(status, 200, answer)
        self.assertEqual(answer["result"]["door"]["status"], "committed")
        self.assertEqual(answer["result"]["door"]["revision_ids"], predicted)
        after = self.head()
        self.assertNotEqual(after, before)
        self.assertEqual((answer["result"]["world"]["status"], answer["result"]["world"]["snapshot"]), ("refreshed", after))
        revision = f"<{self.archive_id}#revision={predicted[0]}>"
        status, _, read = harness.call("POST", "/world/query", {"query": f"ASK {{ GRAPH ?g {{ {revision} ?p ?o }} }}",
                                                                "projection": world.PROJECTION_VERSION})
        self.assertEqual((status, read["result"]["snapshot"], read["result"]["data"]["boolean"]), (200, after, True))
        # A retry of the identical proposal is Door's retry, not a second write.
        status, _, again = harness.call("POST", "/door/admit", proposal)
        self.assertEqual((status, again["result"]["door"]["retry"], self.head()), (200, True, after))

    def test_a_failed_refresh_after_a_commit_is_reported_stale(self) -> None:
        harness = self.harness(writes="commit", role="scratch")
        with patch.object(world, "refresh_served", side_effect=world.ProjectionError("store is broken")):
            status, _, answer = harness.call("POST", "/door/admit", entity_proposal(self.archive_id, "stale", spec=self.spec))
        self.assertEqual((status, answer["result"]["door"]["status"]), (200, "committed"))
        self.assertEqual(answer["result"]["world"], {"status": "stale", "detail": "ProjectionError: store is broken"})



SLOW = ("SELECT (COUNT(*) AS ?n) WHERE { " + " ".join(
    f"VALUES ?{name} {{ {' '.join(map(str, range(90)))} }}" for name in "abcd") + " }")


class FastReadChecks(QueryChecks):
    """Reads at interactive speed (ADR 0063): cached answers, reads side by
    side, and gzip. Where cost is the point, the tests assert the cost."""

    def setUp(self) -> None:
        super().setUp()
        world.rebuild_store(self.archive_root, self.store, export_file=CHAIN_CUT_EXPORT)

    def bomb(self, harness: RelayHarness) -> None:
        def refuse(*args, **kwargs):
            raise AssertionError("this answer should have come from the cache")
        harness.relay.queries.query = refuse

    def test_a_cached_answer_never_reaches_a_worker(self) -> None:
        harness = self.harness(archive_root=self.archive_root, world_store=self.store)
        text = "SELECT ?s ?p WHERE { GRAPH ?g { ?s ?p ?o } } ORDER BY ?s ?p"
        first = self.ask(harness, text)
        self.assertEqual(first[0], 200)
        self.bomb(harness)
        self.assertEqual(self.ask(harness, text), first)
        # Anchor: the bomb is live, so the second answer above did not pass it.
        self.assertEqual(self.ask(harness, text + " LIMIT 1")[0], 500)
        self.assertEqual(self.ask(harness, text, rdf_format="text/turtle")[0], 500)

    def test_a_new_generation_is_answered_again_and_warmed_ahead_of_its_reader(self) -> None:
        harness = self.harness(archive_root=self.archive_root, world_store=self.store)
        text = "SELECT (COUNT(*) AS ?n) WHERE { GRAPH ?g { ?s ?p ?o } }"
        harness.relay.warm()  # the warmer's first look only records the generation
        before = self.ask(harness, text)[1]["result"]
        generation = world.selected_generation(self.store, self.version)
        # Anyone's refresh, not only this relay's commit, moves the generation.
        world.rebuild_store(self.archive_root, self.store, export_file=CHAIN_EXPORT)
        moved = world.selected_generation(self.store, self.version)
        self.assertNotEqual(moved, generation)
        self.assertEqual(harness.relay.warm(), 1)
        self.bomb(harness)
        after = self.ask(harness, text)[1]["result"]
        self.assertNotEqual(after["snapshot"], before["snapshot"])
        self.assertNotEqual(after["data"], before["data"])
        # The old generation's answers are dropped, not kept beside the new.
        self.assertEqual({key[1] for key in harness.relay.cache.entries}, {moved})

    def test_the_warmer_notices_a_refresh_by_itself(self) -> None:
        harness = self.harness(archive_root=self.archive_root, world_store=self.store)
        text = "ASK { GRAPH ?g { ?s ?p ?o } }"
        time.sleep(relay.WARM_POLL_SECONDS * 2)
        self.ask(harness, text)
        world.rebuild_store(self.archive_root, self.store, export_file=CHAIN_EXPORT)
        moved = world.selected_generation(self.store, self.version)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not any(key[1] == moved for key in harness.relay.cache.entries):
            time.sleep(0.1)
        self.bomb(harness)
        self.assertEqual(self.ask(harness, text)[0], 200)

    def test_a_question_refused_on_the_new_generation_does_not_stop_warming(self) -> None:
        bootstrap_rows = len(self.ask(self.harness(archive_root=self.archive_root, world_store=self.store),
                                      "SELECT ?s WHERE { GRAPH ?g { ?s ?p ?o } }")[1]["result"]["data"]["results"]["bindings"])
        harness = self.harness(archive_root=self.archive_root, world_store=self.store, max_rows=bootstrap_rows)
        harness.relay.warm()
        growing = "SELECT ?s WHERE { GRAPH ?g { ?s ?p ?o } }"
        steady = "ASK { GRAPH ?g { ?s ?p ?o } }"
        self.assertEqual(self.ask(harness, growing)[0], 200)
        self.assertEqual(self.ask(harness, steady)[0], 200)
        world.rebuild_store(self.archive_root, self.store, export_file=CHAIN_EXPORT)
        # The growing question now has too many rows: warming it is refused,
        # and the steady one is warmed all the same.
        self.assertEqual(harness.relay.warm(), 1)
        self.assertEqual(self.ask(harness, growing)[1]["error"]["code"], "query_too_many_rows")
        self.bomb(harness)
        self.assertEqual(self.ask(harness, steady)[0], 200)

    def small_while_big(self, workers: int) -> tuple[float, float]:
        harness = self.harness(archive_root=self.archive_root, world_store=self.store, query_workers=workers)
        self.ask(harness, "ASK {}")  # start a worker before timing
        big: dict[str, float] = {}

        def ask_big() -> None:
            started = time.monotonic()
            self.assertEqual(self.ask(harness, SLOW)[0], 200)
            big["seconds"] = time.monotonic() - started

        thread = threading.Thread(target=ask_big)
        thread.start()
        time.sleep(0.3)
        started = time.monotonic()
        self.assertEqual(self.ask(harness, "ASK { GRAPH ?g { ?s ?p ?o } }")[0], 200)
        small = time.monotonic() - started
        thread.join()
        return small, big["seconds"]

    def test_a_small_query_is_not_held_up_by_a_big_one(self) -> None:
        small, big = self.small_while_big(workers=3)
        self.assertGreater(big, 1.0)  # anchor: the big query is big
        self.assertLess(small, 0.5)
        # Anchor: with one worker the small query does wait, so the test can tell.
        waited, _ = self.small_while_big(workers=1)
        self.assertGreater(waited, 0.5)

    def test_gzip_when_asked_and_cached_once(self) -> None:
        harness = self.harness(archive_root=self.archive_root, world_store=self.store)
        body = {"query": "SELECT ?s ?p ?o WHERE { GRAPH ?g { ?s ?p ?o } }", "projection": self.version}
        status, plain_headers, plain = harness.call("POST", "/world/query", body)
        self.assertNotIn("Content-Encoding", plain_headers)
        status, headers, packed = harness.call("POST", "/world/query", body, accept_encoding="br, gzip;q=0.8")
        self.assertEqual((status, headers.get("Content-Encoding")), (200, "gzip"))
        self.assertIn("Accept-Encoding", headers.get("Vary", ""))
        self.assertEqual(packed, plain)
        with patch.object(relay.gzip, "compress", side_effect=AssertionError("compressed twice")):
            status, headers, again = harness.call("POST", "/world/query", body, accept_encoding="gzip")
        self.assertEqual((status, headers.get("Content-Encoding"), again), (200, "gzip", plain))
        _, refused_headers, _ = harness.call("POST", "/world/query", body, accept_encoding="gzip;q=0")
        self.assertNotIn("Content-Encoding", refused_headers)

    def test_identical_questions_asked_together_are_answered_once(self) -> None:
        harness = self.harness(archive_root=self.archive_root, world_store=self.store)
        asked: list[str] = []
        real = harness.relay.queries.query

        def counting(sparql, *args):
            asked.append(sparql)
            return real(sparql, *args)

        harness.relay.queries.query = counting
        threads = [threading.Thread(target=self.ask, args=(harness, SLOW)) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(asked, [SLOW])

    def test_a_failed_warming_is_logged_and_the_warmer_goes_on(self) -> None:
        harness = self.harness(archive_root=self.archive_root, world_store=self.store)
        calls = []

        def failing_once() -> int:
            calls.append(time.monotonic())
            if len(calls) == 1:
                raise RuntimeError("store vanished")
            return 0

        with patch.object(harness.relay, "warm", side_effect=failing_once), \
                patch.object(relay.sys, "stderr", new_callable=io.StringIO) as log:
            deadline = time.monotonic() + 10
            while len(calls) < 2 and time.monotonic() < deadline:
                harness.relay.warm_now.set()
                time.sleep(0.05)
        self.assertGreaterEqual(len(calls), 2)
        self.assertTrue(harness.relay.warmer.is_alive())
        self.assertIn("relay: warming failed: RuntimeError: store vanished", log.getvalue())

    def test_accept_encoding_is_read_as_rfc_9110_says(self) -> None:
        cases = {"gzip": True, "GZIP": True, "deflate, gzip;q=0.5": True, "*": True, "gzip;q=0": False,
                 "*;q=0": False, "identity": False, "": False, None: False, "gzip;q=x": False,
                 "*, gzip;q=0": False}
        for header, expected in cases.items():
            with self.subTest(header):
                self.assertEqual(relay.accepts_gzip(header), expected)


class WriteOrderChecks(Fixture):
    """Door sees one request at a time however many arrive (ADR 0056)."""

    def test_door_requests_never_overlap(self) -> None:
        path = private(self.tmp / "slow-door") / "door.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(path))
        listener.listen(8)
        self.addCleanup(listener.close)
        active, most = [0], [0]
        lock = threading.Lock()

        def handle(connection: socket.socket) -> None:
            with connection:
                connection.recv(65536)
                with lock:
                    active[0] += 1
                    most[0] = max(most[0], active[0])
                time.sleep(0.2)
                with lock:
                    active[0] -= 1
                connection.sendall(json.dumps({"ok": False, "error": {
                    "code": "proposal_refused", "message": "refused after a pause"}}).encode() + b"\n")

        def serve() -> None:
            while True:
                try:
                    connection, _ = listener.accept()
                except OSError:
                    return
                threading.Thread(target=handle, args=(connection,), daemon=True).start()

        threading.Thread(target=serve, daemon=True).start()
        harness = self.harness(door_socket=path)
        statuses: list[int] = []

        def send(n: int) -> None:
            statuses.append(harness.call("POST", "/door/admit-dry-run", entity_proposal(ARCHIVE_ID, f"order-{n}"))[0])

        threads = [threading.Thread(target=send, args=(n,)) for n in range(4)]
        started = time.monotonic()
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(statuses, [422] * 4)
        self.assertEqual(most[0], 1)
        # Anchor: they were sent together, so taking four turns shows they queued.
        self.assertGreater(time.monotonic() - started, 0.75)


if __name__ == "__main__":
    unittest.main()
