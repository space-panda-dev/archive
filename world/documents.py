"""Documents, timed lyrics, sensitivity, and rights (ADR 0085): payload and
reference rules, the checks the relay makes before it stages a document's
bytes, the lyric view joined from a document at display time, and how far an
object may reach under its sensitivity and rights claims.

The four contracts, `archive.claim/document_source`,
`archive.claim/timed_lyrics`, `archive.claim/sensitivity`, and
`archive.claim/rights`, each version 1, are decided by ADR 0085 for
specification 0.0.10. This module holds their rules as World would re-check
them, so an importer, the relay, or a client can check a proposal before Door
does; Door remains the authority (INV-2). It uses only the standard library.

Nothing here stores a lyric. A timed lyrics claim places a document's lines
and words by number; `lyric_view` joins the words from the document's bytes
when something is shown, and the joined text is a view, never a claim.

`reach` is a computed view: how far an object may be shown or shared under
the sensitivity and rights claims on it and on everything it pins. The
circle is private, so rights never keep anything from it; only sensitivity
does. Rights govern sharing beyond the circle. A reach is
necessary, never sufficient: showing anything to anyone but the operator also
needs a disclosure granted by policy (INV-25), which this module does not
decide.
"""

from __future__ import annotations

import base64
import io
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

DOCUMENT_SOURCE = ("archive.claim/document_source", "1")
TIMED_LYRICS = ("archive.claim/timed_lyrics", "1")
SENSITIVITY = ("archive.claim/sensitivity", "1")
RIGHTS = ("archive.claim/rights", "1")
AUDIO_STREAM = ("archive.claim/audio_stream", "1")
DERIVATION_RUN = ("archive.claim/derivation_run", "1")
NOTES = ("archive.claim/notes", "1")                    # ADR 0086, a syllable's carrying notes

OBJECT_ID = re.compile(r"urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
REVISION_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
DIGEST = REVISION_ID
URL = re.compile(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+\Z")
DECIMAL = re.compile(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?\Z")
INSTANT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?Z\Z")
FULL_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
PARTIAL_DATE = re.compile(r"([0-9]{4})(?:-([0-9]{2})(?:-([0-9]{2}))?)?\Z")
JURISDICTION = re.compile(r"[A-Z]{2}\Z")
GOOGLE_DOC = re.compile(r"https://docs\.google\.com/document/d/([A-Za-z0-9_-]{10,200})(?:[/?#].*)?\Z")

MAX_NAME = 200
MAX_FILENAME = 255
MAX_TEXT = 2000
MAX_URL = 2000
MAX_LINES = 10000
MAX_WORDS = 200
MAX_SYLLABLES = 32          # per word (ADR 0085, amended 2026-10-19)
MAX_CARRIED_ONSETS = 64     # notes one syllable is sung on
MAX_LYRIC_NOTES = 8         # notes claims one timed lyrics claim pins
MAX_SUBJECTS = 1000
MAX_DOCUMENT_BYTES = 64 * 1024 * 1024
SPAN_ENDS = ("asserted", "held")


class DocumentError(ValueError):
    """A claim or a staging request that breaks a rule of ADR 0085. `code` is
    the stable refusal code a relay answers with (ADR 0058, ADR 0060)."""

    def __init__(self, message: str, code: str = "request_malformed"):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# Shared pieces


def _closed(value: Any, required: set[str], optional: set[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DocumentError(f"{where} must be an object")
    missing = required - set(value)
    extra = set(value) - required - optional
    if missing or extra:
        raise DocumentError(f"{where} has missing {sorted(missing)} or unknown {sorted(extra)} fields")
    return value


def _int(value: Any, where: str, low: int, high: int | None = None) -> int:
    # bool is an int in Python; a JSON true is not a number.
    if not isinstance(value, int) or isinstance(value, bool) or value < low or (high is not None and value > high):
        bound = f"{low}.." + ("" if high is None else str(high))
        raise DocumentError(f"{where} must be an integer in {bound}")
    return value


def _text(value: Any, where: str, maximum: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise DocumentError(f"{where} must be text of 1 to {maximum} characters")
    return value


def _list(value: Any, where: str, low: int, high: int) -> list[Any]:
    if not isinstance(value, list) or not low <= len(value) <= high:
        raise DocumentError(f"{where} must be a list of {low} to {high} items")
    return value


def _url(value: Any, where: str) -> str:
    url = _text(value, where, MAX_URL)
    if not URL.fullmatch(url):
        raise DocumentError(f"{where} must be an http or https URL")
    return url


def _instant_or_date(value: Any, where: str) -> str:
    if not isinstance(value, str) or not (FULL_DATE.fullmatch(value) or INSTANT.fullmatch(value)):
        raise DocumentError(f"{where} must be an RFC 3339 full date or UTC instant")
    try:
        date.fromisoformat(value[:10])
    except ValueError:
        raise DocumentError(f"{where} is not a calendar date") from None
    return value


def _partial_date(value: Any, where: str) -> tuple[date, date]:
    """A partial date (YYYY, YYYY-MM, or YYYY-MM-DD) as the earliest and
    latest days it can mean."""
    match = PARTIAL_DATE.fullmatch(value) if isinstance(value, str) else None
    if not match:
        raise DocumentError(f"{where} must be YYYY, YYYY-MM, or YYYY-MM-DD")
    year, month, day = (int(g) if g else None for g in match.groups())
    try:
        if day is not None:
            exact = date(year, month, day)
            return exact, exact
        if month is not None:
            first = date(year, month, 1)
            following = date(year + (month == 12), month % 12 + 1, 1)
            return first, date.fromordinal(following.toordinal() - 1)
        return date(year, 1, 1), date(year, 12, 31)
    except ValueError:
        raise DocumentError(f"{where} is not a calendar date") from None


def _refs_by_role(refs: Any, counts: dict[str, tuple[int, int]], where: str) -> dict[str, list[dict[str, Any]]]:
    """References grouped by role, with each role's (minimum, maximum) count."""
    if not isinstance(refs, list):
        raise DocumentError(f"{where} refs must be a list")
    by_role: dict[str, list[dict[str, Any]]] = {role: [] for role in counts}
    for index, ref in enumerate(refs):
        _closed(ref, {"role", "id", "revision_id"}, set(), f"{where} ref {index}")
        if ref["role"] not in counts:
            raise DocumentError(f"{where} ref {index} has role {ref['role']!r}, which this claim cannot have")
        if not isinstance(ref["id"], str) or not OBJECT_ID.fullmatch(ref["id"]) \
                or not isinstance(ref["revision_id"], str) or not REVISION_ID.fullmatch(ref["revision_id"]):
            raise DocumentError(f"{where} ref {index} must pin an object and an exact revision")
        by_role[ref["role"]].append(ref)
    for role, (low, high) in counts.items():
        if not low <= len(by_role[role]) <= high:
            raise DocumentError(f"{where} needs {low} to {high} '{role}' references")
    return by_role


def _distinct_objects(refs: list[dict[str, Any]], where: str) -> None:
    ids = [ref["id"] for ref in refs]
    if len(set(ids)) != len(ids):
        raise DocumentError(f"{where} names one object twice")


def _target(targets: dict[str, dict[str, Any]], ref: dict[str, Any], where: str) -> dict[str, Any]:
    """What a reference pins, as Door would read it from the committed
    revision: `shape` (`entity`, `claim`, `artifact`, or `assessment`), an
    Entity's `kind`, a Claim's `contract` (id, version), and for a stream or a
    document source its `payload` and `refs`."""
    target = targets.get(ref["revision_id"])
    if target is None:
        raise DocumentError(f"{where} pins {ref['revision_id']}, which is not a committed revision")
    if target.get("id", ref["id"]) != ref["id"]:
        raise DocumentError(f"{where} pins a revision of another object")
    return target


def _artifact(targets, ref, where: str) -> dict[str, Any]:
    target = _target(targets, ref, where)
    if target.get("shape") != "artifact":
        raise DocumentError(f"{where} must pin an Artifact")
    return target


def _entity(targets, ref, kinds: set[str] | None, where: str) -> dict[str, Any]:
    target = _target(targets, ref, where)
    if target.get("shape") != "entity" or (kinds is not None and target.get("kind") not in kinds):
        what = "an Entity" if kinds is None else f"an Entity of kind {' or '.join(sorted(kinds))}"
        raise DocumentError(f"{where} must pin {what}")
    return target


def _claim(targets, ref, contracts: set[tuple[str, str]], where: str) -> dict[str, Any]:
    target = _target(targets, ref, where)
    if target.get("shape") != "claim" or tuple(target.get("contract", ())) not in contracts:
        names = " or ".join(f"{c} version {v}" for c, v in sorted(contracts))
        raise DocumentError(f"{where} must pin a Claim of {names}")
    return target


def _check_stream(targets, stream_ref, source_ref, where: str) -> dict[str, int]:
    """The pinned audio stream's payload, after checking it is about the same
    Artifact revision as the `source` pin."""
    stream = _claim(targets, stream_ref, {AUDIO_STREAM}, f"{where} stream")
    measured = [r for r in stream.get("refs", []) if isinstance(r, dict) and r.get("role") == "artifact"]
    if len(measured) != 1 or (measured[0].get("id"), measured[0].get("revision_id")) \
            != (source_ref["id"], source_ref["revision_id"]):
        raise DocumentError(f"{where} pins a stream of another Artifact revision")
    payload = stream.get("payload") if isinstance(stream.get("payload"), dict) else {}
    return {
        "sample_rate": _int(payload.get("sample_rate"), f"{where} stream sample_rate", 1),
        "sample_count": _int(payload.get("sample_count"), f"{where} stream sample_count", 1),
    }


# ---------------------------------------------------------------------------
# Document media types, and what the relay stages


def _utf8_text(data: bytes) -> str | None:
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


def _zip_names(data: bytes) -> list[str] | None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return archive.namelist()
    except (zipfile.BadZipFile, ValueError, OSError):
        return None


def _zip_member(data: bytes, name: str, size: int) -> bytes:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        with archive.open(name) as member:
            return member.read(size)


def _is_text(data: bytes) -> bool:
    return _utf8_text(data) is not None


def _is_json(data: bytes) -> bool:
    text = _utf8_text(data)
    if text is None:
        return False
    try:
        json.loads(text)
    except ValueError:
        return False
    return True


def _is_musicxml(data: bytes) -> bool:
    text = _utf8_text(data)
    return text is not None and ("<score-partwise" in text[:8192] or "<score-timewise" in text[:8192])


def _zip_with(*required: str):
    def sniff(data: bytes) -> bool:
        names = _zip_names(data)
        return names is not None and all(name in names for name in required)
    return sniff


def _is_odt(data: bytes) -> bool:
    names = _zip_names(data)
    return bool(names) and names[0] == "mimetype" \
        and _zip_member(data, "mimetype", 64) == b"application/vnd.oasis.opendocument.text"


def _is_guitar_pro(data: bytes) -> bool:
    # Guitar Pro 3 to 5 begin with a length byte and "FICHIER GUITAR PRO";
    # GPX (6) is a BCFZ or BCFS container; 7 and later are zip files.
    return data[1:19] == b"FICHIER GUITAR PRO" or data[:4] in (b"BCFZ", b"BCFS") \
        or _zip_with("Content/score.gpif")(data)


# The media types a document may be, each with the check its bytes must pass.
# An allowlist, so anything unnamed is refused (fail closed).
DOCUMENT_TYPES: dict[str, Any] = {
    "text/plain": _is_text,                     # lyrics, LRC, ChordPro, transcripts, exports
    "text/markdown": _is_text,
    "application/json": _is_json,               # a saved catalogue response
    "application/pdf": lambda d: d.startswith(b"%PDF-"),
    "application/rtf": lambda d: d.startswith(b"{\\rtf"),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        _zip_with("[Content_Types].xml", "word/document.xml"),
    "application/vnd.oasis.opendocument.text": _is_odt,
    "application/vnd.recordare.musicxml+xml": _is_musicxml,
    "application/vnd.recordare.musicxml": _zip_with("META-INF/container.xml"),
    "application/x-guitar-pro": _is_guitar_pro,
    "audio/midi": lambda d: d.startswith(b"MThd"),   # note events, not sound (flagged in ADR 0085)
    "image/png": lambda d: d.startswith(b"\x89PNG\r\n\x1a\n"),
    "image/jpeg": lambda d: d.startswith(b"\xff\xd8\xff"),
}
TEXT_TYPES = frozenset({"text/plain", "text/markdown"})
ZIP_TYPES = frozenset({
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.recordare.musicxml",
    "application/x-guitar-pro",
})
MIDI_ALIASES = {"audio/x-midi": "audio/midi", "audio/mid": "audio/midi"}


def audio_or_video(data: bytes) -> str | None:
    """The container or codec the bytes begin like, when it is one that
    carries audio or video; None otherwise. A refusal, not an identification:
    it errs towards naming bytes audio or video."""
    head = data[:64]
    if head[:4] == b"RIFF" and head[8:12] in (b"WAVE", b"AVI ", b"RMID"):
        return "RIFF " + head[8:12].decode("latin-1").strip()
    if head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC", b"FRM8"):
        return "AIFF"
    if head[4:8] in (b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip"):
        return "ISO base media (MP4, M4A, MOV)"
    for signature, name in (
            (b"fLaC", "FLAC"), (b"OggS", "Ogg"), (b"ID3", "MP3 (ID3)"), (b"\x1aE\xdf\xa3", "Matroska or WebM"),
            (b"caff", "Core Audio"), (b"wvpk", "WavPack"), (b"MAC ", "Monkey's Audio"), (b"#!AMR", "AMR"),
            (b"FLV\x01", "FLV"), (b"\x30\x26\xb2\x75\x8e\x66\xcf\x11", "ASF (WMA, WMV)"),
            (b"\x00\x00\x01\xba", "MPEG program stream"), (b"\x00\x00\x01\xb3", "MPEG video"),
            (b".snd", "Sun audio"), (b"DSD ", "DSF")):
        if head.startswith(signature):
            return name
    if len(head) >= 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0:
        return "MPEG audio or ADTS frame"
    if len(data) > 188 and data[0] == 0x47 and data[188] == 0x47:
        return "MPEG transport stream"
    return None


def check_document_bytes(declared: str, data: bytes) -> str:
    """The canonical media type of a document's bytes, after checking that the
    declared type is a document type, that the bytes are what it says, and
    that they are not audio or video by declaration, by signature, or inside a
    zip container. Raises DocumentError with a relay refusal code."""
    if not isinstance(declared, str) or not isinstance(data, (bytes, bytearray)):
        raise DocumentError("a staged document needs a media type and bytes")
    media_type = declared.split(";", 1)[0].strip().lower()
    media_type = MIDI_ALIASES.get(media_type, media_type)
    if len(data) == 0:
        raise DocumentError("a staged document has no bytes", "request_malformed")
    if len(data) > MAX_DOCUMENT_BYTES:
        raise DocumentError(f"a staged document is at most {MAX_DOCUMENT_BYTES} bytes", "body_too_large")
    found = audio_or_video(bytes(data))
    if found:
        raise DocumentError(f"the bytes are {found}: the relay stages documents, never audio or video",
                            "content_audio_or_video")
    if (media_type.startswith("audio/") or media_type.startswith("video/")) and media_type != "audio/midi":
        raise DocumentError(f"{media_type} is audio or video: the relay stages documents only",
                            "content_audio_or_video")
    sniff = DOCUMENT_TYPES.get(media_type)
    if sniff is None:
        raise DocumentError(f"{media_type} is not a document type the relay stages", "media_type_unsupported")
    if not sniff(bytes(data)):
        raise DocumentError(f"the bytes are not {media_type}", "content_mismatch")
    if media_type in ZIP_TYPES and _zip_names(bytes(data)) is not None:
        with zipfile.ZipFile(io.BytesIO(bytes(data))) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                with archive.open(info) as member:
                    found = audio_or_video(member.read(256))
                if found:
                    raise DocumentError(f"{info.filename} inside the document is {found}",
                                        "content_audio_or_video")
    return media_type


def check_stage_request(body: Any, caller: str, operator: str) -> tuple[str, bytes, str | None]:
    """A relay staging request, `{media_type, content, filename?}` with the
    bytes as base64 in `content`. Only the active operator's login may stage
    (ADR 0085 D7.2). Returns (media type, bytes, filename)."""
    if caller != operator:
        raise DocumentError("only the operator's login stages documents", "operator_only")
    request = _closed(body, {"media_type", "content"}, {"filename"}, "staging request")
    if not isinstance(request["media_type"], str) or not isinstance(request["content"], str):
        raise DocumentError("staging request media_type and content are text")
    try:
        data = base64.b64decode(request["content"], validate=True)
    except ValueError:
        raise DocumentError("staging request content is not base64") from None
    filename = None
    if "filename" in request:
        filename = _check_filename(request["filename"], "staging request filename")
    return check_document_bytes(request["media_type"], data), data, filename


def _check_filename(value: Any, where: str) -> str:
    name = _text(value, where, MAX_FILENAME)
    if "/" in name or "\\" in name or "\x00" in name or name in (".", ".."):
        raise DocumentError(f"{where} is a name, not a path")
    return name


def check_document_admission(manifest: dict[str, Any], drafts: list[dict[str, Any]],
                             staged: dict[str, str]) -> None:
    """The relay's check of an admission whose manifest lists content: every
    content row is bytes this relay staged (`staged` maps digest to media type,
    for this caller), every Artifact draft carrying it is described in the same
    batch by a document source claim, and that claim states the staged media
    type. Anything else stays with operator tools (ADR 0056)."""
    contents = manifest.get("contents", [])
    if not isinstance(contents, list):
        raise DocumentError("manifest contents must be a list")
    for row in contents:
        digest = row.get("digest") if isinstance(row, dict) else None
        if digest not in staged:
            raise DocumentError(f"content {digest} was not staged by this relay for this caller",
                                "content_not_relayed")
    artifacts = {}
    for draft in drafts:
        if draft.get("shape") == "artifact":
            digest = draft.get("payload", {}).get("content", {}).get("digest")
            artifacts[draft.get("object_id")] = digest
    described: dict[str, str] = {}
    for draft in drafts:
        contracts = draft.get("contracts") or []
        if draft.get("shape") == "claim" and any(
                (c.get("id"), c.get("version")) == DOCUMENT_SOURCE for c in contracts if isinstance(c, dict)):
            for ref in draft.get("refs", []):
                if isinstance(ref, dict) and ref.get("role") == "document":
                    described[ref.get("id")] = draft.get("payload", {}).get("media_type")
    for object_id, digest in artifacts.items():
        if digest not in staged:
            continue
        if object_id not in described:
            raise DocumentError(f"Artifact {object_id} carries staged content but no document source claim "
                                f"in the batch describes it", "content_not_described")
        if described[object_id] != staged[digest]:
            raise DocumentError(f"Artifact {object_id} is described as {described[object_id]}, "
                                f"but was staged as {staged[digest]}", "content_not_described")


def google_doc_url(url: str) -> str:
    """The canonical source URL of a Google Doc, whatever view or export URL it
    was copied from: `https://docs.google.com/document/d/<id>`. Every dated
    export of one Google Doc records the same URL, which is how its exports are
    found together."""
    match = GOOGLE_DOC.fullmatch(url) if isinstance(url, str) else None
    if not match:
        raise DocumentError("not a Google Docs document URL")
    return f"https://docs.google.com/document/d/{match.group(1)}"


# ---------------------------------------------------------------------------
# archive.claim/document_source version 1


DOCUMENT_KINDS = ("lyrics", "chords", "tablature", "score", "transcript", "notes",
                  "booklet", "catalogue_response", "licence", "other")
SOURCE_ROLES = {"document": (1, 1), "derived_from": (0, 8), "work": (0, 16), "run": (0, 1)}


def check_document_source(claim: dict[str, Any], targets: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Check an `archive.claim/document_source` version 1 claim: what a
    document Artifact is (its media type and, optionally, what kind of
    document), where its bytes came from (a URL and when it was retrieved or
    exported), and what it was made from. Returns the payload."""
    where = "document source"
    record = _closed(claim, {"payload", "refs"}, {"revision_id", "agent"}, where)
    by_role = _refs_by_role(record["refs"], SOURCE_ROLES, where)
    payload = _closed(record["payload"], {"media_type"}, {"kinds", "url", "retrieved_at", "filename"},
                      f"{where} payload")
    if payload["media_type"] not in DOCUMENT_TYPES:
        raise DocumentError(f"{where} media_type must be a document type ADR 0085 names")
    if "kinds" in payload:
        kinds = _list(payload["kinds"], f"{where} kinds", 1, len(DOCUMENT_KINDS))
        if any(kind not in DOCUMENT_KINDS for kind in kinds) or kinds != sorted(set(kinds)):
            raise DocumentError(f"{where} kinds must be distinct, ascending, and from {DOCUMENT_KINDS}")
        if "transcript" in kinds and not (by_role["derived_from"] and by_role["run"]):
            raise DocumentError(f"{where} of a transcript pins the run that made it and the audio it transcribed")
    if "url" in payload:
        _url(payload["url"], f"{where} url")
        if "retrieved_at" not in payload:
            raise DocumentError(f"{where} with a url says when the bytes were retrieved")
        if payload["url"].startswith("https://docs.google.com/document/") \
                and google_doc_url(payload["url"]) != payload["url"]:
            raise DocumentError(f"{where} names a Google Doc by its canonical URL")
    if "retrieved_at" in payload:
        _instant_or_date(payload["retrieved_at"], f"{where} retrieved_at")
    if "filename" in payload:
        _check_filename(payload["filename"], f"{where} filename")
    document = by_role["document"][0]
    _artifact(targets, document, f"{where} document")
    for ref in by_role["derived_from"]:
        _artifact(targets, ref, f"{where} derived_from")
        if ref["id"] == document["id"]:
            raise DocumentError(f"{where} says a document was made from itself")
    _distinct_objects(by_role["derived_from"], f"{where} derived_from")
    for ref in by_role["work"]:
        _entity(targets, ref, {"musical_work"}, f"{where} work")
    _distinct_objects(by_role["work"], f"{where} work")
    if by_role["run"]:
        _claim(targets, by_role["run"][0], {DERIVATION_RUN}, f"{where} run")
    return payload


# ---------------------------------------------------------------------------
# archive.claim/timed_lyrics version 1


LOCATOR_SCHEMES = ("text", "lrc")
LYRIC_ROLES = {"document": (1, 1), "source": (1, 1), "stream": (1, 1), "work": (0, 1),
               "run": (0, 1), "corrects": (0, 8), "notes": (0, MAX_LYRIC_NOTES)}


def _check_span(item: dict[str, Any], count: int, held: list[bool], where: str) -> tuple[int, int]:
    start = _int(item["start_sample"], f"{where} start_sample", 0)
    end = _int(item["end_sample"], f"{where} end_sample", 1)
    if end <= start:
        raise DocumentError(f"{where} must end after it starts")
    if end > count:
        raise DocumentError(f"{where} ends past the stream's sample count")
    if "end" in item:
        if item["end"] not in SPAN_ENDS:
            raise DocumentError(f"{where} end must be one of {SPAN_ENDS}")
        held.append(item["end"] == "held")
    return start, end


def _check_confidence(item: dict[str, Any], scaled: bool, where: str) -> None:
    if scaled != ("confidence" in item):
        raise DocumentError(f"{where} carries a confidence exactly when the claim states confidence_scale")
    if scaled and (not isinstance(item["confidence"], str) or not DECIMAL.fullmatch(item["confidence"])):
        raise DocumentError(f"{where} confidence must be exact decimal text")


def _pinned_onsets(by_role: dict[str, list[dict[str, Any]]], targets: dict[str, dict[str, Any]],
                   where: str) -> dict[str, set[int]]:
    """The onsets of each pinned notes claim, by revision, after checking it
    is a notes claim about the lyrics' own `source` revision, pinned once."""
    source = by_role["source"][0]
    onsets: dict[str, set[int]] = {}
    for ref in by_role["notes"]:
        at = f"{where} notes {ref['revision_id']}"
        if ref["revision_id"] in onsets:
            raise DocumentError(f"{at} is pinned twice")
        target = _claim(targets, ref, {NOTES}, at)
        about = [r for r in target.get("refs", []) if isinstance(r, dict) and r.get("role") == "source"]
        if len(about) != 1 or (about[0].get("id"), about[0].get("revision_id")) != (source["id"], source["revision_id"]):
            raise DocumentError(f"{at} is about another Artifact revision than the lyrics' source")
        payload = target.get("payload") if isinstance(target.get("payload"), dict) else {}
        notes = payload.get("notes") if isinstance(payload.get("notes"), list) else []
        onsets[ref["revision_id"]] = {n.get("onset_sample") for n in notes if isinstance(n, dict)}
    return onsets


def _check_syllables(word: dict[str, Any], span: tuple[int, int], count: int, scaled: bool,
                     onsets: dict[str, set[int]], named: set[str], where: str) -> None:
    """A word's syllables: numbered in order, within the word's span, each at
    or after the one before it in samples and in characters; a syllable's
    notes are notes of a pinned claim (ADR 0085, amended 2026-10-19)."""
    previous_number, previous_end, previous_char = 0, span[0], 0
    for index, syllable in enumerate(_list(word["syllables"], f"{where} syllables", 1, MAX_SYLLABLES)):
        at = f"{where} syllable {index}"
        _closed(syllable, {"syllable", "start_char", "end_char", "start_sample", "end_sample"},
                {"confidence", "carried_by"}, at)
        if _int(syllable["syllable"], f"{at} syllable", 1) <= previous_number:
            raise DocumentError(f"{at} must name a later syllable of the word than the syllable before it")
        previous_number = syllable["syllable"]
        start_char = _int(syllable["start_char"], f"{at} start_char", 0)
        end_char = _int(syllable["end_char"], f"{at} end_char", 1)
        if end_char <= start_char:
            raise DocumentError(f"{at} must end after it starts in characters")
        if start_char < previous_char:
            raise DocumentError(f"{at} must start at or after the syllable before it in characters")
        previous_char = end_char
        start, end = _check_span(syllable, count, [], at)
        if start < previous_end or end > span[1]:
            raise DocumentError(f"{at} must lie within its word, at or after the syllable before it")
        previous_end = end
        _check_confidence(syllable, scaled, at)
        if "carried_by" in syllable:
            carried = _closed(syllable["carried_by"], {"notes", "onsets"}, set(), f"{at} carried_by")
            if carried["notes"] not in onsets:
                raise DocumentError(f"{at} carried_by names a notes claim the claim does not pin")
            named.add(carried["notes"])
            sung = _list(carried["onsets"], f"{at} carried_by onsets", 1, MAX_CARRIED_ONSETS)
            for number, onset in enumerate(sung):
                _int(onset, f"{at} carried_by onset {number}", 0)
                if number and onset <= sung[number - 1]:
                    raise DocumentError(f"{at} carried_by onsets must strictly increase")
                if onset not in onsets[carried["notes"]]:
                    raise DocumentError(f"{at} carried_by onset {onset} is no note of {carried['notes']}")


def check_timed_lyrics(claim: dict[str, Any], targets: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Check an `archive.claim/timed_lyrics` version 1 claim: lines, and
    optionally words and their syllables, of one lyric document placed at
    sample spans of one recording's stream. A line is named by its number in
    the document, a word by its number in that line, and a syllable by its
    number in the word and its characters; the claim holds no word of the
    lyric. A syllable may name the notes of a pinned notes claim that carry
    it. Returns the payload; raises DocumentError."""
    where = "timed lyrics"
    record = _closed(claim, {"payload", "refs"}, {"revision_id", "agent"}, where)
    by_role = _refs_by_role(record["refs"], LYRIC_ROLES, where)
    payload = _closed(record["payload"], {"sample_rate", "scheme", "alignment", "lines"},
                      {"hold_rule", "confidence_scale"}, f"{where} payload")
    if payload["scheme"] not in LOCATOR_SCHEMES:
        raise DocumentError(f"{where} scheme must be one of {LOCATOR_SCHEMES}")
    _artifact(targets, by_role["document"][0], f"{where} document")
    source, stream = by_role["source"][0], by_role["stream"][0]
    _artifact(targets, source, f"{where} source")
    if source["id"] == by_role["document"][0]["id"]:
        raise DocumentError(f"{where} places a document on itself")
    facts = _check_stream(targets, stream, source, where)
    if _int(payload["sample_rate"], f"{where} sample_rate", 1) != facts["sample_rate"]:
        raise DocumentError(f"{where} sample_rate differs from the stream's")
    for ref in by_role["work"]:
        _entity(targets, ref, {"musical_work"}, f"{where} work")
    if by_role["run"]:
        _claim(targets, by_role["run"][0], {DERIVATION_RUN}, f"{where} run")
    for ref in by_role["corrects"]:
        _claim(targets, ref, {TIMED_LYRICS}, f"{where} corrects")
    _distinct_objects(by_role["corrects"], f"{where} corrects")
    onsets = _pinned_onsets(by_role, targets, where)
    named: set[str] = set()
    alignment = _closed(payload["alignment"], {"method"}, {"max_error_samples"}, f"{where} alignment")
    _text(alignment["method"], f"{where} alignment method", MAX_NAME)
    if "max_error_samples" in alignment:
        _int(alignment["max_error_samples"], f"{where} alignment max_error_samples", 0)
    scaled = "confidence_scale" in payload
    if scaled:
        _text(payload["confidence_scale"], f"{where} confidence_scale", MAX_NAME)

    held: list[bool] = []
    previous_start = 0
    for index, line in enumerate(_list(payload["lines"], f"{where} lines", 1, MAX_LINES)):
        at = f"{where} line {index}"
        _closed(line, {"line", "start_sample", "end_sample"}, {"end", "words", "confidence"}, at)
        _int(line["line"], f"{at} line", 1)
        start, end = _check_span(line, facts["sample_count"], held, at)
        if start < previous_start:
            raise DocumentError(f"{at} starts before the line before it")
        previous_start = start
        _check_confidence(line, scaled, at)
        word_end, word_number = start, 0
        words = _list(line["words"], f"{at} words", 1, MAX_WORDS) if "words" in line else []
        for number, word in enumerate(words):
            here = f"{at} word {number}"
            _closed(word, {"word", "start_sample", "end_sample"}, {"end", "confidence", "syllables"}, here)
            if _int(word["word"], f"{here} word", 1) <= word_number:
                raise DocumentError(f"{here} must name a later word of the line than the word before it")
            word_number = word["word"]
            w_start, w_end = _check_span(word, facts["sample_count"], held, here)
            if w_start < word_end or w_end > end:
                raise DocumentError(f"{here} must lie within its line, after the word before it")
            word_end = w_end
            _check_confidence(word, scaled, here)
            if "syllables" in word:
                _check_syllables(word, (w_start, w_end), facts["sample_count"], scaled, onsets, named, here)
    if named != set(onsets):
        raise DocumentError(f"{where} pins notes claims that no syllable names")
    if any(held) != ("hold_rule" in payload):
        raise DocumentError(f"{where} states hold_rule exactly when some end is held")
    if "hold_rule" in payload:
        _text(payload["hold_rule"], f"{where} hold_rule", 500)
    return payload


# The characters Unicode gives the White_Space property (PropList.txt). A word
# is a maximal run of other characters; nothing else splits a word.
WHITE_SPACE = frozenset(
    [chr(c) for c in range(0x09, 0x0E)] + [" ", "\u0085", " ", " "]
    + [chr(c) for c in range(0x2000, 0x200B)] + [" ", " ", " ", " ", "　"])
LRC_TAG = re.compile(r"\[[0-9]{1,3}:[0-9]{2}(?:[.:][0-9]{1,3})?\]|<[0-9]{1,3}:[0-9]{2}(?:[.:][0-9]{1,3})?>")


def document_lines(data: bytes, scheme: str) -> list[list[str]]:
    """A text document's lines, each as its words, numbered from 1 in the
    claim (index 0 here is line 1). The bytes are UTF-8, with an optional
    byte order mark; a line ends at LF, CR LF, or a lone CR, and a final line
    break starts no further line. Under `lrc`, time tags (`[mm:ss.xx]` and
    `<mm:ss.xx>`) are removed from a line before its words are counted."""
    text = _utf8_text(data)
    if text is None:
        raise DocumentError("a timed lyrics document must be UTF-8 text")
    if scheme not in LOCATOR_SCHEMES:
        raise DocumentError(f"scheme must be one of {LOCATOR_SCHEMES}")
    raw = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if raw and raw[-1] == "" and text:
        raw.pop()
    lines = []
    for line in raw:
        if scheme == "lrc":
            line = LRC_TAG.sub(" ", line)
        words, current = [], []
        for ch in line:
            if ch in WHITE_SPACE:
                if current:
                    words.append("".join(current))
                    current = []
            else:
                current.append(ch)
        if current:
            words.append("".join(current))
        lines.append(words)
    return lines


def check_locators(claim: dict[str, Any], data: bytes) -> None:
    """Check that every line and word a timed lyrics claim names is in the
    document's bytes, and that each syllable's characters fall within its
    word (counted in Unicode code points). Door does not read content, so
    this is a check for an importer or a view, not an admission rule."""
    payload = claim["payload"]
    lines = document_lines(data, payload["scheme"])
    for index, line in enumerate(payload["lines"]):
        if line["line"] > len(lines):
            raise DocumentError(f"timed lyrics line {index} names line {line['line']}, "
                                f"but the document has {len(lines)}")
        for word in line.get("words", []):
            if word["word"] > len(lines[line["line"] - 1]):
                raise DocumentError(f"timed lyrics line {index} names word {word['word']} of line {line['line']}, "
                                    f"which has {len(lines[line['line'] - 1])}")
            text = lines[line["line"] - 1][word["word"] - 1]
            for syllable in word.get("syllables", []):
                if syllable["end_char"] > len(text):
                    raise DocumentError(f"timed lyrics line {index} word {word['word']} syllable "
                                        f"{syllable['syllable']} ends at character {syllable['end_char']}, "
                                        f"but the word has {len(text)}")


def _syllable_view(word: str, syllable: dict[str, Any]) -> dict[str, Any]:
    out = {"syllable": syllable["syllable"], "text": word[syllable["start_char"]:syllable["end_char"]],
           "start_sample": syllable["start_sample"], "end_sample": syllable["end_sample"]}
    if "carried_by" in syllable:
        out["carried_by"] = {"notes": syllable["carried_by"]["notes"],
                             "onsets": list(syllable["carried_by"]["onsets"])}
    return out


def lyric_view(claim: dict[str, Any], data: bytes) -> list[dict[str, Any]]:
    """The lines of a timed lyrics claim with their words, and each word's
    syllables, joined from the document, for display; a syllable names the
    notes that carry it (`carried_by`: the notes claim and its notes'
    onsets), so a lead sheet sets it under them. A view: regenerable, never
    stored, and shown only where the document itself may be shown."""
    check_locators(claim, data)
    lines = document_lines(data, claim["payload"]["scheme"])
    view = []
    for line in claim["payload"]["lines"]:
        words = lines[line["line"] - 1]
        view.append({
            "line": line["line"], "start_sample": line["start_sample"], "end_sample": line["end_sample"],
            "text": " ".join(words),
            "words": [{"word": w["word"], "text": words[w["word"] - 1],
                       "start_sample": w["start_sample"], "end_sample": w["end_sample"],
                       **({"syllables": [_syllable_view(words[w["word"] - 1], s) for s in w["syllables"]]}
                          if "syllables" in w else {})}
                      for w in line.get("words", [])],
        })
    return view


# ---------------------------------------------------------------------------
# archive.claim/sensitivity version 1


# Exposure classes from least to most exposed (ADR 0010).
CLASSES = ("private", "circle", "service", "open", "distribution")
# Traffic Light Protocol 2.0 labels, and the furthest class each lets an
# object reach (ADR 0016).
LABELS = {"RED": "private", "AMBER": "circle", "GREEN": "service", "CLEAR": "distribution"}
TLP = "https://www.first.org/tlp/"
# The three settings an application offers (ADR 0085 D7.3), and their labels.
APP_SETTINGS = {"operator": "RED", "circle": "AMBER", "published": "CLEAR"}
SENSITIVITY_ROLES = {"subject": (1, MAX_SUBJECTS)}


def check_sensitivity(claim: dict[str, Any], targets: dict[str, dict[str, Any]],
                      agent_kind: str | None = None) -> dict[str, Any]:
    """Check an `archive.claim/sensitivity` version 1 claim: a person's limit
    on how far the objects it names may reach, as a TLP 2.0 label. A person
    asserts it, never a run (ADR 0016, ADR 0018); `agent_kind`, when given, is
    the kind of the envelope agent's Entity. Returns the payload."""
    where = "sensitivity"
    record = _closed(claim, {"payload", "refs"}, {"revision_id", "agent"}, where)
    by_role = _refs_by_role(record["refs"], SENSITIVITY_ROLES, where)
    payload = _closed(record["payload"], {"label"}, {"reason"}, f"{where} payload")
    if payload["label"] not in LABELS:
        raise DocumentError(f"{where} label must be one of {', '.join(LABELS)}")
    if "reason" in payload:
        _text(payload["reason"], f"{where} reason", MAX_TEXT)
    if agent_kind is not None and agent_kind != "person":
        raise DocumentError(f"{where} is asserted by a person")
    for ref in by_role["subject"]:
        _target(targets, ref, f"{where} subject")
    _distinct_objects(by_role["subject"], f"{where} subject")
    return payload


# ---------------------------------------------------------------------------
# archive.claim/rights version 1


RIGHTS_STATEMENTS_BASE = "http://rightsstatements.org/vocab/"
RIGHTS_STATEMENTS = ("InC", "InC-OW-EU", "InC-EDU", "InC-NC", "InC-RUU", "NoC-CR", "NoC-NC",
                     "NoC-OKLR", "NoC-US", "CNE", "UND", "NKC")
PERSONAL_USE = "personal-use-licence"
STATEMENTS = RIGHTS_STATEMENTS + (PERSONAL_USE,)
RIGHTS_ROLES = {"subject": (1, MAX_SUBJECTS), "holder": (0, 8), "licensee": (0, 1),
                "document": (0, 1), "run": (0, 1)}
RIGHTS_SUBJECT_KINDS = {"musical_work", "recording", "release"}


def statement_uri(statement: str) -> str:
    """A RightsStatements.org statement's URI. The local personal-use licence
    has none; its concept is Archive's own."""
    if statement not in RIGHTS_STATEMENTS:
        raise DocumentError(f"{statement!r} is not a RightsStatements.org statement")
    return f"{RIGHTS_STATEMENTS_BASE}{statement}/1.0/"


def check_rights(claim: dict[str, Any], targets: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Check an `archive.claim/rights` version 1 claim: one rights statement,
    with its terms, about Artifacts or Works, recordings, and releases.
    Returns the payload."""
    where = "rights"
    record = _closed(claim, {"payload", "refs"}, {"revision_id", "agent"}, where)
    by_role = _refs_by_role(record["refs"], RIGHTS_ROLES, where)
    payload = _closed(record["payload"], {"statement"}, {
        "jurisdiction", "from", "until", "text", "document_url", "retrieved_at"}, f"{where} payload")
    statement = payload["statement"]
    if statement not in STATEMENTS:
        raise DocumentError(f"{where} statement must be a RightsStatements.org statement or {PERSONAL_USE!r}")
    if (statement == PERSONAL_USE) != bool(by_role["licensee"]):
        raise DocumentError(f"{where} names a licensee exactly when the statement is {PERSONAL_USE!r}")
    for ref in by_role["subject"]:
        target = _target(targets, ref, f"{where} subject")
        if target.get("shape") == "artifact":
            continue
        if target.get("shape") != "entity" or target.get("kind") not in RIGHTS_SUBJECT_KINDS:
            raise DocumentError(f"{where} subject must pin an Artifact, or an Entity of kind "
                                f"{' or '.join(sorted(RIGHTS_SUBJECT_KINDS))}")
    _distinct_objects(by_role["subject"], f"{where} subject")
    for ref in by_role["holder"]:
        _entity(targets, ref, {"person", "organisation"}, f"{where} holder")
    _distinct_objects(by_role["holder"], f"{where} holder")
    for ref in by_role["licensee"]:
        _entity(targets, ref, {"person"}, f"{where} licensee")
    if by_role["run"]:
        _claim(targets, by_role["run"][0], {DERIVATION_RUN}, f"{where} run")
    has_document = bool(by_role["document"])
    if has_document:
        _artifact(targets, by_role["document"][0], f"{where} document")
    for name in ("document_url", "retrieved_at"):
        if name in payload and not has_document:
            raise DocumentError(f"{where} gives {name} without pinning the document it describes")
    if "document_url" in payload:
        _url(payload["document_url"], f"{where} document_url")
    if "retrieved_at" in payload:
        _instant_or_date(payload["retrieved_at"], f"{where} retrieved_at")
    if "jurisdiction" in payload and not (isinstance(payload["jurisdiction"], str)
                                          and JURISDICTION.fullmatch(payload["jurisdiction"])):
        raise DocumentError(f"{where} jurisdiction must be an ISO 3166-1 alpha-2 code")
    start = _partial_date(payload["from"], f"{where} from") if "from" in payload else None
    end = _partial_date(payload["until"], f"{where} until") if "until" in payload else None
    if start and end and end[1] < start[0]:
        raise DocumentError(f"{where} ends before it begins")
    if "text" in payload:
        _text(payload["text"], f"{where} text", MAX_TEXT)
    return payload


# ---------------------------------------------------------------------------
# Reach: how far an object may be shown or shared


@dataclass(frozen=True)
class Reach:
    """How far one object may reach. `labelled` is the furthest class its
    sensitivity claims allow, and `rights` the furthest its rights claims
    allow, never nearer than the circle. `why` names each claim that set a
    limit."""

    labelled: str
    rights: str
    why: tuple[str, ...]

    @property
    def furthest(self) -> str:
        """The furthest class anyone but the operator may be shown it in."""
        return CLASSES[min(CLASSES.index(self.labelled), CLASSES.index(self.rights))]


def _contract(record: dict[str, Any]) -> tuple[str, str] | None:
    contract = record.get("contract")
    return tuple(contract) if contract else None


def heads(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """The records no other record supersedes."""
    records = list(records)
    superseded = {ref["revision_id"] for r in records for ref in r.get("supersedes", [])}
    return [r for r in records if r["revision_id"] not in superseded]


def _pins(records: list[dict[str, Any]]) -> dict[str, set[str]]:
    """Which objects each object pins, over every revision of it, and for a
    document the Artifacts its document source claims say it was made from."""
    pins: dict[str, set[str]] = {}
    for record in records:
        pins.setdefault(record["object_id"], set()).update(
            ref["id"] for ref in record.get("refs", []) if ref.get("role") != "supersedes")
        if _contract(record) == DOCUMENT_SOURCE:
            documents = [ref["id"] for ref in record["refs"] if ref["role"] == "document"]
            made_from = {ref["id"] for ref in record["refs"] if ref["role"] == "derived_from"}
            for document in documents:
                pins.setdefault(document, set()).update(made_from)
    return pins


def closure(object_id: str, records: list[dict[str, Any]],
            pins: dict[str, set[str]] | None = None) -> list[str]:
    """The object and everything it pins, transitively, in the order found.
    `pins`, from `_pins(records)`, saves rebuilding the map per object."""
    pins = _pins(records) if pins is None else pins
    seen, queue = [object_id], [object_id]
    while queue:
        for nxt in sorted(pins.get(queue.pop(0), ())):
            if nxt not in seen:
                seen.append(nxt)
                queue.append(nxt)
    return seen


def _rights_limit(payload: dict[str, Any], holders: set[str], operator: str, today: date) -> str:
    """The furthest class one rights statement lets its subject reach. The
    circle is private, so no statement keeps a subject from it: a personal-use
    licence goes as far as the circle and no further. Service is as far as
    unknown rights go (INV-21): only a statement that permits more, within its
    dates, goes on."""
    statement = payload["statement"]
    if statement == PERSONAL_USE:
        return "circle"
    if statement.startswith("InC") and operator in holders:
        permitted = "distribution"
    elif statement == "NoC-US":
        permitted = "distribution"
    elif statement == "NoC-NC":
        permitted = "open"
    else:
        return "service"
    if "from" in payload and _partial_date(payload["from"], "from")[1] > today:
        return "service"
    if "until" in payload and _partial_date(payload["until"], "until")[0] < today:
        return "service"
    return permitted


class Reacher:
    """Reach over one set of records, read once: the pin map, the head
    sensitivity and rights claims by subject, and each object's kind are
    built when it is made, and each object's reach is computed once. A
    readable set asks the reach of every object it adds; rebuilding these per
    object made it grow with the square of the archive."""

    def __init__(self, records: Iterable[dict[str, Any]], operator: str, today: date) -> None:
        self.records = list(records)
        self.operator, self.today = operator, today
        self.pins = _pins(self.records)
        self.kinds = {r["object_id"]: (r["shape"], r.get("kind")) for r in self.records}
        self.sensitivity: dict[str, list[dict[str, Any]]] = {}
        self.rights: dict[str, list[dict[str, Any]]] = {}
        for record in heads(self.records):
            index = {SENSITIVITY: self.sensitivity, RIGHTS: self.rights}.get(_contract(record))
            if index is None:
                continue
            for subject in dict.fromkeys(ref["id"] for ref in record["refs"] if ref["role"] == "subject"):
                index.setdefault(subject, []).append(record)
        self._known: dict[str, Reach] = {}

    def closure(self, object_id: str) -> list[str]:
        return closure(object_id, self.records, self.pins)

    def reach(self, object_id: str) -> Reach:
        if object_id not in self._known:
            self._known[object_id] = self._reach(object_id)
        return self._known[object_id]

    def _reach(self, object_id: str) -> Reach:
        found = self.closure(object_id)
        why: list[str] = []

        latest: dict[tuple[str, str], dict[str, Any]] = {}
        for subject in found:
            for record in self.sensitivity.get(subject, ()):
                key = (record["agent"], subject)
                held = latest.get(key)
                order = (record["at"], -CLASSES.index(LABELS[record["payload"]["label"]]))
                if held is None or order > (held["at"], -CLASSES.index(LABELS[held["payload"]["label"]])):
                    latest[key] = record
        labelled = "distribution"
        for (agent, subject), record in sorted(latest.items()):
            limit = LABELS[record["payload"]["label"]]
            if CLASSES.index(limit) < CLASSES.index("distribution"):
                why.append(f"{agent} labels {subject} {record['payload']['label']} ({record['revision_id']}): "
                           f"no further than {limit}")
            if CLASSES.index(limit) < CLASSES.index(labelled):
                labelled = limit

        rights = "distribution"
        for subject in found:
            shape, kind = self.kinds.get(subject, (None, None))
            if not (shape == "artifact" or (shape == "entity" and kind in RIGHTS_SUBJECT_KINDS)):
                continue
            about = self.rights.get(subject, [])
            if not about:
                if CLASSES.index("service") < CLASSES.index(rights):
                    rights = "service"
                why.append(f"{subject} has no rights claim: no further than service")
                continue
            for record in about:
                holders = {ref["id"] for ref in record["refs"] if ref["role"] == "holder"}
                limit = _rights_limit(record["payload"], holders, self.operator, self.today)
                if CLASSES.index(limit) < CLASSES.index("distribution"):
                    why.append(f"{subject} is {record['payload']['statement']} ({record['revision_id']}): "
                               f"no further than {limit}")
                if CLASSES.index(limit) < CLASSES.index(rights):
                    rights = limit
        return Reach(labelled, rights, tuple(why))


def reach(object_id: str, records: list[dict[str, Any]], operator: str, today: date) -> Reach:
    """How far `object_id` may reach under the sensitivity and rights claims
    on it and on everything it pins (`closure`). For many objects over the
    same records, make one `Reacher` and ask it.

    `records` are committed records, each `{object_id, revision_id, shape,
    agent, at, payload, refs, supersedes}` with `contract` (id, version) for a
    claim and `kind` for an Entity. `operator` is the active operator's person
    Entity; `today` is the date terms are read on.

    Sensitivity (ADR 0016, ADR 0018): for each person and object, that
    person's latest sensitivity claim about the object is their limit, so only
    they lift it; the strictest limit of all wins. Rights (ADR 0010, INV-21):
    every Artifact, Work, recording, and release in the closure needs a rights
    claim that permits a class beyond service, or it goes no further than
    service; the strictest claim wins, and a personal-use licence keeps it to
    the circle. Rights never limit the circle, which is private."""
    return Reacher(records, operator, today).reach(object_id)


def may_show(object_id: str, viewer: str, audience: str, records: list[dict[str, Any]],
             operator: str, today: date) -> tuple[bool, tuple[str, ...]]:
    """Whether the relay or an application may show `object_id` to `viewer`
    (a person Entity) reaching them as `audience`, one of CLASSES: the
    operator always (no limit takes the operator's own memory, ADR 0018);
    anyone else only within the object's reach, where rights never limit the
    circle. A true answer is necessary,
    never sufficient: the viewer must also be granted the disclosure
    (INV-25), which the circle rules of ADR 0088 decide."""
    if audience not in CLASSES:
        raise DocumentError(f"audience must be one of {CLASSES}")
    if viewer == operator:
        return True, ("the operator sees everything in their own archive",)
    found = reach(object_id, records, operator, today)
    level = CLASSES.index(audience)
    if level > CLASSES.index(found.labelled):
        return False, found.why
    if level > CLASSES.index(found.rights):
        return False, found.why
    return True, found.why
