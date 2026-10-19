"""The relay's circle, views, practice audio, and exports (ADRs 0082, 0088, 0089).

The archive is the published fixture plan built through the real Door, with
one more step: the operator discloses the band's lead sheet to the relay's
circle. Its members are the fixture's guest. What the circle may read is
computed by World's rules (`relay/access.py`); these tests read it back and
check that the relay serves exactly that, to the right caller. Every JSON
answer is checked against relay/openapi.json. Without Door and Archive the
tests are skipped and say so, unless ARCHIVE_REQUIRE_INTEGRATION is set.
"""

from __future__ import annotations

import hashlib
import http.client
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

import jsonschema

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "relay"))

import relay  # noqa: E402
import access  # noqa: E402
import bank  # noqa: E402
import derived  # noqa: E402
import world  # noqa: E402
from world import circle as circles  # noqa: E402
from world import exports as export_rules  # noqa: E402
from world import views as view_rules  # noqa: E402
from test_relay import ARCHIVE, DOOR, OPENAPI, RelayHarness, private  # noqa: E402

needs_door = unittest.skipUnless(DOOR and ARCHIVE, "set ARCHIVE_TEST_DOOR and ARCHIVE_TEST_ARCHIVE")
OPERATOR_LOGIN = "operator-circle@example.test"
MEMBER_LOGIN = "member-circle@example.test"
CIRCLE = "fixture-band-relay"
FEATURES = ["stems/bass", "stems/drums", "stems/other", "stems/vocals"]
RENDERER = {"id": "practice-test", "version": "1"}
EXPORTER = {"id": "lead-sheet-test", "version": "1"}

RENDER_SCRIPT = """import json, sys
given = json.load(open(sys.argv[1]))
with open(sys.argv[2], "wb") as out:
    out.write(b"fLaC" + json.dumps(given, sort_keys=True).encode())
"""
FAILING_SCRIPT = """import sys
sys.stderr.write("no time-stretching library here")
sys.exit(3)
"""
EXPORT_SCRIPT = """import json, sys
given = json.load(open(sys.argv[1]))
joined = [open(item["path"], encoding="utf-8").read() for item in given["joined"]]
with open(sys.argv[2], "w") as out:
    json.dump({"key": given["key"], "records": [r["revision_id"] for r in given["records"]],
               "joined": joined, "types": [item["media_type"] for item in given["joined"]]}, out)
with open(sys.argv[3], "w") as lost:
    json.dump(["alternative readings"], lost)
"""


def sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def remove_tree(path: Path) -> None:
    for directory, _, _ in os.walk(path):
        os.chmod(directory, 0o700)
    shutil.rmtree(path, ignore_errors=True)


def circle_plan() -> dict:
    plan = json.loads((ROOT / "fixture/plan.json").read_text())
    plan["steps"].append({"name": "disclose the band's lead sheet to the circle", "command": "transition", "records": [
        {"key": "circle-approval", "namespace": "archive.transition/publication_approval",
         "name": "relay-circle-approval", "shape": "claim",
         "contracts": [{"id": "archive.transition/publication_approval", "version": "1"}],
         "payload": {"destination": CIRCLE, "decision": "approve"},
         "refs": [{"role": "authorizer", "pin": "operator"}, {"role": "covers", "pin": "lead-sheet-band"}]}]})
    return plan


def stems_entry(bank_root: Path, digest: str) -> tuple[str, dict[str, bytes]]:
    """A complete stems entry of the bank for this content, as the runner
    leaves one, with stand-in FLAC bytes."""
    catalog = bank.Catalog.load()
    key = catalog.keys(digest, ["stems"])["stems"]
    directory = bank.entry_directory(bank_root, digest, "stems", key)
    directory.mkdir(parents=True)
    files, outputs = {}, {}
    for output in ("vocals", "drums", "bass", "other"):
        data = b"fLaC" + output.encode() * 64
        (directory / f"{output}.flac").write_bytes(data)
        files[output] = data
        outputs[output] = {"kind": "file", "media_type": "audio/flac", "suffix": ".flac", "file": f"{output}.flac",
                           "bytes": len(data), "sha256": sha256(data)}
    extractor = catalog["stems"]
    (directory / bank.ENTRY).write_text(json.dumps({
        "contract": bank.ENTRY_CONTRACT, "key": key, "extractor": {"id": "stems", "version": extractor.version},
        "parameters": catalog.parameters("stems"), "content": digest, "media_type": "audio/wav", "needs": {},
        "software": [], "grids": {}, "outputs": outputs, "started_at": "2026-01-01T00:00:00Z",
        "ended_at": "2026-01-01T00:00:01Z", "runner": "relay-test"}))
    return key, files


class Client:
    """Requests to one relay, each JSON answer checked against the description."""

    def __init__(self, test: unittest.TestCase, harness: RelayHarness):
        self.test, self.harness = test, harness

    def __call__(self, method: str, path: str, body: object = None, login: str = OPERATOR_LOGIN,
                 **headers: str) -> tuple[int, dict, object]:
        sent = {name.replace("_", "-"): value for name, value in headers.items()}
        sent["Tailscale-User-Login"] = login
        data = None if body is None else json.dumps(body).encode()
        if data is not None:
            sent["Content-Type"] = "application/json"
        connection = http.client.HTTPConnection("127.0.0.1", self.harness.server.server_address[1], timeout=120)
        try:
            connection.request(method, path, body=data, headers=sent)
            response = connection.getresponse()
            status, answer_headers, payload = response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()
        route, _ = relay.route_of(path)
        operation = OPENAPI["paths"][route][method.lower()]
        described = operation["responses"].get(str(status))
        self.test.assertIsNotNone(described, f"relay/openapi.json describes no {status} for {method} {route}")
        if answer_headers["Content-Type"] == "application/json" and "application/json" in described["content"]:
            answer = json.loads(payload)
            schema = {**described["content"]["application/json"]["schema"], "components": OPENAPI["components"]}
            jsonschema.Draft202012Validator(schema).validate(answer)
            return status, answer_headers, answer
        [declared] = described["content"]
        kind = answer_headers["Content-Type"]
        self.test.assertTrue(declared in (kind, "*/*") or (declared.endswith("/*") and kind.startswith(declared[:-1])),
                             f"{kind} is not {declared}")
        return status, answer_headers, payload


@needs_door
class CircleChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="relay-circle-"))
        cls.addClassCleanup(remove_tree, cls.tmp)
        for variable, program in (("ARCHIVE_GIT_EXECUTABLE", "git"), ("ARCHIVE_GIT_ANNEX_EXECUTABLE", "git-annex")):
            os.environ.setdefault(variable, os.path.realpath(shutil.which(program)))
        os.environ.setdefault("archive_datadir", str(ROOT))
        (cls.tmp / "plan.json").write_text(json.dumps(circle_plan()))
        cls.archive_root = cls.tmp / "archive"
        built = subprocess.run([sys.executable, str(ROOT / "fixture/build.py"), str(cls.archive_root),
                                "--plan", str(cls.tmp / "plan.json"), "--content", str(ROOT / "fixture/content"),
                                "--door", DOOR, "--archive", ARCHIVE], capture_output=True, text=True)
        if built.returncode != 0:
            raise AssertionError(built.stderr)
        cls.manifest = json.loads(built.stdout)
        cls.objects = cls.manifest["objects"]
        cls.operator = cls.manifest["operator_id"]
        cls.member = cls.objects["guest"]["object_id"]
        cls.band = cls.objects["ensemble"]["object_id"]
        cls.store = cls.tmp / "world-store"
        world.refresh_served(cls.archive_root, cls.store, archive_command=ARCHIVE)
        cls.circle = circles.Circle(CIRCLE, frozenset({cls.member}), cls.band)
        cls.access = access.load(cls.store, world.PROJECTION_VERSION, cls.operator, cls.circle, date.today())
        cls.readable = cls.access.readable(access.Caller(cls.member, "member"))
        cls.recording = sha256((ROOT / "fixture/content/recording.wav").read_bytes())
        cls.take = sha256((ROOT / "fixture/content/take-a.bin").read_bytes())
        cls.lyrics = (ROOT / "fixture/content/lyrics.txt").read_bytes()
        cls.media_root = private(cls.tmp / "derived")
        for digest in (cls.recording, cls.take):
            target = derived.path(cls.media_root, digest, "peaks")
            target.parent.mkdir(parents=True)
            target.write_bytes(b"peaks of " + digest.encode())
        cls.bank_root = cls.media_root
        cls.stems_key, cls.stems = stems_entry(cls.bank_root, cls.recording)
        scripts = private(cls.tmp / "scripts")
        for name, text in (("render.py", RENDER_SCRIPT), ("fail.py", FAILING_SCRIPT), ("export.py", EXPORT_SCRIPT)):
            (scripts / name).write_text(text)
        cls.scripts = scripts
        cls.installed = {}
        for bundle_path in (cls.archive_root / "contracts").glob("sha256-*.json"):
            bundle = json.loads(bundle_path.read_bytes())
            cls.installed[(bundle["id"], bundle["version"])] = "sha256:" + bundle_path.stem.removeprefix("sha256-")
        cls.socket = private(cls.tmp / "door") / "door.sock"
        cls.door = subprocess.Popen([DOOR, "serve", "--socket", str(cls.socket)], cwd=cls.archive_root,
                                    stderr=subprocess.DEVNULL)
        cls.addClassCleanup(cls.door.wait, 30)
        cls.addClassCleanup(cls.door.terminate)
        for _ in range(200):
            if cls.socket.exists():
                break
            time.sleep(0.05)

    def setUp(self) -> None:
        # Anchors: the circle reads something, and not everything.
        self.assertTrue(self.readable)
        self.assertNotEqual(set(self.readable), set(self.access.by_revision))
        self.assertNotEqual(self.member, self.operator)
        self.work = Path(tempfile.mkdtemp(prefix="relay-circle-test-"))
        self.addCleanup(remove_tree, self.work)

    def relay(self, **options) -> Client:
        options.setdefault("archive_root", self.archive_root)
        options.setdefault("door_socket", self.socket)
        options.setdefault("world_store", self.store)
        options.setdefault("archive_command", ARCHIVE)
        options.setdefault("role", "scratch")
        options.setdefault("media_root", self.media_root)
        options.setdefault("callers", {OPERATOR_LOGIN: self.operator, MEMBER_LOGIN: self.member})
        options.setdefault("circle", {"destination": CIRCLE, "members": [self.member], "band": self.band})
        options.setdefault("bank_root", self.bank_root)
        options.setdefault("practice_features", tuple(FEATURES))
        options.setdefault("renderers", {RENDERER["id"]: {"version": RENDERER["version"],
                                                          "command": [sys.executable, str(self.scripts / "render.py")]},
                                         "practice-broken": {"version": "1", "command": [
                                             sys.executable, str(self.scripts / "fail.py")]}})
        options.setdefault("exporters", {EXPORTER["id"]: {"version": EXPORTER["version"],
                                                          "command": [sys.executable, str(self.scripts / "export.py")]}})
        harness = RelayHarness(self.work, **options)
        self.addCleanup(harness.close)
        return Client(self, harness)

    def pin(self, key: str) -> dict[str, str]:
        return {"id": self.objects[key]["object_id"], "revision_id": self.objects[key]["revision_id"]}

    def pin_of(self, revision: str) -> dict[str, str]:
        return {"id": self.access.by_revision[revision]["object_id"], "revision_id": revision}

    def hidden_claim(self) -> str:
        """A claim revision the circle cannot read."""
        hidden = sorted(rid for rid, record in self.access.by_revision.items()
                        if record.get("shape") == "claim" and rid not in self.readable)
        self.assertTrue(hidden)
        return hidden[0]

    # -- configuration and identity ---------------------------------------

    def test_the_archive_block_names_the_caller_its_capacity_and_the_circle(self) -> None:
        call = self.relay()
        for login, agent, capacity in ((OPERATOR_LOGIN, self.operator, "operator"),
                                       (MEMBER_LOGIN, self.member, "member")):
            with self.subTest(capacity):
                _, _, answer = call("GET", "/health", login=login)
                self.assertEqual(answer["archive"]["caller"], {"agent": agent, "capacity": capacity})
                self.assertEqual(answer["archive"]["circle"], {"destination": CIRCLE, "band": self.band})
                self.assertEqual((answer["archive"]["writes"], answer["archive"]["role"]), ("dry-run", "scratch"))

    def test_a_circle_whose_member_is_no_person_refuses_to_start(self) -> None:
        stranger = "urn:uuid:" + str(uuid.uuid4())
        with self.assertRaisesRegex(ValueError, "not person Entities"):
            self.relay(callers={OPERATOR_LOGIN: self.operator, MEMBER_LOGIN: stranger},
                       circle={"destination": CIRCLE, "members": [stranger]})
        # Neither does one whose operator is not World's.
        with self.assertRaisesRegex(ValueError, "not the active operator"):
            self.relay(callers={OPERATOR_LOGIN: stranger, MEMBER_LOGIN: self.member},
                       circle={"destination": CIRCLE, "members": [self.member]})

    def test_raw_queries_and_transitions_are_the_operators(self) -> None:
        call = self.relay()
        query = {"query": "ASK { ?s ?p ?o }", "projection": world.PROJECTION_VERSION}
        for path, body in (("/world/query", query), ("/door/transition-dry-run", {"manifest": {}, "records": {}})):
            with self.subTest(path):
                status, _, answer = call("POST", path, body, login=MEMBER_LOGIN)
                self.assertEqual((status, answer["error"]["code"]), (403, "operator_only"))
        self.assertEqual(call("POST", "/world/query", query)[0], 200)

    # -- named views ------------------------------------------------------

    def test_the_served_views_are_world_s_registry(self) -> None:
        status, _, answer = self.relay()("GET", "/world/views", login=MEMBER_LOGIN)
        self.assertEqual((status, answer["result"]["views"]), (200, view_rules.catalogue()))
        self.assertIn("archive.view/chords_current", {item["view"] for item in answer["result"]["views"]})

    def test_a_member_s_view_reads_only_their_readable_set_and_says_how_much_was_withheld(self) -> None:
        call = self.relay()
        request = {"view": "archive.view/chord_names", "version": "1",
                   "subject": {"readings": [{"bass": 7, "tones": [0, 3, 10]}]}}
        _, _, operator = call("POST", "/world/view", request)
        _, _, member = call("POST", "/world/view", request, login=MEMBER_LOGIN)
        self.assertEqual(operator["result"]["withheld"], 0)
        self.assertEqual(member["result"]["withheld"], len(self.access.by_revision) - len(self.readable))
        self.assertEqual(operator["result"]["answer"], member["result"]["answer"])
        self.assertEqual(member["result"]["parameters"]["transpose"], 0)

    def test_an_object_whose_reach_cannot_be_read_is_withheld(self) -> None:
        # The swallowed branch of access._reach: a failed reach never widens.
        lead_sheet = self.pin("lead-sheet-band")["revision_id"]
        self.assertIn(lead_sheet, self.access.circle_set.revisions)
        from unittest import mock

        def unreadable(*args, **kwargs):
            raise ValueError("unreadable")
        for target in ("reach", "__init__"):        # one object's reach, or the records as a whole
            with self.subTest(unreadable=target):
                with mock.patch.object(access.documents.Reacher, target, unreadable):
                    found = access.load(self.store, world.PROJECTION_VERSION, self.operator, self.circle,
                                        date.today())
                self.assertEqual(found.circle_set.revisions, frozenset())
                self.assertIn(lead_sheet, found.circle_set.withheld)
        own = {rid for rid, r in found.by_revision.items() if r.get("agent") == self.member}
        self.assertEqual(found.readable(access.Caller(self.member, "member")), frozenset(own))

    def test_a_member_may_name_only_what_they_read(self) -> None:
        call = self.relay()
        lead_sheet = self.pin("lead-sheet-band")
        self.assertIn(lead_sheet["revision_id"], self.readable)
        shown = {"view": "archive.view/lead_sheet_changes", "version": "1", "subject": {"lead_sheet": lead_sheet}}
        status, _, answer = call("POST", "/world/view", shown, login=MEMBER_LOGIN)
        self.assertEqual(status, 200, answer)
        hidden = {"view": "archive.view/standing", "version": "1",
                  "subject": {"claim": self.pin_of(self.hidden_claim())}}
        status, _, answer = call("POST", "/world/view", hidden, login=MEMBER_LOGIN)
        self.assertEqual((status, answer["error"]["code"]), (403, "not_disclosed"))
        # Something that does not exist is refused the same way, so the
        # refusal reveals nothing; the operator learns it is not there.
        absent = {"view": "archive.view/standing", "version": "1",
                  "subject": {"claim": {"id": "urn:uuid:" + str(uuid.uuid4()), "revision_id": "sha256:" + "0" * 64}}}
        status, _, answer = call("POST", "/world/view", absent, login=MEMBER_LOGIN)
        self.assertEqual((status, answer["error"]["code"]), (403, "not_disclosed"))
        status, _, answer = call("POST", "/world/view", absent)
        self.assertEqual((status, answer["error"]["code"]), (404, "view_subject_not_found"))
        self.assertEqual(call("POST", "/world/view", hidden)[0], 200)

    def test_the_readable_set_is_the_operator_s_to_read(self) -> None:
        call = self.relay()
        request = {"view": "archive.view/readable_set", "version": "1",
                   "subject": {"circle": {"destination": CIRCLE, "members": [self.member], "band": self.band}}}
        status, _, answer = call("POST", "/world/view", request, login=MEMBER_LOGIN)
        self.assertEqual((status, answer["error"]["code"]), (403, "operator_only"))
        status, _, answer = call("POST", "/world/view", request)
        self.assertEqual(status, 200, answer)
        self.assertEqual(set(answer["result"]["answer"]["revisions"]), set(self.access.circle_set.revisions))
        # So is whether an approval may still publish: it names rights and limits.
        approval = next(r for r in self.access.records
                        if (r.get("contract") or [None])[0] == "archive.transition/publication_approval")
        asked = {"view": "archive.view/may_publish", "version": "1",
                 "subject": {"approval": {"id": approval["object_id"], "revision_id": approval["revision_id"]}}}
        status, _, answer = call("POST", "/world/view", asked, login=MEMBER_LOGIN)
        self.assertEqual((status, answer["error"]["code"]), (403, "operator_only"))
        status, _, answer = call("POST", "/world/view", asked)
        self.assertEqual(status, 200, answer)
        self.assertFalse(answer["result"]["answer"]["may_publish"])

    def test_view_refusals(self) -> None:
        call = self.relay()
        for body, expected in (
                ({"view": "archive.view/nothing", "version": "1", "subject": {}}, (404, "view_unknown")),
                ({"view": "archive.view/chord_names", "version": "9", "subject": {}}, (404, "view_not_served")),
                ({"view": "archive.view/chord_names", "version": "1", "subject": {"x": 1}},
                 (400, "view_request_invalid"))):
            with self.subTest(expected[1]):
                status, _, answer = call("POST", "/world/view", body)
                self.assertEqual((status, answer["error"]["code"]), expected)

    def test_a_view_is_answered_once_a_generation_and_caller(self) -> None:
        call = self.relay()
        request = {"view": "archive.view/chord_names", "version": "1",
                   "subject": {"readings": [{"bass": 0, "tones": [4, 7]}]}}
        first = call("POST", "/world/view", request)[2]

        def refuse(*args, **kwargs):
            raise AssertionError("this view should have come from the cache")
        call.harness.relay.queries.view = refuse
        self.assertEqual(call("POST", "/world/view", request)[2], first)
        # Anchor: another caller's view is not the operator's cached one.
        self.assertEqual(call("POST", "/world/view", request, login=MEMBER_LOGIN)[0], 500)

    # -- media under the recording's gate ---------------------------------

    def test_a_member_hears_a_disclosed_recording_and_no_other(self) -> None:
        call = self.relay()
        member = access.Caller(self.member, "member")
        self.assertTrue(self.access.hears(self.recording, member))
        self.assertFalse(self.access.hears(self.take, member))
        status, _, body = call("GET", f"/media/{self.recording}/peaks", login=MEMBER_LOGIN)
        self.assertEqual((status, body), (200, b"peaks of " + self.recording.encode()))
        status, _, answer = call("GET", f"/media/{self.take}/peaks", login=MEMBER_LOGIN)
        self.assertEqual((status, answer["error"]["code"]), (403, "media_forbidden"))
        self.assertEqual(call("GET", f"/media/{self.take}/peaks")[0], 200)

    def test_the_bank_lists_and_serves_the_configured_stems(self) -> None:
        call = self.relay(practice_features=("stems/bass", "stems/drums"))
        status, _, answer = call("GET", f"/media/{self.recording}/bank", login=MEMBER_LOGIN)
        self.assertEqual(status, 200, answer)
        self.assertEqual([item["name"] for item in answer["result"]["features"]], ["stems/bass", "stems/drums"])
        self.assertEqual({item["key"] for item in answer["result"]["features"]}, {self.stems_key})
        self.assertEqual(answer["result"]["missing"], [])
        status, headers, body = call("GET", f"/media/{self.recording}/bank/stems/bass", login=MEMBER_LOGIN,
                                     Range="bytes=0-3")
        self.assertEqual((status, body, headers["ETag"]), (206, b"fLaC", f'"{self.stems_key}/bass"'))
        self.assertEqual(call("GET", f"/media/{self.recording}/bank/stems/bass")[2], self.stems["bass"])
        for path in ("stems/vocals", "stems/levels", "chords/chords"):
            with self.subTest(path):
                status, _, answer = call("GET", f"/media/{self.recording}/bank/{path}")
                self.assertEqual((status, answer["error"]["code"]), (404, "feature_not_served"))
        status, _, answer = call("GET", f"/media/{self.take}/bank")
        self.assertEqual(answer["result"]["missing"], ["stems/bass", "stems/drums"])
        status, _, answer = call("GET", f"/media/{self.take}/bank/stems/bass")
        self.assertEqual((status, answer["error"]["code"]), (404, "feature_not_made"))
        status, _, answer = call("GET", f"/media/{self.take}/bank/stems/bass", login=MEMBER_LOGIN)
        self.assertEqual((status, answer["error"]["code"]), (403, "media_forbidden"))

    def test_without_a_bank_no_feature_is_served(self) -> None:
        call = self.relay(bank_root=None, practice_features=())
        status, _, answer = call("GET", f"/media/{self.recording}/bank")
        self.assertEqual((status, answer["result"]["features"], answer["result"]["missing"]), (200, [], []))
        status, _, answer = call("GET", f"/media/{self.recording}/bank/stems/bass")
        self.assertEqual((status, answer["error"]["code"]), (404, "feature_not_served"))

    # -- practice renders --------------------------------------------------

    def practice(self, **changes) -> dict:
        return {"content": self.recording, "stems": {"extractor": "stems", "key": self.stems_key},
                "gains": {"vocals": 100, "drums": 60, "other": 80}, "semitones": -2, "speed": 75,
                "renderer": RENDERER, **changes}

    def test_a_render_is_begun_in_the_background_and_then_served(self) -> None:
        call = self.relay()
        request = self.practice()
        path = f"/media/{self.recording}/practice"
        status, _, answer = call("POST", path, request, login=MEMBER_LOGIN)
        self.assertEqual(status, 200, answer)
        key = export_rules.practice_key(request)
        self.assertEqual((answer["result"]["status"], answer["result"]["key"]), ("rendering", key))
        call.harness.relay.renderer.work.join()
        status, _, answer = call("POST", path, request, login=MEMBER_LOGIN)
        self.assertEqual((answer["result"]["status"], answer["result"]["speed"]), ("ready", 75))
        status, headers, body = call("GET", f"{path}/{key}", login=MEMBER_LOGIN)
        self.assertEqual((status, headers["Content-Type"], headers["ETag"]), (200, "audio/flac", f'"{key}"'))
        self.assertEqual(len(body), answer["result"]["bytes"])
        given = json.loads(body[4:])
        self.assertEqual(given["shifted"], ["other", "vocals"])   # drums are never pitch-shifted
        self.assertEqual(set(given["inputs"]), {"vocals", "drums", "other"})
        self.assertEqual(Path(given["inputs"]["drums"]).read_bytes(), self.stems["drums"])
        # The same mix asked another way is the same render.
        self.assertEqual(export_rules.practice_key({**request, "gains": dict(reversed(request["gains"].items()))}), key)

    def test_practice_refusals(self) -> None:
        call = self.relay()
        path = f"/media/{self.recording}/practice"
        for body, expected in (
                (self.practice(renderer={"id": "practice-test", "version": "2"}), (422, "renderer_unknown")),
                (self.practice(stems={"extractor": "stems", "key": "sha256:" + "1" * 64}), (404, "stems_not_made")),
                (self.practice(gains={"levels": 100}), (404, "feature_not_served")),
                (self.practice(content=self.take), (400, "request_malformed")),
                (self.practice(speed=300), (400, "request_malformed"))):
            with self.subTest(expected[1]):
                status, _, answer = call("POST", path, body)
                self.assertEqual((status, answer["error"]["code"]), expected)
        status, _, answer = call("POST", f"/media/{self.take}/practice", self.practice(content=self.take),
                                 login=MEMBER_LOGIN)
        self.assertEqual((status, answer["error"]["code"]), (403, "media_forbidden"))
        status, _, answer = call("GET", f"{path}/sha256:{'2' * 64}")
        self.assertEqual((status, answer["error"]["code"]), (404, "media_not_derived"))

    def test_a_failed_render_is_reported_once_and_tried_again(self) -> None:
        call = self.relay()
        request = self.practice(renderer={"id": "practice-broken", "version": "1"})
        path = f"/media/{self.recording}/practice"
        self.assertEqual(call("POST", path, request)[2]["result"]["status"], "rendering")
        call.harness.relay.renderer.work.join()
        status, _, answer = call("POST", path, request)
        self.assertEqual((status, answer["error"]["code"]), (502, "render_failed"))
        self.assertIn("no time-stretching library here", answer["error"]["message"])
        self.assertEqual(call("POST", path, request)[2]["result"]["status"], "rendering")
        call.harness.relay.renderer.work.join()

    # -- exports -----------------------------------------------------------

    def export_request(self, **changes) -> dict:
        return {"format": "jams", "exporter": EXPORTER, "rendered": [self.pin("lead-sheet-band")],
                "joined": [self.pin("lyrics")], "options": {"transpose": 2}, **changes}

    def test_an_export_is_rendered_once_and_served_to_who_may_read_it(self) -> None:
        call = self.relay()
        request = self.export_request()
        status, _, answer = call("POST", "/exports", request, login=MEMBER_LOGIN)
        self.assertEqual(status, 200, answer)
        key = export_rules.export_key(request)
        self.assertEqual((answer["result"]["key"], answer["result"]["media_type"], answer["result"]["losses"]),
                         (key, "application/json", ["alternative readings"]))
        status, headers, body = call("GET", f"/exports/{key}", login=MEMBER_LOGIN)
        self.assertEqual((status, headers["ETag"]), (200, f'"{key}"'))
        made = json.loads(body)
        self.assertEqual(made["joined"], [self.lyrics.decode()])
        self.assertEqual(made["types"], ["text/plain"])
        self.assertIn(self.objects["lead-sheet-band"]["revision_id"], made["records"])
        self.assertTrue(set(made["records"]) <= set(self.readable))
        # The second request finds the made file and runs nothing.
        call.harness.relay.exporters.render = lambda *args: self.fail("rendered twice")
        self.assertEqual(call("POST", "/exports", request)[2]["result"], answer["result"])

    def test_export_refusals(self) -> None:
        call = self.relay()
        hidden = self.pin_of(self.hidden_claim())
        status, _, answer = call("POST", "/exports", self.export_request(rendered=[hidden]), login=MEMBER_LOGIN)
        self.assertEqual((status, answer["error"]["code"]), (403, "not_disclosed"))
        absent = {"id": "urn:uuid:" + str(uuid.uuid4()), "revision_id": "sha256:" + "3" * 64}
        status, _, answer = call("POST", "/exports", self.export_request(rendered=[absent]))
        self.assertEqual((status, answer["error"]["code"]), (404, "export_input_unknown"))
        status, _, answer = call("POST", "/exports", self.export_request(exporter={"id": "nothing", "version": "1"}))
        self.assertEqual((status, answer["error"]["code"]), (422, "exporter_unknown"))
        status, _, answer = call("POST", "/exports", self.export_request(rendered=[self.pin("lyrics")]))
        self.assertEqual((status, answer["error"]["code"]), (400, "request_malformed"))
        status, _, answer = call("GET", f"/exports/sha256:{'4' * 64}")
        self.assertEqual((status, answer["error"]["code"]), (404, "media_not_derived"))

    # -- documents ---------------------------------------------------------

    def test_a_disclosed_document_is_served_with_its_described_type(self) -> None:
        call = self.relay()
        digest = sha256(self.lyrics)
        for login in (OPERATOR_LOGIN, MEMBER_LOGIN):
            with self.subTest(login):
                status, headers, body = call("GET", f"/documents/{digest}", login=login)
                self.assertEqual((status, headers["Content-Type"], body), (200, "text/plain", self.lyrics))
        status, _, answer = call("GET", f"/documents/{self.recording}")
        self.assertEqual((status, answer["error"]["code"]), (404, "media_unknown_digest"))
        status, _, answer = call("POST", "/documents/stage", {"media_type": "text/plain", "content": "eA=="},
                                 login=MEMBER_LOGIN)
        self.assertEqual((status, answer["error"]["code"]), (403, "operator_only"))

    # -- a member's drafts -------------------------------------------------

    def judgment(self, target: dict[str, str], agent: str, contract=("archive.assessment/judgment", "1"),
                 payload=None, shape="assessment") -> dict:
        name = f"relay-member-{uuid.uuid4()}"
        namespace = contract[0]
        object_id = "urn:uuid:" + str(uuid.uuid5(uuid.UUID(self.manifest["archive_id"][9:]), namespace + "\0" + name))
        draft = {"spec": self.manifest["spec"], "object_id": object_id, "shape": shape,
                 "by": "pkg:generic/archive-relay-test@0", "agent": agent,
                 "payload": payload or {"verdict": "affirmed"},
                 "refs": [{"role": "assessment_of" if shape == "assessment" else "subject", **target}],
                 "supersedes": [],
                 "contracts": [{"id": contract[0], "version": contract[1], "digest": self.installed[contract]}]}
        return {"manifest": {"protocol": "archive-admission/1", "records": ["records/draft.json"],
                             "identities": [{"object_id": object_id, "namespace": namespace, "name": name}],
                             "contents": [], "expected_heads": [{"object_id": object_id, "revision_id": None}]},
                "records": {"records/draft.json": draft}}

    def test_a_member_asserts_under_their_own_name_about_what_they_read(self) -> None:
        call = self.relay()
        target = self.pin("sequence-v2")
        self.assertIn(target["revision_id"], self.readable)
        status, _, answer = call("POST", "/door/admit-dry-run", self.judgment(target, self.member), login=MEMBER_LOGIN)
        self.assertEqual(status, 200, answer)
        self.assertEqual(answer["result"]["door"]["status"], "predicted")
        hidden = self.pin_of(self.hidden_claim())
        status, _, answer = call("POST", "/door/admit-dry-run", self.judgment(hidden, self.member), login=MEMBER_LOGIN)
        self.assertEqual((status, answer["error"]["code"]), (403, "not_disclosed"))
        status, _, answer = call("POST", "/door/admit-dry-run", self.judgment(target, self.operator),
                                 login=MEMBER_LOGIN)
        self.assertEqual((status, answer["error"]["code"]), (403, "agent_mismatch"))
        sensitivity = self.judgment(target, self.member, ("archive.claim/sensitivity", "1"), {"label": "RED"}, "claim")
        status, _, answer = call("POST", "/door/admit-dry-run", sensitivity, login=MEMBER_LOGIN)
        self.assertEqual((status, answer["error"]["code"]), (403, "operator_only"))


if __name__ == "__main__":
    unittest.main()
