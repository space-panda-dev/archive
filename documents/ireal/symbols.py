"""The iReal Pro chord-symbol grammar, version 1 (ADR 0116, section 2).

`read_symbol(text, previous)` reads one chord as iReal Pro writes it into a
reading of ADR 0077's chord stacks (ADR 0079's version 2 readings): `root`
the root's pitch class, `bass` the slash bass or else the root, `tones` the
quality's pitch classes less the bass's, ascending, `uncertain` for the
tones a quality does not fix, and `text` the chart's markup verbatim. A
symbol the grammar cannot read raises `UnsupportedSymbol` naming it;
`reading_of` turns that into ADR 0077's text-only reading, which is what an
importer asserts, never a guess.

`symbol_of(reading)` goes back: the reading's verbatim text when it reads to
the same stack, else a root and the first quality in `QUALITIES` whose
classes give the stack. `read_key(field)` reads the key field.

The grammar's conventions, stated once here rather than inferred per chart:
an extension implies the sevenths and ninths below it, a thirteenth leaves
out the eleventh, `7alt` and `11` name tones that may sound (uncertain), and
a minor key is the natural minor. `2` is unsupported until decision Q (add2
or sus2) is taken.
"""

from __future__ import annotations

import re
from typing import Any

GRAMMAR_VERSION = 1

LETTER_CLASS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
# Every letter with an optional flat or sharp: iReal Pro writes `Cb`, so
# enharmonic spellings are read as the pitch class they sound.
NOTE_CLASS = {letter + accidental: (value + shift) % 12 for letter, value in LETTER_CLASS.items()
              for accidental, shift in (("", 0), ("b", -1), ("#", 1))}
FLAT_NAMES = ("C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B")

# Quality: (certain classes above the root, groups of classes that may sound).
# The order matters for `symbol_of`: the first quality that fits is written.
QUALITIES: dict[str, tuple[tuple[int, ...], tuple[tuple[int, ...], ...]]] = {
    "": ((0, 4, 7), ()),
    "-": ((0, 3, 7), ()),
    "+": ((0, 4, 8), ()),
    "o": ((0, 3, 6), ()),
    "sus": ((0, 5, 7), ()),
    "5": ((0, 7), ()),
    "add9": ((0, 2, 4, 7), ()),
    "6": ((0, 4, 7, 9), ()),
    "-6": ((0, 3, 7, 9), ()),
    "69": ((0, 2, 4, 7, 9), ()),
    "-69": ((0, 2, 3, 7, 9), ()),
    "-b6": ((0, 3, 7, 8), ()),
    "-#5": ((0, 3, 8), ()),
    "^7": ((0, 4, 7, 11), ()),
    "^": ((0, 4, 7, 11), ()),
    "-^7": ((0, 3, 7, 11), ()),
    "^9": ((0, 2, 4, 7, 11), ()),
    "-^9": ((0, 2, 3, 7, 11), ()),
    "^13": ((0, 2, 4, 7, 9, 11), ()),
    "^7#11": ((0, 4, 6, 7, 11), ()),
    "^9#11": ((0, 2, 4, 6, 7, 11), ()),
    "^7#5": ((0, 4, 8, 11), ()),
    "o^7": ((0, 3, 6, 11), ()),
    "7": ((0, 4, 7, 10), ()),
    "-7": ((0, 3, 7, 10), ()),
    "h7": ((0, 3, 6, 10), ()),
    "h": ((0, 3, 6, 10), ()),
    "-7b5": ((0, 3, 6, 10), ()),
    "h9": ((0, 2, 3, 6, 10), ()),
    "o7": ((0, 3, 6, 9), ()),
    "-9": ((0, 2, 3, 7, 10), ()),
    "-11": ((0, 2, 3, 5, 7, 10), ()),
    "9": ((0, 2, 4, 7, 10), ()),
    "13": ((0, 2, 4, 7, 9, 10), ()),
    "7sus": ((0, 5, 7, 10), ()),
    "9sus": ((0, 2, 5, 7, 10), ()),
    "13sus": ((0, 2, 5, 7, 9, 10), ()),
    "7b9sus": ((0, 1, 5, 7, 10), ()),
    "7b13sus": ((0, 5, 7, 8, 10), ()),
    "7susadd3": ((0, 4, 5, 7, 10), ()),
    "7b9": ((0, 1, 4, 7, 10), ()),
    "7#9": ((0, 3, 4, 7, 10), ()),
    "7#11": ((0, 4, 6, 7, 10), ()),
    "7b5": ((0, 4, 6, 10), ()),
    "7#5": ((0, 4, 8, 10), ()),
    "7b13": ((0, 4, 7, 8, 10), ()),
    "9#11": ((0, 2, 4, 6, 7, 10), ()),
    "9b5": ((0, 2, 4, 6, 10), ()),
    "9#5": ((0, 2, 4, 8, 10), ()),
    "13#11": ((0, 2, 4, 6, 7, 9, 10), ()),
    "13b9": ((0, 1, 4, 7, 9, 10), ()),
    "13#9": ((0, 3, 4, 7, 9, 10), ()),
    "7#9#5": ((0, 3, 4, 8, 10), ()),
    "7#9b5": ((0, 3, 4, 6, 10), ()),
    "7#9#11": ((0, 3, 4, 6, 7, 10), ()),
    "7b9#11": ((0, 1, 4, 6, 7, 10), ()),
    "7b9b5": ((0, 1, 4, 6, 10), ()),
    "7b9#5": ((0, 1, 4, 8, 10), ()),
    "7b9#9": ((0, 1, 3, 4, 7, 10), ()),
    "7b9b13": ((0, 1, 4, 7, 8, 10), ()),
    "11": ((0, 2, 5, 7, 10), ((4,),)),
    "7alt": ((0, 4, 10), ((1,), (3,), (6,), (8,))),
}

_CHORD = re.compile(r"(?P<root>[A-G][b#]?)(?P<quality>[^/]*)(?:/(?P<bass>[A-G][b#]?))?\Z")
_INVISIBLE = re.compile(r"W/(?P<bass>[A-G][b#]?)\Z")
_KEY = re.compile(r"(?P<tonic>[A-G][b#]?)(?P<minor>-?)\Z")
MAJOR = (0, 2, 4, 5, 7, 9, 11)
NATURAL_MINOR = (0, 2, 3, 5, 7, 8, 10)


class UnsupportedSymbol(ValueError):
    """A chord symbol grammar version 1 does not read."""

    def __init__(self, symbol: str, reason: str) -> None:
        super().__init__(f"unsupported chord symbol {symbol!r}: {reason}")
        self.symbol = symbol
        self.reason = reason


def _note(name: str) -> int:
    return NOTE_CLASS[name]


def _stack(root: int, bass: int, certain: tuple[int, ...], maybe: tuple[tuple[int, ...], ...],
           text: str) -> dict[str, Any]:
    tones = sorted({(root + interval) % 12 for interval in certain} - {bass})
    reading: dict[str, Any] = {"root": root, "bass": bass, "tones": tones}
    taken = set(tones) | {bass}
    groups = []
    for group in maybe:
        classes = sorted({(root + interval) % 12 for interval in group} - taken)
        if classes:
            groups.append({"pitch_classes": classes, "present": "maybe"})
            taken |= set(classes)
    if groups:
        reading["uncertain"] = sorted(groups, key=lambda group: group["pitch_classes"])
    reading["text"] = text
    return reading


def read_symbol(text: str, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read one chord symbol (`C-7`, `Bb7b9/Ab`, `W/G`, `n`) into a reading.

    `previous` is the reading of the chord before, which `W/X` (an invisible
    root: that chord over a new bass) needs. Raises UnsupportedSymbol."""
    if text == "n":
        return {"no_chord": True, "text": text}
    invisible = _INVISIBLE.match(text)
    if invisible:
        if previous is None or "tones" not in previous:
            raise UnsupportedSymbol(text, "an invisible root with no chord before it")
        bass = _note(invisible.group("bass"))
        sounding = set(previous["tones"]) | {previous["bass"]}
        reading: dict[str, Any] = {"bass": bass, "tones": sorted(sounding - {bass})}
        if "root" in previous:
            reading = {"root": previous["root"], **reading}
        groups = [{"pitch_classes": [pc for pc in group["pitch_classes"] if pc != bass], "present": "maybe"}
                  for group in previous.get("uncertain", [])]
        groups = [group for group in groups if group["pitch_classes"]]
        if groups:
            reading["uncertain"] = groups
        reading["text"] = text
        return reading
    if text.startswith("W"):
        raise UnsupportedSymbol(text, "an invisible root with a quality")
    chord = _CHORD.match(text)
    if chord is None:
        raise UnsupportedSymbol(text, "not a chord")
    root = _note(chord.group("root"))
    quality = chord.group("quality")
    if quality not in QUALITIES:
        reason = ("whether 2 is add2 or sus2 is not settled (ADR 0116, decision Q)" if quality == "2"
                  else f"unknown quality {quality!r}")
        raise UnsupportedSymbol(text, reason)
    bass = _note(chord.group("bass")) if chord.group("bass") else root
    certain, maybe = QUALITIES[quality]
    return _stack(root, bass, certain, maybe, text)


def reading_of(text: str, previous: dict[str, Any] | None = None) -> tuple[dict[str, Any], str | None]:
    """The reading an importer asserts: `read_symbol`'s, or a text-only
    reading with the reason it is one."""
    try:
        return read_symbol(text, previous), None
    except UnsupportedSymbol as error:
        return {"text": text}, error.reason


def _same_stack(a: dict[str, Any], b: dict[str, Any]) -> bool:
    keys = ("root", "bass", "tones", "uncertain", "no_chord")
    return all(a.get(key) == b.get(key) for key in keys)


def symbol_of(reading: dict[str, Any], names: tuple[str, ...] = FLAT_NAMES) -> str | None:
    """The iReal Pro symbol of a reading, or None when no quality writes it.

    A reading keeps its own text when that reads to the same stack. Otherwise
    the root must be asserted; the first quality in table order whose
    classes give the stack is written, preferring one whose classes hold the
    bass (C/E rather than C5/E). `names` spells the twelve pitch classes."""
    if reading.get("no_chord"):
        return "n"
    text = reading.get("text")
    if text is not None:
        try:
            if _same_stack(read_symbol(text), reading):
                return text
        except UnsupportedSymbol:
            pass
    if "root" not in reading or "tones" not in reading:
        return None
    root, bass = reading["root"], reading["bass"]
    chosen = None
    for quality, (certain, maybe) in QUALITIES.items():
        candidate = _stack(root, bass, certain, maybe, "")
        if _same_stack(candidate, reading):
            if (bass - root) % 12 in certain:
                chosen = quality
                break
            chosen = quality if chosen is None else chosen
    if chosen is None:
        return None
    return names[root] + chosen + ("" if bass == root else "/" + names[bass])


def read_key(field: str) -> dict[str, Any] | None:
    """The key field as one key reading (ADR 0080): a tonic with the major
    or natural minor collection, or text only. None for an empty field."""
    if field == "":
        return None
    match = _KEY.match(field)
    if match is None or match.group("tonic") not in NOTE_CLASS:
        return {"text": field}
    tonic = NOTE_CLASS[match.group("tonic")]
    minor = bool(match.group("minor"))
    scale = NATURAL_MINOR if minor else MAJOR
    return {"collection": sorted((tonic + step) % 12 for step in scale), "tonic": tonic,
            "mode": "minor" if minor else "major", "text": field}
