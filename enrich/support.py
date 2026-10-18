"""Test support: synthetic audio and scratch archives built by the real writers.

Audio comes from ffmpeg's lavfi sources or Python's `wave` writer; archives
come from `archive init` and the fixture builder, which admits every record
through Door. Nothing here writes an archive record itself.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from typing import Any

import cover  # noqa: F401  first: it pins BLAS to one thread before numpy loads it
import numpy as np  # noqa: E402

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parent
DOOR = os.environ.get("ARCHIVE_TEST_DOOR")
ARCHIVE = os.environ.get("ARCHIVE_TEST_ARCHIVE")
FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
if os.environ.get("ARCHIVE_REQUIRE_INTEGRATION"):
    if not (DOOR and ARCHIVE):
        raise RuntimeError("ARCHIVE_REQUIRE_INTEGRATION is set but ARCHIVE_TEST_DOOR or ARCHIVE_TEST_ARCHIVE is not")
    if not (FFMPEG and FFPROBE):
        raise RuntimeError("ARCHIVE_REQUIRE_INTEGRATION is set but ffmpeg or ffprobe is not on PATH")
needs_ffmpeg = unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg and ffprobe are needed")
needs_door = unittest.skipUnless(DOOR and ARCHIVE and FFMPEG and FFPROBE,
                                 "set ARCHIVE_TEST_DOOR and ARCHIVE_TEST_ARCHIVE and install ffmpeg")

GIT_IDENTITY = {"GIT_AUTHOR_NAME": "Enrich tests", "GIT_AUTHOR_EMAIL": "enrich-tests@example.invalid",
                "GIT_COMMITTER_NAME": "Enrich tests", "GIT_COMMITTER_EMAIL": "enrich-tests@example.invalid"}


def environment() -> dict[str, str]:
    env = dict(os.environ)
    for name, value in GIT_IDENTITY.items():
        env.setdefault(name, value)
    for variable, program in (("ARCHIVE_GIT_EXECUTABLE", "git"), ("ARCHIVE_GIT_ANNEX_EXECUTABLE", "git-annex")):
        if variable not in env and shutil.which(program):
            env[variable] = os.path.realpath(shutil.which(program))
    return env


def scratch(test: unittest.TestCase, prefix: str) -> Path:
    # Short, under /tmp: git-annex keeps sockets in the repositories.
    path = Path(tempfile.mkdtemp(prefix=prefix, dir="/tmp"))
    test.addCleanup(remove_tree, path)
    return path


def remove_tree(path: Path) -> None:
    for directory, _, _ in os.walk(path):
        os.chmod(directory, 0o700)
    shutil.rmtree(path, ignore_errors=True)


def ffmpeg(*arguments: str) -> None:
    completed = subprocess.run([FFMPEG or "ffmpeg", "-nostdin", "-v", "error", "-y", *arguments],
                               capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {completed.stderr}")


def sine_wav(path: Path, rate: int, seconds: str, channels: int = 1, codec: str = "pcm_s16le") -> Path:
    ffmpeg("-f", "lavfi", "-i", f"sine=frequency=440:sample_rate={rate}:duration={seconds}",
           "-ac", str(channels), "-c:a", codec, str(path))
    return path


def noise_flac(path: Path, rate: int, seconds: str) -> Path:
    ffmpeg("-f", "lavfi", "-i", f"anoisesrc=r={rate}:d={seconds}:a=0.1", "-c:a", "flac", str(path))
    return path


def two_stream_mka(path: Path) -> Path:
    """A Matroska file with two FLAC streams at different rates."""
    ffmpeg("-f", "lavfi", "-i", "sine=sample_rate=44100:duration=1", "-f", "lavfi",
           "-i", "anoisesrc=r=22050:d=0.5:a=0.1", "-map", "0", "-map", "1", "-c:a", "flac", str(path))
    return path


def rate_changing_mp3(path: Path) -> Path:
    """Two MP3 streams at different rates, joined frame to frame: every frame
    decodes cleanly, but no single rate counts them."""
    first, second = path.with_suffix(".44100.mp3"), path.with_suffix(".48000.mp3")
    for target, rate in ((first, 44100), (second, 48000)):
        ffmpeg("-f", "lavfi", "-i", f"sine=sample_rate={rate}:duration=1", "-c:a", "libmp3lame",
               "-write_xing", "0", "-id3v2_version", "0", str(target))
    path.write_bytes(first.read_bytes() + second.read_bytes())
    first.unlink()
    second.unlink()
    return path


def channel_changing_mp3(path: Path) -> Path:
    """A mono and a stereo MP3 stream at one rate, joined frame to frame."""
    first, second = path.with_suffix(".mono.mp3"), path.with_suffix(".stereo.mp3")
    for target, channels in ((first, "1"), (second, "2")):
        ffmpeg("-f", "lavfi", "-i", "sine=sample_rate=44100:duration=1", "-ac", channels, "-c:a", "libmp3lame",
               "-write_xing", "0", "-id3v2_version", "0", str(target))
    path.write_bytes(first.read_bytes() + second.read_bytes())
    first.unlink()
    second.unlink()
    return path


def tagged_wav(path: Path, rate: int, seconds: str, **tags: str) -> Path:
    """A mono WAV whose INFO tags hold the given text. A tag's characters
    U+DC80 to U+DCFF reach the file as the single bytes 0x80 to 0xFF, so a
    tag can hold bytes that are not UTF-8."""
    metadata = [argument for name, value in tags.items() for argument in ("-metadata", f"{name}={value}")]
    ffmpeg("-f", "lavfi", "-i", f"sine=frequency=440:sample_rate={rate}:duration={seconds}", *metadata,
           "-c:a", "pcm_s16le", str(path))
    return path


def corrupted_flac(path: Path) -> Path:
    """A FLAC file with damaged frames in the middle; ffmpeg conceals them."""
    noise_flac(path, 48000, "1.25")
    data = bytearray(path.read_bytes())
    middle = len(data) // 2
    for index in range(middle, middle + 400):
        data[index] ^= 0x5A
    path.write_bytes(bytes(data))
    return path


def empty_wav(path: Path) -> Path:
    """A WAV header with no sample frames, written by Python's own writer."""
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(8000)
    return path


def chord(seconds: float, frequencies: tuple[float, ...] = (220.0, 277.18, 329.63), amplitude: float = 0.2):
    """A sustained chord: several sines of equal amplitude."""
    return ("chord", seconds, frequencies, amplitude)


def talk(seconds: float, amplitude: float = 0.2, seed: int = 1):
    """Talk-like noise: white noise whose level rises and falls four times a
    second, like syllables."""
    return ("talk", seconds, seed, amplitude)


def floor_noise(seconds: float, amplitude: float = 0.001, seed: int = 2):
    """A room's floor: faint white noise."""
    return ("floor", seconds, seed, amplitude)


def synthetic_wav(path: Path, rate: int, pieces: list[tuple], channels: int = 1) -> tuple[Path, list[int]]:
    """A 16-bit WAV of the pieces in order, written with numpy and Python's
    `wave`; each piece is a whole number of samples, so its edges are exact.
    Returns the path and each piece's start sample, then the total."""
    parts, edges, at = [], [], 0
    for kind, seconds, *rest in pieces:
        count = int(round(seconds * rate))
        index = np.arange(at, at + count) / rate
        if kind == "chord":
            frequencies, amplitude = rest
            signal = sum(amplitude * np.sin(2 * np.pi * f * index) for f in frequencies)
        elif kind in ("talk", "floor"):
            seed, amplitude = rest
            noise = np.random.default_rng(seed + at).standard_normal(count) * amplitude
            signal = noise * (0.55 + 0.45 * np.sin(2 * np.pi * 4.0 * index)) if kind == "talk" else noise
        else:
            raise ValueError(kind)
        edges.append(at)
        parts.append(signal)
        at += count
    edges.append(at)
    samples = np.clip(np.concatenate(parts), -1.0, 1.0)
    pcm = np.round(samples * 32767).astype("<i2")
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(np.repeat(pcm, channels).tobytes())
    return path, edges
# Two invented tunes, as MIDI note numbers, one note per quarter second.
TUNE = (60, 62, 64, 65, 67, 69, 67, 65, 64, 62, 60, 67, 64, 60, 72, 71)
OTHER_TUNE = (57, 57, 64, 64, 66, 66, 64, 62, 62, 61, 61, 59, 59, 57, 52, 54)


def tune_samples(notes: tuple[int, ...], rate: int, transpose: int = 0, repeats: int = 3, seed: int = 0,
                 note_seconds: float = 0.25):
    """A tune of three-harmonic tones with faint noise, as floats, made with
    numpy: the same tune transposed has the same melody in another key."""
    import numpy
    rng = numpy.random.default_rng(seed)
    length = int(note_seconds * rate)
    t = numpy.arange(length) / rate
    envelope = numpy.minimum(1, numpy.minimum(t / 0.01, (note_seconds - t) / 0.02))
    tones = []
    for note in notes * repeats:
        frequency = 440 * 2 ** ((note + transpose - 69) / 12)
        tones.append(sum(numpy.sin(2 * numpy.pi * frequency * h * t) / h for h in (1, 2, 3)) * envelope)
    samples = numpy.concatenate(tones) * 0.3
    return samples + rng.normal(0, 0.01, size=len(samples))


def write_wav(path: Path, samples, rate: int) -> Path:
    """Mono 16-bit PCM through Python's own writer."""
    import numpy
    data = numpy.clip(numpy.round(numpy.asarray(samples) * 32767), -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(data.tobytes())
    return path


def build_archive(test: unittest.TestCase, contents: dict[str, Path], steps: tuple[dict[str, Any], ...] = (),
                  files: tuple[Path, ...] = ()) -> tuple[Path, dict[str, Any]]:
    """A scratch archive whose Artifacts hold `contents`, admitted by the
    fixture builder through `archive init` and `door admit`, followed by any
    further fixture plan `steps`, whose records may name `files` as content.
    Returns the root and the builder's manifest, whose objects are keyed by
    the plan keys."""
    tmp = scratch(test, "enrich-")
    content = tmp / "content"
    content.mkdir()
    records = []
    for key, source in contents.items():
        shutil.copyfile(source, content / source.name)
        records.append({"key": key, "namespace": "archive.core/artifact", "name": f"capture_{key}",
                        "shape": "artifact", "content": source.name})
    for source in files:
        shutil.copyfile(source, content / source.name)
    plan = {"protocol": "archive-fixture/1", "operator": "enrich-operator",
            "steps": [{"name": "audio", "command": "admit", "records": records}, *steps]}
    (tmp / "plan.json").write_text(json.dumps(plan))
    root = tmp / "archive"
    built = subprocess.run([sys.executable, str(REPOSITORY / "fixture" / "build.py"), str(root),
                            "--plan", str(tmp / "plan.json"), "--content", str(content),
                            "--door", DOOR, "--archive", ARCHIVE], capture_output=True, text=True,
                           env=environment())
    if built.returncode != 0:
        raise RuntimeError(f"fixture builder failed: {built.stderr}")
    return root, json.loads(built.stdout)


def export(root: Path) -> dict[str, Any]:
    completed = subprocess.run([ARCHIVE, "export"], cwd=root, capture_output=True, text=True, env=environment())
    answer = json.loads(completed.stdout)
    if completed.returncode != 0 or answer.get("status") != "ok":
        raise RuntimeError(f"archive export failed: {completed.stdout} {completed.stderr}")
    return answer


def head(root: Path) -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True,
                          check=True).stdout.strip()


# Two invented chord progressions, as MIDI chords, one chord per second.
# Essentia's cover similarity scores renditions of one progression the way it
# scores renditions of a real song (about 0.06), and the two apart (about
# 0.15); single-line tunes score too far apart for its thresholds.
PROGRESSION = ((60, 64, 67), (57, 60, 64), (65, 69, 72), (67, 71, 74),
               (60, 64, 67), (65, 69, 72), (62, 65, 69), (67, 71, 74))
OTHER_PROGRESSION = ((62, 66, 69), (64, 67, 71), (59, 62, 66), (57, 61, 64),
                     (62, 66, 69), (55, 59, 62), (57, 61, 64), (64, 68, 71))


def progression_samples(chords: tuple[tuple[int, ...], ...], rate: int, transpose: int = 0, repeats: int = 4,
                        seed: int = 0, chord_seconds: float = 1.0):
    """Chords of three-harmonic tones with faint noise, as floats, peaking
    at 0.3: the same progression transposed is the same song in another key."""
    import numpy
    rng = numpy.random.default_rng(seed)
    length = int(chord_seconds * rate)
    t = numpy.arange(length) / rate
    envelope = numpy.minimum(1, numpy.minimum(t / 0.01, (chord_seconds - t) / 0.05))
    parts = [sum(numpy.sin(2 * numpy.pi * 440 * 2 ** ((note + transpose - 69) / 12) * h * t) / h
                 for note in chord for h in (1, 2, 3)) * envelope for chord in chords * repeats]
    samples = numpy.concatenate(parts)
    samples = samples / numpy.abs(samples).max() * 0.3
    return samples + rng.normal(0, 0.01, size=len(samples))
