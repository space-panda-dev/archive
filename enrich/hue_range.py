#!/usr/bin/env python3
"""The colour extractor's centroid hue limits for a set of recordings.

A tool beside the extractor, not part of it: it reads the bank, which the
extractor's own code never does (its needs arrive in its request).

A centroid hue spans `hue.low_hz` to `hue.high_hz` in log frequency. Limits
that are wider than the centroids the recordings reach leave most of the hue
circle unused: with 100 Hz to 8 kHz, a studio album's frames (5th to 95th
percentile about 630 Hz to 3.6 kHz) all fell between green and cyan. This
reads the `frames` entries of the recordings named, pools the centroids of
every frame at or above a level (silence has no meaningful centroid), and
prints the configuration that sets the limits to two percentiles of them,
5th and 95th by default:

    hue_range.py --bank <root> <recording>... > colour.json
    extract.py run colour --config colour.json --content <recording> --bank <root>

The limits are parameters, so they are in every entry's key: recordings
coloured with one set's limits are comparable with each other, and a set
that grows keeps its colours until its limits are set again. The
summary it reports on standard error (frames read, the percentiles) is for
the person choosing; the configuration names only the limits.

A recording without a `frames` entry for the installed extractor is refused:
run `frames` (or `colour`, which needs it) first. So is a set with no frame
above the level.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Iterable

ENRICH = Path(__file__).resolve().parent
sys.path.insert(0, str(ENRICH))
sys.path.insert(0, str(ENRICH / "extractors" / "colour"))

import numpy  # noqa: E402

import bank  # noqa: E402
import colour  # noqa: E402


def collect(root: Path, digests: Iterable[str], floor_db: float) -> numpy.ndarray:
    """Every frame's centroid at or above `floor_db` dBFS, pooled across the recordings."""
    reader = bank.Bank(root)
    pooled = []
    for digest in digests:
        found = reader.entry(digest, "frames")
        if found is None:
            raise colour.ColourError(f"{digest} has no frames entry in {root}; run frames on it first")
        centroid, level = found.series("centroid")[:, 0], found.series("rms_db")[:, 0]
        pooled.append(centroid[level >= floor_db])
    values = numpy.concatenate(pooled) if pooled else numpy.zeros(0, dtype=numpy.float32)
    return values[numpy.isfinite(values)]


def limits(centroids: numpy.ndarray, low_percentile: float, high_percentile: float) -> tuple[int, int]:
    """Whole-hertz limits at two percentiles of the centroids: the low one
    rounded down, the high one up, within what `colour` accepts."""
    if not 0 <= low_percentile < high_percentile <= 100:
        raise colour.ColourError(f"percentiles must rise within 0 to 100, not {low_percentile} and {high_percentile}")
    if centroids.size == 0:
        raise colour.ColourError("no frame of the recordings is at or above the level; there is no range to take")
    low_hz, high_hz = numpy.percentile(centroids.astype(numpy.float64), [low_percentile, high_percentile])
    low = min(max(1, math.floor(low_hz)), colour.FRAMES_NYQUIST - 1)
    high = min(max(low + 1, math.ceil(high_hz)), colour.FRAMES_NYQUIST)
    return low, high


def configuration(low: int, high: int) -> dict:
    """The `--config` that sets the limits, the other hue fields at their defaults."""
    hue = {**colour.parameters()["hue"], "low_hz": low, "high_hz": high}
    colour.settings({**colour.parameters(), "hue": hue})
    return {"parameters": {"colour": {"hue": hue}}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hue_range.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("--bank", required=True)
    parser.add_argument("--low-percentile", type=float, default=5.0)
    parser.add_argument("--high-percentile", type=float, default=95.0)
    parser.add_argument("--floor-db", type=float, default=-60.0,
                        help="frames below this RMS level (dBFS) are left out (default -60)")
    parser.add_argument("recordings", nargs="+", help="the recordings' files, or their sha256:<hex> digests")
    arguments = parser.parse_args(argv)
    try:
        digests = [r if bank.derived.is_digest(r) else bank.file_digest(Path(r)) for r in arguments.recordings]
        centroids = collect(Path(arguments.bank), digests, arguments.floor_db)
        low, high = limits(centroids, arguments.low_percentile, arguments.high_percentile)
        config = configuration(low, high)
    except (colour.ColourError, bank.BankError, OSError) as exc:
        print(f"hue_range: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"recordings": len(digests), "frames": int(centroids.size),
                      "percentiles": [arguments.low_percentile, arguments.high_percentile],
                      "low_hz": low, "high_hz": high}), file=sys.stderr)
    print(json.dumps(config, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
