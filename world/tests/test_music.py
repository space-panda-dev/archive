"""World projects specification 0.0.9's chord stacks, meter layers,
contributions, and band membership (ADRs 0077 and 0078, D-17).

Every check reads the published fixture archive, which fixture/build.py admits
through the real Door: a machine's chord stacks from the mix, a chart's chord
stacks and meter with their alignment and locators, credits at each scope,
a detector's located instrument, and two memberships. Refusals are tested
against Door's verdicts in test_door_agreement.py.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from world.tests.fixture_exports import fixture_export
from world import contributions
from world.tests.test_conformance import remove_tree
from world.world import SPARQL_RESULTS_JSON, build_projection, query

ROOT = Path(__file__).resolve().parents[2]
PLAN = json.loads((ROOT / "fixture/plan.json").read_text())
ARCH = "urn:uuid:3e8b29f8-70c9-403e-bb4c-2280d59eca7f#"
PROV = "http://www.w3.org/ns/prov#"
SKOS = "http://www.w3.org/2004/02/skos/core#"
ORG = "http://www.w3.org/ns/org#"
XSD = "http://www.w3.org/2001/XMLSchema#"


def planned(key):
    [record] = [r for step in PLAN["steps"] for r in step["records"] if r["key"] == key]
    return record


class MusicProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.manifest, cls.export = fixture_export()
        cls.projection = build_projection(cls.export)

    @classmethod
    def tearDownClass(cls):
        remove_tree(cls.tmp)

    def iri(self, key):
        return f"{self.export['archive_id']}#revision={self.manifest['objects'][key]['revision_id']}"

    def ask(self, text):
        answer = query(self.projection, text)
        self.assertEqual(answer.media_type, SPARQL_RESULTS_JSON)
        return [{k: v["value"] for k, v in row.items()} for row in answer.data["results"]["bindings"]]

    def test_readings_keep_their_rank_weight_and_tones(self):
        graph = self.iri("chords")
        rows = self.ask(f"""PREFIX arch: <{ARCH}> PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
            SELECT ?span ?rank ?weight ?bass WHERE {{ GRAPH <{graph}> {{
              <{graph}> arch:chord_spans ?s . ?s rdf:value ?span ; arch:readings ?r .
              ?r rdf:value ?rank ; arch:weight ?weight . OPTIONAL {{ ?r arch:bass ?bass }} }} }}
            ORDER BY ?span ?rank""")
        spans = planned("chords")["payload"]["spans"]
        expected = [{"span": str(i), "rank": str(j), "weight": r["weight"], **({"bass": str(r["bass"])} if "bass" in r else {})}
                    for i, s in enumerate(spans) for j, r in enumerate(s["readings"])]
        self.assertEqual(rows, expected)
        tones = self.ask(f"""PREFIX arch: <{ARCH}> SELECT ?tone WHERE {{ GRAPH <{graph}> {{
              <{graph}:span=1:reading=0> arch:tones ?tone }} }} ORDER BY ?tone""")
        self.assertEqual([int(row["tone"]) for row in tones], spans[1]["readings"][0]["tones"])
        uncertain = self.ask(f"""PREFIX arch: <{ARCH}> SELECT ?present ?pc WHERE {{ GRAPH <{graph}> {{
              <{graph}:span=1:reading=0> arch:uncertain_tones ?u . ?u arch:present ?present ; arch:pitch_classes ?pc }} }}
              ORDER BY ?pc""")
        self.assertEqual([(row["present"], int(row["pc"])) for row in uncertain], [("exactly_one", 7), ("exactly_one", 8)])

    def test_a_chart_reading_names_its_document_alignment_and_part(self):
        graph = self.iri("chords-chart")
        [row] = self.ask(f"""PREFIX arch: <{ARCH}> PREFIX dcterms: <http://purl.org/dc/terms/>
            SELECT ?document ?method ?bound ?url ?part ?contribution WHERE {{ GRAPH <{graph}> {{
              <{graph}> dcterms:source ?document ; arch:document_alignment ?a ; arch:document_url ?url ;
                        arch:part ?part ; arch:part_contribution ?contribution .
              ?a arch:alignment_method ?method ; arch:max_error_samples ?bound }} }}""")
        payload = planned("chords-chart")["payload"]
        self.assertEqual(row, {"document": self.iri("chart"), "method": "manual", "bound": "400",
                               "url": payload["document_url"], "part": "keys",
                               "contribution": self.iri("contribution-recording")})
        locators = self.ask(f"""PREFIX arch: <{ARCH}> SELECT ?bar ?beat WHERE {{ GRAPH <{graph}> {{
              ?span arch:locator ?l . ?l arch:locator_bar ?bar ; arch:locator_beat ?beat }} }} ORDER BY ?beat""")
        self.assertEqual(locators, [{"bar": "1", "beat": "1"}, {"bar": "1", "beat": "3"}])

    def test_meter_keeps_layers_groupings_roles_and_links(self):
        graph = self.iri("meter-chart")
        bars = self.ask(f"""PREFIX arch: <{ARCH}> PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
            SELECT ?label ?role ?numerator ?denominator ?grouping WHERE {{ GRAPH <{graph}> {{
              <{graph}> arch:meter_layers ?layer . ?layer rdfs:label ?label ; arch:layer_role ?role ; arch:bars ?bar .
              ?bar arch:numerator ?numerator ; arch:denominator ?denominator ; arch:start_sample ?start .
              OPTIONAL {{ ?bar arch:grouping ?grouping }} }} }} ORDER BY ?label ?start""")
        self.assertEqual(bars, [
            {"label": "in four", "role": "written", "numerator": "4", "denominator": "4"},
            {"label": "in four", "role": "written", "numerator": "6", "denominator": "8", "grouping": "3+3"},
            {"label": "in three", "role": "felt", "numerator": "3", "denominator": "4"},
            {"label": "in three", "role": "felt", "numerator": "6", "denominator": "8", "grouping": "3+3"}])
        links = self.ask(f"""PREFIX arch: <{ARCH}> SELECT ?kind ?bar ?side ?denominator ?dots WHERE {{ GRAPH <{graph}> {{
              <{graph}> arch:metric_modulations ?m . ?m arch:modulation_kind ?kind ; arch:modulated_bar ?bar ;
              ?side ?note . ?note arch:denominator ?denominator . OPTIONAL {{ ?note arch:dots ?dots }}
              VALUES ?side {{ arch:before_note arch:after_note arch:pulse_before arch:pulse_after }} }} }}
              ORDER BY ?bar ?side""")
        self.assertEqual(links, [
            {"kind": "equation", "bar": f"{graph}:layer=0:bar=1", "side": ARCH + "after_note", "denominator": "4", "dots": "1"},
            {"kind": "equation", "bar": f"{graph}:layer=0:bar=1", "side": ARCH + "before_note", "denominator": "4"},
            {"kind": "regrouping", "bar": f"{graph}:layer=1:bar=1", "side": ARCH + "pulse_after", "denominator": "4", "dots": "1"},
            {"kind": "regrouping", "bar": f"{graph}:layer=1:bar=1", "side": ARCH + "pulse_before", "denominator": "4"}])

    def test_a_beat_only_layer_asserts_no_meter(self):
        graph = self.iri("meter-beats")
        rows = self.ask(f"""PREFIX arch: <{ARCH}> SELECT ?beat ?down WHERE {{ GRAPH <{graph}> {{
              <{graph}> arch:meter_layers ?layer . ?layer arch:beat_samples ?beat .
              OPTIONAL {{ ?layer arch:downbeat_samples ?down FILTER (?down = ?beat) }} }} }} ORDER BY ?beat""")
        self.assertEqual(rows, [{"beat": "0", "down": "0"}, {"beat": "1000"}, {"beat": "2000", "down": "2000"},
                                {"beat": "3000"}])
        bars = self.ask(f"""PREFIX arch: <{ARCH}> SELECT ?bar WHERE {{ GRAPH <{graph}> {{ ?layer arch:bars ?bar }} }}""")
        self.assertEqual(bars, [])

    def test_a_sequence_is_untimed_and_keeps_no_lyrics(self):
        graph = self.iri("sequence")
        entries = self.ask(f"""PREFIX arch: <{ARCH}> PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
            SELECT ?n ?line ?section WHERE {{ GRAPH <{graph}> {{
              <{graph}> arch:sequence_entries ?e ; arch:described_work ?work . ?e rdf:value ?n ;
              arch:locator ?l . ?l arch:locator_line ?line . OPTIONAL {{ ?e arch:section ?section }} }} }} ORDER BY ?n""")
        self.assertEqual(entries, [{"n": "0", "line": "2", "section": "Verse"}, {"n": "1", "line": "2"},
                                   {"n": "2", "line": "3", "section": "Coda"}])
        samples = self.ask(f"""PREFIX arch: <{ARCH}> SELECT ?x WHERE {{ GRAPH <{graph}> {{ ?s arch:start_sample ?x }} }}""")
        self.assertEqual(samples, [])
        [repeat] = self.ask(f"""PREFIX arch: <{ARCH}> SELECT ?first ?last ?times WHERE {{ GRAPH <{graph}> {{
              <{graph}> arch:repeats ?r . ?r arch:first_entry ?first ; arch:last_entry ?last ; arch:times ?times }} }}""")
        self.assertEqual(repeat, {"first": f"{graph}:entry=0", "last": f"{graph}:entry=1", "times": "2"})

    def test_a_placement_names_its_sequence_entries_and_held_ends(self):
        graph, sequence = self.iri("chords-placed"), self.iri("sequence")
        rows = self.ask(f"""PREFIX arch: <{ARCH}> PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
            SELECT ?n ?end ?entry ?rule WHERE {{ GRAPH <{graph}> {{
              <{graph}> arch:placed_sequence <{sequence}> ; arch:hold_rule ?rule ; arch:chord_spans ?s .
              ?s rdf:value ?n ; arch:span_end ?end ; arch:sequence_entry ?entry }} }} ORDER BY ?n""")
        rule = planned("chords-placed")["payload"]["hold_rule"]
        self.assertEqual(rows, [{"n": "0", "end": "held", "entry": f"{sequence}:entry=0", "rule": rule},
                                {"n": "1", "end": "held", "entry": f"{sequence}:entry=1", "rule": rule},
                                {"n": "2", "end": "asserted", "entry": f"{sequence}:entry=0", "rule": rule}])

    def test_a_correction_cites_what_it_corrects(self):
        graph = self.iri("chords-correction")
        rows = self.ask(f"""PREFIX arch: <{ARCH}> SELECT ?c WHERE {{ GRAPH <{graph}> {{ <{graph}> arch:corrects ?c }} }}""")
        self.assertEqual(rows, [{"c": self.iri("chords")}])
        # The corrected claim is untouched: its graph still reads as admitted.
        machine = self.ask(f"""PREFIX arch: <{ARCH}> SELECT ?s WHERE {{ GRAPH <{self.iri('chords')}> {{
              <{self.iri('chords')}> arch:chord_spans ?s }} }}""")
        self.assertEqual(len(machine), len(planned("chords")["payload"]["spans"]))

    def test_a_credit_is_a_qualified_attribution_in_a_musicbrainz_role(self):
        graph = self.iri("contribution-recording")
        [row] = self.ask(f"""PREFIX prov: <{PROV}> PREFIX arch: <{ARCH}>
            SELECT ?subject ?agent ?role ?instrument WHERE {{ GRAPH <{graph}> {{
              ?subject prov:qualifiedAttribution ?a . ?a prov:agent ?agent ; prov:hadRole ?role ;
              arch:instruments ?instrument }} }}""")
        kind = contributions.ROLE_TYPES["recording"]["instrument"]
        self.assertEqual(row, {"subject": self.iri("recording-entity"), "agent": self.iri("guest"),
                               "role": contributions.role_concept(kind),
                               "instrument": planned("contribution-recording")["payload"]["role"]["instruments"][0]})
        # The role is a concept of the specification's scheme, matched to
        # MusicBrainz's relationship type by its identifier.
        [concept] = self.ask(f"""PREFIX skos: <{SKOS}> SELECT ?notation ?match WHERE {{ GRAPH <{self.projection.domain_graph.value}> {{
              <{contributions.role_concept(kind)}> skos:notation ?notation ; skos:exactMatch ?match }} }}""")
        self.assertEqual(concept, {"notation": "instrument",
                                   "match": contributions.MUSICBRAINZ_RELATIONSHIP + kind.gid})

    def test_a_detector_locates_without_naming_anyone(self):
        graph = self.iri("contribution-detected")
        rows = self.ask(f"""PREFIX prov: <{PROV}> PREFIX arch: <{ARCH}>
            SELECT ?agent ?scale ?value WHERE {{ GRAPH <{graph}> {{
              ?subject prov:qualifiedAttribution ?a . OPTIONAL {{ ?a prov:agent ?agent }}
              <{graph}> arch:confidence ?c ; prov:wasGeneratedBy ?run . ?c arch:confidence_scale ?scale ;
              arch:confidence_value ?value }} }}""")
        self.assertEqual(rows, [{"scale": "probability", "value": "0.8"}])

    def test_a_membership_is_an_organization_membership(self):
        graph = self.iri("membership")
        [row] = self.ask(f"""PREFIX org: <{ORG}> PREFIX arch: <{ARCH}>
            SELECT ?member ?band ?from ?to ?role WHERE {{ GRAPH <{graph}> {{
              ?member org:memberOf ?band . ?m org:member ?member ; org:organization ?band ; org:role ?role ;
              arch:member_from ?from ; arch:member_to ?to }} }}""")
        self.assertEqual(row, {"member": self.iri("guest"), "band": self.iri("ensemble"), "from": "2020",
                               "to": "2021-06", "role": contributions.role_concept(contributions.MEMBER_OF_BAND)})

    def test_release_and_recording_references_get_no_direct_triple(self):
        # `release` and `recording` are core terms naming Entity kinds; a
        # reference keeps its reified statement, as `artifact` does.
        records = self.projection.records_graph.value
        rows = self.ask(f"""SELECT ?p WHERE {{ GRAPH <{records}> {{ <{self.iri('contribution-release')}> ?p
              <{self.iri('release')}> }} }}""")
        self.assertNotIn({"p": ARCH + "Release"}, rows)
        self.assertIn({"p": "http://www.w3.org/ns/oa#hasTarget"}, rows)


if __name__ == "__main__":
    unittest.main()
