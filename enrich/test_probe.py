"""The probe tool on synthetic files: exact counts, and nothing when inexact (ADR 0065)."""

from __future__ import annotations

import json
import re
import stat
import unittest
import wave
from pathlib import Path

import adapter
import probe
import support
from support import needs_ffmpeg


def fake_program(directory: Path, name: str, script: str) -> str:
    path = directory / name
    path.write_text("#!/bin/sh\n" + script)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


@needs_ffmpeg
class Counting(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = support.scratch(self, "probe-")

    def test_a_stereo_wav_is_counted_frame_by_frame(self) -> None:
        wav = support.sine_wav(self.tmp / "a.wav", 44100, "1.5", channels=2)
        result = probe.probe(wav)
        self.assertEqual(result.refused, [])
        self.assertEqual([s.payload() for s in result.streams], [{
            "stream_index": 0, "codec": "pcm_s16le", "sample_rate": 44100, "channel_count": 2,
            "sample_count": 66150, "bits_per_sample": 16}])
        # Python's own reader of the header agrees with the decoded count.
        with wave.open(str(wav)) as reader:
            self.assertEqual(reader.getnframes(), 66150)

    def test_a_24_bit_wav_reports_its_bits(self) -> None:
        result = probe.probe(support.sine_wav(self.tmp / "a.wav", 48000, "1", codec="pcm_s24le"))
        self.assertEqual(result.streams[0].bits_per_sample, 24)
        self.assertEqual(result.streams[0].sample_count, 48000)

    def test_flac_is_counted_without_bits_per_sample(self) -> None:
        result = probe.probe(support.noise_flac(self.tmp / "b.flac", 48000, "1.25"))
        self.assertEqual([s.payload() for s in result.streams], [{
            "stream_index": 0, "codec": "flac", "sample_rate": 48000, "channel_count": 1, "sample_count": 60000}])

    def test_each_audio_stream_of_a_container_is_counted_on_its_own(self) -> None:
        result = probe.probe(support.two_stream_mka(self.tmp / "two.mka"))
        self.assertEqual([(s.stream_index, s.sample_rate, s.sample_count) for s in result.streams],
                         [(0, 44100, 44100), (1, 22050, 11025)])

    def test_a_file_without_audio_streams_counts_nothing_and_refuses_nothing(self) -> None:
        video = self.tmp / "v.mkv"
        support.ffmpeg("-f", "lavfi", "-i", "testsrc=d=0.2:s=32x32", "-c:v", "ffv1", str(video))
        result = probe.probe(video)
        self.assertEqual((result.streams, result.refused), ([], []))


@needs_ffmpeg
class NothingWhenInexact(unittest.TestCase):
    """A method that cannot count every frame proposes nothing for the stream."""

    def setUp(self) -> None:
        self.tmp = support.scratch(self, "probe-")

    def test_frames_at_two_rates_are_not_counted(self) -> None:
        joined = support.rate_changing_mp3(self.tmp / "joined.mp3")
        # Anchor: one part alone decodes and is counted.
        single = self.tmp / "single.mp3"
        support.ffmpeg("-f", "lavfi", "-i", "sine=sample_rate=48000:duration=1", "-c:a", "libmp3lame",
                       "-write_xing", "0", "-id3v2_version", "0", str(single))
        self.assertEqual(len(probe.probe(single).streams), 1)
        result = probe.probe(joined)
        self.assertEqual(result.streams, [])
        self.assertEqual(len(result.refused), 1)
        self.assertIn("change rate", result.refused[0]["reason"])

    def test_decoder_errors_refuse_even_when_frames_were_concealed(self) -> None:
        clean = support.noise_flac(self.tmp / "clean.flac", 48000, "1.25")
        self.assertEqual(probe.probe(clean).streams[0].sample_count, 60000)
        result = probe.probe(support.corrupted_flac(self.tmp / "bad.flac"))
        self.assertEqual(result.streams, [])
        self.assertIn("decoder reported errors", result.refused[0]["reason"])

    def test_a_stream_without_frames_is_not_claimed(self) -> None:
        result = probe.probe(support.empty_wav(self.tmp / "empty.wav"))
        self.assertEqual(result.streams, [])
        self.assertIn("no frame was decoded", result.refused[0]["reason"])

    def test_more_channels_than_a_claim_states_is_refused(self) -> None:
        path = self.tmp / "wide.wav"
        with wave.open(str(path), "wb") as writer:
            writer.setnchannels(65)
            writer.setsampwidth(2)
            writer.setframerate(8000)
            writer.writeframes(bytes(65 * 2 * 800))
        result = probe.probe(path)
        self.assertEqual(result.streams, [])
        self.assertIn("65 channels", result.refused[0]["reason"])

    def test_a_decoder_that_fails_is_not_counted(self) -> None:
        wav = support.sine_wav(self.tmp / "a.wav", 8000, "0.5")
        failing = fake_program(self.tmp, "ffmpeg", "exit 3\n")
        result = probe.probe(wav, ffmpeg=failing)
        self.assertEqual(result.streams, [])
        self.assertIn("exited 3", result.refused[0]["reason"])

    def test_a_stream_listed_without_index_or_codec_is_not_decoded(self) -> None:
        wav = support.sine_wav(self.tmp / "a.wav", 8000, "0.5")
        listing = json.dumps({"streams": [{"codec_type": "audio", "index": 0}]})
        lister = fake_program(self.tmp, "ffprobe", f"echo '{listing}'\n")
        bomb = fake_program(self.tmp, "ffmpeg", "touch \"$0.ran\"; exit 1\n")
        result = probe.probe(wav, ffmpeg=bomb, ffprobe=lister)
        self.assertIn("index and codec", result.refused[0]["reason"])
        self.assertFalse(Path(bomb + ".ran").exists())

    def test_frames_with_a_zero_sample_total_are_not_claimed(self) -> None:
        # ashowinfo lines the real decoder writes, with every frame empty.
        wav = support.sine_wav(self.tmp / "a.wav", 8000, "0.5")
        decoded = probe._run([support.FFMPEG, *probe.decode_arguments(str(wav), 0)], "ffmpeg").stderr
        frames = [line for line in decoded.splitlines() if "Parsed_ashowinfo_0" in line]
        self.assertTrue(frames)
        emptied = "\n".join(re.sub(r"nb_samples:\d+", "nb_samples:0", line) for line in frames)
        (self.tmp / "log").write_text(emptied + "\n")
        replay = fake_program(self.tmp, "ffmpeg", f"cat '{self.tmp / 'log'}' >&2\n")
        result = probe.probe(wav, ffmpeg=replay)
        self.assertIn("no sample frame", result.refused[0]["reason"])


@needs_ffmpeg
class Failures(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = support.scratch(self, "probe-")

    def test_a_missing_file_is_refused(self) -> None:
        with self.assertRaisesRegex(probe.ProbeError, "not a file"):
            probe.probe(self.tmp / "absent.wav")

    def test_bytes_no_demuxer_reads_are_refused(self) -> None:
        path = self.tmp / "noise.bin"
        path.write_bytes(b"\x00\x01not audio" * 10)
        with self.assertRaisesRegex(probe.ProbeError, "could not read"):
            probe.probe(path)

    def test_a_missing_program_is_refused(self) -> None:
        wav = support.sine_wav(self.tmp / "a.wav", 8000, "0.5")
        with self.assertRaisesRegex(probe.ProbeError, "could not run"):
            probe.probe(wav, ffprobe=str(self.tmp / "no-such-ffprobe"))


@needs_ffmpeg
class Software(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = support.scratch(self, "probe-")

    def test_versions_are_read_from_the_installed_programs(self) -> None:
        found = probe.software()
        names = [name for name, _ in found]
        self.assertEqual(names[:2], ["ffprobe", "ffmpeg"])
        self.assertTrue({"libavcodec", "libavformat", "libavfilter"} <= set(names))
        self.assertTrue(all(version for _, version in found))

    def test_a_program_that_reports_no_version_is_refused(self) -> None:
        silent = fake_program(self.tmp, "ffprobe", "echo hello\n")
        with self.assertRaisesRegex(probe.ProbeError, "did not report a version"):
            probe.software(ffprobe=silent)

    def test_a_decoder_without_its_libraries_is_refused(self) -> None:
        bare = fake_program(self.tmp, "ffmpeg", "echo 'ffmpeg version 0.0 Copyright'\n")
        with self.assertRaisesRegex(probe.ProbeError, "decoding libraries"):
            probe.software(ffmpeg=bare)


class Parameters(unittest.TestCase):
    def test_parameters_are_stable_canonical_json(self) -> None:
        first, second = adapter.canonical(probe.parameters()), adapter.canonical(probe.parameters())
        self.assertEqual(first, second)
        self.assertEqual(json.loads(first)["refusing_log_levels"], ["error", "fatal", "panic"])
        self.assertIn("{stream_index}", json.loads(first)["decode_arguments"][-6])

    def test_the_agent_is_a_package_url_door_accepts(self) -> None:
        self.assertEqual(adapter.check_package_url(probe.AGENT), probe.AGENT)


if __name__ == "__main__":
    unittest.main()
