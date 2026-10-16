"""Count an audio file's streams exactly by decoding them (ADR 0065).

A tool, not an Archive client: it takes a file and returns a plain result.
`ffprobe` lists the streams; `ffmpeg` decodes each audio stream through the
`ashowinfo` filter, which reports every decoded frame's rate, channel count,
and sample count before any conversion. A stream is counted only when every
frame decoded cleanly at one rate and one channel count; otherwise the probe
says why and claims nothing for it. A container's duration or a bitrate
estimate is never used.

Standard library only; `ffmpeg` and `ffprobe` must be installed.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

AGENT = "pkg:generic/archive-enrich-probe@0.1.0"
METHOD_ID = "archive-enrich/probe-decoded-frames"
METHOD_VERSION = "1"

# Messages at these levels mean a frame may have been lost or concealed, so
# the count is not exact. Warnings (a guessed channel layout, a container's
# duration estimate) say nothing about the decoded frames.
REFUSING_LEVELS = ("error", "fatal", "panic")


def stream_arguments(path: str) -> list[str]:
    """The ffprobe arguments, after the program name, that list the streams."""
    return ["-v", "error", "-show_entries",
            "stream=index,codec_type,codec_name,sample_rate,channels,bits_per_sample", "-of", "json", path]


def decode_arguments(path: str, stream_index: int | str) -> list[str]:
    """The ffmpeg arguments after the program name. `repeat` keeps one log
    line per frame instead of collapsing repeats; `level` tags each line;
    `crccheck` makes a decoder report a frame whose checksum fails, so damage
    a decoder would otherwise conceal refuses the count."""
    return ["-nostdin", "-hide_banner", "-nostats", "-loglevel", "repeat+level+info",
            "-err_detect", "crccheck", "-i", path, "-map", f"0:{stream_index}", "-af", "ashowinfo", "-f", "null", "-"]


def parameters() -> dict[str, Any]:
    """The run's configuration (ADR 0006): how the decoder was driven and
    which of its messages make a count inexact. It changes only with the
    method, so its Artifact is admitted once and reused."""
    return {"stream_lister": "ffprobe", "stream_arguments": stream_arguments("{input}"),
            "decoder": "ffmpeg", "decode_arguments": decode_arguments("{input}", "{stream_index}"),
            "refusing_log_levels": list(REFUSING_LEVELS)}


class ProbeError(Exception):
    """The file could not be probed at all."""


@dataclass(frozen=True)
class Stream:
    stream_index: int
    codec: str
    sample_rate: int
    channel_count: int
    sample_count: int
    bits_per_sample: int | None = None

    def payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"stream_index": self.stream_index, "codec": self.codec,
                                   "sample_rate": self.sample_rate, "channel_count": self.channel_count,
                                   "sample_count": self.sample_count}
        if self.bits_per_sample is not None:
            payload["bits_per_sample"] = self.bits_per_sample
        return payload


@dataclass
class ProbeResult:
    streams: list[Stream] = field(default_factory=list)
    refused: list[dict[str, Any]] = field(default_factory=list)  # {stream_index, reason}

    def as_json(self) -> dict[str, Any]:
        return {"streams": [s.payload() for s in self.streams], "refused": self.refused}


_VERSION = re.compile(r"^(ffmpeg|ffprobe) version (\S+)")
_LIBRARY = re.compile(r"^(libavutil|libavcodec|libavformat|libavfilter)\s+(\d+)\.\s*(\d+)\.\s*(\d+)\s")


def _run(arguments: list[str], what: str) -> subprocess.CompletedProcess[str]:
    # The programs echo a file's tags, which need not be UTF-8; only ASCII
    # structure is read from their output, so undecodable bytes are replaced.
    try:
        return subprocess.run(arguments, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ProbeError(f"{what} could not run: {exc}") from exc


def software(ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe") -> tuple[tuple[str, str], ...]:
    """The programs and libraries the count depends on, as installed now:
    ffprobe and ffmpeg, then the libraries ffmpeg reports, in its order."""
    found: list[tuple[str, str]] = []
    for program, wanted_libraries in ((ffprobe, False), (ffmpeg, True)):
        completed = _run([program, "-version"], f"{program} -version")
        lines = completed.stdout.splitlines()
        match = _VERSION.match(lines[0]) if completed.returncode == 0 and lines else None
        if match is None:
            raise ProbeError(f"{program} -version did not report a version: {completed.stdout[:200]!r}")
        found.append((match.group(1), match.group(2)))
        if wanted_libraries:
            libraries = [_LIBRARY.match(line) for line in lines]
            libraries = [m for m in libraries if m]
            names = {m.group(1) for m in libraries}
            if not {"libavcodec", "libavformat", "libavfilter"} <= names:
                raise ProbeError(f"{program} -version did not report its decoding libraries")
            found.extend((m.group(1), f"{int(m.group(2))}.{int(m.group(3))}.{int(m.group(4))}") for m in libraries)
    return tuple(found)


def audio_streams(path: Path, ffprobe: str = "ffprobe") -> list[dict[str, Any]]:
    completed = _run([ffprobe, *stream_arguments(str(path))], "ffprobe")
    try:
        listed = json.loads(completed.stdout)
    except ValueError:
        listed = None
    if completed.returncode != 0 or not isinstance(listed, dict) or not isinstance(listed.get("streams"), list):
        raise ProbeError(f"ffprobe could not read {path}: {completed.stderr.strip()[:500]}")
    return [s for s in listed["streams"] if isinstance(s, dict) and s.get("codec_type") == "audio"]


# Only a line that starts with ashowinfo's own prefix is a frame report, and
# only a line that starts with a log prefix carries a level. ffmpeg also logs
# the file's tags at info level ("[info]     title : ..."), indenting any
# continuation line, so text a file holds can appear inside a line but never
# at its start: a tag cannot add frames to a count or hide a decoder error.
_FRAME = re.compile(r"^\[Parsed_ashowinfo_0 @ [^\]]+\] \[info\] n:(\d+) .*?channels:(\d+) .*?rate:(\d+) "
                    r"nb_samples:(\d+) ")
_LEVEL = re.compile(r"^(?:\[[^\]]+ @ [^\]]+\] ){0,2}\[(" + "|".join(REFUSING_LEVELS) + r")\] ")


def count_stream(path: Path, stream: dict[str, Any], ffmpeg: str = "ffmpeg") -> tuple[Stream | None, str | None]:
    """Decode one stream and count its sample frames. Returns the stream's
    facts, or None and the reason the count is not exact.

    The decoder's log is read line by line as it arrives: a recording of
    hours logs one line per frame, which is never held in memory at once."""
    index = stream.get("index")
    codec = stream.get("codec_name")
    if not isinstance(index, int) or index < 0 or not isinstance(codec, str) or not 1 <= len(codec) <= 100:
        return None, "ffprobe did not report the stream's index and codec"
    try:
        process = subprocess.Popen([ffmpeg, *decode_arguments(str(path), index)], stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except OSError as exc:
        raise ProbeError(f"ffmpeg could not run: {exc}") from exc
    rates, channels, total, expected = set(), set(), 0, 0
    problem = None
    with process:
        assert process.stderr is not None
        for raw in process.stderr:
            for line in raw.decode("utf-8", errors="replace").splitlines():
                frame = _FRAME.match(line)
                if frame:
                    number = int(frame.group(1))
                    # Frames are numbered from 0; a reconfigured filter starts again.
                    if number not in (0, expected) and problem is None:
                        problem = f"frame reports are out of sequence: {line.strip()}"
                    expected = number + 1
                    channels.add(int(frame.group(2)))
                    rates.add(int(frame.group(3)))
                    total += int(frame.group(4))
                elif problem is None and _LEVEL.match(line):
                    problem = line.strip()
    if process.returncode != 0:
        return None, f"ffmpeg exited {process.returncode} decoding the stream"
    if problem is not None:
        return None, "the decoder reported errors, so frames may be lost or concealed: " + problem[:300]
    if len(rates) != 1 or len(channels) != 1:
        if not rates:
            return None, "no frame was decoded"
        return None, (f"decoded frames change rate or channel count ({sorted(rates)} Hz, "
                      f"{sorted(channels)} channels), so no single rate counts them")
    if total < 1:
        return None, "no sample frame was decoded"
    (rate,), (channel_count,) = rates, channels
    if not 1 <= channel_count <= 64:
        return None, f"{channel_count} channels is outside what an audio stream claim states"
    bits = stream.get("bits_per_sample")
    bits_per_sample = bits if codec.startswith("pcm_") and isinstance(bits, int) and 1 <= bits <= 64 else None
    return Stream(index, codec, rate, channel_count, total, bits_per_sample), None


def probe(path: Path, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe") -> ProbeResult:
    """Every audio stream of the file: counted, or refused with a reason."""
    # Absolute, so ffmpeg never reads the name as an option or a protocol.
    path = Path(path).absolute()
    if not path.is_file():
        raise ProbeError(f"{path} is not a file")
    result = ProbeResult()
    for stream in audio_streams(path, ffprobe):
        counted, reason = count_stream(path, stream, ffmpeg)
        if counted is None:
            result.refused.append({"stream_index": stream.get("index"), "reason": reason})
        else:
            result.streams.append(counted)
    return result


def main(argv: list[str] | None = None) -> int:
    """Probe a file and print the plain result; no archive is involved."""
    import argparse
    parser = argparse.ArgumentParser(prog="enrich-probe-file", description=__doc__.splitlines()[0])
    parser.add_argument("file", type=Path)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    args = parser.parse_args(argv)
    try:
        result = probe(args.file, args.ffmpeg, args.ffprobe)
    except ProbeError as exc:
        print(json.dumps({"status": "failed", "detail": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(result.as_json(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
