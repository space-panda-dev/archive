#!/usr/bin/env python3
"""The tonal extractor: tuning and its drift, key, chords, and dissonance (ADR 0074).

It reads the `frames` extractor's pitch-class profiles and levels from the
bank and measures, with Essentia's standard algorithms:

- `pitched`: which frames carry pitched sound (below), on the `frames` grid,
  so that every other output, and any reader, can leave out silence, talk,
  and noise the same way;
- `tuning_frequency`, `tuning_cents`, `tuning_confidence`: the recording's
  reference tuning by `TuningFrequency` over the spectral peaks of its pitched
  frames, as the frequency of A4 in Hz and its distance from 440 Hz in
  cents, folded into [-50, 50);
- `tuning_windows`: the same estimate in overlapping windows of about
  twenty seconds, every row with its confidence and its share of pitched
  frames, and `tuning_drift`: the windows that are at least half pitched, as
  spans whose value is the window's tuning in cents, written near
  `tuning_cents` (so a session tuned 49 cents sharp does not jump to -51 and
  back). Silence, talk, and noise leave gaps rather than invented values;
  `drift_spread_cents` is the spread of the listed windows, from their
  tenth to their ninetieth percentile;
- `key`, `scale`, `key_strength`: `Key` on the pitched frames' mean profile,
  and `keys`: the same in windows of about thirty seconds that are at least
  half pitched, as spans labelled like "C major" whose value is the strength;
- `chords`: `ChordsDetection` over the profile sequence, merged into spans
  of one chord over pitched frames (labelled as Essentia names chords: "A",
  "Bbm", "F#"), valued by their mean strength; `chords_changes_rate` and
  `chords_number_rate` are `ChordsDescriptors`' over the pitched frames;
- `dissonance`: `Dissonance` (sensory roughness) of every frame that
  passes the level gate, on the `frames` grid; a frame below it reads 0, as
  silence does, so a quiet room's noise is not averaged in. Talk and noise
  above the gate still have a value: mask with `pitched` to describe music.

What it reuses and what it cannot. The key, the scale, and the chords read
`frames/hpcp` and measure nothing again; the level gate reads
`frames/rms_db`. Tuning, the pitch gate, and dissonance need each frame's
spectral peaks, frequency by frequency, which the bank does not hold (a
frame's peaks are a ragged list, which no output kind declares, and `frames`
keeps only the twelve-bin profile the peaks make). So this extractor decodes the
recording once more, with the `frames` extractor's own decoder and framing,
and checks that it made as many frames as `frames` did, so its series line
up row for row. That repeats `frames`' windowing, spectrum, and peak
picking, and about doubles the spectral cost of a recording: the exception
to "decode once" that ADR 0074 records, with what would remove it.

The profile `frames` keeps is referenced to 440 Hz. A band tuned near half
a semitone from it splits each pitch class over two bins, and the key and
chords go wrong with it. On synthetic progressions in C, D, and G the key
held to 42 cents either side and failed from 44 (C major 44 cents sharp
reads as A minor), and chords held to 46 and failed from 48. So when the
recording's tuning is more than `max_detune_cents` (40) from 440 Hz,
`detuned` is true and the key, the keys, the chords, and their rates are
left empty rather than given confidently wrong. The test is the whole
recording's tuning; a window that drifts past it is not caught. A
tuning-corrected profile would be a new output of a new `frames` version,
not something to compute here behind its back.

The key profile is Temperley's: on synthetic progressions of harmonic tones
Essentia's default (`bgate`) and Krumhansl's named the subdominant (F for
C major) where Temperley's named the tonic. That is evidence from synthetic
sound only. The V2 system found that whole-segment key estimates of real
rehearsals were often unreliable, and that its repertoire does not
modulate, so `keys` is a window-by-window description, not a claim of
modulation. `ChordsDetection` averages two seconds of profiles; on
synthetic progressions its changes fall within about a hop of the true ones
on average, a few up to half a second late, and short spurious spans appear
between chords that share notes.

Pitched frames. Every peak's distance from the equal-tempered grid on
440 Hz (in cents, modulo 100) is added to a histogram, weighted by its
magnitude. The
concentration of a histogram about a tuning is the share of its weight
within `concentration_cents` of it, going round the circle: peaks that
agree on one tuning give nearly 1, and noise, whose peaks fall anywhere,
about the width's share of the circle (31 % for 15 cents). The harmonics of
a real tone sit off the tempered grid (the fifth harmonic 14 cents flat,
the seventh 31), which lowers the concentration of music a little and is why
the width is 15 cents and not 5. The frames are taken in blocks of about
five seconds; a block is pitched when at least half of it sounds (passes the
level gate) and its sounding frames' histogram concentrates about some
tuning at least `min_concentration`, and a frame is pitched when it sounds
and its block is pitched. The thresholds were set on synthetic sound (tones,
chords, noise, clipping, drum-like hits, a gliding voice-like tone): music
concentrates 0.66 to 1.0 even under noise or drums, and noise, drums alone,
and a gliding tone 0.32 to 0.36, so the threshold is 0.4. `tuning_windows`
keeps every window's evidence so that a reader can set it again on real
recordings.

Times are seconds from the first decoded sample. Each frame stands for the
hop centred on its own centre, so a span of frames k0..k1 runs from half a
hop before frame k0's centre to half a hop after frame k1's, clipped to the
decoded audio, and consecutive spans touch. Invoked by the runner as `tonal.py <request.json>`; the
settings are fixed, and a request with other parameters is refused.
"""

from __future__ import annotations

import importlib.metadata
import json
import platform
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

ENRICH = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ENRICH))
sys.path.insert(0, str(ENRICH / "extractors" / "frames"))

import numpy  # noqa: E402

import cover  # noqa: E402
import frames  # noqa: E402

RATE, FRAME, HOP = frames.RATE, frames.FRAME, frames.HOP
SECONDS_PER_ROW = HOP / RATE
BINS = 100  # one-cent bins of the deviation from the tempered grid

PEAKS = {"sampleRate": RATE, "orderBy": "frequency", "magnitudeThreshold": "0.00001", "minFrequency": 40,
         "maxFrequency": 5000, "maxPeaks": 10000}
GATE = {"rms_db_min": -60}
PITCHED = {"block_frames": 54, "concentration_cents": 15, "min_sounding": "0.5", "min_concentration": "0.4"}
TUNING = {"resolution": 1, "window_frames": 216, "hop_frames": 108, "min_pitched": "0.5"}
KEY = {"profileType": "temperley", "pcpSize": 12, "window_frames": 324, "hop_frames": 162, "min_pitched": "0.5",
       "max_detune_cents": 40}
CHORDS = {"windowSize": "2", "hopSize": HOP, "sampleRate": RATE}
DISSONANCE = {"maxPeaks": 100, "relative_threshold": "0.01"}
TUNING_COLUMNS = ("cents", "confidence", "pitched_share")


class TonalError(Exception):
    pass


def parameters() -> dict[str, Any]:
    """The settings, which the manifest must declare exactly."""
    return {"essentia": cover.ESSENTIA_BUILD, "decode_arguments": frames.decode_arguments("{input}"),
            "sample_scale": 32768, "analysis_rate": RATE, "frame_size": FRAME, "hop_size": HOP,
            "framing": "essentia-framegenerator-centred", "windowing": cover.ANALYSIS["windowing"],
            "spectral_peaks": PEAKS, "gate": GATE, "pitched": PITCHED, "tuning": TUNING, "key": KEY,
            "chords": CHORDS, "dissonance": DISSONANCE}


def tuning_grid() -> dict[str, int]:
    """Row w of `tuning_windows` is the window of frames w x hop_frames to
    w x hop_frames + window_frames - 1, which stand for the audio from half a
    hop before the first one's centre to half a hop after the last one's, so
    it is centred midway between those two frames' centres. The grid cannot
    say that the last row may be cut short by the end of the recording: its
    window holds fewer frames (its pitched share is of those it holds), and
    `tuning_drift` gives its true extent."""
    return {"sample_rate": RATE, "hop": TUNING["hop_frames"] * HOP, "frame": TUNING["window_frames"] * HOP,
            "first_centre": (TUNING["window_frames"] - 1) * HOP // 2}


# -- pure arithmetic, tested on its own ---------------------------------------

def fold(cents: float) -> float:
    """Cents from 440 Hz folded into [-50, 50): Essentia reports [-35, 65)."""
    return (cents + 50.0) % 100.0 - 50.0


def near(cents: float, reference: float) -> float:
    """The equivalent of `cents` (modulo a semitone) closest to `reference`."""
    return cents + 100.0 * round((reference - cents) / 100.0)


def frequency_of(cents: float) -> float:
    return 440.0 * 2.0 ** (cents / 1200.0)


def deviation_histogram(frequencies: numpy.ndarray, magnitudes: numpy.ndarray) -> numpy.ndarray:
    """Peak magnitude by distance above the tempered grid on 440 Hz, in
    one-cent bins from 0 to 99."""
    keep = frequencies > 0
    if not keep.any():
        return numpy.zeros(BINS)
    deviation = numpy.mod(1200.0 * numpy.log2(frequencies[keep].astype(numpy.float64) / 440.0), 100.0)
    return numpy.bincount(numpy.minimum(deviation.astype(numpy.int64), BINS - 1),
                          weights=magnitudes[keep].astype(numpy.float64), minlength=BINS)


def _within(width: float) -> numpy.ndarray:
    """For each centre bin, which bins lie within `width` cents of it."""
    centres = numpy.arange(BINS) + 0.5
    distance = numpy.abs(centres[:, None] - centres[None, :])
    return numpy.minimum(distance, BINS - distance) <= width


def concentration(histogram: numpy.ndarray, cents: float, width: float) -> float:
    """The share of the histogram's weight within `width` cents of `cents`,
    going round the circle; 0 for an empty histogram."""
    total = float(histogram.sum())
    if total <= 0:
        return 0.0
    centres = numpy.arange(BINS) + 0.5
    distance = numpy.abs(centres - (cents % 100.0))
    distance = numpy.minimum(distance, 100.0 - distance)
    return float(histogram[distance <= width].sum() / total)


def best_concentration(histogram: numpy.ndarray, width: float) -> float:
    """The histogram's concentration about whichever tuning concentrates it most."""
    total = float(histogram.sum())
    if total <= 0:
        return 0.0
    return float((_within(width) @ histogram).max() / total)


def windows(rows: int, length: int, hop: int) -> list[tuple[int, int]]:
    """Windows of `length` rows every `hop` rows covering `rows` rows: the
    last may be cut short by the end, and none lies wholly inside the one
    before it."""
    if rows <= 0:
        return []
    count = 1 if rows <= length else -(-(rows - length) // hop) + 1
    return [(index * hop, min(index * hop + length, rows)) for index in range(count)]


def seconds(row: int) -> float:
    return row * SECONDS_PER_ROW


def extent(first: int, stop: int, samples: int) -> tuple[float, float]:
    """The seconds frames first..stop-1 stand for: each frame the hop centred
    on its own centre, clipped to the decoded audio's `samples`."""
    def clip(sample: float) -> float:
        return min(max(sample, 0.0), float(samples)) / RATE
    return clip((first - 0.5) * HOP), clip((stop - 0.5) * HOP)


def runs(labels: Sequence[str], mask: Sequence[bool], strengths: Sequence[float],
         samples: int) -> list[dict[str, Any]]:
    """Spans of consecutive masked-in rows with one label, valued by their
    mean strength; a row masked out ends a span."""
    spans: list[dict[str, Any]] = []
    start = None
    for row in range(len(labels) + 1):
        ends = row == len(labels) or not mask[row] or (start is not None and labels[row] != labels[start])
        if start is not None and ends:
            begin, end = extent(start, row, samples)
            spans.append({"start": begin, "end": end, "label": labels[start],
                          "value": float(numpy.mean(strengths[start:row]))})
            start = None
        if row < len(labels) and mask[row] and start is None:
            start = row
    return spans


def spread(values: Sequence[float]) -> float:
    """From the tenth to the ninetieth percentile; 0 for fewer than two."""
    if len(values) < 2:
        return 0.0
    low, high = numpy.percentile(numpy.asarray(values, dtype=numpy.float64), [10, 90])
    return float(high - low)


def dissonance_peaks(frequencies: numpy.ndarray, magnitudes: numpy.ndarray) -> tuple[numpy.ndarray, numpy.ndarray]:
    """The peaks `Dissonance` is given: those within the relative threshold
    of the frame's loudest, at most `maxPeaks` of the loudest, in frequency
    order. Without the threshold a lone sine's window side lobes, 62 dB
    down, read as a dissonant cluster (Essentia weights peaks by loudness,
    not by magnitude), and every frame measures about 0.5."""
    if len(magnitudes) == 0:
        return frequencies, magnitudes
    keep = magnitudes >= magnitudes.max() * float(DISSONANCE["relative_threshold"])
    frequencies, magnitudes = frequencies[keep], magnitudes[keep]
    if len(magnitudes) > DISSONANCE["maxPeaks"]:
        loudest = numpy.sort(numpy.argsort(-magnitudes, kind="stable")[:DISSONANCE["maxPeaks"]])
        frequencies, magnitudes = frequencies[loudest], magnitudes[loudest]
    return frequencies, magnitudes


# -- measuring ------------------------------------------------------------------

class Tuning:
    """`TuningFrequency` over the whole recording and over each window at
    once: one instance per window that can be open at a time, made when its
    window begins. Pitched frames feed the estimates; every sounding frame's
    deviation histogram feeds the confidence, so noise the pitch gate let
    through still lowers it."""

    def __init__(self, es: Any, rows: int):
        self.es = es
        self.windows = windows(rows, TUNING["window_frames"], TUNING["hop_frames"])
        self.open: dict[int, dict[str, Any]] = {}
        self.whole = es.TuningFrequency(resolution=TUNING["resolution"])
        self.whole_histogram = numpy.zeros(BINS)
        self.whole_estimate: tuple[float, float] | None = None
        self.finished: list[dict[str, Any]] = []
        self.next = 0

    def add(self, row: int, pitched: bool, histogram: numpy.ndarray | None,
            frequencies: numpy.ndarray, magnitudes: numpy.ndarray) -> None:
        while self.next < len(self.windows) and self.windows[self.next][0] == row:
            self.open[self.next] = {"algorithm": self.es.TuningFrequency(resolution=TUNING["resolution"]),
                                    "histogram": numpy.zeros(BINS), "pitched": 0, "estimate": None}
            self.next += 1
        if histogram is not None:
            self.whole_histogram += histogram
            for window in self.open.values():
                window["histogram"] += histogram
        if pitched:
            self.whole_estimate = self.whole(frequencies, magnitudes)
            for window in self.open.values():
                window["estimate"] = window["algorithm"](frequencies, magnitudes)
                window["pitched"] += 1
        for index in [index for index in self.open if self.windows[index][1] == row + 1]:
            window = self.open.pop(index)
            start, end = self.windows[index]
            cents = 0.0 if window["estimate"] is None else fold(float(window["estimate"][1]))
            self.finished.append({"start": start, "end": end, "pitched": window["pitched"],
                                  "defined": window["estimate"] is not None, "cents": cents,
                                  "confidence": concentration(window["histogram"], cents,
                                                              PITCHED["concentration_cents"])
                                  if window["estimate"] is not None else 0.0})

    def result(self, samples: int) -> tuple[dict[str, Any], numpy.ndarray]:
        if self.open or len(self.finished) != len(self.windows):
            raise TonalError(f"{len(self.windows) - len(self.finished)} tuning windows were never closed")
        if self.whole_estimate is None:
            cents, confidence = 0.0, 0.0
        else:
            cents = fold(float(self.whole_estimate[1]))
            confidence = concentration(self.whole_histogram, cents, PITCHED["concentration_cents"])
        rows = numpy.zeros((len(self.finished), len(TUNING_COLUMNS)), dtype="<f4")
        drift = []
        for index, window in enumerate(self.finished):
            share = window["pitched"] / (window["end"] - window["start"])
            window_cents = near(window["cents"], cents) if window["defined"] else 0.0
            rows[index] = (window_cents, window["confidence"], share)
            if window["defined"] and share >= float(TUNING["min_pitched"]):
                begin, end = extent(window["start"], window["end"], samples)
                drift.append({"start": begin, "end": end, "value": window_cents})
        return ({"tuning_frequency": frequency_of(cents), "tuning_cents": cents, "tuning_confidence": confidence,
                 "tuning_drift": drift, "drift_spread_cents": spread([span["value"] for span in drift])}, rows)


def key_of(es: Any, profile: numpy.ndarray) -> tuple[str, str, float]:
    key, scale, strength, _ = es.Key(profileType=KEY["profileType"], pcpSize=KEY["pcpSize"])(
        profile.astype(numpy.float32))
    return key, scale, float(strength)


def keys(es: Any, hpcp: numpy.ndarray, pitched: numpy.ndarray, samples: int) -> dict[str, Any]:
    """The whole recording's key and each window's, from the pitched frames'
    mean profile."""
    if pitched.any() and hpcp[pitched].any():
        key, scale, strength = key_of(es, hpcp[pitched].mean(axis=0))
    else:
        key, scale, strength = "", "", 0.0
    spans = []
    for start, end in windows(len(hpcp), KEY["window_frames"], KEY["hop_frames"]):
        mask = pitched[start:end]
        if mask.sum() < float(KEY["min_pitched"]) * (end - start) or not hpcp[start:end][mask].any():
            continue
        window_key, window_scale, window_strength = key_of(es, hpcp[start:end][mask].mean(axis=0))
        begin, finish = extent(start, end, samples)
        spans.append({"start": begin, "end": finish, "label": f"{window_key} {window_scale}",
                      "value": window_strength})
    return {"key": key, "scale": scale, "key_strength": strength, "keys": spans}


def chords(es: Any, hpcp: numpy.ndarray, pitched: numpy.ndarray, key: str, scale: str,
           samples: int) -> dict[str, Any]:
    labels, strengths = es.ChordsDetection(windowSize=float(CHORDS["windowSize"]), hopSize=CHORDS["hopSize"],
                                           sampleRate=CHORDS["sampleRate"])(hpcp.astype(numpy.float32))
    if len(labels) != len(hpcp):
        raise TonalError(f"ChordsDetection gave {len(labels)} chords for {len(hpcp)} frames")
    strengths = numpy.asarray(strengths, dtype=numpy.float64)
    spans = runs(list(labels), [bool(value) for value in pitched], strengths, samples)
    heard = [label for label, keep in zip(labels, pitched) if keep]
    if heard and key:
        _, number_rate, changes_rate, _, _ = es.ChordsDescriptors()(heard, key, scale)
    else:
        number_rate, changes_rate = 0.0, 0.0
    return {"chords": spans, "chords_changes_rate": float(changes_rate), "chords_number_rate": float(number_rate)}


def detuned(tuning: dict[str, Any]) -> bool:
    """Whether the profiles, referenced to 440 Hz, are too far from the
    recording's tuning for their key and chords to be trusted."""
    return tuning["tuning_confidence"] > 0 and abs(tuning["tuning_cents"]) > KEY["max_detune_cents"]


def harmony(es: Any, hpcp: numpy.ndarray, pitched: numpy.ndarray, samples: int, is_detuned: bool) -> dict[str, Any]:
    """Key and chords, or, for a detuned recording, the same outputs empty."""
    if is_detuned:
        return {"key": "", "scale": "", "key_strength": 0.0, "keys": [], "chords": [], "chords_changes_rate": 0.0,
                "chords_number_rate": 0.0}
    outputs = keys(es, hpcp, pitched, samples)
    outputs.update(chords(es, hpcp, pitched, outputs["key"], outputs["scale"], samples))
    return outputs


def need(request: dict[str, Any], feature: str, columns: int) -> numpy.ndarray:
    """A needed `frames` series, refused unless it is on the grid this
    extractor decodes to."""
    described = request["needs"].get(feature)
    if described is None:
        raise TonalError(f"the request does not carry {feature}")
    if described.get("dtype") != "f32" or described.get("shape", [None, None])[1] != columns:
        raise TonalError(f"{feature} is not an f32 series of {columns} columns")
    grid = described.get("grid") or {}
    if (grid.get("sample_rate"), grid.get("hop"), grid.get("frame"), grid.get("first_centre")) != (RATE, HOP,
                                                                                                    FRAME, 0):
        raise TonalError(f"{feature} is not on the frames grid this extractor decodes to: {grid}")
    values = numpy.fromfile(described["path"], dtype="<f4")
    rows = described["shape"][0]
    if values.size != rows * columns:
        raise TonalError(f"{described['path']} holds {values.size} values, not {rows} x {columns}")
    return values.reshape(rows, columns)


class Blocks:
    """Frames held a block at a time, until it is known whether the block is
    pitched, then handed to the tuning in order."""

    def __init__(self, tuning: Tuning, sounding: numpy.ndarray, pitched: numpy.ndarray):
        self.tuning, self.sounding, self.pitched = tuning, sounding, pitched
        self.held: list[tuple[int, numpy.ndarray | None, numpy.ndarray, numpy.ndarray]] = []

    def add(self, row: int, frequencies: numpy.ndarray, magnitudes: numpy.ndarray) -> None:
        histogram = deviation_histogram(frequencies, magnitudes) if self.sounding[row] else None
        self.held.append((row, histogram, frequencies, magnitudes))
        if len(self.held) == PITCHED["block_frames"]:
            self.flush()

    def flush(self) -> None:
        if not self.held:
            return
        histograms = [histogram for _, histogram, _, _ in self.held if histogram is not None]
        enough = len(histograms) >= float(PITCHED["min_sounding"]) * len(self.held)
        is_pitched = enough and best_concentration(numpy.sum(histograms, axis=0), PITCHED["concentration_cents"]) \
            >= float(PITCHED["min_concentration"])
        for row, histogram, frequencies, magnitudes in self.held:
            self.pitched[row] = is_pitched and histogram is not None
            self.tuning.add(row, bool(self.pitched[row]), histogram, frequencies, magnitudes)
        self.held = []


def measure(request: dict[str, Any], blocks: Iterable[numpy.ndarray] | None = None) -> dict[str, Any]:
    """Every output: scalars and spans by name, and the series as arrays."""
    es = cover.essentia_module()
    hpcp = need(request, "frames/hpcp", 12)
    rms_db = need(request, "frames/rms_db", 1)[:, 0]
    rows = len(hpcp)
    if len(rms_db) != rows:
        raise TonalError(f"frames/hpcp has {rows} rows and frames/rms_db {len(rms_db)}")
    sounding = rms_db >= GATE["rms_db_min"]

    windowing = es.Windowing(**cover._options(cover.ANALYSIS["windowing"]))
    spectrum = es.Spectrum()
    peaks = es.SpectralPeaks(**cover._options(PEAKS))
    dissonance = es.Dissonance()
    tuning = Tuning(es, rows)
    pitched = numpy.zeros(rows, dtype=bool)
    held = Blocks(tuning, sounding, pitched)
    roughness = numpy.zeros(rows, dtype="<f4")
    audio = frames.Counted(frames.decoded_blocks(request["input"]["path"]) if blocks is None else blocks)
    row = 0
    for frame in frames.framed(audio):
        if row >= rows:
            raise TonalError(f"the decode made more frames than the {rows} frames/hpcp has")
        frequencies, magnitudes = peaks(spectrum(windowing(frame)))
        held.add(row, frequencies, magnitudes)
        if sounding[row]:
            roughness[row] = dissonance(*dissonance_peaks(frequencies, magnitudes))
        row += 1
    held.flush()
    if row != rows:
        raise TonalError(f"the decode made {row} frames, but frames/hpcp has {rows}")
    if not numpy.isfinite(roughness).all():
        raise TonalError("Dissonance gave a value that is not finite")
    outputs, tuning_rows = tuning.result(audio.samples)
    outputs["detuned"] = detuned(outputs)
    outputs.update(harmony(es, hpcp, pitched, audio.samples, outputs["detuned"]))
    outputs["pitched"] = pitched.astype("u1").reshape(-1, 1)
    outputs["tuning_windows"] = tuning_rows
    outputs["dissonance"] = roughness.reshape(-1, 1)
    return outputs


SCALARS = ("tuning_frequency", "tuning_cents", "tuning_confidence", "drift_spread_cents", "detuned", "key", "scale",
           "key_strength", "chords_changes_rate", "chords_number_rate")
SPANS = ("tuning_drift", "keys", "chords")
SERIES = {"pitched": "u8", "tuning_windows": "f32", "dissonance": "f32"}


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: tonal.py <request.json>", file=sys.stderr)
        return 2
    try:
        request = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
        if request.get("parameters") != parameters():
            raise TonalError("tonal 2 has fixed settings; the request names others")
        output = Path(request["output_dir"])
        measured = measure(request)
        result_outputs: dict[str, Any] = {name: {"value": measured[name]} for name in SCALARS}
        for name in SPANS:
            (output / f"{name}.json").write_text(json.dumps(measured[name]), encoding="utf-8")
            result_outputs[name] = {}
        for name, dtype in SERIES.items():
            measured[name].tofile(output / f"{name}.{dtype}")
            result_outputs[name] = {"shape": list(measured[name].shape)}
        software = [{"name": "ffmpeg", "version": frames.ffmpeg_version()},
                    {"name": "essentia", "version": importlib.metadata.version("essentia")},
                    {"name": "numpy", "version": numpy.__version__},
                    {"name": "python", "version": platform.python_version()}]
        result = {"contract": "archive-extractor-result/1", "software": software,
                  "grids": {"frames": {"sample_rate": RATE, "hop": HOP, "frame": FRAME, "first_centre": 0},
                            "tuning": tuning_grid()},
                  "outputs": result_outputs}
        (output / "result.json").write_text(json.dumps(result), encoding="utf-8")
    except (TonalError, frames.FramesError, cover.CoverError, OSError, KeyError, ValueError, TypeError,
            RuntimeError) as exc:
        print(f"tonal: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
