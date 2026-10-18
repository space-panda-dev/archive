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
            channel.detect_bursts(frame, start)
        self.assertGreater(raw, 10)  # anchor: the detector does report them
        self.assertEqual(channel.bursts, [])
        # A real burst in the same passage is kept.
        loud = quiet.copy()
        loud[48000:48200] += noise(200 / rate, 0.05, 3, rate).astype(numpy.float32)
        channel = quality.Channel(es, rate)
        for start in range(0, len(loud) - 512, 256):
            channel.detect_bursts(numpy.ascontiguousarray(loud[start:start + 512]), start)
        self.assertTrue(channel.bursts)
        self.assertTrue(all(47900 <= sample <= 48300 for sample, _ in channel.bursts), channel.bursts)


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
        self.assertLess(abs(hum[0]["value"] - 50), 0.05)  # HumDetector read about 1% low
        self.assertEqual((hum[0]["start"], hum[0]["end"]), (0.0, seconds))

    def hum_of(self, signal: numpy.ndarray, rate: int = 8000, piece: int = 7000) -> quality.Hum:
        hum = quality.Hum(rate)
        signal = signal.astype(numpy.float32)
        for start in range(0, len(signal), piece):
            hum.feed(signal[start:start + piece])
        return hum

    def test_hum_through_a_long_recording_is_one_span(self) -> None:
        seconds = 80
        hum = self.hum_of(noise(seconds, 0.05, 5, rate=8000) + tone(seconds, 60, 0.02, rate=8000))
        spans = hum.spans(seconds * 8000)
        self.assertEqual(len(spans), 1, spans)
        self.assertEqual((spans[0]["start"], spans[0]["end"]), (0.0, 80.0))
        self.assertLess(abs(spans[0]["value"] - 60), 0.05)

    def test_a_held_note_off_the_mains_frequencies_is_not_hum(self) -> None:
        # A G held 1% sharp sits 0.8% below 50 Hz, a sharp G an octave up
        # 0.8% below 100 Hz, and 220 Hz is no harmonic of 50 or 60 at all.
        for frequency in (49.6, 99.2, 220.0):
            with self.subTest(frequency=frequency):
                hum = self.hum_of(tone(40, frequency, 0.3, 8000) + noise(40, 0.001, 6, rate=8000))
                self.assertTrue(hum.examined(40 * 8000))
                self.assertEqual(hum.spans(40 * 8000), [])
        # The anchor: the same note at 50 Hz is found, so the others were
        # refused for their frequency.
        hum = self.hum_of(tone(40, 50.0, 0.3, 8000) + noise(40, 0.001, 6, rate=8000))
        self.assertEqual([span["value"] for span in hum.spans(40 * 8000)], [50.0])

    def test_a_harmonic_without_its_series_first_or_second_is_not_hum(self) -> None:
        # 350 Hz is the seventh harmonic of 50 Hz and an F4 0.2% flat: alone
        # it is a note; with 50 Hz under it, it is hum's harmonic.
        background = noise(40, 0.001, 7, rate=8000)
        alone = self.hum_of(tone(40, 350, 0.1, 8000) + background)
        self.assertEqual(alone.spans(40 * 8000), [])
        self.assertTrue(numpy.array(alone.present)[:, alone.frequencies.index(350)].all())  # anchor: the peak is there
        with_mains = self.hum_of(tone(40, 350, 0.1, 8000) + tone(40, 50, 0.01, 8000) + background)
        self.assertEqual(sorted(span["value"] for span in with_mains.spans(40 * 8000)), [50.0, 350.0])

    def test_a_mains_tone_that_stops_in_the_quiet_is_music(self) -> None:
        # 20 s of room noise, 24 s of loud playing holding exactly 50 Hz, 16 s
        # of room noise: the tone does not persist through the quiet, so it
        # is the music's. Hum under everything does.
        rate, seconds = 8000, 60
        room = noise(seconds, 0.0005, 8, rate=rate)
        playing = numpy.zeros(seconds * rate)
        playing[20 * rate:44 * rate] = noise(24, 0.2, 9, rate=rate) + tone(24, 50, 0.1, rate)
        music = self.hum_of(room + playing, rate)
        index = music.frequencies.index(50)
        present = numpy.array(music.present)[:, index]
        self.assertGreaterEqual(int(present.sum()), 4)  # anchor: the tone is a run long enough
        self.assertEqual(music.spans(seconds * rate), [])
        hummed = self.hum_of(room + playing + tone(seconds, 50, 0.003, rate), rate)
        self.assertEqual([(span["start"], span["end"]) for span in hummed.spans(seconds * rate)], [(0.0, 60.0)])

    def bass_line(self, seconds: float, rate: int) -> numpy.ndarray:
        """A bass line through F1, G1, A1, F2, G2, and A2, a note a second,
        each with its second and third harmonics: G1 is 2% below 50 Hz and
        A2 10% above 100."""
        t = numpy.arange(round(seconds * rate)) / rate
        notes = numpy.array([43.65, 49.0, 55.0, 87.3, 98.0, 110.0])[(t // 1.0).astype(int) % 6]
        phase = 2 * numpy.pi * numpy.cumsum(notes) / rate
        return sum(0.3 / k * numpy.sin(k * phase) for k in (1, 2, 3))

    def test_hum_either_side_of_a_bass_line_is_found(self) -> None:
        # Hum at -50 dBFS (50, 100, and 150 Hz) under everything, room noise,
        # and a song with a bass line from 14 to 46 s.
        rate, seconds = 8000, 60
        t = numpy.arange(seconds * rate) / rate
        mains = 10 ** (-50 / 20) * (numpy.sin(2 * numpy.pi * 50 * t) + 0.7 * numpy.sin(2 * numpy.pi * 100 * t)
                                    + 0.4 * numpy.sin(2 * numpy.pi * 150 * t))
        song = numpy.zeros(len(t))
        song[14 * rate:46 * rate] = self.bass_line(32, rate) + noise(32, 0.02, 22, rate=rate)
        room = noise(seconds, 10 ** (-58 / 20), 23, rate=rate)
        hum = self.hum_of(room + song + mains, rate)
        spans = hum.spans(seconds * rate)
        for harmonic in (50, 100, 150):
            with self.subTest(harmonic=harmonic):
                found = [(span["start"], span["end"]) for span in spans if abs(span["value"] - harmonic) < 0.1]
                self.assertEqual(found[0][0], 0.0, spans)  # before the song: 14 s, under the minimum
                self.assertEqual(found[-1][1], 60.0, spans)  # and after it
        # The anchor: beside the hum a note is far stronger, which is all
        # quality 3's first rule needed to lose it, and the hum is not
        # longer than the minimum in either quiet stretch.
        window = 8 * rate
        energy = numpy.abs(numpy.fft.rfft((room + song + mains)[40 * rate:40 * rate + window]
                                          * numpy.hanning(window))) ** 2
        self.assertGreater(energy[int(0.85 * 50 * 8):int(1.15 * 50 * 8)].max(), 100 * energy[50 * 8])
        self.assertLess(14, quality.PARAMETERS["hum_minimum_seconds"])
        # And with no hum, the bass line alone is none.
        self.assertEqual(self.hum_of(room + song, rate).spans(seconds * rate), [])

    def test_hum_in_a_short_quiet_stretch_is_found(self) -> None:
        # 14 s of room noise and hum before a song that buries it: shorter
        # than the minimum, but the hum is there in the quiet.
        rate, seconds = 8000, 50
        signal = noise(seconds, 10 ** (-58 / 20), 24, rate=rate) + tone(seconds, 60, 10 ** (-50 / 20), rate)
        signal[14 * rate:] += noise(seconds - 14, 0.2, 25, rate=rate)
        hum = self.hum_of(signal, rate)
        spans = hum.spans(seconds * rate)
        self.assertEqual(len(spans), 1, spans)
        self.assertEqual(spans[0]["start"], 0.0)
        self.assertLess(spans[0]["end"] - spans[0]["start"], quality.PARAMETERS["hum_minimum_seconds"])  # anchor
        self.assertLess(abs(spans[0]["value"] - 60), 0.05)
        # One quiet window is not enough: a note held in a pause is not hum.
        short = noise(seconds, 10 ** (-58 / 20), 24, rate=rate)
        short[:9 * rate] += tone(9, 60, 10 ** (-50 / 20), rate)
        short[9 * rate:] += noise(seconds - 9, 0.2, 25, rate=rate)
        self.assertEqual(self.hum_of(short, rate).spans(seconds * rate), [])

    def test_with_no_quiet_passage_a_steady_mains_tone_is_hum(self) -> None:
        rate, seconds = 8000, 40
        hum = self.hum_of(noise(seconds, 0.2, 10, rate=rate) + tone(seconds, 60, 0.01, rate), rate)
        levels = numpy.array(hum.levels)
        self.assertLess(levels.max() - levels.min(), 3)  # anchor: no window is quiet
        self.assertEqual([span["value"] for span in hum.spans(seconds * rate)], [60.0])

    def test_a_loop_at_120_beats_a_minute_is_not_hum(self) -> None:
        # A kick every half second is a comb of lines 2 Hz apart, one of them
        # at exactly 50 Hz and one at exactly 60 Hz.
        rate, seconds = 8000, 40
        since = numpy.arange(seconds * rate) / rate % 0.5
        kick = 0.6 * numpy.sin(2 * numpy.pi * 55 * since) * numpy.exp(-since / 0.08)
        hum = self.hum_of(kick + noise(seconds, 0.001, 15, rate=rate), rate)
        self.assertEqual(hum.spans(seconds * rate), [])
        # The anchor: the line at 50 Hz is a narrow peak far above the median
        # around it, which is all a prominence asks.
        window = round(8 * rate)
        energy = numpy.abs(numpy.fft.rfft(kick[:window] * numpy.hanning(window))) ** 2
        line = 50 * 8
        self.assertEqual(int(numpy.argmax(energy[line - 3:line + 4])), 3)
        self.assertGreater(10 * math.log10(energy[line] / numpy.median(energy[340:460])), 15)

    def test_hum_shorter_than_the_minimum_is_not_reported(self) -> None:
        rate, seconds = 8000, 40
        signal = noise(seconds, 0.001, 11, rate=rate)
        signal[:12 * rate] += tone(12, 50, 0.1, rate)
        hum = self.hum_of(signal, rate)
        self.assertTrue(numpy.array(hum.present)[0, hum.frequencies.index(50)])  # anchor: found where it is
        self.assertEqual(hum.spans(seconds * rate), [])

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


def measure_samples(samples: numpy.ndarray, rate: int = RATE, screen: bool = True) -> tuple[quality.Measure, dict]:
    """The measures of samples [frames, channels], fed in blocks as the decoder would."""
    samples = numpy.asarray(samples, dtype=numpy.float32)
    if samples.ndim == 1:
        samples = samples[:, None]
    rows = -(-round(len(samples) * 22050 / rate) // 2048) + 1
    measure = quality.Measure(quality.essentia_module(), rate, samples.shape[1], rows, screen=screen)
    for start in range(0, len(samples), 1 << 16):
        measure.feed(samples[start:start + (1 << 16)])
    return measure, measure.finish()


def sixteen_bit(samples: numpy.ndarray) -> numpy.ndarray:
    """Samples as a 16-bit file holds them."""
    return numpy.clip(numpy.round(numpy.asarray(samples) * 32768), -32768, 32767) / 32768


class Clipping(unittest.TestCase):
    """Flat tops at full scale; a limiter's ceiling below it is not clipping."""

    def test_runs_of_flat_samples_at_full_scale_are_clipping_and_a_peak_is_not(self) -> None:
        top, step = 32767 / 32768, 1 / 32768
        samples = numpy.zeros(100, dtype=numpy.float32)
        samples[10:13] = top  # three samples: a flat top
        samples[20:22] = top  # two: a peak
        samples[30:40] = -1.0  # ten, negative full scale
        samples[50:55] = [top, top - step, top, top, top - step]  # within one 16-bit step: flat
        samples[60:64] = [top, top - 3 * step, top, top - 3 * step]  # a peak that wobbles: not flat
        samples[97:100] = top  # still going at the end
        for pieces in (1, 7, 100):
            with self.subTest(pieces=pieces):
                flats = quality.Flats()
                for start in range(0, 100, pieces):
                    flats.feed(samples[start:start + pieces])
                self.assertEqual(flats.finish(), [(10, 13), (30, 40), (50, 55), (97, 100)])

    def test_below_full_scale_only_long_runs_at_the_peak_are_clipping(self) -> None:
        samples = numpy.zeros(200, dtype=numpy.float32)
        samples[10:30] = 0.5  # 20 samples at the peak: clipped, then turned down
        samples[40:50] = -0.5  # 10 at the peak: a limiter's brief flat top
        samples[60:90] = 0.3  # 30 below the peak: a held level, not clipping
        samples[100:120] = 0.5 - 1 / 32768  # within a step of the peak
        samples[150:166] = 0.5  # exactly the minimum
        for pieces in (1, 13, 200):
            with self.subTest(pieces=pieces):
                flats = quality.Flats()
                for start in range(0, 200, pieces):
                    flats.feed(samples[start:start + pieces])
                self.assertEqual(flats.finish(), [(10, 30), (100, 120), (150, 166)])
        # At twice the rate a run must be twice as long.
        flats = quality.Flats(scale=2)
        flats.feed(samples)
        self.assertEqual(flats.finish(), [])

    def test_a_run_ends_where_its_value_changes(self) -> None:
        flats = quality.Flats()
        flats.feed(numpy.array([0.0, 1.0, 1.0, 1.0, -1.0, -1.0, -1.0, 0.0], dtype=numpy.float32))
        self.assertEqual(flats.finish(), [(1, 4), (4, 7)])

    @needs_ffmpeg
    @needs_essentia
    def test_a_limiter_ceiling_below_full_scale_is_not_clipping(self) -> None:
        # Brief flat tops, 3 or 4 samples, at a ceiling of -0.3 dBFS: what a
        # limiter leaves (a mastered album's longest run at its peak was 8).
        directory = scratch(self)
        ceiling = 10 ** (-0.3 / 20)
        signal = numpy.clip(tone(4, 1000, 1.03 * ceiling), -ceiling, ceiling)
        audio = encode(directory / "limited.wav", numpy.stack([signal, signal], axis=1))
        samples, _ = decoded(audio)
        # The anchor: the flat tops are there, and Essentia's SaturationDetector
        # with quality 2's settings called them clipping.
        saturation = quality.essentia_module().SaturationDetector(frameSize=512, hopSize=256, sampleRate=RATE,
                                                                  energyThreshold=-1.0, differentialThreshold=0.001,
                                                                  minimumDuration=0.005)
        found = sum(len(saturation(numpy.ascontiguousarray(samples[s:s + 512, 0]))[0])
                    for s in range(0, len(samples) - 512, 256))
        self.assertGreater(found, 100)
        entry = measured(self, directory, audio)
        self.assertEqual(entry.items("clipping"), [])
        self.assertEqual((entry.scalar("clipped_seconds"), entry.scalar("full_scale_samples")), (0, 0))
        self.assertAlmostEqual(entry.scalar("sample_peak"), -0.3, delta=0.01)

    def clipped(self, directory: Path) -> Path:
        """Eight seconds of a chord, driven into full scale from 3 to 5 s, in
        a 16-bit file."""
        t = numpy.arange(8 * RATE) / RATE
        chord = sum(0.15 * numpy.sin(2 * numpy.pi * f * t) for f in (110, 165, 220, 277))
        chord[3 * RATE:5 * RATE] *= 6
        signal = numpy.clip(chord + noise(8, 0.001, 26), -1.0, 1.0)
        return encode(directory / "clipped.wav", numpy.stack([signal, signal], axis=1))

    def test_clipping_turned_down_afterwards_is_still_clipping(self) -> None:
        directory = scratch(self)
        clipped = self.clipped(directory)
        quieter = directory / "normalised.wav"
        support.ffmpeg("-i", str(clipped), "-af", "volume=-1dB", "-c:a", "pcm_s16le", str(quieter))
        for audio, peak in ((clipped, 0.0), (quieter, -1.0)):
            with self.subTest(audio.name):
                entry = measured(self, directory, audio)
                self.assertAlmostEqual(entry.scalar("sample_peak"), peak, delta=0.02)
                clipping = entry.items("clipping")
                self.assertGreater(entry.scalar("clipped_seconds"), 0.5)  # both channels, about a third of 2 s
                self.assertTrue(clipping)
                self.assertTrue(all(3.0 <= span["start"] and span["end"] <= 5.0 for span in clipping), clipping)
        # The anchor: nothing in the turned-down file reaches full scale.
        self.assertEqual(measured(self, directory, quieter).scalar("full_scale_samples"), 0)

    def test_clipping_encoded_lossily_is_overs(self) -> None:
        # AAC rings where the flat tops were: no flat run is left, but the
        # decoded signal goes past full scale there.
        directory = scratch(self)
        lossy = directory / "clipped.m4a"
        support.ffmpeg("-i", str(self.clipped(directory)), "-c:a", "aac", "-b:a", "128k", str(lossy))
        entry = measured(self, directory, lossy)
        self.assertEqual(entry.items("clipping"), [])
        overs = entry.items("overs")
        self.assertEqual(sorted({span["label"] for span in overs}), ["1", "2"])
        self.assertTrue(all(2.9 <= span["start"] and span["end"] <= 5.1 for span in overs), overs)


@needs_ffmpeg
@needs_essentia
class Overs(unittest.TestCase):
    def test_intersample_overs_are_found_and_agree_with_ffmpeg(self) -> None:
        directory = scratch(self)
        rate = 44100
        # An eighth of the rate, 22.5 degrees off the samples: every sample
        # misses the wave's peak by 0.69 dB. Faded in and out, so that no
        # edge rings.
        fade = numpy.minimum(1, numpy.minimum(numpy.arange(3 * rate), numpy.arange(3 * rate)[::-1]) / (0.05 * rate))
        left = fade * tone(3, rate / 8, 1.05, rate, phase=math.pi / 8)
        right = fade * tone(3, rate / 8, 0.6, rate, phase=math.pi / 8)
        audio = encode(directory / "overs.wav", numpy.stack([left, right], axis=1), rate)
        entry = measured(self, directory, audio)
        self.assertLess(entry.scalar("sample_peak"), -0.2)  # anchor: no sample is over
        overs = entry.items("overs")
        self.assertEqual([span["label"] for span in overs], ["1"])
        self.assertAlmostEqual(overs[0]["start"], 0.1, delta=0.1)
        self.assertAlmostEqual(overs[0]["end"], 2.9, delta=0.1)
        self.assertAlmostEqual(overs[0]["value"], 20 * math.log10(1.05), delta=0.01)
        self.assertAlmostEqual(overs[0]["value"], entry.scalar("true_peak"), delta=0.1)
        self.assertEqual(entry.items("clipping"), [])

    def test_samples_over_full_scale_in_a_float_file_are_overs_not_clipping(self) -> None:
        directory = scratch(self)
        signal = tone(2, 440, 1.5)
        audio = encode(directory / "float.wav", numpy.stack([signal, 0.5 * signal], axis=1), codec="pcm_f32le")
        entry = measured(self, directory, audio)
        self.assertGreater(entry.scalar("full_scale_samples"), 1000)  # anchor
        self.assertEqual([span["label"] for span in entry.items("overs")], ["1"])
        self.assertAlmostEqual(entry.items("overs")[0]["value"], 20 * math.log10(1.5), delta=0.05)
        self.assertEqual(entry.items("clipping"), [])

    def test_a_kernel_keeps_a_constant_and_a_slow_wave(self) -> None:
        self.assertTrue(numpy.allclose(quality.KERNEL.sum(axis=0), 1.0))
        samples = numpy.sin(2 * numpy.pi * 1000 * numpy.arange(200) / 48000)
        half = quality.KERNEL.shape[0] // 2
        windows = numpy.lib.stride_tricks.sliding_window_view(samples, 2 * half)
        between = windows @ quality.KERNEL
        n = numpy.arange(len(windows))[:, None] + half - 1 + numpy.arange(1, 4) / 4
        self.assertTrue(numpy.allclose(between, numpy.sin(2 * numpy.pi * 1000 * n / 48000), atol=2e-3))


@needs_essentia
class Surroundings(unittest.TestCase):
    """Events weighed against the transients around them."""

    def test_the_context_leaves_out_the_block_and_its_neighbours(self) -> None:
        maxima = numpy.arange(10, dtype=numpy.float64)[:, None]
        found = quality.context(maxima, 0, 10, 2, 50)[:, 0]
        # Block 5 sees blocks 3 and 7; block 0 sees only block 2.
        self.assertEqual(found[5], 5.0)
        self.assertEqual(found[0], 2.0)
        self.assertEqual(found[9], 7.0)
        # A part computed alone is the same as the whole.
        self.assertTrue(numpy.array_equal(quality.context(maxima, 3, 6, 2, 50)[:, 0], found[3:6]))
        # Nothing around a block is no context: 0.
        self.assertEqual(quality.context(numpy.ones((1, 2)), 0, 1, 2, 90).tolist(), [[0.0, 0.0]])

    def test_what_is_left_nearby_is_the_largest_of_three_blocks(self) -> None:
        maxima = numpy.array([[5.0], [1.0], [0.0], [2.0], [0.0], [0.0], [7.0]])
        found = quality.nearby(maxima, 0, 7)[:, 0].tolist()
        self.assertEqual(found, [5.0, 5.0, 2.0, 2.0, 2.0, 7.0, 7.0])
        # A part computed alone is the same as the whole, at either end.
        for first, last in ((0, 2), (2, 5), (5, 7)):
            self.assertEqual(quality.nearby(maxima, first, last)[:, 0].tolist(), found[first:last])

    def hits(self, seconds: float, every: float, rate: int = RATE) -> numpy.ndarray:
        """A quiet tone with a percussive hit every `every` seconds: a
        one-sample spike, as sharp as a click, and a burst of noise behind
        it that decays in 1 ms, with many steps a third as large."""
        signal = tone(seconds, 220, 0.1, rate)
        rng = numpy.random.default_rng(16)
        length = round(0.02 * rate)
        decay = 0.1 * numpy.exp(-numpy.arange(length) / (0.001 * rate))
        for at in (numpy.arange(0.1, seconds - 0.1, every) * rate).astype(int):
            signal[at] += 0.5
            signal[at + 1:at + 1 + length] += decay * rng.standard_normal(length)
        return sixteen_bit(signal)

    def test_a_recurring_hit_is_music_and_a_lone_click_is_a_click(self) -> None:
        measure, values = measure_samples(self.hits(6, 0.2), screen=False)
        self.assertGreater(len(measure.detectors[0].clicks), 20)  # anchor: ClickDetector finds every hit
        self.assertEqual(values["_events"]["clicks"], [])
        self.assertEqual(values["_events"]["noise_bursts"], [])
        self.assertEqual(values["_events"]["discontinuities"], [])
        lone = sixteen_bit(tone(6, 220, 0.1))
        lone[3 * RATE] += 0.5
        _, values = measure_samples(lone)
        self.assertEqual([round(event["time"], 3) for event in values["_events"]["clicks"]], [3.0])
        self.assertTrue(values["_events"]["noise_bursts"])

    def crackle(self, every: float) -> tuple[numpy.ndarray, list[float]]:
        """20 s of -58 dBFS room noise with a one-sample click of 0.3 every
        `every` seconds from 2 to 18 s: a buffer underrun or a sync error."""
        signal = noise(20, 10 ** (-58 / 20), 17)
        times = numpy.arange(2, 18, every)
        signal[(times * RATE).astype(int)] += 0.3
        return sixteen_bit(signal), [round(float(t), 3) for t in times]

    def test_crackle_in_the_quiet_is_every_click(self) -> None:
        for every in (0.15, 0.5, 1.0):
            with self.subTest(every=every):
                signal, times = self.crackle(every)
                measure, values = measure_samples(signal)
                found = [round(event["time"], 3) for event in values["_events"]["clicks"]]
                self.assertEqual(found, times)
        # The anchor: weighed against the surroundings alone, as quality 3
        # first was, crackle that recurs is its own context and none of it
        # is kept.
        signal, times = self.crackle(0.15)
        with patch.dict(quality.PARAMETERS, {"click_alone_ratio": math.inf, "discontinuity_alone_ratio": math.inf}):
            _, values = measure_samples(signal)
        self.assertEqual(values["_events"]["clicks"], [])

    def test_a_click_among_cymbals_is_found_between_their_hits(self) -> None:
        # A held five-note chord and hats at -30 dBFS RMS every quarter
        # second, in room noise; a click of 0.3 half way between two hats.
        rate, seconds = RATE, 20
        t = numpy.arange(seconds * rate) / rate
        chord = sum(0.05 * numpy.sin(2 * numpy.pi * f * t) for f in (196, 247, 294, 392, 494))
        rng = numpy.random.default_rng(18)
        hats = 10 ** (-30 / 20) * rng.standard_normal(len(t)) * numpy.exp(-(t % 0.25) / 0.03)
        signal = noise(seconds, 10 ** (-58 / 20), 19) + chord * (0.3 + 0.7 * numpy.exp(-(t % 2) / 0.7)) + hats
        clean = sixteen_bit(signal)
        signal[round(10.125 * rate)] += 0.3
        measure, values = measure_samples(sixteen_bit(signal))
        self.assertEqual([round(event["time"], 3) for event in values["_events"]["clicks"]], [10.125])
        block = round(10.125 * rate) // measure.block
        # The anchor: among the hats the surroundings' context asks for a
        # step no click of 0.3 has.
        self.assertGreater(quality.PARAMETERS["click_ratio"] * float(measure.step_context.rows[block, 0]), 0.6)
        _, values = measure_samples(clean)
        self.assertEqual(values["_events"]["clicks"], [])  # the hats alone are not clicks

    def test_screening_changes_nothing_but_the_cost(self) -> None:
        rate, seconds = RATE, 12
        left = self.hits(seconds, 0.3)
        right = sixteen_bit(tone(seconds, 440, 0.3, rate, phase=0.7))
        right[round(4.5 * rate)] += 0.4  # a click
        right[6 * rate:] = sixteen_bit(tone(seconds, 440, 0.3, rate, phase=2.7))[6 * rate:]  # a jump
        right[round(8.0 * rate):round(8.1 * rate)] = 0  # a dropout
        right[round(9.0 * rate):round(9.002 * rate)] += noise(0.002, 0.1, 12)  # a burst
        samples = numpy.stack([left, right], axis=1)
        screened, found = measure_samples(samples)
        every, all_found = measure_samples(samples, screen=False)
        events = found["_events"]
        self.assertTrue(events["clicks"] and events["discontinuities"] and events["gaps"] and events["noise_bursts"])
        self.assertEqual(found, all_found)
        # The cost: the detectors ran on a few frames, not all of them.
        frames = every.examined["steps"]  # every frame of every channel
        self.assertEqual(every.examined["bursts"], frames)
        self.assertLess(screened.examined["steps"], frames / 20)
        self.assertLess(screened.examined["bursts"], frames / 20)
        self.assertLess(screened.examined["gaps"], every.examined["gaps"] / 5)

    def test_each_chunk_is_reduced_into_its_block_once(self) -> None:
        # The blocks' maxima are kept as the chunks arrive: reducing every
        # chunk seen so far on every feed made the cost grow with the square
        # of the recording's length (13 minutes for four hours).
        reduced = []
        original = quality.Measure.reduce

        def counted(measure: quality.Measure, chunks: numpy.ndarray) -> numpy.ndarray:
            reduced.append(len(chunks))
            return original(measure, chunks)

        signal = sixteen_bit(numpy.stack([noise(30, 0.01, 20), noise(30, 0.01, 21)], axis=1))
        with patch.object(quality.Measure, "reduce", counted):
            measure, _ = measure_samples(signal)
        blocks = len(measure.block_rows["steps"])
        self.assertEqual(blocks, len(measure.step_context))
        self.assertGreater(len(reduced), 4 * 20)  # anchor: many feeds, each reducing
        # Four series, each chunk once, the last block padded to whole.
        self.assertEqual(sum(reduced), 4 * blocks * measure.block_chunks)

    def test_a_steady_recording_starts_no_detector(self) -> None:
        fade = numpy.minimum(1, numpy.minimum(numpy.arange(8 * RATE), numpy.arange(8 * RATE)[::-1]) / (0.01 * RATE))
        # Faded in and out: a tone that starts at full level is a kink, which the detectors are run on.
        signal = sixteen_bit(numpy.stack([fade * tone(8, 440, 0.3) + noise(8, 0.01, 13), fade * tone(8, 330, 0.3)],
                                         axis=1))
        bomb = AssertionError("a detector ran")
        with patch.object(quality.Channel, "detect_steps", side_effect=bomb), \
                patch.object(quality.Channel, "detect_bursts", side_effect=bomb), \
                patch.object(quality.Channel, "detect_gaps", side_effect=bomb):
            _, values = measure_samples(signal)
            self.assertEqual(values["_events"]["clicks"], [])
            # The anchor: the bombs are armed; a click sets one off.
            signal[4 * RATE, 1] += 0.5
            with self.assertRaisesRegex(AssertionError, "a detector ran"):
                measure_samples(signal)


@needs_ffmpeg
@needs_essentia
class Mastered(unittest.TestCase):
    """What quality 2 called defects on every track of a finished master."""

    def test_a_finished_master_trips_no_detector(self) -> None:
        directory = scratch(self)
        rate, seconds = 44100, 30
        t = numpy.arange(seconds * rate) / rate
        rng = numpy.random.default_rng(14)

        def since(every: float, offset: float = 0.0) -> numpy.ndarray:
            return (t - offset) % every

        # A bass line through a sharp G1 (0.8% below 50 Hz), C2, D2 and F1,
        # half a second each; a kick every beat, a snare every other, and
        # hats every half beat; then limited hard to -0.3 dBFS.
        notes = numpy.array([49.6, 65.4, 73.4, 43.7])[((t // 0.5) % 4).astype(int)]
        bass = 0.35 * numpy.sin(2 * numpy.pi * numpy.cumsum(notes) / rate) * numpy.exp(-since(0.5) / 0.4)
        kick = 0.6 * numpy.sin(2 * numpy.pi * 55 * since(0.5)) * numpy.exp(-since(0.5) / 0.08)
        snare = 0.4 * rng.standard_normal(len(t)) * numpy.exp(-since(1.0, 0.5) / 0.05)
        hats = 0.2 * rng.standard_normal(len(t)) * numpy.exp(-since(0.25) / 0.015)
        mix = bass + kick + snare + hats
        mix *= 1.6 / numpy.abs(mix).max()
        ceiling = 10 ** (-0.3 / 20)
        left = numpy.clip(mix, -ceiling, ceiling)
        right = numpy.clip(0.9 * mix + 0.05 * rng.standard_normal(len(t)) * numpy.exp(-since(0.25) / 0.015),
                           -ceiling, ceiling)
        audio = encode(directory / "master.flac", numpy.stack([left, right], axis=1), rate, codec="flac")
        samples, _ = decoded(audio)
        # The anchor: Essentia's detectors, unweighed, complain about it.
        raw, _ = measure_samples(samples, rate, screen=False)
        self.assertGreater(len(raw.detectors[0].bursts), 100)
        self.assertGreater(len(raw.detectors[0].clicks), 0)
        entry = measured(self, directory, audio)
        for name in ("clipping", "clicks", "discontinuities", "gaps", "noise_bursts", "hum"):
            self.assertEqual(entry.items(name), [], name)
        self.assertTrue(entry.scalar("hum_examined"))
        self.assertEqual(entry.scalar("full_scale_samples"), 0)
        # Hard limiting puts peaks between the samples above the ceiling, at
        # the hits (all on a quarter-second grid); overs says where they
        # reach 0 dBTP, as the true peak does.
        self.assertGreater(entry.scalar("true_peak"), 1.0)  # anchor: ffmpeg's meter sees them
        overs = entry.items("overs")
        self.assertEqual(sorted({span["label"] for span in overs}), ["1", "2"])
        self.assertAlmostEqual(max(span["value"] for span in overs), entry.scalar("true_peak"), delta=0.2)
        self.assertTrue(all(span["start"] % 0.25 < 0.03 for span in overs), overs)


if __name__ == "__main__":
    unittest.main()
