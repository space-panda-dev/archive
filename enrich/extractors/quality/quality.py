#!/usr/bin/env python3
"""The quality extractor: how well a recording was captured (ADR 0074).

It decodes the content's first audio stream once more, at its own sample
rate and with every channel, because each measure here is about the samples
themselves, which the `frames` decode (one channel at 22,050 Hz) changes:

- downmixing hides a defect in one channel and cancels what is out of phase
  between two, so clipping, clicks, gaps, and DC offset are found per
  channel, and the stereo correlation needs both;
- resampling to 22,050 Hz removes everything above 11,025 Hz, which is what
  the effective bandwidth measures, and smooths the flat tops, single-sample
  steps, and true peaks the detectors look for.

From `frames` it reads only `frames/samples`, so that its two series have
exactly the rows of the `frames` grid and line up with every `frames` series.

In one ffmpeg process the stream is split in two: one branch goes through
ffmpeg's `ebur128` filter (EBU R 128 integrated loudness, loudness range, and
true peak) and the other reaches this program as 32-bit floats, read a block
at a time so that memory does not grow with the recording. Essentia's
`LoudnessEBUR128` needs the whole signal in memory (four hours of 48 kHz
stereo is 2.8 GB before its own copies) and its `TruePeakDetector` runs at
about 30 times real time per channel here, where the filter costs the decode
almost nothing; `loudness-r128` measures with the same filter, so the two
integrated values agree.

The rest are Essentia's standard algorithms on each channel, at the stream's
own rate, in frames of 512 samples every 256 at 48 kHz (about 10.7 ms), as
long in time at higher rates (see `detector_frame`), and 2,048 every 1,024
for gaps:
`SaturationDetector` (clipping), `ClickDetector`, `DiscontinuityDetector`,
`GapsDetector` (dropouts), and `NoiseBurstDetector`; `HumDetector` on the
channels' mean in overlapping chunks; and `FalseStereoDetector` on the first
two channels in each `frames` window. Sample peak, DC offset, the whole
recording's correlation, the noise floor, and the long-term spectrum behind
the effective bandwidth are sums numpy keeps as the blocks pass. Essentia has
no SNR for a recording without a noise-only lead-in (its `SNR` assumes one),
so the signal-to-noise ratio here is a level ratio between loud and quiet
windows, stated as such.

Invoked by the runner as `quality.py <request.json>`; the settings are fixed,
and a request with other parameters is refused.
"""

from __future__ import annotations

import importlib.metadata
import json
import math
import platform
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterator

import numpy

# The frames grid, which `frames` defines and this extractor's series follow.
GRID = {"sample_rate": 22050, "hop": 2048, "frame": 4096, "first_centre": 0}
FLOOR_DB = -120  # what a level of exactly nothing is written as
FULL_SCALE = 32767 / 32768  # the largest positive 16-bit sample, as a fraction of full scale

PARAMETERS: dict[str, Any] = {
    "decoder": "ffmpeg",
    "decode_arguments": ["-nostdin", "-hide_banner", "-nostats", "-loglevel", "info", "-i", "{input}",
                         "-filter_complex",
                         "[0:a:0]asplit=2[pcm][meter];[meter]ebur128=peak=true:framelog=quiet[metered]",
                         "-map", "[pcm]", "-ac", "{channels}", "-ar", "{sample_rate}", "-c:a", "pcm_f32le",
                         "-f", "f32le", "pipe:1", "-map", "[metered]", "-f", "null", "-"],
    "essentia": "2.1b6.dev1438",
    "level_floor_db": FLOOR_DB,
    "detector_frame": 512,
    "detector_hop": 256,
    "detector_reference_rate": 48000,
    "saturation": {"energyThreshold": "-1", "differentialThreshold": "0.001", "minimumDuration": "0.005"},
    "clipping_merge_seconds": "0.05",
    "clicks": {"order": 12, "detectionThreshold": "30", "powerEstimationThreshold": 10, "silenceThreshold": -50},
    "discontinuities": {"order": 3, "kernelSize": 7, "detectionThreshold": "8", "energyThreshold": "-60",
                        "subFrameSize": 32, "silenceThreshold": -50},
    "discontinuity_floor": "0.05",
    "noise_bursts": {"threshold": 8, "alpha": "0.9", "silenceThreshold": -50},
    "noise_burst_merge_seconds": "0.01",
    "noise_burst_floor": "0.001",
    "gaps": {"frameSize": 2048, "hopSize": 1024, "silenceThreshold": "-50", "prepowerThreshold": "-30",
             "minimumTime": "10", "maximumTime": "3500", "prepowerTime": "40", "postpowerTime": "40",
             "kernelSize": 11, "attackTime": "0.05", "releaseTime": "0.05"},
    "hum": {"Q0": "0.1", "Q1": "0.55", "detectionThreshold": "5", "frameSize": "0.4", "hopSize": "0.2",
            "minimumFrequency": "22.5", "maximumFrequency": "400", "minimumDuration": "2", "numberHarmonics": 1,
            "timeContinuity": "10", "timeWindow": "10"},
    "hum_salience_floor": "0.1",
    "hum_chunk_seconds": 120,
    "hum_overlap_seconds": 20,
    "hum_minimum_seconds": 20,
    "stereo": {"silenceThreshold": -70, "correlationThreshold": "0.9995"},
    "noise_floor_percentile": 10,
    "signal_percentile": 95,
    "spectrum_frame": 4096,
    "bandwidth_drop_db": 20,
    "bandwidth_median_hz": 500,
    "bandwidth_edge_hz": 300,
    "band_limited_below": "0.85",
}

# The detectors' settings as Essentia takes them: decimal text becomes a float.
def _settings(values: dict[str, Any]) -> dict[str, Any]:
    return {key: float(value) if isinstance(value, str) else value for key, value in values.items()}


class QualityError(Exception):
    pass


# -- decoding ---------------------------------------------------------------------

def probe(path: str, ffprobe: str = "ffprobe") -> tuple[int, int]:
    """The first audio stream's sample rate and channel count."""
    try:
        completed = subprocess.run([ffprobe, "-v", "error", "-select_streams", "a:0", "-show_entries",
                                    "stream=sample_rate,channels", "-of", "json", path],
                                   capture_output=True, text=True)
    except OSError as exc:
        raise QualityError(f"ffprobe could not run: {exc}") from exc
    try:
        stream = json.loads(completed.stdout)["streams"][0]
        rate, channels = int(stream["sample_rate"]), int(stream["channels"])
    except (ValueError, KeyError, IndexError, TypeError):
        raise QualityError(f"ffprobe found no audio stream in {path}: {completed.stderr.strip()[:300]}") from None
    if rate <= 0 or channels <= 0:
        raise QualityError(f"{path}'s first audio stream has rate {rate} and {channels} channels")
    return rate, channels


def decode_arguments(path: str, rate: int, channels: int) -> list[str]:
    values = {"{input}": path, "{sample_rate}": str(rate), "{channels}": str(channels)}
    return [values.get(word, word) for word in PARAMETERS["decode_arguments"]]


class Decoder:
    """The first audio stream at its own rate, every channel, as float32
    blocks of shape [frames, channels]; afterwards, ffmpeg's R 128 summary."""

    def __init__(self, path: str, rate: int, channels: int, ffmpeg: str = "ffmpeg", block_frames: int = 1 << 16):
        self.path, self.rate, self.channels = path, rate, channels
        self.ffmpeg, self.block_bytes = ffmpeg, block_frames * channels * 4
        self.log = ""

    def __iter__(self) -> Iterator[numpy.ndarray]:
        with tempfile.TemporaryFile() as errors:
            try:
                process = subprocess.Popen([self.ffmpeg, *decode_arguments(self.path, self.rate, self.channels)],
                                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errors)
            except OSError as exc:
                raise QualityError(f"ffmpeg could not run: {exc}") from exc
            assert process.stdout is not None
            width = 4 * self.channels
            carry = b""
            try:
                while chunk := process.stdout.read(self.block_bytes):
                    chunk = carry + chunk
                    usable = len(chunk) - len(chunk) % width
                    carry = chunk[usable:]
                    if usable:
                        yield numpy.frombuffer(chunk[:usable], dtype="<f4").reshape(-1, self.channels)
            finally:
                process.stdout.close()
                returncode = process.wait()
            errors.seek(0)
            self.log = errors.read().decode("utf-8", "replace")
            if returncode != 0 or carry:
                raise QualityError(f"ffmpeg could not decode {self.path}: {self.log.strip()[-300:]}")


def r128_summary(log: str) -> dict[str, float]:
    """Integrated loudness, loudness range, and true peak from the summary
    ffmpeg's ebur128 filter prints when its input ends. A true peak of -inf
    (digital silence) is written as the level floor."""
    _, marker, summary = log.rpartition("Summary:")
    if not marker:
        raise QualityError("ffmpeg's ebur128 filter printed no summary")
    sections = {"Integrated loudness": ("I", "integrated"), "Loudness range": ("LRA", "loudness_range"),
                "True peak": ("Peak", "true_peak")}
    found: dict[str, float] = {}
    section = None
    for line in summary.splitlines():
        text = line.strip()
        if text.endswith(":") and text[:-1] in sections:
            section = text[:-1]
            continue
        if section is None:
            continue
        label, name = sections[section]
        match = re.fullmatch(rf"{label}:\s+(-?inf|-?\d+(?:\.\d+)?)\s+(LUFS|LU|dBFS)", text)
        if match and name not in found:
            value = float(match.group(1))
            found[name] = value if math.isfinite(value) else float(FLOOR_DB)
    missing = sorted(set(name for _, name in sections.values()) - set(found))
    if missing:
        raise QualityError(f"ffmpeg's ebur128 summary has no {', '.join(missing)}")
    found["true_peak"] = max(found["true_peak"], float(FLOOR_DB))
    return found


# -- the measures -------------------------------------------------------------------

def level_db(value: float) -> float:
    """A linear amplitude in dB relative to full scale, floored."""
    return max(20 * math.log10(value), FLOOR_DB) if value > 0 else float(FLOOR_DB)


def merge_spans(spans: list[tuple[float, float, float]], tolerance: float) -> list[list[float]]:
    """Spans (start, end, weight) closer than `tolerance` joined, their
    weights summed (for clipping) or maximised (for bursts) by the caller."""
    merged: list[list[float]] = []
    for start, end, weight in sorted(spans):
        if merged and start <= merged[-1][1] + tolerance:
            merged[-1][1] = max(merged[-1][1], end)
            merged[-1][2] += weight
        else:
            merged.append([start, end, weight])
    return merged


def effective_bandwidth(power: numpy.ndarray, rate: int, frame: int) -> float:
    """The frequency above which a long-term power spectrum holds no content.

    Upsampling and lossy encoding leave an edge: the spectrum falls by
    `bandwidth_drop_db` within a few hundred hertz and never comes back.
    Natural spectra fall gradually into a noise floor that reaches the
    Nyquist frequency, and a tone or a loud bass does not move a median.
    Each bin's level is the median of the bins within `bandwidth_median_hz`;
    the edge is the lowest frequency where the level `bandwidth_edge_hz`
    below exceeds both the level the same distance above and everything
    higher by the drop, and the bandwidth is where the level crosses half
    way down that edge. With no edge it is the Nyquist frequency; with no
    power at all, 0. Below about 4.4 kHz the spectrum is too short to hold
    an edge and its margins, and the rate is refused: Nyquist would claim a
    full band nobody looked at."""
    if not numpy.any(power > 0):
        return 0.0
    p = PARAMETERS
    per_bin = rate / frame
    levels = 10 * numpy.log10(numpy.maximum(power, 1e-30))
    width = max(1, round(p["bandwidth_median_hz"] / per_bin)) | 1
    padded = numpy.pad(levels, width // 2, mode="edge")
    median = numpy.median(numpy.lib.stride_tricks.sliding_window_view(padded, width), axis=1)
    edge = max(1, round(p["bandwidth_edge_hz"] / per_bin))
    margin = max(1, round(1000 / per_bin))
    top = len(median) - max(1, round(200 / per_bin))
    if top - margin <= margin:
        raise QualityError(f"at {rate} Hz the spectrum is too short to look for a bandwidth edge")
    # The loudest level at or above each bin, up to just below the Nyquist frequency.
    above = numpy.maximum.accumulate(median[:top][::-1])[::-1]
    drop = p["bandwidth_drop_db"]
    for k in range(margin, top - margin):
        below = median[k - edge]
        if below - median[min(k + edge, top - 1)] >= drop and above[min(k + edge, top - 1)] <= below - drop:
            half = (below + median[min(k + edge, top - 1)]) / 2
            crossing = k - edge + int(numpy.nonzero(median[k - edge:k + edge + 1] >= half)[0].max())
            return float(crossing * per_bin)
    return rate / 2


def detector_frame(rate: int) -> tuple[int, int]:
    """The detectors' frame and hop at a sample rate.

    The settings were chosen at 48 kHz. At twice the rate a frame of the same
    samples is half as long and a tone's second difference a quarter as
    large, so quantisation noise dominates a quiet passage and
    NoiseBurstDetector, whose threshold follows each frame's own spread,
    reports it as bursts (about 15 a second in a -40 dBFS tone at 96 kHz in
    16 bits). The frame is scaled by the power of two nearest to the rate
    over `detector_reference_rate`, never below 1, so it keeps about the
    duration it was chosen at: 1,024 at 88.2 and 96 kHz, 2,048 at 176.4 and
    192 kHz, 512 at 48 kHz and below."""
    p = PARAMETERS
    scale = 2 ** max(0, round(math.log2(rate / p["detector_reference_rate"])))
    return p["detector_frame"] * scale, p["detector_hop"] * scale


class Channel:
    """Essentia's frame detectors on one channel, fed frames in order."""

    def __init__(self, es: Any, rate: int):
        p = PARAMETERS
        frame, hop = detector_frame(rate)
        self.rate = rate
        self.saturation = es.SaturationDetector(frameSize=frame, hopSize=hop, sampleRate=rate,
                                                **_settings(p["saturation"]))
        self.click = es.ClickDetector(frameSize=frame, hopSize=hop, sampleRate=rate, **_settings(p["clicks"]))
        self.discontinuity = es.DiscontinuityDetector(frameSize=frame, hopSize=hop,
                                                      **_settings(p["discontinuities"]))
        self.burst = es.NoiseBurstDetector(**_settings(p["noise_bursts"]))
        gaps = _settings(p["gaps"])
        self.gaps = es.GapsDetector(sampleRate=rate, **gaps)
        self.gap_frame, self.gap_hop = gaps["frameSize"], gaps["hopSize"]
        self.saturated: list[tuple[float, float, float]] = []
        self.clicks: list[tuple[float, float]] = []
        self.discontinuities: list[tuple[float, float]] = []
        self.bursts: list[list[float]] = []  # [start sample, end sample, peak], merged as found
        self.gap_spans: list[tuple[float, float]] = []
        self.burst_tolerance = round(float(p["noise_burst_merge_seconds"]) * rate)
        self.discontinuity_floor = float(p["discontinuity_floor"])
        self.burst_floor = float(p["noise_burst_floor"])

    def detect(self, frame: numpy.ndarray, start: int) -> None:
        """One detector frame, beginning at sample `start`."""
        starts, ends = self.saturation(frame)
        self.saturated += [(float(a), float(b), float(b - a)) for a, b in zip(starts, ends)]
        starts, ends = self.click(frame)
        for a, b in zip(starts, ends):
            first = min(max(round(float(a) * self.rate) - start - 1, 0), len(frame) - 2)
            last = min(max(round(float(b) * self.rate) - start + 2, first + 2), len(frame))
            self.clicks.append((float(a), float(numpy.abs(numpy.diff(frame[first:last])).max())))
        locations, amplitudes = self.discontinuity(frame)
        for index, amplitude in zip(locations, amplitudes):
            if amplitude >= self.discontinuity_floor:
                self.discontinuities.append(((start + int(index)) / self.rate, float(amplitude)))
        indexes = self.burst(frame)
        if len(indexes):
            second = numpy.abs(numpy.diff(frame, 2))
            for index in indexes.astype(int):
                where = start + index
                peak = float(second[min(max(index - 1, 0), len(second) - 1)])
                # A burst smaller than this is quantisation, not noise: a
                # 16-bit sample's step is 3e-5, and in a quiet passage the
                # detector's own threshold falls to a few steps.
                if peak < self.burst_floor:
                    continue
                last = self.bursts[-1] if self.bursts else None
                if last is not None and last[0] - self.burst_tolerance <= where <= last[1] + self.burst_tolerance:
                    last[0], last[1], last[2] = min(last[0], where), max(last[1], where), max(last[2], peak)
                else:
                    self.bursts.append([where, where, peak])

    def detect_gaps(self, frame: numpy.ndarray) -> None:
        starts, ends = self.gaps(frame)
        self.gap_spans += [(float(a), float(b)) for a, b in zip(starts, ends)]


class Hum:
    """Essentia's HumDetector on the channels' mean, in chunks that overlap,
    so that memory is bounded; a tone found in two chunks is joined.

    The detector finds any steady low tone: a chord held for ten minutes is
    reported as three "hum" spans at its notes, each about 1% below the
    note. So a span is a steady tone, not evidence of hum, until the
    detector is calibrated on music."""

    def __init__(self, es: Any, rate: int):
        p = PARAMETERS
        self.es, self.rate = es, rate
        self.chunk = p["hum_chunk_seconds"] * rate
        self.step = (p["hum_chunk_seconds"] - p["hum_overlap_seconds"]) * rate
        self.minimum = p["hum_minimum_seconds"] * rate
        self.settings = _settings(p["hum"])
        self.floor = float(p["hum_salience_floor"])
        self.pending: list[numpy.ndarray] = []
        self.pending_length = 0
        self.offset = 0  # the sample where the pending audio starts
        self.analysed_to = 0  # the sample up to which audio has been examined
        self.tones: list[tuple[float, float, float]] = []
        # A tone is reported only once it has lasted the detector's 10 s
        # `timeWindow`, so below `hum_minimum_seconds`, twice that, the
        # detector could see at most the last seconds of one; such a
        # recording is not examined, and `hum_examined` says so.
        # One detector, reset before each chunk: a new one per chunk leaves
        # memory behind (about 1 MB a chunk in Essentia 2.1b6.dev1438), and
        # its peak grows with the chunk (about 280 MB at 120 s of 48 kHz).
        self.detector = es.HumDetector(sampleRate=rate, **self.settings)

    def feed(self, mono: numpy.ndarray) -> None:
        self.pending.append(mono)
        self.pending_length += len(mono)
        while self.pending_length >= self.chunk:
            audio = numpy.concatenate(self.pending)
            self.examine(audio[:self.chunk])
            rest = audio[self.step:]
            self.pending, self.pending_length = [rest], len(rest)
            self.offset += self.step

    def finish(self) -> bool:
        """Examine what is left; whether any of the recording was examined."""
        if self.pending_length > 0 and self.offset + self.pending_length > self.analysed_to:
            audio = numpy.concatenate(self.pending)
            if len(audio) >= self.minimum:
                self.examine(audio)
        return self.analysed_to > 0

    def examine(self, audio: numpy.ndarray) -> None:
        self.detector.reset()
        _, frequencies, saliences, starts, ends = self.detector(numpy.ascontiguousarray(audio, dtype=numpy.float32))
        base = self.offset / self.rate
        for frequency, salience, start, end in zip(frequencies, saliences, starts, ends):
            if salience >= self.floor:
                self.tones.append((float(frequency), base + float(start), base + float(end)))
        self.analysed_to = self.offset + len(audio)

    def spans(self, seconds: float) -> list[dict[str, Any]]:
        """Tones of about the same frequency less than the detector's own
        `timeContinuity` apart, as one span, ending no later than the
        recording. A tone that crosses from one chunk into the next is found
        again only a `timeWindow` into the next, so the two pieces are that
        far apart and joined here."""
        continuity = self.settings["timeContinuity"]
        joined: list[list[float]] = []
        for frequency, start, end in sorted(self.tones):
            for tone in joined:
                if abs(tone[0] - frequency) <= max(3.0, 0.05 * frequency) and start <= tone[2] + continuity \
                        and end >= tone[1] - continuity:
                    tone[1], tone[2] = min(tone[1], start), max(tone[2], end)
                    break
            else:
                joined.append([frequency, start, end])
        return [{"start": round(min(start, seconds), 3), "end": round(min(end, seconds), 3),
                 "value": round(frequency, 2)}
                for frequency, start, end in sorted(joined, key=lambda tone: (tone[1], tone[0]))]


class Measure:
    """Every measure, fed the decoded blocks in order."""

    def __init__(self, es: Any, rate: int, channels: int, rows: int):
        p = PARAMETERS
        self.es, self.rate, self.channels, self.rows = es, rate, channels, rows
        self.frame, self.hop = detector_frame(rate)
        self.detectors = [Channel(es, rate) for _ in range(channels)]
        self.gap_frame, self.gap_hop = self.detectors[0].gap_frame, self.detectors[0].gap_hop
        self.hum = Hum(es, rate)
        self.stereo = es.FalseStereoDetector(**_settings(p["stereo"]))
        self.silence = 10 ** (p["stereo"]["silenceThreshold"] / 20)
        # The frames grid at this rate: row k is centred on sample k x 2048 at
        # 22,050 Hz, the nearest sample here, over a window as long as a frame.
        self.window = round(GRID["frame"] * rate / GRID["sample_rate"])
        self.correlation = numpy.zeros(rows, dtype="<f4")
        self.peak_db = numpy.zeros(rows, dtype="<f4")
        self.window_level = numpy.zeros(rows, dtype=numpy.float64)
        self.both_active = numpy.zeros(rows, dtype=bool)
        self.spectrum_frame = p["spectrum_frame"]
        self.spectrum_window = numpy.hanning(self.spectrum_frame).astype(numpy.float32)
        self.power = numpy.zeros(self.spectrum_frame // 2 + 1, dtype=numpy.float64)
        self.spectra = 0
        self.total = 0
        self.sums = numpy.zeros(channels, dtype=numpy.float64)
        self.peaks = numpy.zeros(channels, dtype=numpy.float64)
        self.full_scale = 0
        self.pair = numpy.zeros(5, dtype=numpy.float64)  # sums of x, y, xx, yy, xy over the first two channels
        # The decoded audio not yet consumed by every reader, from sample `base`.
        self.buffer = numpy.zeros((0, channels), dtype=numpy.float32)
        self.base = 0
        self.next_frame = 0
        self.next_gap = 0
        self.next_row = 0
        self.next_spectrum = 0

    def row_start(self, k: int) -> int:
        centre = (2 * k * GRID["hop"] * self.rate + GRID["sample_rate"]) // (2 * GRID["sample_rate"])
        return centre - self.window // 2

    def take(self, start: int, length: int) -> numpy.ndarray:
        """Samples [start, start + length), zero outside the audio."""
        out = numpy.zeros((length, self.channels), dtype=numpy.float32)
        first, last = max(start, self.base), min(start + length, self.base + len(self.buffer))
        if last > first:
            out[first - start:last - start] = self.buffer[first - self.base:last - self.base]
        return out

    def feed(self, block: numpy.ndarray) -> None:
        wide = block.astype(numpy.float64)
        self.sums += wide.sum(axis=0)
        magnitude = numpy.abs(block)
        self.peaks = numpy.maximum(self.peaks, magnitude.max(axis=0))
        self.full_scale += int(numpy.count_nonzero(magnitude >= FULL_SCALE))
        x = wide[:, 0]
        y = wide[:, 1] if self.channels > 1 else x
        self.pair += (x.sum(), y.sum(), x @ x, y @ y, x @ y)
        self.hum.feed(block.mean(axis=1, dtype=numpy.float32) if self.channels > 1 else block[:, 0].copy())
        self.buffer = numpy.concatenate((self.buffer, block))
        self.total += len(block)
        self.consume(final=False)

    def consume(self, final: bool) -> None:
        end = self.base + len(self.buffer)
        while self.next_frame + self.frame <= end:
            frames = self.take(self.next_frame, self.frame)
            for channel, detector in enumerate(self.detectors):
                detector.detect(numpy.ascontiguousarray(frames[:, channel]), self.next_frame)
            self.next_frame += self.hop
        while self.next_gap + self.gap_frame <= end:
            frames = self.take(self.next_gap, self.gap_frame)
            for channel, detector in enumerate(self.detectors):
                detector.detect_gaps(numpy.ascontiguousarray(frames[:, channel]))
            self.next_gap += self.gap_hop
        while self.next_row < self.rows and (final or self.row_start(self.next_row) + self.window <= end):
            self.measure_row(self.next_row, self.take(self.row_start(self.next_row), self.window))
            self.next_row += 1
        count = (end - self.next_spectrum) // self.spectrum_frame
        if count > 0:
            frames = self.take(self.next_spectrum, count * self.spectrum_frame)
            frames = frames.reshape(count, self.spectrum_frame, self.channels).transpose(0, 2, 1)
            spectra = numpy.fft.rfft(frames * self.spectrum_window, axis=-1)
            self.power += (spectra.real ** 2 + spectra.imag ** 2).sum(axis=(0, 1))
            self.spectra += count * self.channels
            self.next_spectrum += count * self.spectrum_frame
        keep = min(self.next_frame, self.next_gap, self.next_spectrum,
                   self.row_start(self.next_row) if self.next_row < self.rows else end)
        if keep > self.base:
            self.buffer = self.buffer[keep - self.base:]
            self.base = keep

    def measure_row(self, k: int, window: numpy.ndarray) -> None:
        pair = window[:, :2] if self.channels > 1 else numpy.repeat(window[:, :1], 2, axis=1)
        _, correlation = self.stereo(numpy.ascontiguousarray(pair))
        # Essentia gives 0 for a silent window and NaN when one channel is
        # silent: neither says the channels agree or disagree, so both are 0.
        self.correlation[k] = correlation if math.isfinite(correlation) else 0.0
        self.peak_db[k] = level_db(float(numpy.abs(window).max()))
        power = float(numpy.mean(window.astype(numpy.float64) ** 2))
        self.window_level[k] = 10 * math.log10(power) if power > 0 else -math.inf
        rms = numpy.sqrt(numpy.mean(pair.astype(numpy.float64) ** 2, axis=0))
        self.both_active[k] = bool(numpy.all(rms > self.silence))

    def finish(self) -> dict[str, Any]:
        self.consume(final=True)
        p = PARAMETERS
        n = self.total
        mean_x, mean_y = self.pair[0] / n, self.pair[1] / n
        var_x, var_y = self.pair[2] / n - mean_x ** 2, self.pair[3] / n - mean_y ** 2
        covariance = self.pair[4] / n - mean_x * mean_y
        whole = covariance / math.sqrt(var_x * var_y) if var_x > 0 and var_y > 0 else 0.0
        whole = max(-1.0, min(1.0, whole))
        means = self.sums / n
        worst = int(numpy.abs(means).argmax())
        active = self.both_active
        anti = float(numpy.count_nonzero(self.correlation[active] < 0) / numpy.count_nonzero(active)) \
            if active.any() else 0.0
        levels = self.window_level[numpy.isfinite(self.window_level) & (self.window_level > FLOOR_DB)]
        if len(levels):
            floor = float(numpy.percentile(levels, p["noise_floor_percentile"]))
            loud = float(numpy.percentile(levels, p["signal_percentile"]))
        else:
            floor = loud = float(FLOOR_DB)
        bandwidth = self.bandwidth()
        nyquist = self.rate / 2
        clipping, clipped = self.clipping()
        return {
            "sample_rate": self.rate, "channels": self.channels, "seconds": n / self.rate,
            "sample_peak": level_db(float(self.peaks.max())), "full_scale_samples": self.full_scale,
            "clipped_seconds": round(clipped, 6), "dc_offset": float(means[worst]),
            "whole_correlation": round(float(whole), 6), "anti_phase": round(anti, 6),
            "false_stereo": bool(self.channels > 1 and whole >= float(p["stereo"]["correlationThreshold"])),
            "noise_floor": round(floor, 3), "snr": round(loud - floor, 3),
            "bandwidth": round(bandwidth, 1),
            "band_limited": bool(0 < bandwidth < float(p["band_limited_below"]) * nyquist),
            "hum_examined": self.hum.finish(),
            "_events": {"clipping": clipping, "clicks": self.events("clicks"),
                        "discontinuities": self.events("discontinuities"), "gaps": self.gaps(),
                        "noise_bursts": self.bursts(), "hum": self.hum.spans(n / self.rate)},
        }

    def bandwidth(self) -> float:
        if self.spectra == 0:
            return 0.0
        return effective_bandwidth(self.power / self.spectra, self.rate, self.spectrum_frame)

    def clipping(self) -> tuple[list[dict[str, Any]], float]:
        tolerance = float(PARAMETERS["clipping_merge_seconds"])
        spans, total = [], 0.0
        for channel, detector in enumerate(self.detectors, 1):
            total += sum(weight for _, _, weight in detector.saturated)
            spans += [{"start": round(a, 6), "end": round(b, 6), "value": round(w, 6), "label": str(channel)}
                      for a, b, w in merge_spans(detector.saturated, tolerance)]
        return sorted(spans, key=lambda span: (span["start"], span["label"])), total

    def events(self, name: str) -> list[dict[str, Any]]:
        found = []
        for channel, detector in enumerate(self.detectors, 1):
            seen = -1.0
            for time, value in sorted(getattr(detector, name)):
                if time - seen >= 0.001:  # one event however many frames saw it
                    found.append({"time": round(time, 6), "value": round(value, 6), "label": str(channel)})
                    seen = time
        return sorted(found, key=lambda event: (event["time"], event["label"]))

    def gaps(self) -> list[dict[str, Any]]:
        spans = [{"start": round(a, 6), "end": round(b, 6), "label": str(channel)}
                 for channel, detector in enumerate(self.detectors, 1) for a, b in sorted(set(detector.gap_spans))]
        return sorted(spans, key=lambda span: (span["start"], span["label"]))

    def bursts(self) -> list[dict[str, Any]]:
        spans = [{"start": round(a / self.rate, 6), "end": round((b + 1) / self.rate, 6), "value": round(v, 6),
                  "label": str(channel)}
                 for channel, detector in enumerate(self.detectors, 1) for a, b, v in detector.bursts]
        return sorted(spans, key=lambda span: (span["start"], span["label"]))


SCALARS = ("sample_rate", "channels", "seconds", "integrated", "loudness_range", "true_peak", "sample_peak",
           "full_scale_samples", "clipped_seconds", "dc_offset", "whole_correlation", "anti_phase", "false_stereo",
           "noise_floor", "snr", "bandwidth", "band_limited", "hum_examined")
LISTS = ("clipping", "clicks", "discontinuities", "gaps", "noise_bursts", "hum")
SERIES = ("correlation", "peak_db")


def frames_rows(request: dict[str, Any]) -> int:
    """The rows of the frames grid, from the `frames/samples` the request passes on."""
    samples = request["needs"]["frames/samples"]["value"]
    if type(samples) is not int or samples <= 0:  # bool is an int to isinstance
        raise QualityError(f"frames/samples is {samples!r}, not a positive count")
    return -(-samples // GRID["hop"]) + 1


def ffmpeg_version(ffmpeg: str = "ffmpeg") -> str:
    """The version ffmpeg names on its first line, or "unknown" when that line
    is not "ffmpeg version <v>": provenance says so rather than guess, and a
    build that prints something else still decodes."""
    first = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True).stdout.split("\n", 1)[0].split()
    return first[2] if len(first) > 2 and first[:2] == ["ffmpeg", "version"] else "unknown"


def run(request: dict[str, Any], es: Any, block_frames: int = 1 << 16) -> dict[str, Any]:
    output = Path(request["output_dir"])
    path = request["input"]["path"]
    rows = frames_rows(request)
    rate, channels = probe(path)
    decoder = Decoder(path, rate, channels, block_frames=block_frames)
    measure = Measure(es, rate, channels, rows)
    for block in decoder:
        measure.feed(block)
    if measure.total == 0:
        raise QualityError(f"{path} decodes to no samples")
    expected = measure.total * GRID["sample_rate"] / rate
    samples = request["needs"]["frames/samples"]["value"]
    # Both decodes are of the same stream, so the counts differ only by what
    # resampling rounds: under a sample for MP3, AAC, Opus, and FLAC an hour
    # long. A hop, the grid's own step, refuses another recording without
    # refusing a resampler's rounding.
    if abs(expected - samples) > GRID["hop"]:
        raise QualityError(f"{measure.total} samples at {rate} Hz are not the {samples} frames decoded at "
                           f"{GRID['sample_rate']} Hz: the frames entry is of other audio")
    values = measure.finish()
    values.update(r128_summary(decoder.log))
    lists = values.pop("_events")
    measure.correlation.tofile(output / "correlation.f32")
    measure.peak_db.tofile(output / "peak_db.f32")
    for name in LISTS:
        (output / f"{name}.json").write_text(json.dumps(lists[name]))
    outputs: dict[str, Any] = {name: {"value": values[name]} for name in SCALARS}
    outputs.update({name: {} for name in LISTS})
    outputs.update({name: {"shape": [rows, 1]} for name in SERIES})
    software = [{"name": "ffmpeg", "version": ffmpeg_version()},
                {"name": "essentia", "version": importlib.metadata.version("essentia")},
                {"name": "numpy", "version": numpy.__version__},
                {"name": "python", "version": platform.python_version()}]
    return {"contract": "archive-extractor-result/1", "software": software, "grids": {"frames": GRID},
            "outputs": outputs}


def essentia_module() -> Any:
    """`essentia.standard`, refused unless it is the build the parameters name."""
    try:
        import essentia
        essentia.log.infoActive = False
        essentia.log.warningActive = False
        import essentia.standard
        build = importlib.metadata.version("essentia")
    except (ImportError, importlib.metadata.PackageNotFoundError) as exc:
        raise QualityError(f"Essentia is not importable here: {exc}") from exc
    if build != PARAMETERS["essentia"]:
        raise QualityError(f"quality 1 needs Essentia {PARAMETERS['essentia']}, not {build}")
    return essentia.standard


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: quality.py <request.json>", file=sys.stderr)
        return 2
    try:
        request = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
        if request.get("parameters") != PARAMETERS:
            raise QualityError("quality 1 has fixed settings; the request names others")
        result = run(request, essentia_module())
        (Path(request["output_dir"]) / "result.json").write_text(json.dumps(result))
    except (QualityError, OSError, KeyError, ValueError, RuntimeError) as exc:
        print(f"quality: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
