#!/usr/bin/env python3
"""The f0-torchcrepe extractor: a monophonic pitch track by torchcrepe's
tiny CREPE model, in its own environment; a second opinion beside
`f0-pesto`.

The runner starts this program with the interpreter the operator configured
as `f0-python` (ADR 0074), so the archive's environment never imports
PyTorch. It needs only numpy and `ffmpeg` until the tracker is loaded;
`torch` and `torchcrepe` are imported by `load_tracker` alone.

It decodes the content's first audio stream as 16 kHz mono float32, tracks
it with torchcrepe 0.0.24's `tiny` model (50 to 1,100 Hz, zero-padded
frames) at a 10 ms step, and writes `f0`: one row per 160 samples, the pitch
in Hz and the model's periodicity as a confidence. Only the tiny model: the
full one cost 12 times real time and 5.6 GB on a CPU.

The pitch is decoded here, not by torchcrepe's decoders, which add random
noise of up to 20 cents to every pitch ("dither", from an unseeded
generator), so two runs on one file differed by up to 38 cents. Here the
bins are torchcrepe's Viterbi path, and each frame's pitch is the mean of
the 9 bins around the path weighted by the model's probabilities (CREPE's
own local average), so a run gives the same bits every time.
A monophonic tracker reads one line, so the content is meant to be one
voice: `--from stems/vocals`.

The model file's digest is checked before torchcrepe loads it.

Invoked by the runner as `<f0-python> f0_crepe.py <request.json>`; the
settings are fixed, and a request naming others is refused.
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any, Protocol

import numpy

RATE = 16000
HOP = 160
FMIN_HZ = 50
FMAX_HZ = 1100
BATCH_SIZE = 512
BINS = 360
CENTS_PER_BIN = 20
LOWEST_CENTS = 1997.3794084376191  # torchcrepe's bin 0, in cents above 10 Hz
REACH_BINS = 4                     # the local average spans the path's bin and 4 either side
CAPACITY = "tiny"
WEIGHTS = "sha256:d4993eea36ed1a0ad9ac549c740dae5265b049ce72004f00c2f59e01c0be8432"
TORCHCREPE = "0.0.24"


class F0Error(Exception):
    pass


def decode_arguments(path: str) -> list[str]:
    """The ffmpeg arguments after the program name: the first audio stream,
    mixed to mono, as 16 kHz float32."""
    return ["-nostdin", "-hide_banner", "-nostats", "-loglevel", "error", "-i", path, "-map", "0:a:0", "-ac", "1",
            "-ar", str(RATE), "-c:a", "pcm_f32le", "-f", "f32le", "-"]


def parameters() -> dict[str, Any]:
    """The settings, which the manifest must declare exactly."""
    return {"tracker": f"torchcrepe=={TORCHCREPE}", "model": CAPACITY, "weights": WEIGHTS, "decoder": "viterbi path; pitch the probability-weighted mean of the 9 bins around it; no dither",
            "fmin_hz": FMIN_HZ, "fmax_hz": FMAX_HZ, "batch_size": BATCH_SIZE, "pad": True,
            "confidence": "periodicity", "decode_arguments": decode_arguments("{input}"), "rate": RATE, "hop": HOP}


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 22), b""):
            hasher.update(block)
    return "sha256:" + hasher.hexdigest()


class Tracker(Protocol):
    """`track` takes 16 kHz mono float32 and returns the pitch in Hz and a
    confidence, one row per HOP samples from sample 0."""
    software: list[dict[str, str]]

    def track(self, signal: numpy.ndarray) -> tuple[numpy.ndarray, numpy.ndarray]: ...


class Crepe:
    def __init__(self) -> None:
        import importlib.metadata
        import torch
        import torchcrepe

        version = importlib.metadata.version("torchcrepe")
        if version != TORCHCREPE:
            raise F0Error(f"torchcrepe is {version}, not the pinned {TORCHCREPE}")
        path = Path(torchcrepe.__file__).parent / "assets" / f"{CAPACITY}.pth"
        if not path.is_file():
            raise F0Error(f"the model file {path} was not found")
        actual = sha256_file(path)
        if actual != WEIGHTS:
            raise F0Error(f"the model file {path.name} is {actual}, not the pinned {WEIGHTS}")
        self.torch, self.torchcrepe = torch, torchcrepe
        self.software = [{"name": "torchcrepe", "version": version}, {"name": "torch", "version": torch.__version__}]

    def decode(self, probabilities):
        """torchcrepe's decoder interface: [1, 360, frames] probabilities,
        -inf outside the pitch range, to (bins, pitch in Hz)."""
        torch = self.torch
        bins, _dithered = self.torchcrepe.decode.viterbi(probabilities)
        weights = torch.where(torch.isfinite(probabilities), probabilities, torch.zeros_like(probabilities))
        pitch = torch.from_numpy(local_average_hz(weights[0].double().numpy(), bins[0].numpy()))
        return bins, pitch[None].to(probabilities.dtype)

    def track(self, signal: numpy.ndarray) -> tuple[numpy.ndarray, numpy.ndarray]:
        with self.torch.inference_mode():
            pitch, periodicity = self.torchcrepe.predict(
                self.torch.from_numpy(signal)[None], RATE, HOP, FMIN_HZ, FMAX_HZ, CAPACITY,
                decoder=self.decode, return_periodicity=True, batch_size=BATCH_SIZE, device="cpu", pad=True)
        return pitch[0].numpy(), periodicity[0].numpy()


def local_average_hz(probabilities: numpy.ndarray, path: numpy.ndarray) -> numpy.ndarray:
    """Each frame's pitch: the mean of the bins within REACH_BINS of the
    path's, weighted by their probabilities ([360, frames] and [frames]);
    the path's own bin where they are all 0."""
    cents = LOWEST_CENTS + CENTS_PER_BIN * numpy.arange(BINS, dtype=numpy.float64)
    offsets = numpy.arange(-REACH_BINS, REACH_BINS + 1)
    near = numpy.clip(path[None, :] + offsets[:, None], 0, BINS - 1)          # [9, frames]
    inside = (path[None, :] + offsets[:, None] >= 0) & (path[None, :] + offsets[:, None] < BINS)
    weights = numpy.take_along_axis(probabilities, near, axis=0) * inside
    total = weights.sum(axis=0)
    mean = numpy.where(total > 0, (weights * cents[near]).sum(axis=0) / numpy.where(total > 0, total, 1),
                       cents[numpy.clip(path, 0, BINS - 1)])
    return 10 * 2 ** (mean / 1200)


def load_tracker() -> Tracker:
    """The pinned tracker. Imports PyTorch, so only its environment can run it."""
    try:
        return Crepe()
    except ImportError as exc:
        raise F0Error(f"this interpreter cannot load torchcrepe ({exc}); configure f0-python") from exc


def decode(path: str, ffmpeg: str = "ffmpeg") -> numpy.ndarray:
    completed = subprocess.run([ffmpeg, *decode_arguments(path)], stdin=subprocess.DEVNULL, capture_output=True)
    if completed.returncode != 0 or len(completed.stdout) % 4:
        raise F0Error(f"ffmpeg could not decode {path}: {completed.stderr.decode('utf-8', 'replace').strip()[:300]}")
    signal = numpy.frombuffer(completed.stdout, dtype="<f4").copy()
    if not len(signal):
        raise F0Error(f"{path} decodes to no samples")
    return signal


def on_grid(pitch: numpy.ndarray, confidence: numpy.ndarray, samples: int) -> numpy.ndarray:
    """The track as [rows, 2] float32 on the grid: n samples make n // HOP + 1
    rows; a tracker's extra rows are dropped and missing ones are 0, and a
    pitch or confidence that is not finite is 0."""
    rows = samples // HOP + 1
    pitch = numpy.nan_to_num(numpy.asarray(pitch, dtype=numpy.float64), nan=0.0, posinf=0.0, neginf=0.0)
    confidence = numpy.nan_to_num(numpy.asarray(confidence, dtype=numpy.float64), nan=0.0, posinf=0.0, neginf=0.0)
    if pitch.shape != confidence.shape:
        raise F0Error(f"the tracker gave {pitch.shape} pitches and {confidence.shape} confidences")
    track = numpy.zeros((rows, 2), dtype="<f4")
    kept = min(len(pitch), rows)
    track[:kept, 0], track[:kept, 1] = pitch[:kept], confidence[:kept]
    return track


def ffmpeg_version(ffmpeg: str = "ffmpeg") -> str:
    first = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True).stdout.split("\n", 1)[0].split()
    return first[2] if len(first) > 2 and first[:2] == ["ffmpeg", "version"] else "unknown"


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: f0_crepe.py <request.json>", file=sys.stderr)
        return 2
    try:
        request = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
        if request.get("parameters") != parameters():
            raise F0Error("f0-torchcrepe has fixed settings; the request names others")
        output = Path(request["output_dir"])
        tracker = load_tracker()
        signal = decode(request["input"]["path"])
        pitch, confidence = tracker.track(signal)
        track = on_grid(pitch, confidence, len(signal))
        (output / "f0.f32").write_bytes(track.tobytes())
        software = [*tracker.software, {"name": "ffmpeg", "version": ffmpeg_version()},
                    {"name": "numpy", "version": numpy.__version__},
                    {"name": "python", "version": platform.python_version()}]
        result = {"contract": "archive-extractor-result/1", "software": software,
                  "grids": {"f0": {"sample_rate": RATE, "hop": HOP, "first_centre": 0}},
                  "outputs": {"f0": {"shape": list(track.shape)}, "rows_from_tracker": {"value": int(len(pitch))}}}
        (output / "result.json").write_text(json.dumps(result))
    except (F0Error, OSError, KeyError, ValueError) as exc:
        print(f"f0-torchcrepe: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
