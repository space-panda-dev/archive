"""`enrich detect` end to end: a scratch archive, real Door, archive show, and World."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import adapter
import detector
import support
from support import needs_door
from test_detector import A_MINOR, session
from test_probe import fake_program

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

PROV = "http://www.w3.org/ns/prov#"
OA = "http://www.w3.org/ns/oa#"


@needs_door
class DetectCommand(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = support.scratch(self, "enrich-detect-")
        self.env = support.environment()
        self.wav, self.edges = session(self.tmp)
        short, _ = support.synthetic_wav(self.tmp / "short.wav", 8000, [support.chord(12, A_MINOR)])
        two = support.two_stream_mka(self.tmp / "two.mka")
        self.root, manifest = support.build_archive(self, {"session": self.wav, "short": short, "two": two})
        self.objects = manifest["objects"]
        self.staging = self.tmp / "staging"

    def enrich(self, command: str, key: str, *extra: str, ffmpeg: str | None = None) -> tuple[int, dict]:
        target = self.objects[key]["object_id"] if key in self.objects else key
        arguments = [sys.executable, str(HERE / "enrich.py"), command, "--archive", str(self.root),
                     "--artifact", target, "--door", support.DOOR, "--archive-command", support.ARCHIVE,
                     "--ffmpeg", ffmpeg or support.FFMPEG, "--staging", str(self.staging), *extra]
        if command == "probe":
            arguments += ["--ffprobe", support.FFPROBE]
        completed = subprocess.run(arguments, capture_output=True, text=True, env=self.env)
        return completed.returncode, json.loads(completed.stdout or completed.stderr)

    def probed(self, key: str) -> dict:
        code, answer = self.enrich("probe", key)
        self.assertEqual((code, answer["status"]), (0, "committed"), answer)
        return answer

    def show(self, selector: str) -> dict:
        shown = subprocess.run([support.ARCHIVE, "show", selector], cwd=self.root, capture_output=True, text=True,
                               env=self.env)
        self.assertEqual(shown.returncode, 0, shown.stdout)
        [record] = json.loads(shown.stdout)["records"]
        return record

    def test_spans_reach_the_archive_pinned_to_their_source_stream_and_run(self) -> None:
        stream = self.probed("session")["outputs"][0]
        code, answer = self.enrich("detect", "session")
        self.assertEqual((code, answer["status"]), (0, "committed"), answer)
        [output] = answer["outputs"]
        claim = self.show(output["object_id"])
        # The second break is a hop before the gap's middle: the quiet talk's
        # last hop is already below the silence threshold.
        self.assertEqual(claim["payload"], {"sample_rate": 8000, "spans": [
            {"start_sample": s, "end_sample": e, "class": c} for s, e, c in (
                (0, 344000, "music"), (344000, 711200, "talk"), (711200, 1080000, "music"),
                (1080000, self.edges[7], "talk"), (self.edges[7], self.edges[8], "quiet"),
                (self.edges[8], self.edges[9], "music"))]})
        self.assertEqual(claim["payload"], {key: answer["detection"][key] for key in ("sample_rate", "spans")})
        self.assertEqual(claim["agent"], detector.AGENT)
        refs = {ref["role"]: (ref["id"], ref["revision_id"]) for ref in claim["refs"]}
        self.assertEqual(refs, {
            "source": (self.objects["session"]["object_id"], self.objects["session"]["revision_id"]),
            "stream": (stream["object_id"], stream["revision_id"]),
            "run": (answer["run"]["object_id"], answer["run"]["revision_id"])})

        run = self.show(answer["run"]["object_id"])
        self.assertEqual(run["payload"]["method"], {"id": detector.METHOD_ID, "version": detector.METHOD_VERSION})
        self.assertEqual([s["name"] for s in run["payload"]["software"]][-1], "numpy")
        used = sorted((ref["id"], ref["revision_id"]) for ref in run["refs"] if ref["role"] == "used")
        self.assertEqual(used, sorted([refs["source"], refs["stream"]]))
        parameters = self.show(answer["parameters"]["object_id"])
        data = adapter.canonical(detector.parameters("jam"))
        self.assertEqual(parameters["payload"]["content"], {"digest": adapter.digest_of(data), "size": len(data)})

        from world.world import _record_uri, build_projection, query
        projection = build_projection(support.export(self.root))
        rows = query(projection, f"""
            PREFIX prov: <{PROV}> PREFIX oa: <{OA}>
            SELECT ?run ?body WHERE {{ GRAPH ?g {{
              <{_record_uri(projection.archive_id, output["revision_id"]).value}>
                prov:wasGeneratedBy ?run ; oa:hasBody ?body . }} }}""").data["results"]["bindings"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["run"]["value"],
                         _record_uri(projection.archive_id, answer["run"]["revision_id"]).value)
        self.assertEqual(json.loads(rows[0]["body"]["value"])["spans"], claim["payload"]["spans"])

    def test_a_rerun_on_unchanged_inputs_writes_nothing_and_decodes_nothing(self) -> None:
        self.probed("session")
        code, first = self.enrich("detect", "session")
        self.assertEqual(code, 0, first)
        before = support.head(self.root)
        marker = self.tmp / "decoded"
        bomb = fake_program(self.tmp, "ffmpeg-bomb",
                            f'if [ "$1" = "-version" ]; then exec "{support.FFMPEG}" -version; fi\n'
                            f'touch "{marker}"; exit 99\n')
        code, again = self.enrich("detect", "session", ffmpeg=bomb)
        self.assertEqual((code, again["status"]), (0, "unchanged"), again)
        self.assertEqual(again["run"], first["run"])
        self.assertFalse(marker.exists())
        self.assertEqual(support.head(self.root), before)

    def test_another_preset_is_another_run_with_its_own_parameters(self) -> None:
        self.probed("session")
        code, jam = self.enrich("detect", "session")
        self.assertEqual(code, 0, jam)
        code, show = self.enrich("detect", "session", "--preset", "show")
        self.assertEqual((code, show["status"]), (0, "committed"), show)
        self.assertNotEqual(show["run"]["object_id"], jam["run"]["object_id"])
        self.assertNotEqual(show["parameters"]["object_id"], jam["parameters"]["object_id"])
        self.assertFalse(show["parameters"]["reused"])
        # Both runs' suggestions remain; neither supersedes the other.
        self.show(jam["outputs"][0]["object_id"])
        self.assertEqual(self.show(show["outputs"][0]["object_id"])["supersedes"], [])

    def test_a_dry_run_predicts_and_writes_nothing(self) -> None:
        self.probed("session")
        before = support.head(self.root)
        code, answer = self.enrich("detect", "session", "--dry-run")
        self.assertEqual((code, answer["status"]), (0, "predicted"), answer)
        self.assertEqual(len(answer["revision_ids"]), 3)
        self.assertEqual(support.head(self.root), before)

    def test_an_artifact_without_a_stream_claim_is_refused(self) -> None:
        before = support.head(self.root)
        code, answer = self.enrich("detect", "session")
        self.assertEqual(code, 1)
        self.assertIn("run enrich probe first", answer["detail"])
        self.assertEqual(support.head(self.root), before)

    def test_several_stream_claims_are_never_chosen_between_silently(self) -> None:
        streams = self.probed("two")["outputs"]
        self.assertEqual(len(streams), 2)
        other = self.probed("session")["outputs"][0]["object_id"]
        before = support.head(self.root)
        code, answer = self.enrich("detect", "two")
        self.assertEqual(code, 1)
        self.assertIn("name one with --stream", answer["detail"])
        # Named, the stream is measured, and its half second fills no loudness window pair.
        code, answer = self.enrich("detect", "two", "--stream", streams[1]["object_id"])
        self.assertEqual(code, 1)
        self.assertIn("loudness signal cannot be computed", answer["detail"])
        # A claim about another Artifact is not this Artifact's stream.
        code, answer = self.enrich("detect", "two", "--stream", other)
        self.assertEqual(code, 1)
        self.assertIn("is not an audio_stream claim about revision", answer["detail"])
        self.assertEqual(support.head(self.root), before)

    def test_a_signal_that_cannot_be_computed_proposes_nothing(self) -> None:
        self.probed("short")
        before = support.head(self.root)
        code, answer = self.enrich("detect", "short")
        self.assertEqual(code, 1)
        self.assertIn("chroma_novelty signal cannot be computed", answer["detail"])
        self.assertEqual(support.head(self.root), before)

    def test_door_refuses_suggestions_that_do_not_fit_the_stream(self) -> None:
        # The detector never produces these; Door, not the detector, is what
        # keeps a span inside the pinned stream and at its rate (ADR 0066).
        import enrich
        stream = self.probed("session")["outputs"][0]
        sample_count = self.show(stream["object_id"])["payload"]["sample_count"]
        real = detector.detect

        def past_the_end(*args: object, **kwargs: object) -> detector.Detection:
            found = real(*args, **kwargs)
            # Anchor: the real detection ends exactly at the stream's count.
            self.assertEqual(found.spans[-1][1], sample_count)
            start, end, kind = found.spans[-1]
            found.spans[-1] = (start, end + 1, kind)
            return found

        def other_rate(*args: object, **kwargs: object) -> detector.Detection:
            found = real(*args, **kwargs)
            self.assertEqual(found.sample_rate, 8000)
            found.sample_rate = 16000
            return found

        cases = {"end_sample must not exceed the stream claim's sample_count": past_the_end,
                 "sample_rate must equal the stream claim's sample_rate": other_rate}
        before = support.head(self.root)
        for expected, fake in cases.items():
            with self.subTest(expected=expected):
                errors, printed = io.StringIO(), io.StringIO()
                with mock.patch.object(detector, "detect", fake), redirect_stderr(errors), redirect_stdout(printed):
                    code = enrich.main(["detect", "--archive", str(self.root), "--artifact",
                                        self.objects["session"]["object_id"], "--door", support.DOOR,
                                        "--archive-command", support.ARCHIVE, "--ffmpeg", support.FFMPEG,
                                        "--staging", str(self.staging)])
                self.assertEqual((code, printed.getvalue()), (1, ""))
                detail = json.loads(errors.getvalue())["detail"]
                self.assertIn("door admit failed", detail)
                self.assertIn(expected, detail)
                self.assertEqual(support.head(self.root), before)

    def test_an_unknown_preset_is_refused_before_decoding(self) -> None:
        self.probed("session")
        code, answer = self.enrich("detect", "session", "--preset", "strict-silence")
        self.assertEqual(code, 1)
        self.assertIn("unknown preset", answer["detail"])

    def test_an_object_that_is_not_an_artifact_is_refused(self) -> None:
        code, answer = self.enrich("detect", "operator")
        self.assertEqual(code, 1)
        self.assertIn("not an Artifact", answer["detail"])


if __name__ == "__main__":
    unittest.main()
