"""The named views of 0.0.10's records, the key parameter of the chord
views, and `world view`, answered from a real projection of World's drafted
0.0.10 records (world/tests/plan0010.py), read back from its records graph."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fixture.build import object_id
from world import record_views, view_records, views
from world.tests.plan0010 import ROOT, drafted_export
from world.world import build_projection, rebuild_store


def request(name, subject, **parameters):
    return {"view": "archive.view/" + name, "version": "1", "subject": subject, "parameters": parameters}


class RecordViews(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.export = drafted_export()
        cls.projection = build_projection(cls.export)
        cls.archive = cls.export["archive_id"]
        cls.operator = next(ref["id"] for r in cls.export["records"] for ref in r["refs"] if ref["role"] == "appoints")
        cls.guest = object_id(cls.archive, "archive.core/entity", "fixture-guest")

    def record(self, namespace, name):
        found = [r for r in self.export["records"] if r["object_id"] == object_id(self.archive, namespace, name)]
        self.assertEqual(len(found), 1, name)
        return found[0]

    def pin(self, namespace, name):
        found = self.record(namespace, name)
        return {"id": found["object_id"], "revision_id": found["revision_id"]}

    def answer(self, body, caller=None, readable=None):
        return view_records.answer(self.projection, body, caller=caller or self.operator, readable=readable)

    def test_the_record_views_join_the_one_registry(self):
        self.assertTrue(set(record_views.VIEWS) <= set(views.VIEWS))
        self.assertEqual(set(record_views.VIEWS), set(record_views.HANDLERS))
        self.assertEqual(set(views.VIEWS), set(views.HANDLERS))
        listed = {(v["view"], v["version"]) for v in views.catalogue()}
        self.assertEqual(listed, set(views.VIEWS))

    def test_an_answer_states_its_snapshot_projection_and_withheld(self):
        chords = self.pin("archive.claim/chord_stacks", "world-fixture-chords-v2")
        result = self.answer(request("standing", {"claim": chords}, at=100))
        self.assertEqual((result["snapshot"], result["projection"], result["withheld"]),
                         (self.export["snapshot"], 6, 0))
        self.assertEqual(result["parameters"], {"judge": self.operator, "at": 100, "reading": None})
        kept = {chords["revision_id"]}
        narrowed = self.answer(request("standing", {"claim": chords}, at=100), readable=kept)
        self.assertEqual(narrowed["withheld"], len(self.export["records"]) - 1)
        # Nothing judging it is readable, so nothing stands.
        self.assertIsNone(narrowed["answer"]["verdict"])

    def test_standing_reads_one_judge_at_one_sample(self):
        chords = self.pin("archive.claim/chord_stacks", "world-fixture-chords-v2")
        judgment = self.record("archive.assessment/judgment", "world-fixture-judgment-v2")
        inside = self.answer(request("standing", {"claim": chords}, at=100))["answer"]
        self.assertEqual((inside["verdict"], inside["judgments"]), ("affirmed", [judgment["revision_id"]]))
        self.assertIsNone(self.answer(request("standing", {"claim": chords}, at=3000))["answer"]["verdict"])
        self.assertIsNone(self.answer(request("standing", {"claim": chords}, at=100, judge=self.guest))["answer"]["verdict"])

    def test_reach_names_the_claims_that_limit_it(self):
        take = object_id(self.archive, "archive.core/artifact", "fixture-take-a")
        sensitivity = self.record("archive.claim/sensitivity", "world-fixture-sensitivity")
        answer = self.answer(request("reach", {"object": take}, on="2026-10-19"))["answer"]
        self.assertEqual(answer["labelled"], "circle")
        self.assertTrue(any(sensitivity["revision_id"] in why for why in answer["why"]))
        recording = object_id(self.archive, "archive.core/artifact", "fixture-recording")
        licence = self.record("archive.claim/rights", "world-w-rights-licence")
        answer = self.answer(request("reach", {"object": recording}, on="2026-10-19"))["answer"]
        self.assertEqual(answer["rights"], "circle")
        self.assertTrue(any(licence["revision_id"] in why for why in answer["why"]))

    def test_the_readable_set_follows_the_approval_and_its_pins(self):
        sheet = self.record("archive.claim/lead_sheet", "world-fixture-lead-sheet")
        sequence = self.record("archive.claim/chord_sequence", "world-fixture-sequence-v2")
        circle = {"destination": "world-circle", "members": [self.guest]}
        answer = self.answer(request("readable_set", {"circle": circle}, reader=self.guest, on="2026-10-19"))["answer"]
        self.assertIn(sheet["revision_id"], answer["revisions"])
        self.assertIn(sequence["revision_id"], answer["revisions"])
        self.assertEqual(answer["why"][sequence["revision_id"]], f"pinned by {sheet['revision_id']}")
        own = {r["revision_id"] for r in self.export["records"] if r["agent"] == self.guest}
        self.assertTrue(own)
        self.assertEqual(set(answer["readable"]), set(answer["revisions"]) | own)
        refused = request("readable_set", {"circle": {"destination": "world-circle", "members": [self.operator]}})
        with self.assertRaises(views.ViewError) as raised:
            self.answer(refused)
        self.assertEqual(raised.exception.code, "view_request_invalid")

    def test_lead_sheet_changes_lists_judgments_of_its_pins(self):
        sheet = self.pin("archive.claim/lead_sheet", "world-fixture-lead-sheet")
        sequence = self.record("archive.claim/chord_sequence", "world-fixture-sequence-v2")
        changes = self.answer(request("lead_sheet_changes", {"lead_sheet": sheet}))["answer"]["changes"]
        verdicts = {(c["role"], c["pinned"], c["kind"], c["verdict"]) for c in changes}
        self.assertIn(("chords", sequence["revision_id"], "judged", "rejected"), verdicts)
        self.assertIn(("chords", sequence["revision_id"], "judged", "affirmed"), verdicts)

    def test_played_sections_unroll_repeats_and_the_jump(self):
        form = self.pin("archive.claim/form", "world-w-form-segno")
        played = self.answer(request("played_sections", {"form": form}))["answer"]["played"]
        # A, B twice; the bridge; back to the segno (A), repeats again, to fine (B).
        self.assertEqual([p["section"] for p in played], [0, 1, 2, 1, 2, 3, 1, 2, 1, 2])
        self.assertEqual(played[3], {"section": 1, "label": "A", "kind": "verse"})

    def test_played_bars_follow_the_pinned_form(self):
        meter = self.pin("archive.claim/meter_layers", "world-fixture-meter-written")
        form = self.pin("archive.claim/form", "world-fixture-form")
        bars = self.answer(request("played_bars", {"meter": meter}))["answer"]
        sections = self.answer(request("played_sections", {"form": form}))["answer"]["played"]
        self.assertEqual(bars["form"], form["revision_id"])
        # Each written bar is in its own section here, so the bars play as the sections do.
        self.assertEqual([b["section"] for b in bars["played"]], [s["section"] for s in sections])
        with self.assertRaises(views.ViewError):
            self.answer(request("played_bars", {"meter": meter}, layer=3))

    def test_the_tune_takes_the_selected_notes(self):
        spans = self.pin("archive.claim/melody_spans", "world-fixture-melody-spans")
        notes = self.record("archive.claim/notes", "world-fixture-notes")
        answer = self.answer(request("tune", {"melody_spans": spans}))["answer"]
        self.assertEqual([(n["onset_sample"], n["from"]) for n in answer["notes"]],
                         [(0, notes["revision_id"]), (1000, notes["revision_id"])])
        self.assertEqual(answer["handoffs"], [])

    def test_track_credits_say_why(self):
        recording = object_id(self.archive, "archive.core/entity", "fixture-recording")
        release_credit = self.record("archive.claim/contribution", "fixture-contribution-release")
        credits = self.answer(request("track_credits", {"recording": recording}))["answer"]["credits"]
        by_claim = {c["claim"]: c for c in credits}
        self.assertEqual(by_claim[release_credit["revision_id"]]["via"], "release")
        self.assertIn("tracklist", by_claim[release_credit["revision_id"]]["why"])

    def test_a_subject_world_does_not_hold_or_of_another_kind(self):
        missing = {"id": self.guest, "revision_id": "sha256:" + "0" * 64}
        form = self.pin("archive.claim/form", "world-fixture-form")
        for body, code in ((request("tune", {"melody_spans": missing}), "view_subject_not_found"),
                           (request("tune", {"melody_spans": form}), "view_request_invalid"),
                           (request("track_credits", {"recording": "urn:uuid:00000000-0000-4000-8000-00000000f00d"}),
                            "view_subject_not_found"),
                           (request("reach", {"object": self.guest}, on="19 October"), "view_request_invalid")):
            with self.subTest(view=body["view"]), self.assertRaises(views.ViewError) as raised:
                self.answer(body)
            self.assertEqual(raised.exception.code, code)


class KeySpelling(unittest.TestCase):
    """ADR 0080's spelling served by the chord views (D11.4)."""

    F_MAJOR = {"bass": 5, "tones": [0, 9]}
    C_MAJOR_KEY = {"collection": [0, 2, 4, 5, 7, 9, 11], "tonic": 0, "mode": "major"}

    def names(self, **parameters):
        answer = views.run_view(request("chord_names", {"readings": [self.F_MAJOR]}, **parameters), [])
        return answer["answer"], answer["parameters"]

    def test_the_default_is_unchanged_and_repeated(self):
        answer, parameters = self.names()
        self.assertEqual(parameters, {"transpose": 0, "key": None})
        self.assertEqual(answer["spelling"], "default")
        self.assertEqual(answer["names"][0]["resolutions"][0]["names"][0]["symbol"], "F")

    def test_a_transposed_key_spells_the_moved_chord(self):
        # F up a semitone is F#/Gb: C major moved up is Db major, which names it Gb.
        answer, _ = self.names(transpose=1, key=self.C_MAJOR_KEY)
        self.assertEqual(answer["spelling"], "key")
        self.assertEqual(answer["names"][0]["resolutions"][0]["names"][0]["symbol"], "Gb")
        default, _ = self.names(transpose=1)
        self.assertEqual(default["names"][0]["resolutions"][0]["names"][0]["symbol"], "F#")

    def test_a_key_without_a_collection_spells_by_default(self):
        answer, _ = self.names(key={"no_key": True})
        self.assertEqual(answer["spelling"], "default")

    def test_a_malformed_key_is_refused(self):
        with self.assertRaises(views.ViewError) as raised:
            self.names(key={"collection": [0, 4], "tonic": 2})
        self.assertEqual(raised.exception.code, "view_request_invalid")


class CurrentKey(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.export = drafted_export()
        cls.projection = build_projection(cls.export)
        archive = cls.export["archive_id"]
        cls.operator = next(ref["id"] for r in cls.export["records"] for ref in r["refs"] if ref["role"] == "appoints")
        recording = [r for r in cls.export["records"]
                     if r["object_id"] == object_id(archive, "archive.core/artifact", "fixture-recording")][0]
        cls.source = {"id": recording["object_id"], "revision_id": recording["revision_id"]}
        regions = [r for r in cls.export["records"]
                   if r["object_id"] == object_id(archive, "archive.claim/key_regions", "world-fixture-key-regions")][0]
        cls.regions = {"id": regions["object_id"], "revision_id": regions["revision_id"]}

        # The machine's chords alone: a person with no claims, and its method first.
        cls.guest = object_id(archive, "archive.core/entity", "fixture-guest")
        by_revision = {r["revision_id"]: r for r in cls.export["records"]}
        chords = [r for r in cls.export["records"]
                  if r["object_id"] == object_id(archive, "archive.claim/chord_stacks", "fixture-chords")][0]
        [run] = [by_revision[ref["revision_id"]] for ref in chords["refs"] if ref["role"] == "run"]
        cls.method = run["payload"]["method"]["id"]

    def current(self, **parameters):
        body = request("chords_current", {"source": self.source}, person=self.guest, machine_order=[self.method],
                       **parameters)
        return view_records.answer(self.projection, body, caller=self.operator)

    def test_a_key_claim_spells_each_stretch_by_its_region(self):
        result = self.current(key={"claim": self.regions})
        self.assertEqual(result["parameters"]["key"], {"claim": self.regions})
        spelled = [s for s in result["answer"]["segments"] if "stack" in s]
        self.assertTrue(spelled)
        # Region 0 (0 to 2000) is C major; region 1 asserts no key.
        self.assertEqual([(s["start_sample"], s["end_sample"], s["spelling"]) for s in spelled],
                         [(0, 1000, "key"), (3000, 4000, "default")])

    def test_a_stretch_is_cut_where_the_key_may_change(self):
        d_flat = {"collection": [0, 1, 3, 5, 6, 8, 10], "tonic": 1, "mode": "major"}
        key_at = lambda sample: d_flat if sample < 2000 else None  # noqa: E731
        stretch = {"start_sample": 0, "end_sample": 4000, "chosen": {"claim": "c"},
                   "stack": {"bass": 6, "tones": [1, 10]}}
        cut = views._spell_segments([stretch], key_at, [0, 2000, 4000], 0)
        self.assertEqual([(s["start_sample"], s["end_sample"], s["spelling"]) for s in cut],
                         [(0, 2000, "key"), (2000, 4000, "default")])
        self.assertEqual([s["names"][0]["names"][0]["symbol"] for s in cut], ["Gb", "F#"])
        # C major moved up a semitone is Db major, which names the class Gb;
        # in C major itself it is F#.
        c_major = lambda sample: KeySpelling.C_MAJOR_KEY  # noqa: E731
        self.assertEqual(views._spell_segments([stretch], c_major, [], 0)[0]["names"][0]["names"][0]["symbol"], "F#")
        self.assertEqual(views._spell_segments([stretch], c_major, [], 1)[0]["names"][0]["names"][0]["symbol"], "Gb")
        self.assertNotIn("spelling", json.dumps(self.current()["answer"]))

    def test_a_key_claim_about_another_recording_is_refused(self):
        with self.assertRaises(views.ViewError):
            self.current(key={"claim": {"id": self.source["id"], "revision_id": self.source["revision_id"]}})


class Command(unittest.TestCase):
    def test_world_view_lists_and_answers(self):
        export = drafted_export()
        operator = next(ref["id"] for r in export["records"] for ref in r["refs"] if ref["role"] == "appoints")
        form = [r for r in export["records"]
                if r["object_id"] == object_id(export["archive_id"], "archive.claim/form", "world-w-form-segno")][0]
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "archive").mkdir()
            (tmp / "export.json").write_text(json.dumps(export))
            rebuild_store(tmp / "archive", tmp / "store", export_file=tmp / "export.json")
            world = [sys.executable, str(ROOT / "world/world.py"), "view"]
            listed = subprocess.run(world + ["--list"], capture_output=True, text=True)
            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertEqual({(v["view"], v["version"]) for v in json.loads(listed.stdout)["views"]}, set(views.VIEWS))
            (tmp / "request.json").write_text(json.dumps(request(
                "played_sections", {"form": {"id": form["object_id"], "revision_id": form["revision_id"]}})))
            answered = subprocess.run(world + ["--store", str(tmp / "store"), "--request-file", str(tmp / "request.json"),
                                               "--caller", operator], capture_output=True, text=True)
            self.assertEqual(answered.returncode, 0, answered.stderr)
            result = json.loads(answered.stdout)["result"]
            self.assertEqual(len(result["answer"]["played"]), 10)
            (tmp / "unknown.json").write_text(json.dumps(request("tempo", {})))
            refused = subprocess.run(world + ["--store", str(tmp / "store"), "--request-file", str(tmp / "unknown.json")],
                                     capture_output=True, text=True)
            self.assertEqual(refused.returncode, 4)
            self.assertEqual(json.loads(refused.stderr)["code"], "view_unknown")


if __name__ == "__main__":
    unittest.main()
