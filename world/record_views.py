"""Named views of 0.0.10's records beyond ADR 0082's chord views: a judge's
standing, an object's reach, a circle's readable set, what changed under a
lead sheet, the played order of a form's sections and a meter's bars, a
recording's tune and its hand-offs, and the credits of a recording.

Each is a question World answers by a rule its own module states
(`judgments.standing`, `documents.reach`, `circle.readable_set`,
`lead_sheets.changes_since`, `form.form_order` and `form.bar_order`,
`notes.tune_notes` and `notes.handoffs`, `catalogue.track_credits`); this
module only reads the request and the records, as `world/views.py` does,
and its registry joins that one, so a view is listed exactly when it is
answered. Every answer is computed when asked and never stored (S-18). Each
view is version 1; `world/views.py` says how versions change.

Records are given as `world/views.py` takes them, with `object_id`, `at`,
`supersedes`, and an Entity's `kind` as well. Standard library only.
"""

from __future__ import annotations

import re
from datetime import date
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
    value = params.get("on", date.today().isoformat())
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
        found = circle.readable_set(records, operator, circle.Circle(
            given["destination"], frozenset(given["members"]), given.get("band")),
            lambda object_id: documents.reach(object_id, world, operator, on).furthest)
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
    try:
        notes_tune = notes.tune_notes(claim["payload"], pinned)
    except (ValueError, KeyError) as error:
        raise _fail(ValueError(str(error))) from None
    return {"notes": notes_tune, "handoffs": notes.handoffs(claim["payload"])}


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
}
