"""Chord stacks, chord sequences, and meter layers (ADR 0077): payload rules,
computed names, and lossy exchange views.

The three contracts, `archive.claim/chord_stacks`,
`archive.claim/chord_sequence`, and `archive.claim/meter_layers`, each
version 1, are decided by ADR 0077 (amended by ADR 0078) and installed by
specification 0.0.9 (D-17). This module holds their rules as World re-checks
them when it projects such a claim, the names a chord stack can be given,
and the JAMS views. It uses only the standard library, so an importer or
extractor can check a proposal with it before Door does; Door remains the
authority (INV-2), and a test feeds the same accepted and refused claims to
both.

Every name and export here is a computed view: regenerable, never stored as
the claim, and never a judgment between claims (INV-7). Pitch classes are
integers, C = 0; how a pitch class is spelled (E flat or D sharp) is an open
question, so `PITCH_NAMES` is a stated default, not a claim.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from fractions import Fraction
from itertools import combinations, product
from typing import Any

CHORD_STACKS = ("archive.claim/chord_stacks", "1")
CHORD_SEQUENCE = ("archive.claim/chord_sequence", "1")
METER_LAYERS = ("archive.claim/meter_layers", "1")

PITCH_NAMES = ("C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")
DECIMAL = re.compile(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?\Z")
# An http or https URL in the characters RFC 3986 allows, as specification
# 0.0.9's contracts state it, so World and Door accept the same URLs.
URL = re.compile(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+\Z")
OBJECT_ID = re.compile(r"urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
REVISION_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
DENOMINATORS = (1, 2, 4, 8, 16, 32, 64)
MAX_SPANS = 10000
MAX_BARS = 10000
MAX_BEATS = 100000
MAX_LAYERS = 8
MAX_READINGS = 16
MAX_UNCERTAIN = 4
MAX_VOICING = 32
MAX_TEXT = 200
MAX_URL = 2000
MAX_RULE = 500
MAX_ENTRIES = 10000
MAX_REPEATS = 1000
MAX_LABEL = 100
SPAN_ENDS = ("asserted", "held")
LAYER_ROLES = ("written", "felt")
MODULATION_KINDS = ("equation", "regrouping")


class HarmonyError(ValueError):
    """A chord stacks or meter layers claim that breaks a rule of ADR 0077."""


# ---------------------------------------------------------------------------
# Shared pieces


def _closed(value: Any, required: set[str], optional: set[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HarmonyError(f"{where} must be an object")
    missing = required - set(value)
    extra = set(value) - required - optional
    if missing or extra:
        raise HarmonyError(f"{where} has missing {sorted(missing)} or unknown {sorted(extra)} fields")
    return value


def _int(value: Any, where: str, low: int, high: int | None = None) -> int:
    # bool is an int in Python; a JSON true is not a number.
    if not isinstance(value, int) or isinstance(value, bool) or value < low or (high is not None and value > high):
        bound = f"{low}.." + ("" if high is None else str(high))
        raise HarmonyError(f"{where} must be an integer in {bound}")
    return value


def _text(value: Any, where: str, maximum: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise HarmonyError(f"{where} must be text of 1 to {maximum} characters")
    return value


def _list(value: Any, where: str, low: int, high: int) -> list[Any]:
    if not isinstance(value, list) or not low <= len(value) <= high:
        raise HarmonyError(f"{where} must be a list of {low} to {high} items")
    return value


def _ascending_pitch_classes(value: Any, where: str, low: int) -> list[int]:
    items = _list(value, where, low, 12)
    for index, item in enumerate(items):
        _int(item, f"{where}[{index}]", 0, 11)
    if any(a >= b for a, b in zip(items, items[1:])):
        raise HarmonyError(f"{where} must be distinct pitch classes in ascending order")
    return items


def _refs_by_role(refs: Any, counts: dict[str, tuple[int, int]], where: str) -> dict[str, list[dict[str, Any]]]:
    """References grouped by role, with each role's (minimum, maximum) count."""
    if not isinstance(refs, list):
        raise HarmonyError(f"{where} refs must be a list")
    by_role: dict[str, list[dict[str, Any]]] = {role: [] for role in counts}
    for index, ref in enumerate(refs):
        _closed(ref, {"role", "id", "revision_id"}, set(), f"{where} ref {index}")
        if ref["role"] not in counts:
            raise HarmonyError(f"{where} ref {index} has unknown role {ref['role']!r}")
        if not isinstance(ref["id"], str) or not OBJECT_ID.fullmatch(ref["id"]) \
                or not isinstance(ref["revision_id"], str) or not REVISION_ID.fullmatch(ref["revision_id"]):
            raise HarmonyError(f"{where} ref {index} must pin an object and an exact revision")
        by_role[ref["role"]].append(ref)
    for role, (low, high) in counts.items():
        if not low <= len(by_role[role]) <= high:
            raise HarmonyError(f"{where} needs {low} to {high} '{role}' references")
    return by_role


def _check_stream(by_role: dict[str, list[dict[str, Any]]], stream_claim: Any, where: str) -> tuple[int, int]:
    """The pinned audio stream's (sample_rate, sample_count), after checking
    that it is about the same Artifact revision as the `source` pin."""
    stream = _closed(stream_claim, {"payload", "refs"}, {"revision_id"}, f"{where} stream claim")
    if "revision_id" in stream and stream["revision_id"] != by_role["stream"][0]["revision_id"]:
        raise HarmonyError(f"{where} was given a stream claim other than the one it pins")
    measured = [ref for ref in stream["refs"] if isinstance(ref, dict) and ref.get("role") == "artifact"]
    source = by_role["source"][0]
    if len(measured) != 1 or (measured[0].get("id"), measured[0].get("revision_id")) != (source["id"], source["revision_id"]):
        raise HarmonyError(f"{where} pins a stream of another Artifact revision")
    payload = stream["payload"] if isinstance(stream["payload"], dict) else {}
    rate = _int(payload.get("sample_rate"), f"{where} stream sample_rate", 1)
    count = _int(payload.get("sample_count"), f"{where} stream sample_count", 1)
    return rate, count


def _check_source_fields(payload: dict[str, Any], by_role: dict[str, list[dict[str, Any]]], where: str,
                         timed: bool = True) -> bool:
    """The outside-source fields. A timed claim states an `alignment` exactly
    when it pins a `document` or a `sequence` it placed on the recording; an
    untimed sequence has no samples, so it never states one."""
    has_document = bool(by_role["document"])
    aligned = timed and (has_document or bool(by_role.get("sequence")))
    if aligned != ("alignment" in payload):
        raise HarmonyError(f"{where} states an alignment exactly when it places a document or a sequence"
                           if timed else f"{where} is untimed and states no alignment")
    if "document_url" in payload:
        if not has_document:
            raise HarmonyError(f"{where} names a document_url without pinning the document")
        url = _text(payload["document_url"], f"{where} document_url", MAX_URL)
        if not URL.fullmatch(url):
            raise HarmonyError(f"{where} document_url must be an http or https URL")
    if aligned:
        alignment = _closed(payload["alignment"], {"method"}, {"max_error_samples"}, f"{where} alignment")
        _text(alignment["method"], f"{where} alignment method", MAX_TEXT)
        if "max_error_samples" in alignment:
            _int(alignment["max_error_samples"], f"{where} alignment max_error_samples", 0)
    return has_document


def _check_span_bounds(start: Any, end: Any, previous_end: int, count: int, where: str) -> tuple[int, int]:
    start = _int(start, f"{where} start_sample", 0)
    end = _int(end, f"{where} end_sample", 1)
    if end <= start:
        raise HarmonyError(f"{where} must end after it starts")
    if start < previous_end:
        raise HarmonyError(f"{where} overlaps the one before it")
    if end > count:
        raise HarmonyError(f"{where} ends past the stream's sample count")
    return start, end


def _check_locator(value: Any, has_document: bool, fields: set[str], where: str) -> None:
    if not has_document:
        raise HarmonyError(f"{where} has a locator but the claim pins no document")
    locator = _closed(value, {"bar"}, fields - {"bar"}, f"{where} locator")
    for key in locator:
        _int(locator[key], f"{where} locator {key}", 1)


SOURCE_ROLES = {
    "source": (1, 1),     # the audio whose timeline the spans are in: the mix
    "stream": (1, 1),     # audio_stream v1 about `source`
    "run": (0, 1),        # derivation_run v1, when a machine asserted the claim
    "document": (0, 1),   # an outside source's admitted bytes
    "analysed": (0, 8),   # audio analysed instead of the mix, such as a stem
}



CHORD_ROLES = {
    **SOURCE_ROLES,
    "sequence": (0, 1),   # the chord_sequence v1 claim an alignment placed on this recording
    "corrects": (0, 8),   # chord claims this one corrects; they stay, untouched
    # ADR 0078 amends ADR 0077: the contribution saying whose part this is.
    "contribution": (0, 1),
}

SEQUENCE_ROLES = {
    "work": (0, 1),       # the Work (an Entity of kind musical_work) the sequence describes
    "document": (0, 1),   # the document the sequence was read from
    "run": (0, 1),        # derivation_run v1, when a tool read the document
    "corrects": (0, 8),   # chord_sequence claims this one corrects
}


# ---------------------------------------------------------------------------
# Chord stacks


def check_chord_stacks(claim: dict[str, Any], stream_claim: dict[str, Any],
                       sequence_claim: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Check an `archive.claim/chord_stacks` version 1 claim's payload and
    references against every rule of ADR 0077, with the `audio_stream` claim
    its `stream` reference pins and, when it pins a `sequence`, optionally
    that chord sequence claim, to check each span's `entry` against it.
    Returns the spans; raises HarmonyError. No rule asks the spans to cover
    the recording: a claim of three spans in a long improvisation is whole."""
    where = "chord stacks"
    record = _closed(claim, {"payload", "refs"}, {"revision_id", "agent"}, where)
    by_role = _refs_by_role(record["refs"], CHORD_ROLES, where)
    rate, count = _check_stream(by_role, stream_claim, where)
    payload = _closed(record["payload"], {"sample_rate", "spans"},
                      {"part", "weight_scale", "document_url", "alignment", "hold_rule"}, f"{where} payload")
    if _int(payload["sample_rate"], f"{where} sample_rate", 1) != rate:
        raise HarmonyError(f"{where} sample_rate differs from the stream's")
    if "part" in payload:
        _text(payload["part"], f"{where} part", 100)
    weighted = "weight_scale" in payload
    if weighted:
        _text(payload["weight_scale"], f"{where} weight_scale", MAX_TEXT)
    has_document = _check_source_fields(payload, by_role, where)
    entries = None
    if by_role["sequence"] and sequence_claim is not None:
        if sequence_claim.get("revision_id", by_role["sequence"][0]["revision_id"]) \
                != by_role["sequence"][0]["revision_id"]:
            raise HarmonyError(f"{where} was given a sequence claim other than the one it pins")
        entries = check_chord_sequence(sequence_claim)
    spans = _list(payload["spans"], f"{where} spans", 1, MAX_SPANS)
    previous_end, held = 0, False
    for index, span in enumerate(spans):
        at = f"{where} span {index}"
        _closed(span, {"start_sample", "end_sample", "readings"}, {"locator", "end", "entry"}, at)
        if "entry" in span:
            if not by_role["sequence"]:
                raise HarmonyError(f"{at} names a sequence entry but the claim pins no sequence")
            _int(span["entry"], f"{at} entry", 0, None if entries is None else len(entries) - 1)
        _, previous_end = _check_span_bounds(span["start_sample"], span["end_sample"], previous_end, count, at)
        if span.get("end", "asserted") not in SPAN_ENDS:
            raise HarmonyError(f"{at} end must be one of {SPAN_ENDS}")
        held = held or span.get("end") == "held"
        if "locator" in span:
            _check_locator(span["locator"], has_document, {"bar", "beat"}, at)
        readings = _list(span["readings"], f"{at} readings", 1, MAX_READINGS)
        for number, reading in enumerate(readings):
            check_reading(reading, weighted, f"{at} reading {number}")
        if len({_canonical(reading) for reading in readings}) != len(readings):
            raise HarmonyError(f"{at} repeats a reading")
    # A held end is the importer's, so the claim says by what rule it held.
    if held != ("hold_rule" in payload):
        raise HarmonyError(f"{where} states a hold_rule exactly when a span's end is held")
    if held:
        _text(payload["hold_rule"], f"{where} hold_rule", MAX_RULE)
    return spans


def span_end_held(span: dict[str, Any]) -> bool:
    """Whether a span's end was held by an importer's rule (the claim's
    `hold_rule`) rather than asserted: weaker evidence of where the chord
    stopped than an asserted end, and to be weighed as such by any view."""
    return span.get("end", "asserted") == "held"


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def check_reading(reading: Any, weighted: bool, where: str = "reading") -> str:
    """Check one reading; return its form: `stack`, `no_chord`, or `text`."""
    if not isinstance(reading, dict):
        raise HarmonyError(f"{where} must be an object")
    if weighted != ("weight" in reading):
        raise HarmonyError(f"{where} has a weight exactly when the claim states a weight_scale")
    if "weight" in reading and (not isinstance(reading["weight"], str) or not DECIMAL.fullmatch(reading["weight"])):
        raise HarmonyError(f"{where} weight must be exact decimal text")
    if "text" in reading:
        _text(reading["text"], f"{where} text", MAX_TEXT)
    common = {"text", "weight"}
    if "no_chord" in reading:
        _closed(reading, {"no_chord"}, common, where)
        if reading["no_chord"] is not True:
            raise HarmonyError(f"{where} no_chord must be true")
        return "no_chord"
    if "bass" not in reading:
        _closed(reading, {"text"}, {"weight"}, where)
        return "text"
    _closed(reading, {"bass", "tones"}, common | {"root", "uncertain", "voicing"}, where)
    bass = _int(reading["bass"], f"{where} bass", 0, 11)
    tones = set(_ascending_pitch_classes(reading["tones"], f"{where} tones", 0))
    if "root" in reading:
        _int(reading["root"], f"{where} root", 0, 11)
    unsure: set[int] = set()
    if "uncertain" in reading:
        groups = _list(reading["uncertain"], f"{where} uncertain", 1, MAX_UNCERTAIN)
        for index, group in enumerate(groups):
            at = f"{where} uncertain {index}"
            _closed(group, {"pitch_classes", "present"}, set(), at)
            if group["present"] not in ("exactly_one", "maybe"):
                raise HarmonyError(f"{at} present must be exactly_one or maybe")
            low = 2 if group["present"] == "exactly_one" else 1
            classes = set(_ascending_pitch_classes(group["pitch_classes"], f"{at} pitch_classes", low))
            if classes & (tones | {bass} | unsure):
                raise HarmonyError(f"{at} names a pitch class that is already certain or in another group")
            unsure |= classes
        keys = [group["pitch_classes"] for group in groups]
        if keys != sorted(keys):
            raise HarmonyError(f"{where} uncertain groups must be in ascending order of their pitch classes")
    if "voicing" in reading:
        notes = _list(reading["voicing"], f"{where} voicing", 1, MAX_VOICING)
        for index, note in enumerate(notes):
            _int(note, f"{where} voicing[{index}]", 0, 127)
        if any(a >= b for a, b in zip(notes, notes[1:])):
            raise HarmonyError(f"{where} voicing must be distinct notes in ascending order")
        if notes[0] % 12 != bass:
            raise HarmonyError(f"{where} voicing's lowest note is not the bass")
        above = {note % 12 for note in notes[1:]}
        if not tones <= above or not above <= tones | unsure:
            raise HarmonyError(f"{where} voicing's upper notes disagree with its tones")
    return "stack"


# ---------------------------------------------------------------------------
# Chord sequences


def check_chord_sequence(claim: dict[str, Any]) -> list[dict[str, Any]]:
    """Check an `archive.claim/chord_sequence` version 1 claim: chords in the
    order a document or a person gives them, with no times. Its subject is a
    Work, a document, or both, never a recording's samples. Returns the
    entries; raises HarmonyError.

    An entry's locator points into the document (a line, and optionally a
    word, syllable, or column of it), so a chord can sit over a lyric without
    the claim holding a word of the lyric."""
    where = "chord sequence"
    record = _closed(claim, {"payload", "refs"}, {"revision_id", "agent"}, where)
    by_role = _refs_by_role(record["refs"], SEQUENCE_ROLES, where)
    if not by_role["work"] and not by_role["document"]:
        raise HarmonyError(f"{where} needs a work or a document as its subject")
    payload = _closed(record["payload"], {"entries"},
                      {"part", "weight_scale", "document_url", "repeats"}, f"{where} payload")
    if "part" in payload:
        _text(payload["part"], f"{where} part", 100)
    weighted = "weight_scale" in payload
    if weighted:
        _text(payload["weight_scale"], f"{where} weight_scale", MAX_TEXT)
    has_document = _check_source_fields(payload, by_role, where, timed=False)
    entries = _list(payload["entries"], f"{where} entries", 1, MAX_ENTRIES)
    previous = None
    for index, entry in enumerate(entries):
        at = f"{where} entry {index}"
        _closed(entry, {"readings"}, {"locator", "section"}, at)
        if "section" in entry:
            _text(entry["section"], f"{at} section", MAX_LABEL)
        if "locator" in entry:
            if not has_document:
                raise HarmonyError(f"{at} has a locator but the claim pins no document")
            locator = _closed(entry["locator"], {"line"}, {"word", "syllable", "column"}, f"{at} locator")
            for key in locator:
                _int(locator[key], f"{at} locator {key}", 1)
            if len(locator) > 2:
                raise HarmonyError(f"{at} locator gives at most one of word, syllable, or column")
            position = (locator["line"], locator.get("word", locator.get("syllable", locator.get("column", 0))))
            if previous is not None and position < previous:
                raise HarmonyError(f"{at} is located before the entry before it")
            previous = position
        readings = _list(entry["readings"], f"{at} readings", 1, MAX_READINGS)
        for number, reading in enumerate(readings):
            check_reading(reading, weighted, f"{at} reading {number}")
        if len({_canonical(reading) for reading in readings}) != len(readings):
            raise HarmonyError(f"{at} repeats a reading")
    ranges = []
    for index, repeat in enumerate(_list(payload.get("repeats", []), f"{where} repeats", 0, MAX_REPEATS)):
        at = f"{where} repeat {index}"
        _closed(repeat, {"first", "last", "times"}, set(), at)
        first = _int(repeat["first"], f"{at} first", 0, len(entries) - 1)
        last = _int(repeat["last"], f"{at} last", first, len(entries) - 1)
        _int(repeat["times"], f"{at} times", 2, 999)
        ranges.append((first, -last))
    if ranges != sorted(set(ranges)):
        raise HarmonyError(f"{where} repeats must be distinct and in order of first entry, outer before inner")
    for (a, b), (c, d) in combinations(ranges, 2):
        if c <= -b < -d:  # the second starts inside the first and ends after it
            raise HarmonyError(f"{where} repeats must nest or be apart, never cross")
    return entries


def sequence_order(payload: dict[str, Any]) -> list[int]:
    """The entries' indices in the order played, with every repeat unrolled:
    "x4" over entries 0 and 1 gives 0, 1, 0, 1, 0, 1, 0, 1. A view of a
    checked payload, the order an alignment places on a recording."""
    repeats = sorted(payload.get("repeats", []), key=lambda r: (r["first"], -r["last"]))

    def play(first: int, last: int, inner: list[dict[str, Any]]) -> list[int]:
        order, at = [], first
        while at <= last:
            outer = next((r for r in inner if r["first"] == at), None)
            if outer is None:
                order.append(at)
                at += 1
                continue
            nested = [r for r in inner if r is not outer and outer["first"] <= r["first"] and r["last"] <= outer["last"]]
            order += play(outer["first"], outer["last"], nested) * outer["times"]
            inner = [r for r in inner if r is not outer and r not in nested]
            at = outer["last"] + 1
        return order

    return play(0, len(payload["entries"]) - 1, repeats)


# ---------------------------------------------------------------------------
# Computed names

# Harte (2005) shorthands, the strict set JAMS `chord_harte` accepts.
HARTE_SHORTHANDS: dict[str, frozenset[int]] = {
    "maj": frozenset({0, 4, 7}), "min": frozenset({0, 3, 7}), "dim": frozenset({0, 3, 6}),
    "aug": frozenset({0, 4, 8}), "maj7": frozenset({0, 4, 7, 11}), "min7": frozenset({0, 3, 7, 10}),
    "7": frozenset({0, 4, 7, 10}), "dim7": frozenset({0, 3, 6, 9}), "hdim7": frozenset({0, 3, 6, 10}),
    "minmaj7": frozenset({0, 3, 7, 11}), "maj6": frozenset({0, 4, 7, 9}), "min6": frozenset({0, 3, 7, 9}),
    "9": frozenset({0, 2, 4, 7, 10}), "maj9": frozenset({0, 2, 4, 7, 11}), "min9": frozenset({0, 2, 3, 7, 10}),
    "sus4": frozenset({0, 5, 7}),
}

# Lead-sheet qualities, matched exactly. A missing quality means no symbol,
# never a guess; the Harte name always exists. No suffix holds a slash, so a
# symbol's only slash introduces its bass: 6(9), never 6/9.
SYMBOL_QUALITIES: dict[frozenset[int], str] = {
    frozenset(intervals): suffix for suffix, intervals in (
        ("", {0, 4, 7}), ("m", {0, 3, 7}), ("dim", {0, 3, 6}), ("+", {0, 4, 8}), ("5", {0, 7}),
        ("sus2", {0, 2, 7}), ("sus4", {0, 5, 7}), ("6", {0, 4, 7, 9}), ("m6", {0, 3, 7, 9}),
        ("6(9)", {0, 2, 4, 7, 9}), ("m6(9)", {0, 2, 3, 7, 9}), ("add9", {0, 2, 4, 7}), ("m(add9)", {0, 2, 3, 7}),
        ("maj7", {0, 4, 7, 11}), ("7", {0, 4, 7, 10}), ("m7", {0, 3, 7, 10}), ("m(maj7)", {0, 3, 7, 11}),
        ("m7b5", {0, 3, 6, 10}), ("dim7", {0, 3, 6, 9}), ("7sus4", {0, 5, 7, 10}), ("+7", {0, 4, 8, 10}),
        ("maj7#5", {0, 4, 8, 11}), ("maj7#11", {0, 4, 6, 7, 11}), ("9", {0, 2, 4, 7, 10}),
        ("maj9", {0, 2, 4, 7, 11}), ("m9", {0, 2, 3, 7, 10}), ("m(maj9)", {0, 2, 3, 7, 11}),
        ("7b9", {0, 1, 4, 7, 10}), ("7#9", {0, 3, 4, 7, 10}), ("7#11", {0, 4, 6, 7, 10}),
        ("9sus4", {0, 2, 5, 7, 10}), ("m11", {0, 2, 3, 5, 7, 10}), ("13", {0, 2, 4, 7, 9, 10}),
        ("maj13", {0, 2, 4, 7, 9, 11}), ("m13", {0, 2, 3, 7, 9, 10}), ("m(maj13)", {0, 2, 3, 7, 9, 11}),
    )
}

SIMPLE_DEGREES = ("1", "b2", "2", "b3", "3", "4", "b5", "5", "b6", "6", "b7", "7")


def _added_degree(interval: int, chord: frozenset[int], sounding: frozenset[int]) -> str:
    """The Harte degree for an interval added to a shorthand, written as an
    extension where the chord, or what sounds, has the tone it extends. A
    raised fifth beside a major third and no sounding fifth is the fifth
    itself, #5: C E G# B is C:aug(7), never C:maj7(*5,b13), whatever the
    shorthand it is added to."""
    has = chord | sounding
    sevenths = bool(has & {10, 11})
    raised_fifth = 4 in sounding and 7 not in sounding
    return {
        1: "b9", 2: "9",
        3: "#9" if 4 in has else "b3",
        5: "11" if sevenths else "4",
        6: "#11" if 7 in has else "b5",
        8: "#5" if raised_fifth else ("b13" if 7 in has else "b6"),
        9: "13" if sevenths else "6",
    }.get(interval, SIMPLE_DEGREES[interval])


def _harte(root: int, sounding: frozenset[int], bass: int) -> tuple[str, int]:
    """The strict Harte string for `sounding` intervals above `root`, and its
    distance from a bare shorthand (0 = a shorthand exactly). A shorthand may
    omit only the root and the perfect fifth; when it is no nearer than a
    plain interval list, the list is used."""
    best: tuple[int, int, str] | None = None
    for name, chord in HARTE_SHORTHANDS.items():
        omitted = chord - sounding
        if not omitted <= {0, 7}:
            continue
        added = sounding - chord
        # Adding a third or a seventh changes the chord's kind; adding an
        # extension (a ninth, eleventh, or thirteenth) only extends it.
        cost = len(omitted) + sum(2 if i in {3, 4, 10, 11} else 1 for i in added)
        # At equal cost, fewer marks: an augmented seventh is C:aug(b7), not
        # C:7(*5,#5).
        key = (cost, len(omitted) + len(added), -len(chord), name)
        if best is None or key < best:
            best = key
    explicit_cost = len(sounding - {0}) + (0 if 0 in sounding else 1)
    bass_part = "" if bass == root else "/" + SIMPLE_DEGREES[(bass - root) % 12]
    root_name = PITCH_NAMES[root]
    if best is None or best[0] >= explicit_cost and best[0] > 0:
        degrees = [SIMPLE_DEGREES[i] for i in sorted(sounding - {0})]
        if 0 not in sounding:
            degrees.insert(0, "*1")
        degrees = degrees or ["1"]  # a lone note: Harte has no empty list
        return f"{root_name}:({','.join(degrees)}){bass_part}", explicit_cost
    cost, _, _, name = best
    chord = HARTE_SHORTHANDS[name]
    marks = ["*" + SIMPLE_DEGREES[i] for i in sorted(chord - sounding)]
    marks += [_added_degree(i, chord, sounding) for i in sorted(sounding - chord)]
    extra = f"({','.join(marks)})" if marks else ""
    return f"{root_name}:{name}{extra}{bass_part}", cost


def _symbol(root: int, sounding: frozenset[int], bass: int) -> str | None:
    if sounding == {0}:
        # One pitch class, perhaps doubled: no chord quality, so the symbol
        # follows Harte's lone-note list, C:(1), rather than claiming a
        # fifth it then omits.
        return PITCH_NAMES[root] + "(1)"
    suffix = SYMBOL_QUALITIES.get(sounding | {0})
    if suffix is None and 7 not in sounding:
        found = SYMBOL_QUALITIES.get(sounding | {0, 7})
        suffix = None if found is None else found + "(no5)"
    if suffix is None:
        return None
    if 0 not in sounding:
        suffix += "(no root)"
    return PITCH_NAMES[root] + suffix + ("" if bass == root else "/" + PITCH_NAMES[bass])


@dataclass(frozen=True)
class Name:
    """One computed name of a stack: a view, never a claim."""
    root: int
    asserted_root: bool   # the reading named this root; otherwise it is a candidate
    harte: str            # strict Harte, valid in JAMS `chord_harte` and `chord`
    symbol: str | None    # a lead-sheet symbol, when a known quality matches exactly
    distance: int         # 0 when the Harte name is a bare shorthand


def stack_names(bass: int, tones: set[int] | frozenset[int] | list[int], root: int | None = None) -> list[Name]:
    """Every name of the stack `bass` + `tones` (pitch classes above the bass).

    With an asserted `root`, the names use it, sounding or not. Without one,
    every sounding pitch class is a candidate root, so one stack has several
    names (Cm7/G and Eb6/G). The order is a stated view rule, not a judgment:
    asserted root first, then names with a lead-sheet symbol, then nearer to
    a bare Harte shorthand, then root position, then pitch class."""
    sounding = frozenset(tones) | {bass}
    roots = [root] if root is not None else sorted(sounding)
    names = []
    for candidate in roots:
        intervals = frozenset((pc - candidate) % 12 for pc in sounding)
        harte, distance = _harte(candidate, intervals, bass)
        names.append(Name(candidate, root is not None, harte, _symbol(candidate, intervals, bass), distance))
    names.sort(key=lambda n: (not n.asserted_root, n.symbol is None, n.distance, n.root != bass, n.root))
    return names


def reading_names(reading: dict[str, Any]) -> list[tuple[frozenset[int], list[Name]]]:
    """Names for each way a stack reading's uncertain tones could resolve:
    (the tones above the bass, the names). An `exactly_one` group contributes
    one of its pitch classes; a `maybe` group contributes none or one."""
    check_reading(reading, "weight" in reading)
    if "bass" not in reading:
        return []
    choices = []
    for group in reading.get("uncertain", []):
        options = [frozenset({pc}) for pc in group["pitch_classes"]]
        choices.append(options if group["present"] == "exactly_one" else [frozenset()] + options)
    result = []
    for picked in product(*choices):
        tones = frozenset(reading["tones"]).union(*picked)
        result.append((tones, stack_names(reading["bass"], tones, reading.get("root"))))
    return result


# ---------------------------------------------------------------------------
# Meter layers


def note_value(value: Any, where: str) -> Fraction:
    """The length of a note value in whole notes: {denominator, dots}."""
    note = _closed(value, {"denominator"}, {"dots"}, where)
    if note["denominator"] not in DENOMINATORS or isinstance(note["denominator"], bool):
        raise HarmonyError(f"{where} denominator must be one of {DENOMINATORS}")
    dots = _int(note.get("dots", 0), f"{where} dots", 0, 2)
    return Fraction(1, note["denominator"]) * (2 - Fraction(1, 2 ** dots))


def check_meter_layers(claim: dict[str, Any], stream_claim: dict[str, Any]) -> list[dict[str, Any]]:
    """Check an `archive.claim/meter_layers` version 1 claim against every rule
    of ADR 0077, with the `audio_stream` claim it pins. Returns the layers."""
    where = "meter layers"
    record = _closed(claim, {"payload", "refs"}, {"revision_id", "agent"}, where)
    by_role = _refs_by_role(record["refs"], SOURCE_ROLES, where)
    rate, count = _check_stream(by_role, stream_claim, where)
    payload = _closed(record["payload"], {"sample_rate", "layers"},
                      {"modulations", "document_url", "alignment"}, f"{where} payload")
    if _int(payload["sample_rate"], f"{where} sample_rate", 1) != rate:
        raise HarmonyError(f"{where} sample_rate differs from the stream's")
    has_document = _check_source_fields(payload, by_role, where)
    layers = _list(payload["layers"], f"{where} layers", 1, MAX_LAYERS)
    for number, layer in enumerate(layers):
        at = f"{where} layer {number}"
        if not isinstance(layer, dict) or ("bars" in layer) == ("beats" in layer):
            raise HarmonyError(f"{at} must hold exactly one of bars or beats")
        if "label" in layer:
            _text(layer["label"], f"{at} label", 100)
        if "role" in layer and layer["role"] not in LAYER_ROLES:
            raise HarmonyError(f"{at} role must be one of {LAYER_ROLES}")
        if "beats" in layer:
            _check_beat_layer(layer, count, at)
            continue
        _closed(layer, {"bars"}, {"label", "role"}, at)
        previous_end = 0
        for index, bar in enumerate(_list(layer["bars"], f"{at} bars", 1, MAX_BARS)):
            previous_end = _check_bar(bar, previous_end, count, has_document, f"{at} bar {index}")
    roles = {layer.get("role") for layer in layers}
    if "felt" in roles and "written" not in roles:
        raise HarmonyError(f"{where} has a felt layer but no written layer for it to be felt against")
    seen = []
    for index, link in enumerate(_list(payload.get("modulations", []), f"{where} modulations", 0, MAX_BARS)):
        seen.append(_check_modulation(link, layers, f"{where} modulation {index}"))
    if seen != sorted(set(seen)):
        raise HarmonyError(f"{where} modulations must be distinct and in order of layer and bar")
    return layers


def _check_beat_layer(layer: dict[str, Any], count: int, where: str) -> None:
    """A beat-only layer: beat samples with no bars, so no numerator or
    denominator, and a beat note value only when the asserter states one."""
    _closed(layer, {"beats"}, {"label", "role", "downbeats", "beat_unit"}, where)
    beats = _list(layer["beats"], f"{where} beats", 1, MAX_BEATS)
    for index, beat in enumerate(beats):
        _int(beat, f"{where} beats[{index}]", 0, count - 1)
    if any(a >= b for a, b in zip(beats, beats[1:])):
        raise HarmonyError(f"{where} beats must be distinct samples in ascending order")
    if "downbeats" in layer:
        downbeats = _list(layer["downbeats"], f"{where} downbeats", 1, len(beats))
        for index, beat in enumerate(downbeats):
            _int(beat, f"{where} downbeats[{index}]", 0)
        if any(a >= b for a, b in zip(downbeats, downbeats[1:])):
            raise HarmonyError(f"{where} downbeats must be distinct samples in ascending order")
        if not set(downbeats) <= set(beats):
            raise HarmonyError(f"{where} downbeats must be among its beats")
    if "beat_unit" in layer:
        note_value(layer["beat_unit"], f"{where} beat_unit")


def _check_modulation(link: Any, layers: list[dict[str, Any]], where: str) -> tuple[int, int]:
    """A link between adjacent bars of a bars layer. An `equation` says a note
    value before lasts as long as one after, so the tempo changes by their
    ratio; a `regrouping` keeps every note value's length and moves the felt
    pulse from one note value to another."""
    if not isinstance(link, dict) or link.get("kind") not in MODULATION_KINDS:
        raise HarmonyError(f"{where} kind must be one of {MODULATION_KINDS}")
    if link["kind"] == "equation":
        _closed(link, {"layer", "bar", "kind", "before", "after"}, set(), where)
        note_value(link["before"], f"{where} before")
        note_value(link["after"], f"{where} after")
    else:
        _closed(link, {"layer", "bar", "kind", "pulse_before", "pulse_after"}, set(), where)
        if note_value(link["pulse_before"], f"{where} pulse_before") == \
                note_value(link["pulse_after"], f"{where} pulse_after"):
            raise HarmonyError(f"{where} is a regrouping whose pulse does not move")
    layer = _int(link["layer"], f"{where} layer", 0, len(layers) - 1)
    if "bars" not in layers[layer]:
        raise HarmonyError(f"{where} joins bars of a layer that has only beats")
    bars = layers[layer]["bars"]
    bar = _int(link["bar"], f"{where} bar", 1, len(bars) - 1)
    if bars[bar - 1]["end_sample"] != bars[bar]["start_sample"]:
        raise HarmonyError(f"{where} joins bars that are not adjacent")
    return layer, bar


def tempo_ratio(link: dict[str, Any]) -> Fraction:
    """A checked modulation's tempo before over its tempo after, in any one
    note value per minute: the length of `before` over the length of `after`
    for an equation (dotted quarter = quarter is 3/2), and 1 for a
    regrouping. Derived, never stored."""
    if link["kind"] == "regrouping":
        return Fraction(1)
    return note_value(link["before"], "before") / note_value(link["after"], "after")


def _check_bar(bar: Any, previous_end: int, count: int, has_document: bool, where: str) -> int:
    _closed(bar, {"start_sample", "end_sample", "numerator", "denominator"}, {"grouping", "beats", "locator"}, where)
    start, end = _check_span_bounds(bar["start_sample"], bar["end_sample"], previous_end, count, where)
    numerator = _int(bar["numerator"], f"{where} numerator", 1, 64)
    if bar["denominator"] not in DENOMINATORS or isinstance(bar["denominator"], bool):
        raise HarmonyError(f"{where} denominator must be one of {DENOMINATORS}")
    beat_count = numerator
    if "grouping" in bar:
        grouping = _list(bar["grouping"], f"{where} grouping", 1, 64)
        for index, group in enumerate(grouping):
            _int(group, f"{where} grouping[{index}]", 1)
        if sum(grouping) != numerator:
            raise HarmonyError(f"{where} grouping does not add up to its numerator")
        beat_count = len(grouping)
    if "beats" in bar:
        beats = _list(bar["beats"], f"{where} beats", beat_count, beat_count)
        for index, beat in enumerate(beats):
            _int(beat, f"{where} beats[{index}]", 0)
        if beats[0] != start or any(a >= b for a, b in zip(beats, beats[1:])) or beats[-1] >= end:
            raise HarmonyError(f"{where} beats must start on the downbeat and rise within the bar")
    if "locator" in bar:
        _check_locator(bar["locator"], has_document, {"bar"}, where)
    return end


def bar_tempi(layer: dict[str, Any], sample_rate: int) -> list[Fraction]:
    """Each bar's tempo in quarter notes per minute, derived from its length.
    Tempo is never stored (ADR 0077); this is one of its views."""
    tempi = []
    for bar in layer["bars"]:
        quarters = Fraction(bar["numerator"] * 4, bar["denominator"])
        tempi.append(quarters * 60 * sample_rate / (bar["end_sample"] - bar["start_sample"]))
    return tempi


def beat_tempi(layer: dict[str, Any], sample_rate: int) -> list[Fraction]:
    """The tempo of each interval between a beat-only layer's beats: quarter
    notes per minute when the layer states its `beat_unit`, otherwise beats
    per minute, since the beat's note value is unknown."""
    unit = note_value(layer["beat_unit"], "beat_unit") * 4 if "beat_unit" in layer else Fraction(1)
    beats = layer["beats"]
    return [unit * 60 * sample_rate / (b - a) for a, b in zip(beats, beats[1:])]


def beat_positions(bar: dict[str, Any]) -> list[int]:
    """Each counted beat's 1-based position in units of the denominator:
    2+2+3 gives 1, 3, 5."""
    groups = bar.get("grouping", [1] * bar["numerator"])
    positions, at = [], 1
    for group in groups:
        positions.append(at)
        at += group
    return positions


# ---------------------------------------------------------------------------
# JAMS views (lossy)


def _annotation(namespace: str, data: list[dict[str, Any]], provenance: dict[str, str] | None,
                lossy: list[str], rules: str = "", sandbox: dict[str, Any] | None = None) -> dict[str, Any]:
    provenance = provenance or {}
    return {
        "namespace": namespace,
        "annotation_metadata": {
            "curator": {"name": "", "email": ""},
            "annotator": {"agent": provenance.get("agent", "")},
            "version": "", "corpus": "", "annotation_tools": "",
            "annotation_rules": "ADR 0077 export view" + (f"; {rules}" if rules else ""),
            "validation": "",
            "data_source": provenance.get("revision_id", ""),
        },
        "data": data,
        "sandbox": {"archive_lossy": sorted(set(lossy)), **(sandbox or {})},
        "time": 0,
        "duration": None,
    }


def jams_chords(payload: dict[str, Any], *, rank: int = 0, namespace: str = "chord_harte",
                provenance: dict[str, str] | None = None) -> dict[str, Any]:
    """One JAMS annotation of a checked chord stacks payload: each span's
    reading at `rank` (0 = the asserter's first), as a strict Harte string.

    Lost on the way, and listed in the annotation's sandbox: the other
    readings, uncertain tones, voicings, the source's own text, locators, and
    weights when the claim's scale is not a probability. A JAMS observation
    has no field for a held end, so the claim's `hold_rule` goes in the
    annotation's free-text `annotation_rules` and the held observations'
    indices in sandbox `archive_held_ends`; a reader that ignores both sees
    every end as asserted."""
    if namespace not in ("chord_harte", "chord"):
        raise HarmonyError("JAMS chord namespace must be chord_harte or chord")
    rate = payload["sample_rate"]
    data, lossy, held = [], [], []
    probability = payload.get("weight_scale") == "probability"
    if "weight_scale" in payload and not probability:
        lossy.append(f"weights on the scale {payload['weight_scale']!r}")
    for span in payload["spans"]:
        if "locator" in span:
            lossy.append("source locators")
        if "entry" in span:
            lossy.append("the sequence entry each span was placed from")
        readings = span["readings"]
        if len(readings) > 1:
            lossy.append("alternative readings")
        if rank >= len(readings):
            continue
        reading = readings[rank]
        if "text" in reading:
            lossy.append("the source's own chord text")
        if "no_chord" in reading:
            value = "N"
        elif "bass" not in reading:
            if namespace == "chord_harte":
                lossy.append("readings with no stack (chord_harte has no unknown chord)")
                continue
            value = "X"
        else:
            if "uncertain" in reading:
                lossy.append("uncertain tones")
            if "voicing" in reading:
                lossy.append("voicings")
            value = stack_names(reading["bass"], reading["tones"], reading.get("root"))[0].harte
        if span_end_held(span):
            held.append(len(data))
            lossy.append("held span ends (kept only in annotation_rules and sandbox archive_held_ends)")
        data.append({
            "time": span["start_sample"] / rate,
            "duration": (span["end_sample"] - span["start_sample"]) / rate,
            "value": value,
            "confidence": float(reading["weight"]) if probability else None,
        })
    rules = f"held ends: {payload['hold_rule']}" if held else ""
    return _annotation(namespace, data, provenance, lossy, rules, {"archive_held_ends": held} if held else None)


def jams_meter(payload: dict[str, Any], *, layer: int = 0,
               provenance: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Two JAMS annotations of one layer of a checked meter layers payload.

    A bars layer gives `beat_position` and `tempo` (quarter notes per minute,
    per bar). JAMS has no namespace for a time signature's grouping, a metric
    modulation or regrouping, a layer's role, or simultaneous layers.
    Grouping survives only as the positions of the counted beats; a bar
    without beat times exports its downbeat alone.

    A beat-only layer gives `beat`, whose value is 1 at a downbeat the layer
    marks and null elsewhere (an unknown position, never a guessed count),
    and `tempo` per beat interval: quarter notes per minute when the layer
    states its beat unit, otherwise beats per minute."""
    rate = payload["sample_rate"]
    chosen = payload["layers"][layer]
    lossy = []
    if len(payload["layers"]) > 1:
        lossy.append("other simultaneous layers")
    if "role" in chosen:
        lossy.append(f"the layer's role ({chosen['role']})")
    if any(link["layer"] == layer for link in payload.get("modulations", [])):
        lossy.append("metric modulations and regroupings")
    if "beats" in chosen:
        return _jams_beat_layer(chosen, rate, provenance, lossy)
    beats, tempi = [], []
    for index, (bar, tempo) in enumerate(zip(chosen["bars"], bar_tempi(chosen, rate))):
        if "grouping" in bar:
            lossy.append("beat grouping")
        times = bar.get("beats")
        if times is None:
            lossy.append("bars without beat times")
            times = [bar["start_sample"]]
        measure = bar.get("locator", {}).get("bar", index + 1)
        for time, position in zip(times, beat_positions(bar)):
            beats.append({"time": time / rate, "duration": 0.0, "confidence": None, "value": {
                "position": position, "measure": measure,
                "num_beats": bar["numerator"], "beat_units": bar["denominator"]}})
        tempi.append({"time": bar["start_sample"] / rate,
                      "duration": (bar["end_sample"] - bar["start_sample"]) / rate,
                      "value": float(tempo), "confidence": None})
    return [_annotation("beat_position", beats, provenance, lossy),
            _annotation("tempo", tempi, provenance, lossy + ["tempo is derived from bar lengths"])]


def _jams_beat_layer(chosen: dict[str, Any], rate: int, provenance: dict[str, str] | None,
                     lossy: list[str]) -> list[dict[str, Any]]:
    downbeats = set(chosen.get("downbeats", []))
    beats = [{"time": b / rate, "duration": 0.0, "confidence": None, "value": 1 if b in downbeats else None}
             for b in chosen["beats"]]
    unit = "quarter notes" if "beat_unit" in chosen else "beats (the beat's note value is unstated)"
    tempi = [{"time": a / rate, "duration": (b - a) / rate, "value": float(tempo), "confidence": None}
             for a, b, tempo in zip(chosen["beats"], chosen["beats"][1:], beat_tempi(chosen, rate))]
    return [_annotation("beat", beats, provenance, lossy),
            _annotation("tempo", tempi, provenance,
                        lossy + [f"tempo is derived from beat intervals, in {unit} per minute"])]


def jams_document(duration_seconds: float, annotations: list[dict[str, Any]]) -> dict[str, Any]:
    """A JAMS document holding `annotations`; it names no title or artist."""
    return {
        "file_metadata": {"title": "", "artist": "", "release": "", "duration": duration_seconds,
                          "identifiers": {}, "jams_version": "0.3.4"},
        "annotations": annotations,
        "sandbox": {},
    }
