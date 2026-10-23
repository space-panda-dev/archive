"""iReal Pro links: decode a playlist into charts, and encode them back.

A link is `irealb://` (or the older `irealbook://`) followed by
percent-encoded text: one or more songs separated by `===`, and, when there
is more than one part, a last part naming the playlist. A song is fields
separated by `=`:

- `irealb`: title, composer, (unused), style, key, transpose, music,
  accompaniment style, tempo, chorus count; the music begins with
  `1r34LbKcu7` and is scrambled (`unscramble`);
- `irealbook`: title, composer, style, key, (unused), music, unscrambled.

`decode_playlist` keeps every chart's slice of the link byte for byte (ADR
0116, section 1): links in the wild are encoded by different programs, so a
slice is the chart's evidence and its decoded fields are a reading of it.
`encode_chart` and `encode_playlist` write one canonical encoding (letters,
digits and `=` as they are, every other UTF-8 byte as uppercase `%XX`);
decoding what they wrote and encoding it again gives the same bytes.

Standard library only. Malformed input raises `CodecError`, naming what is
wrong; nothing is repaired or guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote_to_bytes

SCHEMES = ("irealb", "irealbook")
MUSIC_PREFIX = "1r34LbKcu7"
SEPARATOR = "==="

# Field positions in an `irealb` song, and how many fields a song may have:
# links written before accompaniment settings existed stop after the music.
IREALB_FIELDS = ("title", "composer", "unused", "style", "key", "transpose", "music",
                 "accompaniment", "tempo", "choruses")
IREALB_MIN_FIELDS = 7
IREALBOOK_FIELDS = ("title", "composer", "style", "key", "unused", "music")

# One unit of encoded text: an escape, or one raw character (UTF-8 bytes of a
# non-ASCII character together).
_UNIT = re.compile(rb"%[0-9A-Fa-f]{2}|[\x00-\x7f]|[\xc0-\xff][\x80-\xbf]*|[\x80-\xbf]")
_SAFE = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789=")


class CodecError(ValueError):
    """A link that is not an iReal Pro link this codec reads."""


@dataclass(frozen=True)
class Song:
    """One chart of a link: its fields decoded, and its slice of the link."""
    scheme: str
    fields: tuple[str, ...]
    slice: bytes   # the encoded song exactly as it stood in the link

    def field(self, name: str) -> str:
        names = IREALB_FIELDS if self.scheme == "irealb" else IREALBOOK_FIELDS
        index = names.index(name)
        return self.fields[index] if index < len(self.fields) else ""

    @property
    def title(self) -> str:
        return self.field("title")

    @property
    def composer(self) -> str:
        return self.field("composer")

    @property
    def style(self) -> str:
        return self.field("style")

    @property
    def key(self) -> str:
        return self.field("key")

    @property
    def music(self) -> str:
        """The chart's markup, unscrambled: what `chart.read_chart` reads."""
        raw = self.field("music")
        if self.scheme == "irealbook":
            return raw
        return unscramble(raw[len(MUSIC_PREFIX):])


@dataclass(frozen=True)
class Playlist:
    scheme: str
    songs: tuple[Song, ...]
    name: str | None   # None for a link of one song with no playlist part


# ---------------------------------------------------------------------------
# The scrambling: a fixed permutation of each whole 50-character block, except
# a block followed by fewer than two characters. It is its own inverse.


def _permute50(block: str) -> str:
    chars = list(block)
    for i in list(range(0, 5)) + list(range(10, 24)):
        chars[i], chars[49 - i] = chars[49 - i], chars[i]
    return "".join(chars)


def unscramble(text: str) -> str:
    out = []
    while len(text) > 51:
        out.append(_permute50(text[:50]))
        text = text[50:]
    out.append(text)
    return "".join(out)


def scramble(text: str) -> str:
    """The inverse of `unscramble`, which is the same permutation."""
    return unscramble(text)


# ---------------------------------------------------------------------------
# Decoding


def _units(body: bytes) -> list[tuple[bytes, bytes]]:
    """Split encoded text into (raw unit, decoded bytes) pairs."""
    units = []
    position = 0
    while position < len(body):
        match = _UNIT.match(body, position)
        if match is None:  # unreachable: the pattern covers every byte
            raise CodecError(f"unreadable byte at offset {position}")
        raw = match.group(0)
        units.append((raw, unquote_to_bytes(raw) if raw[:1] == b"%" else raw))
        position = match.end()
    return units


def _split_link(data: bytes) -> tuple[str, bytes, bytes]:
    data = data.strip(b"\r\n\t ")
    head, sep, body = data.partition(b"://")
    scheme = head.decode("ascii", "replace")
    if not sep or scheme not in SCHEMES:
        raise CodecError("not an irealb:// or irealbook:// link")
    return scheme, head + sep, body


def _parts(units: list[tuple[bytes, bytes]]) -> list[tuple[bytes, str]]:
    """Cut the units at each run of exactly three encoded `=` into (encoded
    part, decoded part). A run of other length is fields, not a separator."""
    parts: list[tuple[bytes, str]] = []
    start = 0
    i = 0
    while i < len(units):
        if units[i][1] != b"=":
            i += 1
            continue
        j = i
        while j < len(units) and units[j][1] == b"=":
            j += 1
        if j - i == 3:
            parts.append(_part(units[start:i]))
            start = j
        elif j - i > 3:
            raise CodecError(f"a run of {j - i} '=' where songs or fields are separated")
        i = j
    parts.append(_part(units[start:]))
    return parts


def _part(units: list[tuple[bytes, bytes]]) -> tuple[bytes, str]:
    encoded = b"".join(raw for raw, _ in units)
    decoded = b"".join(value for _, value in units)
    try:
        return encoded, decoded.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CodecError(f"text that is not UTF-8 at decoded offset {error.start}") from None


def _song(scheme: str, encoded: bytes, text: str, number: int) -> Song:
    fields = tuple(text.split("="))
    where = f"song {number}"
    if scheme == "irealb":
        if not IREALB_MIN_FIELDS <= len(fields) <= len(IREALB_FIELDS):
            raise CodecError(f"{where} has {len(fields)} fields; an irealb song has 7 to 10")
        if not fields[6].startswith(MUSIC_PREFIX):
            raise CodecError(f"{where}'s music does not begin with {MUSIC_PREFIX}")
    elif len(fields) != len(IREALBOOK_FIELDS):
        raise CodecError(f"{where} has {len(fields)} fields; an irealbook song has 6")
    if not fields[0]:
        raise CodecError(f"{where} has no title")
    return Song(scheme, fields, encoded)


def decode_playlist(data: bytes) -> Playlist:
    """Read a link of one song or a playlist. Raises CodecError."""
    scheme, _, body = _split_link(data)
    parts = _parts(_units(body))
    if len(parts) == 1:
        name = None
        songs = parts
    else:
        name = parts[-1][1]
        songs = parts[:-1]
    if not songs or (len(songs) == 1 and not songs[0][0]):
        raise CodecError("a link with no song")
    return Playlist(scheme, tuple(_song(scheme, enc, text, n + 1) for n, (enc, text) in enumerate(songs)), name)


def chart_link(song: Song | bytes, scheme: str = "irealb") -> bytes:
    """The link of one chart, as iReal Pro shares a single song: the scheme
    and the chart's slice, byte for byte (ADR 0116's chart document)."""
    if isinstance(song, Song):
        return song.scheme.encode("ascii") + b"://" + song.slice
    return scheme.encode("ascii") + b"://" + song


# ---------------------------------------------------------------------------
# Encoding


def _quote(text: str) -> bytes:
    return b"".join(bytes([byte]) if byte in _SAFE else b"%%%02X" % byte for byte in text.encode("utf-8"))


def encode_song(scheme: str, fields: tuple[str, ...] | list[str]) -> bytes:
    """The canonical encoded slice of one song's fields."""
    fields = tuple(fields)
    if any("=" in field for field in fields):
        raise CodecError("a field holds '=', which separates fields")
    encoded = b"=".join(_quote(field) for field in fields)
    _song(scheme, encoded, "=".join(fields), 1)  # the same shape rules as decoding
    return encoded


def encode_chart(song: Song) -> bytes:
    """One song as a link in the canonical encoding."""
    return song.scheme.encode("ascii") + b"://" + encode_song(song.scheme, song.fields)


def encode_playlist(playlist: Playlist) -> bytes:
    """A playlist as a link in the canonical encoding. A playlist with no name
    must hold exactly one song."""
    songs = [encode_song(playlist.scheme, song.fields) for song in playlist.songs]
    if playlist.name is None:
        if len(songs) != 1:
            raise CodecError("a link of several songs needs a playlist name")
        body = songs[0]
    else:
        body = b"===".join(songs + [_quote(playlist.name)])
    return playlist.scheme.encode("ascii") + b"://" + body


def music_field(markup: str) -> str:
    """An `irealb` music field for unscrambled markup."""
    return MUSIC_PREFIX + scramble(markup)
