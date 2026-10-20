#!/usr/bin/env python3
"""Fill the feature bank: run extractors on content (ADRs 0073, 0074).

    extract.py list [--extractors <dir>]...
    extract.py run <extractor|all> --content <file> [--digest sha256:<hex>] [--media-type <type>]
                   --bank <dir> [--from <extractor>/<output>] [--jobs <n>] [--config <file>]
                   [--program <name>=<path>]... [--extractors <dir>]... [--timeout <seconds>] [--dry-run]
                   [--no-prefer] [--region <start>:<end>:<rate>]

`run` resolves what the named extractor needs as a graph over the bank, runs
only the extractors whose entries are missing, each once, upstream first and
up to `--jobs` at a time, and prints one JSON object saying what each did:
`hit` (its entry was already there; nothing ran), `ran`, `failed`, `blocked`
(something it needs failed), or `skipped` (it does not accept the content's
media type). It exits 0 when every extractor hit or ran, 1 when any failed or
was blocked, and 2 when the arguments are not understood.

An extractor is a program in any language, named by its manifest and invoked
with one argument, the path of a request JSON; it writes its outputs and a
`result.json` into the directory the request names. It runs in a process
group of its own, which is stopped whole when it exits or times out. The runner checks them
against the manifest, writes `entry.json`, and renames the directory into the
bank, so an entry is complete or absent. A lock beside the entry makes two
runners on one bank make it once. A failure is recorded beside where
the entry would be and leaves the bank as it was.

With `--digest`, the content is not hashed while every entry is present; the
first extractor to run hashes it and refuses the run if the digest is wrong,
so nothing is ever filed under another file's digest. `--dry-run` prints the
requests that would run, as a worker would receive them, and runs nothing.

With `--from stems/vocals`, the extractors measure that file feature of the
content instead of the content itself: the runner makes the feature first if
it is missing, then runs the named extractors with the stem as their input,
its own digest as their content, and the content's directory as where their
entries are filed (ADR 0075). An extractor never knows it measures a stem.
With `all`, an extractor that makes a file it would accept itself (a
separator) is left out, so a stem is not separated again; and one whose command names a program that is not
configured (`${demucs-python}` on a machine without Demucs) is skipped, and
said to be; named on its own, it fails.

Without `--from`, an extractor whose manifest `prefers` a file feature
(`melody` prefers `stems/vocals`, then `stems-6s/vocals`) measures the
first of those the bank already holds for the content, as `--from` would,
after everything else has run; where the bank holds none, it measures the content. A preference may
set minimums for scalars of its measure (`melody` asks a `voiced_fraction`
of at least 0.2 on the vocal stem); a measure below one is kept but passed
over, and the next default input is measured too. A preference never
makes the feature, and an extractor run only because another needs it always
measures the content. `--no-prefer` measures the content regardless.
`Bank.default_entry` reads back what this made.

With `--region <start>:<end>:<rate>` (samples at the content's stream rate),
the run is limited to that region of the content (ADR 0112): the runner cuts
it once to a FLAC file with pinned arguments, checks the cut holds exactly
that many samples at that rate, and runs every extractor over the cut as over
any content. Entries are region entries (`archive-feature-bank-entry/2`,
keyed by `archive-feature-bank-key/2`), filed under the content's directory
with the region and the cut's digest; an extractor's needs are made over the
same region. Their times are on the cut's timeline; a reader adds `start`.
A stem made over a region is content of its own, measured whole.

The runner uses only Python's standard library. It never reads or writes an
archive and proposes nothing: features are not claims.
"""

from __future__ import annotations

import argparse
import datetime
import fcntl
import json
import mimetypes
import os
import shutil
import signal
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
CUT_MEDIA_TYPE = "audio/flac"
LOG_TAIL = 4000
# After SIGTERM, how long an extractor's process group has before SIGKILL (as Spool's workers wait).
KILL_GRACE = 5.0

# Media types for common recorder and camera files that `mimetypes` may not know.
_MEDIA_TYPES = {".flac": "audio/flac", ".wav": "audio/wav", ".mka": "audio/x-matroska", ".m4a": "audio/mp4",
                ".opus": "audio/ogg", ".ogg": "audio/ogg", ".mp3": "audio/mpeg", ".aif": "audio/aiff",
                ".aiff": "audio/aiff", ".mp4": "video/mp4", ".mov": "video/quicktime", ".mkv": "video/x-matroska",
                ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".heic": "image/heic"}


class RunError(Exception):
    """The run as a whole cannot proceed (bad arguments, a wrong digest)."""


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def sniff_media_type(path: Path) -> str | None:
    """The media type the file's first bytes declare, for content with no
    telling suffix (an archive's content files have none): the containers
    the extractors accept and the images a card holds, or None when they
    name none. A file that cannot be read raises OSError: unreadable is not
    "names none"."""
    with path.open("rb") as handle:
        head = handle.read(16)
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "audio/wav"
    if head[:4] == b"fLaC":
        return "audio/flac"
    if head[:4] == b"OggS":
        return "audio/ogg"
    if head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
        return "audio/aiff"
    if head[:3] == b"ID3" or (len(head) >= 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0 and head[1] & 0x06):
        return "audio/mpeg"
    if head[:4] == b"\x1aE\xdf\xa3":
        return "video/x-matroska"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand == b"qt  ":
            return "video/quicktime"
        if brand in (b"heic", b"heix", b"mif1", b"msf1"):
            return "image/heic"
        return "audio/mp4" if brand in (b"M4A ", b"M4B ") else "video/mp4"
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return None


def media_type_of(path: Path) -> str:
    """The media type of a content file: by its suffix, or, when the suffix
    tells nothing (an archive's `.blob` content), by its first bytes."""
    suffix = path.suffix.lower()
    if suffix in _MEDIA_TYPES:
        return _MEDIA_TYPES[suffix]
    guessed, _ = mimetypes.guess_type(path.name)
    if guessed is None:
        try:
            guessed = sniff_media_type(path)
        except OSError as error:
            raise RunError(f"cannot read the first bytes of {path} ({error.strerror or error}); "
                           f"pass --media-type") from None
    if guessed is None:
        raise RunError(f"cannot tell the media type of {path} from its name or its first bytes; pass --media-type")
    return guessed


def spool_capability(extractor: bank.Extractor) -> str:
    """The Spool capability a worker would offer to run this extractor:
    `archive-extractor.<id>@<version>`, which the manifest grammars keep
    within Spool's (spool/spec/protocol.md: `name@version`, name
    `[A-Za-z0-9._-]+`, version `[A-Za-z0-9.]+`, 128 characters at most)."""
    capability = f"archive-extractor.{extractor.id}@{extractor.version}"
    if not bank.SPOOL_CAPABILITY.fullmatch(capability) or len(capability) > 128:
        raise bank.BankError(f"{extractor.id} {extractor.version} makes no Spool capability: {capability!r}")
    return capability


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


# The process groups of the extractors running now, so that a runner that is
# itself stopped (a Spool worker's run past its time) can stop them: each
# runs in a group of its own, which a signal to the runner's group misses.
_GROUPS: set[int] = set()
_GROUPS_LOCK = threading.Lock()


def signal_running(signal_number: int) -> None:
    """Send a signal to every extractor's process group running now."""
    with _GROUPS_LOCK:
        groups = list(_GROUPS)
    for group in groups:
        _signal_group(group, signal_number)


def _signal_group(group: int, signal_number: int) -> None:
    try:
        os.killpg(group, signal_number)
    except (ProcessLookupError, PermissionError):
        pass  # the group is gone: nothing is left to stop


def run_program(command: list[str], cwd: Path, environment: dict[str, str],
                timeout: float | None) -> tuple[int | None, bytes]:
    """Run an extractor in its own process group and return its exit status
    (None when it ran past `timeout`) and the end of what it printed.

    The whole group is stopped, not only the process started: an extractor
    that is a shell script around ffmpeg or a stem separator has children of
    its own. On a timeout the group is sent SIGTERM, then SIGKILL after
    KILL_GRACE seconds. Whatever is left of the group when the extractor
    exits is sent SIGKILL, so nothing it started writes into its output
    directory while the runner checks it, or keeps a machine busy after the
    runner has finished with it."""
    process = subprocess.Popen(command, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    with _GROUPS_LOCK:
        _GROUPS.add(process.pid)
    tail = bytearray()

    def read() -> None:
        assert process.stdout is not None
        while chunk := process.stdout.read1(1 << 16):
            tail.extend(chunk)
            del tail[:-LOG_TAIL]

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _signal_group(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=KILL_GRACE)
        except subprocess.TimeoutExpired:
            _signal_group(process.pid, signal.SIGKILL)
            process.wait()
    finally:
        # The group outlives its leader while any member lives, and its id is
        # not reused until then, so this reaches exactly what the extractor left.
        _signal_group(process.pid, signal.SIGKILL)
        with _GROUPS_LOCK:
            _GROUPS.discard(process.pid)
        reader.join()
        assert process.stdout is not None
        process.stdout.close()
    return (None if timed_out else process.returncode), bytes(tail)


def _fsync_tree(directory: Path) -> None:
    for path in directory.iterdir():
        with open(path, "rb") as handle:
            os.fsync(handle.fileno())
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def place_entry(output_dir: Path, manifest: dict[str, Any], final: Path) -> bool:
    """File a checked result as an entry: `entry.json` replaces `result.json`,
    every file becomes owner-only, everything is flushed, and the directory
    is renamed into place, so the entry is complete or absent. False when
    another runner filed the same entry first; its entry stands."""
    (output_dir / bank.RESULT).unlink()
    (output_dir / bank.ENTRY).write_bytes(json.dumps(manifest, indent=1, sort_keys=True).encode())
    # A feature can carry the recording (a stem): owner only, as derived media are.
    # check_result refused any file with another link, so this changes no one else's file.
    for path in output_dir.iterdir():
        path.chmod(0o600)
    output_dir.chmod(0o700)
    _fsync_tree(output_dir)
    final.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(output_dir, final)
    except OSError:
        if bank.read_entry(final) is None:
            raise
        return False
    return True


def parse_region(text: str) -> dict[str, int]:
    """`<start>:<end>:<rate>`, in samples at the content's stream rate."""
    parts = text.split(":")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise RunError(f"--region takes <start>:<end>:<rate> in samples, not {text!r}")
    return bank.check_region({"start": int(parts[0]), "end": int(parts[1]), "rate": int(parts[2])}, "--region")


def cut_arguments(source: Path, region: dict[str, int], target: Path) -> list[str]:
    """The pinned ffmpeg arguments of a region's cut: the first audio stream,
    trimmed by sample, as bit-exact FLAC without metadata, so cutting again
    gives the same bytes on the same ffmpeg."""
    return ["-nostdin", "-hide_banner", "-nostats", "-loglevel", "error", "-i", str(source), "-map", "0:a:0",
            "-af", f"atrim=start_sample={region['start']}:end_sample={region['end']},asetpts=PTS-STARTPTS",
            "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:a", "+bitexact", "-c:a", "flac",
            "-compression_level", "5", "-f", "flac", "-y", str(target)]


def flac_stream_info(path: Path) -> tuple[int, int]:
    """A FLAC file's sample rate and total samples per channel, from its
    STREAMINFO block."""
    with open(path, "rb") as handle:
        head = handle.read(42)
    if len(head) < 42 or head[:4] != b"fLaC" or head[4] & 0x7F != 0:
        raise RunError(f"{path} is not a FLAC file that begins with its STREAMINFO")
    packed = int.from_bytes(head[18:26], "big")
    return packed >> 44, packed & ((1 << 36) - 1)


def cut_region(ffmpeg: str, source: Path, region: dict[str, int], target: Path,
               timeout: float | None = None) -> str:
    """Cut a region of the content to `target` and return the cut's digest.
    A region past the content's end, or at another rate than the stream's,
    is refused rather than cut short."""
    try:
        completed = subprocess.run([ffmpeg, *cut_arguments(source, region, target)], capture_output=True,
                                   text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RunError(f"the region could not be cut: {exc}") from exc
    if completed.returncode != 0:
        raise RunError(f"the region could not be cut: ffmpeg exited {completed.returncode}: "
                       f"{completed.stderr.strip()[-300:]}")
    rate, samples = flac_stream_info(target)
    if rate != region["rate"]:
        raise RunError(f"the region is at {region['rate']} Hz but the content's first audio stream is at {rate} Hz")
    if samples != region["end"] - region["start"]:
        raise RunError(f"the region holds {region['end'] - region['start']} samples but the cut holds {samples}; "
                       f"it runs past the content's end")
    return bank.file_digest(target)


class Run:
    """One `run`: the content, the catalog, the bank, and what happened."""

    def __init__(self, catalog: bank.Catalog, root: Path, content: Path, media_type: str, digest: str | None,
                 programs: dict[str, str], parameters: dict[str, dict[str, Any]], timeout: float | None,
                 filed_under: str | None = None, optional: bool = False):
        self.catalog, self.root, self.content, self.media_type = catalog, root, content, media_type
        self.digest, self.digest_checked = digest, digest is None
        self.programs, self.parameters, self.timeout = programs, parameters, timeout
        # The digest whose directory holds the entries: the content's own,
        # or, for a file feature measured as content, the content it was made from.
        self.filed_under = filed_under
        # Whether an extractor whose program is not configured is skipped (`all`) rather than failed.
        self.optional = optional
        self.lock = threading.Lock()
        self.scratch: Path | None = None
        # A region run's cut and its digest, made once before the first extractor runs.
        self.cut: tuple[Path, str] | None = None

    # -- the content's digest --------------------------------------------

    def content_digest(self) -> str:
        if self.digest is None:
            self.digest = bank.file_digest(self.content)
        return self.digest

    def directory_digest(self) -> str:
        return self.filed_under or self.content_digest()

    @property
    def region(self) -> dict[str, int] | None:
        """The region of the content this run is limited to (ADR 0112), from
        the catalog's view (`Catalog.within`); None for the whole content."""
        if self.catalog.region is None:
            return None
        return self.catalog.region_of(self.content_digest())

    def input_media_type(self) -> str:
        return CUT_MEDIA_TYPE if self.region is not None else self.media_type

    def make_cut(self) -> None:
        """Cut the region once, into this run's scratch directory."""
        with self.lock:
            if self.cut is not None or self.region is None:
                return
            assert self.scratch is not None
            ffmpeg = self.programs.get("ffmpeg") or shutil.which("ffmpeg")
            if ffmpeg is None:
                raise RunError("a region run cuts with ffmpeg; pass --program ffmpeg=<path>")
            target = self.scratch / "cut.flac"
            self.cut = (target, cut_region(ffmpeg, self.content, self.region, target, self.timeout))

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
            if not extractor.accepts(self.input_media_type()):
                unusable[extractor_id] = f"does not accept {self.input_media_type()}"
            elif self.optional and (missing := unconfigured(extractor, self.programs)):
                unusable[extractor_id] = (f"runs ${{{missing}}}, which is not configured here; "
                                          f"pass --program {missing}=<path> to run it")
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
            entry = bank.read_entry(bank.entry_directory(self.root, self.directory_digest(), source, keys[source]))
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
                "input": self.input(),
                "needs": needs, "output_dir": str(output_dir)}

    def input(self) -> dict[str, str]:
        """What an extractor reads: the content, or a region run's cut."""
        if self.region is not None:
            assert self.cut is not None
            return {"path": str(self.cut[0].resolve()), "digest": self.cut[1], "media_type": CUT_MEDIA_TYPE}
        return {"path": str(self.content.resolve()), "digest": self.content_digest(), "media_type": self.media_type}

    # -- running one extractor ---------------------------------------------

    def run_one(self, extractor_id: str, keys: dict[str, str]) -> dict[str, Any]:
        """Run one extractor, once per key across every runner on this bank:
        a runner that finds another making the same entry waits for it."""
        key = keys[extractor_id]
        final = bank.entry_directory(self.root, self.directory_digest(), extractor_id, key)
        self.verify_digest()
        try:
            final.parent.mkdir(parents=True, exist_ok=True)
            lock = open(bank.lock_path(self.root, self.directory_digest(), extractor_id, key), "a+b")
        except OSError as exc:
            return self.failed(extractor_id, key, f"{extractor_id} cannot lock its entry: {exc}", b"")
        with lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                if bank.read_entry(final) is not None:
                    return {"status": "hit", "key": key, "entry": str(final), "waited": True}
            except bank.BankError as exc:
                return self.failed(extractor_id, key, str(exc), b"")
            return self.make(extractor_id, keys, final)

    def make(self, extractor_id: str, keys: dict[str, str], final: Path) -> dict[str, Any]:
        extractor = self.catalog[extractor_id]
        key = keys[extractor_id]
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
            environment = {**os.environ, "ARCHIVE_EXTRACTOR_REQUEST": str(work / "request.json"),
                           "ARCHIVE_EXTRACTOR_INPUT": request["input"]["path"],
                           "ARCHIVE_EXTRACTOR_OUTPUT": str(output_dir)}
            try:
                returncode, log = run_program(command, work, environment, self.timeout)
            except OSError as exc:
                raise bank.BankError(f"{extractor_id} could not start: {exc}") from exc
            if returncode is None:
                raise bank.BankError(f"{extractor_id} ran past {self.timeout} seconds")
            if returncode != 0:
                raise bank.BankError(f"{extractor_id} exited {returncode}")
            checked = bank.check_result(output_dir, extractor)
            manifest = {"contract": bank.ENTRY_CONTRACT, "key": key,
                        "extractor": {"id": extractor.id, "version": extractor.version},
                        "parameters": request["parameters"], "content": self.content_digest(),
                        "media_type": self.input_media_type(),
                        "needs": {need: keys[need.split("/", 1)[0]] for need in extractor.needs},
                        **checked, "started_at": started, "ended_at": now(), "runner": RUNNER}
            if self.region is not None:
                assert self.cut is not None
                manifest.update(contract=bank.REGION_ENTRY_CONTRACT, region=self.region, cut=self.cut[1])
            bank.check_entry(manifest, f"{extractor_id} entry")
            if not place_entry(output_dir, manifest, final):
                return {"status": "hit", "key": key, "entry": str(final), "raced": True}
            bank.failure_path(self.root, self.directory_digest(), extractor_id, key).unlink(missing_ok=True)
            return {"status": "ran", "key": key, "entry": str(final),
                    "seconds": round(time.monotonic() - clock, 3)}
        except (bank.BankError, OSError) as exc:
            return self.failed(extractor_id, key, str(exc), log, started)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def failed(self, extractor_id: str, key: str, error: str, log: bytes,
               started: str | None = None) -> dict[str, Any]:
        """A failure, as the run reports it and as recorded beside the entry."""
        extractor = self.catalog[extractor_id]
        failure = {"extractor": {"id": extractor.id, "version": extractor.version}, "key": key,
                   "content": self.content_digest(), "error": error, "started_at": started or now(),
                   "ended_at": now(), "log": log[-LOG_TAIL:].decode("utf-8", "replace")}
        answer = {"status": "failed", "key": key, "error": error, "log": failure["log"]}
        try:
            self.record_failure(extractor_id, key, failure)
        except OSError as exc:
            # The failure stands; only its operational record is missing.
            answer["unrecorded"] = f"the failure could not be recorded: {exc}"
        return answer

    def record_failure(self, extractor_id: str, key: str, failure: dict[str, Any]) -> None:
        """Operational: the last failure, replaced atomically. Never read as an entry."""
        path = bank.failure_path(self.root, self.directory_digest(), extractor_id, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}")
        try:
            temporary.write_text(json.dumps(failure, indent=1, sort_keys=True))
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

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
            try:
                entry = bank.read_entry(bank.entry_directory(self.root, self.directory_digest(), extractor_id,
                                                             keys[extractor_id]))
            except bank.BankError as exc:
                # Damage blocks only what needs this entry. The bank is
                # regenerable: deleting the damaged directory repairs it.
                outcome[extractor_id] = {"status": "failed", "key": keys[extractor_id],
                                         "error": f"{exc}; delete it and run again"}
                continue
            if entry is not None:
                outcome[extractor_id] = {"status": "hit", "key": keys[extractor_id], "entry": str(entry.path)}
            else:
                pending.append(extractor_id)
        if dry_run:
            for extractor_id in pending:
                extractor = self.catalog[extractor_id]
                outcome[extractor_id] = {
                    "status": "would_run", "key": keys[extractor_id],
                    "task": {"capability": spool_capability(extractor),
                             "requires": extractor.manifest.get("requires", []),
                             "after": [keys[s] for s in self.catalog.upstream(extractor_id)],
                             "parameters": self.catalog.parameters(extractor_id, self.parameters),
                             "input": {"digest": self.content_digest(), "media_type": self.input_media_type(),
                                       **({"filed_under": self.filed_under} if self.filed_under else {}),
                                       **({"region": self.region} if self.region is not None else {})}}}
            return self.answer(order, outcome)
        if pending:
            (self.root / "tmp").mkdir(parents=True, exist_ok=True)
            self.scratch = Path(tempfile.mkdtemp(prefix=f"run-{os.getpid()}-", dir=self.root / "tmp"))
        try:
            if pending and self.region is not None:
                self.verify_digest()
                self.make_cut()
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
        answer = {"status": "ok" if ok else "failed", "content": self.content_digest(),
                  "media_type": self.media_type, "bank": str(self.root),
                  "extractors": {e: outcome[e] for e in order}}
        if self.filed_under is not None:
            answer["filed_under"] = self.filed_under
        if self.region is not None:
            answer["region"] = self.region
            if self.cut is not None:
                answer["cut"] = self.cut[1]
        return answer


def run_from(catalog: bank.Catalog, root: Path, content: Path, media_type: str, digest: str | None,
             programs: dict[str, str], parameters: dict[str, dict[str, Any]], timeout: float | None,
             feature: str, targets: list[str], jobs: int, dry_run: bool, optional: bool) -> dict[str, Any]:
    """Run `targets` on a file feature of the content, as content (ADR 0075).

    The feature's extractor runs first, on the content, if its entry is
    missing. Then the targets run with the feature's file as their input:
    its digest, recorded in the entry and checked again before anything is
    filed, is their content, so their keys depend on the stem's bits and
    nothing else; their entries are filed under the content's directory,
    which holds everything made from it."""
    match = bank.FEATURE.fullmatch(feature)
    if match is None:
        raise RunError(f"--from takes <extractor>/<output>, not {feature!r}")
    source, output = match.groups()
    declared = catalog[source].outputs.get(output)
    if declared is None or declared["kind"] != "file":
        raise RunError(f"--from {feature}: only a file feature can be measured as content")
    for target in targets:
        if source in catalog.order([target]):
            raise RunError(f"{target} would measure {feature}, which it makes or needs; it cannot be its own input")
    first = Run(catalog, root, content, media_type, digest, programs, parameters, timeout)
    # The source is made with the operator's parameters; what measures it, with those an extractor prefers there.
    measuring = catalog.overrides_for(feature, parameters)
    made = first.execute([source], jobs, dry_run)
    origin = {"feature": feature, "content": made["content"], "extractors": made["extractors"]}
    status = made["extractors"][source]["status"]
    if status not in ("hit", "ran"):
        # Failed, blocked, skipped, or (in a dry run) not made yet: there is
        # no file, so there is no digest to key anything on.
        ok = dry_run and status == "would_run"
        return {"status": "ok" if ok else "failed", "content": None, "media_type": declared["media_type"],
                "bank": str(root), "extractors": {}, "from": origin}
    found = bank.Bank(root, catalog, parameters).input_of(made["content"], feature)
    if found is None:
        raise RunError(f"{feature} was made but its entry cannot be found")
    path, stem_digest, stem_type = found
    origin["entry"] = made["extractors"][source]["key"]
    second = Run(catalog, root, path, stem_type, stem_digest, programs, measuring, timeout,
                 filed_under=made["content"], optional=optional)
    answer = second.execute(targets, jobs, dry_run)
    answer["from"] = origin
    return answer


def run_default(catalog: bank.Catalog, root: Path, content: Path, media_type: str, digest: str | None,
                programs: dict[str, str], parameters: dict[str, dict[str, Any]], timeout: float | None,
                targets: list[str], jobs: int, dry_run: bool, optional: bool) -> dict[str, Any]:
    """Run `targets` on the content, each on its default input.

    The targets that prefer no feature run first, with everything they need.
    Then each target that prefers one measures the first preferred feature
    the bank now holds (`Bank.default_input`), as `run_from` would, or the
    content where it holds none. Where that measure falls short of the
    preference's `at_least`, the target measures its next default input as
    well (the mix, when its vocal stem is mostly unvoiced). A preferring
    target's answer says which feature it measured (`input`) and the keys of
    the measures it passed over (`passed_over`); what was run to measure a
    feature is under `inputs`."""
    run = Run(catalog, root, content, media_type, digest, programs, parameters, timeout, optional=optional)
    preferring = [t for t in targets if catalog[t].prefers and catalog[t].accepts(media_type)]
    plain = [t for t in targets if t not in preferring]
    answer = run.execute(plain, jobs, dry_run) if plain else {
        "status": "ok", "content": run.content_digest(), "media_type": media_type, "bank": str(root),
        "extractors": {}}
    reader = bank.Bank(root, catalog, parameters)
    passed_over: dict[str, dict[str, str]] = {}
    pending = list(preferring)
    while pending:
        on_content: list[str] = []
        by_feature: dict[str, list[str]] = {}
        for target in pending:
            feature = reader.default_input(run.content_digest(), target)
            (by_feature.setdefault(feature, []) if feature else on_content).append(target)
        if on_content:
            more = run.execute(on_content, jobs, dry_run)
            answer["extractors"].update(more["extractors"])
            answer["status"] = "ok" if answer["status"] == more["status"] == "ok" else "failed"
        pending = []
        for feature, group in by_feature.items():
            made = run_from(catalog, root, content, media_type, run.content_digest(), programs, parameters,
                            timeout, feature, group, jobs, dry_run, optional)
            answer.setdefault("inputs", {})[feature] = {"content": made["content"],
                                                        "extractors": made["extractors"], "from": made["from"]}
            for target in group:
                answer["extractors"][target] = {**made["extractors"][target], "input": feature}
                # A measure that falls short of its preference's `at_least`
                # moves the default on, to the next preference or the
                # content: measure that too. Each round passes over one
                # preference at least, so this ends.
                if (not dry_run and answer["extractors"][target]["status"] in ("hit", "ran")
                        and reader.default_input(run.content_digest(), target) != feature):
                    passed_over.setdefault(target, {})[feature] = answer["extractors"][target]["key"]
                    pending.append(target)
            answer["status"] = "ok" if answer["status"] == made["status"] == "ok" else "failed"
    for target, measures in passed_over.items():
        answer["extractors"][target]["passed_over"] = measures
    return answer


def makes_content(extractor: bank.Extractor) -> bool:
    """Whether the extractor makes a file it would itself accept as content:
    a separator, whose stems are audio. `run all --from` a feature leaves it
    out, since it would make features of a feature (stems of a stem) that
    nobody asked for; named on its own, it runs."""
    return any(declared["kind"] == "file" and extractor.accepts(declared["media_type"])
               for declared in extractor.outputs.values())


def unconfigured(extractor: bank.Extractor, programs: dict[str, str]) -> str | None:
    """The first program the extractor's command names that is not configured."""
    for word in extractor.manifest["command"]:
        match = bank.PROGRAM.fullmatch(word)
        if match and match.group(1) not in programs:
            return match.group(1)
    return None


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
    running.add_argument("--from", dest="source", metavar="EXTRACTOR/OUTPUT")
    running.add_argument("--no-prefer", action="store_true",
                         help="measure the content itself, even where an extractor prefers a feature of it")
    running.add_argument("--region", help="limit the run to [start, end) samples at rate: <start>:<end>:<rate>")
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
        catalog.check_overrides(config.get("parameters", {}), f"configuration {arguments.config}")
        everything = arguments.extractor == "all"
        targets = sorted(catalog.extractors) if everything else [arguments.extractor]
        for target in targets:
            catalog[target]
        root = Path(arguments.bank).resolve()
        media_type = arguments.media_type or media_type_of(content)
        if arguments.region is not None:
            region = parse_region(arguments.region)
            if arguments.digest is None:
                arguments.digest = bank.file_digest(content)
            catalog = catalog.within(arguments.digest, region)
        if arguments.source is not None:
            if everything:
                # Everything that can measure the feature; not what makes it,
                # and not what would make content of it (a separator would
                # separate the stem again).
                source = arguments.source.split("/", 1)[0]
                targets = [t for t in targets if source not in catalog.order([t]) and not makes_content(catalog[t])]
            answer = run_from(catalog, root, content, media_type, arguments.digest, programs,
                              config.get("parameters", {}), arguments.timeout, arguments.source, targets,
                              arguments.jobs, arguments.dry_run, everything)
        elif arguments.no_prefer:
            run = Run(catalog, root, content, media_type, arguments.digest, programs, config.get("parameters", {}),
                      arguments.timeout, optional=everything)
            answer = run.execute(targets, arguments.jobs, arguments.dry_run)
        else:
            answer = run_default(catalog, root, content, media_type, arguments.digest, programs,
                                 config.get("parameters", {}), arguments.timeout, targets, arguments.jobs,
                                 arguments.dry_run, everything)
    except (RunError, bank.BankError) as exc:
        print(json.dumps({"status": "failed", "detail": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(answer, indent=1))
    return 0 if answer["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
