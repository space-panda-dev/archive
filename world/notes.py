"""Note events per part, and melody at two levels (ADR 0086): payload rules
and the views that read them.

Three contracts, each version 1, decided by ADR 0086 and not yet installed by
any specification release:

- `archive.claim/notes`: one asserter's notes of one part of one recording,
  at integer samples of that recording's stream;
- `archive.claim/melody`: a Work's tune, untimed, as note values in written
  order;
- `archive.claim/melody_spans`: which part's notes carry a recording's tune,
  span by span, citing those notes claims.

This module holds their rules as World will re-check them when it projects
such a claim, and the views (the tune's notes, its hand-offs, a Work-level
melody laid on its bars). It uses only the standard library, so an importer
or extractor can check a proposal with it before Door does; Door remains the
authority (INV-2). Every view is computed and never stored (INV-7).

A stem cited by bank key and the `derived_from` role are ADR 0079's, checked
by its one check in `world/harmony.py` (`check_stem_citation`,
`check_derived_from`); the tuning a note's cents are relative to is ADR
0080's `archive.claim/tuning` version 1.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from world import harmony

NOTES = ("archive.claim/notes", "1")
MELODY = ("archive.claim/melody", "1")
MELODY_SPANS = ("archive.claim/melody_spans", "1")
TUNING = ("archive.claim/tuning", "1")                # ADR 0080
UNTIMED_METER = ("archive.claim/meter_layers", "2")   # ADR 0083, untimed form

OBJECT_ID = re.compile(r"urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
REVISION_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
DECIMAL = re.compile(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?\Z")
URL = re.compile(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+\Z")
DENOMINATORS = (1, 2, 4, 8, 16, 32, 64)

MAX_NOTES = 100000          # per notes claim; a longer part is split by range
MAX_MELODY_NOTES = 10000
MAX_SPANS = 10000
MAX_PARTS = 16              # notes claims one melody spans claim may cite
MAX_UNIT = 1024             # the finest note value, as 1/unit of a whole note
MAX_STRINGS = 12
MAX_CAPO = 24
MAX_TEXT = 200
MAX_LABEL = 100
MAX_URL = 2000
SELECTIONS = ("all", "highest", "lowest")
# Written, never measured (ADR 0086, amended 2026-10-19): MusicXML's names,
# but `marcato`, which MusicXML calls `strong-accent`.
ARTICULATIONS = ("staccato", "staccatissimo", "tenuto", "accent", "marcato", "fermata")


class NotesError(ValueError):
    """A notes, melody, or melody spans claim that breaks a rule of ADR 0086."""


# ---------------------------------------------------------------------------
# Shared pieces


def _closed(value: Any, required: set[str], optional: set[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise NotesError(f"{where} must be an object")
    missing = required - set(value)
    extra = set(value) - required - optional
    if missing or extra:
        raise NotesError(f"{where} has missing {sorted(missing)} or unknown {sorted(extra)} fields")
    return value


def _int(value: Any, where: str, low: int, high: int | None = None) -> int:
    # bool is an int in Python; a JSON true is not a number.
    if not isinstance(value, int) or isinstance(value, bool) or value < low or (high is not None and value > high):
        bound = f"{low}.." + ("" if high is None else str(high))
        raise NotesError(f"{where} must be an integer in {bound}")
    return value


def _text(value: Any, where: str, maximum: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise NotesError(f"{where} must be text of 1 to {maximum} characters")
    return value


def _list(value: Any, where: str, low: int, high: int) -> list[Any]:
    if not isinstance(value, list) or not low <= len(value) <= high:
        raise NotesError(f"{where} must be a list of {low} to {high} items")
    return value


def _decimal(value: Any, where: str) -> Fraction:
    if not isinstance(value, str) or not DECIMAL.fullmatch(value):
        raise NotesError(f"{where} must be exact decimal text")
    return Fraction(value)


def _cents(value: Any, where: str) -> Fraction:
    """Cents from the nearest step of the grid: at least -50, less than 50,
    so every pitch has one MIDI number and one offset (ADR 0080's range)."""
    cents = _decimal(value, where)
    if not -50 <= cents < 50:
        raise NotesError(f"{where} must be at least -50 and less than 50")
    return cents


def note_length(value: Any, where: str, zero: bool = False) -> Fraction:
    """A written length in whole notes, `count` notes of value 1/`unit`: a
    dotted quarter is 3/8, a triplet eighth 1/12. A duration is positive; an
    offset from a downbeat (`zero`) may be 0."""
    length = _closed(value, {"count", "unit"}, set(), where)
    count = _int(length["count"], f"{where} count", 0 if zero else 1)
    unit = _int(length["unit"], f"{where} unit", 1, MAX_UNIT)
    return Fraction(count, unit)


def _refs_by_role(refs: Any, counts: dict[str, tuple[int, int]], where: str) -> dict[str, list[dict[str, Any]]]:
    """References grouped by role, with each role's (minimum, maximum) count."""
    if not isinstance(refs, list):
        raise NotesError(f"{where} refs must be a list")
    by_role: dict[str, list[dict[str, Any]]] = {role: [] for role in counts}
    seen = set()
    for index, ref in enumerate(refs):
        _closed(ref, {"role", "id", "revision_id"}, set(), f"{where} ref {index}")
        if ref["role"] not in counts:
            raise NotesError(f"{where} ref {index} has unknown role {ref['role']!r}")
        if not isinstance(ref["id"], str) or not OBJECT_ID.fullmatch(ref["id"]) \
                or not isinstance(ref["revision_id"], str) or not REVISION_ID.fullmatch(ref["revision_id"]):
            raise NotesError(f"{where} ref {index} must pin an object and an exact revision")
        if (ref["role"], ref["revision_id"]) in seen:
            raise NotesError(f"{where} ref {index} pins the same revision twice in one role")
        seen.add((ref["role"], ref["revision_id"]))
        by_role[ref["role"]].append(ref)
    for role, (low, high) in counts.items():
        if not low <= len(by_role[role]) <= high:
            raise NotesError(f"{where} needs {low} to {high} '{role}' references")
    return by_role


def _given(claim: Any, ref: dict[str, Any], where: str) -> dict[str, Any]:
    record = _closed(claim, {"payload", "refs"}, {"revision_id", "agent", "namespace", "version"}, where)
    if record.get("revision_id", ref["revision_id"]) != ref["revision_id"]:
        raise NotesError(f"{where} was given a claim other than the one it pins")
    return record


def _about_source(claim: dict[str, Any], source: dict[str, Any], role: str, where: str) -> None:
    """A pinned claim about audio pins the same Artifact revision, under `role`."""
    pinned = [r for r in claim["refs"] if isinstance(r, dict) and r.get("role") == role]
    if len(pinned) != 1 or (pinned[0].get("id"), pinned[0].get("revision_id")) != (source["id"], source["revision_id"]):
        raise NotesError(f"{where} is about another Artifact revision")


def _check_stream(by_role: dict[str, list[dict[str, Any]]], stream_claim: Any, where: str) -> tuple[int, int]:
    """The pinned audio stream's (sample_rate, sample_count), after checking
    that it is about the same Artifact revision as the `source` pin."""
    stream = _given(stream_claim, by_role["stream"][0], f"{where} stream claim")
    _about_source(stream, by_role["source"][0], "artifact", f"{where} stream")
    payload = stream["payload"] if isinstance(stream["payload"], dict) else {}
    rate = _int(payload.get("sample_rate"), f"{where} stream sample_rate", 1)
    count = _int(payload.get("sample_count"), f"{where} stream sample_count", 1)
    return rate, count


def _check_document_url(payload: dict[str, Any], has_document: bool, where: str) -> None:
    if "document_url" in payload:
        if not has_document:
            raise NotesError(f"{where} names a document_url without pinning the document")
        if not URL.fullmatch(_text(payload["document_url"], f"{where} document_url", MAX_URL)):
            raise NotesError(f"{where} document_url must be an http or https URL")


def _check_articulation(item: dict[str, Any], machine_only: bool, where: str) -> None:
    """A note's or pitch item's articulation: 1 to 6 distinct names, never on
    a claim that pins a run and no document (a machine measures no marks)."""
    if "articulation" not in item:
        return
    if machine_only:
        raise NotesError(f"{where} has articulation, but a claim with a run and no document writes none")
    marks = _list(item["articulation"], f"{where} articulation", 1, len(ARTICULATIONS))
    if any(not isinstance(m, str) or m not in ARTICULATIONS for m in marks) or len(set(marks)) != len(marks):
        raise NotesError(f"{where} articulation must be distinct names from {ARTICULATIONS}")


def check_stem_citation(value: Any, where: str = "stem") -> dict[str, Any]:
    """A stem cited by bank key: ADR 0079's one check, raising NotesError."""
    try:
        return harmony.check_stem_citation(value, where)
    except harmony.HarmonyError as error:
        raise NotesError(str(error)) from error


def check_strings_and_capo(fields: dict[str, Any], where: str) -> None:
    """The instrument fields of a part read from a document (decided here,
    and reused by ADR 0084's lead sheet parts): `strings`, 1 to 12 open
    strings as MIDI note numbers in the order the instrument's tablature
    lists them, lowest staff line first, so a re-entrant tuning keeps its
    order; and `capo`, the fret 1 to 24 a capo stops every string at, only
    with `strings`. They say how a part is fingered; a pitch is still what
    sounds."""
    if "strings" in fields:
        for index, string in enumerate(_list(fields["strings"], f"{where} strings", 1, MAX_STRINGS)):
            _int(string, f"{where} strings[{index}]", 0, 127)
    if "capo" in fields:
        if "strings" not in fields:
            raise NotesError(f"{where} gives a capo without the strings it stops")
        _int(fields["capo"], f"{where} capo", 1, MAX_CAPO)


def _check_derived_from(by_role: dict[str, list[dict[str, Any]]], where: str) -> None:
    """ADR 0079's `derived_from` rule: distinct revisions, none also corrected."""
    try:
        harmony.check_derived_from(by_role, where)
    except harmony.HarmonyError as error:
        raise NotesError(str(error)) from error


# ---------------------------------------------------------------------------
# Notes: one part of one recording


NOTES_ROLES = {
    "source": (1, 1),        # the recording whose timeline the samples count: the mix
    "stream": (1, 1),        # audio_stream v1 about `source`
    "run": (0, 1),           # derivation_run v1, when a machine asserted the notes
    "document": (0, 1),      # the document the part was read from (a transcription's track)
    "analysed": (0, 1),      # a stem a person chose to admit as an Artifact, analysed in place of the mix
    "tuning": (0, 1),        # ADR 0080's tuning claim about `source`; the grid cents are relative to
    "corrects": (0, 8),      # notes v1 claims this one corrects; they stay, untouched
    "derived_from": (0, 8),  # ADR 0079: claims this one was read from, with or without a run
    "contribution": (0, 1),  # ADR 0078: whose part this is
}


def check_notes(claim: dict[str, Any], stream_claim: dict[str, Any],
                tuning_claim: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Check an `archive.claim/notes` version 1 claim against every rule of
    ADR 0086, with the `audio_stream` claim its `stream` reference pins and,
    optionally, the tuning claim its `tuning` reference pins. Returns the
    notes; raises NotesError.

    The claim is one part's notes, from `range`'s start (inclusive) to its
    end (exclusive), or over the whole stream without one: inside it, a time
    with no note is the part asserted silent, and outside it the claim says
    nothing. A long part is split into claims by range."""
    where = "notes"
    record = _closed(claim, {"payload", "refs"}, {"revision_id", "agent"}, where)
    by_role = _refs_by_role(record["refs"], NOTES_ROLES, where)
    _check_derived_from(by_role, where)
    rate, count = _check_stream(by_role, stream_claim, where)
    if tuning_claim is not None and by_role["tuning"]:
        _about_source(_given(tuning_claim, by_role["tuning"][0], f"{where} tuning claim"),
                      by_role["source"][0], "source", f"{where} tuning")
    payload = _closed(record["payload"], {"sample_rate", "part", "notes"},
                      {"stem", "range", "unpitched", "confidence_scale", "strings", "capo",
                       "document_url", "alignment", "bass"}, f"{where} payload")
    if _int(payload["sample_rate"], f"{where} sample_rate", 1) != rate:
        raise NotesError(f"{where} sample_rate differs from the stream's")
    _text(payload["part"], f"{where} part", MAX_LABEL)
    if "stem" in payload:
        if by_role["analysed"]:
            raise NotesError(f"{where} cites a bank stem and an analysed Artifact; a part is read from one")
        check_stem_citation(payload["stem"], f"{where} stem")
    if "bass" in payload and payload["bass"] is not True:
        raise NotesError(f"{where} bass must be true when present")
    if isinstance(payload.get("stem"), dict) and payload["stem"].get("output") == "bass" and "bass" not in payload:
        raise NotesError(f"{where} is read from the bass stem and must say bass")
    has_document = bool(by_role["document"])
    machine_only = bool(by_role["run"]) and not has_document
    if has_document != ("alignment" in payload):
        raise NotesError(f"{where} states an alignment exactly when it places a document")
    if has_document:
        alignment = _closed(payload["alignment"], {"method"}, {"max_error_samples"}, f"{where} alignment")
        _text(alignment["method"], f"{where} alignment method", MAX_TEXT)
        if "max_error_samples" in alignment:
            _int(alignment["max_error_samples"], f"{where} alignment max_error_samples", 0)
    _check_document_url(payload, has_document, where)
    if ("strings" in payload or "capo" in payload) and not has_document:
        raise NotesError(f"{where} gives an instrument's strings or capo, which only a document states")
    check_strings_and_capo(payload, where)
    unpitched = "unpitched" in payload
    if unpitched and payload["unpitched"] is not True:
        raise NotesError(f"{where} unpitched must be true when present")
    if unpitched and by_role["tuning"]:
        raise NotesError(f"{where} is unpitched and pins no tuning")
    scaled = "confidence_scale" in payload
    if scaled:
        _text(payload["confidence_scale"], f"{where} confidence_scale", MAX_TEXT)
    low, high = 0, count
    if "range" in payload:
        span = _closed(payload["range"], {"start_sample", "end_sample"}, set(), f"{where} range")
        low = _int(span["start_sample"], f"{where} range start_sample", 0)
        high = _int(span["end_sample"], f"{where} range end_sample", low + 1, count)
    notes = _list(payload["notes"], f"{where} notes", 1, MAX_NOTES)
    previous: tuple[int, int] | None = None
    for index, note in enumerate(notes):
        at = f"{where} note {index}"
        _closed(note, {"onset_sample", "offset_sample", "pitch"},
                {"cents", "velocity", "confidence", "locator", "articulation"}, at)
        _check_articulation(note, machine_only, at)
        onset = _int(note["onset_sample"], f"{at} onset_sample", low, high - 1)
        _int(note["offset_sample"], f"{at} offset_sample", onset + 1, count)
        pitch = _int(note["pitch"], f"{at} pitch", 0, 127)
        if previous is not None and (onset, pitch) <= previous:
            raise NotesError(f"{at} must follow the note before it by onset, then pitch, and not repeat it")
        previous = (onset, pitch)
        if "cents" in note:
            if unpitched:
                raise NotesError(f"{at} has cents but the part is unpitched")
            if not by_role["tuning"]:
                raise NotesError(f"{at} has cents but the claim pins no tuning for them to be relative to")
            _cents(note["cents"], f"{at} cents")
        if "velocity" in note:
            _int(note["velocity"], f"{at} velocity", 1, 127)
        if scaled != ("confidence" in note):
            raise NotesError(f"{at} has a confidence exactly when the claim states a confidence_scale")
        if scaled:
            _decimal(note["confidence"], f"{at} confidence")
        if "locator" in note:
            if not has_document:
                raise NotesError(f"{at} has a locator but the claim pins no document")
            locator = _closed(note["locator"], {"bar"}, {"at", "duration"}, f"{at} locator")
            _int(locator["bar"], f"{at} locator bar", 1)
            if "at" in locator:
                note_length(locator["at"], f"{at} locator at", zero=True)
            if "duration" in locator:
                note_length(locator["duration"], f"{at} locator duration")
    return notes


def notes_range(claim: dict[str, Any], sample_count: int) -> tuple[int, int]:
    """The samples a checked notes claim covers: its `range`, or the stream."""
    span = claim["payload"].get("range")
    return (0, sample_count) if span is None else (span["start_sample"], span["end_sample"])


# ---------------------------------------------------------------------------
# Melody at Work level: the composition's tune, untimed


MELODY_ROLES = {
    "work": (0, 1),          # the Work (an Entity of kind musical_work) whose tune this is
    "document": (0, 1),      # the document the tune was read from
    "run": (0, 1),           # derivation_run v1, when a tool read it
    "meter": (0, 1),         # ADR 0083: an untimed meter_layers v2 claim whose bars the tune is laid on
    "corrects": (0, 8),      # melody v1 claims this one corrects
    "derived_from": (0, 8),  # ADR 0079: such as the recording-level melody it was transcribed from
}


def check_melody(claim: dict[str, Any], meter_claim: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Check an `archive.claim/melody` version 1 claim: a Work's tune as one
    line of notes and rests in written order, each a written length, with no
    samples. Optionally given the untimed meter claim its `meter` reference
    pins, checks that the tune starts on one of its bars and fits in the
    layer's written bars. Returns the items; raises NotesError."""
    where = "melody"
    record = _closed(claim, {"payload", "refs"}, {"revision_id", "agent"}, where)
    by_role = _refs_by_role(record["refs"], MELODY_ROLES, where)
    _check_derived_from(by_role, where)
    if not by_role["work"] and not by_role["document"]:
        raise NotesError(f"{where} needs a work or a document as its subject")
    payload = _closed(record["payload"], {"notes"}, {"part", "start", "document_url"}, f"{where} payload")
    if "part" in payload:
        _text(payload["part"], f"{where} part", MAX_LABEL)
    has_document = bool(by_role["document"])
    machine_only = bool(by_role["run"]) and not has_document
    _check_document_url(payload, has_document, where)
    items = _list(payload["notes"], f"{where} notes", 1, MAX_MELODY_NOTES)
    total = Fraction(0)
    pitched = False
    for index, item in enumerate(items):
        at = f"{where} item {index}"
        if not isinstance(item, dict) or ("pitch" in item) == ("rest" in item):
            raise NotesError(f"{at} must be exactly one of a pitch or a rest")
        if "rest" in item:
            _closed(item, {"rest", "duration"}, {"locator"}, at)
            if item["rest"] is not True:
                raise NotesError(f"{at} rest must be true")
        else:
            _closed(item, {"pitch", "duration"}, {"locator", "articulation"}, at)
            _int(item["pitch"], f"{at} pitch", 0, 127)
            _check_articulation(item, machine_only, at)
            pitched = True
        total += note_length(item["duration"], f"{at} duration")
        if "locator" in item:
            if not has_document:
                raise NotesError(f"{at} has a locator but the claim pins no document")
            _int(_closed(item["locator"], {"bar"}, set(), f"{at} locator")["bar"], f"{at} locator bar", 1)
    if not pitched:
        raise NotesError(f"{where} holds only rests")
    if "start" in payload:
        if not by_role["meter"]:
            raise NotesError(f"{where} names a start bar but pins no meter")
        start = _closed(payload["start"], {"layer", "bar"}, {"at"}, f"{where} start")
        _int(start["layer"], f"{where} start layer", 0)
        _int(start["bar"], f"{where} start bar", 0)
        if "at" in start:
            note_length(start["at"], f"{where} start at", zero=True)
    if meter_claim is not None and by_role["meter"]:
        lengths = _written_bars(meter_claim, by_role["meter"][0], payload.get("start", {}), where)
        if total > sum(lengths, Fraction(0)):
            raise NotesError(f"{where} runs past the last written bar of its meter layer")
    return items


def _written_bars(meter_claim: dict[str, Any], ref: dict[str, Any], start: dict[str, Any], where: str) -> list[Fraction]:
    """The lengths, in whole notes, of the written bars the tune is laid on:
    from its start bar (less the start offset) to the end of that layer.
    Reads only what this needs of ADR 0083's untimed meter; that record's own
    check is the authority on the rest of the claim."""
    meter = _given(meter_claim, ref, f"{where} meter claim")
    if any(isinstance(r, dict) and r.get("role") == "source" for r in meter["refs"]):
        raise NotesError(f"{where} pins a timed meter claim; a Work's tune is laid on untimed bars")
    layers = meter["payload"].get("layers") if isinstance(meter["payload"], dict) else None
    layer = start.get("layer", 0)
    if not isinstance(layers, list) or layer >= len(layers) or not isinstance(layers[layer], dict) \
            or not isinstance(layers[layer].get("bars"), list):
        raise NotesError(f"{where} start layer is not a bars layer of its meter")
    bars = layers[layer]["bars"]
    first = start.get("bar", 0)
    if first >= len(bars):
        raise NotesError(f"{where} start bar is past its meter layer's last bar")
    lengths = []
    for index, bar in enumerate(bars[first:], first):
        if not isinstance(bar, dict):
            raise NotesError(f"{where} meter bar {index} must be an object")
        numerator = _int(bar.get("numerator"), f"{where} meter bar {index} numerator", 1, 64)
        denominator = bar.get("denominator")
        if denominator not in DENOMINATORS or isinstance(denominator, bool):
            raise NotesError(f"{where} meter bar {index} denominator must be one of {DENOMINATORS}")
        lengths.append(Fraction(numerator, denominator))
    offset = note_length(start["at"], f"{where} start at", zero=True) if "at" in start else Fraction(0)
    if offset >= lengths[0]:
        raise NotesError(f"{where} start falls at or after the end of its bar")
    lengths[0] -= offset
    return lengths


def melody_in_bars(payload: dict[str, Any], bar_lengths: list[Fraction]) -> list[dict[str, Any]]:
    """A checked Work-level melody laid on bars: each item's piece in each bar
    it falls in, as {item, bar, at, length, tied}, with `bar` counted from the
    start bar and `tied` true on every piece but an item's last. A note held
    over a bar line is one item in the claim and two tied pieces here. A view:
    a renderer reads it; the claim stays one line of lengths."""
    pieces = []
    bar, at = 0, Fraction(0)
    for index, item in enumerate(payload["notes"]):
        left = Fraction(item["duration"]["count"], item["duration"]["unit"])
        while left > 0:
            if bar >= len(bar_lengths):
                raise NotesError("melody runs past its last bar")
            room = bar_lengths[bar] - at
            take = min(left, room)
            left -= take
            pieces.append({"item": index, "bar": bar, "at": at, "length": take, "tied": left > 0})
            at += take
            if at == bar_lengths[bar]:
                bar, at = bar + 1, Fraction(0)
    return pieces


# ---------------------------------------------------------------------------
# Melody on a recording: which part carries the tune, span by span


MELODY_SPANS_ROLES = {
    "source": (1, 1),        # the recording: the mix
    "stream": (1, 1),        # audio_stream v1 about `source`
    "run": (0, 1),           # derivation_run v1, when a machine chose
    "notes": (1, MAX_PARTS),  # notes v1 claims about `source` that the spans select
    "corrects": (0, 8),      # melody_spans v1 claims this one corrects
    "derived_from": (0, 8),  # ADR 0079
}


def check_melody_spans(claim: dict[str, Any], stream_claim: dict[str, Any],
                       notes_claims: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Check an `archive.claim/melody_spans` version 1 claim against every
    rule of ADR 0086, with its stream claim and, optionally, the notes claims
    it pins keyed by revision id, to check that each is about the same
    recording and covers the spans that select it. Returns the spans."""
    where = "melody spans"
    record = _closed(claim, {"payload", "refs"}, {"revision_id", "agent"}, where)
    by_role = _refs_by_role(record["refs"], MELODY_SPANS_ROLES, where)
    _check_derived_from(by_role, where)
    rate, count = _check_stream(by_role, stream_claim, where)
    payload = _closed(record["payload"], {"sample_rate", "spans"}, set(), f"{where} payload")
    if _int(payload["sample_rate"], f"{where} sample_rate", 1) != rate:
        raise NotesError(f"{where} sample_rate differs from the stream's")
    pinned = {ref["revision_id"]: ref for ref in by_role["notes"]}
    ranges: dict[str, tuple[int, int]] = {}
    if notes_claims is not None:
        for revision, ref in pinned.items():
            if revision not in notes_claims:
                raise NotesError(f"{where} was not given the notes claim {revision}")
            notes = _given(notes_claims[revision], ref, f"{where} notes claim {revision}")
            _about_source(notes, by_role["source"][0], "source", f"{where} notes claim {revision}")
            ranges[revision] = notes_range(notes, count)
    spans = _list(payload["spans"], f"{where} spans", 1, MAX_SPANS)
    previous_end, cited = 0, set()
    for index, span in enumerate(spans):
        at = f"{where} span {index}"
        _closed(span, {"start_sample", "end_sample", "notes"}, {"select"}, at)
        start = _int(span["start_sample"], f"{at} start_sample", previous_end)
        end = _int(span["end_sample"], f"{at} end_sample", start + 1, count)
        previous_end = end
        if span["notes"] not in pinned:
            raise NotesError(f"{at} selects a notes claim the claim does not pin")
        cited.add(span["notes"])
        if span.get("select", "all") not in SELECTIONS:
            raise NotesError(f"{at} select must be one of {SELECTIONS}")
        if span["notes"] in ranges:
            low, high = ranges[span["notes"]]
            if start < low or end > high:
                raise NotesError(f"{at} reaches outside the range its notes claim covers")
    if cited != set(pinned):
        raise NotesError(f"{where} pins notes claims that no span selects")
    return spans


def handoffs(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Where a checked melody spans claim passes the tune from one part to
    another: each pair of adjacent spans (the first ends where the second
    starts) that select different notes claims, as {sample, from, to}. A gap
    between spans is no tune asserted, not a hand-off."""
    spans = payload["spans"]
    return [{"sample": b["start_sample"], "from": a["notes"], "to": b["notes"]}
            for a, b in zip(spans, spans[1:])
            if a["end_sample"] == b["start_sample"] and a["notes"] != b["notes"]]


def tune_notes(payload: dict[str, Any], notes_claims: dict[str, dict[str, Any]], *,
               by_person: bool = True, left_out: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """The recording's tune, a view of a checked melody spans claim and the
    notes claims it pins (keyed by revision id): in each span, the selected
    claim's notes whose onsets fall in it, each with `from`, the notes claim
    it came from. `highest` keeps, of the notes starting together, the
    highest; `lowest` the lowest; `all` keeps every one. A note keeps its own
    offset, which may ring past its span.

    The bass line is never the tune by default (ADR 0086, amended
    2026-10-19): when the melody spans claim's agent is not a person
    (`by_person` false), a span on a notes claim that says `bass` is left
    out, and appended to `left_out` with why, so the answer says what it
    skipped (INV-24). A person's span on the bass (a bass solo) is kept."""
    tune = []
    for span in payload["spans"]:
        selected = notes_claims[span["notes"]]["payload"]
        if not by_person and selected.get("bass") is True:
            if left_out is not None:
                left_out.append({"start_sample": span["start_sample"], "end_sample": span["end_sample"],
                                 "notes": span["notes"],
                                 "why": "a machine's span on the bass line; the bass is never the tune by default"})
            continue
        chosen = [n for n in selected["notes"]
                  if span["start_sample"] <= n["onset_sample"] < span["end_sample"]]
        select = span.get("select", "all")
        if select != "all":
            pick = max if select == "highest" else min
            by_onset: dict[int, dict[str, Any]] = {}
            for note in chosen:
                held = by_onset.get(note["onset_sample"])
                by_onset[note["onset_sample"]] = note if held is None else pick(held, note, key=lambda n: n["pitch"])
            chosen = [by_onset[onset] for onset in sorted(by_onset)]
        tune += [{**note, "from": span["notes"]} for note in chosen]
    return tune


# ---------------------------------------------------------------------------
# Written rhythm: sounded notes written on a meter's beat grid (ADR 0086,
# amended 2026-10-19). A view, computed and never stored; its thresholds are
# named by the view's version, so two readings at one version agree.


@dataclass(frozen=True)
class RhythmRule:
    """The thresholds of one version of the written rhythm view: `divisions`
    grid points per beat, a note `staccato` when it sounds less than
    `staccato_below` of the interval to the next onset, and a rest only for a
    silence of at least `rest_beats` beats at a phrase boundary."""

    version: str
    divisions: int
    staccato_below: Fraction
    rest_beats: Fraction

    def describe(self) -> dict[str, Any]:
        return {"version": self.version, "divisions": self.divisions,
                "staccato_below": _decimal_text(self.staccato_below), "rest_beats": _decimal_text(self.rest_beats)}


RHYTHM_RULES = {"1": RhythmRule("1", 4, Fraction(3, 5), Fraction(1))}


def _decimal_text(value: Fraction) -> str:
    """A non-negative fraction as exact decimal text, to four places."""
    scaled = round(value * 10000)
    whole, part = divmod(scaled, 10000)
    return str(whole) if part == 0 else f"{whole}.{part:04d}".rstrip("0")


def _length(value: Fraction) -> dict[str, int]:
    """A written length as the claims write one: count notes of 1/unit."""
    return {"count": value.numerator, "unit": value.denominator}


@dataclass(frozen=True)
class Beat:
    """One beat of a timed meter layer: its sample, its written value in whole
    notes, its written position from the layer's first beat, and its bar
    (None in a layer of beats only)."""

    sample: int
    value: Fraction
    position: Fraction
    bar: int | None


def beat_grid(meter_payload: dict[str, Any], layer: int = 0) -> tuple[list[Beat], list[Fraction], int]:
    """The beats of one layer of a checked timed meter claim (ADR 0077,
    0083), and each bar's written start, plus the sample the last beat
    ends at. A bar's beats are its `beats` samples, or its span divided
    evenly; a beat's value is its group of the `grouping` over the
    denominator, or one over it. A layer of beats only has quarter beats
    unless it states `beat_unit`, and its last beat ends one beat after."""
    layers = meter_payload.get("layers") if isinstance(meter_payload, dict) else None
    if not isinstance(layers, list) or not 0 <= layer < len(layers) or not isinstance(layers[layer], dict):
        raise NotesError(f"meter has no layer {layer}")
    found = layers[layer]
    beats: list[Beat] = []
    bar_starts: list[Fraction] = []
    position = Fraction(0)
    if "bars" in found:
        end = None
        for index, bar in enumerate(found["bars"]):
            if "start_sample" not in bar:
                raise NotesError("the written rhythm needs a timed meter; this layer's bars have no samples")
            groups = bar.get("grouping") or [1] * bar["numerator"]
            values = [Fraction(g, bar["denominator"]) for g in groups]
            if "beats" in bar:
                samples = list(bar["beats"])
            else:
                span = bar["end_sample"] - bar["start_sample"]
                total = sum(values, Fraction(0))
                samples, at = [], Fraction(0)
                for value in values:
                    samples.append(bar["start_sample"] + int(span * at / total))
                    at += value
            bar_starts.append(position)
            for sample, value in zip(samples, values):
                beats.append(Beat(sample, value, position, index))
                position += value
            end = bar["end_sample"]
        return beats, bar_starts, end
    unit = Fraction(1, 4)
    if isinstance(found.get("beat_unit"), dict):
        unit = Fraction(found["beat_unit"]["count"], found["beat_unit"]["unit"])
    samples = found["beats"]
    for sample in samples:
        beats.append(Beat(sample, unit, position, None))
        position += unit
    last = samples[-1] + (samples[-1] - samples[-2] if len(samples) > 1 else 1)
    return beats, [], last


def _written_at(sample: float, beats: list[Beat], end: int) -> Fraction:
    """The written position of a sample: linear within its beat."""
    index = max(0, bisect_right([b.sample for b in beats], sample) - 1)
    beat = beats[index]
    following = beats[index + 1].sample if index + 1 < len(beats) else end
    span = max(following - beat.sample, 1)
    return beat.position + beat.value * Fraction(round(sample) - beat.sample, span)


def _snap(position: Fraction, beats: list[Beat], divisions: int) -> Fraction:
    """A written position moved to the nearest grid point of its beat."""
    index = max(0, bisect_right([b.position for b in beats], position) - 1)
    beat = beats[index]
    step = beat.value / divisions
    return beat.position + step * round((position - beat.position) / step)


def _beat_samples(sample: int, beats: list[Beat], end: int) -> int:
    index = max(0, bisect_right([b.sample for b in beats], sample) - 1)
    following = beats[index + 1].sample if index + 1 < len(beats) else end
    return max(following - beats[index].sample, 1)


def _boundary(onset: int, silence: tuple[int, int], lines: list[dict[str, Any]]) -> str | None:
    """The phrase boundary a silence falls at, or None (ADR 0086, amended):
    `line_end` where a timed lyrics line ends on the note before it; `breath`
    where it lies between two words of a line, or no line covers it. Inside
    a word, or inside a line that places no words, it is no boundary."""
    start, end = silence
    if any(onset < line["end_sample"] <= end for line in lines):
        return "line_end"
    covering = [line for line in lines if line["start_sample"] <= start and line["end_sample"] >= end]
    if not covering:
        return "breath"
    for line in covering:
        words = line.get("words", [])
        if not words:
            return None
        if any(w["start_sample"] < end and w["end_sample"] > start for w in words):
            return None     # the silence is inside a word
    return "breath"


def written_rhythm(notes: list[dict[str, Any]], meter_payload: dict[str, Any], *, layer: int = 0,
                   lyrics: list[dict[str, Any]] | None = None, version: str = "1") -> dict[str, Any]:
    """Sounded notes written on a meter's beat grid. `notes` are notes as a
    notes claim holds them (or a tune's), `meter_payload` a checked timed
    meter claim's payload, and `lyrics` the `lines` of timed lyrics claims of
    the same recording, whose line ends and words say where phrases end.

    Notes starting together are one chord. Each chord's onset moves to the
    nearest grid point (`divisions` per beat), and its written value fills
    the interval to the next chord's onset, on the grid. A chord that sounds
    less than `staccato_below` of that interval is marked `staccato` rather
    than followed by a rest. A rest is written only when the silence after
    it is at least `rest_beats` beats long and falls at a phrase boundary
    (`line_end` or `breath`); each rest says which. A note keeps the
    articulation its claim writes. Notes outside the meter's beats are left
    out and counted, never moved (INV-24). Returns {rule, written,
    left_out}; `written` items are `{kind, position, duration, bar, at}`
    with, for a chord, its notes' indices, pitches, onset, sounded share,
    and articulation, and for a rest its boundary."""
    if version not in RHYTHM_RULES:
        raise NotesError(f"no written rhythm rule has version {version}")
    rule = RHYTHM_RULES[version]
    beats, bar_starts, end = beat_grid(meter_payload, layer)
    lines = [line for claim in (lyrics or []) for line in claim]
    inside = [(i, n) for i, n in enumerate(notes) if beats[0].sample <= n["onset_sample"] < end]
    left_out = len(notes) - len(inside)
    groups: list[tuple[Fraction, list[tuple[int, dict[str, Any]]]]] = []
    for index, note in sorted(inside, key=lambda pair: (pair[1]["onset_sample"], pair[1]["pitch"])):
        at = _snap(_written_at(note["onset_sample"], beats, end), beats, rule.divisions)
        if groups and groups[-1][0] == at:
            groups[-1][1].append((index, note))
        else:
            groups.append((at, [(index, note)]))

    def place(position: Fraction) -> dict[str, Any]:
        if not bar_starts:
            return {"bar": None, "at": _length(position)}
        bar = max(0, bisect_right(bar_starts, position) - 1)
        return {"bar": bar, "at": _length(position - bar_starts[bar])}

    written: list[dict[str, Any]] = []
    for number, (at, chord) in enumerate(groups):
        onset = chord[0][1]["onset_sample"]
        offset = max(n["offset_sample"] for _, n in chord)
        if number + 1 < len(groups):
            next_at, next_onset = groups[number + 1][0], groups[number + 1][1][0][1]["onset_sample"]
        else:
            next_at = _snap(_written_at(offset, beats, end), beats, rule.divisions)
            next_onset = max(offset, onset + 1)
        step = beats[max(0, bisect_right([b.position for b in beats], at) - 1)].value / rule.divisions
        if next_at <= at:
            next_at = at + step
        marks = sorted({m for _, n in chord for m in n.get("articulation", [])})
        share = Fraction(min(offset, next_onset) - onset, max(next_onset - onset, 1))
        rest = None
        silence = (offset, next_onset)
        if number + 1 < len(groups) and next_onset - offset >= rule.rest_beats * _beat_samples(offset, beats, end):
            boundary = _boundary(onset, silence, lines)
            if boundary is not None:
                note_end = _snap(_written_at(offset, beats, end), beats, rule.divisions)
                note_end = min(max(note_end, at + step), next_at)
                if note_end < next_at:
                    rest = {"kind": "rest", "position": _length(note_end), "duration": _length(next_at - note_end),
                            **place(note_end), "boundary": boundary}
                    next_at = note_end
        if rest is None and number + 1 < len(groups) and share < rule.staccato_below and "staccato" not in marks \
                and "staccatissimo" not in marks:
            marks = sorted(set(marks) | {"staccato"})
        written.append({"kind": "chord", "notes": [i for i, _ in chord], "pitches": [n["pitch"] for _, n in chord],
                        "onset_sample": onset, "position": _length(at), "duration": _length(next_at - at),
                        **place(at), "sounded_share": _decimal_text(share), "articulation": marks})
        if rest is not None:
            written.append(rest)
    return {"rule": rule.describe(), "written": written, "left_out": left_out}
