"""Derived media: peaks and listening copies (ADR 0069).

The audio is synthetic, made with numpy or ffmpeg's lavfi sources. The tests
run the real ffmpeg and ffprobe; without them they are skipped and say so,
unless ARCHIVE_REQUIRE_INTEGRATION is set, when they fail.
"""

from __future__ import annotations

import io
import json
import math
import os
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
import wave
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "enrich"))

import derived  # noqa: E402
import media  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
if os.environ.get("ARCHIVE_REQUIRE_INTEGRATION") and not (FFMPEG and FFPROBE):
    raise RuntimeError("ARCHIVE_REQUIRE_INTEGRATION is set but ffmpeg or ffprobe is not on PATH")
needs_ffmpeg = unittest.skipUnless(FFMPEG and FFPROBE, "put ffmpeg and ffprobe on PATH to make derived media")


def write_wav(path: Path, samples: np.ndarray, rate: int) -> Path:
    """A 16-bit PCM WAV of ``samples`` (frames x channels, int16)."""
    with wave.open(str(path), "wb") as out:
        out.setnchannels(samples.shape[1])
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(samples.astype("<i2").tobytes())
    return path


def tones(path: Path, rate: int, channels: int, seconds: float = 2.0) -> Path:
    """A WAV with a different sine on each channel, from ffmpeg's lavfi."""
    expressions = "|".join(f"0.5*sin(2*PI*{220 * (n + 1)}*t)" for n in range(channels))
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"aevalsrc=exprs={expressions}:s={rate}:d={seconds}", "-c:a", "pcm_s16le", str(path)], check=True)
    return path


def digest_of(path: Path) -> str:
    return media.content_digest(path)


def expected_peaks(samples: np.ndarray, pixel: int) -> bytes:
    """audiowaveform's 8-bit rule, computed independently: each 16-bit
    sample shifted right by 8, minimum and maximum per pixel per channel."""
    shifted = samples.astype(np.int16) >> 8
    out = bytearray()
    for start in range(0, len(shifted), pixel):
        block = shifted[start:start + pixel]
        for channel in range(samples.shape[1]):
            out += struct.pack("<bb", int(block[:, channel].min()), int(block[:, channel].max()))
    return bytes(out)


def probe_streams(path: Path) -> dict:
    probed = subprocess.run([FFPROBE, "-v", "error", "-show_entries",
                             "stream=codec_name,profile,channels,sample_rate,bit_rate:format=format_name",
                             "-of", "json", str(path)], capture_output=True, text=True, check=True)
    return json.loads(probed.stdout)


def top_level_boxes(path: Path) -> list[bytes]:
    data, boxes, offset = path.read_bytes(), [], 0
    while offset < len(data):
        size, kind = struct.unpack(">I4s", data[offset:offset + 8])
        boxes.append(kind)
        offset += size
    return boxes


def bomb(*args, **kwargs):
    raise AssertionError("a rerun on complete outputs must not decode or write")


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="enrich-media-test-"))
        self.addCleanup(self.remove)
        self.root = self.tmp / "derived"
        self.root.mkdir()

    def remove(self) -> None:
        for directory, _, files in os.walk(self.tmp):
            os.chmod(directory, 0o700)
            for name in files:
                os.chmod(os.path.join(directory, name), 0o600)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def files(self) -> list[Path]:
        return sorted(p for p in self.root.rglob("*") if p.is_file())


class LayoutChecks(unittest.TestCase):
    def test_paths_fan_out_by_digest_and_name_the_method_version(self) -> None:
        digest = "sha256:" + "ab" + "cd" + "e" * 60
        root = Path("/derived")
        self.assertEqual(derived.path(root, digest, "peaks"), root / "sha256/ab/cd" / digest[7:] / "peaks-1.dat")
        self.assertEqual(derived.path(root, digest, "listen"), root / "sha256/ab/cd" / digest[7:] / "listen-1.mp4")
        self.assertEqual(derived.etag(digest, "listen"), f'"{digest}/listen-1"')
        self.assertNotEqual(derived.etag(digest, "peaks"), derived.etag(digest, "listen"))

    def test_only_canonical_digests_name_a_directory(self) -> None:
        for bad in ("sha256:" + "A" * 64, "f" * 64, "sha256:" + "f" * 63, "sha256:../" + "f" * 61, None):
            with self.subTest(bad), self.assertRaises(ValueError):
                derived.directory(Path("/derived"), bad)

    def test_samples_per_pixel_is_the_rate_over_100_rounded_half_to_even(self) -> None:
        cases = {48000: 480, 44100: 441, 22050: 220, 22150: 222, 8000: 80, 96000: 960, 150: 2, 50: 0}
        for rate, pixel in cases.items():
            with self.subTest(rate=rate):
                if pixel == 0:
                    with self.assertRaises(media.MediaError):
                        media.samples_per_pixel(rate)
                else:
                    self.assertEqual(media.samples_per_pixel(rate), pixel)


class CompletenessChecks(Fixture):
    """The checks that decide a rerun writes nothing, on bytes made here."""

    def box(self, kind: bytes, body: bytes = b"") -> bytes:
        return struct.pack(">I4s", 8 + len(body), kind) + body

    def write(self, name: str, data: bytes) -> Path:
        path = self.tmp / name
        path.write_bytes(data)
        return path

    def test_an_mp4_is_complete_only_when_its_boxes_fill_it_with_the_index_first(self) -> None:
        ftyp, moov, mdat = self.box(b"ftyp", b"isom"), self.box(b"moov", b"x" * 4), self.box(b"mdat", b"y" * 9)
        large_mdat = struct.pack(">I4sQ", 1, b"mdat", 16 + 9) + b"y" * 9
        to_end = struct.pack(">I4s", 0, b"mdat") + b"y" * 9
        cases = {
            "index first": (ftyp + moov + mdat, True),
            "64-bit size": (ftyp + moov + large_mdat, True),
            "last box to the end": (ftyp + moov + to_end, True),
            "index last": (ftyp + mdat + moov, False),
            "no index": (ftyp + mdat, False),
            "no ftyp first": (moov + ftyp + mdat, False),
            "truncated": ((ftyp + moov + mdat)[:-1], False),
            "trailing bytes": (ftyp + moov + mdat + b"\0\0\0", False),
            "truncated 64-bit size": (ftyp + moov + struct.pack(">I4s", 1, b"mdat") + b"\0\0", False),
            "size below its header": (ftyp + moov + struct.pack(">I4s", 4, b"mdat"), False),
            "empty": (b"", False),
        }
        for name, (data, complete) in cases.items():
            with self.subTest(name):
                self.assertEqual(media.mp4_complete(self.write("copy.mp4", data)), complete)

    def test_a_peaks_file_is_complete_only_when_its_header_matches_its_size(self) -> None:
        header = lambda length, channels=2, version=2, flags=1: media.PEAKS_HEADER.pack(  # noqa: E731
            version, flags, 48000, 480, length, channels)
        cases = {
            "whole": (header(3) + b"\0" * 12, True),
            "short": (header(3) + b"\0" * 11, False),
            "long": (header(3) + b"\0" * 13, False),
            "16-bit": (header(3, flags=0) + b"\0" * 12, False),
            "version 1": (header(3, version=1) + b"\0" * 12, False),
            "no channels": (header(3, channels=0), False),
            "half a header": (header(3)[:10], False),
        }
        for name, (data, complete) in cases.items():
            with self.subTest(name):
                self.assertEqual(media.peaks_complete(self.write("peaks.dat", data)), complete)

    def test_absent_is_incomplete_but_unreadable_is_an_error(self) -> None:
        self.assertFalse(media.peaks_complete(self.tmp / "absent.dat"))
        self.assertFalse(media.mp4_complete(self.tmp / "absent.mp4"))
        for name, check in (("peaks.dat", media.peaks_complete), ("copy.mp4", media.mp4_complete)):
            with self.subTest(name):
                path = self.write(name, b"\0" * 32)
                path.chmod(0)
                if os.access(path, os.R_OK):
                    self.skipTest("this user reads files whatever their mode")
                with self.assertRaises(PermissionError):
                    check(path)


@needs_ffmpeg
class PeaksChecks(Fixture):
    def test_peaks_follow_audiowaveforms_8_bit_rule_for_every_channel(self) -> None:
        rate, pixel = 22050, 220  # 220.5 rounds to even
        rng = np.random.default_rng(7)
        samples = rng.integers(-32768, 32767, size=(pixel * 37 + 101, 3), dtype=np.int16)
        samples[5] = (-32768, 32767, 0)  # the extremes and silence
        source = write_wav(self.tmp / "noise.wav", samples, rate)
        result = media.derive(source, digest_of(source), self.root)
        data = Path(result["outputs"]["peaks"]["path"]).read_bytes()
        header = media.PEAKS_HEADER.unpack(data[:media.PEAKS_HEADER.size])
        self.assertEqual(header, (2, 1, rate, pixel, math.ceil(len(samples) / pixel), 3))
        self.assertEqual(data[media.PEAKS_HEADER.size:], expected_peaks(samples, pixel))
        # Anchor: the oracle tells channels apart, so a swapped order would fail.
        self.assertNotEqual(expected_peaks(samples, pixel), expected_peaks(samples[:, ::-1], pixel))

    def test_a_recording_longer_than_one_decoded_chunk_is_reduced_whole(self) -> None:
        rate = 8000
        pixel = media.samples_per_pixel(rate)
        rng = np.random.default_rng(11)
        samples = rng.integers(-20000, 20000, size=(pixel * (media.PIXELS_PER_CHUNK * 2 + 3) + 7, 1), dtype=np.int16)
        source = write_wav(self.tmp / "long.wav", samples, rate)
        result = media.derive(source, digest_of(source), self.root)
        data = Path(result["outputs"]["peaks"]["path"]).read_bytes()
        self.assertEqual(data[media.PEAKS_HEADER.size:], expected_peaks(samples, pixel))

    def test_outputs_sit_at_the_layout_path_with_no_temporary_files(self) -> None:
        source = tones(self.tmp / "stereo.wav", 48000, 2)
        digest = digest_of(source)
        result = media.derive(source, digest, self.root)
        self.assertEqual(self.files(), sorted([derived.path(self.root, digest, "peaks"),
                                               derived.path(self.root, digest, "listen")]))
        self.assertEqual({kind: item["status"] for kind, item in result["outputs"].items()},
                         {"peaks": "written", "listen": "written"})

    def test_audio_that_decodes_to_nothing_is_refused(self) -> None:
        source = write_wav(self.tmp / "empty.wav", np.zeros((0, 2), dtype=np.int16), 48000)
        with self.assertRaisesRegex(media.MediaError, "no samples"):
            media.derive(source, digest_of(source), self.root)
        self.assertEqual(self.files(), [])


@needs_ffmpeg
class ListeningCopyChecks(Fixture):
    def test_more_than_two_channels_become_stereo_aac_lc_at_192k_index_first(self) -> None:
        source = tones(self.tmp / "six.wav", 48000, 6, seconds=4)
        result = media.derive(source, digest_of(source), self.root)
        copy = Path(result["outputs"]["listen"]["path"])
        probed = probe_streams(copy)
        [stream] = probed["streams"]
        self.assertEqual((stream["codec_name"], stream["profile"], stream["channels"], stream["sample_rate"]),
                         ("aac", "LC", 2, "48000"))
        self.assertLess(abs(int(stream["bit_rate"]) - 192000), 192000 * 0.1)
        self.assertIn("mp4", probed["format"]["format_name"])
        boxes = top_level_boxes(copy)
        self.assertLess(boxes.index(b"moov"), boxes.index(b"mdat"))
        # The peaks keep all six channels; only the listening copy is downmixed.
        self.assertEqual(media.PEAKS_HEADER.unpack(Path(result["outputs"]["peaks"]["path"]).read_bytes()[:24])[5], 6)

    def test_one_or_two_channels_are_kept(self) -> None:
        for channels in (1, 2):
            with self.subTest(channels=channels):
                source = tones(self.tmp / f"{channels}.wav", 44100, channels)
                copy = Path(media.derive(source, digest_of(source), self.root)["outputs"]["listen"]["path"])
                self.assertEqual(probe_streams(copy)["streams"][0]["channels"], channels)


@needs_ffmpeg
class RerunChecks(Fixture):
    def setUp(self) -> None:
        super().setUp()
        self.source = tones(self.tmp / "stereo.wav", 48000, 2)
        self.digest = digest_of(self.source)
        media.derive(self.source, self.digest, self.root)
        self.before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in self.files()}
        self.assertEqual(len(self.before), 2)

    def bombs(self):
        return [patch.object(media.subprocess, "Popen", bomb), patch.object(media.subprocess, "run", bomb),
                patch.object(media.os, "replace", bomb), patch.object(media.tempfile, "mkstemp", bomb)]

    def test_a_rerun_on_complete_outputs_decodes_and_writes_nothing(self) -> None:
        other = tones(self.tmp / "other.wav", 8000, 1)
        for item in self.bombs():
            item.start()
            self.addCleanup(item.stop)
        result = media.derive(self.source, self.digest, self.root)
        self.assertEqual({kind: item["status"] for kind, item in result["outputs"].items()},
                         {"peaks": "present", "listen": "present"})
        self.assertEqual({path: (path.read_bytes(), path.stat().st_mtime_ns) for path in self.files()}, self.before)
        # Anchor: the bombs are live, so making anything would have failed.
        with self.assertRaises(AssertionError):
            media.derive(other, digest_of(other), self.root)

    def test_an_incomplete_output_is_made_again_and_only_it(self) -> None:
        peaks = derived.path(self.root, self.digest, "peaks")
        original = peaks.read_bytes()
        peaks.chmod(0o600)
        peaks.write_bytes(original[:-1])
        with patch.object(media, "write_listening_copy", bomb):
            result = media.derive(self.source, self.digest, self.root)
        self.assertEqual((result["outputs"]["peaks"]["status"], result["outputs"]["listen"]["status"]),
                         ("written", "present"))
        self.assertEqual(peaks.read_bytes(), original)

    def test_a_truncated_listening_copy_is_made_again(self) -> None:
        copy = derived.path(self.root, self.digest, "listen")
        copy.chmod(0o600)
        copy.write_bytes(copy.read_bytes()[:100])
        with patch.object(media, "write_peaks", bomb):
            result = media.derive(self.source, self.digest, self.root)
        self.assertEqual(result["outputs"]["listen"]["status"], "written")
        self.assertTrue(media.mp4_complete(copy))

    def test_an_unreadable_output_is_an_error_not_a_reason_to_write(self) -> None:
        peaks = derived.path(self.root, self.digest, "peaks")
        peaks.chmod(0)
        if os.access(peaks, os.R_OK):
            self.skipTest("this user reads files whatever their mode")
        patches = self.bombs()
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        with self.assertRaises(PermissionError):
            media.derive(self.source, self.digest, self.root)


@needs_ffmpeg
class RefusalChecks(Fixture):
    def test_the_file_must_have_the_digest_asked_for(self) -> None:
        source = tones(self.tmp / "a.wav", 48000, 2)
        other = tones(self.tmp / "b.wav", 44100, 2)
        self.assertNotEqual(digest_of(source), digest_of(other))
        with self.assertRaisesRegex(media.MediaError, "not the"):
            media.derive(source, digest_of(other), self.root)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_a_malformed_digest_is_refused(self) -> None:
        source = tones(self.tmp / "a.wav", 48000, 2)
        for bad in (digest_of(source).upper(), digest_of(source)[7:], "sha256:../../x"):
            with self.subTest(bad), self.assertRaises(media.MediaError):
                media.derive(source, bad, self.root)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_the_root_must_be_an_existing_directory(self) -> None:
        source = tones(self.tmp / "a.wav", 48000, 2)
        with self.assertRaisesRegex(media.MediaError, "does not exist"):
            media.derive(source, digest_of(source), self.tmp / "absent")
        self.assertFalse((self.tmp / "absent").exists())
        with self.assertRaisesRegex(media.MediaError, "not a directory"):
            media.derive(source, digest_of(source), source)

    def test_a_file_without_audio_is_refused(self) -> None:
        source = self.tmp / "notes.txt"
        source.write_text("not audio\n")
        with self.assertRaisesRegex(media.MediaError, "ffprobe could not read"):
            media.derive(source, digest_of(source), self.root)
        self.assertEqual(self.files(), [])

    def test_two_audio_streams_are_refused(self) -> None:
        a, b = tones(self.tmp / "a.wav", 48000, 1), tones(self.tmp / "b.wav", 48000, 2)
        source = self.tmp / "two.mka"
        subprocess.run([FFMPEG, "-v", "error", "-i", str(a), "-i", str(b), "-map", "0:a", "-map", "1:a",
                        "-c", "copy", str(source)], check=True)
        with self.assertRaisesRegex(media.MediaError, "2 audio streams"):
            media.derive(source, digest_of(source), self.root)
        self.assertEqual(self.files(), [])

    def fake(self, name: str, script: str) -> str:
        path = self.tmp / name
        path.write_text("#!/bin/sh\n" + script)
        path.chmod(0o755)
        return str(path)

    def test_an_unusable_probe_is_refused(self) -> None:
        source = tones(self.tmp / "a.wav", 48000, 2)
        for name, script, message in (
                ("not-json", "echo not json\n", "JSON object"),
                ("a-list", "echo '[]'\n", "JSON object"),
                ("no-rate", 'echo \'{"streams": [{"channels": 2}]}\'\n', "sample rate"),
                ("zero-channels", 'echo \'{"streams": [{"sample_rate": "48000", "channels": 0}]}\'\n', "0 channels")):
            with self.subTest(name), self.assertRaisesRegex(media.MediaError, message):
                media.derive(source, digest_of(source), self.root, ffprobe=self.fake(name, script))
        self.assertEqual(self.files(), [])

    def test_a_failed_decode_or_encode_leaves_no_file_behind(self) -> None:
        source = tones(self.tmp / "a.wav", 48000, 2)
        failing = self.fake("ffmpeg-fails", "echo broken >&2\nexit 1\n")
        with self.assertRaisesRegex(media.MediaError, "could not decode .*broken"):
            media.derive(source, digest_of(source), self.root, ffmpeg=failing)
        self.assertEqual(self.files(), [])
        # The peaks made, the encoder fails: the peaks stay, nothing else does.
        with patch.object(media, "write_peaks", lambda *args: None):
            with self.assertRaisesRegex(media.MediaError, "could not encode .*broken"):
                media.derive(source, digest_of(source), self.root, ffmpeg=failing)
        self.assertEqual(self.files(), [derived.path(self.root, digest_of(source), "peaks")])

    def test_an_encoder_that_leaves_an_incomplete_file_is_refused(self) -> None:
        source = tones(self.tmp / "a.wav", 48000, 2)
        # Writes a truncated MP4 to its last argument, and succeeds.
        truncating = self.fake("ffmpeg-truncates", 'for last; do :; done\nprintf "\\000\\000\\000\\020ftyp" > "$last"\n')
        with patch.object(media, "write_peaks", lambda *args: None):
            with self.assertRaisesRegex(media.MediaError, "not a complete MP4"):
                media.derive(source, digest_of(source), self.root, ffmpeg=truncating)
        self.assertNotIn(derived.path(self.root, digest_of(source), "listen"), self.files())
        self.assertEqual([p for p in self.files() if p.name.endswith(".tmp")], [])

    def test_a_decode_that_ends_inside_a_frame_is_refused(self) -> None:
        source = tones(self.tmp / "a.wav", 48000, 2)
        odd = self.fake("ffmpeg-odd", "printf abc\n")
        with self.assertRaisesRegex(media.MediaError, "inside a sample frame"):
            media.derive(source, digest_of(source), self.root, ffmpeg=odd)
        self.assertEqual(self.files(), [])


@needs_ffmpeg
class CommandChecks(Fixture):
    def run_main(self, *arguments: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = media.main(list(arguments))
        return code, out.getvalue(), err.getvalue()

    def test_the_command_prints_what_it_made_and_then_that_it_is_present(self) -> None:
        source = tones(self.tmp / "a.wav", 48000, 2)
        digest = digest_of(source)
        code, out, _ = self.run_main("--root", str(self.root), "--digest", digest, str(source))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["outputs"]["peaks"]["status"], "written")
        code, out, _ = self.run_main("--root", str(self.root), "--digest", digest, str(source))
        self.assertEqual({kind: item["status"] for kind, item in json.loads(out)["outputs"].items()},
                         {"peaks": "present", "listen": "present"})

    def test_one_kind_is_made_alone_and_the_other_left_missing(self) -> None:
        source = tones(self.tmp / "a.wav", 48000, 2)
        digest = digest_of(source)
        # Peaks alone never encode: an importer makes them before listening copies.
        with patch.object(media, "write_listening_copy", bomb):
            code, out, _ = self.run_main("--root", str(self.root), "--digest", digest, "--kind", "peaks", str(source))
        self.assertEqual(code, 0)
        self.assertEqual({kind: item["status"] for kind, item in json.loads(out)["outputs"].items()},
                         {"peaks": "written"})
        self.assertTrue(derived.path(self.root, digest, "peaks").is_file())
        self.assertFalse(derived.path(self.root, digest, "listen").exists())
        with patch.object(media, "write_peaks", bomb):
            code, out, _ = self.run_main("--root", str(self.root), "--digest", digest, "--kind", "listen", str(source))
        self.assertEqual({kind: item["status"] for kind, item in json.loads(out)["outputs"].items()},
                         {"listen": "written"})

    def test_an_unknown_or_empty_kind_is_refused(self) -> None:
        source = tones(self.tmp / "a.wav", 48000, 2)
        for kinds in ((), ("waveform",)):
            with self.assertRaisesRegex(media.MediaError, "the kinds of derived media are"):
                media.derive(source, digest_of(source), self.root, kinds=kinds)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_a_failure_is_reported_as_json_and_exit_1(self) -> None:
        source = tones(self.tmp / "a.wav", 48000, 2)
        code, out, err = self.run_main("--root", str(self.root), "--digest", "sha256:" + "0" * 64, str(source))
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(json.loads(err)["status"], "failed")
        code, _, err = self.run_main("--root", str(self.root), "--digest", digest_of(source), str(self.tmp / "absent"))
        self.assertEqual((code, json.loads(err)["status"]), (1, "failed"))
        self.assertIn("FileNotFoundError", json.loads(err)["detail"])


if __name__ == "__main__":
    unittest.main()
