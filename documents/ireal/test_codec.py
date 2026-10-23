"""The link codec, on invented songs only."""

from __future__ import annotations

import unittest

import codec
from codec import CodecError, Playlist, Song

MUSIC_A = "[T44*AC^7 A-7 |D-7 G7 |C^7XyQ|G7XyQZ"
MUSIC_B = "{*AT34F XyQ|Bb XyQ}"


def fields(title: str, music: str, composer: str = "Nobody Invented", style: str = "Medium Swing",
           key: str = "C") -> tuple[str, ...]:
    return (title, composer, "", style, key, "", codec.music_field(music), "Jazz-Medium Swing", "120", "1")


def everything_escaped(text: str) -> bytes:
    """A link as some programs write it: every byte, `=` included, as %XX."""
    return b"".join(b"%%%02X" % byte for byte in text.encode("utf-8"))


class Scrambling(unittest.TestCase):
    def test_is_its_own_inverse_at_every_length(self) -> None:
        source = "".join(chr(33 + (i * 7) % 90) for i in range(260))
        for length in range(0, 260):
            text = source[:length]
            self.assertEqual(codec.unscramble(codec.scramble(text)), text)
            self.assertEqual(len(codec.scramble(text)), length)

    def test_permutes_whole_blocks_and_leaves_short_tails(self) -> None:
        text = "".join(chr(65 + i % 26) for i in range(103))   # two blocks and three characters
        scrambled = codec.scramble(text)
        self.assertNotEqual(scrambled[:100], text[:100])
        self.assertEqual(scrambled[100:], text[100:])
        # A block followed by fewer than two characters stays as it is.
        self.assertEqual(codec.scramble(text[:51]), text[:51])
        self.assertNotEqual(codec.scramble(text[:52])[:50], text[:50])


class Decoding(unittest.TestCase):
    def playlist(self) -> Playlist:
        return Playlist("irealb", (Song("irealb", fields("Paper Lanterns", MUSIC_A), b""),
                                   Song("irealb", fields("Tin Roof Waltz", MUSIC_B, key="F-"), b"")),
                        "Invented Book")

    def test_a_canonical_playlist_round_trips_byte_for_byte(self) -> None:
        link = codec.encode_playlist(self.playlist())
        decoded = codec.decode_playlist(link)
        self.assertEqual(decoded.name, "Invented Book")
        self.assertEqual([song.title for song in decoded.songs], ["Paper Lanterns", "Tin Roof Waltz"])
        self.assertEqual(decoded.songs[0].music, MUSIC_A)
        self.assertEqual(decoded.songs[1].key, "F-")
        self.assertEqual(codec.encode_playlist(decoded), link)
        for song in decoded.songs:
            self.assertEqual(codec.encode_chart(song), codec.chart_link(song))

    def test_slices_are_the_link_byte_for_byte(self) -> None:
        link = codec.encode_playlist(self.playlist())
        decoded = codec.decode_playlist(link)
        body = link.split(b"://", 1)[1]
        rebuilt = b"===".join([song.slice for song in decoded.songs] + [b"Invented%20Book"])
        self.assertEqual(rebuilt, body)
        self.assertEqual(codec.chart_link(decoded.songs[1]), b"irealb://" + decoded.songs[1].slice)

    def test_another_programs_encoding_keeps_its_own_slices(self) -> None:
        songs = ["=".join(fields("Paper Lanterns", MUSIC_A)), "=".join(fields("Café Steps", MUSIC_B))]
        escaped = [everything_escaped(song) for song in songs]
        link = b"irealb://" + everything_escaped("===").join(escaped + [everything_escaped("Odd Encoder")])
        decoded = codec.decode_playlist(link)
        self.assertEqual(decoded.name, "Odd Encoder")
        self.assertEqual([song.slice for song in decoded.songs], escaped)
        self.assertEqual(decoded.songs[1].title, "Café Steps")
        self.assertEqual(decoded.songs[0].music, MUSIC_A)
        # Its canonical encoding differs from what it was given, and is stable.
        canonical = codec.encode_chart(decoded.songs[0])
        self.assertNotEqual(canonical, codec.chart_link(decoded.songs[0]))
        self.assertEqual(codec.encode_chart(codec.decode_playlist(canonical).songs[0]), canonical)

    def test_raw_utf8_and_lowercase_escapes_decode(self) -> None:
        link = "irealb://Ni%c3%b1o Azul=".encode("utf-8") + "=".join(fields("x", MUSIC_A)[1:]).encode("utf-8")
        song = codec.decode_playlist(link.replace(b"Ni%c3%b1o", "Niño".encode("utf-8"))).songs[0]
        self.assertEqual(song.title, "Niño Azul")
        self.assertEqual(codec.decode_playlist(link).songs[0].title, "Niño Azul")

    def test_a_single_song_link_has_no_playlist_name(self) -> None:
        song = Song("irealb", fields("Paper Lanterns", MUSIC_A), b"")
        link = codec.encode_chart(song)
        decoded = codec.decode_playlist(link)
        self.assertIsNone(decoded.name)
        self.assertEqual(decoded.songs[0].fields, song.fields)
        self.assertEqual(codec.encode_playlist(decoded), link)

    def test_a_song_without_accompaniment_fields(self) -> None:
        short = fields("Paper Lanterns", MUSIC_A)[:7]
        decoded = codec.decode_playlist(codec.encode_chart(Song("irealb", short, b"")))
        self.assertEqual(decoded.songs[0].fields, short)
        self.assertEqual(decoded.songs[0].field("tempo"), "")

    def test_the_older_irealbook_scheme(self) -> None:
        link = b"irealbook://Slow%20Kettle=Nobody%20Invented=Ballad=Eb=n=" + b"[T44Eb^7 |Bb7 Z"
        song = codec.decode_playlist(link).songs[0]
        self.assertEqual((song.title, song.style, song.key), ("Slow Kettle", "Ballad", "Eb"))
        self.assertEqual(song.music, "[T44Eb^7 |Bb7 Z")
        self.assertEqual(codec.chart_link(song), link)

    def test_malformed_links_fail_naming_the_problem(self) -> None:
        good = "=".join(fields("Paper Lanterns", MUSIC_A))
        cases = {
            b"https://example.invalid/": "not an irealb",
            b"irealb://" + good.replace("1r34LbKcu7", "").encode(): "does not begin",
            b"irealb://A=B=C": "has 3 fields",
            b"irealb://" + good.encode() + b"====Name": "a run of 4",
            b"irealb://": "no song",
            b"irealb://=" + good.split("=", 1)[1].encode(): "no title",
        }
        for link, message in cases.items():
            with self.subTest(link=link[:30]):
                with self.assertRaisesRegex(CodecError, message):
                    codec.decode_playlist(link)

    def test_a_field_holding_the_separator_is_refused(self) -> None:
        with self.assertRaisesRegex(CodecError, "holds '='"):
            codec.encode_song("irealb", fields("A=B", MUSIC_A))


if __name__ == "__main__":
    unittest.main()
