import io
import json
import os
import shlex
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from pyoxigraph import Literal, NamedNode, Quad, QueryResultsFormat, RdfFormat, Store, parse

import world.world as world_module
from world.world import (
    N_TRIPLES,
    PROJECTION_VERSION,
    SPARQL_RESULTS_JSON,
    Projection,
    ProjectionError,
    ProjectionNotServed,
    QueryRefused,
    TooManyRows,
    Unsupported,
    _main,
    _read_current,
    build_projection,
    musicbrainz_work_candidate,
    nquads,
    query,
    rebuild_store,
    refresh_served,
    refresh_store,
    served_projections,
)


ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP_EXPORT = ROOT / "world/tests/fixtures/bootstrap-export.json"
DOOR_CHAIN_EXPORT = ROOT / "world/tests/fixtures/door-world-chain-export.json"
DOOR_CHAIN_CUT_EXPORT = ROOT / "world/tests/fixtures/door-world-chain-cut-export.json"
DOOR_CHAIN_SINCE_EXPORT = ROOT / "world/tests/fixtures/door-world-chain-since-export.json"
EXAMPLES = ROOT / "world/examples"
EXAMPLE_WORK_PLACEHOLDER = "urn:uuid:replace-with-work-id"
EXAMPLE_SELECTION_PLACEHOLDER = "urn:uuid:replace-with-selection-id"
EXAMPLE_OCCURRENCE_PLACEHOLDER = "urn:uuid:replace-with-occurrence-id"
# The Door chain export holds no derivation runs; the fixture archive answers
# these examples (test_derivation.py).
FIXTURE_EXAMPLES = {"recording-duration.sparql", "suggested-spans-of-recording.sparql"}


XSD_INTEGER = "http://www.w3.org/2001/XMLSchema#integer"


def uri(value):
    """An IRI in SPARQL 1.1 Query Results JSON."""
    return {"type": "uri", "value": value}


def lit(value, datatype=None):
    """A literal in SPARQL 1.1 Query Results JSON; a plain string has no datatype."""
    return {"type": "literal", "value": value, **({"datatype": datatype} if datatype else {})}


def integer(number):
    return lit(str(number), XSD_INTEGER)


def rows(answer):
    """The bindings of a SELECT answer, which must be standard results."""
    if answer.media_type != SPARQL_RESULTS_JSON or "results" not in answer.data:
        raise AssertionError(f"not a SELECT answer: {answer}")
    return answer.data["results"]["bindings"]


def rec(revision, object_id, shape, payload, refs=None, contracts=None):
    return {
        "spec": "archive/0.0.2",
        "revision_id": revision,
        "object_id": object_id,
        "shape": shape,
        "by": "pkg:pypi/archive-door@1.0.0",
        "agent": "urn:uuid:20000000-0000-4000-8000-000000000001",
        "at": "2026-01-02T03:04:05Z",
        "payload": payload,
        "refs": refs or [],
        "supersedes": [],
        "contracts": contracts or [],
    }


CLAIM_REV = "sha256:" + "c" * 64
WORK_ID = "urn:uuid:30000000-0000-4000-8000-000000000001"
CLAIM_ID = "urn:uuid:40000000-0000-4000-8000-000000000001"
ASSESSMENT_ID = "urn:uuid:50000000-0000-4000-8000-000000000001"
WORK_REV = "sha256:" + "a" * 64
ASSESSMENT_REV = "sha256:" + "d" * 64


def synthetic_fixture():
    context = json.loads((ROOT / "spec/0.0.2/context.jsonld").read_text())
    mapping_contract = {
        "id": "archive.mapping",
        "version": "1",
        "schema": {"type": "object"},
        "context": {
            "subject": "arch:subject",
            "source": "arch:source",
            "assessment_of": "arch:assessment_of",
        },
    }
    citation = [{"id": "archive.mapping", "version": "1", "digest": "sha256:" + "e" * 64}]
    assessment_contract = {
        "id": "archive.assessment",
        "version": "1",
        "schema": {"type": "object"},
        "context": {
            "assessment_of": "arch:assessment_of",
            "source": "arch:alternate_source",
        },
    }
    return {
        "status": "ok",
        "snapshot": "f" * 40,
        "archive_id": "urn:uuid:60000000-0000-4000-8000-000000000001",
        "spec": "0.0.2",
        "context": context,
        "records": [
            rec(WORK_REV, WORK_ID, "entity", {"name": "Song A"}),
            rec(CLAIM_REV, CLAIM_ID, "claim", {"kind": "work_mapping", "external": "Paper Lanterns"}, [
                {"role": "subject", "id": WORK_ID},
                {"role": "source", "id": WORK_ID, "revision_id": WORK_REV},
            ], citation),
            rec(ASSESSMENT_REV, ASSESSMENT_ID, "assessment", {"judgment": "unreviewed"}, [
                {"role": "assessment_of", "id": CLAIM_ID, "revision_id": CLAIM_REV},
            ], [{"id": "archive.assessment", "version": "1", "digest": "sha256:" + "b" * 64}]),
        ],
        "contracts": [mapping_contract, assessment_contract],
    }


def cites(record, contract, version):
    return any(c["id"] == contract and c["version"] == version for c in record.get("contracts", []))


def example_queries(export):
    """Every example query, with its placeholder replaced by an object of the
    Door chain export."""
    def only(contract, version):
        [match] = [r for r in export["records"] if cites(r, contract, version)]
        return match

    [work] = [r for r in export["records"] if r["payload"].get("kind") == "musical_work"]
    subjects = {
        "selection-tracks.sparql": (EXAMPLE_OCCURRENCE_PLACEHOLDER,
                                    only("archive.claim/song_occurrence", "2")["object_id"]),
        "selection-facts.sparql": (EXAMPLE_SELECTION_PLACEHOLDER,
                                   only("archive.claim/audio_selection", "2")["object_id"]),
    }
    queries = {}
    for path in sorted(EXAMPLES.glob("*.sparql")):
        if path.name in FIXTURE_EXAMPLES:
            continue
        placeholder, subject = subjects.get(path.name, (EXAMPLE_WORK_PLACEHOLDER, work["object_id"]))
        text = path.read_text()
        if placeholder not in text:
            raise AssertionError(f"{path.name} lacks its placeholder {placeholder}")
        queries[path.name] = text.replace(placeholder, subject)
    return queries


class WorldProjectionTests(unittest.TestCase):
    def test_projects_record_shapes_and_provenance(self):
        projection = build_projection(synthetic_fixture())
        graph = f"<{projection.records_graph.value}>"
        found = rows(query(projection, f"""
            SELECT ?shape ?record WHERE {{
              GRAPH {graph} {{
                ?record <http://purl.org/dc/terms/type> ?shape .
                ?record <http://www.w3.org/ns/prov#wasAttributedTo> ?agent .
              }}
            }} ORDER BY ?shape
        """))
        self.assertEqual([row["shape"] for row in found], [lit("assessment"), lit("claim"), lit("entity")])
        data = nquads(projection).decode()
        self.assertIn("http://www.w3.org/ns/oa#Annotation", data)
        self.assertIn("http://www.w3.org/ns/prov#wasAttributedTo", data)
        self.assertIn("assessment_of", data)
        self.assertIn("archive.mapping", data)

    def test_source_and_contract_metadata_stays_with_its_record(self):
        value = synthetic_fixture()
        value["records"][0]["source"] = {"source": "entity-source"}
        value["records"][0]["contracts"] = [{
            "id": "archive.assessment", "version": "1", "digest": "sha256:" + "b" * 64,
        }]
        value["records"][1]["source"] = {"source": "claim-source"}
        projection = build_projection(value)
        graph = f"<{projection.records_graph.value}>"
        found = rows(query(projection, f"""
            SELECT ?record ?source ?contracts WHERE {{ GRAPH {graph} {{
              ?record <urn:uuid:3e8b29f8-70c9-403e-bb4c-2280d59eca7f#source> ?source ;
                      <urn:uuid:3e8b29f8-70c9-403e-bb4c-2280d59eca7f#contracts> ?contracts .
              FILTER(datatype(?source) = <http://www.w3.org/1999/02/22-rdf-syntax-ns#JSON> &&
                     datatype(?contracts) = <http://www.w3.org/1999/02/22-rdf-syntax-ns#JSON>)
            }} }}
        """))
        self.assertEqual(len(found), 2)
        by_revision = {row["record"]["value"]: (row["source"]["value"], row["contracts"]["value"]) for row in found}
        entity_uri = f"{value['archive_id']}#revision={WORK_REV}"
        claim_uri = f"{value['archive_id']}#revision={CLAIM_REV}"
        self.assertIn("entity-source", by_revision[entity_uri][0])
        self.assertIn("archive.assessment", by_revision[entity_uri][1])
        self.assertIn("claim-source", by_revision[claim_uri][0])
        self.assertIn("archive.mapping", by_revision[claim_uri][1])

    def test_rebuild_is_deterministic(self):
        self.assertEqual(nquads(build_projection(synthetic_fixture())), nquads(build_projection(synthetic_fixture())))

    def test_real_archive_export_boundary_and_store_rebuild(self):
        export = json.loads(BOOTSTRAP_EXPORT.read_text())
        projection = build_projection(export)
        self.assertEqual(len(export["records"]), 2)
        self.assertIn("pkg:hackage/archive@0.0.1.0", nquads(projection).decode())
        with tempfile.TemporaryDirectory() as tmp:
            archive_root = Path(tmp) / "archive"
            archive_root.mkdir()
            store_path = Path(tmp) / "world"
            built = rebuild_store(archive_root, store_path, export_file=BOOTSTRAP_EXPORT)
            before = nquads(built)
            current_mtime = (store_path / "CURRENT").stat().st_mtime_ns
            rebuilt = rebuild_store(archive_root, store_path, export_file=BOOTSTRAP_EXPORT)
            self.assertEqual(nquads(rebuilt), before)
            self.assertEqual((store_path / "CURRENT").stat().st_mtime_ns, current_mtime)
            query_projection = _read_current(store_path)
            graph = f"<{query_projection.records_graph.value}>"
            found = rows(query(query_projection, f"SELECT ?record WHERE {{ GRAPH {graph} {{ ?record a <http://www.w3.org/ns/oa#Annotation> }} }}"))
            self.assertEqual(len(found), 1)

    def test_incomplete_export_never_looks_empty(self):
        with self.assertRaises(ProjectionError):
            build_projection({"status": "incomplete"})

    def test_unsupported_shape_is_distinct(self):
        value = synthetic_fixture()
        value["records"][0]["shape"] = "future_shape"
        with self.assertRaises(Unsupported):
            build_projection(value)

    def test_unresolved_pin_is_reported(self):
        value = synthetic_fixture()
        value["records"][1]["refs"][1]["revision_id"] = "sha256:" + "9" * 64
        with self.assertRaisesRegex(ProjectionError, "unresolved pinned reference"):
            build_projection(value)

    def test_pin_must_belong_to_referenced_object(self):
        value = synthetic_fixture()
        value["records"][1]["refs"][1]["id"] = CLAIM_ID
        with self.assertRaisesRegex(ProjectionError, "belongs to"):
            build_projection(value)

    def test_contract_contexts_are_scoped_to_each_record(self):
        value = synthetic_fixture()
        self.assertIsNotNone(build_projection(value))
        value["records"][1]["contracts"].append(value["records"][2]["contracts"][0])
        with self.assertRaisesRegex(Unsupported, "contract contexts collide"):
            build_projection(value)

    def test_projects_performance_and_competing_work_correspondences_without_winner(self):
        value = synthetic_fixture()
        work_id = "urn:uuid:30000000-0000-4000-8000-000000000011"
        work_rev = "sha256:" + "1" * 64
        performance_id = "urn:uuid:30000000-0000-4000-8000-000000000012"
        performance_rev = "sha256:" + "2" * 64
        performance_work_id = "urn:uuid:40000000-0000-4000-8000-000000000011"
        performance_work_rev = "sha256:" + "3" * 64
        correspondence_contract = "archive.claim/work_musicbrainz_correspondence"
        performance_contract = "archive.claim/performance_work"
        work_ref_context = {"work": "arch:work", "performance": "arch:performance"}
        work_claim_citation = [{"id": correspondence_contract, "version": "1", "digest": "sha256:" + "4" * 64}]
        performance_citation = [{"id": performance_contract, "version": "1", "digest": "sha256:" + "5" * 64}]
        first_work = rec(work_rev, work_id, "entity", {"kind": "musical_work", "label": "Paper Lanterns"})
        performance = rec(performance_rev, performance_id, "claim", {"kind": "song_performance"})
        # The payload names the proposed work, while both exact pins stay in refs.
        performance_claim = rec(performance_work_rev, performance_work_id, "claim", {}, [
            {"role": "performance", "id": performance_id, "revision_id": performance_rev},
            {"role": "work", "id": work_id, "revision_id": work_rev},
        ], performance_citation)
        mbids = ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"]
        mappings = []
        for number, (mbid, relation) in enumerate(zip(mbids, ("skos:exactMatch", "skos:closeMatch")), start=1):
            mappings.append(rec("sha256:" + str(number + 5) * 64,
                "urn:uuid:40000000-0000-4000-8000-00000000001" + str(number), "claim",
                {"external_work": "https://musicbrainz.org/work/" + mbid, "mapping": relation},
                [{"role": "work", "id": work_id, "revision_id": work_rev}], work_claim_citation))
        value["records"] = [first_work, performance, performance_claim, *mappings]
        value["contracts"] = [
            {"id": performance_contract, "version": "1", "schema": {}, "context": work_ref_context},
            {"id": correspondence_contract, "version": "1", "schema": {}, "context": {"work": "arch:work"}},
        ]
        projection = build_projection(value)
        graph = f"<{projection.domain_graph.value}>"
        found = rows(query(projection, f"""
            SELECT ?mapping ?claim WHERE {{
              GRAPH {graph} {{ ?concept <http://xmlns.com/foaf/0.1/focus> <{work_id}> . }}
              GRAPH ?claim {{
                ?concept ?mapping ?externalConcept .
                VALUES ?mapping {{ <http://www.w3.org/2004/02/skos/core#exactMatch> <http://www.w3.org/2004/02/skos/core#closeMatch> }}
              }}
            }}
        """))
        self.assertEqual(len(found), 2)
        self.assertIn(uri("http://www.w3.org/2004/02/skos/core#exactMatch"), [row["mapping"] for row in found])
        self.assertIn(uri("http://www.w3.org/2004/02/skos/core#closeMatch"), [row["mapping"] for row in found])
        performance_rows = rows(query(projection, f"""SELECT ?work ?claim WHERE {{
            GRAPH ?claim {{ <{performance_id}> <urn:uuid:3e8b29f8-70c9-403e-bb4c-2280d59eca7f#work> ?work . }}
        }}"""))
        self.assertEqual(performance_rows[0]["work"], uri(work_id))
        self.assertEqual(performance_rows[0]["claim"], uri(f"{value['archive_id']}#revision={performance_work_rev}"))
        data = nquads(projection).decode()
        self.assertNotIn("http://www.w3.org/2002/07/owl#sameAs", data)

    def test_projects_exact_audio_selection_occurrence_and_work_chain(self):
        value = synthetic_fixture()
        value["spec"] = "0.0.4"
        value["context"] = json.loads((ROOT / "spec/0.0.4/context.jsonld").read_text())
        artifact_id = "urn:uuid:70000000-0000-4000-8000-000000000001"
        selection_id = "urn:uuid:70000000-0000-4000-8000-000000000002"
        occurrence_id = "urn:uuid:70000000-0000-4000-8000-000000000003"
        work_id = "urn:uuid:70000000-0000-4000-8000-000000000004"
        link_id = "urn:uuid:70000000-0000-4000-8000-000000000005"
        revisions = ["sha256:" + str(i) * 64 for i in range(1, 6)]
        contract_names = ["audio_selection", "song_occurrence", "performance_work"]
        versions = ["1", "1", "2"]
        files = [
            "sha256-bfde7fb0daaf7ab9af80eabe8d4e684e07f3a9dbf5cbec68266ddf4b9493cc28.json",
            "sha256-990e8f3a0938bb7ddd25f42a112f135b08460514038096807b573a6101a1a16f.json",
            "sha256-320b1bb020c95a0192e759f6c512de91b5ab0cd364fd2022bc8efb553fdafb61.json",
        ]
        value["contracts"] = [json.loads((ROOT / "spec/contracts" / name).read_text()) for name in files]
        citations = [[{"id": "archive.claim/" + name, "version": version, "digest": "sha256:" + "a" * 64}]
                     for name, version in zip(contract_names, versions)]
        artifact = rec(revisions[0], artifact_id, "artifact", {"content": {"digest": "sha256:" + "0" * 64, "size": 0}})
        selection = rec(revisions[1], selection_id, "claim", {"pieces": [
            {"start_sample": 100, "end_sample": 200, "sample_rate": 48000},
            {"start_sample": 250, "end_sample": 400, "sample_rate": 48000},
        ]}, [
            {"role": "piece", "id": artifact_id, "revision_id": revisions[0]},
            {"role": "piece", "id": artifact_id, "revision_id": revisions[0]},
        ], citations[0])
        occurrence = rec(revisions[2], occurrence_id, "claim", {}, [
            {"role": "selection", "id": selection_id, "revision_id": revisions[1]},
        ], citations[1])
        work = rec(revisions[3], work_id, "entity", {"kind": "musical_work", "label": "Paper Lanterns"})
        link = rec(revisions[4], link_id, "claim", {}, [
            {"role": "performance", "id": occurrence_id, "revision_id": revisions[2]},
            {"role": "work", "id": work_id, "revision_id": revisions[3]},
        ], citations[2])
        value["records"] = [artifact, selection, occurrence, work, link]
        projection = build_projection(value)
        graph = f"<{projection.domain_graph.value}>"
        found = rows(query(projection, f"""SELECT ?piece ?start ?source ?work WHERE {{
            GRAPH <{value['archive_id']}#revision={revisions[1]}> {{
                <{value['archive_id']}#revision={revisions[1]}> <urn:uuid:3e8b29f8-70c9-403e-bb4c-2280d59eca7f#piece> ?piece .
                ?piece <urn:uuid:3e8b29f8-70c9-403e-bb4c-2280d59eca7f#start_sample> ?start ;
                       <http://www.w3.org/ns/prov#wasDerivedFrom> ?source .
            }}
            GRAPH <{value['archive_id']}#revision={revisions[2]}> {{
                <{value['archive_id']}#revision={revisions[2]}> <urn:uuid:3e8b29f8-70c9-403e-bb4c-2280d59eca7f#selection> <{value['archive_id']}#revision={revisions[1]}> .
            }}
            GRAPH <{value['archive_id']}#revision={revisions[4]}> {{
                <{value['archive_id']}#revision={revisions[2]}> <urn:uuid:3e8b29f8-70c9-403e-bb4c-2280d59eca7f#work> ?work .
            }}
        }} ORDER BY ?start"""))
        self.assertEqual([row["start"] for row in found], [integer(100), integer(250)])
        self.assertEqual([row["source"] for row in found], [uri(f"{value['archive_id']}#revision={revisions[0]}")] * 2)
        self.assertEqual([row["work"] for row in found], [uri(f"{value['archive_id']}#revision={revisions[3]}")] * 2)
        self.assertIn(":piece=1", nquads(projection).decode())
        selection["payload"]["pieces"][1]["end_sample"] = 250
        with self.assertRaisesRegex(ProjectionError, "invalid piece"):
            build_projection(value)

    def test_door_admitted_chain_answers_every_example_query(self):
        # Captured from Door's worldContractAdmissionChecks with
        # ARCHIVE_TEST_WORLD_EXPORT set, so every record is real Door output.
        export = json.loads(DOOR_CHAIN_EXPORT.read_text())
        self.assertEqual((export["status"], export["spec"]), ("ok", "archive/" + (ROOT / "spec/VERSION").read_text().strip()))
        for record in export["records"]:
            for citation in record.get("contracts", []):
                installed = ROOT / "spec/contracts" / (citation["digest"].replace(":", "-") + ".json")
                self.assertTrue(installed.is_file(), citation)

        def cited(contract, version):
            [match] = [r for r in export["records"]
                       if {"id": contract, "version": version} in
                       [{"id": c["id"], "version": c["version"]} for c in r.get("contracts", [])]]
            return match

        def iri(record):
            return uri(f"{export['archive_id']}#revision={record['revision_id']}")

        by_revision = {r["revision_id"]: r for r in export["records"]}

        def pinned(record, role):
            return [by_revision[ref["revision_id"]] for ref in record["refs"] if ref["role"] == role]

        [work] = [r for r in export["records"] if r["payload"].get("kind") == "musical_work"]
        correspondence = cited("archive.claim/work_musicbrainz_correspondence", "1")
        structural = cited("archive.claim/performance_work", "1")
        typed = cited("archive.claim/performance_work", "2")
        selection = cited("archive.claim/audio_selection", "1")
        [audio] = pinned(selection, "piece")
        occurrence = cited("archive.claim/song_occurrence", "1")
        two_track = cited("archive.claim/audio_selection", "2")
        occurrence_v2 = cited("archive.claim/song_occurrence", "2")
        link_v3 = cited("archive.claim/performance_work", "3")
        # The v2 chain must run selection v2 -> occurrence v2 -> v3 -> the Work.
        self.assertEqual([r["revision_id"] for r in pinned(occurrence_v2, "selection")], [two_track["revision_id"]])
        self.assertEqual([r["revision_id"] for r in pinned(link_v3, "performance")], [occurrence_v2["revision_id"]])
        self.assertEqual([r["revision_id"] for r in pinned(link_v3, "work")], [work["revision_id"]])
        alignment = cited("archive.claim/capture_alignment", "1")
        kind = cited("archive.claim/selection_kind", "1")
        credit = cited("archive.claim/artist_credit", "1")
        judgment = cited("archive.assessment/judgment", "1")
        [judged_title] = pinned(judgment, "assessment_of")
        titles = [r for r in export["records"]
                  if {"id": "archive.claim/selection_title", "version": "1"} in
                  [{"id": c["id"], "version": c["version"]} for c in r.get("contracts", [])]]
        [other_title] = [r for r in titles if r["revision_id"] != judged_title["revision_id"]]
        track_sources = pinned(two_track, "piece")
        # The examples separate the two performance_work versions; if the
        # fixture collapsed them, both queries could pass on one claim.
        self.assertNotEqual(structural["revision_id"], typed["revision_id"])
        # The tracks must be different recordings with different bytes, and
        # the titles must disagree, or the queries could pass on one source
        # or one title.
        self.assertEqual(len(track_sources), 2)
        self.assertNotEqual(track_sources[0]["payload"]["content"]["digest"],
                            track_sources[1]["payload"]["content"]["digest"])
        self.assertNotEqual(judged_title["payload"]["title"], other_title["payload"]["title"])

        expected = {
            "performances-of-work.sparql": [{
                "performance": next(uri(ref["id"]) for ref in structural["refs"] if ref["role"] == "performance"),
                "performanceClaim": iri(structural),
            }],
            "song-occurrences-of-work.sparql": [{
                "occurrence": iri(occurrence),
                "selection": iri(selection),
                "piece": uri(iri(selection)["value"] + ":piece=0"),
                "source": iri(audio),
                "start": integer(10),
                "end": integer(200),
                "rate": integer(48000),
                "workClaim": iri(typed),
            }],
            "selection-tracks.sparql": [
                {
                    "occurrence": iri(occurrence_v2),
                    "selection": iri(two_track), "track": integer(index), "piece": integer(0),
                    "source": iri(source),
                    "start": integer(span["start_sample"]), "end": integer(span["end_sample"]),
                    "rate": integer(span["sample_rate"]), "alignment": iri(alignment),
                }
                for index, (source, span) in enumerate(zip(
                    track_sources, [track["pieces"][0] for track in two_track["payload"]["tracks"]]))
            ],
            "selection-facts.sparql": sorted(
                [
                    {"fact": lit("artist"), "value": iri(artist), "position": integer(index),
                     "claim": iri(credit), "agent": uri(credit["agent"])}
                    for index, artist in enumerate(pinned(credit, "artist"))
                ] + [
                    {"fact": lit("kind"), "value": lit(kind["payload"]["kind"]), "claim": iri(kind),
                     "agent": uri(kind["agent"])},
                    {"fact": lit("title"), "value": lit(judged_title["payload"]["title"]), "claim": iri(judged_title),
                     "agent": uri(judged_title["agent"]), "verdict": lit(judgment["payload"]["verdict"])},
                    {"fact": lit("title"), "value": lit(other_title["payload"]["title"]), "claim": iri(other_title),
                     "agent": uri(other_title["agent"])},
                ],
                key=lambda row: (row["fact"]["value"], int(row["position"]["value"]) if "position" in row else -1,
                                 row["value"]["value"], row["claim"]["value"])),
            "song-occurrence-tracks-of-work.sparql": [
                {
                    "occurrence": iri(occurrence_v2),
                    "selection": iri(two_track), "track": integer(index), "piece": integer(0),
                    "source": iri(source),
                    "start": integer(span["start_sample"]), "end": integer(span["end_sample"]),
                    "rate": integer(span["sample_rate"]), "workClaim": iri(link_v3),
                }
                for index, (source, span) in enumerate(zip(
                    track_sources, [track["pieces"][0] for track in two_track["payload"]["tracks"]]))
            ],
            "work-correspondences.sparql": [{
                "externalWork": uri("https://musicbrainz.org/work/11111111-1111-4111-8111-111111111111"),
                "mapping": uri("http://www.w3.org/2004/02/skos/core#exactMatch"),
                "claimRevision": iri(correspondence),
                "agent": uri(correspondence["agent"]),
            }],
        }
        examples = example_queries(export)
        self.assertEqual(set(examples), set(expected))
        with tempfile.TemporaryDirectory() as tmp:
            archive_root = Path(tmp) / "archive"
            archive_root.mkdir()
            store_path = Path(tmp) / "world"
            rebuild_store(archive_root, store_path, export_file=DOOR_CHAIN_EXPORT)
            projection = _read_current(store_path)
            for name, text in examples.items():
                with self.subTest(example=name):
                    self.assertEqual(rows(query(projection, text)), expected[name])
            # performance_work v3 links the occurrence v2 revision to the exact
            # Work revision inside its own claim graph, like version 2 does.
            linked = rows(query(projection, f"""SELECT ?work WHERE {{
                GRAPH <{iri(link_v3)["value"]}> {{ <{iri(occurrence_v2)["value"]}> <urn:uuid:3e8b29f8-70c9-403e-bb4c-2280d59eca7f#work> ?work . }}
            }}"""))
            self.assertEqual(linked, [{"work": iri(work)}])

    def test_musicbrainz_candidate_is_targeted_and_unavailable_is_explicit(self):
        from unittest.mock import patch
        from urllib.error import URLError

        with patch("world.world.urllib.request.urlopen", side_effect=URLError("offline")) as open_url:
            result = musicbrainz_work_candidate("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(open_url.call_args.args[0].full_url,
                         "https://musicbrainz.org/ws/2/work/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa?fmt=json")
        with self.assertRaisesRegex(ProjectionError, "UUID MBID"):
            musicbrainz_work_candidate("Paper Lanterns")


def fake_archive(directory, full_export=None, since_exports=None):
    """An `archive` command that answers from captured Door exports.

    `archive export` prints ``full_export``, or fails loudly when it is None so
    a test can prove an incremental refresh never asked for it.
    `archive export --since C` prints ``since_exports[C]`` and refuses any
    other commit as the real command does. Every call is logged.
    """
    directory.mkdir(parents=True, exist_ok=True)
    log = directory / "calls"
    log.write_text("")
    since_cases = "".join(
        f"    {commit}) cat {shlex.quote(str(path))}; exit 0;;\n"
        for commit, path in (since_exports or {}).items())
    full = (f"cat {shlex.quote(str(full_export))}; exit 0" if full_export is not None
            else "echo 'a full export was not expected' >&2; exit 97")
    script = directory / "archive"
    script.write_text(
        "#!/bin/sh\n"
        f"echo \"$*\" >> {shlex.quote(str(log))}\n"
        f"if [ \"$#\" = 1 ] && [ \"$1\" = export ]; then {full}; fi\n"
        "if [ \"$#\" = 3 ] && [ \"$1\" = export ] && [ \"$2\" = --since ]; then\n"
        "  case \"$3\" in\n"
        f"{since_cases}"
        "  esac\n"
        "  echo '{\"error\":\"since is not an ancestor of HEAD\",\"status\":\"failed\"}'; exit 1\n"
        "fi\n"
        "exit 64\n")
    script.chmod(0o755)
    return str(script), log


class IncrementalRefreshTests(unittest.TestCase):
    """ADR 0055: an incrementally refreshed generation equals a full one.

    The cut, the export since the cut, and the final export are all captured
    from one archive built by Door's World admission test, so the records,
    pins, and added set are what `archive export --since` really emits.
    """

    def setUp(self):
        self.cut = json.loads(DOOR_CHAIN_CUT_EXPORT.read_text())
        self.since = json.loads(DOOR_CHAIN_SINCE_EXPORT.read_text())
        self.final = json.loads(DOOR_CHAIN_EXPORT.read_text())
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.archive_root = self.tmp / "archive"
        self.archive_root.mkdir()

    def write(self, name, value):
        path = self.tmp / name
        path.write_text(json.dumps(value))
        return path

    def full_rebuild(self, export_file):
        return nquads(rebuild_store(self.archive_root, self.tmp / ("full-" + export_file.stem), export_file=export_file))

    def test_fixtures_are_one_real_cut(self):
        # Anchor the chain: the cut must be an earlier, different snapshot,
        # or every comparison below could pass on identical inputs.
        self.assertNotEqual(self.cut["snapshot"], self.final["snapshot"])
        self.assertEqual(self.since["since"], self.cut["snapshot"])
        self.assertEqual(self.since["snapshot"], self.final["snapshot"])
        revisions = lambda records: {r["revision_id"] for r in records}
        added = revisions(self.since["records"])
        self.assertTrue(added)
        self.assertTrue(self.since["pinned_records"])
        self.assertEqual(added, revisions(self.final["records"]) - revisions(self.cut["records"]))
        self.assertLessEqual(revisions(self.since["pinned_records"]), revisions(self.cut["records"]))
        self.assertNotEqual(nquads(build_projection(self.cut)), nquads(build_projection(self.final)))

    def test_incremental_refresh_equals_full_rebuild(self):
        store = self.tmp / "world"
        rebuild_store(self.archive_root, store, export_file=DOOR_CHAIN_CUT_EXPORT)
        cut_quads = nquads(_read_current(store))
        # The full export is a bomb: an incremental refresh must not ask for it.
        command, log = fake_archive(self.tmp / "bin", None, {self.cut["snapshot"]: DOOR_CHAIN_SINCE_EXPORT})
        refreshed = refresh_store(self.archive_root, store, archive_command=command)
        self.assertEqual((refreshed.mode, refreshed.projection.snapshot), ("incremental", self.final["snapshot"]))
        self.assertEqual(log.read_text().splitlines(), [f"export --since {self.cut['snapshot']}"])
        incremental = _read_current(store)
        full_store = self.tmp / "full"
        full = rebuild_store(self.archive_root, full_store, export_file=DOOR_CHAIN_EXPORT)
        self.assertEqual(nquads(incremental), nquads(full))
        self.assertEqual(nquads(incremental), nquads(build_projection(self.final)))
        # The previous generation is a separate, untouched generation.
        previous = Store.read_only(str(store / "versions" / world_module._generation_id(self.cut["snapshot"], PROJECTION_VERSION, world_module.PROJECTION_REVISIONS[PROJECTION_VERSION]) / "dataset"))
        self.assertEqual(nquads(Projection(previous, *[None] * 5)), cut_quads)
        rows_checked = 0
        for name, text in example_queries(self.final).items():
            with self.subTest(example=name):
                answer = rows(query(incremental, text))
                self.assertTrue(answer)
                self.assertEqual(answer, rows(query(full, text)))
                rows_checked += len(answer)
        self.assertGreater(rows_checked, len(example_queries(self.final)))

    def test_refresh_at_head_writes_nothing(self):
        store = self.tmp / "world"
        rebuild_store(self.archive_root, store, export_file=DOOR_CHAIN_EXPORT)
        # What `archive export --since HEAD` emits: the since export with
        # nothing added (archive/test checks the real command).
        at_head = dict(self.since, since=self.final["snapshot"], records=[], pinned_records=[], contracts=[])
        command, _ = fake_archive(self.tmp / "bin", None, {self.final["snapshot"]: self.write("head.json", at_head)})
        before = (store / "CURRENT").stat().st_mtime_ns
        generations = sorted(p.name for p in (store / "versions").iterdir())
        refreshed = refresh_store(self.archive_root, store, archive_command=command)
        self.assertEqual(refreshed.mode, "unchanged")
        self.assertEqual((store / "CURRENT").stat().st_mtime_ns, before)
        self.assertEqual(sorted(p.name for p in (store / "versions").iterdir()), generations)

    def assert_full(self, store, command, reason):
        refreshed = refresh_store(self.archive_root, store, archive_command=command)
        self.assertEqual(refreshed.mode, "full")
        self.assertIn(reason, refreshed.reason)
        self.assertEqual(nquads(_read_current(store)), self.full_rebuild(DOOR_CHAIN_EXPORT))
        return refreshed

    def test_missing_store_rebuilds_in_full(self):
        command, log = fake_archive(self.tmp / "bin", DOOR_CHAIN_EXPORT)
        self.assert_full(self.tmp / "world", command, "no selected generation")
        self.assertEqual(log.read_text().splitlines(), ["export"])

    def test_other_projection_version_rebuilds_in_full(self):
        store = self.tmp / "world"
        # A store whose only generation was built by an earlier projector.
        earlier = PROJECTION_VERSION - 1
        with patch("world.world.PROJECTION_VERSION", earlier), \
                patch.dict("world.world.PROJECTORS", {earlier: world_module._project}, clear=True):
            rebuild_store(self.archive_root, store, export_file=DOOR_CHAIN_CUT_EXPORT, version=earlier)
        self.assertTrue((store / "CURRENT").read_text().strip().endswith(f"-p{PROJECTION_VERSION - 1}"))
        command, log = fake_archive(self.tmp / "bin", DOOR_CHAIN_EXPORT,
                                    {self.cut["snapshot"]: DOOR_CHAIN_SINCE_EXPORT})
        self.assert_full(store, command, "another projection version")
        self.assertEqual(log.read_text().splitlines(), ["export"])

    def test_an_earlier_revision_rebuilds_in_full(self):
        # A store built before the latest addition to projection 6 (revision
        # 6 projects specification 0.0.10's claims).
        # Extending it would leave its older records without what the
        # addition projects.
        store = self.tmp / "world"
        current = world_module.PROJECTION_REVISIONS[PROJECTION_VERSION]
        self.assertEqual(current, 6)
        with patch.dict("world.world.PROJECTION_REVISIONS", {PROJECTION_VERSION: current - 1}):
            rebuild_store(self.archive_root, store, export_file=DOOR_CHAIN_CUT_EXPORT)
        selected = (store / f"CURRENT-p{PROJECTION_VERSION}").read_text().strip()
        self.assertEqual(selected, f"{self.cut['snapshot']}-p{PROJECTION_VERSION}r{current - 1}")
        command, log = fake_archive(self.tmp / "bin", DOOR_CHAIN_EXPORT,
                                    {self.cut["snapshot"]: DOOR_CHAIN_SINCE_EXPORT})
        self.assert_full(store, command, "predates an addition")
        self.assertEqual(log.read_text().splitlines(), ["export"])
        rebuilt = (store / f"CURRENT-p{PROJECTION_VERSION}").read_text().strip()
        self.assertEqual(rebuilt, f"{self.final['snapshot']}-p{PROJECTION_VERSION}r{current}")
        self.assertEqual(nquads(_read_current(store, PROJECTION_VERSION)), nquads(build_projection(self.final)))
        # The earlier generation still opens, so a relay can serve it until the rebuild.
        self.assertEqual(_read_current(store, PROJECTION_VERSION).snapshot, self.final["snapshot"])

    def test_non_ancestor_rebuilds_in_full(self):
        store = self.tmp / "world"
        rebuild_store(self.archive_root, store, export_file=DOOR_CHAIN_CUT_EXPORT)
        command, log = fake_archive(self.tmp / "bin", DOOR_CHAIN_EXPORT, {})
        self.assert_full(store, command, "not an ancestor")
        self.assertEqual(log.read_text().splitlines(), [f"export --since {self.cut['snapshot']}", "export"])

    def test_changed_context_rebuilds_in_full(self):
        store = self.tmp / "world"
        rebuild_store(self.archive_root, store, export_file=DOOR_CHAIN_CUT_EXPORT)
        changed = dict(self.since, spec=self.since["spec"] + "-changed")
        command, _ = fake_archive(self.tmp / "bin", DOOR_CHAIN_EXPORT,
                                  {self.cut["snapshot"]: self.write("changed.json", changed)})
        self.assert_full(store, command, "core context changed")

    def test_failed_refresh_keeps_the_previous_generation(self):
        store = self.tmp / "world"
        rebuild_store(self.archive_root, store, export_file=DOOR_CHAIN_CUT_EXPORT)
        selected = (store / "CURRENT").read_text()
        broken = dict(self.since, pinned_records=[])
        command, _ = fake_archive(self.tmp / "bin", None, {self.cut["snapshot"]: self.write("broken.json", broken)})
        with self.assertRaisesRegex(ProjectionError, "unresolved pinned reference"):
            refresh_store(self.archive_root, store, archive_command=command)
        self.assertEqual((store / "CURRENT").read_text(), selected)
        self.assertEqual([p.name for p in store.iterdir() if p.name.startswith(".world-build-")], [])
        self.assertEqual([p.name for p in (store / "versions").iterdir()], [selected.strip()])

    def test_unpinned_reference_resolves_through_the_previous_generation(self):
        # The synthetic fixture holds the only unpinned reference; Door's
        # chain pins every reference.
        value = synthetic_fixture()
        work, claim, _ = value["records"]
        claim = dict(claim, refs=[ref for ref in claim["refs"] if "revision_id" not in ref])
        self.assertEqual([ref["id"] for ref in claim["refs"]], [work["object_id"]])
        mapping = value["contracts"][0]
        cut = dict(value, snapshot="1" * 40, records=[work], contracts=[])
        final = dict(value, records=[work, claim], contracts=[mapping])
        since = dict(final, since=cut["snapshot"], records=[claim], pinned_records=[])
        store = self.tmp / "world"
        rebuild_store(self.archive_root, store, export_file=self.write("cut.json", cut))
        command, _ = fake_archive(self.tmp / "bin", None, {cut["snapshot"]: self.write("since.json", since)})
        self.assertEqual(refresh_store(self.archive_root, store, archive_command=command).mode, "incremental")
        self.assertEqual(nquads(_read_current(store)), nquads(build_projection(final)))
        # The swallowed branch: an object no generation holds is refused.
        store = self.tmp / "world-unknown"
        rebuild_store(self.archive_root, store, export_file=self.write("cut.json", cut))
        unknown = dict(claim, refs=[dict(claim["refs"][0], id="urn:uuid:30000000-0000-4000-8000-0000000000ff")])
        command, _ = fake_archive(self.tmp / "bin-unknown", None,
                                  {cut["snapshot"]: self.write("unknown.json", dict(since, records=[unknown]))})
        with self.assertRaisesRegex(ProjectionError, "unresolved reference"):
            refresh_store(self.archive_root, store, archive_command=command)

    def test_full_projection_refuses_an_export_since(self):
        with self.assertRaisesRegex(ProjectionError, "full archive export"):
            build_projection(self.since)

    def test_refresh_command_reports_what_it_did(self):
        store = self.tmp / "world"
        command, _ = fake_archive(self.tmp / "bin", DOOR_CHAIN_CUT_EXPORT, {self.cut["snapshot"]: DOOR_CHAIN_SINCE_EXPORT})
        reports = []
        for _ in range(2):
            output = io.StringIO()
            with redirect_stdout(output):
                code = _main(["refresh", str(self.archive_root), "--store", str(store), "--archive-command", command])
            self.assertEqual(code, 0)
            reports.append(json.loads(output.getvalue()))
        self.assertEqual([(r["refresh"], r["snapshot"]) for r in reports],
                         [("full", self.cut["snapshot"]), ("incremental", self.final["snapshot"])])


if __name__ == "__main__":
    unittest.main()


class StandardAnswerTests(unittest.TestCase):
    """World answers in standard formats and only from its own store (ADR 0059)."""

    def setUp(self):
        self.projection = build_projection(json.loads(DOOR_CHAIN_EXPORT.read_text()))

    def test_select_bindings_equal_the_engine_serializer(self):
        text = "SELECT ?g ?s ?p ?o ?missing WHERE { GRAPH ?g { ?s ?p ?o } } ORDER BY ?g ?s ?p ?o"
        ours = query(self.projection, text)
        engine = json.loads(self.projection.store.query(text).serialize(format=QueryResultsFormat.JSON))
        # Anchor: the answer must hold typed, language-free, and IRI terms, or
        # equality could pass on one kind of term.
        kinds = {(term["type"], "datatype" in term) for row in rows(ours) for term in row.values()}
        self.assertEqual(kinds, {("uri", False), ("literal", True), ("literal", False)})
        self.assertEqual(ours.media_type, SPARQL_RESULTS_JSON)
        self.assertEqual(ours.data, engine)
        self.assertEqual(ours.data["head"]["vars"], ["g", "s", "p", "o", "missing"])

    def test_ask_and_construct_are_standard(self):
        ask = query(self.projection, "ASK { GRAPH ?g { ?s ?p ?o } }")
        self.assertEqual((ask.media_type, ask.data), (SPARQL_RESULTS_JSON, {"head": {}, "boolean": True}))
        built = query(self.projection, "CONSTRUCT { ?s ?p ?o } WHERE { GRAPH ?g { ?s ?p ?o } } LIMIT 20")
        self.assertEqual(built.media_type, N_TRIPLES)
        parsed = list(parse(built.data.encode(), format=RdfFormat.N_TRIPLES))
        self.assertEqual(len(parsed), 20)

    def test_graph_answers_take_each_format_with_the_same_triples(self):
        text = "CONSTRUCT { ?s ?p ?o } WHERE { GRAPH ?g { ?s ?p ?o } } LIMIT 40"
        baseline = set(parse(query(self.projection, text).data.encode(), format=RdfFormat.N_TRIPLES))
        self.assertEqual(len(baseline), 40)
        self.assertEqual(set(world_module.GRAPH_FORMATS), {N_TRIPLES, "text/turtle", "application/ld+json"})
        for media_type, rdf_format in world_module.GRAPH_FORMATS.items():
            with self.subTest(media_type):
                answer = query(self.projection, text, rdf_format=media_type)
                self.assertEqual(answer.media_type, media_type)
                data = answer.data
                if media_type == "application/ld+json":
                    self.assertIsInstance(data, list)
                    data = json.dumps(data)
                self.assertEqual(set(parse(data.encode(), format=rdf_format)), baseline)
        # The format names only graph answers; a SELECT is unchanged.
        select = query(self.projection, "ASK {}", rdf_format="text/turtle")
        self.assertEqual(select.media_type, SPARQL_RESULTS_JSON)
        with self.assertRaises(world_module.QueryRefused):
            query(self.projection, text, rdf_format="application/rdf+xml")

    def test_row_bound_refuses_instead_of_truncating(self):
        text = "SELECT ?s WHERE { GRAPH ?g { ?s ?p ?o } }"
        total = len(rows(query(self.projection, text)))
        self.assertGreater(total, 3)
        self.assertEqual(len(rows(query(self.projection, text, max_rows=total))), total)
        with self.assertRaises(TooManyRows):
            query(self.projection, text, max_rows=total - 1)
        with self.assertRaises(TooManyRows):
            query(self.projection, "CONSTRUCT { ?s ?p ?o } WHERE { GRAPH ?g { ?s ?p ?o } }", max_rows=3)

    def test_service_is_refused_before_the_engine_runs(self):
        refused = [
            "SELECT * WHERE { SERVICE <http://192.0.2.1/> { ?s ?p ?o } }",
            "select * where { service silent <http://192.0.2.1/> { ?s ?p ?o } }",
            "SELECT * WHERE { SeRvIcE ?endpoint { ?s ?p ?o } }",
        ]
        for text in refused:
            with self.subTest(text=text), patch.object(Store, "query", side_effect=AssertionError("engine ran")):
                with self.assertRaises(QueryRefused):
                    query(self.projection, text)
        # The word is not the keyword inside strings, IRIs, comments,
        # variables, or prefixed names; these still run.
        allowed = [
            'SELECT ?service WHERE { GRAPH ?g { ?service ?p "SERVICE" } } LIMIT 1',
            "SELECT * WHERE { GRAPH ?g { ?s <http://example.org/SERVICE> ?o } } # SERVICE <x>",
            "PREFIX service: <http://example.org/> SELECT * WHERE { GRAPH ?g { ?s service:x ?o } }",
        ]
        for text in allowed:
            with self.subTest(text=text):
                self.assertEqual(query(self.projection, text).media_type, SPARQL_RESULTS_JSON)

    def test_the_projection_states_its_version_and_specification(self):
        export = json.loads(DOOR_CHAIN_EXPORT.read_text())
        found = rows(query(self.projection, """
            PREFIX arch: <urn:uuid:3e8b29f8-70c9-403e-bb4c-2280d59eca7f#>
            SELECT ?projection ?version ?spec ?conforms WHERE { GRAPH ?g {
              ?projection a arch:WorldProjection ; arch:projection_version ?version ;
                          arch:spec ?spec ; <http://purl.org/dc/terms/conformsTo> ?conforms .
            } }"""))
        self.assertEqual(found, [{
            "projection": uri(export["archive_id"] + "#world"),
            "version": integer(PROJECTION_VERSION),
            "spec": lit(export["spec"]),
            "conforms": uri(f"urn:uuid:3e8b29f8-70c9-403e-bb4c-2280d59eca7f#world-projection-{PROJECTION_VERSION}"),
        }])


def renamed_projector(store, data, known_object=None):
    """A stand-in next projection version: version 6 with one predicate
    renamed, as a real rename would be."""
    world_module._project(store, data, known_object)
    arch = "urn:uuid:3e8b29f8-70c9-403e-bb4c-2280d59eca7f#"
    for old, new in ((arch + "verdict", arch + "judgment_verdict"),):
        for quad in list(store.quads_for_pattern(None, NamedNode(old), None, None)):
            store.remove(quad)
            store.add(Quad(quad.subject, NamedNode(new), quad.object, quad.graph_name))
    for quad in list(store.quads_for_pattern(None, NamedNode(arch + "projection_version"), None, None)):
        store.remove(quad)
        store.add(Quad(quad.subject, quad.predicate, Literal(PROJECTION_VERSION + 1), quad.graph_name))


class ContractLinkTests(unittest.TestCase):
    """Records link to the contracts they cite, so a query can select by
    contract and reach claim graphs through an index (ADR 0059, revision 2)."""

    def setUp(self):
        self.export = json.loads(DOOR_CHAIN_EXPORT.read_text())
        self.projection = build_projection(self.export)
        self.archive = self.export["archive_id"]

    def select(self, text):
        return sorted(tuple(sorted((k, v["value"]) for k, v in row.items())) for row in rows(query(self.projection, text)))

    def test_each_record_links_exactly_the_contracts_it_cites(self):
        store, records = self.projection.store, self.projection.records_graph
        linked = 0
        for record in self.export["records"]:
            subject = NamedNode(f"{self.archive}#revision={record['revision_id']}")
            found = {quad.object.value for quad in store.quads_for_pattern(
                subject, NamedNode(world_module.DCTERMS + "conformsTo"), None, records)}
            expected = {world_module.contract_node(self.archive, c["id"], c["version"]).value
                        for c in record.get("contracts", [])}
            self.assertEqual(found, expected, record["revision_id"])
            linked += len(found)
            for node in found:
                # Every link resolves to the bundle's node in the context graph.
                self.assertTrue(any(True for _ in store.quads_for_pattern(
                    NamedNode(node), NamedNode(world_module.DCTERMS + "identifier"), None, self.projection.context_graph)))
        self.assertGreater(linked, 0)

    def test_the_documented_patterns_answer_as_the_text_matches_do(self):
        prefix = "PREFIX a: <urn:uuid:3e8b29f8-70c9-403e-bb4c-2280d59eca7f#>\nPREFIX dcterms: <http://purl.org/dc/terms/>\n"
        records, context = f"<{self.archive}#world:records>", f"<{self.archive}#world:context>"
        scanned = self.select('SELECT ?g ?s ?p ?o WHERE { GRAPH ?g { ?s ?p ?o } FILTER(CONTAINS(STR(?g), "#revision=")) }')
        indexed = self.select(prefix + f"SELECT ?g ?s ?p ?o WHERE {{ GRAPH {records} {{ VALUES ?t {{ a:Claim a:Assessment }} ?g a ?t }} GRAPH ?g {{ ?s ?p ?o }} }}")
        self.assertTrue(scanned)
        self.assertEqual(indexed, scanned)
        cited = sorted({c["id"] for r in self.export["records"] for c in r.get("contracts", [])})
        self.assertGreater(len(cited), 1)
        answered = 0
        for contract in cited:
            with self.subTest(contract):
                by_text = self.select(prefix + f"SELECT ?g ?s ?p ?o WHERE {{ GRAPH {records} {{ ?g a:contracts ?c }} FILTER(CONTAINS(STR(?c), '\"id\":\"{contract}\"')) GRAPH ?g {{ ?s ?p ?o }} }}")
                by_link = self.select(prefix + f"SELECT ?g ?s ?p ?o WHERE {{ GRAPH {context} {{ ?contract dcterms:identifier \"{contract}\" }} GRAPH {records} {{ ?g dcterms:conformsTo ?contract }} GRAPH ?g {{ ?s ?p ?o }} }}")
                self.assertEqual(by_link, by_text)
                answered += bool(by_text)
        # Anchor: some contract must select claim graphs, or every equality
        # above could hold between two empty answers.
        self.assertGreater(answered, 0)


class OverlapWindowTests(unittest.TestCase):
    """Two projection versions side by side (ADR 0059)."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = Path(directory.name)
        self.archive_root = self.tmp / "archive"
        self.archive_root.mkdir()
        self.store = self.tmp / "world"
        self.next = PROJECTION_VERSION + 1
        self.verdict = """PREFIX arch: <urn:uuid:3e8b29f8-70c9-403e-bb4c-2280d59eca7f#>
            ASK { GRAPH ?g { ?judgment arch:%s ?verdict } }"""

    def test_selected_generation_names_each_versions_generation_or_none(self):
        self.assertIsNone(world_module.selected_generation(self.store, PROJECTION_VERSION))  # no store yet
        command, _ = fake_archive(self.tmp / "bin", DOOR_CHAIN_EXPORT)
        with patch("world.world.PROJECTION_VERSION", self.next), \
                patch.dict("world.world.PROJECTORS", {self.next: renamed_projector}):
            refresh_served(self.archive_root, self.store, archive_command=command)
        old = world_module.selected_generation(self.store, PROJECTION_VERSION)
        new = world_module.selected_generation(self.store, self.next)
        self.assertNotEqual(old, new)
        self.assertEqual(old, (self.store / f"CURRENT-p{PROJECTION_VERSION}").read_text().strip())
        self.assertEqual(new, (self.store / f"CURRENT-p{self.next}").read_text().strip())
        self.assertIsNone(world_module.selected_generation(self.store, self.next + 1))
        # A store from before per-version pointers: CURRENT answers only for
        # the version its generation was built by.
        (self.store / f"CURRENT-p{PROJECTION_VERSION}").unlink()
        (self.store / f"CURRENT-p{self.next}").unlink()
        self.assertEqual(world_module.selected_generation(self.store, self.next), new)
        self.assertIsNone(world_module.selected_generation(self.store, PROJECTION_VERSION))

    def test_both_versions_are_built_and_served_separately(self):
        command, _ = fake_archive(self.tmp / "bin", DOOR_CHAIN_EXPORT)
        with patch("world.world.PROJECTION_VERSION", self.next), \
                patch.dict("world.world.PROJECTORS", {self.next: renamed_projector}):
            served = refresh_served(self.archive_root, self.store, archive_command=command)
            self.assertEqual(sorted(served), [PROJECTION_VERSION, self.next])
            self.assertEqual(served_projections(self.store), [PROJECTION_VERSION, self.next])
            old, new = _read_current(self.store, PROJECTION_VERSION), _read_current(self.store, self.next)
            # Anchor: the two generations differ, or the checks below could
            # pass on one generation read twice.
            self.assertNotEqual(nquads(old), nquads(new))
            self.assertEqual((old.version, new.version), (PROJECTION_VERSION, self.next))
            self.assertTrue(query(old, self.verdict % "verdict").data["boolean"])
            self.assertFalse(query(old, self.verdict % "judgment_verdict").data["boolean"])
            self.assertTrue(query(new, self.verdict % "judgment_verdict").data["boolean"])
            self.assertEqual(_read_current(self.store).version, self.next)
            with self.assertRaises(ProjectionNotServed) as refused:
                _read_current(self.store, PROJECTION_VERSION - 1)
            self.assertEqual(refused.exception.served, [PROJECTION_VERSION, self.next])

    def test_a_version_leaving_the_window_stops_being_served(self):
        command, _ = fake_archive(self.tmp / "bin", DOOR_CHAIN_EXPORT)
        with patch("world.world.PROJECTION_VERSION", self.next), \
                patch.dict("world.world.PROJECTORS", {self.next: renamed_projector}):
            refresh_served(self.archive_root, self.store, archive_command=command)
        with patch("world.world.PROJECTION_VERSION", self.next), \
                patch.dict("world.world.PROJECTORS", {self.next: renamed_projector}, clear=True):
            refresh_served(self.archive_root, self.store, archive_command=command)
            self.assertEqual(served_projections(self.store), [self.next])
            with self.assertRaises(ProjectionNotServed):
                _read_current(self.store, PROJECTION_VERSION)

    def test_a_store_from_before_version_pointers_still_answers(self):
        rebuild_store(self.archive_root, self.store, export_file=DOOR_CHAIN_EXPORT)
        (self.store / f"CURRENT-p{PROJECTION_VERSION}").unlink()
        self.assertEqual(_read_current(self.store, PROJECTION_VERSION).version, PROJECTION_VERSION)
        self.assertEqual(served_projections(self.store), [PROJECTION_VERSION])

    def test_an_unreadable_pointer_is_an_error_not_a_missing_generation(self):
        rebuild_store(self.archive_root, self.store, export_file=DOOR_CHAIN_EXPORT)
        pointer = self.store / f"CURRENT-p{PROJECTION_VERSION}"
        pointer.chmod(0)
        self.addCleanup(pointer.chmod, 0o600)
        if os.access(pointer, os.R_OK):
            self.skipTest("running as a user who can read any file")
        command, log = fake_archive(self.tmp / "bin", DOOR_CHAIN_EXPORT)
        with self.assertRaises(PermissionError):
            refresh_store(self.archive_root, self.store, archive_command=command)
        self.assertFalse(log.exists() and log.read_text())

    def test_corrupt_generation_metadata_rebuilds_in_full(self):
        rebuild_store(self.archive_root, self.store, export_file=DOOR_CHAIN_CUT_EXPORT)
        selected = (self.store / "CURRENT").read_text().strip()
        (self.store / "versions" / selected / "metadata.json").write_text("{not json")
        command, _ = fake_archive(self.tmp / "bin", DOOR_CHAIN_EXPORT)
        refreshed = refresh_store(self.archive_root, self.store, archive_command=command)
        self.assertEqual((refreshed.mode, refreshed.reason), ("full", "no selected generation"))
