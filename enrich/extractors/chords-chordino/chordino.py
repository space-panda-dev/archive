#!/usr/bin/env python3
"""The chords-chordino extractor: chord labels by Chordino.

Chordino (Mauch and Dixon's NNLS Chroma Vamp plugin) names chords from a
non-negative least squares chroma with an HMM over a small chord dictionary.
It is not learned from data, so it is a second opinion of a different kind
beside a learned recogniser such as `chords-jiang`. This program runs the
plugin build that the `chord-extractor` package bundles, through the `vamp`
host, on a 44,100 Hz mono decode by librosa, and writes its `simplechord`
labels as spans that tile the decoded audio.

The runner starts it with the interpreter the operator configured as
`chordino-python` (ADR 0074), never its own; a plugin library that is not
the pinned bits is refused. Only the standard library is imported until the
plugin is loaded.

Invoked by the runner as `<chordino-python> chordino.py <request.json>`; the
settings are fixed, and a request naming others is refused.
"""

from __future__ import annotations

import os

THREADS = 3
if __name__ == "__main__":
    # Before numpy loads, so that every numeric library has the fixed thread
    # count the parameters name.
    for _name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMBA_NUM_THREADS"):
        os.environ[_name] = str(THREADS)

import hashlib  # noqa: E402
import json  # noqa: E402
import platform  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Protocol, Sequence  # noqa: E402

PLUGIN = "sha256:022f18b7a0b922161bba5f292b916482fd04d9bad4c875292d25c141f98c7d99"
RATE = 44100

# The settings, which the manifest must declare exactly. The plugin's own
# parameters are its defaults; a fraction is decimal text (ADR 0064).
PARAMETERS: dict[str, Any] = {
    "plugin": {"name": "nnls-chroma:chordino", "output": "simplechord", "file": "chord_extractor/_lib/nnls-chroma.so",
               "sha256": PLUGIN},
    "decode": "librosa.load sr 44100 mono (soxr_hq)",
    "plugin_parameters": {"useNNLS": 1, "rollon": 1, "tuningmode": 0, "whitening": 1, "s": "0.7", "boostn": "0.1"},
    "device": "cpu",
    "threads": THREADS,
}


class ChordinoError(Exception):
    pass


class Model(Protocol):
    """A labeller: `label` takes the input's path and returns the decoded
    length in seconds and the plugin's (time, label) changes, in order."""
    software: list[dict[str, str]]

    def label(self, path: str) -> tuple[float, Sequence[tuple[float, str]]]: ...


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 22), b""):
            hasher.update(block)
    return "sha256:" + hasher.hexdigest()


def plugin_parameters() -> dict[str, float | int]:
    """The plugin's parameters as the host takes them: numbers."""
    return {name: float(value) if isinstance(value, str) else value
            for name, value in PARAMETERS["plugin_parameters"].items()}


class Chordino:
    """The bundled plugin through the vamp host."""

    def __init__(self) -> None:
        import importlib.metadata as metadata
        import chord_extractor
        import chord_extractor.extractors  # noqa: F401  (points VAMP_PATH at the bundled plugin)

        library = Path(chord_extractor.__file__).parent / "_lib" / "nnls-chroma.so"
        if not library.is_file() or sha256_file(library) != PLUGIN:
            raise ChordinoError(f"{library} is not the pinned plugin {PLUGIN}")
        self.software = [{"name": name, "version": metadata.version(name)}
                         for name in ("vamp", "chord-extractor", "librosa", "numpy")]
        self.software.append({"name": "python", "version": platform.python_version()})

    def label(self, path: str) -> tuple[float, Sequence[tuple[float, str]]]:
        import librosa
        import vamp
        samples, rate = librosa.load(path, sr=RATE, mono=True)
        found = vamp.collect(samples, rate, PARAMETERS["plugin"]["name"], output=PARAMETERS["plugin"]["output"],
                             parameters=plugin_parameters())
        return len(samples) / rate, [(float(item["timestamp"]), item["label"]) for item in found["list"]]


def load_model() -> Model:
    """The pinned plugin. Needs the vamp host and librosa, so only
    Chordino's environment can run it."""
    try:
        return Chordino()
    except ImportError as exc:
        raise ChordinoError(f"this interpreter cannot load Chordino ({exc}); configure chordino-python") from exc


def spans(seconds: float, changes: Sequence[tuple[float, str]]) -> list[dict[str, Any]]:
    """Each label from its change to the next, the last to the decoded
    length (or its own start, if the plugin reports one past the end)."""
    found = []
    for index, (start, label) in enumerate(changes):
        end = changes[index + 1][0] if index + 1 < len(changes) else max(start, seconds)
        found.append({"start": start, "end": end, "label": label})
    return found


def write(output: Path, seconds: float, changes: Sequence[tuple[float, str]],
          software: list[dict[str, str]]) -> None:
    """The declared outputs and `result.json`."""
    found = spans(seconds, changes)
    (output / "chords.json").write_text(json.dumps(found))
    result = {"contract": "archive-extractor-result/1", "software": software, "grids": {},
              "outputs": {"chords": {}, "chord_count": {"value": len(found)}, "seconds": {"value": round(seconds, 3)}}}
    (output / "result.json").write_text(json.dumps(result))


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: chordino.py <request.json>", file=sys.stderr)
        return 2
    request = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    if request.get("parameters") != PARAMETERS:
        print("chords-chordino 1 has fixed settings; the request names others", file=sys.stderr)
        return 1
    try:
        model = load_model()
        seconds, changes = model.label(request["input"]["path"])
    except ChordinoError as exc:
        print(f"chords-chordino: {exc}", file=sys.stderr)
        return 1
    write(Path(request["output_dir"]), seconds, changes, model.software)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
