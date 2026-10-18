"""`enrich match-windows` and the calibration harness end to end: a scratch
archive built through the real Door, span suggestions proposed by the real
adapter, matches admitted by the real Door, and the claims read back (ADRs
0071, 0072, amendment of 2026-10-18)."""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import adapter
import cover
import numpy

import enrich
import support
import windows
from support import needs_door
from test_cover import needs_essentia
from test_match_cli import link, occurrence, selection, work
from test_probe import fake_program

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

RATE = 44100
PARTS = [{"id": "archive.claim/session_parts", "version": "1"}]
TEST_AGENT = "pkg:generic/archive-enrich-test@0.1.0"


def _bomb(*_, **__):
    raise AssertionError("must not be called")


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
    may be its own evidence. With NEAR_TIE, Work Z has P once more, so P's
    window is a near tie between Works X and Z."""

    NEAR_TIE = False

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
        z1_samples = support.progression_samples(support.PROGRESSION, RATE, transpose=5, seed=6)
        z1 = support.write_wav(self.tmp / "z1.wav", z1_samples, RATE)
        p_start, p_end = second, second + len(p_up)
        q_start = p_end + len(talk)
        q_end = q_start + len(q)
        self.p, self.q = (p_start, p_end), (q_start, q_end)
        half_p, half_q = (p_start + p_end) // 2, (q_start + q_end) // 2
        self.spans = [(0, p_start, "quiet"), (p_start, half_p, "music"), (half_p, p_end, "music"),
                      (p_end, q_start, "talk"), (q_start, half_q, "music"), (half_q, q_end, "unknown"),
                      (q_end, len(recording), "quiet")]
        names = ["x1", "x2", "y1", "short", "self", "mic"] + (["z1"] if self.NEAR_TIE else [])
        steps = (
            {"name": "works and session", "command": "admit", "records": [
                work("work-x", "Test Work X"), work("work-y", "Test Work Y"), work("work-z", "Test Work Z"),
                session("session")]},
            {"name": "parts", "command": "admit", "records": [
                parts("parts-lr", "session", ["query"]), parts("parts-mic", "session", ["mic"])]},
            {"name": "selections", "command": "admit", "records": [
                selection("x1-selection", "x1", [(0, len(x1_samples))], 48000),
                selection("x2-selection", "x2", [(0, len(x2_samples))], RATE),
                selection("y1-selection", "y1", [(0, len(y1_samples))], 48000),
                selection("z1-selection", "z1", [(0, len(z1_samples))], RATE),
                selection("short-selection", "y1", [(0, 4800)], 48000),
                selection("self-selection", "query", [(p_start, p_end)], RATE),
                selection("mic-selection", "mic", [(p_start, p_end)], RATE)]},
            {"name": "occurrences", "command": "admit", "records": [
                occurrence(f"{name}-occurrence", f"{name}-selection") for name in names]},
            {"name": "links", "command": "admit", "records": [
                link("link-x1", "x1-occurrence", "work-x"), link("link-x2", "x2-occurrence", "work-x"),
                link("link-y1", "y1-occurrence", "work-y"), link("link-short", "short-occurrence", "work-y"),
                link("link-self", "self-occurrence", "work-x"), link("link-mic", "mic-occurrence", "work-x"),
                *([link("link-z1", "z1-occurrence", "work-z")] if self.NEAR_TIE else [])]},
        )
        self.root, manifest = support.build_archive(
            self, {"query": query, "mic": mic, "x1": x1, "x2": x2, "y1": y1, "z1": z1}, steps)
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

    def arguments(self, *extra: str, suggestions: str | None = None, ffmpeg: str | None = None,
                  root: Path | None = None) -> list[str]:
        return ["match-windows", "--archive", str(root or self.root),
                "--suggestions", suggestions or self.suggestions["object_id"], "--staging", str(self.staging),
                "--door", support.DOOR, "--archive-command", support.ARCHIVE, "--ffmpeg", ffmpeg or support.FFMPEG,
                "--ffprobe", support.FFPROBE, *extra]

    def enrich(self, *extra: str, suggestions: str | None = None, ffmpeg: str | None = None,
               root: Path | None = None) -> tuple[int, dict]:
        completed = subprocess.run([sys.executable, str(HERE / "enrich.py"),
                                    *self.arguments(*extra, suggestions=suggestions, ffmpeg=ffmpeg, root=root)],
                                   capture_output=True, text=True, env=self.env)
        return completed.returncode, json.loads(completed.stdout or completed.stderr)

    def enrich_here(self, *extra: str) -> tuple[int, dict]:
        """The command run in this process, through the real dispatcher, so
        that what it calls can be watched."""
        out, err = io.StringIO(), io.StringIO()
        with patch.dict("os.environ", self.env), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = enrich.main(self.arguments(*extra))
        return code, json.loads(out.getvalue() or err.getvalue())

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

    def calibrate(self, *arguments: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(HERE / "calibrate_windows.py"), *arguments],
                              capture_output=True, text=True, env=self.env)

    def archive_arguments(self) -> list[str]:
        return ["--archive", str(self.root), "--archive-command", support.ARCHIVE, "--ffmpeg", support.FFMPEG,
                "--ffprobe", support.FFPROBE, "--source", self.pin("query")["object_id"]]

    def claims(self, answer: dict) -> dict[str, dict]:
        """The committed claims, by Work."""
        by_work = {}
        for output in answer["outputs"]:
            record = self.show(output["object_id"])
            [work_ref] = [r for r in record["refs"] if r["role"] == "work"]
            self.assertNotIn(work_ref["id"], by_work)
            by_work[work_ref["id"]] = record
        return by_work


class MatchWindowsClaims(MatchWindowsCommand):
    def test_each_song_is_claimed_once_for_its_closest_work_in_whole_spans(self) -> None:
        before = support.head(self.root)
        code, answer = self.enrich()
        self.assertEqual((code, answer["status"]), (0, "committed"), answer)
        self.assertNotEqual(support.head(self.root), before)
        self.assertEqual(len(answer["windows"]), 2)
        by_work = self.claims(answer)
        self.assertEqual(sorted(by_work), sorted([self.pin("work-x")["object_id"], self.pin("work-y")["object_id"]]))
        edges = {edge for start, end, _ in self.spans for edge in (start, end)}

        x = by_work[self.pin("work-x")["object_id"]]
        [x_window] = [w for w in answer["windows"] if w["window"]["start_sample"] < self.p[1]]
        self.assertEqual(x["agent"], cover.AGENT)
        self.assertEqual(x["contracts"][0]["id"], "archive.claim/work_match")
        self.assertEqual(x["contracts"][0]["version"], "3")
        self.assertEqual(x_window["path"], "quarter_rate")
        self.assertEqual(x["payload"], {"method": {"id": "archive-enrich/whole-recording-serra09", "version": "1"},
                                        "score": x_window["proposed"]["score"],
                                        "scale": "archive-enrich/whole-recording-serra09/quarter-rate-distance",
                                        "direction": "lower_is_closer", "sample_rate": RATE,
                                        "window": x_window["window"]})
        self.assertLessEqual(Decimal(x_window["proposed"]["score"]), Decimal("0.118"))
        # The window is whole suggested spans around P.
        self.assertIn(x["payload"]["window"]["start_sample"], edges)
        self.assertIn(x["payload"]["window"]["end_sample"], edges)
        self.assertEqual(x["payload"]["window"], {"start_sample": self.p[0], "end_sample": self.p[1]})
        refs = {(r["role"], r["id"], r["revision_id"]) for r in x["refs"]}
        self.assertIn(("suggestions", self.suggestions["object_id"], self.suggestions["revision_id"]), refs)
        self.assertIn(("work", self.pin("work-x")["object_id"], self.pin("work-x")["revision_id"]), refs)
        # Work X's corpus is its occurrences that aligned: never the
        # recording itself or its other microphone.
        corpus = sorted((r["id"], r["revision_id"]) for r in x["refs"] if r["role"] == "corpus")
        self.assertEqual(corpus, self.pins("x1-occurrence", "x2-occurrence"))

        y = by_work[self.pin("work-y")["object_id"]]
        self.assertEqual(y["payload"]["window"], {"start_sample": self.q[0], "end_sample": self.q[1]})
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
        blas_name, blas_version = cover.blas()
        self.assertEqual(run["payload"]["software"][-4:],
                         [{"name": "essentia", "version": cover.ESSENTIA_BUILD},
                          {"name": "numpy", "version": numpy.__version__},
                          {"name": blas_name, "version": blas_version},
                          {"name": "blas-runtime", "version": cover.blas_runtime()}])
        self.assertTrue(cover.blas_runtime().endswith(", 1 thread"))
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
        rows = world_query(projection, f"""
            SELECT ?o WHERE {{ GRAPH ?g {{ <{match_uri}:window> ?p ?o . }} }}""").data["results"]["bindings"]
        self.assertEqual(sorted(int(row["o"]["value"]) for row in rows), sorted([*self.p, RATE]))

    def test_the_dispatcher_reaches_the_whole_recording_search_and_never_the_excluded(self) -> None:
        excluded = {self.pin("self-occurrence")["object_id"], self.pin("mic-occurrence")["object_id"]}
        searched, seen = [], set()
        real_search, real_candidates = windows.search_recording, windows.candidates_of
        real_distances, real_exact = cover.QuarterRate.distances, cover.Analyser.distance

        def search(*args, **kwargs):
            searched.append(sorted(args[2]))
            self.assertFalse(excluded & set(args[2]))
            return real_search(*args, **kwargs)

        def candidates(recording, stacks, frames, key, reference, search=None):
            seen.add(key)
            self.assertNotIn(key, excluded)
            return real_candidates(recording, stacks, frames, key, reference, search)

        def distances(quarter, query, keys):
            seen.update(keys)
            self.assertFalse(excluded & set(keys))
            return real_distances(quarter, query, keys)
        with patch.object(windows, "search_recording", search), patch.object(windows, "candidates_of", candidates), \
                patch.object(cover.QuarterRate, "distances", distances), patch.object(cover.Analyser, "distance", _bomb):
            code, answer = self.enrich_here("--dry-run")
        self.assertEqual((code, answer["status"]), (0, "predicted"), answer)
        self.assertEqual(len(searched), 1)
        # Anchor: the allowed occurrences were all reached, so the bomb could have gone off.
        allowed = {self.pin(k)["object_id"] for k in ("x1-occurrence", "x2-occurrence", "y1-occurrence")}
        self.assertEqual(seen, allowed)
        self.assertEqual(set(searched[0]), allowed)

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
        self.assertEqual(one["windows"], two["windows"])
        self.assertTrue(any(w["proposed"] for w in one["windows"]))

    def test_a_window_crosses_a_talk_span_inside_a_song(self) -> None:
        a, b = self.p[0] + 10 * RATE, self.p[0] + 13 * RATE
        crossing = self.suggest([self.spans[0], (self.p[0], a, "music"), (a, b, "talk"), (b, self.p[1], "music"),
                                 *self.spans[3:]])
        code, answer = self.enrich("--dry-run", suggestions=crossing["object_id"])
        self.assertEqual((code, answer["status"]), (0, "predicted"), answer)
        [p] = [w for w in answer["windows"] if w["window"]["start_sample"] < self.p[1]]
        self.assertEqual(p["window"], {"start_sample": self.p[0], "end_sample": self.p[1]})
        self.assertTrue(p["proposed"])

    def test_no_music_spans_proposes_nothing(self) -> None:
        talkative = self.suggest([(s, e, "talk" if c in ("music", "unknown") else c) for s, e, c in self.spans])
        head = support.head(self.root)
        code, answer = self.enrich(suggestions=talkative["object_id"])
        self.assertEqual((code, answer["status"]), (3, "nothing_proposed"), answer)
        self.assertEqual(answer["windows"], [])
        self.assertGreater(answer["hits"], 0)  # anchor: the songs were heard
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

    def test_calibration_judges_each_kept_window_against_what_was_accepted(self) -> None:
        head = support.head(self.root)
        out = self.tmp / "calibration.json"
        completed = self.calibrate(*self.archive_arguments(), "--out", str(out))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(support.head(self.root), head)
        report = json.loads(out.read_text())
        for rule in ("lenient", "strict"):
            [source] = report[rule]["sources"]
            # P is accepted on the recording itself as Work X; Q is not accepted.
            judgments = {tuple(w["closest_works"]): w["judgment"] for w in source["windows"]}
            self.assertEqual(judgments, {(self.pin("work-x")["object_id"],): "correct",
                                         (self.pin("work-y")["object_id"],): "unaccepted"})
            [accepted] = source["accepted"]
            self.assertEqual(accepted["occurrence"], self.pin("self-occurrence")["object_id"])
            self.assertTrue(accepted["reached"])
            self.assertTrue(accepted["suggested"])
        summary = json.loads(completed.stdout)
        self.assertEqual({rule: (s["suggested_correct"], s["suggested_wrong"], s["suggested_unaccepted"],
                                 s["accepted_suggested"]) for rule, s in summary.items()},
                         {"lenient": (1, 0, 1, 1), "strict": (1, 0, 1, 1)})
        # The recording's own occurrence and its other microphone's are not
        # its corpus: four of six accepted occurrences remain, one too short.
        self.assertEqual(report["corpus_sizes"], {self.pin("query")["object_id"]: 3})
        self.assertEqual(list(report["unmeasured"]), [self.pin("short-occurrence")["object_id"]])
        self.assertEqual(report["software"], [[list(s) for s in cover.software()]])

        # Split across machines: measured once here, each recording searched
        # from the workset alone, then judged. The report is the same.
        workset = self.tmp / "workset"
        measured = self.calibrate("measure", *self.archive_arguments(), "--jobs", "2", "--workset", str(workset))
        self.assertEqual(measured.returncode, 0, measured.stderr)
        plan = json.loads(measured.stdout)
        self.assertEqual(plan["sources"], [0])
        self.assertEqual(support.head(self.root), head)
        searched = self.calibrate("search", "--workset", str(workset), "--digest", plan["digest"],
                                  "--source-index", "0")
        self.assertEqual(searched.returncode, 0, searched.stderr)
        self.assertEqual(json.loads(searched.stdout)["needs_exact"], [])
        results = self.tmp / "results.jsonl"
        results.write_text(searched.stdout)
        split_out = self.tmp / "split.json"
        judged = self.calibrate("judge", "--workset", str(workset), "--results", str(results), "--out", str(split_out))
        self.assertEqual(judged.returncode, 0, judged.stderr)
        self.assertEqual(json.loads(split_out.read_text()), report)

        # A recording without a result, twice, a workset that is not the one
        # named, a changed file, and a per-window workset are refused.
        results.write_text("")
        judged = self.calibrate("judge", "--workset", str(workset), "--results", str(results))
        self.assertEqual(judged.returncode, 1)
        self.assertIn("1 recordings have no result", judged.stderr)
        results.write_text(searched.stdout * 2)
        judged = self.calibrate("judge", "--workset", str(workset), "--results", str(results))
        self.assertIn("more than one result", judged.stderr)
        searched = self.calibrate("search", "--workset", str(workset), "--digest", "sha256:" + "0" * 64,
                                  "--source-index", "0")
        self.assertEqual(searched.returncode, 1)
        self.assertIn("is not workset", searched.stderr)
        with (workset / "recording-0.npz").open("ab") as handle:
            handle.write(b"x")
        searched = self.calibrate("search", "--workset", str(workset), "--digest", plan["digest"],
                                  "--source-index", "0")
        self.assertEqual(searched.returncode, 1)
        self.assertIn("is not the file its manifest names", searched.stderr)
        manifest = json.loads((workset / "manifest.json").read_text())
        del manifest["format"]
        (workset / "manifest.json").write_text(json.dumps(manifest))
        judged = self.calibrate("judge", "--workset", str(workset), "--results", str(results))
        self.assertEqual(judged.returncode, 1)
        self.assertIn("is not a whole-recording/1 workset", judged.stderr)

        refused = self.calibrate("--archive", str(self.root), "--archive-command", support.ARCHIVE,
                                 "--source", self.pin("work-x")["object_id"])
        self.assertEqual(refused.returncode, 1)
        self.assertIn("no accepted occurrence is cut from", refused.stderr)


class MatchWindowsNearTie(MatchWindowsCommand):
    NEAR_TIE = True

    def test_a_near_tie_is_decided_by_the_exact_distance_on_fresh_audio(self) -> None:
        decoded = []
        real_features = cover.Comparer.features

        def features(comparer, pieces):
            decoded.append([(p.start_sample, p.end_sample) for p in pieces])
            return real_features(comparer, pieces)
        with patch.object(cover.Comparer, "features", features):
            code, answer = self.enrich_here()
        self.assertEqual((code, answer["status"]), (0, "committed"), answer)
        by_path = {w["path"]: w for w in answer["windows"]}
        self.assertEqual(sorted(by_path), ["exact", "quarter_rate"])
        tie = by_path["exact"]
        self.assertEqual(tie["window"], {"start_sample": self.p[0], "end_sample": self.p[1]})
        # The window, and nothing else, was decoded afresh (the corpus is
        # measured before the recording).
        self.assertEqual(decoded[-1], [self.p])
        self.assertNotIn([self.q], decoded)
        # Anchor: Works X and Z tie on the quarter-rate scale.
        self.assertEqual(tie["quarter_rate_closest"][0]["distance"], tie["quarter_rate_closest"][1]["distance"])
        # The comparisons behind the near tie are kept in the answer.
        works = {self.pin("work-x")["object_id"], self.pin("work-z")["object_id"]}
        self.assertEqual({w["work"] for w in tie["quarter_rate_within"]}, works)
        self.assertLess(tie["quarter_rate_margin"], float(cover.SEARCH["near_tie_margin"]))
        self.assertIn(self.pin("z1-occurrence")["object_id"], tie["compared_exactly"])
        by_work = self.claims(answer)
        [tie_claim] = [c for c in by_work.values() if c["payload"]["window"] == tie["window"]]
        self.assertEqual(tie_claim["payload"]["scale"], "archive-enrich/essentia-serra09/smaller-asymmetric-distance")
        self.assertEqual(tie_claim["payload"]["method"], {"id": cover.METHOD_ID, "version": cover.METHOD_VERSION})
        self.assertLessEqual(Decimal(tie_claim["payload"]["score"]), Decimal("0.08"))
        [other] = [c for c in by_work.values() if c is not tie_claim]
        self.assertEqual(other["payload"]["scale"], cover.QUARTER_RATE_SCALE)

    def test_a_second_work_within_the_limit_is_kept_in_the_answer_and_never_claimed(self) -> None:
        # No margin, so no near tie: the quarter-rate distance decides P's
        # window alone, with Works X and Z both close.
        with patch.dict(cover.SEARCH, near_tie_margin="0"):
            code, answer = self.enrich_here()
        self.assertEqual((code, answer["status"]), (0, "committed"), answer)
        [tie] = [w for w in answer["windows"] if w["window"] == {"start_sample": self.p[0], "end_sample": self.p[1]}]
        self.assertEqual(tie["path"], "quarter_rate")
        work_x, work_z = self.pin("work-x")["object_id"], self.pin("work-z")["object_id"]
        x_keys = {self.pin(k)["object_id"] for k in ("x1-occurrence", "x2-occurrence")}
        z_key = self.pin("z1-occurrence")["object_id"]
        # Anchor: both Works were within the limit, by the occurrences the
        # window was compared with, before anything decides which is kept.
        limit = Decimal(cover.SEARCH["suggestion_distance"])
        close = {o["occurrence"] for o in tie["closest"] if Decimal(o["distance"]) <= limit}
        self.assertTrue(close & x_keys, tie["closest"])
        self.assertIn(z_key, close, tie["closest"])

        claimed = [c for c in self.claims(answer).values() if c["payload"]["window"] == tie["window"]]
        self.assertEqual(len(claimed), 1, claimed)
        [claimed_work] = [r["id"] for r in claimed[0]["refs"] if r["role"] == "work"]
        self.assertEqual(claimed_work, tie["proposed"]["work"]["object_id"])
        [kept] = {work_x, work_z} - {claimed_work}
        # The other Work is kept in the answer, after the claimed one, with
        # its closest occurrence's score.
        self.assertEqual([w["work"] for w in tie["within"]], [claimed_work, kept])
        self.assertEqual(tie["within"][0]["distance"], tie["proposed"]["score"])
        self.assertLessEqual(Decimal(tie["within"][1]["distance"]), limit)
        self.assertGreaterEqual(tie["work_margin"], 0)
        # Every claim the run made is one window's closest Work.
        self.assertEqual(len(answer["outputs"]), sum(1 for w in answer["windows"] if w["proposed"]))
        # The capture import keeps the answer's windows, and counts the claims.
        from capture import capture
        report = capture.match_report(answer)
        self.assertEqual(report["proposed"], len(answer["outputs"]))
        self.assertEqual(report["windows"], answer["windows"])
        # It is regenerable: the recorded run's answer, searched again,
        # proposing nothing.
        head = support.head(self.root)
        with patch.dict(cover.SEARCH, near_tie_margin="0"):
            code, again = self.enrich_here("--regenerate")
        self.assertEqual((code, again["status"]), (0, "regenerated"), again)
        self.assertEqual(again["run"], answer["run"])  # anchor: the run was recorded
        self.assertEqual(again["windows"], answer["windows"])
        self.assertEqual(support.head(self.root), head)

    def test_the_calibration_split_measures_the_near_tie_where_the_audio_is(self) -> None:
        head = support.head(self.root)
        out = self.tmp / "calibration.json"
        completed = self.calibrate(*self.archive_arguments(), "--out", str(out))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(out.read_text())
        workset = self.tmp / "workset"
        measured = self.calibrate("measure", *self.archive_arguments(), "--workset", str(workset))
        self.assertEqual(measured.returncode, 0, measured.stderr)
        plan = json.loads(measured.stdout)
        first = self.calibrate("search", "--workset", str(workset), "--digest", plan["digest"], "--source-index", "0")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(json.loads(first.stdout)["needs_exact"], [list(self.p)])
        results = self.tmp / "results.jsonl"
        results.write_text(first.stdout)
        judged = self.calibrate("judge", "--workset", str(workset), "--results", str(results))
        self.assertEqual(judged.returncode, 1)
        self.assertIn("still needs 1 windows measured afresh", judged.stderr)
        fresh = self.tmp / "fresh.npz"
        windowed = self.calibrate("measure-windows", "--archive", str(self.root), "--archive-command", support.ARCHIVE,
                                  "--ffmpeg", support.FFMPEG, "--ffprobe", support.FFPROBE, "--workset", str(workset),
                                  "--results", str(results), "--out", str(fresh))
        self.assertEqual(windowed.returncode, 0, windowed.stderr)
        self.assertEqual(json.loads(windowed.stdout)["measured"], 1)
        # A file bound to another workset, or to none, is refused.
        with numpy.load(fresh) as arrays:
            entries = {key: arrays[key] for key in arrays.files}
        self.assertEqual(str(entries["workset"]), plan["digest"])
        for bound in ({"workset": numpy.array("sha256:" + "0" * 64)}, {}):
            foreign = self.tmp / "foreign.npz"
            numpy.savez(foreign, **{k: v for k, v in entries.items() if k != "workset"}, **bound)
            refused = self.calibrate("search", "--workset", str(workset), "--source-index", "0",
                                     "--windows", str(foreign))
            self.assertEqual(refused.returncode, 1)
            self.assertIn("was measured for workset", refused.stderr)
        second = self.calibrate("search", "--workset", str(workset), "--source-index", "0", "--windows", str(fresh))
        self.assertEqual(second.returncode, 0, second.stderr)
        decided = json.loads(second.stdout)
        self.assertEqual(decided["needs_exact"], [])
        self.assertEqual([d["path"] for d in decided["kept"]], ["exact", "quarter_rate"])
        results.write_text(second.stdout)
        split_out = self.tmp / "split.json"
        judged = self.calibrate("judge", "--workset", str(workset), "--results", str(results), "--out", str(split_out))
        self.assertEqual(judged.returncode, 0, judged.stderr)
        self.assertEqual(json.loads(split_out.read_text()), report)
        self.assertEqual(support.head(self.root), head)


@needs_essentia
class JudgeSoftware(unittest.TestCase):
    """A run with other software is another run: judge refuses to merge them."""

    def test_results_searched_with_different_software_are_refused(self) -> None:
        import calibrate_windows
        manifest = {"format": calibrate_windows.FORMAT, "corpus": 0, "skipped_links": 0, "works_of": {},
                    "sources": [{"artifact": a, "accepted": [], "corpus_size": 0} for a in ("a", "b")]}

        def result(index: int, software: list) -> dict:
            return {"format": calibrate_windows.FORMAT, "source": index, "artifact": "ab"[index], "needs_exact": [],
                    "kept": [], "software": software}
        here = [list(s) for s in cover.software()]
        other = [*here[:-1], ["blas-runtime", "another kernel, 1 thread"]]
        # Anchor: the same software is judged.
        report = calibrate_windows.judge(manifest, [result(0, here), result(1, here)])
        self.assertEqual(report["software"], [here])
        with self.assertRaisesRegex(enrich.CommandError, "2 different software sets"):
            calibrate_windows.judge(manifest, [result(0, here), result(1, other)])


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
