"""`enrich match-windows` end to end: a scratch archive built through the real
Door, span suggestions proposed by the real adapter, matches admitted by the
real Door, and the claims read back (ADRs 0071, 0072)."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from decimal import Decimal
from pathlib import Path

import adapter
import cover
import enrich
import support
from support import needs_door
from test_cover import needs_essentia
from test_match_cli import link, occurrence, selection, work
from test_probe import fake_program

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

RATE = 44100
PARTS = [{"id": "archive.claim/session_parts", "version": "1"}]
TEST_AGENT = "pkg:generic/archive-enrich-test@0.1.0"


def session(key: str) -> dict:
    return {"key": key, "namespace": "archive.audio/session", "name": f"test-{key}", "shape": "entity",
            "payload": {"kind": "recording_session", "label": f"Test session {key}"}}


def parts(key: str, session_key: str, artifacts: list[str]) -> dict:
    return {"key": key, "namespace": "archive.claim/session_parts", "name": f"test-{key}", "shape": "claim",
            "contracts": PARTS, "payload": {},
            "refs": [{"role": "session", "pin": session_key}, *({"role": "part", "pin": a} for a in artifacts)]}


@needs_essentia
@needs_door
class MatchWindowsCommand(unittest.TestCase):
    """The recording holds progression P (transposed up two), talk, then
    progression Q. Work X has P twice, in two keys; Work Y has Q, and a
    selection too short to align. Work X also has an occurrence cut from the
    recording itself and one from its session's other microphone: neither
    may be its own evidence."""

    def setUp(self) -> None:
        self.tmp = support.scratch(self, "enrich-windows-")
        self.env = support.environment()
        second = RATE
        p_up = support.progression_samples(support.PROGRESSION, RATE, transpose=2, seed=1)
        q = support.progression_samples(support.OTHER_PROGRESSION, RATE, seed=2)
        floor = [0.0] * second
        # Four seconds of talk-like noise, deterministic.
        talk = [0.05 * ((i * 7919) % 200 - 100) / 100 for i in range(4 * second)]
        recording = [*floor, *p_up, *talk, *q, *floor]
        query = support.write_wav(self.tmp / "query.wav", recording, RATE)
        # The other microphone hears the same performance, more quietly.
        mic = support.write_wav(self.tmp / "mic.wav", [0.8 * v for v in recording], RATE)
        x1_samples = support.progression_samples(support.PROGRESSION, 48000, seed=3)
        x1 = support.write_wav(self.tmp / "x1.wav", x1_samples, 48000)
        x2_samples = support.progression_samples(support.PROGRESSION, RATE, transpose=-2, seed=4)
        x2 = support.write_wav(self.tmp / "x2.wav", x2_samples, RATE)
        y1_samples = support.progression_samples(support.OTHER_PROGRESSION, 48000, seed=5)
        y1 = support.write_wav(self.tmp / "y1.wav", y1_samples, 48000)
        p_start, p_end = second, second + len(p_up)
        q_start = p_end + len(talk)
        q_end = q_start + len(q)
        half_p, half_q = (p_start + p_end) // 2, (q_start + q_end) // 2
        self.spans = [(0, p_start, "quiet"), (p_start, half_p, "music"), (half_p, p_end, "music"),
                      (p_end, q_start, "talk"), (q_start, half_q, "music"), (half_q, q_end, "unknown"),
                      (q_end, len(recording), "quiet")]
        steps = (
            {"name": "works and session", "command": "admit", "records": [
                work("work-x", "Test Work X"), work("work-y", "Test Work Y"), session("session")]},
            {"name": "parts", "command": "admit", "records": [
                parts("parts-lr", "session", ["query"]), parts("parts-mic", "session", ["mic"])]},
            {"name": "selections", "command": "admit", "records": [
                selection("x1-selection", "x1", [(0, len(x1_samples))], 48000),
                selection("x2-selection", "x2", [(0, len(x2_samples))], RATE),
                selection("y1-selection", "y1", [(0, len(y1_samples))], 48000),
                selection("short-selection", "y1", [(0, 4800)], 48000),
                selection("self-selection", "query", [(p_start, p_end)], RATE),
                selection("mic-selection", "mic", [(p_start, p_end)], RATE)]},
            {"name": "occurrences", "command": "admit", "records": [
                occurrence(f"{name}-occurrence", f"{name}-selection")
                for name in ("x1", "x2", "y1", "short", "self", "mic")]},
            {"name": "links", "command": "admit", "records": [
                link("link-x1", "x1-occurrence", "work-x"), link("link-x2", "x2-occurrence", "work-x"),
                link("link-y1", "y1-occurrence", "work-y"), link("link-short", "short-occurrence", "work-y"),
                link("link-self", "self-occurrence", "work-x"), link("link-mic", "mic-occurrence", "work-x")]},
        )
        self.root, manifest = support.build_archive(self, {"query": query, "mic": mic, "x1": x1, "x2": x2, "y1": y1},
                                                    steps)
        self.objects = manifest["objects"]
        self.staging = self.tmp / "staging"
        self.suggestions = self.suggest(self.spans)

    def suggest(self, spans: list[tuple[int, int, str]]) -> dict[str, str]:
        """A stream claim from the real probe, then span suggestions proposed
        through the real adapter, as the detector proposes them."""
        probed = subprocess.run(
            [sys.executable, str(HERE / "enrich.py"), "probe", "--archive", str(self.root), "--artifact",
             self.objects["query"]["object_id"], "--staging", str(self.staging), "--door", support.DOOR,
             "--archive-command", support.ARCHIVE, "--ffmpeg", support.FFMPEG, "--ffprobe", support.FFPROBE],
            capture_output=True, text=True, env=self.env)
        self.assertIn(probed.returncode, (0,), probed.stderr)
        reader = adapter.ArchiveReader(self.root, support.ARCHIVE, self.env)
        source = reader.head(self.objects["query"]["object_id"])
        [stream] = enrich.stream_claims(reader, source)
        moment = adapter.now()
        run = adapter.RunResult(
            agent=TEST_AGENT, method_id="archive-enrich/test-spans", method_version=str(len(spans)),
            software=(("test", "1"),), started_at=moment, ended_at=moment,
            used=(adapter.Pin("used", source["object_id"], source["revision_id"]),
                  adapter.Pin("used", stream["object_id"], stream["revision_id"])),
            outputs=(adapter.Output(
                contract=enrich.SPANS_CONTRACT, name="spans",
                payload={"sample_rate": RATE, "spans": [{"start_sample": s, "end_sample": e, "class": c}
                                                        for s, e, c in spans]},
                refs=(adapter.Pin("source", source["object_id"], source["revision_id"]),
                      adapter.Pin("stream", stream["object_id"], stream["revision_id"]))),))
        admission = adapter.propose(reader, run, door=support.DOOR, env=self.env)
        self.assertEqual(admission.status, "committed")
        [output] = admission.outputs
        return output

    def enrich(self, *extra: str, suggestions: str | None = None, ffmpeg: str | None = None,
               root: Path | None = None) -> tuple[int, dict]:
        completed = subprocess.run(
            [sys.executable, str(HERE / "enrich.py"), "match-windows", "--archive", str(root or self.root),
             "--suggestions", suggestions or self.suggestions["object_id"], "--staging", str(self.staging),
             "--door", support.DOOR, "--archive-command", support.ARCHIVE, "--ffmpeg", ffmpeg or support.FFMPEG,
             "--ffprobe", support.FFPROBE, *extra], capture_output=True, text=True, env=self.env)
        return completed.returncode, json.loads(completed.stdout or completed.stderr)

    def show(self, selector: str) -> dict:
        shown = subprocess.run([support.ARCHIVE, "show", selector], cwd=self.root, capture_output=True, text=True,
                               env=self.env)
        self.assertEqual(shown.returncode, 0, shown.stdout)
        [record] = json.loads(shown.stdout)["records"]
        return record

    def pin(self, key: str) -> dict:
        return self.objects[key]

    def pins(self, *keys: str) -> list[tuple[str, str]]:
        return sorted((self.pin(k)["object_id"], self.pin(k)["revision_id"]) for k in keys)

    def test_each_run_proposes_its_work_with_the_window_of_its_best_step(self) -> None:
        before = support.head(self.root)
        code, answer = self.enrich()
        self.assertEqual((code, answer["status"]), (0, "committed"), answer)
        self.assertNotEqual(support.head(self.root), before)
        # Two runs: the talk and the quiet spans end them.
        self.assertEqual([run["spans"] for run in answer["runs"]], [[1, 2], [4, 5]])
        by_work = {}
        for output in answer["outputs"]:
            record = self.show(output["object_id"])
            [work_ref] = [r for r in record["refs"] if r["role"] == "work"]
            by_work[work_ref["id"]] = record
        self.assertEqual(sorted(by_work), sorted([self.pin("work-x")["object_id"], self.pin("work-y")["object_id"]]))

        x = by_work[self.pin("work-x")["object_id"]]
        [x_run] = [run for run in answer["runs"] if run["spans"] == [1, 2]]
        [proposed] = x_run["proposed"]
        self.assertEqual(x["agent"], cover.AGENT)
        self.assertEqual(x["contracts"][0]["id"], "archive.claim/work_match")
        self.assertEqual(x["contracts"][0]["version"], "3")
        self.assertEqual(x["payload"], {"method": {"id": cover.METHOD_ID, "version": cover.METHOD_VERSION},
                                        "score": proposed["score"], "scale": cover.SCALE,
                                        "direction": "lower_is_closer", "sample_rate": RATE,
                                        "window": x_run["window"]})
        self.assertLessEqual(Decimal(proposed["score"]), Decimal(cover.SEARCH["suggestion_distance"]))
        # The window is whole suggested spans: here, the whole run.
        self.assertIn(x_run["window"], [{"start_sample": self.spans[1][0], "end_sample": self.spans[1][1]},
                                        {"start_sample": self.spans[1][0], "end_sample": self.spans[2][1]}])
        refs = {(r["role"], r["id"], r["revision_id"]) for r in x["refs"]}
        self.assertIn(("suggestions", self.suggestions["object_id"], self.suggestions["revision_id"]), refs)
        self.assertIn(("work", self.pin("work-x")["object_id"], self.pin("work-x")["revision_id"]), refs)
        corpus = sorted((r["id"], r["revision_id"]) for r in x["refs"] if r["role"] == "corpus")
        # Work X's corpus is what was compared at the window: never the
        # recording itself or its other microphone.
        self.assertTrue(set(corpus) <= set(self.pins("x1-occurrence", "x2-occurrence")), corpus)
        self.assertTrue(corpus)

        y = by_work[self.pin("work-y")["object_id"]]
        [y_run] = [run for run in answer["runs"] if run["spans"] == [4, 5]]
        self.assertEqual(y["payload"]["window"], y_run["window"])
        self.assertEqual([(r["id"], r["revision_id"]) for r in y["refs"] if r["role"] == "corpus"],
                         self.pins("y1-occurrence"))

        skipped = {s.get("link", {}).get("object_id") or s.get("occurrence"): s["reason"] for s in answer["skipped"]}
        session_reason = "the occurrence's audio comes from the compared session"
        self.assertEqual(skipped[self.pin("link-self")["object_id"]], session_reason)
        self.assertEqual(skipped[self.pin("link-mic")["object_id"]], session_reason)
        self.assertIn("the alignment needs more than 9", skipped[self.pin("short-occurrence")["object_id"]])

        [parameters] = [r for r in x["refs"] if r["role"] == "parameters"]
        data = adapter.canonical(cover.parameters())
        self.assertEqual(self.show(parameters["id"])["payload"]["content"],
                         {"digest": adapter.digest_of(data), "size": len(data)})
        [run_ref] = [r for r in x["refs"] if r["role"] == "run"]
        run = self.show(run_ref["id"])
        self.assertEqual(run["payload"]["method"], {"id": cover.METHOD_ID, "version": cover.METHOD_VERSION})
        self.assertEqual(run["payload"]["software"][-2:],
                         [{"name": "essentia", "version": cover.ESSENTIA_BUILD},
                          {"name": "numpy", "version": __import__("numpy").__version__}])
        used = {(r["id"], r["revision_id"]) for r in run["refs"] if r["role"] == "used"}
        for key in ("query", "x1", "x2", "y1", "x1-occurrence", "x2-occurrence", "y1-occurrence", "short-occurrence",
                    "link-x1", "link-y1", "work-x", "work-y"):
            self.assertIn((self.pin(key)["object_id"], self.pin(key)["revision_id"]), used, key)
        self.assertIn((self.suggestions["object_id"], self.suggestions["revision_id"]), used)

        from world.world import _record_uri, build_projection, query as world_query
        projection = build_projection(support.export(self.root))
        archive_id = projection.archive_id
        match_uri = _record_uri(archive_id, x["revision_id"]).value
        rows = world_query(projection, f"""
            SELECT ?p ?o WHERE {{ GRAPH ?g {{ <{match_uri}> ?p ?o . }} }}""").data["results"]["bindings"]
        values = {row["o"]["value"] for row in rows}
        self.assertIn(_record_uri(archive_id, self.suggestions["revision_id"]).value, values)
        # The window is a node of its own: start, end, and rate.
        rows = world_query(projection, f"""
            SELECT ?o WHERE {{ GRAPH ?g {{ <{match_uri}:window> ?p ?o . }} }}""").data["results"]["bindings"]
        self.assertEqual(sorted(int(row["o"]["value"]) for row in rows),
                         sorted([x_run["window"]["start_sample"], x_run["window"]["end_sample"], RATE]))

    def test_a_rerun_on_unchanged_inputs_writes_and_decodes_nothing(self) -> None:
        code, first = self.enrich()
        self.assertEqual((code, first["status"]), (0, "committed"), first)
        head = support.head(self.root)
        marker = self.tmp / "decoded"
        bomb = fake_program(self.tmp, "ffmpeg-bomb",
                            f'if [ "$1" = "-version" ]; then exec "{support.FFMPEG}" -version; fi\n'
                            f'touch "{marker}"; exit 99\n')
        code, again = self.enrich(ffmpeg=bomb)
        self.assertEqual((code, again["status"]), (0, "unchanged"), again)
        self.assertEqual(again["run"], first["run"])
        self.assertEqual(support.head(self.root), head)
        self.assertFalse(marker.exists())
        # Anchor: other suggestions are other inputs, so the bomb goes off.
        other = self.suggest(self.spans[:3] + [(self.spans[3][0], self.spans[-1][1], "talk")])
        code, changed = self.enrich(suggestions=other["object_id"], ffmpeg=bomb)
        self.assertEqual(code, 1, changed)
        self.assertTrue(marker.exists())

    def test_parallel_workers_find_exactly_what_one_process_finds(self) -> None:
        head = support.head(self.root)
        code, one = self.enrich("--dry-run")
        self.assertEqual((code, one["status"]), (0, "predicted"), one)
        code, two = self.enrich("--dry-run", "--jobs", "2")
        self.assertEqual((code, two["status"]), (0, "predicted"), two)
        self.assertEqual(support.head(self.root), head)
        self.assertEqual(one["runs"], two["runs"])
        self.assertTrue(any(run["proposed"] for run in one["runs"]))

    def test_nothing_within_the_distance_proposes_nothing(self) -> None:
        # Only the talk and quiet are runs' neighbours now: P and Q are both
        # classed talk, so no run exists.
        talkative = self.suggest([(s, e, "talk" if c in ("music", "unknown") else c) for s, e, c in self.spans])
        head = support.head(self.root)
        code, answer = self.enrich(suggestions=talkative["object_id"])
        self.assertEqual((code, answer["status"]), (3, "nothing_proposed"), answer)
        self.assertEqual(answer["runs"], [])
        self.assertEqual(support.head(self.root), head)

    def test_an_empty_corpus_proposes_nothing_and_says_why(self) -> None:
        root, manifest = support.build_archive(self, {"query": self.tmp / "query.wav"})
        self.root, self.objects = root, manifest["objects"]
        bare = self.suggest(self.spans)
        code, answer = self.enrich(suggestions=bare["object_id"])
        self.assertEqual((code, answer["status"]), (3, "nothing_proposed"), answer)
        self.assertIn("the corpus is empty", answer["detail"])

    def test_a_claim_that_is_not_span_suggestions_is_refused(self) -> None:
        code, answer = self.enrich(suggestions=self.pin("x1-occurrence")["object_id"])
        self.assertEqual(code, 1, answer)
        self.assertIn("does not cite archive.claim/span_suggestions version 1", answer["detail"])

    def test_a_corpus_occurrence_that_cannot_be_read_refuses_the_run(self) -> None:
        broken = fake_program(self.tmp, "ffmpeg-broken",
                              f'if [ "$1" = "-version" ]; then exec "{support.FFMPEG}" -version; fi\n'
                              'echo "cannot decode" >&2; exit 1\n')
        head = support.head(self.root)
        code, answer = self.enrich(ffmpeg=broken)
        self.assertEqual(code, 1, answer)
        self.assertIn("could not be measured", answer["detail"])
        self.assertEqual(support.head(self.root), head)


    def test_calibration_judges_each_best_window_against_what_was_accepted(self) -> None:
        head = support.head(self.root)
        out = self.tmp / "calibration.json"
        completed = subprocess.run(
            [sys.executable, str(HERE / "calibrate_windows.py"), "--archive", str(self.root), "--archive-command",
             support.ARCHIVE, "--ffmpeg", support.FFMPEG, "--ffprobe", support.FFPROBE,
             "--source", self.pin("query")["object_id"], "--out", str(out)],
            capture_output=True, text=True, env=self.env)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(support.head(self.root), head)
        report = json.loads(out.read_text())
        [source] = report["sources"]
        self.assertEqual(source["spans"], "archive")
        # P is accepted on the recording itself as Work X; Q is not accepted.
        judgments = {tuple(w["closest_works"]): w["judgment"] for w in source["windows"]}
        self.assertEqual(judgments, {(self.pin("work-x")["object_id"],): "correct",
                                     (self.pin("work-y")["object_id"],): "unaccepted"})
        [accepted] = source["accepted"]
        self.assertEqual(accepted["occurrence"], self.pin("self-occurrence")["object_id"])
        self.assertTrue(accepted["reached"])
        self.assertTrue(accepted["suggested"])
        summary = json.loads(completed.stdout)
        self.assertEqual((summary["suggested_correct"], summary["suggested_wrong"], summary["accepted_suggested"]),
                         (1, 0, 1))
        # The recording's own occurrence and its other microphone's are not
        # its corpus: four of six accepted occurrences remain, one too short.
        self.assertEqual(source["corpus"], 3)
        self.assertEqual(list(report["unmeasured"]), [self.pin("short-occurrence")["object_id"]])

        refused = subprocess.run(
            [sys.executable, str(HERE / "calibrate_windows.py"), "--archive", str(self.root), "--archive-command",
             support.ARCHIVE, "--source", self.pin("work-x")["object_id"]], capture_output=True, text=True,
            env=self.env)
        self.assertEqual(refused.returncode, 1)
        self.assertIn("no accepted occurrence is cut from", refused.stderr)


class SessionArtifacts(unittest.TestCase):
    """The session exclusion follows the session Entity across sources."""

    def test_every_part_of_every_source_of_the_session_is_excluded(self) -> None:
        def claim(object_id: str, session_id: str, part_ids: list[str], head: bool = True) -> dict:
            return {"shape": "claim", "object_id": object_id, "revision_id": "sha256:" + object_id[-1] * 64,
                    "contracts": [{"id": "archive.claim/session_parts", "version": "1"}],
                    "refs": [{"role": "session", "id": session_id}, *({"role": "part", "id": p} for p in part_ids)]}

        superseded = claim("c0", "s1", ["old"])
        reader = adapter.ArchiveReader.from_export(Path("/nonexistent"), {
            "archive_id": "urn:uuid:00000000-0000-4000-8000-000000000000", "spec": "archive/0.0.8", "contracts": [],
            "records": [superseded, {**claim("c0", "s1", ["lr-1"]), "revision_id": "sha256:" + "a" * 64,
                                     "supersedes": [{"revision_id": superseded["revision_id"]}]},
                        claim("c1", "s1", ["lr-1", "lr-2"]), claim("c2", "s1", ["mic-1"]),
                        claim("c3", "s2", ["other"])]})
        self.assertEqual(enrich.session_artifacts(reader, "lr-2"), frozenset({"lr-1", "lr-2", "mic-1"}))
        self.assertEqual(enrich.session_artifacts(reader, "other"), frozenset({"other"}))
        self.assertEqual(enrich.session_artifacts(reader, "alone"), frozenset({"alone"}))
        # A superseded claim's parts are not the session's.
        self.assertNotIn("old", enrich.session_artifacts(reader, "mic-1"))


if __name__ == "__main__":
    unittest.main()
