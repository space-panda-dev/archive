"""Chord stacks, chord sequence, and meter layers version 2 (ADR 0079):
`derived_from`, a stem cited by bank key, the bass-only reading, meter
corrections, and the larger span cap, with version 1 unchanged beside them.
Meter layers version 2 is checked in one place with ADR 0083's additions
(`world/form.py`), so these tests call that check.

Run from the repository root: python3 -m unittest world.tests.test_machine_harmony.
Every example is synthetic (INV-23). The stream claim is the fixture
archive's own stream record from fixture/plan.json, lengthened to three
hours; a stem citation's key is the one the installed `stems` extractor
gives (enrich/bank.py's catalog), and its digest is the bank's own file
digest of a file written here, so a citation is the shape the bank writes.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

from world.form import METER_LAYERS_V2, check_meter_layers_v2, is_untimed_meter
from world.harmony import (
    CHORD_SEQUENCE,
    CHORD_SEQUENCE_V2,
    CHORD_STACKS,
    CHORD_STACKS_V2,
    MAX_SPANS,
    MAX_SPANS_V2,
    METER_LAYERS,
    HarmonyError,
    check_chord_sequence,
    check_chord_sequence_v2,
    check_chord_stacks,
    check_chord_stacks_v2,
    check_meter_layers,
    check_reading_v2,
    check_stem_citation,
    is_bass_only,
    jams_chords,
)

ROOT = Path(__file__).resolve().parents[2]
# Appended, not inserted: World's own test modules must come first, since
# enrich/ has test modules of the same names (test_notes).
sys.path.append(str(ROOT / "enrich"))
import bank  # noqa: E402  (enrich/bank.py)

SOURCE = {"id": "urn:uuid:00000000-0000-4000-8000-0000000000c1", "revision_id": "sha256:" + "c1" * 32}
STREAM = {"id": "urn:uuid:00000000-0000-4000-8000-0000000000c2", "revision_id": "sha256:" + "c2" * 32}
RUN = {"id": "urn:uuid:00000000-0000-4000-8000-0000000000c3", "revision_id": "sha256:" + "c3" * 32}
MACHINE = {"id": "urn:uuid:00000000-0000-4000-8000-0000000000c4", "revision_id": "sha256:" + "c4" * 32}
OTHER = {"id": "urn:uuid:00000000-0000-4000-8000-0000000000c5", "revision_id": "sha256:" + "c5" * 32}
STEM_ARTIFACT = {"id": "urn:uuid:00000000-0000-4000-8000-0000000000c6", "revision_id": "sha256:" + "c6" * 32}
# The content digest of SOURCE's Artifact revision: the recording the bank files the stems under.
MIX_CONTENT = "sha256:" + "d1" * 32
RATE = 8000
SECOND = RATE
HOURS = 3

C, Db, D, Eb, E, F, Gb, G, Ab, A, Bb, B = range(12)


def fixture_stream() -> dict:
    """The fixture archive's audio stream claim, about SOURCE, three hours long."""
    plan = json.loads((ROOT / "fixture/plan.json").read_text())
    records = [r for step in plan["steps"] for r in step.get("records", [])]
    record = next(r for r in records if r.get("namespace") == "archive.claim/audio_stream")
    payload = dict(record["payload"])
    assert payload["sample_rate"] == RATE, "the fixture stream's rate changed; update RATE"
    payload["sample_count"] = HOURS * 3600 * SECOND
    return {"revision_id": STREAM["revision_id"], "payload": payload,
            "refs": [{"role": "artifact", **SOURCE}, {"role": "run", **RUN}]}


def refs(*extra: tuple[str, dict]) -> list[dict]:
    return [{"role": "source", **SOURCE}, {"role": "stream", **STREAM}] + [{"role": r, **v} for r, v in extra]


def span(start: float, stop: float, *readings: dict, **fields) -> dict:
    return {"start_sample": int(start * SECOND), "end_sample": int(stop * SECOND),
            "readings": list(readings), **fields}


def chord_claim(spans: list[dict], *extra: tuple[str, dict], **payload) -> dict:
    return {"payload": {"sample_rate": RATE, "spans": spans, **payload}, "refs": refs(*extra)}


def meter_claim(layers: list[dict], *extra: tuple[str, dict], **payload) -> dict:
    return {"payload": {"sample_rate": RATE, "layers": layers, **payload}, "refs": refs(*extra)}


def real_stem_citation(output: str = "bass") -> dict:
    """A citation built by the bank's own functions: the key the installed
    `stems` extractor gives MIX_CONTENT, and the bank's file digest of a
    stem file."""
    key = bank.Catalog.load().keys(MIX_CONTENT, ["stems"])["stems"]
    with tempfile.TemporaryDirectory() as scratch:
        stem = Path(scratch) / f"{output}.flac"
        stem.write_bytes(b"fLaC synthetic stem bytes")
        digest = bank.file_digest(stem)
    return {"extractor": "stems", "key": key, "output": output, "sha256": digest}


STREAM_CLAIM = fixture_stream()
CMAJ7 = {"bass": C, "tones": [E, G, B], "root": C, "text": "Cmaj7"}
ONLY_E = {"bass": E, "tones": []}          # a stack: E sounds, and nothing above it
BASS_E = {"bass": E}                       # bass-only: E is lowest, nothing said above
BASS_E_UNDER_C = {"bass": E, "root": C}    # bass-only with a root: the bass of some C chord


class Anchors(unittest.TestCase):
    """The distinctions every later test rests on."""

    def test_the_versions_are_one_contract_with_two_versions(self) -> None:
        self.assertEqual(CHORD_STACKS[0], CHORD_STACKS_V2[0])
        self.assertEqual(CHORD_SEQUENCE[0], CHORD_SEQUENCE_V2[0])
        self.assertEqual(METER_LAYERS[0], METER_LAYERS_V2[0])
        self.assertNotEqual(CHORD_STACKS[1], CHORD_STACKS_V2[1])
        self.assertNotEqual(CHORD_SEQUENCE[1], CHORD_SEQUENCE_V2[1])
        self.assertNotEqual(METER_LAYERS[1], METER_LAYERS_V2[1])

    def test_a_bass_only_reading_differs_from_a_stack_with_empty_tones(self) -> None:
        self.assertNotEqual(BASS_E, ONLY_E)
        self.assertTrue(is_bass_only(BASS_E))
        self.assertFalse(is_bass_only(ONLY_E))
        self.assertEqual(check_reading_v2(ONLY_E, False), "stack")
        self.assertEqual(check_reading_v2(BASS_E, False), "bass_only")

    def test_the_span_cap_is_larger_in_version_2(self) -> None:
        self.assertLess(MAX_SPANS, MAX_SPANS_V2)

    def test_the_stem_citation_is_the_banks_own_shape(self) -> None:
        stem = real_stem_citation()
        self.assertTrue(bank.KEY.fullmatch(stem["key"]))
        self.assertTrue(bank.ID.fullmatch(stem["extractor"]))
        self.assertTrue(bank.OUTPUT.fullmatch(stem["output"]))
        self.assertEqual(check_stem_citation(stem), stem)


class ChordStacksV2Accepted(unittest.TestCase):

    def test_a_machines_bass_per_beat_from_the_bass_stem(self) -> None:
        stem = real_stem_citation("bass")
        spans = [span(0, 0.5, BASS_E, end="asserted"), span(0.5, 1, {"bass": A}), span(1.5, 2, BASS_E_UNDER_C)]
        claim = chord_claim(spans, ("run", RUN), stem=stem)
        self.assertEqual(len(check_chord_stacks_v2(claim, STREAM_CLAIM)), 3)

    def test_a_bass_only_reading_may_be_weighted_and_keep_its_text(self) -> None:
        reading = {"bass": G, "text": "/G", "weight": "0.71"}
        claim = chord_claim([span(0, 2, reading, {"bass": D, "weight": "0.2"})], ("run", RUN),
                            weight_scale="lead of the strongest class over the next, per beat")
        check_chord_stacks_v2(claim, STREAM_CLAIM)

    def test_a_persons_claim_derived_from_claims_without_a_run(self) -> None:
        claim = chord_claim([span(10, 12, CMAJ7)], ("derived_from", MACHINE), ("derived_from", OTHER))
        check_chord_stacks_v2(claim, STREAM_CLAIM)

    def test_a_correction_may_also_derive_from_another_claim(self) -> None:
        claim = chord_claim([span(10, 12, CMAJ7)], ("corrects", MACHINE), ("derived_from", OTHER))
        check_chord_stacks_v2(claim, STREAM_CLAIM)

    def test_a_version_1_claim_is_a_valid_version_2_claim(self) -> None:
        claim = chord_claim([span(0, 1, CMAJ7), span(1, 2, {"no_chord": True}), span(2, 3, {"text": "Gm(maj13)/D"})],
                            ("run", RUN), ("analysed", STEM_ARTIFACT))
        self.assertEqual(check_chord_stacks(claim, STREAM_CLAIM), check_chord_stacks_v2(claim, STREAM_CLAIM))

    def test_a_bass_per_beat_over_three_hours_fits_one_claim(self) -> None:
        # 200 beats a minute for three hours: 36,000 beats, each its own bass.
        beat = 60 * SECOND // 200
        count = 200 * 60 * HOURS
        self.assertGreater(count, MAX_SPANS)
        spans = [{"start_sample": i * beat, "end_sample": (i + 1) * beat, "readings": [{"bass": (i * 7) % 12}]}
                 for i in range(count)]
        claim = chord_claim(spans, ("run", RUN), stem=real_stem_citation())
        self.assertEqual(len(check_chord_stacks_v2(claim, STREAM_CLAIM)), count)
        with self.assertRaisesRegex(HarmonyError, "spans must be a list of 1 to 10000"):
            check_chord_stacks(chord_claim(spans, ("run", RUN)), STREAM_CLAIM)


class ChordStacksV2Refused(unittest.TestCase):
    """Malformed version 2 claims fail closed."""

    def refused(self, claim: dict, pattern: str) -> None:
        with self.assertRaisesRegex(HarmonyError, pattern):
            check_chord_stacks_v2(claim, STREAM_CLAIM)

    def test_a_stem_and_an_analysed_artifact_together(self) -> None:
        self.refused(chord_claim([span(0, 1, BASS_E)], ("run", RUN), ("analysed", STEM_ARTIFACT),
                                 stem=real_stem_citation()), "one or neither")

    def test_malformed_stem_citations(self) -> None:
        good = real_stem_citation()
        bad = [
            ({k: v for k, v in good.items() if k != "sha256"}, "missing"),
            ({**good, "version": "1"}, "unknown"),
            ({**good, "key": good["key"].removeprefix("sha256:")}, "key must be a canonical SHA-256"),
            ({**good, "sha256": "sha256:" + "A" * 64}, "sha256 must be a canonical SHA-256"),
            ({**good, "extractor": "Stems"}, "extractor must be a bank extractor id"),
            ({**good, "extractor": "stems/bass"}, "extractor must be a bank extractor id"),
            ({**good, "output": "bass-stem"}, "output must be a bank output name"),
            ({**good, "output": 3}, "output must be a bank output name"),
            ("stems/bass", "must be an object"),
        ]
        for stem, pattern in bad:
            with self.subTest(stem=stem):
                self.refused(chord_claim([span(0, 1, BASS_E)], ("run", RUN), stem=stem), pattern)

    def test_malformed_bass_only_readings(self) -> None:
        bad = [
            ({"bass": 12}, "bass must be an integer in 0..11"),
            ({"bass": True}, "bass must be an integer"),
            ({"bass": E, "root": -1}, "root must be an integer in 0..11"),
            ({"bass": E, "uncertain": [{"pitch_classes": [G, Ab], "present": "exactly_one"}]}, "unknown"),
            ({"bass": E, "voicing": [40]}, "unknown"),
            ({"bass": E, "no_chord": True}, "unknown"),
            ({"bass": E, "text": ""}, "text must be text of 1 to 200"),
            ({"bass": E, "weight": "0.5"}, "weight exactly when"),
        ]
        for reading, pattern in bad:
            with self.subTest(reading=reading):
                self.refused(chord_claim([span(0, 1, reading)], ("run", RUN)), pattern)

    def test_a_bass_only_reading_without_its_weight_in_a_weighted_claim(self) -> None:
        self.refused(chord_claim([span(0, 1, BASS_E)], ("run", RUN), weight_scale="probability"),
                     "weight exactly when")

    def test_a_repeated_bass_only_reading(self) -> None:
        self.refused(chord_claim([span(0, 1, BASS_E, dict(BASS_E))], ("run", RUN)), "repeats a reading")

    def test_derived_from_twice_or_also_corrected(self) -> None:
        self.refused(chord_claim([span(0, 1, CMAJ7)], ("derived_from", MACHINE), ("derived_from", MACHINE)),
                     "derived_from revision twice")
        self.refused(chord_claim([span(0, 1, CMAJ7)], ("corrects", MACHINE), ("derived_from", MACHINE)),
                     "both corrects and derived_from")

    def test_more_than_eight_derived_from(self) -> None:
        many = [("derived_from", {"id": MACHINE["id"], "revision_id": "sha256:" + f"{i:064x}"}) for i in range(9)]
        self.refused(chord_claim([span(0, 1, CMAJ7)], *many), "0 to 8 'derived_from'")

    def test_derived_from_must_pin_an_exact_revision(self) -> None:
        claim = chord_claim([span(0, 1, CMAJ7)])
        claim["refs"].append({"role": "derived_from", "id": MACHINE["id"]})
        self.refused(claim, "missing")

    def test_an_unknown_role_or_field(self) -> None:
        self.refused(chord_claim([span(0, 1, CMAJ7)], ("stem", MACHINE)), "unknown role 'stem'")
        self.refused(chord_claim([span(0, 1, CMAJ7)], bass_stem="bass"), "unknown")

    def test_version_1_rules_still_hold_in_version_2(self) -> None:
        self.refused(chord_claim([span(1, 2, CMAJ7), span(0, 1, CMAJ7)]), "overlaps")
        self.refused(chord_claim([span(0, HOURS * 3600 + 1, BASS_E)]), "past the stream")
        self.refused(chord_claim([span(0, 1, BASS_E, end="held")]), "hold_rule")


class Version1Unchanged(unittest.TestCase):
    """Version 1 refuses every addition of version 2, as it did before."""

    def test_version_1_refuses_a_bass_only_reading(self) -> None:
        with self.assertRaisesRegex(HarmonyError, "missing \\['tones'\\]"):
            check_chord_stacks(chord_claim([span(0, 1, BASS_E)], ("run", RUN)), STREAM_CLAIM)

    def test_version_1_refuses_a_stem_citation(self) -> None:
        with self.assertRaisesRegex(HarmonyError, "unknown \\['stem'\\]"):
            check_chord_stacks(chord_claim([span(0, 1, CMAJ7)], ("run", RUN), stem=real_stem_citation()),
                               STREAM_CLAIM)
        with self.assertRaisesRegex(HarmonyError, "unknown \\['stem'\\]"):
            check_meter_layers(meter_claim([{"beats": [0, SECOND]}], ("run", RUN), stem=real_stem_citation()),
                               STREAM_CLAIM)

    def test_version_1_refuses_derived_from(self) -> None:
        with self.assertRaisesRegex(HarmonyError, "unknown role 'derived_from'"):
            check_chord_stacks(chord_claim([span(0, 1, CMAJ7)], ("derived_from", MACHINE)), STREAM_CLAIM)
        with self.assertRaisesRegex(HarmonyError, "unknown role 'derived_from'"):
            check_meter_layers(meter_claim([{"beats": [0]}], ("derived_from", MACHINE)), STREAM_CLAIM)

    def test_version_1_meter_refuses_corrects(self) -> None:
        with self.assertRaisesRegex(HarmonyError, "unknown role 'corrects'"):
            check_meter_layers(meter_claim([{"beats": [0]}], ("corrects", MACHINE)), STREAM_CLAIM)

    def test_version_1_keeps_its_cap_and_its_claims(self) -> None:
        claim = chord_claim([span(0, 1, CMAJ7), span(1, 2, ONLY_E)], ("run", RUN), ("analysed", STEM_ARTIFACT))
        self.assertEqual(len(check_chord_stacks(claim, STREAM_CLAIM)), 2)
        layers = [{"role": "written", "bars": [{"start_sample": 0, "end_sample": 2 * SECOND,
                                                "numerator": 4, "denominator": 4}]}]
        self.assertEqual(check_meter_layers(meter_claim(layers, ("run", RUN)), STREAM_CLAIM), layers)


class MeterLayersV2(unittest.TestCase):

    BEATS = [{"beats": [0, SECOND // 2, SECOND, 3 * SECOND // 2], "downbeats": [0]}]

    def test_a_machine_beat_grid_from_a_stem(self) -> None:
        stem = real_stem_citation("drums")
        self.assertEqual(check_meter_layers_v2(meter_claim(self.BEATS, ("run", RUN), stem=stem), STREAM_CLAIM),
                         self.BEATS)

    def test_a_persons_correction_of_a_machine_grid(self) -> None:
        bars = [{"role": "written", "bars": [{"start_sample": 0, "end_sample": 2 * SECOND,
                                              "numerator": 7, "denominator": 8, "grouping": [2, 2, 3]}]}]
        claim = meter_claim(bars, ("corrects", MACHINE), ("derived_from", OTHER))
        check_meter_layers_v2(claim, STREAM_CLAIM)

    def test_a_version_1_meter_claim_is_a_valid_version_2_claim(self) -> None:
        claim = meter_claim(self.BEATS, ("run", RUN))
        self.assertEqual(check_meter_layers(claim, STREAM_CLAIM), check_meter_layers_v2(claim, STREAM_CLAIM))

    def test_refused(self) -> None:
        cases = [
            (meter_claim(self.BEATS, ("run", RUN), ("analysed", STEM_ARTIFACT), stem=real_stem_citation()),
             "one or neither"),
            (meter_claim(self.BEATS, ("corrects", MACHINE), ("derived_from", MACHINE)), "both corrects"),
            (meter_claim(self.BEATS, *[("corrects", {"id": MACHINE["id"], "revision_id": "sha256:" + f"{i:064x}"})
                                       for i in range(9)]), "0 to 8 'corrects'"),
            (meter_claim(self.BEATS, stem={"extractor": "stems", "output": "drums"}), "missing"),
            (meter_claim([{"beats": [SECOND, 0]}]), "ascending"),
        ]
        for claim, pattern in cases:
            with self.subTest(pattern=pattern):
                with self.assertRaisesRegex(HarmonyError, pattern):
                    check_meter_layers_v2(claim, STREAM_CLAIM)

    def test_one_check_holds_both_records_additions(self) -> None:
        """ADR 0079's and ADR 0083's additions are one version: an untimed
        claim corrects and derives, and a timed placement may also cite a
        stem and correct, in one claim checked by one function."""
        untimed = {"revision_id": OTHER["revision_id"],
                   "payload": {"layers": [{"bars": [{"numerator": 4, "denominator": 4}]}]},
                   "refs": [{"role": "work", **SOURCE}, {"role": "corrects", **MACHINE},
                            {"role": "derived_from", **STEM_ARTIFACT}]}
        self.assertTrue(is_untimed_meter(untimed))
        check_meter_layers_v2(untimed)
        placed = meter_claim([{"bars": [{"start_sample": 0, "end_sample": SECOND, "numerator": 4,
                                         "denominator": 4, "placed": {"layer": 0, "bar": 0}}]}],
                             ("meter", OTHER), ("corrects", MACHINE), stem=real_stem_citation("drums"),
                             alignment={"method": "manual"})
        check_meter_layers_v2(placed, STREAM_CLAIM, untimed_claim=untimed)
        with self.assertRaisesRegex(HarmonyError, "both corrects"):
            check_meter_layers_v2({**untimed, "refs": untimed["refs"] + [{"role": "derived_from", **MACHINE}]})


class ChordSequenceV2(unittest.TestCase):
    """Chord sequence version 2: a chart's bass-only entry, lineage, and
    corrections; version 1 is unchanged."""

    @staticmethod
    def sequence(entries: list[dict], *extra: tuple[str, dict]) -> dict:
        return {"payload": {"entries": entries},
                "refs": [{"role": "work", **SOURCE}] + [{"role": r, **v} for r, v in extra]}

    def test_a_charts_slash_entry_with_nothing_above_it(self) -> None:
        claim = self.sequence([{"readings": [CMAJ7]}, {"readings": [{"bass": E, "text": "/E"}]}],
                              ("derived_from", MACHINE), ("corrects", OTHER))
        self.assertEqual(len(check_chord_sequence_v2(claim)), 2)

    def test_a_version_1_sequence_is_a_valid_version_2_sequence(self) -> None:
        claim = self.sequence([{"readings": [CMAJ7]}, {"readings": [ONLY_E]}])
        self.assertEqual(check_chord_sequence(claim), check_chord_sequence_v2(claim))

    def test_version_1_refuses_the_additions(self) -> None:
        with self.assertRaisesRegex(HarmonyError, "missing \\['tones'\\]"):
            check_chord_sequence(self.sequence([{"readings": [BASS_E]}]))
        with self.assertRaisesRegex(HarmonyError, "unknown role 'derived_from'"):
            check_chord_sequence(self.sequence([{"readings": [CMAJ7]}], ("derived_from", MACHINE)))

    def test_refused(self) -> None:
        with self.assertRaisesRegex(HarmonyError, "both corrects"):
            check_chord_sequence_v2(self.sequence([{"readings": [CMAJ7]}], ("corrects", MACHINE),
                                                  ("derived_from", MACHINE)))
        with self.assertRaisesRegex(HarmonyError, "unknown \\['stem'\\]"):
            check_chord_sequence_v2({**self.sequence([{"readings": [CMAJ7]}]),
                                     "payload": {"entries": [{"readings": [CMAJ7]}], "stem": real_stem_citation()}})

    def test_chord_stacks_v2_places_a_version_2_sequence(self) -> None:
        sequence = {**self.sequence([{"readings": [BASS_E]}]), "revision_id": OTHER["revision_id"]}
        claim = chord_claim([span(0, 1, BASS_E, entry=0, end="held")], ("sequence", OTHER),
                            alignment={"method": "manual"}, hold_rule="held to the next entry")
        check_chord_stacks_v2(claim, STREAM_CLAIM, sequence)
        with self.assertRaisesRegex(HarmonyError, "missing \\['tones'\\]"):
            check_chord_stacks({**claim, "payload": {**claim["payload"], "spans": [span(0, 1, CMAJ7, entry=0)]}},
                               STREAM_CLAIM, sequence)


class Exchange(unittest.TestCase):

    def test_a_bass_only_reading_is_listed_as_lost_in_jams(self) -> None:
        payload = chord_claim([span(0, 1, CMAJ7), span(1, 2, BASS_E)], ("run", RUN))["payload"]
        harte = jams_chords(payload)
        self.assertEqual([d["value"] for d in harte["data"]], ["C:maj7"])
        self.assertIn("bass-only readings (a bass names no chord)", harte["sandbox"]["archive_lossy"])
        loose = jams_chords(payload, namespace="chord")
        self.assertEqual([d["value"] for d in loose["data"]], ["C:maj7", "X"])


if __name__ == "__main__":
    unittest.main()
