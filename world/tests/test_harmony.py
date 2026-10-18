"""Chord stacks and meter layers (ADR 0077): the payload rules, the computed
names, and the JAMS views.

Run from the repository root: python3 -m unittest world.tests.test_harmony.
The module needs only the standard library. Every example is synthetic
(INV-23): the stream claim is the fixture archive's own stream record from
fixture/plan.json, lengthened so that bars fit.
"""

import json
import re
import unittest
from itertools import combinations
from fractions import Fraction
from pathlib import Path

from world.harmony import (
    HarmonyError,
    bar_tempi,
    beat_positions,
    check_chord_stacks,
    check_meter_layers,
    jams_chords,
    jams_document,
    jams_meter,
    reading_names,
    stack_names,
)

ROOT = Path(__file__).resolve().parents[2]
SOURCE = {"id": "urn:uuid:00000000-0000-4000-8000-0000000000b1", "revision_id": "sha256:" + "b1" * 32}
STREAM = {"id": "urn:uuid:00000000-0000-4000-8000-0000000000b2", "revision_id": "sha256:" + "b2" * 32}
RUN = {"id": "urn:uuid:00000000-0000-4000-8000-0000000000b3", "revision_id": "sha256:" + "b3" * 32}
DOCUMENT = {"id": "urn:uuid:00000000-0000-4000-8000-0000000000b4", "revision_id": "sha256:" + "b4" * 32}
RATE = 8000
SECOND = RATE

# Pitch classes, C = 0.
C, Db, D, Eb, E, F, Gb, G, Ab, A, Bb, B = range(12)


def fixture_stream() -> dict:
    """The fixture archive's audio stream claim, about SOURCE, one minute long."""
    plan = json.loads((ROOT / "fixture/plan.json").read_text())
    records = [r for step in plan["steps"] for r in step.get("records", [])]
    record = next(r for r in records if r.get("namespace") == "archive.claim/audio_stream")
    payload = dict(record["payload"])
    assert payload["sample_rate"] == RATE, "the fixture stream's rate changed; update RATE"
    payload["sample_count"] = 60 * SECOND
    return {"revision_id": STREAM["revision_id"], "payload": payload,
            "refs": [{"role": "artifact", **SOURCE}, {"role": "run", **RUN}]}


def refs(*extra: tuple[str, dict]) -> list[dict]:
    return [{"role": "source", **SOURCE}, {"role": "stream", **STREAM}] + [{"role": r, **v} for r, v in extra]


def chord_claim(spans: list[dict], **payload) -> dict:
    return {"payload": {"sample_rate": RATE, "spans": spans, **payload}, "refs": refs(("run", RUN))}


def span(start: float, end: float, *readings: dict) -> dict:
    return {"start_sample": int(start * SECOND), "end_sample": int(end * SECOND), "readings": list(readings)}


def bar(start: int, end: int, numerator: int, denominator: int, **fields) -> dict:
    return {"start_sample": start, "end_sample": end, "numerator": numerator, "denominator": denominator, **fields}


def meter_claim(layers: list[dict], **payload) -> dict:
    return {"payload": {"sample_rate": RATE, "layers": layers, **payload}, "refs": refs(("run", RUN))}


class ChordStackNames(unittest.TestCase):
    def test_g_minor_major_thirteen_over_d(self):
        # The source wrote "Gm(maj13)/D": G Bb D F# A E over a D bass.
        reading = {"bass": D, "tones": [D, E, Gb, G, A, Bb], "root": G, "text": "Gm(maj13)/D"}
        check_chord_stacks(chord_claim([span(0, 2, reading)]), fixture_stream())
        names = stack_names(reading["bass"], reading["tones"], reading["root"])
        self.assertEqual(len(names), 1)
        self.assertEqual(names[0].harte, "G:minmaj7(9,13)/5")
        self.assertEqual(names[0].symbol, "Gm(maj13)/D")
        self.assertTrue(names[0].asserted_root)
        # The verbatim text is kept as the source wrote it, beside the stack.
        self.assertEqual(reading["text"], "Gm(maj13)/D")

    def test_same_notes_have_both_names(self):
        # Cm7/G and Eb6/G: one stack, two valid names, neither chosen.
        self.assertEqual(stack_names(G, [C, Eb, G, Bb]), stack_names(G, [Eb, G, Bb, C]))
        names = stack_names(G, [C, Eb, G, Bb])
        by_root = {n.root: n for n in names}
        self.assertNotEqual(by_root[C].root, by_root[Eb].root)
        self.assertEqual((by_root[C].harte, by_root[C].symbol), ("C:min7/5", "Cm7/G"))
        self.assertEqual((by_root[Eb].harte, by_root[Eb].symbol), ("Eb:maj6/3", "Eb6/G"))
        self.assertFalse(any(n.asserted_root for n in names))
        # A reader that asserts a root gets only that root's names.
        self.assertEqual([n.symbol for n in stack_names(G, [C, Eb, G, Bb], root=Eb)], ["Eb6/G"])

    def test_quartal_stack_with_no_root(self):
        # C F Bb Eb stacked in fourths, no root asserted.
        reading = {"bass": C, "tones": [Eb, F, Bb], "voicing": [48, 53, 58, 63]}
        check_chord_stacks(chord_claim([span(0, 1, reading)]), fixture_stream())
        names = stack_names(C, [Eb, F, Bb])
        self.assertEqual({n.root for n in names}, {C, Eb, F, Bb})
        self.assertEqual((names[0].harte, names[0].symbol), ("F:sus4(b7)/5", "F7sus4/C"))
        rooted_on_bass = next(n for n in names if n.root == C)
        self.assertEqual(rooted_on_bass.harte, "C:min7(*5,11)")
        self.assertIsNone(rooted_on_bass.symbol)

    def test_cluster_has_no_symbol_but_every_harte_name(self):
        reading = {"bass": C, "tones": [Db, D, Eb]}
        check_chord_stacks(chord_claim([span(0, 1, reading)]), fixture_stream())
        names = stack_names(C, [Db, D, Eb])
        self.assertTrue(all(n.symbol is None for n in names))
        self.assertEqual(names[0].harte, "C:(b2,2,b3)")
        self.assertEqual(len(names), 4)

    def test_uncertain_seventh(self):
        # "A seventh is present; which one is uncertain."
        reading = {"bass": C, "tones": [E, G], "root": C,
                   "uncertain": [{"pitch_classes": [Bb, B], "present": "exactly_one"}]}
        check_chord_stacks(chord_claim([span(0, 1, reading)]), fixture_stream())
        resolved = {tones: names[0].symbol for tones, names in reading_names(reading)}
        self.assertEqual(resolved, {frozenset({E, G, Bb}): "C7", frozenset({E, G, B}): "Cmaj7"})
        maybe = dict(reading, uncertain=[{"pitch_classes": [Bb], "present": "maybe"}])
        self.assertEqual([names[0].symbol for _, names in reading_names(maybe)], ["C", "C7"])

    def test_rootless_voicing_names_an_unsounded_root(self):
        names = stack_names(E, [G, Bb, D], root=C)
        self.assertEqual(names[0].harte, "C:9(*1)/3")
        self.assertEqual(names[0].symbol, "C9(no root)/E")


# The value pattern of JAMS 0.3's `chord_harte` namespace (Harte 2005).
HARTE_DEGREE = r"\*?(b*|#*)([1-9]|1[0-3]?)"
JAMS_CHORD_HARTE = re.compile(
    r"^((N)|(([A-G](b*|#*))((:(maj|min|dim|aug|maj7|min7|7|dim7|hdim7|minmaj7|maj6|min6|9|maj9|min9|sus4)"
    rf"(\(({HARTE_DEGREE})(,{HARTE_DEGREE})*\))?)|(:\(({HARTE_DEGREE})(,{HARTE_DEGREE})*\)))?"
    r"((/((b*|#*)([1-9]|1[0-3]?)))?)?))$")


class HarteStrings(unittest.TestCase):
    def test_every_name_of_every_small_stack_is_strict_harte(self):
        checked = 0
        for size in range(0, 5):
            for tones in combinations(range(12), size):
                for name in stack_names(C, tones) + stack_names(C, tones, root=D):
                    self.assertRegex(name.harte, JAMS_CHORD_HARTE)
                    checked += 1
        self.assertGreater(checked, 1000)


class ChordStackRules(unittest.TestCase):
    def refused(self, claim, message):
        with self.assertRaisesRegex(HarmonyError, message):
            check_chord_stacks(claim, fixture_stream())

    def test_weighted_competing_readings_and_no_chord(self):
        claim = chord_claim([
            span(0, 1, {"bass": G, "tones": [C, Eb, G, Bb], "weight": "0.6"},
                 {"bass": G, "tones": [C, Eb, G, Bb], "root": Eb, "weight": "0.4"}),
            span(1, 2, {"no_chord": True, "text": "N.C.", "weight": "1"}),
            span(3, 4, {"text": "H7alt?", "weight": "0.2"}),
        ], weight_scale="probability", part="keyboard")
        self.assertEqual(len(check_chord_stacks(claim, fixture_stream())), 3)
        claim["payload"]["part"] = ""
        with self.assertRaisesRegex(HarmonyError, "part must be text"):
            check_chord_stacks(claim, fixture_stream())

    def test_refusals(self):
        good = {"bass": C, "tones": [E, G]}
        self.refused(chord_claim([span(0, 1, {"bass": C, "tones": [G, E]})]), "ascending")
        self.refused(chord_claim([span(0, 1, {"bass": C, "tones": [E, E, G]})]), "distinct")
        self.refused(chord_claim([span(0, 2, good), span(1, 3, good)]), "overlaps")
        self.refused(chord_claim([span(59, 61, good)]), "past the stream")
        self.refused(chord_claim([span(0, 1, good, dict(good))]), "repeats a reading")
        self.refused(chord_claim([span(0, 1, dict(good, weight="0.5"))]), "weight_scale")
        self.refused(chord_claim([span(0, 1, good)], weight_scale="probability"), "weight_scale")
        self.refused(chord_claim([span(0, 1, dict(good, uncertain=[{"pitch_classes": [E, F], "present": "exactly_one"}]))]),
                     "already certain")
        self.refused(chord_claim([span(0, 1, dict(good, uncertain=[{"pitch_classes": [Bb], "present": "exactly_one"}]))]),
                     "2 to 12")
        self.refused(chord_claim([span(0, 1, dict(good, voicing=[52, 55, 60]))]), "lowest note is not the bass")
        self.refused(chord_claim([span(0, 1, dict(good, voicing=[48, 55, 64, 70]))]), "upper notes")
        self.refused(chord_claim([span(0, 1, dict(good, bass=True))]), "integer")
        self.refused(chord_claim([span(0, 1, {"no_chord": False})]), "must be true")

    def test_source_fields_need_a_document(self):
        located = dict(span(0, 1, {"bass": C, "tones": [E, G], "text": "C"}), locator={"bar": 17, "beat": 1})
        self.refused(chord_claim([located]), "locator but the claim pins no document")
        claim = chord_claim([located], alignment={"method": "manual"}, document_url="https://example.org/chart")
        self.refused(claim, "alignment exactly when it pins a document")
        claim["refs"].append({"role": "document", **DOCUMENT})
        check_chord_stacks(claim, fixture_stream())
        del claim["payload"]["alignment"]
        self.refused(claim, "alignment exactly when it pins a document")

    def test_stream_must_measure_the_source(self):
        stream = fixture_stream()
        stream["refs"][0]["revision_id"] = "sha256:" + "ee" * 32
        with self.assertRaisesRegex(HarmonyError, "another Artifact revision"):
            check_chord_stacks(chord_claim([span(0, 1, {"bass": C, "tones": []})]), stream)
        claim = chord_claim([span(0, 1, {"bass": C, "tones": []})])
        claim["payload"]["sample_rate"] = 44100
        self.refused(claim, "differs from the stream")


class MeterLayers(unittest.TestCase):
    def refused(self, claim, message):
        with self.assertRaisesRegex(HarmonyError, message):
            check_meter_layers(claim, fixture_stream())

    def test_four_four_then_twelve_eight(self):
        # Two bars of 4/4 at 120 quarter notes a minute (2 s each), then a
        # bar of 12/8 felt in four dotted quarters, also 2 s long.
        bars = [
            bar(0, 2 * SECOND, 4, 4, beats=[0, 4000, 8000, 12000]),
            bar(2 * SECOND, 4 * SECOND, 4, 4),
            bar(4 * SECOND, 6 * SECOND, 12, 8, grouping=[3, 3, 3, 3], beats=[32000, 36000, 40000, 44000]),
        ]
        claim = meter_claim([{"bars": bars}])
        check_meter_layers(claim, fixture_stream())
        self.assertEqual(bar_tempi({"bars": bars}, RATE), [120, 120, 180])
        beat_annotation, tempo = jams_meter(claim["payload"])
        positions = [(o["value"]["measure"], o["value"]["position"], o["value"]["num_beats"], o["value"]["beat_units"])
                     for o in beat_annotation["data"]]
        self.assertEqual(positions[:4], [(1, 1, 4, 4), (1, 2, 4, 4), (1, 3, 4, 4), (1, 4, 4, 4)])
        self.assertEqual(positions[4], (2, 1, 4, 4))  # a bar without beat times: its downbeat only
        self.assertEqual(positions[5:], [(3, 1, 12, 8), (3, 4, 12, 8), (3, 7, 12, 8), (3, 10, 12, 8)])
        self.assertIn("bars without beat times", beat_annotation["sandbox"]["archive_lossy"])
        self.assertEqual([o["value"] for o in tempo["data"]], [120.0, 120.0, 180.0])

    def test_seven_eight_grouped_two_two_three(self):
        seven = bar(0, 7000, 7, 8, grouping=[2, 2, 3], beats=[0, 2000, 4000])
        check_meter_layers(meter_claim([{"bars": [seven]}]), fixture_stream())
        self.assertEqual(beat_positions(seven), [1, 3, 5])
        self.refused(meter_claim([{"bars": [dict(seven, grouping=[2, 2, 2])]}]), "add up to its numerator")
        self.refused(meter_claim([{"bars": [dict(seven, beats=[0, 2000])]}]), "3 to 3")
        self.refused(meter_claim([{"bars": [dict(seven, beats=[1, 2000, 4000])]}]), "downbeat")
        self.refused(meter_claim([{"bars": [dict(seven, beats=[0, 4000, 2000])]}]), "rise")
        self.refused(meter_claim([{"bars": [dict(seven, denominator=6)]}]), "denominator")

    def test_metric_modulation_dotted_quarter_equals_quarter(self):
        # A 6/8 bar of two dotted quarters, then 2/4 in which the new quarter
        # lasts as long as the old dotted quarter: the pulse ratio is 3:2.
        bars = [bar(0, 2 * SECOND, 6, 8, grouping=[3, 3], beats=[0, SECOND]),
                bar(2 * SECOND, 4 * SECOND, 2, 4, beats=[2 * SECOND, 3 * SECOND])]
        link = {"layer": 0, "bar": 1, "before": {"denominator": 4, "dots": 1}, "after": {"denominator": 4},
                "ratio": [3, 2]}
        claim = meter_claim([{"bars": bars}], modulations=[link])
        check_meter_layers(claim, fixture_stream())
        # The derived tempo shows the same modulation: 90 to 60 quarters a minute.
        tempi = bar_tempi({"bars": bars}, RATE)
        self.assertEqual(tempi, [90, 60])
        self.assertEqual(tempi[0] / tempi[1], Fraction(3, 2))
        self.refused(meter_claim([{"bars": bars}], modulations=[dict(link, ratio=[2, 3])]), "ratio of its note values")
        self.refused(meter_claim([{"bars": bars}], modulations=[dict(link, ratio=[6, 4])]), "lowest terms")
        self.refused(meter_claim([{"bars": bars}], modulations=[dict(link, bar=0)]), "bar must be")
        apart = [bars[0], dict(bars[1], start_sample=2 * SECOND + 1, beats=[2 * SECOND + 1, 3 * SECOND])]
        self.refused(meter_claim([{"bars": apart}], modulations=[link]), "not adjacent")
        self.assertIn("metric modulations", jams_meter(claim["payload"])[0]["sandbox"]["archive_lossy"])

    def test_two_simultaneous_layers_three_against_two(self):
        # The same two seconds counted in two by one player and in three by
        # another: two layers of one claim, neither primary.
        two = {"label": "in two", "bars": [bar(0, 2 * SECOND, 2, 4, beats=[0, SECOND])]}
        three = {"label": "in three", "bars": [bar(0, 2 * SECOND, 3, 4, beats=[0, 5333, 10667])]}
        claim = meter_claim([two, three])
        check_meter_layers(claim, fixture_stream())
        self.assertEqual(bar_tempi(three, RATE)[0] / bar_tempi(two, RATE)[0], Fraction(3, 2))
        first, = [a for a in jams_meter(claim["payload"], layer=1) if a["namespace"] == "beat_position"]
        self.assertEqual([o["value"]["position"] for o in first["data"]], [1, 2, 3])
        self.assertIn("other simultaneous layers", first["sandbox"]["archive_lossy"])
        self.refused(meter_claim([two] * 9), "1 to 8")

    def test_overlapping_bars_in_one_layer_are_refused(self):
        self.refused(meter_claim([{"bars": [bar(0, 4000, 2, 4), bar(3000, 8000, 2, 4)]}]), "overlaps")


class JamsChords(unittest.TestCase):
    def test_export_is_lossy_and_says_so(self):
        payload = chord_claim([
            span(0, 2, {"bass": D, "tones": [D, E, Gb, G, A, Bb], "root": G, "text": "Gm(maj13)/D",
                        "weight": "0.9"},
                 {"bass": D, "tones": [D, E, Gb, G, A, Bb], "weight": "0.1"}),
            span(2, 3, {"bass": C, "tones": [E, G], "root": C, "weight": "0.5",
                        "uncertain": [{"pitch_classes": [Bb, B], "present": "exactly_one"}]}),
            span(3, 4, {"no_chord": True, "weight": "1"}),
            span(4, 5, {"text": "?", "weight": "1"}),
        ], weight_scale="probability")
        check_chord_stacks(payload, fixture_stream())
        strict = jams_chords(payload["payload"], provenance={"agent": "pkg:generic/example@1",
                                                             "revision_id": "sha256:" + "aa" * 32})
        self.assertEqual([o["value"] for o in strict["data"]], ["G:minmaj7(9,13)/5", "C:maj", "N"])
        self.assertEqual([o["confidence"] for o in strict["data"]], [0.9, 0.5, 1.0])
        self.assertEqual(strict["data"][0]["time"], 0.0)
        self.assertEqual(strict["data"][1]["duration"], 1.0)
        self.assertEqual(strict["annotation_metadata"]["data_source"], "sha256:" + "aa" * 32)
        for loss in ("alternative readings", "uncertain tones", "the source's own chord text"):
            self.assertIn(loss, strict["sandbox"]["archive_lossy"])
        loose = jams_chords(payload["payload"], namespace="chord")
        self.assertEqual(loose["data"][-1]["value"], "X")
        second = jams_chords(payload["payload"], rank=1)
        self.assertEqual([o["value"] for o in second["data"]], ["G:minmaj7(9,13)/5"])
        document = jams_document(60.0, [strict])
        self.assertEqual(document["file_metadata"]["title"], "")
        json.dumps(document)

    def test_weights_on_another_scale_are_not_confidences(self):
        payload = chord_claim([span(0, 1, {"bass": C, "tones": [E, G], "weight": "3"})], weight_scale="votes")
        check_chord_stacks(payload, fixture_stream())
        exported = jams_chords(payload["payload"])
        self.assertIsNone(exported["data"][0]["confidence"])
        self.assertIn("weights on the scale 'votes'", exported["sandbox"]["archive_lossy"])


if __name__ == "__main__":
    unittest.main()
