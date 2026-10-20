#!/usr/bin/env python3
"""The f0-pesto extractor: a monophonic pitch track by PESTO, in its own
environment.

The runner starts this program with the interpreter the operator configured
as `f0-python` (ADR 0074), so the archive's environment never imports
PyTorch. It needs only numpy and `ffmpeg` until the tracker is loaded;
`torch` and `pesto` are imported by `load_tracker` alone.

It decodes the content's first audio stream as 16 kHz mono float32, tracks
it a minute at a time with PESTO's `mir-1k_g7` checkpoint at a 10 ms step,
and writes `f0`: one row per 160 samples, the pitch in Hz (0 where the
tracker gives none) and PESTO's confidence. A monophonic tracker reads one
line, so the content is meant to be one voice: `--from stems/vocals`, or
the bass line, `--from stems/bass`.

Version 2 also writes the level of the same audio on the same rows,
`level_db` (the RMS in dBFS of the 320 samples, 20 ms, centred on each row;
silence outside the audio; floored at -120), and `loud_db`, its 99th
percentile, so a note builder can find re-articulations and gate voicing
without decoding the audio again.

A region run (ADR 0112) is laid on the whole run's grid. Where the
request's input carries the `region` it lies at in the recording, the audio
is placed at that offset (rounded to a sample at 16 kHz): silence before it
back to the whole run's row at or before its start, and the first minute cut
short at the whole run's next minute, so every row and every minute's edge
falls where the whole run's does. A row before the region is dropped, and
the grid's `first_centre` says where the first row lies on the cut's
timeline (0 to 159 samples). Only the silence where the whole run hears what
lies before or after the region differs, and `loud_db` is the region's.

The checkpoint is a pickle that PESTO loads with `weights_only=False`, so
its digest is checked before it is loaded, and the checked file is the one
loaded.

Invoked by the runner as `<f0-python> f0_pesto.py <request.json>`; the
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
STEP_MS = 10
CHUNK_SECONDS = 60
CHUNK = CHUNK_SECONDS * RATE  # samples a chunk; only a test sets another
CHECKPOINT = "mir-1k_g7"
WEIGHTS = "sha256:16c32e06ddd950e3e4866dfa3c7f8a87c4988f8adf43e57977b189f031f26f3e"
PESTO = "2.0.1"
LEVEL_FRAME = 320
LEVEL_FLOOR_DB = -120
LOUD_PERCENTILE = 99


class F0Error(Exception):
    pass


def decode_arguments(path: str) -> list[str]:
    """The ffmpeg arguments after the program name: the first audio stream,
    mixed to mono, as 16 kHz float32."""
    return ["-nostdin", "-hide_banner", "-nostats", "-loglevel", "error", "-i", path, "-map", "0:a:0", "-ac", "1",
            "-ar", str(RATE), "-c:a", "pcm_f32le", "-f", "f32le", "-"]


def parameters() -> dict[str, Any]:
    """The settings, which the manifest must declare exactly."""
    return {"tracker": f"pesto-pitch=={PESTO}", "model": CHECKPOINT, "weights": WEIGHTS, "reduction": "alwa",
            "step_ms": STEP_MS, "chunk_seconds": CHUNK_SECONDS, "confidence": "pesto confidence",
            "decode_arguments": decode_arguments("{input}"), "rate": RATE, "hop": HOP,
            "level": {"frame": LEVEL_FRAME, "floor_db": LEVEL_FLOOR_DB, "loud_percentile": LOUD_PERCENTILE}}


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

    def track(self, signal: numpy.ndarray, first: int | None = None) -> tuple[numpy.ndarray, numpy.ndarray]: ...


class Pesto:
    def __init__(self) -> None:
        import importlib.metadata
        import pesto
        import torch
        from pesto.loader import load_model

        version = importlib.metadata.version("pesto-pitch")
        if version != PESTO:
            raise F0Error(f"pesto-pitch is {version}, not the pinned {PESTO}")
        path = Path(pesto.__file__).parent / "weights" / f"{CHECKPOINT}.ckpt"
        if not path.is_file():
            raise F0Error(f"the checkpoint {path} was not found")
        actual = sha256_file(path)
        if actual != WEIGHTS:
            raise F0Error(f"the checkpoint {path.name} is {actual}, not the pinned {WEIGHTS}")
        self.model = load_model(str(path), float(STEP_MS), sampling_rate=RATE)
        self.model.reduction = "alwa"
        self.torch = torch
        self.software = [{"name": "pesto-pitch", "version": version}, {"name": "torch", "version": torch.__version__}]

    def track(self, signal: numpy.ndarray, first: int | None = None) -> tuple[numpy.ndarray, numpy.ndarray]:
        """A minute at a time, each chunk's rows past its own minute dropped,
        so every row stays on the grid of the whole signal; the first chunk
        `first` samples long when given (a region's, see `chunks`)."""
        pitches, confidences = [], []
        with self.torch.inference_mode():
            for start, end in chunks(len(signal), first):
                chunk = self.torch.from_numpy(signal[start:end])
                pitch, confidence, _volume, _activations = self.model(chunk, sr=RATE, convert_to_freq=True,
                                                                      return_activations=True)
                keep = (end - start) // HOP if end < len(signal) else None
                pitches.append(pitch.reshape(-1).numpy()[:keep])
                confidences.append(confidence.reshape(-1).numpy()[:keep])
        return numpy.concatenate(pitches), numpy.concatenate(confidences)


def chunks(samples: int, first: int | None = None) -> list[tuple[int, int]]:
    """[start, end) of each chunk the tracker is given: a minute each, the
    first `first` samples (a multiple of HOP) when given."""
    step = CHUNK
    edges = [0, *range(first or step, samples, step)]
    return [(start, min(end, samples)) for start, end in zip(edges, [*edges[1:], samples])]


def offset_at(region: dict[str, int] | None, rate: int) -> int | None:
    """Where a region's audio starts on the recording's timeline, in samples
    at `rate`, to the nearest (half to even); None for whole content."""
    if region is None:
        return None
    whole, part = divmod(region["start"] * rate, region["rate"])
    return whole + (1 if 2 * part > region["rate"] or (2 * part == region["rate"] and whole % 2) else 0)


def placed(signal: numpy.ndarray, offset: int) -> tuple[numpy.ndarray, int, int]:
    """A region's signal at `offset` (samples at RATE) on the whole run's
    grid: the signal after the silence back to the whole's row at or before
    it, that silence's length, and the first chunk's (to the whole's next
    minute)."""
    lead = offset % HOP
    step = CHUNK
    return (numpy.concatenate([numpy.zeros(lead, dtype=signal.dtype), signal]), lead,
            step - (offset - lead) % step)


def load_tracker() -> Tracker:
    """The pinned tracker. Imports PyTorch, so only its environment can run it."""
    try:
        return Pesto()
    except ImportError as exc:
        raise F0Error(f"this interpreter cannot load PESTO ({exc}); configure f0-python") from exc


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


def row_levels(signal: numpy.ndarray, rows: int) -> numpy.ndarray:
    """The RMS level in dBFS of the LEVEL_FRAME samples centred on each row
    (row k on sample k x HOP), as [rows, 1] float32; samples outside the
    audio are silence, and the level is floored at LEVEL_FLOOR_DB."""
    cumulative = numpy.concatenate([[0.0], numpy.cumsum(numpy.asarray(signal, dtype=numpy.float64) ** 2)])
    centres = numpy.arange(rows) * HOP
    low = numpy.clip(centres - LEVEL_FRAME // 2, 0, len(signal))
    high = numpy.clip(centres + LEVEL_FRAME // 2, 0, len(signal))
    mean_square = numpy.maximum(cumulative[high] - cumulative[low], 0.0) / LEVEL_FRAME
    level = 10 * numpy.log10(numpy.maximum(mean_square, 10 ** (LEVEL_FLOOR_DB / 10)))
    return level.astype("<f4").reshape(-1, 1)


def ffmpeg_version(ffmpeg: str = "ffmpeg") -> str:
    first = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True).stdout.split("\n", 1)[0].split()
    return first[2] if len(first) > 2 and first[:2] == ["ffmpeg", "version"] else "unknown"


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: f0_pesto.py <request.json>", file=sys.stderr)
        return 2
    try:
        request = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
        if request.get("parameters") != parameters():
            raise F0Error("f0-pesto has fixed settings; the request names others")
        output = Path(request["output_dir"])
        tracker = load_tracker()
        signal = decode(request["input"]["path"])
        offset = offset_at(request["input"].get("region"), RATE)
        if offset is None:
            pitch, confidence = tracker.track(signal)
            track = on_grid(pitch, confidence, len(signal))
            level = row_levels(signal, len(track))
            first_centre = 0
        else:
            # A region on the whole run's grid (see the module).
            signal, lead, first = placed(signal, offset)
            pitch, confidence = tracker.track(signal, first)
            track = on_grid(pitch, confidence, len(signal))
            level = row_levels(signal, len(track))
            drop = 1 if lead else 0
            track, level, first_centre = track[drop:], level[drop:], drop * HOP - lead
        (output / "f0.f32").write_bytes(track.tobytes())
        (output / "level_db.f32").write_bytes(level.tobytes())
        software = [*tracker.software, {"name": "ffmpeg", "version": ffmpeg_version()},
                    {"name": "numpy", "version": numpy.__version__},
                    {"name": "python", "version": platform.python_version()}]
        result = {"contract": "archive-extractor-result/1", "software": software,
                  "grids": {"f0": {"sample_rate": RATE, "hop": HOP, "first_centre": first_centre}},
                  "outputs": {"f0": {"shape": list(track.shape)}, "rows_from_tracker": {"value": int(len(pitch))},
                              "level_db": {"shape": list(level.shape)},
                              "loud_db": {"value": float(numpy.percentile(level, LOUD_PERCENTILE))}}}
        (output / "result.json").write_text(json.dumps(result))
    except (F0Error, OSError, KeyError, ValueError) as exc:
        print(f"f0-pesto: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
