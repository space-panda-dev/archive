"""The span detector on synthetic audio of known structure (ADR 0066).

Every recording here is generated: chords of sines, amplitude-modulated noise
standing in for talk, and faint noise for a room's floor, written sample by
sample so each piece's edges are exact. Edges are asserted against the
pieces' own sample edges, within the tolerance each signal states.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from fractions import Fraction
from pathlib import Path
from unittest import mock

import numpy as np

import adapter
import detector
import probe
import support
from support import chord, floor_noise, needs_ffmpeg, talk
from test_probe import fake_program

A_MINOR = (220.0, 261.63, 329.63)
G_MAJOR = (196.0, 246.94, 293.66)
C_MAJOR = (261.63, 329.63, 392.0)
B_FLAT = (233.08, 293.66, 349.23)
JAM = detector.PRESETS["jam"]


def counted(path: Path) -> detector.Stream:
    """The stream's facts as the probe counts them: the detector is always
    given a real count, never one a test wrote down."""
    [facts] = probe.probe(path).streams
    return detector.Stream(facts.stream_index, facts.sample_rate, facts.channel_count, facts.sample_count)


# Talk between songs is quieter than the music: this talk's median hop level
# is about 18 dB below the session's 80th-percentile hop level.
QUIET_TALK = 0.05


def session(directory: Path, rate: int = 8000, channels: int = 1,
            talk_amplitude: float = QUIET_TALK) -> tuple[Path, list[int]]:
    """Music, talk, music, talk, a long quiet, music: the first three pairs
    separated by six-second gaps of the room's floor."""
    return support.synthetic_wav(directory / "session.wav", rate, [
        chord(40, A_MINOR), floor_noise(6), talk(40, talk_amplitude), floor_noise(6), chord(40, G_MAJOR),
        floor_noise(6), talk(40, talk_amplitude), floor_noise(72), chord(40, C_MAJOR)], channels=channels)


def nan_wav(directory: Path) -> Path:
    """A 32-bit float WAV written by ffmpeg, with one stretch of samples
    replaced by NaN, which the decoder passes through."""
    path = directory / "nan.wav"
    support.ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:sample_rate=8000:duration=40", "-c:a", "pcm_f32le",
                   str(path))
    data = bytearray(path.read_bytes())
    nan = np.array([np.nan], dtype="<f4").tobytes()
    middle = len(data) // 2 // 4 * 4
    for offset in range(middle, middle + 4 * 800, 4):
        data[offset:offset + 4] = nan
    path.write_bytes(bytes(data))
    return path


@needs_ffmpeg
class KnownStructure(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = support.scratch(self, "detect-")

    def test_gaps_talk_and_quiet_give_exact_edges_and_classes(self) -> None:
        wav, edges = session(self.tmp)
        stream = counted(wav)
        self.assertEqual(stream.sample_count, edges[-1])
        result = detector.detect(wav, stream, JAM)
        hop = result.hop_samples
        self.assertEqual(hop, 800)
        # A silence break falls on the middle hop of its gap; the quiet run
        # starts and ends where the floor does. Tolerance: one hop.
        gap_middles = [(edges[1] + edges[2]) // 2, (edges[3] + edges[4]) // 2, (edges[5] + edges[6]) // 2]
        expected = [0, *gap_middles, edges[7], edges[8], edges[9]]
        starts = [s for s, _, _ in result.spans] + [result.spans[-1][1]]
        self.assertEqual(len(starts), len(expected), result.spans)
        for got, want in zip(starts, expected):
            self.assertLessEqual(abs(got - want), hop, (got, want))
        self.assertEqual([c for _, _, c in result.spans], ["music", "talk", "music", "talk", "quiet", "music"])
        # The spans tile the stream in order, and the payload states the claim's rate.
        self.assertEqual(result.spans[0][0], 0)
        self.assertEqual(result.spans[-1][1], stream.sample_count)
        self.assertTrue(all(a[1] == b[0] for a, b in zip(result.spans, result.spans[1:])))
        self.assertEqual(result.payload()["sample_rate"], 8000)
        # Every signal contributed: silence for the gaps, the others nearby.
        self.assertGreater(result.report["points"]["silence"], 0)
        self.assertEqual(result.report["quiet_runs"], 1)

    def test_talk_as_loud_as_the_music_is_unknown(self) -> None:
        # Loud singing ranks like talk; level is the evidence the rank lacks (ADR 0091).
        wav, _ = session(self.tmp, talk_amplitude=0.2)
        stream = counted(wav)
        # Anchor: without the level gate the rank alone calls both talk-like spans talk.
        ungated = detector.detect(wav, stream, {**JAM, "talk_margin_db": 0, "talk_reference_percentile": 100})
        self.assertEqual([c for _, _, c in ungated.spans], ["music", "talk", "music", "talk", "quiet", "music"])
        gated = detector.detect(wav, stream, JAM)
        self.assertEqual([s[:2] for s in gated.spans], [s[:2] for s in ungated.spans])
        self.assertEqual([c for _, _, c in gated.spans], ["music", "unknown", "music", "unknown", "quiet", "music"])

    def test_the_studio_preset_never_says_talk(self) -> None:
        wav, _ = session(self.tmp)
        stream = counted(wav)
        jam = detector.detect(wav, stream, JAM)
        studio = detector.detect(wav, stream, detector.PRESETS["studio"])
        # Anchor: the same audio under `jam` has talk.
        self.assertEqual([c for _, _, c in jam.spans].count("talk"), 2)
        self.assertEqual([s[:2] for s in studio.spans], [s[:2] for s in jam.spans])
        self.assertEqual([c for _, _, c in studio.spans], ["music", "unknown", "music", "unknown", "quiet", "music"])

    def test_a_change_of_chord_without_a_gap_is_found_by_novelty_alone(self) -> None:
        wav, edges = support.synthetic_wav(self.tmp / "two.wav", 44100, [chord(60, A_MINOR), chord(60, B_FLAT)],
                                           channels=2)
        stream = counted(wav)
        self.assertEqual((stream.sample_rate, stream.channel_count), (44100, 2))
        result = detector.detect(wav, stream, JAM)
        # Anchor: neither silence nor loudness sees this edge.
        self.assertEqual((result.report["points"]["silence"], result.report["points"]["loudness"]), (0, 0))
        [(s0, e0, _), (s1, e1, _)] = result.spans
        # A novelty peak is a block start; tolerance: one chroma block.
        self.assertLessEqual(abs(e0 - edges[1]), 44100)
        self.assertEqual((s0, s1, e1), (0, e0, edges[2]))
        self.assertEqual(e0 % result.hop_samples, 0)

    def test_digital_silence_is_one_quiet_span_and_its_flat_novelty_has_no_peaks(self) -> None:
        wav = self.tmp / "silent.wav"
        support.ffmpeg("-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", "90", "-c:a", "pcm_s16le", str(wav))
        result = detector.detect(wav, counted(wav), JAM)
        self.assertEqual(result.spans, [(0, 720000, "quiet")])
        self.assertEqual(result.report["points"]["chroma_novelty"], 0)
        self.assertEqual(result.report["floor_db"], detector.FLOOR_DB)

    def test_a_span_too_short_to_classify_is_unknown(self) -> None:
        wav, edges = support.synthetic_wav(self.tmp / "tail.wav", 8000, [
            chord(40, A_MINOR), floor_noise(6), talk(40), floor_noise(6), chord(40, G_MAJOR), floor_noise(70),
            chord(1, C_MAJOR)])
        result = detector.detect(wav, counted(wav), JAM)
        self.assertEqual(result.spans[-2][2], "quiet")
        last = result.spans[-1]
        self.assertEqual((last[0], last[1]), (edges[6], edges[7]))
        self.assertLess(last[1] - last[0], JAM["classify_min_ms"] * 8)
        self.assertEqual(last[2], "unknown")

    def test_two_runs_are_equal(self) -> None:
        wav, _ = session(self.tmp, channels=2)
        stream = counted(wav)
        first, second = detector.detect(wav, stream, JAM), detector.detect(wav, stream, JAM)
        self.assertEqual(first.as_json(), second.as_json())
        self.assertEqual(adapter.canonical(first.payload()), adapter.canonical(second.payload()))

    def test_the_show_preset_is_its_own_configuration(self) -> None:
        self.assertNotEqual(adapter.canonical(detector.parameters("jam")),
                            adapter.canonical(detector.parameters("show")))
        wav, _ = session(self.tmp)
        result = detector.detect(wav, counted(wav), detector.PRESETS["show"])
        self.assertTrue(set(c for _, _, c in result.spans) <= set(detector.CLASSES))
        self.assertEqual(result.spans[-1][1], counted(wav).sample_count)


@needs_ffmpeg
class SignalsAreNeverDropped(unittest.TestCase):
    """A signal that cannot be computed refuses the detection; the other
    signals never stand in for it."""

    def setUp(self) -> None:
        self.tmp = support.scratch(self, "detect-")

    def test_too_little_audio_for_the_novelty_kernel_is_refused(self) -> None:
        wav, _ = support.synthetic_wav(self.tmp / "short.wav", 8000, [chord(12, A_MINOR)])
        stream = counted(wav)
        hops = detector.measure(wav, stream, JAM["hop_ms"], JAM["chroma_min_hz"], JAM["chroma_max_hz"])
        # Anchor: silence and loudness are computable on this audio.
        detector.silence_breaks(hops.level_db, JAM)
        detector.loudness_transitions(hops, JAM)
        with self.assertRaises(detector.SignalError) as raised:
            detector.detect(wav, stream, JAM)
        self.assertEqual(raised.exception.signal, "chroma_novelty")
        self.assertIn("fills only 12", str(raised.exception))

    def test_a_rate_too_low_to_bin_every_pitch_class_is_refused(self) -> None:
        # At 200 Hz the FFT of a 20-sample hop has bins only up to 100 Hz,
        # which miss four pitch classes: a chroma that cannot see them is not
        # computed, and its flat curve never stands in for "no change".
        wav, _ = support.synthetic_wav(self.tmp / "low.wav", 200, [floor_noise(60, amplitude=0.1)])
        stream = counted(wav)
        self.assertEqual((stream.sample_rate, stream.sample_count), (200, 12000))
        with self.assertRaises(detector.SignalError) as raised:
            detector.detect(wav, stream, JAM)
        self.assertEqual(raised.exception.signal, "chroma_novelty")
        self.assertIn("pitch classes [0, 3, 8, 10]", str(raised.exception))
        # Anchor: at 1 kHz every class has a bin and the same audio is detected.
        wav, _ = support.synthetic_wav(self.tmp / "khz.wav", 1000, [floor_noise(60, amplitude=0.1)])
        detector.detect(wav, counted(wav), JAM)

    def test_too_little_audio_for_two_loudness_windows_is_refused(self) -> None:
        wav, _ = support.synthetic_wav(self.tmp / "tiny.wav", 8000, [chord(5, A_MINOR)])
        with self.assertRaises(detector.SignalError) as raised:
            detector.detect(wav, counted(wav), JAM)
        self.assertEqual(raised.exception.signal, "loudness")

    def test_samples_that_are_not_finite_are_refused(self) -> None:
        clean = self.tmp / "clean.wav"
        support.ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:sample_rate=8000:duration=40", "-c:a", "pcm_f32le",
                       str(clean))
        # Anchor: the same audio without NaN is detected.
        detector.detect(clean, counted(clean), JAM)
        wav = nan_wav(self.tmp)
        with self.assertRaises(detector.SignalError) as raised:
            detector.detect(wav, counted(wav), JAM)
        self.assertIn("not finite", str(raised.exception))

    def test_every_signal_refuses_values_that_are_not_finite_on_its_own(self) -> None:
        # detect() stops at the first signal, so each signal's own check is
        # reached only by calling it: removing any one would otherwise pass.
        wav = nan_wav(self.tmp)
        hops = detector.measure(wav, counted(wav), JAM["hop_ms"], JAM["chroma_min_hz"], JAM["chroma_max_hz"])
        # Anchor: the measurement carries the NaN into every per-hop value.
        self.assertFalse(np.all(np.isfinite(hops.energy)))
        self.assertFalse(np.all(np.isfinite(hops.chroma)))
        signals = {"silence": lambda: detector.silence_breaks(hops.level_db, JAM),
                   "loudness": lambda: detector.loudness_transitions(hops, JAM),
                   "chroma_novelty": lambda: detector.chroma_novelty(hops, JAM),
                   "quiet": lambda: detector.quiet_runs(hops.level_db, JAM)}
        for name, compute in signals.items():
            with self.subTest(signal=name), self.assertRaises(detector.SignalError) as raised:
                compute()
            self.assertEqual(raised.exception.signal, name)
            self.assertIn("not finite", str(raised.exception))

    def test_a_signal_failure_stops_the_others(self) -> None:
        wav, _ = session(self.tmp)
        failing = mock.patch.object(detector, "loudness_transitions",
                                    side_effect=detector.SignalError("loudness", "test"))
        bomb = mock.patch.object(detector, "chroma_novelty", side_effect=AssertionError("ran after a failure"))
        with failing, bomb, self.assertRaises(detector.SignalError):
            detector.detect(wav, counted(wav), JAM)


@needs_ffmpeg
class StreamFacts(unittest.TestCase):
    """The spans are about the pinned stream claim's samples, or nothing."""

    def setUp(self) -> None:
        self.tmp = support.scratch(self, "detect-")
        self.wav, _ = support.synthetic_wav(self.tmp / "a.wav", 8000, [chord(30, A_MINOR)])
        self.stream = counted(self.wav)

    def test_a_count_that_differs_from_the_claim_is_refused(self) -> None:
        wrong = detector.Stream(0, 8000, 1, self.stream.sample_count + 1)
        with self.assertRaisesRegex(detector.DetectorError, "stream claim counts"):
            detector.detect(self.wav, wrong, JAM)

    def test_a_channel_count_that_differs_from_the_claim_is_refused(self) -> None:
        odd, _ = support.synthetic_wav(self.tmp / "odd.wav", 8000, [chord(30.000125, A_MINOR)])
        self.assertEqual(counted(odd).sample_count % 2, 1)
        with self.assertRaisesRegex(detector.DetectorError, "whole 2-channel frames"):
            detector.detect(odd, detector.Stream(0, 8000, 2, counted(odd).sample_count), JAM)
        with self.assertRaisesRegex(detector.DetectorError, "stream claim counts"):
            detector.detect(self.wav, detector.Stream(0, 8000, 2, self.stream.sample_count), JAM)

    def test_every_sample_is_in_exactly_one_hop_including_a_short_last_one(self) -> None:
        odd, _ = support.synthetic_wav(self.tmp / "odd.wav", 8000, [chord(30.000125, A_MINOR)])
        stream = counted(odd)
        self.assertEqual(stream.sample_count, 240001)
        # Read one hop at a time too, so the short hop is the only one in its read.
        for frames in (detector.FRAMES_PER_READ, 1):
            with self.subTest(frames=frames), mock.patch.object(detector, "FRAMES_PER_READ", frames):
                hops = detector.measure(odd, stream, JAM["hop_ms"], JAM["chroma_min_hz"], JAM["chroma_max_hz"])
                self.assertEqual((hops.size, hops.count), (800, 240001))
                self.assertEqual(len(hops.lengths), 301)
                self.assertEqual(hops.lengths[-1], 1)
                self.assertEqual(int(hops.lengths.sum()), stream.sample_count)
                self.assertEqual((len(hops.energy), len(hops.chroma), len(hops.flatness)), (301, 301, 301))

    def test_a_stream_that_decodes_to_nothing_is_refused(self) -> None:
        empty = support.empty_wav(self.tmp / "empty.wav")
        with self.assertRaisesRegex(detector.DetectorError, "no samples"):
            detector.detect(empty, detector.Stream(0, 8000, 1, 0), JAM)

    def test_a_decoder_that_fails_or_is_missing_is_refused(self) -> None:
        failing = fake_program(self.tmp, "ffmpeg", "echo broken >&2; exit 3\n")
        with self.assertRaisesRegex(detector.DetectorError, "exited 3.*broken"):
            detector.detect(self.wav, self.stream, JAM, ffmpeg=failing)
        with self.assertRaisesRegex(detector.DetectorError, "could not run"):
            detector.detect(self.wav, self.stream, JAM, ffmpeg=str(self.tmp / "no-such-ffmpeg"))

    def test_a_missing_file_is_refused(self) -> None:
        with self.assertRaisesRegex(detector.DetectorError, "not a file"):
            detector.detect(self.tmp / "absent.wav", self.stream, JAM)

    def test_more_spans_than_a_claim_holds_are_refused(self) -> None:
        wav, _ = session(self.tmp)
        stream = counted(wav)
        self.assertEqual(len(detector.detect(wav, stream, JAM).spans), 6)
        with mock.patch.object(detector, "MAX_SPANS", 5), \
                self.assertRaisesRegex(detector.DetectorError, "6 spans exceed the 5"):
            detector.detect(wav, stream, JAM)


@needs_ffmpeg
class StandaloneCommand(unittest.TestCase):
    """`detector.py <file>` probes, detects, and prints; no archive is involved."""

    def setUp(self) -> None:
        self.tmp = support.scratch(self, "detect-")

    def run_tool(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(Path(detector.__file__)), *arguments, "--ffmpeg", support.FFMPEG,
                               "--ffprobe", support.FFPROBE], capture_output=True, text=True)

    def test_a_file_is_probed_and_its_spans_printed(self) -> None:
        wav, edges = session(self.tmp)
        done = self.run_tool(str(wav), "--preset", "show")
        self.assertEqual(done.returncode, 0, done.stderr)
        printed = json.loads(done.stdout)
        self.assertEqual(printed, detector.detect(wav, counted(wav), detector.PRESETS["show"]).as_json())
        self.assertEqual((printed["sample_count"], printed["spans"][-1]["end_sample"]), (edges[-1], edges[-1]))

    def test_a_stream_that_is_not_counted_and_a_failed_detection_are_refused(self) -> None:
        wav, _ = support.synthetic_wav(self.tmp / "short.wav", 8000, [chord(12, A_MINOR)])
        done = self.run_tool(str(wav), "--stream-index", "1")
        self.assertEqual(done.returncode, 1)
        self.assertIn("stream 1 is not an exactly counted audio stream", json.loads(done.stderr)["detail"])
        done = self.run_tool(str(wav))
        self.assertEqual((done.returncode, done.stdout), (1, ""))
        self.assertIn("chroma_novelty signal cannot be computed", json.loads(done.stderr)["detail"])


class Rules(unittest.TestCase):
    def test_a_lone_value_has_no_rank_and_equal_values_rank_in_order(self) -> None:
        self.assertEqual(detector.percentile_ranks([0.5]), [None])
        self.assertEqual(detector.percentile_ranks([0.2, None, 0.2, 0.1]),
                         [Fraction(1, 2), None, Fraction(1), Fraction(0)])

    def test_classes_follow_the_mean_rank_with_its_band(self) -> None:
        # Key confidence ranks up, flatness ranks down: the third span is the most tonal.
        # Every level is far below the reference, so only the rank decides.
        features = [(0.1, 0.9, -60.0), (0.5, 0.5, -60.0), (0.9, 0.1, -60.0), (0.4, 0.55, -60.0), (None, None, None)]
        self.assertEqual(detector.classify(features, -10.0, JAM), ["talk", "music", "music", "unknown", "unknown"])
        self.assertEqual(detector.classify([(0.3, 0.3, -60.0)], -10.0, JAM), ["unknown"])

    def test_the_rank_only_nominates_talk_and_the_level_gate_is_strict(self) -> None:
        # The first span ranks lowest; the gate is `level < reference - margin`.
        margin = JAM["talk_margin_db"]
        def first(level: float, preset: dict[str, int] = JAM) -> str:
            features = [(0.1, 0.9, level), (0.5, 0.5, -60.0), (0.9, 0.1, -60.0)]
            return detector.classify(features, -10.0, preset)[0]
        self.assertEqual(first(-10.0 - margin - 0.001), "talk")
        self.assertEqual(first(-10.0 - margin), "unknown")
        self.assertEqual(first(-10.0), "unknown")
        self.assertEqual(first(-60.0, detector.PRESETS["studio"]), "unknown")
        self.assertFalse(detector.is_talk_level(None, -10.0, JAM))

    def test_the_band_edges_are_inclusive_and_exact(self) -> None:
        # Twenty-one key confidences rank 0, 1/20, ..., 1; without flatness the
        # mean rank is the key rank, so ranks 6/20, 7/20 and 8/20 sit exactly
        # on the talk edge, inside the band, and on the music edge.
        features = [(float(index), None, -60.0) for index in range(21)]
        classes = detector.classify(features, -10.0, JAM)
        self.assertEqual(classes[:7], ["talk"] * 7)
        self.assertEqual(classes[7], "unknown")
        self.assertEqual(classes[8:], ["music"] * 13)

    def test_an_edge_too_close_to_the_previous_one_or_the_end_is_dropped(self) -> None:
        def region(hop: int) -> list[detector.Point]:
            return [detector.Point(hop, "silence", 1.0)]
        # A minimum of 10 hops in a stream of 100: 5 is too close to the start,
        # 12 is kept, 20 is too close to 12, 30 is kept, 95 is too close to the end.
        self.assertEqual(detector.boundaries([region(h) for h in (5, 12, 20, 30, 95)], 10, 100), [12, 30])
        self.assertEqual(detector.boundaries([region(90)], 10, 100), [90])

    def test_a_region_breaks_at_silence_before_loudness_before_novelty(self) -> None:
        region = [detector.Point(10, "chroma_novelty", 60.0), detector.Point(20, "loudness", 30.0),
                  detector.Point(30, "silence", 2.0), detector.Point(40, "silence", 5.0)]
        self.assertEqual(detector.region_edge(region), 40)
        self.assertEqual(detector.region_edge(region[:2]), 20)
        self.assertEqual(detector.region_edge(region[:1]), 10)

    def test_key_confidence_needs_pitch_energy_that_varies(self) -> None:
        self.assertIsNone(detector.key_confidence(np.zeros((4, 12))))
        self.assertIsNone(detector.key_confidence(np.ones((4, 12))))
        tonic = np.zeros((1, 12))
        tonic[0, [0, 4, 7]] = 1.0
        self.assertGreater(detector.key_confidence(tonic), 0.5)

    def test_a_flat_novelty_curve_has_no_peaks(self) -> None:
        hops = detector.Hops(size=800, count=800 * 300, energy=np.ones(300), lengths=np.full(300, 800.0),
                             chroma=np.ones((300, 12)), flatness=np.ones(300))
        self.assertEqual(detector.chroma_novelty(hops, JAM), [])

    def test_the_kernel_is_signed_by_quadrant_and_balanced(self) -> None:
        kernel = detector.checkerboard_kernel(4)
        self.assertGreater(kernel[0, 0], 0)
        self.assertLess(kernel[0, 7], 0)
        self.assertAlmostEqual(float(kernel.sum()), 0.0, places=12)


class Presets(unittest.TestCase):
    def test_parameters_are_stable_canonical_json_with_the_preset_named(self) -> None:
        first = adapter.canonical(detector.parameters("jam"))
        self.assertEqual(first, adapter.canonical(detector.parameters("jam")))
        decoded = json.loads(first)
        self.assertEqual((decoded["preset"], decoded["values"]), ("jam", JAM))
        self.assertIn("{stream_index}", decoded["decode_arguments"][8])

    def test_studio_is_jam_without_talk(self) -> None:
        studio = detector.parameters("studio")["values"]
        self.assertEqual({key for key in studio if studio[key] != JAM[key]}, {"assign_talk"})
        self.assertEqual((JAM["assign_talk"], studio["assign_talk"]), (1, 0))

    def test_an_unknown_preset_is_refused(self) -> None:
        with self.assertRaisesRegex(detector.DetectorError, "unknown preset"):
            detector.parameters("strict-silence")

    def test_values_the_rules_cannot_use_are_refused(self) -> None:
        cases = {"hop_ms": 0, "window_ms": 10050, "max_review_points": 0, "chroma_peak_percentile": 101,
                 "chroma_min_hz": 6000, "rank_band_percent": 40, "assign_talk": 2, "talk_margin_db": -1,
                 "talk_reference_percentile": 101}
        for key, value in cases.items():
            with self.subTest(key=key), self.assertRaises(detector.DetectorError):
                detector.check_preset({**JAM, key: value})
        with self.assertRaisesRegex(detector.DetectorError, "must be an integer"):
            detector.check_preset({**JAM, "quiet_margin_db": 3.0})
        with self.assertRaisesRegex(detector.DetectorError, "unknown \\['extra'\\]"):
            detector.check_preset({**JAM, "extra": 1})

    def test_a_hop_shorter_than_a_sample_is_refused(self) -> None:
        with self.assertRaisesRegex(detector.DetectorError, "shorter than one sample"):
            detector.hop_samples(5, 100)

    def test_the_agent_is_a_package_url_door_accepts(self) -> None:
        self.assertEqual(adapter.check_package_url(detector.AGENT), detector.AGENT)


@needs_ffmpeg
class Software(unittest.TestCase):
    def test_the_decoder_its_libraries_and_numpy_are_named(self) -> None:
        names = [name for name, _ in detector.software()]
        self.assertEqual((names[0], names[-1]), ("ffmpeg", "numpy"))
        self.assertIn("libavcodec", names)
        self.assertNotIn("ffprobe", names)

    def test_a_decoder_that_reports_no_version_is_refused(self) -> None:
        tmp = support.scratch(self, "detect-")
        with self.assertRaisesRegex(detector.DetectorError, "did not report a version"):
            detector.software(fake_program(tmp, "ffmpeg", "echo hello\n"))


if __name__ == "__main__":
    unittest.main()
