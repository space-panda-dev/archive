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
be the pinned bits, and its Python code that of the pinned commit. The repository's code predates numpy 1.24 and librosa
0.10, so the aliases `np.int`, `np.float`, `np.bool`, and `np.object` are
restored for it and its constant-Q transform is replaced by the same
transform in librosa's current interface; both are named in the parameters.
Only the standard library and numpy are imported until the model is loaded.

Version 2 bounds its memory, which in version 1 grew by about 6 GB an hour
of audio: the networks' first layers hold 16 channels of the whole
spectrogram, and librosa's tuning estimate the whole short-time spectrum. It
reads the audio a block at a time (the same samples `librosa.load` gives),
estimates the tuning a chunk at a time (the same estimate), and computes the
constant-Q spectrogram a chunk at a time on the whole run's frame grid with
that tuning (the same values to float rounding). A recording no longer than
`WINDOW_S` then runs the networks as version 1 did, and gives its outputs.
A longer one cannot: each convolutional layer normalises each channel by
its mean and deviation over everything it is given (instance
normalisation), so a single pass needs the whole recording's activations at
once. Here those statistics are measured over a sample of `WINDOW_S` of the
recording, in blocks spread evenly over it (`statistics`), and the features
are then made a chunk at a time with them: that sample is the one thing
that differs from a single pass, and it bounds the memory. The recurrent
layer runs a chunk at a time in each direction, carrying its state (the
same values), and the decoder over the whole recording, so there are no
seams in the labels; it keeps one row of scores and small back pointers
(`viterbi`) and decides exactly as the repository's `XHMMDecoder.decode`.

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
# The repository's own Python code, every `.py` file in the checkout (67 at
# that commit): the digest of the lines "<sha256 of the file>  <its path>",
# sorted by path (`code_digest`). The commit alone pins nothing, since the
# program runs whatever the checkout holds; an edited, added, or removed
# module would give other outputs under the same key.
CODE = "sha256:0d065fd0b95506a737e9abff161f36596c0d9bb6ad73857a73ba531a0300e14f"
TOP = 3
# A recording longer than this is measured in windows of equal length, none
# longer (see the module's docstring); the frames either side a window's
# features are computed with, and its constant-Q frames; frames per chunk of
# the recurrent layer and of the decoder's observations.
WINDOW_S = 600
HALO = 10
CQT_MARGIN = 128
CHUNK = 8192
BLOCK = 64
TUNING_FFT = 2048  # librosa's estimate_tuning

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
    "windows": f"a recording longer than {WINDOW_S} s: tuning and spectrogram {CHUNK} frames at a time with "
               f"{CQT_MARGIN} frames of audio either side; each convolutional layer normalised per channel by the "
               f"mean and deviation over {WINDOW_S} s of it in blocks of {BLOCK} frames spread evenly, each with "
               f"{HALO} frames either side; features {CHUNK} frames at a time with {HALO} either side; the "
               f"recurrent layer {CHUNK} frames at a time each way, carrying its state; the decoder over the whole",
}


class JiangError(Exception):
    pass


class Decoded:
    """What the model and decoder give: the decoded (start, end, label)
    spans, the decoder's template names, each frame's observation
    log-probability of every template ([frames, templates], an array or
    anything that slices into one by frames, as `Observations` does), and the
    frame's length in seconds."""

    def __init__(self, labels: Sequence[tuple[float, float, str]], names: Sequence[str],
                 log_probabilities: Any, frame_seconds: float):
        self.labels = labels
        self.names = list(names)
        self.log_probabilities = log_probabilities
        self.frame_seconds = frame_seconds


class Observations:
    """The decoder's observation log-probabilities of the frames, computed
    from the networks' mean outputs when a slice of frames is asked for (the
    decoder computes each frame's from that frame's outputs alone), so the
    whole [frames, templates] array is never held."""

    def __init__(self, decoder: Any, outputs: Sequence[numpy.ndarray]):
        self.decoder = decoder
        self.outputs = list(outputs)
        self.shape = (int(self.outputs[0].shape[0]), len(decoder.get_chord_tag_obs([o[:1] for o in self.outputs])[0]))

    def names(self) -> list[str]:
        return list(self.decoder.get_chord_tag_obs([o[:1] for o in self.outputs])[0])

    def __getitem__(self, frames: slice) -> numpy.ndarray:
        return self.decoder.get_chord_tag_obs([o[frames] for o in self.outputs])[1]


def viterbi(observations: Any, penalty: float) -> list[int]:
    """The template index of every frame, decided exactly as the repository's
    `XHMMDecoder.decode` decides it without beats (every frame may change,
    paying `penalty`): the same arithmetic in the same types, with one row
    of scores kept instead of all of them, the back pointers as small
    integers, and the observations read `CHUNK` frames at a time."""
    frames, count = observations.shape
    back = numpy.zeros((frames, count), dtype=numpy.int16 if count < 2 ** 15 else numpy.int32)
    every = numpy.arange(count)
    best = numpy.zeros(frames, dtype=numpy.int64)
    score = None
    for first in range(0, frames, CHUNK):
        rows = observations[first:first + CHUNK]
        for offset in range(rows.shape[0]):
            t = first + offset
            if score is None:
                score = numpy.zeros_like(rows[0])
                score[1:] -= numpy.inf
                score += rows[0]
                back[0, :] = -1
            else:
                same = score
                change = score[best[t - 1]] - penalty
                keep = same > change
                row = numpy.zeros_like(rows[offset])
                row[:] = numpy.maximum(change, same) + rows[offset]
                back[t, :] = best[t - 1]
                back[t, keep] = every[keep]
                score = row
            best[t] = numpy.argmax(score)
    found = [0] * frames
    if frames:
        found[-1] = int(best[-1])
        for t in range(frames - 2, -1, -1):
            found[t] = int(back[t + 1, found[t + 1]])
    return found


def chordlab(tags: Sequence[str], frame_seconds: float) -> list[tuple[float, float, str]]:
    """The repository's `decode_to_chordlab` spans of the frames' labels."""
    found, last = [], 0
    for i in range(len(tags)):
        if i + 1 == len(tags) or tags[i + 1] != tags[i]:
            found.append((float(last * frame_seconds), float((i + 1) * frame_seconds), str(tags[i])))
            last = i + 1
    return found


def windows(frames: int, size: int) -> list[tuple[int, int]]:
    """The cores of equal windows of at most `size` frames tiling `frames`."""
    count = max(1, -(-frames // size))
    edges = [round(k * frames / count) for k in range(count + 1)]
    return list(zip(edges[:-1], edges[1:]))


class Model(Protocol):
    software: list[dict[str, str]]

    def decode(self, path: str) -> Decoded: ...


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 22), b""):
            hasher.update(block)
    return "sha256:" + hasher.hexdigest()


def code_digest(repository: Path) -> str:
    """One digest of every `.py` file in the checkout, outside `.git` and
    `__pycache__`: the lines "<sha256>  <relative path>", sorted by path."""
    found = sorted((path.relative_to(repository).as_posix(), path) for path in repository.rglob("*.py")
                   if not {"__pycache__", ".git"} & set(path.relative_to(repository).parts))
    lines = "".join(f"{sha256_file(path)}  {name}\n" for name, path in found)
    return "sha256:" + hashlib.sha256(lines.encode("utf-8")).hexdigest()


def check_files(repository: Path) -> None:
    """Every pinned file must be the bits its digest names: other weights,
    another chord list, or other code are another method, and must be
    another key."""
    for name, digest in FILES.items():
        path = repository / name
        if not path.is_file():
            raise JiangError(f"{repository} has no {name}; configure jiang-repo as a checkout of {COMMIT}")
        if sha256_file(path) != digest:
            raise JiangError(f"{name} is not the pinned file {digest}")
    if code_digest(repository) != CODE:
        raise JiangError(f"{repository}'s Python code is not that of {COMMIT} ({CODE}); "
                         f"configure jiang-repo as a clean checkout of it")


class Jiang:
    """The repository's ensemble and decoder, imported from its checkout."""

    def __init__(self, repository: Path):
        import importlib.metadata as metadata
        os.chdir(repository)  # the repository opens its models and chord list by relative path
        sys.path.insert(0, str(repository))
        import librosa
        import soundfile
        import soxr
        import torch
        for alias, kind in (("int", int), ("float", float), ("bool", bool), ("object", object)):
            if not hasattr(numpy, alias):
                setattr(numpy, alias, kind)
        torch.set_num_threads(THREADS)
        import chordnet_ismir_naive
        from settings import DEFAULT_HOP_LENGTH, DEFAULT_SR

        def spectrogram(music: numpy.ndarray, rate: int, hop: int, tuning: float) -> numpy.ndarray:
            found = librosa.hybrid_cqt(y=music, sr=rate, bins_per_octave=36,
                                       fmin=librosa.note_to_hz("F#0"), n_bins=288, tuning=tuning,
                                       hop_length=hop).T
            return abs(found).astype(numpy.float32)

        from chord_recognition import MODEL_NAMES
        from chordnet_ismir_naive import ChordNet
        from extractors.xhmm_ismir import XHMMDecoder
        from mir.nn.train import NetworkInterface

        self.librosa, self.soundfile, self.soxr, self.torch = librosa, soundfile, soxr, torch
        self.spectrogram = spectrogram
        self.net = chordnet_ismir_naive
        self.rate, self.hop = DEFAULT_SR, DEFAULT_HOP_LENGTH
        self.decoder = XHMMDecoder(template_file="data/submission_chord_list.txt")
        self.networks = [NetworkInterface(ChordNet(None), name, load_checkpoint=False) for name in MODEL_NAMES]
        self.software = [{"name": name, "version": metadata.version(name)}
                         for name in ("torch", "librosa", "numpy", "scipy", "soundfile", "soxr")]
        self.software.append({"name": "python", "version": platform.python_version()})

    def audio(self, path: str) -> numpy.ndarray:
        """The samples `librosa.load(path, sr=rate, mono=True)` gives, as the
        repository's `MusicIO` reads them, decoded and resampled a block at a
        time (soxr's stream gives the bits of its one-shot resampling). A
        file soundfile cannot read is loaded whole by librosa, as there."""
        librosa, soundfile, soxr = self.librosa, self.soundfile, self.soxr
        try:
            source = soundfile.SoundFile(path)
        except soundfile.SoundFileRuntimeError:
            return librosa.load(path, sr=self.rate, mono=True)[0]
        with source:
            native, total = source.samplerate, source.frames
            # librosa's length: the resampled samples, cut or padded with zeros to it.
            size = total if native == self.rate else int(numpy.ceil(total * (float(self.rate) / native)))
            stream = None if native == self.rate else soxr.ResampleStream(native, self.rate, 1, dtype="float32",
                                                                           quality="soxr_hq")
            music = numpy.zeros(size, dtype=numpy.float32)
            read = written = 0
            while True:
                block = source.read(frames=1 << 18, dtype="float32", always_2d=False).T
                read += block.shape[-1]
                last = block.shape[-1] == 0 or read >= total
                mono = librosa.to_mono(block)
                found = mono if stream is None else stream.resample_chunk(mono, last=last)
                take = max(0, min(len(found), size - written))
                music[written:written + take] = found[:take]
                written += take
                if last:
                    break
        if read != total:
            raise JiangError(f"{path} gave {read} frames where its header says {total}")
        return music

    def decode(self, path: str) -> Decoded: ...


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 22), b""):
            hasher.update(block)
    return "sha256:" + hasher.hexdigest()


def code_digest(repository: Path) -> str:
    """One digest of every `.py` file in the checkout, outside `.git` and
    `__pycache__`: the lines "<sha256>  <relative path>", sorted by path."""
    found = sorted((path.relative_to(repository).as_posix(), path) for path in repository.rglob("*.py")
                   if not {"__pycache__", ".git"} & set(path.relative_to(repository).parts))
    lines = "".join(f"{sha256_file(path)}  {name}\n" for name, path in found)
    return "sha256:" + hashlib.sha256(lines.encode("utf-8")).hexdigest()


def check_files(repository: Path) -> None:
    """Every pinned file must be the bits its digest names: other weights,
    another chord list, or other code are another method, and must be
    another key."""
    for name, digest in FILES.items():
        path = repository / name
        if not path.is_file():
            raise JiangError(f"{repository} has no {name}; configure jiang-repo as a checkout of {COMMIT}")
        if sha256_file(path) != digest:
            raise JiangError(f"{name} is not the pinned file {digest}")
    if code_digest(repository) != CODE:
        raise JiangError(f"{repository}'s Python code is not that of {COMMIT} ({CODE}); "
                         f"configure jiang-repo as a clean checkout of it")


class Jiang:
    """The repository's ensemble and decoder, imported from its checkout."""

    def __init__(self, repository: Path):
        import importlib.metadata as metadata
        os.chdir(repository)  # the repository opens its models and chord list by relative path
        sys.path.insert(0, str(repository))
        import librosa
        import soundfile
        import soxr
        import torch
        for alias, kind in (("int", int), ("float", float), ("bool", bool), ("object", object)):
            if not hasattr(numpy, alias):
                setattr(numpy, alias, kind)
        torch.set_num_threads(THREADS)
        import chordnet_ismir_naive
        from settings import DEFAULT_HOP_LENGTH, DEFAULT_SR

        def spectrogram(music: numpy.ndarray, rate: int, hop: int, tuning: float) -> numpy.ndarray:
            found = librosa.hybrid_cqt(y=music, sr=rate, bins_per_octave=36,
                                       fmin=librosa.note_to_hz("F#0"), n_bins=288, tuning=tuning,
                                       hop_length=hop).T
            return abs(found).astype(numpy.float32)

        from chord_recognition import MODEL_NAMES
        from chordnet_ismir_naive import ChordNet
        from extractors.xhmm_ismir import XHMMDecoder
        from mir.nn.train import NetworkInterface

        self.librosa, self.soundfile, self.soxr, self.torch = librosa, soundfile, soxr, torch
        self.spectrogram = spectrogram
        self.net = chordnet_ismir_naive
        self.rate, self.hop = DEFAULT_SR, DEFAULT_HOP_LENGTH
        self.decoder = XHMMDecoder(template_file="data/submission_chord_list.txt")
        self.networks = [NetworkInterface(ChordNet(None), name, load_checkpoint=False) for name in MODEL_NAMES]
        self.software = [{"name": name, "version": metadata.version(name)}
                         for name in ("torch", "librosa", "numpy", "scipy", "soundfile", "soxr")]
        self.software.append({"name": "python", "version": platform.python_version()})

    def audio(self, path: str) -> numpy.ndarray:
        """The samples `librosa.load(path, sr=rate, mono=True)` gives, as the
        repository's `MusicIO` reads them, decoded and resampled a block at a
        time (soxr's stream gives the bits of its one-shot resampling). A
        file soundfile cannot read is loaded whole by librosa, as there."""
        librosa, soundfile, soxr = self.librosa, self.soundfile, self.soxr
        try:
            source = soundfile.SoundFile(path)
        except soundfile.SoundFileRuntimeError:
            return librosa.load(path, sr=self.rate, mono=True)[0]
        with source:
            native, total = source.samplerate, source.frames
            stream = None if native == self.rate else soxr.ResampleStream(native, self.rate, 1, dtype="float32",
                                                                           quality="soxr_hq")
            found, read = [], 0
            while True:
                block = source.read(frames=1 << 18, dtype="float32", always_2d=False).T
                read += block.shape[-1]
                mono = librosa.to_mono(block)
                last = block.shape[-1] == 0 or read >= total
                found.append(mono if stream is None else stream.resample_chunk(mono, last=last))
                if last:
                    break
        music = numpy.concatenate(found) if found else numpy.zeros(0, dtype=numpy.float32)
        if stream is not None:
            music = librosa.util.fix_length(music, size=int(numpy.ceil(read * (float(self.rate) / native))))
        return numpy.ascontiguousarray(music, dtype=numpy.float32)

    def decode(self, path: str) -> Decoded:
        music = self.audio(path)
        frames = 1 + len(music) // self.hop
        size = int(round(WINDOW_S * self.rate / self.hop))
        if frames <= size:
            outputs = self.whole(music)
        else:
            spectrum = self.spectra(music, frames)
            del music
            outputs = self.windowed(spectrum, size)
        observations = Observations(self.decoder, outputs)
        names = observations.names()
        tags = [names[k] for k in viterbi(observations, self.decoder.diff_trans_penalty)]
        return Decoded(chordlab(tags, self.hop / self.rate), names, observations, self.hop / self.rate)

    def whole(self, music: numpy.ndarray) -> list[numpy.ndarray]:
        """Version 1's pass over a recording no longer than a window: the
        networks over its whole spectrogram, and the mean of their outputs."""
        spectrum = self.spectrogram(music, self.rate, self.hop, self.tuning(music))
        outputs = [network.inference(spectrum) for network in self.networks]
        return [numpy.mean([output[i] for output in outputs], axis=0) for i in range(len(outputs[0]))]

    def tuning(self, music: numpy.ndarray) -> float:
        """librosa's `estimate_tuning` of the whole recording, which
        `hybrid_cqt` makes with `tuning=None`, computed `CHUNK` frames at a
        time: its peaks (`piptrack`) are each frame's own, and the median
        and the histogram of the whole set do not depend on their order."""
        librosa, step = self.librosa, TUNING_FFT // 4
        frames = 1 + len(music) // step
        pitches, magnitudes = [], []
        for first, last in windows(frames, CHUNK):
            begin = max(0, (first - CQT_MARGIN) * step)
            end = min(len(music), (last + CQT_MARGIN) * step)
            pitch, magnitude = librosa.piptrack(y=music[begin:end], sr=self.rate, n_fft=TUNING_FFT)
            offset = first - begin // step
            pitch, magnitude = pitch[:, offset:offset + last - first], magnitude[:, offset:offset + last - first]
            peaks = pitch > 0
            pitches.append(pitch[peaks])
            magnitudes.append(magnitude[peaks])
        pitch, magnitude = numpy.concatenate(pitches), numpy.concatenate(magnitudes)
        threshold = numpy.median(magnitude) if len(magnitude) else 0.0
        return float(librosa.pitch_tuning(pitch[magnitude >= threshold], resolution=0.01, bins_per_octave=36))

    def spectra(self, music: numpy.ndarray, frames: int) -> numpy.ndarray:
        """The network's band of the spectrogram of a recording longer than a
        window, computed `CHUNK` frames at a time on the whole run's frame
        grid with the whole recording's tuning."""
        low = self.net.SHIFT_HIGH * self.net.SHIFT_STEP
        tuning = self.tuning(music)
        spectrum = numpy.empty((frames, self.net.SPEC_DIM), dtype=numpy.float32)
        for first, last in windows(frames, CHUNK):
            begin = max(0, (first - CQT_MARGIN) * self.hop)
            end = min(len(music), (last + CQT_MARGIN) * self.hop)
            part = self.spectrogram(music[begin:end], self.rate, self.hop, tuning)
            offset = first - begin // self.hop
            spectrum[first:last] = part[offset:offset + last - first, low:low + self.net.SPEC_DIM]
        return spectrum

    def windowed(self, spectrum: numpy.ndarray, size: int) -> list[numpy.ndarray]:
        """The mean of the networks' outputs over a recording longer than a
        window: each network's features window by window, then its recurrent
        layer and output layer over the whole a chunk at a time."""
        total: list[numpy.ndarray] | None = None
        for network in self.networks:
            heads = self.heads(network.net, spectrum, size)
            if total is None:
                total = heads
            else:
                for mine, theirs in zip(total, heads):
                    mine += theirs
        assert total is not None
        return [numpy.true_divide(head, len(self.networks)).astype(numpy.float32) for head in total]

    def heads(self, net: Any, spectrum: numpy.ndarray, size: int) -> list[numpy.ndarray]:
        """One network's six softmax outputs over every frame, as
        `ChordNet.inference` gives them, except that each channel of its
        convolutional layers is normalised by the statistics of a sample of
        the recording (`statistics`) rather than of all of it; its features
        are made `CHUNK` frames at a time, and its bidirectional recurrent
        layer runs `CHUNK` frames at a time in each direction, carrying its
        state."""
        torch, frames = self.torch, spectrum.shape[0]
        features = numpy.empty((frames, net.audio_feature_block.output_size), dtype=numpy.float32)
        with torch.no_grad():
            block = net.audio_feature_block
            statistics = self.statistics(block, spectrum, size)
            for first, last in windows(frames, CHUNK):
                begin, end = max(0, first - HALO), min(frames, last + HALO)
                part = torch.from_numpy(spectrum[begin:end]).view((1, 1, end - begin, self.net.SPEC_DIM))
                features[first:last] = self.convolve(block, part, statistics)[0, first - begin:last - begin].numpy()
            forward, backward = self.directions(net.lstm1)
            hidden = net.hidden_dim1 // 2
            recurrent = numpy.empty((frames, 2 * hidden), dtype=numpy.float32)
            state = None
            for first in range(0, frames, CHUNK):
                part = torch.from_numpy(features[first:first + CHUNK])[None]
                out, state = forward(part, state)
                recurrent[first:first + CHUNK, :hidden] = out[0].numpy()
            state = None
            for last in range(frames, 0, -CHUNK):
                first = max(0, last - CHUNK)
                part = torch.flip(torch.from_numpy(features[first:last]), dims=[0])[None]
                out, state = backward(part, state)
                recurrent[first:last, hidden:] = torch.flip(out[0], dims=[0]).numpy()
            del features
            limit = self.net.chord_limit
            edges = [0, limit.bass_slice_begin, limit.bass_slice_begin + 12 + 1]
            for extra in (limit.seventh_limit, limit.ninth_limit, limit.eleventh_limit, limit.thirteenth_limit):
                edges.append(edges[-1] + extra + 1)
            heads = [numpy.empty((frames, b - a), dtype=numpy.float32) for a, b in zip(edges[:-1], edges[1:])]
            for first in range(0, frames, CHUNK):
                out = net.final_fc1(torch.from_numpy(recurrent[first:first + CHUNK]))
                for head, a, b in zip(heads, edges[:-1], edges[1:]):
                    head[first:first + CHUNK] = torch.nn.functional.softmax(out[:, a:b], dim=1).numpy()
        return heads

    STEPS = ("1a", "1b", "1c", "pool1", "2a", "2b", "2c", "pool2", "3a", "3b", "pool3", "4a", "4b")

    def convolve(self, block: Any, x: Any, statistics: list[tuple[Any, Any]] | None,
                 found: list[tuple[Any, Any]] | None = None) -> Any:
        """The repository's `CNNFeatureExtractor.forward` over `x` ([blocks,
        1, frames, bins]), each instance normalisation replaced by the given
        per-channel mean and standard deviation; with `found`, those of each
        layer are measured over the blocks' cores (all but `HALO` frames at
        either end), appended to it, and used. Returns [blocks, frames,
        features]."""
        torch = self.torch
        layer = 0
        for step in self.STEPS:
            if step.startswith("pool"):
                x = getattr(block, step)(x)
                continue
            x = getattr(block, "conv" + step)(x)
            if found is not None:
                core = x[:, :, HALO:x.shape[2] - HALO, :]
                total = torch.zeros(x.shape[1], dtype=torch.float64)
                squares = torch.zeros(x.shape[1], dtype=torch.float64)
                for one in core:
                    one = one.double()
                    total += one.sum(dim=(1, 2))
                    squares += (one * one).sum(dim=(1, 2))
                count = core.shape[0] * core.shape[2] * core.shape[3]
                mean = total / count
                variance = torch.clamp(squares / count - mean * mean, min=0.0)
                eps = getattr(block, "norm" + step).eps
                found.append((mean.float().view(1, -1, 1, 1), torch.sqrt(variance + eps).float().view(1, -1, 1, 1)))
                mean, deviation = found[-1]
            else:
                assert statistics is not None
                mean, deviation = statistics[layer]
            x = torch.nn.functional.selu(x.sub_(mean).div_(deviation), inplace=True)
            layer += 1
        return x.transpose(1, 2).contiguous().view((x.shape[0], x.shape[2], block.output_size))

    def statistics(self, block: Any, spectrum: numpy.ndarray, size: int) -> list[tuple[Any, Any]]:
        """Each convolutional layer's per-channel mean and standard deviation
        over a sample of the recording: `size // BLOCK` blocks of `BLOCK`
        frames spread evenly over it, each run with `HALO` frames either side
        so its core is what a run over the whole would give from the same
        statistics. They stand for the recording's own, which a single pass
        normalises by; the sample is held at once, so its size bounds the
        memory."""
        torch, frames = self.torch, spectrum.shape[0]
        count = max(1, size // BLOCK)
        starts = [min(max(int(round((k + 0.5) * frames / count - BLOCK / 2)), HALO), frames - BLOCK - HALO)
                  for k in range(count)]
        sample = numpy.stack([spectrum[start - HALO:start + BLOCK + HALO] for start in starts])
        found: list[tuple[Any, Any]] = []
        self.convolve(block, torch.from_numpy(sample)[:, None], None, found)
        return found

    def directions(self, lstm: Any) -> tuple[Any, Any]:
        """The bidirectional layer's two directions as one-way layers with its
        weights, so each can run a chunk at a time."""
        torch = self.torch
        made = []
        for suffix in ("", "_reverse"):
            one = torch.nn.LSTM(input_size=lstm.input_size, hidden_size=lstm.hidden_size, num_layers=1,
                                batch_first=True)
            for name in ("weight_ih_l0", "weight_hh_l0", "bias_ih_l0", "bias_hh_l0"):
                getattr(one, name).data.copy_(getattr(lstm, name + suffix).data)
            one.eval()
            made.append(one)
        return made[0], made[1]


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


def mean_posteriors(log_probabilities: Any, first: int, last: int) -> numpy.ndarray:
    """The mean over frames `first` to `last` of each frame's posteriors,
    read `CHUNK` frames at a time. The frames are summed in order, each
    chunk onto the sum so far, as numpy's mean sums the rows of the whole
    span, so the weights are the same bits as from the whole array."""
    total = None
    for begin in range(first, last, CHUNK):
        rows = posteriors(log_probabilities[begin:min(begin + CHUNK, last)])
        total = rows.sum(axis=0) if total is None else numpy.concatenate([total[None, :], rows]).sum(axis=0)
    return numpy.true_divide(total, last - first)


def readings(decoded: Decoded) -> tuple[list[dict[str, Any]], dict[str, Any], float]:
    """The `chords` spans (each valued by its own label's weight, 0 when the
    label is not a template), the `readings` document, and the share of
    spans whose label is among their top three."""
    frames = decoded.log_probabilities.shape[0]
    found, read, hits = [], [], 0
    for start, end, label in decoded.labels:
        first = min(max(int(round(start / decoded.frame_seconds)), 0), frames - 1)
        last = min(max(int(round(end / decoded.frame_seconds)), first + 1), frames)
        weights = mean_posteriors(decoded.log_probabilities, first, last)
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
