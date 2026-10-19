"""Scoped judgments and span annotations (ADR 0081): payload and reference
rules, and the standing view of one judge's judgments of one claim.

`archive.assessment/judgment` version 2 is version 1 (ADR 0048) with an
optional scope: one sample span of a timed claim, one range of a sequence's
entries, or one reading of one span or entry. Version 1 stays admissible and
always judges the whole claim revision. `archive.claim/span_annotation`
version 1 is a person's mark on a span of a recording or on a range of a
chord sequence's entries: `unsure`, `interesting`, or a `note` in the
asserter's own words. Both are decided by ADR 0081; no specification release
installs them yet. This module holds their rules as World would re-check them
when it projects such a record, so a client can check a proposal before Door
does; Door remains the authority (INV-2). It uses only the standard library.

Nothing here weighs one claim against another (INV-8). "Prefer A over B" is
two judgments by the same judge, affirming A's span and rejecting B's; the
standing view reads one judge's judgments of one claim and never combines
judges. Machine-detected differences between claims (a mix's chord and one
part's) are computed views, never annotations (ADR 0081, decision D3.2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

JUDGMENT = "archive.assessment/judgment"
JUDGMENT_V1 = (JUDGMENT, "1")
JUDGMENT_V2 = (JUDGMENT, "2")
SPAN_ANNOTATION = ("archive.claim/span_annotation", "1")
CHORD_SEQUENCE = ("archive.claim/chord_sequence", "1")
AUDIO_STREAM = ("archive.claim/audio_stream", "1")

VERDICTS = ("affirmed", "rejected", "withdrawn")
ANNOTATION_KINDS = ("unsure", "interesting", "note")
OBJECT_ID = re.compile(r"urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
REVISION_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
MAX_REASON = 2000
MAX_NOTE = 2000
MAX_ABOUT = 8

# How narrow each scope is. A narrower judgment by the same judge stands over
# a wider one where both apply.
RANK = {"claim": 0, "span": 1, "entries": 1, "reading": 2}


class JudgmentError(ValueError):
    """A judgment that breaks a rule of ADR 0048 (version 1) or ADR 0081 (version 2)."""


class AnnotationError(ValueError):
    """A span annotation that breaks a rule of ADR 0081."""


# ---------------------------------------------------------------------------
# Shared pieces


def _closed(value: Any, required: set[str], optional: set[str], where: str, error: type) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise error(f"{where} must be an object")
    missing = required - set(value)
    extra = set(value) - required - optional
    if missing or extra:
        raise error(f"{where} has missing {sorted(missing)} or unknown {sorted(extra)} fields")
    return value


def _int(value: Any, where: str, low: int, error: type, high: int | None = None) -> int:
    # bool is an int in Python; a JSON true is not a number.
    if not isinstance(value, int) or isinstance(value, bool) or value < low or (high is not None and value > high):
        bound = f"{low}.." + ("" if high is None else str(high))
        raise error(f"{where} must be an integer in {bound}")
    return value


def _text(value: Any, where: str, maximum: int, error: type) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise error(f"{where} must be text of 1 to {maximum} characters")
    return value


def _refs_by_role(refs: Any, counts: dict[str, tuple[int, int]], where: str,
                  error: type) -> dict[str, list[dict[str, Any]]]:
    """References grouped by role, with each role's (minimum, maximum) count.
    Every reference pins an exact revision; an unknown role is refused."""
    if not isinstance(refs, list):
        raise error(f"{where} refs must be a list")
    by_role: dict[str, list[dict[str, Any]]] = {role: [] for role in counts}
    for index, ref in enumerate(refs):
        _closed(ref, {"role", "id", "revision_id"}, set(), f"{where} ref {index}", error)
        if ref["role"] not in counts:
            raise error(f"{where} ref {index} has unknown role {ref['role']!r}")
        if not isinstance(ref["id"], str) or not OBJECT_ID.fullmatch(ref["id"]) \
                or not isinstance(ref["revision_id"], str) or not REVISION_ID.fullmatch(ref["revision_id"]):
            raise error(f"{where} ref {index} must pin an object and an exact revision")
        by_role[ref["role"]].append(ref)
    for role, (low, high) in counts.items():
        if not low <= len(by_role[role]) <= high:
            raise error(f"{where} needs {low} to {high} '{role}' references")
    return by_role


def _cited(record: dict[str, Any], contract: str, where: str, error: type) -> str:
    """The version of `contract` a record cites, from its envelope `contracts`."""
    cited = record.get("contracts")
    if not isinstance(cited, list):
        raise error(f"{where} must cite its contract")
    versions = [c.get("version") for c in cited if isinstance(c, dict) and c.get("id") == contract]
    if len(versions) != 1 or not isinstance(versions[0], str):
        raise error(f"{where} must cite {contract} exactly once")
    return versions[0]


def _pins(record: dict[str, Any], role: str) -> list[dict[str, Any]]:
    refs = record.get("refs") if isinstance(record.get("refs"), list) else []
    return [ref for ref in refs if isinstance(ref, dict) and ref.get("role") == role]


def _same_revision(given: dict[str, Any], pin: dict[str, Any], where: str, error: type) -> None:
    if "revision_id" in given and given["revision_id"] != pin["revision_id"]:
        raise error(f"{where} was given a record other than the one it pins")


def _is_timed(claim: dict[str, Any]) -> bool:
    """A claim on a recording's timeline: its payload states an integer
    `sample_rate` and it pins exactly one `stream`. Chord stacks, meter
    layers, a span contribution, span suggestions, and a span annotation
    are; the rule reads the shape, not a list of contracts, so a later timed
    contract is judged by span with no change here."""
    payload = claim.get("payload") if isinstance(claim.get("payload"), dict) else {}
    rate = payload.get("sample_rate")
    return isinstance(rate, int) and not isinstance(rate, bool) and len(_pins(claim, "stream")) == 1


def _stream_bounds(stream: Any, artifact: dict[str, Any] | None, pin: dict[str, Any], where: str,
                   error: type) -> tuple[int, int]:
    """The (sample_rate, sample_count) of the pinned audio stream claim, after
    checking that it is the pinned revision and, when `artifact` is given,
    that it is about that Artifact revision."""
    record = _closed(stream, {"payload", "refs"}, {"revision_id", "agent", "contracts"}, f"{where} stream claim", error)
    _same_revision(record, pin, f"{where} stream", error)
    if artifact is not None:
        measured = _pins(record, "artifact")
        if len(measured) != 1 or (measured[0].get("id"), measured[0].get("revision_id")) \
                != (artifact["id"], artifact["revision_id"]):
            raise error(f"{where} pins a stream of another Artifact revision")
    payload = record["payload"] if isinstance(record["payload"], dict) else {}
    rate = _int(payload.get("sample_rate"), f"{where} stream sample_rate", 1, error)
    count = _int(payload.get("sample_count"), f"{where} stream sample_count", 1, error)
    return rate, count


# ---------------------------------------------------------------------------
# Judgments


@dataclass(frozen=True)
class Scope:
    """What part of the judged claim revision a judgment judges.

    `kind` is `claim` (the whole revision: every version 1 judgment, and a
    version 2 judgment with no scope), `span` (samples `start` to `end`,
    half-open), `entries` (sequence entries `start` to `end`, half-open, so a
    payload's `first` and `last` are `start` and `end - 1`), or `reading`
    (reading `reading` of span or entry `position`, on the axis `axis`)."""

    kind: str
    axis: str | None = None        # "samples" or "entries"
    start: int | None = None
    end: int | None = None
    position: int | None = None    # the span or entry index a reading scope names
    reading: int | None = None


def check_judgment(judgment: dict[str, Any], judged: dict[str, Any] | None = None,
                   stream: dict[str, Any] | None = None) -> Scope:
    """Check an `archive.assessment/judgment` version 1 or 2 record, and, when
    given, against the claim revision it judges and that claim's pinned
    `stream`. Returns the judgment's scope; raises JudgmentError.

    Version 1 is ADR 0048's, unchanged: a verdict and an optional reason
    about the whole claim. Version 2 adds at most one of `span` (with
    `sample_rate`), `entries`, or `reading`, each checked against the judged
    revision's own positions, and refuses a `withdrawn` verdict by anyone
    but the judged claim's asserter."""
    where = "judgment"
    record = _closed(judgment, {"payload", "refs", "contracts"}, {"revision_id", "agent"}, where, JudgmentError)
    version = _cited(record, JUDGMENT, where, JudgmentError)
    if version not in ("1", "2"):
        raise JudgmentError(f"{where} cites unknown version {version!r} of {JUDGMENT}")
    by_role = _refs_by_role(record["refs"], {"assessment_of": (1, 1)}, where, JudgmentError)
    target = by_role["assessment_of"][0]
    scoped = {"sample_rate", "span", "entries", "reading"} if version == "2" else set()
    payload = _closed(record["payload"], {"verdict"}, {"reason"} | scoped, f"{where} payload", JudgmentError)
    if payload["verdict"] not in VERDICTS:
        raise JudgmentError(f"{where} verdict must be one of {VERDICTS}")
    if "reason" in payload:
        _text(payload["reason"], f"{where} reason", MAX_REASON, JudgmentError)
    if judged is not None:
        _closed(judged, {"payload", "refs"}, {"revision_id", "agent", "contracts"}, f"{where} judged claim",
                JudgmentError)
        _same_revision(judged, target, f"{where} judged claim", JudgmentError)
    if version == "1":
        return Scope("claim")
    if payload["verdict"] == "withdrawn" and judged is not None and "agent" in record and "agent" in judged \
            and record["agent"] != judged["agent"]:
        raise JudgmentError(f"{where} withdraws a claim its own agent did not assert")
    forms = [key for key in ("span", "entries", "reading") if key in payload]
    if len(forms) > 1:
        raise JudgmentError(f"{where} scopes to at most one of span, entries, or reading")
    if ("span" in payload) != ("sample_rate" in payload):
        raise JudgmentError(f"{where} states a sample_rate exactly when it scopes to a span")
    if not forms:
        return Scope("claim")
    form = forms[0]
    if form == "span":
        return _judged_span(payload, judged, stream, where)
    if form == "entries":
        entries = _closed(payload["entries"], {"first", "last"}, set(), f"{where} entries", JudgmentError)
        first = _int(entries["first"], f"{where} entries first", 0, JudgmentError)
        last = _int(entries["last"], f"{where} entries last", first, JudgmentError)
        if judged is not None:
            held = _judged_list(judged, "entries", where)
            if last >= len(held):
                raise JudgmentError(f"{where} names an entry past the judged claim's last")
        return Scope("entries", "entries", first, last + 1)
    reading = _closed(payload["reading"], {"reading"}, {"span", "entry"}, f"{where} reading", JudgmentError)
    axes = [key for key in ("span", "entry") if key in reading]
    if len(axes) != 1:
        raise JudgmentError(f"{where} reading names exactly one of span or entry")
    position = _int(reading[axes[0]], f"{where} reading {axes[0]}", 0, JudgmentError)
    index = _int(reading["reading"], f"{where} reading index", 0, JudgmentError)
    axis = "samples" if axes[0] == "span" else "entries"
    start = end = None
    if judged is not None:
        if axis == "samples" and not _is_timed(judged):
            raise JudgmentError(f"{where} names a span's reading of a claim that is not timed")
        held = _judged_list(judged, "spans" if axis == "samples" else "entries", where)
        if position >= len(held):
            raise JudgmentError(f"{where} names a {axes[0]} past the judged claim's last")
        item = held[position] if isinstance(held[position], dict) else {}
        readings = item.get("readings")
        if not isinstance(readings, list) or index >= len(readings):
            raise JudgmentError(f"{where} names a reading the judged {axes[0]} does not have")
        if axis == "samples":
            start, end = item.get("start_sample"), item.get("end_sample")
        else:
            start, end = position, position + 1
    elif axis == "entries":
        start, end = position, position + 1
    return Scope("reading", axis, start, end, position, index)


def _judged_span(payload: dict[str, Any], judged: dict[str, Any] | None, stream: dict[str, Any] | None,
                 where: str) -> Scope:
    rate = _int(payload["sample_rate"], f"{where} sample_rate", 1, JudgmentError)
    span = _closed(payload["span"], {"start_sample", "end_sample"}, set(), f"{where} span", JudgmentError)
    start = _int(span["start_sample"], f"{where} span start_sample", 0, JudgmentError)
    end = _int(span["end_sample"], f"{where} span end_sample", 1, JudgmentError)
    if end <= start:
        raise JudgmentError(f"{where} span must end after it starts")
    if judged is not None:
        if not _is_timed(judged):
            raise JudgmentError(f"{where} scopes to samples of a claim that is not timed")
        if judged["payload"]["sample_rate"] != rate:
            raise JudgmentError(f"{where} sample_rate differs from the judged claim's")
        if stream is not None:
            _, count = _stream_bounds(stream, None, _pins(judged, "stream")[0], where, JudgmentError)
            if end > count:
                raise JudgmentError(f"{where} span ends past the judged claim's stream")
    return Scope("span", "samples", start, end)


def _judged_list(judged: dict[str, Any], key: str, where: str) -> list[Any]:
    held = judged["payload"].get(key) if isinstance(judged["payload"], dict) else None
    if not isinstance(held, list):
        raise JudgmentError(f"{where} scopes to {key} the judged claim does not have")
    return held


@dataclass(frozen=True)
class Standing:
    """One judge's verdict on one claim revision at one position, and why.

    `verdict` is None when no judgment by that judge applies there. When the
    narrowest applicable judgments disagree, `conflict` is true and `verdict`
    is None: the view reports and chooses neither (INV-24)."""

    verdict: str | None
    conflict: bool
    judgments: tuple[str, ...]
    why: str


def standing(judged: dict[str, Any], judgments: Iterable[dict[str, Any]], judge: str,
             at: int | None = None, reading: int | None = None) -> Standing:
    """What `judge` holds of the `judged` claim revision at position `at` (a
    sample of a timed claim, or an entry index of a sequence), for reading
    `reading` of the span or entry there, or for the claim's statement there
    as a whole when `reading` is None. With `at` None, only judgments of the
    whole claim apply.

    `judgments` are head revisions (a superseded judgment no longer speaks)
    with `revision_id` and `agent`; only those by `judge` of this exact
    revision are read, and each is checked against the claim. A judgment of
    the whole claim applies everywhere; of a span or range of entries, at
    every position in it, to every reading there; of one reading, only to
    that reading of that span or entry. The narrowest applicable judgment
    stands: a reading over a span or range, a span or range over the whole
    claim, and a range inside another of the same kind over that one.
    Judgments that apply equally narrowly and disagree are a conflict. Other
    judges' judgments are never read: a view that sets two people's
    judgments side by side reads each through this one."""
    revision = judged.get("revision_id")
    applicable: list[tuple[Scope, dict[str, Any]]] = []
    for judgment in judgments:
        target = _pins(judgment, "assessment_of")
        if judgment.get("agent") != judge or len(target) != 1 or target[0].get("revision_id") != revision:
            continue
        scope = check_judgment(judgment, judged)
        if _applies(scope, judged, at, reading):
            applicable.append((scope, judgment))
    if not applicable:
        return Standing(None, False, (), "no judgment by this judge applies here")
    top = max(RANK[scope.kind] for scope, _ in applicable)
    narrowest = [(scope, j) for scope, j in applicable if RANK[scope.kind] == top]
    inner = [(s, j) for s, j in narrowest if all(_within(s, other) for other, _ in narrowest)]
    deciding = [(s, j) for s, j in narrowest if inner and _extent(s) == _extent(inner[0][0])] if inner else narrowest
    revisions = tuple(sorted(str(j.get("revision_id")) for _, j in deciding))
    verdicts = {j["payload"]["verdict"] for _, j in deciding}
    what = {"claim": "the whole claim", "span": "a span", "entries": "a range of entries",
            "reading": "one reading"}[deciding[0][0].kind]
    if len(verdicts) > 1:
        return Standing(None, True, revisions,
                        f"{len(deciding)} judgments of {what} apply equally narrowly here and disagree")
    verdict = verdicts.pop()
    wider = len(applicable) - len(deciding)
    return Standing(verdict, False, revisions,
                    f"{verdict} by a judgment of {what}" + (f", narrower than {wider} that also apply" if wider else ""))


def _extent(scope: Scope) -> tuple[int, int] | None:
    return None if scope.kind == "claim" else (scope.start, scope.end)


def _within(inner: Scope, outer: Scope) -> bool:
    a, b = _extent(inner), _extent(outer)
    return b is None or (a is not None and b[0] <= a[0] and a[1] <= b[1])


def _applies(scope: Scope, judged: dict[str, Any], at: int | None, reading: int | None) -> bool:
    if scope.kind == "claim":
        return True
    if at is None:
        return False
    axis = "samples" if _is_timed(judged) else "entries"
    if scope.axis != axis:
        return False
    if scope.kind == "reading" and scope.reading != reading:
        return False
    return scope.start <= at < scope.end


# ---------------------------------------------------------------------------
# Span annotations


@dataclass(frozen=True)
class Mark:
    """A checked span annotation: its kind, the axis and half-open extent it
    marks, and the claims it says it is about."""

    kind: str
    axis: str            # "samples" or "entries"
    start: int
    end: int
    about: tuple[str, ...]


ANNOTATION_ROLES = {
    "source": (0, 1),     # the recording's audio, whose samples the span counts
    "stream": (0, 1),     # audio_stream v1 about `source`
    "sequence": (0, 1),   # chord_sequence v1 whose entries the range counts
    "work": (0, 1),       # the Work that sequence describes, when the annotation names it
    "about": (0, MAX_ABOUT),  # claims the mark is about: what the person looked at
}


def check_span_annotation(claim: dict[str, Any], stream: dict[str, Any] | None = None,
                          sequence: dict[str, Any] | None = None,
                          about: dict[str, dict[str, Any]] | None = None,
                          agent_kind: str | None = None) -> Mark:
    """Check an `archive.claim/span_annotation` version 1 claim, and, when
    given, the stream or sequence it pins, the claims it is `about` (by
    revision), and the kind of the Entity its agent names. Returns the mark;
    raises AnnotationError.

    A mark is one person's: its agent is an Archive object, never a Package
    URL, and it pins no run and no document. It marks one span of a
    recording (`source`, `stream`, `sample_rate`, `span`) or one range of a
    chord sequence's entries (`sequence`, optional `work`, `entries`). Its
    `kind` is `unsure`, `interesting`, or `note`, and only a note has
    `text`, which it must have: the asserter's own words, never a source's."""
    where = "span annotation"
    record = _closed(claim, {"payload", "refs", "agent"}, {"revision_id", "contracts"}, where, AnnotationError)
    if "contracts" in record and _cited(record, SPAN_ANNOTATION[0], where, AnnotationError) != SPAN_ANNOTATION[1]:
        raise AnnotationError(f"{where} must cite {SPAN_ANNOTATION[0]} version {SPAN_ANNOTATION[1]}")
    if not isinstance(record["agent"], str) or not OBJECT_ID.fullmatch(record["agent"]):
        raise AnnotationError(f"{where} is a person's: its agent must be an Archive object, not a program")
    if agent_kind is not None and agent_kind != "person":
        raise AnnotationError(f"{where} agent must be a person, not a {agent_kind}")
    by_role = _refs_by_role(record["refs"], ANNOTATION_ROLES, where, AnnotationError)
    on_recording = bool(by_role["source"] or by_role["stream"])
    on_sequence = bool(by_role["sequence"] or by_role["work"])
    if on_recording == on_sequence:
        raise AnnotationError(f"{where} marks either a recording's samples or a sequence's entries, not both or neither")
    common = {"text"}
    payload = record["payload"]
    if on_recording:
        if not (by_role["source"] and by_role["stream"]):
            raise AnnotationError(f"{where} on a recording pins one source and one stream")
        _closed(payload, {"kind", "sample_rate", "span"}, common, f"{where} payload", AnnotationError)
        rate = _int(payload["sample_rate"], f"{where} sample_rate", 1, AnnotationError)
        span = _closed(payload["span"], {"start_sample", "end_sample"}, set(), f"{where} span", AnnotationError)
        start = _int(span["start_sample"], f"{where} span start_sample", 0, AnnotationError)
        end = _int(span["end_sample"], f"{where} span end_sample", 1, AnnotationError)
        if end <= start:
            raise AnnotationError(f"{where} span must end after it starts")
        if stream is not None:
            stream_rate, count = _stream_bounds(stream, by_role["source"][0], by_role["stream"][0], where,
                                                AnnotationError)
            if rate != stream_rate:
                raise AnnotationError(f"{where} sample_rate differs from the stream's")
            if end > count:
                raise AnnotationError(f"{where} span ends past the stream's sample count")
        axis, subjects = "samples", {by_role["source"][0]["revision_id"]}
    else:
        if not by_role["sequence"]:
            raise AnnotationError(f"{where} on a Work marks a range of a sequence's entries, so it pins the sequence")
        _closed(payload, {"kind", "entries"}, common, f"{where} payload", AnnotationError)
        entries = _closed(payload["entries"], {"first", "last"}, set(), f"{where} entries", AnnotationError)
        start = _int(entries["first"], f"{where} entries first", 0, AnnotationError)
        end = _int(entries["last"], f"{where} entries last", start, AnnotationError) + 1
        axis, subjects = "entries", set()
        if sequence is not None:
            pinned = by_role["sequence"][0]
            _closed(sequence, {"payload", "refs"}, {"revision_id", "agent", "contracts"}, f"{where} sequence",
                    AnnotationError)
            _same_revision(sequence, pinned, f"{where} sequence", AnnotationError)
            if "contracts" in sequence \
                    and _cited(sequence, CHORD_SEQUENCE[0], f"{where} sequence", AnnotationError) != CHORD_SEQUENCE[1]:
                raise AnnotationError(f"{where} sequence must be a chord sequence version 1")
            held = sequence["payload"].get("entries") if isinstance(sequence["payload"], dict) else None
            if not isinstance(held, list) or end > len(held):
                raise AnnotationError(f"{where} names an entry past the sequence's last")
            works = _pins(sequence, "work")
            if by_role["work"] and (len(works) != 1 or works[0].get("revision_id") != by_role["work"][0]["revision_id"]):
                raise AnnotationError(f"{where} names a Work its sequence does not describe")
            subjects = {ref.get("revision_id") for ref in works + _pins(sequence, "document")}
    if payload["kind"] not in ANNOTATION_KINDS:
        raise AnnotationError(f"{where} kind must be one of {ANNOTATION_KINDS}")
    if (payload["kind"] == "note") != ("text" in payload):
        raise AnnotationError(f"{where} has text exactly when its kind is note")
    if "text" in payload:
        _text(payload["text"], f"{where} text", MAX_NOTE, AnnotationError)
    revisions = [ref["revision_id"] for ref in by_role["about"]]
    if len(set(revisions)) != len(revisions):
        raise AnnotationError(f"{where} names a claim it is about twice")
    frame = {ref["revision_id"] for role in ("stream", "sequence") for ref in by_role[role]}
    if frame & set(revisions):
        raise AnnotationError(f"{where} is already on its stream or sequence; about names other claims")
    for revision in revisions:
        other = (about or {}).get(revision)
        if other is None or not subjects:
            continue
        pinned = {ref.get("revision_id") for ref in other.get("refs", []) if isinstance(ref, dict)}
        if not pinned & subjects:
            raise AnnotationError(f"{where} is about a claim {revision} that is not about the same "
                                  + ("recording" if axis == "samples" else "Work or document"))
    return Mark(payload["kind"], axis, start, end, tuple(revisions))
