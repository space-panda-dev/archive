"""Form, and meter without a recording (ADR 0083): payload rules and the
played-order views.

Three contracts are decided by ADR 0083 and not yet installed by any
specification release:

- `archive.claim/form` version 1: a Work's or a document's form with no
  times: sections in written order, repeats with first and second endings,
  and D.C. and D.S. jumps with their fine or coda;
- `archive.claim/form_spans` version 1: sections at sample spans of one
  recording's stream, as a machine, a person, or an alignment of a form
  placed them;
- `archive.claim/meter_layers` version 2: version 1's timed claim, which may
  now cite the untimed claim it was placed from, or an untimed claim about a
  Work or a document whose bars have no samples and are tied to chord
  sequence entries and form sections. The same version carries ADR 0079's
  `corrects`, `derived_from`, and (timed only) bank `stem` citation, checked
  here too, so the version has one check.

Version 1 of `meter_layers` is unchanged and still checked by
`harmony.check_meter_layers`. The repeat shape `{first, last, times}` and its
rules (ordered, outer before inner, nest or apart) are chord sequence version
1's (ADR 0077), applied here to sections; only `endings` is new.

Like `harmony.py`, this module uses only the standard library, so an importer
can check a proposal before Door does; Door remains the authority (INV-2).
Played orders are computed views, never stored (INV-7).
"""

from __future__ import annotations

from fractions import Fraction
from itertools import combinations
from typing import Any

from world.harmony import (
    DENOMINATORS,
    LAYER_ROLES,
    MAX_BARS,
    MAX_LABEL,
    MAX_LAYERS,
    MAX_REPEATS,
    MAX_SPANS,
    MAX_TEXT,
    MAX_URL,
    SOURCE_ROLES,
    URL,
    HarmonyError,
    _check_bar,
    _check_beat_layer,
    _check_modulation,
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
    note_value,
)
from world.notes import NotesError, note_length

FORM = ("archive.claim/form", "1")
FORM_SPANS = ("archive.claim/form_spans", "1")
METER_LAYERS_V2 = ("archive.claim/meter_layers", "2")

MAX_SECTIONS = 1000
MAX_JUMPS = 8
MAX_ENDINGS = 16
MAX_CHORDS_PER_BAR = 64

# The optional common vocabulary of a section's `kind`. A section's `label`
# is always the source's own text; `kind` is what a reader may filter on.
SECTION_KINDS = (
    "intro", "verse", "pre_chorus", "chorus", "post_chorus", "refrain",
    "bridge", "interlude", "instrumental", "solo", "break", "vamp", "head",
    "outro", "coda", "tag",
)
JUMP_KINDS = ("da_capo", "dal_segno")
JUMP_ENDS = ("end", "fine", "coda")


class FormError(HarmonyError):
    """A form, form spans, or meter layers version 2 claim that breaks a rule
    of ADR 0083."""


FORM_ROLES = {
    "work": (0, 1),       # the Work (an Entity of kind musical_work) whose form this is
    "document": (0, 1),   # the document the form was read from
    "run": (0, 1),        # derivation_run v1, when a tool read the document
    "corrects": (0, 8),   # form v1 claims this one corrects
}

FORM_SPANS_ROLES = {
    **SOURCE_ROLES,
    "form": (0, 1),       # the form v1 claim an alignment placed on this recording
    "corrects": (0, 8),   # form_spans v1 claims this one corrects
}

# Meter layers version 2 is one contract version decided by two records:
# ADR 0079 adds `corrects`, `derived_from`, and a bank `stem` citation (timed
# only); ADR 0083 adds the untimed form, `meter`, and `placed` bars. This
# module checks every addition of both, in one place.
TIMED_METER_ROLES = {
    **SOURCE_ROLES,
    "meter": (0, 1),         # the untimed meter_layers v2 claim an alignment placed on this recording
    "corrects": (0, 8),      # timed meter_layers v1 or v2 claims this one corrects (ADR 0079)
    "derived_from": (0, 8),  # claims this one was read from, with or without a run (ADR 0079)
}

UNTIMED_METER_ROLES = {
    "work": (0, 1),
    "document": (0, 1),
    "run": (0, 1),
    "form": (0, 1),       # form v1: the sections the bars' `section` index
    "sequence": (0, 1),   # chord_sequence v1 or v2: the entries the bars' `chords` index
    "corrects": (0, 8),   # untimed meter_layers v2 claims this one corrects (ADR 0079)
    "derived_from": (0, 8),  # claims this one was read from (ADR 0079)
}


# ---------------------------------------------------------------------------
# Shared pieces


def _pinned(given: dict[str, Any] | None, ref: dict[str, Any], where: str, what: str) -> None:
    if given is not None and given.get("revision_id", ref["revision_id"]) != ref["revision_id"]:
        raise FormError(f"{where} was given a {what} claim other than the one it pins")


def _check_untimed_subject(payload: dict[str, Any], by_role: dict[str, list[dict[str, Any]]], where: str) -> bool:
    """An untimed claim is about a Work, a document, or both; it states no
    alignment, and a `document_url` only beside a pinned document."""
    if not by_role["work"] and not by_role["document"]:
        raise FormError(f"{where} needs a work or a document as its subject")
    return _check_url(payload, by_role, where)


def _check_url(payload: dict[str, Any], by_role: dict[str, list[dict[str, Any]]], where: str) -> bool:
    has_document = bool(by_role["document"])
    if "document_url" in payload:
        if not has_document:
            raise FormError(f"{where} names a document_url without pinning the document")
        if not URL.fullmatch(_text(payload["document_url"], f"{where} document_url", MAX_URL)):
            raise FormError(f"{where} document_url must be an http or https URL")
    return has_document


def _check_alignment(payload: dict[str, Any], aligned: bool, where: str) -> None:
    """A timed claim states an `alignment` exactly when it places a document
    or an untimed claim on the recording (ADR 0077's rule)."""
    if aligned != ("alignment" in payload):
        raise FormError(f"{where} states an alignment exactly when it places a document or an untimed claim")
    if aligned:
        alignment = _closed(payload["alignment"], {"method"}, {"max_error_samples"}, f"{where} alignment")
        _text(alignment["method"], f"{where} alignment method", MAX_TEXT)
        if "max_error_samples" in alignment:
            _int(alignment["max_error_samples"], f"{where} alignment max_error_samples", 0)


def _check_label(value: dict[str, Any], where: str) -> None:
    if "label" in value:
        _text(value["label"], f"{where} label", MAX_LABEL)
    if "kind" in value and value["kind"] not in SECTION_KINDS:
        raise FormError(f"{where} kind must be one of {SECTION_KINDS}")


def _one_locator(value: Any, keys: set[str], where: str) -> tuple[str, int]:
    """A locator of exactly one of `keys`, a positive integer."""
    if not isinstance(value, dict) or len(value) != 1 or not set(value) <= keys:
        raise FormError(f"{where} locator must give exactly one of {sorted(keys)}")
    (key, number), = value.items()
    return key, _int(number, f"{where} locator {key}", 1)


def _inside(position: int, repeat: dict[str, Any]) -> bool:
    """Strictly inside a repeat: past its first section, up to its last."""
    return repeat["first"] < position <= repeat["last"]


# ---------------------------------------------------------------------------
# Form


def check_form(claim: dict[str, Any]) -> list[dict[str, Any]]:
    """Check an `archive.claim/form` version 1 claim: a Work's or a
    document's sections in written order, with repeats, endings, and jumps,
    and no times. Returns the sections; raises FormError (a HarmonyError)."""
    where = "form"
    record = _closed(claim, {"payload", "refs"}, {"revision_id", "agent"}, where)
    by_role = _refs_by_role(record["refs"], FORM_ROLES, where)
    payload = _closed(record["payload"], {"sections"}, {"repeats", "jumps", "document_url"}, f"{where} payload")
    if "alignment" in payload:
        raise FormError(f"{where} is untimed and states no alignment")
    has_document = _check_untimed_subject(payload, by_role, where)
    sections = _list(payload["sections"], f"{where} sections", 1, MAX_SECTIONS)
    previous: tuple[str, int] | None = None
    for index, section in enumerate(sections):
        at = f"{where} section {index}"
        _closed(section, {"label"}, {"kind", "locator"}, at)
        _check_label(section, at)
        if "locator" in section:
            if not has_document:
                raise FormError(f"{at} has a locator but the claim pins no document")
            located = _one_locator(section["locator"], {"line", "bar"}, at)
            if previous is not None and (located[0] != previous[0] or located[1] < previous[1]):
                raise FormError(f"{at} is located by another kind of locator, or before the section before it")
            previous = located
    repeats = _check_repeats(payload.get("repeats", []), len(sections), where)
    _check_jumps(payload.get("jumps", []), len(sections), repeats, where)
    return sections


def _check_repeats(value: Any, count: int, where: str) -> list[dict[str, Any]]:
    """Chord sequence version 1's repeat rules over sections, plus endings."""
    repeats = _list(value, f"{where} repeats", 0, MAX_REPEATS)
    ranges = []
    for index, repeat in enumerate(repeats):
        at = f"{where} repeat {index}"
        _closed(repeat, {"first", "last", "times"}, {"endings"}, at)
        first = _int(repeat["first"], f"{at} first", 0, count - 1)
        last = _int(repeat["last"], f"{at} last", first, count - 1)
        times = _int(repeat["times"], f"{at} times", 2, 999)
        if "endings" in repeat:
            _check_endings(repeat["endings"], first, last, times, at)
        ranges.append((first, -last))
    if ranges != sorted(set(ranges)):
        raise FormError(f"{where} repeats must be distinct and in order of first section, outer before inner")
    for (a, b), (c, d) in combinations(ranges, 2):
        if c <= -b < -d:
            raise FormError(f"{where} repeats must nest or be apart, never cross")
    # A repeat inside one with endings lies wholly in its body or in one ending.
    for outer in repeats:
        parts = _parts(outer)
        for inner in repeats:
            if inner is outer or not (outer["first"] <= inner["first"] and inner["last"] <= outer["last"]):
                continue
            if not any(low <= inner["first"] and inner["last"] <= high for low, high in parts):
                raise FormError(f"{where} has a repeat across the body and an ending of another")
    return repeats


def _parts(repeat: dict[str, Any]) -> list[tuple[int, int]]:
    """A repeat's body and its endings as (first, last) section ranges."""
    if "endings" not in repeat:
        return [(repeat["first"], repeat["last"])]
    endings = repeat["endings"]
    return [(repeat["first"], endings[0]["first"] - 1)] + [(e["first"], e["last"]) for e in endings]


def _check_endings(value: Any, first: int, last: int, times: int, where: str) -> None:
    """Endings follow a non-empty body, one after another, the last ending at
    the repeat's last section; each pass of the repeat takes exactly one."""
    endings = _list(value, f"{where} endings", 2, MAX_ENDINGS)
    passes: list[int] = []
    start = None
    for index, ending in enumerate(endings):
        at = f"{where} ending {index}"
        _closed(ending, {"first", "last", "passes"}, set(), at)
        low = _int(ending["first"], f"{at} first", first + 1, last)
        high = _int(ending["last"], f"{at} last", low, last)
        if start is not None and low != start:
            raise FormError(f"{at} must start right after the ending before it")
        start = high + 1
        numbers = _list(ending["passes"], f"{at} passes", 1, times)
        for number, item in enumerate(numbers):
            _int(item, f"{at} passes[{number}]", 1, times)
        if any(a >= b for a, b in zip(numbers, numbers[1:])):
            raise FormError(f"{at} passes must be distinct and ascending")
        passes += numbers
    if start != last + 1:
        raise FormError(f"{where} last ending must end at the repeat's last section")
    if sorted(passes) != list(range(1, times + 1)):
        raise FormError(f"{where} endings must give every pass from 1 to {times} exactly one ending")


def _check_jumps(value: Any, count: int, repeats: list[dict[str, Any]], where: str) -> None:
    """A D.C. or D.S. sign at the end of section `after`: go back to the start
    or to the segno at section `to`, then play to the end, to the end of the
    `fine` section, or to the end of `coda_from` and on from the `coda`."""
    jumps = _list(value, f"{where} jumps", 0, MAX_JUMPS)
    afters = []
    for index, jump in enumerate(jumps):
        at = f"{where} jump {index}"
        if not isinstance(jump, dict) or jump.get("kind") not in JUMP_KINDS or jump.get("then") not in JUMP_ENDS:
            raise FormError(f"{at} kind must be one of {JUMP_KINDS} and then one of {JUMP_ENDS}")
        required = {"after", "kind", "then"} | ({"to"} if jump["kind"] == "dal_segno" else set())
        required |= {"fine": {"fine"}, "coda": {"coda_from", "coda"}, "end": set()}[jump["then"]]
        _closed(jump, required, {"repeats_on_return"}, at)
        after = _int(jump["after"], f"{at} after", 0, count - 1)
        start = _int(jump["to"], f"{at} to", 0, after) if "to" in jump else 0
        if "repeats_on_return" in jump and not isinstance(jump["repeats_on_return"], bool):
            raise FormError(f"{at} repeats_on_return must be true or false")
        if "fine" in jump:
            _int(jump["fine"], f"{at} fine", start, count - 1)
        if "coda" in jump:
            coda_from = _int(jump["coda_from"], f"{at} coda_from", start, count - 2)
            coda = _int(jump["coda"], f"{at} coda", max(after, coda_from) + 1, count - 1)
            landings = (start, coda)
        else:
            landings = (start,)
        for landing in landings:
            if any(_inside(landing, repeat) for repeat in repeats):
                raise FormError(f"{at} lands inside a repeat rather than at its first section")
        afters.append(after)
    if afters != sorted(set(afters)):
        raise FormError(f"{where} jumps must be at distinct sections, in written order")


def _play(first: int, last: int, repeats: list[dict[str, Any]], take: bool, final: bool) -> list[tuple[int, bool]]:
    """Sections first to last as played, each with whether this is its last
    pass through every repeat around it. With `take` false a repeat is played
    once, with the ending of its last pass, as on a return from a jump."""
    inside = sorted((r for r in repeats if first <= r["first"] and r["last"] <= last),
                    key=lambda r: (r["first"], -r["last"]))
    order: list[tuple[int, bool]] = []
    at = first
    while at <= last:
        outer = next((r for r in inside if r["first"] == at), None)
        if outer is None:
            order.append((at, final))
            at += 1
            continue
        nested = [r for r in inside if r is not outer and outer["first"] <= r["first"] and r["last"] <= outer["last"]]
        for number in range(1, outer["times"] + 1) if take else [outer["times"]]:
            last_pass = final and number == outer["times"]
            if "endings" in outer:
                body_last = outer["endings"][0]["first"] - 1
                ending = next(e for e in outer["endings"] if number in e["passes"])
                order += _play(outer["first"], body_last, nested, take, last_pass)
                order += _play(ending["first"], ending["last"], nested, take, last_pass)
            else:
                order += _play(outer["first"], outer["last"], nested, take, last_pass)
        at = outer["last"] + 1
    return order


def form_order(payload: dict[str, Any]) -> list[int]:
    """The sections' indices in the order played: every repeat unrolled, each
    pass taking its ending, and each jump taken once, the first time its
    section ends on the last pass of every repeat around it. On the return a
    repeat is played once with its last ending unless the jump says
    `repeats_on_return`. A view of a checked payload, never stored."""
    sections = payload["sections"]
    repeats = payload.get("repeats", [])
    jumps = {jump["after"]: jump for jump in payload.get("jumps", [])}
    last = len(sections) - 1

    def play_from(start: int, take: bool) -> list[tuple[int, bool]]:
        return _play(start, last, [r for r in repeats if r["first"] >= start], take, True)

    order: list[int] = []
    events, index, active, taken = play_from(0, True), 0, None, set()
    while index < len(events):
        section, final = events[index]
        order.append(section)
        index += 1
        if not final:
            continue
        if active is not None and active["then"] == "fine" and section == active["fine"]:
            break
        if active is not None and active["then"] == "coda" and section == active["coda_from"]:
            events, index, active = play_from(active["coda"], True), 0, None
            continue
        if section in jumps and section not in taken:
            active = jumps[section]
            taken.add(section)
            events, index = play_from(active.get("to", 0), active.get("repeats_on_return", False)), 0
    return order


# ---------------------------------------------------------------------------
# Form spans


def check_form_spans(claim: dict[str, Any], stream_claim: dict[str, Any],
                     form_claim: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Check an `archive.claim/form_spans` version 1 claim: sections at
    sample spans of one stream, with the `audio_stream` claim it pins and,
    when it pins a `form`, optionally that form claim, to check each span's
    `section` against it. Returns the spans; raises FormError."""
    where = "form spans"
    record = _closed(claim, {"payload", "refs"}, {"revision_id", "agent"}, where)
    by_role = _refs_by_role(record["refs"], FORM_SPANS_ROLES, where)
    rate, count = _check_stream(by_role, stream_claim, where)
    payload = _closed(record["payload"], {"sample_rate", "spans"}, {"document_url", "alignment"}, f"{where} payload")
    if _int(payload["sample_rate"], f"{where} sample_rate", 1) != rate:
        raise FormError(f"{where} sample_rate differs from the stream's")
    has_document = _check_url(payload, by_role, where)
    placed = bool(by_role["form"])
    _check_alignment(payload, has_document or placed, where)
    sections = None
    if placed and form_claim is not None:
        _pinned(form_claim, by_role["form"][0], where, "form")
        sections = check_form(form_claim)
    previous_end = 0
    for index, span in enumerate(_list(payload["spans"], f"{where} spans", 1, MAX_SPANS)):
        at = f"{where} span {index}"
        _closed(span, {"start_sample", "end_sample"}, {"label", "kind", "section", "locator"}, at)
        _, previous_end = _check_span_bounds(span["start_sample"], span["end_sample"], previous_end, count, at)
        _check_label(span, at)
        if "section" in span:
            if not placed:
                raise FormError(f"{at} names a form section but the claim pins no form")
            _int(span["section"], f"{at} section", 0, None if sections is None else len(sections) - 1)
        elif "label" not in span:
            raise FormError(f"{at} needs a label or a form section")
        if "locator" in span:
            if not has_document:
                raise FormError(f"{at} has a locator but the claim pins no document")
            _one_locator(span["locator"], {"line", "bar"}, at)
    return payload["spans"]


# ---------------------------------------------------------------------------
# Meter layers version 2


def is_untimed_meter(claim: dict[str, Any]) -> bool:
    """A meter layers version 2 claim is untimed when it pins no `source`."""
    refs = claim.get("refs") if isinstance(claim, dict) else None
    return isinstance(refs, list) and not any(isinstance(r, dict) and r.get("role") == "source" for r in refs)


def check_meter_layers_v2(claim: dict[str, Any], stream_claim: dict[str, Any] | None = None, *,
                          untimed_claim: dict[str, Any] | None = None,
                          form_claim: dict[str, Any] | None = None,
                          sequence_claim: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Check an `archive.claim/meter_layers` version 2 claim. A timed claim
    (it pins a `source`) is version 1's, with the `audio_stream` claim it
    pins, and may cite the untimed claim it was placed from (`meter`), each
    placed bar naming the bar it came from. An untimed claim is about a Work
    or a document: bars without samples, optionally in form sections and
    holding chord sequence entries. Returns the layers; raises FormError."""
    if is_untimed_meter(claim):
        return _check_untimed_meter(claim, form_claim, sequence_claim)
    if stream_claim is None:
        raise FormError("meter layers v2 pins a source, so its stream claim is needed")
    return _check_timed_meter(claim, stream_claim, untimed_claim)


def _check_layer_header(layer: Any, where: str) -> None:
    if not isinstance(layer, dict):
        raise FormError(f"{where} must be an object")
    if "label" in layer:
        _text(layer["label"], f"{where} label", MAX_LABEL)
    if "role" in layer and layer["role"] not in LAYER_ROLES:
        raise FormError(f"{where} role must be one of {LAYER_ROLES}")


def _check_roles(layers: list[dict[str, Any]], where: str) -> None:
    roles = {layer.get("role") for layer in layers}
    if "felt" in roles and "written" not in roles:
        raise FormError(f"{where} has a felt layer but no written layer for it to be felt against")


def _check_timed_meter(claim: dict[str, Any], stream_claim: dict[str, Any],
                       untimed_claim: dict[str, Any] | None) -> list[dict[str, Any]]:
    where = "meter layers v2"
    record = _closed(claim, {"payload", "refs"}, {"revision_id", "agent"}, where)
    by_role = _refs_by_role(record["refs"], TIMED_METER_ROLES, where)
    rate, count = _check_stream(by_role, stream_claim, where)
    payload = _closed(record["payload"], {"sample_rate", "layers"},
                      {"modulations", "document_url", "alignment", "stem"}, f"{where} payload")
    if _int(payload["sample_rate"], f"{where} sample_rate", 1) != rate:
        raise FormError(f"{where} sample_rate differs from the stream's")
    check_lineage(payload, by_role, where)
    has_document = _check_url(payload, by_role, where)
    placed = bool(by_role["meter"])
    _check_alignment(payload, has_document or placed, where)
    written = None
    if placed and untimed_claim is not None:
        _pinned(untimed_claim, by_role["meter"][0], where, "meter")
        if not is_untimed_meter(untimed_claim):
            raise FormError(f"{where} places a meter claim that is not untimed")
        written = _check_untimed_meter(untimed_claim, None, None)
    layers = _list(payload["layers"], f"{where} layers", 1, MAX_LAYERS)
    for number, layer in enumerate(layers):
        at = f"{where} layer {number}"
        _check_layer_header(layer, at)
        if ("bars" in layer) == ("beats" in layer):
            raise FormError(f"{at} must hold exactly one of bars or beats")
        if "beats" in layer:
            _check_beat_layer(layer, count, at)
            continue
        _closed(layer, {"bars"}, {"label", "role"}, at)
        previous_end = 0
        for index, bar in enumerate(_list(layer["bars"], f"{at} bars", 1, MAX_BARS)):
            bat = f"{at} bar {index}"
            if not isinstance(bar, dict):
                raise FormError(f"{bat} must be an object")
            plain = {key: value for key, value in bar.items() if key != "placed"}
            previous_end = _check_bar(plain, previous_end, count, has_document, bat)
            if "placed" in bar:
                if not placed:
                    raise FormError(f"{bat} names a placed bar but the claim pins no meter claim")
                _check_placed(bar, written, bat)
    _check_roles(layers, where)
    seen = [_check_modulation(link, layers, f"{where} modulation {index}")
            for index, link in enumerate(_list(payload.get("modulations", []), f"{where} modulations", 0, MAX_BARS))]
    if seen != sorted(set(seen)):
        raise FormError(f"{where} modulations must be distinct and in order of layer and bar")
    return layers


def _check_placed(bar: dict[str, Any], written: list[dict[str, Any]] | None, where: str) -> None:
    """A placed bar names the untimed bar it was placed from and states that
    bar's time signature and grouping; a repeated bar is placed once per pass."""
    placed = _closed(bar["placed"], {"layer", "bar"}, set(), f"{where} placed")
    layer = _int(placed["layer"], f"{where} placed layer", 0, None if written is None else len(written) - 1)
    if written is None:
        _int(placed["bar"], f"{where} placed bar", 0)
        return
    bars = written[layer]["bars"]
    source = bars[_int(placed["bar"], f"{where} placed bar", 0, len(bars) - 1)]
    for key in ("numerator", "denominator", "grouping"):
        if bar.get(key) != source.get(key):
            raise FormError(f"{where} states another {key} than the bar it was placed from")


def _check_untimed_meter(claim: dict[str, Any], form_claim: dict[str, Any] | None,
                         sequence_claim: dict[str, Any] | None) -> list[dict[str, Any]]:
    where = "untimed meter layers"
    record = _closed(claim, {"payload", "refs"}, {"revision_id", "agent"}, where)
    by_role = _refs_by_role(record["refs"], UNTIMED_METER_ROLES, where)
    payload = _closed(record["payload"], {"layers"}, {"modulations", "document_url"}, f"{where} payload")
    check_derived_from(by_role, where)
    has_document = _check_untimed_subject(payload, by_role, where)
    sections = entries = None
    if by_role["form"] and form_claim is not None:
        _pinned(form_claim, by_role["form"][0], where, "form")
        sections = check_form(form_claim)
    if by_role["sequence"] and sequence_claim is not None:
        _pinned(sequence_claim, by_role["sequence"][0], where, "sequence")
        entries = check_chord_sequence_v2(sequence_claim)
    layers = _list(payload["layers"], f"{where} layers", 1, MAX_LAYERS)
    for number, layer in enumerate(layers):
        at = f"{where} layer {number}"
        _check_layer_header(layer, at)
        _closed(layer, {"bars"}, {"label", "role"}, f"{at} (an untimed layer holds bars, never beats)")
        last_section, last_entry, last_locator = 0, 0, 0
        for index, bar in enumerate(_list(layer["bars"], f"{at} bars", 1, MAX_BARS)):
            bat = f"{at} bar {index}"
            _closed(bar, {"numerator", "denominator"}, {"grouping", "locator", "section", "chords"}, bat)
            length = _check_signature(bar, bat)
            if "locator" in bar:
                if not has_document:
                    raise FormError(f"{bat} has a locator but the claim pins no document")
                locator = _closed(bar["locator"], {"bar"}, set(), f"{bat} locator")
                last_locator = _rising(_int(locator["bar"], f"{bat} locator bar", 1), last_locator, bat, "document bar")
            if "section" in bar:
                if not by_role["form"]:
                    raise FormError(f"{bat} names a form section but the claim pins no form")
                section = _int(bar["section"], f"{bat} section", 0, None if sections is None else len(sections) - 1)
                last_section = _rising(section, last_section, bat, "section")
            if "chords" in bar:
                if not by_role["sequence"]:
                    raise FormError(f"{bat} holds chord entries but the claim pins no sequence")
                last_entry = _check_chords(bar["chords"], length, entries, last_entry, bat)
    _check_roles(layers, where)
    seen = []
    for index, link in enumerate(_list(payload.get("modulations", []), f"{where} modulations", 0, MAX_BARS)):
        at = f"{where} modulation {index}"
        plain = dict(link) if isinstance(link, dict) else link
        layer = _int(plain.get("layer") if isinstance(plain, dict) else None, f"{at} layer", 0, len(layers) - 1)
        # Untimed bars are adjacent by position: give the timed check samples that say so.
        stand_in = [{"bars": [{"start_sample": i, "end_sample": i + 1} for i in range(len(l["bars"]))]}
                    if i == layer else l for i, l in enumerate(layers)]
        seen.append(_check_modulation(plain, stand_in, at))
    if seen != sorted(set(seen)):
        raise FormError(f"{where} modulations must be distinct and in order of layer and bar")
    return layers


def _rising(value: int, previous: int, where: str, what: str) -> int:
    if value < previous:
        raise FormError(f"{where} {what} goes back: bars are in written order")
    return value


def _check_signature(bar: dict[str, Any], where: str) -> Fraction:
    """A bar's numerator, denominator, and grouping, as version 1 states them.
    Returns the bar's length in whole notes."""
    numerator = _int(bar["numerator"], f"{where} numerator", 1, 64)
    if bar["denominator"] not in DENOMINATORS or isinstance(bar["denominator"], bool):
        raise FormError(f"{where} denominator must be one of {DENOMINATORS}")
    if "grouping" in bar:
        grouping = _list(bar["grouping"], f"{where} grouping", 1, 64)
        for index, group in enumerate(grouping):
            _int(group, f"{where} grouping[{index}]", 1)
        if sum(grouping) != numerator:
            raise FormError(f"{where} grouping does not add up to its numerator")
    return Fraction(numerator, bar["denominator"])


def _check_chords(value: Any, length: Fraction, entries: list[dict[str, Any]] | None,
                  previous: int, where: str) -> int:
    """The sequence entries that change in this bar, each `at` an offset from
    the downbeat (`count` notes of value 1/`unit`; absent, the downbeat),
    earlier than the bar's end and later than the one before. Entries never
    go back: the bars are written order, and a chord held over a bar line is
    not listed again."""
    chords = _list(value, f"{where} chords", 1, MAX_CHORDS_PER_BAR)
    last_offset = None
    for index, chord in enumerate(chords):
        at = f"{where} chord {index}"
        _closed(chord, {"entry"}, {"at"}, at)
        entry = _int(chord["entry"], f"{at} entry", 0, None if entries is None else len(entries) - 1)
        previous = _rising(entry, previous, at, "sequence entry")
        offset = Fraction(0)
        if "at" in chord:
            # ADR 0086's written length, the one definition, so a triplet offset is writable.
            try:
                offset = note_length(chord["at"], f"{at} at", zero=True)
            except NotesError as error:
                raise FormError(str(error)) from None
        if offset >= length:
            raise FormError(f"{at} falls at or after the end of its bar")
        if last_offset is not None and offset <= last_offset:
            raise FormError(f"{at} must fall after the chord before it")
        last_offset = offset
    return previous


def bar_order(layer: dict[str, Any], form_payload: dict[str, Any] | None = None) -> list[int]:
    """An untimed bars layer's bar indices in the order played: the form's
    played sections, each with its bars in written order. Without a form the
    bars are played as written. Every bar must name a section when a form is
    given, so that no bar is dropped silently. A view, never stored."""
    bars = layer["bars"]
    if form_payload is None:
        return list(range(len(bars)))
    if any("section" not in bar for bar in bars):
        raise FormError("every bar needs a section for the form to order it")
    by_section: dict[int, list[int]] = {}
    for index, bar in enumerate(bars):
        by_section.setdefault(bar["section"], []).append(index)
    return [index for section in form_order(form_payload) for index in by_section.get(section, [])]
