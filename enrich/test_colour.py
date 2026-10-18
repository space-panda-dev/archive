"""The colour extractor, run by the real runner on synthetic audio (ADR 0074).

Its colours must be one per pixel of the derived-media peaks (which it must
not copy), must follow the frames series it reads, and must change where in
time those series change; its palette must pass its own check; and its
preview must decode, in another program's PNG decoder, to the image its
colours and the derived-media peaks describe.
"""

from __future__ import annotations

import colorsys
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
import derived
import extract
import media
import support
from support import needs_ffmpeg
from test_cover import needs_essentia

sys.path.insert(0, str(Path(__file__).resolve().parent / "extractors" / "colour"))
import colour  # noqa: E402

RATE = 44100


def scratch(test: unittest.TestCase) -> Path:
    directory = Path(tempfile.mkdtemp(prefix="bank-colour-"))
    test.addCleanup(shutil.rmtree, directory, True)
    return directory


def run(directory: Path, audio: Path, *extra: str, target: str = "colour") -> tuple[int, dict]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = extract.main(["run", target, "--content", str(audio), "--bank", str(directory / "bank"), *extra])
    return code, json.loads(out.getvalue() or err.getvalue())


def configured(directory: Path, name: str, **overrides) -> tuple[str, str, dict]:
    """`--config` with colour parameters overridden, and the parameters it gives."""
    path = directory / f"{name}.json"
    path.write_text(json.dumps({"parameters": {"colour": overrides}}))
    return "--config", str(path), {**colour.parameters(), **overrides}


def entry(directory: Path, audio: Path, parameters: dict | None = None) -> bank.Entry:
    found = bank.Bank(directory / "bank", parameters={"colour": parameters} if parameters else None).entry(
        bank.file_digest(audio), "colour")
    assert found is not None
    return found


def derived_peaks(directory: Path, audio: Path) -> tuple[int, int, numpy.ndarray]:
    """The peaks `enrich/media.py` makes for this content as derived media,
    by the real tool into a derived-media root of its own."""
    root = directory / "media"
    root.mkdir(exist_ok=True)
    digest = bank.file_digest(audio)
    media.derive(audio, digest, root, kinds=("peaks",))
    return colour.read_peaks(derived.path(root, digest, "peaks"))


def crossing(values: numpy.ndarray, level: float) -> float:
    """The fractional index where a rising series first reaches ``level``,
    by linear interpolation between the two samples around it."""
    above = int(numpy.argmax(values >= level))
    assert above > 0 and values[above] >= level > values[above - 1]
    return above - 1 + (level - values[above - 1]) / (values[above] - values[above - 1])


def fake_program(directory: Path, name: str, script: str) -> str:
    path = directory / name
    path.write_text("#!/bin/sh\n" + script + "\n")
    path.chmod(0o755)
    return str(path)


def hues_and_lightness(rgb: numpy.ndarray) -> tuple[numpy.ndarray, numpy.ndarray, numpy.ndarray]:
    """Hue in degrees, lightness, and saturation by Python's own colorsys, an
    oracle independent of the program's conversion."""
    triples = [colorsys.rgb_to_hls(*(rgb_row / 255.0)) for rgb_row in rgb.astype(numpy.float64)]
    hls = numpy.array(triples)
    return hls[:, 0] * 360, hls[:, 1], hls[:, 2]


def decoded_png(path: Path, width: int, height: int) -> numpy.ndarray:
    """The PNG decoded to RGBA by ffmpeg, not by anything in this repository."""
    raw = subprocess.run([support.FFMPEG, "-nostdin", "-v", "error", "-i", str(path), "-f", "rawvideo",
                          "-pix_fmt", "rgba", "-"], capture_output=True, check=True).stdout
    return numpy.frombuffer(raw, dtype=numpy.uint8).reshape(height, width, 4)


def sine(frequency: float, seconds: float, amplitude: float, rate: int = RATE) -> numpy.ndarray:
    return amplitude * numpy.sin(2 * numpy.pi * frequency * numpy.arange(int(seconds * rate)) / rate)


def bright_noise(seconds: float, amplitude: float, rate: int = RATE, seed: int = 3) -> numpy.ndarray:
    """White noise with its low half removed: a centroid in the kilohertz."""
    noise = numpy.random.default_rng(seed).standard_normal(int(seconds * rate))
    spectrum = numpy.fft.rfft(noise)
    spectrum[: len(spectrum) // 3] = 0
    shaped = numpy.fft.irfft(spectrum, len(noise))
    return amplitude * shaped / numpy.abs(shaped).max()


class Manifest(unittest.TestCase):
    def test_the_manifest_declares_exactly_what_the_program_implements(self) -> None:
        extractor = bank.Catalog.load()["colour"]
        self.assertEqual(extractor.manifest["parameters"], colour.parameters())
        self.assertEqual(colour.settings(extractor.manifest["parameters"]).hue["source"], "centroid")
        self.assertEqual(tuple(extractor.needs), colour.NEEDS)
        frames = bank.Catalog.load()["frames"]
        for need in extractor.needs:
            source, output = need.split("/")
            self.assertEqual(source, "frames")
            self.assertEqual(frames.outputs[output]["kind"], "series")
        # The peaks are the derived-media kind; a new peaks method changes the key.
        kind = derived.KINDS["peaks"]
        self.assertEqual(colour.parameters()["peaks"], f"{kind.name}-{kind.version}")


class Settings(unittest.TestCase):
    def test_each_setting_it_does_not_implement_is_refused(self) -> None:
        good = colour.parameters()
        chroma = {"source": "chroma", "order": "fifths", "reference_hue": 0}
        self.assertEqual(colour.settings({**good, "hue": chroma}).hue, chroma)
        bad = {
            "unknown field": {**good, "gamma": "2.2"},
            "missing field": {k: v for k, v in good.items() if k != "preview"},
            "other peaks": {**good, "peaks": "peaks-2"},
            "other interpolation": {**good, "interpolation": "cubic"},
            "unknown hue source": {**good, "hue": {**good["hue"], "source": "mfcc"}},
            "hue limits reversed": {**good, "hue": {**good["hue"], "low_hz": 8000, "high_hz": 100}},
            "hue above Nyquist": {**good, "hue": {**good["hue"], "high_hz": 20000}},
            "hue out of the circle": {**good, "hue": {**good["hue"], "high_hue": 400}},
            "centroid field on chroma": {**good, "hue": {**chroma, "low_hz": 100}},
            "unknown order": {**good, "hue": {**chroma, "order": "thirds"}},
            "saturation as a number": {**good, "saturation": 1},
            "saturation above one": {**good, "saturation": "1.5"},
            "saturation not decimal": {**good, "saturation": "1e-1"},
            "lightness source": {**good, "lightness": {**good["lightness"], "source": "loudness"}},
            "lightness reference": {**good, "lightness": {**good["lightness"], "reference": "peak"}},
            "floor above ceiling": {**good, "lightness": {**good["lightness"], "floor_db": -1, "ceiling_db": -10}},
            "darkest above lightest": {**good, "lightness": {**good["lightness"], "darkest": "0.7"}},
            "boolean width": {**good, "preview": {"width": True, "height": 160}},
            "tiny preview": {**good, "preview": {"width": 4, "height": 160}},
            "preview too wide to hold": {**good, "preview": {"width": colour.MAX_PREVIEW_WIDTH + 1, "height": 160}},
            "preview too tall to hold": {**good, "preview": {"width": 1600, "height": colour.MAX_PREVIEW_HEIGHT + 1}},
            "not an object": [],
        }
        for name, value in bad.items():
            with self.subTest(name):
                with self.assertRaises(colour.ColourError):
                    colour.settings(value)


class Mapping(unittest.TestCase):
    def test_hsl_is_csss_as_colorsys_computes_it(self) -> None:
        self.assertEqual(colour.hsl_to_rgb(numpy.array([0, 120, 240]), 1.0, numpy.full(3, 0.5)).tolist(),
                         [[255, 0, 0], [0, 255, 0], [0, 0, 255]])
        generator = numpy.random.default_rng(1)
        hue, saturation, light = generator.uniform(0, 360, 500), generator.uniform(0, 1, 500), generator.uniform(
            0, 1, 500)
        mine = colour.hsl_to_rgb(hue, saturation, light).astype(int)
        theirs = numpy.array([[round(v * 255) for v in colorsys.hls_to_rgb(h / 360, l, s)]
                              for h, s, l in zip(hue, saturation, light)])
        self.assertLessEqual(int(numpy.abs(mine - theirs).max()), 1)

    def test_centroid_hue_is_log_frequency_held_at_the_limits(self) -> None:
        hue = colour.parameters()["hue"]
        found = colour.centroid_hue(numpy.array([0.0, 50, 100, math.sqrt(100 * 8000), 8000, 11025]), hue)
        self.assertTrue(numpy.allclose(found, [0, 0, 0, 120, 240, 240]))

    def test_chroma_hue_follows_the_strongest_pitch_class(self) -> None:
        one_hot = numpy.eye(12)
        for order, step in (("fifths", 210), ("chromatic", 30)):
            with self.subTest(order):
                hue, concentration = colour.chroma_hue(one_hot, {"order": order, "reference_hue": 15})
                self.assertTrue(numpy.allclose(hue, (numpy.arange(12) * step + 15) % 360))
                self.assertTrue(numpy.allclose(concentration, 1))
        # Every pitch class alike, and silence, have no hue to speak of.
        _, flat = colour.chroma_hue(numpy.ones((1, 12)), {"order": "fifths", "reference_hue": 0})
        _, silent = colour.chroma_hue(numpy.zeros((1, 12)), {"order": "fifths", "reference_hue": 0})
        self.assertAlmostEqual(float(flat[0]), 0, places=6)
        self.assertEqual(float(silent[0]), 0)
        # On the circle of fifths a fifth is a near neighbour; chromatically it is far.
        triad = numpy.zeros((1, 12))
        triad[0, [0, 4, 7]] = 1  # A, C sharp, E
        self.assertGreater(float(colour.chroma_hue(triad, {"order": "fifths", "reference_hue": 0})[1][0]),
                           float(colour.chroma_hue(triad, {"order": "chromatic", "reference_hue": 0})[1][0]))

    def test_a_pixel_centre_is_the_right_frames_row(self) -> None:
        grid = {"sample_rate": 22050, "hop": 2048, "frame": 4096, "first_centre": 0}
        positions = colour.frame_positions(1000, 441, 44100, grid)
        # Pixel k is centred (k + 0.5) x 10 ms in; row r is centred r x 2048 / 22050 s in.
        self.assertAlmostEqual(float(positions[0]), 0.005 * 22050 / 2048)
        self.assertAlmostEqual(float(positions[999]), 9.995 * 22050 / 2048)
        series = numpy.arange(10, dtype=numpy.float32)[:, None]
        self.assertEqual(colour.interpolate(series, numpy.array([-3, 0, 2.25, 9, 40]))[:, 0].tolist(),
                         [0, 0, 2.25, 9, 9])

    def test_series_of_another_length_are_refused(self) -> None:
        grid = {"sample_rate": 22050, "hop": 2048, "frame": 4096, "first_centre": 0}
        colour.check_lengths(1000, 441, 44100, math.ceil(10 * 22050 / 2048) + 1, grid)
        with self.assertRaisesRegex(colour.ColourError, "not measures of the same audio"):
            colour.check_lengths(1000, 441, 44100, math.ceil(20 * 22050 / 2048) + 1, grid)
        with self.assertRaisesRegex(colour.ColourError, "not measures of the same audio"):
            colour.check_lengths(1000, 441, 44100, 50, grid)

    def test_a_recording_reference_puts_its_loudest_frame_at_the_ceiling(self) -> None:
        chosen = colour.settings(colour.parameters())
        levels = numpy.array([-40.0, -30.0])
        self.assertEqual(colour.lightness_range(levels, chosen), (-60, 0))
        relative = colour.settings({**colour.parameters(),
                                    "lightness": {**colour.parameters()["lightness"], "reference": "recording"}})
        self.assertEqual(colour.lightness_range(levels, relative), (-90, -30))

    def test_a_recording_quieter_than_the_floor_is_not_drawn_light(self) -> None:
        relative = colour.settings({**colour.parameters(),
                                    "lightness": {**colour.parameters()["lightness"], "reference": "recording"}})
        silent = numpy.full(10, -120.0)
        floor_db, ceiling_db = colour.lightness_range(silent, relative)
        self.assertEqual((floor_db, ceiling_db), (-120, -60))
        self.assertTrue(numpy.allclose(colour.lightness(silent, floor_db, ceiling_db, relative), relative.darkest))
        # Just below the floor, the loudest frame is nearly, not quite, the lightest.
        quiet = colour.lightness(numpy.array([-61.0]), *colour.lightness_range(numpy.array([-61.0]), relative),
                                 relative)
        self.assertLess(float(quiet[0]), relative.lightest)
        self.assertGreater(float(quiet[0]), relative.darkest + 0.9 * (relative.lightest - relative.darkest))

    def test_no_frames_have_no_lightness_range(self) -> None:
        for reference in ("full-scale", "recording"):
            with self.subTest(reference):
                chosen = colour.settings({**colour.parameters(), "lightness": {
                    **colour.parameters()["lightness"], "reference": reference}})
                with self.assertRaisesRegex(colour.ColourError, "no frames"):
                    colour.lightness_range(numpy.zeros(0), chosen)


class Palette(unittest.TestCase):
    """The palette as the program's own writer makes it, and what its check refuses."""

    GRID = {"sample_rate": 22050, "hop": 2048, "frame": 4096, "first_centre": 0}

    def written(self, hue: dict | None = None) -> dict:
        parameters = {**colour.parameters(), **({"hue": hue} if hue else {})}
        return json.loads(json.dumps(colour.palette(colour.settings(parameters), parameters, -60.0, 0.0, 800, 441,
                                                    44100, 2, {"name": "frames", **self.GRID})))

    def test_the_writers_palettes_pass_their_check(self) -> None:
        centroid = self.written()
        chroma = self.written({"source": "chroma", "order": "fifths", "reference_hue": 0})
        for described in (centroid, chroma):
            colour.check_palette(described)
        self.assertEqual(centroid["frames"], self.GRID)  # the grid's name is not a grid field
        self.assertEqual(centroid["peaks"]["kind"], "peaks-1")
        self.assertEqual(chroma["hue"]["stops"][4]["value"], "C#")

    def test_any_other_shape_is_refused(self) -> None:
        good = self.written()

        def changed(path: str, value) -> dict:
            copy = json.loads(json.dumps(good))
            *parents, last = path.split(".")
            target = copy
            for key in parents:
                target = target[int(key)] if isinstance(target, list) else target[key]
            if value is KeyError:
                del target[last]
            elif isinstance(target, list):
                target[int(last)] = value
            else:
                target[last] = value
            return copy

        bad = {
            "other contract": changed("contract", "archive-colour-palette/1"),
            "extra field": changed("legend", []),
            "missing field": changed("frames", KeyError),
            "parameters it would refuse": changed("parameters.interpolation", "cubic"),
            "rows and pixels differ": changed("colour.rows", 799),
            "no rows": changed("colour.rows", 0),
            "other columns": changed("colour.columns", ["r", "g", "b"]),
            "peaks of another kind": changed("peaks.kind", "peaks-2"),
            "peaks rate as text": changed("peaks.sample_rate", "44100"),
            "boolean channels": changed("peaks.channels", True),
            "grid without a hop": changed("frames.hop", KeyError),
            "grid with a name": changed("frames.name", "frames"),
            "hue source unlike the parameters": changed("hue.source", "chroma"),
            "one hue stop": changed("hue.stops", good["hue"]["stops"][:1]),
            "hue stop in another unit": changed("hue.stops.0.unit", "kHz"),
            "hue stop off the circle": changed("hue.stops.0.hue", 400),
            "hue stop without rgb": changed("hue.stops.0.rgb", KeyError),
            "rgb not hex": changed("hue.stops.0.rgb", "red"),
            "saturation unlike the source": changed("saturation", "fixed-ish"),
            "floor above ceiling": changed("lightness.floor_db", 10),
            "infinite floor": changed("lightness.floor_db", float("-inf")),
            "other darkest": changed("lightness.darkest", 0.2),
            "lightness above one": changed("lightness.stops.0.lightness", 1.5),
            "lightness in another unit": changed("lightness.unit", "LUFS"),
            "not an object": [],
        }
        for name, value in bad.items():
            with self.subTest(name):
                with self.assertRaises(colour.ColourError):
                    colour.check_palette(value)


class Software(unittest.TestCase):
    def test_a_version_it_cannot_read_is_refused_not_recorded_as_unknown(self) -> None:
        directory = scratch(self)
        good = fake_program(directory, "good", 'echo "ffprobe version 9.9-test Copyright"')
        self.assertEqual(colour.program_version(good, "ffprobe"), "9.9-test")
        cases = {
            "another program's answer": fake_program(directory, "other", 'echo "something version 1"'),
            "no answer": fake_program(directory, "quiet", "true"),
            "a failure": fake_program(directory, "failing", 'echo "ffprobe version 9.9"; exit 3'),
            "no program": str(directory / "absent"),
        }
        for name, program in cases.items():
            with self.subTest(name):
                with self.assertRaises(colour.ColourError):
                    colour.program_version(program, "ffprobe")


class Preview(unittest.TestCase):
    def test_silence_draws_the_centre_and_a_full_scale_column_the_whole_height(self) -> None:
        peaks = numpy.zeros((4, 1, 2), dtype=numpy.int8)
        peaks[2:, 0] = [-128, 127]
        rgb = numpy.array([[10, 20, 30]] * 2 + [[200, 100, 0]] * 2, dtype=numpy.uint8)
        image = colour.render(peaks, rgb, 4, 9)
        self.assertEqual(image[:, 0, 3].tolist(), [0, 0, 0, 0, 255, 0, 0, 0, 0])
        self.assertEqual(image[4, 0, :3].tolist(), [10, 20, 30])
        self.assertEqual(image[:, 3, 3].tolist(), [255] * 9)
        self.assertEqual(image[0, 3, :3].tolist(), [200, 100, 0])
        # Transparent is transparent black, so the image does not hide colours behind alpha 0.
        self.assertEqual(image[0, 0].tolist(), [0, 0, 0, 0])

    def test_a_preview_wider_than_the_recording_repeats_pixels(self) -> None:
        peaks = numpy.array([[[-10, 10]], [[-100, 100]]], dtype=numpy.int8)
        rgb = numpy.array([[255, 0, 0], [0, 0, 255]], dtype=numpy.uint8)
        image = colour.render(peaks, rgb, 6, 33)
        self.assertEqual([image[16, x, :3].tolist() for x in range(6)],
                         [[255, 0, 0]] * 3 + [[0, 0, 255]] * 3)

    @needs_ffmpeg
    def test_the_png_decodes_elsewhere_to_the_same_pixels(self) -> None:
        directory = scratch(self)
        image = numpy.random.default_rng(5).integers(0, 256, (7, 13, 4), dtype=numpy.uint8)
        (directory / "x.png").write_bytes(colour.png(image))
        self.assertTrue(numpy.array_equal(decoded_png(directory / "x.png", 13, 7), image))


@needs_ffmpeg
@needs_essentia
class Colour(unittest.TestCase):
    def test_low_bright_and_silent_parts_get_their_colours_one_per_derived_media_pixel(self) -> None:
        directory = scratch(self)
        mono = support.write_wav(directory / "mono.wav", numpy.concatenate(
            [sine(110, 3, 0.5), bright_noise(3, 0.5), numpy.zeros(2 * RATE)]), RATE)
        audio = directory / "parts.wav"
        support.ffmpeg("-i", str(mono), "-ac", "2", "-c:a", "pcm_s16le", str(audio))
        code, answer = run(directory, audio)
        self.assertEqual(code, 0, answer)
        self.assertEqual({k: v["status"] for k, v in answer["extractors"].items()},
                         {"frames": "ran", "colour": "ran"})
        found = entry(directory, audio)
        digest = bank.file_digest(audio)

        # One colour per pixel of the peaks enrich/media.py serves for this
        # content, and no copy of those peaks in the bank.
        self.assertEqual(set(found.manifest["outputs"]), {"colour", "palette", "preview"})
        self.assertEqual([p.name for p in found.path.iterdir() if p.suffix == ".dat"], [])
        rate, pixel, peaks = derived_peaks(directory, audio)
        self.assertEqual((rate, pixel, peaks.shape), (RATE, 441, (800, 2, 2)))
        self.assertEqual({s["name"] for s in found.manifest["software"]},
                         {"ffmpeg", "ffprobe", "numpy", "zlib", "python"})

        rgb = found.series("colour")
        self.assertEqual(rgb.shape, (800, 3))
        self.assertEqual(rgb.dtype, numpy.uint8)
        self.assertEqual(found.grid("colour"), {"sample_rate": 2 * RATE, "hop": 882, "frame": 882,
                                                "first_centre": 441})
        hue, light, _ = hues_and_lightness(rgb)
        low, bright, silent = slice(30, 270), slice(330, 570), slice(630, 770)
        # Anchor: the parts differ, so the comparisons below are about them.
        self.assertGreater(abs(float(numpy.median(hue[bright])) - float(numpy.median(hue[low]))), 90)
        self.assertLess(float(numpy.median(hue[low])), 60)  # red to orange: a low centroid
        self.assertGreater(float(numpy.median(hue[bright])), 180)  # towards blue: a high one
        chosen = colour.settings(colour.parameters())
        self.assertTrue(numpy.allclose(light[silent], chosen.darkest, atol=0.005))
        # A sine of amplitude 0.5 is -9.03 dBFS; ffmpeg's mono-to-stereo upmix puts
        # each channel 3 dB down, and frames reads their mean: -12.04 dBFS.
        level = float(numpy.median(bank.Bank(directory / "bank").entry(digest, "frames").series("rms_db")[5:25]))
        self.assertAlmostEqual(level, 20 * math.log10(0.5 / math.sqrt(2)) - 3.01, delta=0.05)
        expected = chosen.darkest + (level + 60) / 60 * (chosen.lightest - chosen.darkest)
        self.assertAlmostEqual(float(numpy.median(light[low])), expected, delta=0.01)

        described = json.loads(found.file("palette").read_text())
        colour.check_palette(described)
        self.assertEqual(described["contract"], colour.PALETTE_CONTRACT)
        self.assertEqual(described["parameters"], colour.parameters())
        self.assertEqual(described["colour"]["rows"], 800)
        self.assertEqual(described["peaks"], {"kind": "peaks-1", "format": "audiowaveform binary version 2, 8-bit",
                                              "sample_rate": RATE, "samples_per_pixel": 441, "pixels": 800,
                                              "channels": 2})
        self.assertEqual(described["frames"], bank.Bank(directory / "bank").entry(digest, "frames").grid("hpcp"))
        self.assertEqual([s["value"] for s in described["hue"]["stops"]],
                         [100, 125, 250, 500, 1000, 2000, 4000, 8000])
        self.assertEqual((described["lightness"]["floor_db"], described["lightness"]["ceiling_db"]), (-60, 0))

        width, height = colour.parameters()["preview"]["width"], colour.parameters()["preview"]["height"]
        shown = decoded_png(found.file("preview"), width, height)
        self.assertTrue(numpy.array_equal(shown, colour.render(peaks, rgb, width, height)))
        self.assertGreater(int((shown[..., 3] > 0).sum()), width)  # more than a centre line

    def test_the_colour_changes_where_the_frames_say_and_leads_the_onset(self) -> None:
        # Silence, then a sine from exactly 5.000 s: pixel 500 at 100 pixels a second.
        for rate in (44100, 48000):
            with self.subTest(rate=rate):
                directory = scratch(self)
                audio = support.write_wav(directory / "step.wav", numpy.concatenate(
                    [numpy.zeros(5 * rate), sine(440, 3, 0.5, rate)]), rate)
                code, answer = run(directory, audio)
                self.assertEqual(code, 0, answer)
                found = entry(directory, audio)
                _, pixel, peaks = derived_peaks(directory, audio)
                onset = int(numpy.argmax(peaks[:, 0, 1] > 0))
                self.assertEqual(onset, 500)

                # The oracle: the time the frames series itself reaches the
                # level drawn at mid lightness (-30 dBFS between -60 and 0), from
                # its rows' own times, row r centred at first_centre + r x hop.
                frames = bank.Bank(directory / "bank").entry(bank.file_digest(audio), "frames")
                grid = frames.grid("rms_db")
                row = crossing(frames.series("rms_db")[:, 0].astype(numpy.float64), -30.0)
                seconds = (grid["first_centre"] + row * grid["hop"]) / grid["sample_rate"]
                expected = seconds * rate / pixel - 0.5  # pixel k is centred at (k + 0.5) x pixel / rate
                chosen = colour.settings(colour.parameters())
                _, light, _ = hues_and_lightness(found.series("colour"))
                measured = crossing(light, (chosen.darkest + chosen.lightest) / 2)
                # Half a pixel, or a frame hop (9.3 pixels), off would fail this.
                self.assertAlmostEqual(measured, expected, delta=0.15)
                # The README's stated lead: a 4,096-sample window and a dB ramp
                # up from the -120 dBFS floor reach mid lightness 5 to 12 pixels
                # (50 to 120 ms) before the onset.
                self.assertGreater(onset - measured, 5)
                self.assertLess(onset - measured, 12)

    def test_a_second_run_is_a_hit_that_starts_nothing(self) -> None:
        directory = scratch(self)
        audio = support.write_wav(directory / "tone.wav", sine(440, 2, 0.3), RATE)

        def bomb(*_args, **_kwargs):
            raise AssertionError("a cache hit started a program")
        # Anchor: on a miss, the bomb goes off.
        with patch.object(extract.subprocess, "Popen", bomb):
            with self.assertRaisesRegex(AssertionError, "cache hit"):
                run(directory, audio)
        self.assertEqual(run(directory, audio)[0], 0)
        with patch.object(extract.subprocess, "Popen", bomb):
            code, answer = run(directory, audio)
        self.assertEqual(code, 0, answer)
        self.assertEqual({k: v["status"] for k, v in answer["extractors"].items()},
                         {"frames": "hit", "colour": "hit"})

    def test_chroma_hue_follows_the_pitch_class(self) -> None:
        directory = scratch(self)
        # A, then C sharp, then noise, each with an octave partial two octaves
        # up. frames' HPCP (cover's settings) normalises two bands apart, and
        # its spectral peaks are reported at twice their frequency (SpectralPeaks
        # assumes 44,100 Hz on 22,050 Hz audio), so the split is really 250 Hz:
        # a tone on one side alone lets the other band's noise peaks reach
        # full weight. A third harmonic would add the fifth.
        def tone(frequency: float) -> numpy.ndarray:
            return sine(frequency, 3, 0.2) + sine(4 * frequency, 3, 0.1)
        audio = support.write_wav(directory / "pitches.wav", numpy.concatenate(
            [tone(220.0), tone(138.59), numpy.random.default_rng(4).standard_normal(3 * RATE) * 0.2]), RATE)
        flag, path, chroma = configured(directory, "chroma",
                                        hue={"source": "chroma", "order": "fifths", "reference_hue": 0})
        code, answer = run(directory, audio, flag, path)
        self.assertEqual(code, 0, answer)
        found = entry(directory, audio, chroma)
        hue, _, saturation = hues_and_lightness(found.series("colour"))
        a, c_sharp, noise = slice(30, 270), slice(330, 570), slice(630, 870)

        def circular_median(values: numpy.ndarray) -> float:
            return float(numpy.rad2deg(numpy.angle(numpy.exp(1j * numpy.deg2rad(values)).mean())) % 360)
        # A is 0 degrees; C sharp is four semitones up, 4 x 210 = 840, so 120 on the circle of fifths.
        self.assertLess(min(circular_median(hue[a]), 360 - circular_median(hue[a])), 15)
        self.assertLess(abs(circular_median(hue[c_sharp]) - 120), 15)
        self.assertGreater(float(numpy.median(saturation[a])), 2 * float(numpy.median(saturation[noise])))
        self.assertEqual(json.loads(found.file("palette").read_text())["hue"]["stops"][4]["value"], "C#")
        # The default mapping is another entry; both stand.
        self.assertEqual(run(directory, audio)[0], 0)
        self.assertNotEqual(entry(directory, audio).manifest["key"], found.manifest["key"])

    def test_lightness_relative_to_the_recording_and_clipping(self) -> None:
        directory = scratch(self)
        quiet = support.write_wav(directory / "quiet.wav", sine(440, 2, 10 ** (-27 / 20)), RATE)  # -30 dBFS RMS
        clipped = support.write_wav(directory / "clipped.wav", sine(440, 2, 3.0), RATE)
        flag, path, relative = configured(directory, "relative",
                                          lightness={**colour.parameters()["lightness"], "reference": "recording"})
        chosen = colour.settings(colour.parameters())
        for audio in (quiet, clipped):
            self.assertEqual(run(directory, audio)[0], 0)
            self.assertEqual(run(directory, audio, flag, path)[0], 0)
        middle = slice(20, 180)
        _, absolute_light, _ = hues_and_lightness(entry(directory, quiet).series("colour")[middle])
        _, relative_light, _ = hues_and_lightness(entry(directory, quiet, relative).series("colour")[middle])
        self.assertAlmostEqual(float(numpy.median(absolute_light)),
                               chosen.darkest + 0.5 * (chosen.lightest - chosen.darkest), delta=0.01)
        self.assertAlmostEqual(float(numpy.median(relative_light)), chosen.lightest, delta=0.01)
        ceiling = json.loads(entry(directory, quiet, relative).file("palette").read_text())["lightness"]["ceiling_db"]
        self.assertAlmostEqual(ceiling, -30, delta=0.2)
        # Clipped: the peaks reach both rails and the colour is as light as it gets.
        _, _, peaks = derived_peaks(directory, clipped)
        self.assertEqual((int(peaks[:, 0, 0].min()), int(peaks[:, 0, 1].max())), (-128, 127))
        _, clipped_light, _ = hues_and_lightness(entry(directory, clipped).series("colour")[middle])
        self.assertAlmostEqual(float(numpy.median(clipped_light)), chosen.lightest, delta=0.01)

    def test_a_video_is_coloured_from_its_audio(self) -> None:
        directory = scratch(self)
        video = directory / "clip.mp4"
        support.ffmpeg("-f", "lavfi", "-i", "testsrc=size=64x48:rate=10:duration=2", "-f", "lavfi",
                       "-i", "sine=frequency=440:sample_rate=48000:duration=2", "-c:v", "mpeg4", "-c:a", "aac",
                       "-shortest", str(video))
        code, answer = run(directory, video)
        self.assertEqual(code, 0, answer)
        self.assertEqual(answer["media_type"], "video/mp4")
        rate, pixel, peaks = derived_peaks(directory, video)
        self.assertEqual((rate, pixel), (48000, 480))
        self.assertEqual(json.loads(entry(directory, video).file("palette").read_text())["peaks"]["pixels"], len(peaks))
        self.assertEqual(entry(directory, video).series("colour").shape, (len(peaks), 3))


@needs_ffmpeg
@needs_essentia
class Failures(unittest.TestCase):
    def test_other_settings_are_refused_and_nothing_is_filed(self) -> None:
        directory = scratch(self)
        audio = support.write_wav(directory / "tone.wav", sine(440, 1, 0.3), RATE)
        flag, path, _ = configured(directory, "bad", interpolation="cubic")
        code, answer = run(directory, audio, flag, path)
        self.assertEqual(code, 1)
        self.assertEqual(answer["extractors"]["frames"]["status"], "ran")
        self.assertEqual(answer["extractors"]["colour"]["status"], "failed")
        self.assertIn("interpolation 'cubic' is not implemented", answer["extractors"]["colour"]["log"])
        self.assertEqual(list((directory / "bank").glob("sha256/*/*/*/bank/colour/*/entry.json")), [])

    def test_two_audio_streams_are_refused_rather_than_guessed(self) -> None:
        directory = scratch(self)
        audio = support.two_stream_mka(directory / "two.mka")
        code, answer = run(directory, audio)
        self.assertEqual(code, 1)
        self.assertEqual(answer["extractors"]["frames"]["status"], "ran")  # frames reads the first stream
        self.assertIn("exactly one is supported", answer["extractors"]["colour"]["log"])

    def test_no_samples_fails_frames_and_blocks_colour(self) -> None:
        directory = scratch(self)
        code, answer = run(directory, support.empty_wav(directory / "empty.wav"))
        self.assertEqual(code, 1)
        self.assertEqual(answer["extractors"]["colour"]["status"], "blocked")

    def test_a_request_without_its_series_or_with_damaged_ones_fails(self) -> None:
        directory = scratch(self)
        audio = support.write_wav(directory / "tone.wav", sine(440, 1, 0.3), RATE)
        self.assertEqual(run(directory, audio)[0], 0)
        frames = bank.Bank(directory / "bank").entry(bank.file_digest(audio), "frames")
        needs = {}
        for feature, columns in (("frames/centroid", 1), ("frames/hpcp", 12), ("frames/rms_db", 1)):
            name = feature.split("/")[1]
            needs[feature] = {**frames.output(name), "path": str(frames.path / frames.output(name)["file"]),
                              "grid": {"name": "frames", **frames.grid(name)}}
        out = directory / "out"
        out.mkdir()
        request = {"parameters": colour.parameters(), "input": {"path": str(audio)}, "needs": needs,
                   "output_dir": str(out)}
        # Anchor: the hand-made request works, so what follows fails for its own reason.
        self.assertEqual(colour.make(request)["outputs"]["colour"], {"shape": [100, 3]})
        cases = {
            "missing": ({k: v for k, v in needs.items() if k != "frames/hpcp"}, "does not provide frames/hpcp"),
            "short": ({**needs, "frames/rms_db": {**needs["frames/rms_db"], "shape": [frames.output("rms_db")[
                "shape"][0] + 1, 1]}}, "holds"),
            "other grid": ({**needs, "frames/centroid": {**needs["frames/centroid"],
                                                         "grid": {**needs["frames/centroid"]["grid"], "hop": 1024}}},
                           "not on one grid"),
            "wrong columns": ({**needs, "frames/hpcp": {**needs["frames/hpcp"], "columns": 1}}, "not an f32 series"),
        }
        for name, (given, message) in cases.items():
            with self.subTest(name):
                with self.assertRaisesRegex(colour.ColourError, message):
                    colour.make({**request, "needs": given})
        # Through main, a failure is a message on stderr and exit 1, never a result.
        for leftover in out.iterdir():
            leftover.unlink()
        (directory / "request.json").write_text(json.dumps({**request, "needs": {}}))
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(colour.main([str(directory / "request.json")]), 1)
        self.assertIn("does not provide", err.getvalue())
        self.assertFalse((out / "result.json").exists())

    def test_a_file_ffmpeg_cannot_decode_fails_with_its_reason(self) -> None:
        directory = scratch(self)
        audio = support.write_wav(directory / "tone.wav", sine(440, 1, 0.3), RATE)
        self.assertEqual(run(directory, audio)[0], 0)
        frames = bank.Bank(directory / "bank").entry(bank.file_digest(audio), "frames")
        needs = {f: {**frames.output(f.split("/")[1]), "path": str(frames.path / frames.output(f.split("/")[1])["file"]),
                     "grid": {"name": "frames", **frames.grid(f.split("/")[1])}} for f in colour.NEEDS}
        junk = directory / "junk.wav"
        junk.write_bytes(b"RIFF this is not audio")
        out = directory / "out"
        out.mkdir()
        with self.assertRaises(media.MediaError):  # the same class as waveform.MediaError
            colour.make({"parameters": colour.parameters(), "input": {"path": str(junk)}, "needs": needs,
                         "output_dir": str(out)})


if __name__ == "__main__":
    unittest.main()
