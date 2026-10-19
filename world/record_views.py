"""Named views of 0.0.10's records beyond ADR 0082's chord views: a judge's
standing, an object's reach, a circle's readable set, what changed under a
lead sheet, the played order of a form's sections and a meter's bars, a
recording's tune and its hand-offs, the credits of a recording, notes
written on a beat grid, a recording's bass line, and whether an approval
may still publish.

Each is a question World answers by a rule its own module states
(`judgments.standing`, `documents.reach`, `circle.readable_set`,
`lead_sheets.changes_since`, `form.form_order` and `form.bar_order`,
`notes.tune_notes` and `notes.handoffs`, `catalogue.track_credits`,
`notes.written_rhythm`, `circle.may_publish`); this
module only reads the request and the records, as `world/views.py` does,
and its registry joins that one, so a view is listed exactly when it is
answered. Every answer is computed when asked and never stored (S-18). Each
view is version 1 but the written rhythm and the bass line, version 2: their
parameters are the written rhythm rule's of that version
(`notes.RHYTHM_RULES`), and version 1, which no release served, is replaced;
`world/views.py` says how versions change.

Records are given as `world/views.py` takes them, with `object_id`, `at`,
`supersedes`, and an Entity's `kind` as well. Standard library only.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any, Callable

from world import catalogue, circle, documents, form, judgments, lead_sheets, notes
from world.view_requests import ViewError, _bad, _closed, _object_id, _pin

VIEW = "archive.view/"
OPERATOR_INITIALIZATION = ("archive.transition/operator_initialization", "1")
JUDGMENT = {("archive.assessment/judgment", "1"), ("archive.assessment/judgment", "2")}
FORM = ("archive.claim/form", "1")
METER = {("archive.claim/meter_layers", "1"), ("archive.claim/meter_layers", "2")}
MELODY_SPANS = ("archive.claim/melody_spans", "1")
NOTES = ("archive.claim/notes", "1")
TIMED_LYRICS = ("archive.claim/timed_lyrics", "1")
METER_TIMED = {("archive.claim/meter_layers", "1"), ("archive.claim/meter_layers", "2")}
RHYTHM = "2"        # the written rhythm and bass line views' version, and their rule's (`notes.RHYTHM_RULES`)
APPROVALS = {("archive.transition/publication_approval", "1"), ("archive.transition/publication_approval", "2")}
LEAD_SHEET = ("archive.claim/lead_sheet", "1")
TRACKLIST = ("archive.claim/tracklist", "1")
RECORDING_AUDIO = ("archive.claim/recording_audio", "1")
CONTRIBUTION = ("archive.claim/contribution", "1")
DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")


# ---------------------------------------------------------------------------
# Reading the records


def _contract(record: dict[str, Any]) -> tuple[str, str] | None:
    contract = record.get("contract")
    return (contract[0], contract[1]) if contract else None


def _heads(world: list[dict[str, Any]], contracts: set[tuple[str, str]]) -> list[dict[str, Any]]:
    return [r for r in world if _contract(r) in contracts and r.get("head", True)]


def _record(world: list[dict[str, Any]], pin: dict[str, str], contracts: set[tuple[str, str]],
            what: str) -> dict[str, Any]:
    """The pinned revision, which must be a record of one of `contracts`; a
    revision World does not hold (or the caller may not read) is not found."""
    found = next((r for r in world if r.get("revision_id") == pin["revision_id"]), None)
    if found is None or found.get("id", found.get("object_id")) != pin["id"]:
        raise ViewError("view_subject_not_found", f"no record is {pin['revision_id']}")
    if _contract(found) not in contracts:
        raise _bad(f"subject {pin['revision_id']} is not {what}")
    return found


def _view(record: dict[str, Any]) -> dict[str, Any]:
    return {"payload": record.get("payload"), "refs": record.get("refs", []), "revision_id": record["revision_id"]}


def _operator(world: list[dict[str, Any]]) -> str:
    appointed = {ref["id"] for r in world if _contract(r) == OPERATOR_INITIALIZATION
                 for ref in r.get("refs", []) if ref.get("role") == "appoints"}
    if len(appointed) != 1:
        raise ViewError("view_subject_not_found", "the active operator is not among the records World holds")
    return next(iter(appointed))


def _on(params: dict[str, Any]) -> str:
    value = params.get("on", datetime.now(timezone.utc).date().isoformat())  # Door's day: UTC
    if not isinstance(value, str) or not DATE.fullmatch(value):
        raise _bad("parameters on must be a date, YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise _bad("parameters on must be a date, YYYY-MM-DD") from None
    return value


def _index(value: Any, where: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise _bad(f"{where} must be an integer of at least 0")
    return value


def _fail(error: ValueError) -> ViewError:
    """A record a view's rule cannot read: the request is refused, never
    answered by skipping it (INV-24)."""
    return _bad(f"a record the view reads is malformed: {error}")


# ---------------------------------------------------------------------------
# archive.view/standing version 1


def _standing_subject(subject: Any) -> dict[str, Any]:
    _closed(subject, {"claim"}, set(), "subject")
    return {"claim": _pin(subject["claim"], "subject claim")}


def _standing_parameters(params: dict[str, Any], caller: str | None) -> dict[str, Any]:
    _closed(params, set(), {"judge", "at", "reading"}, "parameters")
    if "judge" in params:
        judge = _object_id(params["judge"], "parameters judge")
    elif caller is not None:
        judge = _object_id(caller, "parameters judge (the caller's person)")
    else:
        raise _bad("parameters judge is required when the caller is not bound to a person")
    answer = {"judge": judge, "at": None, "reading": None}
    for name in ("at", "reading"):
        if params.get(name) is not None:
            answer[name] = _index(params[name], f"parameters {name}")
    if answer["reading"] is not None and answer["at"] is None:
        raise _bad("parameters reading needs at, the position whose reading is judged")
    return answer


def standing(world: list[dict[str, Any]], subject: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """What one judge holds of one claim revision (ADR 0081): the narrowest
    applicable judgment by that judge stands; equally narrow ones that
    disagree are a conflict, and no verdict."""
    pin = subject["claim"]
    claim = next((r for r in world if r.get("revision_id") == pin["revision_id"]), None)
    if claim is None or claim.get("shape") not in ("claim", None):
        raise ViewError("view_subject_not_found", f"no claim is {pin['revision_id']}")
    found = [{"payload": r.get("payload"), "refs": r.get("refs", []), "revision_id": r["revision_id"],
              "agent": r.get("agent"), "contracts": [{"id": _contract(r)[0], "version": _contract(r)[1]}]}
             for r in _heads(world, JUDGMENT)]
    judged = {k: claim[k] for k in ("payload", "refs", "revision_id", "agent") if k in claim}
    try:
        held = judgments.standing(judged, found, params["judge"], params["at"], params["reading"])
    except ValueError as error:
        raise _fail(error) from None
    return {"verdict": held.verdict, "conflict": held.conflict, "judgments": list(held.judgments), "why": held.why}


# ---------------------------------------------------------------------------
# archive.view/reach version 1


def _object_subject(subject: Any) -> dict[str, Any]:
    _closed(subject, {"object"}, set(), "subject")
    return {"object": _object_id(subject["object"], "subject object")}


def _on_parameters(params: dict[str, Any], caller: str | None) -> dict[str, Any]:
    _closed(params, set(), {"on"}, "parameters")
    return {"on": _on(params)}


def reach(world: list[dict[str, Any]], subject: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """How far one object may reach under the sensitivity and rights claims
    on it and everything it pins (ADR 0085), on the date `on`. Rights never
    limit the private circle; the strictest limit wins."""
    if not any(r.get("object_id") == subject["object"] for r in world):
        raise ViewError("view_subject_not_found", f"no record is of {subject['object']}")
    try:
        found = documents.reach(subject["object"], world, _operator(world), date.fromisoformat(params["on"]))
    except (ValueError, KeyError) as error:
        raise _fail(ValueError(str(error))) from None
    return {"labelled": found.labelled, "rights": found.rights, "furthest": found.furthest, "why": list(found.why)}


# ---------------------------------------------------------------------------
# archive.view/readable_set version 1


def _circle_subject(subject: Any) -> dict[str, Any]:
    _closed(subject, {"circle"}, set(), "subject")
    given = _closed(subject["circle"], {"destination", "members"}, {"band"}, "subject circle")
    if not isinstance(given["destination"], str) or not given["destination"]:
        raise _bad("subject circle destination must be text")
    members = given["members"]
    if not isinstance(members, list) or len(set(map(str, members))) != len(members):
        raise _bad("subject circle members must be distinct object identifiers")
    checked = {"destination": given["destination"],
               "members": sorted(_object_id(m, "subject circle member") for m in members)}
    if "band" in given:
        checked["band"] = _object_id(given["band"], "subject circle band")
    return {"circle": checked}


def _reader_parameters(params: dict[str, Any], caller: str | None) -> dict[str, Any]:
    _closed(params, set(), {"reader", "on"}, "parameters")
    reader = params.get("reader")
    return {"reader": None if reader is None else _object_id(reader, "parameters reader"), "on": _on(params)}


def readable_set(world: list[dict[str, Any]], subject: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """What a circle may read (ADR 0088), and why each revision is in it;
    with a `reader`, what that one person reads: the circle's set and their
    own records."""
    operator = _operator(world)
    given = subject["circle"]
    if operator in given["members"]:
        raise _bad("the operator is never a circle member")
    records = {r["revision_id"]: r for r in world}
    on = date.fromisoformat(params["on"])
    try:
        reacher = documents.Reacher(world, operator, on)
        found = circle.readable_set(records, operator, circle.Circle(
            given["destination"], frozenset(given["members"]), given.get("band")),
            lambda object_id: reacher.reach(object_id).furthest)
    except (ValueError, KeyError) as error:
        raise _fail(ValueError(str(error))) from None
    answer = {"revisions": sorted(found.revisions), "withheld": sorted(found.withheld),
              "why": {rid: found.why[rid] for rid in sorted(found.why)}}
    if params["reader"] is not None:
        if params["reader"] != operator and params["reader"] not in given["members"]:
            raise _bad("parameters reader must be the operator or a member of the circle")
        answer["readable"] = sorted(circle.readable_by(params["reader"], operator, found, records))
    return answer


# ---------------------------------------------------------------------------
# archive.view/lead_sheet_changes version 1


def _lead_sheet_subject(subject: Any) -> dict[str, Any]:
    _closed(subject, {"lead_sheet"}, set(), "subject")
    return {"lead_sheet": _pin(subject["lead_sheet"], "subject lead_sheet")}


def _no_parameters(params: dict[str, Any], caller: str | None) -> dict[str, Any]:
    _closed(params, set(), set(), "parameters")
    return {}


def lead_sheet_changes(world: list[dict[str, Any]], subject: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """What happened to a lead sheet's inputs after it pinned them (ADR
    0084): a later revision, a correction, or a judgment. The lead sheet
    keeps its pins; only its asserter's new revision moves them."""
    sheet = _record(world, subject["lead_sheet"], {LEAD_SHEET}, "a lead sheet")
    records = [{"revision_id": r["revision_id"], "shape": r.get("shape"), "refs": r.get("refs", []),
                "payload": r.get("payload"), "agent": r.get("agent"),
                "supersedes": [s["revision_id"] for s in r.get("supersedes", []) if isinstance(s, dict)]}
               for r in world]
    changes = lead_sheets.changes_since(_view(sheet), records)
    return {"changes": [{"role": c.role, "pinned": c.pinned, "kind": c.kind, "by": c.by, "verdict": c.verdict}
                        for c in changes]}


# ---------------------------------------------------------------------------
# archive.view/played_sections and archive.view/played_bars version 1


def _form_subject(subject: Any) -> dict[str, Any]:
    _closed(subject, {"form"}, set(), "subject")
    return {"form": _pin(subject["form"], "subject form")}


def played_sections(world: list[dict[str, Any]], subject: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """A form's sections in the order played (ADR 0083): repeats unrolled,
    each pass taking its ending, each jump taken once."""
    claim = _record(world, subject["form"], {FORM}, "a form")
    try:
        form.check_form(_view(claim))
        order = form.form_order(claim["payload"])
    except ValueError as error:
        raise _fail(error) from None
    sections = claim["payload"]["sections"]
    return {"played": [{"section": index, "label": sections[index]["label"],
                        **({"kind": sections[index]["kind"]} if "kind" in sections[index] else {})}
                       for index in order]}


def _meter_subject(subject: Any) -> dict[str, Any]:
    _closed(subject, {"meter"}, set(), "subject")
    return {"meter": _pin(subject["meter"], "subject meter")}


def _layer_parameters(params: dict[str, Any], caller: str | None) -> dict[str, Any]:
    _closed(params, set(), {"layer"}, "parameters")
    return {"layer": _index(params.get("layer", 0), "parameters layer")}


def played_bars(world: list[dict[str, Any]], subject: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """A written meter layer's bars in the order played (ADR 0083): the
    sections of the form the meter pins in their played order, each with its
    bars in written order; with no form, as written."""
    meter = _record(world, subject["meter"], METER, "a meter layers claim")
    layers = meter["payload"].get("layers", [])
    if params["layer"] >= len(layers) or "bars" not in layers[params["layer"]]:
        raise _bad("parameters layer must name a layer of bars")
    pinned = [r for r in meter.get("refs", []) if r.get("role") == "form"]
    form_payload = None
    if pinned:
        form_payload = _record(world, {"id": pinned[0]["id"], "revision_id": pinned[0]["revision_id"]},
                               {FORM}, "a form")["payload"]
    try:
        order = form.bar_order(layers[params["layer"]], form_payload)
    except ValueError as error:
        raise _fail(error) from None
    bars = layers[params["layer"]]["bars"]
    return {"form": pinned[0]["revision_id"] if pinned else None,
            "played": [{"bar": index, **{k: bars[index][k] for k in ("section", "numerator", "denominator")
                                         if k in bars[index]}} for index in order]}


# ---------------------------------------------------------------------------
# archive.view/tune version 1


def _tune_subject(subject: Any) -> dict[str, Any]:
    _closed(subject, {"melody_spans"}, set(), "subject")
    return {"melody_spans": _pin(subject["melody_spans"], "subject melody_spans")}


def tune(world: list[dict[str, Any]], subject: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """A recording's tune (ADR 0086): in each span, the notes of the notes
    claim it selects, and where the tune passes from one part to another."""
    claim = _record(world, subject["melody_spans"], {MELODY_SPANS}, "a melody spans claim")
    pinned = {}
    for ref in claim.get("refs", []):
        if ref.get("role") == "notes":
            pinned[ref["revision_id"]] = _view(_record(world, {"id": ref["id"], "revision_id": ref["revision_id"]},
                                                       {NOTES}, "a notes claim"))
    left_out: list[dict[str, Any]] = []
    try:
        notes_tune = notes.tune_notes(claim["payload"], pinned, by_person=_is_person(world, claim.get("agent")),
                                      left_out=left_out)
    except (ValueError, KeyError) as error:
        raise _fail(ValueError(str(error))) from None
    return {"notes": notes_tune, "handoffs": notes.handoffs(claim["payload"]), "left_out": left_out}


def _is_person(world: list[dict[str, Any]], agent: Any) -> bool:
    """Whether an agent is a person Entity World holds; a tool's Package URL
    or an unknown agent is not."""
    return any(r.get("object_id") == agent and r.get("shape") == "entity" and r.get("kind") == "person"
               for r in world)


# ---------------------------------------------------------------------------
# archive.view/written_rhythm version 2


def _rhythm_subject(subject: Any) -> dict[str, Any]:
    _closed(subject, set(), {"notes", "melody_spans"}, "subject")
    if len(subject) != 1:
        raise _bad("subject must be exactly one of {notes} or {melody_spans}")
    (name, value), = subject.items()
    return {name: _pin(value, f"subject {name}")}


SWITCHES = ("fold_octaves", "fill_syllables")


def _switches(params: dict[str, Any]) -> dict[str, bool | None]:
    """The written rhythm rule's switches a reader sets: true, false, or
    absent (null) for the line's default (`notes.RHYTHM_RULES`)."""
    for name in SWITCHES:
        if params.get(name) is not None and not isinstance(params[name], bool):
            raise _bad(f"parameters {name} must be true or false")
    return {name: params.get(name) for name in SWITCHES}


def _rhythm_parameters(params: dict[str, Any], caller: str | None) -> dict[str, Any]:
    _closed(params, set(), {"meter", "layer", "lyrics", *SWITCHES}, "parameters")
    lyrics = params.get("lyrics", [])
    if not isinstance(lyrics, list) or len(lyrics) > 8:
        raise _bad("parameters lyrics must be a list of at most 8 timed lyrics claims")
    return {"meter": None if params.get("meter") is None else _pin(params["meter"], "parameters meter"),
            "layer": _index(params.get("layer", 0), "parameters layer"),
            "lyrics": [_pin(pin, f"parameters lyrics {n}") for n, pin in enumerate(lyrics)],
            **_switches(params)}


def _machine(world: list[dict[str, Any]], claim: dict[str, Any]) -> bool:
    """Whether a notes claim is a machine's: its agent is no person, and it
    was not read from a document. Only a machine's notes are folded or
    filled by the written rhythm view."""
    return not _is_person(world, claim.get("agent")) and \
        not any(ref.get("role") == "document" for ref in claim.get("refs", []))


def _write(world: list[dict[str, Any]], sounded: list[dict[str, Any]], sample_rate: int,
           params: dict[str, Any], *, line: str, claims: list[str], machine_claims: set[str]) -> dict[str, Any]:
    """Sounded notes written on the named meter's grid, or why nothing is.
    `claims` is each note's notes claim revision; `machine_claims` those
    that are a machine's."""
    switches = {name: params.get(name) for name in SWITCHES}
    rule = notes.RHYTHM_RULES[RHYTHM].describe(notes.RHYTHM_RULES[RHYTHM].switched(line, **switches))
    if params["meter"] is None:
        return {"rule": rule, "written": [], "left_out": len(sounded),
                "why": "no meter claim was named, so there is no beat grid to write on"}
    meter = _record(world, params["meter"], METER_TIMED, "a meter claim")
    lines = [_record(world, pin, {TIMED_LYRICS}, "a timed lyrics claim")["payload"]["lines"]
             for pin in params["lyrics"]]
    try:
        return notes.written_rhythm(sounded, meter["payload"], sample_rate=sample_rate, layer=params["layer"],
                                    lyrics=lines, version=RHYTHM, line=line, claims=claims,
                                    machine_claims=machine_claims, **switches)
    except (ValueError, KeyError, TypeError, IndexError) as error:
        raise _fail(ValueError(str(error))) from None


def written_rhythm(world: list[dict[str, Any]], subject: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Sounded notes written on a meter's beat grid as a musician writes them
    (ADR 0086, amended 2026-10-19; `notes.written_rhythm`): a notes claim's
    notes, or a recording's tune. Onsets are placed by timing error plus a
    notation cost, each note fills to the next onset, a rest is written
    only at a lyric line's end or a breath, staccato only for a clearly
    detached note, and every syllable of the named timed lyrics is sung on
    a note."""
    if "notes" in subject:
        claim = _record(world, subject["notes"], {NOTES}, "a notes claim")
        sounded, rate = claim["payload"]["notes"], claim["payload"]["sample_rate"]
        left: list[dict[str, Any]] = []
        claims = [claim["revision_id"]] * len(sounded)
        machine_claims = {claim["revision_id"]} if _machine(world, claim) else set()
        line = "bass" if claim["payload"].get("bass") is True else "vocal"
    else:
        spans = _record(world, subject["melody_spans"], {MELODY_SPANS}, "a melody spans claim")
        played = tune(world, {"melody_spans": subject["melody_spans"]}, {})
        sounded, left, rate = played["notes"], played["left_out"], spans["payload"]["sample_rate"]
        claims = [note["from"] for note in sounded]
        machine_claims = {ref["revision_id"] for ref in spans.get("refs", []) if ref.get("role") == "notes"
                          and _machine(world, _record(world, {"id": ref["id"], "revision_id": ref["revision_id"]},
                                                      {NOTES}, "a notes claim"))}
        line = "vocal"
    answer = _write(world, sounded, rate, params, line=line, claims=claims, machine_claims=machine_claims)
    if left:
        answer["tune_left_out"] = left
    return answer


# ---------------------------------------------------------------------------
# archive.view/bass_line version 2


def _bass_subject(subject: Any) -> dict[str, Any]:
    _closed(subject, {"source"}, set(), "subject")
    return {"source": _object_id(subject["source"], "subject source")}


def _bass_parameters(params: dict[str, Any], caller: str | None) -> dict[str, Any]:
    _closed(params, set(), {"meter", "layer", "fold_octaves"}, "parameters")
    return {"meter": None if params.get("meter") is None else _pin(params["meter"], "parameters meter"),
            "layer": _index(params.get("layer", 0), "parameters layer"), "lyrics": [],
            **_switches(params)}


def bass_line(world: list[dict[str, Any]], subject: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """A recording's bass line, a line of its own (ADR 0086, amended
    2026-10-19): every head notes claim about the recording that says
    `bass`, each written by the written rhythm view's rule for a bass clef
    staff, never merged into a melody. No lyrics: its phrases end at its
    silences. Nothing is chosen between two asserters' bass lines; both are
    listed."""
    if not any(r.get("object_id") == subject["source"] for r in world):
        raise ViewError("view_subject_not_found", f"no record is of {subject['source']}")
    lines = []
    for claim in sorted(_heads(world, {NOTES}), key=lambda c: c["revision_id"]):
        payload = claim.get("payload") or {}
        sources = [ref["id"] for ref in claim.get("refs", []) if ref.get("role") == "source"]
        if payload.get("bass") is not True or sources != [subject["source"]]:
            continue
        written = _write(world, payload["notes"], payload["sample_rate"], params, line="bass",
                         claims=[claim["revision_id"]] * len(payload["notes"]),
                         machine_claims={claim["revision_id"]} if _machine(world, claim) else set())
        written.pop("clef_basis", None)          # the staff is the bass clef's, never suggested
        lines.append({"claim": claim["revision_id"], "agent": claim.get("agent"), "part": payload.get("part"),
                      **({"range": payload["range"]} if "range" in payload else {}),
                      **written, "clef": "bass"})
    return {"lines": lines}


# ---------------------------------------------------------------------------
# archive.view/may_publish version 1


def _approval_subject(subject: Any) -> dict[str, Any]:
    _closed(subject, {"approval"}, set(), "subject")
    return {"approval": _pin(subject["approval"], "subject approval")}


def may_publish(world: list[dict[str, Any]], subject: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Whether a publication approval still permits publication on the date
    `on` (ADR 0085, amended 2026-10-19), and everything that blocks it. A
    publisher calls it when it publishes; 0.0.10 ships none."""
    _record(world, subject["approval"], APPROVALS, "a publication approval")
    records = {r["revision_id"]: r for r in world}
    try:
        return circle.may_publish(subject["approval"]["revision_id"], records, _operator(world),
                                  date.fromisoformat(params["on"]))
    except (ValueError, KeyError, TypeError) as error:
        raise _fail(ValueError(str(error))) from None


# ---------------------------------------------------------------------------
# archive.view/track_credits version 1


def _recording_subject(subject: Any) -> dict[str, Any]:
    _closed(subject, {"recording"}, set(), "subject")
    return {"recording": _object_id(subject["recording"], "subject recording")}


def track_credits(world: list[dict[str, Any]], subject: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """The contributions that apply to one recording Entity (ADR 0087), each
    with why: named on it, on audio that is it, or on a release whose
    tracklist places it. Nothing is ranked by asserter."""
    recording = subject["recording"]
    if not any(r.get("object_id") == recording and r.get("shape") == "entity" for r in world):
        raise ViewError("view_subject_not_found", f"no Entity is {recording}")
    try:
        found = catalogue.track_credits(recording, _heads(world, {TRACKLIST}), _heads(world, {CONTRIBUTION}),
                                        _heads(world, {RECORDING_AUDIO}))
    except (ValueError, KeyError) as error:
        raise _fail(ValueError(str(error))) from None
    return {"credits": [{"claim": c.revision_id, "scope": c.scope, "via": c.via, "why": c.why,
                         "refined_by": list(c.refined_by)} for c in found]}


# ---------------------------------------------------------------------------
# Registry, joined to world/views.py's


VIEWS: dict[tuple[str, str], dict[str, Any]] = {
    (VIEW + "standing", "1"): {
        "question": "What does this judge hold of this claim revision, here?",
        "subject": _standing_subject, "parameters": _standing_parameters},
    (VIEW + "reach", "1"): {
        "question": "How far may this object reach, and why?",
        "subject": _object_subject, "parameters": _on_parameters},
    (VIEW + "readable_set", "1"): {
        "question": "What may this circle read, and why?",
        "subject": _circle_subject, "parameters": _reader_parameters},
    (VIEW + "lead_sheet_changes", "1"): {
        "question": "What changed among this lead sheet's inputs since it pinned them?",
        "subject": _lead_sheet_subject, "parameters": _no_parameters},
    (VIEW + "played_sections", "1"): {
        "question": "In what order are this form's sections played?",
        "subject": _form_subject, "parameters": _no_parameters},
    (VIEW + "played_bars", "1"): {
        "question": "In what order are this meter's written bars played?",
        "subject": _meter_subject, "parameters": _layer_parameters},
    (VIEW + "tune", "1"): {
        "question": "What is this recording's tune, and where does it pass between parts?",
        "subject": _tune_subject, "parameters": _no_parameters},
    (VIEW + "track_credits", "1"): {
        "question": "Which credits apply to this recording, and why?",
        "subject": _recording_subject, "parameters": _no_parameters},
    (VIEW + "written_rhythm", RHYTHM): {
        "question": "How are these sounded notes written on this meter's beats?",
        "subject": _rhythm_subject, "parameters": _rhythm_parameters},
    (VIEW + "bass_line", RHYTHM): {
        "question": "What is this recording's bass line, written on its beats?",
        "subject": _bass_subject, "parameters": _bass_parameters},
    (VIEW + "may_publish", "1"): {
        "question": "May this publication approval still publish, on this date?",
        "subject": _approval_subject, "parameters": _on_parameters},
}

HANDLERS: dict[tuple[str, str], Callable[[list, dict, dict], dict]] = {
    (VIEW + "standing", "1"): standing,
    (VIEW + "reach", "1"): reach,
    (VIEW + "readable_set", "1"): readable_set,
    (VIEW + "lead_sheet_changes", "1"): lead_sheet_changes,
    (VIEW + "played_sections", "1"): played_sections,
    (VIEW + "played_bars", "1"): played_bars,
    (VIEW + "tune", "1"): tune,
    (VIEW + "track_credits", "1"): track_credits,
    (VIEW + "written_rhythm", RHYTHM): written_rhythm,
    (VIEW + "bass_line", RHYTHM): bass_line,
    (VIEW + "may_publish", "1"): may_publish,
}
