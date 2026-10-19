#!/usr/bin/env python3
"""The stems extractor: Demucs source separation, in Demucs's own environment.

The runner starts this program with the interpreter the operator configured
as `demucs-python` (ADR 0074), never its own, so the archive's environment
never imports PyTorch. It needs only numpy and `ffmpeg` until the model is
loaded; `torch`, `demucs`, `huggingface_hub`, and `yaml` are imported by
`load_model` alone.

It decodes the content's first audio stream once, as 44,100 Hz stereo
float32 (htdemucs's rate and channels), separates it a chunk at a time with
the pinned htdemucs weights, joins the chunks with a linear crossfade, and
writes, for each of vocals, drums, bass, and other:

- the stem as 16-bit FLAC, quantised here (round to nearest, clamped), so the
  file's bits do not depend on ffmpeg's sample-format conversion;
- its level summaries, measured on the float stem before quantisation:
  whole-stem RMS and peak in dBFS, the number of samples the 16-bit file had
  to clamp, and a level series every 4,096 samples (about 10.8 rows a second,
  the same instants as the `frames` grid).

Chunks bound memory: htdemucs holds a whole input and four outputs as float32
tensors, about 6 GB an hour, so a three-hour recording would not fit a 16 GB
machine. Each chunk is normalised as Demucs's own API normalises a whole
track, so the chunked result is close to, but not the bits of, one whole-file
run. With `shifts` 0 Demucs applies no random offset, so a run on one build
gives the same samples each time.

Invoked by the runner as `<demucs-python> stems.py <request.json>`; the
settings are fixed but for the device the model runs on (`apply.device`,
one of DEVICES: a GPU's stems are other bits, so another key), and a request
naming others is refused.
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable, Iterator, Protocol

import numpy

RATE = 44100
CHANNELS = 2
STEMS = ("vocals", "drums", "bass", "other")
CHUNK_SECONDS = 600
CROSSFADE_SECONDS = 10
SCALE = 32768
LEVEL_HOP = 4096
LEVEL_FRAME = 2 * LEVEL_HOP
FLOOR_DB = -120
BLOCK_BYTES = 1 << 22
MEASURE_BLOCK = 1 << 18  # samples per channel measured and encoded at once

MODEL = {"name": "htdemucs", "demucs": "4.1.0", "repository": "adefossez/HTDemucs",
         "revision": "cbc8a9b1a87023b7fd74e7b3412e6321c0eab003",
         "files": {"htdemucs.yaml": "sha256:239c445d0b14454d541ad8bd9bb271c9e536d267e8a4625208744cbb2e7bb66c",
                   "955717e8.safetensors":
                       "sha256:d9fa14133cfcc034a6758923bb3a8ca9f8dfd0b582134643bbf83f72c17576dd"}}
APPLY = {"shifts": 0, "split": True, "overlap": "0.25", "segment": None, "device": "cpu",
         "normalise": "chunk mean and standard deviation, as demucs.api.Separator.separate_tensor"}
# The devices the model may run on. The device is a parameter, so stems made
# on a GPU are another key beside the CPU's: the same weights give other bits
# on other hardware. "cpu" is the manifest's; an operator who has Apple's
# Metal configures {"stems": {"apply": {..., "device": "mps"}}}.
DEVICES = ("cpu", "mps")


class StemsError(Exception):
    pass


def decode_arguments(path: str) -> list[str]:
    """The ffmpeg arguments after the program name: the first audio stream,
    as interleaved 44,100 Hz stereo float32 (a mono stream is duplicated)."""
    return ["-nostdin", "-hide_banner", "-nostats", "-loglevel", "error", "-i", path, "-map", "0:a:0",
            "-ac", str(CHANNELS), "-ar", str(RATE), "-c:a", "pcm_f32le", "-f", "f32le", "-"]


def encode_arguments(path: str) -> list[str]:
    """The ffmpeg arguments that write 16-bit samples as FLAC. Bit-exact
    flags keep ffmpeg's version out of the file, so one build of the model
    and one FLAC encoder give one digest."""
    return ["-nostdin", "-hide_banner", "-nostats", "-loglevel", "error", "-f", "s16le", "-ar", str(RATE),
            "-ac", str(CHANNELS), "-i", "-", "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:a",
            "+bitexact", "-c:a", "flac", "-compression_level", "5", "-f", "flac", path]


def apply_on(device: str) -> dict[str, Any]:
    """Demucs's application settings, on one of DEVICES."""
    return {**APPLY, "device": device}


def parameters(device: str = "cpu") -> dict[str, Any]:
    """The settings, which the manifest must declare exactly, on the CPU;
    on another of DEVICES they differ only in `apply.device`."""
    return {"decoder": "ffmpeg", "decode_arguments": decode_arguments("{input}"), "model": MODEL,
            "apply": apply_on(device),
            "chunk_seconds": CHUNK_SECONDS, "crossfade_seconds": CROSSFADE_SECONDS, "stems": list(STEMS),
            "encoding": {"encoder": "ffmpeg", "encode_arguments": encode_arguments("{output}"), "bits": 16,
                         "scale": SCALE, "rounding": "nearest, ties to even", "overflow": "clamp"},
            "levels": {"hop": LEVEL_HOP, "frame": LEVEL_FRAME, "floor_db": FLOOR_DB,
                       "measure": "RMS over both channels of the float stem, zero outside it"}}


# -- the model ------------------------------------------------------------------

class Model(Protocol):
    """A separator: `separate` takes [channels, samples] float32 and returns
    [len(sources), channels, samples] float32, in `sources` order."""
    sources: list[str]
    samplerate: int
    channels: int
    software: list[dict[str, str]]

    def separate(self, mix: numpy.ndarray) -> numpy.ndarray: ...


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 22), b""):
            hasher.update(block)
    return "sha256:" + hasher.hexdigest()


def check_files(paths: dict[str, Path], pinned: dict[str, str]) -> None:
    """Every pinned model file must be the bits its digest names: another
    revision of the weights is another method, and must be another key."""
    for name, digest in pinned.items():
        if name not in paths:
            raise StemsError(f"the model file {name} was not found")
        actual = sha256_file(paths[name])
        if actual != digest:
            raise StemsError(f"the model file {name} is {actual}, not the pinned {digest}")


class Demucs:
    """htdemucs through Demucs's Python interface, with its weights loaded
    from the pinned revision and checked against their digests."""

    def __init__(self, settings: dict[str, Any], apply: dict[str, Any]):
        import demucs
        import torch
        import yaml
        from demucs.apply import BagOfModels, apply_model
        from demucs.hf import load_safetensors_model
        from huggingface_hub import hf_hub_download

        if demucs.__version__ != settings["demucs"]:
            raise StemsError(f"demucs is {demucs.__version__}, not the pinned {settings['demucs']}")
        paths = {name: Path(hf_hub_download(settings["repository"], name, revision=settings["revision"]))
                 for name in settings["files"]}
        check_files(paths, settings["files"])
        bag = yaml.safe_load(paths[f"{settings['name']}.yaml"].read_text())
        models = []
        for signature in bag["models"]:
            if f"{signature}.safetensors" not in paths:
                raise StemsError(f"{settings['name']} names the model {signature}, which is not pinned")
            models.append(load_safetensors_model(paths[f"{signature}.safetensors"]))
        self.model = BagOfModels(models, bag.get("weights"), bag.get("segment"))
        self.model.eval()
        self.torch, self.apply_model, self.apply = torch, apply_model, apply
        self.sources = list(self.model.sources)
        self.samplerate = int(self.model.samplerate)
        self.channels = int(self.model.audio_channels)
        self.software = [{"name": "demucs", "version": demucs.__version__},
                         {"name": "torch", "version": torch.__version__},
                         {"name": settings["name"], "version": f"{settings['repository']}@{settings['revision']}"}]

    def separate(self, mix: numpy.ndarray) -> numpy.ndarray:
        torch = self.torch
        with torch.no_grad():
            wav = torch.from_numpy(numpy.ascontiguousarray(mix, dtype=numpy.float32))
            reference = wav.mean(0)
            mean = reference.mean()
            std = reference.std() + 1e-8
            out = self.apply_model(self.model, ((wav - mean) / std)[None], shifts=self.apply["shifts"],
                                   split=self.apply["split"], overlap=float(self.apply["overlap"]),
                                   segment=self.apply["segment"], device=self.apply["device"], progress=False)
            out = out * std + mean
        return out[0].cpu().numpy()


def load_model(settings: dict[str, Any], apply: dict[str, Any]) -> Model:
    """The pinned model. Imports PyTorch, so only Demucs's environment can run it."""
    try:
        return Demucs(settings, apply)
    except ImportError as exc:
        raise StemsError(f"this interpreter cannot load Demucs ({exc}); configure demucs-python") from exc


# -- decoding, chunking, and joining ----------------------------------------------

def decoded_blocks(path: str, ffmpeg: str = "ffmpeg", block_bytes: int = BLOCK_BYTES) -> Iterator[numpy.ndarray]:
    """The first audio stream as [channels, n] float32 blocks, in order."""
    frame_bytes = 4 * CHANNELS
    with tempfile.TemporaryFile() as errors:
        try:
            process = subprocess.Popen([ffmpeg, *decode_arguments(path)], stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=errors)
        except OSError as exc:
            raise StemsError(f"ffmpeg could not run: {exc}") from exc
        assert process.stdout is not None
        carry = b""
        try:
            while chunk := process.stdout.read(block_bytes):
                chunk = carry + chunk
                usable = len(chunk) - len(chunk) % frame_bytes
                carry = chunk[usable:]
                if usable:
                    yield numpy.frombuffer(chunk[:usable], dtype="<f4").reshape(-1, CHANNELS).T
        finally:
            process.stdout.close()
            returncode = process.wait()
        if returncode != 0 or carry:
            errors.seek(0)
            raise StemsError(f"ffmpeg could not decode {path}: "
                             f"{errors.read().decode('utf-8', 'replace').strip()[:300]}")


def windows(blocks: Iterable[numpy.ndarray], chunk: int, overlap: int) -> Iterator[tuple[numpy.ndarray, bool]]:
    """Consecutive windows of `chunk + overlap` samples starting every
    `chunk`, so each overlaps the next by `overlap`; the last holds the rest
    and is marked last. Every window after the first is longer than
    `overlap`, since a window is not last only when more than
    `chunk + overlap` samples remain."""
    if chunk < 1 or overlap < 0:
        raise StemsError("a chunk must be positive and an overlap not negative")
    pieces: list[numpy.ndarray] = []
    held = 0
    for block in blocks:
        pieces.append(block)
        held += block.shape[1]
        if held > chunk + overlap:
            buffer = numpy.concatenate(pieces, axis=1)  # once per window, not once per block
            while buffer.shape[1] > chunk + overlap:
                yield buffer[:, :chunk + overlap], False
                buffer = buffer[:, chunk:]
            pieces, held = [buffer], buffer.shape[1]
    if held:
        yield numpy.concatenate(pieces, axis=1), True


class Joiner:
    """Joins separated windows into one signal per source: where two windows
    overlap, the earlier fades out and the later fades in, linearly, so a
    source that is the same in both comes through unchanged."""

    def __init__(self, overlap: int):
        self.overlap = overlap
        self.tail: numpy.ndarray | None = None
        # The weight of the later window at each sample of the overlap.
        self.ramp = ((numpy.arange(overlap, dtype=numpy.float64) + 0.5) / overlap).astype(numpy.float32)

    def add(self, separated: numpy.ndarray, last: bool) -> Iterator[numpy.ndarray]:
        """The samples now final, as [sources, channels, n] blocks."""
        rest = separated
        if self.tail is not None:
            head = separated[..., :self.overlap]
            yield self.tail * (1 - self.ramp) + head * self.ramp
            rest = separated[..., self.overlap:]
            self.tail = None
        if last or self.overlap == 0:
            yield rest
        else:
            yield rest[..., :rest.shape[-1] - self.overlap]
            self.tail = numpy.array(rest[..., rest.shape[-1] - self.overlap:])


# -- measuring and writing -----------------------------------------------------------

def decibels(power: float) -> float:
    """A mean square as dBFS, floored."""
    return max(10 * float(numpy.log10(power)), FLOOR_DB) if power > 0 else float(FLOOR_DB)


class Levels:
    """Per-source energy in hops of LEVEL_HOP samples, summed over channels,
    and the whole-signal measures, accumulated block by block."""

    def __init__(self, sources: int):
        self.sources = sources
        self.hops: list[numpy.ndarray] = []
        self.partial = numpy.zeros(sources, dtype=numpy.float64)
        self.filled = 0
        self.total = numpy.zeros(sources, dtype=numpy.float64)
        self.peak = numpy.zeros(sources, dtype=numpy.float64)
        self.samples = 0

    def add(self, block: numpy.ndarray) -> None:
        squares = numpy.square(block, dtype=numpy.float64).sum(axis=1)  # [sources, n]
        if squares.shape[1] == 0:
            return
        self.total += squares.sum(axis=1)
        self.peak = numpy.maximum(self.peak, numpy.abs(block).max(axis=(1, 2)))
        self.samples += squares.shape[1]
        position = 0
        if self.filled:
            take = min(LEVEL_HOP - self.filled, squares.shape[1])
            self.partial += squares[:, :take].sum(axis=1)
            self.filled += take
            position = take
            if self.filled == LEVEL_HOP:
                self.hops.append(self.partial)
                self.partial, self.filled = numpy.zeros(self.sources, dtype=numpy.float64), 0
        full = (squares.shape[1] - position) // LEVEL_HOP
        if full:
            whole = squares[:, position:position + full * LEVEL_HOP].reshape(self.sources, full, LEVEL_HOP)
            self.hops.extend(whole.sum(axis=2).T)
            position += full * LEVEL_HOP
        if position < squares.shape[1]:
            self.partial = self.partial + squares[:, position:].sum(axis=1)
            self.filled += squares.shape[1] - position

    def series(self) -> numpy.ndarray:
        """Row k: the RMS of the LEVEL_FRAME samples centred on sample
        k x LEVEL_HOP (hops k - 1 and k), zero outside the signal, in dBFS;
        n samples make ceil(n / LEVEL_HOP) + 1 rows."""
        hops = list(self.hops) + ([self.partial] if self.filled else [])
        energy = numpy.zeros((len(hops) + 1, self.sources), dtype=numpy.float64)
        if hops:
            stacked = numpy.array(hops)
            energy[:-1] += stacked
            energy[1:] += stacked
        power = energy / (LEVEL_FRAME * CHANNELS)
        with numpy.errstate(divide="ignore"):
            levels = numpy.where(power > 0, 10 * numpy.log10(numpy.where(power > 0, power, 1)), FLOOR_DB)
        return numpy.maximum(levels, FLOOR_DB).astype("<f4")

    def rms_db(self, source: int) -> float:
        return decibels(self.total[source] / (self.samples * CHANNELS)) if self.samples else float(FLOOR_DB)

    def peak_db(self, source: int) -> float:
        return decibels(self.peak[source] ** 2)


def quantise(block: numpy.ndarray) -> tuple[bytes, int]:
    """[channels, n] float samples as interleaved 16-bit little-endian, and
    how many samples had to be clamped to fit."""
    scaled = numpy.rint(block.astype(numpy.float64) * SCALE)
    clamped = int(numpy.count_nonzero((scaled > SCALE - 1) | (scaled < -SCALE)))
    return numpy.clip(scaled, -SCALE, SCALE - 1).astype("<i2").T.tobytes(), clamped


class Encoder:
    """One ffmpeg writing one stem's FLAC from 16-bit samples on its input."""

    def __init__(self, path: Path, ffmpeg: str = "ffmpeg"):
        self.path = path
        self.errors = tempfile.TemporaryFile()
        try:
            self.process = subprocess.Popen([ffmpeg, *encode_arguments(str(path))], stdin=subprocess.PIPE,
                                            stdout=subprocess.DEVNULL, stderr=self.errors)
        except OSError as exc:
            self.errors.close()
            raise StemsError(f"ffmpeg could not run: {exc}") from exc
        self.clamped = 0

    def write(self, block: numpy.ndarray) -> None:
        data, clamped = quantise(block)
        self.clamped += clamped
        assert self.process.stdin is not None
        try:
            self.process.stdin.write(data)
        except BrokenPipeError:
            raise StemsError(f"ffmpeg stopped encoding {self.path.name}: {self.error()}") from None

    def error(self) -> str:
        self.process.wait()
        self.errors.seek(0)
        return self.errors.read().decode("utf-8", "replace").strip()[:300]

    def close(self) -> None:
        assert self.process.stdin is not None
        try:
            self.process.stdin.close()
        except BrokenPipeError:
            pass
        if self.process.wait() != 0:
            raise StemsError(f"ffmpeg could not encode {self.path.name}: {self.error()}")
        self.errors.close()

    def kill(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait()
        try:
            self.process.stdin.close()  # type: ignore[union-attr]
        except BrokenPipeError:
            pass
        self.errors.close()


def ffmpeg_version(ffmpeg: str = "ffmpeg") -> str:
    first = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True).stdout.split("\n", 1)[0].split()
    return first[2] if len(first) > 2 and first[:2] == ["ffmpeg", "version"] else "unknown"


def separate(input_path: str, output: Path, model: Model, chunk: int = CHUNK_SECONDS * RATE,
             overlap: int = CROSSFADE_SECONDS * RATE) -> dict[str, Any]:
    """Decode, separate, join, measure, and encode; returns the result's
    outputs. `chunk` and `overlap` are in samples; the parameters fix them,
    and only a test passes others, to join many chunks of a short input."""
    if model.samplerate != RATE or model.channels != CHANNELS:
        raise StemsError(f"the model separates {model.channels} channels at {model.samplerate} Hz, "
                         f"not {CHANNELS} at {RATE}")
    missing = [stem for stem in STEMS if stem not in model.sources]
    if missing:
        raise StemsError(f"the model has no {', '.join(missing)} source")
    order = [model.sources.index(stem) for stem in STEMS]
    encoders: list[Encoder] = []
    try:
        for stem in STEMS:
            encoders.append(Encoder(output / f"{stem}.flac"))
        levels = Levels(len(STEMS))
        mix = Levels(1)
        joiner = Joiner(overlap)

        def counted(blocks: Iterable[numpy.ndarray]) -> Iterator[numpy.ndarray]:
            for block in blocks:
                mix.add(block[None])
                yield block

        for window, last in windows(counted(decoded_blocks(input_path)), chunk, overlap):
            separated = numpy.asarray(model.separate(window))
            if separated.shape != (len(model.sources), CHANNELS, window.shape[1]):
                raise StemsError(f"the model returned shape {separated.shape} for "
                                 f"{window.shape[1]} samples of {CHANNELS} channels")
            if not numpy.isfinite(separated).all():
                raise StemsError("the model returned a value that is not finite")
            for joined in joiner.add(separated.astype(numpy.float32, copy=False), last):
                # A block at a time, so the reordered copy, the float64
                # squares, and the scaled samples never span a whole window.
                for start in range(0, joined.shape[-1], MEASURE_BLOCK):
                    block = joined[order, :, start:start + MEASURE_BLOCK]
                    levels.add(block)
                    for encoder, stem_block in zip(encoders, block):
                        encoder.write(stem_block)
        for encoder in encoders:
            encoder.close()
    except BaseException:
        for encoder in encoders:
            encoder.kill()
        raise
    if mix.samples == 0:
        raise StemsError(f"{input_path} decodes to no samples")
    if levels.samples != mix.samples:
        raise StemsError(f"{mix.samples} samples were decoded and {levels.samples} separated")
    series = levels.series()
    rows = -(-mix.samples // LEVEL_HOP) + 1
    if series.shape != (rows, len(STEMS)):
        raise StemsError(f"{mix.samples} samples made {series.shape[0]} level rows, not {rows}")
    (output / "levels.f32").write_bytes(series.tobytes())
    outputs: dict[str, Any] = {stem: {} for stem in STEMS}
    outputs["levels"] = {"shape": [rows, len(STEMS)]}
    for index, stem in enumerate(STEMS):
        outputs[f"{stem}_rms_db"] = {"value": round(levels.rms_db(index), 3)}
        outputs[f"{stem}_peak_db"] = {"value": round(levels.peak_db(index), 3)}
        outputs[f"{stem}_clamped"] = {"value": encoders[index].clamped}
    outputs["mix_rms_db"] = {"value": round(mix.rms_db(0), 3)}
    outputs["samples"] = {"value": mix.samples}
    outputs["seconds"] = {"value": mix.samples / RATE}
    return outputs


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: stems.py <request.json>", file=sys.stderr)
        return 2
    try:
        request = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
        device = next((d for d in DEVICES if request.get("parameters") == parameters(d)), None)
        if device is None:
            raise StemsError("stems 1 has fixed settings but for the device "
                             f"({', '.join(DEVICES)}); the request names others")
        output = Path(request["output_dir"])
        model = load_model(MODEL, apply_on(device))
        outputs = separate(request["input"]["path"], output, model)
        software = [*model.software, {"name": "ffmpeg", "version": ffmpeg_version()},
                    {"name": "numpy", "version": numpy.__version__},
                    {"name": "python", "version": platform.python_version()}]
        result = {"contract": "archive-extractor-result/1", "software": software,
                  "grids": {"stems": {"sample_rate": RATE, "hop": LEVEL_HOP, "frame": LEVEL_FRAME,
                                      "first_centre": 0}},
                  "outputs": outputs}
        (output / "result.json").write_text(json.dumps(result))
    except (StemsError, OSError, KeyError, ValueError) as exc:
        print(f"stems: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
