"""Tracklists, the audio of a recording, and catalogue correspondence
(ADR 0087): payload and reference rules, and the per-track view of credits.

Three contracts, decided by ADR 0087 for specification 0.0.10:

- `archive.claim/tracklist` version 1: a release Entity's media and tracks in
  order, each track pinning the `recording` Entity it carries;
- `archive.claim/recording_audio` version 1: archived audio (an Artifact, an
  audio selection, or a song occurrence) is a recording Entity, whole or in
  part, optionally as one track of a tracklist;
- `archive.claim/catalogue_correspondence` version 1: an artist, release, or
  recording Entity corresponds to a MusicBrainz or Discogs entry, by a SKOS
  mapping term, as ADR 0042 maps a Work.

This module holds their rules as World would re-check them, so an importer can
check a proposal before Door does; Door remains the authority (INV-2). It uses
only the standard library, and reuses ADR 0078's reference and provenance
pieces from world/contributions.py.

The per-track view of credits is a computed view: regenerable, never stored,
never a judgment between claims (INV-7, INV-8). It applies a release's credits
to every track a tracklist places on that release, as MusicBrainz's style
guide reads a release relationship, and says which tracklist placed each one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from world import contributions as c
from world.contributions import ContributionError

TRACKLIST = ("archive.claim/tracklist", "1")
RECORDING_AUDIO = ("archive.claim/recording_audio", "1")
CORRESPONDENCE = ("archive.claim/catalogue_correspondence", "1")
CONTRIBUTION = c.CONTRIBUTION

MAX_MEDIA = 200
MAX_TRACKS_PER_MEDIUM = 999
MAX_TRACKS = 4000
MAX_NUMBER = 20
MAX_TITLE = 500
MAX_FORMAT = 100

EXTENTS = ("whole", "part")
EXACT = "skos:exactMatch"
CLOSE = "skos:closeMatch"
BROAD = "skos:broadMatch"

MBID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
DISCOGS_ID = r"[1-9][0-9]{0,11}"


@dataclass(frozen=True)
class Catalogue:
    """One kind of outside catalogue entry a local Entity may correspond to."""

    catalogue: str          # "musicbrainz" or "discogs"
    entry: str              # the catalogue's own entity type
    subject: str            # the reference role of the local subject
    mappings: frozenset[str]
    pattern: re.Pattern[str]


def _entry(catalogue: str, entry: str, subject: str, mappings: set[str], base: str, ident: str) -> Catalogue:
    return Catalogue(catalogue, entry, subject, frozenset(mappings),
                     re.compile(re.escape(base + entry + "/") + ident + r"\Z"))


MB = "https://musicbrainz.org/"
DISCOGS = "https://www.discogs.com/"
SAME_LEVEL = {EXACT, CLOSE}

# Each external entry kind: which local subject it maps from and by which
# terms. A release group or a Discogs master groups editions; a release
# Entity is one edition, so it maps to a group only as narrower than it
# (skos:broadMatch: the group is the broader concept).
CATALOGUES: tuple[Catalogue, ...] = (
    _entry("musicbrainz", "artist", "artist", SAME_LEVEL, MB, MBID),
    _entry("discogs", "artist", "artist", SAME_LEVEL, DISCOGS, DISCOGS_ID),
    _entry("musicbrainz", "release", "release", SAME_LEVEL, MB, MBID),
    _entry("discogs", "release", "release", SAME_LEVEL, DISCOGS, DISCOGS_ID),
    _entry("musicbrainz", "release-group", "release", {BROAD}, MB, MBID),
    _entry("discogs", "master", "release", {BROAD}, DISCOGS, DISCOGS_ID),
    _entry("musicbrainz", "recording", "recording", SAME_LEVEL, MB, MBID),
)

SUBJECT_KINDS = {
    "artist": {"person", "organisation"},
    "release": {"release"},
    "recording": {"recording"},
}


class CatalogueError(ValueError):
    """A tracklist, recording audio, or correspondence claim that breaks a
    rule of ADR 0087."""


def _wrapped(check):
    """Report ADR 0078's shared rules, reused here, as this module's error."""
    def run(*args, **kwargs):
        try:
            return check(*args, **kwargs)
        except ContributionError as error:
            if isinstance(error, CatalogueError):
                raise
            raise CatalogueError(str(error)) from None
    run.__name__, run.__doc__ = check.__name__, check.__doc__
    return run


def external_entry(uri: Any) -> Catalogue:
    """The catalogue entry kind a canonical external URI names."""
    if isinstance(uri, str):
        for kind in CATALOGUES:
            if kind.pattern.fullmatch(uri):
                return kind
    raise CatalogueError(
        f"external {uri!r} is not a canonical MusicBrainz artist, release, release-group, or recording URI "
        f"(https://musicbrainz.org/<type>/<MBID>) or Discogs artist, release, or master URI "
        f"(https://www.discogs.com/<type>/<id>)")


# ---------------------------------------------------------------------------
# Shared provenance


SOURCE_FIELDS = {"document_url", "retrieved_at", "locator", "text", "confidence"}


def _check_source(payload: dict[str, Any], by_role, targets, where: str) -> None:
    """ADR 0078's asserter fields: a machine pins its `run`, an outside source
    pins its `document` (and only then gives `document_url`, `retrieved_at`,
    `locator`), a person is the envelope agent. `text` is optional here: a
    tracklist's own titles and numbers are the source's wording."""
    if by_role["run"]:
        c._claim(targets, by_role["run"][0], {c.DERIVATION_RUN}, f"{where} run")
    has_document = bool(by_role["document"])
    if has_document:
        c._artifact(targets, by_role["document"][0], f"{where} document")
    for name in ("document_url", "retrieved_at", "locator"):
        if name in payload and not has_document:
            raise CatalogueError(f"{where} gives {name} without pinning the document it describes")
    # 0078's check needs `text` with a document; supply a placeholder so only
    # the field formats are re-checked, then check `text` itself.
    probe = {k: v for k, v in payload.items() if k in SOURCE_FIELDS}
    if has_document and "text" not in probe:
        probe["text"] = "-"
    c._check_provenance(probe, {"run": [], "document": by_role["document"]}, targets, where)


# ---------------------------------------------------------------------------
# Tracklist


@_wrapped
def check_tracklist(claim: dict[str, Any], targets: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Check an `archive.claim/tracklist` version 1 claim. `targets` maps each
    pinned revision ID to what Door would read from it (see
    world.contributions._target). Returns the payload; raises CatalogueError."""
    where = "tracklist"
    record = c._closed(claim, {"payload", "refs"}, {"revision_id", "agent"}, where)
    payload = c._closed(record["payload"], {"media"}, {"partial"} | SOURCE_FIELDS, f"{where} payload")
    by_role = c._refs_by_role(record["refs"], {
        "release": (1, 1), "track": (1, MAX_TRACKS), "run": (0, 1), "document": (0, 1)}, where)
    _check_source(payload, by_role, targets, where)
    c._entity(targets, by_role["release"][0], {"release"}, f"{where} release")
    partial = payload.get("partial", False)
    if "partial" in payload and partial is not True:
        raise CatalogueError(f"{where} partial is true, and given only when the list leaves tracks out")

    media = c._list(payload["media"], f"{where} media", 1, MAX_MEDIA)
    total = 0
    previous = 0
    for m, medium in enumerate(media):
        at = f"{where} medium {m}"
        c._closed(medium, {"position", "tracks"}, {"format", "title"}, at)
        position = c._int(medium["position"], f"{at} position", 1)
        if position <= previous:
            raise CatalogueError(f"{at} position must be after the one before it")
        if not partial and position != m + 1:
            raise CatalogueError(f"{at} position must be {m + 1}: a complete tracklist numbers its media 1, 2, ...")
        previous = position
        for name, maximum in (("format", MAX_FORMAT), ("title", MAX_TITLE)):
            if name in medium:
                c._text(medium[name], f"{at} {name}", maximum)
        tracks = c._list(medium["tracks"], f"{at} tracks", 1, MAX_TRACKS_PER_MEDIUM)
        last = -1
        for t, track in enumerate(tracks):
            here = f"{at} track {t}"
            c._closed(track, {"position"}, {"number", "title", "artist_text", "length_ms"}, here)
            number = c._int(track["position"], f"{here} position", 0, MAX_TRACKS_PER_MEDIUM)
            if number <= last:
                raise CatalogueError(f"{here} position must be after the one before it")
            if number == 0 and t != 0:
                raise CatalogueError(f"{here} position 0, a hidden pregap track, comes first")
            if not partial and last >= 0 and number != last + 1:
                raise CatalogueError(f"{here} position must be {last + 1}: a complete tracklist leaves no gap")
            if not partial and t == 0 and number not in (0, 1):
                raise CatalogueError(f"{here} position must be 0 or 1: a complete tracklist starts at its first track")
            last = number
            for name, maximum in (("number", MAX_NUMBER), ("title", MAX_TITLE), ("artist_text", MAX_TITLE)):
                if name in track:
                    c._text(track[name], f"{here} {name}", maximum)
            if "length_ms" in track:
                c._int(track["length_ms"], f"{here} length_ms", 1)
        total += len(tracks)
    if total > MAX_TRACKS:
        raise CatalogueError(f"{where} lists more than {MAX_TRACKS} tracks")
    if total != len(by_role["track"]):
        raise CatalogueError(f"{where} pins {len(by_role['track'])} track references for {total} tracks; "
                             f"it pins one per track, in order")
    for index, ref in enumerate(by_role["track"]):
        c._entity(targets, ref, {"recording"}, f"{where} track reference {index}")
    return payload


def tracks(tracklist: dict[str, Any]) -> list[tuple[int, int, dict[str, Any]]]:
    """A checked tracklist's tracks as (medium position, track position, the
    `track` reference it pins), in order."""
    pins = [r for r in tracklist["refs"] if r["role"] == "track"]
    found = []
    for medium in tracklist["payload"]["media"]:
        for track in medium["tracks"]:
            found.append((medium["position"], track["position"], pins[len(found)]))
    return found


# ---------------------------------------------------------------------------
# The audio of a recording


AUDIO_CLAIMS = {c.AUDIO_SELECTION, c.SONG_OCCURRENCE}


@_wrapped
def check_recording_audio(claim: dict[str, Any], targets: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Check an `archive.claim/recording_audio` version 1 claim: archived audio
    is a recording Entity, whole or in part, optionally as one track of a
    tracklist. Returns the payload; raises CatalogueError."""
    where = "recording audio"
    record = c._closed(claim, {"payload", "refs"}, {"revision_id", "agent"}, where)
    payload = c._closed(record["payload"], {"extent"}, {"medium", "track"} | SOURCE_FIELDS, f"{where} payload")
    by_role = c._refs_by_role(record["refs"], {
        "recording": (1, 1), "audio": (1, 1), "tracklist": (0, 1), "run": (0, 1), "document": (0, 1)}, where)
    _check_source(payload, by_role, targets, where)
    if payload["extent"] not in EXTENTS:
        raise CatalogueError(f"{where} extent must be one of {', '.join(EXTENTS)}")
    recording = by_role["recording"][0]
    c._entity(targets, recording, {"recording"}, f"{where} recording")
    audio = c._target(targets, by_role["audio"][0], f"{where} audio")
    if not (audio.get("shape") == "artifact"
            or (audio.get("shape") == "claim" and tuple(audio.get("contract", ())) in AUDIO_CLAIMS)):
        raise CatalogueError(f"{where} audio must pin an Artifact, or an audio selection or song occurrence "
                             f"version 2 Claim")
    placed = {"medium", "track"} & set(payload)
    if bool(by_role["tracklist"]) != bool(placed) or (placed and placed != {"medium", "track"}):
        raise CatalogueError(f"{where} gives medium and track exactly when it pins a tracklist")
    if by_role["tracklist"]:
        listed = c._claim(targets, by_role["tracklist"][0], {TRACKLIST}, f"{where} tracklist")
        medium = c._int(payload["medium"], f"{where} medium", 1)
        track = c._int(payload["track"], f"{where} track", 0)
        try:
            entries = tracks(listed)
        except (KeyError, TypeError, IndexError):
            raise CatalogueError(f"{where} tracklist is not a readable tracklist version 1 claim") from None
        match = [pin for m, t, pin in entries if (m, t) == (medium, track)]
        if not match:
            raise CatalogueError(f"{where} names medium {medium} track {track}, which the tracklist does not list")
        if match[0]["id"] != recording["id"]:
            raise CatalogueError(f"{where} names a track the tracklist says carries another recording")
    return payload


# ---------------------------------------------------------------------------
# Catalogue correspondence


@_wrapped
def check_correspondence(claim: dict[str, Any], targets: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Check an `archive.claim/catalogue_correspondence` version 1 claim: one
    artist, release, or recording Entity maps to one MusicBrainz or Discogs
    entry by a SKOS mapping term. Returns the payload; raises CatalogueError."""
    where = "correspondence"
    record = c._closed(claim, {"payload", "refs"}, {"revision_id", "agent"}, where)
    payload = c._closed(record["payload"], {"external", "mapping"}, SOURCE_FIELDS, f"{where} payload")
    by_role = c._refs_by_role(record["refs"], {
        "artist": (0, 1), "release": (0, 1), "recording": (0, 1), "run": (0, 1), "document": (0, 1)}, where)
    subjects = [role for role in SUBJECT_KINDS if by_role[role]]
    if len(subjects) != 1:
        raise CatalogueError(f"{where} pins exactly one artist, release, or recording")
    subject = subjects[0]
    c._entity(targets, by_role[subject][0], SUBJECT_KINDS[subject], f"{where} {subject}")
    _check_source(payload, by_role, targets, where)
    kind = external_entry(payload["external"])
    if kind.subject != subject:
        raise CatalogueError(f"{where} maps its {subject!r} reference to a {kind.catalogue} {kind.entry}, "
                             f"which corresponds only to a {kind.subject!r} reference")
    if payload["mapping"] not in kind.mappings:
        raise CatalogueError(f"{where} maps to a {kind.catalogue} {kind.entry} only by "
                             f"{' or '.join(sorted(kind.mappings))}")
    return payload


def subject_of(correspondence: dict[str, Any]) -> dict[str, Any]:
    """The local Entity reference a checked correspondence maps."""
    return next(r for r in correspondence["refs"] if r["role"] in SUBJECT_KINDS)


def corresponding(external: str, correspondences: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    """Every local Entity object that checked correspondence claims map to
    `external`, as (entity object ID, mapping term, claim revision ID), in
    input order. An importer recognises an Entity it made before by this
    lookup. Two claims may map two Entities to one entry; both are returned,
    and nothing merges them."""
    return [(subject_of(claim)["id"], claim["payload"]["mapping"], claim["revision_id"])
            for claim in correspondences if claim["payload"]["external"] == external]


# ---------------------------------------------------------------------------
# Credits per track


@dataclass(frozen=True)
class TrackCredit:
    """One contribution claim that applies to a recording. `via` is
    `recording` (it names the recording Entity), `audio` (it names audio that
    a recording audio claim says is the recording), or `release` (it credits a
    release whose tracklist places the recording). `why` names the claims
    that placed it. `refined_by` lists recording-level credits, on the same
    recording, of the same contributor and role type, that refine a release
    credit."""

    revision_id: str
    scope: str
    via: str
    why: str
    refined_by: tuple[str, ...] = ()


def _ref(claim: dict[str, Any], role: str) -> dict[str, Any] | None:
    return next((r for r in claim["refs"] if r["role"] == role), None)


def track_credits(recording_id: str, tracklists: list[dict[str, Any]], contributions: list[dict[str, Any]],
                  recording_audio: list[dict[str, Any]] = ()) -> list[TrackCredit]:
    """The contributions that apply to one recording Entity, each with why.

    A release credit applies to every track a tracklist of that release
    places the recording on, as MusicBrainz reads a release relationship;
    each tracklist that places it is named, and two tracklists that disagree
    are both used, neither preferred. A credit about archived audio applies
    when a recording audio claim says that audio is the recording. A
    recording-level credit refines a release credit when it names the same
    contributor object and role type, and its instruments and vocals are
    within the release credit's (when the release credit names any). Nothing
    is hidden and nothing is ranked by asserter."""
    audio_of = {}
    for claim in recording_audio:
        if _ref(claim, "recording")["id"] == recording_id:
            audio_of.setdefault(_ref(claim, "audio")["id"], []).append(claim["revision_id"])
    on_release: dict[str, list[str]] = {}
    for listed in tracklists:
        release = _ref(listed, "release")["id"]
        for medium, track, pin in tracks(listed):
            if pin["id"] == recording_id:
                on_release.setdefault(release, []).append(
                    f"tracklist {listed['revision_id']} places it at medium {medium} track {track}")

    found: list[dict[str, Any]] = []
    for claim in contributions:
        scope = claim["payload"]["scope"]
        if scope == "release":
            release = _ref(claim, "release")["id"]
            if release in on_release:
                found.append({"claim": claim, "via": "release",
                              "why": f"it credits release {release}; " + "; ".join(on_release[release])})
            continue
        target = _ref(claim, "recording")
        if scope in ("span", "channel") and target is None:
            target = _ref(claim, "source")
        if target is None:
            continue
        if target["id"] == recording_id:
            found.append({"claim": claim, "via": "recording", "why": f"it names recording {recording_id}"})
        elif target["id"] in audio_of:
            found.append({"claim": claim, "via": "audio",
                          "why": f"it names audio {target['id']}, which recording audio "
                                 f"{', '.join(audio_of[target['id']])} says is recording {recording_id}"})

    result = []
    for entry in found:
        claim = entry["claim"]
        refined = ()
        if entry["via"] == "release":
            refined = tuple(other["claim"]["revision_id"] for other in found
                            if other["via"] != "release" and _same_credit(other["claim"], claim))
        result.append(TrackCredit(claim["revision_id"], claim["payload"]["scope"], entry["via"], entry["why"],
                                  refined))
    return result


def _same_credit(fine: dict[str, Any], release: dict[str, Any]) -> bool:
    fc, rc = _ref(fine, "contributor"), _ref(release, "contributor")
    fr, rr = fine["payload"].get("role"), release["payload"].get("role")
    if not (fc and rc and fc["id"] == rc["id"] and fr and rr and fr["type"] == rr["type"]):
        return False
    return all(name not in rr or (name in fr and set(fr[name]) <= set(rr[name]))
               for name in ("instruments", "vocals"))
