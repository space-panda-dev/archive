#!/usr/bin/env python3
"""The beats-beatthis extractor: beats and downbeats by Beat This.

Beat This (Foscarin, Schlüter, and Widmer, ISMIR 2024) is a learned beat and
downbeat tracker. This program runs its `final0` checkpoint on the CPU with
the package's minimal postprocessor (peak picking, no dynamic Bayesian
network), so the metrical level and the downbeats are the model's own:
nothing here asserts a meter, and the spacing of the downbeats is not one.

The runner starts it with the interpreter the operator configured as
`beatthis-python` (ADR 0074), never its own, so the archive's environment
never imports PyTorch, and passes the checkpoint the operator configured as
`beatthis-checkpoint`; a checkpoint that is not the pinned bits is refused.
Only the standard library is imported until the model is loaded.

Invoked by the runner as
`<beatthis-python> beatthis.py <checkpoint file> <request.json>`; the
settings are fixed, and a request naming others is refused.
"""

from __future__ import annotations

import os

THREADS = 3
if __name__ == "__main__":
    # Before numpy or torch load, so that every numeric library has the
    # fixed thread count the parameters name.
    for _name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMBA_NUM_THREADS"):
        os.environ[_name] = str(THREADS)

import hashlib  # noqa: E402
import json  # noqa: E402
import platform  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Protocol, Sequence  # noqa: E402

CHECKPOINT = "sha256:8c328b45f59d8dd3dff219253ff6a8d6482be57d0133a29140e2febbf8eb8331"

# The settings, which the manifest must declare exactly. They are the
# method's description as well as its key: the decode and the frame rate are
# what beat_this 1.1.0 does, and are not choices this program can change.
PARAMETERS: dict[str, Any] = {
    "model": {"name": "beat_this final0", "package": "beat_this==1.1.0", "file": "beat_this-final0.ckpt",
              "sha256": CHECKPOINT},
    "decode": "soundfile.read float32, both channels, at the file's own rate (beat_this resamples to 22050 Hz mono)",
    "postprocessor": "minimal",
    "frame_rate": 50,
    "device": "cpu",
    "threads": THREADS,
}


class BeatThisError(Exception):
    pass


class Model(Protocol):
    """A tracker: `track` takes the input's path and returns the beat and
    downbeat times in seconds, in order."""
    software: list[dict[str, str]]

    def track(self, path: str) -> tuple[Sequence[float], Sequence[float]]: ...


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 22), b""):
            hasher.update(block)
    return "sha256:" + hasher.hexdigest()


class BeatThis:
    """beat_this's own inference: its spectrogram, the model, and the
    minimal postprocessor, on the CPU."""

    def __init__(self, checkpoint: Path):
        import importlib.metadata as metadata
        import torch
        from beat_this.inference import Audio2Frames, Postprocessor

        torch.set_num_threads(THREADS)
        self.audio2frames = Audio2Frames(checkpoint_path=str(checkpoint), device=PARAMETERS["device"])
        self.postprocess = Postprocessor(type=PARAMETERS["postprocessor"])
        self.software = [{"name": name, "version": metadata.version(name)}
                         for name in ("beat_this", "torch", "numpy", "soundfile")]
        self.software.append({"name": "python", "version": platform.python_version()})

    def track(self, path: str) -> tuple[Sequence[float], Sequence[float]]:
        import soundfile
        samples, rate = soundfile.read(path, dtype="float32")
        beat_logits, downbeat_logits = self.audio2frames(samples, rate)
        return self.postprocess(beat_logits, downbeat_logits)


def load_model(checkpoint: Path) -> Model:
    """The pinned checkpoint in beat_this. Imports PyTorch, so only Beat
    This's environment can run it."""
    if not checkpoint.is_file():
        raise BeatThisError(f"no checkpoint at {checkpoint}; configure beatthis-checkpoint")
    if sha256_file(checkpoint) != CHECKPOINT:
        raise BeatThisError(f"{checkpoint} is not the pinned checkpoint {CHECKPOINT}")
    try:
        return BeatThis(checkpoint)
    except ImportError as exc:
        raise BeatThisError(f"this interpreter cannot load beat_this ({exc}); configure beatthis-python") from exc


def median_bpm(beats: Sequence[float]) -> float:
    """60 over the median beat interval, to a thousandth; 0 with fewer than
    two beats. The median of an even count is the mean of the middle two,
    as numpy's is."""
    if len(beats) < 2:
        return 0
    intervals = sorted(b - a for a, b in zip(beats, beats[1:]))
    middle = len(intervals) // 2
    median = intervals[middle] if len(intervals) % 2 else (intervals[middle - 1] + intervals[middle]) / 2
    return round(60.0 / median, 3)


def write(output: Path, beats: Sequence[float], downbeats: Sequence[float],
          software: list[dict[str, str]]) -> None:
    """The declared outputs and `result.json`."""
    beats = [float(t) for t in beats]
    downbeats = [float(t) for t in downbeats]
    (output / "beats.json").write_text(json.dumps([{"time": t} for t in beats]))
    (output / "downbeats.json").write_text(json.dumps([{"time": t} for t in downbeats]))
    result = {"contract": "archive-extractor-result/1", "software": software, "grids": {},
              "outputs": {"beats": {}, "downbeats": {}, "beat_count": {"value": len(beats)},
                          "downbeat_count": {"value": len(downbeats)},
                          "median_bpm": {"value": median_bpm(beats)}}}
    (output / "result.json").write_text(json.dumps(result))


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: beatthis.py <checkpoint> <request.json>", file=sys.stderr)
        return 2
    request = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    if request.get("parameters") != PARAMETERS:
        print("beats-beatthis 1 has fixed settings; the request names others", file=sys.stderr)
        return 1
    try:
        model = load_model(Path(argv[0]))
        beats, downbeats = model.track(request["input"]["path"])
    except BeatThisError as exc:
        print(f"beats-beatthis: {exc}", file=sys.stderr)
        return 1
    write(Path(request["output_dir"]), beats, downbeats, model.software)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
