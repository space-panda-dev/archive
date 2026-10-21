"""Melody version 2 (ADR 0113): a Work's tune whose pitch items may be
unmeasured, a sketch, beside version 1's unchanged rules.

Run from the repository root: python3 -m unittest world.tests.test_melody_v2.
Only the standard library; every example is synthetic (INV-23).
"""

import unittest

from world.notes import (
    MELODY,
    MELODY_V2,
    UNTIMED_METER,
    UNTIMED_METER_V3,
    NotesError,
    check_melody,
    is_sketch,
)


def pin(n: int) -> dict:
    return {"id": f"urn:uuid:00000000-0000-4000-8000-0000000000{n:02x}", "revision_id": "sha256:" + f"{n:02x}" * 32}


WORK, METER, EARLIER, RUN = pin(0xa1), pin(0xa2), pin(0xa3), pin(0xa4)


def ref(role: str, target: dict) -> dict:
    return {"role": role, **target}


def length(count: int, unit: int) -> dict:
    return {"count": count, "unit": unit}


def melody(items: list, *extra: dict, **payload) -> dict:
    return {"payload": {"notes": items, **payload}, "refs": [ref("work", WORK), *extra]}


def meter(contract=None) -> dict:
    claim = {"revision_id": METER["revision_id"], "refs": [ref("work", WORK)],
             "payload": {"layers": [{"role": "written", "bars": [{"numerator": 4, "denominator": 4}] * 2}]}}
    if contract is not None:
        claim["contract"] = list(contract)
    return claim


SKETCH = [{"pitch": 64}, {"pitch": 67}, {"pitch": 69, "duration": length(1, 2)},
          {"rest": True, "duration": length(1, 4)}, {"pitch": 67, "articulation": ["fermata"]}]
MEASURED = [{"pitch": 64, "duration": length(1, 4)}, {"pitch": 67, "duration": length(3, 4)}]


class Sketch(unittest.TestCase):
    def test_the_records_example_is_a_sketch(self):
        claim = melody(SKETCH, part="voice")
        self.assertEqual(len(check_melody(claim, version="2")), 5)
        self.assertTrue(is_sketch(claim["payload"]))

    def test_version_1_still_needs_every_duration(self):
        with self.assertRaisesRegex(NotesError, "item 0 has missing"):
            check_melody(melody(SKETCH))

    def test_a_rest_always_has_a_duration(self):
        with self.assertRaisesRegex(NotesError, "item 1 has missing"):
            check_melody(melody([{"pitch": 60}, {"rest": True}]), version="2")

    def test_a_sketch_pins_no_meter(self):
        with self.assertRaisesRegex(NotesError, "sketch .* pins no meter"):
            check_melody(melody(SKETCH, ref("meter", METER)), meter(), version="2")

    def test_a_sketch_has_no_start(self):
        claim = melody(SKETCH, start={"layer": 0, "bar": 0})
        with self.assertRaisesRegex(NotesError, "sketch .* has no start"):
            check_melody(claim, version="2")

    def test_rules_that_read_no_duration_still_hold(self):
        with self.assertRaisesRegex(NotesError, "only rests"):
            check_melody(melody([{"rest": True, "duration": length(1, 4)}]), version="2")
        with self.assertRaisesRegex(NotesError, "work or a document"):
            check_melody({"payload": {"notes": [{"pitch": 60}]}, "refs": []}, version="2")
        with self.assertRaisesRegex(NotesError, "pitch must be an integer"):
            check_melody(melody([{"pitch": 128}]), version="2")
        with self.assertRaisesRegex(NotesError, "articulation"):
            check_melody(melody([{"pitch": 60, "articulation": ["fermata"]}], ref("run", RUN)), version="2")
        with self.assertRaisesRegex(NotesError, "locator"):
            check_melody(melody([{"pitch": 60, "locator": {"bar": 1}}]), version="2")

    def test_corrects_and_derived_from_keep_their_rules(self):
        claim = melody(SKETCH, ref("corrects", EARLIER), ref("derived_from", EARLIER))
        with self.assertRaises(NotesError):
            check_melody(claim, version="2")
        self.assertEqual(len(check_melody(melody(SKETCH, ref("corrects", EARLIER)), version="2")), 5)

    def test_unknown_version_is_refused(self):
        with self.assertRaisesRegex(NotesError, "version"):
            check_melody(melody(MEASURED), version="3")


class Measured(unittest.TestCase):
    def test_a_measured_melody_says_the_same_in_either_version(self):
        claim = melody(MEASURED, ref("meter", METER), start={"layer": 0, "bar": 0})
        self.assertEqual(check_melody(claim, meter()), check_melody(claim, meter(), version="2"))
        self.assertFalse(is_sketch(claim["payload"]))

    def test_fit_check_runs_in_version_2(self):
        long = [{"pitch": 60, "duration": length(3, 1)}]
        with self.assertRaisesRegex(NotesError, "runs past the last written bar"):
            check_melody(melody(long, ref("meter", METER)), meter(), version="2")

    def test_version_2_meter_may_be_meter_layers_2_or_3(self):
        claim = melody(MEASURED, ref("meter", METER))
        for contract in (UNTIMED_METER, UNTIMED_METER_V3):
            self.assertEqual(len(check_melody(claim, meter(contract), version="2")), 2)
        with self.assertRaisesRegex(NotesError, "meter_layers version 2 or .* version 3"):
            check_melody(claim, meter(("archive.claim/meter_layers", "1")), version="2")

    def test_is_sketch_reads_any_payload(self):
        self.assertFalse(is_sketch(None))
        self.assertFalse(is_sketch({"notes": "x"}))
        self.assertTrue(is_sketch({"notes": [{"pitch": 60}]}))

    def test_contract_names(self):
        self.assertEqual(MELODY, ("archive.claim/melody", "1"))
        self.assertEqual(MELODY_V2, ("archive.claim/melody", "2"))


if __name__ == "__main__":
    unittest.main()
