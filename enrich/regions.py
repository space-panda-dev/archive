"""Song spans and the eager plan of a mode (ADR 0112).

Which parts of a recording the expensive extractors run over eagerly. A
recording's song spans come from the first of these sources that has any for
it, in this order of precedence:

1. `song_occurrences`: the pieces on this recording of the selections that
   head `song_occurrence` claims pin. Curators said these are songs.
2. `selections`: the pieces on this recording of head `audio_selection`
   claims. A curator marked them, though no occurrence says they are songs.
3. `span_suggestions`: the `music` spans of head `span_suggestions` claims
   about this recording (ADRs 0066, 0091), a machine's guess.
4. `playing`: the `structure` extractor's `playing` spans, when the caller
   read them from the bank; they cover most of a rehearsal and save little.

A higher source wins whole, never mixed with a lower one: one curated song
on a recording means the detector's guesses are not used for it. A recording
with none of them has no song spans (`none`), and its expensive features are
made only on request.

Each span is widened by a margin on both sides and snapped outward to whole
seconds, and regions less than a gap apart are merged, so a span edited
within its margin is still covered and curation reruns nothing. The margin
and gap are the operator's to confirm (ADR 0112, "For the operator to
confirm", item 2).

`plan` turns a mode into what each extractor is made over eagerly: the whole
content, these regions, or nothing until asked for. Requests are made in
every mode; the scheduler (`enrich backfill`) consumes this.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

SOURCES = ("song_occurrences", "selections", "span_suggestions", "playing")
MODES = ("songs", "eager", "on-demand")

OCCURRENCE = "archive.claim/song_occurrence"
SELECTION = "archive.claim/audio_selection"
SUGGESTIONS = ("archive.claim/span_suggestions", "1")
STREAM = ("archive.claim/audio_stream", "1")

# Proposed in ADR 0112 and awaiting the operator: 10 s each side, merging
# regions less than 30 s apart, and 0.04 CPU-seconds per audio-second.
MARGIN_SECONDS = 10
GAP_SECONDS = 30
EXPENSIVE_THRESHOLD = 0.04

# The backfill profile of 2026-10-19 (x86-64 laptop, 2715 s rehearsal, run as
# the backfill runs them): seconds per audio-second, both passes counted
# where an extractor runs twice. The `bench-cost` suite's recorded baselines
# replace these; until then they decide what is expensive.
PROFILED_COSTS: dict[str, float] = {
    "stems": 0.576, "f0-pesto": 0.125, "melody": 0.079, "chords-jiang": 0.060, "notes-basic-pitch": 0.045,
    "rhythm": 0.031, "chords-chordino": 0.027, "frames": 0.022, "tonal": 0.014, "structure": 0.001,
}


class RegionError(Exception):
    """A record this module cannot read as the contract says; never taken
    for absent (INV-24)."""


@dataclass(frozen=True, order=True)
class Region:
    """`[start, end)` in samples at `rate`, the content's stream rate."""
    start: int
    end: int
    rate: int

    def as_json(self) -> dict[str, int]:
        return {"start": self.start, "end": self.end, "rate": self.rate}

    def covers(self, start: int, end: int, rate: int) -> bool:
        """Whether `[start, end)` at `rate` lies inside this region, exactly."""
        return self.start * rate <= start * self.rate and end * self.rate <= self.end * rate


# -- reading spans from an export ----------------------------------------------

def _cites(record: Mapping[str, Any], contract: str, versions: Iterable[str] | None = None) -> bool:
    return any(c.get("id") == contract and (versions is None or c.get("version") in versions)
               for c in record.get("contracts", []))


def _head(reader: Any, record: Mapping[str, Any]) -> bool:
    return reader.heads.get(record["object_id"]) is record


def _rescale(start: int, end: int, rate: int, to: int) -> tuple[int, int]:
    """A span at `rate` on the `to` timeline, widened outward to whole samples."""
    if rate <= 0:
        raise RegionError(f"a span at rate {rate}")
    return (start * to) // rate, -((-end * to) // rate)


def selection_spans(selection: Mapping[str, Any], artifact_id: str, rate: int) -> list[tuple[int, int]]:
    """The pieces of a selection (version 1 or 2) on this Artifact, at `rate`.
    Version 2's tracks are read in order, each piece paired with the next
    `piece` reference, as the contract keeps them."""
    payload = selection["payload"]
    if _cites(selection, SELECTION, ("1",)):
        pieces = payload["pieces"]
    elif _cites(selection, SELECTION, ("2",)):
        pieces = [piece for track in payload["tracks"] for piece in track["pieces"]]
    else:
        raise RegionError(f"{selection['object_id']} cites no audio_selection version this reads (1 or 2)")
    refs = [ref for ref in selection["refs"] if ref["role"] == "piece"]
    if len(refs) != len(pieces):
        raise RegionError(f"{selection['revision_id']} has {len(pieces)} pieces and {len(refs)} piece references")
    return [_rescale(piece["start_sample"], piece["end_sample"], piece["sample_rate"], rate)
            for ref, piece in zip(refs, pieces) if ref["id"] == artifact_id]


def _pinned(reader: Any, ref: Mapping[str, Any]) -> dict[str, Any]:
    record = reader.by_revision.get(ref.get("revision_id"))
    if record is None or record["object_id"] != ref.get("id"):
        raise RegionError(f"the export lacks revision {ref.get('revision_id')} of {ref.get('id')}")
    return record


def stream_of(reader: Any, artifact_id: str) -> tuple[int, int | None]:
    """The rate and length in samples of the Artifact's first audio stream,
    the one every extractor decodes (`0:a:0`), from its head
    `audio_stream` claims. None for the length when the probe gave none."""
    streams = [record for record in reader.records
               if record["shape"] == "claim" and _cites(record, STREAM[0], [STREAM[1]]) and _head(reader, record)
               and any(ref["role"] == "artifact" and ref["id"] == artifact_id for ref in record["refs"])]
    if not streams:
        raise RegionError(f"{artifact_id} has no audio_stream claim; probe it first")
    first = min(streams, key=lambda record: record["payload"]["stream_index"])
    return first["payload"]["sample_rate"], first["payload"].get("sample_count")


def song_spans(reader: Any, artifact_id: str, rate: int,
               playing: Iterable[Mapping[str, Any]] | None = None) -> tuple[str, list[tuple[int, int]]]:
    """The recording's song spans at `rate` and the source they came from,
    by the precedence in this module's description. `playing` is the
    `structure/playing` spans (seconds) read from the bank, or None when the
    caller has none."""
    occurrences: list[tuple[int, int]] = []
    selections: list[tuple[int, int]] = []
    suggestions: list[tuple[int, int]] = []
    for record in reader.records:
        if record["shape"] != "claim" or not _head(reader, record):
            continue
        if _cites(record, OCCURRENCE):
            for ref in record["refs"]:
                if ref["role"] == "selection":
                    occurrences += selection_spans(_pinned(reader, ref), artifact_id, rate)
        elif _cites(record, SELECTION):
            selections += selection_spans(record, artifact_id, rate)
        elif _cites(record, SUGGESTIONS[0], [SUGGESTIONS[1]]):
            if not any(ref["role"] == "source" and ref["id"] == artifact_id for ref in record["refs"]):
                continue
            given = record["payload"]["sample_rate"]
            suggestions += [_rescale(span["start_sample"], span["end_sample"], given, rate)
                            for span in record["payload"]["spans"] if span["class"] == "music"]
    measured = [_rescale(round(span["start"] * 1000), round(span["end"] * 1000), 1000, rate)
                for span in (playing or [])]
    for source, spans in zip(SOURCES, (occurrences, selections, suggestions, measured)):
        spans = [(start, end) for start, end in spans if end > start]
        if spans:
            return source, sorted(spans)
    return "none", []


# -- widening and merging ------------------------------------------------------

def regions_of(spans: Iterable[tuple[int, int]], rate: int, length: int | None = None,
               margin_seconds: int = MARGIN_SECONDS, gap_seconds: int = GAP_SECONDS) -> list[Region]:
    """Each span widened by the margin and snapped outward to whole seconds,
    clipped to the content, and merged with any region less than the gap
    away. The result is sorted and disjoint."""
    if rate <= 0:
        raise RegionError(f"a rate of {rate}")
    widened = []
    for start, end in spans:
        if not 0 <= start < end:
            raise RegionError(f"a span [{start}, {end}) is not 0 <= start < end")
        low = max(0, (start - margin_seconds * rate) // rate * rate)
        high = -(-(end + margin_seconds * rate) // rate) * rate
        if length is not None:
            high = min(high, length)
        if low < high:
            widened.append((low, high))
    merged: list[list[int]] = []
    for start, end in sorted(widened):
        if merged and start - merged[-1][1] < gap_seconds * rate:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [Region(start, end, rate) for start, end in merged]


def regions_for(reader: Any, artifact: Mapping[str, Any],
                playing: Iterable[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """The song regions of an Artifact (an export record): `{source, rate,
    length, spans, regions}`. The scheduler makes the expensive extractors
    over `regions` in `songs` mode."""
    rate, length = stream_of(reader, artifact["object_id"])
    source, spans = song_spans(reader, artifact["object_id"], rate, playing)
    return {"source": source, "rate": rate, "length": length, "spans": spans,
            "regions": regions_of(spans, rate, length)}


def covered(regions: Iterable[Region], start: int, end: int, rate: int) -> bool:
    """Whether one region holds the whole span; regions are never stitched."""
    return any(region.covers(start, end, rate) for region in regions)


# -- the plan of a mode --------------------------------------------------------

def expensive(extractor_id: str, costs: Mapping[str, float] = PROFILED_COSTS,
              threshold: float = EXPENSIVE_THRESHOLD) -> bool:
    """Whether an extractor's recorded cost is above the threshold. One with
    no recorded cost runs whole, as ADR 0112 lists every extractor the
    profile did not single out; the cost gate gives each a recorded cost."""
    cost = costs.get(extractor_id)
    return cost is not None and cost > threshold


def plan(mode: str, extractor_ids: Iterable[str], regions: list[Region],
         costs: Mapping[str, float] = PROFILED_COSTS, threshold: float = EXPENSIVE_THRESHOLD,
         length: int | None = None) -> dict[str, Any]:
    """What each extractor is made over eagerly in this mode: `"whole"`, a
    list of regions, or `None` (made only when a request asks for it).

    - `eager`: everything whole.
    - `songs`: cheap extractors whole; expensive ones over the song regions,
      and on request only where there are none. One region that is the whole
      content (`length` samples: a recording selected end to end) is made
      whole, so readers of whole entries find it and a routed unit may go to
      its worker.
    - `on-demand`: nothing.
    """
    if mode not in MODES:
        raise RegionError(f"mode {mode!r} is not one of {', '.join(MODES)}")
    if length is not None and len(regions) == 1 and (regions[0].start, regions[0].end) == (0, length):
        regions = []
        mode = "eager" if mode == "songs" else mode
    chosen: dict[str, Any] = {}
    for extractor_id in sorted(extractor_ids):
        if mode == "eager":
            chosen[extractor_id] = "whole"
        elif mode == "on-demand":
            chosen[extractor_id] = None
        elif not expensive(extractor_id, costs, threshold):
            chosen[extractor_id] = "whole"
        else:
            chosen[extractor_id] = list(regions) or None
    return chosen
