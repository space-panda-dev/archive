"""The quality extractor, run by the real runner on audio ffmpeg encodes (ADR 0074).

Each recording is made by numpy, so that a defect sits at a known sample,
and encoded by ffmpeg, the writer real recordings come from. Every test of a
measure first checks that the recording holds what the measure should find,
or should not.
"""

from __future__ import annotations

import io
import json
import math
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import numpy

import bank
import extract
import support
from support import needs_ffmpeg
from test_cover import needs_essentia

sys.path.insert(0, str(Path(__file__).resolve().parent / "extractors" / "quality"))
import quality  # noqa: E402

RATE = 48000


def scratch(test: unittest.TestCase) -> Path:
    directory = Path(tempfile.mkdtemp(prefix="bank-quality-"))
    test.addCleanup(shutil.rmtree, directory, True)
    return directory


def encode(path: Path, samples: numpy.ndarray, rate: int = RATE, codec: str = "pcm_s16le") -> Path:
    """Samples [frames, channels] in -1..1, encoded by ffmpeg."""
    samples = numpy.asarray(samples, dtype="<f4")
    if samples.ndim == 1:
        samples = samples[:, None]
    completed = subprocess.run([support.FFMPEG or "ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "f32le",
                                "-ar", str(rate), "-ac", str(samples.shape[1]), "-i", "-", "-c:a", codec,
                                str(path)], input=samples.tobytes(), capture_output=True)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.decode())
    return path


def tone(seconds: float, frequency: float = 1000.0, amplitude: float = 0.3, rate: int = RATE,
         phase: float = 0.0) -> numpy.ndarray:
    return amplitude * numpy.sin(2 * numpy.pi * frequency * numpy.arange(round(seconds * rate)) / rate + phase)


def noise(seconds: float, amplitude: float, seed: int, rate: int = RATE) -> numpy.ndarray:
    return amplitude * numpy.random.default_rng(seed).standard_normal(round(seconds * rate))


def run_quality(directory: Path, audio: Path, *extra: str, target: str = "quality") -> tuple[int, dict]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = extract.main(["run", target, "--content", str(audio), "--bank", str(directory / "bank"), *extra])
    return code, json.loads(out.getvalue() or err.getvalue())


def measured(test: unittest.TestCase, directory: Path, audio: Path) -> bank.Entry:
    code, answer = run_quality(directory, audio)
    test.assertEqual(code, 0, answer)
    entry = bank.Bank(directory / "bank").entry(bank.file_digest(audio), "quality")
    test.assertIsNotNone(entry)
    return entry


def decoded(path: Path) -> tuple[numpy.ndarray, int]:
    rate, channels = quality.probe(str(path))
    data = subprocess.run([support.FFMPEG or "ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-"],
                          capture_output=True, check=True).stdout
    return numpy.frombuffer(data, dtype="<f4").reshape(-1, channels), rate


class Manifest(unittest.TestCase):
    def test_the_manifest_declares_exactly_what_the_program_implements(self) -> None:
        extractor = bank.Catalog.load()["quality"]
        self.assertEqual(extractor.manifest["parameters"], quality.PARAMETERS)
        self.assertEqual(set(extractor.outputs), set(quality.SCALARS) | set(quality.LISTS) | set(quality.SERIES))
        self.assertEqual(extractor.needs, ["frames/samples"])
        for name in quality.SERIES:
            self.assertEqual(extractor.outputs[name]["grid"], "frames")
        for name in quality.LISTS:
            self.assertIn(extractor.outputs[name]["kind"], ("events", "spans"))
        # The grid is the one frames writes, so the series line up row for row.
        frames = bank.Catalog.load()["frames"].manifest["parameters"]
        self.assertEqual((quality.GRID["sample_rate"], quality.GRID["hop"], quality.GRID["frame"]),
                         (frames["analysis_rate"], frames["hop_size"], frames["frame_size"]))


class Bandwidth(unittest.TestCase):
    """The edge detector, on spectra whose answer is known."""

    def spectrum(self, cutoff: float | None, floor_db: float = -140.0, rate: int = 44100) -> numpy.ndarray:
        frequencies = numpy.arange(quality.PARAMETERS["spectrum_frame"] // 2 + 1) * rate / 4096
        levels = numpy.full(len(frequencies), -40.0) - 10 * numpy.log10(1 + frequencies / 1000)
        if cutoff is not None:
            levels[frequencies > cutoff] = floor_db
        return 10 ** (levels / 10)

    def test_an_edge_is_found_where_it_is(self) -> None:
        for cutoff in (8000.0, 11025.0, 16000.0, 19500.0):
            with self.subTest(cutoff=cutoff):
                found = quality.effective_bandwidth(self.spectrum(cutoff), 44100, 4096)
                self.assertLess(abs(found - cutoff), 44100 / 4096 * 2)

    def test_a_spectrum_that_falls_gradually_reaches_nyquist(self) -> None:
        self.assertEqual(quality.effective_bandwidth(self.spectrum(None), 44100, 4096), 22050.0)

    def test_a_tone_above_a_noise_floor_is_not_an_edge(self) -> None:
        power = numpy.full(2049, 1e-9)
        power[20] = 1.0  # a loud low tone, 100 dB above the floor
        self.assertEqual(quality.effective_bandwidth(power, 48000, 4096), 24000.0)

    def test_a_floor_that_rises_again_is_not_an_edge(self) -> None:
        power = self.spectrum(10000.0)
        frequencies = numpy.arange(len(power)) * 44100 / 4096
        power[(frequencies > 15000) & (frequencies < 17000)] = 1e-5  # content returns above the drop
        self.assertGreater(quality.effective_bandwidth(power, 44100, 4096), 15000)

    def test_no_power_is_no_bandwidth(self) -> None:
        self.assertEqual(quality.effective_bandwidth(numpy.zeros(2049), 44100, 4096), 0.0)

    def test_a_spectrum_too_short_to_search_is_refused_not_full_band(self) -> None:
        self.assertEqual(quality.effective_bandwidth(self.spectrum(None, rate=8000), 8000, 4096), 4000.0)  # anchor
        with self.assertRaisesRegex(quality.QualityError, "too short"):
            quality.effective_bandwidth(self.spectrum(None, rate=4000), 4000, 4096)


class DetectorFrames(unittest.TestCase):
    def test_the_frame_keeps_its_duration_at_higher_rates(self) -> None:
        found = {rate: quality.detector_frame(rate) for rate in (8000, 22050, 44100, 48000, 64000, 88200, 96000,
                                                                 176400, 192000)}
        self.assertEqual(found, {8000: (512, 256), 22050: (512, 256), 44100: (512, 256), 48000: (512, 256),
                                 64000: (512, 256), 88200: (1024, 512), 96000: (1024, 512),
                                 176400: (2048, 1024), 192000: (2048, 1024)})

    @needs_essentia
    def test_a_burst_below_the_floor_is_quantisation_and_dropped(self) -> None:
        # A -40 dBFS tone in 16 bits at 96 kHz, in the 512-sample frames the
        # settings were chosen with at 48 kHz: the detector itself reports
        # bursts that are a step or two of quantisation.
        es = quality.essentia_module()
        rate = 96000
        quiet = numpy.round(tone(1, 440, 0.01, rate) * 32768).astype(numpy.float32) / 32768
        detector = es.NoiseBurstDetector(**quality._settings(quality.PARAMETERS["noise_bursts"]))
        channel = quality.Channel(es, rate)
        raw = 0
        for start in range(0, len(quiet) - 512, 256):
            frame = numpy.ascontiguousarray(quiet[start:start + 512])
            raw += len(detector(frame))
            channel.detect(frame, start)
        self.assertGreater(raw, 10)  # anchor: the detector does report them
        self.assertEqual(channel.bursts, [])
        # A real burst in the same passage is kept.
        loud = quiet.copy()
        loud[48000:48200] += noise(200 / rate, 0.05, 3, rate).astype(numpy.float32)
        channel = quality.Channel(es, rate)
        for start in range(0, len(loud) - 512, 256):
            channel.detect(numpy.ascontiguousarray(loud[start:start + 512]), start)
        self.assertTrue(channel.bursts)
        self.assertTrue(all(47900 <= start and end <= 48300 for start, end, _ in channel.bursts), channel.bursts)


class Summary(unittest.TestCase):
    """ffmpeg's R 128 summary, as ffmpeg prints it."""

    def log_of(self, samples: numpy.ndarray) -> str:
        directory = scratch(self)
        path = encode(directory / "x.wav", samples)
        completed = subprocess.run([support.FFMPEG or "ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-i",
                                    str(path), "-af", "ebur128=peak=true:framelog=quiet", "-f", "null", "-"],
                                   capture_output=True, text=True)
        return completed.stderr

    @needs_ffmpeg
    def test_reads_the_summary(self) -> None:
        found = quality.r128_summary(self.log_of(numpy.stack([tone(4), tone(4)], axis=1)))
        self.assertEqual(set(found), {"integrated", "loudness_range", "true_peak"})
        self.assertAlmostEqual(found["true_peak"], 20 * math.log10(0.3), delta=0.2)

    @needs_ffmpeg
    def test_silence_has_a_floored_true_peak(self) -> None:
        log = self.log_of(numpy.zeros((RATE, 2)))
        self.assertIn("-inf dBFS", log)  # what ffmpeg prints, which JSON cannot hold
        found = quality.r128_summary(log)
        self.assertEqual(found["true_peak"], quality.FLOOR_DB)
        self.assertEqual(found["integrated"], -70.0)

    def test_a_log_without_a_summary_or_a_value_is_refused(self) -> None:
        with self.assertRaisesRegex(quality.QualityError, "no summary"):
            quality.r128_summary("ffmpeg ended early\n")
        partial = "Summary:\n\n  Integrated loudness:\n    I:  -20.0 LUFS\n\n  Loudness range:\n    LRA: 1.0 LU\n"
        with self.assertRaisesRegex(quality.QualityError, "true_peak"):
            quality.r128_summary(partial)


@needs_ffmpeg
class Failures(unittest.TestCase):
    """What the program refuses, each on its own."""

    def test_a_file_without_audio_is_refused(self) -> None:
        directory = scratch(self)
        text = directory / "notes.wav"
        text.write_text("not audio")
        with self.assertRaisesRegex(quality.QualityError, "no audio stream"):
            quality.probe(str(text))
        with self.assertRaisesRegex(quality.QualityError, "could not run"):
            quality.probe(str(text), ffprobe=str(directory / "no-ffprobe"))

    def test_a_decode_that_fails_is_refused(self) -> None:
        directory = scratch(self)
        text = directory / "notes.wav"
        text.write_text("not audio")
        with self.assertRaisesRegex(quality.QualityError, "could not decode"):
            list(quality.Decoder(str(text), 48000, 2))
        with self.assertRaisesRegex(quality.QualityError, "could not run"):
            list(quality.Decoder(str(text), 48000, 2, ffmpeg=str(directory / "no-ffmpeg")))

    def test_frames_samples_must_be_a_positive_count(self) -> None:
        for value in (0, -3, 1.5, "10", None, True, False):
            with self.subTest(value=value):
                with self.assertRaisesRegex(quality.QualityError, "positive count"):
                    quality.frames_rows({"needs": {"frames/samples": {"value": value}}})

    def test_an_ffmpeg_that_names_no_version_is_unknown(self) -> None:
        directory = scratch(self)
        program = directory / "ffmpeg"
        program.write_text("#!/bin/sh\necho 'a build that says something else'\n")
        program.chmod(0o755)
        self.assertEqual(quality.ffmpeg_version(str(program)), "unknown")
        self.assertNotEqual(quality.ffmpeg_version(support.FFMPEG or "ffmpeg"), "unknown")  # anchor

    @needs_essentia
    def test_a_frames_entry_of_other_audio_is_refused(self) -> None:
        directory = scratch(self)
        audio = encode(directory / "x.wav", tone(2))
        (directory / "out").mkdir()
        request = {"output_dir": str(directory / "out"), "input": {"path": str(audio)},
                   "needs": {"frames/samples": {"value": 3 * 22050}}}
        with self.assertRaisesRegex(quality.QualityError, "other audio"):
            quality.run(request, quality.essentia_module())
        request["needs"]["frames/samples"]["value"] = 2 * 22050 + 2049  # a hop and a sample more
        with self.assertRaisesRegex(quality.QualityError, "other audio"):
            quality.run(request, quality.essentia_module())
        request["needs"]["frames/samples"]["value"] = 2 * 22050  # the anchor: the right count is accepted
        self.assertEqual(quality.run(request, quality.essentia_module())["outputs"]["seconds"], {"value": 2.0})

    @needs_essentia
    def test_audio_with_no_samples_is_refused(self) -> None:
        directory = scratch(self)
        (directory / "out").mkdir()
        request = {"output_dir": str(directory / "out"),
                   "input": {"path": str(support.empty_wav(directory / "empty.wav"))},
                   "needs": {"frames/samples": {"value": 1}}}
        with self.assertRaisesRegex(quality.QualityError, "no samples"):
            quality.run(request, quality.essentia_module())

    def test_another_essentia_build_is_refused(self) -> None:
        with patch.object(quality.importlib.metadata, "version", return_value="2.1b5"):
            with self.assertRaisesRegex(quality.QualityError, "needs Essentia"):
                quality.essentia_module()

    def test_the_program_reports_a_refusal_and_exits_1(self) -> None:
        directory = scratch(self)
        request = directory / "request.json"
        request.write_text(json.dumps({"parameters": {}, "output_dir": str(directory)}))
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(quality.main([str(request)]), 1)
        self.assertIn("fixed settings", err.getvalue())
        with redirect_stderr(io.StringIO()):
            self.assertEqual(quality.main([]), 2)


@needs_ffmpeg
@needs_essentia
class Runner(unittest.TestCase):
    """Through the real runner: settings, dependencies, the cache."""

    def test_other_settings_are_refused_not_measured(self) -> None:
        directory = scratch(self)
        audio = encode(directory / "x.wav", tone(1))
        config = directory / "config.json"
        config.write_text(json.dumps({"parameters": {"quality": {"bandwidth_drop_db": 30}}}))
        code, answer = run_quality(directory, audio, "--config", str(config))
        self.assertEqual(code, 1)
        self.assertEqual(answer["extractors"]["frames"]["status"], "ran")
        self.assertIn("fixed settings", answer["extractors"]["quality"]["log"])

    def test_is_blocked_when_frames_fails(self) -> None:
        directory = scratch(self)
        code, answer = run_quality(directory, support.empty_wav(directory / "empty.wav"))
        self.assertEqual(code, 1)
        self.assertEqual(answer["extractors"]["frames"]["status"], "failed")
        self.assertEqual(answer["extractors"]["quality"]["status"], "blocked")

    def test_a_second_run_is_a_hit_and_starts_nothing(self) -> None:
        directory = scratch(self)
        audio = encode(directory / "x.wav", numpy.stack([tone(2), tone(2, phase=1)], axis=1))
        measured(self, directory, audio)
        with patch.object(extract.subprocess, "Popen", side_effect=AssertionError("ran")):
            code, answer = run_quality(directory, audio)
        self.assertEqual(code, 0)
        self.assertEqual({e: v["status"] for e, v in answer["extractors"].items()},
                         {"frames": "hit", "quality": "hit"})


@needs_ffmpeg
@needs_essentia
class Measures(unittest.TestCase):
    def test_a_clean_recording_has_no_defects_and_its_loudness_agrees(self) -> None:
        directory = scratch(self)
        left = tone(25, 1000, 0.3) + noise(25, 0.001, 1)
        right = tone(25, 1000, 0.3, phase=0.5) + noise(25, 0.001, 2)
        audio = encode(directory / "clean.flac", numpy.stack([left, right], axis=1), codec="flac")
        entry = measured(self, directory, audio)
        for name in quality.LISTS:
            self.assertEqual(entry.items(name), [], name)
        self.assertTrue(entry.scalar("hum_examined"))  # so the empty hum list means none was found
        self.assertEqual((entry.scalar("sample_rate"), entry.scalar("channels"), entry.scalar("seconds")),
                         (RATE, 2, 25.0))
        self.assertAlmostEqual(entry.scalar("sample_peak"), 20 * math.log10(0.3), delta=0.2)  # with the noise
        self.assertAlmostEqual(entry.scalar("true_peak"), entry.scalar("sample_peak"), delta=0.2)
        self.assertEqual(entry.scalar("full_scale_samples"), 0)
        self.assertEqual(entry.scalar("clipped_seconds"), 0)
        self.assertLess(abs(entry.scalar("dc_offset")), 1e-4)
        self.assertEqual(entry.scalar("bandwidth"), RATE / 2)  # the noise reaches Nyquist
        self.assertFalse(entry.scalar("band_limited"))
        # A steady tone: one level throughout, so the level ratio is about 0.
        self.assertLess(entry.scalar("snr"), 1.0)
        self.assertAlmostEqual(entry.scalar("loudness_range"), 0.0, delta=0.2)
        # Integrated loudness is the loudness-r128 extractor's, and Essentia's.
        code, answer = run_quality(directory, audio, target="loudness-r128")
        self.assertEqual(code, 0, answer)
        r128 = bank.Bank(directory / "bank").entry(bank.file_digest(audio), "loudness-r128")
        self.assertEqual(entry.scalar("integrated"), r128.scalar("integrated"))
        samples, rate = decoded(audio)
        essentia = quality.essentia_module().LoudnessEBUR128(sampleRate=rate)(samples)
        self.assertAlmostEqual(entry.scalar("integrated"), float(essentia[2]), delta=0.15)

    def test_loudness_range_agrees_with_essentia(self) -> None:
        directory = scratch(self)
        quiet, loud = tone(15, 500, 0.05), tone(15, 500, 0.3)
        signal = numpy.concatenate([quiet, loud])
        audio = encode(directory / "steps.wav", numpy.stack([signal, signal], axis=1))
        entry = measured(self, directory, audio)
        samples, rate = decoded(audio)
        _, _, integrated, loudness_range = quality.essentia_module().LoudnessEBUR128(sampleRate=rate)(samples)
        self.assertGreater(float(loudness_range), 10)  # anchor: the steps make a range
        self.assertAlmostEqual(entry.scalar("loudness_range"), float(loudness_range), delta=0.6)
        self.assertAlmostEqual(entry.scalar("integrated"), float(integrated), delta=0.15)
        self.assertGreater(entry.scalar("snr"), 12)  # 15.6 dB between the steps

    def test_each_defect_is_found_where_it_is_and_in_its_channel(self) -> None:
        directory = scratch(self)
        for rate in (44100, 48000, 96000):
            with self.subTest(rate=rate):
                self.defects_are_found(directory, rate)

    def defects_are_found(self, directory: Path, rate: int) -> None:
        seconds = 8
        left = tone(seconds, 440, 0.3, rate)
        right = tone(seconds, 440, 0.3, rate, phase=0.7)
        quiet = slice(round(0.3 * rate), round(1.95 * rate))
        left[quiet] *= 0.01 / 0.3  # a quiet passage, -43 dBFS, where 16 bits' steps show
        right[quiet] *= 0.02 / 0.3
        clip = slice(2 * rate, 3 * rate)
        left[clip] = numpy.clip(4 * left[clip], -1.0, 1.0)  # flat tops at full scale, left only
        click = round(4.5 * rate)
        left[click] += 0.6  # one sample, left only
        jump = 6 * rate
        right[jump:] = tone(seconds, 440, 0.3, rate, phase=2.7)[jump:]  # a jump in phase, right only
        gap = slice(round(7.0 * rate), round(7.1 * rate))
        left[gap] = right[gap] = 0  # a dropout in both
        audio = encode(directory / f"defects-{rate}.wav", numpy.stack([left, right], axis=1), rate)
        entry = measured(self, directory, audio)

        clipping = entry.items("clipping")
        self.assertEqual([span["label"] for span in clipping], ["1"])
        self.assertAlmostEqual(clipping[0]["start"], 2.0, delta=0.01)
        self.assertAlmostEqual(clipping[0]["end"], 3.0, delta=0.01)
        self.assertGreater(clipping[0]["value"], 0.1)
        self.assertAlmostEqual(entry.scalar("clipped_seconds"), clipping[0]["value"], places=5)
        self.assertGreater(entry.scalar("full_scale_samples"), 1000)
        self.assertGreater(entry.scalar("sample_peak"), -0.01)

        clicks = [event for event in entry.items("clicks") if abs(event["time"] - 4.5) < 0.005]
        self.assertEqual([event["label"] for event in clicks], ["1"])
        self.assertGreater(clicks[0]["value"], 0.5)

        jumps = [event for event in entry.items("discontinuities") if abs(event["time"] - 6.0) < 0.005]
        self.assertEqual([event["label"] for event in jumps], ["2"])
        self.assertGreaterEqual(jumps[0]["value"], float(quality.PARAMETERS["discontinuity_floor"]))

        gaps = entry.items("gaps")
        self.assertEqual(sorted(span["label"] for span in gaps), ["1", "2"])
        for span in gaps:
            self.assertAlmostEqual(span["start"], 7.0, delta=0.01)
            self.assertAlmostEqual(span["end"], 7.1, delta=0.01)

        bursts = entry.items("noise_bursts")
        self.assertTrue(any(span["start"] - 0.005 <= 4.5 <= span["end"] + 0.005 and span["label"] == "1"
                            for span in bursts), bursts)
        # Nothing is reported in a stretch with no defect, quiet or not.
        for name in ("clipping", "clicks", "discontinuities", "gaps", "noise_bursts"):
            for item in entry.items(name):
                self.assertFalse(0.5 < item.get("time", item.get("start")) < 1.9, (name, item))

    def test_hum_is_found_with_its_frequency(self) -> None:
        directory = scratch(self)
        seconds = 40
        signal = noise(seconds, 0.05, 3) + tone(seconds, 50, 0.02)
        audio = encode(directory / "hum.flac", numpy.stack([signal, noise(seconds, 0.05, 4)], axis=1),
                       codec="flac")
        entry = measured(self, directory, audio)
        self.assertTrue(entry.scalar("hum_examined"))
        hum = entry.items("hum")
        self.assertEqual(len(hum), 1, hum)
        self.assertLess(abs(hum[0]["value"] - 50), 4)
        self.assertLess(hum[0]["start"], 15)  # the detector reports a tone a window after it starts
        self.assertEqual(hum[0]["end"], seconds)

    def test_a_tone_crossing_chunks_is_one_span(self) -> None:
        es = quality.essentia_module()
        hum = quality.Hum(es, 8000)
        hum.chunk, hum.step = 40 * 8000, 25 * 8000  # chunks of 40 s every 25 s
        signal = (noise(80, 0.05, 5, rate=8000) + tone(80, 60, 0.02, rate=8000)).astype(numpy.float32)
        for start in range(0, len(signal), 7000):
            hum.feed(signal[start:start + 7000])
        self.assertTrue(hum.finish())
        self.assertGreater(len(hum.tones), 1)  # anchor: more than one chunk found it
        spans = hum.spans(80.0)
        self.assertEqual(len(spans), 1, spans)
        self.assertLess(spans[0]["start"], 15)
        self.assertGreater(spans[0]["end"], 70)

    def test_a_held_note_is_reported_as_a_steady_tone(self) -> None:
        # What the hum description warns of: the detector cannot tell a
        # held note from mains hum, and reads it about 1% low.
        hum = quality.Hum(quality.essentia_module(), 8000)
        hum.feed(tone(40, 220, 0.3, 8000).astype(numpy.float32))
        self.assertTrue(hum.finish())
        spans = hum.spans(40.0)
        self.assertEqual(len(spans), 1, spans)
        self.assertTrue(212 < spans[0]["value"] < 220, spans)

    def test_a_short_recording_says_hum_was_not_examined(self) -> None:
        directory = scratch(self)
        audio = encode(directory / "short.wav", tone(5, 50, 0.3))
        entry = measured(self, directory, audio)
        self.assertFalse(entry.scalar("hum_examined"))
        self.assertEqual(entry.items("hum"), [])

    def test_dc_offset_is_the_worst_channels_mean(self) -> None:
        directory = scratch(self)
        audio = encode(directory / "dc.wav", numpy.stack([tone(3), tone(3) - 0.1], axis=1))
        entry = measured(self, directory, audio)
        self.assertAlmostEqual(entry.scalar("dc_offset"), -0.1, delta=0.001)

    def test_stereo_correlation_per_window_and_whole(self) -> None:
        directory = scratch(self)
        signal = noise(3, 0.1, 6)
        cases = {"anti": (numpy.stack([signal, -signal], axis=1), -1.0, 1.0, False),
                 "same": (numpy.stack([signal, signal], axis=1), 1.0, 0.0, True),
                 "mono": (signal, 1.0, 0.0, False),
                 "independent": (numpy.stack([signal, noise(3, 0.1, 7)], axis=1), None, None, False)}
        for name, (samples, correlation, anti, false_stereo) in cases.items():
            with self.subTest(name):
                entry = measured(self, directory, encode(directory / f"{name}.wav", samples))
                series = entry.series("correlation")[:, 0]
                middle = series[3:-3]
                self.assertEqual(entry.scalar("false_stereo"), false_stereo)
                if correlation is None:
                    self.assertLess(abs(entry.scalar("whole_correlation")), 0.05)
                    self.assertTrue(numpy.all(numpy.abs(middle) < 0.3), middle)
                    continue
                self.assertAlmostEqual(entry.scalar("whole_correlation"), correlation, places=4)
                self.assertTrue(numpy.allclose(middle, correlation, atol=1e-3), middle)
                self.assertAlmostEqual(entry.scalar("anti_phase"), anti)

    def test_a_silent_channel_correlates_with_nothing(self) -> None:
        directory = scratch(self)
        entry = measured(self, directory, encode(directory / "one.wav",
                                                 numpy.stack([noise(3, 0.1, 8), numpy.zeros(3 * RATE)], axis=1)))
        self.assertTrue(numpy.all(entry.series("correlation") == 0))  # Essentia's NaN is not written
        self.assertEqual(entry.scalar("anti_phase"), 0.0)
        self.assertEqual(entry.scalar("whole_correlation"), 0.0)

    def test_silence_is_measured_and_finite(self) -> None:
        directory = scratch(self)
        entry = measured(self, directory, encode(directory / "silence.wav", numpy.zeros((2 * RATE, 2))))
        self.assertEqual(entry.scalar("true_peak"), quality.FLOOR_DB)
        self.assertEqual(entry.scalar("sample_peak"), quality.FLOOR_DB)
        self.assertEqual(entry.scalar("integrated"), -70.0)
        self.assertEqual(entry.scalar("bandwidth"), 0.0)
        self.assertFalse(entry.scalar("band_limited"))
        self.assertEqual((entry.scalar("noise_floor"), entry.scalar("snr")), (quality.FLOOR_DB, 0.0))
        self.assertTrue(numpy.all(entry.series("peak_db") == quality.FLOOR_DB))

    def test_an_upsampled_source_is_band_limited(self) -> None:
        directory = scratch(self)
        low = directory / "low.wav"
        support.ffmpeg("-f", "lavfi", "-i", "anoisesrc=r=22050:d=6:a=0.1", "-c:a", "pcm_s16le", str(low))
        up = directory / "up.wav"
        support.ffmpeg("-i", str(low), "-ar", "44100", "-c:a", "pcm_s16le", str(up))
        full = directory / "full.wav"
        support.ffmpeg("-f", "lavfi", "-i", "anoisesrc=r=44100:d=6:a=0.1", "-c:a", "pcm_s16le", str(full))
        upsampled, native = measured(self, directory, up), measured(self, directory, full)
        self.assertEqual((native.scalar("bandwidth"), native.scalar("band_limited")), (22050.0, False))
        self.assertTrue(upsampled.scalar("band_limited"))
        self.assertTrue(10000 < upsampled.scalar("bandwidth") < 13500, upsampled.scalar("bandwidth"))

    def test_the_series_have_the_frames_grid_rows_at_every_rate(self) -> None:
        directory = scratch(self)
        for rate, seconds in ((22050, 1.3), (44100, 1.31), (48000, 0.77), (96000, 1.0001), (8000, 2.2)):
            with self.subTest(rate=rate):
                audio = encode(directory / f"r{rate}.wav", numpy.stack([tone(seconds, 300, 0.3, rate)] * 2, axis=1),
                               rate)
                entry = measured(self, directory, audio)
                frames = bank.Bank(directory / "bank").entry(bank.file_digest(audio), "frames")
                rows = frames.series("rms_db").shape[0]
                self.assertEqual(rows, math.ceil(frames.scalar("samples") / 2048) + 1)
                for name in quality.SERIES:
                    self.assertEqual(entry.series(name).shape, (rows, 1))
                    self.assertEqual(entry.grid(name), frames.grid("rms_db"))
                # The windows' peaks are the tone's wherever a window lies inside the audio.
                peak = entry.series("peak_db")[2:-2, 0]
                self.assertTrue(numpy.allclose(peak, 20 * math.log10(0.3), atol=0.1), peak)

    def test_the_series_rows_are_the_frames_rows_in_time(self) -> None:
        # One sample in silence, 7 samples after 2 s: only the windows that
        # hold it have a peak, and they are the rows frames gives a level.
        directory = scratch(self)
        for rate in (22050, 44100, 48000, 96000):
            with self.subTest(rate=rate):
                samples = numpy.zeros((4 * rate, 2))
                where = 2 * rate + 7
                samples[where] = 0.9
                audio = encode(directory / f"i{rate}.wav", samples, rate)
                entry = measured(self, directory, audio)
                frames = bank.Bank(directory / "bank").entry(bank.file_digest(audio), "frames")
                window = round(4096 * rate / 22050)
                expected = [k for k in range(entry.series("peak_db").shape[0])
                            if 0 <= where - (round(k * 2048 * rate / 22050) - window // 2) < window]
                self.assertEqual(len(expected), 2, expected)  # anchor: two windows, not the first ones
                self.assertGreater(expected[0], 5)
                peaked = numpy.nonzero(entry.series("peak_db")[:, 0] > -100)[0].tolist()
                self.assertEqual(peaked, expected)
                self.assertEqual(numpy.nonzero(frames.series("rms_db")[:, 0] > -100)[0].tolist(), expected)
                correlated = numpy.nonzero(entry.series("correlation")[:, 0] != 0)[0].tolist()
                self.assertEqual(correlated, expected)

    def test_block_size_does_not_change_any_output(self) -> None:
        directory = scratch(self)
        left = tone(6, 440, 0.3)
        left[3 * RATE] += 0.6
        left[4 * RATE:4 * RATE + 2400] = 0
        audio = encode(directory / "x.wav", numpy.stack([left, noise(6, 0.05, 9)], axis=1))
        es = quality.essentia_module()
        results = []
        for block in (1000, 4096, 1 << 16):
            out = directory / f"out-{block}"
            out.mkdir()
            request = {"output_dir": str(out), "input": {"path": str(audio)},
                       "needs": {"frames/samples": {"value": 6 * 22050}}}
            result = quality.run(request, es, block_frames=block)
            files = {path.name: path.read_bytes() for path in sorted(out.iterdir())}
            results.append((result["outputs"], files))
        self.assertTrue(json.loads(results[0][1]["clicks.json"]))  # anchor: something to compare
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0], results[2])


if __name__ == "__main__":
    unittest.main()
