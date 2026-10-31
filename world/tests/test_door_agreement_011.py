"""Door and World agree on specification 0.0.11's six contract versions (D-19).

As test_door_agreement_010.py does for 0.0.10's: each case starts from a
record the real Door admitted into the published fixture archive
(fixture/build.py), changes one thing, and gives the result to both. Door
judges it by `door admit --dry-run`; World by the check its module states
for the contract (form, notes, lead_sheets), given the pinned records read
back from `archive export`. Every list has accepted and refused cases.

D-19 names the cross-claim checks that are World's alone: a lead sheet
pinning a sketch, a lead sheet's timed lyrics carried by notes no pinned
melody spans selects, an underlay's items and passes against its melody,
form, and meter, and form spans' passes against the pinned form. The cases
in WORLD_ONLY show that split as it is stated: Door admits each and World
refuses it, so a change on either side that moves a rule is seen here.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable

from world import exports, form, lead_sheets, notes
from world.tests import test_door_agreement as agreement
from world.tests.test_door_agreement import (
    ROOT,
    Change,
    both,
    delete_in,
    drop_role,
    keep,
    payload,
    pin,
    repin,
    set_in,
    whole,
)
from world.tests.test_conformance import build_fixture_archive, integration_binaries, remove_tree


def at(*path: Any) -> Callable[[dict[str, Any]], Any]:
    def walk(p: dict[str, Any]) -> Any:
        for step in path:
            p = p[step]
        return p
    return walk


def measured(*indices: int) -> Change:
    """Give the sketch's items at `indices` a quarter note each."""
    def change(p: dict[str, Any]) -> None:
        for index in indices:
            p["notes"][index]["duration"] = {"count": 1, "unit": 4}
    return payload(change)


Case = tuple[str, str, Change, bool]

FORM: list[Case] = [
    ("form-open", "the open form as admitted", keep, True),
    ("form-open", "an open vamp with no endings", delete_in(at("repeats", 0), "endings"), True),
    ("form-open", "an open repeat with two last-time endings",
     set_in(at("repeats", 0, "endings", 0), "passes", ["last"]), False),
    ("form-open", "an open repeat with no default ending",
     set_in(at("repeats", 0, "endings", 0), "passes", [1]), False),
    ("form-open", "a fixed repeat with a default and a last-time ending", set_in(at("repeats", 0), "times", 2), False),
    ("form-open", "a fixed repeat whose endings name its passes",
     both(set_in(at("repeats", 0), "times", 2), set_in(at("repeats", 0, "endings", 0), "passes", [1]),
          set_in(at("repeats", 0, "endings", 1), "passes", [2])), True),
    ("form-open", "an instruction longer than 200 characters", set_in(at("repeats", 0), "instruction", "x" * 201), False),
    ("form-spans-passes", "the passes as admitted", keep, True),
    ("form-spans-passes", "passes on a departure", set_in(at("spans", 5), "passes", [1]), False),
    ("form-spans-passes", "a span played on a return", set_in(at("spans", 6), "return", True), True),
    ("form-spans-passes", "a return that says false", set_in(at("spans", 6), "return", False), False),
    ("form-spans-passes", "a precision no one defined", set_in(at("alignment"), "precision", "close"), False),
    ("form-spans-passes", "a rough placement", set_in(at("alignment"), "precision", "rough"), True),
]

METER: list[Case] = [
    ("meter-open", "bars in the open form as admitted", keep, True),
    ("meter-open", "a section that goes back", set_in(at("layers", 0, "bars", 3), "section", 1), False),
    ("meter-open", "a grouping that does not add up", set_in(at("layers", 0, "bars", 0), "grouping", [3, 2]), False),
    ("meter-open", "two bars in one section", set_in(at("layers", 0, "bars", 2), "section", 1), True),
    # Written directions (ADR 0116, decision D).
    ("meter-open", "no directions", delete_in(at("layers", 0, "bars", 0), "directions"), True),
    ("meter-open", "an empty list of directions", set_in(at("layers", 0, "bars", 0), "directions", []), False),
    ("meter-open", "nine directions on a bar",
     set_in(at("layers", 0, "bars", 1), "directions", [{"text": f"cue {n}"} for n in range(9)]), False),
    ("meter-open", "a direction of 201 characters",
     set_in(at("layers", 0, "bars", 1), "directions", [{"text": "x" * 201}]), False),
    ("meter-open", "a tempo with a trailing zero", set_in(at("layers", 0, "bars", 0, "directions", 0, "tempo"),
                                                         "bpm", "160.0"), False),
    ("meter-open", "a tempo of 999.999", set_in(at("layers", 0, "bars", 0, "directions", 0, "tempo"),
                                               "bpm", "999.999"), True),
    ("meter-open", "a tempo counted in a beat no note value has",
     set_in(at("layers", 0, "bars", 0, "directions", 0, "tempo"), "beat", {"denominator": 3}), False),
    ("meter-open", "a direction field no one defined", set_in(at("layers", 0, "bars", 0, "directions", 1),
                                                             "lyric", True), False),
]

EXPORT: list[Case] = [
    ("export-ireal", "the iReal export as admitted", keep, True),
    ("export-ireal", "a format no version names", set_in(whole, "format", "irealpro"), False),
    ("export-ireal", "an empty list of losses", set_in(whole, "losses", []), False),
]

MELODY: list[Case] = [
    ("melody-sketch", "the sketch as admitted", keep, True),
    ("melody-sketch", "a rest of unknown length", delete_in(at("notes", 3), "duration"), False),
    ("melody-sketch", "a sketch that starts on a bar", set_in(whole, "start", {"layer": 0, "bar": 1}), False),
    ("melody-sketch", "a sketch laid in bars",
     both(pin("meter", "meter-open"), set_in(whole, "start", {"layer": 0, "bar": 1})), False),
    ("melody-sketch", "every pitch measured, in no bars", measured(0, 1, 4), True),
    ("melody-measured", "the measured tune as admitted", keep, True),
    ("melody-measured", "a pitch of unknown length laid in bars", delete_in(at("notes", 0), "duration"), False),
]

UNDERLAY: list[Case] = [
    ("underlay-sketch", "words under the sketch as admitted", keep, True),
    ("underlay-sketch", "two words on one note",
     set_in(at("lines", 0, "words", 1, "syllables", 0), "items", [0]), False),
    ("underlay-sketch", "syllables that overlap",
     set_in(at("lines", 0, "words", 1, "syllables", 1), "start_char", 1), False),
    ("underlay-sketch", "a word with items and syllables", set_in(at("lines", 0, "words", 1), "items", [4]), False),
    ("underlay-sketch", "a melisma", set_in(at("lines", 0, "words", 1, "syllables", 1), "items", [2, 4]), True),
    ("underlay-verse", "verse one under the first pass as admitted", keep, True),
    ("underlay-verse", "passes out of order", set_in(at("passes"), "passes", [2, 1]), False),
    ("underlay-verse", "the last pass before a number", set_in(at("passes"), "passes", ["last", 1]), False),
    ("underlay-verse", "the last pass alone", set_in(at("passes"), "passes", ["last"]), True),
    ("underlay-verse", "passes without a form", drop_role("form"), False),
    ("underlay-verse", "a form without passes", delete_in(whole, "passes"), False),
]

LEAD_SHEET: list[Case] = [
    ("lead-sheet-v2", "the version 2 lead sheet as admitted", keep, True),
    ("lead-sheet-v2", "an underlay without the lyrics", drop_role("lyrics"), False),
    ("lead-sheet-v2", "an underlay on a melody the sheet does not show", repin("underlay", "underlay-sketch"), False),
    ("lead-sheet-v2", "no underlay", drop_role("underlay"), True),
]

# Door admits each; World refuses it, by a check D-19 leaves to World.
WORLD_ONLY: list[tuple[str, str, Change]] = [
    ("lead-sheet-v2", "a sketch pinned as the melody (E.3)",
     both(repin("melody", "melody-sketch"), drop_role("underlay"))),
    ("underlay-sketch", "a syllable on a rest",
     set_in(at("lines", 0, "words", 1, "syllables", 1), "items", [3])),
    ("underlay-sketch", "an item past the melody's end",
     set_in(at("lines", 0, "words", 1, "syllables", 1), "items", [5])),
    ("underlay-verse", "passes of a repeat the form does not have", set_in(at("passes"), "repeat", 1)),
    ("form-spans-passes", "two passes for a section inside one repeat", set_in(at("spans", 1), "passes", [1, 1])),
]

ALL = FORM + METER + MELODY + UNDERLAY + LEAD_SHEET + EXPORT


class Agreement011(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.door, cls.archive = integration_binaries()
        cls.tmp = Path(tempfile.mkdtemp())
        cls.manifest, cls.export = build_fixture_archive(cls.tmp)
        cls.root = cls.tmp / "archive"
        cls.by_revision = {r["revision_id"]: r for r in cls.export["records"]}
        cls.environment = dict(os.environ)
        for variable, program in (("ARCHIVE_GIT_EXECUTABLE", "git"), ("ARCHIVE_GIT_ANNEX_EXECUTABLE", "git-annex")):
            cls.environment.setdefault(variable, os.path.realpath(shutil.which(program) or program))
        cls.count = 0

    @classmethod
    def tearDownClass(cls):
        remove_tree(cls.tmp)

    pinned = agreement.Agreement.pinned
    record = agreement.Agreement.record
    door_admits = agreement.Agreement.door_admits

    def view(self, revision: str) -> dict[str, Any]:
        found = self.by_revision[revision]
        return {"payload": found["payload"], "refs": found["refs"], "revision_id": revision}

    def one(self, record: dict[str, Any], role: str) -> dict[str, Any] | None:
        refs = [r for r in record["refs"] if r["role"] == role and r.get("revision_id") in self.by_revision]
        return self.view(refs[0]["revision_id"]) if refs else None

    def targets(self) -> dict[str, dict[str, Any]]:
        out = {}
        for r in self.export["records"]:
            target = {"id": r["object_id"], "shape": r["shape"], "payload": r["payload"], "refs": r["refs"],
                      "agent": r["agent"]}
            if r["shape"] == "entity":
                target["kind"] = r["payload"].get("kind")
            if r.get("contracts"):
                target["contract"] = (r["contracts"][0]["id"], r["contracts"][0]["version"])
                target["contracts"] = r["contracts"]
            out[r["revision_id"]] = target
        return out

    def world_admits(self, record: dict[str, Any]) -> tuple[bool, str]:
        cited = {(c["id"], c["version"]) for c in record.get("contracts", [])}
        claim = {"payload": record["payload"], "refs": record["refs"], "agent": record["agent"]}

        def run() -> None:
            if form.FORM_V2 in cited:
                form.check_form_v2(claim)
            elif form.FORM_SPANS_V2 in cited:
                form.check_form_spans_v2(claim, self.one(record, "stream"), self.one(record, "form"))
            elif form.METER_LAYERS_V3 in cited:
                form.check_meter_layers_v3(claim, self.one(record, "stream"), untimed_claim=self.one(record, "meter"),
                                           form_claim=self.one(record, "form"),
                                           sequence_claim=self.one(record, "sequence"))
            elif ("archive.claim/melody", "2") in cited:
                notes.check_melody(claim, self.one(record, "meter"), version="2")
            elif ("archive.claim/underlay", "1") in cited:
                melody = self.one(record, "melody")
                meter = self.one(self.by_revision[melody["revision_id"]], "meter") if melody else None
                notes.check_underlay(claim, melody, None, self.one(record, "form"), meter)
            elif exports.EXPORT_V2 in cited:
                exports.check_export(claim, self.targets(), "2")
            elif ("archive.claim/lead_sheet", "2") in cited:
                agents = {r["object_id"]: r["payload"].get("kind") for r in self.export["records"]
                          if r["shape"] == "entity"}
                lead_sheets.check_lead_sheet(claim, self.targets(), agents, version="2")
            else:
                raise AssertionError(f"no World check for {sorted(cited)}")

        try:
            run()
        except AssertionError:
            raise
        except Exception as exc:  # each module raises its own error class
            return False, f"{type(exc).__name__}: {exc}"
        return True, ""

    def agree(self, cases: list[Case]) -> None:
        for key, label, change, admitted in cases:
            with self.subTest(base=key, case=label):
                record = copy.deepcopy(self.record(key))
                change(record, self)
                door, door_reason = self.door_admits(record)
                self.assertEqual(door, admitted, f"Door: {door_reason}")
                world, world_reason = self.world_admits(record)
                self.assertEqual(world, admitted, f"World: {world_reason}")

    def test_form_and_form_spans_version_2(self):
        self.agree(FORM)

    def test_meter_layers_version_3(self):
        self.agree(METER)

    def test_melody_version_2(self):
        self.agree(MELODY)

    def test_the_underlay(self):
        self.agree(UNDERLAY)

    def test_lead_sheet_version_2(self):
        self.agree(LEAD_SHEET)

    def test_export_version_2(self):
        self.agree(EXPORT)

    def test_the_checks_d19_leaves_to_world(self):
        for key, label, change in WORLD_ONLY:
            with self.subTest(base=key, case=label):
                record = copy.deepcopy(self.record(key))
                change(record, self)
                door, door_reason = self.door_admits(record)
                self.assertTrue(door, f"Door: {door_reason}")
                world, world_reason = self.world_admits(record)
                self.assertFalse(world, "World admitted a record D-19 says World refuses")

    def test_every_case_list_has_both_verdicts(self):
        for name, cases in (("form", FORM), ("meter", METER), ("melody", MELODY), ("underlay", UNDERLAY),
                            ("lead sheet", LEAD_SHEET), ("export", EXPORT)):
            with self.subTest(cases=name):
                self.assertEqual({case[3] for case in cases}, {True, False})

    def test_every_new_contract_is_covered(self):
        def catalog(version: str) -> set[tuple[str, str]]:
            data = json.loads((ROOT / "spec" / version / "contracts.json").read_text())
            return {(c["id"], c["version"]) for c in data["contracts"]}
        added = catalog("0.0.11") - catalog("0.0.10")
        covered = {(c["id"], c["version"]) for key, *_ in ALL for c in self.record(key)["contracts"]}
        self.assertEqual(added, covered)


if __name__ == "__main__":
    unittest.main()
