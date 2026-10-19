"""Key regions, key sequences, and tuning (ADR 0080): the payload rules, the
spelling a key gives, the tuning grid, and the JAMS key_mode view.

Run from the repository root: python3 -m unittest world.tests.test_tonality.
Every example is synthetic (INV-23): the stream claim is the fixture
archive's own stream record from fixture/plan.json, lengthened to a minute.
"""

import copy
import unittest
from fractions import Fraction

from world.harmony import PITCH_NAMES, check_chord_stacks, stack_names
from world.tests.test_harmony import (
    DOCUMENT, RATE, RUN, SECOND, SEQUENCE, SOURCE, STREAM, WORK, chord_claim, fixture_stream, span,
)
from world.tonality import (
    KEY_REGIONS, KEY_SEQUENCE, MODES, TUNING, TonalityError, check_key_reading, check_key_regions,
    check_key_sequence, check_tuning, grid_cents_at, jams_keys, key_pitch_names, pitch_name_class,
    reference_hz, region_at, spell_name, spell_stack,
)

C, Db, D, Eb, E, F, Gb, G, Ab, A, Bb, B = range(12)
TUNING_REF = {"id": "urn:uuid:00000000-0000-4000-8000-0000000000c1", "revision_id": "sha256:" + "c1" * 32}
KEYSEQ = {"id": "urn:uuid:00000000-0000-4000-8000-0000000000c2", "revision_id": "sha256:" + "c2" * 32}
FORM = {"id": "urn:uuid:00000000-0000-4000-8000-0000000000c3", "revision_id": "sha256:" + "c3" * 32}
CHORDS = {"id": "urn:uuid:00000000-0000-4000-8000-0000000000c4", "revision_id": "sha256:" + "c4" * 32}
OTHER = {"id": "urn:uuid:00000000-0000-4000-8000-0000000000c5", "revision_id": "sha256:" + "c5" * 32}


def scale(tonic: int, mode: str) -> list[int]:
    return sorted((tonic + i) % 12 for i in MODES[mode][0])


def key(tonic: int | None, mode: str | None = None, collection: list[int] | None = None, **fields) -> dict:
    reading = {"collection": collection or scale(tonic, mode or "ionian"), **fields}
    if tonic is not None:
        reading["tonic"] = tonic
    if mode is not None:
        reading["mode"] = mode
    return reading


def ref(role: str, pin: dict) -> dict:
    return {"role": role, **pin}


def region(first_second: float, last_second: float, *readings: dict, **fields) -> dict:
    return {"start_sample": int(first_second * SECOND), "end_sample": int(last_second * SECOND),
            "readings": list(readings), **fields}


def regions_claim(regions: list[dict], *extra: dict, **payload) -> dict:
    return {"payload": {"sample_rate": RATE, "regions": regions, **payload},
            "refs": [ref("source", SOURCE), ref("stream", STREAM), *extra]}


def sequence_claim(regions: list[dict], *extra: dict, **payload) -> dict:
    return {"revision_id": KEYSEQ["revision_id"], "payload": {"regions": regions, **payload},
            "refs": [ref("work", WORK), *extra]}


def tuning_claim(**payload) -> dict:
    return {"revision_id": TUNING_REF["revision_id"],
            "payload": {"sample_rate": RATE, "reference_cents": "0", **payload},
            "refs": [ref("source", SOURCE), ref("stream", STREAM), ref("run", RUN)]}


def chord_sequence(entries: int) -> dict:
    return {"revision_id": CHORDS["revision_id"],
            "payload": {"entries": [{"readings": [{"text": "x"}]} for _ in range(entries)]},
            "refs": [ref("work", WORK)]}


class Contracts(unittest.TestCase):
    def test_names_and_versions(self):
        self.assertEqual(KEY_REGIONS, ("archive.claim/key_regions", "1"))
        self.assertEqual(KEY_SEQUENCE, ("archive.claim/key_sequence", "1"))
        self.assertEqual(TUNING, ("archive.claim/tuning", "1"))


class KeyReadings(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(check_key_reading(key(D, "dorian"), False), "key")
        self.assertEqual(check_key_reading({"no_key": True, "text": "atonal"}, False), "no_key")
        self.assertEqual(check_key_reading({"text": "Key: Bb"}, False), "text")
        # A collection alone: a machine sure of the notes but not the tonic.
        self.assertEqual(check_key_reading(key(None, collection=scale(Eb, "ionian")), False), "key")
        # A mode outside the checked vocabulary is the asserter's own text.
        self.assertEqual(check_key_reading(key(C, "blues", collection=[C, Eb, F, Gb, G, Bb]), False), "key")
        # A pentatonic collection with its tonic and no mode.
        self.assertEqual(check_key_reading(key(A, collection=[C, D, E, G, A]), False), "key")

    def test_people_say_minor_for_three_collections(self):
        for collection in ("aeolian", "harmonic minor", "melodic minor"):
            check_key_reading(key(A, "minor", collection=scale(A, collection)), False)
        with self.assertRaises(TonalityError):
            check_key_reading(key(A, "minor", collection=scale(A, "dorian")), False)
        check_key_reading(key(G, "major"), False)

    def test_spelling_is_the_asserters(self):
        # The spelling follows the collection's ascending order, not the tonic's.
        reading = key(Gb, "major", spelling=["Db", "Eb", "F", "Gb", "Ab", "Bb", "Cb"])
        self.assertEqual(reading["collection"], [Db, Eb, F, Gb, Ab, Bb, B])
        check_key_reading(reading, False)
        with self.assertRaises(TonalityError):
            check_key_reading(key(Gb, "major", spelling=["Gb", "Ab", "Bb", "Cb", "Db", "Eb", "F"]), False)

    def test_refused(self):
        bad = [
            ({"collection": []}, False),                                   # no classes
            ({"collection": [2, 0]}, False),                               # not ascending
            ({"collection": [0, 0, 4]}, False),                            # repeated
            ({"collection": [0, 12]}, False),                              # out of range
            ({"collection": [0, 4, 7], "tonic": 2}, False),             # tonic outside the collection
            ({"collection": [0, 2, 4, 5, 7, 9, 11], "mode": "ionian"}, False),  # mode without tonic
            (key(D, "ionian", collection=scale(D, "dorian")), False),      # mode disagrees with the notes
            (key(C, spelling=["C", "D"]), False),                          # spelling of another length
            (key(C, spelling=["C", "D", "E", "F", "G", "A", "B#"]), False),  # B# sounds C, not B
            ({"no_key": False}, False),
            ({"no_key": True, "collection": [0]}, False),
            ({"collection": [0], "weight": "0.5"}, False),                 # a weight with no scale
            ({"collection": [0]}, True),                                   # no weight under a scale
            ({"collection": [0], "weight": "high"}, True),
            ({"collection": [0], "certainty": "high"}, False),             # unknown field
            ({"collection": [0], "mode": "", "tonic": 0}, False),
            ("C major", False),
        ]
        for reading, weighted in bad:
            with self.subTest(reading=reading), self.assertRaises(TonalityError):
                check_key_reading(reading, weighted)

    def test_pitch_names(self):
        self.assertEqual([pitch_name_class(n) for n in ("C", "B#", "Cb", "F##", "Ebb")],
                         [0, 0, 11, 7, 2])
        for bad in ("H", "c", "E###", "Eb#", ""):
            with self.subTest(bad=bad), self.assertRaises(TonalityError):
                pitch_name_class(bad)


class KeyRegions(unittest.TestCase):
    def test_machine_regions_with_its_own_uncertainty(self):
        # Two candidates sharing notes where the tonic does not lead; then
        # the notes alone; a gap asserts nothing.
        claim = regions_claim([
            region(0, 20, key(F, "dorian", weight="0.02"), key(Eb, "ionian", weight="0.01")),
            region(20, 40, key(None, collection=scale(Ab, "ionian"), weight="0")),
            region(45, 60, key(C, "aeolian", weight="0.2")),
        ], ref("run", RUN), weight_scale="tonic margin of the tonal extractor, version 4")
        self.assertEqual(len(check_key_regions(claim, fixture_stream())), 3)
        self.assertIsNone(region_at(claim["payload"], 42 * SECOND))
        self.assertEqual(region_at(claim["payload"], 20 * SECOND)["readings"][0]["collection"], scale(Ab, "ionian"))

    def test_a_person_with_a_tuning_and_a_document(self):
        tuning = tuning_claim()
        claim = regions_claim([region(0, 30, key(G, "major", text="G"), locator={"bar": 1}),
                               region(30, 60, key(A, "major", text="A"), locator={"bar": 17})],
                              ref("document", DOCUMENT), ref("tuning", TUNING_REF),
                              alignment={"method": "manual"}, document_url="https://example.org/chart")
        check_key_regions(claim, fixture_stream(), tuning_claim=tuning)
        other = copy.deepcopy(tuning)
        other["refs"][0] = ref("source", OTHER)
        with self.assertRaises(TonalityError):
            check_key_regions(claim, fixture_stream(), tuning_claim=other)

    def test_placed_from_a_work_with_held_ends(self):
        sequence = sequence_claim([{"readings": [key(G, "major")]}, {"readings": [key(A, "major")]}])
        claim = regions_claim([region(0, 30, key(G, "major"), region=0, end="held"),
                               region(30, 60, key(A, "major"), region=1, end="held")],
                              ref("sequence", KEYSEQ), alignment={"method": "chord-alignment"},
                              hold_rule="until the next region's first chord")
        check_key_regions(claim, fixture_stream(), sequence_claim=sequence)
        out_of_range = copy.deepcopy(claim)
        out_of_range["payload"]["regions"][1]["region"] = 2
        with self.assertRaises(TonalityError):
            check_key_regions(out_of_range, fixture_stream(), sequence_claim=sequence)

    def test_corrections_and_parts(self):
        claim = regions_claim([region(10, 20, key(E, "minor"))], ref("corrects", OTHER), ref("derived_from", OTHER),
                              part="guitar")
        check_key_regions(claim, fixture_stream())

    def test_refused(self):
        good = regions_claim([region(0, 10, key(C, "major"))])
        check_key_regions(good, fixture_stream())
        cases = {
            "overlap": regions_claim([region(0, 10, key(C)), region(5, 20, key(G))]),
            "past the stream": regions_claim([region(50, 61, key(C))]),
            "empty": regions_claim([]),
            "no readings": regions_claim([region(0, 10)]),
            "repeated reading": regions_claim([region(0, 10, key(C), key(C))]),
            "rate": {**good, "payload": {**good["payload"], "sample_rate": RATE + 1}},
            "region without sequence": regions_claim([region(0, 10, key(C), region=0)]),
            "held without rule": regions_claim([region(0, 10, key(C), end="held")]),
            "rule without held": regions_claim([region(0, 10, key(C))], hold_rule="x"),
            "bad end": regions_claim([region(0, 10, key(C), end="soft")]),
            "locator without document": regions_claim([region(0, 10, key(C), locator={"bar": 1})]),
            "beat locator": regions_claim([region(0, 10, key(C), locator={"bar": 1, "beat": 2})],
                                          ref("document", DOCUMENT), alignment={"method": "manual"}),
            "document without alignment": regions_claim([region(0, 10, key(C))], ref("document", DOCUMENT)),
            "alignment without document": regions_claim([region(0, 10, key(C))], alignment={"method": "manual"}),
            "two runs": regions_claim([region(0, 10, key(C))], ref("run", RUN), ref("run", RUN)),
            "analysed is not a role": regions_claim([region(0, 10, key(C))], ref("analysed", OTHER)),
            "unknown field": regions_claim([region(0, 10, key(C))], tonic=0),
            "weight without scale": regions_claim([region(0, 10, key(C, weight="1"))]),
            "unpinned": {"payload": good["payload"], "refs": [ref("source", SOURCE),
                                                              {"role": "stream", "id": STREAM["id"]}]},
        }
        for name, claim in cases.items():
            with self.subTest(name), self.assertRaises(TonalityError):
                check_key_regions(claim, fixture_stream())

    def test_stream_of_another_source_refused(self):
        stream = fixture_stream()
        stream["refs"][0] = ref("artifact", OTHER)
        with self.assertRaises(TonalityError):
            check_key_regions(regions_claim([region(0, 10, key(C))]), stream)


class KeySequences(unittest.TestCase):
    def test_a_works_one_key(self):
        regions = check_key_sequence(sequence_claim([{"readings": [key(Bb, "major", text="Bb")]}]))
        self.assertEqual(len(regions), 1)

    def test_over_chord_sequence_entries(self):
        claim = sequence_claim([{"readings": [key(G, "major")], "first": 0, "last": 15, "section": "Verse"},
                                {"readings": [key(B, "minor")], "first": 16, "last": 23, "section": "Bridge"},
                                {"readings": [key(G, "major")], "first": 28, "last": 31}],
                               ref("chord_sequence", CHORDS))
        check_key_sequence(claim, chord_sequence_claim=chord_sequence(32))
        with self.assertRaises(TonalityError):
            check_key_sequence(claim, chord_sequence_claim=chord_sequence(30))

    def test_over_form_sections(self):
        claim = sequence_claim([{"readings": [key(D, "mixolydian")], "first": 0, "last": 2},
                                {"readings": [key(E, "mixolydian")], "first": 3, "last": 3}], ref("form", FORM))
        check_key_sequence(claim, section_count=4)
        with self.assertRaises(TonalityError):
            check_key_sequence(claim, section_count=3)

    def test_from_a_document_alone(self):
        claim = {"payload": {"regions": [{"readings": [{"text": "Key: F#m"}], "locator": {"line": 1}}],
                             "document_url": "https://example.org/chart.txt"},
                 "refs": [ref("document", DOCUMENT), ref("run", RUN)]}
        check_key_sequence(claim)

    def test_refused(self):
        anchored = [ref("chord_sequence", CHORDS)]
        cases = {
            "no subject": {"payload": {"regions": [{"readings": [key(C)]}]}, "refs": []},
            "two anchors": sequence_claim([{"readings": [key(C)], "first": 0, "last": 1}],
                                          ref("chord_sequence", CHORDS), ref("form", FORM)),
            "anchored without range": sequence_claim([{"readings": [key(C)]}], *anchored),
            "range without anchor": sequence_claim([{"readings": [key(C)], "first": 0, "last": 1}]),
            "overlap": sequence_claim([{"readings": [key(C)], "first": 0, "last": 4},
                                       {"readings": [key(G)], "first": 4, "last": 6}], *anchored),
            "backwards": sequence_claim([{"readings": [key(C)], "first": 5, "last": 4}], *anchored),
            "locator without document": sequence_claim([{"readings": [key(C)], "locator": {"line": 1}}]),
            "locator out of order": {"payload": {"regions": [
                {"readings": [key(C)], "locator": {"line": 3}}, {"readings": [key(G)], "locator": {"line": 2}}]},
                "refs": [ref("document", DOCUMENT)]},
            "word locator": {"payload": {"regions": [{"readings": [key(C)], "locator": {"line": 1, "word": 2}}]},
                             "refs": [ref("document", DOCUMENT)]},
            "alignment": sequence_claim([{"readings": [key(C)]}], alignment={"method": "manual"}),
            "samples": sequence_claim([{"readings": [key(C)], "start_sample": 0}]),
            "url without document": sequence_claim([{"readings": [key(C)]}], document_url="https://example.org/"),
            "source is not a role": sequence_claim([{"readings": [key(C)]}], ref("source", SOURCE)),
            "long section": sequence_claim([{"readings": [key(C)], "section": "x" * 101}]),
        }
        for name, claim in cases.items():
            with self.subTest(name), self.assertRaises(TonalityError):
                check_key_sequence(claim)
        with self.assertRaises(TonalityError):
            check_key_sequence(sequence_claim([{"readings": [key(C)], "first": 0, "last": 0}], *anchored),
                               chord_sequence_claim={**chord_sequence(4), "revision_id": SEQUENCE["revision_id"]})


class Tuning(unittest.TestCase):
    def test_reference_and_drift(self):
        claim = tuning_claim(reference_cents="-31.77", confidence_scale="tuning confidence of the tonal extractor",
                             confidence="0.8",
                             drift=[{"start_sample": 0, "end_sample": 30 * SECOND, "cents": "-28", "confidence": "0.7"},
                                    {"start_sample": 30 * SECOND, "end_sample": 60 * SECOND, "cents": "-36",
                                     "confidence": "0.9"}])
        check_tuning(claim, fixture_stream())
        self.assertEqual(grid_cents_at(claim["payload"], 31 * SECOND), Fraction(-36))
        self.assertEqual(grid_cents_at(tuning_claim(reference_cents="12.5")["payload"], 0), Fraction(25, 2))
        self.assertAlmostEqual(reference_hz("-31.77"), 432.0, places=2)
        self.assertEqual(reference_hz("0"), 440.0)

    def test_a_document_states_it(self):
        claim = tuning_claim(reference_cents="-31.77", document_url="https://example.org/notes")
        claim["refs"] = [ref("source", SOURCE), ref("stream", STREAM), ref("document", DOCUMENT)]
        check_tuning(claim, fixture_stream())

    def test_refused(self):
        def drift(*spans):
            return [{"start_sample": a * SECOND, "end_sample": b * SECOND, "cents": c} for a, b, c in spans]
        cases = {
            "fifty": tuning_claim(reference_cents="50"),
            "below": tuning_claim(reference_cents="-50.01"),
            "a semitone is not a tuning": tuning_claim(reference_cents="-100"),
            "float": {**tuning_claim(), "payload": {"sample_rate": RATE, "reference_cents": -10.0}},
            "text": tuning_claim(reference_cents="+10"),
            "confidence without scale": tuning_claim(confidence="1"),
            "scale without confidence": tuning_claim(confidence_scale="x"),
            "span confidence without scale": tuning_claim(drift=[{"start_sample": 0, "end_sample": 10,
                                                                  "cents": "1", "confidence": "1"}]),
            "drift overlaps": tuning_claim(drift=drift((0, 10, "1"), (5, 20, "2"))),
            "drift past the stream": tuning_claim(drift=drift((50, 61, "1"))),
            "drift out of range": tuning_claim(drift=drift((0, 10, "75"))),
            "empty drift": tuning_claim(drift=[]),
            "url without document": tuning_claim(document_url="https://example.org/"),
            "a tuning has no regions": tuning_claim(regions=[]),
            "rate": tuning_claim(sample_rate=RATE * 2),
        }
        for name, claim in cases.items():
            with self.subTest(name), self.assertRaises(TonalityError):
                check_tuning(claim, fixture_stream())


class Spelling(unittest.TestCase):
    def test_no_key_keeps_the_default(self):
        self.assertEqual(key_pitch_names(None), PITCH_NAMES)
        self.assertEqual(key_pitch_names({"no_key": True}), PITCH_NAMES)
        self.assertEqual(key_pitch_names({"text": "Key: Bb"}), PITCH_NAMES)

    def test_keys_spell_their_notes(self):
        self.assertEqual([key_pitch_names(key(E, "major"))[pc] for pc in scale(E, "ionian")],
                         ["C#", "D#", "E", "F#", "G#", "A", "B"])
        self.assertEqual([key_pitch_names(key(F, "dorian"))[pc] for pc in scale(F, "dorian")],
                         ["C", "D", "Eb", "F", "G", "Ab", "Bb"])
        # Six sharps or six flats: flats, unless the asserter spelled it.
        self.assertEqual(key_pitch_names(key(Gb, "major"))[Gb], "Gb")
        sharp = key(Gb, "major", spelling=["C#", "D#", "E#", "F#", "G#", "A#", "B"])
        self.assertEqual(key_pitch_names(sharp)[Gb], "F#")
        self.assertEqual(key_pitch_names(sharp)[F], "E#")
        # Seven sharps loses to five flats.
        self.assertEqual(key_pitch_names(key(Db, "major"))[Db], "Db")
        # The leading tone of a harmonic minor key.
        self.assertEqual(key_pitch_names(key(G, "harmonic minor"))[Gb], "F#")
        # A collection with no tonic is spelled from its lowest class.
        self.assertEqual(key_pitch_names(key(None, collection=scale(A, "ionian")))[Ab], "G#")

    def test_chromatic_notes_follow_the_keys_side(self):
        self.assertEqual(key_pitch_names(key(F, "major"))[Gb], "Gb")
        self.assertEqual(key_pitch_names(key(D, "major"))[Ab], "G#")
        self.assertEqual(key_pitch_names(key(F, "major"))[B], "B")

    def test_open_question_example(self):
        # The G in Gm(maj13) has an F#, not a Gb; and D# where a sharp key puts it.
        reading = {"bass": D, "tones": [D, E, Gb, G, A, Bb], "root": G}
        check_chord_stacks(chord_claim([span(0, 2, reading)]), fixture_stream())
        spelled = spell_stack(D, reading["tones"], G, key(G, "harmonic minor"))
        self.assertEqual(spelled, {"root": "G", "bass": "D", "tones": ["D", "E", "F#", "G", "A", "Bb"]})
        name = stack_names(Eb, [G, Bb], Eb)[0]
        self.assertEqual(name.symbol, "Eb")
        self.assertEqual(spell_name(name, Eb, key(B, "major")).symbol, "D#")
        self.assertEqual(spell_name(name, Eb, key(Bb, "major")).symbol, "Eb")
        self.assertEqual(spell_name(name, Eb, None), name)

    def test_slash_bass_is_spelled_by_degree(self):
        name = stack_names(Gb, [D, A], D)[0]
        self.assertEqual(name.symbol, "D/F#")
        self.assertEqual(spell_name(name, Gb, key(Gb, "major")).symbol, "D/F#")
        cm7 = [n for n in stack_names(G, [C, Eb, G, Bb]) if n.root == C][0]
        self.assertEqual(spell_name(cm7, G, key(C, "minor", collection=scale(C, "aeolian"))).symbol, "Cm7/G")
        self.assertEqual(spell_name(cm7, G, key(C, "minor", collection=scale(C, "aeolian"))).harte, "C:min7/5")
        fs = stack_names(Gb, [Bb, Db], Gb)[0]
        self.assertEqual(spell_name(fs, Gb, key(E, "major")).harte, "F#:maj")
        self.assertEqual(spell_name(fs, Gb, key(Db, "major")).harte, "Gb:maj")


class Jams(unittest.TestCase):
    def test_key_mode(self):
        payload = regions_claim([
            region(0, 10, key(F, "dorian", weight="0.9")),
            region(10, 20, key(None, collection=scale(Ab, "ionian"), weight="0.5")),
            region(20, 30, key(G, "harmonic minor", weight="0.4")),
            region(30, 40, {"no_key": True, "weight": "0.3"}),
            region(40, 50, key(C, "blues", collection=[C, Eb, F, Gb, G, Bb], weight="0.2")),
            region(50, 60, key(Gb, "major", spelling=["C#", "D#", "E#", "F#", "G#", "A#", "B"], weight="1")),
        ], weight_scale="probability")["payload"]
        annotation = jams_keys(payload)
        self.assertEqual(annotation["namespace"], "key_mode")
        self.assertEqual([d["value"] for d in annotation["data"]], ["F:dorian", "G:minor", "N", "C", "F#:major"])
        self.assertEqual(annotation["data"][0]["confidence"], 0.9)
        self.assertEqual(annotation["sandbox"]["archive_dropped_regions"], [1])
        self.assertIn("harmonic and melodic minor, exported as minor", annotation["sandbox"]["archive_lossy"])
        self.assertTrue(annotation["annotation_metadata"]["annotation_rules"].startswith("ADR 0080"))

    def test_rank_beyond_a_regions_readings_is_dropped(self):
        payload = regions_claim([region(0, 10, key(C, "major"), key(A, "minor")), region(10, 20, key(G))])["payload"]
        annotation = jams_keys(payload, rank=1)
        self.assertEqual([d["value"] for d in annotation["data"]], ["A:minor"])
        self.assertEqual(annotation["sandbox"]["archive_dropped_regions"], [1])
        self.assertIsNone(annotation["data"][0]["confidence"])


if __name__ == "__main__":
    unittest.main()
