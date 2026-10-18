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

Essentia's `ClickDetector`, `DiscontinuityDetector`, `NoiseBurstDetector`,
and `GapsDetector` (dropouts) run on each channel, at the stream's own rate,
in frames of 512 samples every 256 at 48 kHz (about 10.7 ms), as long in
time at higher rates (see `detector_frame`), and 2,048 every 1,024 for
gaps; `FalseStereoDetector` on the first two channels in each `frames`
window. Sample peak, DC offset, the whole recording's correlation, the
noise floor, and the long-term spectrum behind the effective bandwidth are
sums numpy keeps as the blocks pass. Essentia has no SNR for a recording
without a noise-only lead-in (its `SNR` assumes one), so the
signal-to-noise ratio here is a level ratio between loud and quiet windows,
stated as such.

Quality 3 is calibrated for music as well as for rehearsal rooms. Quality 2
ran Essentia's detectors as they come, and on a finished master (a
commercial album of 18 tracks, 69 minutes, true peaks at -0.1 dBTP) every
one fired on every track: a limiter's flat tops at -0.1 dBFS were clipping
on 16 tracks with no sample at full scale, drum hits were 40,000 noise
bursts and about 1,000 clicks and discontinuities, and bass notes were 400
spans of hum. So:

- clipping is flat tops at full scale, found with numpy (`Flats`), not
  `SaturationDetector`'s anything flat above -1 dBFS, or long flat tops
  at the recording's own peak (clipped audio turned down afterwards);
- `overs` is its own finding: where the signal between samples, 4x
  oversampled, or a sample of a floating-point file reaches 0 dBTP (what a
  converter or a lossy encoder clips, and what is left of clipping that
  was encoded lossily), with its times, beside the whole-recording
  `true_peak`;
- a click, a discontinuity, or a noise burst is kept only if it stands out
  from the transients around it, and a click or a discontinuity also if
  it stands alone in its own neighbourhood (`Measure` says how both are
  measured). The first is by ratios set on that album and a 56-minute
  live room recording: 16 for clicks, 20 for discontinuities, 30 for
  noise bursts. They leave, of 44,000 events on the album, one click and
  three bursts on one track, where a sampled glitch sound stands up to 60
  times above its surroundings; a click cannot be told from such a sound
  by its waveform. On its own it misses what recurs: crackle at two or
  more a second (a buffer underrun, a sync error) is its own
  surroundings, and among cymbals the surroundings ask for a step no
  click has. The second rule finds those: a click's step against what is
  left of the 150 ms around it once each chunk's largest event is taken
  out, by a ratio of 6, which the album's drums, hats, and glitch sounds
  reach 4.5 at most, but for a sampled tick that recurs through 8 seconds
  of one track (8.5, three discontinuities). What neither finds is a
  click whose step is under 6 times the steps of the 150 ms around it (a
  click of 0.3 of full scale on a hat at -30 dBFS RMS), and ClickDetector
  itself skips frames below its -50 dBFS silence threshold, so it finds
  no single-sample click below about -22 dBFS in -60 dBFS room noise.
  The ratios should be revisited on rehearsal recordings with known
  defects;
- hum is mains hum: narrow, steady peaks at 50 or 60 Hz and their
  harmonics, which persist through the quiet or sound in it (`Hum`), not
  any steady low tone.

The weighing also makes quality faster (3.6 times on that album): the detectors
run only on the frames that could hold an event that would be kept, and
`GapsDetector` only near hops that hold silence; quality 2 spent nearly
all its time running them on every frame. The surroundings are kept
block by block as the audio arrives, so the cost grows with the
recording's length and not with its square.

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
    "clipping": {"level_db": "-0.01", "minimum_samples": 3, "flat_step": "0.000030517578125",
                 "candidate_db": -20, "ceiling_db": "0.05", "ceiling_minimum_samples": 16},
    "clipping_merge_seconds": "0.05",
    "overs": {"oversampling": 4, "half_taps": 16, "kaiser_beta": "8", "level_db": 0, "merge_seconds": "0.05"},
    "context": {"block_seconds": "0.05", "seconds": 1, "percentile": 90, "isolation_samples": 4},
    "clicks": {"order": 12, "detectionThreshold": "30", "powerEstimationThreshold": 10, "silenceThreshold": -50},
    "click_ratio": 16,
    "click_alone_ratio": 6,
    "discontinuities": {"order": 3, "kernelSize": 7, "detectionThreshold": "8", "energyThreshold": "-60",
                        "subFrameSize": 32, "silenceThreshold": -50},
    "discontinuity_floor": "0.05",
    "discontinuity_ratio": 20,
    "discontinuity_alone_ratio": 6,
    "noise_bursts": {"threshold": 8, "alpha": "0.9", "silenceThreshold": -50},
    "noise_burst_merge_seconds": "0.01",
    "noise_burst_floor": "0.001",
    "noise_burst_ratio": 30,
    "gaps": {"frameSize": 2048, "hopSize": 1024, "silenceThreshold": "-50", "prepowerThreshold": "-30",
             "minimumTime": "10", "maximumTime": "3500", "prepowerTime": "40", "postpowerTime": "40",
             "kernelSize": 11, "attackTime": "0.05", "releaseTime": "0.05"},
    "gap_margin_seconds": "0.25",
    "hum": {"mains": [50, 60], "maximum_frequency": 400, "window_seconds": 8, "hop_seconds": 4,
            "tolerance": "0.002", "neighbourhood": "0.15", "prominence_db": 15, "comb_db": 6, "comb_below_db": 20,
            "quiet_db": 20, "quiet_windows": 2},
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
    weights summed."""
    merged: list[list[float]] = []
    for start, end, weight in sorted(spans):
        if merged and start <= merged[-1][1] + tolerance:
            merged[-1][1] = max(merged[-1][1], end)
            merged[-1][2] += weight
        else:
            merged.append([start, end, weight])
    return merged


def merge_peaks(spans: list[tuple[int, int, float]], tolerance: int) -> list[list[float]]:
    """Spans (first sample, last sample, peak) closer than `tolerance`
    samples joined, keeping the larger peak."""
    merged: list[list[float]] = []
    for first, last, peak in sorted(spans):
        if merged and first <= merged[-1][1] + tolerance:
            merged[-1][1] = max(merged[-1][1], last)
            merged[-1][2] = max(merged[-1][2], peak)
        else:
            merged.append([first, last, peak])
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
    192 kHz, 512 at 48 kHz and below. The frame is always two hops, which
    the steps' screening below relies on."""
    p = PARAMETERS
    scale = 2 ** max(0, round(math.log2(rate / p["detector_reference_rate"])))
    return p["detector_frame"] * scale, p["detector_hop"] * scale


def oversampling_kernel() -> numpy.ndarray:
    """The weights that put the points between two samples, 4x oversampled:
    column p - 1 holds, for the samples n - 15 to n + 16, the weights of the
    point p/4 of the way from sample n to n + 1. Each is a sinc under a
    Kaiser window `half_taps` samples either side, scaled to sum to 1 so
    that a constant stays constant. With 16 and a beta of 8 a sine up to
    0.8 of the Nyquist frequency is put within 0.002 dB of its level (8
    samples either side was 0.6 dB out); BS.1770-4's own 48-tap filter is
    not used, and Essentia's TruePeakDetector read the peaks of a mastered
    album 0.04 to 0.08 dB higher."""
    p = PARAMETERS["overs"]
    factor, half, beta = p["oversampling"], p["half_taps"], float(p["kaiser_beta"])
    offsets = numpy.arange(-half + 1, half + 1)
    columns = []
    for phase in range(1, factor):
        t = offsets - phase / factor
        weights = numpy.sinc(t) * numpy.i0(beta * numpy.sqrt(numpy.clip(1 - (t / half) ** 2, 0, None)))
        columns.append(weights / weights.sum())
    return numpy.stack(columns, axis=1)


KERNEL = oversampling_kernel()
# The most any point between samples can exceed the largest sample near it,
# so that only stretches whose samples come within that of the level are
# oversampled.
KERNEL_GAIN = float(numpy.abs(KERNEL).sum(axis=0).max())


class Flats:
    """Clipping in one channel, fed the channel's samples in order: runs
    whose neighbours differ by at most `flat_step`, either at least
    `minimum_samples` long at or above `level_db` (full scale), or at
    least `ceiling_minimum_samples` long at the channel's own peak, within
    `ceiling_db` (clipped audio whose gain was changed afterwards).

    A converter or a fixed-point file clips at full scale, where every
    clipped sample holds one value. A sine whose peak just reaches full
    scale stays within one 16-bit step of its peak for one or two samples
    at 44.1 kHz above about 40 Hz, so three samples is a flat top, not a
    peak. A limiter's ceiling is below full scale (-0.1 dBFS is common),
    and its output there is not clipping: SaturationDetector's -1 dB
    threshold called every mastered track clipped. But clipped audio
    normalised or turned down afterwards keeps its flat tops, at the
    recording's new peak. On a mastered album of 18 tracks the longest run
    at a track's peak was 8 samples (most tracks' runs there were 3 to 6,
    and few); a recording hard-clipped for 10 seconds and then normalised
    to -1 dBFS had 264 runs of 20 samples or more at its peak. So below full
    scale only runs of `ceiling_minimum_samples` (at 48 kHz, scaled by the
    detectors' power of two at higher rates) count: brief clipping in
    normalised audio is missed. Lossy encoding (AAC, MP3) turns flat tops
    into ringing that has no flat runs at all; what is left of it is
    `overs`, where the decoded signal goes past 0 dBTP."""

    def __init__(self, scale: int = 1) -> None:
        p = PARAMETERS["clipping"]
        self.level = 10 ** (float(p["level_db"]) / 20)
        self.candidate = 10 ** (p["candidate_db"] / 20)
        self.step = float(p["flat_step"])
        self.minimum = p["minimum_samples"]
        self.long = p["ceiling_minimum_samples"] * scale
        self.previous = 0.0  # the last sample fed
        self.open: int | None = None  # where a run still going at the last sample started
        self.open_level = 0.0  # that run's first magnitude
        self.position = 0  # samples fed
        self.peak = 0.0  # the largest magnitude fed
        self.runs: list[tuple[int, int, float]] = []  # [start, end) in samples, and its first sample's magnitude

    def feed(self, samples: numpy.ndarray) -> None:
        n = len(samples)
        if n == 0:
            return
        magnitude = numpy.abs(samples)
        self.peak = max(self.peak, float(magnitude.max()))
        at = magnitude >= self.candidate
        before = numpy.empty(n, dtype=numpy.float64)
        before[0], before[1:] = self.previous, samples[:-1]
        at_before = numpy.empty(n, dtype=bool)
        at_before[0], at_before[1:] = self.open is not None, at[:-1]
        joined = at & at_before & (numpy.abs(samples - before) <= self.step)
        follows = numpy.zeros(n, dtype=bool)
        follows[:-1] = joined[1:]
        starts = numpy.flatnonzero(at & ~joined)
        ends = numpy.flatnonzero(at & ~follows) + 1
        if joined[0]:  # the run open at the last block goes on, and ends at the first end
            if ends[0] == n:
                starts, ends = starts[:0], ends[:0]  # through the whole block
            else:
                self.close(self.open, self.position + int(ends[0]), self.open_level)
                self.open = None
                ends = ends[1:]
        elif self.open is not None:
            self.close(self.open, self.position, self.open_level)
            self.open = None
        if len(ends) and ends[-1] == n:  # the last run may go on into the next block
            self.open, self.open_level = self.position + int(starts[-1]), float(magnitude[starts[-1]])
            starts, ends = starts[:-1], ends[:-1]
        # Most samples of loud music are above the candidate level and start
        # a run of one: only the runs long enough to keep reach Python.
        lengths, levels = ends - starts, magnitude[starts]
        keep = (lengths >= self.long) | ((lengths >= self.minimum) & (levels + self.step >= self.level))
        for start, end, level in zip(starts[keep].tolist(), ends[keep].tolist(), levels[keep].tolist()):
            self.close(self.position + start, self.position + end, level)
        self.previous = float(samples[-1])
        self.position += n

    def close(self, start: int, end: int, level: float) -> None:
        length = end - start
        if length >= self.long or (length >= self.minimum and level + self.step >= self.level):
            self.runs.append((start, end, level))

    def finish(self) -> list[tuple[int, int]]:
        if self.open is not None:
            self.close(self.open, self.position, self.open_level)
            self.open = None
        ceiling = self.peak * 10 ** (-float(PARAMETERS["clipping"]["ceiling_db"]) / 20) - self.step
        return [(start, end) for start, end, level in self.runs
                if (end - start >= self.minimum and level + self.step >= self.level)
                or (end - start >= self.long and level >= ceiling)]


class Channel:
    """Essentia's frame detectors on one channel, run only on the frames the
    caller passes them, each found event kept with its own step so that the
    caller can weigh it against its surroundings."""

    def __init__(self, es: Any, rate: int):
        p = PARAMETERS
        frame, hop = detector_frame(rate)
        self.rate = rate
        self.click = es.ClickDetector(frameSize=frame, hopSize=hop, sampleRate=rate, **_settings(p["clicks"]))
        self.discontinuity = es.DiscontinuityDetector(frameSize=frame, hopSize=hop,
                                                      **_settings(p["discontinuities"]))
        self.burst = es.NoiseBurstDetector(**_settings(p["noise_bursts"]))
        gaps = _settings(p["gaps"])
        self.gaps = es.GapsDetector(sampleRate=rate, **gaps)
        self.gap_frame, self.gap_hop = gaps["frameSize"], gaps["hopSize"]
        self.flats = Flats(frame // p["detector_frame"])
        # (sample, value, size): the event, what the detector measured, and
        # what the caller weighs: the largest sample-to-sample step at a
        # click, the largest second difference at a discontinuity.
        self.clicks: list[tuple[int, float, float]] = []
        self.discontinuities: list[tuple[int, float, float]] = []
        self.bursts: list[tuple[int, float]] = []  # (sample, second difference)
        self.gap_spans: list[tuple[float, float]] = []
        self.gap_origin = 0.0
        self.discontinuity_floor = float(p["discontinuity_floor"])
        self.burst_floor = float(p["noise_burst_floor"])

    def detect_steps(self, frame: numpy.ndarray, start: int) -> None:
        """ClickDetector and DiscontinuityDetector on one frame, beginning at
        sample `start`. Neither keeps anything from one frame to the next
        but ClickDetector's count of frames, which it turns into times; it is
        reset so that its times are within this frame."""
        self.click.reset()
        starts, ends = self.click(frame)
        for a, b in zip(starts, ends):
            first = min(max(round(float(a) * self.rate) - 1, 0), len(frame) - 2)
            last = min(max(round(float(b) * self.rate) + 2, first + 2), len(frame))
            step = float(numpy.abs(numpy.diff(frame[first:last])).max())
            self.clicks.append((start + round(float(a) * self.rate), step, step))
        locations, amplitudes = self.discontinuity(frame)
        for index, amplitude in zip(locations, amplitudes):
            if amplitude >= self.discontinuity_floor:
                at = int(index)
                curve = float(numpy.abs(numpy.diff(frame[max(at - 2, 0):at + 3], 2)).max(initial=0.0))
                self.discontinuities.append((start + at, float(amplitude), curve))

    def detect_bursts(self, frame: numpy.ndarray, start: int) -> None:
        indexes = self.burst(frame)
        if len(indexes):
            second = numpy.abs(numpy.diff(frame, 2))
            for index in indexes.astype(int):
                peak = float(second[min(max(index - 1, 0), len(second) - 1)])
                # A burst smaller than this is quantisation, not noise: a
                # 16-bit sample's step is 3e-5, and in a quiet passage the
                # detector's own threshold falls to a few steps.
                if peak >= self.burst_floor:
                    self.bursts.append((start + int(index), peak))

    def detect_gaps(self, frame: numpy.ndarray, start: int, fresh: bool) -> None:
        """GapsDetector on one frame, beginning at sample `start`. It carries
        its envelope from frame to frame, and counts frames into its times,
        so each stretch of frames it is given starts `fresh`: reset, its
        times then counted from that stretch's first sample."""
        if fresh:
            self.gaps.reset()
            self.gap_origin = start / self.rate
        starts, ends = self.gaps(frame)
        self.gap_spans += [(self.gap_origin + float(a), self.gap_origin + float(b)) for a, b in zip(starts, ends)]


class Hum:
    """Mains hum: a narrow, steady peak at 50 or 60 Hz or a harmonic of
    either, up to `maximum_frequency`, in the channels' mean.

    Each `window_seconds` window, every `hop_seconds`, is a Hann-windowed
    spectrum with bins 1/8 Hz apart. A harmonic f is present in a window
    when the spectrum's largest bin within `tolerance` of f is a local
    peak, its interpolated frequency is within `tolerance` of f, it
    stands `prominence_db` above the median of the bins within
    `neighbourhood` of f (the peak's own bins left out), and it is not one
    line of a comb (`comb`); and a harmonic above the second counts only
    in a window where its series' first or second is present, as mains
    hum always has one of them. Hum is a run of windows where one harmonic
    is present, gaps of one window bridged, that is either present in at
    least `quiet_windows` quiet windows (`quiet_db` below the recording's
    95th-percentile window level: room noise between songs, a lead-in), or
    at least `hum_minimum_seconds` long and present in a quiet window when
    the recording has one. A recording with no quiet window (a master, or
    a recording that never stops) has no such guard: its hum rests on
    frequency, narrowness, and length alone.

    Mains frequency holds within about 0.1% of nominal, and a recorder's
    clock adds less; equal-tempered notes at A = 440 Hz sit at least 1.1%
    from every harmonic up to 400 Hz but F4, 0.2% below 350 Hz. So a note
    is hum only when it is held or repeated within 0.2% of a mains harmonic
    for 20 seconds through the quiet, or sounds in the quiet itself for
    two windows (12 seconds); with no quiet window an F4 held a few cents
    sharp, beside hum's 50 Hz, can pass as its seventh harmonic. A held
    bass note beside a harmonic does not hide it: the median ignores a few
    loud bins, and quality 3's first rule, that the peak be above every
    bin within `neighbourhood`, lost hum beside any bass line passing G1
    or A1. A bass line that moves every second spreads its notes over the
    harmonic, and buries hum at -50 dBFS while it plays; the hum is then
    found in the quiet either side. Essentia's HumDetector, which
    quality 2 used, reports any steady tone from 22.5 to 400 Hz, about 1%
    low, and called the bass notes of every track of a mastered album
    hum."""

    def __init__(self, rate: int):
        p = PARAMETERS["hum"]
        self.rate = rate
        self.window = round(p["window_seconds"] * rate)
        self.hop = round(p["hop_seconds"] * rate)
        self.minimum = PARAMETERS["hum_minimum_seconds"] * rate
        self.taper = numpy.hanning(self.window)
        self.tolerance, self.neighbourhood = float(p["tolerance"]), float(p["neighbourhood"])
        self.prominence = p["prominence_db"]
        self.frequencies = sorted({k * mains for mains in p["mains"]
                                   for k in range(1, p["maximum_frequency"] // mains + 1)})
        self.top = int(math.ceil(p["maximum_frequency"] * (1 + self.neighbourhood) * self.window / rate)) + 2
        self.pending: list[numpy.ndarray] = []
        self.pending_length = 0
        self.offset = 0  # the sample where the pending audio starts
        self.starts: list[int] = []  # each examined window's first sample
        self.levels: list[float] = []  # its level, dBFS
        self.present: list[list[bool]] = []  # whether each harmonic is present in it
        self.found: list[list[float]] = []  # each harmonic's interpolated frequency there

    def feed(self, mono: numpy.ndarray) -> None:
        self.pending.append(mono)
        self.pending_length += len(mono)
        while self.pending_length >= self.window:
            audio = numpy.concatenate(self.pending)
            self.examine(audio[:self.window])
            rest = audio[self.hop:]
            self.pending, self.pending_length = [rest], len(rest)
            self.offset += self.hop

    def examine(self, audio: numpy.ndarray) -> None:
        samples = audio.astype(numpy.float64)
        power = numpy.mean(samples ** 2)
        spectrum = numpy.fft.rfft(samples * self.taper)[:self.top]
        energy = spectrum.real ** 2 + spectrum.imag ** 2
        peaks = [self.peak(energy, f) for f in self.frequencies]
        self.starts.append(self.offset)
        self.levels.append(10 * math.log10(power) if power > 0 else float(FLOOR_DB))
        self.present.append([present for present, _ in peaks])
        self.found.append([frequency for _, frequency in peaks])

    def peak(self, energy: numpy.ndarray, f: float) -> tuple[bool, float]:
        """Whether the harmonic f is present in one window's spectrum, and
        the frequency of the peak found for it."""
        scale = self.window / self.rate  # bins per hertz
        inner = (int(math.floor(f * (1 - 3 * self.tolerance) * scale)), int(math.ceil(f * (1 + 3 * self.tolerance) * scale)))
        k = inner[0] + int(numpy.argmax(energy[inner[0]:inner[1] + 1]))
        if not (energy[k] > 0 and energy[k] >= energy[k - 1] and energy[k] >= energy[k + 1]):
            return False, 0.0
        a, b, c = numpy.log(numpy.maximum(energy[k - 1:k + 2], 1e-300))
        curve = a - 2 * b + c
        frequency = (k + (0.5 * (a - c) / curve if curve < 0 else 0.0)) / scale
        outer = (int(f * (1 - self.neighbourhood) * scale), int(f * (1 + self.neighbourhood) * scale))
        around = numpy.concatenate((energy[outer[0]:inner[0]], energy[inner[1] + 1:outer[1] + 1]))
        background = float(numpy.median(around))
        prominent = background <= 0 or 10 * math.log10(energy[k] / background) >= self.prominence
        present = bool(prominent and abs(frequency - f) <= self.tolerance * f)
        return present and not self.comb(energy, k, outer, inner, background), float(frequency)

    def comb(self, energy: numpy.ndarray, k: int, outer: tuple[int, int], inner: tuple[int, int],
             background: float) -> bool:
        """Whether the peak at bin k is one line of a comb. A loop or a
        figure repeating every P seconds is lines 1/P apart, and at 120
        beats a minute one of them is 50 Hz exactly. A line is a local peak
        in the neighbourhood standing `comb_db` above its median and no more
        than `comb_below_db` below k (a drum loop's lines fall away from
        its strongest by more than the `prominence_db` k needs, and noise
        has local peaks `comb_db` up everywhere); k is one of them when, for
        the distance d
        to any line, there are lines d either side of k and 2d either side
        (each within a bin). Notes are not spaced so evenly around a mains
        harmonic, so a bass note beside the hum, however loud, does not
        hide it. A comb whose lines fall within the peak's own bins (0.6%
        of f: at 50 Hz, a loop longer than about 2 seconds) cannot be told
        from hum here."""
        p = PARAMETERS["hum"]
        line = max(background * 10 ** (p["comb_db"] / 10), energy[k] * 10 ** (-p["comb_below_db"] / 10))
        lines = numpy.zeros(len(energy), dtype=bool)
        middle = energy[outer[0]:outer[1] + 1]
        lines[outer[0] + 1:outer[1]] = (middle[1:-1] >= middle[:-2]) & (middle[1:-1] >= middle[2:]) & \
            (middle[1:-1] >= line)
        lines[inner[0]:inner[1] + 1] = False

        def near(centre: int) -> bool:
            return bool(lines[max(centre - 1, 0):centre + 2].any())
        for d in sorted({abs(int(i) - k) for i in numpy.flatnonzero(lines)}):
            if near(k - d) and near(k + d) and near(k - 2 * d) and near(k + 2 * d):
                return True
        return False

    def examined(self, total: int) -> bool:
        """Whether hum was looked for: a recording shorter than
        `hum_minimum_seconds` cannot hold it."""
        return total >= self.minimum and bool(self.starts)

    def spans(self, total: int) -> list[dict[str, Any]]:
        if not self.examined(total):
            return []
        present = numpy.array(self.present, dtype=bool)
        found = numpy.array(self.found)
        levels = numpy.array(self.levels)
        sounding = levels > FLOOR_DB
        quiet = numpy.zeros(len(levels), dtype=bool)
        if sounding.any():
            loud = float(numpy.percentile(levels[sounding], PARAMETERS["signal_percentile"]))
            quiet = sounding & (levels <= loud - PARAMETERS["hum"]["quiet_db"])
        index = {f: i for i, f in enumerate(self.frequencies)}
        spans: set[tuple[float, float, float]] = set()
        for mains in PARAMETERS["hum"]["mains"]:
            series = present[:, index[mains]] | present[:, index[2 * mains]]
            for f in self.frequencies:
                if f % mains:
                    continue
                held_here = present[:, index[f]] & series
                for first, last in runs(held_here):
                    start, end = self.starts[first], min(self.starts[last] + self.window, total)
                    in_quiet = int(numpy.count_nonzero(quiet[first:last + 1] & held_here[first:last + 1]))
                    long_enough = end - start >= self.minimum and (in_quiet > 0 or not quiet.any())
                    if not (long_enough or in_quiet >= PARAMETERS["hum"]["quiet_windows"]):
                        continue
                    held = present[first:last + 1, index[f]]
                    frequency = float(numpy.median(found[first:last + 1, index[f]][held]))
                    spans.add((round(start / self.rate, 3), round(end / self.rate, 3), round(frequency, 2)))
        return [{"start": start, "end": end, "value": value} for start, end, value in sorted(spans)]


def runs(mask: numpy.ndarray) -> list[tuple[int, int]]:
    """The first and last index of each run of true values, a single false
    between two trues bridged."""
    found: list[tuple[int, int]] = []
    for i in numpy.flatnonzero(mask).tolist():
        if found and i - found[-1][1] <= 2:
            found[-1] = (found[-1][0], i)
        else:
            found.append((i, i))
    return found


class Rows:
    """A float array [rows, channels] that grows as rows are appended."""

    def __init__(self, channels: int):
        self.data = numpy.zeros((1024, channels), dtype=numpy.float32)
        self.length = 0

    def append(self, rows: numpy.ndarray) -> None:
        need = self.length + len(rows)
        if need > len(self.data):
            grown = numpy.zeros((max(need, 2 * len(self.data)), self.data.shape[1]), dtype=numpy.float32)
            grown[:self.length] = self.data[:self.length]
            self.data = grown
        self.data[self.length:need] = rows
        self.length = need

    def __len__(self) -> int:
        return self.length

    @property
    def rows(self) -> numpy.ndarray:
        return self.data[:self.length]


def context(maxima: numpy.ndarray, first: int, last: int, reach: int, percentile: float) -> numpy.ndarray:
    """For blocks `first` to `last` - 1, the percentile of the blocks'
    maxima within `reach` blocks either side, the block itself and its two
    neighbours left out (an event and its own decay are not its context);
    0 where there are no such blocks."""
    total, channels = maxima.shape
    padded = numpy.full((last - first + 2 * reach, channels), numpy.nan, dtype=numpy.float64)
    low, high = max(first - reach, 0), min(last + reach, total)
    padded[low - (first - reach):high - (first - reach)] = maxima[low:high]
    windows = numpy.lib.stride_tricks.sliding_window_view(padded, 2 * reach + 1, axis=0)[:last - first]
    keep = numpy.r_[0:reach - 1, reach + 2:2 * reach + 1]
    windows = windows[:, :, keep]
    out = numpy.zeros((last - first, channels))
    partial = numpy.isnan(windows).any(axis=(1, 2))
    if (~partial).any():
        out[~partial] = numpy.percentile(windows[~partial], percentile, axis=-1)
    for row in numpy.flatnonzero(partial):
        for channel in range(channels):
            values = windows[row, channel][~numpy.isnan(windows[row, channel])]
            out[row, channel] = numpy.percentile(values, percentile) if len(values) else 0.0
    return out


def nearby(maxima: numpy.ndarray, first: int, last: int) -> numpy.ndarray:
    """For blocks `first` to `last` - 1, the largest of the block's maxima
    and its two neighbours' (maxima are never negative)."""
    low, high = max(first - 1, 0), min(last + 1, len(maxima))
    padded = numpy.zeros((last - first + 2, maxima.shape[1]), dtype=numpy.float64)
    padded[low - (first - 1):high - (first - 1)] = maxima[low:high]
    return numpy.maximum(numpy.maximum(padded[:-2], padded[1:-1]), padded[2:])


class Measure:
    """Every measure, fed the decoded blocks in order.

    Clicks, discontinuities, and noise bursts are judged against their
    surroundings. The samples are cut into chunks of one detector hop, and
    each chunk keeps its largest sample-to-sample step and second
    difference; a block of chunks about `context.block_seconds` long keeps
    their maxima, and a block's context is the `context.percentile`th
    percentile of the blocks' maxima within `context.seconds` either side.
    An event is kept if its step (its second difference, for a burst)
    exceeds its ratio times its block's context. A drum hit, a pick, or a
    slap recurs, so the blocks around it hold steps as large; a click, a
    dropout's edge, or a burst of noise stands out from them.

    A click or a discontinuity is also kept if it stands alone. Each chunk
    also keeps its largest step and second difference more than
    `context.isolation_samples` (at 48 kHz) from its largest one: what is
    left of it once one event is taken out. A click or a discontinuity is
    kept if its step exceeds `click_alone_ratio` (its second difference
    `discontinuity_alone_ratio`) times the largest of these in its block
    and the two beside it. A drum hit, a hat, or a pick has many steps
    nearly as large as its largest within a few milliseconds, and a
    single-sample click has none, however often it recurs: crackle is
    found in the quiet and between the hits of a band.

    The detectors then run only on frames that could hold such an event: a
    frame whose largest step and second difference exceed no ratio times
    the smaller context of the blocks it touches holds no event that would
    be kept. Frames are therefore examined `context.seconds` after they are
    decoded, once the context to their right is known."""

    def __init__(self, es: Any, rate: int, channels: int, rows: int, screen: bool = True):
        p = PARAMETERS
        self.es, self.rate, self.channels, self.rows = es, rate, channels, rows
        self.screen = screen  # False runs every detector on every frame: for the tests, which compare
        self.frame, self.hop = detector_frame(rate)
        self.detectors = [Channel(es, rate) for _ in range(channels)]
        self.gap_frame, self.gap_hop = self.detectors[0].gap_frame, self.detectors[0].gap_hop
        self.hum = Hum(rate)
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
        # The surroundings: each chunk's largest step and second difference.
        c = p["context"]
        self.chunk_steps, self.chunk_curves = Rows(channels), Rows(channels)
        # The same chunks' largest step and second difference away from their
        # largest: what is left of a chunk once one event is taken out.
        self.chunk_steps_alone, self.chunk_curves_alone = Rows(channels), Rows(channels)
        self.isolation = int(c["isolation_samples"]) * self.hop // p["detector_hop"]
        self.block_chunks = max(1, round(float(c["block_seconds"]) * rate / self.hop))
        self.block = self.block_chunks * self.hop  # samples
        self.reach = max(2, round(c["seconds"] * rate / self.block))
        self.percentile = c["percentile"]
        # Each complete block's maxima of the four, kept as the chunks arrive.
        self.block_rows = {name: Rows(channels) for name in ("steps", "curves", "steps_alone", "curves_alone")}
        self.step_context, self.curve_context = Rows(channels), Rows(channels)
        self.step_alone, self.curve_alone = Rows(channels), Rows(channels)
        self.ratios = {"clicks": float(p["click_ratio"]), "discontinuities": float(p["discontinuity_ratio"]),
                       "noise_bursts": float(p["noise_burst_ratio"])}
        self.alone_ratios = {"clicks": float(p["click_alone_ratio"]),
                             "discontinuities": float(p["discontinuity_alone_ratio"])}
        self.examined = {"steps": 0, "bursts": 0, "gaps": 0}  # frames the detectors ran on, per channel
        gaps = PARAMETERS["gaps"]
        self.gap_silence = 10 ** (float(gaps["silenceThreshold"]) / 20)
        self.gap_quiet_needed = min(self.gap_hop, round(float(gaps["minimumTime"]) / 1000 * rate)) // 2
        self.gap_margin = math.ceil(float(PARAMETERS["gap_margin_seconds"]) * rate / self.gap_hop)
        self.gap_quiet = Rows(channels)  # per hop and channel, whether it holds that many samples below silence
        self.gap_follows = [-1] * channels  # the frame each channel's detector would take next
        # Overs, per channel: (first sample, last sample, peak).
        self.over_level = 10 ** (PARAMETERS["overs"]["level_db"] / 20)
        self.over_runs: list[list[tuple[int, int, float]]] = [[] for _ in range(channels)]
        self.over_tolerance = round(float(PARAMETERS["overs"]["merge_seconds"]) * rate)
        self.half = KERNEL.shape[0] // 2
        # The decoded audio not yet consumed by every reader, from sample `base`.
        self.buffer = numpy.zeros((0, channels), dtype=numpy.float32)
        self.base = 0
        self.next_frame = 0
        self.next_gap = 0
        self.next_row = 0
        self.next_spectrum = 0
        self.next_over = 0

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
        for channel, detector in enumerate(self.detectors):
            detector.flats.feed(block[:, channel])
        self.hum.feed(block.mean(axis=1, dtype=numpy.float32) if self.channels > 1 else block[:, 0].copy())
        self.buffer = numpy.concatenate((self.buffer, block))
        self.total += len(block)
        self.consume(final=False)

    def consume(self, final: bool) -> None:
        end = self.base + len(self.buffer)
        self.surroundings(final)
        self.screened_frames(final)
        self.screened_gaps(final)
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
        self.overs(final)
        keep = min(self.next_frame, self.next_gap, self.next_spectrum, self.next_over - self.half,
                   len(self.chunk_steps) * self.hop - 1,
                   self.row_start(self.next_row) if self.next_row < self.rows else end)
        if keep > self.base:
            self.buffer = self.buffer[keep - self.base:]
            self.base = keep

    def surroundings(self, final: bool) -> None:
        """Each new chunk's largest step and second difference, with and
        without its largest event, and the contexts of each block whose
        surroundings are now known."""
        done = len(self.chunk_steps)
        start = done * self.hop
        # A chunk's second differences need the sample after it.
        count = (-(-(self.total - start) // self.hop) if final
                 else (self.base + len(self.buffer) - 1 - start) // self.hop)
        if count > 0:
            samples = self.take(start - 1, count * self.hop + 2).astype(numpy.float64)
            steps = numpy.abs(samples[1:-1] - samples[:-2])
            curves = numpy.abs(samples[2:] - 2 * samples[1:-1] + samples[:-2])
            if final:  # nothing past the end is a step
                beyond = start + numpy.arange(count * self.hop)
                steps[beyond >= self.total] = 0
                curves[beyond + 1 >= self.total] = 0
            for values, largest, alone in ((steps, self.chunk_steps, self.chunk_steps_alone),
                                           (curves, self.chunk_curves, self.chunk_curves_alone)):
                chunks = values.reshape(count, self.hop, self.channels)
                largest.append(chunks.max(axis=1))
                alone.append(self.without_largest(chunks))  # last: it overwrites the chunks
        self.blocks(final)
        blocks = len(self.block_rows["steps"])
        ready = blocks if final else blocks - self.reach
        first = len(self.step_context)
        if ready <= first:
            return
        rows = {name: values.rows for name, values in self.block_rows.items()}
        self.step_context.append(context(rows["steps"], first, ready, self.reach, self.percentile))
        self.curve_context.append(context(rows["curves"], first, ready, self.reach, self.percentile))
        self.step_alone.append(nearby(rows["steps_alone"], first, ready))
        self.curve_alone.append(nearby(rows["curves_alone"], first, ready))

    def without_largest(self, chunks: numpy.ndarray) -> numpy.ndarray:
        """Each chunk's largest value more than `isolation` samples from its
        largest one: chunks [count, hop, channels] to [count, channels].
        The chunks are overwritten there."""
        count, hop, channels = chunks.shape
        where = chunks.argmax(axis=1)[:, None, :] + numpy.arange(-self.isolation, self.isolation + 1)[None, :, None]
        chunks[numpy.arange(count)[:, None, None], numpy.clip(where, 0, hop - 1),
               numpy.arange(channels)[None, None, :]] = 0
        return chunks.max(axis=1)

    def blocks(self, final: bool) -> None:
        """The maxima of each block whose chunks are all known (with `final`,
        of the last block too, however few chunks it has), appended to
        `block_rows`: each chunk is reduced once, so the cost grows with the
        recording, not with its square."""
        chunks = len(self.chunk_steps)
        first = len(self.block_rows["steps"])
        last = -(-chunks // self.block_chunks) if final else chunks // self.block_chunks
        if last <= first:
            return
        sources = {"steps": self.chunk_steps, "curves": self.chunk_curves,
                   "steps_alone": self.chunk_steps_alone, "curves_alone": self.chunk_curves_alone}
        for name, source in sources.items():
            part = source.rows[first * self.block_chunks:last * self.block_chunks]
            padded = numpy.zeros(((last - first) * self.block_chunks, self.channels), dtype=numpy.float32)
            padded[:len(part)] = part
            self.block_rows[name].append(self.reduce(padded))

    def reduce(self, chunks: numpy.ndarray) -> numpy.ndarray:
        """Whole blocks of chunks [blocks x block_chunks, channels] to each
        block's maxima."""
        return chunks.reshape(-1, self.block_chunks, self.channels).max(axis=1)

    def screened_frames(self, final: bool) -> None:
        """The detectors, on every frame whose context is known and that could
        hold an event that would be kept."""
        end = self.base + len(self.buffer)
        known = len(self.step_context) * self.block  # samples whose block context is known
        last = min(end, known) if not final else end
        count = (last - self.frame - self.next_frame) // self.hop + 1
        count = min(count, len(self.chunk_steps) - 1 - self.next_frame // self.hop)
        if count <= 0:
            return
        starts = self.next_frame + self.hop * numpy.arange(count)
        chunk = starts // self.hop
        steps = numpy.maximum(self.chunk_steps.rows[chunk], self.chunk_steps.rows[chunk + 1])
        curves = numpy.maximum(self.chunk_curves.rows[chunk], self.chunk_curves.rows[chunk + 1])
        near, far = starts // self.block, (starts + self.frame - 1) // self.block
        step_context = numpy.minimum(self.step_context.rows[near], self.step_context.rows[far])
        curve_context = numpy.minimum(self.curve_context.rows[near], self.curve_context.rows[far])
        step_alone = numpy.minimum(self.step_alone.rows[near], self.step_alone.rows[far])
        curve_alone = numpy.minimum(self.curve_alone.rows[near], self.curve_alone.rows[far])
        if self.screen:
            run_steps = (steps > self.ratios["clicks"] * step_context) | \
                (curves > self.ratios["discontinuities"] * curve_context) | \
                (steps > self.alone_ratios["clicks"] * step_alone) | \
                (curves > self.alone_ratios["discontinuities"] * curve_alone)
            run_bursts = (curves > self.ratios["noise_bursts"] * curve_context) & \
                (curves >= self.detectors[0].burst_floor)
        else:
            run_steps = run_bursts = numpy.ones((count, self.channels), dtype=bool)
        frames_from = self.next_frame
        for index in numpy.flatnonzero((run_steps | run_bursts).any(axis=1)).tolist():
            start = frames_from + index * self.hop
            frames = self.take(start, self.frame)
            for channel, detector in enumerate(self.detectors):
                samples = numpy.ascontiguousarray(frames[:, channel])
                if run_steps[index, channel]:
                    detector.detect_steps(samples, start)
                    self.examined["steps"] += 1
                if run_bursts[index, channel]:
                    detector.detect_bursts(samples, start)
                    self.examined["bursts"] += 1
        self.next_frame = frames_from + count * self.hop

    def screened_gaps(self, final: bool) -> None:
        """GapsDetector on the stretches of frames that could hold a gap.

        A gap is at least `minimumTime` below `silenceThreshold`, so one of
        the hops it touches has at least half that many samples below the
        threshold. GapsDetector runs on every frame within `gap_margin_seconds`
        of such a hop, longer than the power it measures before and after
        a gap, and nowhere else: most of a recording has no quiet hop."""
        end = self.base + len(self.buffer)
        hop = self.gap_hop
        known = len(self.gap_quiet)
        count = (end - known * hop) // hop
        if count > 0:
            below = numpy.abs(self.take(known * hop, count * hop)) < self.gap_silence
            self.gap_quiet.append(numpy.count_nonzero(below.reshape(count, hop, self.channels), axis=1)
                                  >= self.gap_quiet_needed)
            known += count
        margin = self.gap_margin
        first = self.next_gap // hop
        # Frame f covers hops f and f + 1; it is wanted near a quiet hop.
        last = (end - self.gap_frame) // hop  # the last frame inside the audio
        if not final:
            last = min(last, known - 2 - margin)
        if last < first:
            return
        low, high = max(first - margin, 0), min(last + 1 + margin + 1, known)
        padded = numpy.zeros((last - first + 2 * margin + 2, self.channels), dtype=bool)
        padded[low - (first - margin):high - (first - margin)] = self.gap_quiet.rows[low:high].astype(bool)
        near = numpy.lib.stride_tricks.sliding_window_view(padded, 2 * margin + 2, axis=0).any(axis=-1)
        if not self.screen:
            near[:] = True
        for index in numpy.flatnonzero(near.any(axis=1)).tolist():
            f = first + index
            frames = self.take(f * hop, self.gap_frame)
            for channel, detector in enumerate(self.detectors):
                if near[index, channel]:
                    detector.detect_gaps(numpy.ascontiguousarray(frames[:, channel]), f * hop,
                                         fresh=self.gap_follows[channel] != f)
                    self.gap_follows[channel] = f + 1
                    self.examined["gaps"] += 1
        self.next_gap = (last + 1) * hop

    def overs(self, final: bool) -> None:
        """Where the signal between samples, 4x oversampled, reaches the
        over level: only stretches whose samples come within the kernel's
        gain of it are oversampled."""
        end = self.base + len(self.buffer)
        stop = self.total if final else end - self.half
        count = (stop - self.next_over) // self.hop if not final else -(-(stop - self.next_over) // self.hop)
        if count <= 0:
            return
        first = self.next_over
        rows = count * self.hop
        samples = self.take(first - self.half + 1, rows + 2 * self.half - 1)
        magnitude = numpy.abs(samples)
        # Row n's window is samples[n - first : n - first + 2 * half]; a
        # chunk's windows lie within it and the next chunk.
        chunk_max = numpy.vstack([magnitude[:count * self.hop].reshape(count, self.hop, self.channels).max(axis=1),
                                  magnitude[count * self.hop:].max(axis=0, keepdims=True)])
        reach = numpy.maximum(chunk_max[:-1], chunk_max[1:])
        for channel in range(self.channels):
            candidates = numpy.flatnonzero(reach[:, channel] * KERNEL_GAIN >= self.over_level)
            if not len(candidates):
                continue
            windows = numpy.lib.stride_tricks.sliding_window_view(samples[:, channel], 2 * self.half)
            at = (candidates[:, None] * self.hop + numpy.arange(self.hop)).ravel()
            at = at[first + at < self.total]
            between = numpy.abs(windows[at].astype(numpy.float64) @ KERNEL).max(axis=1)
            own = magnitude[at + self.half - 1, channel]
            peak = numpy.maximum(between, own)
            over = numpy.flatnonzero(peak >= self.over_level)
            if not len(over):
                continue
            # Consecutive overs within the merge tolerance are one run.
            breaks = numpy.flatnonzero(numpy.diff(at[over]) > self.over_tolerance) + 1
            for group in numpy.split(over, breaks):
                self.over_runs[channel].append((first + int(at[group[0]]), first + int(at[group[-1]]),
                                                float(peak[group].max())))
        self.next_over = first + rows

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
            "hum_examined": self.hum.examined(n),
            "_events": {"clipping": clipping, "overs": self.over_spans(),
                        "clicks": self.events("clicks"), "discontinuities": self.events("discontinuities"),
                        "gaps": self.gaps(), "noise_bursts": self.bursts(), "hum": self.hum.spans(n)},
        }

    def bandwidth(self) -> float:
        if self.spectra == 0:
            return 0.0
        return effective_bandwidth(self.power / self.spectra, self.rate, self.spectrum_frame)

    def clipping(self) -> tuple[list[dict[str, Any]], float]:
        tolerance = float(PARAMETERS["clipping_merge_seconds"])
        spans, total = [], 0.0
        for channel, detector in enumerate(self.detectors, 1):
            runs = [(a / self.rate, b / self.rate, (b - a) / self.rate) for a, b in detector.flats.finish()]
            total += sum(weight for _, _, weight in runs)
            spans += [{"start": round(a, 6), "end": round(b, 6), "value": round(w, 6), "label": str(channel)}
                      for a, b, w in merge_spans(runs, tolerance)]
        return sorted(spans, key=lambda span: (span["start"], span["label"])), total

    def over_spans(self) -> list[dict[str, Any]]:
        spans = [{"start": round(a / self.rate, 6), "end": round((b + 1) / self.rate, 6),
                  "value": round(level_db(peak), 3), "label": str(channel)}
                 for channel, found in enumerate(self.over_runs, 1)
                 for a, b, peak in merge_peaks(found, self.over_tolerance)]
        return sorted(spans, key=lambda span: (span["start"], span["label"]))

    def standing_out(self, name: str, channel: int, sample: int, step: float) -> bool:
        """Whether an event's step exceeds its ratio times its block's
        context, or (a click or a discontinuity) its ratio alone times
        what is left of the blocks around it with their largest events
        taken out."""
        rows = (self.step_context if name == "clicks" else self.curve_context).rows
        block = min(sample // self.block, len(rows) - 1)
        if step > self.ratios[name] * float(rows[block, channel]):
            return True
        if name not in self.alone_ratios:
            return False
        alone = (self.step_alone if name == "clicks" else self.curve_alone).rows
        return step > self.alone_ratios[name] * float(alone[block, channel])

    def events(self, name: str) -> list[dict[str, Any]]:
        found = []
        for channel, detector in enumerate(self.detectors):
            seen = -1.0
            for sample, value, step in sorted(getattr(detector, name)):
                time = sample / self.rate
                if time - seen >= 0.001 and self.standing_out(name, channel, sample, step):
                    found.append({"time": round(time, 6), "value": round(value, 6), "label": str(channel + 1)})
                    seen = time  # one event however many frames saw it
        return sorted(found, key=lambda event: (event["time"], event["label"]))

    def gaps(self) -> list[dict[str, Any]]:
        spans = [{"start": round(a, 6), "end": round(b, 6), "label": str(channel)}
                 for channel, detector in enumerate(self.detectors, 1) for a, b in sorted(set(detector.gap_spans))]
        return sorted(spans, key=lambda span: (span["start"], span["label"]))

    def bursts(self) -> list[dict[str, Any]]:
        tolerance = round(float(PARAMETERS["noise_burst_merge_seconds"]) * self.rate)
        spans = []
        for channel, detector in enumerate(self.detectors):
            kept = [(sample, sample, peak) for sample, peak in detector.bursts
                    if self.standing_out("noise_bursts", channel, sample, peak)]
            spans += [{"start": round(a / self.rate, 6), "end": round((b + 1) / self.rate, 6), "value": round(v, 6),
                       "label": str(channel + 1)} for a, b, v in merge_peaks(kept, tolerance)]
        return sorted(spans, key=lambda span: (span["start"], span["label"]))


SCALARS = ("sample_rate", "channels", "seconds", "integrated", "loudness_range", "true_peak", "sample_peak",
           "full_scale_samples", "clipped_seconds", "dc_offset", "whole_correlation", "anti_phase", "false_stereo",
           "noise_floor", "snr", "bandwidth", "band_limited", "hum_examined")
LISTS = ("clipping", "overs", "clicks", "discontinuities", "gaps", "noise_bursts", "hum")
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
        raise QualityError(f"quality needs Essentia {PARAMETERS['essentia']}, not {build}")
    return essentia.standard


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: quality.py <request.json>", file=sys.stderr)
        return 2
    try:
        request = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
        if request.get("parameters") != PARAMETERS:
            raise QualityError("quality has fixed settings; the request names others")
        result = run(request, essentia_module())
        (Path(request["output_dir"]) / "result.json").write_text(json.dumps(result))
    except (QualityError, OSError, KeyError, ValueError, RuntimeError) as exc:
        print(f"quality: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
