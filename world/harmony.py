"""Chord stacks and meter layers (ADR 0077): payload rules, computed names, and
lossy exchange views.

The two contracts, `archive.claim/chord_stacks` version 1 and
`archive.claim/meter_layers` version 1, are decided by ADR 0077 and are not
yet in a specification release, so Door cannot admit either. This module holds
their rules as World will re-check them when it projects such a claim, the
names a chord stack can be given, and the JAMS view of both. It uses only the
standard library, so a future importer or extractor can check a proposal with
it before Door does; Door remains the authority (INV-2).

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
from itertools import product
from typing import Any

CHORD_STACKS = ("archive.claim/chord_stacks", "1")
METER_LAYERS = ("archive.claim/meter_layers", "1")

PITCH_NAMES = ("C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")
DECIMAL = re.compile(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?\Z")
URL = re.compile(r"https?://[^\s]+\Z")
OBJECT_ID = re.compile(r"urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
REVISION_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
DENOMINATORS = (1, 2, 4, 8, 16, 32, 64)
MAX_SPANS = 10000
MAX_BARS = 10000
MAX_LAYERS = 8
MAX_READINGS = 16
MAX_UNCERTAIN = 4
MAX_VOICING = 32
MAX_TEXT = 200
MAX_URL = 2000


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


def _check_source_fields(payload: dict[str, Any], by_role: dict[str, list[dict[str, Any]]], where: str) -> bool:
    """The outside-source fields: present exactly when a `document` is pinned."""
    has_document = bool(by_role["document"])
    if has_document != ("alignment" in payload):
        raise HarmonyError(f"{where} states an alignment exactly when it pins a document")
    if "document_url" in payload:
        if not has_document:
            raise HarmonyError(f"{where} names a document_url without pinning the document")
        url = _text(payload["document_url"], f"{where} document_url", MAX_URL)
        if not URL.fullmatch(url):
            raise HarmonyError(f"{where} document_url must be an http or https URL")
    if has_document:
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


# ---------------------------------------------------------------------------
# Chord stacks


def check_chord_stacks(claim: dict[str, Any], stream_claim: dict[str, Any]) -> list[dict[str, Any]]:
    """Check an `archive.claim/chord_stacks` version 1 claim's payload and
    references against every rule of ADR 0077, with the `audio_stream` claim
    its `stream` reference pins. Returns the spans; raises HarmonyError."""
    where = "chord stacks"
    record = _closed(claim, {"payload", "refs"}, {"revision_id", "agent"}, where)
    by_role = _refs_by_role(record["refs"], SOURCE_ROLES, where)
    rate, count = _check_stream(by_role, stream_claim, where)
    payload = _closed(record["payload"], {"sample_rate", "spans"},
                      {"part", "weight_scale", "document_url", "alignment"}, f"{where} payload")
    if _int(payload["sample_rate"], f"{where} sample_rate", 1) != rate:
        raise HarmonyError(f"{where} sample_rate differs from the stream's")
    if "part" in payload:
        _text(payload["part"], f"{where} part", 100)
    weighted = "weight_scale" in payload
    if weighted:
        _text(payload["weight_scale"], f"{where} weight_scale", MAX_TEXT)
    has_document = _check_source_fields(payload, by_role, where)
    spans = _list(payload["spans"], f"{where} spans", 1, MAX_SPANS)
    previous_end = 0
    for index, span in enumerate(spans):
        at = f"{where} span {index}"
        _closed(span, {"start_sample", "end_sample", "readings"}, {"locator"}, at)
        _, previous_end = _check_span_bounds(span["start_sample"], span["end_sample"], previous_end, count, at)
        if "locator" in span:
            _check_locator(span["locator"], has_document, {"bar", "beat"}, at)
        readings = _list(span["readings"], f"{at} readings", 1, MAX_READINGS)
        for number, reading in enumerate(readings):
            check_reading(reading, weighted, f"{at} reading {number}")
        if len({_canonical(reading) for reading in readings}) != len(readings):
            raise HarmonyError(f"{at} repeats a reading")
    return spans


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
# never a guess; the Harte name always exists.
SYMBOL_QUALITIES: dict[frozenset[int], str] = {
    frozenset(intervals): suffix for suffix, intervals in (
        ("", {0, 4, 7}), ("m", {0, 3, 7}), ("dim", {0, 3, 6}), ("+", {0, 4, 8}), ("5", {0, 7}),
        ("sus2", {0, 2, 7}), ("sus4", {0, 5, 7}), ("6", {0, 4, 7, 9}), ("m6", {0, 3, 7, 9}),
        ("6/9", {0, 2, 4, 7, 9}), ("m6/9", {0, 2, 3, 7, 9}), ("add9", {0, 2, 4, 7}), ("m(add9)", {0, 2, 3, 7}),
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


def _added_degree(interval: int, chord: frozenset[int]) -> str:
    """The Harte degree for an interval added to a shorthand, written as an
    extension where the chord has the tone it extends."""
    sevenths = bool(chord & {10, 11})
    return {
        1: "b9", 2: "9",
        3: "#9" if 4 in chord else "b3",
        5: "11" if sevenths else "4",
        6: "#11" if 7 in chord else "b5",
        8: "b13" if 7 in chord else ("#5" if 4 in chord else "b6"),
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
        key = (cost, -len(chord), name)
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
    cost, _, name = best
    chord = HARTE_SHORTHANDS[name]
    marks = ["*" + SIMPLE_DEGREES[i] for i in sorted(chord - sounding)]
    marks += [_added_degree(i, chord) for i in sorted(sounding - chord)]
    extra = f"({','.join(marks)})" if marks else ""
    return f"{root_name}:{name}{extra}{bass_part}", cost


def _symbol(root: int, sounding: frozenset[int], bass: int) -> str | None:
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
        _closed(layer, {"bars"}, {"label"}, at)
        if "label" in layer:
            _text(layer["label"], f"{at} label", 100)
        previous_end = 0
        for index, bar in enumerate(_list(layer["bars"], f"{at} bars", 1, MAX_BARS)):
            previous_end = _check_bar(bar, previous_end, count, has_document, f"{at} bar {index}")
    seen = []
    for index, link in enumerate(_list(payload.get("modulations", []), f"{where} modulations", 0, MAX_BARS)):
        at = f"{where} modulation {index}"
        _closed(link, {"layer", "bar", "before", "after", "ratio"}, set(), at)
        layer = _int(link["layer"], f"{at} layer", 0, len(layers) - 1)
        bars = layers[layer]["bars"]
        bar = _int(link["bar"], f"{at} bar", 1, len(bars) - 1)
        if bars[bar - 1]["end_sample"] != bars[bar]["start_sample"]:
            raise HarmonyError(f"{at} joins bars that are not adjacent")
        ratio = _list(link["ratio"], f"{at} ratio", 2, 2)
        p, q = (_int(r, f"{at} ratio", 1) for r in ratio)
        stated = Fraction(p, q)
        if (stated.numerator, stated.denominator) != (p, q):
            raise HarmonyError(f"{at} ratio must be in lowest terms")
        if note_value(link["before"], f"{at} before") / note_value(link["after"], f"{at} after") != stated:
            raise HarmonyError(f"{at} ratio is not the ratio of its note values")
        seen.append((layer, bar))
    if seen != sorted(set(seen)):
        raise HarmonyError(f"{where} modulations must be distinct and in order of layer and bar")
    return layers


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
                lossy: list[str]) -> dict[str, Any]:
    provenance = provenance or {}
    return {
        "namespace": namespace,
        "annotation_metadata": {
            "curator": {"name": "", "email": ""},
            "annotator": {"agent": provenance.get("agent", "")},
            "version": "", "corpus": "", "annotation_tools": "",
            "annotation_rules": "ADR 0077 export view",
            "validation": "",
            "data_source": provenance.get("revision_id", ""),
        },
        "data": data,
        "sandbox": {"archive_lossy": sorted(set(lossy))},
        "time": 0,
        "duration": None,
    }


def jams_chords(payload: dict[str, Any], *, rank: int = 0, namespace: str = "chord_harte",
                provenance: dict[str, str] | None = None) -> dict[str, Any]:
    """One JAMS annotation of a checked chord stacks payload: each span's
    reading at `rank` (0 = the asserter's first), as a strict Harte string.

    Lost on the way, and listed in the annotation's sandbox: the other
    readings, uncertain tones, voicings, the source's own text, locators, and
    weights when the claim's scale is not a probability."""
    if namespace not in ("chord_harte", "chord"):
        raise HarmonyError("JAMS chord namespace must be chord_harte or chord")
    rate = payload["sample_rate"]
    data, lossy = [], []
    probability = payload.get("weight_scale") == "probability"
    if "weight_scale" in payload and not probability:
        lossy.append(f"weights on the scale {payload['weight_scale']!r}")
    for span in payload["spans"]:
        if "locator" in span:
            lossy.append("source locators")
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
        data.append({
            "time": span["start_sample"] / rate,
            "duration": (span["end_sample"] - span["start_sample"]) / rate,
            "value": value,
            "confidence": float(reading["weight"]) if probability else None,
        })
    return _annotation(namespace, data, provenance, lossy)


def jams_meter(payload: dict[str, Any], *, layer: int = 0,
               provenance: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Two JAMS annotations of one layer of a checked meter layers payload:
    `beat_position` and `tempo` (quarter notes per minute, per bar).

    JAMS has no namespace for a time signature's grouping, a metric
    modulation, or simultaneous layers. Grouping survives only as the
    positions of the counted beats; a bar without beat times exports its
    downbeat alone."""
    rate = payload["sample_rate"]
    chosen = payload["layers"][layer]
    lossy = []
    if len(payload["layers"]) > 1:
        lossy.append("other simultaneous layers")
    if payload.get("modulations"):
        lossy.append("metric modulations")
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


def jams_document(duration_seconds: float, annotations: list[dict[str, Any]]) -> dict[str, Any]:
    """A JAMS document holding `annotations`; it names no title or artist."""
    return {
        "file_metadata": {"title": "", "artist": "", "release": "", "duration": duration_seconds,
                          "identifiers": {}, "jams_version": "0.3.4"},
        "annotations": annotations,
        "sandbox": {},
    }
