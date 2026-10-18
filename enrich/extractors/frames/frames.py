#!/usr/bin/env python3
"""The frames extractor: one decode, one pass, the base series (ADR 0074).

It decodes the content's first audio stream once, as `cover.py` decodes a
whole file (mono 16-bit PCM at 22,050 Hz, divided by 32,768), cuts it into
Essentia's frames of 4,096 samples every 2,048, and measures every frame once:

- `hpcp`: the pitch-class profile exactly as `cover.Analyser.features`
  computes it, with the same algorithm instances, so a reader gets the same
  bits as a fresh analysis of the whole recording;
- `rms_db`: the frame's RMS level, unwindowed, in dB relative to full scale,
  floored at -120 dB;
- `centroid`, `rolloff`: the windowed magnitude spectrum's centroid and 85 %
  roll-off frequency, in Hz;
- `flatness`: its spectral flatness, 0 to 1;
- `flux`: the L2 norm of its change from the previous frame's spectrum;
- `onset_hfc`: Essentia's high-frequency-content onset detection function;
- `mfcc`, `melbands`: 13 MFCCs and the 40 mel-band energies they come from;
- `samples`, `seconds`: how long the decoded audio is, at the analysis rate.

Frame k is centred on analysis sample k x 2,048 and zero-padded outside the
audio (Essentia's FrameGenerator with `startFromZero` false); n samples make
ceil(n / 2,048) + 1 frames. The decode is streamed and cut into the same
frames a block at a time, and the series are written a block of rows at a
time, so memory does not grow with the recording's length. At about 10.8 frames a second, the grid suits
song-length and section-length questions, not note onsets.

Invoked by the runner as `frames.py <request.json>`; the settings are fixed,
and a request with other parameters is refused rather than measured with
settings that are not the ones it names.
"""

from __future__ import annotations

import importlib.metadata
import json
import math
import platform
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable, Iterator

ENRICH = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ENRICH))

import numpy  # noqa: E402

import cover  # noqa: E402

RATE = cover.ANALYSIS["analysis_rate"]
FRAME = cover.ANALYSIS["frame_size"]
HOP = cover.ANALYSIS["hop_size"]
RMS_FLOOR_DB = -120
MFCC = {"numberBands": 40, "numberCoefficients": 13, "highFrequencyBound": RATE // 2}
ROLLOFF = "0.85"
SERIES = {"hpcp": 12, "rms_db": 1, "centroid": 1, "rolloff": 1, "flatness": 1, "flux": 1, "onset_hfc": 1,
          "mfcc": MFCC["numberCoefficients"], "melbands": MFCC["numberBands"]}


class FramesError(Exception):
    pass


def decode_arguments(path: str) -> list[str]:
    """The ffmpeg arguments after the program name: the first audio stream,
    whole, as `cover.py` decodes a whole file."""
    return ["-nostdin", "-hide_banner", "-nostats", "-loglevel", "error", "-i", path, "-map", "0:a:0", "-ac", "1",
            "-ar", str(RATE), "-c:a", "pcm_s16le", "-f", "s16le", "-"]


def parameters() -> dict[str, Any]:
    """The settings, which the manifest must declare exactly."""
    return {"decoder": "ffmpeg", "decode_arguments": decode_arguments("{input}"), "sample_scale": 32768,
            "essentia": cover.ESSENTIA_BUILD, "analysis_rate": RATE, "frame_size": FRAME, "hop_size": HOP,
            "framing": "essentia-framegenerator-centred",
            "windowing": cover.ANALYSIS["windowing"], "spectral_peaks": cover.ANALYSIS["spectral_peaks"],
            "hpcp": cover.ANALYSIS["hpcp"], "rms_floor_db": RMS_FLOOR_DB, "rolloff_cutoff": ROLLOFF,
            "onset_method": "hfc", "mfcc": MFCC}


class Sink:
    """The series files, written a block of rows at a time, so memory does
    not grow with the recording."""

    BLOCK = 4096

    def __init__(self, directory: Path):
        self.handles = {name: open(directory / f"{name}.f32", "wb") for name in SERIES}
        self.block = {name: numpy.empty((self.BLOCK, columns), dtype="<f4") for name, columns in SERIES.items()}
        self.filled = 0
        self.rows = 0

    def row(self) -> int:
        """The index in the current block of the next row, flushing a full block."""
        if self.filled == self.BLOCK:
            self.flush()
        self.filled += 1
        self.rows += 1
        return self.filled - 1

    def flush(self) -> None:
        for name, handle in self.handles.items():
            handle.write(self.block[name][:self.filled].tobytes())
        self.filled = 0

    def close(self) -> None:
        self.flush()
        for handle in self.handles.values():
            handle.close()


def decoded_blocks(path: str, ffmpeg: str = "ffmpeg", block_bytes: int = 1 << 20) -> Iterator[numpy.ndarray]:
    """The first audio stream decoded as `cover.py` decodes a whole file,
    as float32 blocks of samples divided by 32,768, in order."""
    with tempfile.TemporaryFile() as errors:
        try:
            process = subprocess.Popen([ffmpeg, *decode_arguments(path)], stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=errors)
        except OSError as exc:
            raise FramesError(f"ffmpeg could not run: {exc}") from exc
        assert process.stdout is not None
        carry = b""
        try:
            while chunk := process.stdout.read(block_bytes):
                chunk = carry + chunk
                usable = len(chunk) - len(chunk) % 2
                carry = chunk[usable:]
                if usable:
                    yield numpy.frombuffer(chunk[:usable], dtype="<i2").astype(numpy.float32) / numpy.float32(32768)
        finally:
            process.stdout.close()
            returncode = process.wait()
        if returncode != 0 or carry:
            errors.seek(0)
            raise FramesError(f"ffmpeg could not decode {path}: "
                              f"{errors.read().decode('utf-8', 'replace').strip()[:300]}")


def framed(blocks: Iterable[numpy.ndarray], frame: int = FRAME, hop: int = HOP) -> Iterator[numpy.ndarray]:
    """Essentia's FrameGenerator frames (`startFromZero` false) of the
    concatenated blocks, without holding them all: frame k is samples
    [k * hop - frame / 2, k * hop + frame / 2), zero outside the audio, and
    frames continue while their first sample is before the end, so n
    samples make ceil(n / hop) + 1 frames when frame is 2 x hop.
    `test_framing_is_frame_generators` holds it to Essentia's for many lengths."""
    buffer = numpy.zeros(frame // 2, dtype=numpy.float32)  # the padding before sample 0
    offset = 0  # the padded index of buffer[0]
    k = 0
    total = 0
    for block in blocks:
        total += len(block)
        buffer = numpy.concatenate((buffer, block))
        while k * hop + frame <= offset + len(buffer):
            begin = k * hop - offset
            yield buffer[begin:begin + frame]
            k += 1
        drop = k * hop - offset
        if drop > 0:
            buffer = buffer[drop:]
            offset += drop
    if total == 0:
        return
    # A frame starts at padded index k * hop, which is sample k * hop - frame / 2.
    last = -(-(total + frame // 2) // hop) - 1
    needed = last * hop + frame - offset
    if needed > len(buffer):
        buffer = numpy.concatenate((buffer, numpy.zeros(needed - len(buffer), dtype=numpy.float32)))
    while k <= last:
        begin = k * hop - offset
        yield buffer[begin:begin + frame]
        k += 1


def measure(frames: Iterable[numpy.ndarray], sink: Sink) -> None:
    """Every series, in one pass over the frames."""
    analyser = cover.Analyser()
    es = analyser.es
    rms = es.RMS()
    centroid = es.Centroid(range=RATE / 2)
    rolloff = es.RollOff(sampleRate=RATE, cutoff=float(ROLLOFF))
    flatness = es.Flatness()
    flux = es.Flux()
    onset = es.OnsetDetection(method="hfc", sampleRate=RATE)
    mfcc = es.MFCC(inputSize=FRAME // 2 + 1, sampleRate=RATE, **MFCC)
    no_phase = numpy.zeros(FRAME // 2 + 1, dtype=numpy.float32)
    block = sink.block
    for frame in frames:
        index = sink.row()
        spectrum = analyser.spectrum(analyser.windowing(frame))
        block["hpcp"][index] = analyser.hpcp(*analyser.peaks(spectrum))
        level = float(rms(frame))
        block["rms_db"][index] = max(20 * math.log10(level), RMS_FLOOR_DB) if level > 0 else RMS_FLOOR_DB
        block["centroid"][index] = centroid(spectrum)
        block["rolloff"][index] = rolloff(spectrum)
        block["flatness"][index] = flatness(spectrum)
        block["flux"][index] = flux(spectrum)
        block["onset_hfc"][index] = onset(spectrum, no_phase)
        block["melbands"][index], block["mfcc"][index] = mfcc(spectrum)


class Counted:
    """Blocks passed through, counting their samples."""

    def __init__(self, blocks: Iterable[numpy.ndarray]):
        self.blocks, self.samples = blocks, 0

    def __iter__(self) -> Iterator[numpy.ndarray]:
        for block in self.blocks:
            self.samples += len(block)
            yield block


def ffmpeg_version(ffmpeg: str = "ffmpeg") -> str:
    first = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True).stdout.split("\n", 1)[0].split()
    return first[2] if len(first) > 2 and first[:2] == ["ffmpeg", "version"] else "unknown"


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: frames.py <request.json>", file=sys.stderr)
        return 2
    try:
        request = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
        if request.get("parameters") != parameters():
            raise FramesError("frames 1 has fixed settings; the request names others")
        output = Path(request["output_dir"])
        audio = Counted(decoded_blocks(request["input"]["path"]))
        sink = Sink(output)
        try:
            measure(framed(audio), sink)
        finally:
            sink.close()
        if audio.samples == 0:
            raise FramesError(f"{request['input']['path']} decodes to no samples")
        count = -(-audio.samples // HOP) + 1
        if sink.rows != count:
            raise FramesError(f"{audio.samples} samples made {sink.rows} frames, not the {count} the grid says")
        software = [{"name": "ffmpeg", "version": ffmpeg_version()},
                    {"name": "essentia", "version": importlib.metadata.version("essentia")},
                    {"name": "numpy", "version": numpy.__version__},
                    {"name": "python", "version": platform.python_version()}]
        outputs: dict[str, Any] = {name: {"shape": [sink.rows, columns]} for name, columns in SERIES.items()}
        outputs["samples"] = {"value": audio.samples}
        outputs["seconds"] = {"value": audio.samples / RATE}
        result = {"contract": "archive-extractor-result/1", "software": software,
                  "grids": {"frames": {"sample_rate": RATE, "hop": HOP, "frame": FRAME, "first_centre": 0}},
                  "outputs": outputs}
        (output / "result.json").write_text(json.dumps(result))
    except (FramesError, cover.CoverError, OSError, KeyError, ValueError) as exc:
        print(f"frames: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
