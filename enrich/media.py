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
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import IO, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

import derived  # noqa: E402

# The audiowaveform binary format, version 2: version, flags (bit 0 set for
# 8-bit values), sample rate, samples per pixel, length in pixels, channels,
# all little-endian 32-bit; then, per pixel and per channel, minimum and
# maximum.
PEAKS_HEADER = struct.Struct("<iIiiIi")
PEAKS_VERSION = 2
PEAKS_FLAGS_8_BIT = 1
LISTEN_BITRATE = "192k"
# Pixels decoded and reduced at a time; bounds memory for long recordings.
PIXELS_PER_CHUNK = 4096


class MediaError(Exception):
    """A file this tool cannot make derived media from, or a failed step."""


@dataclass(frozen=True)
class AudioStream:
    sample_rate: int
    channels: int


def samples_per_pixel(sample_rate: int) -> int:
    """About ten milliseconds: the sample rate over 100, rounded half to even."""
    pixel = round(Fraction(sample_rate, 100))
    if pixel < 1:
        raise MediaError(f"a sample rate of {sample_rate} Hz is too low for 100 pixels a second")
    return pixel


def content_digest(source: Path) -> str:
    hasher = hashlib.sha256()
    with open(source, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            hasher.update(block)
    return "sha256:" + hasher.hexdigest()


def probe(source: Path, ffprobe: str) -> AudioStream:
    """The file's one audio stream. None, or more than one, is refused: which
    stream is the recording is not this tool's to guess."""
    probed = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "a", "-show_entries", "stream=sample_rate,channels",
         "-of", "json", str(source)], capture_output=True, text=True)
    if probed.returncode != 0:
        raise MediaError(f"ffprobe could not read {source}: {probed.stderr.strip()}")
    try:
        streams = json.loads(probed.stdout).get("streams", [])
    except (json.JSONDecodeError, AttributeError) as exc:
        raise MediaError(f"ffprobe answered something other than a JSON object for {source}") from exc
    if len(streams) != 1:
        raise MediaError(f"{source} has {len(streams)} audio streams; exactly one is supported")
    try:
        stream = AudioStream(int(streams[0]["sample_rate"]), int(streams[0]["channels"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise MediaError(f"ffprobe did not report the sample rate and channels of {source}") from exc
    if stream.sample_rate < 1 or stream.channels < 1:
        raise MediaError(f"{source} reports {stream.sample_rate} Hz and {stream.channels} channels")
    return stream


# -- completeness ---------------------------------------------------------


def peaks_complete(path: Path) -> bool:
    """Whether ``path`` is a whole version 2, 8-bit peaks file. Absent is
    False; a file that cannot be read is an error, never False."""
    try:
        with open(path, "rb") as handle:
            header = handle.read(PEAKS_HEADER.size)
            size = os.fstat(handle.fileno()).st_size
    except FileNotFoundError:
        return False
    if len(header) != PEAKS_HEADER.size:
        return False
    version, flags, rate, pixel, length, channels = PEAKS_HEADER.unpack(header)
    return (version == PEAKS_VERSION and flags == PEAKS_FLAGS_8_BIT and rate >= 1 and pixel >= 1
            and channels >= 1 and size == PEAKS_HEADER.size + length * channels * 2)


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


def _read_full(stream: IO[bytes], size: int) -> bytes:
    """Up to ``size`` bytes, fewer only at the end of the stream."""
    parts, wanted = [], size
    while wanted:
        part = stream.read(wanted)
        if not part:
            break
        parts.append(part)
        wanted -= len(part)
    return b"".join(parts)


def _quantize(values):  # type: ignore[no-untyped-def]
    """Full-scale floats to signed 8-bit as audiowaveform does for 16-bit
    samples (an arithmetic shift right by 8): floor(x * 128), clipped."""
    import numpy as np

    return np.clip(np.floor(values * 128.0), -128, 127).astype(np.int8)


def write_peaks(source: Path, stream: AudioStream, output: IO[bytes], ffmpeg: str) -> int:
    """Decode every channel at full resolution and write a peaks file to
    ``output``; returns its length in pixels."""
    import numpy as np

    pixel = samples_per_pixel(stream.sample_rate)
    frame_bytes = 4 * stream.channels
    output.write(PEAKS_HEADER.pack(PEAKS_VERSION, PEAKS_FLAGS_8_BIT, stream.sample_rate, pixel, 0, stream.channels))
    length = 0
    with tempfile.TemporaryFile() as errors:
        decoder = subprocess.Popen(
            [ffmpeg, "-nostdin", "-v", "error", "-i", str(source), "-map", "0:a:0",
             "-f", "f32le", "-acodec", "pcm_f32le", "pipe:1"],
            stdout=subprocess.PIPE, stderr=errors)
        try:
            while True:
                data = _read_full(decoder.stdout, PIXELS_PER_CHUNK * pixel * frame_bytes)
                if len(data) % frame_bytes:
                    raise MediaError(f"the decode of {source} ended inside a sample frame")
                if not data:
                    break
                frames = np.frombuffer(data, dtype="<f4").reshape(-1, stream.channels)
                whole = len(frames) // pixel
                minima, maxima = [], []
                if whole:
                    blocks = frames[:whole * pixel].reshape(whole, pixel, stream.channels)
                    minima.append(blocks.min(axis=1))
                    maxima.append(blocks.max(axis=1))
                if len(frames) > whole * pixel:
                    rest = frames[whole * pixel:]
                    minima.append(rest.min(axis=0, keepdims=True))
                    maxima.append(rest.max(axis=0, keepdims=True))
                pairs = np.stack([_quantize(np.concatenate(minima)), _quantize(np.concatenate(maxima))], axis=-1)
                output.write(pairs.tobytes())
                length += pairs.shape[0]
        finally:
            decoder.stdout.close()
            returncode = decoder.wait()
        if returncode != 0:
            errors.seek(0)
            raise MediaError(f"ffmpeg could not decode {source}: {errors.read().decode(errors='replace').strip()}")
    if length == 0:
        raise MediaError(f"{source} decodes to no samples")
    output.seek(0)
    output.write(PEAKS_HEADER.pack(PEAKS_VERSION, PEAKS_FLAGS_8_BIT, stream.sample_rate, pixel, length,
                                   stream.channels))
    return length


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
