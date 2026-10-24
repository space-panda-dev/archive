"""The Zoom H4essential grammar: names, sources, and the rollover rule.

Every name here is synthetic. The rollover tests use files of the recorder's
real size cap, written sparse so they take almost no disk.
"""

from __future__ import annotations

import shutil
import struct
import tempfile
import unittest
from datetime import datetime, timedelta
from fractions import Fraction
from pathlib import Path

import grammar
from grammar import GrammarError, ZoomH4E, wav_duration

BYTE_RATE = 384_000  # 48 kHz, 2 channels, 32-bit float


def wav_header(data_size: int, byte_rate: int = BYTE_RATE, extra: bytes = b"") -> bytes:
    fmt = struct.pack("<HHIIHH", 3, 2, 48000, byte_rate, 8, 32)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + extra + b"data" + struct.pack("<I", data_size)
    return b"RIFF" + struct.pack("<I", len(body) + data_size) + body


def sparse_wav(path: Path, size: int, byte_rate: int = BYTE_RATE) -> Path:
    """A WAV file of exactly `size` bytes whose header states the rest as
    data, written sparse: only the header occupies disk."""
    header = wav_header(size - 44, byte_rate)
    assert len(header) == 44
    with open(path, "wb") as handle:
        handle.write(header)
        handle.truncate(size)
    return path


class Names(unittest.TestCase):
    def setUp(self) -> None:
        self.zoom = ZoomH4E()

    def test_lr_name_parses_take_source_and_start(self) -> None:
        found = self.zoom.recognise("010203_040506_TrLR.WAV")
        self.assertEqual(found, grammar.Recognised("zoom-h4e", "010203_040506", "TrLR",
                                                   datetime(2003, 1, 2, 4, 5, 6)))

    def test_mic_name_is_another_source_of_the_same_take(self) -> None:
        lr = self.zoom.recognise("010203_040506_TrLR.WAV")
        mic = self.zoom.recognise("010203_040506_TrMic.WAV")
        self.assertNotEqual(lr.source, mic.source)
        self.assertEqual((mic.take, mic.started), (lr.take, lr.started))

    def test_lr_is_the_first_source(self) -> None:
        self.assertEqual(self.zoom.sources, ("TrLR", "TrMic"))

    def test_the_mic_track_is_the_mix_tracks_twin_by_default(self) -> None:
        # The operator's recorder writes each take twice (ADR 0070, twin amendment).
        self.assertTrue(self.zoom.twins)
        self.assertTrue(ZoomH4E(twins=True).twins)

    def test_a_grammar_without_twins_keeps_every_source(self) -> None:
        self.assertFalse(ZoomH4E(twins=False).twins)
        self.assertEqual(ZoomH4E(twins=False).sources, ("TrLR", "TrMic"))

    def test_the_importers_grammar_has_twins(self) -> None:
        [zoom] = grammar.GRAMMARS
        self.assertTrue(zoom.twins)

    def test_names_outside_the_grammar_are_not_recognised(self) -> None:
        for name in ("010203_040506_TrLR-0001.WAV",  # a rollover-numbered suffix
                     "010203_040506_TrLR.wav",  # a lowercase extension
                     "010203_040506_trlr.WAV",
                     "010203_040506_Tr1.WAV",  # another track token
                     "010203_040506.WAV",  # no track
                     "010203_040506_TrLR.WAV.tmp",
                     "x010203_040506_TrLR.WAV",
                     "take.WAV",
                     "133203_040506_TrLR.WAV",  # month 13
                     "023003_040506_TrLR.WAV",  # 30 February
                     "010203_250506_TrLR.WAV"):  # hour 25
            with self.subTest(name=name):
                self.assertIsNone(self.zoom.recognise(name))


class Rollover(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="capture-grammar-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.zoom = ZoomH4E()
        self.start = datetime(2001, 1, 1, 12, 0, 0)
        self.capped = sparse_wav(self.tmp / "capped.WAV", ZoomH4E.SPLIT_SIZE)
        self.duration = Fraction(ZoomH4E.SPLIT_SIZE - 44, BYTE_RATE)

    def test_the_cap_is_the_measured_size_and_the_file_is_that_size(self) -> None:
        self.assertEqual(ZoomH4E.SPLIT_SIZE, 2_147_075_072)
        self.assertEqual(self.capped.stat().st_size, ZoomH4E.SPLIT_SIZE)
        self.assertEqual(wav_duration(self.capped), self.duration)

    def whole_seconds_after(self, seconds: int) -> datetime:
        """A start time a whole number of seconds after the capped file's
        start, as recorder names have."""
        return self.start + timedelta(seconds=seconds)

    def test_a_capped_file_followed_within_the_gap_continues(self) -> None:
        # The duration is 5591.341... s; the next name rounds up to 5592.
        following = self.whole_seconds_after(int(self.duration) + 1)
        self.assertTrue(self.zoom.continues(self.capped, self.start, following))

    def test_a_short_file_followed_seconds_later_does_not_continue(self) -> None:
        short = sparse_wav(self.tmp / "short.WAV", 44 + 10 * BYTE_RATE)
        self.assertNotEqual(short.stat().st_size, ZoomH4E.SPLIT_SIZE)
        self.assertFalse(self.zoom.continues(short, self.start, self.whole_seconds_after(11)))

    def test_a_capped_file_followed_thirty_seconds_later_does_not_continue(self) -> None:
        self.assertFalse(self.zoom.continues(self.capped, self.start,
                                             self.whole_seconds_after(int(self.duration) + 30)))

    def test_the_gap_bound_is_inclusive_on_both_sides(self) -> None:
        # A file of exactly ten seconds at a cap of its own size.
        zoom = ZoomH4E(split_size=44 + 10 * BYTE_RATE)
        exact = sparse_wav(self.tmp / "exact.WAV", 44 + 10 * BYTE_RATE)
        for seconds, expected in ((12, True), (13, False), (8, True), (7, False), (10, True)):
            with self.subTest(seconds=seconds):
                self.assertEqual(zoom.continues(exact, self.start, self.whole_seconds_after(seconds)), expected)

    def test_a_fractional_duration_is_compared_exactly(self) -> None:
        # 10.5 seconds: a next take 12 s after the start is 1.5 s after the
        # end, and 13 s is 2.5 s after it.
        size = 44 + 10 * BYTE_RATE + BYTE_RATE // 2
        zoom = ZoomH4E(split_size=size)
        half = sparse_wav(self.tmp / "half.WAV", size)
        self.assertEqual(wav_duration(half), Fraction(21, 2))
        self.assertTrue(zoom.continues(half, self.start, self.whole_seconds_after(12)))
        self.assertFalse(zoom.continues(half, self.start, self.whole_seconds_after(13)))

    def test_a_capped_file_whose_header_cannot_be_read_is_refused(self) -> None:
        broken = self.tmp / "broken.WAV"
        with open(broken, "wb") as handle:
            handle.write(b"RIFX" + bytes(40))
            handle.truncate(ZoomH4E.SPLIT_SIZE)
        with self.assertRaisesRegex(GrammarError, "not a RIFF WAVE"):
            self.zoom.continues(broken, self.start, self.whole_seconds_after(5592))


class Headers(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="capture-header-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, data: bytes) -> Path:
        path = self.tmp / "file.WAV"
        path.write_bytes(data)
        return path

    def test_chunks_before_the_data_are_skipped_with_their_padding(self) -> None:
        odd = b"LIST" + struct.pack("<I", 3) + b"abc" + b"\0"
        path = self.write(wav_header(BYTE_RATE * 2, extra=odd) + bytes(16))
        self.assertEqual(wav_duration(path), 2)

    def test_headers_that_state_no_duration_are_refused(self) -> None:
        fmt_short = b"RIFF" + bytes(4) + b"WAVE" + b"fmt " + struct.pack("<I", 4) + bytes(4)
        no_rate = b"RIFF" + bytes(4) + b"WAVE" + b"fmt " + struct.pack("<I", 16) + bytes(16) \
            + b"data" + struct.pack("<I", 8)
        data_first = b"RIFF" + bytes(4) + b"WAVE" + b"data" + struct.pack("<I", 8)
        no_data = b"RIFF" + bytes(4) + b"WAVE" + b"fmt " + struct.pack("<I", 16) \
            + struct.pack("<HHIIHH", 1, 1, 8000, 16000, 2, 16)
        for data, message in ((b"", "not a RIFF WAVE"), (fmt_short, "short fmt"),
                              (no_rate, "no byte rate"), (data_first, "no byte rate"), (no_data, "no data chunk")):
            with self.subTest(message=message):
                with self.assertRaisesRegex(GrammarError, message):
                    wav_duration(self.write(data))

    def test_an_unreadable_file_is_refused(self) -> None:
        with self.assertRaisesRegex(GrammarError, "could not be read"):
            wav_duration(self.tmp / "absent.WAV")


if __name__ == "__main__":
    unittest.main()
