"""Key regions, key sequences, and tuning (ADR 0080): payload rules, the
spelling a key gives pitch classes, the tuning grid in force at a sample, and
the lossy JAMS `key_mode` view.

The three contracts, `archive.claim/key_regions`, `archive.claim/key_sequence`,
and `archive.claim/tuning`, each version 1, are proposed by ADR 0080 for
specification 0.0.10. No release installs them yet, so Door cannot admit
them; this module holds their rules as World would re-check them, so an
importer or extractor can check a proposal first. It uses only the standard
library and the shared helpers of `world/harmony.py` (ADR 0077), whose
reference and span rules these contracts repeat.

Pitch classes are integers, C = 0, in twelve-tone equal temperament, as in
ADR 0077. A tuning claim says where that grid lies on a recording; a pitch
class is the nearest step of the grid in force at its samples. Every name and
export here is a computed view: regenerable, never stored as the claim, and
never a judgment between claims (INV-7, INV-8).
"""

from __future__ import annotations

import re
from fractions import Fraction
from typing import Any

from world.harmony import (
    CHORD_SEQUENCE,
    DECIMAL,
    MAX_LABEL,
    MAX_READINGS,
    MAX_RULE,
    MAX_TEXT,
    PITCH_NAMES,
    SIMPLE_DEGREES,
    HarmonyError,
    Name,
    _annotation,
    _ascending_pitch_classes,
    _canonical,
    _check_locator,
    _check_source_fields,
    _check_span_bounds,
    _check_stream,
    _closed,
    _int,
    _list,
    _refs_by_role,
    _text,
    check_chord_sequence_v2,
    check_derived_from,
    check_lineage,
)

KEY_REGIONS = ("archive.claim/key_regions", "1")
KEY_SEQUENCE = ("archive.claim/key_sequence", "1")
TUNING = ("archive.claim/tuning", "1")

MAX_REGIONS = 1000
MAX_DRIFT = 10000
MAX_MODE = 100
SPAN_ENDS = ("asserted", "held")

# The modes whose name fixes the collection on the tonic. A mode named
# otherwise ("blues", a raga's name) is the asserter's text, unchecked.
MODES: dict[str, tuple[frozenset[int], ...]] = {
    "ionian": (frozenset({0, 2, 4, 5, 7, 9, 11}),),
    "dorian": (frozenset({0, 2, 3, 5, 7, 9, 10}),),
    "phrygian": (frozenset({0, 1, 3, 5, 7, 8, 10}),),
    "lydian": (frozenset({0, 2, 4, 6, 7, 9, 11}),),
    "mixolydian": (frozenset({0, 2, 4, 5, 7, 9, 10}),),
    "aeolian": (frozenset({0, 2, 3, 5, 7, 8, 10}),),
    "locrian": (frozenset({0, 1, 3, 5, 6, 8, 10}),),
    "harmonic minor": (frozenset({0, 2, 3, 5, 7, 8, 11}),),
    "melodic minor": (frozenset({0, 2, 3, 5, 7, 9, 11}),),
}
MODES["major"] = MODES["ionian"]
# "Minor" as people say it: the natural, harmonic, or melodic collection.
MODES["minor"] = MODES["aeolian"] + MODES["harmonic minor"] + MODES["melodic minor"]

LETTERS = "CDEFGAB"
LETTER_CLASS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
ACCIDENTALS = {"bb": -2, "b": -1, "": 0, "#": 1, "##": 2}
PITCH_NAME = re.compile(r"([A-G])(bb|b|##|#)?\Z")

KEY_REGION_ROLES = {
    "source": (1, 1),        # the audio whose timeline the regions are in
    "stream": (1, 1),        # audio_stream v1 about `source`
    "run": (0, 1),           # derivation_run v1, when a machine asserted the claim
    "document": (0, 1),      # an outside source's admitted bytes
    "tuning": (0, 1),        # the tuning v1 claim whose grid the pitch classes are on
    "sequence": (0, 1),      # the key_sequence v1 claim an alignment placed on this recording
    "corrects": (0, 8),      # key_regions v1 claims this one corrects
    "derived_from": (0, 8),  # claims the keys were read from (ADR 0079's role)
}

KEY_SEQUENCE_ROLES = {
    "work": (0, 1),            # the Work (an Entity of kind musical_work) the keys describe
    "document": (0, 1),        # the document the keys were read from
    "run": (0, 1),             # derivation_run v1, when a tool read the document
    "chord_sequence": (0, 1),  # chord_sequence v1 or v2 whose entries the regions range over
    "form": (0, 1),            # the form claim (ADR 0083) whose sections the regions range over
    "corrects": (0, 8),        # key_sequence v1 claims this one corrects
    "derived_from": (0, 8),    # claims the keys were read from (ADR 0079's role)
}

TUNING_ROLES = {
    "source": (1, 1),     # the audio whose grid this is
    "stream": (1, 1),     # audio_stream v1 about `source`
    "run": (0, 1),        # derivation_run v1, when a machine measured it
    "document": (0, 1),   # an outside source stating it ("tuned to A = 432 Hz")
    "corrects": (0, 8),   # tuning v1 claims this one corrects
    "derived_from": (0, 8),  # claims the tuning was read from (ADR 0079's role)
}


class TonalityError(HarmonyError):
    """A key regions, key sequence, or tuning claim that breaks a rule of ADR 0080."""


def _wrapped(check):
    def run(*args, **kwargs):
        try:
            return check(*args, **kwargs)
        except TonalityError:
            raise
        except HarmonyError as error:
            raise TonalityError(str(error)) from error
    run.__name__, run.__doc__ = check.__name__, check.__doc__
    return run


# ---------------------------------------------------------------------------
# Readings


def pitch_name_class(name: str) -> int:
    """The pitch class a spelled name sounds: Eb and D# are both 3."""
    match = PITCH_NAME.fullmatch(name) if isinstance(name, str) else None
    if match is None:
        raise TonalityError(f"{name!r} is not a pitch name (a letter A-G and at most two sharps or flats)")
    return (LETTER_CLASS[match.group(1)] + ACCIDENTALS[match.group(2) or ""]) % 12


@_wrapped
def check_key_reading(reading: Any, weighted: bool, where: str = "key reading") -> str:
    """Check one key reading; return its form: `key`, `no_key`, or `text`.

    A key is a `collection` of pitch classes, an optional `tonic` among them,
    an optional `mode` (which needs a tonic), and an optional `spelling`, the
    asserter's name for each class of the collection in the same order. A
    mode named in MODES must name the collection on that tonic."""
    if not isinstance(reading, dict):
        raise TonalityError(f"{where} must be an object")
    if weighted != ("weight" in reading):
        raise TonalityError(f"{where} has a weight exactly when the claim states a weight_scale")
    if "weight" in reading and (not isinstance(reading["weight"], str) or not DECIMAL.fullmatch(reading["weight"])):
        raise TonalityError(f"{where} weight must be exact decimal text")
    if "text" in reading:
        _text(reading["text"], f"{where} text", MAX_TEXT)
    common = {"text", "weight"}
    if "no_key" in reading:
        _closed(reading, {"no_key"}, common, where)
        if reading["no_key"] is not True:
            raise TonalityError(f"{where} no_key must be true")
        return "no_key"
    if "collection" not in reading:
        _closed(reading, {"text"}, {"weight"}, where)
        return "text"
    _closed(reading, {"collection"}, common | {"tonic", "mode", "spelling"}, where)
    collection = _ascending_pitch_classes(reading["collection"], f"{where} collection", 1)
    if "tonic" in reading:
        if _int(reading["tonic"], f"{where} tonic", 0, 11) not in collection:
            raise TonalityError(f"{where} tonic is not in its collection")
    if "mode" in reading:
        mode = _text(reading["mode"], f"{where} mode", MAX_MODE)
        if "tonic" not in reading:
            raise TonalityError(f"{where} names a mode without a tonic")
        if mode in MODES:
            intervals = frozenset((pc - reading["tonic"]) % 12 for pc in collection)
            if intervals not in MODES[mode]:
                raise TonalityError(f"{where} collection is not the {mode} collection on its tonic")
    if "spelling" in reading:
        names = _list(reading["spelling"], f"{where} spelling", len(collection), len(collection))
        for index, (name, pc) in enumerate(zip(names, collection)):
            if pitch_name_class(name) != pc:
                raise TonalityError(f"{where} spelling[{index}] {name!r} does not sound pitch class {pc}")
    return "key"


def _check_readings(value: Any, weighted: bool, where: str) -> list[dict[str, Any]]:
    readings = _list(value, f"{where} readings", 1, MAX_READINGS)
    for number, reading in enumerate(readings):
        check_key_reading(reading, weighted, f"{where} reading {number}")
    if len({_canonical(reading) for reading in readings}) != len(readings):
        raise TonalityError(f"{where} repeats a reading")
    return readings


def _check_scale(payload: dict[str, Any], where: str) -> bool:
    if "weight_scale" in payload:
        _text(payload["weight_scale"], f"{where} weight_scale", MAX_TEXT)
        return True
    return False


# ---------------------------------------------------------------------------
# Key sequences: keys over a Work, untimed


@_wrapped
def check_key_sequence(claim: dict[str, Any], chord_sequence_claim: dict[str, Any] | None = None,
                       section_count: int | None = None) -> list[dict[str, Any]]:
    """Check an `archive.claim/key_sequence` version 1 claim: keys over a
    Work or a document, in written order, with no times. Its subject is a
    `work`, a `document`, or both. Regions range over the entries of a pinned
    `chord_sequence` or the sections of a pinned `form` claim (ADR 0083) by
    `first` and `last` index; with neither pinned they are in order only.
    Pass the pinned chord sequence claim, or the form's number of sections,
    to bound the indices. Returns the regions; raises TonalityError."""
    where = "key sequence"
    record = _closed(claim, {"payload", "refs"}, {"revision_id", "agent"}, where)
    by_role = _refs_by_role(record["refs"], KEY_SEQUENCE_ROLES, where)
    if not by_role["work"] and not by_role["document"]:
        raise TonalityError(f"{where} needs a work or a document as its subject")
    if by_role["chord_sequence"] and by_role["form"]:
        raise TonalityError(f"{where} ranges over a chord sequence or a form, not both")
    anchored = bool(by_role["chord_sequence"] or by_role["form"])
    bound = None
    if by_role["chord_sequence"] and chord_sequence_claim is not None:
        if chord_sequence_claim.get("revision_id", by_role["chord_sequence"][0]["revision_id"]) \
                != by_role["chord_sequence"][0]["revision_id"]:
            raise TonalityError(f"{where} was given a chord sequence other than the one it pins")
        bound = len(check_chord_sequence_v2(chord_sequence_claim)) - 1
    if by_role["form"] and section_count is not None:
        bound = _int(section_count, f"{where} form section count", 1) - 1
    payload = _closed(record["payload"], {"regions"}, {"part", "weight_scale", "document_url"}, f"{where} payload")
    check_derived_from(by_role, where)
    if "part" in payload:
        _text(payload["part"], f"{where} part", MAX_LABEL)
    weighted = _check_scale(payload, where)
    has_document = _check_source_fields(payload, by_role, where, timed=False)
    regions = _list(payload["regions"], f"{where} regions", 1, MAX_REGIONS)
    previous_last, previous_line = -1, 0
    for index, region in enumerate(regions):
        at = f"{where} region {index}"
        _closed(region, {"readings"} | ({"first", "last"} if anchored else set()), {"section", "locator"}, at)
        if anchored:
            first = _int(region["first"], f"{at} first", 0, bound)
            _int(region["last"], f"{at} last", first, bound)
            if first <= previous_last:
                raise TonalityError(f"{at} starts at or before the end of the region before it")
            previous_last = region["last"]
        if "section" in region:
            _text(region["section"], f"{at} section", MAX_LABEL)
        if "locator" in region:
            if not has_document:
                raise TonalityError(f"{at} has a locator but the claim pins no document")
            locator = _closed(region["locator"], {"line"}, set(), f"{at} locator")
            line = _int(locator["line"], f"{at} locator line", 1)
            if line < previous_line:
                raise TonalityError(f"{at} is located before the region before it")
            previous_line = line
        _check_readings(region["readings"], weighted, at)
    return regions


# ---------------------------------------------------------------------------
# Key regions: keys over a recording's samples


@_wrapped
def check_key_regions(claim: dict[str, Any], stream_claim: dict[str, Any],
                      sequence_claim: dict[str, Any] | None = None,
                      tuning_claim: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Check an `archive.claim/key_regions` version 1 claim, one asserter's
    keys over sample spans of one audio stream, with the `audio_stream`
    claim its `stream` pins and, optionally, the key sequence and tuning
    claims it pins. Returns the regions; raises TonalityError. As with chord
    stacks, a gap between regions asserts nothing."""
    where = "key regions"
    record = _closed(claim, {"payload", "refs"}, {"revision_id", "agent"}, where)
    by_role = _refs_by_role(record["refs"], KEY_REGION_ROLES, where)
    rate, count = _check_stream(by_role, stream_claim, where)
    if by_role["tuning"] and tuning_claim is not None:
        if tuning_claim.get("revision_id", by_role["tuning"][0]["revision_id"]) != by_role["tuning"][0]["revision_id"]:
            raise TonalityError(f"{where} was given a tuning claim other than the one it pins")
        tuning_source = [ref for ref in tuning_claim.get("refs", []) if isinstance(ref, dict) and ref.get("role") == "source"]
        source = by_role["source"][0]
        if len(tuning_source) != 1 or (tuning_source[0].get("id"), tuning_source[0].get("revision_id")) \
                != (source["id"], source["revision_id"]):
            raise TonalityError(f"{where} pins a tuning of another Artifact revision")
    payload = _closed(record["payload"], {"sample_rate", "regions"},
                      {"part", "weight_scale", "document_url", "alignment", "hold_rule", "stem"}, f"{where} payload")
    if _int(payload["sample_rate"], f"{where} sample_rate", 1) != rate:
        raise TonalityError(f"{where} sample_rate differs from the stream's")
    check_lineage(payload, by_role, where)
    if "part" in payload:
        _text(payload["part"], f"{where} part", MAX_LABEL)
    weighted = _check_scale(payload, where)
    has_document = _check_source_fields(payload, by_role, where)
    placed = None
    if by_role["sequence"] and sequence_claim is not None:
        if sequence_claim.get("revision_id", by_role["sequence"][0]["revision_id"]) \
                != by_role["sequence"][0]["revision_id"]:
            raise TonalityError(f"{where} was given a key sequence other than the one it pins")
        placed = check_key_sequence(sequence_claim)
    regions = _list(payload["regions"], f"{where} regions", 1, MAX_REGIONS)
    previous_end, held = 0, False
    for index, region in enumerate(regions):
        at = f"{where} region {index}"
        _closed(region, {"start_sample", "end_sample", "readings"}, {"end", "locator", "region"}, at)
        if "region" in region:
            if not by_role["sequence"]:
                raise TonalityError(f"{at} names a sequence region but the claim pins no key sequence")
            _int(region["region"], f"{at} region", 0, None if placed is None else len(placed) - 1)
        _, previous_end = _check_span_bounds(region["start_sample"], region["end_sample"], previous_end, count, at)
        if region.get("end", "asserted") not in SPAN_ENDS:
            raise TonalityError(f"{at} end must be one of {SPAN_ENDS}")
        held = held or region.get("end") == "held"
        if "locator" in region:
            _check_locator(region["locator"], has_document, {"bar"}, at)
        _check_readings(region["readings"], weighted, at)
    if held != ("hold_rule" in payload):
        raise TonalityError(f"{where} states a hold_rule exactly when a region's end is held")
    if held:
        _text(payload["hold_rule"], f"{where} hold_rule", MAX_RULE)
    return regions


def region_at(payload: dict[str, Any], sample: int) -> dict[str, Any] | None:
    """The region of a checked key regions payload sounding at `sample`, or
    None in a gap, where the claim asserts nothing."""
    for region in payload["regions"]:
        if region["start_sample"] <= sample < region["end_sample"]:
            return region
    return None


# ---------------------------------------------------------------------------
# Tuning


def _cents(value: Any, where: str) -> Fraction:
    """A grid offset: exact decimal text, in cents from A = 440 Hz, folded to
    [-50, 50) since a twelve-tone grid repeats every 100 cents."""
    if not isinstance(value, str) or not DECIMAL.fullmatch(value):
        raise TonalityError(f"{where} must be exact decimal text")
    cents = Fraction(value)
    if not -50 <= cents < 50:
        raise TonalityError(f"{where} must be at least -50 and less than 50 cents")
    return cents


@_wrapped
def check_tuning(claim: dict[str, Any], stream_claim: dict[str, Any]) -> dict[str, Any]:
    """Check an `archive.claim/tuning` version 1 claim: where the
    twelve-tone grid lies on one audio stream, as `reference_cents` from
    A = 440 Hz, with optional `drift` spans whose own offset holds there.
    Returns the payload; raises TonalityError."""
    where = "tuning"
    record = _closed(claim, {"payload", "refs"}, {"revision_id", "agent"}, where)
    by_role = _refs_by_role(record["refs"], TUNING_ROLES, where)
    rate, count = _check_stream(by_role, stream_claim, where)
    payload = _closed(record["payload"], {"sample_rate", "reference_cents"},
                      {"confidence_scale", "confidence", "drift", "document_url", "stem"}, f"{where} payload")
    if _int(payload["sample_rate"], f"{where} sample_rate", 1) != rate:
        raise TonalityError(f"{where} sample_rate differs from the stream's")
    check_lineage(payload, by_role, where)
    _cents(payload["reference_cents"], f"{where} reference_cents")
    _check_source_fields(payload, by_role, where, timed=False)
    scaled = "confidence_scale" in payload
    if scaled:
        _text(payload["confidence_scale"], f"{where} confidence_scale", MAX_TEXT)
    if scaled != ("confidence" in payload):
        raise TonalityError(f"{where} states a confidence exactly when it states a confidence_scale")
    if scaled and (not isinstance(payload["confidence"], str) or not DECIMAL.fullmatch(payload["confidence"])):
        raise TonalityError(f"{where} confidence must be exact decimal text")
    previous_end = 0
    drift = _list(payload["drift"], f"{where} drift", 1, MAX_DRIFT) if "drift" in payload else []
    for index, span in enumerate(drift):
        at = f"{where} drift {index}"
        _closed(span, {"start_sample", "end_sample", "cents"}, {"confidence"}, at)
        _, previous_end = _check_span_bounds(span["start_sample"], span["end_sample"], previous_end, count, at)
        _cents(span["cents"], f"{at} cents")
        if scaled != ("confidence" in span):
            raise TonalityError(f"{at} has a confidence exactly when the claim states a confidence_scale")
        if scaled and (not isinstance(span["confidence"], str) or not DECIMAL.fullmatch(span["confidence"])):
            raise TonalityError(f"{at} confidence must be exact decimal text")
    return payload


def grid_cents_at(payload: dict[str, Any], sample: int) -> Fraction:
    """The grid's offset from A = 440 Hz in force at `sample`: a drift span's
    own where one covers it, otherwise the reference. A pitch class at that
    sample names the nearest step of this grid."""
    for span in payload.get("drift", []):
        if span["start_sample"] <= sample < span["end_sample"]:
            return Fraction(span["cents"])
    return Fraction(payload["reference_cents"])


def reference_hz(cents: Fraction | str) -> float:
    """The frequency of A4 on a grid offset `cents` from A = 440 Hz: a view."""
    return 440.0 * 2 ** (float(Fraction(cents)) / 1200)


# ---------------------------------------------------------------------------
# Spelling: a view that follows the key


def _name(letter: int, pc: int) -> str | None:
    """The name of `pc` on letter LETTERS[letter], or None past a double accidental."""
    accidental = (pc - LETTER_CLASS[LETTERS[letter]] + 6) % 12 - 6
    marks = {value: mark for mark, value in ACCIDENTALS.items()}
    return LETTERS[letter] + marks[accidental] if accidental in marks else None


def _accidentals(name: str) -> int:
    return abs(ACCIDENTALS[name[1:]])


def _heptatonic(collection: list[int], first: int) -> dict[int, str] | None:
    """One letter per class of a seven-class collection, ascending from
    `first`, choosing the fewest accidentals, then the fewest on `first`,
    then flats over sharps (Gb major over F# major)."""
    order = collection[collection.index(first):] + collection[:collection.index(first)]
    best = None
    for letter in range(7):
        names = [_name((letter + step) % 7, pc) for step, pc in enumerate(order)]
        if None in names:
            continue
        sharps = sum(ACCIDENTALS[n[1:]] > 0 for n in names)
        rank = (sum(map(_accidentals, names)), _accidentals(names[0]), sharps)
        if best is None or rank < best[0]:
            best = (rank, dict(zip(order, names)))
    return None if best is None else best[1]


def key_pitch_names(reading: dict[str, Any] | None) -> tuple[str, ...]:
    """The name of each pitch class 0 to 11 under a checked key reading: the
    view rule by which spelling follows the asserted key.

    1. The reading's own `spelling` names its collection's classes.
    2. Otherwise a collection of seven classes takes one letter per class,
       ascending from the tonic (or from its lowest class), with the fewest
       accidentals, then the fewest on the tonic, then flats over sharps.
    3. A class outside the collection is a sharpened collection class below
       it or a flattened one above it, whichever has fewer accidentals; at a
       tie, flats where the collection's names use flats, else sharps.
    4. Anything still unnamed, and every class when there is no key reading
       (or it is `no_key` or text only), takes the default PITCH_NAMES."""
    if reading is None or "collection" not in reading:
        return PITCH_NAMES
    collection = list(reading["collection"])
    members: dict[int, str] | None = None
    if "spelling" in reading:
        members = dict(zip(collection, reading["spelling"]))
    elif len(collection) == 7:
        members = _heptatonic(collection, reading.get("tonic", collection[0]))
    if members is None:
        return PITCH_NAMES
    flats = any(ACCIDENTALS[n[1:]] < 0 for n in members.values())
    names = []
    for pc in range(12):
        if pc in members:
            names.append(members[pc])
            continue
        candidates = []
        for neighbour, shift in (((pc - 1) % 12, 1), ((pc + 1) % 12, -1)):
            if neighbour in members:
                letter = LETTERS.index(members[neighbour][0])
                name = _name(letter, pc)
                if name is not None:
                    candidates.append(name)
        if not candidates:
            names.append(PITCH_NAMES[pc])
            continue
        candidates.sort(key=lambda n: (_accidentals(n), (ACCIDENTALS[n[1:]] > 0) if flats
                                       else (ACCIDENTALS[n[1:]] < 0)))
        names.append(candidates[0])
    return tuple(names)


def _degree_letters(interval: int, intervals: frozenset[int]) -> int:
    """How many letters above the root a chord tone is written: its degree
    less one. A minor third beside a major third is a raised ninth, a flat
    fifth beside a perfect fifth a raised eleventh, and a raised fifth over a
    major third with no fifth is a fifth, as in ADR 0077's names."""
    if interval == 3 and 4 in intervals:
        return 1
    if interval == 6 and 7 in intervals:
        return 3
    if interval == 8 and 4 in intervals and 7 not in intervals:
        return 4
    return int(SIMPLE_DEGREES[interval].lstrip("b#")) - 1


def spell_tone(root_name: str, interval: int, intervals: frozenset[int] = frozenset()) -> str:
    """The name of the tone `interval` semitones above a spelled root, by its
    degree: the major seventh of G is F#, never Gb."""
    root = pitch_name_class(root_name)
    letter = (LETTERS.index(root_name[0]) + _degree_letters(interval % 12, intervals)) % 7
    return _name(letter, (root + interval) % 12) or PITCH_NAMES[(root + interval) % 12]


def spell_stack(bass: int, tones: list[int] | frozenset[int], root: int, key: dict[str, Any] | None) -> dict[str, Any]:
    """A stack's root named by the key, and its bass and tones named by their
    degree above that root: Gm(maj13)/D in G minor has F#, not Gb."""
    root_name = key_pitch_names(key)[root]
    intervals = frozenset((pc - root) % 12 for pc in set(tones) | {bass})
    return {"root": root_name,
            "bass": spell_tone(root_name, (bass - root) % 12, intervals),
            "tones": [spell_tone(root_name, (pc - root) % 12, intervals) for pc in tones]}


def spell_name(name: Name, bass: int, key: dict[str, Any] | None) -> Name:
    """A computed name (world.harmony.stack_names) respelled under a key
    reading: its root as the key names it, its bass by its degree above the
    root. Without a key the name is unchanged. Still a view, never a claim."""
    old_root = PITCH_NAMES[name.root]
    root_name = key_pitch_names(key)[name.root]
    harte = root_name + name.harte[len(old_root):]
    symbol = name.symbol
    if symbol is not None:
        body = symbol[len(old_root):]
        if bass != name.root:
            body = body[: -len("/" + PITCH_NAMES[bass])] + "/" + spell_tone(root_name, (bass - name.root) % 12)
        symbol = root_name + body
    return Name(name.root, name.asserted_root, harte, symbol, name.distance)


# ---------------------------------------------------------------------------
# JAMS view (lossy)

JAMS_MODES = ("major", "minor", "ionian", "dorian", "phrygian", "lydian", "mixolydian", "aeolian", "locrian")
JAMS_TONIC = re.compile(r"[A-G][b#]?\Z")


def jams_keys(payload: dict[str, Any], *, rank: int = 0,
              provenance: dict[str, str] | None = None) -> dict[str, Any]:
    """One JAMS `key_mode` annotation of a checked key regions payload: each
    region's reading at `rank` (0 = the asserter's first). JAMS has a tonic
    and one of nine mode names, so a collection without a tonic, a mode
    outside them, a spelling with a double accidental, alternative readings,
    a held end, and weights on any scale but a probability are dropped and
    listed in the sandbox."""
    rate = payload["sample_rate"]
    lossy = ["alternative readings", "the pitch collection"]
    data, dropped = [], []
    probability = payload.get("weight_scale") == "probability"
    if "weight_scale" in payload and not probability:
        lossy.append(f"weights on the scale {payload['weight_scale']!r}")
    for index, region in enumerate(payload["regions"]):
        if region.get("end") == "held":
            lossy.append("held ends")
        if rank >= len(region["readings"]):
            dropped.append(index)
            continue
        reading = region["readings"][rank]
        if "no_key" in reading:
            value = "N"
        elif "tonic" in reading:
            tonic = key_pitch_names(reading)[reading["tonic"]]
            if not JAMS_TONIC.fullmatch(tonic):
                lossy.append("a double accidental in a tonic's name")
                tonic = PITCH_NAMES[reading["tonic"]]
            mode = reading.get("mode")
            if mode in ("harmonic minor", "melodic minor"):
                lossy.append("harmonic and melodic minor, exported as minor")
                mode = "minor"
            elif mode is not None and mode not in JAMS_MODES:
                lossy.append("mode names outside JAMS's nine")
                mode = None
            value = tonic + (f":{mode}" if mode else "")
        else:
            dropped.append(index)
            lossy.append("a reading with no tonic (a collection only, or text only)")
            continue
        confidence = float(Fraction(reading["weight"])) if probability else None
        data.append({"time": region["start_sample"] / rate,
                     "duration": (region["end_sample"] - region["start_sample"]) / rate,
                     "value": value, "confidence": confidence})
    annotation = _annotation("key_mode", data, provenance, lossy, sandbox={"archive_dropped_regions": dropped})
    annotation["annotation_metadata"]["annotation_rules"] = "ADR 0080 export view" + (
        f"; {payload['hold_rule']}" if "hold_rule" in payload else "")
    return annotation
