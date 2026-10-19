"""Named views over chord claims (ADR 0082): the request rules, the first
current rule, the correction overlay, computed differences, chord names, and
the lead sheet a viewer reads.

A view is a question World answers from the claims and assessments in its
selected generation, by a rule named with an identifier and a version. Its
answer is computed when asked, never admitted, and never stored as evidence
(S-18, INV-7): a cache of it is regenerable, like any relay answer
(ADR 0063). A current rule is a view that chooses; it chooses per question,
names the claim it chose and why, and reports a conflict rather than
choosing where its rule cannot (INV-8, INV-24).

Five views, each version 1:

- `archive.view/chord_names`: the computed names of chord readings, so a
  client never reimplements `world/harmony.py`;
- `archive.view/chord_corrections`: ADR 0077's correction overlay of one
  recording's chord claims;
- `archive.view/chord_differences`: where a part's chord differs from the
  mix's, two parts differ, or two tools differ, labelled `difference`,
  never `conflict`;
- `archive.view/chords_current`: the first current rule for chords, for one
  chosen person (decision D4.2);
- `archive.view/lead_sheet_current`: which lead sheet of a Work a viewer
  reads, a person's or a band's (decision D4.1).

The two that name chords take `transpose` (ADR 0089) and `key`, the key
reading, or for `chords_current` the key regions claim, whose spelling
names them (ADR 0080). `world/record_views.py` adds the views of 0.0.10's
other records to the same registry, at the end of this module.

Records are given as World reads them: dictionaries of `revision_id`, `id`,
`shape`, `contract` (`[id, version]`), `agent`, `payload`, `refs`, and
`head` (false when a later revision supersedes it; absent means true). A
view reads only the records it is given; the relay gives it only what the
caller may read and states how many it withheld. Standard library only.
"""

from __future__ import annotations

import json
import re
from bisect import bisect_right
from typing import Any, Callable

from world import judgments as judging
from world.exports import transpose_key, transpose_reading
from world.harmony import HarmonyError, check_reading_v2, is_bass_only, reading_names, stack_names
from world.tonality import check_key_reading, region_at, spell_name
from world.view_requests import OBJECT_ID, REVISION_ID, ViewError, _bad, _closed, _object_id, _pin  # noqa: F401

VIEW = "archive.view/"
CHORD_STACKS = {("archive.claim/chord_stacks", "1"), ("archive.claim/chord_stacks", "2")}
JUDGMENT = {("archive.assessment/judgment", "1"), ("archive.assessment/judgment", "2")}
LEAD_SHEET = {("archive.claim/lead_sheet", "1")}
KEY_REGIONS = ("archive.claim/key_regions", "1")
BAND_MEMBERSHIP = {("archive.claim/band_membership", "1")}
DERIVATION_RUN = ("archive.claim/derivation_run", "1")
METHOD_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}\Z")
MAX_NAMED_READINGS = 1000
MAX_MACHINES = 16
TIERS = ("person", "document", "machine")
DIFFERENCE_KINDS = ("part_vs_mix", "part_vs_part", "tools")

# Decision D4.2: the operator's stated order of chord machines, by the
# method identifier their derivation runs name (ADR 0064). A parameter, not
# a ranking of machines in general: a viewer may state another order.
DEFAULT_MACHINE_ORDER = ("chords-jiang", "chords-chordino")


# ---------------------------------------------------------------------------
# Requests


def _sample(value: Any, where: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise _bad(f"{where} must be an integer of at least 0")
    return value


def _window(params: dict[str, Any]) -> tuple[int, int | None]:
    start = _sample(params.get("start_sample", 0), "parameters start_sample")
    end = params.get("end_sample")
    if end is not None and _sample(end, "parameters end_sample") <= start:
        raise _bad("parameters end_sample must be after start_sample")
    return start, end


def _source_subject(subject: Any) -> dict[str, Any]:
    _closed(subject, {"source"}, set(), "subject")
    return {"source": _pin(subject["source"], "subject source")}


TRANSPOSE = (-11, 11)


def _transpose(params: dict[str, Any]) -> int:
    """ADR 0089's transposition: semitones moved before naming; a view
    parameter, never a claim."""
    value = params.get("transpose", 0)
    if isinstance(value, bool) or not isinstance(value, int) or not TRANSPOSE[0] <= value <= TRANSPOSE[1]:
        raise _bad(f"parameters transpose must be an integer in {TRANSPOSE[0]}..{TRANSPOSE[1]}")
    return value


def _key_reading(value: Any, where: str) -> dict[str, Any]:
    """A key reading of ADR 0080, by its own check, whose spelling names the
    chords (a view parameter: the reader's choice of key, never a claim)."""
    try:
        check_key_reading(value, isinstance(value, dict) and "weight" in value, where)
    except HarmonyError as error:
        raise _bad(str(error)) from None
    return value


def _names_parameters(params: dict[str, Any], caller: str | None) -> dict[str, Any]:
    _closed(params, set(), {"transpose", "key"}, "parameters")
    key = params.get("key")
    return {"transpose": _transpose(params),
            "key": None if key is None else _key_reading(key, "parameters key")}


def _current_key(value: Any) -> dict[str, Any] | None:
    """Which key spells the chosen chords: a key reading, or the key regions
    claim (pinned) whose region over each stretch spells it. ADR 0080 names
    no current key, so the reader chooses; none is World's default spelling."""
    if value is None:
        return None
    if not isinstance(value, dict) or len(value) != 1 or next(iter(value)) not in ("reading", "claim"):
        raise _bad("parameters key must be exactly one of {reading} or {claim}")
    if "reading" in value:
        return {"reading": _key_reading(value["reading"], "parameters key reading")}
    return {"claim": _pin(value["claim"], "parameters key claim")}


def _window_parameters(params: dict[str, Any], caller: str | None) -> dict[str, Any]:
    _closed(params, set(), {"start_sample", "end_sample"}, "parameters")
    start, end = _window(params)
    return {"start_sample": start, **({} if end is None else {"end_sample": end})}


def _names_subject(subject: Any) -> dict[str, Any]:
    _closed(subject, {"readings"}, set(), "subject")
    readings = subject["readings"]
    if not isinstance(readings, list) or not 1 <= len(readings) <= MAX_NAMED_READINGS:
        raise _bad(f"subject readings must be a list of 1 to {MAX_NAMED_READINGS} readings")
    for index, reading in enumerate(readings):
        _check_any_reading(reading, f"subject reading {index}")
    return {"readings": readings}


def _differences_parameters(params: dict[str, Any], caller: str | None) -> dict[str, Any]:
    _closed(params, set(), {"kinds", "start_sample", "end_sample"}, "parameters")
    kinds = params.get("kinds", list(DIFFERENCE_KINDS))
    if not isinstance(kinds, list) or not kinds or any(k not in DIFFERENCE_KINDS for k in kinds) \
            or len(set(kinds)) != len(kinds):
        raise _bad(f"parameters kinds must be distinct values of {DIFFERENCE_KINDS}")
    start, end = _window(params)
    return {"kinds": [k for k in DIFFERENCE_KINDS if k in kinds], "start_sample": start,
            **({} if end is None else {"end_sample": end})}


def _person(params: dict[str, Any], caller: str | None, where: str) -> str:
    if "person" in params:
        return _object_id(params["person"], where)
    if caller is None:
        raise _bad(f"{where} is required when the caller is not bound to a person")
    return _object_id(caller, f"{where} (the caller's person)")


def _current_parameters(params: dict[str, Any], caller: str | None) -> dict[str, Any]:
    _closed(params, set(), {"person", "machine_order", "start_sample", "end_sample", "transpose", "key"}, "parameters")
    order = params.get("machine_order", list(DEFAULT_MACHINE_ORDER))
    if not isinstance(order, list) or len(order) > MAX_MACHINES or len(set(map(str, order))) != len(order) \
            or any(not isinstance(m, str) or not METHOD_ID.fullmatch(m) for m in order):
        raise _bad(f"parameters machine_order must be 0 to {MAX_MACHINES} distinct method identifiers")
    start, end = _window(params)
    return {"person": _person(params, caller, "parameters person"), "machine_order": list(order),
            "transpose": _transpose(params), "key": _current_key(params.get("key")),
            "start_sample": start, **({} if end is None else {"end_sample": end})}


def _lead_sheet_subject(subject: Any) -> dict[str, Any]:
    _closed(subject, {"work"}, {"recording"}, "subject")
    result = {"work": _object_id(subject["work"], "subject work")}
    if "recording" in subject:
        result["recording"] = _object_id(subject["recording"], "subject recording")
    return result


def _lead_sheet_parameters(params: dict[str, Any], caller: str | None) -> dict[str, Any]:
    _closed(params, set(), {"by"}, "parameters")
    if "by" not in params:
        return {"by": {"person": _person({}, caller, "parameters by person")}}
    by = params["by"]
    if not isinstance(by, dict) or len(by) != 1 or next(iter(by)) not in ("person", "band"):
        raise _bad("parameters by must be exactly one of {person} or {band}")
    kind = next(iter(by))
    return {"by": {kind: _object_id(by[kind], f"parameters by {kind}")}}


# The registry: every view World serves, at every version it serves. The
# relay's GET /world/views lists it and POST /world/view dispatches through
# it, so a view missing here is neither listed nor answered.
VIEWS: dict[tuple[str, str], dict[str, Any]] = {
    (VIEW + "chord_names", "1"): {
        "question": "What are the computed names of these chord readings?",
        "subject": _names_subject, "parameters": _names_parameters},
    (VIEW + "chord_corrections", "1"): {
        "question": "Over this recording, which chord claims correct which, and where?",
        "subject": _source_subject, "parameters": _window_parameters},
    (VIEW + "chord_differences", "1"): {
        "question": "Where do a part and the mix, two parts, or two tools hear different chords?",
        "subject": _source_subject, "parameters": _differences_parameters},
    (VIEW + "chords_current", "1"): {
        "question": "Which chord sounds over these samples, for this person?",
        "subject": _source_subject, "parameters": _current_parameters},
    (VIEW + "lead_sheet_current", "1"): {
        "question": "Which lead sheet of this Work does this viewer read?",
        "subject": _lead_sheet_subject, "parameters": _lead_sheet_parameters},
}


def check_request(request: Any, caller: str | None = None) -> dict[str, Any]:
    """Check a view request and return it with every default filled in, as
    the answer repeats it. `caller` is the person the relay's login is bound
    to; it is the default `person` where a view takes one. Fails closed: an
    unknown view, version, field, or value is refused, never ignored."""
    body = _closed(request, {"view", "version", "subject"}, {"parameters"}, "request")
    view, version = body["view"], body["version"]
    if not isinstance(view, str) or not isinstance(version, str):
        raise _bad("request view and version must be text")
    if not any(key[0] == view for key in VIEWS):
        raise ViewError("view_unknown", f"no view is named {view!r}")
    if (view, version) not in VIEWS:
        raise ViewError("view_not_served", f"{view} version {version} is not served")
    entry = VIEWS[(view, version)]
    params = body.get("parameters", {})
    if not isinstance(params, dict):
        raise _bad("request parameters must be an object")
    return {"view": view, "version": version, "subject": entry["subject"](body["subject"]),
            "parameters": entry["parameters"](params, caller)}


def catalogue() -> list[dict[str, str]]:
    """The served views, as GET /world/views lists them."""
    return [{"view": view, "version": version, "question": entry["question"]}
            for (view, version), entry in sorted(VIEWS.items())]


# ---------------------------------------------------------------------------
# Reading the records


def _check_any_reading(reading: Any, where: str) -> str:
    """A reading of any form chord stacks version 2 holds (ADR 0079's check),
    weighted exactly when it carries a weight, since a request has no
    claim to state a scale."""
    try:
        return check_reading_v2(reading, isinstance(reading, dict) and "weight" in reading, where)
    except HarmonyError as error:
        raise _bad(str(error)) from None


def _contract(record: dict[str, Any]) -> tuple[str, str]:
    contract = record.get("contract") or ("", "")
    return contract[0], contract[1]


def _refs(record: dict[str, Any], role: str) -> list[dict[str, Any]]:
    return [r for r in record.get("refs", []) if r.get("role") == role]


def _heads(world: list[dict[str, Any]], contracts: set[tuple[str, str]]) -> list[dict[str, Any]]:
    return [r for r in world if _contract(r) in contracts and r.get("head", True)]


class Judgments:
    """Judgments of claims, by the claim revision they assess, read through
    ADR 0081's standing (`world/judgments.py`): one judge at a time, the
    narrowest applicable judgment standing, at one sample of the judged
    claim. A version 1 judgment, or a version 2 one with no scope, judges
    the whole claim; a version 2 one may judge a span of samples or one
    reading of one span. A conflict between one judge's equally narrow
    judgments is no verdict here."""

    def __init__(self, world: list[dict[str, Any]]):
        self.of: dict[str, list[dict[str, Any]]] = {}
        for record in _heads(world, JUDGMENT):
            target = _refs(record, "assessment_of")
            if len(target) != 1:
                continue
            contract = _contract(record)
            self.of.setdefault(target[0]["revision_id"], []).append({
                "payload": record.get("payload", {}), "refs": record.get("refs", []),
                "contracts": [{"id": contract[0], "version": contract[1]}],
                "revision_id": record.get("revision_id"), "agent": record.get("agent")})

    def _verdict(self, claim: dict[str, Any], judge: str | None, at: int | None, reading: int | None = None):
        found = self.of.get(claim["revision_id"])
        if not found or judge is None:
            return None
        judged = {k: claim[k] for k in ("payload", "refs", "revision_id", "agent") if k in claim}
        try:
            return judging.standing(judged, found, judge, at, reading).verdict
        except (judging.JudgmentError, ValueError) as error:
            raise ViewError("view_request_invalid", f"a judgment of {claim['revision_id']} is malformed: {error}") \
                from None

    def boundaries(self, claim: dict[str, Any]) -> set[int]:
        """The samples where a span-scoped judgment of this claim starts or
        ends, so a view cuts its stretches there."""
        points: set[int] = set()
        for judgment in self.of.get(claim["revision_id"], []):
            span = judgment["payload"].get("span")
            if isinstance(span, dict):
                points |= {p for p in (span.get("start_sample"), span.get("end_sample")) if isinstance(p, int)}
        return points

    def withdrawn(self, claim: dict[str, Any], at: int | None = None) -> bool:
        """Taken back by its own asserter, whole (`at` None) or at this sample."""
        return self._verdict(claim, claim.get("agent"), at) == "withdrawn"

    def rejected(self, claim: dict[str, Any], person: str, at: int, reading: int | None = None) -> bool:
        return self._verdict(claim, person, at, reading) == "rejected"

    def affirmed(self, claim: dict[str, Any], person: str, at: int, index: int) -> tuple[bool, int | None]:
        """Whether `person` affirmed what the claim says at this sample, and
        the one reading of span `index` they affirmed, when a judgment of one
        reading decided it."""
        whole = self._verdict(claim, person, at)
        readings = claim["payload"]["spans"][index]["readings"]
        picked = [r for r in range(len(readings)) if self._verdict(claim, person, at, r) == "affirmed"]
        if whole != "affirmed" and len(picked) == 1:
            return True, picked[0]
        return whole == "affirmed", None


def _chord_claims(world: list[dict[str, Any]], source: dict[str, str]) -> list[dict[str, Any]]:
    found = [c for c in _heads(world, CHORD_STACKS)
             if any((r["id"], r.get("revision_id")) == (source["id"], source["revision_id"])
                    for r in _refs(c, "source"))]
    known = found or any(r.get("revision_id") == source["revision_id"] and r.get("id") == source["id"]
                         for r in world)
    if not known:
        raise ViewError("view_subject_not_found", f"no record is {source['revision_id']}")
    return sorted(found, key=lambda c: c["revision_id"])


def _method(world_by_revision: dict[str, dict[str, Any]], claim: dict[str, Any]) -> str | None:
    runs = _refs(claim, "run")
    if not runs:
        return None
    run = world_by_revision.get(runs[0].get("revision_id"))
    if run is None or _contract(run) != DERIVATION_RUN:
        return None
    method = run.get("payload", {}).get("method", {})
    return method.get("id") if isinstance(method, dict) else None


def _ref(claim: dict[str, Any], span: int, reading: int | None = None) -> dict[str, Any]:
    ref = {"claim": claim["revision_id"], "span": span}
    return ref if reading is None else {**ref, "reading": reading}


def _stack_key(reading: dict[str, Any]) -> str:
    """What two readings must share to agree: the stack, or no chord, or the
    text of a reading kept only as text. Weights, voicings, and the
    source's own spelling are not part of what sounds."""
    if reading.get("no_chord"):
        return "no_chord"
    if "bass" not in reading:
        return "text:" + reading.get("text", "")
    return json.dumps({k: reading[k] for k in ("bass", "tones", "root", "uncertain") if k in reading},
                      sort_keys=True)


def _names(reading: dict[str, Any], key: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Every computed name of a stack, spelled by `key` (ADR 0080's rule:
    the root as the key names it, the bass by its degree above the root)
    when a key reading is given, and by World's default spelling otherwise."""
    if is_bass_only(reading) or "bass" not in reading:
        return []
    return [{"tones": sorted(tones),
             "names": [_name(spell_name(n, reading["bass"], key) if key else n) for n in names]}
            for tones, names in reading_names(reading)]


def _spelled_by(key: dict[str, Any] | None) -> bool:
    return bool(key) and "collection" in key


def _name(name: Any) -> dict[str, Any]:
    return {"root": name.root, "asserted_root": name.asserted_root, "harte": name.harte,
            "symbol": name.symbol, "distance": name.distance}


def _segments(intervals: list[tuple[int, int]], start: int, end: int | None,
              points: set[int] = frozenset()) -> list[tuple[int, int]]:
    """The elementary segments every interval boundary, and every extra
    point (where a judgment's span starts or ends), cuts inside the window,
    keeping only those some interval covers."""
    cuts = sorted({p for interval in intervals for p in interval} | set(points) | {start}
                  | (set() if end is None else {end}))
    cuts = [p for p in cuts if p >= start and (end is None or p <= end)]
    position = {p: n for n, p in enumerate(cuts)}
    depth = [0] * (len(cuts) + 1)
    for s, e in intervals:
        s, e = max(s, start), e if end is None else min(e, end)
        if s < e:
            depth[position[s]] += 1
            depth[position[e]] -= 1
    result, open_count = [], 0
    for n, (a, b) in enumerate(zip(cuts, cuts[1:])):
        open_count += depth[n]
        if open_count > 0:
            result.append((a, b))
    return result


class _Spans:
    """One claim's spans, for finding the span covering a sample."""

    def __init__(self, claim: dict[str, Any]):
        self.claim = claim
        self.spans = claim["payload"]["spans"]
        self.starts = [s["start_sample"] for s in self.spans]

    def covering(self, start: int, end: int) -> int | None:
        index = bisect_right(self.starts, start) - 1
        if index >= 0 and self.spans[index]["end_sample"] >= end:
            return index
        return None


# ---------------------------------------------------------------------------
# archive.view/chord_names version 1


def chord_names(world: list[dict[str, Any]], subject: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Every computed name of each reading, in the order World ranks names
    (ADR 0077): one entry per way its uncertain tones can resolve."""
    result = []
    key = params.get("key")
    if key is not None and params["transpose"]:
        key = transpose_key(key, params["transpose"])
    key = key if _spelled_by(key) else None
    for reading in subject["readings"]:
        reading = transpose_reading(reading, params["transpose"]) if params["transpose"] else reading
        if is_bass_only(reading):
            result.append({"form": "bass_only", "resolutions": []})
        elif reading.get("no_chord"):
            result.append({"form": "no_chord", "resolutions": []})
        elif "bass" not in reading:
            result.append({"form": "text", "resolutions": []})
        else:
            result.append({"form": "stack", "resolutions": _names(reading, key)})
    return {"names": result, "spelling": "key" if key else "default"}


# ---------------------------------------------------------------------------
# archive.view/chord_corrections version 1


def chord_corrections(world: list[dict[str, Any]], subject: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """ADR 0077's overlay. Over the samples where a claim's spans overlap
    the spans of a claim it names in `corrects`, the correcting reading is
    shown first and the corrected one beside it, marked by whom. Two
    correctors of one claim that disagree, or claims that correct each
    other, are a conflict, and the view chooses neither. Elsewhere every
    claim stands as asserted, so only corrected stretches are listed."""
    judgments = Judgments(world)
    claims = [c for c in _chord_claims(world, subject["source"]) if not judgments.withdrawn(c)]
    spans = [_Spans(c) for c in claims]
    corrects = {c["revision_id"]: {r.get("revision_id") for r in _refs(c, "corrects")} for c in claims}
    intervals = [(s["start_sample"], s["end_sample"]) for x in spans for s in x.spans]
    points = {p for c in claims for p in judgments.boundaries(c)}
    segments = []
    for a, b in _segments(intervals, params["start_sample"], params.get("end_sample"), points):
        cover = [(x.claim, i) for x in spans if (i := x.covering(a, b)) is not None
                 and not judgments.withdrawn(x.claim, a)]
        present = {c["revision_id"] for c, _ in cover}
        corrected = []
        for claim, index in cover:
            by = sorted(rev for rev in present if claim["revision_id"] in corrects[rev])
            if by:
                corrected.append({**_ref(claim, index), "by": by})
        if not corrected:
            continue
        lost = {c["claim"] for c in corrected}
        shown = [(c, i) for c, i in cover if c["revision_id"] not in lost
                 and corrects[c["revision_id"]] & present]
        conflict = not shown or len({_stack_key(c["payload"]["spans"][i]["readings"][0]) for c, i in shown}) > 1
        segment = {"start_sample": a, "end_sample": b, "shown": [_ref(c, i) for c, i in shown],
                   "corrected": corrected, "conflict": conflict}
        if conflict:
            segment["why"] = ("claims correct each other" if not shown
                              else "two correcting claims disagree over these samples")
        segments.append(segment)
    return {"segments": _merge(segments)}


def _merge(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for segment in segments:
        last = merged[-1] if merged else None
        if last and last["end_sample"] == segment["start_sample"] and \
                {k: v for k, v in last.items() if k not in ("start_sample", "end_sample")} == \
                {k: v for k, v in segment.items() if k not in ("start_sample", "end_sample")}:
            last["end_sample"] = segment["end_sample"]
        else:
            merged.append(dict(segment))
    return merged


# ---------------------------------------------------------------------------
# archive.view/chord_differences version 1


def chord_differences(world: list[dict[str, Any]], subject: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Where two chord claims about one recording hear different pitch
    classes over the same samples. Each pair's first readings are compared:
    the pitch classes sounding certainly (bass and tones), and the bass.
    A pair is compared when one names a `part` and the other none (a part
    against the mix), when both name different parts, or when both are
    whole-sound claims of machines of different methods (two tools). The
    label is `difference`: the mix's harmony and one player's local chord
    are both true, so this is a place to look, never a conflict."""
    judgments = Judgments(world)
    by_revision = {r["revision_id"]: r for r in world if "revision_id" in r}
    claims = [c for c in _chord_claims(world, subject["source"]) if not judgments.withdrawn(c)]
    start, end = params["start_sample"], params.get("end_sample")
    found, not_compared = [], 0
    for i, a in enumerate(claims):
        for b in claims[i + 1:]:
            kind, first, second = _pair_kind(a, b, by_revision)
            if kind not in params["kinds"]:
                continue
            for si, sa in enumerate(first["payload"]["spans"]):
                for sj, sb in enumerate(second["payload"]["spans"]):
                    lo = max(sa["start_sample"], sb["start_sample"], start)
                    hi = min(sa["end_sample"], sb["end_sample"], *(() if end is None else (end,)))
                    if lo >= hi:
                        continue
                    entry = _difference(sa["readings"][0], sb["readings"][0])
                    if entry is None:
                        continue
                    cuts = sorted({lo, hi} | {p for p in judgments.boundaries(first) | judgments.boundaries(second)
                                              if lo < p < hi})
                    for x, y in zip(cuts, cuts[1:]):
                        if judgments.withdrawn(first, x) or judgments.withdrawn(second, x):
                            continue
                        if entry == "not_compared":
                            not_compared += 1
                            continue
                        found.append({"kind": kind, "label": "difference", "start_sample": x, "end_sample": y,
                                      "a": _side(first, si, by_revision), "b": _side(second, sj, by_revision),
                                      **entry})
    found.sort(key=lambda d: (d["start_sample"], d["end_sample"], d["a"]["claim"], d["b"]["claim"]))
    return {"differences": found, "not_compared": not_compared,
            "unresolved_runs": _unresolved_runs(claims, by_revision)}


def _pair_kind(a: dict[str, Any], b: dict[str, Any], by_revision: dict[str, dict[str, Any]]) \
        -> tuple[str | None, dict[str, Any], dict[str, Any]]:
    pa, pb = a["payload"].get("part"), b["payload"].get("part")
    if pa is not None and pb is None:
        return "part_vs_mix", a, b
    if pb is not None and pa is None:
        return "part_vs_mix", b, a
    if pa is not None and pb is not None:
        return ("part_vs_part" if pa != pb else None), a, b
    ma, mb = _method(by_revision, a), _method(by_revision, b)
    return ("tools" if ma and mb and ma != mb else None), a, b


def _side(claim: dict[str, Any], span: int, by_revision: dict[str, dict[str, Any]]) -> dict[str, Any]:
    side = _ref(claim, span, 0)
    if "part" in claim["payload"]:
        side["part"] = claim["payload"]["part"]
    method = _method(by_revision, claim)
    if method:
        side["method"] = method
    return side


def _difference(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any] | str | None:
    if is_bass_only(a) or is_bass_only(b) or "bass" not in a and not a.get("no_chord") \
            or "bass" not in b and not b.get("no_chord"):
        return "not_compared"
    if a.get("no_chord") or b.get("no_chord"):
        if a.get("no_chord") and b.get("no_chord"):
            return None
        return {"no_chord": "a" if a.get("no_chord") else "b"}
    sa, sb = {a["bass"], *a["tones"]}, {b["bass"], *b["tones"]}
    if sa == sb and a["bass"] == b["bass"]:
        return None
    uncertain = sorted({pc for r in (a, b) for g in r.get("uncertain", []) for pc in g["pitch_classes"]})
    entry = {"only_in_a": sorted(sa - sb), "only_in_b": sorted(sb - sa), "bass_a": a["bass"], "bass_b": b["bass"]}
    if uncertain:
        entry["uncertain"] = uncertain
    return entry


# ---------------------------------------------------------------------------
# archive.view/chords_current version 1: the first current rule


def _candidate(claim: dict[str, Any], index: int, at: int, person: str, order: list[str], judgments: Judgments,
               by_revision: dict[str, dict[str, Any]]) -> tuple[int, int, int, str] | None:
    """(tier, rank, reading, why) for one span under the rule, or None when
    the rule never chooses it: withdrawn, rejected by the person, every
    reading rejected, or from an asserter the rule does not name."""
    if judgments.withdrawn(claim, at) or judgments.rejected(claim, person, at):
        return None
    readings = claim["payload"]["spans"][index]["readings"]
    affirmed, affirmed_reading = judgments.affirmed(claim, person, at, index)
    usable = [r for r, reading in enumerate(readings)
              if not is_bass_only(reading) and not judgments.rejected(claim, person, at, r)]
    if affirmed_reading is not None and affirmed_reading in usable:
        usable = [affirmed_reading]
    if not usable:
        return None
    if claim.get("agent") == person:
        return 0, 0, usable[0], "asserted by the chosen person"
    if affirmed:
        return 0, 0, usable[0], "affirmed by the chosen person"
    if _refs(claim, "document") or _refs(claim, "sequence"):
        return 1, 0, usable[0], "placed from an aligned document"
    method = _method(by_revision, claim)
    if method in order:
        return 2, order.index(method), usable[0], f"machine {method}, {order.index(method) + 1} in the stated order"
    return None


def chords_current(world: list[dict[str, Any]], subject: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """The first current rule for chords (decision D4.2), for one chosen
    person, over one recording. Over each stretch of samples it chooses, in
    order: the person's own claims and the spans they affirmed; then claims
    placed from an aligned document; then machines, in the stated order.
    A correction among the claims it may choose replaces what it corrects.
    Where a machine's chord is chosen and machines' bass-only claims from the
    bass stem agree on the bass, that bass is the chord's slash bass. A held
    span's continuation yields, within its tier, to an asserted one. Two
    choices of the same tier and rank that disagree are a conflict, and the
    rule chooses nothing there. Every other reading over the stretch is
    listed beside the choice: nothing is hidden."""
    person, order = params["person"], params["machine_order"]
    judgments = Judgments(world)
    by_revision = {r["revision_id"]: r for r in world if "revision_id" in r}
    claims = [c for c in _chord_claims(world, subject["source"]) if not judgments.withdrawn(c)]
    chords = [c for c in claims if not all(is_bass_only(s["readings"][0]) for s in c["payload"]["spans"])]
    basses = [_Spans(c) for c in claims if c not in chords and _refs(c, "run") and _from_bass_stem(c)]
    spans = [_Spans(c) for c in chords]
    corrects = {c["revision_id"]: {r.get("revision_id") for r in _refs(c, "corrects")} for c in chords}
    # Bass spans cut the stretches too, so a slash bass changes where it does.
    intervals = [(s["start_sample"], s["end_sample"]) for x in spans + basses for s in x.spans]
    points = {p for x in spans + basses for p in judgments.boundaries(x.claim)}
    segments = []
    for a, b in _segments(intervals, params["start_sample"], params.get("end_sample"), points):
        cover = [(x.claim, i) for x in spans if (i := x.covering(a, b)) is not None]
        ranked = {}
        for claim, index in cover:
            found = _candidate(claim, index, a, person, order, judgments, by_revision)
            if found:
                ranked[claim["revision_id"]] = (claim, index, found)
        segment = _choose(a, b, cover, ranked, corrects, judgments, basses, person, params["transpose"])
        if segment:
            segments.append(segment)
    segments = _merge(segments)
    if params.get("key") is not None:
        key_at, cuts = _key_at(world, subject["source"], params["key"])
        segments = _spell_segments(segments, key_at, cuts, params["transpose"])
    return {"rule": "chosen person, then aligned documents, then machines in the stated order",
            "segments": segments, "unresolved_runs": _unresolved_runs(chords, by_revision)}


def _key_at(world: list[dict[str, Any]], source: dict[str, str], key: dict[str, Any]):
    """The key reading in force at a sample, and the samples where it may
    change: one reading everywhere, or the first reading of the asserter's
    region of a key regions claim about the same recording (a gap, or a
    region with no key, is spelled by default)."""
    if "reading" in key:
        return lambda sample: key["reading"], []
    claim = next((r for r in world if r.get("revision_id") == key["claim"]["revision_id"]
                  and _contract(r) == KEY_REGIONS), None)
    if claim is None or not any((r.get("id"), r.get("revision_id")) == (source["id"], source["revision_id"])
                                for r in _refs(claim, "source")):
        raise _bad("parameters key claim must be a key regions claim about the subject's source")
    payload = claim["payload"]

    def at(sample: int) -> dict[str, Any] | None:
        region = region_at(payload, sample)
        return region["readings"][0] if region else None
    return at, sorted({p for region in payload["regions"] for p in (region["start_sample"], region["end_sample"])})


def _spell_segments(segments: list[dict[str, Any]], key_at, cuts: list[int], transpose: int) -> list[dict[str, Any]]:
    """Each stretch with a stack named by the key in force there, cut where
    the key may change; `spelling` says which spelling named it."""
    spelled = []
    for segment in segments:
        points = [segment["start_sample"]] + [p for p in cuts if segment["start_sample"] < p < segment["end_sample"]] \
            + [segment["end_sample"]]
        for a, b in zip(points, points[1:]):
            piece = {**segment, "start_sample": a, "end_sample": b}
            if "stack" in piece:
                key = key_at(a)
                if key is not None and transpose:
                    key = transpose_key(key, transpose)
                key = key if _spelled_by(key) else None
                piece["names"] = _names(piece["stack"], key)
                piece["spelling"] = "key" if key else "default"
            spelled.append(piece)
    return spelled


def _unresolved_runs(claims: list[dict[str, Any]], by_revision: dict[str, dict[str, Any]]) -> list[str]:
    """Claims that pin a run this view could not read (withheld, or not a
    derivation run): their method is unknown, so no rule can rank them by
    it, and the answer says so rather than treating them as unranked
    (INV-24)."""
    return [c["revision_id"] for c in claims if _refs(c, "run") and _method(by_revision, c) is None]


def _choose(a: int, b: int, cover: list, ranked: dict, corrects: dict, judgments: Judgments,
            basses: list, person: str, transpose: int = 0) -> dict[str, Any] | None:
    if not cover:
        return None
    present = set(ranked)
    corrected = {rev for rev in present if any(rev in corrects[other] for other in present if other != rev)}
    live = [ranked[rev] for rev in sorted(present - corrected)]
    segment: dict[str, Any] = {"start_sample": a, "end_sample": b}
    why = []
    if corrected:
        why.append("corrected here by a claim the rule may choose: " + ", ".join(sorted(corrected)))
    if not live:
        segment.update(conflict=[_ref(c, i, f[2]) for c, i, f in ranked.values()],
                       why=why + (["the claims the rule may choose correct each other"] if ranked
                                  else ["no claim here is one the rule may choose"]))
        if not ranked:
            segment.pop("conflict")
        segment["others"] = _others(cover, [], judgments)
        return segment
    best = min(f[0:2] for _, _, f in live)
    group = [(c, i, f) for c, i, f in live if f[0:2] == best]
    asserted = [(c, i, f) for c, i, f in group if not _in_held_stretch(c, i, a)]
    if asserted and len(asserted) < len(group):
        why.append("a held end's continuation yields to an asserted chord of the same tier")
        group = asserted
    keys = {_stack_key(c["payload"]["spans"][i]["readings"][f[2]]) for c, i, f in group}
    if len(keys) > 1:
        segment.update(conflict=[_ref(c, i, f[2]) for c, i, f in group],
                       why=why + [f"{len(group)} claims of tier {TIERS[best[0]]} disagree; the rule chooses none"])
        segment["others"] = _others(cover, segment["conflict"], judgments)
        return segment
    claim, index, (tier, rank, reading_index, reason) = group[0]
    reading = claim["payload"]["spans"][index]["readings"][reading_index]
    chosen = _ref(claim, index, reading_index)
    segment["chosen"] = {**chosen, "tier": TIERS[tier], "rank": rank}
    why.insert(0, reason)
    if len(group) > 1:
        why.append("agreeing: " + ", ".join(c["revision_id"] for c, _, _ in group[1:]))
    stack = {k: reading[k] for k in ("bass", "tones", "root", "uncertain", "no_chord", "text") if k in reading}
    if TIERS[tier] == "machine" and "bass" in reading and "uncertain" not in reading:
        found = _stem_bass(a, b, basses, judgments, person)
        if isinstance(found, str):
            why.append(found)
        elif found is not None:
            bass, ref = found
            if bass != reading["bass"]:
                sounding = {reading["bass"], *reading["tones"]}
                stack = {"bass": bass, "tones": sorted(sounding - {bass}),
                         **({"root": reading["root"]} if "root" in reading else {})}
            segment["bass_from"] = ref
            why.append("the bass is the machines' bass-stem reading")
    segment["held"] = _in_held_stretch(claim, index, a)
    # ADR 0089: a transposition moves what is shown, stack and names alike; `chosen` names the claim's own reading.
    stack = transpose_reading(stack, transpose) if transpose else stack
    segment["stack"] = stack
    segment["names"] = _names(stack) if "bass" in stack else []
    segment["why"] = why
    segment["others"] = _others(cover, [chosen], judgments)
    return segment


def _in_held_stretch(claim: dict[str, Any], index: int, start: int) -> bool:
    span = claim["payload"]["spans"][index]
    return span.get("end") == "held" and start > span["start_sample"]


def _from_bass_stem(claim: dict[str, Any]) -> bool:
    """A claim read from a bass stem (ADR 0079): its payload cites, by bank
    key, a stem whose output is `bass`. D4.2's slash bass is the stem bass,
    so a bass-only claim read from the mix or another stem is not one."""
    stem = claim["payload"].get("stem")
    return isinstance(stem, dict) and stem.get("output") == "bass"


def _stem_bass(a: int, b: int, basses: list, judgments: Judgments, person: str):
    """The bass the machines' bass-only claims (from the bass stem) agree on
    over these samples, with the span it came from; a sentence when they
    disagree; None when none covers them."""
    found = []
    for x in basses:
        index = x.covering(a, b)
        if index is None or judgments.withdrawn(x.claim, a) or judgments.rejected(x.claim, person, a):
            continue
        found.append((x.spans[index]["readings"][0]["bass"], _ref(x.claim, index, 0)))
    if not found:
        return None
    if len({bass for bass, _ in found}) > 1:
        return "the bass-stem readings disagree, so the machine's own bass is kept"
    return found[0]


def _others(cover: list, excluded: list[dict[str, Any]], judgments: Judgments) -> list[dict[str, Any]]:
    skip = {(r["claim"], r["span"], r.get("reading")) for r in excluded}
    result = []
    for claim, index in cover:
        for number, _ in enumerate(claim["payload"]["spans"][index]["readings"]):
            if (claim["revision_id"], index, number) not in skip:
                result.append(_ref(claim, index, number))
    return result


# ---------------------------------------------------------------------------
# archive.view/lead_sheet_current version 1


def lead_sheet_current(world: list[dict[str, Any]], subject: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Which lead sheet of a Work, and of one recording when the subject
    names one, the viewer reads (decision D4.1). A lead sheet is for the
    recording its `recording` reference names or, without one, for its
    `source` Artifact; one with no `recording` is also read for the Work. By default the viewer's
    chosen person's; or the band's, a lead sheet whose agent is the band
    (ADR 0078 membership names its members). Every other head lead sheet of
    the same Work is listed beside it. Two of the chosen agent's own lead
    sheets for the same subject are a conflict, and the view chooses none."""
    judgments = Judgments(world)
    (kind, agent), = params["by"].items()
    sheets = []
    for claim in _heads(world, LEAD_SHEET):
        works, recordings = _refs(claim, "work"), _refs(claim, "recording")
        if not works or works[0]["id"] != subject["work"] or judgments.withdrawn(claim):
            continue
        if "recording" in subject:
            # A lead sheet is for the recording it names, or, naming none,
            # for its `source`: the audio its timed inputs count.
            if [r["id"] for r in recordings or _refs(claim, "source")] != [subject["recording"]]:
                continue
        elif recordings:
            continue
        sheets.append(claim)
    sheets.sort(key=lambda c: c["revision_id"])
    mine = [c for c in sheets if c.get("agent") == agent]
    answer: dict[str, Any] = {"by": {kind: agent},
                              "others": [{"claim": c["revision_id"], "agent": c.get("agent")}
                                         for c in sheets if c not in mine]}
    if len(mine) == 1:
        answer["chosen"] = {"claim": mine[0]["revision_id"], "agent": agent}
        answer["why"] = f"the only lead sheet the chosen {kind} asserted for this subject"
    elif mine:
        answer["conflict"] = [{"claim": c["revision_id"], "agent": agent} for c in mine]
        answer["why"] = f"the chosen {kind} asserted {len(mine)} lead sheets for this subject; the view chooses none"
    else:
        answer["why"] = f"the chosen {kind} asserted no lead sheet for this subject"
    if kind == "band":
        answer["members"] = sorted(
            ({"member": _refs(m, "member")[0]["id"], "claim": m["revision_id"],
              **{k: m["payload"][k] for k in ("from", "to", "ended") if k in m["payload"]}}
             for m in _heads(world, BAND_MEMBERSHIP)
             if _refs(m, "band") and _refs(m, "band")[0]["id"] == agent and _refs(m, "member")
             and not judgments.withdrawn(m)),
            key=lambda m: (m["member"], m["claim"]))
    return answer


# ---------------------------------------------------------------------------
# Dispatch


HANDLERS: dict[tuple[str, str], Callable[[list, dict, dict], dict]] = {
    (VIEW + "chord_names", "1"): chord_names,
    (VIEW + "chord_corrections", "1"): chord_corrections,
    (VIEW + "chord_differences", "1"): chord_differences,
    (VIEW + "chords_current", "1"): chords_current,
    (VIEW + "lead_sheet_current", "1"): lead_sheet_current,
}


# The views of 0.0.10's other records join the one registry, so the listing
# route and the dispatch still read a single list.
from world import record_views  # noqa: E402

VIEWS.update(record_views.VIEWS)
HANDLERS.update(record_views.HANDLERS)


def run_view(request: Any, world: list[dict[str, Any]], *, caller: str | None = None,
             withheld: int = 0) -> dict[str, Any]:
    """Check a request and answer it from `world`, the records the caller
    may read. `withheld` is how many records the relay kept from the
    caller; an answer states it, so an empty answer is never mistaken for
    nothing being there (INV-24)."""
    checked = check_request(request, caller)
    handler = HANDLERS[(checked["view"], checked["version"])]
    return {**checked, "answer": handler(world, checked["subject"], checked["parameters"]),
            "withheld": withheld}
