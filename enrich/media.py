#!/usr/bin/env python3
"""Make waveform peaks and a listening copy of one audio file (ADR 0069).

    enrich/media.py --root <derived-media root> --digest sha256:<hex> <file>

Both are regenerable derived media, never admitted. They are written under
the root at `sha256/<ab>/<cd>/<hex>/<name>` (enrich/derived.py), each to a
temporary file in the same directory and then renamed, so a reader sees a
complete file or none. The file's SHA-256 must be the digest asked for: the
digest names the output, so a wrong file would be served as another
recording's media. A rerun on complete outputs decodes and writes nothing.

- Peaks: an audiowaveform binary file, version 2, with 8-bit values. Each
  pixel holds a signed minimum and maximum for every channel of the source,
  computed from the full-resolution decode, with `samples_per_pixel` equal to
  the sample rate divided by 100, rounded half to even.
- Listening copy: AAC-LC at 192 kb/s in MP4 with its index at the start,
  downmixed to stereo when the source has more than two channels.

It prints one JSON object naming each output and whether it was written or
already present. A failure prints `{"status": "failed", "detail": ...}` to
stderr and exits 1.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import IO, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

import derived  # noqa: E402
# The peaks' format and decode (enrich/waveform.py), named here as before.
from waveform import (PEAKS_FLAGS_8_BIT, PEAKS_HEADER, PEAKS_VERSION, PIXELS_PER_CHUNK,  # noqa: E402,F401
                      AudioStream, MediaError, peaks_complete, probe, samples_per_pixel, write_peaks)

LISTEN_BITRATE = "192k"


def content_digest(source: Path) -> str:
    hasher = hashlib.sha256()
    with open(source, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            hasher.update(block)
    return "sha256:" + hasher.hexdigest()


# -- completeness ---------------------------------------------------------


def mp4_complete(path: Path) -> bool:
    """Whether ``path`` is a whole MP4 with its index first: top-level boxes
    that exactly fill the file, starting with `ftyp`, with `moov` before
    `mdat`. Absent is False; a file that cannot be read is an error."""
    try:
        handle = open(path, "rb")
    except FileNotFoundError:
        return False
    boxes = []
    with handle:
        size = os.fstat(handle.fileno()).st_size
        offset = 0
        while offset < size:
            handle.seek(offset)
            header = handle.read(8)
            if len(header) < 8:
                return False
            box_size, box_type = struct.unpack(">I4s", header)
            header_size = 8
            if box_size == 1:
                large = handle.read(8)
                if len(large) < 8:
                    return False
                box_size, header_size = struct.unpack(">Q", large)[0], 16
            elif box_size == 0:
                box_size = size - offset
            if box_size < header_size or offset + box_size > size:
                return False
            boxes.append(box_type)
            offset += box_size
    return (bool(boxes) and boxes[0] == b"ftyp" and b"moov" in boxes and b"mdat" in boxes
            and boxes.index(b"moov") < boxes.index(b"mdat"))


COMPLETE: dict[str, Callable[[Path], bool]] = {"peaks": peaks_complete, "listen": mp4_complete}


# -- making ---------------------------------------------------------------


def write_listening_copy(source: Path, stream: AudioStream, target: Path, ffmpeg: str) -> None:
    """Encode ``source`` to ``target``, which must not be the final name."""
    downmix = ["-ac", "2"] if stream.channels > 2 else []
    encoded = subprocess.run(
        [ffmpeg, "-nostdin", "-v", "error", "-y", "-i", str(source), "-map", "0:a:0", "-vn", "-sn", "-dn",
         "-map_metadata", "-1", "-c:a", "aac", "-profile:a", "aac_low", "-b:a", LISTEN_BITRATE, *downmix,
         "-fflags", "+bitexact", "-flags:a", "+bitexact", "-movflags", "+faststart", "-f", "mp4", str(target)],
        capture_output=True, text=True)
    if encoded.returncode != 0:
        raise MediaError(f"ffmpeg could not encode a listening copy of {source}: {encoded.stderr.strip()}")
    if not mp4_complete(target):
        raise MediaError(f"ffmpeg's listening copy of {source} is not a complete MP4 with its index first")


def _install(target: Path, make: Callable[[Path, IO[bytes]], None]) -> None:
    """Make ``target`` in a temporary file beside it, then rename it into
    place, so a reader sees all of it or none of it."""
    descriptor, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w+b") as handle:
            make(temporary, handle)
        # By name: a program given the path may have replaced the file.
        _fsync(temporary)
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    _fsync(target.parent)


def _fsync(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def derive(source: Path, digest: str, root: Path, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe",
           kinds: tuple[str, ...] = tuple(derived.KINDS)) -> dict:
    """Make whatever derived media of ``source`` are missing under ``root``,
    of the kinds asked for: peaks alone are quick, and an importer makes them
    before the slow listening copy."""
    unknown = [kind for kind in kinds if kind not in derived.KINDS]
    if unknown or not kinds:
        raise MediaError(f"the kinds of derived media are {sorted(derived.KINDS)}, not {list(kinds)}")
    if not derived.is_digest(digest):
        raise MediaError(f"not a canonical content digest: {digest!r}")
    try:
        if not stat.S_ISDIR(os.stat(root).st_mode):
            raise MediaError(f"the derived-media root is not a directory: {root}")
    except FileNotFoundError as exc:
        raise MediaError(f"the derived-media root does not exist: {root}") from exc
    actual = content_digest(source)
    if actual != digest:
        raise MediaError(f"{source} has digest {actual}, not the {digest} asked for")
    targets = {kind: derived.path(root, digest, kind) for kind in derived.KINDS if kind in kinds}
    status = {kind: "present" if COMPLETE[kind](target) else "missing" for kind, target in targets.items()}
    if "missing" in status.values():
        stream = probe(source, ffprobe)
        derived.directory(root, digest).mkdir(parents=True, exist_ok=True)
        if status.get("peaks") == "missing":
            _install(targets["peaks"], lambda _, handle: write_peaks(source, stream, handle, ffmpeg))
            status["peaks"] = "written"
        if status.get("listen") == "missing":
            _install(targets["listen"], lambda temporary, _: write_listening_copy(source, stream, temporary, ffmpeg))
            status["listen"] = "written"
    return {"digest": digest,
            "outputs": {kind: {"path": str(targets[kind]), "status": status[kind]} for kind in targets}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="enrich-media", description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path, help="the audio file, whose content has the given digest")
    parser.add_argument("--root", type=Path, required=True, help="the derived-media root")
    parser.add_argument("--digest", required=True, help="the file's content digest, sha256:<hex>")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--kind", action="append", choices=sorted(derived.KINDS),
                        help="make only this kind (repeatable); every kind when omitted")
    args = parser.parse_args(argv)
    try:
        result = derive(args.source, args.digest, args.root, ffmpeg=args.ffmpeg, ffprobe=args.ffprobe,
                        kinds=tuple(args.kind) if args.kind else tuple(derived.KINDS))
    except (MediaError, OSError) as exc:
        print(json.dumps({"status": "failed", "detail": f"{type(exc).__name__}: {exc}"}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
