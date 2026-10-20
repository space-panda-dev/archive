"""Turn an enrichment tool's result into an admission proposal for Door.

A tool takes files and returns a plain result. This adapter is the
Archive-facing part ADR 0006 describes: it reads the archive only through
`archive export`, packages the result as one derivation run and its outputs
(ADR 0064), stages the run's parameters as content when no Artifact already
holds them (ADR 0023), and hands the batch to `door admit`. It never writes a
record or touches the archive's files itself: Door decides every revision.

Standard library only. The interface is documented in README.md.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BY = "pkg:generic/archive-enrich@0.1.0"
RUN_CONTRACT = ("archive.claim/derivation_run", "1")
RUN_NAMESPACE = "archive.derivation/run"
ARTIFACT_NAMESPACE = "archive.core/artifact"
STAGING_REMOTE = "enrich-staging"
_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class AdapterError(Exception):
    """The adapter refused to propose, or Door refused the proposal."""


# -- canonical JSON (RFC 8785) and identities --------------------------------

def canonical(value: Any) -> bytes:
    """RFC 8785 canonical JSON for objects, arrays, strings, integers,
    booleans, and null. Floats are refused: their RFC 8785 form is not
    Python's, and nothing an enrichment proposes needs one."""
    return _canonical(value).encode("utf-8")


def _canonical(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return json.dumps(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        raise AdapterError("canonical JSON here does not encode floating-point numbers")
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_canonical(item) for item in value) + "]"
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise AdapterError("canonical JSON object keys must be strings")
        # RFC 8785 orders members by their keys' UTF-16 code units.
        keys = sorted(value, key=lambda key: key.encode("utf-16-be"))
        return "{" + ",".join(json.dumps(key, ensure_ascii=False) + ":" + _canonical(value[key])
                              for key in keys) + "}"
    raise AdapterError(f"canonical JSON cannot encode {type(value).__name__}")


def digest_of(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def revision_of(draft: dict[str, Any]) -> str:
    """The revision Door will compute for a draft: SHA-256 over the canonical
    record without `revision_id` and `at`. Door's answer is compared with it."""
    return digest_of(canonical({key: item for key, item in draft.items() if key not in ("revision_id", "at")}))


def object_id(archive_id: str, namespace: str, name: str) -> str:
    """D-2: UUIDv5 in the archive's namespace over `namespace NUL name`."""
    return "urn:uuid:" + str(uuid.uuid5(uuid.UUID(archive_id.removeprefix("urn:uuid:")), namespace + "\0" + name))


def new_run_name() -> str:
    """D-15: `run-` and a lowercase UUIDv4, so every run is a new object."""
    return "run-" + str(uuid.uuid4())


def check_package_url(value: str, what: str = "agent") -> str:
    """D-7's rules for a Package URL agent: `pkg:`, a type and a name, no
    qualifier, subpath, authority, or component that parses as a hostname."""
    rest = value.removeprefix("pkg:") if value.startswith("pkg:") else None
    if not rest or any(c in rest for c in "?#:") or "//" in rest:
        raise AdapterError(f"{what} {value!r} is not a canonical Package URL without qualifiers or subpath")
    components = rest.split("/")
    if len(components) < 2 or any(not c for c in components):
        raise AdapterError(f"{what} {value!r} needs a type and a name")
    for component in components:
        if not all(c.isalnum() or c in "._-~%@" for c in component):
            raise AdapterError(f"{what} {value!r} has a character a Package URL component may not hold")
        labels = component.split(".")
        if len(labels) > 1 and all(label and label[0] != "-" and label[-1] != "-"
                                   and all(c.isalnum() or c == "-" for c in label) for label in labels):
            raise AdapterError(f"{what} {value!r} has a component that reads as a hostname")
    return value


def utc_instant(moment: datetime) -> str:
    """RFC 3339 in UTC with a `Z` suffix, as D-15 requires of run instants."""
    if moment.tzinfo is None:
        raise AdapterError("run instants must be timezone-aware")
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def now() -> datetime:
    return datetime.now(timezone.utc)


# -- what a tool hands the adapter -------------------------------------------

@dataclass(frozen=True)
class Pin:
    """A reference to an exact revision."""
    role: str
    object_id: str
    revision_id: str

    def ref(self) -> dict[str, str]:
        return {"role": self.role, "id": self.object_id, "revision_id": self.revision_id}


@dataclass(frozen=True)
class Output:
    """One claim a run made. `name` is a token unique among the run's
    outputs; the claim's identity name is the run's name, a hyphen, and this
    token, in the namespace of its contract, so it is new with every run.
    `refs` are its own pins; the adapter adds the `run` pin. When
    `parameters_role` is set, the adapter also pins the run's parameters
    Artifact in that role, since a new one exists only inside `propose`."""
    contract: tuple[str, str]
    name: str
    payload: dict[str, Any]
    refs: tuple[Pin, ...] = ()
    parameters_role: str | None = None


@dataclass(frozen=True)
class RunResult:
    """One execution of one method, as a tool reports it."""
    agent: str
    method_id: str
    method_version: str
    software: tuple[tuple[str, str], ...]
    started_at: datetime
    ended_at: datetime
    used: tuple[Pin, ...]
    outputs: tuple[Output, ...]
    parameters: Any = None  # JSON value, or None for a run without configuration

    def parameter_bytes(self) -> bytes | None:
        return None if self.parameters is None else canonical(self.parameters)


@dataclass
class Admission:
    status: str  # "committed" | "predicted" | "unchanged"
    run: dict[str, str]
    outputs: list[dict[str, str]] = field(default_factory=list)
    parameters: dict[str, Any] | None = None
    revision_ids: list[str] = field(default_factory=list)

    def as_json(self) -> dict[str, Any]:
        return {"status": self.status, "run": self.run, "outputs": self.outputs,
                "parameters": self.parameters, "revision_ids": self.revision_ids}


# -- reading the archive -----------------------------------------------------

def _run(arguments: list[str], cwd: Path, what: str, env: dict[str, str] | None = None) -> str:
    try:
        completed = subprocess.run(arguments, cwd=cwd, env=env, capture_output=True, text=True)
    except OSError as exc:
        raise AdapterError(f"{what} could not run: {exc}") from exc
    if completed.returncode != 0:
        raise AdapterError(f"{what} failed ({completed.returncode}): "
                           f"{refusal_text(completed.stderr) or completed.stdout.strip()}")
    return completed.stdout


def refusal_text(stderr: str) -> str:
    """A command's stderr for a person. Door ends a refusal with its JSON
    reply, `{"ok": false, "error": {"code", "message"}}`, after the line that
    says it; that line is kept and the code added, not the JSON repeated."""
    text = stderr.strip()
    head, _, last = text.rpartition("\n")
    try:
        reply = json.loads(last)
    except ValueError:
        return text
    error = reply.get("error") if isinstance(reply, dict) and reply.get("ok") is False else None
    if not isinstance(error, dict) or not isinstance(error.get("code"), str):
        return text
    return f"{head.strip() or error.get('message', '')} (refusal code {error['code']})"


class ArchiveReader:
    """The committed archive as `archive export` reports it.

    An incomplete or failed export is refused: the adapter never proposes
    against part of an archive (INV-24)."""

    def __init__(self, root: Path, archive: str = "archive", env: dict[str, str] | None = None):
        self.root = Path(root).absolute()
        try:
            completed = subprocess.run([archive, "export"], cwd=self.root, env=env, capture_output=True, text=True)
        except OSError as exc:
            raise AdapterError(f"archive export could not run: {exc}") from exc
        try:
            export = json.loads(completed.stdout)
        except ValueError:
            export = None
        if completed.returncode != 0 or not isinstance(export, dict) or export.get("status") != "ok":
            status = export.get("status") if isinstance(export, dict) else None
            raise AdapterError(f"archive export of {self.root} is not complete (status {status!r}, "
                               f"exit {completed.returncode}): {completed.stderr.strip()}")
        self.load(export)

    @classmethod
    def from_export(cls, root: Path, export: dict[str, Any]) -> "ArchiveReader":
        reader = cls.__new__(cls)
        reader.root = Path(root).absolute()
        reader.load(export)
        return reader

    def load(self, export: dict[str, Any], installed: dict[tuple[str, str], str] | None = None) -> None:
        self.export = export
        self.snapshot: str | None = export.get("snapshot")
        self.archive_id: str = export["archive_id"]
        self.spec: str = export["spec"]
        self.records: list[dict[str, Any]] = export["records"]
        self.by_revision = {record["revision_id"]: record for record in self.records}
        superseded = {ref["revision_id"] for record in self.records for ref in record.get("supersedes", [])}
        self.heads: dict[str, dict[str, Any]] = {}
        for record in self.records:
            if record["revision_id"] not in superseded:
                self.heads[record["object_id"]] = record
        # The installed bundles, by the digest Door resolves a citation by:
        # canonical JSON of every bundle takes about a second on a 0.0.10
        # archive, so a refresh (same archive, same specification) keeps them.
        if installed is not None:
            self.installed = installed
            return
        self.installed: dict[tuple[str, str], str] = {}
        for bundle in export["contracts"]:
            self.installed[(bundle["id"], bundle["version"])] = digest_of(canonical(bundle))

    def refresh(self, archive: str = "archive", env: dict[str, str] | None = None) -> None:
        """Add what was committed since this reader's snapshot, from `archive
        export --since` (ADR 0055), instead of exporting every record again.
        A specification or archive change between the two is refused: the
        installed contracts this reader holds would no longer be the
        archive's."""
        if self.snapshot is None:
            raise AdapterError("this reader has no snapshot to refresh from")
        try:
            completed = subprocess.run([archive, "export", "--since", self.snapshot], cwd=self.root, env=env,
                                       capture_output=True, text=True)
        except OSError as exc:
            raise AdapterError(f"archive export --since could not run: {exc}") from exc
        try:
            since = json.loads(completed.stdout)
        except ValueError:
            since = None
        if completed.returncode != 0 or not isinstance(since, dict) or since.get("status") != "ok":
            raise AdapterError(f"archive export --since {self.snapshot} failed (exit {completed.returncode}): "
                               f"{completed.stderr.strip() or completed.stdout.strip()[:300]}")
        if (since.get("archive_id"), since.get("spec")) != (self.archive_id, self.spec):
            raise AdapterError(f"the archive changed from {self.archive_id} at {self.spec} to "
                               f"{since.get('archive_id')} at {since.get('spec')} while it was being read")
        known = set(self.by_revision)
        added = [record for record in since["records"] if record["revision_id"] not in known]
        self.load({**self.export, "snapshot": since["snapshot"], "records": [*self.records, *added]}, self.installed)

    def citation(self, contract: tuple[str, str]) -> dict[str, str]:
        digest = self.installed.get(contract)
        if digest is None:
            raise AdapterError(f"the archive does not install {contract[0]} version {contract[1]} "
                               f"(its specification is {self.spec})")
        return {"id": contract[0], "version": contract[1], "digest": digest}

    def head(self, object_id_: str) -> dict[str, Any]:
        record = self.heads.get(object_id_)
        if record is None:
            raise AdapterError(f"the archive holds no object {object_id_}")
        return record

    def artifact_with_digest(self, digest: str) -> dict[str, Any] | None:
        """The head revision of the first Artifact, in export order, whose
        content is exactly these bytes."""
        for record in self.records:
            if record["shape"] == "artifact" and record["payload"].get("content", {}).get("digest") == digest \
                    and self.heads.get(record["object_id"]) is record:
                return record
        return None

    def cites(self, record: dict[str, Any], contract: tuple[str, str]) -> bool:
        return any(c.get("id") == contract[0] and c.get("version") == contract[1]
                   for c in record.get("contracts", []))

    def equivalent_run(self, result: RunResult) -> dict[str, Any] | None:
        """A recorded run of the same agent, method, software, used revisions,
        and parameter bytes that kept at least one output. Rerunning it would
        write nothing new (INV-5), so a caller may skip the work."""
        parameters = result.parameter_bytes()
        wanted_parameters = None if parameters is None else digest_of(parameters)
        wanted_used = sorted((pin.object_id, pin.revision_id) for pin in result.used)
        method = {"id": result.method_id, "version": result.method_version}
        software = [{"name": name, "version": version} for name, version in result.software]
        generated = {ref["revision_id"] for record in self.records for ref in record.get("refs", [])
                     if ref.get("role") == "run" and ref.get("revision_id")}
        for record in self.records:
            if record["shape"] != "claim" or not self.cites(record, RUN_CONTRACT):
                continue
            payload = record["payload"]
            if record["agent"] != result.agent or payload.get("method") != method \
                    or payload.get("software") != software or record["revision_id"] not in generated:
                continue
            refs = record.get("refs", [])
            used = sorted((ref["id"], ref.get("revision_id")) for ref in refs if ref["role"] == "used")
            pinned = [ref.get("revision_id") for ref in refs if ref["role"] == "parameters"]
            digest = None
            if pinned:
                artifact = self.by_revision.get(pinned[0])
                digest = artifact["payload"]["content"]["digest"] if artifact else None
            if used == wanted_used and digest == wanted_parameters:
                return record
        return None


# -- staging content ---------------------------------------------------------

# Linux's FICLONE ioctl: the new file shares the source's extents (a Btrfs
# reflink), so staging a large recording writes no data.
FICLONE = 0x40049409
# What a file system or kernel answers when it cannot share extents between
# these two files; anything else is a real failure.
_NO_CLONE = {errno.EXDEV, errno.EOPNOTSUPP, errno.ENOTTY, errno.EINVAL, errno.ENOSYS, errno.EPERM}


def clone_file(source: Path, target: Path) -> bool:
    """Make ``target`` a reflink of ``source`` when the file system can, and
    answer whether it did. A file system that cannot clone answers False and
    leaves ``target`` empty, so the caller copies instead."""
    try:
        import fcntl
    except ImportError:
        return False
    with open(source, "rb") as reading, open(target, "wb") as writing:
        try:
            fcntl.ioctl(writing.fileno(), FICLONE, reading.fileno())
        except OSError as exc:
            if exc.errno in _NO_CLONE:
                writing.truncate(0)
                return False
            raise
    return True


class Staging:
    """A git-annex repository that offers new content to the archive as a
    configured remote (ADR 0023). The remote's configuration in the archive is
    operational, not evidence; the bytes count only once Door admits them."""

    def __init__(self, path: Path, git: str, annex: str, env: dict[str, str] | None = None,
                 remote: str = STAGING_REMOTE):
        self.path = Path(path).absolute()
        self.git, self.annex, self.env, self.remote = git, annex, env, remote

    def remote_name(self, archive_root: Path, create: bool) -> str:
        """The archive's remote for this repository: an existing remote whose
        URL is this path, else this staging's remote name (`enrich-staging`
        by default), added when `create` is set."""
        listed = subprocess.run([self.git, "config", "--get-regexp", r"^remote\..*\.url$"], cwd=archive_root,
                                env=self.env, capture_output=True, text=True)
        if listed.returncode not in (0, 1):  # 1 means no remote is configured
            raise AdapterError(f"reading the archive's remotes failed: {listed.stderr.strip()}")
        mine = os.path.realpath(self.path)
        names = {}
        for line in listed.stdout.splitlines():
            key, _, url = line.partition(" ")
            name = key.removeprefix("remote.").removesuffix(".url")
            names[name] = url
            if os.path.realpath(url) == mine:
                return name
        if self.remote in names:
            raise AdapterError(f"the archive's remote {self.remote} is {names[self.remote]}, "
                               f"not the staging repository {self.path}")
        if create:
            _run([self.git, "remote", "add", self.remote, str(self.path)], archive_root,
                 "adding the staging remote", self.env)
        return self.remote

    def expected_key(self, data: bytes, filename: str) -> str:
        """git-annex's SHA256E key for these bytes under this file name."""
        return self.key_for(digest_of(data), len(data), filename)

    def key_for(self, digest: str, size: int, filename: str) -> str:
        """git-annex's SHA256E key for content of this digest and size under
        this file name."""
        extension = "".join(Path(filename).suffixes)
        return f"SHA256E-s{size}--{digest.removeprefix('sha256:')}{extension}"

    def _repository(self) -> None:
        if not self.path.exists() or not any(self.path.iterdir()):
            self.path.mkdir(parents=True, exist_ok=True)
            _run([self.git, "init", "-q"], self.path, "git init for the staging repository", self.env)
            _run([self.annex, "init", "-q", "enrich staging"], self.path, "git-annex init", self.env)
        elif not (self.path / ".git").exists():
            raise AdapterError(f"{self.path} is neither empty nor a git repository")

    def _added(self, filename: str, expected: str) -> str:
        key = _run([self.annex, "lookupkey", filename], self.path, "git-annex lookupkey", self.env).strip()
        if key != expected:
            raise AdapterError(f"staged {filename} has key {key}, not the key of the proposed bytes")
        return key

    def stage(self, data: bytes, filename: str) -> str:
        """Add the bytes to the repository, creating it when absent, and
        return their annex key. Staged bytes already present write nothing."""
        self._repository()
        target = self.path / filename
        if not target.exists():
            target.write_bytes(data)
            _run([self.annex, "add", "-q", filename], self.path, "git-annex add", self.env)
            _run([self.git, "commit", "-q", "-m", f"Stage {filename}"], self.path, "staging commit", self.env)
        return self._added(filename, self.expected_key(data, filename))

    def stage_file(self, source: Path, digest: str, size: int, filename: str) -> str:
        """Copy a file into the repository and add it, as `stage` does for
        bytes, without holding the file in memory. The source is only read.
        The key must be that of `digest` and `size`, so a file that changed
        after it was hashed is refused, never staged as the hashed content.
        The copy is hashed before it is added: refused bytes are removed and
        never committed, so a later run with the right bytes is not blocked
        by a wrong file already staged under the name."""
        self._repository()
        target = self.path / filename
        if not os.path.lexists(target):
            partial = self.path / f".{filename}.partial"
            try:
                hasher, copied = hashlib.sha256(), 0
                if clone_file(source, partial):
                    # The clone shares the source's extents; hashing it reads
                    # the bytes the archive will get, as a copy would.
                    with open(partial, "rb") as reading:
                        for block in iter(lambda: reading.read(1 << 20), b""):
                            hasher.update(block)
                            copied += len(block)
                else:
                    with open(source, "rb") as reading, open(partial, "wb") as writing:
                        for block in iter(lambda: reading.read(1 << 20), b""):
                            hasher.update(block)
                            copied += len(block)
                            writing.write(block)
                if ("sha256:" + hasher.hexdigest(), copied) != (digest, size):
                    raise AdapterError(f"{source} is no longer the content that was hashed ({digest}, "
                                       f"{size} bytes); nothing was staged")
                os.replace(partial, target)
            except OSError as exc:
                raise AdapterError(f"{source} could not be copied into the staging repository: {exc}") from exc
            finally:
                if os.path.lexists(partial):
                    os.unlink(partial)
            _run([self.annex, "add", "-q", filename], self.path, "git-annex add", self.env)
            _run([self.git, "commit", "-q", "-m", f"Stage {filename}"], self.path, "staging commit", self.env)
        return self._added(filename, self.key_for(digest, size, filename))


# -- the proposal ------------------------------------------------------------

def _check_result(result: RunResult) -> None:
    check_package_url(result.agent)
    if not result.outputs:
        raise AdapterError("a run that keeps no output is not proposed (ADR 0064)")
    if result.ended_at < result.started_at:
        raise AdapterError("the run ended before it started")
    names = [output.name for output in result.outputs]
    for name in names:
        if not _NAME.match(name):
            raise AdapterError(f"output identity name {name!r} is not an ASCII token")
    if len(set(zip(names, [o.contract for o in result.outputs]))) != len(names):
        raise AdapterError("two outputs of one run share an identity")
    for output in result.outputs:
        if any(pin.role == "run" for pin in output.refs):
            raise AdapterError("an output's run pin is the adapter's to add")
        if output.parameters_role is not None:
            if result.parameters is None:
                raise AdapterError("an output pins the run's parameters, but the run has none")
            if output.parameters_role == "run" or any(pin.role == output.parameters_role for pin in output.refs):
                raise AdapterError(f"an output's {output.parameters_role} pin is the adapter's to add")


def propose(reader: ArchiveReader, result: RunResult, *, door: str = "door",
            staging: Staging | None = None, dry_run: bool = False, by: str = BY,
            env: dict[str, str] | None = None) -> Admission:
    """Propose one run and all its outputs in one `door admit` batch.

    The parameters, when the result has any, are pinned from the first
    Artifact already holding exactly their canonical bytes; only when none
    does is a new Artifact proposed, which needs `staging`. A dry run stages
    nothing, configures nothing, and asks Door to predict."""
    _check_result(result)
    check_package_url(by, "by")
    run_name = new_run_name()
    run_id = object_id(reader.archive_id, RUN_NAMESPACE, run_name)
    drafts: list[tuple[str, dict[str, Any]]] = []
    identities: list[dict[str, str]] = []
    contents: list[dict[str, Any]] = []
    run_refs = [pin.ref() for pin in result.used]
    for pin in result.used:
        if pin.role != "used":
            raise AdapterError(f"a run's input pins have role used, not {pin.role!r}")
        pinned = reader.by_revision.get(pin.revision_id)
        if pinned is None or pinned["object_id"] != pin.object_id:
            raise AdapterError(f"used revision {pin.revision_id} of {pin.object_id} is not in the archive")

    parameters_report = None
    parameters_pin: dict[str, str] | None = None
    data = result.parameter_bytes()
    if data is not None:
        digest = digest_of(data)
        existing = reader.artifact_with_digest(digest)
        if existing is not None:
            parameters_pin = {"id": existing["object_id"], "revision_id": existing["revision_id"]}
            parameters_report = {"object_id": existing["object_id"], "revision_id": existing["revision_id"],
                                 "digest": digest, "reused": True}
        else:
            if staging is None:
                raise AdapterError("no Artifact holds this run's parameters yet; staging is needed to propose one")
            filename = digest.removeprefix("sha256:") + ".json"
            key = staging.expected_key(data, filename) if dry_run else staging.stage(data, filename)
            remote = staging.remote_name(reader.root, create=not dry_run)
            name = "parameters-" + str(uuid.uuid4())
            artifact_id = object_id(reader.archive_id, ARTIFACT_NAMESPACE, name)
            artifact = {"spec": reader.spec, "object_id": artifact_id, "shape": "artifact", "by": by,
                        "agent": result.agent, "payload": {"content": {"digest": digest, "size": len(data)}},
                        "refs": [], "supersedes": []}
            drafts.append(("parameters", artifact))
            identities.append({"object_id": artifact_id, "namespace": ARTIFACT_NAMESPACE, "name": name})
            contents.append({"digest": digest, "size": len(data), "annex_key": key, "staging_remote": remote})
            parameters_pin = {"id": artifact_id, "revision_id": revision_of(artifact)}
            parameters_report = {"object_id": artifact_id, "revision_id": revision_of(artifact),
                                 "digest": digest, "reused": False}

    if parameters_pin is not None:
        run_refs.append({"role": "parameters", **parameters_pin})
    run = {"spec": reader.spec, "object_id": run_id, "shape": "claim", "by": by, "agent": result.agent,
           "payload": {"method": {"id": result.method_id, "version": result.method_version},
                       "software": [{"name": n, "version": v} for n, v in result.software],
                       "started_at": utc_instant(result.started_at), "ended_at": utc_instant(result.ended_at)},
           "refs": run_refs, "supersedes": [], "contracts": [reader.citation(RUN_CONTRACT)]}
    run_revision = revision_of(run)
    drafts.append(("run", run))
    identities.append({"object_id": run_id, "namespace": RUN_NAMESPACE, "name": run_name})
    outputs = []
    for output in result.outputs:
        namespace = output.contract[0]
        name = f"{run_name}-{output.name}"
        output_id = object_id(reader.archive_id, namespace, name)
        draft = {"spec": reader.spec, "object_id": output_id, "shape": "claim", "by": by, "agent": result.agent,
                 "payload": output.payload,
                 "refs": [pin.ref() for pin in output.refs]
                 + ([{"role": output.parameters_role, **parameters_pin}]
                    if output.parameters_role is not None and parameters_pin is not None else [])
                 + [{"role": "run", "id": run_id, "revision_id": run_revision}],
                 "supersedes": [], "contracts": [reader.citation(output.contract)]}
        drafts.append((name, draft))
        identities.append({"object_id": output_id, "namespace": namespace, "name": name})
        outputs.append({"object_id": output_id, "revision_id": revision_of(draft), "contract": namespace})
    planned = [revision_of(draft) for _, draft in drafts]

    manifest = {"protocol": "archive-admission/1",
                "records": [f"records/{index:03d}.json" for index in range(len(drafts))],
                "identities": identities, "contents": contents,
                "expected_heads": [{"object_id": draft["object_id"], "revision_id": None} for _, draft in drafts]}
    proposal = Path(tempfile.mkdtemp(prefix="enrich-proposal-"))
    try:
        (proposal / "records").mkdir()
        (proposal / "manifest.json").write_bytes(canonical(manifest))
        for path, (_, draft) in zip(manifest["records"], drafts):
            (proposal / path).write_bytes(canonical(draft))
        command = [door, "admit", "--proposal", str(proposal)] + (["--dry-run"] if dry_run else [])
        output = _run(command, reader.root, "door admit", env)
    finally:
        shutil.rmtree(proposal, ignore_errors=True)
    try:
        answer = json.loads(output)
    except ValueError:
        answer = None
    if not isinstance(answer, dict):
        raise AdapterError(f"door admit answered something other than a JSON object: {output[:300]!r}")
    expected_status = "predicted" if dry_run else "committed"
    if answer.get("status") != expected_status or answer.get("revision_ids") != planned:
        raise AdapterError(f"Door answered {answer} but the adapter planned {planned}")
    return Admission(status=expected_status, run={"object_id": run_id, "revision_id": run_revision},
                     outputs=outputs, parameters=parameters_report, revision_ids=planned)
