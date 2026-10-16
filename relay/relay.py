#!/usr/bin/env python3
"""Relay HTTP from the operator's tailnet to Door's service and World (ADR 0056).

The relay owns no state. Writes become proposal directories handed to
`door serve`; reads are read-only SPARQL over a World generation. Every route,
body, answer, and refusal code is described in relay/openapi.json (ADR 0060).
"""

from __future__ import annotations

import argparse
import gzip
import ipaddress
import json
import multiprocessing
import queue
import os
import re
import shutil
import socket
import stat
import sys
import tempfile
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "world"))

import world  # noqa: E402

MAX_BODY = 1024 * 1024
DOOR_TIMEOUT_SECONDS = 600.0
RECORD_PATH = re.compile(r"records/[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.json")
PROTOCOLS = {"admit": "archive-admission/1", "transition": "archive-transition/1"}
DOOR_ROUTES = {
    "/door/admit-dry-run": ("admit", True),
    "/door/admit": ("admit", False),
    "/door/transition-dry-run": ("transition", True),
    "/door/transition": ("transition", False),
}
ROUTES = {"/health": "GET", "/world/query": "POST", **{path: "POST" for path in DOOR_ROUTES}}
ROLES = ("shadow", "scratch", "live")
CONFIG_FIELDS = {
    "listen", "archive_root", "door_socket", "world_store", "archive_command", "staging",
    "writes", "role", "origins", "callers", "query_timeout_seconds", "max_rows",
    "query_workers", "cache_bytes",
}
# Answers smaller than this go out uncompressed: gzip would not pay for itself.
GZIP_MIN_BYTES = 1024
# How often the warmer looks for a new World generation it was not told of.
WARM_POLL_SECONDS = 1.0
# How many recently asked queries the warmer answers again for a new generation.
WARM_QUERIES = 32

# The relay's own refusal codes and their statuses (ADR 0060). Door's codes
# (ADR 0058) pass through unchanged with the statuses in DOOR_STATUSES.
RELAY_CODES: dict[str, HTTPStatus] = {
    "request_malformed": HTTPStatus.BAD_REQUEST,
    "forbidden_identity": HTTPStatus.FORBIDDEN,
    "forbidden_origin": HTTPStatus.FORBIDDEN,
    "agent_mismatch": HTTPStatus.FORBIDDEN,
    "commits_disabled": HTTPStatus.FORBIDDEN,
    "not_found": HTTPStatus.NOT_FOUND,
    "method_not_allowed": HTTPStatus.METHOD_NOT_ALLOWED,
    "length_required": HTTPStatus.LENGTH_REQUIRED,
    "body_too_large": HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
    "unsupported_media_type": HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
    "content_not_relayed": HTTPStatus.UNPROCESSABLE_ENTITY,
    "query_invalid": HTTPStatus.BAD_REQUEST,
    "query_refused": HTTPStatus.BAD_REQUEST,
    "query_too_many_rows": HTTPStatus.UNPROCESSABLE_ENTITY,
    "projection_not_served": HTTPStatus.NOT_FOUND,
    "query_timeout": HTTPStatus.GATEWAY_TIMEOUT,
    "world_unavailable": HTTPStatus.SERVICE_UNAVAILABLE,
    "door_unreachable": HTTPStatus.SERVICE_UNAVAILABLE,
    "door_failed": HTTPStatus.BAD_GATEWAY,
    "internal_error": HTTPStatus.INTERNAL_SERVER_ERROR,
}
DOOR_STATUSES: dict[str, HTTPStatus] = {
    "request_malformed": HTTPStatus.BAD_GATEWAY,
    "proposal_malformed": HTTPStatus.UNPROCESSABLE_ENTITY,
    "proposal_refused": HTTPStatus.UNPROCESSABLE_ENTITY,
    "expected_head_stale": HTTPStatus.CONFLICT,
    "archive_unreadable": HTTPStatus.SERVICE_UNAVAILABLE,
    "integrity_failure": HTTPStatus.INTERNAL_SERVER_ERROR,
    "configuration_failure": HTTPStatus.INTERNAL_SERVER_ERROR,
    "io_failure": HTTPStatus.INTERNAL_SERVER_ERROR,
    "subprocess_failure": HTTPStatus.INTERNAL_SERVER_ERROR,
    "operation_failed": HTTPStatus.INTERNAL_SERVER_ERROR,
    "outcome_uncertain": HTTPStatus.SERVICE_UNAVAILABLE,
    "internal_error": HTTPStatus.INTERNAL_SERVER_ERROR,
}


class Refused(Exception):
    """A request the relay will not answer with success: a code, its HTTP
    status, and a message. ``extra`` carries Door's other refusal fields."""

    def __init__(self, code: str, message: str, status: HTTPStatus | None = None,
                 extra: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.status = status or RELAY_CODES[code]
        self.extra = extra or {}

    def error(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), **self.extra}


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    archive_root: Path
    door_socket: Path
    world_store: Path
    archive_command: str
    staging: Path
    writes: str
    role: str
    origins: frozenset[str]
    callers: dict[str, str]
    query_timeout_seconds: float
    max_rows: int
    query_workers: int = 3
    cache_bytes: int = 64 * 1024 * 1024


def load_config(path: Path) -> Config:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) - CONFIG_FIELDS:
        raise ValueError(f"relay configuration allows only {sorted(CONFIG_FIELDS)}")
    listen = data.get("listen")
    if not isinstance(listen, dict) or set(listen) != {"host", "port"}:
        raise ValueError("listen must be {host, port}")
    if not ipaddress.ip_address(listen["host"]).is_loopback:
        raise ValueError("the relay listens on the loopback interface only; tailscale serve publishes it")
    if data.get("writes") not in ("dry-run", "commit"):
        raise ValueError("writes must be dry-run or commit")
    if data.get("role") not in ROLES:
        raise ValueError(f"role must be one of {', '.join(ROLES)}")
    if data["role"] == "shadow" and data["writes"] != "dry-run":
        raise ValueError("a shadow relay is dry-run only: a commit would stop the shadow being a faithful migration")
    origins = data.get("origins")
    if not isinstance(origins, list) or not all(isinstance(o, str) and o.startswith("https://") for o in origins):
        raise ValueError("origins must list https origins")
    callers = data.get("callers")
    if (not isinstance(callers, dict) or not callers
            or not all(isinstance(k, str) and isinstance(v, str) and v.startswith("urn:uuid:") for k, v in callers.items())):
        raise ValueError("callers must map each tailnet login to an agent urn:uuid")
    for field in ("archive_root", "door_socket", "world_store", "staging"):
        if not isinstance(data.get(field), str):
            raise ValueError(f"{field} must be a path")
    return Config(
        host=listen["host"],
        port=int(listen["port"]),
        archive_root=Path(data["archive_root"]),
        door_socket=Path(data["door_socket"]),
        world_store=Path(data["world_store"]),
        archive_command=data.get("archive_command", "archive"),
        staging=Path(data["staging"]),
        writes=data["writes"],
        role=data["role"],
        origins=frozenset(origins),
        callers=dict(callers),
        query_timeout_seconds=float(data.get("query_timeout_seconds", 10)),
        max_rows=int(data.get("max_rows", 10000)),
        query_workers=_count(data, "query_workers", 3, minimum=1),
        cache_bytes=_count(data, "cache_bytes", 64 * 1024 * 1024, minimum=0),
    )


def _count(data: dict[str, Any], field: str, default: int, minimum: int) -> int:
    value = data.get(field, default)
    if type(value) is not int or value < minimum:
        raise ValueError(f"{field} must be an integer of at least {minimum}")
    return value


def private_directory(directory: Path) -> None:
    """The staging directory must be this user's and closed to others."""
    status = os.stat(directory, follow_symlinks=False)
    if not stat.S_ISDIR(status.st_mode):
        raise ValueError(f"staging is not a directory: {directory}")
    if status.st_uid != os.geteuid():
        raise ValueError(f"staging is not owned by this user: {directory}")
    if status.st_mode & 0o077:
        raise ValueError(f"staging is open to other users: {directory}")


def read_archive_manifest(archive_root: Path) -> tuple[str, str]:
    """The archive's identifier and specification, from its manifest. Door
    changes the manifest only in a specification upgrade, which changes the
    specification and never the identifier (ADR 0068)."""
    manifest = json.loads((archive_root / "archive.json").read_text(encoding="utf-8"))
    archive_id, spec = manifest.get("archive_id"), manifest.get("spec")
    if not (isinstance(archive_id, str) and archive_id.startswith("urn:uuid:") and isinstance(spec, str)):
        raise ValueError(f"{archive_root / 'archive.json'} does not name an archive and its specification")
    return archive_id, spec


def check_proposal(kind: str, body: Any, agent: str) -> tuple[dict[str, Any], list[tuple[str, Any]]]:
    """Return the manifest and the drafts in manifest order, or refuse."""
    if not isinstance(body, dict) or set(body) != {"manifest", "records"}:
        raise Refused("request_malformed", "a proposal must be exactly {manifest, records}")
    manifest, records = body["manifest"], body["records"]
    if not isinstance(manifest, dict) or not isinstance(records, dict):
        raise Refused("request_malformed", "manifest and records must be objects")
    if manifest.get("protocol") != PROTOCOLS[kind]:
        raise Refused("request_malformed", f"a {kind} proposal uses protocol {PROTOCOLS[kind]}")
    order = manifest.get("records")
    if not isinstance(order, list) or not order:
        raise Refused("request_malformed", "manifest.records must list the drafts")
    for name in order:
        if not isinstance(name, str) or not RECORD_PATH.fullmatch(name):
            raise Refused("request_malformed", f"draft path is not records/<name>.json: {name!r}")
    if len(set(order)) != len(order) or set(order) != set(records):
        raise Refused("request_malformed", "records must hold exactly the drafts manifest.records names")
    if kind == "admit" and manifest.get("contents") != []:
        raise Refused("content_not_relayed",
                      "the relay admits no content; content admission stays with operator tools (ADR 0056)")
    drafts = [(name, records[name]) for name in order]
    for name, draft in drafts:
        if not isinstance(draft, dict) or draft.get("agent") != agent:
            raise Refused("agent_mismatch", f"{name} must name the caller's agent {agent} (ADR 0056)")
    return manifest, drafts


def stage_proposal(staging: Path, manifest: dict[str, Any], drafts: list[tuple[str, Any]]) -> Path:
    directory = Path(tempfile.mkdtemp(prefix="proposal-", dir=staging))
    (directory / "records").mkdir()
    (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    for name, draft in drafts:
        (directory / name).write_text(json.dumps(draft, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return directory


def door_request(socket_path: Path, command: str, proposal: Path, commits: bool) -> dict[str, Any]:
    """One request to `door serve`: one JSON line out, one JSON line back.

    Before the request is sent nothing can have been written, so a failure
    is ``door_unreachable``. After it is sent, a failure of a commit leaves
    its outcome unknown, which is Door's ``outcome_uncertain`` (ADR 0058).
    """
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(DOOR_TIMEOUT_SECONDS)
        try:
            connection.connect(str(socket_path))
        except OSError as exc:
            raise Refused("door_unreachable", f"Door's service is not reachable: {exc}") from exc
        try:
            connection.sendall(json.dumps({"command": command, "proposal": str(proposal)}).encode() + b"\n")
            answer = b""
            while not answer.endswith(b"\n"):
                chunk = connection.recv(65536)
                if not chunk:
                    break
                answer += chunk
        except OSError as exc:
            raise _no_answer(commits, f"Door's service stopped answering: {exc}") from exc
    try:
        reply = json.loads(answer)
    except json.JSONDecodeError as exc:
        raise _no_answer(commits, "Door's service answered malformed JSON") from exc
    if not isinstance(reply, dict) or not isinstance(reply.get("ok"), bool):
        raise _no_answer(commits, "Door's service answered without ok")
    if not reply["ok"]:
        error = reply.get("error")
        if not isinstance(error, dict) or error.get("code") not in DOOR_STATUSES or not isinstance(error.get("message"), str):
            raise _no_answer(commits, "Door's service refused without a known code")
        extra = {key: value for key, value in error.items() if key not in ("code", "message")}
        raise Refused(error["code"], error["message"], DOOR_STATUSES[error["code"]], extra)
    if not isinstance(reply.get("result"), dict):
        raise _no_answer(commits, "Door's service answered ok without a result")
    return reply["result"]


def _no_answer(commits: bool, message: str) -> Refused:
    if commits:
        return Refused("outcome_uncertain", message + "; send the identical proposal again to learn the outcome",
                       DOOR_STATUSES["outcome_uncertain"])
    return Refused("door_failed", message)


def _query_worker(store_root: str, max_rows: int, requests: Any) -> None:
    """Answer queries against the selected generation of each projection
    version, reopening one when its pointer moves."""
    opened: dict[int, tuple[str, Any]] = {}
    store = Path(store_root)
    while True:
        sparql, version, rdf_format = requests.recv()
        try:
            pointer = store / f"CURRENT-p{version}"
            selected = (pointer if pointer.exists() else store / "CURRENT").read_text(encoding="ascii").strip()
            if version not in opened or opened[version][0] != selected:
                opened[version] = (selected, world._read_current(store, version))
            projection = opened[version][1]
            answer = world.query(projection, sparql, max_rows, rdf_format)
            # Encoded here, in parallel with the other workers, and sent as
            # bytes, which cross the pipe far faster than the parsed value.
            data = json.dumps(answer.data, ensure_ascii=False, sort_keys=True).encode("utf-8")
            requests.send(("ok", selected, projection.snapshot, answer.media_type, data))
        except world.ProjectionNotServed as exc:
            requests.send(("projection_not_served", str(exc)))
        except world.QueryRefused as exc:
            requests.send(("query_refused", str(exc)))
        except world.TooManyRows as exc:
            requests.send(("query_too_many_rows", str(exc)))
        except SyntaxError as exc:
            requests.send(("query_invalid", str(exc)))
        except (world.ProjectionError, OSError) as exc:
            requests.send(("world_unavailable", str(exc)))
        except ValueError as exc:
            # The engine reports evaluation errors, such as a bad IRI, this way.
            requests.send(("query_invalid", str(exc)))


class QueryWorker:
    """One worker process holding World open; stopped and replaced when a query overruns."""

    def __init__(self, store_root: Path, max_rows: int, timeout: float):
        self.store_root, self.max_rows, self.timeout = store_root, max_rows, timeout
        self.context = multiprocessing.get_context("spawn")
        self.process = None
        self.connection = None

    def _start(self) -> None:
        parent, child = self.context.Pipe()
        self.process = self.context.Process(
            target=_query_worker, args=(str(self.store_root), self.max_rows, child), daemon=True)
        self.process.start()
        self.connection = parent

    def stop(self) -> None:
        if self.process is not None:
            self.process.kill()
            self.process.join()
        self.process = self.connection = None

    def query(self, sparql: str, version: int, rdf_format: str) -> tuple[str, str, Any]:
        if self.process is None or not self.process.is_alive():
            self._start()
        self.connection.send((sparql, version, rdf_format))
        if not self.connection.poll(self.timeout):
            self.stop()
            raise Refused("query_timeout", f"the query ran longer than {self.timeout:g} seconds")
        try:
            answer = self.connection.recv()
        except EOFError as exc:
            self.stop()
            raise Refused("internal_error", "the query worker stopped") from exc
        if answer[0] != "ok":
            raise Refused(answer[0], answer[1])
        return answer[1:]


class QueryPool:
    """Several query workers, so one long read does not hold up the others.

    A request takes an idle worker, waiting for one when all are busy. Each
    worker opens World read-only in its own process; Oxigraph lets any number
    of read-only openers share a store.
    """

    def __init__(self, store_root: Path, max_rows: int, timeout: float, size: int):
        self.workers = [QueryWorker(store_root, max_rows, timeout) for _ in range(size)]
        self.idle: queue.Queue[QueryWorker] = queue.Queue()
        for worker in self.workers:
            self.idle.put(worker)

    def query(self, sparql: str, version: int, rdf_format: str) -> tuple[str, str, str, bytes]:
        """(generation, snapshot, media type, encoded data) for one query."""
        worker = self.idle.get()
        try:
            return worker.query(sparql, version, rdf_format)
        finally:
            self.idle.put(worker)

    def stop(self) -> None:
        for worker in self.workers:
            worker.stop()


class AnswerCache:
    """Encoded answers by (projection, generation, format, query text).

    A generation never changes once built (ADR 0055), so an answer for it
    stays right for as long as it is kept; a new generation is a new key.
    Least recently used answers go first once ``max_bytes`` is reached.
    """

    def __init__(self, max_bytes: int):
        self.max_bytes = max_bytes
        self.entries: OrderedDict[tuple, dict[str, Any]] = OrderedDict()
        self.size = 0
        self.lock = threading.Lock()
        self.pending: dict[tuple, threading.Event] = {}

    def get(self, key: tuple) -> dict[str, Any] | None:
        with self.lock:
            entry = self.entries.get(key)
            if entry is not None:
                self.entries.move_to_end(key)
            return entry

    def put(self, key: tuple, entry: dict[str, Any]) -> None:
        cost = len(entry["result"]) + len(key[3])
        if cost > self.max_bytes:
            return
        with self.lock:
            if key in self.entries:
                return
            entry["cost"] = cost
            self.entries[key] = entry
            self.size += cost
            while self.size > self.max_bytes:
                _, dropped = self.entries.popitem(last=False)
                self.size -= dropped["cost"] + dropped.get("gzip_cost", 0)

    def add_gzip(self, key: tuple, archive: bytes, body: bytes) -> None:
        """Keep the compressed body for one archive block beside the answer."""
        with self.lock:
            entry = self.entries.get(key)
            if entry is None or entry.get("gzip_archive") == archive:
                return
            self.size += len(body) - entry.get("gzip_cost", 0)
            entry.update(gzip_archive=archive, gzip_body=body, gzip_cost=len(body))
            while self.size > self.max_bytes and len(self.entries) > 1:
                _, dropped = self.entries.popitem(last=False)
                self.size -= dropped["cost"] + dropped.get("gzip_cost", 0)

    def recent(self, limit: int) -> list[tuple]:
        with self.lock:
            return list(self.entries)[-limit:][::-1]

    def drop_other_generations(self, version: int, generation: str) -> None:
        with self.lock:
            for key in [k for k in self.entries if k[0] == version and k[1] != generation]:
                dropped = self.entries.pop(key)
                self.size -= dropped["cost"] + dropped.get("gzip_cost", 0)

    def claim(self, key: tuple) -> threading.Event | None:
        """None if this caller should compute ``key``; otherwise an event
        set when the caller already computing it finishes."""
        with self.lock:
            if key in self.pending:
                return self.pending[key]
            self.pending[key] = threading.Event()
            return None

    def release(self, key: tuple) -> None:
        with self.lock:
            event = self.pending.pop(key, None)
        if event is not None:
            event.set()


def result_bytes(snapshot: str, projection: int, media_type: str, data: bytes) -> bytes:
    """The ``result`` member of a query answer, as json.dumps would write it."""
    return (b'{"data": ' + data + b', "media_type": ' + json.dumps(media_type).encode()
            + b', "projection": ' + str(projection).encode() + b', "snapshot": '
            + json.dumps(snapshot).encode() + b"}")


def accepts_gzip(header: str | None) -> bool:
    """Whether an Accept-Encoding header allows gzip (RFC 9110 12.5.3)."""
    if not header:
        return False
    allowed = {}
    for item in header.split(","):
        name, _, parameters = item.strip().partition(";")
        quality = 1.0
        for parameter in parameters.split(";"):
            key, _, value = parameter.strip().partition("=")
            if key.lower() == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        allowed[name.strip().lower()] = quality
    return allowed.get("gzip", allowed.get("*", 0.0)) > 0


class Relay:
    def __init__(self, config: Config):
        private_directory(config.staging)
        self.config = config
        self.archive_id, _ = read_archive_manifest(config.archive_root)
        self.queries = QueryPool(config.world_store, config.max_rows, config.query_timeout_seconds,
                                 config.query_workers)
        self.cache = AnswerCache(config.cache_bytes)
        # Door's service is serial, and a commit's World refresh must finish
        # before the next write is planned against it (ADR 0056); reads are
        # not held by this lock.
        self.writes = threading.Lock()
        self.generations: dict[int, str | None] = {}
        self.warm_now = threading.Event()
        self.stopping = threading.Event()
        self.warmer = threading.Thread(target=self._warm_forever, name="relay-warmer", daemon=True)
        self.warmer.start()

    def stop(self) -> None:
        self.stopping.set()
        self.warm_now.set()
        self.warmer.join(timeout=30)
        self.queries.stop()

    def archive(self) -> dict[str, Any]:
        """What every authorized answer says about the archive and World. The
        specification is read again for each answer, so an upgrade committed
        while the relay runs is reported from the next answer on."""
        archive_id, spec = read_archive_manifest(self.config.archive_root)
        if archive_id != self.archive_id:
            raise ValueError(f"{self.config.archive_root / 'archive.json'} names another archive")
        return {"archive_id": self.archive_id, "spec": spec, "role": self.config.role,
                "projections": world.served_projections(self.config.world_store)}

    def health(self) -> tuple[HTTPStatus, dict[str, Any]]:
        door = "reachable"
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.settimeout(5)
                probe.connect(str(self.config.door_socket))
        except OSError:
            door = "unreachable"
        try:
            selected = world._read_current(self.config.world_store)
            world_state: dict[str, Any] = {"status": "ok", "snapshot": selected.snapshot}
        except (world.ProjectionError, OSError) as exc:
            world_state = {"status": "unavailable", "detail": str(exc)}
        healthy = door == "reachable" and world_state["status"] == "ok"
        answer = {"healthy": healthy, "writes": self.config.writes, "door": door, "world": world_state}
        return (HTTPStatus.OK if healthy else HTTPStatus.SERVICE_UNAVAILABLE), answer

    def query(self, body: Any) -> tuple[tuple, dict[str, Any]]:
        """The cache key and cached entry answering one query request."""
        if (not isinstance(body, dict) or not {"query", "projection"} <= set(body) <= {"query", "projection", "rdf_format"}
                or not isinstance(body["query"], str) or type(body["projection"]) is not int):
            raise Refused("request_malformed",
                          "a query is {query, projection, rdf_format?}, with an integer projection")
        rdf_format = body.get("rdf_format", world.N_TRIPLES)
        if rdf_format not in world.GRAPH_FORMATS:
            raise Refused("request_malformed", f"rdf_format must be one of {', '.join(world.GRAPH_FORMATS)}")
        return self.answer(body["query"], body["projection"], rdf_format)

    def answer(self, sparql: str, version: int, rdf_format: str) -> tuple[tuple, dict[str, Any]]:
        while True:
            waiting = None
            generation = world.selected_generation(self.config.world_store, version)
            key = (version, generation, rdf_format, sparql)
            if generation is not None:
                cached = self.cache.get(key)
                if cached is not None:
                    return key, cached
                waiting = self.cache.claim(key)
                if waiting is not None:
                    # The same question is being answered; share its answer.
                    waiting.wait(self.config.query_timeout_seconds * 2)
                    if self.cache.get(key) is not None:
                        continue
            try:
                answered, snapshot, media_type, data = self.queries.query(sparql, version, rdf_format)
            finally:
                if generation is not None and waiting is None:
                    self.cache.release(key)
            entry = {"result": result_bytes(snapshot, version, media_type, data)}
            key = (version, answered, rdf_format, sparql)
            self.cache.put(key, entry)
            return key, entry

    def _warm_forever(self) -> None:
        """Answer recent questions again whenever a projection's generation
        changes, whether this relay's commit or anyone's refresh changed it,
        so the first read after a refresh is not the slow one."""
        while not self.stopping.is_set():
            self.warm_now.wait(WARM_POLL_SECONDS)
            self.warm_now.clear()
            if self.stopping.is_set():
                return
            try:
                self.warm()
            except Exception as exc:  # noqa: BLE001 - warming is an optimisation; reads still answer
                sys.stderr.write(f"relay: warming failed: {type(exc).__name__}: {exc}\n")

    def warm(self) -> int:
        """Re-ask the recent questions of each projection whose generation
        moved; returns how many were answered."""
        warmed = 0
        for version in world.served_projections(self.config.world_store):
            generation = world.selected_generation(self.config.world_store, version)
            previous = self.generations.get(version)
            self.generations[version] = generation
            if generation is None or previous in (None, generation):
                continue
            for key in self.cache.recent(WARM_QUERIES):
                if key[0] == version and key[1] == previous and not self.stopping.is_set():
                    try:
                        self.answer(key[3], version, key[2])
                        warmed += 1
                    except Refused:
                        pass  # it would be refused for its asker too, who will see why
            self.cache.drop_other_generations(version, generation)
        return warmed

    def door(self, path: str, body: Any, agent: str) -> dict[str, Any]:
        with self.writes:
            answer = self._door(path, body, agent)
        if "world" in answer:
            self.warm_now.set()
        return answer

    def _door(self, path: str, body: Any, agent: str) -> dict[str, Any]:
        kind, dry_run = DOOR_ROUTES[path]
        if not dry_run and self.config.writes != "commit":
            raise Refused("commits_disabled", "commits are off: the relay runs in dry-run mode (ADR 0056)")
        manifest, drafts = check_proposal(kind, body, agent)
        try:
            directory = stage_proposal(self.config.staging, manifest, drafts)
        except OSError as exc:
            raise Refused("internal_error", f"the relay could not stage the proposal: {exc}") from exc
        try:
            result = door_request(self.config.door_socket, kind + ("-dry-run" if dry_run else ""), directory,
                                  commits=not dry_run)
        finally:
            shutil.rmtree(directory, ignore_errors=True)
        answer: dict[str, Any] = {"door": result}
        if not dry_run:
            # Door has committed. A failed refresh is reported, never hidden:
            # queries keep answering from the earlier snapshot until it works.
            try:
                refreshed = world.refresh_served(self.config.archive_root, self.config.world_store,
                                                 archive_command=self.config.archive_command)
                newest = refreshed[max(refreshed)]
                answer["world"] = {"status": "refreshed", "snapshot": newest.projection.snapshot,
                                   "projections": {str(v): item.mode for v, item in sorted(refreshed.items())}}
            except Exception as exc:  # noqa: BLE001 - the commit stands whatever the refresh does
                answer["world"] = {"status": "stale", "detail": f"{type(exc).__name__}: {exc}"}
        return answer


def make_handler(relay: Relay) -> type[BaseHTTPRequestHandler]:
    config = relay.config

    class Handler(BaseHTTPRequestHandler):
        server_version = "archive-relay"
        sys_version = ""
        error_content_type = "application/json"
        error_message_format = '{"ok": false, "error": {"code": "request_malformed", "message": "%(message)s"}}'

        def log_message(self, format: str, *args: Any) -> None:
            # Request lines and statuses only; bodies hold personal data, and
            # the caller is logged as its agent, never its login.
            headers = getattr(self, "headers", None)
            login = headers.get("Tailscale-User-Login") if headers is not None else None
            sys.stderr.write("relay: %s %s\n" % (config.callers.get(login, "-"), format % args))

        def _agent(self) -> str:
            login = self.headers.get("Tailscale-User-Login")
            if login not in config.callers:
                raise Refused("forbidden_identity", "this tailnet identity may not use the relay")
            origin = self.headers.get("Origin")
            if origin is not None and origin not in config.origins:
                raise Refused("forbidden_origin", "this origin may not use the relay")
            return config.callers[login]

        def _send(self, status: HTTPStatus, value: Any, allow: str | None = None, body: bytes | None = None,
                  compressed: bytes | None = None) -> None:
            if body is None:
                body = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
            if accepts_gzip(self.headers.get("Accept-Encoding")) and len(body) >= GZIP_MIN_BYTES:
                body = compressed if compressed is not None else gzip.compress(body, compresslevel=5)
                encoding = "gzip"
            else:
                encoding = None
            self.send_response(status)
            origin = self.headers.get("Origin")
            if origin in config.origins:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            if allow:
                self.send_header("Allow", allow)
            self.send_header("Vary", "Accept-Encoding")
            if encoding:
                self.send_header("Content-Encoding", encoding)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _answer(self, method: str) -> None:
            authorized = False
            try:
                agent = self._agent()
                authorized = True
                expected = ROUTES.get(self.path)
                if expected is None:
                    raise Refused("not_found", "no such route")
                if expected != method:
                    raise Refused("method_not_allowed", f"{self.path} answers {expected} only")
                if self.path == "/health":
                    status, result = relay.health()
                elif self.path == "/world/query":
                    self._send_answer(*relay.query(self._body()))
                    return
                else:
                    status, result = HTTPStatus.OK, relay.door(self.path, self._body(), agent)
                self._send(status, {"ok": status == HTTPStatus.OK, "archive": relay.archive(), "result": result})
            except Refused as refused:
                answer: dict[str, Any] = {"ok": False, "error": refused.error()}
                if authorized:
                    answer["archive"] = relay.archive()
                allow = ROUTES.get(self.path) if refused.code == "method_not_allowed" else None
                self._send(refused.status, answer, allow)
            except Exception as exc:  # noqa: BLE001 - every request gets an answer
                sys.stderr.write(f"relay: internal error: {type(exc).__name__}: {exc}\n")
                self._send(HTTPStatus.INTERNAL_SERVER_ERROR,
                           {"ok": False, "error": {"code": "internal_error", "message": "the relay failed unexpectedly"}})

        def _send_answer(self, key: tuple, entry: dict[str, Any]) -> None:
            """A query answer from its cached ``result`` bytes, with the
            archive block as of now and, when asked, the cached gzip body."""
            archive = json.dumps(relay.archive(), ensure_ascii=False, sort_keys=True).encode("utf-8")
            body = b'{"archive": ' + archive + b', "ok": true, "result": ' + entry["result"] + b"}"
            compressed = None
            if accepts_gzip(self.headers.get("Accept-Encoding")) and len(body) >= GZIP_MIN_BYTES:
                if entry.get("gzip_archive") == archive:
                    compressed = entry["gzip_body"]
                else:
                    compressed = gzip.compress(body, compresslevel=5)
                    relay.cache.add_gzip(key, archive, compressed)
            self._send(HTTPStatus.OK, None, body=body, compressed=compressed)

        def _body(self) -> Any:
            if self.headers.get_content_type() != "application/json":
                raise Refused("unsupported_media_type", "send application/json")
            declared = self.headers.get("Content-Length")
            if declared is None:
                raise Refused("length_required", "Content-Length is required")
            try:
                length = int(declared)
            except ValueError as exc:
                raise Refused("request_malformed", "Content-Length must be a number of bytes") from exc
            if length < 0:
                raise Refused("request_malformed", "Content-Length must not be negative")
            if length > MAX_BODY:
                raise Refused("body_too_large", f"bodies are limited to {MAX_BODY} bytes")
            try:
                return json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise Refused("request_malformed", "the body is not JSON") from exc

        def do_OPTIONS(self) -> None:
            def preflight() -> None:
                self._agent()
                if self.headers.get("Origin") is None:
                    raise Refused("request_malformed", "a preflight names its origin")
            try:
                preflight()
            except Refused as refused:
                self._send(refused.status, {"ok": False, "error": refused.error()})
                return
            self.send_response(HTTPStatus.NO_CONTENT)
            self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
            self.send_header("Access-Control-Allow-Methods", "GET, POST")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Vary", "Origin")
            self.end_headers()

        def do_GET(self) -> None:
            self._answer("GET")

        def do_POST(self) -> None:
            self._answer("POST")

        def do_PUT(self) -> None:
            self._answer("PUT")

        def do_DELETE(self) -> None:
            self._answer("DELETE")

        def do_PATCH(self) -> None:
            self._answer("PATCH")

    return Handler


def make_server(relay: Relay, host: str, port: int) -> ThreadingHTTPServer:
    """One thread per connection: reads run side by side in the query pool,
    and writes take Relay.writes in turn."""
    server = ThreadingHTTPServer((host, port), make_handler(relay))
    server.daemon_threads = True
    return server


def serve(config: Config) -> None:
    relay = Relay(config)
    server = make_server(relay, config.host, config.port)
    sys.stderr.write(f"relay: listening on {config.host}:{config.port}, role {config.role}, writes {config.writes}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        relay.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="relay", description="Relay the operator's tailnet to Door's service and World")
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"relay: {exc}", file=sys.stderr)
        return 2
    serve(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
