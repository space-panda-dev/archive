"""Practice audio and exports (ADR 0089): which bank stems the relay serves
for practice and to whom, the practice practice request (any subset of stems,
gains, transposition, speed) and the key that names its regenerable file,
transposition as a view, the export request and the key that names its
regenerable file, the `archive.claim/export` version 1 claim that admits a
sent file, and the MusicXML `<harmony>` mapping ADR 0077 left unbuilt.

ADR 0089 is proposed. No specification release installs
`archive.claim/export` yet, so Door cannot admit one; this module holds its
rules as World would re-check them, so an importer can check a proposal
before Door does (INV-2). It uses only the standard library.

An export is a computed view: regenerable from the exact revisions it
renders, never stored as a claim, and never a judgment between claims
(INV-7). Only a file that a person sent is admitted, as an Artifact with an
export claim saying what it rendered. Stems are bank content (ADRs 0073 and
0075) and practice renders are derived media (ADR 0069): never admitted
unless a person chooses, and served only to callers the recording itself
may reach. Nothing transposed is ever a claim.
"""

from __future__ import annotations

import hashlib
import json
import re
from fractions import Fraction
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from world.harmony import BANK_EXTRACTOR, BANK_OUTPUT, PITCH_NAMES, check_reading, stack_names

EXPORT = ("archive.claim/export", "1")

# format -> (media type, file suffix). MusicXML is the uncompressed 4.0
# score-partwise file; JAMS is JSON with its own suffix.
FORMATS: dict[str, tuple[str, str]] = {
    "pdf": ("application/pdf", ".pdf"),
    "musicxml": ("application/vnd.recordare.musicxml+xml", ".musicxml"),
    "lilypond": ("text/x-lilypond", ".ly"),
    "jams": ("application/json", ".jams"),
}

OBJECT_ID = re.compile(r"urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
REVISION_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
KEY = re.compile(r"sha256:[0-9a-f]{64}\Z")
EXPORTER_ID = re.compile(r"[a-z][a-z0-9-]{0,62}\Z")
EXPORTER_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9.]{0,39}\Z")
OPTION_NAME = re.compile(r"[a-z][a-z0-9_]{0,62}\Z")
# A bank feature, `<extractor>/<output>`, as enrich/bank.py names it.
FEATURE = re.compile(r"([a-z][a-z0-9-]{0,62})/([a-z][a-z0-9_]{0,62})\Z")
MAX_RENDERED = 64
MAX_JOINED = 16
MAX_OPTIONS = 32
MAX_SOFTWARE = 32
MAX_LOSSES = 32
MAX_TEXT = 200
MAX_VERSION = 100


class ExportError(ValueError):
    """An export request or export claim that breaks a rule of ADR 0089."""


# ---------------------------------------------------------------------------
# Shared pieces


def _closed(value: Any, required: set[str], optional: set[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ExportError(f"{where} must be an object")
    missing = required - set(value)
    extra = set(value) - required - optional
    if missing or extra:
        raise ExportError(f"{where} has missing {sorted(missing)} or unknown {sorted(extra)} fields")
    return value


def _text(value: Any, where: str, maximum: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise ExportError(f"{where} must be text of 1 to {maximum} characters")
    return value


def _list(value: Any, where: str, low: int, high: int) -> list[Any]:
    if not isinstance(value, list) or not low <= len(value) <= high:
        raise ExportError(f"{where} must be a list of {low} to {high} items")
    return value


def _pin(value: Any, where: str) -> tuple[str, str]:
    pin = _closed(value, {"id", "revision_id"}, set(), where)
    if not isinstance(pin["id"], str) or not OBJECT_ID.fullmatch(pin["id"]) \
            or not isinstance(pin["revision_id"], str) or not REVISION_ID.fullmatch(pin["revision_id"]):
        raise ExportError(f"{where} must pin an object and an exact revision")
    return pin["id"], pin["revision_id"]


def _pins(value: Any, where: str, low: int, high: int) -> list[tuple[str, str]]:
    pins = [_pin(item, f"{where}[{index}]") for index, item in enumerate(_list(value, where, low, high))]
    if len(set(pins)) != len(pins):
        raise ExportError(f"{where} repeats a revision")
    return pins


def canonical(value: Any) -> bytes:
    """Canonical JSON, as the feature bank's keys use it: sorted keys, no
    spaces, UTF-8."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


# ---------------------------------------------------------------------------
# Export requests and keys


def _check_exporter(value: Any, where: str) -> dict[str, str]:
    exporter = _closed(value, {"id", "version"}, set(), where)
    if not isinstance(exporter["id"], str) or not EXPORTER_ID.fullmatch(exporter["id"]):
        raise ExportError(f"{where} id must be lowercase letters, digits, and hyphens, starting with a letter")
    if not isinstance(exporter["version"], str) or not EXPORTER_VERSION.fullmatch(exporter["version"]):
        raise ExportError(f"{where} version must be 1 to 40 letters, digits, and dots")
    return exporter


def _check_options(value: Any, where: str) -> dict[str, Any]:
    """An exporter's options: at most 32 named scalars. Numbers are integers
    only, so that canonical JSON, and so the key, has one spelling."""
    if not isinstance(value, dict) or len(value) > MAX_OPTIONS:
        raise ExportError(f"{where} must be an object of at most {MAX_OPTIONS} options")
    for name, item in value.items():
        if not OPTION_NAME.fullmatch(name):
            raise ExportError(f"{where} option {name!r} must be a lowercase name")
        if isinstance(item, bool) or (isinstance(item, int) and -2**53 < item < 2**53):
            continue
        if isinstance(item, str) and 1 <= len(item) <= MAX_TEXT:
            continue
        raise ExportError(f"{where} option {name!r} must be a boolean, a safe integer, or text of 1 to {MAX_TEXT}")
    return value


def check_request(request: Any) -> dict[str, Any]:
    """Check an export request: what a client asks to be rendered. Returns it;
    raises ExportError. The request is the whole of what decides the file, so
    its key names the file."""
    where = "export request"
    _closed(request, {"format", "exporter", "rendered"}, {"joined", "options"}, where)
    if request["format"] not in FORMATS:
        raise ExportError(f"{where} format must be one of {', '.join(FORMATS)}")
    _check_exporter(request["exporter"], f"{where} exporter")
    rendered = _pins(request["rendered"], f"{where} rendered", 1, MAX_RENDERED)
    joined = _pins(request.get("joined", []), f"{where} joined", 0, MAX_JOINED)
    if set(rendered) & set(joined):
        raise ExportError(f"{where} pins one revision as both rendered and joined")
    _check_options(request.get("options", {}), f"{where} options")
    return request


def export_key(request: dict[str, Any]) -> str:
    """The key of an export: the SHA-256 of the canonical JSON of its format,
    exporter, options, and the exact revisions it renders and joins, in the
    request's order (the order is the export's: a JAMS file's annotations, a
    score's parts). Absent options and joins are the empty ones, so they
    have one key."""
    check_request(request)
    material = {
        "format": request["format"],
        "exporter": {"id": request["exporter"]["id"], "version": request["exporter"]["version"]},
        "options": request.get("options", {}),
        "rendered": [[pin["id"], pin["revision_id"]] for pin in request["rendered"]],
        "joined": [[pin["id"], pin["revision_id"]] for pin in request.get("joined", [])],
    }
    return "sha256:" + hashlib.sha256(canonical(material)).hexdigest()


def export_path(root: Path, key: str, export_format: str) -> Path:
    """Where an export's file lives under a derived-media root:
    `exports/<ab>/<cd>/<key hex>/export<suffix>`, with `request.json`
    beside it. The directory is regenerable, like derived media
    (ADR 0069)."""
    if not isinstance(key, str) or not KEY.fullmatch(key):
        raise ExportError("an export key is sha256:<64 lowercase hex>")
    if export_format not in FORMATS:
        raise ExportError(f"format must be one of {', '.join(FORMATS)}")
    hexd = key.split(":", 1)[1]
    return Path(root) / "exports" / hexd[:2] / hexd[2:4] / hexd / ("export" + FORMATS[export_format][1])


# ---------------------------------------------------------------------------
# archive.claim/export version 1


EXPORT_ROLES = {"artifact": (1, 1), "rendered": (1, MAX_RENDERED), "joined": (0, MAX_JOINED)}


def _refs_by_role(refs: Any, where: str) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(refs, list):
        raise ExportError(f"{where} refs must be a list")
    by_role: dict[str, list[dict[str, Any]]] = {role: [] for role in EXPORT_ROLES}
    for index, ref in enumerate(refs):
        _closed(ref, {"role", "id", "revision_id"}, set(), f"{where} ref {index}")
        if ref["role"] not in EXPORT_ROLES:
            raise ExportError(f"{where} ref {index} has role {ref['role']!r}, which this claim cannot have")
        _pin({"id": ref["id"], "revision_id": ref["revision_id"]}, f"{where} ref {index}")
        by_role[ref["role"]].append(ref)
    for role, (low, high) in EXPORT_ROLES.items():
        if not low <= len(by_role[role]) <= high:
            raise ExportError(f"{where} needs {low} to {high} '{role}' references")
    return by_role


def _target_shape(targets: dict[str, dict[str, Any]], ref: dict[str, Any], shape: str, where: str) -> None:
    target = targets.get(ref["revision_id"])
    if target is None:
        raise ExportError(f"{where} pins {ref['revision_id']}, which is not a committed revision")
    if target.get("id", ref["id"]) != ref["id"]:
        raise ExportError(f"{where} pins a revision of another object")
    if target.get("shape") != shape:
        raise ExportError(f"{where} must pin {'an Artifact' if shape == 'artifact' else 'a Claim'}")


def check_export(claim: dict[str, Any], targets: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Check an `archive.claim/export` version 1 claim against every rule of
    ADR 0089. `targets` maps each pinned revision ID to what Door would find
    there (`shape`, and `id`). Returns the payload; raises ExportError."""
    where = "export"
    record = _closed(claim, {"payload", "refs"}, {"revision_id", "agent"}, where)
    payload = _closed(record["payload"], {"format", "media_type", "exporter", "key"},
                      {"options", "software", "losses"}, f"{where} payload")
    by_role = _refs_by_role(record["refs"], where)
    if payload["format"] not in FORMATS:
        raise ExportError(f"{where} format must be one of {', '.join(FORMATS)}")
    if payload["media_type"] != FORMATS[payload["format"]][0]:
        raise ExportError(f"{where} media_type of a {payload['format']} export is {FORMATS[payload['format']][0]}")
    _check_exporter(payload["exporter"], f"{where} exporter")
    if "options" in payload:
        _check_options(payload["options"], f"{where} options")
    if "software" in payload:
        for index, item in enumerate(_list(payload["software"], f"{where} software", 1, MAX_SOFTWARE)):
            entry = _closed(item, {"name", "version"}, set(), f"{where} software[{index}]")
            _text(entry["name"], f"{where} software[{index}] name", MAX_TEXT)
            _text(entry["version"], f"{where} software[{index}] version", MAX_VERSION)
    if "losses" in payload:
        losses = _list(payload["losses"], f"{where} losses", 1, MAX_LOSSES)
        for index, item in enumerate(losses):
            _text(item, f"{where} losses[{index}]", MAX_TEXT)
        if len(set(losses)) != len(losses):
            raise ExportError(f"{where} losses repeats an item")

    artifact = by_role["artifact"][0]
    _target_shape(targets, artifact, "artifact", f"{where} artifact")
    for ref in by_role["rendered"]:
        _target_shape(targets, ref, "claim", f"{where} rendered")
    for ref in by_role["joined"]:
        _target_shape(targets, ref, "artifact", f"{where} joined")
    pinned = {(ref["id"], ref["revision_id"]) for ref in by_role["rendered"] + by_role["joined"]}
    if (artifact["id"], artifact["revision_id"]) in pinned:
        raise ExportError(f"{where} artifact is the exported file, never one of its inputs")

    request = {
        "format": payload["format"],
        "exporter": payload["exporter"],
        "rendered": [{"id": r["id"], "revision_id": r["revision_id"]} for r in by_role["rendered"]],
        "joined": [{"id": r["id"], "revision_id": r["revision_id"]} for r in by_role["joined"]],
        "options": payload.get("options", {}),
    }
    try:
        expected = export_key(request)
    except ExportError as exc:
        raise ExportError(f"{where} does not describe a valid export request: {exc}") from None
    if payload["key"] != expected:
        raise ExportError(f"{where} key is not the key of the request its references and payload state")
    return payload


def request_of(claim: dict[str, Any]) -> dict[str, Any]:
    """The export request an admitted export claim states, so that the same
    view can be rendered again and compared with the file that was sent."""
    refs = claim["refs"]
    payload = claim["payload"]
    request = {
        "format": payload["format"],
        "exporter": dict(payload["exporter"]),
        "rendered": [{"id": r["id"], "revision_id": r["revision_id"]} for r in refs if r["role"] == "rendered"],
    }
    joined = [{"id": r["id"], "revision_id": r["revision_id"]} for r in refs if r["role"] == "joined"]
    if joined:
        request["joined"] = joined
    if payload.get("options"):
        request["options"] = dict(payload["options"])
    return request


# ---------------------------------------------------------------------------
# Practice audio


def feature_name(value: Any) -> tuple[str, str]:
    """A bank feature name, `<extractor>/<output>`: (extractor, output)."""
    match = FEATURE.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise ExportError("a feature name is <extractor>/<output>, such as stems/bass")
    return match.group(1), match.group(2)


def practice_refusal(feature: str, declared: dict[str, Any] | None, served: set[str] | frozenset[str],
                     caller: str, operator: str | None, recording_reaches_caller: bool | None) -> str | None:
    """The relay's decision for one practice-audio request, before it looks
    for the entry: None to serve, or the refusal code.

    - `declared`: the installed extractor's declaration of the output, or
      None when no installed extractor declares it;
    - `served`: the feature names the relay's configuration serves;
    - `recording_reaches_caller`: whether the recording itself may be shown
      to the caller under its sensitivity, rights, and disclosure
      (ADRs 0085 and 0088); None when that cannot be established.

    A stem or practice mix is the recording, so it takes the recording's
    answer and never has one of its own. A fact that cannot be established
    never widens disclosure (S-32): only the active operator is served
    without one."""
    feature_name(feature)
    if declared is None or feature not in served or declared.get("kind") != "file" \
            or not str(declared.get("media_type", "")).startswith("audio/"):
        return "feature_not_served"
    if operator is None:
        return "media_forbidden"
    if caller == operator:
        return None
    if recording_reaches_caller is True:
        return None
    return "media_forbidden"


# ---------------------------------------------------------------------------
# Practice renders (decisions D11.3 to D11.5)

RENDERER_ID = EXPORTER_ID
MAX_RENDER_STEMS = 16
GAINS = (1, 200)          # percent of the stem as separated; a stem left out is not in the mix
SEMITONES = (-12, 12)
SPEEDS = (25, 200)        # percent of the original tempo
DEFAULT_SEMITONES = 0
DEFAULT_SPEED = 100
# Outputs never pitch-shifted, whatever the request: they have no pitch to
# move (D11.4). A renderer that changes this list is a new renderer version.
UNPITCHED_OUTPUTS = frozenset({"drums"})


def check_practice_request(request: Any) -> dict[str, Any]:
    """Check a practice practice request: the settings a client also applies
    live (D11.5), sent so a machine renders the same mix. Returns it with
    its defaults filled in; raises ExportError.

    - `content`: the recording's content digest, `sha256:<hex>`;
    - `stems`: the bank entry the stems come from, `{extractor, key}` as
      ADR 0079 cites a stem, so a 4-stem and a 6-stem separation (two
      extractors, or one with other parameters) are told apart by key;
    - `gains`: 1 to 16 of that entry's `file` outputs, each with an integer
      gain in percent (1 to 200). Any subset is a mix (D11.3); an output left
      out is not in it, so "minus drums and bass" names the other stems;
    - `semitones` (-12 to 12, default 0) and `speed` (25 to 200 percent,
      default 100): a pitch shift and a time stretch, each optional;
    - `renderer`: `{id, version}`, whose version stands for its code, as an
      extractor's does.

    Every number is an integer, so canonical JSON, and the key, has one
    spelling."""
    where = "practice request"
    _closed(request, {"content", "stems", "gains", "renderer"}, {"semitones", "speed"}, where)
    if not isinstance(request["content"], str) or not KEY.fullmatch(request["content"]):
        raise ExportError(f"{where} content must be a recording's digest, sha256:<64 lowercase hex>")
    stems = _closed(request["stems"], {"extractor", "key"}, set(), f"{where} stems")
    if not isinstance(stems["extractor"], str) or not BANK_EXTRACTOR.fullmatch(stems["extractor"]):
        raise ExportError(f"{where} stems extractor must be a bank extractor id")
    if not isinstance(stems["key"], str) or not KEY.fullmatch(stems["key"]):
        raise ExportError(f"{where} stems key must be the entry's key, sha256:<64 lowercase hex>")
    gains = request["gains"]
    if not isinstance(gains, dict) or not 1 <= len(gains) <= MAX_RENDER_STEMS:
        raise ExportError(f"{where} gains must name 1 to {MAX_RENDER_STEMS} stems")
    for output, gain in gains.items():
        if not BANK_OUTPUT.fullmatch(output):
            raise ExportError(f"{where} gains names {output!r}, which is not a bank output name")
        if isinstance(gain, bool) or not isinstance(gain, int) or not GAINS[0] <= gain <= GAINS[1]:
            raise ExportError(f"{where} gain of {output} must be an integer percent in {GAINS[0]}..{GAINS[1]}")
    _check_exporter(request["renderer"], f"{where} renderer")
    for name, (low, high), default in (("semitones", SEMITONES, DEFAULT_SEMITONES), ("speed", SPEEDS, DEFAULT_SPEED)):
        value = request.get(name, default)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ExportError(f"{where} {name} must be an integer in {low}..{high}")
    return {**request, "semitones": request.get("semitones", DEFAULT_SEMITONES),
            "speed": request.get("speed", DEFAULT_SPEED)}


def practice_key(request: dict[str, Any]) -> str:
    """The key of a practice render: the SHA-256 of the canonical JSON of
    the recording, the stems entry, the gains, the semitones, the speed, and
    the renderer, defaults filled in, so one mix has one key however it was
    asked for. It is known before rendering, so a client asks by settings
    and the relay finds the file by key."""
    checked = check_practice_request(request)
    material = {key: checked[key] for key in ("content", "stems", "gains", "renderer", "semitones", "speed")}
    return "sha256:" + hashlib.sha256(canonical(material)).hexdigest()


def practice_path(root: Path, request: dict[str, Any]) -> Path:
    """Where a render lives under a derived-media root: in its recording's
    own directory, as ADR 0069 files derived media,
    `sha256/<ab>/<cd>/<content hex>/practice/<key hex>.flac`, with the
    request beside it as `<key hex>.json`, so destroying the recording finds
    its renders."""
    checked = check_practice_request(request)
    content = checked["content"].split(":", 1)[1]
    key = practice_key(checked).split(":", 1)[1]
    return Path(root) / "sha256" / content[:2] / content[2:4] / content / "practice" / (key + ".flac")


def shifted_outputs(request: dict[str, Any]) -> list[str]:
    """The stems a render pitch-shifts: every stem in the mix but the
    unpitched ones, and none when the request moves nothing (D11.4)."""
    checked = check_practice_request(request)
    if checked["semitones"] == 0:
        return []
    return sorted(output for output in checked["gains"] if output not in UNPITCHED_OUTPUTS)


def render_seconds(original_seconds: Fraction | int, speed: int) -> Fraction:
    """Where a musical position of the recording falls in a render made at
    `speed` percent: a render starts where the recording does and is
    stretched evenly, so a client switching from its live mix to the render
    (D11.5) seeks here and stays at the same place in the music."""
    if isinstance(speed, bool) or not isinstance(speed, int) or not SPEEDS[0] <= speed <= SPEEDS[1]:
        raise ExportError(f"speed must be an integer in {SPEEDS[0]}..{SPEEDS[1]}")
    return Fraction(original_seconds) * 100 / speed


def original_seconds(render_position: Fraction | int, speed: int) -> Fraction:
    """The inverse of `render_seconds`: the recording's position, and so the
    samples every claim counts, of a place in a render."""
    render_seconds(0, speed)  # the same check of `speed`
    return Fraction(render_position) * speed / 100


# ---------------------------------------------------------------------------
# Transposition as a view (D11.4)


def transpose_reading(reading: dict[str, Any], semitones: int) -> dict[str, Any]:
    """A chord reading moved by `semitones`, for display only: every pitch
    class moves, a voicing moves when it stays within MIDI (and is dropped
    otherwise), and the source's verbatim `text` is dropped, since it names
    the chord before the move. A weight stays: it compares readings within
    their claim. The result is a view, never a claim."""
    if isinstance(semitones, bool) or not isinstance(semitones, int):
        raise ExportError("semitones must be an integer")
    moved = {k: v for k, v in reading.items() if k != "text"}
    for field in ("bass", "root"):
        if field in moved:
            moved[field] = (moved[field] + semitones) % 12
    if "tones" in moved:
        moved["tones"] = sorted((pc + semitones) % 12 for pc in moved["tones"])
    if "uncertain" in moved:
        moved["uncertain"] = sorted(({**group, "pitch_classes": sorted((pc + semitones) % 12
                                                                       for pc in group["pitch_classes"])}
                                     for group in moved["uncertain"]), key=lambda group: group["pitch_classes"])
    if "voicing" in moved:
        notes = [note + semitones for note in moved["voicing"]]
        if all(0 <= note <= 127 for note in notes):
            moved["voicing"] = notes
        else:
            del moved["voicing"]
    return moved


def transpose_key(key: dict[str, Any], semitones: int) -> dict[str, Any]:
    """A key reading (ADR 0080) moved by `semitones`: the collection and
    tonic move and the mode stays; the asserter's own `spelling` and `text`
    are dropped, so the moved key is spelled by ADR 0080's stated rule (Gb
    major moved up a semitone is G major, not F## anything). A `no_key` or
    text-only reading moves to no key reading at all."""
    if isinstance(semitones, bool) or not isinstance(semitones, int):
        raise ExportError("semitones must be an integer")
    if "collection" not in key:
        return {"no_key": True} if key.get("no_key") else {}
    moved = {k: v for k, v in key.items() if k not in ("spelling", "text")}
    moved["collection"] = sorted((pc + semitones) % 12 for pc in key["collection"])
    if "tonic" in moved:
        moved["tonic"] = (moved["tonic"] + semitones) % 12
    return moved


# ---------------------------------------------------------------------------
# MusicXML <harmony> (ADR 0077's mapping)

# MusicXML 4.0 kind values and their intervals above the root, each interval
# with the degree (value, alter) that names it within that kind. Kinds whose
# meaning is functional (Neapolitan, Italian, French, German, Tristan) or
# not a chord quality (pedal) are never chosen from a stack.
KINDS: dict[str, dict[int, tuple[int, int]]] = {
    "major": {0: (1, 0), 4: (3, 0), 7: (5, 0)},
    "minor": {0: (1, 0), 3: (3, -1), 7: (5, 0)},
    "augmented": {0: (1, 0), 4: (3, 0), 8: (5, 1)},
    "diminished": {0: (1, 0), 3: (3, -1), 6: (5, -1)},
    "dominant": {0: (1, 0), 4: (3, 0), 7: (5, 0), 10: (7, -1)},
    "major-seventh": {0: (1, 0), 4: (3, 0), 7: (5, 0), 11: (7, 0)},
    "minor-seventh": {0: (1, 0), 3: (3, -1), 7: (5, 0), 10: (7, -1)},
    "diminished-seventh": {0: (1, 0), 3: (3, -1), 6: (5, -1), 9: (7, -2)},
    "augmented-seventh": {0: (1, 0), 4: (3, 0), 8: (5, 1), 10: (7, -1)},
    "half-diminished": {0: (1, 0), 3: (3, -1), 6: (5, -1), 10: (7, -1)},
    "major-minor": {0: (1, 0), 3: (3, -1), 7: (5, 0), 11: (7, 0)},
    "major-sixth": {0: (1, 0), 4: (3, 0), 7: (5, 0), 9: (6, 0)},
    "minor-sixth": {0: (1, 0), 3: (3, -1), 7: (5, 0), 9: (6, 0)},
    "dominant-ninth": {0: (1, 0), 4: (3, 0), 7: (5, 0), 10: (7, -1), 2: (9, 0)},
    "major-ninth": {0: (1, 0), 4: (3, 0), 7: (5, 0), 11: (7, 0), 2: (9, 0)},
    "minor-ninth": {0: (1, 0), 3: (3, -1), 7: (5, 0), 10: (7, -1), 2: (9, 0)},
    "dominant-11th": {0: (1, 0), 4: (3, 0), 7: (5, 0), 10: (7, -1), 2: (9, 0), 5: (11, 0)},
    "major-11th": {0: (1, 0), 4: (3, 0), 7: (5, 0), 11: (7, 0), 2: (9, 0), 5: (11, 0)},
    "minor-11th": {0: (1, 0), 3: (3, -1), 7: (5, 0), 10: (7, -1), 2: (9, 0), 5: (11, 0)},
    "dominant-13th": {0: (1, 0), 4: (3, 0), 7: (5, 0), 10: (7, -1), 2: (9, 0), 5: (11, 0), 9: (13, 0)},
    "major-13th": {0: (1, 0), 4: (3, 0), 7: (5, 0), 11: (7, 0), 2: (9, 0), 5: (11, 0), 9: (13, 0)},
    "minor-13th": {0: (1, 0), 3: (3, -1), 7: (5, 0), 10: (7, -1), 2: (9, 0), 5: (11, 0), 9: (13, 0)},
    "suspended-second": {0: (1, 0), 2: (2, 0), 7: (5, 0)},
    "suspended-fourth": {0: (1, 0), 5: (4, 0), 7: (5, 0)},
    "power": {0: (1, 0), 7: (5, 0)},
}


def _added(interval: int, chord: set[int]) -> tuple[int, int]:
    """An added interval's degree: an extension above a seventh (a ninth,
    eleventh, or thirteenth), the simple degree below one, as Harte names
    them in world/harmony.py."""
    sevenths = bool(chord & {10, 11})
    return {
        1: (9, -1), 2: (9, 0),
        3: (9, 1) if 4 in chord else (3, -1),
        4: (3, 0),
        5: (11, 0) if sevenths else (4, 0),
        6: (11, 1) if 7 in chord else (5, -1),
        7: (5, 0),
        8: (13, -1) if 7 in chord else (5, 1),
        9: (13, 0) if sevenths else (6, 0),
        10: (7, -1), 11: (7, 0),
    }[interval]


def _spell(pitch_class: int, spelling: tuple[str, ...] | None) -> tuple[str, int]:
    names = spelling or PITCH_NAMES
    if len(names) != 12:
        raise ExportError("a spelling names all twelve pitch classes")
    name = names[pitch_class]
    if not isinstance(name, str) or not re.fullmatch(r"[A-G](#{1,2}|b{1,2})?", name):
        raise ExportError(f"spelling {name!r} is not a step and accidentals")
    step, accidentals = name[0], name[1:]
    alter = accidentals.count("#") - accidentals.count("b")
    natural = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}[step]
    if (natural + alter) % 12 != pitch_class:
        raise ExportError(f"spelling {name!r} does not spell pitch class {pitch_class}")
    return step, alter


def _best_kind(intervals: set[int]) -> tuple[str, list[int], list[int]]:
    """The kind needing fewest degree marks; at equal marks fewer
    subtractions, then the earlier kind in the table."""
    best = None
    for order, (kind, degrees) in enumerate(KINDS.items()):
        chord = set(degrees)
        added = sorted(intervals - chord)
        removed = sorted(chord - intervals - {0})
        cost = (len(added) + len(removed), len(removed), order)
        if best is None or cost < best[0]:
            best = (cost, kind, added, removed)
    _, kind, added, removed = best
    return kind, added, removed


def _step_xml(prefix: str, step: str, alter: int) -> str:
    inner = f"<{prefix}-step>{step}</{prefix}-step>"
    if alter:
        inner += f"<{prefix}-alter>{alter}</{prefix}-alter>"
    return inner


def musicxml_harmony(readings: list[dict[str, Any]], spelling: tuple[str, ...] | None = None) \
        -> tuple[str | None, list[str]]:
    """A MusicXML `<harmony>` element for a span's readings, and what it
    dropped. The first reading is written; the rest are losses, as are
    weights, uncertain tones, a voicing, and an asserted root that does not
    sound (ADR 0077's table). `spelling` names each pitch class (twelve
    names, C = 0), as a key claim spells it (ADR 0080); absent, World's
    stated default spelling is used.

    A stack's root is its asserted root, or the first computed name's root
    (ADR 0077's stated order); its kind is the MusicXML kind needing fewest
    `<degree>` marks; `kind@text` is the computed lead-sheet symbol without
    the root and bass when one exists. "No chord" is kind `none` over a
    nominal C root, as MusicXML requires a root. A text-only reading writes
    no harmony."""
    if not isinstance(readings, list) or not readings:
        raise ExportError("readings must be a non-empty list")
    losses: list[str] = []
    first = readings[0]
    weighted = "weight" in first
    for index, reading in enumerate(readings):
        try:
            check_reading(reading, weighted, f"reading {index}")
        except ValueError as exc:
            raise ExportError(str(exc)) from None
    if len(readings) > 1:
        losses.append("alternative readings")
    if weighted:
        losses.append("weights")
    if first.get("no_chord") is True:
        return ('<harmony><root><root-step>C</root-step></root><kind text="N.C.">none</kind></harmony>',
                losses)
    if "bass" not in first:
        losses.append("a reading with no stack")
        return None, losses
    if "uncertain" in first:
        losses.append("uncertain tones")
    if "voicing" in first:
        losses.append("voicing")
    bass, tones = first["bass"], frozenset(first["tones"])
    name = stack_names(bass, tones, first.get("root"))[0]
    root = name.root
    sounding = tones | {bass}
    if root not in sounding:
        losses.append("a root that does not sound")
    intervals = {(pc - root) % 12 for pc in sounding} | {0}
    kind, added, removed = _best_kind(intervals)
    step, alter = _spell(root, spelling)
    text = ""
    if name.symbol is not None:
        symbol = name.symbol.split("/", 1)[0]
        text = symbol[len(PITCH_NAMES[root]):]
    parts = [f"<root>{_step_xml('root', step, alter)}</root>",
             f'<kind text="{escape(text, {chr(34): "&quot;"})}">{kind}</kind>']
    if bass != root:
        b_step, b_alter = _spell(bass, spelling)
        parts.append(f"<bass>{_step_xml('bass', b_step, b_alter)}</bass>")
    chord = set(KINDS[kind])
    for interval in added:
        value, degree_alter = _added(interval, chord | intervals)
        parts.append(f"<degree><degree-value>{value}</degree-value><degree-alter>{degree_alter}</degree-alter>"
                     f"<degree-type>add</degree-type></degree>")
    for interval in removed:
        value, degree_alter = KINDS[kind][interval]
        parts.append(f"<degree><degree-value>{value}</degree-value><degree-alter>{degree_alter}</degree-alter>"
                     f"<degree-type>subtract</degree-type></degree>")
    return "<harmony>" + "".join(parts) + "</harmony>", losses
