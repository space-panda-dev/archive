"""Controlled contour, event and rendering checks, without model downloads."""
from __future__ import annotations

import json
import unittest

import numpy as np

import vocal_contour as contour


def track(pitches: list[float | None]) -> tuple[np.ndarray, ...]:
    midi = np.array([0 if p is None else p for p in pitches], dtype=np.float64)
    voice = np.array([p is not None for p in pitches])
    hz = np.where(voice, 440 * 2 ** ((midi - 69) / 12), 0)
    frequency = np.repeat(hz, 160)
    phase = np.cumsum(2 * np.pi * frequency / 16000)
    audio = 0.1 * np.sin(phase) * np.repeat(voice, 160)
    rms = np.sqrt(np.mean(audio.reshape(-1, 160) ** 2, axis=1)) if len(audio) else np.zeros(0)
    db = 20 * np.log10(np.maximum(rms, 1e-6))
    return audio, hz, voice.astype(float) * 0.99, db


class VocalContour(unittest.TestCase):
    def test_empty_is_silent_with_no_events(self) -> None:
        result = contour.analyze(*track([]), np.zeros(0))
        self.assertEqual(result["notes"], [])
        json.dumps(result["note_reading"], allow_nan=False)
        self.assertEqual(contour.render([], []).size, 0)

    def test_explicit_reattack_splits_same_pitch_without_changing_the_contour(self) -> None:
        args = track([57] * 200)
        result = contour.analyze(*args, args[1], [1.0])
        self.assertEqual(result["attacks"]["selected_times_s"], [1.0])
        self.assertEqual([n["pitch"] for n in result["notes"]], [57, 57])
        self.assertEqual(result["notes"][0]["end"], result["notes"][1]["start"])
        dry = contour.render(result["frames"]["render_midi"], result["frames"]["voiced"])
        wet = contour.render(result["frames"]["render_midi"], result["frames"]["voiced"], [1.0])
        self.assertEqual(wet[16000], 0)
        np.testing.assert_array_equal(dry[:15808], wet[:15808])
        np.testing.assert_array_equal(dry[16193:], wet[16193:])

    def test_voicing_gaps_and_true_octaves_are_not_repaired_or_merged(self) -> None:
        values = [57] * 50 + [None] * 8 + [69] * 50 + [None] * 8 + [57] * 50
        args = track(values)
        result = contour.analyze(*args, args[1])
        self.assertEqual([n["pitch"] for n in result["notes"]], [57, 69, 57])
        frames = result["frames"]
        pcm = contour.render(frames["render_midi"], frames["voiced"])
        self.assertTrue(np.all(pcm[8000:9280] == 0))
        self.assertTrue(np.all(pcm[17280:18560] == 0))
        self.assertLess(result["notes"][0]["end"], result["notes"][1]["start"])

    def test_scoop_is_one_landing_and_its_motion_stays_in_the_contour(self) -> None:
        values = np.r_[np.linspace(60, 64, 30), np.full(70, 64)].tolist()
        args = track(values)
        result = contour.analyze(*args, args[1])
        self.assertEqual([n["pitch"] for n in result["notes"]], [64])
        self.assertLess(result["frames"]["new_render_midi"][0], 61)
        self.assertGreater(result["frames"]["new_render_midi"][-1], 63)

    def test_vibrato_does_not_become_a_chromatic_note_ladder(self) -> None:
        pitch = 69 + 0.4 * np.sin(np.arange(200) * 0.01 * 2 * np.pi * 5.5)
        reading = contour.to_notes({"events": []}, pitch, np.ones(200, dtype=bool))
        self.assertEqual([n["pitch"] for n in reading["notes"]], [69])
        self.assertEqual((reading["notes"][0]["start"], reading["notes"][0]["end"]), (0, 2))

    def test_fast_unlanded_run_keeps_short_targets_and_covers_every_voiced_frame(self) -> None:
        pitch = np.repeat([60, 62, 64, 65, 64, 62], 6)
        reading = contour.to_notes({"events": []}, pitch, np.ones(len(pitch), dtype=bool))
        self.assertEqual([n["pitch"] for n in reading["notes"]], [60, 62, 64, 65, 64, 62])
        self.assertEqual(reading["notes"][0]["start"], 0)
        self.assertEqual(reading["notes"][-1]["end"], 0.36)
        for left, right in zip(reading["notes"], reading["notes"][1:]):
            self.assertEqual(left["end"], right["start"])

    def test_unlanded_glissando_is_uncertain_but_not_asserted_silence(self) -> None:
        pitch = np.linspace(57, 69, 50)
        reading = contour.to_notes({"events": []}, pitch, np.ones(50, dtype=bool))
        self.assertEqual(len(reading["notes"]), 1)
        self.assertEqual((reading["notes"][0]["start"], reading["notes"][0]["end"]), (0, 0.5))
        self.assertEqual(len(reading["uncertain_regions"]), 1)

    def test_secondary_pitch_only_replaces_moving_voiced_rows(self) -> None:
        g = dict(confidence=np.ones(4), render_midi=np.array([57., 58., 59., 60.]),
                 voiced=np.array([True, True, True, False]),
                 events=[dict(onset=0, landing=0.01, end=0.02, target_midi=58)])
        replacement = 440 * 2 ** ((np.full(4, 69) - 69) / 12)
        result = contour.replace_moving(g, replacement)
        np.testing.assert_array_equal(result["new_render_midi"], [69, 58, 69, 60])
        np.testing.assert_array_equal(g["render_midi"], [57, 58, 59, 60])

    def test_qualifier_rejects_gaps_transitions_and_nonfinite_times(self) -> None:
        pitch = np.r_[np.full(100, 60), np.full(100, 64)]
        voice = np.ones(200, bool)
        voice[50] = False
        result = contour.qualify_attacks([0.01, 0.5, 1.0, 1.5], pitch, voice)
        self.assertEqual(result["selected_times_s"], [1.5])
        with self.assertRaises(ValueError):
            contour.qualify_attacks([float("nan")], pitch, voice)

    def test_short_tracks_are_valid_and_notes_are_json_primitives(self) -> None:
        for count in (1, 2, 20):
            args = track([60] * count)
            result = contour.analyze(*args, args[1])
            json.dumps(result["note_reading"], allow_nan=False)
            self.assertAlmostEqual(sum(n["end"] - n["start"] for n in result["notes"]), count * .01)

    def test_invalid_grid_fails_instead_of_silently_truncating(self) -> None:
        args = track([60] * 20)
        with self.assertRaises(ValueError):
            contour.segment(args[1][:-1], args[2], args[3], args[0])
        with self.assertRaises(ValueError):
            contour.segment(args[1], args[2], args[3], args[0][:160])


if __name__ == "__main__":
    unittest.main()
