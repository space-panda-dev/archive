"""The note builder (enrich/note_builder.py): one note per held pitch.

Each test builds a pitch track row by row, as f0-pesto writes one (a row
every 10 ms: pitch in Hz, confidence, and the level in dBFS), and checks the
notes the builder makes of it with the voice's and the bass's settings.
"""

from __future__ import annotations

import unittest

import numpy

import note_builder

HOP = 0.01


def hz(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


class Track:
    """Rows of pitch (Hz, 0 for none), confidence, and level (dBFS)."""

    def __init__(self) -> None:
        self.hz: list[float] = []
        self.confidence: list[float] = []
        self.level: list[float] = []

    def add(self, seconds: float, midi: float | list[float] | None, level: float = -10.0,
            confidence: float = 0.9) -> "Track":
        rows = int(round(seconds / HOP))
        pitches = midi if isinstance(midi, list) else [midi] * rows
        assert len(pitches) == rows
        for value in pitches:
            self.hz.append(0.0 if value is None else hz(value))
            self.confidence.append(0.0 if value is None else confidence)
            self.level.append(-70.0 if value is None else level)
        return self

    def build(self, settings: dict, **options: float) -> tuple[list[dict], dict]:
        return note_builder.build(numpy.array(self.hz), numpy.array(self.confidence), numpy.array(self.level),
                                  HOP, settings, **options)


def silence(track: Track, seconds: float = 0.3) -> Track:
    return track.add(seconds, None)


def pitches(notes: list[dict]) -> list[int]:
    return [round(n["pitch"]) for n in notes]


class Notes(unittest.TestCase):
    def test_a_held_note_with_vibrato_is_one_note_at_its_centre(self) -> None:
        rows = numpy.arange(100) * HOP
        vibrato = [69 + 0.4 * float(numpy.sin(2 * numpy.pi * 5.5 * t)) for t in rows]
        notes, _ = silence(silence(Track()).add(1.0, vibrato)).build(note_builder.VOICE)
        self.assertEqual(pitches(notes), [69])
        self.assertLess(abs(notes[0]["pitch"] - 69), 0.1)
        self.assertEqual((notes[0]["first_row"], notes[0]["end_row"]), (30, 130))

    def test_a_slide_between_two_held_pitches_is_two_notes_split_inside_it(self) -> None:
        slide = [60 + 4 * (k + 1) / 11 for k in range(10)]
        track = silence(silence(Track()).add(0.4, 60).add(0.1, slide).add(0.4, 64))
        notes, _ = track.build(note_builder.VOICE)
        self.assertEqual(pitches(notes), [60, 64])
        self.assertTrue(70 <= notes[0]["end_row"] == notes[1]["first_row"] <= 80, notes)

    def test_a_short_gap_on_one_pitch_is_bridged_for_a_voice_but_never_for_a_bass(self) -> None:
        gap = 0.05
        self.assertLessEqual(gap, float(note_builder.VOICE["bridge_s"]))  # anchor: short enough for a voice
        self.assertEqual(note_builder.BASS["bridge_s"], "0")
        track = silence(silence(Track()).add(0.3, 45).add(gap, None).add(0.3, 45))
        sung, counts = track.build(note_builder.VOICE)
        self.assertEqual((pitches(sung), counts["bridged"]), ([45], 1))
        self.assertEqual(sung[0]["end_row"] - sung[0]["first_row"], 65)
        played, counts = track.build(note_builder.BASS)
        self.assertEqual((pitches(played), counts["bridged"]), ([45, 45], 0))

    def test_a_longer_gap_is_never_bridged(self) -> None:
        track = silence(silence(Track()).add(0.3, 64).add(0.15, None).add(0.3, 64))
        self.assertEqual(pitches(track.build(note_builder.VOICE)[0]), [64, 64])

    def test_a_level_dip_splits_a_bass_note_and_only_a_deep_one_a_sung_note(self) -> None:
        dip = [-10.0] * 30 + [-12.0, -16.0, -20.0, -16.0, -12.0] + [-10.0] * 30
        track = silence(Track())
        for level in dip:
            track.add(HOP, 40, level=level)
        silence(track)
        self.assertEqual(pitches(track.build(note_builder.VOICE)[0]), [40])  # 10 dB: under the voice's 14
        played = track.build(note_builder.BASS)[0]
        self.assertEqual(pitches(played), [40, 40])
        self.assertEqual(played[0]["end_row"], 30 + 32)  # at the lowest row

    def test_a_blip_is_dropped_and_a_note_an_octave_off_its_neighbours_is_folded(self) -> None:
        track = silence(Track())
        for midi in (60, 62, 64, 77, 67, 69):
            track.add(0.25, midi).add(0.15, None)
        track.add(0.04, 72).add(0.3, None)
        notes, counts = track.build(note_builder.VOICE)
        self.assertEqual(pitches(notes), [60, 62, 64, 65, 67, 69])
        self.assertEqual((counts["octaves_folded"], counts["dropped_short"]), (1, 1))

    def test_a_bass_keeps_a_note_too_short_for_a_voice(self) -> None:
        self.assertLessEqual(float(note_builder.BASS["min_note_s"]), 0.05)  # anchor: the 50 ms note is
        self.assertLess(0.05, float(note_builder.VOICE["min_note_s"]))         # long enough for one only
        track = silence(silence(Track()).add(0.3, 40).add(0.1, None).add(0.05, 43).add(0.1, None).add(0.3, 40))
        self.assertEqual(pitches(track.build(note_builder.BASS)[0]), [40, 43, 40])
        self.assertEqual(pitches(track.build(note_builder.VOICE)[0]), [40, 40])

    def test_voicing_starts_at_the_higher_confidence_and_stays_at_the_lower(self) -> None:
        never = silence(silence(Track()).add(0.3, 57, confidence=0.5))
        self.assertEqual(never.build(note_builder.VOICE)[0], [])
        held = silence(silence(Track()).add(0.3, 57, confidence=0.7).add(0.3, 57, confidence=0.4))
        [note] = held.build(note_builder.VOICE)[0]
        self.assertEqual((note["first_row"], note["end_row"]), (30, 90))

    def test_the_pitch_is_measured_against_the_tuning(self) -> None:
        track = silence(silence(Track()).add(0.5, 69 - 0.4))
        [flat] = track.build(note_builder.VOICE)[0]
        self.assertLess(abs(flat["pitch"] - 68.6), 0.01)
        [tuned] = track.build(note_builder.VOICE, tuning_cents=-40.0)[0]
        self.assertLess(abs(tuned["pitch"] - 69.0), 0.01)

    def test_the_settings_are_decimal_text_or_whole_numbers(self) -> None:
        for settings in (note_builder.VOICE, note_builder.BASS):
            for name, value in settings.items():
                self.assertFalse(isinstance(value, float), name)  # a run's parameters encode no floats


if __name__ == "__main__":
    unittest.main()
