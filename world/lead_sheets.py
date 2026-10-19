"""Lead sheets (ADR 0084): a person's or a band's claim that pins the exact
chord, meter, form, lyric, and melody claim revisions it chose for a Work,
optionally for one recording.

`archive.claim/lead_sheet` version 1 is decided by ADR 0084 for
specification 0.0.10. This module holds its rules as World will re-check them
when it projects such a claim, so a client can check a proposal before Door
does; Door remains the authority (INV-2). It also holds three computed views:
what changed among a lead sheet's inputs since it pinned them, which pinned
revisions a render discloses, and the key a render is filed under. It uses
only the standard library.

A lead sheet asserts no reading of the music. It states a choice: these
revisions, together, are this asserter's lead sheet. Its inputs stay pinned
when they are later revised, corrected, or judged; a new lead sheet revision
follows only when its asserter makes one. Renders (PDF, MusicXML, LilyPond,
JAMS) are regenerable views keyed by the lead sheet revision; one is admitted
as an Artifact only when it is shared.

Several input contracts are defined by ADRs drafted beside this one (0079,
0080, 0083, 0085, 0086). Their identifiers below are this record's
assumptions and are marked; the integrator reconciles them with those records
before a release installs any of them.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

LEAD_SHEET = ("archive.claim/lead_sheet", "1")

# Contracts installed by specification 0.0.9 (D-17).
CHORD_STACKS = ("archive.claim/chord_stacks", "1")
CHORD_SEQUENCE = ("archive.claim/chord_sequence", "1")
METER_LAYERS = ("archive.claim/meter_layers", "1")
CONTRIBUTION = ("archive.claim/contribution", "1")
BAND_MEMBERSHIP = ("archive.claim/band_membership", "1")
AUDIO_SELECTION = ("archive.claim/audio_selection", "2")
SONG_OCCURRENCE = ("archive.claim/song_occurrence", "2")
JUDGMENT = ("archive.assessment/judgment", "1")

# The contracts of the other 0.0.10 records, by the names those records define.
from world.documents import TIMED_LYRICS                     # noqa: E402  ADR 0085
from world.form import FORM, FORM_SPANS, METER_LAYERS_V2     # noqa: E402  ADR 0083 (meter v2 shared with 0079)
from world.harmony import CHORD_SEQUENCE_V2, CHORD_STACKS_V2  # noqa: E402  ADR 0079
from world.notes import MELODY, MELODY_SPANS                 # noqa: E402  ADR 0086
from world.tonality import KEY_REGIONS, KEY_SEQUENCE         # noqa: E402  ADR 0080

OBJECT_ID = re.compile(r"urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
REVISION_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
INSTRUMENT = re.compile(r"https://musicbrainz\.org/instrument/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
RENDERER = re.compile(r"[a-z][a-z0-9._-]{0,63}@[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}\Z")
MAX_LABEL = 200
MAX_PART_NAME = 100
MAX_PARTS = 16
MAX_STRINGS = 12
MAX_CAPO = 24
RENDER_FORMATS = ("pdf", "musicxml", "lilypond", "jams")

# Reference roles: (minimum, maximum) and the contracts or shapes each pins.
# The order of references within one role is meaningful: for `chords`,
# `meter`, `form`, `key`, `lyric_timing`, and `melody`, an earlier reference
# is shown where two cover the same music (the asserter's own precedence).
ROLES: dict[str, tuple[int, int]] = {
    "work": (1, 1),          # Entity, kind musical_work: what the lead sheet is of
    "source": (0, 1),        # Artifact: the recording's mix, for a recording-scoped lead sheet
    "recording": (0, 1),     # recording Entity, a take (selection or occurrence v2), or Artifact
    "chords": (0, 16),
    "meter": (0, 4),
    "form": (0, 4),
    "key": (0, 4),
    "lyrics": (0, 1),        # Artifact: the lyric document; its words are never copied
    "lyric_timing": (0, 4),
    "melody": (0, 4),
    "credits": (0, 32),      # contribution v1, scope work, about the same Work
    "document": (0, 8),      # Artifacts a part's tuning or capo was read from
    "derived_from": (0, 8),  # lead sheet v1 revisions this one started from
    "band": (0, 1),          # organisation Entity: the band that adopted this lead sheet
    "adopted_by": (0, 16),   # band_membership v1: the members who adopted it
}
INPUT_CONTRACTS: dict[str, set[tuple[str, str]]] = {
    "chords": {CHORD_STACKS, CHORD_SEQUENCE, CHORD_STACKS_V2, CHORD_SEQUENCE_V2},
    "meter": {METER_LAYERS, METER_LAYERS_V2},
    "form": {FORM, FORM_SPANS},
    "key": {KEY_SEQUENCE, KEY_REGIONS},
    "lyric_timing": {TIMED_LYRICS},
    "melody": {MELODY, MELODY_SPANS},
}
# Every role a render reads, so every revision a render discloses.
RENDERED_ROLES = ("work", "source", "recording", "chords", "meter", "form", "key",
                  "lyrics", "lyric_timing", "melody", "credits")


class LeadSheetError(ValueError):
    """A lead sheet claim that breaks a rule of ADR 0084."""


# ---------------------------------------------------------------------------
# Shared pieces (as in world/harmony.py and world/contributions.py)


def _closed(value: Any, required: set[str], optional: set[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LeadSheetError(f"{where} must be an object")
    missing = required - set(value)
    extra = set(value) - required - optional
    if missing or extra:
        raise LeadSheetError(f"{where} has missing {sorted(missing)} or unknown {sorted(extra)} fields")
    return value


def _int(value: Any, where: str, low: int, high: int | None = None) -> int:
    # bool is an int in Python; a JSON true is not a number.
    if not isinstance(value, int) or isinstance(value, bool) or value < low or (high is not None and value > high):
        bound = f"{low}.." + ("" if high is None else str(high))
        raise LeadSheetError(f"{where} must be an integer in {bound}")
    return value


def _text(value: Any, where: str, maximum: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise LeadSheetError(f"{where} must be text of 1 to {maximum} characters")
    return value


def _list(value: Any, where: str, low: int, high: int) -> list[Any]:
    if not isinstance(value, list) or not low <= len(value) <= high:
        raise LeadSheetError(f"{where} must be a list of {low} to {high} items")
    return value


def _refs_by_role(refs: Any, where: str) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(refs, list):
        raise LeadSheetError(f"{where} refs must be a list")
    by_role: dict[str, list[dict[str, Any]]] = {role: [] for role in ROLES}
    for index, ref in enumerate(refs):
        _closed(ref, {"role", "id", "revision_id"}, set(), f"{where} ref {index}")
        if ref["role"] not in ROLES:
            raise LeadSheetError(f"{where} ref {index} has role {ref['role']!r}, which a lead sheet cannot have")
        if not isinstance(ref["id"], str) or not OBJECT_ID.fullmatch(ref["id"]) \
                or not isinstance(ref["revision_id"], str) or not REVISION_ID.fullmatch(ref["revision_id"]):
            raise LeadSheetError(f"{where} ref {index} must pin an object and an exact revision")
        by_role[ref["role"]].append(ref)
    for role, (low, high) in ROLES.items():
        if not low <= len(by_role[role]) <= high:
            raise LeadSheetError(f"{where} needs {low} to {high} '{role}' references")
    pinned = [ref["revision_id"] for ref in refs]
    if len(set(pinned)) != len(pinned):
        raise LeadSheetError(f"{where} pins one revision twice")
    return by_role


def _target(targets: dict[str, dict[str, Any]], ref: dict[str, Any], where: str) -> dict[str, Any]:
    """What a reference pins, as Door reads it from the committed revision:
    `shape`, an Entity's `kind`, a Claim's `contract`, `payload`, and `refs`."""
    target = targets.get(ref["revision_id"])
    if target is None:
        raise LeadSheetError(f"{where} pins {ref['revision_id']}, which is not a committed revision")
    if target.get("id", ref["id"]) != ref["id"]:
        raise LeadSheetError(f"{where} pins a revision of another object")
    return target


def _entity(targets, ref, kinds: set[str], where: str) -> dict[str, Any]:
    target = _target(targets, ref, where)
    if target.get("shape") != "entity" or target.get("kind") not in kinds:
        raise LeadSheetError(f"{where} must pin an Entity of kind {' or '.join(sorted(kinds))}")
    return target


def _claim(targets, ref, contracts: set[tuple[str, str]], where: str) -> dict[str, Any]:
    target = _target(targets, ref, where)
    if target.get("shape") != "claim" or tuple(target.get("contract", ())) not in contracts:
        names = " or ".join(f"{c} version {v}" for c, v in sorted(contracts))
        raise LeadSheetError(f"{where} must pin a Claim of {names}")
    return target


def _artifact(targets, ref, where: str) -> None:
    if _target(targets, ref, where).get("shape") != "artifact":
        raise LeadSheetError(f"{where} must pin an Artifact")


def _target_refs(target: dict[str, Any], role: str) -> list[dict[str, Any]]:
    return [r for r in target.get("refs", []) if isinstance(r, dict) and r.get("role") == role]


# ---------------------------------------------------------------------------
# The lead sheet claim


def check_lead_sheet(claim: dict[str, Any], targets: dict[str, dict[str, Any]],
                     agents: dict[str, str]) -> dict[str, Any]:
    """Check an `archive.claim/lead_sheet` version 1 claim against every rule
    of ADR 0084. `targets` maps each pinned revision ID to what it is (see
    `_target`); `agents` maps an agent's object URN to its Entity kind.
    Returns the payload; raises LeadSheetError."""
    where = "lead sheet"
    record = _closed(claim, {"payload", "refs", "agent"}, {"revision_id"}, where)
    payload = _closed(record["payload"], set(), {"label", "parts"}, f"{where} payload")
    by_role = _refs_by_role(record["refs"], where)

    # Who: a person, or a band that adopted it (D6.1, D4.1). Never a machine.
    agent = record["agent"]
    kind = agents.get(agent) if isinstance(agent, str) else None
    if kind not in ("person", "organisation"):
        raise LeadSheetError(f"{where} is asserted by a person or a band, never by software, a model, or a process")
    _check_adoption(by_role, targets, agent, kind, where)

    # Of what: a Work, optionally one recording.
    work = by_role["work"][0]
    _entity(targets, work, {"musical_work"}, f"{where} work")
    source = by_role["source"][0] if by_role["source"] else None
    if source:
        _artifact(targets, source, f"{where} source")
    if by_role["recording"]:
        if source is None:
            raise LeadSheetError(f"{where} names a recording without the source its timed inputs count")
        _check_recording(targets, by_role["recording"][0], f"{where} recording")

    # What it chose.
    if not by_role["chords"] and not by_role["melody"]:
        raise LeadSheetError(f"{where} pins at least one chord or melody claim")
    for role, contracts in INPUT_CONTRACTS.items():
        for index, ref in enumerate(by_role[role]):
            target = _claim(targets, ref, contracts, f"{where} {role} {index}")
            _check_input_subject(target, work, source, f"{where} {role} {index}")
    if by_role["lyrics"]:
        _artifact(targets, by_role["lyrics"][0], f"{where} lyrics")
    for index, ref in enumerate(by_role["lyric_timing"]):
        if not by_role["lyrics"]:
            raise LeadSheetError(f"{where} pins lyric timing without the lyric document it places")
        target = targets[ref["revision_id"]]
        lyric = by_role["lyrics"][0]
        placed = [(r.get("id"), r.get("revision_id")) for r in _target_refs(target, "document")]
        if placed != [(lyric["id"], lyric["revision_id"])]:
            raise LeadSheetError(f"{where} lyric_timing {index} must place exactly the lyric document pinned")
    for index, ref in enumerate(by_role["credits"]):
        target = _claim(targets, ref, {CONTRIBUTION}, f"{where} credits {index}")
        payload_scope = target.get("payload", {}).get("scope") if isinstance(target.get("payload"), dict) else None
        works = _target_refs(target, "work")
        if payload_scope != "work" or len(works) != 1 or works[0].get("id") != work["id"]:
            raise LeadSheetError(f"{where} credits {index} must be a work-scope contribution about the same Work")
    for index, ref in enumerate(by_role["derived_from"]):
        target = _claim(targets, ref, {LEAD_SHEET}, f"{where} derived_from {index}")
        works = _target_refs(target, "work")
        if len(works) != 1 or works[0].get("id") != work["id"]:
            raise LeadSheetError(f"{where} derived_from {index} is a lead sheet of another Work")
    for index, ref in enumerate(by_role["document"]):
        _artifact(targets, ref, f"{where} document {index}")

    # Its parts.
    if "label" in payload:
        _text(payload["label"], f"{where} label", MAX_LABEL)
    if "parts" in payload:
        _check_parts(payload["parts"], len(by_role["document"]), where)
    return payload


def _check_adoption(by_role, targets, agent: str, kind: str, where: str) -> None:
    """A band's lead sheet pins the band (its agent) and the memberships of
    the members who adopted it; a person's pins neither."""
    if kind == "person":
        if by_role["band"] or by_role["adopted_by"]:
            raise LeadSheetError(f"{where} by a person names no band and no adopting members")
        return
    if not by_role["band"] or not by_role["adopted_by"]:
        raise LeadSheetError(f"{where} by a band pins the band and the memberships of the members who adopted it")
    band = by_role["band"][0]
    _entity(targets, band, {"organisation"}, f"{where} band")
    if band["id"] != agent:
        raise LeadSheetError(f"{where} by a band names that band as its agent")
    members = []
    for index, ref in enumerate(by_role["adopted_by"]):
        target = _claim(targets, ref, {BAND_MEMBERSHIP}, f"{where} adopted_by {index}")
        bands = _target_refs(target, "band")
        if len(bands) != 1 or bands[0].get("id") != band["id"]:
            raise LeadSheetError(f"{where} adopted_by {index} is a membership of another band")
        members.extend(r.get("id") for r in _target_refs(target, "member"))
    if len(set(members)) != len(members):
        raise LeadSheetError(f"{where} names one member's adoption twice")


def _check_recording(targets, ref, where: str) -> None:
    """As ADR 0078: a recording Entity, a take in the archive (an audio
    selection or song occurrence, version 2), or a whole Artifact."""
    target = _target(targets, ref, where)
    shape = target.get("shape")
    if shape == "entity" and target.get("kind") == "recording":
        return
    if shape == "claim" and tuple(target.get("contract", ())) in {AUDIO_SELECTION, SONG_OCCURRENCE}:
        return
    if shape == "artifact":
        return
    raise LeadSheetError(f"{where} must pin a recording Entity, an audio selection or song occurrence "
                         f"version 2, or an Artifact")


def _check_input_subject(target: dict[str, Any], work: dict[str, Any], source: dict[str, Any] | None,
                         where: str) -> None:
    """A timed input (one that pins a `source`) counts the lead sheet's own
    source revision; an input that pins a `work` is about the lead sheet's
    Work, at any revision of it."""
    sources = _target_refs(target, "source")
    if sources:
        if source is None:
            raise LeadSheetError(f"{where} is timed on a recording; a lead sheet of the Work alone pins "
                                 f"only untimed inputs")
        if any((r.get("id"), r.get("revision_id")) != (source["id"], source["revision_id"]) for r in sources):
            raise LeadSheetError(f"{where} counts the samples of another source than the lead sheet's")
    if any(r.get("id") != work["id"] for r in _target_refs(target, "work")):
        raise LeadSheetError(f"{where} is about another Work")


def _check_parts(value: Any, documents: int, where: str) -> None:
    parts = _list(value, f"{where} parts", 1, MAX_PARTS)
    names = []
    for index, part in enumerate(parts):
        at = f"{where} part {index}"
        _closed(part, {"name"}, {"instrument", "tuning", "capo", "document"}, at)
        names.append(_text(part["name"], f"{at} name", MAX_PART_NAME))
        if "instrument" in part:
            if not isinstance(part["instrument"], str) or not INSTRUMENT.fullmatch(part["instrument"]):
                raise LeadSheetError(f"{at} instrument must be a https://musicbrainz.org/instrument/<MBID> URI")
        if "tuning" in part:
            strings = _list(part["tuning"], f"{at} tuning", 1, MAX_STRINGS)
            for number, note in enumerate(strings):
                _int(note, f"{at} tuning[{number}]", 0, 127)
        if "capo" in part:
            if "tuning" not in part:
                raise LeadSheetError(f"{at} gives a capo without the tuning of the strings it stops")
            _int(part["capo"], f"{at} capo", 1, MAX_CAPO)
        if "document" in part:
            if "tuning" not in part:
                raise LeadSheetError(f"{at} cites a document for a tuning it does not give")
            if not documents:
                raise LeadSheetError(f"{at} cites a document, but the lead sheet pins none")
            _int(part["document"], f"{at} document", 0, documents - 1)
    if len(set(names)) != len(names):
        raise LeadSheetError(f"{where} names one part twice")


def check_lead_sheet_revision(previous: dict[str, Any], new: dict[str, Any]) -> None:
    """A new revision of a lead sheet keeps its asserter and its subject: the
    same agent, the same Work object, and the same source revision (or none).
    Another person's version, or the same music on another recording, is a
    new lead sheet that may pin this one in `derived_from`."""
    if previous.get("agent") != new.get("agent"):
        raise LeadSheetError("a lead sheet revision keeps its agent; another asserter makes a new lead sheet")

    def subject(claim: dict[str, Any]) -> tuple:
        refs = claim.get("refs", [])
        work = [r.get("id") for r in refs if isinstance(r, dict) and r.get("role") == "work"]
        source = [(r.get("id"), r.get("revision_id")) for r in refs if isinstance(r, dict) and r.get("role") == "source"]
        return tuple(work), tuple(source)

    if subject(previous) != subject(new):
        raise LeadSheetError("a lead sheet revision keeps its Work and its recording; another is a new lead sheet")


# ---------------------------------------------------------------------------
# Views


@dataclass(frozen=True)
class Change:
    """One thing that happened to a pinned input after the lead sheet pinned
    it. `kind` is `revised` (a later revision of the same object supersedes
    it), `corrected` (a claim names it in `corrects`), or `judged` (a judgment
    of that exact revision, with its `verdict`)."""

    role: str
    pinned: str
    kind: str
    by: str
    verdict: str | None = None


def changes_since(lead_sheet: dict[str, Any], records: list[dict[str, Any]]) -> list[Change]:
    """What changed among a lead sheet's inputs since it pinned them. A
    computed view, never stored: the lead sheet keeps its pins, and only its
    asserter's new revision moves them. `records` are committed records with
    `revision_id`, `shape`, `supersedes` (revision IDs), and `refs`."""
    successors: dict[str, list[str]] = {}
    for record in records:
        for earlier in record.get("supersedes", []):
            successors.setdefault(earlier, []).append(record["revision_id"])
    changes: list[Change] = []
    for ref in lead_sheet.get("refs", []):
        role, pinned = ref["role"], ref["revision_id"]
        if role not in RENDERED_ROLES or role == "work":
            continue
        seen, frontier = set(), [pinned]
        while frontier:
            for later in successors.get(frontier.pop(), []):
                if later not in seen:
                    seen.add(later)
                    frontier.append(later)
                    changes.append(Change(role, pinned, "revised", later))
        for record in records:
            for other in record.get("refs", []):
                if other.get("revision_id") != pinned:
                    continue
                if other.get("role") == "corrects" and record.get("shape") == "claim":
                    changes.append(Change(role, pinned, "corrected", record["revision_id"]))
                elif other.get("role") == "assessment_of" and record.get("shape") == "assessment":
                    verdict = record.get("payload", {}).get("verdict")
                    changes.append(Change(role, pinned, "judged", record["revision_id"], verdict))
    return changes


def rendered_inputs(lead_sheet: dict[str, Any]) -> list[dict[str, Any]]:
    """Every pinned revision a render of this lead sheet shows, so every one
    whose sensitivity and rights the relay checks before showing or sending
    it. Lineage (`derived_from`), the band, its memberships, and the documents
    a tuning was read from are not shown."""
    return [ref for ref in lead_sheet.get("refs", []) if ref.get("role") in RENDERED_ROLES]


def render_key(lead_sheet_revision: str, fmt: str, renderer: str, parameters: dict[str, Any] | None = None) -> str:
    """The derived-media path a render is filed under (ADR 0069's layout):
    the lead sheet revision's digest, then the format, the renderer and its
    version, and a digest of the view's parameters (a transposition, a paper
    size, the parts chosen). The lead sheet revision pins every input
    exactly, so this key names one render; losing it loses nothing."""
    if not isinstance(lead_sheet_revision, str) or not REVISION_ID.fullmatch(lead_sheet_revision):
        raise LeadSheetError("a render is keyed by an exact lead sheet revision")
    if fmt not in RENDER_FORMATS:
        raise LeadSheetError(f"a render's format is one of {', '.join(RENDER_FORMATS)}")
    if not isinstance(renderer, str) or not RENDERER.fullmatch(renderer):
        raise LeadSheetError("a renderer is named as name@version")
    canonical = json.dumps(parameters or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    view = hashlib.sha256(canonical.encode()).hexdigest()[:16]
    digest = lead_sheet_revision.removeprefix("sha256:")
    return f"sha256/{digest[:2]}/{digest[2:4]}/{digest}/lead-sheet.{fmt}.{renderer}.{view}"
