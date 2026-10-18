#!/usr/bin/env python3
"""Fill the feature bank: run extractors on content (ADRs 0073, 0074).

    extract.py list [--extractors <dir>]...
    extract.py run <extractor|all> --content <file> [--digest sha256:<hex>] [--media-type <type>]
                   --bank <dir> [--jobs <n>] [--config <file>] [--program <name>=<path>]...
                   [--extractors <dir>]... [--timeout <seconds>] [--dry-run]

`run` resolves what the named extractor needs as a graph over the bank, runs
only the extractors whose entries are missing, each once, upstream first and
up to `--jobs` at a time, and prints one JSON object saying what each did:
`hit` (its entry was already there; nothing ran), `ran`, `failed`, `blocked`
(something it needs failed), or `skipped` (it does not accept the content's
media type). It exits 0 when every extractor hit or ran, 1 when any failed or
was blocked, and 2 when the arguments are not understood.

An extractor is a program in any language, named by its manifest and invoked
with one argument, the path of a request JSON; it writes its outputs and a
`result.json` into the directory the request names. The runner checks them
against the manifest, writes `entry.json`, and renames the directory into the
bank, so an entry is complete or absent. A failure is recorded beside where
the entry would be and leaves the bank as it was.

With `--digest`, the content is not hashed while every entry is present; the
first extractor to run hashes it and refuses the run if the digest is wrong,
so nothing is ever filed under another file's digest. `--dry-run` prints the
requests that would run, as a worker would receive them, and runs nothing.

The runner uses only Python's standard library. It never reads or writes an
archive and proposes nothing: features are not claims.
"""

from __future__ import annotations

import argparse
import datetime
import json
import mimetypes
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bank  # noqa: E402

RUNNER = {"name": "archive-enrich-extract", "version": "1"}
LOG_TAIL = 4000

# Media types for common recorder and camera files that `mimetypes` may not know.
_MEDIA_TYPES = {".flac": "audio/flac", ".wav": "audio/wav", ".mka": "audio/x-matroska", ".m4a": "audio/mp4",
                ".opus": "audio/ogg", ".ogg": "audio/ogg", ".mp3": "audio/mpeg", ".aif": "audio/aiff",
                ".aiff": "audio/aiff", ".mp4": "video/mp4", ".mov": "video/quicktime", ".mkv": "video/x-matroska",
                ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".heic": "image/heic"}


class RunError(Exception):
    """The run as a whole cannot proceed (bad arguments, a wrong digest)."""


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def media_type_of(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in _MEDIA_TYPES:
        return _MEDIA_TYPES[suffix]
    guessed, _ = mimetypes.guess_type(path.name)
    if guessed is None:
        raise RunError(f"cannot tell the media type of {path}; pass --media-type")
    return guessed


def resolve_command(extractor: bank.Extractor, programs: dict[str, str]) -> list[str]:
    """The extractor's command as an argument list: `${name}` is replaced by
    the configured program of that name, and a word naming a file in the
    extractor's directory becomes that file's absolute path."""
    words = []
    for word in extractor.manifest["command"]:
        match = bank.PROGRAM.fullmatch(word)
        if match:
            if match.group(1) not in programs:
                raise bank.BankError(f"{extractor.id} runs ${{{match.group(1)}}}, which is not configured; "
                                     f"pass --program {match.group(1)}=<path>")
            words.append(programs[match.group(1)])
        elif not os.path.isabs(word) and (extractor.directory / word).is_file():
            words.append(str(extractor.directory / word))
        else:
            words.append(word)
    return words


def _fsync_tree(directory: Path) -> None:
    for path in directory.iterdir():
        with open(path, "rb") as handle:
            os.fsync(handle.fileno())
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class Run:
    """One `run`: the content, the catalog, the bank, and what happened."""

    def __init__(self, catalog: bank.Catalog, root: Path, content: Path, media_type: str, digest: str | None,
                 programs: dict[str, str], parameters: dict[str, dict[str, Any]], timeout: float | None):
        self.catalog, self.root, self.content, self.media_type = catalog, root, content, media_type
        self.digest, self.digest_checked = digest, digest is None
        self.programs, self.parameters, self.timeout = programs, parameters, timeout
        self.lock = threading.Lock()
        self.scratch: Path | None = None

    # -- the content's digest --------------------------------------------

    def content_digest(self) -> str:
        if self.digest is None:
            self.digest = bank.file_digest(self.content)
        return self.digest

    def verify_digest(self) -> None:
        """Hash the content once, before anything is filed under its digest."""
        with self.lock:
            if self.digest_checked:
                return
            actual = bank.file_digest(self.content)
            if actual != self.digest:
                raise RunError(f"{self.content} has digest {actual}, not the {self.digest} given")
            self.digest_checked = True

    # -- planning the graph ------------------------------------------------

    def graph(self, targets: list[str]) -> tuple[list[str], dict[str, str], dict[str, str]]:
        """The extractors to consider, upstream first; their keys; and why
        any is not applicable to this content."""
        order = self.catalog.order(targets)
        keys = self.catalog.keys(self.content_digest(), order, self.parameters)
        unusable: dict[str, str] = {}
        for extractor_id in order:
            extractor = self.catalog[extractor_id]
            if not extractor.accepts(self.media_type):
                unusable[extractor_id] = f"does not accept {self.media_type}"
            else:
                for source in self.catalog.upstream(extractor_id):
                    if source in unusable:
                        unusable[extractor_id] = f"needs {source}, which {unusable[source]}"
                        break
        return order, keys, unusable

    def request(self, extractor_id: str, keys: dict[str, str], output_dir: Path) -> dict[str, Any]:
        """The request an extractor receives: everything it may read, by path
        and digest, and where to write."""
        extractor = self.catalog[extractor_id]
        needs: dict[str, Any] = {}
        for need in extractor.needs:
            source, output = need.split("/", 1)
            entry = bank.read_entry(bank.entry_directory(self.root, self.content_digest(), source, keys[source]))
            if entry is None:
                raise bank.BankError(f"{need} is missing from the bank")
            described = dict(entry.output(output))
            described["entry"] = entry.manifest["key"]
            if "file" in described:
                described["path"] = str(entry.path / described["file"])
            if described.get("grid") is not None:
                described["grid"] = {"name": described["grid"], **entry.manifest["grids"][described["grid"]]}
            needs[need] = described
        return {"contract": bank.REQUEST_CONTRACT,
                "extractor": {"id": extractor.id, "version": extractor.version},
                "key": keys[extractor_id],
                "parameters": self.catalog.parameters(extractor_id, self.parameters),
                "input": {"path": str(self.content.resolve()), "digest": self.content_digest(),
                          "media_type": self.media_type},
                "needs": needs, "output_dir": str(output_dir)}

    # -- running one extractor ---------------------------------------------

    def run_one(self, extractor_id: str, keys: dict[str, str]) -> dict[str, Any]:
        extractor = self.catalog[extractor_id]
        key = keys[extractor_id]
        final = bank.entry_directory(self.root, self.content_digest(), extractor_id, key)
        self.verify_digest()
        assert self.scratch is not None
        work = Path(tempfile.mkdtemp(prefix=f"{extractor_id}-", dir=self.scratch))
        output_dir = work / "out"
        output_dir.mkdir()
        started, clock = now(), time.monotonic()
        log = b""
        try:
            request = self.request(extractor_id, keys, output_dir)
            (work / "request.json").write_bytes(bank.canonical(request))
            command = resolve_command(extractor, self.programs) + [str(work / "request.json")]
            try:
                environment = {**os.environ, "ARCHIVE_EXTRACTOR_REQUEST": str(work / "request.json"),
                               "ARCHIVE_EXTRACTOR_INPUT": request["input"]["path"],
                               "ARCHIVE_EXTRACTOR_OUTPUT": str(output_dir)}
                completed = subprocess.run(command, cwd=work, env=environment, stdin=subprocess.DEVNULL,
                                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=self.timeout)
            except subprocess.TimeoutExpired as exc:
                log = exc.output or b""
                raise bank.BankError(f"{extractor_id} ran past {self.timeout} seconds") from None
            except OSError as exc:
                raise bank.BankError(f"{extractor_id} could not start: {exc}") from exc
            log = completed.stdout
            if completed.returncode != 0:
                raise bank.BankError(f"{extractor_id} exited {completed.returncode}")
            checked = bank.check_result(output_dir, extractor)
            manifest = {"contract": bank.ENTRY_CONTRACT, "key": key,
                        "extractor": {"id": extractor.id, "version": extractor.version},
                        "parameters": request["parameters"], "content": self.content_digest(),
                        "media_type": self.media_type,
                        "needs": {need: keys[need.split("/", 1)[0]] for need in extractor.needs},
                        **checked, "started_at": started, "ended_at": now(), "runner": RUNNER}
            bank.check_entry(manifest, f"{extractor_id} entry")
            (output_dir / bank.RESULT).unlink()
            (output_dir / bank.ENTRY).write_bytes(json.dumps(manifest, indent=1, sort_keys=True).encode())
            # A feature can carry the recording (a stem): owner only, as derived media are.
            for path in output_dir.iterdir():
                path.chmod(0o600)
            output_dir.chmod(0o700)
            _fsync_tree(output_dir)
            final.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.rename(output_dir, final)
            except OSError:
                # Another runner filed the same entry first; its entry stands.
                if bank.read_entry(final) is None:
                    raise
                return {"status": "hit", "key": key, "entry": str(final), "raced": True}
            bank.failure_path(self.root, self.content_digest(), extractor_id, key).unlink(missing_ok=True)
            return {"status": "ran", "key": key, "entry": str(final),
                    "seconds": round(time.monotonic() - clock, 3)}
        except (bank.BankError, OSError) as exc:
            failure = {"extractor": {"id": extractor.id, "version": extractor.version}, "key": key,
                       "content": self.content_digest(), "error": str(exc), "started_at": started,
                       "ended_at": now(), "log": log[-LOG_TAIL:].decode("utf-8", "replace")}
            self.record_failure(extractor_id, key, failure)
            return {"status": "failed", "key": key, "error": str(exc), "log": failure["log"]}
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def record_failure(self, extractor_id: str, key: str, failure: dict[str, Any]) -> None:
        """Operational: the last failure, replaced atomically. Never read as an entry."""
        path = bank.failure_path(self.root, self.content_digest(), extractor_id, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}")
        temporary.write_text(json.dumps(failure, indent=1, sort_keys=True))
        os.replace(temporary, path)

    # -- the whole graph ---------------------------------------------------

    def execute(self, targets: list[str], jobs: int, dry_run: bool = False) -> dict[str, Any]:
        order, keys, unusable = self.graph(targets)
        outcome: dict[str, dict[str, Any]] = {}
        pending: list[str] = []
        for extractor_id in order:
            if extractor_id in unusable:
                outcome[extractor_id] = {"status": "skipped", "key": keys[extractor_id],
                                         "reason": unusable[extractor_id]}
                continue
            entry = bank.read_entry(bank.entry_directory(self.root, self.content_digest(), extractor_id,
                                                         keys[extractor_id]))
            if entry is not None:
                outcome[extractor_id] = {"status": "hit", "key": keys[extractor_id], "entry": str(entry.path)}
            else:
                pending.append(extractor_id)
        if dry_run:
            for extractor_id in pending:
                extractor = self.catalog[extractor_id]
                outcome[extractor_id] = {
                    "status": "would_run", "key": keys[extractor_id],
                    "task": {"capability": f"archive-extractor/{extractor.id}@{extractor.version}",
                             "requires": extractor.manifest.get("requires", []),
                             "after": [keys[s] for s in self.catalog.upstream(extractor_id)],
                             "parameters": self.catalog.parameters(extractor_id, self.parameters),
                             "input": {"digest": self.content_digest(), "media_type": self.media_type}}}
            return self.answer(order, outcome)
        if pending:
            (self.root / "tmp").mkdir(parents=True, exist_ok=True)
            self.scratch = Path(tempfile.mkdtemp(prefix=f"run-{os.getpid()}-", dir=self.root / "tmp"))
        try:
            with ThreadPoolExecutor(max_workers=jobs) as pool:
                running: dict[Future, str] = {}
                while pending or running:
                    for extractor_id in list(pending):
                        sources = self.catalog.upstream(extractor_id)
                        states = [outcome.get(s, {}).get("status") for s in sources]
                        if any(state in ("failed", "blocked") for state in states):
                            failed = [s for s, state in zip(sources, states) if state in ("failed", "blocked")]
                            outcome[extractor_id] = {"status": "blocked", "key": keys[extractor_id],
                                                     "reason": f"needs {', '.join(failed)}, which did not finish"}
                            pending.remove(extractor_id)
                        elif all(state in ("hit", "ran") for state in states) and len(running) < jobs:
                            running[pool.submit(self.run_one, extractor_id, keys)] = extractor_id
                            pending.remove(extractor_id)
                    if not running:
                        if pending:
                            raise RunError(f"nothing can start: {', '.join(pending)} wait on each other")
                        continue
                    done, _ = wait(running, return_when=FIRST_COMPLETED)
                    for future in done:
                        outcome[running.pop(future)] = future.result()
        finally:
            if self.scratch is not None:
                shutil.rmtree(self.scratch, ignore_errors=True)
        return self.answer(order, outcome)

    def answer(self, order: list[str], outcome: dict[str, dict[str, Any]]) -> dict[str, Any]:
        ok = all(outcome[e]["status"] in ("hit", "ran", "skipped", "would_run") for e in order)
        return {"status": "ok" if ok else "failed", "content": self.content_digest(),
                "media_type": self.media_type, "bank": str(self.root),
                "extractors": {e: outcome[e] for e in order}}


# -- the command line ----------------------------------------------------------

def load_config(path: str | None) -> dict[str, Any]:
    """Configuration: `{"programs": {name: path}, "parameters": {extractor:
    {field: value}}, "extractors": [directory, ...]}`, all optional."""
    if path is None:
        return {}
    try:
        config = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RunError(f"configuration {path} is unreadable: {exc}") from exc
    if not isinstance(config, dict) or set(config) - {"programs", "parameters", "extractors"}:
        raise RunError(f"configuration {path} must be an object of programs, parameters, and extractors")
    if not all(isinstance(v, str) for v in config.get("programs", {}).values()):
        raise RunError(f"configuration {path}: each program is a path")
    if not all(isinstance(v, dict) for v in config.get("parameters", {}).values()):
        raise RunError(f"configuration {path}: each extractor's parameters are an object")
    return config


def catalog_of(arguments: argparse.Namespace, config: dict[str, Any]) -> bank.Catalog:
    directories = [Path(d) for d in config.get("extractors", [])] + [Path(d) for d in arguments.extractors or []]
    return bank.Catalog.load(directories or [bank.EXTRACTORS])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="extract.py", description="Fill the feature bank.")
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list", help="list the extractors")
    listing.add_argument("--extractors", action="append")
    listing.add_argument("--config")
    running = commands.add_parser("run", help="run an extractor and what it needs")
    running.add_argument("extractor")
    running.add_argument("--content", required=True)
    running.add_argument("--digest")
    running.add_argument("--media-type")
    running.add_argument("--bank", required=True)
    running.add_argument("--jobs", type=int, default=1)
    running.add_argument("--config")
    running.add_argument("--program", action="append", default=[])
    running.add_argument("--extractors", action="append")
    running.add_argument("--timeout", type=float)
    running.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        config = load_config(arguments.config)
        catalog = catalog_of(arguments, config)
        if arguments.command == "list":
            print(json.dumps({"extractors": [
                {"id": e.id, "version": e.version, "accepts": e.manifest["accepts"], "needs": e.needs,
                 "requires": e.manifest.get("requires", []), "outputs": sorted(e.outputs),
                 "directory": str(e.directory)} for e in sorted(catalog.extractors.values(), key=lambda e: e.id)]},
                indent=1))
            return 0
        if arguments.jobs < 1:
            raise RunError("--jobs must be at least 1")
        content = Path(arguments.content)
        if not content.is_file():
            raise RunError(f"no content file at {content}")
        if arguments.digest is not None and not bank.derived.is_digest(arguments.digest):
            raise RunError(f"--digest must be sha256:<64 hex>, not {arguments.digest!r}")
        programs = {"python": sys.executable, "sh": "/bin/sh", **config.get("programs", {})}
        for item in arguments.program:
            name, separator, value = item.partition("=")
            if not separator or not bank.ID.fullmatch(name) or not value:
                raise RunError(f"--program takes <name>=<path>, not {item!r}")
            programs[name] = value
        targets = sorted(catalog.extractors) if arguments.extractor == "all" else [arguments.extractor]
        for target in targets:
            catalog[target]
        run = Run(catalog, Path(arguments.bank).resolve(), content, arguments.media_type or media_type_of(content),
                  arguments.digest, programs, config.get("parameters", {}), arguments.timeout)
        answer = run.execute(targets, arguments.jobs, arguments.dry_run)
    except (RunError, bank.BankError) as exc:
        print(json.dumps({"status": "failed", "detail": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(answer, indent=1))
    return 0 if answer["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
