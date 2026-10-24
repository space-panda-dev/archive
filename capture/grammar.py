"""Recorder grammars: what a recorder's file names say (ADR 0070).

A grammar is a named module of knowledge about one recorder model: the form
of its file names, its track suffixes, which simultaneous source comes
first, and its rollover rule. It reads names, sizes, and file headers; it
never reads an archive and asserts nothing by itself. The importer turns its
grouping into a proposal for Door.

Standard library only.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from pathlib import Path


class GrammarError(Exception):
    """A grammar recognised a file but cannot decide what it needs about it."""


@dataclass(frozen=True)
class Recognised:
    """What a grammar read from one file name.

    `take` names one press of the record button: every simultaneous source
    of that take shares it. `source` is the source's token as the grammar
    spells it. `started` is the recorder's own clock, naive: recorders do
    not say which zone they were set to, and only differences are used."""
    grammar: str
    take: str
    source: str
    started: datetime


def wav_duration(path: Path) -> Fraction:
    """The duration a RIFF WAVE header states: the `data` chunk's size over
    the `fmt ` chunk's byte rate. Nothing is decoded; this is the recorder's
    own statement, good enough to compare with a gap of seconds and never
    used as a sample count."""
    try:
        with open(path, "rb") as handle:
            header = handle.read(12)
            if len(header) != 12 or header[0:4] != b"RIFF" or header[8:12] != b"WAVE":
                raise GrammarError(f"{path.name} is not a RIFF WAVE file")
            byte_rate = None
            while True:
                chunk = handle.read(8)
                if len(chunk) < 8:
                    raise GrammarError(f"{path.name} has no data chunk")
                kind, size = chunk[0:4], struct.unpack("<I", chunk[4:8])[0]
                if kind == b"fmt ":
                    body = handle.read(size)
                    if len(body) < 16:
                        raise GrammarError(f"{path.name} has a short fmt chunk")
                    byte_rate = struct.unpack("<I", body[8:12])[0]
                    if size % 2:
                        handle.seek(1, 1)
                elif kind == b"data":
                    if not byte_rate:
                        raise GrammarError(f"{path.name} states no byte rate before its data")
                    return Fraction(size, byte_rate)
                else:
                    handle.seek(size + (size % 2), 1)
    except OSError as exc:
        raise GrammarError(f"{path.name} could not be read: {exc}") from exc


class ZoomH4E:
    """The Zoom H4essential's names: `MMDDYY_HHMMSS_TrLR.WAV` for the
    built-in stereo pair and `MMDDYY_HHMMSS_TrMic.WAV` for the external
    input, written at once by one take.

    - **Sources.** `TrLR` comes first, so it is a session's primary source
      when present; `TrMic` second. Any other track token, a lowercase
      extension, or anything after the extension is not this grammar.
    - **Rollover.** The recorder stops a file at exactly 2,147,075,072 bytes
      and opens the next take at once. A take continues the previous one
      only when the previous file is exactly that size *and* the next take
      starts within two seconds of the previous file's end, as its WAV header
      states it: the gap alone joins two short takes seconds apart, and the
      size alone joins a take that stopped at the cap with nothing after it.

    - **Twins.** The operator's recorder writes every take twice: `TrMic`
      carries what the operator treats as the same sound as `TrLR`. With
      `twins` (the default) the importer keeps one file of each take, the
      same one every time (ADR 0070, amendment on twins); without it, every
      source is kept.

    The measured values are the V2 system's, observed on every split file it
    held. `split_size` exists so a test can exercise the rule without
    writing two gigabytes; the importer always uses the default."""

    name = "zoom-h4e"
    label = "Zoom H4essential"
    sources = ("TrLR", "TrMic")
    SPLIT_SIZE = 2_147_075_072
    MAX_GAP = Fraction(2)
    _NAME = re.compile(r"^(\d{2})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})_(TrLR|TrMic)\.WAV$")

    def __init__(self, split_size: int = SPLIT_SIZE, twins: bool = True):
        self.split_size = split_size
        self.twins = twins

    def recognise(self, file_name: str) -> Recognised | None:
        match = self._NAME.match(file_name)
        if match is None:
            return None
        month, day, year, hour, minute, second = (int(group) for group in match.groups()[:6])
        try:
            started = datetime(2000 + year, month, day, hour, minute, second)
        except ValueError:
            return None
        return Recognised(self.name, file_name[:13], match.group(7), started)

    def continues(self, previous: Path, previous_started: datetime, following_started: datetime) -> bool:
        """Whether a take starting at `following_started` continues the file
        at `previous`, which started at `previous_started`."""
        if previous.stat().st_size != self.split_size:
            return False
        # Exact seconds: the header's duration is a fraction, the names whole.
        elapsed = following_started - previous_started
        gap = Fraction(elapsed.days * 86400 + elapsed.seconds) - wav_duration(previous)
        return -self.MAX_GAP <= gap <= self.MAX_GAP


GRAMMARS = (ZoomH4E(),)
