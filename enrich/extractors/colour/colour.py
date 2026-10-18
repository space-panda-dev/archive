#!/usr/bin/env python3
"""The colour extractor: a colour-coded waveform's data (ADR 0074).

For every pixel of the waveform peaks that `enrich/media.py` serves as
derived media (ADR 0069, kind `peaks-1`; about ten milliseconds each), it
writes one colour, so a review client can draw the waveform it already
draws, tinted by what is sounding. The peaks themselves stay derived media:
the bank holds no copy of them (ADR 0073).

- `colour`: a `u8` series of [pixels, 3], red, green, and blue, one row per
  peaks pixel, on the grid `peaks`. A pixel's centre is half a pixel into it,
  so the grid is written at twice the source rate (`sample_rate` 2 x rate,
  `hop` and `frame` 2 x samples_per_pixel, `first_centre` samples_per_pixel),
  which puts every centre on an integer sample of that grid.
- `palette`: JSON describing how the colours were made (the mapping, the
  decibel range lightness spans for this recording, and legend stops), so a
  client can draw a key without knowing this program. Its fields are in this
  directory's README; `check_palette` refuses any other shape, and the
  program checks its own palette before writing it.
- `preview`: a PNG of the whole recording, transparent behind the waveform.

Colour comes from the bank's `frames` series, read rather than measured
again: hue from the spectral centroid (log frequency between two limits,
which `enrich/hue_range.py` sets from a set of recordings; see the README) or
from the pitch-class profile (its circular mean, on the circle of fifths or
the chromatic circle), saturation fixed or scaled by how concentrated the
profile is, and lightness from the frame RMS level in dBFS. The frames grid
(about 10.8 rows a second) is interpolated linearly to each pixel's centre.

The peaks grid cannot come from the bank: it needs the source's own rate
and length, and the preview needs every channel's envelope, while `frames`
holds one mono channel at 22,050 Hz reduced to 4,096-sample frames. So this
extractor decodes the content once more with the derived-media decoder
(`enrich/waveform.py`), into a temporary file it deletes. Essentia has no
algorithm for any step here; the features it measured arrive through
`frames`.

Invoked by the runner as `colour.py <request.json>`. Parameters it does not
implement are refused, never approximated.
"""

from __future__ import annotations

import json
import platform
import re
import struct
import subprocess
import sys
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

ENRICH = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ENRICH))

import numpy  # noqa: E402

import derived  # noqa: E402
import waveform  # noqa: E402

NEEDS = ("frames/centroid", "frames/hpcp", "frames/rms_db")
PEAKS_KIND = derived.KINDS["peaks"]
# HPCP's bin 0 is A (440 Hz reference) and bins rise by a semitone.
PITCH_CLASSES = ("A", "A#", "B", "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#")
PALETTE_CONTRACT = "archive-colour-palette/2"
DECIMAL = re.compile(r"-?[0-9]+(\.[0-9]+)?")
# The frames grid's analysis rate: a centroid above its Nyquist frequency cannot occur.
FRAMES_NYQUIST = 11025
# The centroid's hue limits by default, for mastered music (version 3; see the
# README): about the 5th to 95th percentiles of a studio album's frames.
DEFAULT_HUE_HZ = (500, 4000)
# The preview is held whole in memory as RGBA: at most 32 MiB.
MAX_PREVIEW_WIDTH, MAX_PREVIEW_HEIGHT = 8192, 1024


class ColourError(Exception):
    pass


def parameters() -> dict[str, Any]:
    """The default settings, which the manifest must declare exactly."""
    return {"peaks": f"{PEAKS_KIND.name}-{PEAKS_KIND.version}",
            "hue": {"source": "centroid", "low_hz": DEFAULT_HUE_HZ[0], "high_hz": DEFAULT_HUE_HZ[1],
                    "low_hue": 0, "high_hue": 240},
            "saturation": "0.85",
            "lightness": {"source": "rms_db", "reference": "full-scale", "floor_db": -60, "ceiling_db": 0,
                          "darkest": "0.15", "lightest": "0.6"},
            "interpolation": "linear",
            "preview": {"width": 1600, "height": 160}}


# -- settings ----------------------------------------------------------------

@dataclass(frozen=True)
class Settings:
    hue: dict[str, Any]
    saturation: float
    reference: str
    floor_db: float
    ceiling_db: float
    darkest: float
    lightest: float
    width: int
    height: int


def _fraction(value: Any, where: str, low: float, high: float) -> float:
    if not isinstance(value, str) or DECIMAL.fullmatch(value) is None:
        raise ColourError(f"{where} must be decimal text such as \"0.5\", not {value!r}")
    number = float(value)
    if not low <= number <= high:
        raise ColourError(f"{where} is {value}; it must be from {low} to {high}")
    return number


def _integer(value: Any, where: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ColourError(f"{where} must be an integer from {low} to {high}, not {value!r}")
    return value


def _fields(value: Any, where: str, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        got = sorted(value) if isinstance(value, dict) else value
        raise ColourError(f"{where} must have exactly the fields {sorted(fields)}, not {got!r}")
    return value


def settings(value: Any) -> Settings:
    """The request's parameters checked against what this program implements."""
    top = _fields(value, "parameters", set(parameters()))
    expected_peaks = parameters()["peaks"]
    if top["peaks"] != expected_peaks:
        raise ColourError(f"peaks are made by {expected_peaks}, not {top['peaks']!r}")
    if top["interpolation"] != "linear":
        raise ColourError(f"interpolation {top['interpolation']!r} is not implemented; only \"linear\" is")
    hue = top["hue"]
    source = hue.get("source") if isinstance(hue, dict) else None
    if source == "centroid":
        _fields(hue, "hue", {"source", "low_hz", "high_hz", "low_hue", "high_hue"})
        low = _integer(hue["low_hz"], "hue.low_hz", 1, FRAMES_NYQUIST - 1)
        _integer(hue["high_hz"], "hue.high_hz", low + 1, FRAMES_NYQUIST)
        _integer(hue["low_hue"], "hue.low_hue", 0, 360)
        _integer(hue["high_hue"], "hue.high_hue", 0, 360)
    elif source == "chroma":
        _fields(hue, "hue", {"source", "order", "reference_hue"})
        if hue["order"] not in ("fifths", "chromatic"):
            raise ColourError(f"hue.order must be \"fifths\" or \"chromatic\", not {hue['order']!r}")
        _integer(hue["reference_hue"], "hue.reference_hue", 0, 359)
    else:
        raise ColourError(f"hue.source must be \"centroid\" or \"chroma\", not {source!r}")
    light = _fields(top["lightness"], "lightness",
                    {"source", "reference", "floor_db", "ceiling_db", "darkest", "lightest"})
    if light["source"] != "rms_db":
        raise ColourError(f"lightness.source {light['source']!r} is not implemented; only \"rms_db\" is")
    if light["reference"] not in ("full-scale", "recording"):
        raise ColourError(f"lightness.reference must be \"full-scale\" or \"recording\", not {light['reference']!r}")
    floor = _integer(light["floor_db"], "lightness.floor_db", -120, -1)
    ceiling = _integer(light["ceiling_db"], "lightness.ceiling_db", floor + 1, 0)
    darkest = _fraction(light["darkest"], "lightness.darkest", 0.0, 1.0)
    lightest = _fraction(light["lightest"], "lightness.lightest", 0.0, 1.0)
    if darkest >= lightest:
        raise ColourError(f"lightness.darkest ({light['darkest']}) must be below lightest ({light['lightest']})")
    preview = _fields(top["preview"], "preview", {"width", "height"})
    return Settings(hue=dict(hue), saturation=_fraction(top["saturation"], "saturation", 0.0, 1.0),
                    reference=light["reference"], floor_db=float(floor), ceiling_db=float(ceiling),
                    darkest=darkest, lightest=lightest,
                    width=_integer(preview["width"], "preview.width", 16, MAX_PREVIEW_WIDTH),
                    height=_integer(preview["height"], "preview.height", 8, MAX_PREVIEW_HEIGHT))


# -- the bank's series -------------------------------------------------------

def read_need(request: Mapping[str, Any], feature: str, columns: int) -> tuple[numpy.ndarray, dict[str, int]]:
    """A needed `frames` series and its grid, checked against what the request describes."""
    try:
        need = request["needs"][feature]
    except KeyError:
        raise ColourError(f"the request does not provide {feature}") from None
    if need.get("kind") != "series" or need.get("dtype") != "f32" or need.get("columns") != columns:
        raise ColourError(f"{feature} is not an f32 series of {columns} columns")
    values = numpy.fromfile(need["path"], dtype="<f4")
    rows = need["shape"][0]
    if values.size != rows * columns:
        raise ColourError(f"{need['path']} holds {values.size} values, not the {rows} x {columns} described")
    return values.reshape(rows, columns), need["grid"]


def frame_positions(pixels: int, samples_per_pixel: int, rate: int, grid: Mapping[str, int]) -> numpy.ndarray:
    """Each pixel's centre as a fractional row of a series on `grid`."""
    centres = (numpy.arange(pixels, dtype=numpy.float64) + 0.5) * samples_per_pixel / rate
    return (centres * grid["sample_rate"] - grid["first_centre"]) / grid["hop"]


def check_lengths(pixels: int, samples_per_pixel: int, rate: int, rows: int, grid: Mapping[str, int]) -> None:
    """Refuse series that do not cover the same audio as the peaks: within a
    frame hop at the end, as the two decodes resample differently."""
    peaks_seconds = pixels * samples_per_pixel / rate
    frames_seconds = (rows - 1) * grid["hop"] / grid["sample_rate"]
    slack = 2 * grid["hop"] / grid["sample_rate"] + samples_per_pixel / rate
    if abs(frames_seconds - peaks_seconds) > slack:
        raise ColourError(f"the frames series cover {frames_seconds:.3f} s and the peaks {peaks_seconds:.3f} s; "
                          "they are not measures of the same audio")


def interpolate(series: numpy.ndarray, positions: numpy.ndarray) -> numpy.ndarray:
    """Linear interpolation of each column at fractional rows, held at the ends."""
    clipped = numpy.clip(positions, 0, len(series) - 1)
    below = numpy.floor(clipped).astype(numpy.int64)
    above = numpy.minimum(below + 1, len(series) - 1)
    weight = (clipped - below)[:, None]
    values = series.astype(numpy.float64)
    return values[below] * (1 - weight) + values[above] * weight


# -- the mapping -------------------------------------------------------------

def centroid_hue(centroid_hz: numpy.ndarray, hue: Mapping[str, Any]) -> numpy.ndarray:
    """Hue in degrees from log frequency: low_hz and below is low_hue, high_hz
    and above high_hue."""
    low, high = numpy.log2(hue["low_hz"]), numpy.log2(hue["high_hz"])
    position = (numpy.log2(numpy.maximum(centroid_hz, hue["low_hz"])) - low) / (high - low)
    position = numpy.clip(position, 0.0, 1.0)
    return hue["low_hue"] + position * (hue["high_hue"] - hue["low_hue"])


def pitch_angles(order: str) -> numpy.ndarray:
    """The angle in degrees of each HPCP bin: chromatic steps of 30, or the
    circle of fifths (a semitone is seven fifths, 210 degrees)."""
    step = 30 if order == "chromatic" else 210
    return (numpy.arange(12) * step) % 360


def chroma_hue(hpcp: numpy.ndarray, hue: Mapping[str, Any]) -> tuple[numpy.ndarray, numpy.ndarray]:
    """Hue from the profile's circular mean, and its concentration: 1 for one
    pitch class, 0 for all twelve equally (or for silence)."""
    angles = numpy.deg2rad(pitch_angles(hue["order"]))
    weights = numpy.maximum(hpcp, 0.0)
    x = weights @ numpy.cos(angles)
    y = weights @ numpy.sin(angles)
    total = weights.sum(axis=1)
    concentration = numpy.where(total > 0, numpy.hypot(x, y) / numpy.where(total > 0, total, 1), 0.0)
    degrees = (numpy.rad2deg(numpy.arctan2(y, x)) + hue["reference_hue"]) % 360
    return degrees, numpy.clip(concentration, 0.0, 1.0)


def lightness_range(rms_db: numpy.ndarray, chosen: Settings) -> tuple[float, float]:
    """The decibels lightness spans: as configured, or (reference
    "recording") shifted so the recording's loudest frame is the ceiling.
    The shifted ceiling is never below `floor_db`: a recording quieter
    throughout than the floor is not raised to the lightest colour."""
    if rms_db.size == 0:
        raise ColourError("there are no frames to take a lightness range from")
    if chosen.reference == "full-scale":
        return chosen.floor_db, chosen.ceiling_db
    ceiling = max(float(rms_db.max()), chosen.floor_db)
    return ceiling - (chosen.ceiling_db - chosen.floor_db), ceiling


def lightness(rms_db: numpy.ndarray, floor_db: float, ceiling_db: float, chosen: Settings) -> numpy.ndarray:
    position = numpy.clip((rms_db - floor_db) / (ceiling_db - floor_db), 0.0, 1.0)
    return chosen.darkest + position * (chosen.lightest - chosen.darkest)


def hsl_to_rgb(hue: numpy.ndarray, saturation: numpy.ndarray, light: numpy.ndarray) -> numpy.ndarray:
    """CSS's HSL to sRGB, as [n, 3] bytes."""
    hue = numpy.asarray(hue, dtype=numpy.float64) % 360
    saturation = numpy.broadcast_to(numpy.asarray(saturation, dtype=numpy.float64), hue.shape)
    light = numpy.asarray(light, dtype=numpy.float64)
    a = saturation * numpy.minimum(light, 1 - light)
    channels = []
    for n in (0, 8, 4):
        k = (n + hue / 30) % 12
        channels.append(light - a * numpy.clip(numpy.minimum(k - 3, 9 - k), -1, 1))
    return numpy.clip(numpy.rint(numpy.stack(channels, axis=-1) * 255), 0, 255).astype(numpy.uint8)


def hex_colour(rgb: numpy.ndarray) -> str:
    return "#" + "".join(f"{int(v):02x}" for v in rgb)


def colours(chosen: Settings, centroid: numpy.ndarray, hpcp: numpy.ndarray,
            rms_db: numpy.ndarray, floor_db: float, ceiling_db: float) -> numpy.ndarray:
    """One colour per row of the interpolated series."""
    if chosen.hue["source"] == "centroid":
        hue = centroid_hue(centroid, chosen.hue)
        saturation = numpy.full(hue.shape, chosen.saturation)
    else:
        hue, concentration = chroma_hue(hpcp, chosen.hue)
        saturation = chosen.saturation * concentration
    return hsl_to_rgb(hue, saturation, lightness(rms_db, floor_db, ceiling_db, chosen))


GRID_FIELDS = ("sample_rate", "hop", "frame", "first_centre")
PEAKS_FORMAT = "audiowaveform binary version 2, 8-bit"
CHROMA_SATURATION = "saturation x the pitch-class profile's concentration"


def palette(chosen: Settings, parameters_used: Mapping[str, Any], floor_db: float, ceiling_db: float,
            pixels: int, samples_per_pixel: int, rate: int, channels: int,
            frames_grid: Mapping[str, Any]) -> dict[str, Any]:
    """How the colours were made, with legend stops at mid lightness. The
    fields are documented in this directory's README; `check_palette` is
    their check."""
    middle = (chosen.darkest + chosen.lightest) / 2
    if chosen.hue["source"] == "centroid":
        candidates = [chosen.hue["low_hz"], 125, 250, 500, 1000, 2000, 4000, 8000, chosen.hue["high_hz"]]
        if len(set(candidates) & set(range(chosen.hue["low_hz"], chosen.hue["high_hz"] + 1))) < 4:
            # A narrow range: stops at its limits and three between, evenly in log frequency.
            candidates += [round(chosen.hue["low_hz"] * (chosen.hue["high_hz"] / chosen.hue["low_hz"]) ** (k / 4))
                           for k in (1, 2, 3)]
        values = sorted({v for v in candidates if chosen.hue["low_hz"] <= v <= chosen.hue["high_hz"]})
        hues = centroid_hue(numpy.array(values, dtype=numpy.float64), chosen.hue)
        rgb = hsl_to_rgb(hues, chosen.saturation, numpy.full(len(values), middle))
        hue_stops = [{"value": v, "unit": "Hz", "hue": round(float(h), 3), "rgb": hex_colour(c)}
                     for v, h, c in zip(values, hues, rgb)]
    else:
        hues = (pitch_angles(chosen.hue["order"]) + chosen.hue["reference_hue"]) % 360
        rgb = hsl_to_rgb(hues.astype(numpy.float64), chosen.saturation, numpy.full(12, middle))
        hue_stops = [{"value": name, "unit": "pitch class", "hue": int(h), "rgb": hex_colour(c)}
                     for name, h, c in zip(PITCH_CLASSES, hues, rgb)]
    levels = numpy.linspace(floor_db, ceiling_db, 7)
    light = lightness(levels, floor_db, ceiling_db, chosen)
    grey = hsl_to_rgb(numpy.zeros(len(levels)), 0.0, light)
    return {"contract": PALETTE_CONTRACT,
            "parameters": dict(parameters_used),
            "colour": {"dtype": "u8", "columns": ["red", "green", "blue"], "rows": pixels,
                       "row": "peaks pixel k, centred (k + 0.5) x samples_per_pixel / sample_rate seconds"},
            "peaks": {"kind": parameters_used["peaks"], "format": PEAKS_FORMAT, "sample_rate": rate,
                      "samples_per_pixel": samples_per_pixel, "pixels": pixels, "channels": channels},
            "frames": {field: frames_grid[field] for field in GRID_FIELDS if field in frames_grid},
            "hue": {"source": chosen.hue["source"], "stops": hue_stops},
            "saturation": "fixed" if chosen.hue["source"] == "centroid" else CHROMA_SATURATION,
            "lightness": {"source": "frames/rms_db", "unit": "dBFS", "floor_db": round(floor_db, 3),
                          "ceiling_db": round(ceiling_db, 3), "darkest": chosen.darkest,
                          "lightest": chosen.lightest,
                          "stops": [{"value": round(float(v), 3), "lightness": round(float(l), 4),
                                     "rgb": hex_colour(c)} for v, l, c in zip(levels, light, grey)]}}


HEX_COLOUR = re.compile(r"#[0-9a-f]{6}")


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and numpy.isfinite(value)


def _count(value: Any, low: int = 1) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= low


def check_palette(value: Any) -> None:
    """Refuse anything but an `archive-colour-palette/2` document: exactly the
    fields the README lists, of their types, and consistent with each other
    and with its own parameters."""
    def require(condition: bool, message: str) -> None:
        if not condition:
            raise ColourError(f"palette: {message}")

    top = _fields(value, "palette", {"contract", "parameters", "colour", "peaks", "frames", "hue",
                                     "saturation", "lightness"})
    require(top["contract"] == PALETTE_CONTRACT, f"contract is {top['contract']!r}, not {PALETTE_CONTRACT}")
    chosen = settings(top["parameters"])
    rows = _fields(top["colour"], "palette.colour", {"dtype", "columns", "rows", "row"})
    require(rows["dtype"] == "u8" and rows["columns"] == ["red", "green", "blue"], "colour is u8 red, green, blue")
    require(_count(rows["rows"]) and isinstance(rows["row"], str), "colour.rows is a count and row is text")
    peaks = _fields(top["peaks"], "palette.peaks",
                    {"kind", "format", "sample_rate", "samples_per_pixel", "pixels", "channels"})
    require(peaks["kind"] == top["parameters"]["peaks"], "peaks.kind is the parameters' peaks")
    require(peaks["format"] == PEAKS_FORMAT, f"peaks.format is {PEAKS_FORMAT!r}")
    require(all(_count(peaks[f]) for f in ("sample_rate", "samples_per_pixel", "pixels", "channels")),
            "peaks' rate, samples per pixel, pixels, and channels are counts")
    require(peaks["pixels"] == rows["rows"], "there is one colour row per peaks pixel")
    frames = top["frames"]
    require(isinstance(frames, dict) and set(frames) <= set(GRID_FIELDS)
            and {"sample_rate", "hop", "first_centre"} <= set(frames)
            and all(_count(v, 0) for v in frames.values()) and frames["sample_rate"] >= 1 and frames["hop"] >= 1,
            "frames is a grid: sample_rate, hop, first_centre, and perhaps frame, as integers")
    hue = _fields(top["hue"], "palette.hue", {"source", "stops"})
    require(hue["source"] == chosen.hue["source"], "hue.source is the parameters' hue source")
    stops = hue["stops"]
    require(isinstance(stops, list) and len(stops) >= 2, "hue.stops is a list of at least two stops")
    unit = "Hz" if chosen.hue["source"] == "centroid" else "pitch class"
    for stop in stops:
        _fields(stop, "palette.hue.stops[]", {"value", "unit", "hue", "rgb"})
        require(stop["unit"] == unit, f"every hue stop's unit is {unit!r}")
        require(_number(stop["value"]) if unit == "Hz" else stop["value"] in PITCH_CLASSES,
                "a hue stop's value is a frequency or a pitch class")
        require(_number(stop["hue"]) and 0 <= stop["hue"] <= 360, "a hue stop's hue is degrees from 0 to 360")
        require(isinstance(stop["rgb"], str) and HEX_COLOUR.fullmatch(stop["rgb"]) is not None,
                "a stop's rgb is #rrggbb")
    expected = "fixed" if chosen.hue["source"] == "centroid" else CHROMA_SATURATION
    require(top["saturation"] == expected, f"saturation is {expected!r} for this hue source")
    light = _fields(top["lightness"], "palette.lightness",
                    {"source", "unit", "floor_db", "ceiling_db", "darkest", "lightest", "stops"})
    require(light["source"] == "frames/rms_db" and light["unit"] == "dBFS", "lightness is frames/rms_db in dBFS")
    require(_number(light["floor_db"]) and _number(light["ceiling_db"]) and light["floor_db"] < light["ceiling_db"],
            "lightness.floor_db is below ceiling_db")
    require(light["darkest"] == chosen.darkest and light["lightest"] == chosen.lightest,
            "lightness.darkest and lightest are the parameters'")
    require(isinstance(light["stops"], list) and len(light["stops"]) >= 2, "lightness.stops has at least two stops")
    for stop in light["stops"]:
        _fields(stop, "palette.lightness.stops[]", {"value", "lightness", "rgb"})
        require(_number(stop["value"]) and _number(stop["lightness"]) and 0 <= stop["lightness"] <= 1,
                "a lightness stop is decibels and a lightness from 0 to 1")
        require(isinstance(stop["rgb"], str) and HEX_COLOUR.fullmatch(stop["rgb"]) is not None,
                "a stop's rgb is #rrggbb")


# -- peaks and the preview ---------------------------------------------------

def read_peaks(path: Path) -> tuple[int, int, numpy.ndarray]:
    """A peaks file's rate, samples per pixel, and [pixels, channels, 2] values."""
    data = path.read_bytes()
    if len(data) < waveform.PEAKS_HEADER.size:
        raise ColourError(f"{path} is shorter than a peaks header")
    version, flags, rate, pixel, length, channels = waveform.PEAKS_HEADER.unpack_from(data)
    if not waveform.peaks_complete(path) or version != waveform.PEAKS_VERSION:
        raise ColourError(f"{path} is not a complete version 2, 8-bit peaks file")
    values = numpy.frombuffer(data, dtype=numpy.int8, offset=waveform.PEAKS_HEADER.size)
    return rate, pixel, values.reshape(length, channels, 2)


def render(peaks: numpy.ndarray, rgb: numpy.ndarray, width: int, height: int) -> numpy.ndarray:
    """An RGBA image [height, width, 4] of the whole recording: each column
    spans the lowest minimum to the highest maximum of every channel of its
    pixels, in their colour weighted by each pixel's range; the rest is
    transparent. Silence still draws the centre row."""
    pixels = len(peaks)
    starts = (numpy.arange(width, dtype=numpy.int64) * pixels) // width
    lows = numpy.minimum.reduceat(peaks[:, :, 0].min(axis=1), starts).astype(numpy.float64) / 128
    highs = numpy.maximum.reduceat(peaks[:, :, 1].max(axis=1), starts).astype(numpy.float64) / 128
    weight = (peaks[:, :, 1].max(axis=1).astype(numpy.float64) - peaks[:, :, 0].min(axis=1) + 1)
    total = numpy.add.reduceat(weight, starts)
    mixed = numpy.add.reduceat(rgb.astype(numpy.float64) * weight[:, None], starts) / total[:, None]
    half = (height - 1) / 2
    top = numpy.floor(half * (1 - highs)).astype(numpy.int64)
    bottom = numpy.ceil(half * (1 - lows)).astype(numpy.int64)
    centre_low, centre_high = int(numpy.floor(half)), int(numpy.ceil(half))
    top = numpy.minimum(top, centre_low)
    bottom = numpy.maximum(bottom, centre_high)
    rows = numpy.arange(height)[:, None]
    mask = (rows >= top[None, :]) & (rows <= bottom[None, :])
    image = numpy.zeros((height, width, 4), dtype=numpy.uint8)
    image[..., :3] = numpy.clip(numpy.rint(mixed), 0, 255).astype(numpy.uint8)[None, :, :]
    image[..., 3] = numpy.where(mask, 255, 0)
    image[..., :3] *= mask[..., None]
    return image


def png(image: numpy.ndarray) -> bytes:
    """An 8-bit RGBA PNG of an [height, width, 4] image: one IDAT, no filtering."""
    height, width, depth = image.shape
    if depth != 4 or image.dtype != numpy.uint8:
        raise ColourError("a preview is 8-bit RGBA")

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    rows = numpy.concatenate((numpy.zeros((height, 1), dtype=numpy.uint8), image.reshape(height, width * 4)),
                             axis=1)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows.tobytes(), 9))
            + chunk(b"IEND", b""))


# -- the program -------------------------------------------------------------

def program_version(program: str, name: str) -> str:
    """The version an ffmpeg-family program reports on its first line
    (`<name> version <v> ...`). A program that answers otherwise is refused:
    the result's software is provenance, and "unknown" would not be."""
    try:
        answered = subprocess.run([program, "-version"], capture_output=True, text=True)
    except OSError as exc:
        raise ColourError(f"{name} could not be run as {program!r}: {exc}") from exc
    first = answered.stdout.split("\n", 1)[0].split()
    if answered.returncode != 0 or len(first) < 3 or first[:2] != [name, "version"]:
        raise ColourError(f"{program} -version did not report a {name} version")
    return first[2]


def make(request: Mapping[str, Any], ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe") -> dict[str, Any]:
    """Write every output into the request's output directory; returns the result."""
    chosen = settings(request.get("parameters"))
    output = Path(request["output_dir"])
    source = Path(request["input"]["path"])
    centroid, grid = read_need(request, "frames/centroid", 1)
    hpcp, hpcp_grid = read_need(request, "frames/hpcp", 12)
    rms_db, rms_grid = read_need(request, "frames/rms_db", 1)
    if not (len(centroid) == len(hpcp) == len(rms_db) and grid == hpcp_grid == rms_grid):
        raise ColourError("the frames series are not on one grid")
    software = [{"name": "ffmpeg", "version": program_version(ffmpeg, "ffmpeg")},
                {"name": "ffprobe", "version": program_version(ffprobe, "ffprobe")},
                {"name": "numpy", "version": numpy.__version__},
                {"name": "zlib", "version": zlib.ZLIB_RUNTIME_VERSION},
                {"name": "python", "version": platform.python_version()}]

    # The derived-media decode, kept only while this program runs: the peaks
    # are derived media, served from there, and never a bank output.
    stream = waveform.probe(source, ffprobe)
    with tempfile.TemporaryDirectory(prefix="colour-peaks-") as scratch:
        with open(Path(scratch) / "peaks.dat", "w+b") as handle:
            waveform.write_peaks(source, stream, handle, ffmpeg)
        rate, samples_per_pixel, peaks = read_peaks(Path(scratch) / "peaks.dat")
        peaks = peaks.copy()
    pixels = len(peaks)
    check_lengths(pixels, samples_per_pixel, rate, len(centroid), grid)

    positions = frame_positions(pixels, samples_per_pixel, rate, grid)
    floor_db, ceiling_db = lightness_range(rms_db[:, 0], chosen)
    rgb = colours(chosen, interpolate(centroid, positions)[:, 0], interpolate(hpcp, positions),
                  interpolate(rms_db, positions)[:, 0], floor_db, ceiling_db)
    described = palette(chosen, request["parameters"], floor_db, ceiling_db, pixels, samples_per_pixel, rate,
                        stream.channels, grid)
    check_palette(described)
    (output / "colour.u8").write_bytes(rgb.tobytes())
    (output / "palette.json").write_text(json.dumps(described, indent=1, sort_keys=True) + "\n")
    (output / "preview.png").write_bytes(png(render(peaks, rgb, chosen.width, chosen.height)))
    return {"contract": "archive-extractor-result/1",
            "software": software,
            "grids": {"peaks": {"sample_rate": 2 * rate, "hop": 2 * samples_per_pixel,
                                "frame": 2 * samples_per_pixel, "first_centre": samples_per_pixel}},
            "outputs": {"colour": {"shape": [pixels, 3]}, "palette": {}, "preview": {}}}


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: colour.py <request.json>", file=sys.stderr)
        return 2
    try:
        request = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
        result = make(request)
        (Path(request["output_dir"]) / "result.json").write_text(json.dumps(result))
    except (ColourError, waveform.MediaError, OSError, KeyError, TypeError, ValueError) as exc:
        print(f"colour: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
