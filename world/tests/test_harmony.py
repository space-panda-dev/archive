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
    beat_tempi,
    check_chord_sequence,
    check_chord_stacks,
    check_meter_layers,
    jams_chords,
    jams_document,
    jams_meter,
    reading_names,
    sequence_order,
    span_end_held,
    stack_names,
    tempo_ratio,
)

ROOT = Path(__file__).resolve().parents[2]
SOURCE = {"id": "urn:uuid:00000000-0000-4000-8000-0000000000b1", "revision_id": "sha256:" + "b1" * 32}
STREAM = {"id": "urn:uuid:00000000-0000-4000-8000-0000000000b2", "revision_id": "sha256:" + "b2" * 32}
RUN = {"id": "urn:uuid:00000000-0000-4000-8000-0000000000b3", "revision_id": "sha256:" + "b3" * 32}
WORK = {"id": "urn:uuid:00000000-0000-4000-8000-0000000000b5", "revision_id": "sha256:" + "b5" * 32}
SEQUENCE = {"id": "urn:uuid:00000000-0000-4000-8000-0000000000b6", "revision_id": "sha256:" + "b6" * 32}
MACHINE = {"id": "urn:uuid:00000000-0000-4000-8000-0000000000b7", "revision_id": "sha256:" + "b7" * 32}
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

    def test_raised_fifth_over_an_omitted_fifth_is_not_a_flat_thirteenth(self):
        # Found in an album's chart import: "Cmaj7#5" (C E G# B) was named
        # C:maj7(*5,b13), and "G+7" over its own root G:7(*5,b13). A b13
        # needs the fifth it sits above; with the fifth absent the raised
        # tone is the fifth, so the names are augmented ones.
        major = stack_names(C, [E, Ab, B], root=C)[0]
        self.assertEqual((major.harte, major.symbol), ("C:aug(7)", "Cmaj7#5"))
        dominant = stack_names(G, [Eb, F, B], root=G)[0]
        self.assertEqual((dominant.harte, dominant.symbol), ("G:aug(b7)", "G+7"))
        # With the fifth sounding, the same added tone is still a b13.
        self.assertEqual(stack_names(G, [B, D, Eb, F], root=G)[0].harte, "G:7(b13)")
        # A raised eleventh over an omitted fifth stays an eleventh, as the
        # symbol says.
        eleventh = stack_names(C, [E, Gb, B], root=C)[0]
        self.assertEqual((eleventh.harte, eleventh.symbol), ("C:maj7(*5,#11)", "Cmaj7#11(no5)"))

    def test_a_single_note_is_not_a_power_chord_without_its_fifth(self):
        # Found in a transcription's bass part: one sounding note was given
        # the symbol "Eb5(no5)". Harte writes a lone note as Eb:(1), and the
        # symbol follows it; a doubled note is still one pitch class.
        for reading in ({"bass": Eb, "tones": []}, {"bass": Eb, "tones": [Eb], "voicing": [39, 51]}):
            check_chord_stacks(chord_claim([span(0, 1, reading)]), fixture_stream())
            names = stack_names(reading["bass"], reading["tones"])
            self.assertEqual([(n.harte, n.symbol) for n in names], [("Eb:(1)", "Eb(1)")])
        # Two pitch classes a fifth apart are still a power chord.
        self.assertEqual(stack_names(Eb, [Bb])[0].symbol, "Eb5")

    def test_six_nine_over_a_bass_has_one_slash(self):
        # Found in an album's chart import: a 6/9 chord over its sixth printed
        # "Bb6/9/G", which reads as two slash basses.
        name = stack_names(G, [Bb, C, D, F], root=Bb)[0]
        self.assertEqual((name.harte, name.symbol), ("Bb:maj6(9)/6", "Bb6(9)/G"))
        self.assertEqual(stack_names(C, [D, Eb, G, A], root=C)[0].symbol, "Cm6(9)")
        for size in range(3, 7):
            for tones in combinations(range(12), size):
                for n in stack_names(C, tones):
                    if n.symbol is not None:
                        self.assertLessEqual(n.symbol.count("/"), 1, n.symbol)

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
        self.refused(claim, "alignment exactly when it places a document")
        claim["refs"].append({"role": "document", **DOCUMENT})
        check_chord_stacks(claim, fixture_stream())
        del claim["payload"]["alignment"]
        self.refused(claim, "alignment exactly when it places a document")

    def test_held_ends(self):
        # A transcription's chord names give each chord a start and no end;
        # its importer held each name until the next one, so those ends are
        # the importer's, not the document's.
        rule = "a name holds until the next name, at most to the end of the following bar"
        named = [dict(span(0, 2, {"bass": C, "tones": [E, G], "text": "C"}), end="held"),
                 dict(span(2, 3, {"bass": F, "tones": [C, A], "text": "F"}), end="asserted"),
                 span(3, 4, {"bass": G, "tones": [D, B], "text": "G"})]
        claim = chord_claim(named, hold_rule=rule)
        spans = check_chord_stacks(claim, fixture_stream())
        self.assertEqual([span_end_held(s) for s in spans], [True, False, False])
        # The sample end stays, so spans still order and tile.
        self.assertEqual(spans[0]["end_sample"], spans[1]["start_sample"])
        self.refused(chord_claim(named), "hold_rule exactly when")
        self.refused(chord_claim([named[1], named[2]], hold_rule=rule), "hold_rule exactly when")
        self.refused(chord_claim(named, hold_rule=""), "hold_rule must be text")
        self.refused(chord_claim([dict(named[0], end="inferred")], hold_rule=rule), "end must be one of")
        self.refused(chord_claim([dict(named[0], end=None)], hold_rule=rule), "end must be one of")
        # The export keeps the rule and which observations were held.
        exported = jams_chords(claim["payload"])
        self.assertIn(rule, exported["annotation_metadata"]["annotation_rules"])
        self.assertEqual(exported["sandbox"]["archive_held_ends"], [0])
        self.assertTrue(any("held span ends" in loss for loss in exported["sandbox"]["archive_lossy"]))
        self.assertNotIn("archive_held_ends", jams_chords(chord_claim(named[1:])["payload"])["sandbox"])

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
        # lasts as long as the old dotted quarter: a tempo equation, 3:2.
        bars = [bar(0, 2 * SECOND, 6, 8, grouping=[3, 3], beats=[0, SECOND]),
                bar(2 * SECOND, 4 * SECOND, 2, 4, beats=[2 * SECOND, 3 * SECOND])]
        link = {"layer": 0, "bar": 1, "kind": "equation",
                "before": {"denominator": 4, "dots": 1}, "after": {"denominator": 4}}
        claim = meter_claim([{"bars": bars}], modulations=[link])
        check_meter_layers(claim, fixture_stream())
        # The ratio is derived from the note values, never stored, and the
        # bars' derived tempi agree with it: 90 to 60 quarters a minute.
        tempi = bar_tempi({"bars": bars}, RATE)
        self.assertEqual(tempi, [90, 60])
        self.assertEqual(tempo_ratio(link), Fraction(3, 2))
        self.assertEqual(tempi[0] / tempi[1], tempo_ratio(link))
        self.refused(meter_claim([{"bars": bars}], modulations=[dict(link, ratio=[3, 2])]), "unknown \\['ratio'\\]")
        self.refused(meter_claim([{"bars": bars}], modulations=[{k: v for k, v in link.items() if k != "kind"}]),
                     "kind must be one of")
        self.refused(meter_claim([{"bars": bars}], modulations=[dict(link, kind="ratio")]), "kind must be one of")
        self.refused(meter_claim([{"bars": bars}], modulations=[dict(link, after={"denominator": 3})]), "denominator")
        self.refused(meter_claim([{"bars": bars}], modulations=[dict(link, bar=0)]), "bar must be")
        apart = [bars[0], dict(bars[1], start_sample=2 * SECOND + 1, beats=[2 * SECOND + 1, 3 * SECOND])]
        self.refused(meter_claim([{"bars": apart}], modulations=[link]), "not adjacent")
        self.assertIn("metric modulations and regroupings", jams_meter(claim["payload"])[0]["sandbox"]["archive_lossy"])

    def test_regrouping_keeps_the_eighth_and_moves_the_pulse(self):
        # Found in an album's chart: 4/4 bars, one bar of 12/8 felt in four
        # dotted quarters, then 4/4 again, with the eighth unchanged. Its
        # importer could only write "eighth = eighth, ratio 1:1", which says
        # nothing changed. The pulse moved, from the quarter to the dotted
        # quarter and back, and the tempo did not.
        bars = [bar(0, 2 * SECOND, 4, 4, beats=[0, 4000, 8000, 12000]),
                bar(2 * SECOND, 5 * SECOND, 12, 8, grouping=[3, 3, 3, 3], beats=[16000, 22000, 28000, 34000]),
                bar(5 * SECOND, 7 * SECOND, 4, 4)]
        quarter, dotted = {"denominator": 4}, {"denominator": 4, "dots": 1}
        links = [{"layer": 0, "bar": 1, "kind": "regrouping", "pulse_before": quarter, "pulse_after": dotted},
                 {"layer": 0, "bar": 2, "kind": "regrouping", "pulse_before": dotted, "pulse_after": quarter}]
        claim = meter_claim([{"bars": bars}], modulations=links)
        check_meter_layers(claim, fixture_stream())
        self.assertEqual([tempo_ratio(link) for link in links], [1, 1])
        self.assertEqual(set(bar_tempi({"bars": bars}, RATE)), {120})
        self.refused(meter_claim([{"bars": bars}], modulations=[dict(links[0], pulse_after=quarter)]),
                     "pulse does not move")
        self.refused(meter_claim([{"bars": bars}], modulations=[dict(links[0], before=quarter)]), "unknown")
        self.refused(meter_claim([{"bars": bars}], modulations=[{k: v for k, v in links[0].items()
                                                                 if k != "pulse_before"}]), "missing")
        self.refused(meter_claim([{"bars": bars}], modulations=[dict(links[0], pulse_before={"denominator": 4,
                                                                                            "dots": 3})]), "dots")
        self.refused(meter_claim([{"bars": bars}], modulations=[links[1], links[0]]), "in order")

    def test_felt_layer_over_a_written_one(self):
        # A triplet feel written in 4/4: the written layer counts 4/4, and a
        # felt layer counts 12/8 over the same samples.
        written = {"role": "written", "bars": [bar(0, 2 * SECOND, 4, 4)]}
        felt = {"role": "felt", "label": "triplet feel",
                "bars": [bar(0, 2 * SECOND, 12, 8, grouping=[3, 3, 3, 3])]}
        claim = meter_claim([written, felt])
        check_meter_layers(claim, fixture_stream())
        self.assertIn("the layer's role (felt)", jams_meter(claim["payload"], layer=1)[0]["sandbox"]["archive_lossy"])
        self.refused(meter_claim([felt]), "no written layer")
        self.refused(meter_claim([dict(written, role="notated"), felt]), "role must be one of")
        self.refused(meter_claim([dict(written, role=None)]), "role must be one of")

    def test_beat_only_layer(self):
        # Found in an album's machine claims: a beat tracker gives beats and no
        # meter, and its claim had to write each inter-beat interval as a 1/4
        # bar. A beat-only layer asserts the beats and nothing else.
        beats = [800, 4800, 8800, 12800, 16800, 20800]
        tracked = {"label": "beat tracker", "beats": beats}
        claim = meter_claim([tracked])
        check_meter_layers(claim, fixture_stream())
        self.assertEqual(beat_tempi(tracked, RATE), [120] * 5)  # beats, not quarters, per minute
        beat, tempo = jams_meter(claim["payload"])
        self.assertEqual(beat["namespace"], "beat")
        self.assertEqual({o["value"] for o in beat["data"]}, {None})
        self.assertIn("beats (the beat's note value is unstated)", " ".join(tempo["sandbox"]["archive_lossy"]))
        # A tracker that finds downbeats marks them; a stated unit converts.
        marked = dict(tracked, downbeats=[800, 16800], beat_unit={"denominator": 8})
        check_meter_layers(meter_claim([marked]), fixture_stream())
        self.assertEqual(beat_tempi(marked, RATE), [60] * 5)
        self.assertEqual([o["value"] for o in jams_meter(meter_claim([marked])["payload"])[0]["data"]],
                         [1, None, None, None, 1, None])
        # Refusals: a layer of both or neither, beats out of order or past the
        # stream, downbeats that are not beats, a bad unit, and bar fields.
        self.refused(meter_claim([dict(tracked, bars=[bar(0, 4000, 1, 4)])]), "exactly one of bars or beats")
        self.refused(meter_claim([{"label": "empty"}]), "exactly one of bars or beats")
        self.refused(meter_claim([dict(tracked, beats=[4800, 800])]), "ascending")
        self.refused(meter_claim([dict(tracked, beats=[800, 800])]), "ascending")
        self.refused(meter_claim([dict(tracked, beats=[800, 60 * SECOND])]), "beats\\[1\\] must be an integer")
        self.refused(meter_claim([dict(tracked, beats=[])]), "1 to 100000")
        self.refused(meter_claim([dict(tracked, downbeats=[801])]), "among its beats")
        self.refused(meter_claim([dict(tracked, downbeats=[16800, 800])]), "ascending")
        self.refused(meter_claim([dict(tracked, beat_unit={"denominator": 5})]), "denominator")
        self.refused(meter_claim([dict(tracked, numerator=4)]), "unknown \\['numerator'\\]")
        # A modulation joins bars, so it cannot point at a beat-only layer.
        link = {"layer": 0, "bar": 1, "kind": "equation", "before": {"denominator": 4}, "after": {"denominator": 4}}
        self.refused(meter_claim([tracked], modulations=[link]), "only beats")

    def test_a_claim_without_the_amended_fields_still_checks(self):
        # The shape the beat tracker's claim was written in before beat-only
        # layers: each inter-beat interval as a 1/4 bar. It asserts a meter
        # the tracker never stated, but it is still a valid version 1 claim.
        placeholder = {"label": "beat grid", "bars": [bar(800, 4800, 1, 4), bar(4800, 8800, 1, 4)]}
        check_meter_layers(meter_claim([placeholder]), fixture_stream())
        check_chord_stacks(chord_claim([span(0, 1, {"bass": C, "tones": [E, G]})]), fixture_stream())

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


def symbol(text: str, bass: int, tones: list[int], root: int) -> dict:
    return {"bass": bass, "tones": sorted(tones), "root": root, "text": text}


def chords_over_lyrics() -> dict:
    """The shape of a real chords-over-lyrics chart, with generic chords and
    no words: an intro line of two chords marked "x2", then a verse whose
    chords sit over lyric lines at the columns of the words they fall on,
    and a chorus. Line numbers are the document's own; the lyric lines they
    point at are not in the claim."""
    Dm7, G7, Cmaj7 = symbol("Dm7", D, [F, A, C], D), symbol("G7", G, [D, F, B], G), symbol("Cmaj7", C, [E, G, B], C)
    Am7, Fmaj7 = symbol("Am7", A, [C, E, G], A), symbol("Fmaj7", F, [C, E, A], F)
    entries = [
        {"section": "Intro", "locator": {"line": 3, "column": 1}, "readings": [Dm7]},
        {"section": "Intro", "locator": {"line": 3, "column": 6}, "readings": [G7]},
        {"section": "Verse", "locator": {"line": 6, "column": 1}, "readings": [Cmaj7]},
        {"section": "Verse", "locator": {"line": 6, "column": 14}, "readings": [Am7]},
        {"section": "Verse", "locator": {"line": 8, "column": 4}, "readings": [Fmaj7]},
        {"section": "Verse", "locator": {"line": 8, "column": 20}, "readings": [G7, symbol("G7sus4", G, [C, D, F], G)]},
        {"section": "Chorus", "locator": {"line": 11, "word": 1}, "readings": [Fmaj7]},
        {"section": "Chorus", "locator": {"line": 11, "word": 5}, "readings": [{"no_chord": True, "text": "N.C."}]},
    ]
    return {"agent": "person", "payload": {"entries": entries, "repeats": [{"first": 0, "last": 1, "times": 2}],
                                           "document_url": "https://example.org/chart"},
            "refs": [{"role": "work", **WORK}, {"role": "document", **DOCUMENT}]}


class ChordSequences(unittest.TestCase):
    def refused(self, claim, message):
        with self.assertRaisesRegex(HarmonyError, message):
            check_chord_sequence(claim)

    def test_chords_over_lyrics_have_an_order_and_no_times(self):
        claim = chords_over_lyrics()
        entries = check_chord_sequence(claim)
        self.assertEqual(len(entries), 8)
        self.assertEqual(sequence_order(claim["payload"]), [0, 1, 0, 1, 2, 3, 4, 5, 6, 7])
        # The person's spelling stays as written, beside the stack.
        self.assertEqual(entries[5]["readings"][1]["text"], "G7sus4")
        # A Work alone is a subject too: "the bridge goes to Fmaj7#11".
        said = {"payload": {"entries": [{"section": "Bridge", "readings": [
            symbol("Fmaj7#11", F, [C, E, A, B], F)]}]}, "refs": [{"role": "work", **WORK}]}
        check_chord_sequence(said)

    def test_cue_sheet_repeats_nest(self):
        # "intro: Gm7 C9 x4", inside a section played twice.
        Gm7, C9 = symbol("Gm7", G, [D, F, Bb], G), symbol("C9", C, [D, E, G, Bb], C)
        claim = {"payload": {"entries": [{"section": "Intro", "readings": [Gm7]},
                                         {"section": "Intro", "readings": [C9]},
                                         {"section": "Intro", "readings": [{"text": "turnaround"}]}],
                             "repeats": [{"first": 0, "last": 2, "times": 2}, {"first": 0, "last": 1, "times": 4}]},
                 "refs": [{"role": "work", **WORK}]}
        check_chord_sequence(claim)
        self.assertEqual(sequence_order(claim["payload"]), [0, 1] * 4 + [2] + [0, 1] * 4 + [2])

    def test_malformed_sequences_are_refused(self):
        good = chords_over_lyrics()

        def changed(**payload):
            claim = chords_over_lyrics()
            claim["payload"].update(payload)
            return claim

        entries = good["payload"]["entries"]
        no_subject = chords_over_lyrics()
        no_subject["refs"] = []
        self.refused(no_subject, "work or a document")
        stream_subject = chords_over_lyrics()
        stream_subject["refs"].append({"role": "stream", **STREAM})
        self.refused(stream_subject, "unknown role 'stream'")
        self.refused(changed(sample_rate=RATE), "unknown \\['sample_rate'\\]")
        self.refused(changed(alignment={"method": "manual"}), "unknown \\['alignment'\\]")
        self.refused(changed(entries=[]), "1 to 10000")
        self.refused(changed(entries=[dict(entries[0], start_sample=0)]), "unknown \\['start_sample'\\]")
        self.refused(changed(entries=[dict(entries[0], lyric="words")]), "unknown \\['lyric'\\]")
        self.refused(changed(entries=[dict(entries[0], locator={"line": 3, "text": "words"})]), "unknown")
        self.refused(changed(entries=[dict(entries[0], locator={"line": 3, "word": 2, "column": 7})]), "at most one")
        self.refused(changed(entries=[dict(entries[0], locator={"line": 0})]), "line must be an integer")
        self.refused(changed(entries=[dict(entries[0], locator={"word": 2})]), "missing \\['line'\\]")
        self.refused(changed(entries=[entries[2], entries[0]]), "located before")
        self.refused(changed(entries=[dict(entries[0], section="")]), "section must be text")
        self.refused(changed(entries=[dict(entries[0], readings=[])]), "1 to 16")
        self.refused(changed(entries=[dict(entries[0], readings=[{"bass": C, "tones": [G, E]}])]), "ascending")
        self.refused(changed(repeats=[{"first": 0, "last": 1, "times": 1}]), "times must be")
        self.refused(changed(repeats=[{"first": 1, "last": 0, "times": 2}]), "last must be")
        self.refused(changed(repeats=[{"first": 0, "last": 8, "times": 2}]), "last must be")
        self.refused(changed(repeats=[{"first": 0, "last": 2, "times": 2}, {"first": 1, "last": 3, "times": 2}]),
                     "never cross")
        self.refused(changed(repeats=[{"first": 2, "last": 3, "times": 2}, {"first": 0, "last": 1, "times": 2}]),
                     "in order")
        work_only = chords_over_lyrics()
        work_only["refs"] = [{"role": "work", **WORK}]
        del work_only["payload"]["document_url"]
        self.refused(work_only, "locator but the claim pins no document")


class TimedFromSequence(unittest.TestCase):
    def test_partial_coverage_is_a_whole_claim(self):
        # A person corrects three chords of a long improvisation and leaves
        # the rest to the machine: three spans, far apart, are a valid claim,
        # and it cites the machine claim it corrects.
        corrected = [span(10, 12, symbol("Ebmaj9", Eb, [D, F, G, Bb], Eb)),
                     span(31, 32, symbol("Ab13", Ab, [C, Eb, F, Gb], Ab)),
                     span(55, 58, {"bass": Db, "tones": [C, F, Ab]})]
        claim = {"agent": "person", "payload": {"sample_rate": RATE, "spans": corrected},
                 "refs": refs(("corrects", MACHINE))}
        self.assertEqual(len(check_chord_stacks(claim, fixture_stream())), 3)
        claim["refs"] += [{"role": "corrects", **MACHINE}] * 8
        with self.assertRaisesRegex(HarmonyError, "0 to 8 'corrects'"):
            check_chord_stacks(claim, fixture_stream())

    def test_an_aligned_sequence_cites_where_it_came_from(self):
        # An alignment run places the sequence on one recording: a separate
        # timed claim that pins the sequence, names each span's entry, and
        # states its method and error bound. The sequence stays untimed.
        sequence = dict(chords_over_lyrics(), revision_id=SEQUENCE["revision_id"])
        order = sequence_order(sequence["payload"])
        entries = sequence["payload"]["entries"]
        spans = [dict(span(2 * i, 2 * i + 2, entries[e]["readings"][0]), entry=e, end="held")
                 for i, e in enumerate(order)]
        timed = {"payload": {"sample_rate": RATE, "spans": spans, "hold_rule": "until the next entry in order",
                             "alignment": {"method": "chroma alignment to the mix's machine chords",
                                           "max_error_samples": 4000}},
                 "refs": refs(("run", RUN), ("sequence", SEQUENCE))}
        self.assertEqual([s["entry"] for s in check_chord_stacks(timed, fixture_stream(), sequence)], order)
        self.assertNotIn("start_sample", json.dumps(sequence))
        # Refusals: no alignment, an entry without the sequence pinned, an
        # entry past the sequence, and another sequence than the one pinned.
        without = json.loads(json.dumps(timed))
        del without["payload"]["alignment"]
        with self.assertRaisesRegex(HarmonyError, "alignment exactly when"):
            check_chord_stacks(without, fixture_stream())
        unpinned = json.loads(json.dumps(timed))
        unpinned["refs"] = refs(("run", RUN))
        del unpinned["payload"]["alignment"]
        with self.assertRaisesRegex(HarmonyError, "pins no sequence"):
            check_chord_stacks(unpinned, fixture_stream())
        past = json.loads(json.dumps(timed))
        past["payload"]["spans"][0]["entry"] = len(entries)
        with self.assertRaisesRegex(HarmonyError, "entry must be an integer"):
            check_chord_stacks(past, fixture_stream(), sequence)
        with self.assertRaisesRegex(HarmonyError, "other than the one it pins"):
            check_chord_stacks(timed, fixture_stream(), dict(sequence, revision_id="sha256:" + "ee" * 32))


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
