#!/usr/bin/env python3
"""The stems-6s extractor: Demucs's six-source htdemucs_6s, a second separator
beside `stems`.

It is the `stems` program (`../stems/stems.py`) with another pinned model and
six stems, vocals, drums, bass, guitar, piano, and other, in that order:
the same decode, 600-second chunks and 10-second crossfades, 16-bit FLAC,
and level measures. Its stems are other content than `stems`' (ADR 0075),
so a measure of `stems-6s/vocals` is keyed by that stem's own digest and
never replaces a measure of `stems/vocals`.

On an album of dense neo-soul it separated a Rhodes into `guitar` and left
`piano` near silent, so the names say which of the model's sources a stem
is, not which instrument played it.

Invoked by the runner as `<demucs-python> stems6s.py <request.json>`.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "stems"))

import stems  # noqa: E402

STEMS = ("vocals", "drums", "bass", "guitar", "piano", "other")
MODEL = {"name": "htdemucs_6s", "demucs": "4.1.0", "repository": "adefossez/HTDemucs-6s",
         "revision": "3c5ee475be622df764938de97e4281a7b07ffa58",
         "files": {"htdemucs_6s.yaml": "sha256:207405151270af8fd81c2373c25d27950916682ac91dca7884a11ce13dad6f58",
                   "5c90dfd2.safetensors":
                       "sha256:d2a1745f0744721f6b8ca5bf469b67c651ea5ed1b52998cab033b2158609d411"}}
HTDEMUCS_6S = stems.Separation("stems-6s", MODEL, STEMS)


def parameters() -> dict:
    """The settings, which the manifest must declare exactly."""
    return stems.parameters(HTDEMUCS_6S)


def main(argv: list[str]) -> int:
    return stems.main(argv, HTDEMUCS_6S)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
