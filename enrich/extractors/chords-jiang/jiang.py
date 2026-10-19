#!/usr/bin/env python3
"""The chords-jiang extractor: large-vocabulary chords with weighted readings.

Jiang, Li, Wang, and Xia's chord recogniser (ISMIR 2019) names chords from a
vocabulary of sevenths, extensions, suspensions, and inversions in Harte
spelling. This program runs its published five-model ensemble (the mean of
the five networks' outputs) over the repository's hybrid constant-Q
spectrogram and decodes it with the repository's HMM over its submission
chord list, as the repository's own `chord_recognition.py` does.

It adds one thing the repository does not: for each decoded span, the three
chord templates with the highest mean per-frame posterior, with those
weights. A per-frame posterior is the softmax over every template of the
decoder's own observation log-probabilities, so a weight is a share of the
whole template set and a span's three sum to at most 1. The decoded label
need not be among them: the HMM pays for changes, the readings do not.

The runner starts it with the interpreter the operator configured as
`jiang-python` (ADR 0074), never its own, and passes the repository checkout
the operator configured as `jiang-repo`; every pinned model file in it must
be the pinned bits. The repository's code predates numpy 1.24 and librosa
0.10, so the aliases `np.int`, `np.float`, `np.bool`, and `np.object` are
restored for it and its constant-Q transform is replaced by the same
transform in librosa's current interface; both are named in the parameters.
Only the standard library and numpy are imported until the model is loaded.

Invoked by the runner as `<jiang-python> jiang.py <repository> <request.json>`;
the settings are fixed, and a request naming others is refused.
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

import numpy  # noqa: E402

COMMIT = "481f4ce703f8822b99f4037e9104ba1760e21ea3"
MODEL_NAME = "joint_chord_net_ismir_naive_v1.0_reweight(0.0,10.0)"
FILES = {
    f"cache_data/{MODEL_NAME}_s0.best.sdict": "sha256:921b42d5d1cf9ce1c0c0e45a74d409b8066e0acec46058ef74e24ee0fb540761",
    f"cache_data/{MODEL_NAME}_s1.best.sdict": "sha256:bcb75859e0efa256696cf5da396b320093317b9b1d9560c304f46c25fe1f8b17",
    f"cache_data/{MODEL_NAME}_s2.best.sdict": "sha256:acddf85c3fff29954c4877021177d72e2cba9f729ce80c1010f054c477bf3f61",
    f"cache_data/{MODEL_NAME}_s3.best.sdict": "sha256:65d81a3ab73435aaaade586981b4cabdf57b8953d76052703e6968c32ef8421c",
    f"cache_data/{MODEL_NAME}_s4.best.sdict": "sha256:5ff6b0ec85640e17a09a9b3de68c93fdd45adc24488e8fa9be5715c28d561122",
    "data/submission_chord_list.txt": "sha256:ac68a26e87242fc58c946d476028d4e8c8d12c9e707ced7020a4d7e31c794d7b",
}
TOP = 3

# The settings, which the manifest must declare exactly.
PARAMETERS: dict[str, Any] = {
    "model": {"name": f"{MODEL_NAME}, seeds s0-s4, mean of the five", "repository_commit": COMMIT, "files": FILES},
    "cqt": "librosa.hybrid_cqt bins_per_octave 36, fmin F#0, n_bins 288, tuning None, "
           "the repo's DEFAULT_SR and DEFAULT_HOP_LENGTH, magnitude float32",
    "numpy_aliases": "np.int, np.float, np.bool, np.object restored for the repo's code",
    "decoder": "XHMMDecoder submission chord list, decode_to_chordlab",
    "readings": {"top": TOP, "weight": "mean over the span's frames of the per-frame softmax over the decoder's "
                                      "chord templates of get_chord_tag_obs log-probabilities"},
    "device": "cpu",
    "threads": THREADS,
}


class JiangError(Exception):
    pass


class Decoded:
    """What the model and decoder give: the decoded (start, end, label)
    spans, the decoder's template names, each frame's observation
    log-probability of every template ([frames, templates]), and the
    frame's length in seconds."""

    def __init__(self, labels: Sequence[tuple[float, float, str]], names: Sequence[str],
                 log_probabilities: numpy.ndarray, frame_seconds: float):
        self.labels = labels
        self.names = list(names)
        self.log_probabilities = log_probabilities
        self.frame_seconds = frame_seconds


class Model(Protocol):
    software: list[dict[str, str]]

    def decode(self, path: str) -> Decoded: ...


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 22), b""):
            hasher.update(block)
    return "sha256:" + hasher.hexdigest()


def check_files(repository: Path) -> None:
    """Every pinned file must be the bits its digest names: other weights
    or another chord list are another method, and must be another key."""
    for name, digest in FILES.items():
        path = repository / name
        if not path.is_file():
            raise JiangError(f"{repository} has no {name}; configure jiang-repo as a checkout of {COMMIT}")
        if sha256_file(path) != digest:
            raise JiangError(f"{name} is not the pinned file {digest}")


class Jiang:
    """The repository's ensemble and decoder, imported from its checkout."""

    def __init__(self, repository: Path):
        import importlib.metadata as metadata
        os.chdir(repository)  # the repository opens its models and chord list by relative path
        sys.path.insert(0, str(repository))
        import librosa
        import torch
        for alias, kind in (("int", int), ("float", float), ("bool", bool), ("object", object)):
            if not hasattr(numpy, alias):
                setattr(numpy, alias, kind)
        torch.set_num_threads(THREADS)
        import extractors.cqt as cqt
        from settings import DEFAULT_HOP_LENGTH, DEFAULT_SR

        def hybrid_cqt(self: Any, entry: Any, **kwargs: Any) -> numpy.ndarray:
            music = entry.dict[kwargs["source"]].get(entry) if "source" in kwargs else entry.music
            found = librosa.hybrid_cqt(y=music, sr=entry.prop.sr, bins_per_octave=36,
                                       fmin=librosa.note_to_hz("F#0"), n_bins=288, tuning=None,
                                       hop_length=entry.prop.hop_length).T
            return abs(found).astype(numpy.float32)

        cqt.CQTV2.extract = hybrid_cqt
        from chord_recognition import MODEL_NAMES
        from chordnet_ismir_naive import ChordNet
        from extractors.xhmm_ismir import XHMMDecoder
        from mir import DataEntry, io
        from mir.nn.train import NetworkInterface

        self.cqt, self.DataEntry, self.io = cqt, DataEntry, io
        self.rate, self.hop = DEFAULT_SR, DEFAULT_HOP_LENGTH
        self.decoder = XHMMDecoder(template_file="data/submission_chord_list.txt")
        self.networks = [NetworkInterface(ChordNet(None), name, load_checkpoint=False) for name in MODEL_NAMES]
        self.software = [{"name": name, "version": metadata.version(name)}
                         for name in ("torch", "librosa", "numpy", "scipy")]
        self.software.append({"name": "python", "version": platform.python_version()})

    def decode(self, path: str) -> Decoded:
        entry = self.DataEntry()
        entry.prop.set("sr", self.rate)
        entry.prop.set("hop_length", self.hop)
        entry.append_file(path, self.io.MusicIO, "music")
        entry.append_extractor(self.cqt.CQTV2, "cqt")
        outputs = [network.inference(entry.cqt) for network in self.networks]
        mean = [numpy.mean([output[i] for output in outputs], axis=0) for i in range(len(outputs[0]))]
        labels = self.decoder.decode_to_chordlab(entry, mean, False)
        names, log_probabilities = self.decoder.get_chord_tag_obs(mean)
        return Decoded([(float(a), float(b), str(c)) for a, b, c in labels], list(names), log_probabilities,
                       self.hop / self.rate)


def load_model(repository: Path) -> Model:
    """The pinned ensemble from the repository's checkout. Imports PyTorch,
    so only that repository's environment can run it."""
    repository = repository.resolve()
    check_files(repository)
    try:
        return Jiang(repository)
    except ImportError as exc:
        raise JiangError(f"this interpreter cannot load the recogniser ({exc}); configure jiang-python") from exc


def posteriors(log_probabilities: numpy.ndarray) -> numpy.ndarray:
    """Each frame's softmax over the templates."""
    shifted = log_probabilities - log_probabilities.max(axis=1, keepdims=True)
    found = numpy.exp(shifted)
    found /= found.sum(axis=1, keepdims=True)
    return found


def readings(decoded: Decoded) -> tuple[list[dict[str, Any]], dict[str, Any], float]:
    """The `chords` spans (each valued by its own label's weight, 0 when the
    label is not a template), the `readings` document, and the share of
    spans whose label is among their top three."""
    weights_by_frame = posteriors(decoded.log_probabilities)
    frames = weights_by_frame.shape[0]
    found, read, hits = [], [], 0
    for start, end, label in decoded.labels:
        first = min(max(int(round(start / decoded.frame_seconds)), 0), frames - 1)
        last = min(max(int(round(end / decoded.frame_seconds)), first + 1), frames)
        weights = weights_by_frame[first:last].mean(axis=0)
        top = [[decoded.names[k], round(float(weights[k]), 4)] for k in numpy.argsort(-weights)[:TOP]]
        if label in [name for name, _ in top]:
            hits += 1
        own = float(weights[decoded.names.index(label)]) if label in decoded.names else 0.0
        found.append({"start": start, "end": end, "label": label, "value": round(own, 4)})
        read.append({"start": start, "end": end, "decoded": label, "top": top})
    document = {"frame_s": decoded.frame_seconds, "templates": len(decoded.names), "spans": read}
    return found, document, round(hits / len(found), 4) if found else 0


def write(output: Path, decoded: Decoded, software: list[dict[str, str]]) -> None:
    """The declared outputs and `result.json`."""
    found, document, share = readings(decoded)
    (output / "chords.json").write_text(json.dumps(found))
    (output / "readings.json").write_text(json.dumps(document, separators=(",", ":")))
    result = {"contract": "archive-extractor-result/1", "software": software, "grids": {},
              "outputs": {"chords": {}, "readings": {}, "chord_count": {"value": len(found)},
                          "decoded_in_top3": {"value": share}}}
    (output / "result.json").write_text(json.dumps(result))


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: jiang.py <repository> <request.json>", file=sys.stderr)
        return 2
    request = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    if request.get("parameters") != PARAMETERS:
        print("chords-jiang 1 has fixed settings; the request names others", file=sys.stderr)
        return 1
    try:
        model = load_model(Path(argv[0]))
        decoded = model.decode(request["input"]["path"])
    except JiangError as exc:
        print(f"chords-jiang: {exc}", file=sys.stderr)
        return 1
    write(Path(request["output_dir"]), decoded, model.software)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
