"""The chord-symbol grammar, checked against World's reading checks."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from world.harmony import check_reading_v2  # noqa: E402
from world.tonality import check_key_reading  # noqa: E402

import symbols  # noqa: E402
from symbols import QUALITIES, UnsupportedSymbol, read_key, read_symbol, reading_of, symbol_of  # noqa: E402


class Reading(unittest.TestCase):
    def test_every_quality_on_every_root_is_a_valid_stack(self) -> None:
        for quality in QUALITIES:
            for name in ("C", "Db", "F#", "B", "Cb", "E#"):
                for bass in ("", "/E", "/Bb"):
                    text = name + quality + bass
                    with self.subTest(text=text):
                        reading = read_symbol(text)
                        self.assertEqual(check_reading_v2(reading, False), "stack")
                        self.assertEqual(reading["text"], text)

    def test_spot_readings(self) -> None:
        self.assertEqual(read_symbol("C-7"), {"root": 0, "bass": 0, "tones": [3, 7, 10], "text": "C-7"})
        self.assertEqual(read_symbol("Bb7b9/Ab"),
                         {"root": 10, "bass": 8, "tones": [2, 5, 10, 11], "text": "Bb7b9/Ab"})
        self.assertEqual(read_symbol("D/F#"), {"root": 2, "bass": 6, "tones": [2, 9], "text": "D/F#"})
        self.assertEqual(read_symbol("Cb^7")["root"], 11)
        self.assertEqual(read_symbol("n"), {"no_chord": True, "text": "n"})

    def test_uncertain_tones(self) -> None:
        alt = read_symbol("G7alt")
        self.assertEqual(alt["tones"], [5, 11])   # the root is the bass, so not among the tones
        self.assertEqual(alt["uncertain"], [{"pitch_classes": [1], "present": "maybe"},
                                            {"pitch_classes": [3], "present": "maybe"},
                                            {"pitch_classes": [8], "present": "maybe"},
                                            {"pitch_classes": [10], "present": "maybe"}])
        # A maybe-tone that is the bass is certain, not maybe.
        self.assertNotIn([8], [group["pitch_classes"] for group in read_symbol("G7alt/Ab")["uncertain"]])
        self.assertEqual(read_symbol("F11")["uncertain"], [{"pitch_classes": [9], "present": "maybe"}])

    def test_an_invisible_root_keeps_the_chord_over_a_new_bass(self) -> None:
        before = read_symbol("C^7")
        reading = read_symbol("W/B", before)
        self.assertEqual(reading, {"root": 0, "bass": 11, "tones": [0, 4, 7], "text": "W/B"})
        self.assertEqual(check_reading_v2(reading, False), "stack")
        with self.assertRaisesRegex(UnsupportedSymbol, "no chord before it"):
            read_symbol("W/B")

    def test_unsupported_symbols_fail_naming_the_symbol(self) -> None:
        cases = {"C2": "add2 or sus2", "C7b9b9b9": "unknown quality '7b9b9b9'", "Cmaj7": "unknown quality",
                 "C*glow*": "unknown quality", "W7/G": "invisible root with a quality", "H7": "not a chord"}
        for text, reason in cases.items():
            with self.subTest(text=text):
                with self.assertRaisesRegex(UnsupportedSymbol, reason) as raised:
                    read_symbol(text, read_symbol("C"))
                self.assertIn(repr(text), str(raised.exception))
                reading, why = reading_of(text, read_symbol("C"))
                self.assertEqual(reading, {"text": text})
                self.assertEqual(check_reading_v2(reading, False), "text")
                self.assertIsNotNone(why)


class Writing(unittest.TestCase):
    def test_a_reading_keeps_its_own_text(self) -> None:
        for text in ("C^", "C^7", "Dh", "D-7b5", "Eb7alt/G", "n"):
            self.assertEqual(symbol_of(read_symbol(text)), text)

    def test_a_stack_without_text_gets_the_first_quality_that_fits(self) -> None:
        for quality, (certain, _) in QUALITIES.items():
            for root in range(12):
                reading = read_symbol(symbols.FLAT_NAMES[root] + quality)
                del reading["text"]
                written = symbol_of(reading)
                with self.subTest(quality=quality, root=root):
                    self.assertIsNotNone(written)
                    again = read_symbol(written)
                    del again["text"]
                    self.assertEqual(again, reading)

    def test_slash_chords_prefer_a_quality_that_holds_the_bass(self) -> None:
        reading = read_symbol("C/E")
        del reading["text"]
        self.assertEqual(symbol_of(reading), "C/E")
        self.assertEqual(symbol_of(reading, ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")), "C/E")

    def test_no_quality_writes_some_stacks(self) -> None:
        self.assertIsNone(symbol_of({"root": 0, "bass": 0, "tones": [1, 2]}))
        self.assertIsNone(symbol_of({"bass": 0, "tones": [4, 7]}))   # the root is not asserted
        self.assertIsNone(symbol_of({"text": "C*glow*"}))


class Key(unittest.TestCase):
    def test_major_and_natural_minor(self) -> None:
        major = read_key("Eb")
        self.assertEqual((major["tonic"], major["mode"], major["text"]), (3, "major", "Eb"))
        minor = read_key("C#-")
        self.assertEqual(minor["collection"], [1, 3, 4, 6, 8, 9, 11])
        for reading in (major, minor):
            self.assertEqual(check_key_reading(reading, False), "key")

    def test_other_fields(self) -> None:
        self.assertIsNone(read_key(""))
        self.assertEqual(read_key("C dorian"), {"text": "C dorian"})
        self.assertEqual(check_key_reading(read_key("C dorian"), False), "text")


if __name__ == "__main__":
    unittest.main()
