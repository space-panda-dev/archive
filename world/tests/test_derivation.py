"""World projects specification 0.0.7's runs and what they make (ADRs 0064-0067).

Every check reads the published fixture archive, which fixture/build.py
admits through the real Door: derivation runs, an audio stream claim about a
synthetic recording, a detector's span suggestions, an alignment and two
matches that pin their runs. The refusal checks change one field of a real
record and require World to refuse it rather than project it.
"""

import copy
import json
import shlex
import tempfile
import unittest
import wave
from pathlib import Path

from pyoxigraph import NamedNode

import world.world as world_module
from world.tests.test_conformance import build_fixture_archive, integration_binaries, remove_tree
# The examples the fixture archive answers; the Door chain export answers the
# others (test_world.py).
from world.tests.test_world import FIXTURE_EXAMPLES
from world.world import (
    PROJECTION_REVISIONS,
    PROJECTION_VERSION,
    SPARQL_RESULTS_JSON,
    ProjectionError,
    Unsupported,
    _read_current,
    build_projection,
    nquads,
    query,
    rebuild_store,
    refresh_store,
)

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "world/examples"
PLAN = json.loads((ROOT / "fixture/plan.json").read_text())
ARCH = "urn:uuid:3e8b29f8-70c9-403e-bb4c-2280d59eca7f#"
PROV = "http://www.w3.org/ns/prov#"
XSD = "http://www.w3.org/2001/XMLSchema#"
RECORDING_PLACEHOLDER = "urn:uuid:replace-with-recording-id"


def planned(key):
    """The plan's record for ``key``."""
    [record] = [r for step in PLAN["steps"] for r in step["records"] if r["key"] == key]
    return record


def uri(value):
    return {"type": "uri", "value": value}


def lit(value, datatype=None):
    return {"type": "literal", "value": str(value), **({"datatype": datatype} if datatype else {})}


def integer(number):
    return lit(number, XSD + "integer")


def rows(answer):
    if answer.media_type != SPARQL_RESULTS_JSON or "results" not in answer.data:
        raise AssertionError(f"not a SELECT answer: {answer}")
    return answer.data["results"]["bindings"]


def fixture_examples(recording_id):
    queries = {}
    for name in sorted(FIXTURE_EXAMPLES):
        text = (EXAMPLES / name).read_text()
        if RECORDING_PLACEHOLDER not in text:
            raise AssertionError(f"{name} lacks its placeholder {RECORDING_PLACEHOLDER}")
        queries[name] = text.replace(RECORDING_PLACEHOLDER, recording_id)
    return queries


class DerivationProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.manifest, cls.export = build_fixture_archive(cls.tmp)
        cls.projection = build_projection(cls.export)
        cls.by_revision = {r["revision_id"]: r for r in cls.export["records"]}

    @classmethod
    def tearDownClass(cls):
        remove_tree(cls.tmp)

    def record(self, key):
        return self.by_revision[self.manifest["objects"][key]["revision_id"]]

    def iri(self, key):
        return uri(f"{self.export['archive_id']}#revision={self.manifest['objects'][key]['revision_id']}")

    def ask(self, text):
        return rows(query(self.projection, text))

    # -- the fixture is what it says ------------------------------------

    def test_the_stream_claim_states_the_recordings_real_facts(self):
        # A fixture that only agreed with World would prove nothing: read
        # the admitted bytes and compare them with the plan's stream claim.
        with wave.open(str(ROOT / "fixture/content/recording.wav")) as audio:
            facts = {"sample_rate": audio.getframerate(), "channel_count": audio.getnchannels(),
                     "sample_count": audio.getnframes(), "bits_per_sample": 8 * audio.getsampwidth()}
        claimed = self.record("stream")["payload"]
        self.assertEqual({key: claimed[key] for key in facts}, facts)
        self.assertEqual((claimed["stream_index"], claimed["codec"]), (0, "pcm_s16le"))
        spans = self.record("suggestions")["payload"]["spans"]
        self.assertEqual(spans[-1]["end_sample"], facts["sample_count"])

    def test_runs_and_their_outputs_are_asserted_by_the_tools(self):
        for key in ("run-probe", "stream", "run-detect", "suggestions", "run-match", "work-match-selection"):
            self.assertTrue(self.record(key)["agent"].startswith("pkg:"), key)
        # Anchor: the run is a different object from what it made.
        self.assertNotEqual(self.record("run-probe")["object_id"], self.record("stream")["object_id"])

    # -- examples ---------------------------------------------------------

    def test_the_fixture_examples_answer_their_golden_rows(self):
        recording = self.iri("recording")
        detect, probe = self.iri("run-detect"), self.iri("run-probe")
        suggestions, stream = self.iri("suggestions"), self.iri("stream")
        planned_spans = planned("suggestions")["payload"]["spans"]
        method = planned("run-detect")["payload"]["method"]
        expected = {
            "suggested-spans-of-recording.sparql": [
                {"recording": recording, "suggestions": suggestions, "position": integer(index),
                 "start": integer(span["start_sample"]), "end": integer(span["end_sample"]),
                 "class": lit(span["class"]), "rate": integer(8000), "stream": stream, "run": detect,
                 "method": lit(method["id"]), "methodVersion": lit(method["version"])}
                for index, span in enumerate(planned_spans)
            ],
            "recording-duration.sparql": [
                {"recording": recording, "streamIndex": integer(0), "samples": integer(4000),
                 "rate": integer(8000), "seconds": lit("0.5", XSD + "decimal"), "channels": integer(1),
                 "codec": lit("pcm_s16le"), "claim": stream, "run": probe},
            ],
        }
        # Three spans with three classes, or one wrong row could pass.
        self.assertEqual(len({span["class"] for span in planned_spans}), 3)
        examples = fixture_examples(self.manifest["objects"]["recording"]["object_id"])
        self.assertEqual(set(examples), set(expected))
        for name, text in examples.items():
            with self.subTest(example=name):
                self.assertEqual(self.ask(text), expected[name])

    def test_the_readme_patterns_answer_as_the_examples_do(self):
        text = (ROOT / "world/README.md").read_text()
        section = text[text.index("### Runs, stream facts, and suggested spans"):text.index("## Refresh")]
        blocks = [block.split("```", 1)[0] for block in section.split("```sparql\n")[1:]]
        self.assertEqual(len(blocks), 2)
        recording_id = self.manifest["objects"]["recording"]["object_id"]
        examples = fixture_examples(recording_id)
        for block, example in zip(blocks, ("suggested-spans-of-recording.sparql", "recording-duration.sparql")):
            with self.subTest(example=example):
                pattern = block.replace("{archive}", self.export["archive_id"]).replace("{recording}", recording_id)
                answer = self.ask(pattern)
                full = self.ask(examples[example])
                self.assertTrue(answer)
                self.assertEqual(answer, [{name: row[name] for name in answer[0]} for row in full])

    # -- what each claim graph holds ---------------------------------------

    def test_a_run_is_an_activity_with_its_method_programs_times_and_inputs(self):
        run = self.iri("run-detect")["value"]
        plan = planned("run-detect")["payload"]
        answer = self.ask(f"""
            PREFIX prov: <{PROV}> PREFIX arch: <{ARCH}>
            PREFIX foaf: <http://xmlns.com/foaf/0.1/> PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
            SELECT ?started ?ended ?position ?name ?version WHERE {{ GRAPH <{run}> {{
              <{run}> a prov:Activity ; prov:startedAtTime ?started ; prov:endedAtTime ?ended ;
                      prov:wasAssociatedWith ?program .
              ?program a prov:SoftwareAgent ; rdf:value ?position ; foaf:name ?name ; arch:version ?version .
            }} }} ORDER BY ?position""")
        self.assertEqual(answer, [
            {"started": lit(plan["started_at"], XSD + "dateTime"), "ended": lit(plan["ended_at"], XSD + "dateTime"),
             "position": integer(index), "name": lit(item["name"]), "version": lit(item["version"])}
            for index, item in enumerate(plan["software"])])
        inputs = self.ask(f"""SELECT ?input ?parameters WHERE {{ GRAPH <{run}> {{
              <{run}> <{PROV}used> ?input ; <{ARCH}parameters> ?parameters }} }} ORDER BY ?input""")
        self.assertEqual({row["input"]["value"] for row in inputs},
                         {self.iri("recording")["value"], self.iri("stream")["value"]})
        self.assertEqual({row["parameters"]["value"] for row in inputs}, {self.iri("detector-parameters")["value"]})
        # Outputs reach the run through the records graph as well.
        made = self.ask(f"""SELECT ?output WHERE {{ GRAPH <{self.projection.records_graph.value}> {{
              ?output <{PROV}wasGeneratedBy> <{run}> }} }}""")
        self.assertEqual(made, [{"output": self.iri("suggestions")}])

    def test_a_run_without_programs_has_no_software_nodes(self):
        run = self.iri("run-align")["value"]
        self.assertEqual(planned("run-align")["payload"]["software"], [])
        self.assertEqual(self.ask(f"""SELECT ?p WHERE {{ GRAPH <{run}> {{ <{run}> <{PROV}wasAssociatedWith> ?p }} }}"""), [])
        self.assertTrue(self.ask(f"""SELECT ?m WHERE {{ GRAPH <{run}> {{ <{run}> <{ARCH}method> ?m }} }}"""))

    def test_matches_name_what_they_compared_and_their_run(self):
        for key, role, compared in (("work-match-occurrence", "performance", "occurrence-v2"),
                                    ("work-match-selection", "selection", "selection-v2")):
            with self.subTest(match=key):
                graph = self.iri(key)["value"]
                plan = planned(key)
                answer = self.ask(f"""PREFIX arch: <{ARCH}> PREFIX prov: <{PROV}>
                    SELECT ?compared ?work ?score ?scale ?direction ?run WHERE {{ GRAPH <{graph}> {{
                      <{graph}> arch:{role} ?compared ; arch:work ?work ; arch:score ?score ; arch:score_scale ?scale ;
                                arch:score_direction ?direction ; prov:wasGeneratedBy ?run }} }}""")
                work = next(ref["pin"] for ref in plan["refs"] if ref["role"] == "work")
                self.assertEqual(answer, [{
                    "compared": self.iri(compared), "work": self.iri(work),
                    "score": lit(plan["payload"]["score"], XSD + "decimal"), "scale": lit(plan["payload"]["scale"]),
                    "direction": lit(plan["payload"]["direction"]), "run": self.iri("run-match")}])
                corpus = self.ask(f"""SELECT ?c WHERE {{ GRAPH <{graph}> {{ <{graph}> <{ARCH}match_corpus> ?c }} }} ORDER BY ?c""")
                self.assertEqual(sorted(row["c"]["value"] for row in corpus),
                                 sorted(self.iri(ref["pin"])["value"] for ref in plan["refs"] if ref["role"] == "corpus"))
        # Scores compare as numbers, not text: "1.5" sorts after "0.25".
        ordered = self.ask(f"""SELECT ?g WHERE {{ GRAPH ?g {{ ?g <{ARCH}score> ?s }} }} ORDER BY DESC(?s)""")
        self.assertEqual([row["g"] for row in ordered], [self.iri("work-match-selection"), self.iri("work-match-occurrence")])

    def test_an_alignment_version_2_projects_as_version_1(self):
        graph = self.iri("alignment-v2")["value"]
        anchors = self.ask(f"""SELECT ?n WHERE {{ GRAPH <{graph}> {{ <{graph}> <{ARCH}anchors> ?n }} }}""")
        self.assertEqual(len(anchors), len(planned("alignment-v2")["payload"]["anchors"]))

    def test_artifact_and_source_roles_keep_their_statement_but_no_direct_triple(self):
        records = self.projection.records_graph
        suggestions = NamedNode(self.iri("suggestions")["value"])
        stream = NamedNode(self.iri("stream")["value"])
        recording = NamedNode(self.iri("recording")["value"])
        # Anchor: the references exist, as reified statements naming the role.
        roles = {(quad.object.value) for quad in self.projection.store.quads_for_pattern(
            None, NamedNode(ARCH + "role"), None, records)}
        self.assertLessEqual({ARCH + "source", ARCH + "Artifact"}, roles)
        self.assertEqual(list(self.projection.store.quads_for_pattern(suggestions, NamedNode(ARCH + "source"), None, records)), [])
        self.assertEqual(list(self.projection.store.quads_for_pattern(stream, NamedNode(ARCH + "Artifact"), None, records)), [])
        self.assertTrue(list(self.projection.store.quads_for_pattern(suggestions, NamedNode(PROV + "wasDerivedFrom"), recording, records)))

    # -- refresh -----------------------------------------------------------

    def test_an_incremental_refresh_across_the_new_records_equals_a_full_build(self):
        _, archive = integration_binaries()
        steps = [step["name"] for step in self.manifest["steps"]]
        # Cut after the probe, so the detector's suggestions pin a stream and
        # a recording committed before the cut.
        cut_commit = self.manifest["steps"][steps.index("probe")]["commit"]
        since = json.loads(self.run_archive(archive, ["export", "--since", cut_commit]))
        added = {r["revision_id"] for r in since["records"]}
        cut = dict(self.export, snapshot=cut_commit,
                   records=[r for r in self.export["records"] if r["revision_id"] not in added])
        # Anchor the cut: both sides hold records, and the suggestions pin a
        # stream only the cut holds.
        self.assertTrue(cut["records"] and since["records"])
        self.assertIn(self.record("suggestions")["revision_id"], added)
        self.assertNotIn(self.record("stream")["revision_id"], added)
        self.assertIn(self.record("stream")["revision_id"], {r["revision_id"] for r in since["pinned_records"]})
        work = self.tmp / "refresh"
        (work / "bin").mkdir(parents=True)
        cut_file = work / "cut.json"
        cut_file.write_text(json.dumps(cut))
        store = work / "world"
        root = self.tmp / "archive"
        rebuild_store(root, store, export_file=cut_file)
        # A bomb: the refresh must ask only for the records since the cut.
        bomb = work / "bin" / "archive"
        bomb.write_text("#!/bin/sh\n"
                        "if [ \"$#\" = 1 ]; then echo 'a full export was not expected' >&2; exit 97; fi\n"
                        f"exec {shlex.quote(archive)} \"$@\"\n")
        bomb.chmod(0o755)
        refreshed = refresh_store(root, store, archive_command=str(bomb))
        self.assertEqual((refreshed.mode, refreshed.projection.snapshot), ("incremental", self.export["snapshot"]))
        self.assertEqual(nquads(_read_current(store)), nquads(self.projection))
        self.assertTrue(store.joinpath("versions", world_module._generation_id(
            self.export["snapshot"], PROJECTION_VERSION, PROJECTION_REVISIONS[PROJECTION_VERSION])).is_dir())

    def run_archive(self, archive, arguments):
        import os
        import subprocess
        completed = subprocess.run([archive, *arguments], cwd=self.tmp / "archive", capture_output=True,
                                   text=True, env=dict(os.environ))
        if completed.returncode != 0:
            raise AssertionError(f"archive {' '.join(arguments)} failed: {completed.stderr or completed.stdout}")
        return completed.stdout


class DerivationRefusalTests(unittest.TestCase):
    """Each change to a real record must be refused, never projected; the
    unchanged export is the anchor."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.manifest, cls.base = build_fixture_archive(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        remove_tree(cls.tmp)

    def setUp(self):
        self.export = copy.deepcopy(self.base)
        self.by_revision = {r["revision_id"]: r for r in self.export["records"]}

    def record(self, key):
        return self.by_revision[self.manifest["objects"][key]["revision_id"]]

    def ref(self, key, role):
        [found] = [ref for ref in self.record(key)["refs"] if ref["role"] == role]
        return found

    def pin_to(self, ref, key):
        ref["id"] = self.manifest["objects"][key]["object_id"]
        ref["revision_id"] = self.manifest["objects"][key]["revision_id"]

    def refused(self, pattern, error=ProjectionError):
        with self.assertRaisesRegex(error, pattern):
            build_projection(self.export)

    def test_the_unchanged_export_projects(self):
        self.assertIsNotNone(build_projection(self.export))

    def test_a_run_that_ends_before_it_starts(self):
        payload = self.record("run-probe")["payload"]
        payload["ended_at"] = "2025-12-31T23:59:59.999999999Z"
        self.refused("ordered UTC instants")

    def test_a_run_instant_without_utc(self):
        self.record("run-probe")["payload"]["started_at"] = "2026-01-01T00:00:00+01:00"
        self.refused("ordered UTC instants")

    def test_a_run_with_malformed_software(self):
        self.record("run-detect")["payload"]["software"] = [{"name": "x"}]
        self.refused("name and version pairs")

    def test_a_run_with_two_parameters(self):
        refs = self.record("run-detect")["refs"]
        refs.append(dict(self.ref("run-detect", "parameters")))
        self.refused("one parameters Artifact")

    def test_a_run_with_another_role(self):
        # `reference` is a core term, so the records graph can name it; the
        # run's own graph still refuses it.
        self.ref("run-detect", "parameters")["role"] = "reference"
        self.refused("one parameters Artifact")

    def test_a_run_using_an_assessment(self):
        self.pin_to(self.record("run-probe")["refs"][0], "judgment")
        self.refused("invalid 'used' pin")

    def test_parameters_that_are_not_an_artifact(self):
        self.pin_to(self.ref("run-detect", "parameters"), "stream")
        self.refused("invalid 'parameters' pin")

    def test_a_run_without_a_method(self):
        del self.record("run-probe")["payload"]["method"]
        self.refused("invalid closed payload")

    def test_a_method_without_a_version(self):
        self.record("run-probe")["payload"]["method"] = {"id": "x", "version": ""}
        self.refused("method with an id and version")

    def test_a_contract_without_the_instant_datatype(self):
        for bundle in self.export["contracts"]:
            if bundle["id"] == "archive.claim/derivation_run":
                bundle["context"]["started_at"] = "prov:startedAtTime"
        self.refused("does not declare the datatype", Unsupported)

    def test_a_stream_whose_run_is_not_a_derivation_run(self):
        self.pin_to(self.ref("stream", "run"), "title")
        self.refused("derivation_run version 1")

    def test_a_stream_with_too_many_channels(self):
        self.record("stream")["payload"]["channel_count"] = 65
        self.refused("above 64")

    def test_a_stream_without_a_codec(self):
        self.record("stream")["payload"]["codec"] = ""
        self.refused("invalid closed payload")

    def test_a_stream_with_a_zero_sample_count(self):
        self.record("stream")["payload"]["sample_count"] = 0
        self.refused("sample_count must be an integer")

    def test_suggestions_for_another_artifact_than_their_stream(self):
        self.pin_to(self.ref("suggestions", "source"), "take-a")
        self.refused("stream of another Artifact revision")

    def test_suggestions_at_another_rate_than_their_stream(self):
        self.record("suggestions")["payload"]["sample_rate"] = 48000
        self.refused("differ from their stream's sample rate")

    def test_a_span_past_the_stream(self):
        self.record("suggestions")["payload"]["spans"][-1]["end_sample"] = 4001
        self.refused("ends past the stream's sample count")

    def test_overlapping_spans(self):
        self.record("suggestions")["payload"]["spans"][1]["start_sample"] = 999
        self.refused("start_sample must be an integer of at least 1000")

    def test_an_unknown_span_class(self):
        self.record("suggestions")["payload"]["spans"][0]["class"] = "speech"
        self.refused("invalid span at 0")

    def test_no_spans(self):
        self.record("suggestions")["payload"]["spans"] = []
        self.refused("1 to 10000 spans")

    def test_suggestions_measured_against_a_claim_that_is_not_a_stream(self):
        self.pin_to(self.ref("suggestions", "stream"), "run-probe")
        self.refused("audio_stream version 1")

    def test_a_match_comparing_both_an_occurrence_and_a_selection(self):
        refs = self.record("work-match-selection")["refs"]
        refs.append({"role": "occurrence", **{k: v for k, v in self.ref("work-match-occurrence", "occurrence").items() if k != "role"}})
        self.refused("one occurrence or selection")

    def test_a_match_whose_corpus_is_not_an_occurrence(self):
        self.pin_to(self.record("work-match-selection")["refs"][2], "selection-v2")
        self.refused("song_occurrence version 2")

    def test_a_match_whose_corpus_holds_its_occurrence(self):
        self.pin_to(self.ref("work-match-occurrence", "corpus"), "occurrence-v2")
        self.refused("compares an occurrence with itself")

    def test_a_match_with_a_score_that_is_not_a_decimal(self):
        self.record("work-match-occurrence")["payload"]["score"] = "1e3"
        self.refused("invalid closed payload")

    def test_a_match_of_a_work_that_is_not_a_musical_work(self):
        self.pin_to(self.ref("work-match-occurrence", "work"), "guest")
        self.refused("must pin a musical_work")

    def test_a_match_whose_selection_is_not_version_2(self):
        self.pin_to(self.ref("work-match-selection", "selection"), "selection-v1")
        self.refused("audio_selection version 2")

    def test_an_alignment_version_2_whose_run_is_not_a_derivation_run(self):
        self.pin_to(self.ref("alignment-v2", "run"), "title")
        self.refused("derivation_run version 1")


class InstantOrderTests(unittest.TestCase):
    def test_fractions_of_any_length_compare_as_numbers(self):
        order = world_module._instant_order
        self.assertLess(order("2026-01-01T00:00:01.25Z"), order("2026-01-01T00:00:01.5Z"))
        self.assertLess(order("2026-01-01T00:00:01Z"), order("2026-01-01T00:00:01.0000000001Z"))
        self.assertEqual(order("2026-01-01T00:00:01Z"), order("2026-01-01T00:00:01.000Z"))


if __name__ == "__main__":
    unittest.main()
