"""The relay's media routes (ADR 0069).

The archive is built by fixture/build.py through the real Door, from a plan
and synthetic recordings made here with ffmpeg's lavfi sources; the derived
media are made by enrich/media.py. Every answer is checked against
relay/openapi.json. Without Door, Archive, or ffmpeg the tests are skipped
and say so, unless ARCHIVE_REQUIRE_INTEGRATION is set, when they fail.
"""

from __future__ import annotations

import http.client
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

import jsonschema

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "relay"))
sys.path.insert(0, str(ROOT / "enrich"))

import derived  # noqa: E402
import media  # noqa: E402
import relay  # noqa: E402
import world  # noqa: E402
from test_relay import AGENT, ARCHIVE, DOOR, LOGIN, OPENAPI, ORIGIN, RelayHarness, entity_proposal  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
if os.environ.get("ARCHIVE_REQUIRE_INTEGRATION") and not shutil.which("ffprobe"):
    raise RuntimeError("ARCHIVE_REQUIRE_INTEGRATION is set but ffmpeg or ffprobe is not on PATH")
needs_everything = unittest.skipUnless(DOOR and ARCHIVE and FFMPEG and shutil.which("ffprobe"),
                                       "set ARCHIVE_TEST_DOOR and ARCHIVE_TEST_ARCHIVE and put ffmpeg on PATH")
OPERATOR_LOGIN = "operator-media@example.test"
TEMPLATES = {kind: route for route, kind in relay.MEDIA_ROUTES.items()}


def remove_tree(path: Path) -> None:
    for directory, _, _ in os.walk(path):
        os.chmod(directory, 0o700)
    shutil.rmtree(path, ignore_errors=True)


def tone(path: Path, frequency: int) -> Path:
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"sine=frequency={frequency}:sample_rate=48000:duration=2", "-ac", "2", "-c:a", "pcm_s16le",
                    str(path)], check=True)
    return path


def plan() -> dict:
    """Three recordings; the second retired and destroyed."""
    artifact = lambda key: {"key": key, "namespace": "archive.core/artifact", "name": f"media-{key}",  # noqa: E731
                            "shape": "artifact", "content": f"{key}.wav"}
    operator = [{"role": "author", "pin": "operator"}, {"role": "authorizer", "pin": "operator"}]
    return {"protocol": "archive-fixture/1", "description": "Synthetic recordings for the relay's media tests.",
            "operator": "media-operator", "steps": [
                {"name": "recordings", "command": "admit",
                 "records": [artifact("kept"), artifact("destroyed"), artifact("underived")]},
                {"name": "retire", "command": "transition", "records": [
                    {"key": "retire", "namespace": "archive.transition/retire", "name": "media-retire",
                     "shape": "claim", "contracts": [{"id": "archive.transition/retire", "version": "1"}],
                     "payload": {"reason": "Synthetic retirement"},
                     "refs": operator + [{"role": "retires", "pin": "destroyed"}]}]},
                {"name": "destroy", "command": "transition", "records": [
                    {"key": "destroy", "namespace": "archive.transition/destroy", "name": "media-destroy",
                     "shape": "claim", "contracts": [{"id": "archive.transition/destroy", "version": "1"}],
                     "payload": {"content": "$content:destroyed.wav", "reason": "Synthetic destruction",
                                 "external_survivors": []},
                     "refs": operator + [{"role": "destroys", "pin": "destroyed"}]}]},
            ]}


@needs_everything
class MediaChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="relay-media-"))
        cls.addClassCleanup(remove_tree, cls.tmp)
        for variable, program in (("ARCHIVE_GIT_EXECUTABLE", "git"), ("ARCHIVE_GIT_ANNEX_EXECUTABLE", "git-annex")):
            os.environ.setdefault(variable, os.path.realpath(shutil.which(program)))
        os.environ.setdefault("archive_datadir", str(ROOT))
        content = cls.tmp / "content"
        content.mkdir()
        sources = {key: tone(content / f"{key}.wav", frequency)
                   for key, frequency in (("kept", 440), ("destroyed", 550), ("underived", 660))}
        outsider = tone(cls.tmp / "outsider.wav", 770)
        (cls.tmp / "plan.json").write_text(json.dumps(plan()))
        cls.archive_root = cls.tmp / "archive"
        built = subprocess.run([sys.executable, str(ROOT / "fixture/build.py"), str(cls.archive_root),
                                "--plan", str(cls.tmp / "plan.json"), "--content", str(content),
                                "--door", DOOR, "--archive", ARCHIVE], capture_output=True, text=True)
        if built.returncode != 0:
            raise AssertionError(built.stderr)
        cls.manifest = json.loads(built.stdout)
        cls.store = cls.tmp / "world-store"
        world.refresh_served(cls.archive_root, cls.store, archive_command=ARCHIVE)
        cls.media_root = cls.tmp / "derived"
        cls.media_root.mkdir()
        cls.sources = {**sources, "outsider": outsider}
        cls.digests = {key: media.content_digest(path) for key, path in cls.sources.items()}
        for key in ("kept", "destroyed", "outsider"):
            media.derive(cls.sources[key], cls.digests[key], cls.media_root)

    def setUp(self) -> None:
        # Anchor: the operator is not the other configured caller.
        self.operator = self.manifest["operator_id"]
        self.assertNotEqual(self.operator, AGENT)
        self.work = Path(tempfile.mkdtemp(prefix="relay-media-test-"))
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)

    def harness(self, **options) -> RelayHarness:
        options.setdefault("archive_root", self.archive_root)
        options.setdefault("door_socket", self.work / "no-door" / "door.sock")
        options.setdefault("world_store", self.store)
        options.setdefault("media_root", self.media_root)
        options.setdefault("callers", {OPERATOR_LOGIN: self.operator, LOGIN: AGENT})
        harness = RelayHarness(self.work, **options)
        self.addCleanup(harness.close)
        return harness

    def get(self, harness: RelayHarness, path: str, login: str | None = OPERATOR_LOGIN, method: str = "GET",
            **headers: str) -> tuple[int, dict, bytes | dict]:
        """One request; a JSON answer is parsed and checked against the
        description, and a success's media type must be the described one."""
        sent = {name.replace("_", "-"): value for name, value in headers.items()}
        if login is not None:
            sent["Tailscale-User-Login"] = login
        connection = http.client.HTTPConnection("127.0.0.1", harness.server.server_address[1], timeout=60)
        self.addCleanup(connection.close)
        connection.request(method, path, headers=sent)
        response = connection.getresponse()
        status, answer_headers, body = response.status, dict(response.getheaders()), response.read()
        if status == 204:
            return status, answer_headers, body
        route, _ = relay.route_of(path)
        operation = OPENAPI["paths"].get(route, {}).get(method.lower()) or OPENAPI["paths"]["/health"]["get"]
        described = operation["responses"].get(str(status))
        self.assertIsNotNone(described, f"relay/openapi.json describes no {status} for {method} {route}")
        if answer_headers["Content-Type"] == "application/json":
            answer = json.loads(body)
            schema = {**described["content"]["application/json"]["schema"], "components": OPENAPI["components"]}
            jsonschema.Draft202012Validator(schema).validate(answer)
            return status, answer_headers, answer
        self.assertEqual(list(described["content"]), [answer_headers["Content-Type"]])
        return status, answer_headers, body

    def path(self, key: str, kind: str = "peaks") -> str:
        return f"/media/{self.digests[key]}/{kind}"

    def file(self, key: str, kind: str = "peaks") -> Path:
        return derived.path(self.media_root, self.digests[key], kind)

    # -- serving --------------------------------------------------------

    def test_each_kind_is_served_whole_to_the_operator(self) -> None:
        harness = self.harness()
        for kind in derived.KINDS:
            with self.subTest(kind):
                status, headers, body = self.get(harness, self.path("kept", kind))
                self.assertEqual(status, 200)
                self.assertEqual(body, self.file("kept", kind).read_bytes())
                self.assertEqual((headers["Content-Type"], headers["ETag"], headers["Accept-Ranges"]),
                                 (derived.KINDS[kind].media_type, derived.etag(self.digests["kept"], kind), "bytes"))
                self.assertIn("immutable", headers["Cache-Control"])
                self.assertEqual(int(headers["Content-Length"]), len(body))
                self.assertNotIn("Content-Range", headers)
        # What enrich/media.py made is what a browser receives: a whole MP4, index first.
        copy = self.work / "served.mp4"
        copy.write_bytes(self.get(harness, self.path("kept", "listen"))[2])
        self.assertTrue(media.mp4_complete(copy))

    def test_the_described_media_types_are_the_ones_the_layout_names(self) -> None:
        for kind, route in TEMPLATES.items():
            with self.subTest(kind):
                responses = OPENAPI["paths"][route]["get"]["responses"]
                self.assertEqual(list(responses["200"]["content"]), [derived.KINDS[kind].media_type])
                self.assertEqual(list(responses["206"]["content"]), [derived.KINDS[kind].media_type])

    def test_one_range_is_served_as_partial_content(self) -> None:
        harness = self.harness()
        whole = self.file("kept", "listen").read_bytes()
        size = len(whole)
        cases = {"bytes=0-99": (0, 99), "bytes=100-": (100, size - 1), "bytes=-10": (size - 10, size - 1),
                 f"bytes={size - 5}-{size * 10}": (size - 5, size - 1), f"bytes=-{size * 2}": (0, size - 1),
                 " bytes=7-7 ": (7, 7)}
        for header, (first, last) in cases.items():
            with self.subTest(header):
                status, headers, body = self.get(harness, self.path("kept", "listen"), Range=header)
                self.assertEqual(status, 206)
                self.assertEqual(headers["Content-Range"], f"bytes {first}-{last}/{size}")
                self.assertEqual(body, whole[first:last + 1])

    def test_a_range_outside_the_file_is_refused_with_its_size(self) -> None:
        harness = self.harness()
        size = self.file("kept").stat().st_size
        for header in (f"bytes={size}-", f"bytes={size + 10}-{size + 20}", "bytes=-0"):
            with self.subTest(header):
                status, headers, answer = self.get(harness, self.path("kept"), Range=header)
                self.assertEqual((status, answer["error"]["code"]), (416, "range_not_satisfiable"))
                self.assertEqual(headers["Content-Range"], f"bytes */{size}")
                self.assertIn("archive", answer)

    def test_a_range_it_will_not_honour_sends_the_whole_file(self) -> None:
        harness = self.harness()
        whole = self.file("kept").read_bytes()
        for header in ("bytes=0-1,5-9", "items=0-1", "bytes=9-2", "nonsense", "bytes=-", "bytes=0x1-2"):
            with self.subTest(header):
                status, headers, body = self.get(harness, self.path("kept"), Range=header)
                self.assertEqual((status, body), (200, whole))
                self.assertNotIn("Content-Range", headers)

    def test_if_range_honours_a_range_only_for_this_entity_tag(self) -> None:
        harness = self.harness()
        tag = derived.etag(self.digests["kept"], "peaks")
        self.assertEqual(self.get(harness, self.path("kept"), Range="bytes=0-9", If_Range=tag)[0], 206)
        for other in ('"sha256:0/peaks-0"', "Wed, 21 Oct 2015 07:28:00 GMT"):
            with self.subTest(other):
                status, _, body = self.get(harness, self.path("kept"), Range="bytes=0-9", If_Range=other)
                self.assertEqual((status, len(body)), (200, self.file("kept").stat().st_size))

    def test_a_listed_origin_may_read_the_range_headers(self) -> None:
        harness = self.harness()
        _, headers, _ = self.get(harness, self.path("kept"), Origin=ORIGIN, Range="bytes=0-3")
        self.assertEqual(headers["Access-Control-Allow-Origin"], ORIGIN)
        self.assertIn("Content-Range", headers["Access-Control-Expose-Headers"])
        # A year-long cached answer to a request without Origin (an audio
        # element's) must not be reused for a listed origin's: it varies.
        _, headers, _ = self.get(harness, self.path("kept"))
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertIn("Origin", headers["Vary"])
        status, headers, _ = self.get(harness, self.path("kept"), method="OPTIONS", Origin=ORIGIN)
        self.assertEqual(status, 204)
        self.assertIn("Range", headers["Access-Control-Allow-Headers"])

    def test_a_percent_encoded_digest_names_the_same_content(self) -> None:
        harness = self.harness()
        encoded = self.path("kept").replace(":", "%3A")
        self.assertNotEqual(encoded, self.path("kept"))
        self.assertEqual(self.get(harness, encoded)[2], self.file("kept").read_bytes())

    # -- refusals -------------------------------------------------------

    def test_only_the_active_operators_person_is_served(self) -> None:
        harness = self.harness()
        status, _, answer = self.get(harness, self.path("kept"), login=LOGIN)
        self.assertEqual((status, answer["error"]["code"]), (403, "media_forbidden"))
        status, _, answer = self.get(harness, self.path("kept"), login="stranger@example.test")
        self.assertEqual((status, answer["error"]["code"]), (403, "forbidden_identity"))
        self.assertNotIn("archive", answer)
        # Anchor: the same request from the operator's login is served.
        self.assertEqual(self.get(harness, self.path("kept"))[0], 200)

    def test_no_single_operator_in_world_serves_no_one(self) -> None:
        harness = self.harness()
        real = harness.relay.media_index()
        self.assertEqual(real.operator(), self.operator)
        for operators in (frozenset(), frozenset({self.operator, AGENT})):
            with self.subTest(operators=len(operators)):
                fake = relay.MediaIndex(real.generation, real.artifacts, real.destroyed, operators)
                with patch.object(harness.relay, "media_index", return_value=fake):
                    status, _, answer = self.get(harness, self.path("kept"))
                self.assertEqual((status, answer["error"]["code"]), (403, "media_forbidden"))

    def test_a_digest_no_artifact_has_is_unknown_even_when_derived(self) -> None:
        self.assertTrue(self.file("outsider").is_file())
        status, _, answer = self.get(self.harness(), self.path("outsider"))
        self.assertEqual((status, answer["error"]["code"]), (404, "media_unknown_digest"))

    def test_destroyed_content_is_not_served_through_its_derivative(self) -> None:
        harness = self.harness()
        self.assertTrue(self.file("destroyed").is_file())
        index = harness.relay.media_index()
        self.assertIn(self.digests["destroyed"], index.artifacts)
        self.assertEqual(index.destroyed, frozenset({self.digests["destroyed"]}))
        status, _, answer = self.get(harness, self.path("destroyed"))
        self.assertEqual((status, answer["error"]["code"]), (404, "media_unknown_digest"))

    def test_an_artifact_not_yet_derived_is_not_derived(self) -> None:
        self.assertFalse(self.file("underived").exists())
        for kind in derived.KINDS:
            with self.subTest(kind):
                status, _, answer = self.get(self.harness(), self.path("underived", kind))
                self.assertEqual((status, answer["error"]["code"]), (404, "media_not_derived"))

    def test_without_a_media_root_every_media_route_refuses_and_world_is_not_asked(self) -> None:
        harness = self.harness(media_root=None)
        with patch.object(harness.relay, "answer", side_effect=AssertionError("World was asked")):
            for kind in derived.KINDS:
                with self.subTest(kind):
                    status, _, answer = self.get(harness, self.path("kept", kind))
                    self.assertEqual((status, answer["error"]["code"]), (404, "media_not_derived"))
                    self.assertIn("no derived-media root", answer["error"]["message"])
        # The rest of the relay answers as before.
        self.assertEqual(self.get(harness, "/health")[2]["archive"]["archive_id"], self.manifest["archive_id"])

    def test_malformed_paths_and_methods(self) -> None:
        harness = self.harness()
        digest = self.digests["kept"]
        for path in (f"/media/{digest.upper()}/peaks", f"/media/{digest[7:]}/peaks", "/media/{digest}/peaks",
                     "/media//peaks", f"/media/{digest}%2F..%2Fx/peaks"):
            with self.subTest(path):
                status, _, answer = self.get(harness, path)
                self.assertEqual((status, answer["error"]["code"]), (400, "request_malformed"))
        for path in (f"/media/{digest}/waveform", f"/media/{digest}/peaks/", f"/media/{digest}/peaks?x=1",
                     f"/media/{digest}"):
            with self.subTest(path):
                status, _, answer = self.get(harness, path)
                self.assertEqual((status, answer["error"]["code"]), (404, "not_found"))
        status, headers, answer = self.get(harness, self.path("kept"), method="POST")
        self.assertEqual((status, answer["error"]["code"], headers["Allow"]), (405, "method_not_allowed", "GET"))

    # -- World ----------------------------------------------------------

    def test_world_is_asked_once_a_generation(self) -> None:
        harness = self.harness()
        lookups = patch.object(relay, "media_index", wraps=relay.media_index)
        asked = lookups.start()
        self.addCleanup(lookups.stop)
        self.assertEqual(self.get(harness, self.path("kept"))[0], 200)
        self.assertEqual(self.get(harness, self.path("kept", "listen"))[0], 200)
        self.assertEqual(self.get(harness, self.path("underived"))[0], 404)
        self.assertEqual(asked.call_count, 1)
        # A new generation is looked up again, and then served from memory.
        before = world.selected_generation(self.store, world.PROJECTION_VERSION)
        self.commit_an_entity()
        world.refresh_served(self.archive_root, self.store, archive_command=ARCHIVE)
        self.assertNotEqual(world.selected_generation(self.store, world.PROJECTION_VERSION), before)
        self.assertEqual(self.get(harness, self.path("kept"))[0], 200)
        self.assertEqual(self.get(harness, self.path("kept"))[0], 200)
        self.assertEqual(asked.call_count, 2)

    def commit_an_entity(self) -> None:
        proposal = entity_proposal(self.manifest["archive_id"], f"media-{uuid.uuid4()}", agent=self.operator,
                                   spec=self.manifest["spec"])
        directory = self.work / "proposal"
        (directory / "records").mkdir(parents=True)
        (directory / "manifest.json").write_text(json.dumps(proposal["manifest"]))
        for name, draft in proposal["records"].items():
            (directory / name).write_text(json.dumps(draft))
        admitted = subprocess.run([DOOR, "admit", "--proposal", str(directory)], cwd=self.archive_root,
                                  capture_output=True, text=True)
        self.assertEqual(admitted.returncode, 0, admitted.stderr)

    def test_a_missing_world_is_unavailable_not_unknown(self) -> None:
        status, _, answer = self.get(self.harness(world_store=self.work / "absent"), self.path("kept"))
        self.assertEqual((status, answer["error"]["code"]), (503, "world_unavailable"))

    def test_a_lookup_that_overruns_is_a_timeout(self) -> None:
        harness = self.harness(query_timeout_seconds=1e-6, query_workers=1)
        status, _, answer = self.get(harness, self.path("kept"))
        self.assertEqual((status, answer["error"]["code"]), (504, "query_timeout"))

    # -- the derived-media root -----------------------------------------

    def test_an_unreadable_derivative_is_an_error_not_a_missing_one(self) -> None:
        harness = self.harness()
        target = self.file("kept")
        target.chmod(0)
        self.addCleanup(target.chmod, 0o600)
        if os.access(target, os.R_OK):
            self.skipTest("this user reads files whatever their mode")
        status, _, answer = self.get(harness, self.path("kept"))
        self.assertEqual((status, answer["error"]["code"]), (500, "internal_error"))
        self.assertIn("could not be read", answer["error"]["message"])
        target.chmod(0o600)
        self.assertEqual(self.get(harness, self.path("kept"))[0], 200)

    def test_a_link_at_the_layout_path_is_never_followed_to_an_original(self) -> None:
        harness = self.harness()
        target = self.file("underived")
        target.parent.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, target.parent)
        target.symlink_to(self.sources["underived"])
        status, _, answer = self.get(harness, self.path("underived"))
        self.assertEqual((status, answer["error"]["code"]), (500, "internal_error"))
        self.assertIn("could not be read", answer["error"]["message"])
        target.unlink()
        target.mkdir()
        status, _, answer = self.get(harness, self.path("underived"))
        self.assertEqual((status, answer["error"]["code"]), (500, "internal_error"))
        self.assertIn("not a regular file", answer["error"]["message"])

    def test_a_read_failure_after_the_headers_ends_the_connection_and_the_relay_goes_on(self) -> None:
        harness = self.harness()
        connection = http.client.HTTPConnection("127.0.0.1", harness.server.server_address[1], timeout=60)
        self.addCleanup(connection.close)
        logged = io.StringIO()
        with patch.object(relay, "copy_range", side_effect=OSError("disk gone")), \
                patch.object(relay.sys, "stderr", logged):
            connection.request("GET", self.path("kept"), headers={"Tailscale-User-Login": OPERATOR_LOGIN})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            with self.assertRaises(http.client.IncompleteRead):
                response.read()
        self.assertIn("relay: media read failed: OSError: disk gone", logged.getvalue())
        self.assertEqual(self.get(harness, self.path("kept"))[0], 200)


class MediaUnitChecks(unittest.TestCase):
    """The pieces whose failures a real archive cannot produce on demand."""

    DIGEST = "sha256:" + "a" * 64

    def results(self, *rows: tuple[str, str]) -> dict:
        return {"head": {"vars": ["kind", "values"]}, "results": {"bindings": [
            {"kind": {"type": "literal", "value": kind}, "values": {"type": "literal", "value": values}}
            for kind, values in rows]}}

    def test_the_lookup_is_read_strictly(self) -> None:
        index = relay.media_index("g", self.results(("artifact", self.DIGEST), ("operator", AGENT)))
        self.assertEqual((index.artifacts, index.destroyed, index.operator()), ({self.DIGEST}, frozenset(), AGENT))
        self.assertTrue(index.serves(self.DIGEST))
        for name, data in {"no results": {}, "not an object": [], "no kind": {"results": {"bindings": [{}]}},
                           "unknown kind": self.results(("artist", "x")),
                           "bad digest": self.results(("artifact", "sha256:XYZ")),
                           "bad destroyed digest": self.results(("destroyed", "{\"content\": 1}"))}.items():
            with self.subTest(name), self.assertRaises(relay.Refused) as refused:
                relay.media_index("g", data)
            self.assertEqual(refused.exception.code, "internal_error")

    def test_no_rows_is_an_empty_archive_with_no_operator(self) -> None:
        index = relay.media_index("g", self.results())
        self.assertEqual((index.artifacts, index.operator()), (frozenset(), None))
        self.assertFalse(index.serves(self.DIGEST))

    def test_ranges(self) -> None:
        self.assertIsNone(relay.byte_range(None, 10))
        self.assertEqual(relay.byte_range("bytes=2-4", 10), (2, 4))
        with self.assertRaises(relay.Refused) as refused:
            relay.byte_range("bytes=-5", 0)
        self.assertEqual(refused.exception.headers, {"Content-Range": "bytes */0"})

    def test_a_client_that_goes_away_stops_the_copy_quietly(self) -> None:
        class Gone:
            def __init__(self, error: type[OSError]):
                self.error = error

            def write(self, data: bytes) -> None:
                raise self.error("client went away")

        for error in (BrokenPipeError, ConnectionResetError):
            with self.subTest(error.__name__):
                self.assertFalse(relay.copy_range(io.BytesIO(b"x" * 10), 0, 10, Gone(error)))
        sink = io.BytesIO()
        self.assertTrue(relay.copy_range(io.BytesIO(b"0123456789"), 3, 4, sink))
        self.assertEqual(sink.getvalue(), b"3456")
        with self.assertRaisesRegex(OSError, "ended early"):
            relay.copy_range(io.BytesIO(b"0123"), 2, 4, io.BytesIO())

    def test_route_names_the_template_and_the_digest(self) -> None:
        self.assertEqual(relay.route_of(f"/media/{self.DIGEST}/peaks"), ("/media/{digest}/peaks", self.DIGEST))
        self.assertEqual(relay.route_of("/media/x/other"), ("/media/x/other", None))
        self.assertEqual(relay.route_of("/health"), ("/health", None))


if __name__ == "__main__":
    unittest.main()
