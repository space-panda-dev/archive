"""Chord labels read as chord stack readings (ADRs 0077 and 0079).

A chord extractor names its chords in its own spelling: `chords-jiang` in
Harte's (C:maj7, A:min7/b3, N), `chords-chordino` in Chordino's (Cmaj7, Am7,
F/A, N). A proposer turns each label into a reading of a chord stacks
version 2 claim: the label's root as `root`, its bass (the root when it
names none) as `bass`, the quality's pitch classes other than the bass as
`tones` (a label says nothing about doubling, so the bass's own class is
never a tone), and the label itself, verbatim, as `text`. `N` is no chord.
A label this module cannot read is kept as a text-only reading, never
guessed at.

Pitch classes are integers, C = 0. Standard library only.
"""

from __future__ import annotations

import re
from typing import Any

LETTERS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

# Harte (2005) shorthands and the extended ones mir_eval reads, as intervals
# above the root.
HARTE_QUALITIES: dict[str, frozenset[int]] = {
    "maj": frozenset({0, 4, 7}), "min": frozenset({0, 3, 7}), "dim": frozenset({0, 3, 6}),
    "aug": frozenset({0, 4, 8}), "maj7": frozenset({0, 4, 7, 11}), "min7": frozenset({0, 3, 7, 10}),
    "7": frozenset({0, 4, 7, 10}), "dim7": frozenset({0, 3, 6, 9}), "hdim7": frozenset({0, 3, 6, 10}),
    "minmaj7": frozenset({0, 3, 7, 11}), "maj6": frozenset({0, 4, 7, 9}), "min6": frozenset({0, 3, 7, 9}),
    "9": frozenset({0, 2, 4, 7, 10}), "maj9": frozenset({0, 2, 4, 7, 11}), "min9": frozenset({0, 2, 3, 7, 10}),
    "sus2": frozenset({0, 2, 7}), "sus4": frozenset({0, 5, 7}), "11": frozenset({0, 2, 4, 5, 7, 10}),
    "min11": frozenset({0, 2, 3, 5, 7, 10}), "13": frozenset({0, 2, 4, 5, 7, 9, 10}),
    "maj13": frozenset({0, 2, 4, 5, 7, 9, 11}), "min13": frozenset({0, 2, 3, 5, 7, 9, 10}),
    "1": frozenset({0}), "5": frozenset({0, 7}),
}

# Chordino's chord dictionary as its labels spell it, with the lead-sheet
# suffixes for the same chords.
CHORDINO_QUALITIES: dict[str, frozenset[int]] = {
    "": HARTE_QUALITIES["maj"], "m": HARTE_QUALITIES["min"], "dim": HARTE_QUALITIES["dim"],
    "aug": HARTE_QUALITIES["aug"], "+": HARTE_QUALITIES["aug"], "maj7": HARTE_QUALITIES["maj7"],
    "7": HARTE_QUALITIES["7"], "m7": HARTE_QUALITIES["min7"], "m7b5": HARTE_QUALITIES["hdim7"],
    "dim7": HARTE_QUALITIES["dim7"], "6": HARTE_QUALITIES["maj6"], "m6": HARTE_QUALITIES["min6"],
    "mmaj7": HARTE_QUALITIES["minmaj7"], "m(maj7)": HARTE_QUALITIES["minmaj7"], "sus2": HARTE_QUALITIES["sus2"],
    "sus4": HARTE_QUALITIES["sus4"], "7sus4": frozenset({0, 5, 7, 10}), "9": HARTE_QUALITIES["9"],
    "maj9": HARTE_QUALITIES["maj9"], "m9": HARTE_QUALITIES["min9"], "5": HARTE_QUALITIES["5"],
}

NO_CHORD = "N"
_NOTE = r"[A-G](?:#+|b+)?"
_HARTE = re.compile(rf"(?P<root>{_NOTE})(?::(?P<quality>[^/(]*)(?:\((?P<degrees>[^)]*)\))?)?"
                    rf"(?:/(?P<bass>[#b]*[0-9]{{1,2}}))?\Z")
_DEGREE = re.compile(r"(?P<omit>\*)?(?P<accidentals>[#b]*)(?P<number>[0-9]{1,2})\Z")
_CHORDINO = re.compile(rf"(?P<root>{_NOTE})(?P<quality>[^/]*)(?:/(?P<bass>{_NOTE}))?\Z")
_DEGREE_SEMITONES = {1: 0, 2: 2, 3: 4, 4: 5, 5: 7, 6: 9, 7: 11}


def note_class(name: str) -> int:
    """The pitch class of a note name: a letter and any sharps or flats."""
    return (LETTERS[name[0]] + name.count("#") - name.count("b")) % 12


def degree_semitones(degree: str) -> int | None:
    """A Harte degree without its omission mark (b3, #11, 13) in semitones
    above the root, folded to one octave; None when it is not one."""
    found = _DEGREE.fullmatch(degree)
    if found is None or found.group("omit"):
        return None
    number = int(found.group("number"))
    if not 1 <= number <= 13:
        return None
    base = _DEGREE_SEMITONES[(number - 1) % 7 + 1]
    accidentals = found.group("accidentals")
    return (base + accidentals.count("#") - accidentals.count("b")) % 12


def stack(root: int, intervals: frozenset[int], bass: int, text: str) -> dict[str, Any]:
    """A stack reading: the chord's classes other than the bass as tones."""
    classes = {(root + interval) % 12 for interval in intervals}
    return {"bass": bass, "tones": sorted(classes - {bass}), "root": root, "text": text}


def text_only(label: str) -> dict[str, Any]:
    return {"text": label}


def harte_reading(label: str) -> dict[str, Any]:
    """A reading of one Harte label (Harte 2005, with mir_eval's extended
    shorthands): `C:min7/b3`, `C:(1,3,5)`, `C:maj(*5,9)`, `C` (C:maj), `N`."""
    if label == NO_CHORD:
        return {"no_chord": True, "text": label}
    found = _HARTE.fullmatch(label)
    if found is None:
        return text_only(label)
    root = note_class(found.group("root"))
    quality, degrees = found.group("quality"), found.group("degrees")
    if quality is None:
        intervals = set(HARTE_QUALITIES["maj"])
    elif quality == "":
        if degrees is None:
            return text_only(label)
        intervals = {0}  # an interval list alone holds the root unless it omits it
    elif quality in HARTE_QUALITIES:
        intervals = set(HARTE_QUALITIES[quality])
    else:
        return text_only(label)
    for degree in (degrees.split(",") if degrees else []):
        degree = degree.strip()
        omitted = degree.startswith("*")
        semitones = degree_semitones(degree.removeprefix("*"))
        if semitones is None:
            return text_only(label)
        (intervals.discard if omitted else intervals.add)(semitones)
    bass = root
    if found.group("bass") is not None:
        semitones = degree_semitones(found.group("bass"))
        if semitones is None:
            return text_only(label)
        bass = (root + semitones) % 12
    if not intervals and bass == root:
        return text_only(label)
    return stack(root, frozenset(intervals), bass, label)


def chordino_reading(label: str) -> dict[str, Any]:
    """A reading of one Chordino label: `C`, `Am7`, `F#m7b5`, `F/A`, `N`."""
    if label == NO_CHORD:
        return {"no_chord": True, "text": label}
    found = _CHORDINO.fullmatch(label)
    if found is None or found.group("quality") not in CHORDINO_QUALITIES:
        return text_only(label)
    root = note_class(found.group("root"))
    bass = root if found.group("bass") is None else note_class(found.group("bass"))
    return stack(root, CHORDINO_QUALITIES[found.group("quality")], bass, label)
