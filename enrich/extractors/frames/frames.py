#!/usr/bin/env python3
"""The frames extractor: one decode, one pass, the base tracks (ADR 0074).

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
ceil(n / 2,048) + 1 frames. At about 10.8 frames a second, the grid suits
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
from pathlib import Path
from typing import Any

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
TRACKS = {"hpcp": 12, "rms_db": 1, "centroid": 1, "rolloff": 1, "flatness": 1, "flux": 1, "onset_hfc": 1,
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


def decode(path: str, ffmpeg: str = "ffmpeg") -> numpy.ndarray:
    try:
        completed = subprocess.run([ffmpeg, *decode_arguments(path)], stdin=subprocess.DEVNULL, capture_output=True)
    except OSError as exc:
        raise FramesError(f"ffmpeg could not run: {exc}") from exc
    if completed.returncode != 0:
        raise FramesError(f"ffmpeg could not decode {path}: "
                          f"{completed.stderr.decode('utf-8', 'replace').strip()[:300]}")
    samples = numpy.frombuffer(completed.stdout, dtype="<i2").astype(numpy.float32) / numpy.float32(32768)
    if len(samples) == 0:
        raise FramesError(f"{path} decodes to no samples")
    return samples


def measure(audio: numpy.ndarray) -> dict[str, numpy.ndarray]:
    """Every track, in one pass over the frames."""
    analyser = cover.Analyser()
    es = analyser.es
    rms = es.RMS()
    centroid = es.Centroid(range=RATE / 2)
    rolloff = es.RollOff(sampleRate=RATE, cutoff=float(ROLLOFF))
    flatness = es.Flatness()
    flux = es.Flux()
    onset = es.OnsetDetection(method="hfc", sampleRate=RATE)
    mfcc = es.MFCC(inputSize=FRAME // 2 + 1, sampleRate=RATE, **MFCC)
    count = -(-len(audio) // HOP) + 1
    tracks = {name: numpy.empty((count, columns), dtype=numpy.float32) for name, columns in TRACKS.items()}
    no_phase = numpy.zeros(FRAME // 2 + 1, dtype=numpy.float32)
    index = 0
    for frame in es.FrameGenerator(audio, frameSize=FRAME, hopSize=HOP):
        if index == count:
            raise FramesError(f"{len(audio)} samples made more than the {count} frames the grid allows")
        spectrum = analyser.spectrum(analyser.windowing(frame))
        tracks["hpcp"][index] = analyser.hpcp(*analyser.peaks(spectrum))
        level = float(rms(frame))
        tracks["rms_db"][index] = max(20 * math.log10(level), RMS_FLOOR_DB) if level > 0 else RMS_FLOOR_DB
        tracks["centroid"][index] = centroid(spectrum)
        tracks["rolloff"][index] = rolloff(spectrum)
        tracks["flatness"][index] = flatness(spectrum)
        tracks["flux"][index] = flux(spectrum)
        tracks["onset_hfc"][index] = onset(spectrum, no_phase)
        tracks["melbands"][index], tracks["mfcc"][index] = mfcc(spectrum)
        index += 1
    if index != count:
        raise FramesError(f"{len(audio)} samples made {index} frames, not the {count} the grid says")
    return tracks


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
        audio = decode(request["input"]["path"])
        tracks = measure(audio)
        for name, values in tracks.items():
            values.astype("<f4").tofile(output / f"{name}.f32")
        software = [{"name": "ffmpeg", "version": ffmpeg_version()},
                    {"name": "essentia", "version": importlib.metadata.version("essentia")},
                    {"name": "numpy", "version": numpy.__version__},
                    {"name": "python", "version": platform.python_version()}]
        outputs: dict[str, Any] = {name: {"shape": list(values.shape)} for name, values in tracks.items()}
        outputs["samples"] = {"value": len(audio)}
        outputs["seconds"] = {"value": len(audio) / RATE}
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
