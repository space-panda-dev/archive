"""Contributions and band membership (ADR 0078): payload and reference rules,
the inherited MusicBrainz roles, and the resolved view that prefers a finer
scope and says why.

The two contracts, `archive.claim/contribution` version 1 and
`archive.claim/band_membership` version 1, are decided by ADR 0078 and
installed by specification 0.0.9 (D-17). This module holds their rules as
World re-checks them when it projects such a claim, so an importer can check
a proposal before Door does; Door remains the authority (INV-2), and a test
feeds the same accepted and refused claims to both. It uses only the
standard library.

Roles are inherited, not invented (ADR 0012): a role's `type` is a MusicBrainz
relationship type name for the entity pair its scope implies (artist-work,
artist-release, artist-recording, place-release, place-recording), its
attributes and vocals are MusicBrainz relationship attribute names, and an
instrument is a MusicBrainz instrument URI. MusicBrainz's relationship types,
attributes, and instruments are CC0 data. The identifiers below were read from
musicbrainz.org's relationship and attribute pages on 2026-10-18;
specification 0.0.9 ships them as a SKOS scheme (D-17), and
world/tests/test_role_scheme.py checks this table against it and against the
contract bundles. The only local role is `label`, which MusicBrainz keeps as
release data rather than as a relationship.

The resolved view is a computed view: regenerable, never stored, and never a
judgment between claims (INV-7, INV-8). It never ranks asserters.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable
from urllib.parse import quote

CONTRIBUTION = ("archive.claim/contribution", "1")
BAND_MEMBERSHIP = ("archive.claim/band_membership", "1")
AUDIO_STREAM = ("archive.claim/audio_stream", "1")
DERIVATION_RUN = ("archive.claim/derivation_run", "1")
AUDIO_SELECTION = ("archive.claim/audio_selection", "2")
SONG_OCCURRENCE = ("archive.claim/song_occurrence", "2")

SCOPES = ("work", "release", "recording", "span", "channel")
OBJECT_ID = re.compile(r"urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
REVISION_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
INSTRUMENT = re.compile(r"https://musicbrainz\.org/instrument/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
URL = re.compile(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+\Z")
DECIMAL = re.compile(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?\Z")
INSTANT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?Z\Z")
FULL_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
PARTIAL_DATE = re.compile(r"([0-9]{4})(?:-([0-9]{2})(?:-([0-9]{2}))?)?\Z")
MAX_TEXT = 1000
MAX_NAME = 200
MAX_URL = 2000
MAX_SPANS = 1000
MAX_INSTRUMENTS = 16
MAX_ANALYSED = 8


class ContributionError(ValueError):
    """A contribution or band membership claim that breaks a rule of ADR 0078."""


# ---------------------------------------------------------------------------
# Inherited roles


@dataclass(frozen=True)
class RoleType:
    """One MusicBrainz relationship type, for one entity pair."""

    name: str
    pair: str               # MusicBrainz entity pair, e.g. "artist-recording"
    gid: str | None         # MusicBrainz link type MBID; None for the local `label`
    attributes: frozenset[str] = frozenset()
    instruments: bool = False   # takes MusicBrainz instruments
    vocals: bool = False        # takes MusicBrainz vocal attributes
    place: bool = False         # the contributor is a place, which v1 cannot pin

    @property
    def requires_instrument(self) -> bool:
        return self.name == "instrument"


def _types(pair: str, rows: Iterable[tuple], **flags: bool) -> dict[str, RoleType]:
    table = {}
    for name, gid, attributes, *rest in rows:
        kinds = set(rest[0]) if rest else set()
        table[name] = RoleType(name, pair, gid, frozenset(attributes.split()),
                               instruments="instrument" in kinds, vocals="vocal" in kinds, **flags)
    return table


PERF = "additional guest solo"
TECH = "additional assistant associate co"

ARTIST_RECORDING = _types("artist-recording", [
    ("performer", "628a9658-f54c-4142-b0c0-95f031b544da", PERF),
    ("instrument", "59054b12-01ac-43ee-a618-285fd397e461", PERF, ("instrument",)),
    ("vocal", "0fdbe3c6-7700-4a31-ae54-b53f06ae1cfa", PERF, ("vocal",)),
    ("performing orchestra", "3b6616c5-88ba-4341-b4ee-81ce1e6d7ebb", "additional"),
    ("conductor", "234670ce-5f22-4fd0-921b-ef1662695c5d", "additional assistant"),
    ("chorus master", "45115945-597e-4cb9-852f-4e6ba583fcc8", "additional assistant"),
    ("concertmaster", "ffeaa74f-8295-45ee-a2f2-7c0cc1f73b1e", "guest"),
    ("arranger", "22661fb8-cdb7-4f67-8385-b2a8be6c9f0d", "additional associate co"),
    ("instrument arranger", "4820daa1-98d6-4f8b-aa4b-6895c5b79b27", "additional associate co", ("instrument",)),
    ("orchestrator", "38fa7405-f9a5-48cb-827a-8ac601933ba0", "additional"),
    ("vocal arranger", "8a2799e8-a7e2-41ce-a7da-b5f520687216", "additional associate co", ("vocal",)),
    ("remixer", "7950be4d-13a3-48e7-906b-5af562e39544", "additional assistant"),
    ("producer", "5c0ceac3-feb4-41f0-868d-dc06f6e27fc0", TECH + " executive"),
    ("engineer", "5dcc52af-7064-4051-8d62-7d80f4c3c907", TECH + " executive"),
    ("audio", "ca8d6d99-b847-439c-b0ec-33d8a1b942bc", TECH),
    ("sound", "0cd6aa63-c297-42ed-8725-c16d31913a98", TECH),
    ("mix", "3e3102e1-1896-4f50-b5b2-dd9824e46efe", TECH),
    ("recording", "a01ee869-80a8-45ef-9447-c59e91aa7926", TECH),
    ("field recordist", "fc9b963a-29fa-4949-b22a-3bffd2440024", ""),
    ("programming", "36c50022-44e0-488d-994b-33f11d20301e", "additional assistant associate", ("instrument",)),
    ("editor", "40dff87a-e475-4aa6-b615-9935b564d756", TECH),
    ("balance", "0748fa55-56b5-4ad5-8ce8-15b97f82a0c2", TECH),
    ("instrument technician", "e88fefc3-042b-4f28-83af-6a79793b630b", "", ("instrument",)),
])

ARTIST_RELEASE = _types("artist-release", [
    ("performer", "888a2320-52e4-4fe8-a8a0-7a4c8dfde167", PERF),
    ("instrument", "67555849-61e5-455b-96e3-29733f0115f5", PERF, ("instrument",)),
    ("vocal", "eb10f8a0-0f4c-4dce-aa47-87bcb2bc42f3", PERF, ("vocal",)),
    ("performing orchestra", "23a2e2e7-81ca-4865-8d05-2243848a77bf", "additional"),
    ("conductor", "9ae9e4d0-f26b-42fb-ab5c-1149a47cf83b", "additional assistant"),
    ("chorus master", "b9129850-73ec-4af5-803c-1c12b97e25d2", "additional assistant"),
    ("compiler", "2f81887a-8674-4d8b-bd48-8bfd4c6fa332", ""),
    ("remixer", "ac6a86db-f757-4815-a07e-744428d2382b", "additional assistant"),
    ("arranger", "34d5334e-a4c8-4b65-a5f8-bbcc9c81d13d", "additional associate co"),
    ("instrument arranger", "18f159bb-44f0-4aef-b198-a4736ad9b659", "additional associate co", ("instrument",)),
    ("orchestrator", "04e1f0b6-ef40-4168-ba25-42a87729fe09", "additional"),
    ("vocal arranger", "d7d9128d-e676-4d8f-a353-f48a55a98501", "additional associate co", ("vocal",)),
    ("producer", "8bf377ba-8d71-4ecc-97f2-7bb2d8a2a75f", TECH + " executive"),
    ("engineer", "87e922ba-872e-418a-9f41-0a63aa3c30cc", TECH + " executive"),
    ("audio", "b04848d7-dbd9-4be0-9d8c-13df6d6e40db", TECH),
    ("mastering", "84453d28-c3e8-4864-9aae-25aa968bcf9e", TECH + " pre re"),
    ("lacquer cut", "904e57f3-cbbc-43ab-8798-13e710e400d3", ""),
    ("sound", "271306ca-c77f-4fe0-94bc-dd4b87ae0205", TECH),
    ("mix", "6cc958c0-533b-4540-a281-058fbb941890", TECH),
    ("recording", "023a6c6d-80af-4f88-ae69-f5f6213f9bf4", TECH),
    ("programming", "617063ad-dbb5-4877-9ba0-ba2b9198d5a7", "additional assistant associate", ("instrument",)),
    ("editor", "f30c29d3-a3f1-420d-9b6c-a750fd6bc2aa", TECH),
    ("balance", "97169e5e-c978-486e-a5ea-da353ca9ea42", TECH),
    ("transfer", "9fe3ca27-f9df-4e06-ab2d-84947f3f897e", TECH),
    ("art direction", "f3b80a09-5ebf-4ad2-9c46-3e6bce971d1b", "additional assistant"),
    ("design", "9c02ea37-7680-4fb5-8555-e330c7aa885b", "additional assistant"),
    ("graphic design", "cf43b79e-3299-4b0c-9244-59ea06337107", "additional"),
    ("illustration", "a6029157-d96b-4dc3-9f73-f99f76423d11", "additional"),
    ("photography", "0b58dc9b-9c49-4b19-bb58-9c06d41c8fbf", "additional"),
    ("liner notes", "01323b4f-7aba-410c-8c91-cb224b963a40", "additional translator"),
    ("booklet editor", "74518a6b-589d-460e-8dd7-a8383851040a", ""),
    ("copyright", "730b5251-7432-4896-8fc6-e1cba943bfe1", ""),
    ("phonographic copyright", "01d3488d-8d2a-4cff-9226-5250404db4dc", ""),
    ("licensor", "eaaf08cf-a698-4e3b-a871-f9570f8fdab1", ""),
    ("instrument technician", "bd780597-0b67-4b97-a4a0-671a70e1182d", "", ("instrument",)),
])
# MusicBrainz keeps a release's label as release data, not a relationship.
ARTIST_RELEASE["label"] = RoleType("label", "label-release", None)

ARTIST_WORK = _types("artist-work", [
    ("writer", "a255bca1-b157-4518-9108-7b147dc3fc68", "additional"),
    ("composer", "d59d99ea-23d4-4a80-b066-edca32ee158f", "additional"),
    ("lyricist", "3e48faba-ec01-47fd-8e89-30e81161661c", "additional"),
    ("librettist", "7474ab81-486f-40b5-8685-3a4f8ea624cb", "additional"),
    ("translator", "da6c5d8a-ce13-474d-9375-61feb29039a5", "additional"),
    ("revised by", "eeb9c319-9fde-4313-b76d-29db1576aad8", ""),
    ("arranger", "d3fd781c-5894-47e2-8c12-86cc0e2c8d08", "additional associate co"),
    ("instrument arranger", "0084e70a-873e-4f7f-b3ff-635b9e863dae", "additional associate co", ("instrument",)),
    ("orchestrator", "0a1771e1-8639-4740-8a43-bdafc050c3da", "additional"),
    ("vocal arranger", "6a88b92b-8fb5-41b3-aa1f-169ee7e05ed6", "additional associate co", ("vocal",)),
    ("adapter", "89fff34a-2c51-4068-9709-50d6c6dd3d01", "additional"),
])

PLACE_RECORDING = _types("place-recording", [
    ("recorded at", "ad462279-14b0-4180-9b58-571d0eef7c51", ""),
    ("mixed at", "11d74801-1493-4a5d-bc0f-4ddc537acddb", ""),
    ("edited at", "069c1cf0-a9b2-448e-8486-1eced48b48f9", ""),
    ("engineered at", "f845a95e-b2b5-4a94-9645-fc8b031ab0bd", ""),
    ("produced at", "6bc3827d-bc20-4621-ae14-9c3707ad140a", ""),
    ("remixed at", "88183045-b769-48ba-a55c-c58450903241", ""),
    ("arranged at", "ce8dbff2-0f07-45f3-8b7c-2ea40d82cbfe", ""),
    ("programmed at", "e9771fa3-57ce-41cd-895f-52e70ced99cd", ""),
], place=True)

PLACE_RELEASE = _types("place-release", [
    ("recorded at", "3b1fae9f-5b22-42c5-a40c-d1e5c9b90251", ""),
    ("mixed at", "8ebfc2f6-0ac7-40f6-b03e-67fe3428f5d4", ""),
    ("mastered at", "5d075afa-6bb8-4327-9528-e3e4d3d68f49", ""),
    ("edited at", "9b678c30-453a-47c4-8f69-9ba96b2c5e9c", ""),
    ("engineered at", "b35aae66-5578-41d1-b34b-1c9b1897ad49", ""),
    ("produced at", "1c73465d-9bf7-41f4-bcb6-fb1852ffa64c", ""),
    ("remixed at", "32af03cf-8879-4d07-b7c2-68c6cbf060bd", ""),
    ("lacquer cut at", "e87ae6d1-4453-4e73-aa29-86a6badc1f1d", ""),
    ("pressed at", "eabf5903-8467-4329-b905-7504336e8241", ""),
    ("manufactured at", "3de9787f-c148-4b4f-b4bf-cb9a2616a092", ""),
], place=True)

# Which role types each scope accepts: the MusicBrainz entity pair it implies.
# A span or a channel is part of a recording, so it takes recording roles.
ROLE_TYPES: dict[str, dict[str, RoleType]] = {
    "work": ARTIST_WORK,
    "release": {**ARTIST_RELEASE, **PLACE_RELEASE},
    "recording": {**ARTIST_RECORDING, **PLACE_RECORDING},
    "span": {**ARTIST_RECORDING, **PLACE_RECORDING},
    "channel": dict(ARTIST_RECORDING),
}

# MusicBrainz relationship attributes that the role types above and band
# membership take, by name, with their MusicBrainz attribute MBIDs. Each name
# is an attribute at the top of MusicBrainz's tree; every one has an MBID.
ATTRIBUTES: dict[str, str] = {
    "additional": "0a5341f8-3b1d-4f99-a0c6-26b7f4e42c7f",
    "assistant": "8c4196b1-7053-4b16-921a-f22b2898ed44",
    "associate": "8d23d2dd-13df-43ea-85a0-d7eb38dc32ec",
    "co": "ac6f6b4c-a4ec-4483-a04e-9f425a914573",
    "eponymous": "15eea3b8-5655-43c8-b2c2-dd03c901a79b",
    "executive": "e0039285-6667-4f94-80d6-aa6520c6d359",
    "guest": "b3045913-62ac-433e-9211-ac683cdf6b5c",
    "original": "4fd3b255-a7d7-4424-9a63-40fa543b601c",
    "pre": "288b973a-26ea-4880-8eca-45af4b8e8665",
    "re": "9b72452f-550e-4ace-93ed-fb8789cdc245",
    "solo": "63daa0d3-9b63-4434-acff-4977c07808ca",
    "translator": "25dfb08e-9b99-44db-b30c-1d6ec6747af8",
}

# MusicBrainz vocal attributes (the `vocal` tree), by name, with their MBIDs.
VOCAL_IDS: dict[str, str] = {
    "lead vocals": "8e2a3255-87c2-4809-a174-98cb3704f1a5",
    "alto vocals": "9f63c4ba-b76f-40d5-9e99-2fb08bd4c286",
    "bass-baritone vocals": "629763ee-3dc7-4225-b209-0ebb6d49bfab",
    "contralto vocals": "80d94f2e-e38f-4561-add2-c866f083d276",
    "treble vocals": "433631a2-68b7-49e6-90b4-5af19e26fc75",
    "baritone vocals": "a40b43ed-2722-4b4a-98a5-478283cdf8df",
    "bass vocals": "1bfdb77e-f339-4e8e-9627-331ca9d9e920",
    "countertenor vocals": "435a19f5-55dc-4a08-8c59-4257680b4217",
    "mezzo-soprano vocals": "f81325d7-593c-4197-b776-4f8a62c67a8e",
    "soprano vocals": "e88f0be8-a07e-4c0d-bd06-e938eea4d5f6",
    "tenor vocals": "122c11da-651f-46cc-9118-c523a14afa1d",
    "meane vocals": "a31bdea5-7ef5-4a35-81c9-2abed9850d6d",
    "choir vocals": "43427f08-837b-46b8-bc77-483453af6a7b",
    "background vocals": "75052401-7340-4e5b-a71d-ea024a128849",
    "other vocals": "c359be96-620a-435c-bd25-2eb0ce81a22e",
    "spoken vocals": "d3a36e62-a7c4-4eb9-839f-adfebe87ac12",
    "whistling": "ed220196-6250-456d-ab7b-465bee605b16",
}
VOCALS = frozenset(VOCAL_IDS)

# "member of band" (5be4c609-9afa-4ea0-910b-12ffb71e3821), artist-artist.
MEMBERSHIP_ATTRIBUTES = frozenset({"additional", "original", "eponymous"})


# "member of band" as a role: band membership's attributes, instruments, and
# vocals are this type's.
MEMBER_OF_BAND = RoleType("member of band", "artist-artist", "5be4c609-9afa-4ea0-910b-12ffb71e3821",
                          MEMBERSHIP_ATTRIBUTES, instruments=True, vocals=True)

# The role scheme of specification 0.0.9 (D-17) names each type, pair, and
# vocal attribute by these IRIs; World's domain graph uses the same ones.
ARCH = "urn:uuid:3e8b29f8-70c9-403e-bb4c-2280d59eca7f#"
MUSICBRAINZ_RELATIONSHIP = "https://musicbrainz.org/relationship/"
MUSICBRAINZ_ATTRIBUTE = "https://musicbrainz.org/relationship-attribute/"


def _scheme_iri(*parts: str) -> str:
    return ARCH + "-".join(quote(part.replace(" ", "_"), safe="") for part in parts)


def role_scheme(pair: str) -> str:
    """The concept scheme of one MusicBrainz entity pair's role types."""
    return _scheme_iri("roles", pair)


def role_concept(kind: RoleType) -> str:
    """A role type's concept: skos:exactMatch its MusicBrainz relationship type."""
    return _scheme_iri("role", kind.pair, kind.name)


VOCAL_SCHEME = _scheme_iri("vocals")
ATTRIBUTE_SCHEME = _scheme_iri("attributes")


def vocal_concept(name: str) -> str:
    """A vocal attribute's concept: skos:exactMatch its MusicBrainz attribute."""
    return _scheme_iri("vocal", name)


def attribute_concept(name: str) -> str:
    """A relationship attribute's concept: skos:exactMatch its MusicBrainz attribute."""
    return _scheme_iri("attribute", name)


def role_type(scope: str, name: str) -> RoleType:
    try:
        return ROLE_TYPES[scope][name]
    except KeyError:
        raise ContributionError(f"role type {name!r} is not a MusicBrainz type for a {scope} scope") from None


# ---------------------------------------------------------------------------
# Shared pieces


def _closed(value: Any, required: set[str], optional: set[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContributionError(f"{where} must be an object")
    missing = required - set(value)
    extra = set(value) - required - optional
    if missing or extra:
        raise ContributionError(f"{where} has missing {sorted(missing)} or unknown {sorted(extra)} fields")
    return value


def _int(value: Any, where: str, low: int, high: int | None = None) -> int:
    # bool is an int in Python; a JSON true is not a number.
    if not isinstance(value, int) or isinstance(value, bool) or value < low or (high is not None and value > high):
        bound = f"{low}.." + ("" if high is None else str(high))
        raise ContributionError(f"{where} must be an integer in {bound}")
    return value


def _text(value: Any, where: str, maximum: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise ContributionError(f"{where} must be text of 1 to {maximum} characters")
    return value


def _list(value: Any, where: str, low: int, high: int) -> list[Any]:
    if not isinstance(value, list) or not low <= len(value) <= high:
        raise ContributionError(f"{where} must be a list of {low} to {high} items")
    return value


def _distinct(items: list[Any], where: str) -> None:
    if len(set(items)) != len(items):
        raise ContributionError(f"{where} repeats an item")


def _refs_by_role(refs: Any, counts: dict[str, tuple[int, int]], where: str) -> dict[str, list[dict[str, Any]]]:
    """References grouped by role, with each role's (minimum, maximum) count."""
    if not isinstance(refs, list):
        raise ContributionError(f"{where} refs must be a list")
    by_role: dict[str, list[dict[str, Any]]] = {role: [] for role in counts}
    for index, ref in enumerate(refs):
        _closed(ref, {"role", "id", "revision_id"}, set(), f"{where} ref {index}")
        if ref["role"] not in counts:
            raise ContributionError(f"{where} ref {index} has role {ref['role']!r}, which this claim cannot have")
        if not isinstance(ref["id"], str) or not OBJECT_ID.fullmatch(ref["id"]) \
                or not isinstance(ref["revision_id"], str) or not REVISION_ID.fullmatch(ref["revision_id"]):
            raise ContributionError(f"{where} ref {index} must pin an object and an exact revision")
        by_role[ref["role"]].append(ref)
    for role, (low, high) in counts.items():
        if not low <= len(by_role[role]) <= high:
            raise ContributionError(f"{where} needs {low} to {high} '{role}' references")
    return by_role


def _target(targets: dict[str, dict[str, Any]], ref: dict[str, Any], where: str) -> dict[str, Any]:
    """What a reference pins, as Door would read it from the committed revision:
    `shape` (`entity`, `claim`, or `artifact`), an Entity's `kind`, a Claim's
    `contract` (id, version), and for a stream its `payload` and `refs`."""
    target = targets.get(ref["revision_id"])
    if target is None:
        raise ContributionError(f"{where} pins {ref['revision_id']}, which is not a committed revision")
    if target.get("id", ref["id"]) != ref["id"]:
        raise ContributionError(f"{where} pins a revision of another object")
    return target


def _entity(targets, ref, kinds: set[str], where: str) -> None:
    target = _target(targets, ref, where)
    if target.get("shape") != "entity" or target.get("kind") not in kinds:
        raise ContributionError(f"{where} must pin an Entity of kind {' or '.join(sorted(kinds))}")


def _claim(targets, ref, contracts: set[tuple[str, str]], where: str) -> dict[str, Any]:
    target = _target(targets, ref, where)
    if target.get("shape") != "claim" or tuple(target.get("contract", ())) not in contracts:
        names = " or ".join(f"{c} version {v}" for c, v in sorted(contracts))
        raise ContributionError(f"{where} must pin a Claim of {names}")
    return target


def _artifact(targets, ref, where: str) -> None:
    if _target(targets, ref, where).get("shape") != "artifact":
        raise ContributionError(f"{where} must pin an Artifact")


def _check_stream(targets, stream_ref, source_ref, where: str) -> dict[str, int]:
    """The pinned audio stream's payload, after checking it is about the same
    Artifact revision as the `source` pin."""
    stream = _claim(targets, stream_ref, {AUDIO_STREAM}, f"{where} stream")
    measured = [r for r in stream.get("refs", []) if isinstance(r, dict) and r.get("role") == "artifact"]
    if len(measured) != 1 or (measured[0].get("id"), measured[0].get("revision_id")) \
            != (source_ref["id"], source_ref["revision_id"]):
        raise ContributionError(f"{where} pins a stream of another Artifact revision")
    payload = stream.get("payload") if isinstance(stream.get("payload"), dict) else {}
    return {
        "sample_rate": _int(payload.get("sample_rate"), f"{where} stream sample_rate", 1),
        "sample_count": _int(payload.get("sample_count"), f"{where} stream sample_count", 1),
        "channel_count": _int(payload.get("channel_count"), f"{where} stream channel_count", 1),
    }


def _check_provenance(payload: dict[str, Any], by_role, targets, where: str) -> bool:
    """The asserter fields shared by both contracts. Returns whether the claim
    pins an outside source's document."""
    if by_role["run"]:
        _claim(targets, by_role["run"][0], {DERIVATION_RUN}, f"{where} run")
    has_document = bool(by_role["document"])
    if has_document:
        _artifact(targets, by_role["document"][0], f"{where} document")
        if "text" not in payload:
            raise ContributionError(f"{where} pins a document, so it keeps the document's own wording as text")
    for name in ("document_url", "retrieved_at", "locator"):
        if name in payload and not has_document:
            raise ContributionError(f"{where} gives {name} without pinning the document it describes")
    if "document_url" in payload:
        url = _text(payload["document_url"], f"{where} document_url", MAX_URL)
        if not URL.fullmatch(url):
            raise ContributionError(f"{where} document_url must be an http or https URL")
    if "retrieved_at" in payload:
        value = payload["retrieved_at"]
        if not isinstance(value, str) or not (FULL_DATE.fullmatch(value) or INSTANT.fullmatch(value)):
            raise ContributionError(f"{where} retrieved_at must be an RFC 3339 full date or UTC instant")
        try:
            date.fromisoformat(value[:10])
        except ValueError:
            raise ContributionError(f"{where} retrieved_at is not a calendar date") from None
    if "locator" in payload:
        _text(payload["locator"], f"{where} locator", MAX_NAME)
    if "text" in payload:
        _text(payload["text"], f"{where} text", MAX_TEXT)
    if "credited_as" in payload:
        _text(payload["credited_as"], f"{where} credited_as", MAX_NAME)
    if "confidence" in payload:
        confidence = _closed(payload["confidence"], {"scale", "value"}, set(), f"{where} confidence")
        _text(confidence["scale"], f"{where} confidence scale", MAX_NAME)
        if not isinstance(confidence["value"], str) or not DECIMAL.fullmatch(confidence["value"]):
            raise ContributionError(f"{where} confidence value must be exact decimal text")
    return has_document


def _check_instruments(value: Any, where: str) -> list[str]:
    items = _list(value, where, 1, MAX_INSTRUMENTS)
    for index, item in enumerate(items):
        if not isinstance(item, str) or not INSTRUMENT.fullmatch(item):
            raise ContributionError(f"{where}[{index}] must be a https://musicbrainz.org/instrument/<MBID> URI")
    _distinct(items, where)
    return items


def _check_vocals(value: Any, where: str) -> list[str]:
    items = _list(value, where, 1, len(VOCALS))
    for item in items:
        if item not in VOCALS:
            raise ContributionError(f"{where} names {item!r}, which is not a MusicBrainz vocal attribute")
    _distinct(items, where)
    return items


def _check_attributes(value: Any, allowed: frozenset[str], where: str) -> list[str]:
    items = _list(value, where, 1, max(1, len(allowed)))
    for item in items:
        if item not in allowed:
            raise ContributionError(f"{where} names {item!r}, which this MusicBrainz type does not take")
    if items != sorted(set(items)):
        raise ContributionError(f"{where} must be distinct and in ascending order")
    return items


# ---------------------------------------------------------------------------
# Contribution


# Reference roles each scope's subject needs, beside `contributor`, `run`, and
# `document`, which every scope may have.
SCOPE_ROLES: dict[str, dict[str, tuple[int, int]]] = {
    "work": {"work": (1, 1)},
    "release": {"release": (1, 1)},
    "recording": {"recording": (1, 1)},
    "span": {"source": (1, 1), "stream": (1, 1), "recording": (0, 1)},
    "channel": {"source": (1, 1), "stream": (0, 1), "recording": (0, 1)},
}
COMMON_ROLES = {"contributor": (0, 1), "run": (0, 1), "document": (0, 1)}
CONTRIBUTOR_KINDS = {"person", "organisation"}


def check_contribution(claim: dict[str, Any], targets: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Check an `archive.claim/contribution` version 1 claim against every rule
    of ADR 0078. `targets` maps each pinned revision ID to what it is (see
    `_target`). Returns the payload; raises ContributionError."""
    where = "contribution"
    record = _closed(claim, {"payload", "refs"}, {"revision_id", "agent"}, where)
    payload = _closed(record["payload"], {"scope"}, {
        "role", "text", "credited_as", "spans", "sample_rate", "channel", "alignment",
        "document_url", "retrieved_at", "locator", "confidence"}, f"{where} payload")
    scope = payload["scope"]
    if scope not in SCOPES:
        raise ContributionError(f"{where} scope must be one of {', '.join(SCOPES)}")
    by_role = _refs_by_role(record["refs"], {**SCOPE_ROLES[scope], **COMMON_ROLES}, where)
    has_document = _check_provenance(payload, by_role, targets, where)

    # Who.
    contributor = by_role["contributor"][0] if by_role["contributor"] else None
    if contributor:
        _entity(targets, contributor, CONTRIBUTOR_KINDS, f"{where} contributor")

    # In what role.
    kind = None
    if "role" in payload:
        role = _closed(payload["role"], {"type"}, {"attributes", "instruments", "vocals"}, f"{where} role")
        kind = role_type(scope, role["type"])
        if "attributes" in role:
            _check_attributes(role["attributes"], kind.attributes, f"{where} role attributes")
        if "instruments" in role:
            if not kind.instruments:
                raise ContributionError(f"{where} role {kind.name!r} takes no instruments")
            _check_instruments(role["instruments"], f"{where} role instruments")
        elif kind.requires_instrument:
            raise ContributionError(f"{where} role 'instrument' names at least one instrument")
        if "vocals" in role:
            if not kind.vocals:
                raise ContributionError(f"{where} role {kind.name!r} takes no vocals")
            _check_vocals(role["vocals"], f"{where} role vocals")
    elif "text" not in payload:
        raise ContributionError(f"{where} without a role keeps the credit's verbatim text")
    if kind and kind.place:
        if contributor:
            raise ContributionError(f"{where} role {kind.name!r} credits a place, which no Entity kind holds yet")
        if "text" not in payload:
            raise ContributionError(f"{where} crediting a place keeps the place as written in text")
    elif contributor is None and kind is None:
        raise ContributionError(f"{where} names a contributor, a role, or both")
    if "credited_as" in payload and contributor is None and not (kind and kind.place):
        raise ContributionError(f"{where} gives credited_as without a contributor")

    # To what.
    if scope == "work":
        _entity(targets, by_role["work"][0], {"musical_work"}, f"{where} work")
    elif scope == "release":
        _entity(targets, by_role["release"][0], {"release"}, f"{where} release")
    else:
        for ref in by_role["recording"]:
            _check_recording(targets, ref, f"{where} recording")
    span_fields = {"spans", "sample_rate"}
    if scope == "span":
        source, stream = by_role["source"][0], by_role["stream"][0]
        _artifact(targets, source, f"{where} source")
        facts = _check_stream(targets, stream, source, where)
        if not span_fields <= set(payload):
            raise ContributionError(f"{where} span scope gives sample_rate and spans")
        if _int(payload["sample_rate"], f"{where} sample_rate", 1) != facts["sample_rate"]:
            raise ContributionError(f"{where} sample_rate differs from the stream's")
        _check_spans(payload["spans"], facts["sample_count"], where)
        if has_document != ("alignment" in payload):
            raise ContributionError(f"{where} span scope states an alignment exactly when it pins a document")
        if "alignment" in payload:
            alignment = _closed(payload["alignment"], {"method"}, {"max_error_samples"}, f"{where} alignment")
            _text(alignment["method"], f"{where} alignment method", MAX_NAME)
            if "max_error_samples" in alignment:
                _int(alignment["max_error_samples"], f"{where} alignment max_error_samples", 0)
    else:
        for name in span_fields | {"alignment"}:
            if name in payload:
                raise ContributionError(f"{where} gives {name} outside a span scope")
    if scope == "channel":
        source = by_role["source"][0]
        _artifact(targets, source, f"{where} source")
        if "channel" in payload:
            if not by_role["stream"]:
                raise ContributionError(f"{where} names a channel index without the stream that counts channels")
            facts = _check_stream(targets, by_role["stream"][0], source, where)
            _int(payload["channel"], f"{where} channel", 1, facts["channel_count"])
        elif by_role["stream"]:
            _check_stream(targets, by_role["stream"][0], source, where)
    elif "channel" in payload:
        raise ContributionError(f"{where} gives a channel outside a channel scope")
    return payload


def _check_recording(targets, ref, where: str) -> None:
    """A recording is a `recording` Entity, a take in the archive (an audio
    selection or song occurrence, version 2), or a whole Artifact."""
    target = _target(targets, ref, where)
    shape = target.get("shape")
    if shape == "entity" and target.get("kind") == "recording":
        return
    if shape == "claim" and tuple(target.get("contract", ())) in {AUDIO_SELECTION, SONG_OCCURRENCE}:
        return
    if shape == "artifact":
        return
    raise ContributionError(f"{where} must pin a recording Entity, an audio selection or song occurrence "
                            f"version 2, or an Artifact")


def _check_spans(value: Any, count: int, where: str) -> None:
    spans = _list(value, f"{where} spans", 1, MAX_SPANS)
    previous_end = 0
    for index, span in enumerate(spans):
        at = f"{where} span {index}"
        _closed(span, {"start_sample", "end_sample"}, set(), at)
        start = _int(span["start_sample"], f"{at} start_sample", 0)
        end = _int(span["end_sample"], f"{at} end_sample", 1)
        if end <= start:
            raise ContributionError(f"{at} must end after it starts")
        if start < previous_end:
            raise ContributionError(f"{at} overlaps or precedes the one before it")
        if end > count:
            raise ContributionError(f"{at} ends past the stream's sample count")
        previous_end = end


# ---------------------------------------------------------------------------
# Band membership


def check_band_membership(claim: dict[str, Any], targets: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Check an `archive.claim/band_membership` version 1 claim: MusicBrainz's
    "member of band", with the member's instruments and vocals and partial
    from and to dates. Returns the payload; raises ContributionError."""
    where = "band membership"
    record = _closed(claim, {"payload", "refs"}, {"revision_id", "agent"}, where)
    payload = _closed(record["payload"], set(), {
        "attributes", "instruments", "vocals", "from", "to", "ended", "text", "credited_as",
        "document_url", "retrieved_at", "locator", "confidence"}, f"{where} payload")
    by_role = _refs_by_role(record["refs"], {"band": (1, 1), "member": (1, 1), **{
        k: v for k, v in COMMON_ROLES.items() if k != "contributor"}}, where)
    _entity(targets, by_role["band"][0], {"organisation"}, f"{where} band")
    _entity(targets, by_role["member"][0], {"person"}, f"{where} member")
    if by_role["band"][0]["id"] == by_role["member"][0]["id"]:
        raise ContributionError(f"{where} names one object as band and member")
    _check_provenance(payload, by_role, targets, where)
    if "attributes" in payload:
        _check_attributes(payload["attributes"], MEMBERSHIP_ATTRIBUTES, f"{where} attributes")
    if "instruments" in payload:
        _check_instruments(payload["instruments"], f"{where} instruments")
    if "vocals" in payload:
        _check_vocals(payload["vocals"], f"{where} vocals")
    start = _partial_date(payload["from"], f"{where} from") if "from" in payload else None
    end = _partial_date(payload["to"], f"{where} to") if "to" in payload else None
    if start and end and end[1] < start[0]:
        raise ContributionError(f"{where} ends before it begins")
    if "ended" in payload and (payload["ended"] is not True or end):
        raise ContributionError(f"{where} ended is true, and only when no end date is known")
    return payload


def _partial_date(value: Any, where: str) -> tuple[date, date]:
    """A MusicBrainz partial date (YYYY, YYYY-MM, or YYYY-MM-DD) as the
    earliest and latest days it can mean."""
    match = PARTIAL_DATE.fullmatch(value) if isinstance(value, str) else None
    if not match:
        raise ContributionError(f"{where} must be YYYY, YYYY-MM, or YYYY-MM-DD")
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
        raise ContributionError(f"{where} is not a calendar date") from None


# ---------------------------------------------------------------------------
# The resolved view: finer scope refines, never contradicts


@dataclass(frozen=True)
class Entry:
    """One contribution claim in the resolved view. `refines` names the coarser
    claims it refines and `refined_by` the finer ones that refine it; `why`
    says, for each relation, which rule placed it. `located_by` names finer
    claims with no contributor (an instrument detector's spans) that match its
    role: they locate the contribution but name no one."""

    revision_id: str
    scope: str
    contributor: str | None
    role: str | None
    agent: str | None
    refines: tuple[str, ...] = ()
    refined_by: tuple[str, ...] = ()
    located_by: tuple[str, ...] = ()
    why: tuple[str, ...] = field(default=())

    @property
    def finest(self) -> bool:
        """True when no finer claim refines this one. A coarse claim that is
        not finest is still shown: it is refined, never overruled."""
        return not self.refined_by


def _ref(claim: dict[str, Any], role: str) -> dict[str, Any] | None:
    found = [r for r in claim["refs"] if r["role"] == role]
    return found[0] if found else None


def _within(fine: dict[str, Any], coarse: dict[str, Any]) -> str | None:
    """Why `fine`'s subject is part of `coarse`'s, or None. Containment is
    taken only from what the claims themselves pin: a recording's place on a
    release, and a work's recordings, need claims no contract states yet."""
    fs, cs = fine["payload"]["scope"], coarse["payload"]["scope"]
    if fs in ("span", "channel") and cs == "recording":
        recording, parent = _ref(fine, "recording"), _ref(coarse, "recording")
        if recording and recording["id"] == parent["id"]:
            return f"its {fs} is in recording {parent['id']} by its own recording reference"
        if _ref(fine, "source")["id"] == parent["id"]:
            return f"its {fs} is in recording {parent['id']}, which is the Artifact it counts"
        return None
    if fs == "span" and cs in ("span", "channel"):
        if _ref(fine, "source")["id"] != _ref(coarse, "source")["id"]:
            return None
        if cs == "channel":
            return f"its spans are in the channel {_ref(coarse, 'source')['id']} counts"
        outer = coarse["payload"]["spans"]
        inner = fine["payload"]["spans"]
        if inner == outer:
            return None
        if all(any(o["start_sample"] <= i["start_sample"] and i["end_sample"] <= o["end_sample"] for o in outer)
               for i in inner):
            return "its spans lie within the coarser claim's spans of the same source"
    return None


def _role_within(fine: dict[str, Any], coarse: dict[str, Any]) -> str | None:
    fr, cr = fine["payload"].get("role"), coarse["payload"].get("role")
    if not fr or not cr or fr["type"] != cr["type"]:
        return None
    for name in ("instruments", "vocals"):
        # A coarser claim that names instruments is refined only by a claim
        # naming some of them; one that names none is refined by any.
        if name in cr and (name not in fr or not set(fr[name]) <= set(cr[name])):
            return None
    detail = ", ".join(f"{n} {sorted(fr[n])}" for n in ("instruments", "vocals") if n in fr)
    return f"same role {fr['type']!r}" + (f" ({detail} within the coarser claim's)" if detail else "")


def resolve(claims: list[dict[str, Any]]) -> list[Entry]:
    """The resolved view of checked contribution claims, in input order.

    A finer claim refines a coarser one when both name the same contributor
    object and role type, the finer claim's instruments and vocals are within
    the coarser one's, and the finer subject is part of the coarser one by a
    reference either claim pins (see `_within`). Asserters do not matter: a
    person's span may refine a booklet's recording credit, and both are kept
    with their agents. No claim implies absence: a span credit says nothing
    about the samples outside it, so it never contradicts a recording credit.
    Claims at the same scope never refine each other; they coexist."""
    entries = {c["revision_id"]: {"refines": [], "refined_by": [], "located_by": [], "why": []} for c in claims}
    for fine in claims:
        for coarse in claims:
            place = _within(fine, coarse) if fine is not coarse else None
            role = _role_within(fine, coarse) if place else None
            if not role:
                continue
            fc, cc = _ref(fine, "contributor"), _ref(coarse, "contributor")
            f, c = fine["revision_id"], coarse["revision_id"]
            if fc and cc and fc["id"] == cc["id"]:
                entries[f]["refines"].append(c)
                entries[c]["refined_by"].append(f)
                reason = f"{f} refines {c}: same contributor {fc['id']}, {role}; {place}"
                entries[f]["why"].append(reason)
                entries[c]["why"].append(reason)
            elif fc is None and cc is not None:
                entries[c]["located_by"].append(f)
                entries[c]["why"].append(f"{f} locates {c} but names no contributor: {role}; {place}")
    result = []
    for claim in claims:
        e = entries[claim["revision_id"]]
        contributor = _ref(claim, "contributor")
        role = claim["payload"].get("role")
        result.append(Entry(claim["revision_id"], claim["payload"]["scope"],
                            contributor["id"] if contributor else None, role["type"] if role else None,
                            claim.get("agent"), tuple(e["refines"]), tuple(e["refined_by"]),
                            tuple(e["located_by"]), tuple(e["why"])))
    return result


# ---------------------------------------------------------------------------
# Whose part a chord stack describes (ADR 0077)


def part_contributions(chord_claim: dict[str, Any], contributions: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """The contribution claims that say whose part an
    `archive.claim/chord_stacks` claim describes, each with why: a
    contribution it pins in role `contribution` (the amendment ADR 0078 makes
    to ADR 0077), or a channel-scope contribution whose source is audio the
    stacks claim pins as `analysed` (a player's stem or input channel)."""
    pinned = {r["revision_id"] for r in chord_claim["refs"] if r["role"] == "contribution"}
    analysed = {r["id"] for r in chord_claim["refs"] if r["role"] == "analysed"}
    found = []
    for claim in contributions:
        if claim["revision_id"] in pinned:
            found.append((claim["revision_id"], "the chord stacks claim pins it as its contribution"))
        elif claim["payload"]["scope"] == "channel" and _ref(claim, "contributor") \
                and _ref(claim, "source")["id"] in analysed:
            found.append((claim["revision_id"],
                          f"it credits the channel {_ref(claim, 'source')['id']} the stacks were analysed from"))
    missing = pinned - {revision for revision, _ in found}
    if missing:
        raise ContributionError(f"chord stacks claim pins contributions not given: {sorted(missing)}")
    return found
