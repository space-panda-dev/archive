#!/usr/bin/env python3
"""Waveform peaks: the format, and the decode that makes them (ADR 0069).

A peaks file is an audiowaveform binary file, version 2, with 8-bit values.
Each pixel holds a signed minimum and maximum for every channel of the
source, computed from the full-resolution decode, with `samples_per_pixel`
equal to the sample rate divided by 100, rounded half to even.

`enrich/media.py` installs them as derived media; the colour extractor
decodes them for its grid and preview. They live here, apart from the
derived-media tool's command line and listening copy, so that what decides
the peaks' bytes is one small module a test can pin.
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import tempfile
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import IO

# The audiowaveform binary format, version 2: version, flags (bit 0 set for
# 8-bit values), sample rate, samples per pixel, length in pixels, channels,
# all little-endian 32-bit; then, per pixel and per channel, minimum and
# maximum.
PEAKS_HEADER = struct.Struct("<iIiiIi")
PEAKS_VERSION = 2
PEAKS_FLAGS_8_BIT = 1
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
