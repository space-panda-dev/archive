"""The role scheme of specification 0.0.9 (D-17) is one table in three places.

world/contributions.py holds MusicBrainz's role types, attributes, and vocals
as World re-checks them; the specification ships the same table as a SKOS
scheme in Turtle; and the contribution and band membership bundles encode it
as JSON Schema for Door. A change to one that is not made to the others fails
here. Needs rdflib (world/requirements-test.txt).
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import rdflib
from rdflib.namespace import RDF, SKOS

from world import contributions as C

ROOT = Path(__file__).resolve().parents[2]
ARCH = rdflib.Namespace(C.ARCH)


def bundle(identifier: str) -> dict:
    catalog = json.loads((ROOT / "spec/0.0.9/contracts.json").read_text())
    [digest] = [c["digest"] for c in catalog["contracts"] if c["id"] == identifier and c["version"] == "1"]
    return json.loads((ROOT / "spec/contracts" / f"sha256-{digest.removeprefix('sha256:')}.json").read_text())


def specification_scheme() -> rdflib.Graph:
    text = (ROOT / "spec/archive-spec.md").read_text()
    blocks = re.findall(r"```turtle\n(.*?)```", text, re.S)
    if len(blocks) != 1:
        raise AssertionError(f"the specification holds {len(blocks)} Turtle blocks, not the one role scheme")
    graph = rdflib.Graph()
    graph.parse(data=blocks[0], format="turtle")
    return graph


def table() -> dict[str, dict[str, C.RoleType]]:
    pairs: dict[str, dict[str, C.RoleType]] = {}
    for scope in C.ROLE_TYPES.values():
        for kind in scope.values():
            pairs.setdefault(kind.pair, {})[kind.name] = kind
    pairs[C.MEMBER_OF_BAND.pair] = {C.MEMBER_OF_BAND.name: C.MEMBER_OF_BAND}
    return pairs


class RoleScheme(unittest.TestCase):
    def test_the_specification_scheme_is_the_code_table(self):
        graph = specification_scheme()
        found = {}
        for concept in graph.subjects(RDF.type, SKOS.Concept):
            scheme = graph.value(concept, SKOS.inScheme)
            if scheme == rdflib.URIRef(C.VOCAL_SCHEME):
                continue
            match = graph.value(concept, SKOS.exactMatch)
            found[str(concept)] = (
                str(scheme), str(graph.value(concept, SKOS.notation)),
                str(match).removeprefix(C.MUSICBRAINZ_RELATIONSHIP) if match else None,
                frozenset(str(a) for a in graph.objects(concept, ARCH.takesAttribute)),
                bool(graph.value(concept, ARCH.takesInstruments)), bool(graph.value(concept, ARCH.takesVocals)))
        expected = {C.role_concept(kind): (C.role_scheme(pair), kind.name, kind.gid, kind.attributes,
                                            kind.instruments, kind.vocals)
                    for pair, kinds in table().items() for kind in kinds.values()}
        # Anchor: the table is not empty and names the types ADR 0078 cites.
        self.assertIn(C.role_concept(C.ROLE_TYPES["recording"]["instrument"]), expected)
        self.assertEqual(found, expected)
        vocals = {str(graph.value(concept, SKOS.notation))
                  for concept in graph.subjects(SKOS.inScheme, rdflib.URIRef(C.VOCAL_SCHEME))}
        self.assertEqual(vocals, set(C.VOCALS))

    def test_the_contribution_bundle_encodes_the_code_table(self):
        schema = bundle("archive.claim/contribution")["schema"]
        for scope in C.SCOPES:
            name = "recording" if scope == "span" else scope
            with self.subTest(scope=scope):
                encoded = {}
                for branch in schema["$defs"]["role_" + name]["anyOf"]:
                    properties = branch["properties"]
                    encoded[properties["type"]["const"]] = (
                        frozenset(properties.get("attributes", {}).get("items", {}).get("enum", [])),
                        "instruments" in properties, "instruments" in branch["required"], "vocals" in properties)
                expected = {kind.name: (kind.attributes, kind.instruments, kind.requires_instrument, kind.vocals)
                            for kind in C.ROLE_TYPES[scope].values()}
                self.assertEqual(encoded, expected)
        # The scope's role is the table's: every scope's `if` points at it.
        pointed = {branch["if"]["properties"]["payload"]["properties"]["scope"]["const"]:
                   branch["then"]["properties"]["payload"]["properties"]["role"]["$ref"]
                   for branch in schema["allOf"] if "if" in branch and "scope" in json.dumps(branch["if"])}
        self.assertEqual(pointed, {scope: "#/$defs/role_" + ("recording" if scope == "span" else scope)
                                   for scope in C.SCOPES})

    def test_vocals_and_membership_attributes_are_the_code_tables(self):
        membership = bundle("archive.claim/band_membership")["schema"]["properties"]["payload"]["properties"]
        self.assertEqual(set(membership["vocals"]["items"]["enum"]), set(C.VOCALS))
        self.assertEqual(set(membership["attributes"]["items"]["enum"]), set(C.MEMBERSHIP_ATTRIBUTES))
        self.assertEqual(C.MEMBER_OF_BAND.attributes, C.MEMBERSHIP_ATTRIBUTES)


if __name__ == "__main__":
    unittest.main()
