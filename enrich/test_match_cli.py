"""`enrich match` end to end: a scratch archive built through the real Door,
proposals admitted by the real Door, and the claims read back (ADR 0067)."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from decimal import Decimal
from pathlib import Path

import adapter
import enrich
import match
import support
from support import needs_door
from test_probe import fake_program

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

PROV = "http://www.w3.org/ns/prov#"
SELECTION = [{"id": "archive.claim/audio_selection", "version": "2"}]
OCCURRENCE = [{"id": "archive.claim/song_occurrence", "version": "2"}]
LINK = [{"id": "archive.claim/performance_work", "version": "3"}]


def selection(key: str, artifact: str, spans: list[tuple[int, int]], rate: int) -> dict:
    return {"key": key, "namespace": "archive.claim/audio_selection", "name": f"test-{key}", "shape": "claim",
            "contracts": SELECTION,
            "payload": {"tracks": [{"pieces": [{"start_sample": s, "end_sample": e, "sample_rate": rate}
                                               for s, e in spans]}]},
            "refs": [{"role": "piece", "pin": artifact} for _ in spans]}


def occurrence(key: str, selection_key: str) -> dict:
    return {"key": key, "namespace": "archive.claim/song_occurrence", "name": f"test-{key}", "shape": "claim",
            "contracts": OCCURRENCE, "payload": {}, "refs": [{"role": "selection", "pin": selection_key}]}


def link(key: str, occurrence_key: str, work: str) -> dict:
    return {"key": key, "namespace": "archive.claim/performance_work", "name": f"test-{key}", "shape": "claim",
            "contracts": LINK, "payload": {},
            "refs": [{"role": "performance", "pin": occurrence_key}, {"role": "work", "pin": work}]}


def work(key: str, label: str) -> dict:
    return {"key": key, "namespace": "archive.audio/song", "name": f"song-test-{key}", "shape": "entity",
            "payload": {"kind": "musical_work", "label": label}}


@needs_door
class MatchCommand(unittest.TestCase):
    """Work X has the compared tune in its own key and transposed down, as two
    occurrences; Work Y has another tune. The compared selection is the tune
    transposed up, between silences in a longer recording."""

    def setUp(self) -> None:
        self.tmp = support.scratch(self, "enrich-match-")
        self.env = support.environment()
        silence = [0.0] * 44100
        tune_up = support.tune_samples(support.TUNE, 44100, transpose=3, seed=1)
        query = support.write_wav(self.tmp / "query.wav", [*silence, *tune_up, *silence], 44100)
        x1 = support.write_wav(self.tmp / "x1.wav", support.tune_samples(support.TUNE, 48000, seed=2), 48000)
        x2_samples = support.tune_samples(support.TUNE, 44100, transpose=-2, seed=4)
        x2 = support.write_wav(self.tmp / "x2.wav", x2_samples, 44100)
        y1 = support.write_wav(self.tmp / "y1.wav", support.tune_samples(support.OTHER_TUNE, 48000, seed=3), 48000)
        length_x1, length_y1 = len(support.tune_samples(support.TUNE, 48000)), len(
            support.tune_samples(support.OTHER_TUNE, 48000))
        half = len(x2_samples) // 2
        steps = (
            {"name": "works", "command": "admit", "records": [work("work-x", "Test Work X"),
                                                              work("work-y", "Test Work Y")]},
            {"name": "selections", "command": "admit", "records": [
                selection("query-selection", "query", [(44100, 44100 + len(tune_up))], 44100),
                selection("x1-selection", "x1", [(0, length_x1)], 48000),
                # Two spans of one recording, joined in order.
                selection("x2-selection", "x2", [(0, half), (half, len(x2_samples))], 44100),
                selection("y1-selection", "y1", [(0, length_y1)], 48000),
                selection("past-end", "y1", [(0, length_y1 + 1)], 48000),
                selection("wrong-rate", "y1", [(0, 1000)], 44100)]},
            {"name": "occurrences", "command": "admit", "records": [
                occurrence("query-occurrence", "query-selection"), occurrence("x1-occurrence", "x1-selection"),
                occurrence("x2-occurrence", "x2-selection"), occurrence("y1-occurrence", "y1-selection")]},
            {"name": "links", "command": "admit", "records": [
                link("link-x1", "x1-occurrence", "work-x"), link("link-x2", "x2-occurrence", "work-x"),
                link("link-query", "query-occurrence", "work-x"), link("link-y-old", "y1-occurrence", "work-y")]},
            {"name": "corrected occurrence", "command": "admit", "records": [
                {**occurrence("y1-occurrence-2", "y1-selection"), "revises": "y1-occurrence"}]},
            {"name": "link to the correction", "command": "admit", "records": [
                link("link-y", "y1-occurrence-2", "work-y")]},
        )
        self.root, manifest = support.build_archive(self, {"query": query, "x1": x1, "x2": x2, "y1": y1}, steps)
        self.objects = manifest["objects"]
        self.staging = self.tmp / "staging"

    def enrich(self, *extra: str, selection_key: str | None = "query-selection", ffmpeg: str | None = None,
               root: Path | None = None) -> tuple[int, dict]:
        target = ["--selection", self.objects[selection_key]["object_id"]] if selection_key else []
        completed = subprocess.run(
            [sys.executable, str(HERE / "enrich.py"), "match", "--archive", str(root or self.root), *target,
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

    def best(self, answer: dict) -> dict[str, Decimal]:
        by_object = {self.pin("work-x")["object_id"]: "x", self.pin("work-y")["object_id"]: "y"}
        return {by_object[w["work"]["object_id"]]: Decimal(w["best"]["score"]) for w in answer["works"]}

    def test_the_closer_work_scores_lower_and_each_work_gets_its_best_match(self) -> None:
        before = support.head(self.root)
        code, answer = self.enrich("--limit", "1000", "--staging", str(self.staging))
        self.assertEqual((code, answer["status"]), (0, "committed"), answer)
        self.assertNotEqual(support.head(self.root), before)
        best = self.best(answer)
        self.assertNotEqual(best["x"], best["y"])
        self.assertLess(best["x"], best["y"])
        # One match per Work, though Work X has two occurrences.
        self.assertEqual(len(answer["outputs"]), 2)
        [x_report] = [w for w in answer["works"] if w["work"] == self.pin("work-x")]
        self.assertEqual(len(x_report["scores"]), 2)
        self.assertEqual(x_report["best"]["score"], min((s["score"] for s in x_report["scores"]), key=Decimal))

        matches = {}
        for output in answer["outputs"]:
            record = self.show(output["object_id"])
            [work_ref] = [r for r in record["refs"] if r["role"] == "work"]
            matches[work_ref["id"]] = record
        x = matches[self.pin("work-x")["object_id"]]
        self.assertEqual(x["agent"], match.AGENT)
        self.assertEqual(x["contracts"][0]["id"], "archive.claim/work_match")
        self.assertEqual(x["contracts"][0]["version"], "2")
        self.assertEqual(x["payload"], {"method": {"id": match.METHOD_ID, "version": match.METHOD_VERSION},
                                        "score": x_report["best"]["score"], "scale": match.SCALE,
                                        "direction": "lower_is_closer"})
        refs = {(r["role"], r["id"], r["revision_id"]) for r in x["refs"]}
        query = self.pin("query-selection")
        self.assertIn(("selection", query["object_id"], query["revision_id"]), refs)
        self.assertIn(("work", self.pin("work-x")["object_id"], self.pin("work-x")["revision_id"]), refs)
        corpus = sorted((r["id"], r["revision_id"]) for r in x["refs"] if r["role"] == "corpus")
        self.assertEqual(corpus, sorted((self.pin(k)["object_id"], self.pin(k)["revision_id"])
                                        for k in ("x1-occurrence", "x2-occurrence")))
        # The query's own occurrence is linked to Work X, and is not its own corpus.
        self.assertNotIn(self.pin("query-occurrence")["object_id"], [c for c, _ in corpus])
        [parameters] = [r for r in x["refs"] if r["role"] == "parameters"]
        self.assertEqual(parameters["id"], answer["parameters"]["object_id"])
        data = adapter.canonical(match.parameters("1000"))
        self.assertEqual(self.show(parameters["id"])["payload"]["content"],
                         {"digest": adapter.digest_of(data), "size": len(data)})
        [run_ref] = [r for r in x["refs"] if r["role"] == "run"]
        run = self.show(run_ref["id"])
        self.assertEqual(run["payload"]["method"], {"id": match.METHOD_ID, "version": match.METHOD_VERSION})
        self.assertEqual([s["name"] for s in run["payload"]["software"]][-2:], ["numpy", "scipy"])
        used = {(r["id"], r["revision_id"]) for r in run["refs"] if r["role"] == "used"}
        for key in ("query-selection", "x1-occurrence", "x2-occurrence", "y1-occurrence-2", "link-x1", "link-y",
                    "work-x", "work-y", "query", "x1", "x2", "y1"):
            self.assertIn((self.pin(key)["object_id"], self.pin(key)["revision_id"]), used, key)
        y = matches[self.pin("work-y")["object_id"]]
        y_corpus = [(r["id"], r["revision_id"]) for r in y["refs"] if r["role"] == "corpus"]
        self.assertEqual(y_corpus, [(self.pin("y1-occurrence-2")["object_id"],
                                     self.pin("y1-occurrence-2")["revision_id"])])

        skipped = {s["link"]["object_id"]: s["reason"] for s in answer["skipped"]}
        self.assertEqual(skipped, {self.pin("link-query")["object_id"]: "the occurrence holds the compared audio",
                                   self.pin("link-y-old")["object_id"]:
                                       "the link pins a superseded occurrence revision"})

        from world.world import _record_uri, build_projection, query as world_query
        projection = build_projection(support.export(self.root))
        rows = world_query(projection, f"""
            PREFIX prov: <{PROV}>
            SELECT ?match ?run WHERE {{ GRAPH ?g {{ ?match prov:wasGeneratedBy ?run . }} }}""").data
        generated = {row["match"]["value"]: row["run"]["value"] for row in rows["results"]["bindings"]}
        archive_id = projection.archive_id
        self.assertEqual(generated[_record_uri(archive_id, answer["outputs"][0]["revision_id"]).value],
                         _record_uri(archive_id, answer["run"]["revision_id"]).value)

    def test_only_a_work_at_or_below_the_limit_is_proposed(self) -> None:
        code, predicted = self.enrich("--limit", "1000", "--dry-run", "--staging", str(self.staging))
        self.assertEqual((code, predicted["status"]), (0, "predicted"), predicted)
        best = self.best(predicted)
        self.assertLess(best["x"], best["y"])
        # The limit is inclusive: exactly Work X's best score keeps Work X.
        code, answer = self.enrich("--limit", str(best["x"]), "--staging", str(self.staging))
        self.assertEqual((code, answer["status"]), (0, "committed"), answer)
        self.assertEqual(len(answer["outputs"]), 1)
        [record] = [self.show(answer["outputs"][0]["object_id"])]
        self.assertIn(self.pin("work-x")["object_id"], [r["id"] for r in record["refs"] if r["role"] == "work"])
        self.assertEqual({w["work"]["object_id"]: w["proposed"] for w in answer["works"]},
                         {self.pin("work-x")["object_id"]: True, self.pin("work-y")["object_id"]: False})

    def test_no_work_within_the_limit_proposes_nothing(self) -> None:
        before = support.head(self.root)
        code, answer = self.enrich("--limit", "0", "--staging", str(self.staging))
        self.assertEqual((code, answer["status"]), (3, "nothing_proposed"), answer)
        self.assertEqual(len(answer["works"]), 2)
        self.assertTrue(all(w["best"] is not None and not w["proposed"] for w in answer["works"]))
        self.assertEqual(support.head(self.root), before)
        self.assertFalse(self.staging.exists())

    def test_scores_are_deterministic_across_runs(self) -> None:
        _, first = self.enrich("--limit", "1000", "--dry-run", "--staging", str(self.staging))
        _, second = self.enrich("--limit", "1000", "--dry-run", "--staging", str(self.staging))
        self.assertEqual(first["works"], second["works"])
        self.assertNotEqual(first["run"], second["run"])  # every run is a new object

    def test_a_rerun_on_unchanged_inputs_writes_nothing_and_decodes_nothing(self) -> None:
        code, first = self.enrich("--limit", "1000", "--staging", str(self.staging))
        self.assertEqual(code, 0, first)
        before = support.head(self.root)
        marker = self.tmp / "decoded"
        bomb = fake_program(self.tmp, "ffmpeg-bomb",
                            f'if [ "$1" = "-version" ]; then exec "{support.FFMPEG}" -version; fi\n'
                            f'touch "{marker}"; exit 99\n')
        code, again = self.enrich("--limit", "1000", "--staging", str(self.staging), ffmpeg=bomb)
        self.assertEqual((code, again["status"]), (0, "unchanged"), again)
        self.assertEqual(again["run"], first["run"])
        self.assertFalse(marker.exists())
        self.assertEqual(support.head(self.root), before)
        # Anchor: another limit is another configuration, so the bomb goes off.
        code, changed = self.enrich("--limit", "999", "--staging", str(self.staging), ffmpeg=bomb)
        self.assertEqual(code, 1, changed)
        self.assertTrue(marker.exists())

    def test_an_occurrence_is_compared_by_its_selection(self) -> None:
        code, answer = self.enrich("--limit", "1000", "--dry-run", "--staging", str(self.staging), "--occurrence",
                                   self.pin("query-occurrence")["object_id"], selection_key=None)
        self.assertEqual((code, answer["status"]), (0, "predicted"), answer)
        self.assertEqual(answer["occurrence"], self.pin("query-occurrence"))
        _, by_selection = self.enrich("--limit", "1000", "--dry-run", "--staging", str(self.staging))
        self.assertEqual(answer["works"], by_selection["works"])

    def test_a_later_run_pins_the_parameters_an_earlier_one_admitted(self) -> None:
        code, first = self.enrich("--limit", "1000", "--staging", str(self.staging))
        self.assertEqual(code, 0, first)
        code, second = self.enrich("--limit", "1000", "--occurrence", self.pin("query-occurrence")["object_id"],
                                   selection_key=None)
        self.assertEqual((code, second["status"]), (0, "committed"), second)
        self.assertFalse(first["parameters"]["reused"])
        self.assertTrue(second["parameters"]["reused"])
        self.assertNotEqual(first["run"], second["run"])
        for output in second["outputs"]:
            record = self.show(output["object_id"])
            self.assertIn({"role": "parameters", "id": first["parameters"]["object_id"],
                           "revision_id": first["parameters"]["revision_id"]}, record["refs"])
            self.assertIn({"role": "occurrence", "id": self.pin("query-occurrence")["object_id"],
                           "revision_id": self.pin("query-occurrence")["revision_id"]}, record["refs"])

    def test_records_that_are_not_the_compared_kind_are_refused(self) -> None:
        for key in ("query-occurrence", "query", "work-x"):
            code, answer = self.enrich("--limit", "1", "--selection", self.pin(key)["object_id"], selection_key=None)
            self.assertEqual(code, 1, key)
            self.assertIn("does not cite archive.claim/audio_selection version 2", answer["detail"])
        code, answer = self.enrich("--limit", "1", "--occurrence", self.pin("query-selection")["object_id"],
                                   selection_key=None)
        self.assertEqual(code, 1)
        self.assertIn("does not cite archive.claim/song_occurrence version 2", answer["detail"])

    def test_a_limit_that_is_not_decimal_text_is_refused(self) -> None:
        code, answer = self.enrich("--limit", "0.1e1")
        self.assertEqual(code, 1)
        self.assertIn("not unsigned decimal text", answer["detail"])

    def test_both_or_neither_compared_record_is_not_understood(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(HERE / "enrich.py"), "match", "--archive", str(self.root), "--limit", "1",
             "--selection", self.pin("query-selection")["object_id"],
             "--occurrence", self.pin("query-occurrence")["object_id"]], capture_output=True, text=True, env=self.env)
        self.assertEqual((completed.returncode, completed.stdout), (2, ""))
        completed = subprocess.run([sys.executable, str(HERE / "enrich.py"), "match", "--archive", str(self.root),
                                    "--limit", "1"], capture_output=True, text=True, env=self.env)
        self.assertEqual((completed.returncode, completed.stdout), (2, ""))

    def test_audio_a_span_does_not_denote_is_refused(self) -> None:
        before = support.head(self.root)
        code, answer = self.enrich("--limit", "1000", "--staging", str(self.staging), selection_key="past-end")
        self.assertEqual(code, 1, answer)
        self.assertIn("runs past its audio", answer["detail"])
        code, answer = self.enrich("--limit", "1000", "--staging", str(self.staging), selection_key="wrong-rate")
        self.assertEqual(code, 1, answer)
        self.assertIn("sampled at 48000 Hz", answer["detail"])
        self.assertEqual(support.head(self.root), before)

    def test_content_that_is_absent_is_refused(self) -> None:
        digest = self.show(self.pin("y1")["object_id"])["payload"]["content"]["digest"].removeprefix("sha256:")
        local = (self.root / "content" / "sha256" / digest[:2] / digest[2:4] / digest).resolve()
        self.assertTrue(local.is_file())
        local.parent.chmod(0o700)
        local.unlink()
        code, answer = self.enrich("--limit", "1000", "--staging", str(self.staging))
        self.assertEqual(code, 1)
        self.assertIn("not locally present", answer["detail"])


@needs_door
class EmptyCorpus(unittest.TestCase):
    def test_an_archive_with_no_linked_occurrence_is_refused(self) -> None:
        tmp = support.scratch(self, "enrich-match-empty-")
        wav = support.write_wav(tmp / "a.wav", support.tune_samples(support.TUNE, 8000), 8000)
        root, manifest = support.build_archive(self, {"a": wav}, (
            {"name": "works", "command": "admit", "records": [work("work-x", "Test Work X")]},
            {"name": "selection", "command": "admit", "records": [selection("s", "a", [(0, 1000)], 8000)]},
            {"name": "occurrence", "command": "admit", "records": [occurrence("o", "s")]},
            {"name": "own link", "command": "admit", "records": [link("l", "o", "work-x")]}))
        completed = subprocess.run(
            [sys.executable, str(HERE / "enrich.py"), "match", "--archive", str(root), "--selection",
             manifest["objects"]["s"]["object_id"], "--limit", "1", "--door", support.DOOR,
             "--archive-command", support.ARCHIVE], capture_output=True, text=True, env=support.environment())
        self.assertEqual(completed.returncode, 1, completed.stdout)
        detail = json.loads(completed.stderr)["detail"]
        # Anchor: the archive does hold a link; it is the compared audio's own.
        self.assertIn("corpus is empty", detail)
        self.assertIn("(1 links skipped)", detail)


@needs_door
class CorpusFromExport(unittest.TestCase):
    """A real export with one field changed, for a shape Door would refuse to write."""

    def test_a_work_whose_head_is_not_a_musical_work_is_skipped(self) -> None:
        tmp = support.scratch(self, "enrich-match-kind-")
        wav = support.write_wav(tmp / "a.wav", support.tune_samples(support.TUNE, 8000), 8000)
        root, manifest = support.build_archive(self, {"a": wav}, (
            {"name": "works", "command": "admit", "records": [work("work-x", "Test Work X")]},
            {"name": "selection", "command": "admit", "records": [selection("s", "a", [(0, 1000)], 8000)]},
            {"name": "occurrence", "command": "admit", "records": [occurrence("o", "s")]},
            {"name": "link", "command": "admit", "records": [link("l", "o", "work-x")]}))
        export = support.export(root)
        reader = adapter.ArchiveReader.from_export(root, export)
        works, skipped = enrich.match_corpus(reader, "urn:uuid:00000000-0000-4000-8000-000000000000", set())
        # Anchor: unchanged, the Work is in the corpus.
        self.assertEqual(list(works), [manifest["objects"]["work-x"]["object_id"]])
        self.assertEqual(skipped, [])
        for record in export["records"]:
            if record["object_id"] == manifest["objects"]["work-x"]["object_id"]:
                record["payload"]["kind"] = "person"
        works, skipped = enrich.match_corpus(adapter.ArchiveReader.from_export(root, export),
                                             "urn:uuid:00000000-0000-4000-8000-000000000000", set())
        self.assertEqual(works, {})
        self.assertEqual([s["reason"] for s in skipped], ["the Work's head is not a musical_work Entity"])

    def test_a_selection_is_compared_by_its_first_track(self) -> None:
        tmp = support.scratch(self, "enrich-match-tracks-")
        a = support.write_wav(tmp / "a.wav", support.tune_samples(support.TUNE, 8000), 8000)
        b = support.write_wav(tmp / "b.wav", support.tune_samples(support.OTHER_TUNE, 8000), 8000)
        two_tracks = {**selection("s", "a", [(0, 1000)], 8000),
                      "payload": {"tracks": [
                          {"pieces": [{"start_sample": 0, "end_sample": 1000, "sample_rate": 8000}]},
                          {"pieces": [{"start_sample": 0, "end_sample": 500, "sample_rate": 8000},
                                      {"start_sample": 500, "end_sample": 1000, "sample_rate": 8000}]}]},
                      "refs": [{"role": "piece", "pin": "a"}, {"role": "piece", "pin": "b"},
                               {"role": "piece", "pin": "b"}]}
        root, manifest = support.build_archive(self, {"a": a, "b": b}, (
            {"name": "selection", "command": "admit", "records": [two_tracks]},))
        objects = manifest["objects"]
        reader = adapter.ArchiveReader.from_export(root, support.export(root))
        record = reader.head(objects["s"]["object_id"])
        # Anchor: Door kept both tracks and all three piece references.
        self.assertEqual(len(record["payload"]["tracks"]), 2)
        self.assertEqual(len([r for r in record["refs"] if r["role"] == "piece"]), 3)
        pieces = enrich.selection_pieces(reader, record)
        self.assertEqual([(artifact["object_id"], piece) for artifact, piece in pieces],
                         [(objects["a"]["object_id"], {"start_sample": 0, "end_sample": 1000, "sample_rate": 8000})])

    def test_only_the_head_revision_of_a_link_counts(self) -> None:
        tmp = support.scratch(self, "enrich-match-relink-")
        wav = support.write_wav(tmp / "a.wav", support.tune_samples(support.TUNE, 8000), 8000)
        root, manifest = support.build_archive(self, {"a": wav}, (
            {"name": "works", "command": "admit", "records": [work("work-x", "Test Work X"),
                                                              work("work-y", "Test Work Y")]},
            {"name": "selection", "command": "admit", "records": [selection("s", "a", [(0, 1000)], 8000)]},
            {"name": "occurrence", "command": "admit", "records": [occurrence("o", "s")]},
            {"name": "link", "command": "admit", "records": [link("l", "o", "work-x")]},
            {"name": "relink", "command": "admit", "records": [{**link("l2", "o", "work-y"), "revises": "l"}]}))
        objects = manifest["objects"]
        reader = adapter.ArchiveReader.from_export(root, support.export(root))
        # Anchor: the earlier revision is still exported, and it links Work X.
        earlier = reader.by_revision[objects["l"]["revision_id"]]
        self.assertIn(objects["work-x"]["object_id"], [r["id"] for r in earlier["refs"] if r["role"] == "work"])
        self.assertNotEqual(objects["l"]["revision_id"], objects["l2"]["revision_id"])
        works, skipped = enrich.match_corpus(reader, "urn:uuid:00000000-0000-4000-8000-000000000000", set())
        self.assertEqual(list(works), [objects["work-y"]["object_id"]])
        self.assertEqual([link["revision_id"] for link in works[objects["work-y"]["object_id"]]["links"]],
                         [objects["l2"]["revision_id"]])
        self.assertEqual(skipped, [])


@needs_door
class DoorRefusesMalformedMatches(unittest.TestCase):
    """The matcher's proposal path ends at Door, and Door refuses a match that
    breaks work_match version 2 however the matcher builds it."""

    def setUp(self) -> None:
        tmp = support.scratch(self, "enrich-match-refuse-")
        wav = support.write_wav(tmp / "a.wav", support.tune_samples(support.TUNE, 8000), 8000)
        self.env = support.environment()
        self.root, manifest = support.build_archive(self, {"a": wav}, (
            {"name": "works", "command": "admit", "records": [work("work-x", "Test Work X")]},
            {"name": "selections", "command": "admit", "records": [
                selection("s", "a", [(0, 1000)], 8000), selection("t", "a", [(1000, 2000)], 8000)]},
            {"name": "occurrences", "command": "admit", "records": [occurrence("o", "s"), occurrence("p", "t")]},
            {"name": "link", "command": "admit", "records": [link("l", "p", "work-x")]}))
        self.objects = manifest["objects"]
        self.reader = adapter.ArchiveReader(self.root, support.ARCHIVE, self.env)

    def pin(self, role: str, key: str) -> adapter.Pin:
        return adapter.Pin(role, self.objects[key]["object_id"], self.objects[key]["revision_id"])

    def propose(self, *refs: adapter.Pin) -> adapter.Admission:
        started = adapter.now()
        output = adapter.Output(
            contract=enrich.MATCH_CONTRACT, name="work",
            payload={"method": {"id": match.METHOD_ID, "version": match.METHOD_VERSION}, "score": "0.100000",
                     "scale": match.SCALE, "direction": match.DIRECTION}, refs=refs)
        run = adapter.RunResult(agent=match.AGENT, method_id=match.METHOD_ID, method_version=match.METHOD_VERSION,
                                software=(), started_at=started, ended_at=started,
                                used=(self.pin("used", "s"),), outputs=(output,))
        return adapter.propose(self.reader, run, door=support.DOOR, dry_run=True, env=self.env)

    def test_door_refuses_what_work_match_version_2_forbids(self) -> None:
        work_pin, corpus = self.pin("work", "work-x"), self.pin("corpus", "p")
        # Anchor: the well-formed match is predicted, so each refusal below is the defect's.
        self.assertEqual(self.propose(self.pin("selection", "s"), work_pin, corpus).status, "predicted")
        self.assertEqual(self.propose(self.pin("occurrence", "o"), work_pin, corpus).status, "predicted")
        malformed = {
            "both compared roles": (self.pin("selection", "s"), self.pin("occurrence", "o"), work_pin, corpus),
            "no compared record": (work_pin, corpus),
            "a selection role pinning an occurrence": (self.pin("selection", "o"), work_pin, corpus),
            "an occurrence role pinning a selection": (self.pin("occurrence", "s"), work_pin, corpus),
            "the compared occurrence in its own corpus": (self.pin("occurrence", "p"), work_pin, corpus),
            "a corpus selection": (self.pin("selection", "s"), work_pin, self.pin("corpus", "t")),
            "no corpus": (self.pin("selection", "s"), work_pin),
            "a Work that is a selection": (self.pin("selection", "s"), self.pin("work", "t"), corpus),
        }
        for case, refs in malformed.items():
            with self.subTest(case), self.assertRaisesRegex(adapter.AdapterError, "door admit failed"):
                self.propose(*refs)


if __name__ == "__main__":
    unittest.main()
