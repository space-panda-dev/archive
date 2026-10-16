"""Where derived media live, and what each kind is called (ADR 0069).

The layout is the contract between `enrich/media.py`, which writes derived
media, and the relay, which serves them. It needs only the standard library,
so the relay can read it without the enrichment tools' dependencies.

A derived-media root holds `sha256/<ab>/<cd>/<hex>/<name>`, where `<hex>` is
the source content's SHA-256 and `<name>` carries the method version. The
root is regenerable: losing it loses nothing (docs/state-model.md).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


@dataclass(frozen=True)
class Kind:
    """One kind of derived media made by one version of one method."""

    name: str
    version: int
    suffix: str
    media_type: str

    @property
    def file_name(self) -> str:
        return f"{self.name}-{self.version}{self.suffix}"


# Peaks: an audiowaveform binary file, version 2, 8-bit, about ten
# milliseconds a pixel. Listening copy: AAC-LC at 192 kb/s in MP4, index first.
# A change to either method's output is a new version, so a new file name.
KINDS: dict[str, Kind] = {
    "peaks": Kind("peaks", 1, ".dat", "application/octet-stream"),
    "listen": Kind("listen", 1, ".mp4", "audio/mp4"),
}


def is_digest(value: object) -> bool:
    return isinstance(value, str) and DIGEST.fullmatch(value) is not None


def directory(root: Path, digest: str) -> Path:
    """The directory holding every derivative of one content digest."""
    if not is_digest(digest):
        raise ValueError(f"not a canonical content digest: {digest!r}")
    hexdigest = digest.removeprefix("sha256:")
    return root / "sha256" / hexdigest[0:2] / hexdigest[2:4] / hexdigest


def path(root: Path, digest: str, kind: str) -> Path:
    return directory(root, digest) / KINDS[kind].file_name


def etag(digest: str, kind: str) -> str:
    """A strong entity tag naming the source digest and the method version."""
    return f'"{digest}/{KINDS[kind].name}-{KINDS[kind].version}"'
