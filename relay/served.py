"""The relay's routes of specification 0.0.10: named views, document
staging and bytes, the feature bank's stems, practice renders, and exports
(ADRs 0082, 0085, 0088, 0089).

`Served` is mixed into the relay. It decides what each route answers and to
whom, from the rules World holds (`world/views.py`, `world/circle.py`,
`world/documents.py`, `world/exports.py`) and from the caller's access
(`access.py`); `relay.py` carries the HTTP. Nothing here writes archival
state: views and exports are regenerable, stems and renders are derived
media, and staged documents are operational until Door admits them.

Who may have content: the active operator, or a member the recording (or
document) has been disclosed to (ADR 0088). A stem, a render, a waveform,
or a listening copy is the recording, so it takes the recording's answer
and never has one of its own (ADR 0089).
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import derived
import world
from world import documents as document_rules
from world import exports as export_rules
from world import views as view_rules

import access
import exporting
import practice
import staging as document_staging
from access import Caller
from refusals import Refused, coded

KEY = export_rules.KEY


@dataclass(frozen=True)
class FileAnswer:
    """Bytes a route answers with: the file, its media type, its entity tag,
    and what to say when it has not been made."""

    path: Path
    media_type: str
    etag: str
    missing: str


def _digest(digest: str) -> str:
    if not derived.is_digest(digest):
        raise Refused("request_malformed", "a route names a content digest, sha256:<64 lowercase hex>")
    return digest


def _key(key: str) -> str:
    if not isinstance(key, str) or not KEY.fullmatch(key):
        raise Refused("request_malformed", "a route names a key, sha256:<64 lowercase hex>")
    return key


class Served:
    """Mixed into the relay, which provides `config`, `operator`, `circle`,
    `queries`, `cache`, and `media_index()`."""

    def setup_served(self) -> None:
        config = self.config
        self.features = practice.Features(config.bank_root, tuple(config.practice_features), config.bank_parameters)
        self.renderer = practice.Renderer(practice.check_renderers(config.renderers, "renderers"),
                                          config.media_root, config.render_timeout_seconds)
        self.exporters = exporting.Exporters(practice.check_renderers(config.exporters, "exporters"),
                                             config.media_root, config.export_timeout_seconds)
        self.documents = document_staging.Documents(config.staging, config.archive_root, config.writes == "commit")
        self.known_access: access.Access | None = None
        self.access_lock = threading.Lock()
        if self.circle is not None:
            # A member's person must be a person Entity, and the configured
            # operator World's active operator, before anyone is served.
            try:
                problem = self.access().problem
            except Refused as exc:
                raise ValueError(f"the circle cannot be checked against World: {exc}") from None
            if problem is not None:
                raise ValueError(f"the circle does not match the archive: {problem}")

    def caller(self, agent: str) -> Caller:
        return Caller(agent, "operator" if agent == self.operator else "member")

    def access(self) -> access.Access:
        """Access for World's selected generation, read once a generation
        (and once a day, since rights terms are read on a date)."""
        version = world.PROJECTION_VERSION
        generation = world.selected_generation(self.config.world_store, version)
        today = datetime.now(timezone.utc).date()  # Door reads rights on the UTC day
        known = self.known_access
        if known is not None and (known.generation, known.today) == (generation, today):
            return known
        with self.access_lock:
            known = self.known_access
            if known is None or (known.generation, known.today) != (generation, today):
                known = access.load(self.config.world_store, version, self.operator, self.circle, today)
                self.known_access = known
        return known

    # -- named views (ADR 0082) -------------------------------------------

    def view(self, body: Any, caller: Caller) -> tuple[tuple, dict[str, Any]]:
        """The cache key and entry answering one view request. A member's
        view is given only what they may read, and may name nothing else."""
        try:
            checked = view_rules.check_request(body, caller.agent)
        except view_rules.ViewError as exc:
            raise Refused(exc.code, str(exc)) from None
        version = world.PROJECTION_VERSION
        canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for _ in range(3):
            readable = None
            if caller.capacity == "operator":
                generation = world.selected_generation(self.config.world_store, version)
            else:
                found = self.access()
                found.check_disclosed(checked, caller)
                generation, readable = found.generation, sorted(found.readable(caller))
            key = (version, generation, ("view", caller.agent), canonical)
            cached = self.cache.get(key) if generation is not None else None
            if cached is not None:
                return key, cached
            answered, _, _, data = self.queries.view(version, body, caller.agent, readable)
            if readable is not None and answered != generation:
                continue  # the readable set was another generation's
            key = (version, answered, ("view", caller.agent), canonical)
            entry = {"result": data}
            self.cache.put(key, entry)
            return key, entry
        raise Refused("world_unavailable", "World's generation kept changing while the view was answered")

    # -- who may have content (ADRs 0069, 0088, 0089) ---------------------

    def media_gate(self, digest: str, caller: Caller) -> None:
        """Content of an Artifact in World that no destroy names, and a caller
        who is the active operator or a member it is disclosed to."""
        index = self.media_index()
        operator = index.operator()
        if operator is None:
            raise Refused("media_forbidden", "World names no single active operator, so media are served to no one")
        if not index.serves(digest):
            raise Refused("media_unknown_digest", "no Artifact in World's selected generation has this content")
        if caller.agent == operator:
            return
        if caller.capacity == "member" and self.access().hears(digest, caller):
            return
        raise Refused("media_forbidden", "media are served only to the active operator and to circle members "
                                         "the recording is disclosed to (ADRs 0069, 0088, 0089)")

    def media_file(self, kind: str, digest: str, caller: Caller) -> FileAnswer:
        """A derivative a caller may have, or the refusal."""
        _digest(digest)
        if self.config.media_root is None:
            raise Refused("media_not_derived", "this relay has no derived-media root configured")
        self.media_gate(digest, caller)
        return FileAnswer(derived.path(self.config.media_root, digest, kind), derived.KINDS[kind].media_type,
                          derived.etag(digest, kind), f"no {kind} has been made for this content yet")

    # -- the feature bank and practice renders (ADR 0089) -----------------

    def bank_listing(self, digest: str, caller: Caller) -> dict[str, Any]:
        _digest(digest)
        self.media_gate(digest, caller)
        if self.features.bank is None:
            return {"content": digest, "features": [], "missing": []}
        return self.features.listing(digest)

    def bank_file(self, digest: str, extractor: str, output: str, caller: Caller) -> FileAnswer:
        _digest(digest)
        name = f"{extractor}/{output}"
        self.features.served(name)
        self.media_gate(digest, caller)
        found = self.features.find(digest, name)
        if found is None:
            raise Refused("feature_not_made", f"{name} has not been made for this content")
        path, described, key = found
        return FileAnswer(path, described["media_type"], f'"{key}/{output}"', f"{name} has not been made for this content")

    def practice(self, digest: str, body: Any, caller: Caller) -> dict[str, Any]:
        _digest(digest)
        try:
            checked = export_rules.check_practice_request(body)
        except export_rules.ExportError as exc:
            raise Refused("request_malformed", str(exc)) from None
        if checked["content"] != digest:
            raise Refused("request_malformed", "a practice request's content is the route's digest")
        for output in checked["gains"]:
            self.features.served(f"{checked['stems']['extractor']}/{output}")
        if self.config.media_root is None:
            raise Refused("media_not_derived", "this relay has no derived-media root configured")
        self.media_gate(digest, caller)
        self.renderer.command(checked["renderer"])
        return self.renderer.request(checked, self.features.stems(checked))

    def practice_file(self, digest: str, key: str, caller: Caller) -> FileAnswer:
        _digest(digest)
        _key(key)
        if self.config.media_root is None:
            raise Refused("media_not_derived", "this relay has no derived-media root configured")
        self.media_gate(digest, caller)
        path = practice.render_path(self.config.media_root, digest, key)
        try:
            request = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise Refused("media_not_derived", "no render with this key has been made for this recording") from None
        except (OSError, ValueError) as exc:
            raise Refused("internal_error", f"the render's request cannot be read: {exc}") from None
        # A feature no longer served is not served through a render of it either.
        for output in request.get("gains", {}):
            self.features.served(f"{request['stems']['extractor']}/{output}")
        return FileAnswer(path, practice.RENDER_MEDIA_TYPE, f'"{key}"',
                          "no render with this key has been made for this recording")

    # -- exports (ADR 0089) -----------------------------------------------

    def export(self, body: Any, caller: Caller) -> dict[str, Any]:
        try:
            request = export_rules.check_request(body)
        except export_rules.ExportError as exc:
            raise Refused("request_malformed", str(exc)) from None
        self.exporters.command(request["exporter"])
        found = self.access()
        found.check_inputs(request["rendered"], request.get("joined", []), caller)
        records = found.closure([pin["revision_id"] for pin in request["rendered"]], caller)
        joined = []
        for pin in request.get("joined", []):
            digest = access.content_digest(found.by_revision[pin["revision_id"]])
            joined.append({**pin, "media_type": found.document_type(digest),
                           "path": str(document_staging.archive_bytes(self.config.archive_root, digest))})
        return self.exporters.export(request, records, joined)

    def export_file(self, key: str, caller: Caller) -> FileAnswer:
        _key(key)
        made = self.exporters.made(key)
        if made is None:
            raise Refused("media_not_derived", "no export has been made with this key")
        path, request, _ = made
        self.access().check_inputs(request["rendered"], request.get("joined", []), caller)
        return FileAnswer(path, export_rules.FORMATS[request["format"]][0], f'"{key}"',
                          "no export has been made with this key")

    # -- documents (ADR 0085) ---------------------------------------------

    def stage_document(self, body: Any, caller: Caller) -> dict[str, Any]:
        try:
            media_type, data, filename = document_rules.check_stage_request(body, caller.agent, self.operator)
        except document_rules.DocumentError as exc:
            raise coded(exc) from None
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        return self.documents.stage(media_type, data, filename, caller.agent, self.access().artifacts(digest))

    def check_contents(self, manifest: dict[str, Any], drafts: list[tuple[str, Any]], caller: Caller) -> None:
        """An admission listing content passes only when every row is bytes
        this relay staged for this caller and each Artifact carrying them is
        described as staged (ADR 0085's amendment of ADR 0056)."""
        if caller.capacity != "operator":
            raise Refused("content_not_relayed", "only the operator's staged documents are relayed")
        staged = self.documents.staged_for(caller.agent)
        for row in manifest["contents"]:
            entry = staged.get(row.get("digest")) if isinstance(row, dict) else None
            if entry is None or row != entry["row"]:
                raise Refused("content_not_relayed", "the relay admits only document bytes it staged for this caller; "
                                                     "other content admission stays with operator tools (ADR 0056)")
        try:
            document_rules.check_document_admission(manifest, [draft for _, draft in drafts],
                                                    {digest: entry["media_type"] for digest, entry in staged.items()})
        except document_rules.DocumentError as exc:
            raise coded(exc) from None

    def document_file(self, digest: str, caller: Caller) -> FileAnswer:
        _digest(digest)
        index = self.media_index()
        if not index.serves(digest):
            raise Refused("media_unknown_digest", "no Artifact in World's selected generation has this content")
        found = self.access()
        media_type = found.document_type(digest)
        if media_type is None:
            raise Refused("media_unknown_digest", "no document source claim describes an Artifact with this content")
        if not ((caller.capacity == "operator" and caller.agent == found.operator) or found.hears(digest, caller)):
            raise Refused("media_forbidden", "a document is served only to the operator and to circle members "
                                             "it is disclosed to (ADRs 0085, 0088)")
        path = document_staging.archive_bytes(self.config.archive_root, digest)
        with open(path, "rb") as source:
            kind = document_rules.audio_or_video(source.read(256))
        if kind is not None:
            raise Refused("content_audio_or_video", f"the bytes are {kind}; the relay serves documents only")
        return FileAnswer(path, media_type, f'"{digest}"', "the archive holds no copy of this content here")
