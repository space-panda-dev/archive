#!/usr/bin/env python3
"""Run an extractor on another machine through Spool, and file what it makes
in the feature bank (ADRs 0073 to 0075, and 0090).

    remote.py put <extractor> --content <file> --bank <dir> --spool <command>
                  [--digest sha256:<hex>] [--media-type <type>] [--config <file>] [--extractors <dir>]...
    remote.py work [--config <file>] [--extractors <dir>]...
    remote.py collect --bank <dir> --spool <command> [--config <file>] [--extractors <dir>]...

`put`, on the machine that holds the bank, computes the entry's key as the
runner would, and puts one Spool task with the content attached, unless the
entry is already there. `--spool` is the command that reaches the spool,
split on whitespace, to which a word such as `put` is appended: `spool --dir
/srv/spool`, or the same behind `sudo -u <spool account>`.

`work` is the Spool capability program on the machine that runs the
extractor. Spool starts it with the task's payload on standard input in a
fresh directory holding the content at `attachments/<hex>`. It checks that
its own extractor gives the key the task names, runs it with the real runner
into a bank of its own in that directory, and leaves every output's file in
`outputs/`, named by its digest, for Spool to send back as result
attachments. It prints the entry's manifest as the task's result.

`collect`, on the machine that holds the bank, reads the spool's results,
takes each result attachment, and files the entry: only when the key is the
one its own extractors give that extractor, parameters, and content, and
when the files are the bits the manifest names, as the runner checks a
result it made itself. It then collects the task. A result that fails a
check is not filed and not collected; its failure is recorded beside where
the entry would be.

Every command prints one JSON object and exits 0 when everything was put,
filed, or already there, 1 when anything failed, and 2 when the arguments
are not understood. `work` exits 65 for a task it can never run (the
payload is malformed, or its extractor is another version), which Spool
fails for good, and 1 for a run that failed, which Spool retries.

Only an extractor that needs no other feature travels: a need is a file of
the bank, which the task does not carry. Like the runner, this uses only
Python's standard library. Sending content to a worker is sending the
recording to that machine: point `--spool` only at a spool whose workers are
the operator's own (ADR 0016 governs any other).
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bank  # noqa: E402
import extract  # noqa: E402

TASK_CONTRACT = "archive-extractor-task/1"
TASK_RESULT_CONTRACT = "archive-extractor-task-result/1"
# A task's ID is this and the key's hexadecimal digits: one task per entry,
# so putting it again while the spool holds it is `existing`, not a second run.
TASK_PREFIX = "bank-"
CAPABILITY_PREFIX = "archive-extractor."
EX_DATAERR = 65


class RemoteError(Exception):
    """A task or result that cannot be used, or a spool that cannot be reached."""


class Refused(RemoteError):
    """A task this worker can never run: Spool must not try again."""


# -- the task and its result ---------------------------------------------------

def task_id(key: str) -> str:
    return TASK_PREFIX + key.removeprefix("sha256:")


def hex_of(digest: str) -> str:
    return digest.removeprefix("sha256:")


def check_payload(payload: Any) -> dict[str, Any]:
    """A task's payload, as `put` writes it: `transport`, when present, names
    the lossless encoding of the content that travels in its place."""
    fields = {"contract", "extractor", "key", "parameters", "input"}
    if not isinstance(payload, dict) or payload.get("contract") != TASK_CONTRACT or set(payload) - {
            "transport"} != fields:
        raise Refused(f"the payload must be {TASK_CONTRACT} with exactly {sorted(fields)} and perhaps transport")
    if "transport" in payload:
        carried = payload["transport"]
        if not (isinstance(carried, dict) and set(carried) == {"codec", "digest", "media_type"}
                and carried["codec"] in TRANSPORTS and bank.derived.is_digest(carried["digest"])
                and carried["media_type"] == TRANSPORTS[carried["codec"]]["media_type"]):
            raise Refused("the payload's transport must be {codec, digest, media_type} of a known codec")
    extractor, given = payload["extractor"], payload["input"]
    if not (isinstance(extractor, dict) and set(extractor) == {"id", "version"}
            and isinstance(extractor["id"], str) and bank.ID.fullmatch(extractor["id"])
            and isinstance(extractor["version"], str) and bank.VERSION.fullmatch(extractor["version"])):
        raise Refused("the payload's extractor must be {id, version}")
    if not isinstance(payload["key"], str) or not bank.KEY.fullmatch(payload["key"]):
        raise Refused("the payload's key must be sha256:<hex>")
    if not (isinstance(given, dict) and set(given) == {"digest", "media_type"}
            and bank.derived.is_digest(given["digest"]) and isinstance(given["media_type"], str)
            and bank.MEDIA_TYPE.fullmatch(given["media_type"]) and "*" not in given["media_type"]):
        raise Refused("the payload's input must be {digest, media_type}")
    if not isinstance(payload["parameters"], dict):
        raise Refused("the payload's parameters must be an object")
    try:
        bank.check_parameters(payload["parameters"], "the payload's parameters")
    except bank.BankError as exc:
        raise Refused(str(exc)) from exc
    return payload


def check_task_result(result: Any) -> dict[str, Any]:
    """A task's result, as `work` prints it: the entry the worker's runner filed."""
    if not isinstance(result, dict) or result.get("contract") != TASK_RESULT_CONTRACT or set(result) != {
            "contract", "entry"}:
        raise RemoteError(f"the result must be {TASK_RESULT_CONTRACT} with exactly its entry")
    try:
        return bank.check_entry(result["entry"], "the result's entry")
    except bank.BankError as exc:
        raise RemoteError(str(exc)) from exc


def traveling(catalog: bank.Catalog, extractor_id: str) -> bank.Extractor:
    """An extractor that can run elsewhere: one that needs no feature."""
    extractor = catalog[extractor_id]
    if extractor.needs:
        raise RemoteError(f"{extractor_id} needs {', '.join(extractor.needs)}; only an extractor that needs "
                          "no other feature runs through Spool")
    return extractor


def parameters_given(catalog: bank.Catalog, extractor_id: str, parameters: dict[str, Any]) -> dict[str, Any]:
    """The overrides that make exactly these parameters, refused when the
    extractor here does not declare every field of them."""
    overrides = {extractor_id: parameters}
    catalog.check_overrides(overrides, "the task")
    if catalog.parameters(extractor_id, overrides) != parameters:
        raise bank.BankError(f"the task's {extractor_id} parameters lack fields this extractor declares")
    return overrides


# -- reaching the spool ----------------------------------------------------------

class Spool:
    """The command that reaches a spool, as a list of words."""

    def __init__(self, command: str):
        self.words = shlex.split(command)
        if not self.words:
            raise RemoteError("--spool names no command")

    def call(self, *arguments: str, given: bytes = b"", ok: tuple[int, ...] = (0,)) -> bytes:
        try:
            completed = subprocess.run([*self.words, *arguments], input=given, capture_output=True)
        except OSError as exc:
            raise RemoteError(f"the spool cannot be reached: {exc}") from exc
        if completed.returncode not in ok:
            raise RemoteError(f"spool {' '.join(arguments)} exited {completed.returncode}: "
                              f"{completed.stderr.decode('utf-8', 'replace').strip()[-500:]}")
        return completed.stdout

    def lines(self, *arguments: str, given: bytes = b"") -> list[dict[str, Any]]:
        out = self.call(*arguments, given=given)
        try:
            return [json.loads(line) for line in out.decode("utf-8").splitlines() if line.strip()]
        except ValueError as exc:
            raise RemoteError(f"spool {' '.join(arguments)} answered what is not JSON lines: {exc}") from exc

    def take(self, task: str, digest: str, path: Path) -> None:
        """One result attachment into `path`, which must not exist."""
        request = json.dumps({"task_id": task, "sha256": hex_of(digest)}).encode()
        with open(path, "xb") as handle, tempfile.TemporaryFile() as errors:
            try:
                completed = subprocess.run([*self.words, "take"], input=request, stdout=handle, stderr=errors)
            except OSError as exc:
                raise RemoteError(f"the spool cannot be reached: {exc}") from exc
            if completed.returncode != 0:
                errors.seek(0)
                raise RemoteError(f"spool take {hex_of(digest)[:16]} exited {completed.returncode}: "
                                  f"{errors.read().decode('utf-8', 'replace').strip()[-500:]}")


# -- the transport -----------------------------------------------------------------
#
# What travels may be a lossless re-encoding of the content's first audio
# stream rather than its bits: a rehearsal's 32-bit float WAV is 0.38 MB per
# audio-second, its WavPack 0.28, and the link to a worker is the slowest
# step (round 1 of ADR 0112's backfill). FLAC holds integers only, so a float
# recording needs WavPack; FLAC serves a 16- or 24-bit one. Either is used
# only when it decodes, here, to the very samples the content decodes to
# (`same_samples`); otherwise the content itself travels. A transport serves
# only an extractor that reads nothing of its input but the first audio
# stream's samples, which is why the operator names it per extractor.

TRANSPORTS = {
    "wavpack": {"media_type": "audio/x-wavpack", "arguments": ["-c:a", "wavpack", "-f", "wv"]},
    "flac": {"media_type": "audio/flac", "arguments": ["-c:a", "flac", "-compression_level", "5", "-f", "flac"]},
}


def ffmpeg_program(ffmpeg: str | None) -> str:
    found = ffmpeg or shutil.which("ffmpeg")
    if found is None:
        raise RemoteError("a transport is encoded with ffmpeg; configure programs.ffmpeg")
    return found


def encode_transport(ffmpeg: str, content: Path, transport: str, target: Path) -> None:
    """The content's first audio stream, losslessly, without metadata."""
    arguments = [ffmpeg, "-nostdin", "-hide_banner", "-nostats", "-loglevel", "error", "-i", str(content),
                 "-map", "0:a:0", "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:a", "+bitexact",
                 *TRANSPORTS[transport]["arguments"], str(target)]
    completed = subprocess.run(arguments, capture_output=True)
    if completed.returncode != 0:
        raise RemoteError(f"ffmpeg could not encode the {transport} transport: "
                          f"{completed.stderr.decode('utf-8', 'replace').strip()[-500:]}")


def decoded_digest(ffmpeg: str, path: Path) -> str:
    """The digest of the first audio stream's samples as 64-bit floats, which
    hold every 32-bit integer and float sample exactly, in a WAV whose header
    carries the rate and the channel layout."""
    arguments = [ffmpeg, "-nostdin", "-hide_banner", "-nostats", "-loglevel", "error", "-i", str(path),
                 "-map", "0:a:0", "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:a", "+bitexact",
                 "-c:a", "pcm_f64le", "-f", "wav", "-"]
    digest = hashlib.sha256()
    with subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        assert process.stdout is not None and process.stderr is not None
        for block in iter(lambda: process.stdout.read(1 << 20), b""):
            digest.update(block)
        errors = process.stderr.read()
    if process.returncode != 0:
        raise RemoteError(f"ffmpeg could not decode {path.name}: {errors.decode('utf-8', 'replace').strip()[-500:]}")
    return "sha256:" + digest.hexdigest()


def same_samples(ffmpeg: str, content: Path, transport: Path) -> bool:
    return decoded_digest(ffmpeg, content) == decoded_digest(ffmpeg, transport)


def make_transport(ffmpeg: str | None, content: Path, media_type: str, transport: str,
                   directory: Path) -> tuple[Path, str] | str:
    """The transport file in `directory` and its digest, or why the content
    must travel as it is."""
    if transport not in TRANSPORTS:
        raise RemoteError(f"the transport is one of {', '.join(TRANSPORTS)}, not {transport!r}")
    if not media_type.startswith("audio/"):
        return f"{media_type} is not audio"
    program = ffmpeg_program(ffmpeg)
    target = directory / f"transport.{transport}"
    encode_transport(program, content, transport, target)
    if not same_samples(program, content, target):
        target.unlink()
        return f"{transport} does not decode to the content's samples"
    if target.stat().st_size >= content.stat().st_size:
        target.unlink()
        return f"{transport} is no smaller than the content"
    return target, bank.file_digest(target)


# -- put -------------------------------------------------------------------------

def put(catalog: bank.Catalog, root: Path, spool: Spool, extractor_id: str, content: Path, media_type: str,
        digest: str | None, parameters: dict[str, Any], exact: bool = False, transport: str | None = None,
        ffmpeg: str | None = None) -> dict[str, Any]:
    """Put one task for the entry of this extractor and content, unless the
    bank holds it already, or (unless exact) an entry that stands in for it
    (ADR 0090's amendment): a recording separated on one device is never
    separated again on another. With `transport`, the content's samples
    travel in that lossless encoding when it decodes to the same samples
    (`make_transport`); the answer says which travelled."""
    extractor = traveling(catalog, extractor_id)
    if not extractor.accepts(media_type):
        raise RemoteError(f"{extractor_id} does not accept {media_type}")
    actual = bank.file_digest(content)
    if digest is not None and digest != actual:
        raise RemoteError(f"{content} has digest {actual}, not the {digest} given")
    key = catalog.keys(actual, [extractor_id], parameters)[extractor_id]
    answer = {"extractor": extractor_id, "key": key, "content": actual, "task_id": task_id(key)}
    found = bank.Bank(root, catalog, parameters, exact=exact).entry(actual, extractor_id)
    if found is not None:
        if found.manifest["key"] != key:
            return {"status": "hit", **answer, "key": found.manifest["key"], "stands_in_for": key}
        return {"status": "hit", **answer}
    payload = {"contract": TASK_CONTRACT, "extractor": {"id": extractor.id, "version": extractor.version},
               "key": key, "parameters": catalog.parameters(extractor_id, parameters),
               "input": {"digest": actual, "media_type": media_type}}
    (root / "tmp").mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="put-", dir=root / "tmp"))
    try:
        travels, sent = content, actual
        if transport is not None:
            made = make_transport(ffmpeg, content, media_type, transport, staging)
            if isinstance(made, str):
                answer["transport"] = {"codec": None, "why": made}
            else:
                travels, sent = made
                payload["transport"] = {"codec": transport, "digest": sent,
                                        "media_type": TRANSPORTS[transport]["media_type"]}
                answer["transport"] = {"codec": transport, "bytes": travels.stat().st_size,
                                       "content_bytes": content.stat().st_size}
        check_payload(payload)
        task = {"task_id": task_id(key), "capability": extract.spool_capability(extractor), "payload": payload,
                "attachments": [{"sha256": hex_of(sent), "size": travels.stat().st_size}]}
        # Spool reads the attachment from <dir>/<hex> and copies it; a link
        # costs nothing where the bank and the content share a file system.
        source = staging / hex_of(sent)
        try:
            os.link(travels, source)
        except OSError:
            shutil.copyfile(travels, source)
        if travels != content:
            travels.unlink()
        staging.chmod(0o755)  # a spool under another account must read it
        lines = spool.lines("put", "--attachments", str(staging), given=bank.canonical(task) + b"\n")
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    statuses = [line.get("status") for line in lines if line.get("task_id") == task["task_id"]]
    if statuses not in (["inserted"], ["existing"]):
        raise RemoteError(f"spool put answered {lines!r}")
    return {"status": "put" if statuses == ["inserted"] else "existing", **answer}


# -- work ------------------------------------------------------------------------

def work(catalog: bank.Catalog, directory: Path, programs: dict[str, str], payload: Any) -> dict[str, Any]:
    """Run one task in Spool's working directory and leave its files in
    `outputs/`; returns the task's result."""
    payload = check_payload(payload)
    extractor_id = payload["extractor"]["id"]
    try:
        extractor = traveling(catalog, extractor_id)
        overrides = parameters_given(catalog, extractor_id, payload["parameters"])
    except (bank.BankError, RemoteError) as exc:
        raise Refused(str(exc)) from exc
    if extractor.version != payload["extractor"]["version"]:
        raise Refused(f"this worker's {extractor_id} is version {extractor.version}, "
                      f"not {payload['extractor']['version']}")
    digest, media_type = payload["input"]["digest"], payload["input"]["media_type"]
    if not extractor.accepts(media_type):
        raise Refused(f"{extractor_id} does not accept {media_type}")
    key = catalog.keys(digest, [extractor_id], overrides)[extractor_id]
    if key != payload["key"]:
        raise Refused(f"this worker gives the key {key}, not the task's {payload['key']}")
    # The content's own bits, or its samples in the task's transport, which
    # the requester checked decode to the same samples (`make_transport`).
    carried = payload.get("transport", {}).get("digest", digest)
    content = directory / "attachments" / hex_of(carried)
    if not content.is_file():
        raise Refused(f"the task carries no attachment {hex_of(carried)}")
    # Hashed here, not trusted to Spool: an extractor run on other bits
    # would be filed under this digest. The bits will not change on a retry.
    if bank.file_digest(content) != carried:
        raise Refused(f"the attachment {hex_of(carried)} is not the bits its digest names")
    root = directory / "bank"
    run = extract.Run(catalog, root, content, media_type, digest, programs, overrides, None, exact=True)
    # Checked just above: the content's own digest, or the transport's.
    run.digest_checked = True
    answer = run.execute([extractor_id], 1)
    outcome = answer["extractors"][extractor_id]
    if outcome["status"] != "ran":
        raise RemoteError(f"{extractor_id} {outcome['status']}: {outcome.get('error') or outcome.get('reason')}"
                          + (f"\n{outcome['log']}" if outcome.get("log") else ""))
    entry = bank.read_entry(bank.entry_directory(root, digest, extractor_id, key))
    if entry is None:
        raise RemoteError(f"{extractor_id} ran but its entry is missing")
    outputs = directory / "outputs"
    outputs.mkdir(exist_ok=True)
    for described in entry.manifest["outputs"].values():
        if "file" in described:
            # Two outputs with the same bits are one file, sent once.
            os.replace(entry.path / described["file"], outputs / hex_of(described["sha256"]))
    return {"contract": TASK_RESULT_CONTRACT, "entry": entry.manifest}


def stop_on_term() -> None:
    """Spool stops a run by signalling its process group, which the
    extractor's own group is not in: pass the signal on, and kill what is
    left before Spool's own SIGKILL ends this program."""
    def stop(signal_number: int, frame: Any) -> None:
        extract.signal_running(signal.SIGTERM)
        killer = threading.Timer(extract.KILL_GRACE - 2, extract.signal_running, (signal.SIGKILL,))
        killer.daemon = True
        killer.start()
        raise SystemExit(128 + signal_number)
    signal.signal(signal.SIGTERM, stop)


# -- collect ---------------------------------------------------------------------

def file_result(catalog: bank.Catalog, root: Path, spool: Spool, record: dict[str, Any]) -> dict[str, Any]:
    """File one done task's entry, checked as the runner checks its own."""
    entry = check_task_result(record.get("result"))
    extractor_id = entry["extractor"]["id"]
    try:
        extractor = traveling(catalog, extractor_id)
        parameters_given(catalog, extractor_id, entry["parameters"])
    except bank.BankError as exc:
        raise RemoteError(str(exc)) from exc
    if extractor.version != entry["extractor"]["version"]:
        raise RemoteError(f"the entry is {extractor_id} {entry['extractor']['version']}; "
                          f"this bank's is {extractor.version}")
    if record.get("capability") != extract.spool_capability(extractor) or record.get("task_id") != task_id(
            entry["key"]):
        raise RemoteError("the task's capability or ID is not the entry's")
    if entry["needs"]:
        raise RemoteError("the entry names needs; no task carries them")
    # check_entry has already recomputed the key from what the entry says.
    content, key = entry["content"], entry["key"]
    final = bank.entry_directory(root, content, extractor_id, key)
    final.parent.mkdir(parents=True, exist_ok=True)
    with open(bank.lock_path(root, content, extractor_id, key), "a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if bank.read_entry(final) is not None:
            return {"status": "hit", "key": key, "entry": str(final)}
        (root / "tmp").mkdir(parents=True, exist_ok=True)
        work_dir = Path(tempfile.mkdtemp(prefix=f"collect-{extractor_id}-", dir=root / "tmp"))
        try:
            output_dir = work_dir / "out"
            output_dir.mkdir()
            carried = {item.get("sha256") for item in record.get("attachments") or [] if isinstance(item, dict)}
            if set(entry["outputs"]) != set(extractor.outputs):
                raise RemoteError(f"the entry's outputs are not {extractor_id}'s")
            reported: dict[str, Any] = {}
            # File names come from this bank's manifest, never from the result.
            for name, declared in extractor.outputs.items():
                described = entry["outputs"][name]
                if declared["kind"] == "series":
                    reported[name] = {"shape": described.get("shape")}
                elif declared["kind"] == "scalar":
                    reported[name] = {"value": described.get("value")}
                else:
                    reported[name] = {}
                file_name = bank.output_file(name, declared)
                if file_name is None:
                    continue
                digest = described.get("sha256")
                if not (isinstance(digest, str) and bank.derived.is_digest(digest)) or hex_of(digest) not in carried:
                    raise RemoteError(f"the result does not carry {name}'s file")
                spool.take(record["task_id"], digest, output_dir / file_name)
            (output_dir / bank.RESULT).write_text(json.dumps(
                {"contract": bank.RESULT_CONTRACT, "software": entry["software"], "grids": entry["grids"],
                 "outputs": reported}))
            # The runner's own check of a result: every declared file, its
            # shape, its finite values, and its digest computed here.
            checked = bank.check_result(output_dir, extractor)
            made = {"software": entry["software"], "grids": entry["grids"], "outputs": entry["outputs"]}
            if checked != made:
                raise RemoteError("the files taken are not what the entry names")
            if not extract.place_entry(output_dir, entry, final):
                return {"status": "hit", "key": key, "entry": str(final), "raced": True}
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
    bank.failure_path(root, content, extractor_id, key).unlink(missing_ok=True)
    return {"status": "filed", "key": key, "entry": str(final)}


def record_refusal(root: Path, record: dict[str, Any], error: str) -> str | None:
    """Beside where the entry would be, when the result names one."""
    try:
        entry = record["result"]["entry"]
        extractor_id, key, content = entry["extractor"]["id"], entry["key"], entry["content"]
        path = bank.failure_path(root, content, extractor_id, key)
    except (KeyError, TypeError, bank.BankError):
        return None
    failure = {"extractor": entry.get("extractor"), "key": key, "content": content,
               "error": f"collected from Spool task {record.get('task_id')}: {error}",
               "started_at": extract.now(), "ended_at": extract.now(), "log": ""}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}")
    try:
        temporary.write_text(json.dumps(failure, indent=1, sort_keys=True))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return str(path)


def collect(catalog: bank.Catalog, root: Path, spool: Spool) -> dict[str, Any]:
    """File every done extractor task's entry and collect the task."""
    tasks: dict[str, Any] = {}
    for record in spool.lines("results"):
        name = record.get("task_id")
        if not (isinstance(name, str) and name.startswith(TASK_PREFIX)
                and str(record.get("capability", "")).startswith(CAPABILITY_PREFIX)):
            continue
        try:
            tasks[name] = file_result(catalog, root, spool, record)
        except (RemoteError, bank.BankError, OSError) as exc:
            tasks[name] = {"status": "refused", "error": str(exc)}
            recorded = record_refusal(root, record, str(exc))
            if recorded:
                tasks[name]["failure"] = recorded
            continue
        collected = spool.lines("collect", given=json.dumps({"task_id": name}).encode() + b"\n")
        tasks[name]["collected"] = [line.get("status") for line in collected] == ["collected"]
    failed = {}
    for record in spool.lines("failures"):
        name = record.get("task_id")
        if not (isinstance(name, str) and name.startswith(TASK_PREFIX)):
            continue
        retried = record.get("retried")
        if not isinstance(retried, bool):
            # Not read as retried (it would vanish) nor as final: said to be malformed.
            failed[name] = f"the spool's failure record is malformed: retried is {retried!r}"
        elif not retried:
            failed[name] = record.get("reason")
    ok = all(task["status"] in ("filed", "hit") and task.get("collected") for task in tasks.values())
    return {"status": "ok" if ok and not failed else "failed", "bank": str(root), "tasks": tasks,
            "failed": failed}


# -- the command line -------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="remote.py", description="Run extractors through Spool.")
    commands = parser.add_subparsers(dest="command", required=True)
    putting = commands.add_parser("put")
    putting.add_argument("extractor")
    putting.add_argument("--content", required=True)
    putting.add_argument("--digest")
    putting.add_argument("--media-type")
    putting.add_argument("--bank", required=True)
    putting.add_argument("--spool", required=True)
    putting.add_argument("--exact", action="store_true",
                         help="put the task unless the configured key's own entry is in the bank")
    putting.add_argument("--transport", choices=sorted(TRANSPORTS),
                         help="send the content's samples in this lossless encoding when it decodes to them")
    working = commands.add_parser("work")
    collecting = commands.add_parser("collect")
    collecting.add_argument("--bank", required=True)
    collecting.add_argument("--spool", required=True)
    for command in (putting, working, collecting):
        command.add_argument("--config")
        command.add_argument("--extractors", action="append")
    arguments = parser.parse_args(argv)
    if arguments.command == "work":
        return main_work(arguments)
    try:
        config = extract.load_config(arguments.config)
        catalog = extract.catalog_of(arguments, config)
        spool = Spool(arguments.spool)
        root = Path(arguments.bank).resolve()
        if arguments.command == "put":
            content = Path(arguments.content)
            if not content.is_file():
                raise RemoteError(f"no content file at {content}")
            if arguments.digest is not None and not bank.derived.is_digest(arguments.digest):
                raise RemoteError(f"--digest must be sha256:<64 hex>, not {arguments.digest!r}")
            parameters = config.get("parameters", {})
            catalog.check_overrides(parameters, f"configuration {arguments.config}")
            catalog[arguments.extractor]
            answer = put(catalog, root, spool, arguments.extractor, content,
                         arguments.media_type or extract.media_type_of(content), arguments.digest, parameters,
                         arguments.exact, arguments.transport, config.get("programs", {}).get("ffmpeg"))
            ok = True
        else:
            answer = collect(catalog, root, spool)
            ok = answer["status"] == "ok"
    except (RemoteError, extract.RunError, bank.BankError, OSError) as exc:
        print(json.dumps({"status": "failed", "detail": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(answer, indent=1))
    return 0 if ok else 1


def main_work(arguments: argparse.Namespace) -> int:
    """The capability program: the payload on stdin, the result on stdout."""
    stop_on_term()
    try:
        config = extract.load_config(arguments.config)
        if config.get("parameters"):
            raise Refused("a worker's configuration sets no parameters: the task names them")
        catalog = extract.catalog_of(arguments, config)
        programs = {"python": sys.executable, "sh": "/bin/sh", **config.get("programs", {})}
        try:
            payload = json.load(sys.stdin)
        except ValueError as exc:
            raise Refused(f"the payload is not JSON: {exc}") from exc
        started = time.monotonic()
        result = work(catalog, Path.cwd(), programs, payload)
    except Refused as exc:
        print(f"remote.py work: {exc}", file=sys.stderr)
        return EX_DATAERR
    except (RemoteError, extract.RunError, bank.BankError, OSError) as exc:
        print(f"remote.py work: {exc}", file=sys.stderr)
        return 1
    print(f"remote.py work: {result['entry']['extractor']['id']} ran in {time.monotonic() - started:.1f} s",
          file=sys.stderr)
    sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
